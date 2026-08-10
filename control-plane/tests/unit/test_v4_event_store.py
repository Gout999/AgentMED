"""Stage 1A v4 event routing is workspace-scoped and isolated from v3."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models.tables import Event, Outbox
from app.services.v4_event_store import V4EventStore, V4EventStoreError


WS_ONE = "ws_01J0000000000001"
WS_TWO = "ws_01J0000000000002"
PRINCIPAL = "prn_01J0000000000001"
TRANSACTION = "txn_01J0000000000001"
CASE_ID = "case_01J0000000000001"
SIGNAL_ID = "sig_01J0000000000001"
AUTHORITY_ID = "arec_01J0000000000001"
DIGEST = "sha256:" + "a" * 64
NOW = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)


def _controller_payload(**business: object) -> dict[str, object]:
    return {
        "subject_kind": "SIGNAL_RECORD",
        "subject_id": SIGNAL_ID,
        "subject_revision": None,
        "subject_digest": DIGEST,
        "authority_receipt_id": AUTHORITY_ID,
        **business,
    }


def test_v4_case_opened_uses_exact_route_and_never_emits_v3_case_created(
    sqlite_session,
) -> None:
    event = V4EventStore(sqlite_session).append_event(
        workspace_id=WS_ONE,
        aggregate_type="quality_case",
        aggregate_id=CASE_ID,
        event_type="case.opened",
        payload=_controller_payload(
            case_id=CASE_ID,
            opening_signal_id=SIGNAL_ID,
            subject_kind="QUALITY_CASE",
            subject_id=CASE_ID,
            subject_revision=1,
        ),
        causation_id="evt_01J0000000000001",
        correlation_id=CASE_ID,
        actor_principal=PRINCIPAL,
        transaction_id=TRANSACTION,
        occurred_at=NOW,
    )

    persisted = sqlite_session.get(Event, event.event_id)
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == event.event_id)
    )

    assert persisted is event
    assert event.contract_version == "v4"
    assert event.event_version == "1.0"
    assert event.workspace_id == WS_ONE
    assert event.aggregate_type == "quality_case"
    assert event.event_type == "case.opened"
    assert event.actor == "case-controller"
    assert event.actor_principal == PRINCIPAL
    assert event.transaction_id == TRANSACTION
    assert event.payload_digest.startswith("sha256:")
    assert outbox is not None
    assert outbox.contract_version == "v4"
    assert outbox.channel == "v4.domain.events"
    assert outbox.event_type == "case.opened"
    assert outbox.payload["contract_version"] == "v4"
    assert outbox.payload["aggregate_type"] == "quality_case"
    assert outbox.payload["event_type"] == "case.opened"
    assert outbox.payload["payload_digest"] == event.payload_digest
    assert not sqlite_session.scalars(
        select(Outbox).where(Outbox.event_type == "CASE_CREATED")
    ).all()


def test_v4_event_sequence_is_scoped_by_workspace_and_aggregate_type(
    sqlite_session,
) -> None:
    store = V4EventStore(sqlite_session)
    first = store.append_event(
        workspace_id=WS_ONE,
        aggregate_type="signal",
        aggregate_id=SIGNAL_ID,
        event_type="signal.received",
        payload=_controller_payload(
            signal_id=SIGNAL_ID,
            signal_digest=DIGEST,
            source_id="src_01J0000000000001",
            source_event_id="maintainer-report-1",
        ),
        causation_id="req_01J0000000000001",
        correlation_id=CASE_ID,
        actor_principal=PRINCIPAL,
        transaction_id=TRANSACTION,
        occurred_at=NOW,
    )
    second = store.append_event(
        workspace_id=WS_ONE,
        aggregate_type="signal",
        aggregate_id=SIGNAL_ID,
        event_type="signal_case_link.linked",
        payload=_controller_payload(
            signal_id=SIGNAL_ID,
            case_id=CASE_ID,
            link_digest=DIGEST,
            subject_kind="SIGNAL_CASE_LINK",
            subject_id="scl_01J0000000000001",
            subject_revision=1,
        ),
        causation_id=first.event_id,
        correlation_id=CASE_ID,
        actor_principal=PRINCIPAL,
        transaction_id=TRANSACTION,
        occurred_at=NOW,
    )
    other_workspace = store.append_event(
        workspace_id=WS_TWO,
        aggregate_type="signal",
        aggregate_id=SIGNAL_ID,
        event_type="signal.received",
        payload=_controller_payload(
            signal_id=SIGNAL_ID,
            signal_digest=DIGEST,
            source_id="src_01J0000000000002",
            source_event_id="maintainer-report-1",
        ),
        causation_id="req_01J0000000000002",
        correlation_id=CASE_ID,
        actor_principal=PRINCIPAL,
        transaction_id=TRANSACTION,
        occurred_at=NOW,
    )
    other_type = store.append_event(
        workspace_id=WS_ONE,
        aggregate_type="quality_case",
        aggregate_id=SIGNAL_ID,
        event_type="case.opened",
        payload=_controller_payload(
            case_id=SIGNAL_ID,
            opening_signal_id=SIGNAL_ID,
            subject_kind="QUALITY_CASE",
            subject_id=SIGNAL_ID,
            subject_revision=1,
        ),
        causation_id=first.event_id,
        correlation_id=CASE_ID,
        actor_principal=PRINCIPAL,
        transaction_id=TRANSACTION,
        occurred_at=NOW,
    )

    assert (first.seq, second.seq) == (1, 2)
    assert other_workspace.seq == 1
    assert other_type.seq == 1


@pytest.mark.parametrize(
    ("aggregate_type", "event_type", "payload"),
    [
        (
            "case",
            "case.opened",
            _controller_payload(case_id=CASE_ID, opening_signal_id=SIGNAL_ID),
        ),
        (
            "quality_case",
            "case.opened",
            _controller_payload(case_id=CASE_ID),
        ),
        (
            "quality_case",
            "case.opened",
            _controller_payload(
                case_id=CASE_ID,
                opening_signal_id=SIGNAL_ID,
                score=0.5,
            ),
        ),
        (
            "quality_case",
            "case.opened",
            _controller_payload(
                case_id=CASE_ID,
                opening_signal_id=SIGNAL_ID,
                subject_kind="SIGNAL_RECORD",
                subject_id=CASE_ID,
                subject_revision=1,
            ),
        ),
    ],
    ids=[
        "v3-aggregate-type",
        "missing-required-payload",
        "float-forbidden",
        "wrong-subject-kind",
    ],
)
def test_v4_event_store_rejects_route_payload_or_integrity_drift(
    sqlite_session,
    aggregate_type: str,
    event_type: str,
    payload: dict[str, object],
) -> None:
    with pytest.raises(V4EventStoreError):
        V4EventStore(sqlite_session).append_event(
            workspace_id=WS_ONE,
            aggregate_type=aggregate_type,
            aggregate_id=CASE_ID,
            event_type=event_type,
            payload=payload,
            causation_id="evt_01J0000000000001",
            correlation_id=CASE_ID,
            actor_principal=PRINCIPAL,
            transaction_id=TRANSACTION,
            occurred_at=NOW,
        )
