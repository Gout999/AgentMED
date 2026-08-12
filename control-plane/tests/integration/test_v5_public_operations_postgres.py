"""V5-2B real-PostgreSQL idempotency and reconnect proof."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker

from app.models.v4_tables import PublicCommandIdempotency
from app.models.v5_work_tables import AutomationRequest, WorkTask
from app.public_api.v5_models import InvestigationStartRequest, OperationCancelRequest
from app.services.public_operations import PublicOperationError, PublicOperationService
from app.services.v5_work_kernel import V5WorkKernelError, WorkKernelService
from app.utils.v4_integrity import canonical_digest
from v5_public_operation_fixtures import (
    CASE,
    NOW,
    WORKSPACE,
    principal_context,
    seed_public_operation_world,
)

pytestmark = pytest.mark.integration


def test_concurrent_start_and_process_restart_preserve_one_durable_operation(
    pg_engine,
) -> None:
    factory = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        case_digest = seed_public_operation_world(session)

    def start_once(index: int) -> tuple[str, str]:
        with factory() as session, session.begin():
            response = PublicOperationService(
                session,
                cursor_signing_key="v5-2b-pg-cursor-key",
                clock=lambda: NOW,
            ).start_investigation(
                CASE,
                InvestigationStartRequest(
                    schema_version="2.0",
                    case_revision=1,
                    case_digest=case_digest,
                    instructions="Concurrent durable investigation.",
                    max_attempts=2,
                ),
                principal=principal_context("investigations:start"),
                idempotency_key="pg-investigation-start-0001",
                request_id=f"req_01J0000000000Q0{index}",
            )
            return (
                response.operation.operation_id,
                response.operation.exact_work_task_binding.id,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(start_once, (1, 2)))

    assert outcomes[0] == outcomes[1]
    operation_id, task_id = outcomes[0]
    with factory() as session:
        assert session.scalar(
            sa.select(sa.func.count()).select_from(AutomationRequest)
        ) == 1
        assert session.scalar(sa.select(sa.func.count()).select_from(WorkTask)) == 1
        assert session.scalar(
            sa.select(sa.func.count()).select_from(PublicCommandIdempotency)
        ) == 1

    # Dispose the pool to model a server/process restart.  A fresh Session can
    # still observe and stop the operation solely from PostgreSQL facts.
    pg_engine.dispose()
    with factory() as session, session.begin():
        service = PublicOperationService(
            session,
            cursor_signing_key="v5-2b-pg-cursor-key",
            clock=lambda: NOW,
        )
        restored = service.get_operation(
            operation_id,
            principal=principal_context("operations:read"),
            request_id="req_01J0000000000Q03",
        )
        assert restored.operation.exact_work_task_binding.id == task_id
        assert restored.operation.state == "SUBMITTED"
        canceled = service.request_cancel(
            operation_id,
            OperationCancelRequest(
                schema_version="2.0", reason="restart-path operator stop"
            ),
            principal=principal_context("operations:cancel"),
            idempotency_key="pg-operation-cancel-0001",
            request_id="req_01J0000000000Q04",
        )
        assert canceled.operation.state == "CANCEL_REQUESTED"

    pg_engine.dispose()
    with factory() as session:
        final = PublicOperationService(
            session,
            cursor_signing_key="v5-2b-pg-cursor-key",
            clock=lambda: NOW,
        ).get_operation(
            operation_id,
            principal=principal_context("operations:read"),
            request_id="req_01J0000000000Q05",
        )
        task = session.get(WorkTask, task_id)
        assert final.operation.state == "CANCEL_REQUESTED"
        assert final.operation.cancel_requested is True
        assert task is not None and task.state == "QUEUED"


def test_completion_cancel_race_has_one_authoritative_outcome(pg_engine) -> None:
    factory = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        case_digest = seed_public_operation_world(session)
    with factory() as session, session.begin():
        operations = PublicOperationService(
            session,
            cursor_signing_key="v5-2b-pg-cursor-key",
            clock=lambda: NOW,
        )
        started = operations.start_investigation(
            CASE,
            InvestigationStartRequest(
                schema_version="2.0",
                case_revision=1,
                case_digest=case_digest,
                instructions="Race completion against a stop request.",
                max_attempts=2,
            ),
            principal=principal_context("investigations:start"),
            idempotency_key="pg-investigation-race-0001",
            request_id="req_01J0000000000S01",
        )
        task_id = started.operation.exact_work_task_binding.id
        operation_id = started.operation.operation_id
        claimed = operations.work.claim(
            workspace_id=WORKSPACE,
            task_id=task_id,
            worker_identity="race-worker",
            request_id="req_01J0000000000S02",
        )
        attempt = operations.work.start_attempt(
            workspace_id=WORKSPACE,
            task_id=task_id,
            attempt_id=claimed.attempt.attempt_id,
            fencing_token=claimed.attempt.fence_token,
            runtime_adapter="fixture-adapter",
            runtime_session="race-session",
            request_id="req_01J0000000000S03",
        )
        terminal_receipt_digest = operations.work.record_terminal_receipt(
            workspace_id=WORKSPACE,
            task_id=task_id,
            attempt_id=attempt.attempt_id,
            fencing_token=attempt.fence_token,
            issuer="race-worker",
            process_exit_code=0,
            stream_complete=True,
            structured_output_valid=True,
            request_id="req_01J0000000000S04",
        )
        artifact_payload = {"summary": "race artifact", "gate_status": "NOT_RUN"}
        operations.work.record_output(
            workspace_id=WORKSPACE,
            task_id=task_id,
            attempt_id=attempt.attempt_id,
            fencing_token=attempt.fence_token,
            output_payload={
                "artifact_kind": "INVESTIGATION_REPORT",
                "schema_major": 2,
                "domain_verdict": "UNKNOWN",
                "evidence_completeness": "COMPLETE",
                "exact_artifact_binding": {
                    "kind": "INVESTIGATION_REPORT",
                    "id": "report_01J0000000000S01",
                    "revision": 1,
                    "digest": canonical_digest(artifact_payload),
                },
                "payload": artifact_payload,
            },
            stream_complete=True,
            request_id="req_01J0000000000S05",
        )
        attempt_id = attempt.attempt_id
        fence_token = attempt.fence_token

    def complete_once() -> str:
        with factory() as session, session.begin():
            try:
                WorkKernelService(session, clock=lambda: NOW).complete_attempt(
                    workspace_id=WORKSPACE,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    fencing_token=fence_token,
                    terminal_receipt_digest=terminal_receipt_digest,
                    request_id="req_01J0000000000S06",
                )
                return "completed"
            except V5WorkKernelError as exc:
                return exc.code

    def cancel_once() -> str:
        with factory() as session, session.begin():
            try:
                PublicOperationService(
                    session,
                    cursor_signing_key="v5-2b-pg-cursor-key",
                    clock=lambda: NOW,
                ).request_cancel(
                    operation_id,
                    OperationCancelRequest(
                        schema_version="2.0", reason="race stop request"
                    ),
                    principal=principal_context("operations:cancel"),
                    idempotency_key="pg-operation-race-cancel-0001",
                    request_id="req_01J0000000000S07",
                )
                return "cancel_requested"
            except PublicOperationError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        completed_future = pool.submit(complete_once)
        canceled_future = pool.submit(cancel_once)
        outcomes = {completed_future.result(), canceled_future.result()}

    assert outcomes in (
        {"completed", "OPERATION_NOT_CANCELLABLE"},
        {"cancel_requested", "v5.work.lease_lost"},
    )
    with factory() as session:
        operation = PublicOperationService(
            session,
            cursor_signing_key="v5-2b-pg-cursor-key",
            clock=lambda: NOW,
        ).get_operation(
            operation_id,
            principal=principal_context("operations:read"),
            request_id="req_01J0000000000S08",
        )
        assert operation.operation.state in {"COMPLETED", "CANCEL_REQUESTED"}
        assert (operation.operation.state == "COMPLETED") is (
            operation.operation.artifact_or_null is not None
        )
