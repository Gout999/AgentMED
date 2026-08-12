"""V5-2A deterministic fixture executor tests (Master §6 2A-4).

Crash injection before/after claim, after output, and after decision, plus
the three mandatory rejections: post-action proposal, ghost success, and
ambiguous outcome.  Everything runs on the deterministic fixture executor —
no model, AgentTeams, or provider is involved, so these are contract/replay
facts, never live-facet claims.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.models.v4_tables import ControllerRegistration
from app.models.v5_work_tables import WorkAttempt, WorkTask
from app.services.v4_audit import V4AuditService
from app.services.v5_authority import build_v5_controller_registration_record
from app.services.v5_work_fixture_executor import FixtureWorkExecutor
from app.services.v5_work_kernel import V5WorkKernelError, WorkKernelService
from app.utils.v4_integrity import canonical_digest

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_work_fixture"
PRINCIPAL = "prn_work_fixture"
WORK_CONTROLLER = "prn_work_controller_f"
PROPOSAL_CONTROLLER = "prn_proposal_controller_f"

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
    digest = canonical_digest(
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
        target=f"creg_fx_{suffix}",
        params={"owner": owner, "service_identity_digest": digest},
        transaction_id=f"txn_seed_f_{suffix}",
        evidence_refs={
            "owner": owner,
            "controller_registration_id": f"creg_fx_{suffix}",
            "controller_principal": principal,
        },
        occurred_at=NOW - timedelta(minutes=1),
    )
    built = build_v5_controller_registration_record(
        controller_registration_id=f"creg_fx_{suffix}",
        workspace_id=WORKSPACE,
        owner=owner,
        controller_principal=principal,
        allowed_commands=commands,
        service_identity_digest=digest,
        registered_by_human_principal=PRINCIPAL,
        registration_audit_ref=audit.audit_ref,
        valid_from=NOW - timedelta(minutes=1),
        registered_at=NOW - timedelta(minutes=1),
    )
    session.add(ControllerRegistration(**built.row_values))
    session.flush()


@pytest.fixture()
def executor(sqlite_session):
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
    return FixtureWorkExecutor(sqlite_session, clock=lambda: NOW)


def test_deterministic_output_is_a_pure_function(executor) -> None:
    left = FixtureWorkExecutor.deterministic_output({"probe": "p1"})
    right = FixtureWorkExecutor.deterministic_output({"probe": "p1"})
    assert left == right
    other = FixtureWorkExecutor.deterministic_output({"probe": "p2"})
    assert other["result_digest"] != left["result_digest"]


def test_happy_path_runs_to_completed(executor, sqlite_session) -> None:
    result = executor.run_to_completion(
        workspace_id=WORKSPACE, probe="alpha", idempotency_key="fx-1"
    )
    assert result.terminal_state == "SUCCEEDED"
    assert result.output_digest.startswith("sha256:")
    sqlite_session.expire_all()
    task = sqlite_session.get(WorkTask, result.task.task_id)
    assert task.state == "COMPLETED"


def test_crash_after_claim_then_recover_via_cancel_and_retry(
    executor, sqlite_session
) -> None:
    """CREATED-stage crash: the worker claimed but never started, so nothing
    is ambiguous.  The expired lease cancels the attempt on legal hops and
    returns the task to WAITING_RETRY; the next claim mints attempt 2."""
    clock = {"now": NOW}
    executor.kernel.clock = lambda: clock["now"]
    claim = executor.crash_after_claim(
        workspace_id=WORKSPACE, probe="beta", idempotency_key="fx-2"
    )
    task_id = claim.task.task_id
    clock["now"] = NOW + timedelta(seconds=300)  # lease (60s) expired
    recovered = executor.claim(workspace_id=WORKSPACE, task_id=task_id)
    assert recovered.attempt.attempt_number == 2
    sqlite_session.expire_all()
    first = sqlite_session.get(WorkAttempt, claim.attempt.attempt_id)
    assert first.state == "CANCELLED"


def test_crash_after_output_complete_then_task_settles(
    executor, sqlite_session
) -> None:
    clock = {"now": NOW}
    executor.kernel.clock = lambda: clock["now"]
    claim = executor.crash_after_output(
        workspace_id=WORKSPACE, probe="gamma", idempotency_key="fx-3"
    )
    task_id = claim.task.task_id
    # The "recovered" worker holds the same live lease: completion with the
    # valid fence token still succeeds exactly once.
    finished = executor.kernel.complete_attempt(
        workspace_id=WORKSPACE,
        task_id=task_id,
        attempt_id=claim.attempt.attempt_id,
        fencing_token=claim.attempt.fence_token,
        terminal_receipt_digest=claim.attempt.receipt_payload["receipt_digest"],
        transaction_id="txn_fx_done",
        request_id="req_fx_done",
    )
    assert finished.state == "SUCCEEDED"


def test_ambiguous_outcome_rejected_until_reconciled(
    executor, sqlite_session
) -> None:
    """Crash after output (a started attempt): the outcome is genuinely
    ambiguous, so the lease expiry fails closed into UNKNOWN and re-claim is
    rejected until an explicit reconcile."""
    clock = {"now": NOW}
    executor.kernel.clock = lambda: clock["now"]
    claim = executor.crash_after_output(
        workspace_id=WORKSPACE, probe="delta", idempotency_key="fx-4"
    )
    clock["now"] = NOW + timedelta(seconds=300)
    with pytest.raises(V5WorkKernelError) as exc:
        executor.claim(workspace_id=WORKSPACE, task_id=claim.task.task_id)
    assert exc.value.code == "v5.work.reconcile_required"
    sqlite_session.expire_all()
    attempt = sqlite_session.get(WorkAttempt, claim.attempt.attempt_id)
    assert attempt.state == "UNKNOWN"
    # Writing over the ambiguous attempt without reconcile is refused.
    with pytest.raises(V5WorkKernelError) as exc:
        executor.kernel.record_output(
            workspace_id=WORKSPACE,
            task_id=claim.task.task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=claim.attempt.fence_token,
            output_payload={"late": True},
            stream_complete=True,
            transaction_id="txn_fx_late",
            request_id="req_fx_late",
        )
    assert exc.value.code in {"v5.work.lease_lost", "v5.work.stale_fence"}


def test_post_action_proposal_rejected(executor, sqlite_session) -> None:
    result = executor.run_to_completion(
        workspace_id=WORKSPACE, probe="epsilon", idempotency_key="fx-5"
    )
    with pytest.raises(V5WorkKernelError) as exc:
        executor.kernel.submit_proposal(
            workspace_id=WORKSPACE,
            task_id=result.task.task_id,
            proposer_principal=PRINCIPAL,
            payload={"action": "too-late"},
            transaction_id="txn_fx_prop",
            request_id="req_fx_prop",
        )
    assert exc.value.code == "v5.work.proposal_post_action"


def test_ghost_success_rejected(executor, sqlite_session) -> None:
    task = executor.request(
        workspace_id=WORKSPACE, probe="zeta", idempotency_key="fx-6"
    )
    proposal = executor.kernel.submit_proposal(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        proposer_principal=PRINCIPAL,
        payload={"action": "accept-me"},
        transaction_id="txn_fx_prop2",
        request_id="req_fx_prop2",
    )
    with pytest.raises(V5WorkKernelError) as exc:
        executor.kernel.decide_proposal(
            workspace_id=WORKSPACE,
            proposal_id=proposal.proposal_id,
            decided_by_principal=PRINCIPAL,
            accept=True,
            transaction_id="txn_fx_dec2",
            request_id="req_fx_dec2",
        )
    assert exc.value.code == "v5.work.downstream_intent_required"
