"""P0-2 transactional outbox, Trust, notification, and audit closure tests."""
from __future__ import annotations

import json
import hashlib
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
    ReleaseClosure,
    TrustLedger,
    TrustLedgerEntry,
    WorkOrder,
)
from app.notifications.adapters import FeishuMockAdapter
from app.services.audit import AuditWriteError
from app.services.case_service import CaseService, CaseServiceError
from app.services.event_store import EventStore
from app.services.gate_service import GateService
from app.services.notification_service import NotificationService
from app.services.outbox_relay import (
    DomainEventConsumer,
    OutboxDeliveryError,
    OutboxDispatcher,
    OutboxSnapshot,
)
from app.services.trust_service import ACTION_TYPE, RISK_CLASS, TrustService
from app.utils.jcs import canonical_json_digest, workorder_hash
from tests.conftest import make_gate_report, make_workorder


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


def _reply_artifact(tmp_path: Path, name: str, text: str = "resolved") -> tuple[str, str]:
    path = tmp_path / f"{name}.txt"
    path.write_text(text, encoding="utf-8")
    return path.resolve().as_uri(), "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class _TrustOnlyConsumer:
    """Unit boundary for Trust math; production DomainEventConsumer also closes Cases."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def consume(self, session, row):
        return TrustService(session, self.settings).consume_release_event(row.payload or {})


def _append_event(factory, *, aggregate_id: str, event_type: str, payload: dict | None = None):
    with factory() as session, session.begin():
        return EventStore(session).append_event(
            aggregate_type=aggregate_id.split("_", 1)[0],
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload or {},
            new_state="TEST_STATE",
        ).event_id


def _seed_notifying_case(
    factory, case_id: str, *, thread_ref: str = "thread:1"
) -> tuple[str, str]:
    release_id = f"rel_{case_id}"
    with factory() as session, session.begin():
        session.add(
            Aggregate(
                aggregate_type="case",
                aggregate_id=case_id,
                state="RELEASING",
                payload={
                    "resolution": "fixed",
                    "original_channel": "feishu-mock:contract-replay:",
                    "original_thread_ref": thread_ref,
                },
                revision=1,
            )
        )
        session.flush()
        event = EventStore(session).append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="case.resolved",
            payload={"release_id": release_id, "resolution": "fixed"},
            causation_id="evt_release_promoted",
            correlation_id=case_id,
            expected_revision=1,
            machine="case",
            merge_payload={"resolved_release_id": release_id, "resolution": "fixed"},
        )
        return release_id, event.event_id


def test_required_domain_events_are_transactionally_enveloped(sqlite_engine):
    factory = _factory(sqlite_engine)
    expected = {
        "case.opened": "CASE_CREATED",
        "case.attribution_completed": "ATTRIBUTION_DECIDED",
        "eval.bound": "GATE_COMPLETED",
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
def test_gate_completed_is_not_published_before_final_workorder_binding(
    sqlite_engine, tmp_path, overall_status, source_event_type
):
    factory = _factory(sqlite_engine)
    workorder_id = f"wo_gate_envelope_{overall_status}"
    eval_id = f"eval_gateenvelope{overall_status}01"
    report = make_gate_report(
        workorder_id,
        overall_status=overall_status,
        eval_id=eval_id,
        policy_profile="isolated-replay",
    )
    with factory() as session, session.begin():
        gates = GateService(
            session,
            _settings(
                tmp_path,
                gate_policy_profile="isolated-replay",
                allow_isolated_replay_gate=True,
            ),
        )
        gates.register_report(
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
        assert session.scalar(
            select(Outbox).where(
                Outbox.aggregate_id == eval_id,
                Outbox.event_type == "GATE_COMPLETED",
            )
        ) is None
        workorder = make_workorder(
            workorder_id=workorder_id,
            nonce="00000000-0000-0000-0000-000000000905",
            case_id="case_gate_envelope_bound",
        )
        workorder["gate_report_ref"] = {
            "uri": f"eval://{eval_id}",
            "digest": f"sha256:{canonical_json_digest(report, prefix=False)}",
        }
        workorder["target_versionset_digest"] = report["subject"][
            "target_versionset_digest"
        ]
        workorder["hash"] = workorder_hash(workorder)
        gates.bind_workorder(workorder)

    with factory() as session:
        terminal_event = session.scalar(
            select(Event).where(
                Event.aggregate_id == eval_id,
                Event.event_type == source_event_type,
            )
        )
        assert terminal_event is not None
        outbox = session.scalar(
            select(Outbox).where(
                Outbox.aggregate_id == eval_id,
                Outbox.event_type == "GATE_COMPLETED",
            )
        )
        assert outbox is not None
        envelope = outbox.payload
        bound_event = session.get(Event, envelope["source_event_id"])
        assert bound_event is not None and bound_event.event_type == "eval.bound"
        assert envelope["source_event_type"] == "eval.bound"
        assert bound_event.causation_id == terminal_event.event_id
        assert envelope["causation_id"] == terminal_event.event_id
        assert envelope["payload"] == bound_event.payload
        assert bound_event.payload["workorder_hash"] == workorder["hash"]
        assert bound_event.payload["binding_digest"].startswith("sha256:")
        assert bound_event.payload["report_ref"] == f"eval://{eval_id}"
        assert bound_event.payload["report_digest"] == f"sha256:{bound_event.payload['report_hash']}"


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
    settings = _settings(tmp_path)
    dispatcher = OutboxDispatcher(
        factory,
        settings,
        domain_consumer=_TrustOnlyConsumer(settings),
        worker_id="test:trust",
    )
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
            self.real = _TrustOnlyConsumer(_settings(tmp_path))

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
    release_id, resolved_event_id = _seed_notifying_case(factory, "case_notify_1")
    body_ref, body_digest = _reply_artifact(tmp_path, "notify-1")
    with factory() as session, session.begin():
        queued = NotificationService(session, _settings(tmp_path)).queue(
            case_id="case_notify_1",
            release_id=release_id,
            causation_id=resolved_event_id,
            channel="feishu-mock:contract-replay:",
            thread_ref="thread:1",
            body_ref=body_ref,
            body_digest=body_digest,
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


def test_real_rollback_receipt_closes_case_notifies_origin_and_records_trust_once(
    sqlite_engine, tmp_path
):
    """A configured B1 rollback uses the same durable closure as promote."""

    factory = _factory(sqlite_engine)
    settings = _settings(tmp_path)
    case_id = "case_rollback_closure_1"
    release_id = "rel_rollback_closure_1"
    channel = "feishu-mock:contract-replay:"
    thread_ref = "feishu-mock:contract-replay:om_rollback"
    body_ref, body_digest = _reply_artifact(
        tmp_path,
        "rollback-closure",
        "Canary verification failed; the approved baseline was restored.",
    )
    workorder_payload = make_workorder(
        workorder_id="wo_rollback_closure_1",
        nonce="00000000-0000-0000-0000-000000009901",
        case_id=case_id,
    )

    with factory() as session, session.begin():
        session.add(
            Aggregate(
                aggregate_type="case",
                aggregate_id=case_id,
                state="RELEASING",
                payload={},
                revision=1,
            )
        )
        session.flush()
        store = EventStore(session)
        store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="complaint.received",
            payload={"channel": channel, "thread_ref": thread_ref},
            correlation_id=case_id,
            actor="controller:case",
            expected_revision=1,
        )
        session.add(
            WorkOrder(
                workorder_id=workorder_payload["workorder_id"],
                case_id=case_id,
                hash=workorder_payload["hash"],
                channel=workorder_payload["channel"],
                payload=workorder_payload,
            )
        )
        release_binding = {
            "case_id": case_id,
            "workorder_id": workorder_payload["workorder_id"],
            "workorder_hash": workorder_payload["hash"],
            "target_versionset_digest": workorder_payload["target_versionset_digest"],
            "expected_restore_digest": workorder_payload["base_versionset_digest"],
        }
        store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.requested",
            payload=release_binding,
            correlation_id=case_id,
            actor="controller:release",
            machine="release",
            merge_payload=release_binding,
        )
        store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.staged",
            payload={"revision": 2},
            correlation_id=case_id,
            actor="controller:release",
            expected_revision=1,
            machine="release",
        )
        store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.canary_started",
            payload={"percent": 5, "revision": 3},
            correlation_id=case_id,
            actor="controller:release",
            expected_revision=2,
            machine="release",
        )
        store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.verification_completed",
            payload={"result": "failed"},
            correlation_id=case_id,
            actor="controller:release",
            expected_revision=3,
            machine="release",
            merge_payload={"verification": "failed"},
        )
        store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.rollback_started",
            payload={"reason": "post-canary gate failed"},
            correlation_id=case_id,
            actor="controller:release",
            expected_revision=4,
            machine="release",
            guard="verification=failed",
        )
        terminal = store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.rolled_back",
            payload={
                "restored_digest": workorder_payload["base_versionset_digest"],
                "operation_id": "quality-op-rollback-1",
            },
            correlation_id=case_id,
            actor="controller:release",
            expected_revision=5,
            machine="release",
            merge_payload={
                "rolled_back": True,
                "restored_digest": workorder_payload["base_versionset_digest"],
            },
        )
        terminal_event_id = terminal.event_id
        session.add(
            ReleaseClosure(
                release_id=release_id,
                case_id=case_id,
                channel=channel,
                thread_ref=thread_ref,
                body_ref=body_ref,
                body_digest=body_digest,
                status="configured",
            )
        )

    adapter = FeishuMockAdapter()
    dispatcher = OutboxDispatcher(
        factory,
        settings,
        notification_adapter=adapter,
        worker_id="test:rollback-closure",
    )
    stats = dispatcher.dispatch_batch(limit=20)
    with factory() as session:
        delivery_failures = [
            (row.event_type, row.status, row.last_error)
            for row in session.scalars(select(Outbox).order_by(Outbox.created_at)).all()
            if row.status != "SENT"
        ]
    assert stats["dead"] == 0 and stats["retried"] == 0, delivery_failures

    with factory() as session:
        case = session.get(
            Aggregate,
            {"aggregate_type": "case", "aggregate_id": case_id},
        )
        closure = session.get(ReleaseClosure, release_id)
        entry = session.scalar(
            select(TrustLedgerEntry).where(TrustLedgerEntry.action_ref == release_id)
        )
        resolved = session.scalar(
            select(Event).where(
                Event.aggregate_type == "case",
                Event.aggregate_id == case_id,
                Event.event_type == "case.resolved",
            )
        )
        assert case is not None and case.state == "CLOSED"
        assert (case.payload or {})["resolution"] == "rolled_back"
        assert resolved is not None and resolved.causation_id == terminal_event_id
        assert resolved.payload["resolution_digest"] == workorder_payload["base_versionset_digest"]
        assert closure is not None and closure.status == "queued"
        assert entry is not None and entry.outcome == "failure"
        assert entry.source_event_id == terminal_event_id
        assert session.scalar(
            select(func.count()).select_from(TrustLedgerEntry).where(
                TrustLedgerEntry.action_ref == release_id
            )
        ) == 1
        assert adapter.calls


def test_provider_success_before_ack_is_retried_with_same_idempotency_key(
    sqlite_engine, tmp_path
):
    factory = _factory(sqlite_engine)
    release_id, resolved_event_id = _seed_notifying_case(
        factory, "case_crash_1", thread_ref="thread:crash"
    )
    body_ref, body_digest = _reply_artifact(tmp_path, "notify-crash")
    with factory() as session, session.begin():
        queued = NotificationService(session, _settings(tmp_path)).queue(
            case_id="case_crash_1",
            release_id=release_id,
            causation_id=resolved_event_id,
            channel="feishu-mock:contract-replay:",
            thread_ref="thread:crash",
            body_ref=body_ref,
            body_digest=body_digest,
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


def test_notification_retry_after_provider_dedup_window_fails_closed(
    sqlite_engine, tmp_path
):
    factory = _factory(sqlite_engine)
    release_id, resolved_event_id = _seed_notifying_case(
        factory, "case_expired_dedup", thread_ref="thread:expired"
    )
    body_ref, body_digest = _reply_artifact(tmp_path, "notify-expired")
    with factory() as session, session.begin():
        queued = NotificationService(session, _settings(tmp_path)).queue(
            case_id="case_expired_dedup",
            release_id=release_id,
            causation_id=resolved_event_id,
            channel="feishu-mock:contract-replay:",
            thread_ref="thread:expired",
            body_ref=body_ref,
            body_digest=body_digest,
        )
        row = session.get(Outbox, queued["outbox_id"])
        assert row is not None
        row.attempts = 1
        row.first_attempted_at = datetime.now(timezone.utc) - timedelta(seconds=2)

    class ExpiringAdapter(FeishuMockAdapter):
        idempotency_window_seconds = 1

    adapter = ExpiringAdapter()
    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path),
        notification_adapter=adapter,
        worker_id="test:expired-dedup-window",
    )
    stats = dispatcher.dispatch_batch(limit=1)
    assert stats["dead"] == 1
    assert adapter.calls == []
    with factory() as session:
        row = session.get(Outbox, queued["outbox_id"])
        case = session.get(
            Aggregate,
            {"aggregate_type": "case", "aggregate_id": "case_expired_dedup"},
        )
        assert row is not None and row.status == "DEAD" and row.attempts == 2
        assert "ambiguous beyond the provider dedup window" in (row.last_error or "")
        assert case is not None and case.state == "ESCALATED"
        audit = session.scalar(
            select(Audit).where(
                Audit.action == "outbox.delivery.dead",
                Audit.target == queued["outbox_id"],
            )
        )
        assert audit is not None and audit.error_code == "provider_idempotency_window_expired"


def test_invalid_notification_receipt_dead_letters_and_escalates_case(sqlite_engine, tmp_path):
    factory = _factory(sqlite_engine)
    release_id, resolved_event_id = _seed_notifying_case(
        factory, "case_bad_receipt", thread_ref="thread:bad"
    )
    body_ref, body_digest = _reply_artifact(tmp_path, "notify-bad")
    with factory() as session, session.begin():
        queued = NotificationService(session, _settings(tmp_path)).queue(
            case_id="case_bad_receipt",
            release_id=release_id,
            causation_id=resolved_event_id,
            channel="feishu-mock:contract-replay:",
            thread_ref="thread:bad",
            body_ref=body_ref,
            body_digest=body_digest,
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


def test_live_feishu_receipt_requires_official_provider_origin():
    payload = {
        "thread_ref": "feishu:oc_live:om_original",
        "body_digest": "sha256:" + "1" * 64,
    }
    snapshot = OutboxSnapshot(
        outbox_id="obx_origin_exact",
        aggregate_id="notif_origin_exact",
        source_event_id="evt_origin_exact",
        channel="notification",
        event_type="NOTIFICATION_SENT",
        payload=payload,
        payload_digest="sha256:" + "2" * 64,
        attempts=1,
        claim_token="claim-origin",
        first_attempted_at=datetime.now(timezone.utc),
    )
    receipt = {
        "status": "sent",
        "provider": "feishu",
        "provider_origin": "http://127.0.0.1:9999",
        "provider_message_id": "om_reply",
        "outbox_id": snapshot.outbox_id,
        "payload_digest": snapshot.payload_digest,
        "thread_ref": payload["thread_ref"],
        "body_digest": payload["body_digest"],
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }

    with pytest.raises(OutboxDeliveryError) as exc:
        OutboxDispatcher._validate_provider_receipt(snapshot, receipt)

    assert exc.value.code == "invalid_receipt"


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
