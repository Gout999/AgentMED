"""V5-2A state-machine conformance: every emitted event stream must walk the
V4 machines verbatim (D-016 'reused without redefinition').

For each scenario we replay the persisted major-2 event stream of each
aggregate through the frozen V4 state machine and require every hop to
exist.  This is the mechanical guard against any code path emitting a
transition the contract does not allow — the failure mode the independent
verifier caught in the first 2A-2 draft.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
import yaml

from app.models import Event
from app.models.v4_tables import ControllerRegistration
from app.models.v5_work_tables import WorkAttempt, WorkTask
from app.services.v4_audit import V4AuditService
from app.services.v5_authority import build_v5_controller_registration_record
from app.services.v5_work_kernel import WorkKernelService
from app.utils.ids import new_transaction_id
from app.utils.v4_integrity import canonical_digest

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_work_sm"
PRINCIPAL = "prn_work_sm"

MACHINES = yaml.safe_load(
    (
        Path(__file__).resolve().parents[3]
        / "contracts/v4/events/state-machines.yaml"
    ).read_text(encoding="utf-8")
)["machines"]

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


def _walk(machine_name: str, event_types: list[str]) -> str:
    """Replay an event sequence through a V4 machine; fail on any hop the
    machine does not define.  Creation events (the machine's birth) pin the
    initial state instead of consuming a transition."""
    machine = MACHINES[machine_name]
    state = machine["initial"]
    creation = {
        "worker_task": "work.requested",
        "attempt": "attempt.created",
    }[machine_name]
    for event_type in event_types:
        if event_type == creation:
            continue
        hop = next(
            (
                t
                for t in machine["transitions"]
                if t["from"] == state and t["on"] == event_type
            ),
            None,
        )
        assert hop is not None, (
            f"{machine_name}: illegal transition {state} --{event_type}-->"
        )
        state = hop["to"]
    return state


def _assert_streams_legal(session) -> None:
    """Walk every Work aggregate's persisted event stream in this session."""
    events = session.scalars(
        sa.select(Event)
        .where(Event.contract_version == "v5")
        .order_by(Event.aggregate_id, Event.seq)
    ).all()
    by_aggregate: dict[str, list[str]] = {}
    for event in events:
        by_aggregate.setdefault(event.aggregate_id, []).append(event.event_type)
    for aggregate_id, types in by_aggregate.items():
        if types and types[0] == "work.requested":
            final = _walk("worker_task", types)
            task = session.get(WorkTask, aggregate_id)
            assert task is not None and task.state == final
        elif types and types[0] == "attempt.created":
            final = _walk("attempt", types)
            attempt = session.get(WorkAttempt, aggregate_id)
            assert attempt is not None and attempt.state == final


def _seed(session) -> WorkKernelService:
    for owner, principal, commands, suffix in (
        ("work-controller", "prn_sm_work", WORK_COMMANDS, "w"),
        ("proposal-controller", "prn_sm_prop", PROPOSAL_COMMANDS, "p"),
    ):
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
            target=f"creg_sm_{suffix}",
            params={"owner": owner, "service_identity_digest": digest},
            transaction_id=f"txn_seed_sm_{suffix}",
            evidence_refs={
                "owner": owner,
                "controller_registration_id": f"creg_sm_{suffix}",
                "controller_principal": principal,
            },
            occurred_at=NOW - timedelta(minutes=1),
        )
        built = build_v5_controller_registration_record(
            controller_registration_id=f"creg_sm_{suffix}",
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
    return WorkKernelService(session, clock=lambda: NOW)


def _request(kernel, key: str) -> WorkTask:
    return kernel.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": key},
        requester_principal=PRINCIPAL,
        idempotency_key=key,
        request_fingerprint=f"fp-{key}",
        transaction_id=new_transaction_id(),
        request_id=f"req_sm_{key}",
    )


def _claim(kernel, task: WorkTask, worker: str = "worker-sm"):
    return kernel.claim(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        worker_identity=worker,
        transaction_id=new_transaction_id(),
        request_id=f"req_sm_c_{task.task_id[-8:]}",
    )


def _start_and_output(kernel, task: WorkTask, attempt) -> None:
    kernel.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        fencing_token=attempt.fence_token,
        runtime_adapter="fixture",
        runtime_session="sm-session",
        transaction_id=new_transaction_id(),
        request_id=f"req_sm_s_{attempt.attempt_id[-8:]}",
    )
    kernel.record_output(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=attempt.attempt_id,
        fencing_token=attempt.fence_token,
        output_payload={"ok": True},
        stream_complete=True,
        transaction_id=new_transaction_id(),
        request_id=f"req_sm_o_{attempt.attempt_id[-8:]}",
    )


def test_happy_path_stream_walks_both_machines(sqlite_session) -> None:
    kernel = _seed(sqlite_session)
    task = _request(kernel, "sm-happy")
    claim = _claim(kernel, task)
    _start_and_output(kernel, task, claim.attempt)
    kernel.complete_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claim.attempt.attempt_id,
        fencing_token=claim.attempt.fence_token,
        terminal_receipt_digest="sha256:sm",
        transaction_id=new_transaction_id(),
        request_id="req_sm_done",
    )
    _assert_streams_legal(sqlite_session)


def test_fail_retry_exhaust_stream_is_legal(sqlite_session) -> None:
    kernel = _seed(sqlite_session)
    task = kernel.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "sm-fail"},
        requester_principal=PRINCIPAL,
        idempotency_key="sm-fail",
        request_fingerprint="fp-sm-fail",
        max_attempts=1,
        transaction_id=new_transaction_id(),
        request_id="req_sm_f",
    )
    claim = _claim(kernel, task)
    _start_and_output(kernel, task, claim.attempt)
    kernel.fail_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claim.attempt.attempt_id,
        fencing_token=claim.attempt.fence_token,
        failure_code="boom",
        transaction_id=new_transaction_id(),
        request_id="req_sm_fail",
    )
    _assert_streams_legal(sqlite_session)


def test_unknown_reconcile_retry_stream_is_legal(sqlite_session) -> None:
    kernel = _seed(sqlite_session)
    task = _request(kernel, "sm-unk")
    claim = _claim(kernel, task)
    kernel.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claim.attempt.attempt_id,
        fencing_token=claim.attempt.fence_token,
        runtime_adapter="fixture",
        runtime_session="sm-u",
        transaction_id=new_transaction_id(),
        request_id="req_sm_us",
    )
    kernel.mark_attempt_unknown(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claim.attempt.attempt_id,
        ambiguity_reason="vanished",
        transaction_id=new_transaction_id(),
        request_id="req_sm_u",
    )
    kernel.reconcile_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claim.attempt.attempt_id,
        outcome="failed",
        reconciliation_receipt_digest="sha256:smrecon",
        transaction_id=new_transaction_id(),
        request_id="req_sm_r",
    )
    _assert_streams_legal(sqlite_session)


def test_reconcile_exhausted_walks_two_legal_hops(sqlite_session) -> None:
    kernel = _seed(sqlite_session)
    task = kernel.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "sm-rex"},
        requester_principal=PRINCIPAL,
        idempotency_key="sm-rex",
        request_fingerprint="fp-sm-rex",
        max_attempts=1,
        transaction_id=new_transaction_id(),
        request_id="req_sm_rex",
    )
    claim = _claim(kernel, task)
    kernel.start_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claim.attempt.attempt_id,
        fencing_token=claim.attempt.fence_token,
        runtime_adapter="fixture",
        runtime_session="sm-rex",
        transaction_id=new_transaction_id(),
        request_id="req_sm_rex_s",
    )
    kernel.mark_attempt_unknown(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claim.attempt.attempt_id,
        ambiguity_reason="vanished",
        transaction_id=new_transaction_id(),
        request_id="req_sm_rex_u",
    )
    kernel.reconcile_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claim.attempt.attempt_id,
        outcome="failed",
        reconciliation_receipt_digest="sha256:smrex",
        transaction_id=new_transaction_id(),
        request_id="req_sm_rex_r",
    )
    sqlite_session.expire_all()
    task = sqlite_session.get(WorkTask, task.task_id)
    assert task.state == "EXHAUSTED"
    _assert_streams_legal(sqlite_session)


def test_cancel_flow_stream_is_legal(sqlite_session) -> None:
    kernel = _seed(sqlite_session)
    task = _request(kernel, "sm-cancel")
    claim = _claim(kernel, task)
    kernel.cancel_task(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        reason="operator",
        requested_by_principal=PRINCIPAL,
        transaction_id=new_transaction_id(),
        request_id="req_sm_cx",
    )
    kernel.cancel_attempt(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        attempt_id=claim.attempt.attempt_id,
        reason="operator",
        transaction_id=new_transaction_id(),
        request_id="req_sm_ca",
    )
    _assert_streams_legal(sqlite_session)


def test_lease_expiry_recovery_streams_are_legal(sqlite_session) -> None:
    clock = {"now": NOW}
    sqlite_session  # session bound below via service fixture pattern
    kernel = _seed(sqlite_session)
    kernel.clock = lambda: clock["now"]
    task = _request(kernel, "sm-expire")
    claim = _claim(kernel, task)  # CREATED, never started
    clock["now"] = NOW + timedelta(seconds=300)
    recovered = kernel.claim(
        workspace_id=WORKSPACE,
        task_id=task.task_id,
        worker_identity="worker-sm2",
        transaction_id=new_transaction_id(),
        request_id="req_sm_reclaim",
    )
    assert recovered.attempt.attempt_number == 2
    _assert_streams_legal(sqlite_session)
