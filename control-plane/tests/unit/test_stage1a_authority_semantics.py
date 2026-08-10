"""Coordinated-reseal attacks against the Stage 1A authority/event graph."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm.attributes import set_committed_value

from app.models import Audit, Event, Outbox
from app.models.v4_tables import AuthorityReceipt, ControllerRegistration
from app.services.authority import AuthorityError, AuthorityService
from app.services.v4_event_store import v4_outbox_envelope
from app.utils.v4_integrity import canonical_digest, record_digest
from tests.unit.test_stage1a_signal_intake import (
    CONTRACTS,
    NOW,
    _seed_committed_signal_receipt,
    _wire_time,
)


def _receipts(sqlite_session) -> dict[str, AuthorityReceipt]:
    _seed_committed_signal_receipt(sqlite_session)
    return {
        row.event_type: row
        for row in sqlite_session.scalars(select(AuthorityReceipt)).all()
    }


def _reject(sqlite_session, receipt: AuthorityReceipt) -> None:
    with pytest.raises(AuthorityError):
        AuthorityService(
            sqlite_session, contracts_root=CONTRACTS
        ).validate_receipt_binding(
            authority_receipt_id=receipt.authority_receipt_id,
            workspace_id=receipt.workspace_id,
            subject_kind=receipt.subject_kind,
            subject_id=receipt.subject_id,
            subject_revision=receipt.subject_revision,
            subject_digest=receipt.subject_digest,
        )


def _reseal_audit(row: Audit) -> None:
    payload = {
        "schema_version": "1.0",
        "audit_id": row.audit_id,
        "workspace_id": row.workspace_id,
        "actor_principal": row.actor_principal,
        "action": row.action,
        "target": row.target,
        "params_digest": row.params_digest,
        "result": row.result,
        "error_code": row.error_code,
        "trace_id": row.trace_id,
        "transaction_id": row.transaction_id,
        "evidence_refs": row.evidence_refs,
        "recorded_at": _wire_time(row.ts),
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/audit_digest)",
        "audit_digest": "",
    }
    set_committed_value(
        row,
        "audit_digest",
        record_digest(payload, self_digest_field="audit_digest"),
    )


def _reseal_receipt(row: AuthorityReceipt) -> None:
    payload = {
        **row.receipt_payload,
        "controller_registration": {
            **row.receipt_payload["controller_registration"],
            "digest": row.controller_registration_digest,
        },
        "transaction_id": row.transaction_id,
        "recorded_at": _wire_time(row.recorded_at),
        "authority_receipt_digest": "",
    }
    digest = record_digest(payload, self_digest_field="authority_receipt_digest")
    payload["authority_receipt_digest"] = digest
    set_committed_value(row, "receipt_payload", payload)
    set_committed_value(row, "authority_receipt_digest", digest)


def _reseal_registration(row: ControllerRegistration) -> None:
    payload = {
        **row.registration_payload,
        "state": row.state,
        "valid_from": _wire_time(row.valid_from),
        "expires_at": _wire_time(row.expires_at) if row.expires_at else None,
        "registered_at": _wire_time(row.registered_at),
        "registration_digest": "",
    }
    digest = record_digest(payload, self_digest_field="registration_digest")
    payload["registration_digest"] = digest
    set_committed_value(row, "registration_payload", payload)
    set_committed_value(row, "registration_digest", digest)


def _bind_registration_digest(
    registration: ControllerRegistration, receipt: AuthorityReceipt
) -> None:
    set_committed_value(
        receipt, "controller_registration_digest", registration.registration_digest
    )
    _reseal_receipt(receipt)


def _reseal_event_and_outbox(sqlite_session, event: Event) -> None:
    set_committed_value(event, "payload_digest", canonical_digest(event.payload))
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == event.event_id)
    )
    assert outbox is not None
    envelope = v4_outbox_envelope(event)
    # Event/Outbox are append-only by service policy but do not have ORM update
    # listeners.  Mark the coordinated replacement dirty so later SELECTs do
    # not refresh the old database envelope over this attack fixture.
    outbox.source_event_seq = event.seq
    outbox.transaction_id = event.transaction_id
    outbox.created_at = event.occurred_at
    outbox.payload = envelope
    outbox.payload_digest = canonical_digest(envelope)


def test_registration_rejects_30_day_coordinated_reseal(sqlite_session) -> None:
    receipt = _receipts(sqlite_session)["signal.received"]
    registration = sqlite_session.get(
        ControllerRegistration,
        (receipt.controller_registration_id, receipt.controller_registration_revision),
    )
    assert registration is not None
    set_committed_value(registration, "valid_from", NOW - timedelta(days=31))
    set_committed_value(registration, "registered_at", NOW - timedelta(days=30))
    _reseal_registration(registration)
    _bind_registration_digest(registration, receipt)

    _reject(sqlite_session, receipt)


@pytest.mark.parametrize("mode", ["REVOKED", "NOT_YET_VALID", "EXPIRED"])
def test_receipt_requires_registration_active_at_recorded_time(
    sqlite_session, mode: str
) -> None:
    receipt = _receipts(sqlite_session)["signal.received"]
    registration = sqlite_session.get(
        ControllerRegistration,
        (receipt.controller_registration_id, receipt.controller_registration_revision),
    )
    assert registration is not None
    audit = sqlite_session.get(
        Audit, registration.registration_audit_ref.removeprefix("audit://")
    )
    assert audit is not None
    if mode == "REVOKED":
        set_committed_value(registration, "state", "REVOKED")
    elif mode == "NOT_YET_VALID":
        future = receipt.recorded_at + timedelta(days=1)
        set_committed_value(registration, "valid_from", future)
        set_committed_value(registration, "registered_at", future)
        set_committed_value(audit, "ts", future)
        _reseal_audit(audit)
    else:
        set_committed_value(registration, "expires_at", receipt.recorded_at)
    _reseal_registration(registration)
    _bind_registration_digest(registration, receipt)

    _reject(sqlite_session, receipt)


@pytest.mark.parametrize(
    ("event_type", "field", "value"),
    [
        ("signal.received", "source_event_id", "forged-source-event"),
        ("case.opened", "opening_signal_id", "sig_FORGED0001"),
        ("signal_case_link.linked", "case_id", "case_FORGED0001"),
        ("evidence.recorded", "completeness", "COMPLETE"),
    ],
)
def test_coordinated_event_outbox_reseal_rejects_subject_business_drift(
    sqlite_session, event_type: str, field: str, value: str
) -> None:
    receipt = _receipts(sqlite_session)[event_type]
    event = sqlite_session.get(Event, receipt.event_id)
    assert event is not None
    set_committed_value(event, "payload", {**event.payload, field: value})
    _reseal_event_and_outbox(sqlite_session, event)

    _reject(sqlite_session, receipt)


@pytest.mark.parametrize(
    ("event_type", "mutation"),
    [
        ("signal.received", "correlation"),
        ("case.opened", "causation"),
        ("signal_case_link.linked", "causation"),
        ("evidence.recorded", "causation"),
        ("signal_case_link.linked", "seq"),
        ("case.opened", "transaction"),
    ],
)
def test_coordinated_event_outbox_reseal_rejects_graph_rebinding(
    sqlite_session, event_type: str, mutation: str
) -> None:
    receipt = _receipts(sqlite_session)[event_type]
    event = sqlite_session.get(Event, receipt.event_id)
    assert event is not None
    if mutation == "correlation":
        set_committed_value(event, "correlation_id", "case_FORGED0001")
    elif mutation == "causation":
        set_committed_value(event, "causation_id", "evt_FORGED0001")
    elif mutation == "seq":
        set_committed_value(event, "seq", 99)
    else:
        set_committed_value(event, "transaction_id", "txn_FORGED0001")
        set_committed_value(receipt, "transaction_id", event.transaction_id)
        audit = sqlite_session.get(
            Audit, receipt.audit_ref.removeprefix("audit://")
        )
        assert audit is not None
        set_committed_value(audit, "transaction_id", event.transaction_id)
        _reseal_audit(audit)
        _reseal_receipt(receipt)
    _reseal_event_and_outbox(sqlite_session, event)

    _reject(sqlite_session, receipt)


def test_coordinated_chain_time_reseal_cannot_reorder_case_before_signal(
    sqlite_session,
) -> None:
    receipts = _receipts(sqlite_session)
    receipt = receipts["case.opened"]
    signal_event = sqlite_session.get(Event, receipts["signal.received"].event_id)
    event = sqlite_session.get(Event, receipt.event_id)
    audit = sqlite_session.get(Audit, receipt.audit_ref.removeprefix("audit://"))
    assert signal_event is not None and event is not None and audit is not None
    set_committed_value(event, "occurred_at", signal_event.occurred_at)
    set_committed_value(event, "created_at", signal_event.occurred_at)
    set_committed_value(audit, "ts", signal_event.occurred_at)
    set_committed_value(receipt, "recorded_at", signal_event.occurred_at)
    _reseal_audit(audit)
    _reseal_receipt(receipt)
    _reseal_event_and_outbox(sqlite_session, event)

    _reject(sqlite_session, receipt)


def test_self_consistent_controller_trace_rebind_needs_real_public_command(
    sqlite_session,
) -> None:
    receipt = _receipts(sqlite_session)["signal.received"]
    event = sqlite_session.get(Event, receipt.event_id)
    audit = sqlite_session.get(Audit, receipt.audit_ref.removeprefix("audit://"))
    assert event is not None and audit is not None
    forged_request_id = "req_FORGED0001"
    set_committed_value(event, "causation_id", forged_request_id)
    set_committed_value(audit, "trace_id", forged_request_id)
    _reseal_audit(audit)
    _reseal_event_and_outbox(sqlite_session, event)

    _reject(sqlite_session, receipt)
