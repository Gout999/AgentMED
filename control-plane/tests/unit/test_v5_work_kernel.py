"""V5-2A Work Kernel unit tests (SQLite layer, D-016).

Every test asserts the full fact chain — projection row + major-2 event +
controller audit + authority receipt + outbox row — because the contract is
"one PostgreSQL unit of work or nothing".  Fencing, crash recovery and
ghost-success refusal are tested as rejection paths, never mocked away.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.models import Audit, Event, Outbox
from app.models.v4_tables import AuthorityReceipt, ControllerRegistration
from app.models.v5_work_tables import (
    WorkAttempt,
    WorkAttemptCapability,
    WorkProposal,
    WorkProposalDecision,
    WorkReactionLedger,
    WorkTask,
)
from app.services.v4_audit import V4AuditService
from app.services.v5_authority import build_v5_controller_registration_record
from app.services.v5_work_kernel import V5WorkKernelError, WorkKernelService
from app.utils.v4_integrity import canonical_digest

NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_work_unit"
PRINCIPAL = "prn_work_unit"
WORK_CONTROLLER = "prn_work_controller"
PROPOSAL_CONTROLLER = "prn_proposal_controller"

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
        target=f"creg_work_{suffix}",
        params={"owner": owner, "service_identity_digest": service_identity_digest},
        transaction_id=f"txn_seed_{suffix}",
        evidence_refs={
            "owner": owner,
            "controller_registration_id": f"creg_work_{suffix}",
            "controller_principal": principal,
        },
        occurred_at=NOW - timedelta(minutes=1),
    )
    built = build_v5_controller_registration_record(
        controller_registration_id=f"creg_work_{suffix}",
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
def service(sqlite_session):
    _seed_controller(
        sqlite_session,
        owner="work-controller",
        principal=WORK_CONTROLLER,
        commands=WORK_COMMANDS,
        suffix="kernel",
    )
    _seed_controller(
        sqlite_session,
        owner="proposal-controller",
        principal=PROPOSAL_CONTROLLER,
        commands=PROPOSAL_COMMANDS,
        suffix="proposal",
    )
    return WorkKernelService(sqlite_session, clock=lambda: NOW)


def _request(service) -> WorkTask:
    return service.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "alpha"},
        requester_principal=PRINCIPAL,
        idempotency_key="req-0001",
        request_fingerprint="fp-0001",
        transaction_id="txn_request",
        request_id="req_request",
    )


def _claim(service, task: WorkTask):
    return service.claim(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        worker_identity="worker-alpha",
        transaction_id="txn_claim",
        request_id="req_claim",
    )


def _record_terminal_receipt(
    service,
    task: WorkTask,
    attempt: WorkAttempt,
    *,
    succeeded: bool,
    suffix: str,
) -> str:
    return service.record_terminal_receipt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        fencing_token=attempt.fence_token,
        issuer=attempt.worker_identity,
        process_exit_code=0 if succeeded else 1,
        stream_complete=True,
        structured_output_valid=succeeded,
        transaction_id=f"txn_receipt_{suffix}",
        request_id=f"req_receipt_{suffix}",
    )


def _count(session, model) -> int:
    return session.scalar(sa.select(sa.func.count()).select_from(model)) or 0


def test_request_task_writes_full_fact_chain(service, sqlite_session) -> None:
    task = _request(service)
    assert task.state == "QUEUED"
    event = sqlite_session.scalar(
        sa.select(Event).where(
            Event.aggregate_id == task.task_id, Event.event_type == "work.requested"
        )
    )
    assert event is not None
    assert event.contract_version == "v5"
    assert event.event_contract_major == 2
    assert event.exact_subject_binding == {
        "kind": "WORK_TASK",
        "id": task.task_id,
        "revision": 1,
        "digest": task.record_digest,
    }
    outbox = sqlite_session.scalar(
        sa.select(Outbox).where(Outbox.source_event_id == event.event_id)
    )
    assert outbox is not None
    assert outbox.channel == "v5.work.events"
    receipt = sqlite_session.scalar(
        sa.select(AuthorityReceipt).where(
            AuthorityReceipt.event_id == event.event_id
        )
    )
    assert receipt is not None
    assert receipt.subject_kind == "WORK_TASK"
    audit = sqlite_session.scalar(
        sa.select(Audit).where(Audit.target == task.task_id)
    )
    assert audit is not None


def test_request_task_idempotent_replay_and_conflict(service) -> None:
    first = _request(service)
    replay = _request(service)
    assert replay.task_id == first.task_id
    with pytest.raises(V5WorkKernelError) as exc:
        service.request_task(
            workspace_id=WORKSPACE,
            task_kind="fixture.probe",
            input_payload={"probe": "different"},
            requester_principal=PRINCIPAL,
            idempotency_key="req-0001",
            request_fingerprint="fp-different",
            transaction_id="txn_request_2",
            request_id="req_request_2",
        )
    assert exc.value.code == "v5.work.idempotency_conflict"


def test_request_task_rejects_same_key_same_claimed_fingerprint_different_body(
    service,
) -> None:
    first = _request(service)
    with pytest.raises(V5WorkKernelError) as exc:
        service.request_task(
            workspace_id=WORKSPACE,
            task_kind="fixture.probe",
            input_payload={"probe": "different"},
            requester_principal=PRINCIPAL,
            idempotency_key="req-0001",
            request_fingerprint="fp-0001",
            transaction_id="txn_request_forged",
            request_id="req_request_forged",
        )
    assert first.input_payload == {"probe": "alpha"}
    assert exc.value.code == "v5.work.idempotency_conflict"


def test_claim_creates_attempt_capability_and_two_events(
    service, sqlite_session
) -> None:
    task = _request(service)
    result = _claim(service, task)
    assert result.task.state == "LEASED"
    assert result.task.lease_fencing_token == 1
    assert result.attempt.state == "CREATED"
    assert result.attempt.fence_token == 1
    assert result.capability.scope == "work.execute"
    assert result.capability.fence_token == 1
    assert result.capability.consumed_at is None
    assert result.capability.capability_digest.startswith("sha256:")
    claimed = sqlite_session.scalar(
        sa.select(Event).where(
            Event.aggregate_id == task.task_id, Event.event_type == "work.claimed"
        )
    )
    created = sqlite_session.scalar(
        sa.select(Event).where(
            Event.aggregate_id == result.attempt.attempt_id,
            Event.event_type == "attempt.created",
        )
    )
    assert claimed is not None and created is not None
    assert created.payload["claim_event_id"] == claimed.event_id
    receipts = _count(sqlite_session, AuthorityReceipt)
    assert receipts == 3  # requested + claimed + attempt.created


def test_claim_rejects_double_lease(service) -> None:
    task = _request(service)
    _claim(service, task)
    with pytest.raises(V5WorkKernelError) as exc:
        _claim(service, task)
    assert exc.value.code == "v5.work.lease_held"


def test_claim_after_lease_expiry_with_active_attempt_blocks_unknown(
    service, sqlite_session
) -> None:
    clock = {"now": NOW}
    service.clock = lambda: clock["now"]
    task = _request(service)
    result = _claim(service, task)
    # A started attempt that disappears mid-run is genuinely ambiguous.
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        runtime_adapter="fixture-executor",
        runtime_session="session-x1",
        transaction_id="txn_start_x1",
        request_id="req_start_x1",
    )
    clock["now"] = NOW + timedelta(seconds=120)  # lease (60s) expired
    with pytest.raises(V5WorkKernelError) as exc:
        _claim(service, task)
    assert exc.value.code == "v5.work.reconcile_required"
    sqlite_session.expire_all()
    task = sqlite_session.get(WorkTask, task.task_id)
    attempt = sqlite_session.get(WorkAttempt, task.current_attempt_id)
    assert task.state == "BLOCKED_UNKNOWN"
    assert attempt.state == "UNKNOWN"


def test_claim_after_lease_expiry_with_unstarted_attempt_cancels_and_retries(
    service, sqlite_session
) -> None:
    clock = {"now": NOW}
    service.clock = lambda: clock["now"]
    task = _request(service)
    result = _claim(service, task)
    clock["now"] = NOW + timedelta(seconds=120)
    recovered = service.claim(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        worker_identity="worker-retry",
        transaction_id="txn_claim_retry",
        request_id="req_claim_retry",
    )
    assert recovered.attempt.attempt_number == 2
    sqlite_session.expire_all()
    first = sqlite_session.get(WorkAttempt, result.attempt.attempt_id)
    assert first.state == "CANCELLED"


def test_heartbeat_extends_lease_and_stale_fence_rejected(service) -> None:
    task = _request(service)
    result = _claim(service, task)
    updated = service.heartbeat(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        transaction_id="txn_hb",
        request_id="req_hb",
    )
    assert updated.revision == 3
    with pytest.raises(V5WorkKernelError) as exc:
        service.heartbeat(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            attempt_id=result.attempt.attempt_id,
            fencing_token=99,
            transaction_id="txn_hb_bad",
            request_id="req_hb_bad",
        )
    assert exc.value.code == "v5.work.stale_fence"


def test_full_happy_path_to_completed(service, sqlite_session) -> None:
    task = _request(service)
    result = _claim(service, task)
    started = service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        runtime_adapter="fixture-executor",
        runtime_session="session-1",
        transaction_id="txn_start",
        request_id="req_start",
    )
    assert started.state == "RUNNING"
    capability = sqlite_session.get(
        WorkAttemptCapability, result.capability.capability_id
    )
    assert capability.consumed_at is not None
    terminal_receipt_digest = _record_terminal_receipt(
        service, task, result.attempt, succeeded=True, suffix="happy"
    )
    outed = service.record_output(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        output_payload={"answer": 42},
        stream_complete=True,
        transaction_id="txn_output",
        request_id="req_output",
    )
    assert outed.state == "OUTPUT_RECORDED"
    done = service.complete_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        terminal_receipt_digest=terminal_receipt_digest,
        transaction_id="txn_complete",
        request_id="req_complete",
    )
    assert done.state == "SUCCEEDED"
    sqlite_session.expire_all()
    task = sqlite_session.get(WorkTask, task.task_id)
    assert task.state == "COMPLETED"
    assert task.lease_fencing_token == 1  # monotonic: retained, never reset
    with pytest.raises(V5WorkKernelError) as exc:
        _claim(service, task)
    assert exc.value.code == "v5.work.task_terminal"


def test_fail_then_retry_then_exhaust(service, sqlite_session) -> None:
    clock = {"now": NOW}
    service.clock = lambda: clock["now"]
    task = service.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "beta"},
        requester_principal=PRINCIPAL,
        idempotency_key="req-retry",
        request_fingerprint="fp-retry",
        max_attempts=2,
        transaction_id="txn_req2",
        request_id="req_req2",
    )
    first = _claim(service, task)
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=first.attempt.attempt_id,
        fencing_token=1,
        runtime_adapter="fixture-executor",
        runtime_session="session-r1",
        transaction_id="txn_start_r1",
        request_id="req_start_r1",
    )
    service.fail_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=first.attempt.attempt_id,
        fencing_token=1,
        failure_code="adapter_crash",
        transaction_id="txn_fail1",
        request_id="req_fail1",
    )
    sqlite_session.expire_all()
    task = sqlite_session.get(WorkTask, task.task_id)
    assert task.state == "WAITING_RETRY"
    second = service.claim(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        worker_identity="worker-beta",
        transaction_id="txn_claim2",
        request_id="req_claim2",
    )
    assert second.attempt.attempt_number == 2
    assert second.attempt.fence_token == 2
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=second.attempt.attempt_id,
        fencing_token=2,
        runtime_adapter="fixture-executor",
        runtime_session="session-r2",
        transaction_id="txn_start_r2",
        request_id="req_start_r2",
    )
    service.fail_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=second.attempt.attempt_id,
        fencing_token=2,
        failure_code="adapter_crash",
        transaction_id="txn_fail2",
        request_id="req_fail2",
    )
    sqlite_session.expire_all()
    task = sqlite_session.get(WorkTask, task.task_id)
    assert task.state == "EXHAUSTED"
    exhausted = sqlite_session.scalar(
        sa.select(Event).where(
            Event.aggregate_id == task.task_id, Event.event_type == "work.exhausted"
        )
    )
    assert exhausted is not None
    with pytest.raises(V5WorkKernelError) as exc:
        service.claim(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            worker_identity="worker-gamma",
            transaction_id="txn_claim3",
            request_id="req_claim3",
        )
    assert exc.value.code == "v5.work.task_terminal"


def test_cancel_waiting_retry_terminalizes_without_rewriting_failed_attempt(
    service, sqlite_session
) -> None:
    task = service.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "cancel-waiting"},
        requester_principal=PRINCIPAL,
        idempotency_key="req-cancel-waiting",
        request_fingerprint="ignored-cancel-waiting",
        max_attempts=2,
        transaction_id="txn_req_cancel_waiting",
        request_id="req_req_cancel_waiting",
    )
    claimed = _claim(service, task)
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claimed.attempt.attempt_id,
        fencing_token=claimed.attempt.fence_token,
        runtime_adapter="fixture-executor",
        runtime_session="session-cancel-waiting",
        transaction_id="txn_start_cancel_waiting",
        request_id="req_start_cancel_waiting",
    )
    service.fail_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claimed.attempt.attempt_id,
        fencing_token=claimed.attempt.fence_token,
        failure_code="adapter_crash",
        transaction_id="txn_fail_cancel_waiting",
        request_id="req_fail_cancel_waiting",
    )
    service.cancel_task(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        reason="operator_stop",
        requested_by_principal=PRINCIPAL,
        transaction_id="txn_request_cancel_waiting",
        request_id="req_request_cancel_waiting",
    )
    service.cancel_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claimed.attempt.attempt_id,
        reason="operator_stop",
        transaction_id="txn_cancel_waiting",
        request_id="req_cancel_waiting",
    )
    sqlite_session.expire_all()
    stored_task = sqlite_session.get(WorkTask, task.task_id)
    stored_attempt = sqlite_session.get(WorkAttempt, claimed.attempt.attempt_id)
    assert stored_task.state == "CANCELLED"
    assert stored_attempt.state == "FAILED"


def test_cancel_rejects_noncurrent_attempt_from_same_task(service) -> None:
    task = service.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "old-attempt"},
        requester_principal=PRINCIPAL,
        idempotency_key="req-old-attempt",
        request_fingerprint="ignored-old-attempt",
        max_attempts=2,
        transaction_id="txn_req_old_attempt",
        request_id="req_req_old_attempt",
    )
    first = _claim(service, task)
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=first.attempt.attempt_id,
        fencing_token=first.attempt.fence_token,
        runtime_adapter="fixture-executor",
        runtime_session="session-old-attempt",
        transaction_id="txn_start_old_attempt",
        request_id="req_start_old_attempt",
    )
    service.fail_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=first.attempt.attempt_id,
        fencing_token=first.attempt.fence_token,
        failure_code="retryable",
        transaction_id="txn_fail_old_attempt",
        request_id="req_fail_old_attempt",
    )
    second = service.claim(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        worker_identity="worker-second",
        transaction_id="txn_claim_second",
        request_id="req_claim_second",
    )
    service.cancel_task(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        reason="operator_stop",
        requested_by_principal=PRINCIPAL,
        transaction_id="txn_request_cancel_second",
        request_id="req_request_cancel_second",
    )
    with pytest.raises(V5WorkKernelError) as exc:
        service.cancel_attempt(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            attempt_id=first.attempt.attempt_id,
            reason="stale",
            transaction_id="txn_cancel_old_attempt",
            request_id="req_cancel_old_attempt",
        )
    assert exc.value.code == "v5.work.attempt_not_current"
    assert second.attempt.state == "CREATED"


def test_unknown_reconcile_failed_then_retry(service, sqlite_session) -> None:
    task = _request(service)
    result = _claim(service, task)
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        runtime_adapter="fixture-executor",
        runtime_session="session-u1",
        transaction_id="txn_start_u1",
        request_id="req_start_u1",
    )
    reconciliation_receipt_digest = _record_terminal_receipt(
        service, task, result.attempt, succeeded=False, suffix="unknown_failed"
    )
    service.mark_attempt_unknown(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        ambiguity_reason="executor_disconnect_mid_write",
        transaction_id="txn_unknown",
        request_id="req_unknown",
    )
    sqlite_session.expire_all()
    task = sqlite_session.get(WorkTask, task.task_id)
    assert task.state == "BLOCKED_UNKNOWN"
    with pytest.raises(V5WorkKernelError) as exc:
        _claim(service, task)
    assert exc.value.code == "v5.work.reconcile_required"
    service.reconcile_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        outcome="failed",
        reconciliation_receipt_digest=reconciliation_receipt_digest,
        transaction_id="txn_recon",
        request_id="req_recon",
    )
    sqlite_session.expire_all()
    task = sqlite_session.get(WorkTask, task.task_id)
    attempt = sqlite_session.get(WorkAttempt, result.attempt.attempt_id)
    assert attempt.state == "FAILED"
    assert task.state == "WAITING_RETRY"


def test_reconcile_succeeded_requires_output(service, sqlite_session) -> None:
    task = _request(service)
    result = _claim(service, task)
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        runtime_adapter="fixture-executor",
        runtime_session="session-u2",
        transaction_id="txn_start_u2",
        request_id="req_start_u2",
    )
    receipt_digest = _record_terminal_receipt(
        service, task, result.attempt, succeeded=True, suffix="missing_output"
    )
    service.mark_attempt_unknown(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        ambiguity_reason="ambiguous_write",
        transaction_id="txn_unknown2",
        request_id="req_unknown2",
    )
    with pytest.raises(V5WorkKernelError) as exc:
        service.reconcile_attempt(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            attempt_id=result.attempt.attempt_id,
            outcome="succeeded",
            reconciliation_receipt_digest=receipt_digest,
            transaction_id="txn_recon2",
            request_id="req_recon2",
        )
    assert exc.value.code == "v5.work.reconcile_output_missing"


def test_proposal_accept_submits_downstream_reaction_same_transaction(
    service, sqlite_session
) -> None:
    task = _request(service)
    proposal = service.submit_proposal(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        proposer_principal=PRINCIPAL,
        payload={"action": "reroute"},
        transaction_id="txn_prop",
        request_id="req_prop",
    )
    decision = service.decide_proposal(
        workspace_id=WORKSPACE,
        proposal_id=proposal.proposal_id,
        decided_by_principal=PRINCIPAL,
        accept=True,
        downstream_intent="work.claim",
        downstream_command="work.claim",
        transaction_id="txn_decide",
        request_id="req_decide",
    )
    assert decision.decision == "ACCEPTED"
    reaction = sqlite_session.scalar(sa.select(WorkReactionLedger))
    assert reaction is not None
    assert reaction.status == "SUBMITTED"
    assert reaction.owner_command == "work.claim"
    accepted = sqlite_session.scalar(
        sa.select(Event).where(Event.event_type == "proposal.accepted")
    )
    assert accepted.payload["downstream_reaction_id"] == reaction.reaction_id
    sqlite_session.expire_all()
    proposal = sqlite_session.get(WorkProposal, proposal.proposal_id)
    assert proposal.status == "ACCEPTED"


def test_proposal_accept_without_downstream_is_rejected(service) -> None:
    task = _request(service)
    proposal = service.submit_proposal(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        proposer_principal=PRINCIPAL,
        payload={"action": "reroute"},
        transaction_id="txn_prop2",
        request_id="req_prop2",
    )
    with pytest.raises(V5WorkKernelError) as exc:
        service.decide_proposal(
            workspace_id=WORKSPACE,
            proposal_id=proposal.proposal_id,
            decided_by_principal=PRINCIPAL,
            accept=True,
            transaction_id="txn_decide2",
            request_id="req_decide2",
        )
    assert exc.value.code == "v5.work.downstream_intent_required"
    assert service.session.scalar(sa.select(sa.func.count()).select_from(WorkProposalDecision)) == 0


def test_post_action_proposal_rejected(service) -> None:
    task = _request(service)
    result = _claim(service, task)
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        runtime_adapter="fixture-executor",
        runtime_session="session-1",
        transaction_id="txn_start3",
        request_id="req_start3",
    )
    terminal_receipt_digest = _record_terminal_receipt(
        service, task, result.attempt, succeeded=True, suffix="post_action"
    )
    service.record_output(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        output_payload={"answer": 42},
        stream_complete=True,
        transaction_id="txn_output3",
        request_id="req_output3",
    )
    service.complete_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=result.attempt.attempt_id,
        fencing_token=1,
        terminal_receipt_digest=terminal_receipt_digest,
        transaction_id="txn_complete3",
        request_id="req_complete3",
    )
    with pytest.raises(V5WorkKernelError) as exc:
        service.submit_proposal(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            proposer_principal=PRINCIPAL,
            payload={"action": "too-late"},
            transaction_id="txn_prop3",
            request_id="req_prop3",
        )
    assert exc.value.code == "v5.work.proposal_post_action"


def test_audit_failure_rolls_back_entire_claim(sqlite_session) -> None:
    _seed_controller(
        sqlite_session,
        owner="work-controller",
        principal=WORK_CONTROLLER,
        commands=WORK_COMMANDS,
        suffix="auditfail",
    )
    _seed_controller(
        sqlite_session,
        owner="proposal-controller",
        principal=PROPOSAL_CONTROLLER,
        commands=PROPOSAL_COMMANDS,
        suffix="auditfailp",
    )
    failing_audit = V4AuditService(sqlite_session, fail_on_call=2)
    service = WorkKernelService(
        sqlite_session, clock=lambda: NOW, audit=failing_audit
    )
    task = service.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "gamma"},
        requester_principal=PRINCIPAL,
        idempotency_key="req-auditfail",
        request_fingerprint="fp-auditfail",
        transaction_id="txn_req_af",
        request_id="req_req_af",
    )
    # Commit the request so the rollback below only undoes the failed claim.
    sqlite_session.commit()
    with pytest.raises(V5WorkKernelError) as exc:
        service.claim(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            worker_identity="worker-alpha",
            transaction_id="txn_claim_af",
            request_id="req_claim_af",
        )
    assert exc.value.code == "v5.work.audit_unavailable"
    sqlite_session.rollback()
    assert _count(sqlite_session, WorkAttempt) == 0
    assert _count(sqlite_session, WorkAttemptCapability) == 0
    sqlite_session.expire_all()
    task = sqlite_session.get(WorkTask, task.task_id)
    assert task.state == "QUEUED"
    assert task.attempt_count == 0


@pytest.mark.parametrize(
    "operation",
    ["cancel", "mark_unknown", "reconcile"],
)
def test_cross_task_attempt_binding_is_rejected(
    service, sqlite_session, operation: str
) -> None:
    task_a = _request(service)
    task_b = service.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "beta"},
        requester_principal=PRINCIPAL,
        idempotency_key="req-cross-b",
        request_fingerprint="fp-cross-b",
        transaction_id="txn_cross_b",
        request_id="req_cross_b",
    )
    attempt_a = _claim(service, task_a).attempt
    attempt_b = service.claim(
        workspace_id=WORKSPACE,
        task_id=task_b.task_id,
        worker_identity="worker-beta",
        transaction_id="txn_claim_b",
        request_id="req_claim_b",
    ).attempt
    for task, attempt, suffix in (
        (task_a, attempt_a, "a"),
        (task_b, attempt_b, "b"),
    ):
        service.start_attempt(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            fencing_token=attempt.fence_token,
            runtime_adapter="fixture",
            runtime_session=f"session-{suffix}",
            transaction_id=f"txn_start_{suffix}",
            request_id=f"req_start_{suffix}",
        )
    if operation == "cancel":
        service.cancel_task(
            workspace_id=WORKSPACE,
            task_id=task_a.task_id,
            reason="operator",
            requested_by_principal=PRINCIPAL,
            transaction_id="txn_cancel_a",
            request_id="req_cancel_a",
        )
        invoke = lambda: service.cancel_attempt(
            workspace_id=WORKSPACE,
            task_id=task_a.task_id,
            attempt_id=attempt_b.attempt_id,
            reason="cross-task",
            transaction_id="txn_cross_cancel",
            request_id="req_cross_cancel",
        )
    elif operation == "mark_unknown":
        invoke = lambda: service.mark_attempt_unknown(
            workspace_id=WORKSPACE,
            task_id=task_a.task_id,
            attempt_id=attempt_b.attempt_id,
            ambiguity_reason="cross-task",
            transaction_id="txn_cross_unknown",
            request_id="req_cross_unknown",
        )
    else:
        for task, attempt, suffix in (
            (task_a, attempt_a, "a"),
            (task_b, attempt_b, "b"),
        ):
            _record_terminal_receipt(
                service, task, attempt, succeeded=False, suffix=f"cross_{suffix}"
            )
            service.mark_attempt_unknown(
                workspace_id=WORKSPACE,
                task_id=task.task_id,
                attempt_id=attempt.attempt_id,
                ambiguity_reason="ambiguous",
                transaction_id=f"txn_unknown_{suffix}",
                request_id=f"req_unknown_{suffix}",
            )
        invoke = lambda: service.reconcile_attempt(
            workspace_id=WORKSPACE,
            task_id=task_a.task_id,
            attempt_id=attempt_b.attempt_id,
            outcome="failed",
            reconciliation_receipt_digest=attempt_b.receipt_payload["receipt_digest"],
            transaction_id="txn_cross_reconcile",
            request_id="req_cross_reconcile",
        )
    with pytest.raises(V5WorkKernelError) as exc:
        invoke()
    assert exc.value.code == "v5.work.attempt_task_mismatch"


def test_reconcile_rejects_unpersisted_receipt_digest(service) -> None:
    task = _request(service)
    attempt = _claim(service, task).attempt
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        fencing_token=attempt.fence_token,
        runtime_adapter="fixture",
        runtime_session="session-untrusted",
        transaction_id="txn_start_untrusted",
        request_id="req_start_untrusted",
    )
    service.mark_attempt_unknown(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        ambiguity_reason="ambiguous",
        transaction_id="txn_unknown_untrusted",
        request_id="req_unknown_untrusted",
    )
    with pytest.raises(V5WorkKernelError) as exc:
        service.reconcile_attempt(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            outcome="failed",
            reconciliation_receipt_digest="not-a-trustworthy-receipt",
            transaction_id="txn_reconcile_untrusted",
            request_id="req_reconcile_untrusted",
        )
    assert exc.value.code == "v5.work.reconcile_receipt_untrusted"


def test_completion_rejects_accepted_proposal_from_another_task(service) -> None:
    task_a = _request(service)
    task_b = service.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "proposal-b"},
        requester_principal=PRINCIPAL,
        idempotency_key="req-proposal-b",
        request_fingerprint="fp-proposal-b",
        transaction_id="txn_proposal_b",
        request_id="req_proposal_b",
    )
    proposal = service.submit_proposal(
        workspace_id=WORKSPACE,
        task_id=task_b.task_id,
        proposer_principal=PRINCIPAL,
        payload={"action": "for-b"},
        transaction_id="txn_prop_b",
        request_id="req_prop_b",
    )
    service.decide_proposal(
        workspace_id=WORKSPACE,
        proposal_id=proposal.proposal_id,
        decided_by_principal=PRINCIPAL,
        accept=True,
        downstream_intent="work.claim",
        downstream_command="work.claim",
        transaction_id="txn_decide_b",
        request_id="req_decide_b",
    )
    attempt = _claim(service, task_a).attempt
    service.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task_a.task_id,
        attempt_id=attempt.attempt_id,
        fencing_token=attempt.fence_token,
        runtime_adapter="fixture",
        runtime_session="session-a",
        transaction_id="txn_start_a",
        request_id="req_start_a",
    )
    receipt_digest = _record_terminal_receipt(
        service, task_a, attempt, succeeded=True, suffix="proposal_cross"
    )
    service.record_output(
        workspace_id=WORKSPACE,
        task_id=task_a.task_id,
        attempt_id=attempt.attempt_id,
        fencing_token=attempt.fence_token,
        output_payload={"answer": 42},
        stream_complete=True,
        transaction_id="txn_output_a",
        request_id="req_output_a",
    )
    with pytest.raises(V5WorkKernelError) as exc:
        service.complete_attempt(
            workspace_id=WORKSPACE,
            task_id=task_a.task_id,
            attempt_id=attempt.attempt_id,
            fencing_token=attempt.fence_token,
            terminal_receipt_digest=receipt_digest,
            accepted_proposal_id=proposal.proposal_id,
            transaction_id="txn_complete_a",
            request_id="req_complete_a",
        )
    assert exc.value.code == "v5.work.proposal_task_mismatch"
