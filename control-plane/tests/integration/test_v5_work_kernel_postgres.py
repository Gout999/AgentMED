"""V5-2A closure: real-PostgreSQL proof for the Work Kernel (Master §6 2A-5).

Exit criteria under test here: no double lease, no ghost success and no
ambiguous retry on a real PostgreSQL unit of work.  SQLite cannot prove the
concurrency half of these, so they live in the integration lane.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.models import Audit, Event, Outbox
from app.models.v4_tables import AuthorityReceipt, ControllerRegistration
from app.models.v5_work_tables import (
    V5_WORK_EVENT_CHANNEL,
    WorkAttempt,
    WorkAttemptCapability,
    WorkProposal,
    WorkTask,
)
from app.services.v4_audit import V4AuditService
from app.services.v5_authority import build_v5_controller_registration_record
from app.services.v5_work_kernel import V5WorkKernelError, WorkKernelService
from app.utils.ids import new_transaction_id
from app.utils.v4_integrity import canonical_digest

pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 12, 11, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_work_pg"
PRINCIPAL = "prn_work_pg"
WORK_CONTROLLER = "prn_work_controller_pg"
PROPOSAL_CONTROLLER = "prn_proposal_controller_pg"

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


def _seed_controllers(session) -> None:
    for owner, principal, commands, suffix in (
        ("work-controller", WORK_CONTROLLER, WORK_COMMANDS, "work"),
        ("proposal-controller", PROPOSAL_CONTROLLER, PROPOSAL_COMMANDS, "prop"),
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
            target=f"creg_pg_{suffix}",
            params={"owner": owner, "service_identity_digest": digest},
            transaction_id=f"txn_seed_pg_{suffix}",
            evidence_refs={
                "owner": owner,
                "controller_registration_id": f"creg_pg_{suffix}",
                "controller_principal": principal,
            },
            occurred_at=NOW - timedelta(minutes=1),
        )
        built = build_v5_controller_registration_record(
            controller_registration_id=f"creg_pg_{suffix}",
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


def _new_task(session) -> str:
    kernel = WorkKernelService(session, clock=lambda: NOW)
    task = kernel.request_task(
        workspace_id=WORKSPACE,
        task_kind="fixture.probe",
        input_payload={"probe": "pg"},
        requester_principal=PRINCIPAL,
        idempotency_key=f"pg-{new_transaction_id()}",
        request_fingerprint="fp-pg",
        transaction_id=new_transaction_id(),
        request_id="req_pg_req",
    )
    return task.task_id


def test_concurrent_claims_on_one_task_mint_exactly_one_lease(
    pg_engine,
) -> None:
    """Two workers race the same task: exactly one claim commits; the loser
    gets lease_held and no second attempt, capability or fencing token
    exists."""
    factory = sessionmaker(bind=pg_engine, autoflush=False, future=True)
    with factory() as session, session.begin():
        _seed_controllers(session)
        task_id = _new_task(session)

    def try_claim(worker: str) -> str:
        with factory() as session:
            kernel = WorkKernelService(session, clock=lambda: NOW)
            try:
                with session.begin():
                    result = kernel.claim(
                        workspace_id=WORKSPACE,
                        task_id=task_id,
                        worker_identity=worker,
                        transaction_id=new_transaction_id(),
                        request_id=f"req_pg_{worker}",
                    )
                    return f"ok:{result.attempt.attempt_id}"
            except V5WorkKernelError as exc:
                session.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(try_claim, ("workerA", "workerB")))

    winners = [o for o in outcomes if o.startswith("ok:")]
    losers = [o for o in outcomes if not o.startswith("ok:")]
    assert len(winners) == 1, outcomes
    assert losers == ["v5.work.lease_held"]
    with factory() as session:
        attempts = session.scalars(
            sa.select(WorkAttempt).where(WorkAttempt.task_id == task_id)
        ).all()
        assert len(attempts) == 1
        assert attempts[0].fence_token == 1
        capabilities = session.scalars(
            sa.select(WorkAttemptCapability).where(
                WorkAttemptCapability.task_id == task_id
            )
        ).all()
        assert len(capabilities) == 1
        task = session.get(WorkTask, task_id)
        assert task.state == "LEASED"
        assert task.lease_fencing_token == 1


def test_concurrent_claims_on_distinct_tasks_all_succeed(pg_engine) -> None:
    factory = sessionmaker(bind=pg_engine, autoflush=False, future=True)
    with factory() as session, session.begin():
        _seed_controllers(session)
        task_ids = [_new_task(session) for _ in range(4)]

    def try_claim(item) -> str:
        index, task_id = item
        with factory() as session:
            kernel = WorkKernelService(session, clock=lambda: NOW)
            with session.begin():
                result = kernel.claim(
                    workspace_id=WORKSPACE,
                    task_id=task_id,
                    worker_identity=f"worker{index}",
                    transaction_id=new_transaction_id(),
                    request_id=f"req_pg_distinct_{index}",
                )
                return result.attempt.attempt_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        attempts = list(pool.map(try_claim, enumerate(task_ids)))
    assert len(set(attempts)) == 4
    with factory() as session:
        assert session.scalar(
            sa.select(sa.func.count())
            .select_from(WorkAttempt)
            .where(WorkAttempt.workspace_id == WORKSPACE)
        ) == 4


def test_full_fact_chain_is_exact_on_postgres(pg_engine) -> None:
    """Happy path on real PG: every command leaves event+outbox+audit+receipt
    rows bound to the same transaction and digest."""
    factory = sessionmaker(bind=pg_engine, autoflush=False, future=True)
    with factory() as session, session.begin():
        _seed_controllers(session)
        kernel = WorkKernelService(session, clock=lambda: NOW)
        task = kernel.request_task(
            workspace_id=WORKSPACE,
            task_kind="fixture.probe",
            input_payload={"probe": "pgfull"},
            requester_principal=PRINCIPAL,
            idempotency_key="pg-full",
            request_fingerprint="fp-pg-full",
            transaction_id="txn_pg_full_req",
            request_id="req_pg_full_req",
        )
        claim = kernel.claim(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            worker_identity="worker-pg",
            transaction_id="txn_pg_full_claim",
            request_id="req_pg_full_claim",
        )
        kernel.start_attempt(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=1,
            runtime_adapter="fixture",
            runtime_session="pg-session",
            transaction_id="txn_pg_full_start",
            request_id="req_pg_full_start",
        )
        kernel.record_output(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=1,
            output_payload={"answer": "pg"},
            stream_complete=True,
            transaction_id="txn_pg_full_out",
            request_id="req_pg_full_out",
        )
        kernel.complete_attempt(
            workspace_id=WORKSPACE,
            task_id=task.task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=1,
            terminal_receipt_digest="sha256:pgterminal",
            transaction_id="txn_pg_full_done",
            request_id="req_pg_full_done",
        )
        task_id = task.task_id
        attempt_id = claim.attempt.attempt_id

    with factory() as session:
        events = session.scalars(
            sa.select(Event).where(Event.workspace_id == WORKSPACE)
        ).all()
        # requested, claimed, attempt.created, attempt.starting,
        # attempt.started, attempt.output_recorded, attempt.succeeded,
        # work.completed
        assert len(events) == 8
        work_events = {
            e.event_type
            for e in events
            if e.aggregate_id == task_id
        }
        attempt_events = {
            e.event_type
            for e in events
            if e.aggregate_id == attempt_id
        }
        assert {"work.requested", "work.claimed", "work.completed"} <= work_events
        assert {
            "attempt.created",
            "attempt.started",
            "attempt.output_recorded",
            "attempt.succeeded",
        } <= attempt_events
        for event in events:
            assert event.contract_version == "v5"
            assert event.event_contract_major == 2
            outbox = session.scalar(
                sa.select(Outbox).where(Outbox.source_event_id == event.event_id)
            )
            assert outbox is not None
            assert outbox.channel == V5_WORK_EVENT_CHANNEL
            receipt = session.scalar(
                sa.select(AuthorityReceipt).where(
                    AuthorityReceipt.event_id == event.event_id
                )
            )
            assert receipt is not None
            audit = session.scalar(
                sa.select(Audit).where(
                    Audit.evidence_refs["event_id"].as_string() == event.event_id
                )
            )
            assert audit is not None
        task = session.get(WorkTask, task_id)
        assert task.state == "COMPLETED"


def test_ghost_success_rejected_on_postgres(pg_engine) -> None:
    factory = sessionmaker(bind=pg_engine, autoflush=False, future=True)
    with factory() as session, session.begin():
        _seed_controllers(session)
        task_id = _new_task(session)
        kernel = WorkKernelService(session, clock=lambda: NOW)
        proposal = kernel.submit_proposal(
            workspace_id=WORKSPACE,
            task_id=task_id,
            proposer_principal=PRINCIPAL,
            payload={"action": "x"},
            transaction_id="txn_pg_prop",
            request_id="req_pg_prop",
        )
        with pytest.raises(V5WorkKernelError) as exc:
            kernel.decide_proposal(
                workspace_id=WORKSPACE,
                proposal_id=proposal.proposal_id,
                decided_by_principal=PRINCIPAL,
                accept=True,
                transaction_id="txn_pg_dec",
                request_id="req_pg_dec",
            )
        assert exc.value.code == "v5.work.downstream_intent_required"
        proposal_id = proposal.proposal_id
    with factory() as session:
        proposal = session.get(WorkProposal, proposal_id)
        assert proposal.status == "SUBMITTED"


def test_ambiguous_retry_blocked_until_reconcile_on_postgres(pg_engine) -> None:
    factory = sessionmaker(bind=pg_engine, autoflush=False, future=True)
    with factory() as session, session.begin():
        _seed_controllers(session)
        task_id = _new_task(session)
        kernel = WorkKernelService(session, clock=lambda: NOW)
        claim = kernel.claim(
            workspace_id=WORKSPACE,
            task_id=task_id,
            worker_identity="worker-pg",
            transaction_id="txn_pg_amb_claim",
            request_id="req_pg_amb_claim",
        )
        kernel.start_attempt(
            workspace_id=WORKSPACE,
            task_id=task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=1,
            runtime_adapter="fixture",
            runtime_session="pg-amb-session",
            transaction_id="txn_pg_amb_start",
            request_id="req_pg_amb_start",
        )
        kernel.mark_attempt_unknown(
            workspace_id=WORKSPACE,
            task_id=task_id,
            attempt_id=claim.attempt.attempt_id,
            ambiguity_reason="executor_vanished",
            transaction_id="txn_pg_amb_unknown",
            request_id="req_pg_amb_unknown",
        )
        attempt_id = claim.attempt.attempt_id

    with factory() as session:
        kernel = WorkKernelService(session, clock=lambda: NOW)
        with pytest.raises(V5WorkKernelError) as exc:
            with session.begin():
                kernel.claim(
                    workspace_id=WORKSPACE,
                    task_id=task_id,
                    worker_identity="worker-pg2",
                    transaction_id="txn_pg_amb_reclaim",
                    request_id="req_pg_amb_reclaim",
                )
        assert exc.value.code == "v5.work.reconcile_required"

    with factory() as session, session.begin():
        kernel = WorkKernelService(session, clock=lambda: NOW)
        kernel.reconcile_attempt(
            workspace_id=WORKSPACE,
            task_id=task_id,
            attempt_id=attempt_id,
            outcome="failed",
            reconciliation_receipt_digest="sha256:pgrecon",
            transaction_id="txn_pg_amb_recon",
            request_id="req_pg_amb_recon",
        )

    with factory() as session:
        kernel = WorkKernelService(session, clock=lambda: NOW)
        with session.begin():
            recovered = kernel.claim(
                workspace_id=WORKSPACE,
                task_id=task_id,
                worker_identity="worker-pg3",
                transaction_id="txn_pg_amb_retry",
                request_id="req_pg_amb_retry",
            )
        assert recovered.attempt.attempt_number == 2
        assert recovered.attempt.fence_token == 2
