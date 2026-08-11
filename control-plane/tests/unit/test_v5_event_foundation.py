"""R1 major-2 event foundation without reinterpreting V3/V4 rows."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Audit, Event, Outbox
from app.models.v4_tables import PublicPrincipal
from app.services.v4_audit import V4AuditService
from app.services.v4_event_store import (
    V4EventIntegrityError,
    V4EventStore,
    V4EventStoreError,
    validate_v4_event_row,
    validate_v4_outbox_row,
    validate_v5_event_row,
    validate_v5_outbox_row,
)


WORKSPACE = "ws_r1_event_foundation"
PRINCIPAL = "prn_r1_manifest_importer"
TRANSACTION = "txn_r1_event_foundation"
AUTHORITY = "arec_r1_event_foundation"
APPLICATION = "app_r1_event_foundation"
COMPONENT = "cmp_r1_event_foundation"
COMPONENT_REVISION = "crev_r1_event_foundation"
NOW = datetime(2026, 8, 11, 8, 0, tzinfo=timezone.utc)


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _binding(kind: str, record_id: str, revision: int, char: str) -> dict[str, object]:
    return {
        "kind": kind,
        "id": record_id,
        "revision": revision,
        "digest": _digest(char),
    }


def _manifest_context() -> dict[str, str]:
    return {
        "root_intent": "system-manifests.import",
        "workflow_owner": "manifest_import_coordinator",
        "authenticated_request_digest": _digest("8"),
        "manifest_digest": _digest("9"),
        "idempotency_key": "r1-manifest-import-0001",
        "workspace_id": WORKSPACE,
        "initiating_principal_id": PRINCIPAL,
        "initiating_principal_type": "human",
        "initiating_command_audit_ref": "audit://aud_r1_manifest_import",
    }


def _append(store: V4EventStore, **values: object) -> Event:
    return store.append_event(
        workspace_id=WORKSPACE,
        causation_id="req_r1_event_foundation",
        correlation_id="manifest_r1_event_foundation",
        actor_principal=PRINCIPAL,
        transaction_id=TRANSACTION,
        occurred_at=NOW,
        authority_receipt_id=AUTHORITY,
        **values,
    )


def test_v5_registered_route_uses_closed_major_2_envelope_and_exact_self_binding(
    sqlite_session,
) -> None:
    binding = _binding("AI_APPLICATION", APPLICATION, 1, "a")
    event = _append(
        V4EventStore(sqlite_session),
        aggregate_type="ai_application",
        aggregate_id=APPLICATION,
        event_type="application.registered",
        payload={
            "exact_previous_application_binding_or_null": None,
            "exact_application_binding": binding,
            "project_id": "project-r1",
            "slug": "r1-app",
            "lifecycle_state": "REGISTERED",
        },
    )
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == event.event_id)
    )

    assert event.contract_version == "v5"
    assert event.event_version == "2.0"
    assert event.event_contract_major == 2
    assert event.exact_subject_binding == binding
    assert event.routing_key == {
        "contract_major": 2,
        "resource_kind": "AI_APPLICATION",
        "subject_id": APPLICATION,
    }
    assert event.authority_receipt_id == AUTHORITY
    assert set(event.payload) == {
        "exact_previous_application_binding_or_null",
        "exact_application_binding",
        "project_id",
        "slug",
        "lifecycle_state",
    }
    assert outbox is not None
    assert outbox.contract_version == "v5"
    assert outbox.event_contract_major == 2
    assert outbox.channel == "v5.domain.events"
    assert set(outbox.payload) == {
        "event_id",
        "event_type",
        "event_version",
        "event_contract_major",
        "workspace_id",
        "transaction_id",
        "occurred_at",
        "actor_principal",
        "correlation_id",
        "causation_id",
        "routing_key",
        "exact_subject_binding",
        "authority_receipt_id",
        "payload",
        "payload_digest",
    }
    validate_v5_event_row(
        event,
        workspace_id=WORKSPACE,
        event_type="application.registered",
        transaction_id=TRANSACTION,
        actor_principal=PRINCIPAL,
        subject_kind="AI_APPLICATION",
        subject_id=APPLICATION,
        subject_revision=1,
        subject_digest=_digest("a"),
        authority_receipt_id=AUTHORITY,
    )
    validate_v5_outbox_row(outbox, event=event)


def test_committed_registered_producer_shape_remains_v4_byte_compatible(
    sqlite_session,
) -> None:
    event = V4EventStore(sqlite_session).append_event(
        workspace_id=WORKSPACE,
        aggregate_type="ai_application",
        aggregate_id=APPLICATION,
        event_type="application.registered",
        payload={
            "application_id": APPLICATION,
            "project_id": "project-r1",
            "slug": "r1-app",
            "lifecycle_state": "ACTIVE",
            "subject_kind": "AI_APPLICATION",
            "subject_id": APPLICATION,
            "subject_revision": 1,
            "subject_digest": _digest("a"),
            "authority_receipt_id": AUTHORITY,
        },
        causation_id="req_r1_legacy_producer",
        correlation_id=APPLICATION,
        actor_principal=PRINCIPAL,
        transaction_id=TRANSACTION,
        occurred_at=NOW,
    )
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == event.event_id)
    )

    assert event.contract_version == "v4"
    assert event.event_version == "1.0"
    assert event.event_contract_major is None
    assert event.routing_key is None
    assert event.exact_subject_binding is None
    assert event.authority_receipt_id is None
    assert outbox is not None
    assert outbox.channel == "v4.domain.events"
    assert outbox.payload["contract_version"] == "v4"
    assert outbox.payload["event_version"] == "1.0"


@pytest.mark.parametrize(
    ("aggregate_type", "aggregate_id", "event_type", "payload"),
    [
        (
            "system_component",
            COMPONENT,
            "system_component.activated",
            {
                "exact_previous_system_component_binding": _binding(
                    "SYSTEM_COMPONENT", COMPONENT, 1, "b"
                ),
                "exact_system_component_binding": _binding(
                    "SYSTEM_COMPONENT", COMPONENT, 2, "c"
                ),
                "lifecycle_state": "ACTIVE",
                "manifest_activation_context": _manifest_context(),
                "initiating_command_audit_ref": "audit://aud_r1_manifest_import",
            },
        ),
        (
            "system_component",
            COMPONENT,
            "system_component.activated",
            {
                "exact_previous_system_component_binding": _binding(
                    "SYSTEM_COMPONENT", COMPONENT, 1, "b"
                ),
                "exact_system_component_binding": _binding(
                    "SYSTEM_COMPONENT", COMPONENT, 2, "c"
                ),
                "lifecycle_state": "ACTIVE",
                "manifest_activation_context": {
                    **_manifest_context(),
                    "workflow_owner": "forged-manifest-coordinator",
                },
                "initiating_command_audit_ref": "audit://aud_r1_manifest_import",
            },
        ),
        (
            "ai_application",
            APPLICATION,
            "application.activated",
            {
                "exact_previous_application_binding": _binding(
                    "AI_APPLICATION", APPLICATION, 1, "a"
                ),
                "exact_application_binding": _binding(
                    "AI_APPLICATION", APPLICATION, 2, "b"
                ),
                "lifecycle_state": "ACTIVE",
                "manifest_activation_context": {
                    **_manifest_context(),
                    "initiating_command_audit_ref": "https://invalid/audit",
                },
                "initiating_command_audit_ref": "https://invalid/audit",
            },
        ),
    ],
    ids=["direct", "forged-context", "uri-audit-ref"],
)
def test_r1_activation_writer_routes_are_disabled_and_never_persist(
    sqlite_session,
    aggregate_type: str,
    aggregate_id: str,
    event_type: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(V4EventStoreError, match="v5.event_route_not_activated"):
        _append(
            V4EventStore(sqlite_session),
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
        )
    assert sqlite_session.scalar(select(func.count()).select_from(Event)) == 0
    assert sqlite_session.scalar(select(func.count()).select_from(Outbox)) == 0


def test_r1_rejects_component_revision_major_2_writer_before_r3_activation(
    sqlite_session,
) -> None:
    with pytest.raises(V4EventStoreError, match="v5.event_route_not_activated"):
        _append(
            V4EventStore(sqlite_session),
            aggregate_type="component_revision",
            aggregate_id=COMPONENT_REVISION,
            event_type="component_revision.recorded",
            payload={
                "exact_component_revision_binding": _binding(
                    "COMPONENT_REVISION", COMPONENT_REVISION, 1, "d"
                ),
                "exact_system_component_binding": _binding(
                    "SYSTEM_COMPONENT", COMPONENT, 2, "c"
                ),
                "component_kind": "API",
                "identity_assurance": "STRONG",
                "configuration_digest": _digest("e"),
            },
        )
    assert sqlite_session.scalar(select(func.count()).select_from(Event)) == 0
    assert sqlite_session.scalar(select(func.count()).select_from(Outbox)) == 0


def test_event_flush_failure_rolls_back_event_outbox_and_same_transaction_audit(
    sqlite_session,
    monkeypatch,
) -> None:
    audit = V4AuditService(sqlite_session, clock=lambda: NOW).record(
        workspace_id=WORKSPACE,
        actor_principal=PRINCIPAL,
        action="system-manifests.import",
        target="manifest:r1",
        params={"manifest_digest": _digest("9")},
        transaction_id=TRANSACTION,
        trace_id="trace_r1_manifest_import",
    )
    original_flush = sqlite_session.flush

    def fail_event_flush(*args, **kwargs):
        raise RuntimeError("synthetic event/outbox write failure")

    monkeypatch.setattr(sqlite_session, "flush", fail_event_flush)
    with pytest.raises(RuntimeError, match="synthetic event/outbox write failure"):
        _append(
            V4EventStore(sqlite_session),
            aggregate_type="ai_application",
            aggregate_id=APPLICATION,
            event_type="application.registered",
            payload={
                "exact_previous_application_binding_or_null": None,
                "exact_application_binding": _binding(
                    "AI_APPLICATION", APPLICATION, 1, "a"
                ),
                "project_id": "project-r1",
                "slug": "r1-app",
                "lifecycle_state": "REGISTERED",
            },
        )
    monkeypatch.setattr(sqlite_session, "flush", original_flush)
    sqlite_session.rollback()

    assert sqlite_session.get(Audit, audit.row.audit_id) is None
    assert sqlite_session.scalar(select(func.count()).select_from(Event)) == 0
    assert sqlite_session.scalar(select(func.count()).select_from(Outbox)) == 0


def test_public_principal_trust_roles_is_non_null_with_empty_default(
    sqlite_session,
) -> None:
    principal = PublicPrincipal(
        principal_id="prn_r1_trust_roles",
        workspace_id=WORKSPACE,
        principal_type="service",
        subject_digest=_digest("f"),
        audiences=[],
        project_ids=[],
        environment_ids=[],
        scopes=[],
        claims_digest=_digest("1"),
    )
    sqlite_session.add(principal)
    sqlite_session.flush()

    assert principal.trust_roles == []
    assert PublicPrincipal.__table__.c.trust_roles.nullable is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_contract_major", 2),
        (
            "routing_key",
            {
                "contract_major": 2,
                "resource_kind": "AI_APPLICATION",
                "subject_id": APPLICATION,
            },
        ),
        (
            "exact_subject_binding",
            _binding("AI_APPLICATION", APPLICATION, 1, "a"),
        ),
        ("authority_receipt_id", AUTHORITY),
    ],
)
def test_legacy_event_insert_rejects_every_major_2_envelope_field(
    sqlite_engine,
    field: str,
    value: object,
) -> None:
    values = {
        "event_id": f"evt_legacy_rebound_{field}",
        "aggregate_type": "legacy",
        "aggregate_id": f"legacy_{field}",
        "seq": 1,
        "event_type": "LEGACY_EVENT",
        "payload": {},
        "causation_id": "legacy-cause",
        "correlation_id": "legacy-correlation",
        "actor": "legacy-controller",
        "trace_id": None,
        "occurred_at": NOW,
        "created_at": NOW,
        "contract_version": None,
        field: value,
    }
    with pytest.raises(IntegrityError):
        with sqlite_engine.begin() as connection:
            connection.execute(Event.__table__.insert().values(**values))


def test_null_contract_event_cannot_bypass_check_with_complete_v5_context(
    sqlite_engine,
) -> None:
    with pytest.raises(IntegrityError):
        with sqlite_engine.begin() as connection:
            connection.execute(
                Event.__table__.insert().values(
                    event_id="evt_null_contract_complete_v5",
                    aggregate_type="ai_application",
                    aggregate_id=APPLICATION,
                    seq=1,
                    event_type="application.registered",
                    payload={},
                    causation_id="null-contract-cause",
                    correlation_id="null-contract-correlation",
                    actor="application-catalog-controller",
                    trace_id=None,
                    occurred_at=NOW,
                    created_at=NOW,
                    contract_version=None,
                    workspace_id=WORKSPACE,
                    event_version="2.0",
                    event_contract_major=2,
                    routing_key={
                        "contract_major": 2,
                        "resource_kind": "AI_APPLICATION",
                        "subject_id": APPLICATION,
                    },
                    exact_subject_binding=_binding(
                        "AI_APPLICATION", APPLICATION, 1, "a"
                    ),
                    authority_receipt_id=AUTHORITY,
                    transaction_id=TRANSACTION,
                    actor_principal=PRINCIPAL,
                    payload_digest=_digest("2"),
                )
            )


def test_legacy_outbox_insert_rejects_major_2_contract_marker(sqlite_engine) -> None:
    with pytest.raises(IntegrityError):
        with sqlite_engine.begin() as connection:
            connection.execute(
                Outbox.__table__.insert().values(
                    outbox_id="obx_legacy_rebound_major_2",
                    aggregate_id="legacy_outbox",
                    source_event_id="evt_legacy_outbox",
                    source_event_seq=1,
                    channel="legacy.domain.events",
                    event_type="LEGACY_EVENT",
                    payload={},
                    payload_digest=_digest("2"),
                    status="PENDING",
                    attempts=0,
                    created_at=NOW,
                    contract_version=None,
                    event_contract_major=2,
                )
            )


def test_null_contract_outbox_cannot_bypass_check_with_complete_v5_context(
    sqlite_engine,
) -> None:
    with pytest.raises(IntegrityError):
        with sqlite_engine.begin() as connection:
            connection.execute(
                Outbox.__table__.insert().values(
                    outbox_id="obx_null_contract_complete_v5",
                    aggregate_id=APPLICATION,
                    source_event_id="evt_null_contract_complete_v5",
                    source_event_seq=1,
                    channel="v5.domain.events",
                    event_type="application.registered",
                    payload={},
                    payload_digest=_digest("2"),
                    status="PENDING",
                    attempts=0,
                    created_at=NOW,
                    contract_version=None,
                    workspace_id=WORKSPACE,
                    aggregate_type="ai_application",
                    event_version="2.0",
                    event_contract_major=2,
                    transaction_id=TRANSACTION,
                    actor_principal=PRINCIPAL,
                )
            )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_contract_major", 2),
        (
            "routing_key",
            {
                "contract_major": 2,
                "resource_kind": "AI_APPLICATION",
                "subject_id": APPLICATION,
            },
        ),
        (
            "exact_subject_binding",
            _binding("AI_APPLICATION", APPLICATION, 1, "a"),
        ),
        ("authority_receipt_id", AUTHORITY),
    ],
)
def test_v4_validator_rejects_major_2_event_field_rebinding(
    sqlite_session,
    field: str,
    value: object,
) -> None:
    payload = {
        "application_id": APPLICATION,
        "project_id": "project-r1",
        "slug": "r1-app",
        "lifecycle_state": "ACTIVE",
        "subject_kind": "AI_APPLICATION",
        "subject_id": APPLICATION,
        "subject_revision": 1,
        "subject_digest": _digest("a"),
        "authority_receipt_id": AUTHORITY,
    }
    event = V4EventStore(sqlite_session).append_event(
        workspace_id=WORKSPACE,
        aggregate_type="ai_application",
        aggregate_id=APPLICATION,
        event_type="application.registered",
        payload=payload,
        causation_id="req_r1_v4_validator",
        correlation_id=APPLICATION,
        actor_principal=PRINCIPAL,
        transaction_id=TRANSACTION,
        occurred_at=NOW,
    )
    setattr(event, field, value)

    with pytest.raises(V4EventIntegrityError, match="v4.event_binding_mismatch"):
        validate_v4_event_row(
            event,
            workspace_id=WORKSPACE,
            event_type="application.registered",
            transaction_id=TRANSACTION,
            actor_principal=PRINCIPAL,
            subject_kind="AI_APPLICATION",
            subject_id=APPLICATION,
            subject_revision=1,
            subject_digest=_digest("a"),
            authority_receipt_id=AUTHORITY,
        )


def test_v4_outbox_validator_rejects_major_2_contract_marker(sqlite_session) -> None:
    event = V4EventStore(sqlite_session).append_event(
        workspace_id=WORKSPACE,
        aggregate_type="ai_application",
        aggregate_id=APPLICATION,
        event_type="application.registered",
        payload={
            "application_id": APPLICATION,
            "project_id": "project-r1",
            "slug": "r1-app",
            "lifecycle_state": "ACTIVE",
            "subject_kind": "AI_APPLICATION",
            "subject_id": APPLICATION,
            "subject_revision": 1,
            "subject_digest": _digest("a"),
            "authority_receipt_id": AUTHORITY,
        },
        causation_id="req_r1_v4_outbox_validator",
        correlation_id=APPLICATION,
        actor_principal=PRINCIPAL,
        transaction_id=TRANSACTION,
        occurred_at=NOW,
    )
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == event.event_id)
    )
    assert outbox is not None
    outbox.event_contract_major = 2

    with pytest.raises(V4EventIntegrityError, match="v4.outbox_binding_mismatch"):
        validate_v4_outbox_row(outbox, event=event)
