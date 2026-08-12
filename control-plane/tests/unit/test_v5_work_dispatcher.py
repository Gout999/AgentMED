"""V5-2A Work channel dispatcher tests (SQLite layer, D-016 §6 2A-3).

Proves: per-aggregate causal order, consumer idempotency via the reaction
ledger, the cancel reaction submitting only the next owner command, the
legacy lane never touching v5.work.events, and tamper fail-closed to DEAD.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.models import Audit, Outbox
from app.models.tables import OutboxDeliveryReceipt
from app.models.v4_tables import ControllerRegistration
from app.models.v5_work_tables import (
    V5_WORK_EVENT_CHANNEL,
    WorkAttempt,
    WorkReactionLedger,
    WorkTask,
)
from app.services.v4_audit import V4AuditService
from app.services.v5_authority import build_v5_controller_registration_record
from app.services.v5_work_dispatcher import (
    V5WorkDispatcherError,
    WorkReactionDispatcher,
    WorkReactionRelay,
)
from app.services.v5_work_kernel import WorkKernelService
from app.utils.v4_integrity import canonical_digest

NOW = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_work_dispatch"
PRINCIPAL = "prn_work_dispatch"
WORK_CONTROLLER = "prn_work_controller_d"
PROPOSAL_CONTROLLER = "prn_proposal_controller_d"

WORK_COMMANDS = [
    "work.request",
    "work.claim",
    "work.heartbeat",
    "work.cancel-request",
    "work.exhaust",
    "attempts.start",
    "attempts.record-receipt",
    "attempts.complete",
    "attempts.fail",
    "attempts.mark-unknown",
    "attempts.cancel",
    "attempts.reconcile",
]
PROPOSAL_COMMANDS = ["proposals.submit", "proposals.decide"]


def _seed_controller(
    session, *, owner: str, principal: str, commands: list[str], suffix: str
) -> None:
    service_identity_digest = canonical_digest(
        {
            "schema_version": "1.0",
            "workspace_id": WORKSPACE,
            "owner": owner,
            "controller_principal": principal,
            "principal_type": "CONTROLLER_SERVICE",
            "service": "caseloop-control-plane",
        }
    )
    audit = V4AuditService(session).record(
        workspace_id=WORKSPACE,
        actor_principal=PRINCIPAL,
        action="controllers.register",
        target=f"creg_dispatch_{suffix}",
        params={"owner": owner, "service_identity_digest": service_identity_digest},
        transaction_id=f"txn_seed_d_{suffix}",
        evidence_refs={
            "owner": owner,
            "controller_registration_id": f"creg_dispatch_{suffix}",
            "controller_principal": principal,
        },
        occurred_at=NOW - timedelta(minutes=1),
    )
    built = build_v5_controller_registration_record(
        controller_registration_id=f"creg_dispatch_{suffix}",
        workspace_id=WORKSPACE,
        owner=owner,
        controller_principal=principal,
        allowed_commands=commands,
        service_identity_digest=service_identity_digest,
        registered_by_human_principal=PRINCIPAL,
        registration_audit_ref=audit.audit_ref,
        valid_from=NOW - timedelta(minutes=1),
        registered_at=NOW - timedelta(minutes=1),
    )
    session.add(ControllerRegistration(**built.row_values))
    session.flush()


@pytest.fixture()
def kernel(sqlite_session):
    _seed_controller(
        sqlite_session,
        owner="work-controller",
        principal=WORK_CONTROLLER,
        commands=WORK_COMMANDS,
        suffix="work",
    )
    _seed_controller(
        sqlite_session,
        owner="proposal-controller",
        principal=PROPOSAL_CONTROLLER,
        commands=PROPOSAL_COMMANDS,
        suffix="proposal",
    )
    return WorkKernelService(sqlite_session, clock=lambda: NOW)


def _request_and_claim(kernel):
    task = kernel.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "dispatch"},
        requester_principal=PRINCIPAL,
        idempotency_key="req-dispatch",
        request_fingerprint="fp-dispatch",
        transaction_id="txn_d_req",
        request_id="req_d_req",
    )
    result = kernel.claim(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        worker_identity="worker-dispatch",
        transaction_id="txn_d_claim",
        request_id="req_d_claim",
    )
    return task, result


def _outbox_for(sqlite_session, event_type: str) -> Outbox:
    return sqlite_session.scalar(
        sa.select(Outbox).where(
            Outbox.channel == V5_WORK_EVENT_CHANNEL,
            Outbox.event_type == event_type,
        )
    )


def test_dispatch_plain_event_acks_without_reaction(kernel, sqlite_session) -> None:
    task, _ = _request_and_claim(kernel)
    dispatcher = WorkReactionDispatcher(sqlite_session, clock=lambda: NOW)
    row = _outbox_for(sqlite_session, "work.requested")
    assert row is not None and row.status == "PENDING"
    result = dispatcher.dispatch(row)
    assert result.reaction == "none"
    assert row.status == "SENT"
    assert row.receipt == {
        "schema_version": "1.0",
        "status": "consumed",
        "consumer": "v5-work-reaction-dispatcher",
        "outbox_id": row.outbox_id,
        "source_event_id": row.source_event_id,
        "payload_digest": row.payload_digest,
        "reaction": "none",
        "reaction_id": None,
    }
    immutable = sqlite_session.scalar(
        sa.select(OutboxDeliveryReceipt).where(
            OutboxDeliveryReceipt.outbox_id == row.outbox_id
        )
    )
    assert immutable is not None and immutable.receipt == row.receipt
    audit = sqlite_session.scalar(
        sa.select(Audit).where(
            Audit.action == "v5.work.outbox.sent",
            Audit.target == row.outbox_id,
        )
    )
    assert audit is not None
    assert _count(sqlite_session, WorkReactionLedger) == 0


def _count(session, model) -> int:
    return session.scalar(sa.select(sa.func.count()).select_from(model)) or 0


def test_cancel_requested_event_submits_cancel_reaction(
    kernel, sqlite_session
) -> None:
    task, result = _request_and_claim(kernel)
    kernel.cancel_task(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        reason="operator_stop",
        requested_by_principal=PRINCIPAL,
        transaction_id="txn_d_cancel",
        request_id="req_d_cancel",
    )
    dispatcher = WorkReactionDispatcher(sqlite_session, clock=lambda: NOW)
    row = _outbox_for(sqlite_session, "work.cancel_requested")
    outcome = dispatcher.dispatch(row)
    assert outcome.reaction == "submitted"
    reaction = sqlite_session.scalar(sa.select(WorkReactionLedger))
    assert reaction.owner_command == "attempts.cancel"
    assert reaction.status == "SUBMITTED"
    sqlite_session.expire_all()
    attempt = sqlite_session.get(WorkAttempt, result.attempt.attempt_id)
    task = sqlite_session.get(WorkTask, task.task_id)
    assert attempt.state == "CANCELLED"
    assert task.state == "CANCELLED"


def test_redelivery_does_not_mint_second_reaction(kernel, sqlite_session) -> None:
    task, _ = _request_and_claim(kernel)
    kernel.cancel_task(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        reason="operator_stop",
        requested_by_principal=PRINCIPAL,
        transaction_id="txn_d_cancel2",
        request_id="req_d_cancel2",
    )
    dispatcher = WorkReactionDispatcher(sqlite_session, clock=lambda: NOW)
    row = _outbox_for(sqlite_session, "work.cancel_requested")
    first = dispatcher.dispatch(row)
    assert first.reaction == "submitted"
    # Simulate a redelivery of the same source event to a fresh dispatcher.
    row.status = "PENDING"
    sqlite_session.flush()
    second = WorkReactionDispatcher(sqlite_session, clock=lambda: NOW).dispatch(row)
    assert second.reaction == "already_recorded"
    assert _count(sqlite_session, WorkReactionLedger) == 1


def test_causal_order_blocks_later_event(kernel, sqlite_session) -> None:
    task, _ = _request_and_claim(kernel)
    requested = _outbox_for(sqlite_session, "work.requested")
    claimed = _outbox_for(sqlite_session, "work.claimed")
    assert requested is not None and claimed is not None
    dispatcher = WorkReactionDispatcher(sqlite_session, clock=lambda: NOW)
    # The later event must not be claimed while the earlier one is pending.
    picked = dispatcher.claim_next()
    assert picked is not None
    assert picked.event_type == "work.requested"
    dispatcher.dispatch(picked)
    nxt = dispatcher.claim_next()
    assert nxt is not None
    assert nxt.event_type == "work.claimed"


def test_legacy_lane_never_claims_work_channel(kernel, sqlite_session) -> None:
    _request_and_claim(kernel)
    # The legacy dispatcher's channel allowlist must not include the work
    # channel; this is the explicit disposition of its deliberate ignorance.
    from app.services.outbox_relay import LEGACY_OUTBOX_CHANNELS

    assert V5_WORK_EVENT_CHANNEL not in LEGACY_OUTBOX_CHANNELS
    rows = sqlite_session.scalars(
        sa.select(Outbox).where(Outbox.channel == V5_WORK_EVENT_CHANNEL)
    ).all()
    assert rows
    assert all(row.channel == V5_WORK_EVENT_CHANNEL for row in rows)


def test_tampered_payload_goes_dead(kernel, sqlite_session) -> None:
    task, _ = _request_and_claim(kernel)
    row = _outbox_for(sqlite_session, "work.requested")
    row.payload = {"tampered": True}
    sqlite_session.flush()
    dispatcher = WorkReactionDispatcher(sqlite_session, clock=lambda: NOW)
    with pytest.raises(V5WorkDispatcherError) as exc:
        dispatcher.dispatch(row)
    assert exc.value.code == "v5.work.dispatcher_payload_mismatch"
    assert row.status == "DEAD"


def test_dispatcher_rejects_foreign_channel(kernel, sqlite_session) -> None:
    task, _ = _request_and_claim(kernel)
    row = _outbox_for(sqlite_session, "work.requested")
    row.channel = "v5.domain.events"
    sqlite_session.flush()
    dispatcher = WorkReactionDispatcher(sqlite_session, clock=lambda: NOW)
    with pytest.raises(V5WorkDispatcherError) as exc:
        dispatcher.dispatch(row)
    assert exc.value.code == "v5.work.dispatcher_channel_forbidden"


def test_expired_processing_claim_is_reclaimed(kernel, sqlite_session) -> None:
    _request_and_claim(kernel)
    clock = {"now": NOW}
    first_dispatcher = WorkReactionDispatcher(
        sqlite_session,
        clock=lambda: clock["now"],
        worker_id="worker-v5-first",
        claim_ttl_seconds=30,
    )
    first = first_dispatcher.claim_next()
    assert first is not None
    first_token = first.claim_token
    assert first.status == "PROCESSING"
    assert first.attempts == 1

    clock["now"] = NOW + timedelta(seconds=31)
    reclaimed = WorkReactionDispatcher(
        sqlite_session,
        clock=lambda: clock["now"],
        worker_id="worker-v5-second",
        claim_ttl_seconds=30,
    ).claim_next()
    assert reclaimed is not None
    assert reclaimed.outbox_id == first.outbox_id
    assert reclaimed.attempts == 2
    assert reclaimed.claimed_by == "worker-v5-second"
    assert reclaimed.claim_token != first_token


def test_retry_backoff_exhausts_to_dead(
    kernel, sqlite_session, monkeypatch
) -> None:
    _request_and_claim(kernel)
    requested = _outbox_for(sqlite_session, "work.requested")
    for other in sqlite_session.scalars(
        sa.select(Outbox).where(
            Outbox.channel == V5_WORK_EVENT_CHANNEL,
            Outbox.outbox_id != requested.outbox_id,
        )
    ):
        other.status = "SENT"
        other.sent_at = NOW
    sqlite_session.flush()
    clock = {"now": NOW}
    dispatcher = WorkReactionDispatcher(
        sqlite_session,
        clock=lambda: clock["now"],
        max_delivery_attempts=2,
        retry_initial_seconds=1,
        retry_max_seconds=1,
    )

    def fail_temporarily(_row):
        raise RuntimeError("temporary-dispatch-failure")

    monkeypatch.setattr(dispatcher, "_react", fail_temporarily)
    with pytest.raises(RuntimeError, match="temporary-dispatch-failure"):
        dispatcher.poll_once()
    row = requested
    assert row.status == "PENDING"
    assert row.attempts == 1
    assert row.next_retry_at is not None
    assert row.claim_token is None
    assert dispatcher.poll_once() is None

    clock["now"] = NOW + timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="temporary-dispatch-failure"):
        dispatcher.poll_once()
    assert row.status == "DEAD"
    assert row.attempts == 2
    assert row.next_retry_at is None
    assert row.claim_token is None


def test_relay_commits_claim_then_reaction_and_ack(
    kernel, sqlite_session, sqlite_engine
) -> None:
    task, result = _request_and_claim(kernel)
    kernel.cancel_task(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        reason="operator_stop",
        requested_by_principal=PRINCIPAL,
        transaction_id="txn_d_relay_cancel",
        request_id="req_d_relay_cancel",
    )
    sqlite_session.commit()
    factory = sessionmaker(
        bind=sqlite_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    stats = WorkReactionRelay(
        factory,
        clock=lambda: NOW,
        worker_id="worker:v5-work-test",
    ).dispatch_batch(limit=10)
    assert stats == {
        "claimed": 7,
        "sent": 7,
        "retried": 0,
        "dead": 0,
        "blocked": 0,
    }
    with factory() as check:
        rows = check.scalars(
            sa.select(Outbox)
            .where(Outbox.channel == V5_WORK_EVENT_CHANNEL)
            .order_by(Outbox.source_event_seq)
        ).all()
        assert rows and all(row.status == "SENT" for row in rows)
        stored_task = check.get(WorkTask, task.task_id)
        stored_attempt = check.get(WorkAttempt, result.attempt.attempt_id)
        assert stored_task.state == "CANCELLED"
        assert stored_attempt.state == "CANCELLED"
        assert _count(check, WorkReactionLedger) == 1
        assert _count(check, OutboxDeliveryReceipt) == 7


def test_fixed_worker_entrypoint_is_importable() -> None:
    from app.workers.v5_work import main

    assert callable(main)
