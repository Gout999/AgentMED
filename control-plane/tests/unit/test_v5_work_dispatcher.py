"""V5-2A Work channel dispatcher tests (SQLite layer, D-016 §6 2A-3).

Proves: per-aggregate causal order, consumer idempotency via the reaction
ledger, the cancel reaction submitting only the next owner command, the
legacy lane never touching v5.work.events, and tamper fail-closed to DEAD.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.models import Outbox
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
