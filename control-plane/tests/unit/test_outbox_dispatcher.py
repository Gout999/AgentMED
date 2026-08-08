"""P0-2 transactional outbox, Trust, notification, and audit closure tests."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models.tables import (
    Aggregate,
    Audit,
    Event,
    Inbox,
    Outbox,
    OutboxDeliveryReceipt,
    TrustLedger,
    TrustLedgerEntry,
)
from app.notifications.adapters import FeishuMockAdapter
from app.services.audit import AuditWriteError
from app.services.case_service import CaseService, CaseServiceError
from app.services.event_store import EventStore
from app.services.gate_service import GateService
from app.services.notification_service import NotificationService
from app.services.outbox_relay import DomainEventConsumer, OutboxDispatcher
from app.services.trust_service import ACTION_TYPE, RISK_CLASS, TrustService
from app.utils.jcs import canonical_json_digest
from tests.conftest import make_gate_report


def _factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "database_url": "sqlite:///:memory:",
        "audit_jsonl_path": str(tmp_path / "audit.jsonl"),
        "outbox_retry_initial_seconds": 0,
        "outbox_retry_max_seconds": 0,
        "outbox_claim_ttl_seconds": 1,
        "outbox_max_attempts": 3,
    }
    values.update(overrides)
    return Settings(**values)


def _append_event(factory, *, aggregate_id: str, event_type: str, payload: dict | None = None):
    with factory() as session, session.begin():
        return EventStore(session).append_event(
            aggregate_type=aggregate_id.split("_", 1)[0],
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload or {},
            new_state="TEST_STATE",
        ).event_id


def _seed_notifying_case(factory, case_id: str) -> None:
    with factory() as session, session.begin():
        session.add(
            Aggregate(
                aggregate_type="case",
                aggregate_id=case_id,
                state="NOTIFYING",
                payload={"resolution": "fixed"},
                revision=1,
            )
        )


def test_required_domain_events_are_transactionally_enveloped(sqlite_engine):
    factory = _factory(sqlite_engine)
    expected = {
        "case.opened": "CASE_CREATED",
        "case.attribution_completed": "ATTRIBUTION_DECIDED",
        "eval.passed": "GATE_COMPLETED",
        "release.requested": "RELEASE_STARTED",
        "release.promoted": "RELEASE_PROMOTED",
        "release.rolled_back": "RELEASE_ROLLED_BACK",
        "release.unknown_detected": "RELEASE_UNKNOWN",
        "notification.sent": "NOTIFICATION_SENT",
        "case.closed": "CASE_ARCHIVED",
    }
    with factory() as session, session.begin():
        store = EventStore(session)
        for index, (source_type, domain_type) in enumerate(expected.items()):
            aggregate_type = source_type.split(".", 1)[0]
            aggregate_id = f"{aggregate_type}_coverage_{index}"
            event = store.append_event(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                event_type=source_type,
                payload={"coverage": domain_type},
                new_state="TEST_STATE",
            )
            outbox = session.scalar(
                select(Outbox).where(
                    Outbox.source_event_id == event.event_id,
                    Outbox.event_type == domain_type,
                )
            )
            assert outbox is not None
            assert outbox.status == "PENDING"
            assert outbox.channel == "domain.events"
            assert outbox.payload["domain_event_type"] == domain_type
            assert outbox.payload["source_event_id"] == event.event_id
            assert outbox.source_event_seq == event.seq
            assert outbox.payload["aggregate_seq"] == event.seq
            assert outbox.payload_digest.startswith("sha256:")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Outbox)) == len(expected)


@pytest.mark.parametrize(
    ("overall_status", "source_event_type"),
    [
        ("passed", "eval.passed"),
        ("failed", "eval.failed"),
        ("error", "eval.error"),
    ],
)
def test_gate_completed_envelope_matches_terminal_event_contract(
    sqlite_engine, tmp_path, overall_status, source_event_type
):
    factory = _factory(sqlite_engine)
    workorder_id = f"wo_gate_envelope_{overall_status}"
    eval_id = f"eval_gateenvelope{overall_status}01"
    report = make_gate_report(
        workorder_id,
        overall_status=overall_status,
        eval_id=eval_id,
    )
    with factory() as session, session.begin():
        GateService(session, _settings(tmp_path)).register_report(
            {
                "report": report,
                "workorder_id": workorder_id,
                "target_versionset_id": "vs_gate_envelope_1",
                "target_revision": 1,
                "dataset_id": "gate-envelope-contract",
                "dataset_version": "1.0.0",
                "evidence_digest": canonical_json_digest(report["artifact_refs"]),
            }
        )

    with factory() as session:
        outbox = session.scalar(
            select(Outbox).where(
                Outbox.aggregate_id == eval_id,
                Outbox.event_type == "GATE_COMPLETED",
            )
        )
        assert outbox is not None
        envelope = outbox.payload
        terminal_payload = envelope["payload"]
        terminal_event = session.get(Event, envelope["source_event_id"])
        judge_event = session.scalar(
            select(Event).where(
                Event.aggregate_id == eval_id,
                Event.event_type == "eval.judge_track_completed",
            )
        )
        assert terminal_event is not None and judge_event is not None
        assert envelope["source_event_type"] == source_event_type
        assert terminal_event.event_type == source_event_type
        assert terminal_event.causation_id == judge_event.event_id
        assert envelope["causation_id"] == judge_event.event_id
        assert terminal_payload == terminal_event.payload
        assert terminal_payload["report_ref"] == f"eval://{eval_id}"
        assert terminal_payload["report_digest"] == f"sha256:{outbox.payload['payload']['report_hash']}"
        if overall_status == "failed":
            assert terminal_payload["failing_checks"]
        elif overall_status == "error":
            assert terminal_payload["error"]
            assert terminal_payload["retryable"] is False


def test_same_aggregate_unknown_cannot_be_overtaken_by_later_promote(
    sqlite_engine, tmp_path
):
    factory = _factory(sqlite_engine)
    with factory() as session, session.begin():
        store = EventStore(session)
        store.append_event(
            aggregate_type="release",
            aggregate_id="release_ordered_unknown",
            event_type="release.unknown_detected",
            payload={"operation_id": "op_unknown_first"},
            new_state="UNKNOWN",
        )
        store.append_event(
            aggregate_type="release",
            aggregate_id="release_ordered_unknown",
            event_type="release.promoted",
            payload={"operation_id": "op_promote_later"},
            new_state="COMPLETED",
        )

    settings = _settings(tmp_path)
    first_worker = OutboxDispatcher(factory, settings, worker_id="test:order:first")
    second_worker = OutboxDispatcher(factory, settings, worker_id="test:order:second")
    first = first_worker._claim_one()
    assert first is not None and first.event_type == "RELEASE_UNKNOWN"
    assert second_worker._claim_one() is None

    first_worker._dispatch(first)
    later = second_worker.dispatch_batch(limit=2)
    assert later["dead"] == 1 and later["sent"] == 0
    with factory() as session:
        ledger = session.get(
            TrustLedger,
            {"risk_class": RISK_CLASS, "action_type": ACTION_TYPE, "epoch": 1},
        )
        assert ledger is not None
        assert ledger.autonomy_state == "BLOCKED_UNKNOWN"
        assert ledger.trials == 0 and ledger.successes == 0
        rows = list(
            session.scalars(
                select(Outbox)
                .where(Outbox.aggregate_id == "release_ordered_unknown")
                .order_by(Outbox.source_event_seq)
            )
        )
        assert [row.status for row in rows] == ["SENT", "DEAD"]


def test_audit_failure_rolls_back_case_event_and_outbox(sqlite_engine, tmp_path):
    factory = _factory(sqlite_engine)
    settings = _settings(tmp_path, audit_force_fail=True)
    session = factory()
    try:
        with pytest.raises(AuditWriteError):
            with session.begin():
                CaseService(session, settings).ingest_complaint(
                    source="webhook",
                    text="audit must fail closed",
                    external_id="audit-fail-1",
                )
    finally:
        session.close()
    with factory() as verify:
        assert verify.scalar(select(func.count()).select_from(Inbox)) == 0
        assert verify.scalar(select(func.count()).select_from(Aggregate)) == 0
        assert verify.scalar(select(func.count()).select_from(Event)) == 0
        assert verify.scalar(select(func.count()).select_from(Outbox)) == 0


def test_release_events_feed_trust_once_and_three_of_three_is_denied(
    sqlite_engine, tmp_path
):
    factory = _factory(sqlite_engine)
    source_ids = []
    for index in range(3):
        source_ids.append(
            _append_event(
                factory,
                aggregate_id=f"release_{index}",
                event_type="release.promoted",
                payload={
                    "operation_id": f"op_{index}",
                    "probes": [f"probe_{probe}" for probe in range(12)],
                },
            )
        )
    dispatcher = OutboxDispatcher(factory, _settings(tmp_path), worker_id="test:trust")
    stats = dispatcher.dispatch_batch(limit=20)
    assert stats == {"claimed": 3, "sent": 3, "retried": 0, "dead": 0, "blocked": 0}

    with factory() as session:
        ledger = session.get(
            TrustLedger,
            {"risk_class": RISK_CLASS, "action_type": ACTION_TYPE, "epoch": 1},
        )
        assert ledger is not None
        assert ledger.successes == 3
        assert ledger.trials == 3
        assert ledger.autonomy_state == "MANUAL"
        assert abs(float((ledger.payload or {})["wilson_lower"]) - 0.438494) < 1e-5
        assert (ledger.payload or {})["promotion_eligible"] is False
        entries = list(session.scalars(select(TrustLedgerEntry).order_by(TrustLedgerEntry.action_ref)))
        assert len(entries) == 3
        schema = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "contracts"
                / "schemas"
                / "trust-ledger-entry.schema.json"
            ).read_text(encoding="utf-8")
        )
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for entry in entries:
            assert not list(validator.iter_errors(entry.payload))
            assert entry.payload["sample_rule"] == "one_action_one_sample"
            assert entry.payload["promotion"]["decision"] == "denied"
        assert session.scalar(select(func.count()).select_from(OutboxDeliveryReceipt)) == 3
        assert session.scalar(
            select(func.count()).select_from(Audit).where(Audit.action == "trust.promotion_denied")
        ) == 3
        trust_events = list(
            session.scalars(
                select(Event)
                .where(Event.aggregate_type == "trust")
                .order_by(Event.seq)
            )
        )
        evidence_by_action = {
            event.payload["action_ref"]: event
            for event in trust_events
            if event.event_type == "trust.evidence_recorded"
        }
        denials = [
            event for event in trust_events if event.event_type == "trust.promotion_denied"
        ]
        assert len(evidence_by_action) == len(denials) == 3
        for evidence_event in evidence_by_action.values():
            assert evidence_event.causation_id == evidence_event.payload["source_event_id"]
        for denial_event in denials:
            evidence_event = evidence_by_action[denial_event.payload["action_ref"]]
            assert denial_event.causation_id == evidence_event.event_id

    # A second source/probe for the same release action is coalesced and cannot
    # increment the raw sample count.
    with factory() as session, session.begin():
        duplicate = TrustService(session, _settings(tmp_path)).record_outcome(
            source_event_id="evt_extra_probe",
            action_ref="release_0",
            success=True,
            detail="another probe from the same release action",
        )
        assert duplicate["duplicate"] is True
    assert dispatcher.dispatch_batch(limit=20)["claimed"] == 0
    with factory() as session:
        ledger = session.get(
            TrustLedger,
            {"risk_class": RISK_CLASS, "action_type": ACTION_TYPE, "epoch": 1},
        )
        assert ledger is not None and ledger.trials == 3


def test_release_unknown_blocks_trust_and_later_outcome_fails_closed(sqlite_engine, tmp_path):
    factory = _factory(sqlite_engine)
    _append_event(
        factory,
        aggregate_id="release_unknown_1",
        event_type="release.unknown_detected",
        payload={"operation_id": "op_unknown", "last_known": "VERIFYING"},
    )
    dispatcher = OutboxDispatcher(factory, _settings(tmp_path), worker_id="test:unknown")
    assert dispatcher.dispatch_batch(limit=5)["sent"] == 1
    with factory() as session:
        ledger = session.get(
            TrustLedger,
            {"risk_class": RISK_CLASS, "action_type": ACTION_TYPE, "epoch": 1},
        )
        assert ledger is not None
        assert ledger.autonomy_state == "BLOCKED_UNKNOWN"
        assert ledger.trials == 0

    _append_event(
        factory,
        aggregate_id="release_unknown_1",
        event_type="release.promoted",
        payload={"operation_id": "op_late"},
    )
    result = dispatcher.dispatch_batch(limit=5)
    assert result["dead"] == 1
    with factory() as session:
        ledger = session.get(
            TrustLedger,
            {"risk_class": RISK_CLASS, "action_type": ACTION_TYPE, "epoch": 1},
        )
        assert ledger is not None and ledger.trials == 0
        late = session.scalar(
            select(Outbox).where(Outbox.event_type == "RELEASE_PROMOTED")
        )
        assert late is not None and late.status == "DEAD"


def test_dispatcher_retries_without_duplicate_receipt(sqlite_engine, tmp_path):
    factory = _factory(sqlite_engine)
    _append_event(
        factory,
        aggregate_id="case_retry_1",
        event_type="case.opened",
        payload={"title": "retry"},
    )

    class FailOnceConsumer:
        def __init__(self):
            self.calls = 0
            self.real = DomainEventConsumer()

        def consume(self, session, row):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary consumer outage")
            return self.real.consume(session, row)

    consumer = FailOnceConsumer()
    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path),
        domain_consumer=consumer,
        worker_id="test:retry",
    )
    first = dispatcher.dispatch_batch(limit=1)
    assert first["retried"] == 1 and first["sent"] == 0
    second = dispatcher.dispatch_batch(limit=1)
    assert second["sent"] == 1
    with factory() as session:
        row = session.scalar(select(Outbox))
        assert row is not None and row.status == "SENT" and row.attempts == 2
        assert session.scalar(select(func.count()).select_from(OutboxDeliveryReceipt)) == 1


def test_trust_consumer_retry_rolls_back_first_attempt_and_counts_once(sqlite_engine, tmp_path):
    factory = _factory(sqlite_engine)
    _append_event(
        factory,
        aggregate_id="release_retry_trust",
        event_type="release.promoted",
        payload={"operation_id": "op_retry_trust", "probes": ["a", "b", "c"]},
    )

    class FailAfterTrustMutationOnce:
        def __init__(self):
            self.calls = 0
            self.real = DomainEventConsumer()

        def consume(self, session, row):
            self.calls += 1
            receipt = self.real.consume(session, row)
            if self.calls == 1:
                raise RuntimeError("crash before outbox ACK")
            return receipt

    consumer = FailAfterTrustMutationOnce()
    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path),
        domain_consumer=consumer,
        worker_id="test:trust-retry",
    )
    assert dispatcher.dispatch_batch(limit=1)["retried"] == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(TrustLedgerEntry)) == 0
    assert dispatcher.dispatch_batch(limit=1)["sent"] == 1
    with factory() as session:
        ledger = session.get(
            TrustLedger,
            {"risk_class": RISK_CLASS, "action_type": ACTION_TYPE, "epoch": 1},
        )
        assert ledger is not None and ledger.successes == 1 and ledger.trials == 1
        assert session.scalar(select(func.count()).select_from(TrustLedgerEntry)) == 1


def test_audit_outage_prevents_claim_consumer_and_trust_mutation(sqlite_engine, tmp_path):
    factory = _factory(sqlite_engine)
    _append_event(
        factory,
        aggregate_id="release_audit_block",
        event_type="release.promoted",
        payload={"operation_id": "op_audit_block"},
    )
    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path, audit_force_fail=True),
        worker_id="test:audit-block",
    )
    stats = dispatcher.dispatch_batch(limit=1)
    assert stats["blocked"] == 1 and stats["claimed"] == 0
    with factory() as session:
        row = session.scalar(select(Outbox))
        assert row is not None and row.status == "PENDING" and row.attempts == 0
        assert session.scalar(select(func.count()).select_from(TrustLedgerEntry)) == 0
        assert session.scalar(select(func.count()).select_from(Audit)) == 0


def test_notification_receipt_archives_case_and_emits_both_domain_events(
    sqlite_engine, tmp_path
):
    factory = _factory(sqlite_engine)
    _seed_notifying_case(factory, "case_notify_1")
    with factory() as session, session.begin():
        queued = NotificationService(session, _settings(tmp_path)).queue(
            case_id="case_notify_1",
            channel="feishu-mock:contract-replay:",
            thread_ref="thread:1",
            body_ref="artifact://reply/1",
        )
    adapter = FeishuMockAdapter()
    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path),
        notification_adapter=adapter,
        worker_id="test:notification",
    )
    stats = dispatcher.dispatch_batch(limit=10)
    assert stats["sent"] == 3  # delivery command + NOTIFICATION_SENT + CASE_ARCHIVED
    assert adapter.calls == [queued["outbox_id"]]
    with factory() as session:
        case = session.get(
            Aggregate, {"aggregate_type": "case", "aggregate_id": "case_notify_1"}
        )
        notification = session.get(
            Aggregate,
            {"aggregate_type": "notification", "aggregate_id": queued["notification_id"]},
        )
        assert case is not None and case.state == "CLOSED"
        assert notification is not None and notification.state == "SENT"
        assert (notification.payload or {})["receipt"]["provider"] == "feishu-mock"
        outboxes = list(session.scalars(select(Outbox).order_by(Outbox.created_at)))
        assert {row.event_type for row in outboxes} == {
            "NOTIFICATION_DELIVERY_REQUESTED",
            "NOTIFICATION_SENT",
            "CASE_ARCHIVED",
        }
        assert all(row.status == "SENT" and row.receipt for row in outboxes)
        case_events = list(
            session.scalars(
                select(Event)
                .where(Event.aggregate_id == "case_notify_1")
                .order_by(Event.seq)
            )
        )
        sent_event = session.scalar(
            select(Event).where(
                Event.aggregate_type == "notification",
                Event.aggregate_id == queued["notification_id"],
                Event.event_type == "notification.sent",
            )
        )
        assert sent_event is not None
        assert sent_event.causation_id == (notification.payload or {})["receipt_digest"]
        assert case_events[-1].event_type == "case.closed"
        assert case_events[-1].causation_id == sent_event.event_id


def test_provider_success_before_ack_is_retried_with_same_idempotency_key(
    sqlite_engine, tmp_path
):
    factory = _factory(sqlite_engine)
    _seed_notifying_case(factory, "case_crash_1")
    with factory() as session, session.begin():
        queued = NotificationService(session, _settings(tmp_path)).queue(
            case_id="case_crash_1",
            channel="feishu-mock:contract-replay:",
            thread_ref="thread:crash",
            body_ref="artifact://reply/crash",
        )
    # Simulate a local pre-ACK invariant failure after the provider accepted the
    # stable outbox id. The claim stays PROCESSING because failure handling also
    # cannot safely detach the notification from its Case.
    with factory() as session, session.begin():
        case = session.get(
            Aggregate, {"aggregate_type": "case", "aggregate_id": "case_crash_1"}
        )
        assert case is not None
        case.state = "OPEN"
    adapter = FeishuMockAdapter()
    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path),
        notification_adapter=adapter,
        worker_id="test:crash",
    )
    first = dispatcher.dispatch_batch(limit=1)
    assert first["blocked"] == 1
    assert adapter.calls == [queued["outbox_id"]]

    with factory() as session, session.begin():
        case = session.get(
            Aggregate, {"aggregate_type": "case", "aggregate_id": "case_crash_1"}
        )
        outbox = session.get(Outbox, queued["outbox_id"])
        assert case is not None and outbox is not None
        case.state = "NOTIFYING"
        outbox.claim_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    second = dispatcher.dispatch_batch(limit=1)
    assert second["sent"] == 1
    # The mock adapter returned its original receipt; it did not perform a
    # second provider send.
    assert adapter.calls == [queued["outbox_id"]]
    with factory() as session:
        row = session.get(Outbox, queued["outbox_id"])
        assert row is not None and row.status == "SENT" and row.attempts == 2


def test_invalid_notification_receipt_dead_letters_and_escalates_case(sqlite_engine, tmp_path):
    factory = _factory(sqlite_engine)
    _seed_notifying_case(factory, "case_bad_receipt")
    with factory() as session, session.begin():
        queued = NotificationService(session, _settings(tmp_path)).queue(
            case_id="case_bad_receipt",
            channel="feishu-mock:contract-replay:",
            thread_ref="thread:bad",
            body_ref="artifact://reply/bad",
        )

    class BadReceiptAdapter:
        def deliver(self, *, outbox_id, payload, payload_digest):
            return {
                "status": "sent",
                "provider": "feishu-mock",
                "provider_message_id": "msg-bad",
                "outbox_id": outbox_id,
                "payload_digest": "sha256:" + "0" * 64,
            }

    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path),
        notification_adapter=BadReceiptAdapter(),
        worker_id="test:bad-receipt",
    )
    stats = dispatcher.dispatch_batch(limit=1)
    assert stats["dead"] == 1
    with factory() as session:
        row = session.get(Outbox, queued["outbox_id"])
        notification = session.get(
            Aggregate,
            {"aggregate_type": "notification", "aggregate_id": queued["notification_id"]},
        )
        case = session.get(
            Aggregate, {"aggregate_type": "case", "aggregate_id": "case_bad_receipt"}
        )
        assert row is not None and row.status == "DEAD" and row.receipt is None
        assert notification is not None and notification.state == "DEAD_LETTERED"
        assert case is not None and case.state == "ESCALATED"


def test_case_closed_cannot_be_forged_through_generic_transition(sqlite_session):
    sqlite_session.add(
        Aggregate(
            aggregate_type="case",
            aggregate_id="case_protected_close",
            state="NOTIFYING",
            payload={},
            revision=1,
        )
    )
    sqlite_session.flush()
    with pytest.raises(CaseServiceError) as exc:
        CaseService(sqlite_session, Settings()).transition(
            "case_protected_close",
            "case.closed",
            {"notification_id": "notif_fake", "resolution": "fixed"},
        )
    assert exc.value.code == "forbidden_transition"


def test_outbox_relay_requires_internal_authority_and_has_no_fake_sent_route(app_client):
    client, _ = app_client
    response = client.post(
        "/v1/complaints",
        json={"source": "webhook", "text": "dispatch me", "external_id": "dispatch-auth-1"},
    )
    assert response.status_code == 200
    token = client.headers.pop("Authorization")
    try:
        denied = client.post("/v1/outbox/relay")
        assert denied.status_code == 401
    finally:
        client.headers["Authorization"] = token
    dispatched = client.post("/v1/outbox/relay")
    assert dispatched.status_code == 200
    assert dispatched.json()["sent"] == 1
    notification_id = "notif_00000000000000000000000000"
    assert client.post(
        f"/v1/notifications/{notification_id}/sent",
        json={"provider_message_id": "forged"},
    ).status_code == 404
