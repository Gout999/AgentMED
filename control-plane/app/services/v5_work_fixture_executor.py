"""V5-2A deterministic fixture executor (Master §6 2A-4).

A reference executor that drives the Work Kernel through its lifecycle
without any model, AgentTeams, or provider dependency.  Outputs are pure
functions of the declared input, so every run is independently re-checkable.

It exists to prove the kernel's failure semantics, not to claim any live or
agent-causal facet: crash injection before/after claim, after output, and
after decision must leave the aggregates in states the kernel can reconcile
or safely retry; post-action proposals, ghost successes and ambiguous
outcomes must be rejected by the kernel, not by the executor.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from app.models.v5_work_tables import WorkTask
from app.services.v5_work_kernel import ClaimResult, WorkKernelService
from app.utils.ids import new_transaction_id
from app.utils.v4_integrity import canonical_digest

FIXTURE_TASK_KIND = "fixture.deterministic-probe"
FIXTURE_WORKER = "fixture-executor"


@dataclass(frozen=True)
class FixtureRunResult:
    task: WorkTask
    attempt_id: str
    output_digest: str
    terminal_state: str


class FixtureWorkExecutor:
    """Deterministic worker driving WorkKernelService through real commands."""

    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] | None = None,
        kernel: WorkKernelService | None = None,
    ) -> None:
        self.session = session
        self.clock = clock
        self.kernel = kernel or WorkKernelService(session, clock=clock)

    @staticmethod
    def deterministic_output(input_payload: dict[str, Any]) -> dict[str, Any]:
        """Pure function of the input: the same probe always yields the same
        output digest, so replay evidence is independently verifiable."""
        return {
            "executor": FIXTURE_WORKER,
            "input_digest": canonical_digest(input_payload),
            "result_digest": canonical_digest({"fixture": input_payload}),
        }

    def request(self, *, workspace_id: str, probe: str, idempotency_key: str) -> WorkTask:
        return self.kernel.request_task(
            workspace_id=workspace_id,
            task_kind=FIXTURE_TASK_KIND,
            input_payload={"probe": probe},
            requester_principal=FIXTURE_WORKER,
            idempotency_key=idempotency_key,
            transaction_id=new_transaction_id(),
            request_id=f"fxreq_{idempotency_key}",
        )

    def claim(self, *, workspace_id: str, task_id: str) -> ClaimResult:
        return self.kernel.claim(
            workspace_id=workspace_id,
            task_id=task_id,
            worker_identity=FIXTURE_WORKER,
            transaction_id=new_transaction_id(),
            request_id=f"fxclaim_{task_id[-12:]}",
        )

    def record_terminal_receipt(
        self,
        *,
        workspace_id: str,
        task_id: str,
        claim: ClaimResult,
        succeeded: bool,
    ) -> str:
        return self.kernel.record_terminal_receipt(
            workspace_id=workspace_id,
            task_id=task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=claim.attempt.fence_token,
            issuer=FIXTURE_WORKER,
            process_exit_code=0 if succeeded else 1,
            stream_complete=True,
            structured_output_valid=succeeded,
            transaction_id=new_transaction_id(),
            request_id=f"fxreceipt_{claim.attempt.attempt_id[-12:]}",
        )

    def run_to_completion(
        self,
        *,
        workspace_id: str,
        probe: str,
        idempotency_key: str,
    ) -> FixtureRunResult:
        """Full happy path: request -> claim -> start -> output -> complete."""
        task = self.request(
            workspace_id=workspace_id, probe=probe, idempotency_key=idempotency_key
        )
        claim = self.claim(workspace_id=workspace_id, task_id=task.task_id)
        self.kernel.start_attempt(
            workspace_id=workspace_id,
            task_id=task.task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=claim.attempt.fence_token,
            runtime_adapter="fixture",
            runtime_session=f"fxsess_{claim.attempt.attempt_id[-12:]}",
            transaction_id=new_transaction_id(),
            request_id=f"fxstart_{claim.attempt.attempt_id[-12:]}",
        )
        terminal_receipt_digest = self.record_terminal_receipt(
            workspace_id=workspace_id,
            task_id=task.task_id,
            claim=claim,
            succeeded=True,
        )
        output = self.deterministic_output(task.input_payload)
        self.kernel.record_output(
            workspace_id=workspace_id,
            task_id=task.task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=claim.attempt.fence_token,
            output_payload=output,
            stream_complete=True,
            transaction_id=new_transaction_id(),
            request_id=f"fxout_{claim.attempt.attempt_id[-12:]}",
        )
        finished = self.kernel.complete_attempt(
            workspace_id=workspace_id,
            task_id=task.task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=claim.attempt.fence_token,
            terminal_receipt_digest=terminal_receipt_digest,
            transaction_id=new_transaction_id(),
            request_id=f"fxdone_{claim.attempt.attempt_id[-12:]}",
        )
        return FixtureRunResult(
            task=task,
            attempt_id=claim.attempt.attempt_id,
            output_digest=finished.output_digest or "",
            terminal_state=finished.state,
        )

    def crash_after_claim(
        self,
        *,
        workspace_id: str,
        probe: str,
        idempotency_key: str,
    ) -> ClaimResult:
        """Crash immediately after a successful claim: the attempt is left
        CREATED with the lease held.  Recovery is the caller's job (lease
        expiry -> UNKNOWN -> reconcile), matching the kernel contract."""
        task = self.request(
            workspace_id=workspace_id, probe=probe, idempotency_key=idempotency_key
        )
        return self.claim(workspace_id=workspace_id, task_id=task.task_id)

    def crash_after_output(
        self,
        *,
        workspace_id: str,
        probe: str,
        idempotency_key: str,
    ) -> ClaimResult:
        """Crash after recording output but before completion: the attempt
        stays OUTPUT_RECORDED under a live lease."""
        task = self.request(
            workspace_id=workspace_id, probe=probe, idempotency_key=idempotency_key
        )
        claim = self.claim(workspace_id=workspace_id, task_id=task.task_id)
        self.kernel.start_attempt(
            workspace_id=workspace_id,
            task_id=task.task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=claim.attempt.fence_token,
            runtime_adapter="fixture",
            runtime_session=f"fxsess_{claim.attempt.attempt_id[-12:]}",
            transaction_id=new_transaction_id(),
            request_id=f"fxstart_{claim.attempt.attempt_id[-12:]}",
        )
        self.record_terminal_receipt(
            workspace_id=workspace_id,
            task_id=task.task_id,
            claim=claim,
            succeeded=True,
        )
        self.kernel.record_output(
            workspace_id=workspace_id,
            task_id=task.task_id,
            attempt_id=claim.attempt.attempt_id,
            fencing_token=claim.attempt.fence_token,
            output_payload=self.deterministic_output(task.input_payload),
            stream_complete=True,
            transaction_id=new_transaction_id(),
            request_id=f"fxout_{claim.attempt.attempt_id[-12:]}",
        )
        return claim
