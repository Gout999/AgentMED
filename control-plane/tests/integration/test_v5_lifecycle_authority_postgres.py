"""Real-PostgreSQL proof for the internal D-014 lifecycle authority kernel.

This invokes a private foundation-test seam and is persistence/CAS evidence
only.  It does not prove R2 manifest authorization, business activation,
event/audit/outbox/receipt orchestration, or a public workflow.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.models.v5_tables import (
    AIApplication,
    AIApplicationLifecycleRevision,
    SystemComponent,
    SystemComponentLifecycleRevision,
)
from app.services.v5_authority import V5AuthorityService
from app.services.v5_lifecycle_authority import (
    V5LifecycleAuthorityError,
    V5LifecycleAuthorityService,
)
from app.utils.v5_integrity import V5_HASH_RULE, v5_record_digest


pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_lifecycle_pg"
PROJECT = "proj_lifecycle_pg"
PRINCIPAL = "prn_lifecycle_pg"
APPLICATION = "app_lifecycle_pg"
COMPONENT = "cmp_lifecycle_pg"


def _envelope(
    *,
    revision: int,
    state: str,
    receipt_suffix: str,
    previous: dict | None = None,
) -> dict:
    payload = {
        "application_id": APPLICATION,
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "slug": "lifecycle-pg",
        "display_name": "Lifecycle PG",
        "owner_principal_ids": [PRINCIPAL],
        "criticality": "P1",
        "data_classification": "INTERNAL",
        "governance_mode": "MANAGED",
        "lifecycle_state": state,
        (
            "exact_previous_application_binding_or_null"
            if revision == 1
            else "exact_previous_application_binding"
        ): previous,
        "record_envelope": {
            "schema_version": "2.0",
            "workspace_id": WORKSPACE,
            "revision": revision,
            "recorded_by_principal": PRINCIPAL,
            "recorded_at": (NOW + timedelta(seconds=revision)).isoformat().replace(
                "+00:00", "Z"
            ),
            "immutable": True,
            "hash_rule": V5_HASH_RULE,
            "record_digest": "",
            "authority_receipt_id": f"ar_lifecycle_pg_{receipt_suffix}",
        },
    }
    payload["record_envelope"]["record_digest"] = v5_record_digest(payload)
    return payload


def _binding(envelope: dict) -> dict:
    record = envelope["record_envelope"]
    return {
        "kind": "AI_APPLICATION",
        "id": APPLICATION,
        "revision": record["revision"],
        "digest": record["record_digest"],
    }


def _component_envelope(
    *,
    revision: int,
    state: str,
    receipt_suffix: str,
    previous: dict | None = None,
) -> dict:
    payload = {
        "component_id": COMPONENT,
        "workspace_id": WORKSPACE,
        "application_id": APPLICATION,
        "component_kind": "APPLICATION_CODE",
        "logical_name": "runtime",
        "owner_principal_ids": [PRINCIPAL],
        "criticality": "P1",
        "data_classification": "INTERNAL",
        "permission_classification": "READ_ONLY",
        "effect_classification": "LOCAL",
        "lifecycle_state": state,
        (
            "exact_previous_system_component_binding_or_null"
            if revision == 1
            else "exact_previous_system_component_binding"
        ): previous,
        "record_envelope": {
            "schema_version": "2.0",
            "workspace_id": WORKSPACE,
            "revision": revision,
            "recorded_by_principal": PRINCIPAL,
            "recorded_at": (NOW + timedelta(seconds=10 + revision))
            .isoformat()
            .replace("+00:00", "Z"),
            "immutable": True,
            "hash_rule": V5_HASH_RULE,
            "record_digest": "",
            "authority_receipt_id": f"ar_component_pg_{receipt_suffix}",
        },
    }
    payload["record_envelope"]["record_digest"] = v5_record_digest(payload)
    return payload


def _component_binding(envelope: dict) -> dict:
    record = envelope["record_envelope"]
    return {
        "kind": "SYSTEM_COMPONENT",
        "id": COMPONENT,
        "revision": record["revision"],
        "digest": record["record_digest"],
    }


def test_postgres_activation_storage_primitive_has_exactly_one_writer(
    pg_engine,
) -> None:
    sessions = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    registered = _envelope(
        revision=1, state="REGISTERED", receipt_suffix="registered"
    )
    with sessions() as seed:
        V5LifecycleAuthorityService(seed).append_registration_revision(
            kind="AI_APPLICATION", envelope_payload=registered
        )
        seed.commit()

    previous = _binding(registered)
    candidates = [
        _envelope(
            revision=2,
            state="ACTIVE",
            receipt_suffix=suffix,
            previous=previous,
        )
        for suffix in ("candidate_a", "candidate_b")
    ]
    barrier = threading.Barrier(2)

    def activate(candidate: dict) -> tuple[str, str]:
        with sessions() as worker:
            barrier.wait(timeout=5)
            try:
                result = V5LifecycleAuthorityService(
                    worker
                )._append_activation_revision_for_foundation_test(
                    kind="AI_APPLICATION", envelope_payload=candidate
                )
                worker.commit()
                status = "written" if not result.replayed else "replayed"
                return status, result.history.record_digest
            except V5LifecycleAuthorityError as exc:
                worker.rollback()
                return exc.code, candidate["record_envelope"]["record_digest"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(activate, candidates))

    assert [status for status, _digest in outcomes].count("written") == 1
    assert [status for status, _digest in outcomes].count(
        "v5.lifecycle.revision_conflict"
    ) == 1

    with sessions() as verify:
        rows = list(
            verify.scalars(
                sa.select(AIApplicationLifecycleRevision).order_by(
                    AIApplicationLifecycleRevision.revision
                )
            )
        )
        head = verify.get(AIApplication, APPLICATION)
        assert [row.revision for row in rows] == [1, 2]
        assert head is not None and head.revision == 2
        assert head.record_digest == rows[1].record_digest
        assert rows[0].record_digest == registered["record_envelope"]["record_digest"]

        authority = V5AuthorityService(verify)
        assert authority.validate_exact_lifecycle_binding(
            workspace_id=WORKSPACE, binding=_binding(registered)
        ).revision == 1
        assert authority.validate_exact_lifecycle_binding(
            workspace_id=WORKSPACE,
            binding={
                "kind": "AI_APPLICATION",
                "id": APPLICATION,
                "revision": 2,
                "digest": head.record_digest,
            },
            require_current=True,
            require_active=True,
        ).revision == 2


def test_postgres_outer_transaction_rollback_removes_projection_and_history(
    pg_engine,
) -> None:
    sessions = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    registered = _envelope(
        revision=1, state="REGISTERED", receipt_suffix="rollback"
    )
    with sessions() as writer:
        V5LifecycleAuthorityService(writer).append_registration_revision(
            kind="AI_APPLICATION", envelope_payload=registered
        )
        writer.rollback()

    with sessions() as verify:
        assert verify.get(AIApplication, APPLICATION) is None
        assert (
            verify.scalar(
                sa.select(sa.func.count()).select_from(
                    AIApplicationLifecycleRevision
                )
            )
            == 0
        )


def test_postgres_component_activation_storage_primitive_has_one_writer(
    pg_engine,
) -> None:
    sessions = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    app_registered = _envelope(
        revision=1, state="REGISTERED", receipt_suffix="component_app_registered"
    )
    app_activated = _envelope(
        revision=2,
        state="ACTIVE",
        receipt_suffix="component_app_activated",
        previous=_binding(app_registered),
    )
    component_registered = _component_envelope(
        revision=1, state="REGISTERED", receipt_suffix="registered"
    )
    with sessions() as seed:
        lifecycle = V5LifecycleAuthorityService(seed)
        lifecycle.append_registration_revision(
            kind="AI_APPLICATION", envelope_payload=app_registered
        )
        lifecycle._append_activation_revision_for_foundation_test(
            kind="AI_APPLICATION", envelope_payload=app_activated
        )
        lifecycle.append_registration_revision(
            kind="SYSTEM_COMPONENT", envelope_payload=component_registered
        )
        seed.commit()

    previous = _component_binding(component_registered)
    candidates = [
        _component_envelope(
            revision=2,
            state="ACTIVE",
            receipt_suffix=suffix,
            previous=previous,
        )
        for suffix in ("candidate_a", "candidate_b")
    ]
    barrier = threading.Barrier(2)

    def activate(candidate: dict) -> tuple[str, str]:
        with sessions() as worker:
            barrier.wait(timeout=5)
            try:
                result = V5LifecycleAuthorityService(
                    worker
                )._append_activation_revision_for_foundation_test(
                    kind="SYSTEM_COMPONENT", envelope_payload=candidate
                )
                worker.commit()
                status = "written" if not result.replayed else "replayed"
                return status, result.history.record_digest
            except V5LifecycleAuthorityError as exc:
                worker.rollback()
                return exc.code, candidate["record_envelope"]["record_digest"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(activate, candidates))

    assert [status for status, _digest in outcomes].count("written") == 1
    assert [status for status, _digest in outcomes].count(
        "v5.lifecycle.revision_conflict"
    ) == 1
    with sessions() as verify:
        rows = list(
            verify.scalars(
                sa.select(SystemComponentLifecycleRevision).order_by(
                    SystemComponentLifecycleRevision.revision
                )
            )
        )
        head = verify.get(SystemComponent, COMPONENT)
        assert [row.revision for row in rows] == [1, 2]
        assert head is not None and head.application_id == APPLICATION
        assert head.workspace_id == WORKSPACE and head.lifecycle_state == "ACTIVE"
        assert head.record_digest == rows[1].record_digest
        binding = {
            "kind": "SYSTEM_COMPONENT",
            "id": COMPONENT,
            "revision": 2,
            "digest": head.record_digest,
        }
        assert V5AuthorityService(verify).validate_exact_lifecycle_binding(
            workspace_id=WORKSPACE,
            binding=binding,
            require_current=True,
            require_active=True,
            application_id=APPLICATION,
        ).revision == 2
