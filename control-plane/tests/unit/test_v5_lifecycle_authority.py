from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.models import Event, Outbox
from app.models.v4_tables import AuthorityReceipt, ControllerRegistration
from app.models.v5_tables import (
    AIApplication,
    AIApplicationLifecycleRevision,
    SystemComponentLifecycleRevision,
)
from app.services.v4_audit import V4AuditService
from app.services.v4_event_store import V4EventStore
from app.services.v4_event_store import (
    V4EventIntegrityError,
    V5_DOMAIN_EVENT_CHANNEL,
    validate_v5_event_row,
    v5_outbox_envelope,
)
from app.services.v5_authority import (
    V5_CATALOG_OWNER,
    V5AuthorityError,
    V5AuthorityService,
    build_v5_controller_registration_record,
)
from app.services.v5_lifecycle_authority import (
    V5LifecycleAuthorityError,
    V5LifecycleAuthorityService,
)
from app.utils.v5_integrity import V5_HASH_RULE, v5_record_digest
from app.utils.v4_integrity import canonical_digest, record_digest


NOW = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_lifecycle_unit"
PROJECT = "proj_lifecycle_unit"
PRINCIPAL = "prn_lifecycle_unit"
APPLICATION = "app_lifecycle_unit"
COMPONENT = "cmp_lifecycle_unit"
CONTROLLER = "prn_lifecycle_controller"


def _seed_controller(session) -> None:
    service_identity_digest = canonical_digest(
        {
            "schema_version": "1.0",
            "workspace_id": WORKSPACE,
            "owner": V5_CATALOG_OWNER,
            "controller_principal": CONTROLLER,
            "principal_type": "CONTROLLER_SERVICE",
            "service": "caseloop-control-plane",
        }
    )
    audit = V4AuditService(session).record(
        workspace_id=WORKSPACE,
        actor_principal=PRINCIPAL,
        action="controllers.register",
        target="creg_lifecycle_unit",
        params={
            "owner": V5_CATALOG_OWNER,
            "service_identity_digest": service_identity_digest,
        },
        transaction_id="txn_lifecycle_controller",
        evidence_refs={
            "owner": V5_CATALOG_OWNER,
            "controller_registration_id": "creg_lifecycle_unit",
            "controller_principal": CONTROLLER,
        },
        occurred_at=NOW - timedelta(minutes=1),
    )
    built = build_v5_controller_registration_record(
        controller_registration_id="creg_lifecycle_unit",
        workspace_id=WORKSPACE,
        owner=V5_CATALOG_OWNER,
        controller_principal=CONTROLLER,
        allowed_commands=["applications.register", "applications.activate"],
        service_identity_digest=service_identity_digest,
        registered_by_human_principal=PRINCIPAL,
        registration_audit_ref=audit.audit_ref,
        valid_from=NOW - timedelta(minutes=1),
        registered_at=NOW - timedelta(minutes=1),
    )
    session.add(ControllerRegistration(**built.row_values))
    session.flush()


def _wire_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _application_envelope(
    *, revision: int, state: str, previous: dict | None = None
) -> dict:
    envelope = {
        "application_id": APPLICATION,
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "slug": "lifecycle-unit",
        "display_name": "Lifecycle unit",
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
            "recorded_at": _wire_time(NOW + timedelta(seconds=revision)),
            "immutable": True,
            "hash_rule": V5_HASH_RULE,
            "record_digest": "",
            "authority_receipt_id": f"ar_app_lifecycle_{revision}",
        },
    }
    envelope["record_envelope"]["record_digest"] = v5_record_digest(envelope)
    return envelope


def _component_envelope(
    *, revision: int, state: str, previous: dict | None = None
) -> dict:
    envelope = {
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
        "dataset_role": None,
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
            "recorded_at": _wire_time(NOW + timedelta(seconds=10 + revision)),
            "immutable": True,
            "hash_rule": V5_HASH_RULE,
            "record_digest": "",
            "authority_receipt_id": f"ar_cmp_lifecycle_{revision}",
        },
    }
    envelope["record_envelope"]["record_digest"] = v5_record_digest(envelope)
    return envelope


def _binding(kind: str, subject_id: str, envelope: dict) -> dict:
    record_envelope = envelope["record_envelope"]
    return {
        "kind": kind,
        "id": subject_id,
        "revision": record_envelope["revision"],
        "digest": record_envelope["record_digest"],
    }


def _rehash(envelope: dict) -> dict:
    envelope["record_envelope"]["record_digest"] = v5_record_digest(envelope)
    return envelope


def _manifest_context(*, audit_ref: str) -> dict[str, str]:
    return {
        "root_intent": "system-manifests.import",
        "workflow_owner": "manifest_import_coordinator",
        "authenticated_request_digest": "sha256:" + "8" * 64,
        "manifest_digest": "sha256:" + "9" * 64,
        "idempotency_key": "lifecycle-unit-manifest-import",
        "workspace_id": WORKSPACE,
        "initiating_principal_id": PRINCIPAL,
        "initiating_principal_type": "human",
        "initiating_command_audit_ref": audit_ref,
    }


def _persist_activation_event_foundation(
    session,
    *,
    payload: dict,
    exact: dict,
    receipt_id: str,
    transaction_id: str,
    at: datetime,
) -> Event:
    """Persist the frozen envelope without activating a production writer."""

    event = Event(
        event_id="evt_lifecycle_activation_foundation",
        aggregate_type="ai_application",
        aggregate_id=APPLICATION,
        seq=1,
        event_type="application.activated",
        payload=payload,
        causation_id="req_lifecycle_activated",
        correlation_id=APPLICATION,
        actor=V5_CATALOG_OWNER,
        trace_id=None,
        occurred_at=at,
        created_at=at,
        contract_version="v5",
        workspace_id=WORKSPACE,
        event_version="2.0",
        event_contract_major=2,
        routing_key={
            "contract_major": 2,
            "resource_kind": "AI_APPLICATION",
            "subject_id": APPLICATION,
        },
        exact_subject_binding=exact,
        authority_receipt_id=receipt_id,
        transaction_id=transaction_id,
        actor_principal=CONTROLLER,
        payload_digest=canonical_digest(payload),
    )
    envelope = v5_outbox_envelope(event)
    outbox = Outbox(
        outbox_id="ob_lifecycle_activation_foundation",
        aggregate_id=APPLICATION,
        source_event_id=event.event_id,
        source_event_seq=1,
        channel=V5_DOMAIN_EVENT_CHANNEL,
        event_type=event.event_type,
        payload=envelope,
        payload_digest=canonical_digest(envelope),
        status="PENDING",
        attempts=0,
        created_at=at,
        contract_version="v5",
        workspace_id=WORKSPACE,
        aggregate_type="ai_application",
        event_version="2.0",
        event_contract_major=2,
        transaction_id=transaction_id,
        actor_principal=CONTROLLER,
    )
    session.add_all([event, outbox])
    session.flush()
    return event


def _register_and_activate_application(session) -> tuple[dict, dict]:
    lifecycle = V5LifecycleAuthorityService(session)
    registered = _application_envelope(revision=1, state="REGISTERED")
    lifecycle.append_registration_revision(
        kind="AI_APPLICATION", envelope_payload=registered
    )
    activated = _application_envelope(
        revision=2,
        state="ACTIVE",
        previous=_binding("AI_APPLICATION", APPLICATION, registered),
    )
    lifecycle._append_activation_revision_for_foundation_test(
        kind="AI_APPLICATION", envelope_payload=activated
    )
    return registered, activated


def test_registration_activation_are_append_only_and_historical_replay_resolves(
    sqlite_session,
) -> None:
    registered, activated = _register_and_activate_application(sqlite_session)
    authority = V5AuthorityService(sqlite_session)

    rev1 = authority.validate_exact_lifecycle_binding(
        workspace_id=WORKSPACE,
        binding=_binding("AI_APPLICATION", APPLICATION, registered),
    )
    rev2 = authority.validate_exact_lifecycle_binding(
        workspace_id=WORKSPACE,
        binding=_binding("AI_APPLICATION", APPLICATION, activated),
        require_current=True,
        require_active=True,
    )
    head = sqlite_session.get(AIApplication, APPLICATION)

    assert rev1.revision == 1 and rev1.lifecycle_state == "REGISTERED"
    assert rev2.revision == 2 and rev2.lifecycle_state == "ACTIVE"
    assert head is not None and head.revision == 2
    assert head.record_digest == activated["record_envelope"]["record_digest"]
    assert (
        sqlite_session.scalar(
            sa.select(sa.func.count()).select_from(AIApplicationLifecycleRevision)
        )
        == 2
    )

    replay = V5LifecycleAuthorityService(
        sqlite_session
    )._append_activation_revision_for_foundation_test(
        kind="AI_APPLICATION", envelope_payload=activated
    )
    assert replay.replayed is True
    assert (
        sqlite_session.scalar(
            sa.select(sa.func.count()).select_from(AIApplicationLifecycleRevision)
        )
        == 2
    )


def test_direct_activation_append_is_denied_without_projection_or_history_change(
    sqlite_session,
) -> None:
    lifecycle = V5LifecycleAuthorityService(sqlite_session)
    registered = _application_envelope(revision=1, state="REGISTERED")
    lifecycle.append_registration_revision(
        kind="AI_APPLICATION", envelope_payload=registered
    )
    activated = _application_envelope(
        revision=2,
        state="ACTIVE",
        previous=_binding("AI_APPLICATION", APPLICATION, registered),
    )

    with pytest.raises(V5LifecycleAuthorityError) as exc:
        lifecycle.append_activation_revision(
            kind="AI_APPLICATION", envelope_payload=activated
        )
    assert exc.value.code == "v5.lifecycle.composition_required"

    head = sqlite_session.get(AIApplication, APPLICATION)
    assert head is not None
    assert head.revision == 1 and head.lifecycle_state == "REGISTERED"
    assert head.envelope_payload == registered
    rows = list(
        sqlite_session.scalars(
            sa.select(AIApplicationLifecycleRevision).order_by(
                AIApplicationLifecycleRevision.revision
            )
        )
    )
    assert [(row.revision, row.lifecycle_state) for row in rows] == [
        (1, "REGISTERED")
    ]


def test_component_binding_requires_current_authoritative_active_revision(
    sqlite_session,
) -> None:
    _register_and_activate_application(sqlite_session)
    lifecycle = V5LifecycleAuthorityService(sqlite_session)
    registered = _component_envelope(revision=1, state="REGISTERED")
    lifecycle.append_registration_revision(
        kind="SYSTEM_COMPONENT", envelope_payload=registered
    )
    activated = _component_envelope(
        revision=2,
        state="ACTIVE",
        previous=_binding("SYSTEM_COMPONENT", COMPONENT, registered),
    )
    lifecycle._append_activation_revision_for_foundation_test(
        kind="SYSTEM_COMPONENT", envelope_payload=activated
    )
    authority = V5AuthorityService(sqlite_session)

    row = authority.validate_exact_lifecycle_binding(
        workspace_id=WORKSPACE,
        binding=_binding("SYSTEM_COMPONENT", COMPONENT, activated),
        require_current=True,
        require_active=True,
        application_id=APPLICATION,
    )
    assert row.application_id == APPLICATION
    with pytest.raises(V5AuthorityError) as exc:
        authority.validate_exact_lifecycle_binding(
            workspace_id=WORKSPACE,
            binding=_binding("SYSTEM_COMPONENT", COMPONENT, registered),
            require_current=True,
            require_active=True,
            application_id=APPLICATION,
        )
    assert exc.value.code == "v5.authority.lifecycle_binding_not_current"
    assert (
        sqlite_session.scalar(
            sa.select(sa.func.count()).select_from(
                SystemComponentLifecycleRevision
            )
        )
        == 2
    )


def test_projection_or_history_tamper_fails_closed(sqlite_session) -> None:
    _registered, activated = _register_and_activate_application(sqlite_session)
    authority = V5AuthorityService(sqlite_session)
    exact = _binding("AI_APPLICATION", APPLICATION, activated)

    sqlite_session.execute(
        sa.update(AIApplication)
        .where(AIApplication.application_id == APPLICATION)
        .values(lifecycle_state="ARCHIVED")
    )
    with pytest.raises(V5AuthorityError) as exc:
        authority.validate_exact_lifecycle_binding(
            workspace_id=WORKSPACE, binding=exact
        )
    assert exc.value.code == "v5.authority.lifecycle_current_head_binding_mismatch"

    sqlite_session.rollback()
    _register_and_activate_application(sqlite_session)
    sqlite_session.execute(
        sa.update(AIApplicationLifecycleRevision)
        .where(
            AIApplicationLifecycleRevision.workspace_id == WORKSPACE,
            AIApplicationLifecycleRevision.application_id == APPLICATION,
            AIApplicationLifecycleRevision.revision == 1,
        )
        .values(record_digest="sha256:" + "f" * 64)
    )
    with pytest.raises(V5AuthorityError) as exc:
        authority.validate_exact_lifecycle_binding(
            workspace_id=WORKSPACE,
            binding=_binding(
                "AI_APPLICATION",
                APPLICATION,
                _application_envelope(revision=1, state="REGISTERED"),
            ),
        )
    assert exc.value.code in {
        "v5.authority.lifecycle_history_binding_mismatch",
        "v5.authority.lifecycle_previous_binding_missing",
    }


def test_history_rows_reject_orm_update_and_delete(sqlite_session) -> None:
    _register_and_activate_application(sqlite_session)
    sqlite_session.commit()
    rev1 = sqlite_session.get(
        AIApplicationLifecycleRevision, (WORKSPACE, APPLICATION, 1)
    )
    assert rev1 is not None
    rev1.lifecycle_state = "ACTIVE"
    with pytest.raises(RuntimeError, match="immutable_record_update_forbidden"):
        sqlite_session.flush()
    sqlite_session.rollback()

    rev1 = sqlite_session.get(
        AIApplicationLifecycleRevision, (WORKSPACE, APPLICATION, 1)
    )
    assert rev1 is not None
    sqlite_session.delete(rev1)
    with pytest.raises(RuntimeError, match="immutable_record_update_forbidden"):
        sqlite_session.flush()
    sqlite_session.rollback()


def test_write_path_rejects_unknown_dual_previous_and_bad_nested_envelope(
    sqlite_session,
) -> None:
    lifecycle = V5LifecycleAuthorityService(sqlite_session)
    for mutate, code in (
        (
            lambda value: value.update({"attacker_extra": "forbidden"}),
            "v5.lifecycle.envelope_fields_mismatch",
        ),
        (
            lambda value: value.update(
                {"exact_previous_application_binding": None}
            ),
            "v5.lifecycle.envelope_fields_mismatch",
        ),
        (
            lambda value: value["record_envelope"].update(
                {"attacker_extra": "forbidden"}
            ),
            "v5.lifecycle.envelope_invalid",
        ),
        (
            lambda value: value["record_envelope"].update(
                {"schema_version": "2.1"}
            ),
            "v5.lifecycle.envelope_invalid",
        ),
    ):
        candidate = _application_envelope(revision=1, state="REGISTERED")
        mutate(candidate)
        _rehash(candidate)
        with pytest.raises(V5LifecycleAuthorityError) as exc:
            lifecycle.append_registration_revision(
                kind="AI_APPLICATION", envelope_payload=candidate
            )
        assert exc.value.code == code

    registered = _application_envelope(revision=1, state="REGISTERED")
    lifecycle.append_registration_revision(
        kind="AI_APPLICATION", envelope_payload=registered
    )
    for mutate, code in (
        (
            lambda value: value.update({"attacker_extra": "forbidden"}),
            "v5.lifecycle.envelope_fields_mismatch",
        ),
        (
            lambda value: value.update(
                {"exact_previous_application_binding_or_null": None}
            ),
            "v5.lifecycle.envelope_fields_mismatch",
        ),
        (
            lambda value: value["record_envelope"].update(
                {"schema_version": "2.1"}
            ),
            "v5.lifecycle.envelope_invalid",
        ),
    ):
        candidate = _application_envelope(
            revision=2,
            state="ACTIVE",
            previous=_binding("AI_APPLICATION", APPLICATION, registered),
        )
        mutate(candidate)
        _rehash(candidate)
        with pytest.raises(V5LifecycleAuthorityError) as exc:
            lifecycle._append_activation_revision_for_foundation_test(
                kind="AI_APPLICATION", envelope_payload=candidate
            )
        assert exc.value.code == code


def test_replay_rejects_synchronized_rehashed_closed_shape_tamper(
    sqlite_session,
) -> None:
    _registered, activated = _register_and_activate_application(sqlite_session)
    sqlite_session.commit()
    authority = V5AuthorityService(sqlite_session)

    for mutate, code in (
        (
            lambda value: value.update({"attacker_extra": "forbidden"}),
            "v5.authority.lifecycle_history_fields_invalid",
        ),
        (
            lambda value: value.update(
                {"exact_previous_application_binding_or_null": None}
            ),
            "v5.authority.lifecycle_history_fields_invalid",
        ),
        (
            lambda value: value["record_envelope"].update(
                {"schema_version": "2.1"}
            ),
            "v5.authority.lifecycle_history_integrity_invalid",
        ),
    ):
        tampered = copy.deepcopy(activated)
        mutate(tampered)
        _rehash(tampered)
        digest = tampered["record_envelope"]["record_digest"]
        sqlite_session.execute(
            sa.update(AIApplicationLifecycleRevision)
            .where(
                AIApplicationLifecycleRevision.workspace_id == WORKSPACE,
                AIApplicationLifecycleRevision.application_id == APPLICATION,
                AIApplicationLifecycleRevision.revision == 2,
            )
            .values(envelope_payload=tampered, record_digest=digest)
        )
        sqlite_session.execute(
            sa.update(AIApplication)
            .where(AIApplication.application_id == APPLICATION)
            .values(envelope_payload=tampered, record_digest=digest)
        )
        sqlite_session.expire_all()
        with pytest.raises(V5AuthorityError) as exc:
            authority.validate_exact_lifecycle_binding(
                workspace_id=WORKSPACE,
                binding={
                    "kind": "AI_APPLICATION",
                    "id": APPLICATION,
                    "revision": 2,
                    "digest": digest,
                },
            )
        assert exc.value.code == code
        sqlite_session.rollback()


def test_component_closed_shape_allows_only_optional_dataset_role(
    sqlite_session,
) -> None:
    _register_and_activate_application(sqlite_session)
    lifecycle = V5LifecycleAuthorityService(sqlite_session)

    registered = _component_envelope(revision=1, state="REGISTERED")
    registered.pop("dataset_role")
    _rehash(registered)
    for extra in (
        {"attacker_extra": "forbidden"},
        {"exact_previous_system_component_binding": None},
    ):
        candidate = copy.deepcopy(registered)
        candidate.update(extra)
        _rehash(candidate)
        with pytest.raises(V5LifecycleAuthorityError) as exc:
            lifecycle.append_registration_revision(
                kind="SYSTEM_COMPONENT", envelope_payload=candidate
            )
        assert exc.value.code == "v5.lifecycle.envelope_fields_mismatch"
    lifecycle.append_registration_revision(
        kind="SYSTEM_COMPONENT", envelope_payload=registered
    )

    activated = _component_envelope(
        revision=2,
        state="ACTIVE",
        previous=_binding("SYSTEM_COMPONENT", COMPONENT, registered),
    )
    activated.pop("dataset_role")
    _rehash(activated)
    for extra in (
        {"attacker_extra": "forbidden"},
        {"exact_previous_system_component_binding_or_null": None},
    ):
        candidate = copy.deepcopy(activated)
        candidate.update(extra)
        _rehash(candidate)
        with pytest.raises(V5LifecycleAuthorityError) as exc:
            lifecycle._append_activation_revision_for_foundation_test(
                kind="SYSTEM_COMPONENT", envelope_payload=candidate
            )
        assert exc.value.code == "v5.lifecycle.envelope_fields_mismatch"
    result = lifecycle._append_activation_revision_for_foundation_test(
        kind="SYSTEM_COMPONENT", envelope_payload=activated
    )
    assert result.history.lifecycle_state == "ACTIVE"

def test_wrong_previous_and_outer_transaction_rollback_leave_no_history(
    sqlite_session,
) -> None:
    lifecycle = V5LifecycleAuthorityService(sqlite_session)
    registered = _application_envelope(revision=1, state="REGISTERED")
    lifecycle.append_registration_revision(
        kind="AI_APPLICATION", envelope_payload=registered
    )
    wrong = _application_envelope(
        revision=2,
        state="ACTIVE",
        previous={
            **_binding("AI_APPLICATION", APPLICATION, registered),
            "digest": "sha256:" + "0" * 64,
        },
    )
    with pytest.raises(V5LifecycleAuthorityError) as exc:
        lifecycle._append_activation_revision_for_foundation_test(
            kind="AI_APPLICATION", envelope_payload=wrong
        )
    assert exc.value.code == "v5.lifecycle.activation_previous_invalid"

    sqlite_session.rollback()
    assert sqlite_session.get(AIApplication, APPLICATION) is None
    assert (
        sqlite_session.scalar(
            sa.select(sa.func.count()).select_from(AIApplicationLifecycleRevision)
        )
        == 0
    )


def test_major2_revision_1_receipt_replays_after_head_is_revision_2_and_tamper_fails(
    sqlite_session,
) -> None:
    _seed_controller(sqlite_session)
    registered = _application_envelope(revision=1, state="REGISTERED")
    lifecycle = V5LifecycleAuthorityService(sqlite_session)
    lifecycle.append_registration_revision(
        kind="AI_APPLICATION", envelope_payload=registered
    )
    exact = _binding("AI_APPLICATION", APPLICATION, registered)
    transaction_id = "txn_lifecycle_registered"
    receipt_id = registered["record_envelope"]["authority_receipt_id"]
    event = V4EventStore(sqlite_session).append_event(
        workspace_id=WORKSPACE,
        aggregate_type="ai_application",
        aggregate_id=APPLICATION,
        event_type="application.registered",
        payload={
            "exact_previous_application_binding_or_null": None,
            "exact_application_binding": exact,
            "project_id": PROJECT,
            "slug": "lifecycle-unit",
            "lifecycle_state": "REGISTERED",
        },
        causation_id="req_lifecycle_registered",
        correlation_id=APPLICATION,
        actor_principal=CONTROLLER,
        transaction_id=transaction_id,
        occurred_at=NOW + timedelta(seconds=1),
        authority_receipt_id=receipt_id,
    )
    audit = V4AuditService(sqlite_session).record(
        workspace_id=WORKSPACE,
        actor_principal=CONTROLLER,
        action="controller.application.registered",
        target=APPLICATION,
        params={"command": "applications.register"},
        transaction_id=transaction_id,
        evidence_refs={
            "subject_kind": "AI_APPLICATION",
            "subject_id": APPLICATION,
            "subject_revision": 1,
            "subject_digest": exact["digest"],
            "event_id": event.event_id,
        },
        occurred_at=NOW + timedelta(seconds=1),
    )
    authority = V5AuthorityService(sqlite_session)
    resolved = authority.resolve_controller(
        workspace_id=WORKSPACE,
        subject_kind="AI_APPLICATION",
        command="applications.register",
        event_type="application.registered",
        recorded_at=NOW + timedelta(seconds=1),
    )
    receipt = authority.record_receipt(
        resolved=resolved,
        authority_receipt_id=receipt_id,
        workspace_id=WORKSPACE,
        subject_id=APPLICATION,
        subject_revision=1,
        subject_digest=exact["digest"],
        event_id=event.event_id,
        transaction_id=transaction_id,
        audit_ref=audit.audit_ref,
        recorded_at=NOW + timedelta(seconds=1),
        lifecycle_history=True,
    )
    assert set(receipt.receipt_payload) == {
        "schema_version",
        "authority_receipt_id",
        "workspace_id",
        "controller_registration",
        "subject",
        "owner",
        "controller_principal",
        "command",
        "source_event_id",
        "transaction_id",
        "audit_ref",
        "recorded_at",
        "immutable",
        "hash_rule",
        "authority_receipt_digest",
    }
    assert receipt.receipt_payload["source_event_id"] == event.event_id

    activated = _application_envelope(
        revision=2,
        state="ACTIVE",
        previous=exact,
    )
    lifecycle._append_activation_revision_for_foundation_test(
        kind="AI_APPLICATION", envelope_payload=activated
    )
    replayed = authority.validate_receipt_binding(
        authority_receipt_id=receipt_id,
        workspace_id=WORKSPACE,
        subject_kind="AI_APPLICATION",
        subject_id=APPLICATION,
        subject_revision=1,
        subject_digest=exact["digest"],
        lifecycle_history=True,
    )
    assert replayed.authority_receipt_id == receipt_id

    with pytest.raises(V5AuthorityError) as exc:
        authority.validate_receipt_binding(
            authority_receipt_id=receipt_id,
            workspace_id=WORKSPACE,
            subject_kind="AI_APPLICATION",
            subject_id=APPLICATION,
            subject_revision=1,
            subject_digest=exact["digest"],
            lifecycle_history=False,
        )
    assert exc.value.code == "v5.authority.lifecycle_mode_mismatch"

    sqlite_session.commit()
    sqlite_session.execute(
        sa.update(Event)
        .where(Event.event_id == event.event_id)
        .values(payload={"tampered": True})
    )
    sqlite_session.expire_all()
    with pytest.raises(V5AuthorityError) as exc:
        authority.validate_receipt_binding(
            authority_receipt_id=receipt_id,
            workspace_id=WORKSPACE,
            subject_kind="AI_APPLICATION",
            subject_id=APPLICATION,
            subject_revision=1,
            subject_digest=exact["digest"],
            lifecycle_history=True,
        )
    assert exc.value.code == "v5.authority.controller_chain_binding_mismatch"
    sqlite_session.rollback()

    sqlite_session.execute(
        sa.update(AuthorityReceipt)
        .where(AuthorityReceipt.authority_receipt_id == receipt_id)
        .values(receipt_payload={"tampered": True})
    )
    sqlite_session.expire_all()
    with pytest.raises(V5AuthorityError) as exc:
        authority.validate_receipt_binding(
            authority_receipt_id=receipt_id,
            workspace_id=WORKSPACE,
            subject_kind="AI_APPLICATION",
            subject_id=APPLICATION,
            subject_revision=1,
            subject_digest=exact["digest"],
            lifecycle_history=True,
        )
    assert exc.value.code == "v5.authority.receipt_integrity_invalid"
    sqlite_session.rollback()

    outbox_id = sqlite_session.scalar(
        sa.select(Outbox.outbox_id).where(Outbox.source_event_id == event.event_id)
    )
    assert outbox_id is not None
    sqlite_session.execute(
        sa.update(Outbox)
        .where(Outbox.outbox_id == outbox_id)
        .values(payload={"tampered": True})
    )
    sqlite_session.expire_all()
    with pytest.raises(V5AuthorityError) as exc:
        authority.validate_receipt_binding(
            authority_receipt_id=receipt_id,
            workspace_id=WORKSPACE,
            subject_kind="AI_APPLICATION",
            subject_id=APPLICATION,
            subject_revision=1,
            subject_digest=exact["digest"],
            lifecycle_history=True,
        )
    assert exc.value.code == "v5.authority.controller_chain_binding_mismatch"


def test_lifecycle_history_mode_rejects_a_persisted_v4_event(sqlite_session) -> None:
    payload = {"legacy": True}
    legacy = Event(
        event_id="evt_lifecycle_legacy",
        aggregate_type="ai_application",
        aggregate_id=APPLICATION,
        seq=1,
        event_type="application.registered",
        payload=payload,
        causation_id="req_legacy",
        correlation_id=APPLICATION,
        actor=V5_CATALOG_OWNER,
        trace_id=None,
        occurred_at=NOW,
        created_at=NOW,
        contract_version="v4",
        workspace_id=WORKSPACE,
        event_version="1.0",
        event_contract_major=None,
        routing_key=None,
        exact_subject_binding=None,
        authority_receipt_id=None,
        transaction_id="txn_legacy",
        actor_principal=CONTROLLER,
        payload_digest=canonical_digest(payload),
    )
    sqlite_session.add(legacy)
    sqlite_session.flush()

    with pytest.raises(V5AuthorityError) as exc:
        V5AuthorityService(sqlite_session)._validate_lifecycle_authority_mode(
            kind="AI_APPLICATION",
            event_id=legacy.event_id,
            lifecycle_history=True,
        )
    assert exc.value.code == "v5.authority.lifecycle_mode_mismatch"


def test_revision_2_activation_receipt_keeps_context_in_event_not_subject_history(
    sqlite_session,
) -> None:
    _seed_controller(sqlite_session)
    registered, activated = _register_and_activate_application(sqlite_session)
    assert "manifest_activation_context" not in activated
    assert "initiating_command_audit_ref" not in activated

    transaction_id = "txn_lifecycle_activated"
    at = NOW + timedelta(seconds=2)
    initiating_audit = V4AuditService(sqlite_session).record(
        workspace_id=WORKSPACE,
        actor_principal=PRINCIPAL,
        action="system-manifests.import",
        target="manifest_lifecycle_unit",
        params={
            "authenticated_request_digest": "sha256:" + "8" * 64,
            "manifest_digest": "sha256:" + "9" * 64,
        },
        transaction_id=transaction_id,
        evidence_refs={"application_id": APPLICATION},
        occurred_at=at,
    )
    context = _manifest_context(audit_ref=initiating_audit.audit_ref)
    previous = _binding("AI_APPLICATION", APPLICATION, registered)
    exact = _binding("AI_APPLICATION", APPLICATION, activated)
    receipt_id = activated["record_envelope"]["authority_receipt_id"]

    event_payload = {
        "exact_previous_application_binding": previous,
        "exact_application_binding": exact,
        "lifecycle_state": "ACTIVE",
        "manifest_activation_context": context,
        "initiating_command_audit_ref": initiating_audit.audit_ref,
    }
    event = _persist_activation_event_foundation(
        sqlite_session,
        payload=event_payload,
        exact=exact,
        receipt_id=receipt_id,
        transaction_id=transaction_id,
        at=at,
    )
    mismatched_context = dict(context)
    mismatched_context["initiating_command_audit_ref"] = "audit://aud_wrong"
    event.payload = {
        **event_payload,
        "manifest_activation_context": mismatched_context,
    }
    event.payload_digest = canonical_digest(event.payload)
    with pytest.raises(V4EventIntegrityError, match="payload_binding_mismatch"):
        validate_v5_event_row(
            event,
            workspace_id=WORKSPACE,
            event_type="application.activated",
            transaction_id=transaction_id,
            actor_principal=CONTROLLER,
            subject_kind="AI_APPLICATION",
            subject_id=APPLICATION,
            subject_revision=2,
            subject_digest=exact["digest"],
            authority_receipt_id=receipt_id,
        )
    event.payload = event_payload
    event.payload_digest = canonical_digest(event_payload)
    sqlite_session.flush()
    controller_audit = V4AuditService(sqlite_session).record(
        workspace_id=WORKSPACE,
        actor_principal=CONTROLLER,
        action="controller.application.activated",
        target=APPLICATION,
        params={"command": "applications.activate"},
        transaction_id=transaction_id,
        evidence_refs={
            "subject_kind": "AI_APPLICATION",
            "subject_id": APPLICATION,
            "subject_revision": 2,
            "subject_digest": exact["digest"],
            "event_id": event.event_id,
        },
        occurred_at=at,
    )
    authority = V5AuthorityService(sqlite_session)
    resolved = authority.resolve_controller(
        workspace_id=WORKSPACE,
        subject_kind="AI_APPLICATION",
        command="applications.activate",
        event_type="application.activated",
        recorded_at=at,
    )
    receipt = authority.record_receipt(
        resolved=resolved,
        authority_receipt_id=receipt_id,
        workspace_id=WORKSPACE,
        subject_id=APPLICATION,
        subject_revision=2,
        subject_digest=exact["digest"],
        event_id=event.event_id,
        transaction_id=transaction_id,
        audit_ref=controller_audit.audit_ref,
        recorded_at=at,
        lifecycle_history=True,
    )
    assert receipt.receipt_payload["controller_registration"]["contract_major"] == 1
    assert authority.validate_receipt_binding(
        authority_receipt_id=receipt_id,
        workspace_id=WORKSPACE,
        subject_kind="AI_APPLICATION",
        subject_id=APPLICATION,
        subject_revision=2,
        subject_digest=exact["digest"],
        lifecycle_history=True,
    ).authority_receipt_id == receipt_id

    sqlite_session.commit()
    baseline_payload = copy.deepcopy(receipt.receipt_payload)
    for contract_major in (None, 2):
        tampered = copy.deepcopy(baseline_payload)
        if contract_major is None:
            tampered["controller_registration"].pop("contract_major")
        else:
            tampered["controller_registration"]["contract_major"] = contract_major
        digest = record_digest(
            tampered, self_digest_field="authority_receipt_digest"
        )
        tampered["authority_receipt_digest"] = digest
        sqlite_session.execute(
            sa.update(AuthorityReceipt)
            .where(AuthorityReceipt.authority_receipt_id == receipt_id)
            .values(receipt_payload=tampered, authority_receipt_digest=digest)
        )
        sqlite_session.expire_all()
        with pytest.raises(V5AuthorityError) as exc:
            authority.validate_receipt_binding(
                authority_receipt_id=receipt_id,
                workspace_id=WORKSPACE,
                subject_kind="AI_APPLICATION",
                subject_id=APPLICATION,
                subject_revision=2,
                subject_digest=exact["digest"],
                lifecycle_history=True,
            )
        assert exc.value.code == "v5.authority.receipt_projection_binding_mismatch"
        sqlite_session.rollback()
