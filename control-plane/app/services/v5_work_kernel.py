"""V5-2A Durable Work Kernel service (D-016, Master §6 2A-2/2A-3).

work-controller owns worker_task and attempt; proposal-controller owns
proposal and proposal_decision.  Every state advance is one PostgreSQL unit
of work: projection row update + major-2 domain event + controller audit +
authority receipt + outbox row, all flushed together — any failure rolls the
whole command back.  Projections are mutable; the event stream is the record.

Fencing: every claim increments ``work_tasks.lease_fencing_token`` by one and
the attempt stores the token it was created under.  Heartbeats, output,
completion and failure all require the live token; a stale writer is
rejected, never silently ignored.  ``UNKNOWN`` is not terminal: only an
explicit reconcile with a terminal receipt digest may move a task out of
``BLOCKED_UNKNOWN``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Event
from app.models.v5_work_tables import (
    WorkAttempt,
    WorkAttemptCapability,
    WorkProposal,
    WorkProposalDecision,
    WorkReactionLedger,
    WorkTask,
)
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from app.services.v4_event_store import V4EventStore, V4EventStoreError
from app.services.v5_authority import (
    V5AuthorityError,
    V5AuthorityService,
    V5ResolvedController,
)
from app.utils.ids import (
    new_authority_receipt_id,
    new_transaction_id,
    new_work_attempt_id,
    new_work_capability_id,
    new_work_decision_id,
    new_work_proposal_id,
    new_work_reaction_id,
    new_work_task_id,
)
from app.utils.v4_integrity import V4IntegrityError, canonical_digest


class V5WorkKernelError(RuntimeError):
    def __init__(self, code: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(code)


TASK_TERMINAL = frozenset({"COMPLETED", "CANCELLED", "EXHAUSTED"})
ATTEMPT_TERMINAL = frozenset({"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED"})
ATTEMPT_ACTIVE = frozenset(
    {"CREATED", "STARTING", "RUNNING", "OUTPUT_RECORDED", "CANCEL_REQUESTED"}
)
# V4 attempt machine source-state restrictions (reused verbatim):
ATTEMPT_FAILABLE = frozenset({"STARTING", "RUNNING", "OUTPUT_RECORDED"})
ATTEMPT_CANCELLABLE = frozenset({"CREATED", "STARTING", "RUNNING"})
ATTEMPT_UNKNOWABLE = frozenset({"STARTING", "RUNNING", "OUTPUT_RECORDED"})
DEFAULT_LEASE_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 3


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _task_snapshot(task: WorkTask) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "workspace_id": task.workspace_id,
        "revision": task.revision,
        "state": task.state,
        "task_kind": task.task_kind,
        "input_digest": task.input_digest,
        "attempt_count": task.attempt_count,
        "max_attempts": task.max_attempts,
        "current_attempt_id": task.current_attempt_id,
        "lease_owner": task.lease_owner,
        "lease_fencing_token": task.lease_fencing_token,
        "lease_expires_at": _wire_time(task.lease_expires_at),
        "last_heartbeat_at": _wire_time(task.last_heartbeat_at),
        "terminal_reason": task.terminal_reason,
    }


def _attempt_snapshot(attempt: WorkAttempt) -> dict[str, Any]:
    return {
        "attempt_id": attempt.attempt_id,
        "workspace_id": attempt.workspace_id,
        "task_id": attempt.task_id,
        "revision": attempt.revision,
        "attempt_number": attempt.attempt_number,
        "state": attempt.state,
        "worker_identity": attempt.worker_identity,
        "fence_token": attempt.fence_token,
        "output_digest": attempt.output_digest,
        "fallback_of_attempt_id": attempt.fallback_of_attempt_id,
        "started_at": _wire_time(attempt.started_at),
        "ended_at": _wire_time(attempt.ended_at),
    }


def _proposal_snapshot(proposal: WorkProposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.proposal_id,
        "workspace_id": proposal.workspace_id,
        "revision": proposal.revision,
        "task_id": proposal.task_id,
        "attempt_id": proposal.attempt_id,
        "proposer_principal": proposal.proposer_principal,
        "payload_digest": proposal.payload_digest,
        "status": proposal.status,
        "decided_at": _wire_time(proposal.decided_at),
    }


def _decision_snapshot(decision: WorkProposalDecision) -> dict[str, Any]:
    return {
        "decision_id": decision.decision_id,
        "workspace_id": decision.workspace_id,
        "revision": decision.revision,
        "proposal_id": decision.proposal_id,
        "decision": decision.decision,
        "decided_by_principal": decision.decided_by_principal,
        "downstream_intent": decision.downstream_intent,
    }


def _binding(kind: str, subject_id: str, revision: int, digest: str) -> dict[str, Any]:
    return {"kind": kind, "id": subject_id, "revision": revision, "digest": digest}


@dataclass(frozen=True)
class ClaimResult:
    task: WorkTask
    attempt: WorkAttempt
    capability: WorkAttemptCapability


class WorkKernelService:
    """Single-owner write path for the V5 Work aggregates."""

    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] | None = None,
        audit: V4AuditService | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.authority = V5AuthorityService(session)
        self.events = V4EventStore(session)
        self.audit = audit or V4AuditService(session)

    # ------------------------------------------------------------------
    # authority plumbing: event + audit + receipt + outbox in one flush
    # ------------------------------------------------------------------
    def _write_fact(
        self,
        *,
        workspace_id: str,
        subject_kind: str,
        subject_id: str,
        subject_revision: int,
        subject_digest: str,
        aggregate_type: str,
        event_type: str,
        payload: dict[str, Any],
        command: str,
        transaction_id: str,
        request_id: str,
        now: datetime,
        authority_receipt_id: str,
    ) -> Event:
        try:
            resolved = self.authority.resolve_controller(
                workspace_id=workspace_id,
                subject_kind=subject_kind,
                command=command,
                event_type=event_type,
                recorded_at=now,
            )
            event = self.events.append_event(
                workspace_id=workspace_id,
                aggregate_type=aggregate_type,
                aggregate_id=subject_id,
                event_type=event_type,
                payload=payload,
                causation_id=request_id,
                correlation_id=workspace_id,
                actor_principal=resolved.controller_principal,
                transaction_id=transaction_id,
                occurred_at=now,
                authority_receipt_id=authority_receipt_id,
            )
            audit = self.audit.record(
                workspace_id=workspace_id,
                actor_principal=resolved.controller_principal,
                action=f"controller.{event_type}",
                target=subject_id,
                params={"command": command},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "subject_kind": subject_kind,
                    "subject_id": subject_id,
                    "subject_revision": subject_revision,
                    "subject_digest": subject_digest,
                    "event_id": event.event_id,
                },
                occurred_at=now,
            )
            self.authority.record_receipt(
                resolved=resolved,
                authority_receipt_id=authority_receipt_id,
                workspace_id=workspace_id,
                subject_id=subject_id,
                subject_revision=subject_revision,
                subject_digest=subject_digest,
                event_id=event.event_id,
                transaction_id=transaction_id,
                audit_ref=audit.audit_ref,
                recorded_at=now,
            )
        except (V5AuthorityError, V4EventStoreError, V4IntegrityError) as exc:
            raise V5WorkKernelError("v5.work.authority_chain_failed") from exc
        except V4AuditUnavailable as exc:
            raise V5WorkKernelError("v5.work.audit_unavailable") from exc
        return event

    def _locked_task(self, workspace_id: str, task_id: str) -> WorkTask:
        task = self.session.scalar(
            select(WorkTask)
            .where(
                WorkTask.workspace_id == workspace_id,
                WorkTask.task_id == task_id,
            )
            .with_for_update()
        )
        if task is None:
            raise V5WorkKernelError("v5.work.task_not_found")
        return task

    def _refresh_task_head(self, task: WorkTask, receipt_id: str) -> None:
        task.revision += 1
        task.authority_receipt_id = receipt_id
        task.record_digest = canonical_digest(_task_snapshot(task))
        task.updated_at = self.clock()

    def _refresh_attempt_head(self, attempt: WorkAttempt, receipt_id: str) -> None:
        attempt.revision += 1
        attempt.authority_receipt_id = receipt_id
        attempt.record_digest = canonical_digest(_attempt_snapshot(attempt))
        attempt.updated_at = self.clock()

    # ------------------------------------------------------------------
    # work.request
    # ------------------------------------------------------------------
    def request_task(
        self,
        *,
        workspace_id: str,
        task_kind: str,
        input_payload: dict[str, Any],
        requester_principal: str,
        idempotency_key: str,
        request_fingerprint: str,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkTask:
        if not all(
            isinstance(value, str) and value
            for value in (
                workspace_id,
                task_kind,
                requester_principal,
                idempotency_key,
                request_fingerprint,
                request_id,
            )
        ) or not isinstance(input_payload, dict) or max_attempts < 1:
            raise V5WorkKernelError("v5.work.request_invalid")
        existing = self.session.scalar(
            select(WorkTask).where(
                WorkTask.workspace_id == workspace_id,
                WorkTask.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise V5WorkKernelError("v5.work.idempotency_conflict")
            return existing
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = WorkTask(
            task_id=new_work_task_id(),
            workspace_id=workspace_id,
            revision=1,
            task_kind=task_kind,
            input_payload=input_payload,
            input_digest=canonical_digest(input_payload),
            requester_principal=requester_principal,
            state="QUEUED",
            attempt_count=0,
            max_attempts=max_attempts,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            created_at=now,
            updated_at=now,
        )
        receipt_id = new_authority_receipt_id()
        task.authority_receipt_id = receipt_id
        task.record_digest = canonical_digest(_task_snapshot(task))
        self.session.add(task)
        self.session.flush()
        binding = _binding("WORK_TASK", task.task_id, 1, task.record_digest)
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=1,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type="work.requested",
            payload={
                "exact_work_task_binding": binding,
                "task_kind": task_kind,
                "input_digest": task.input_digest,
                "requester_principal": requester_principal,
            },
            command="work.request",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )
        return task

    # ------------------------------------------------------------------
    # work.claim — the atomic claim transaction
    # ------------------------------------------------------------------
    def claim(
        self,
        *,
        workspace_id: str,
        task_id: str,
        worker_identity: str,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        transaction_id: str | None = None,
        request_id: str,
    ) -> ClaimResult:
        if not all(
            isinstance(value, str) and value
            for value in (workspace_id, task_id, worker_identity, request_id)
        ) or lease_seconds < 1:
            raise V5WorkKernelError("v5.work.claim_invalid")
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)

        if task.state in TASK_TERMINAL:
            raise V5WorkKernelError("v5.work.task_terminal")
        if task.state == "BLOCKED_UNKNOWN":
            raise V5WorkKernelError("v5.work.reconcile_required")
        if task.state == "CANCEL_REQUESTED":
            raise V5WorkKernelError("v5.work.cancel_pending")
        if task.state == "LEASED":
            expires = _as_utc(task.lease_expires_at)
            if expires > now:
                raise V5WorkKernelError("v5.work.lease_held")
            current = self.session.get(WorkAttempt, task.current_attempt_id)
            if current is not None and current.state in ATTEMPT_UNKNOWABLE:
                # Started but outcome ambiguous (crash mid-execution): fail
                # closed into UNKNOWN/BLOCKED_UNKNOWN; reconcile must precede
                # retry (Master §517).
                self._mark_attempt_unknown_locked(
                    task,
                    current,
                    ambiguity_reason="lease_expired_with_active_attempt",
                    transaction_id=transaction_id,
                    request_id=request_id,
                    now=now,
                )
                raise V5WorkKernelError("v5.work.reconcile_required")
            if current is not None and current.state == "CREATED":
                # Claimed but never started: no execution happened, so there
                # is nothing ambiguous — cancel the attempt and return the
                # task to WAITING_RETRY, both on legal V4 hops.
                self._cancel_attempt_locked(
                    task,
                    current,
                    reason="lease_expired_before_start",
                    transaction_id=transaction_id,
                    request_id=request_id,
                    now=now,
                )
                self._schedule_retry_locked(
                    task,
                    failed_attempt_id=current.attempt_id,
                    reason="lease_expired_before_start",
                    transaction_id=transaction_id,
                    request_id=request_id,
                    now=now,
                )
            elif current is not None and current.state == "CANCEL_REQUESTED":
                # Cancellation was already requested; finish it on the legal
                # CANCEL_REQUESTED -> CANCELLED hop and settle the task.
                self._finish_cancel_locked(
                    task,
                    current,
                    transaction_id=transaction_id,
                    request_id=request_id,
                    now=now,
                )
                raise V5WorkKernelError("v5.work.cancel_pending")
            elif task.state == "LEASED":
                # Attempt already terminal (defensive): the lease is dead, so
                # step the task back to WAITING_RETRY before re-leasing —
                # the V4 machine has no LEASED -> LEASED hop.
                self._schedule_retry_locked(
                    task,
                    failed_attempt_id=task.current_attempt_id,
                    reason="lease_expired",
                    transaction_id=transaction_id,
                    request_id=request_id,
                    now=now,
                )

        if task.attempt_count >= task.max_attempts:
            self._exhaust_locked(
                task,
                reason="max_attempts",
                terminal_attempt_id=task.current_attempt_id,
                transaction_id=transaction_id,
                request_id=request_id,
                now=now,
            )
            raise V5WorkKernelError("v5.work.exhausted")

        task.attempt_count += 1
        fence = (task.lease_fencing_token or 0) + 1
        attempt = WorkAttempt(
            attempt_id=new_work_attempt_id(),
            workspace_id=workspace_id,
            task_id=task.task_id,
            revision=1,
            attempt_number=task.attempt_count,
            state="CREATED",
            worker_identity=worker_identity,
            fence_token=fence,
            created_at=now,
            updated_at=now,
        )
        capability = WorkAttemptCapability(
            capability_id=new_work_capability_id(),
            workspace_id=workspace_id,
            task_id=task.task_id,
            attempt_id=attempt.attempt_id,
            scope="work.execute",
            fence_token=fence,
            capability_digest="",
            issued_by_principal=worker_identity,
            issued_at=now,
            expires_at=now + timedelta(seconds=lease_seconds),
        )
        capability.capability_digest = canonical_digest(
            {
                "capability_id": capability.capability_id,
                "workspace_id": workspace_id,
                "task_id": task.task_id,
                "attempt_id": attempt.attempt_id,
                "scope": capability.scope,
                "fence_token": fence,
                "issued_at": _wire_time(now),
                "expires_at": _wire_time(capability.expires_at),
            }
        )
        attempt_receipt_id = new_authority_receipt_id()
        attempt.authority_receipt_id = attempt_receipt_id
        attempt.record_digest = canonical_digest(_attempt_snapshot(attempt))

        task.state = "LEASED"
        task.current_attempt_id = attempt.attempt_id
        task.lease_owner = worker_identity
        task.lease_fencing_token = fence
        task.lease_expires_at = now + timedelta(seconds=lease_seconds)
        task.last_heartbeat_at = now
        task_receipt_id = new_authority_receipt_id()
        self._refresh_task_head(task, task_receipt_id)

        self.session.add_all([attempt, capability])
        self.session.flush()

        claim_event = self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=task.revision,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type="work.claimed",
            payload={
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT", attempt.attempt_id, 1, attempt.record_digest
                ),
                "worker_principal": worker_identity,
                "fencing_token": fence,
                "lease_expires_at": _wire_time(task.lease_expires_at),
            },
            command="work.claim",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=task_receipt_id,
        )
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=1,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.created",
            payload={
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT", attempt.attempt_id, 1, attempt.record_digest
                ),
                "worker_task_id": task.task_id,
                "attempt_number": attempt.attempt_number,
                "worker_identity": worker_identity,
                "fence_token": fence,
                "claim_event_id": claim_event.event_id,
                "fallback_of_attempt_id_or_null": None,
            },
            command="work.claim",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=attempt_receipt_id,
        )
        return ClaimResult(task=task, attempt=attempt, capability=capability)

    # ------------------------------------------------------------------
    # lease guards
    # ------------------------------------------------------------------
    def _require_live_lease(
        self, task: WorkTask, attempt: WorkAttempt, fencing_token: int, now: datetime
    ) -> None:
        if task.state != "LEASED":
            raise V5WorkKernelError("v5.work.lease_lost")
        if (
            task.lease_fencing_token != fencing_token
            or attempt.fence_token != fencing_token
            or task.current_attempt_id != attempt.attempt_id
        ):
            raise V5WorkKernelError("v5.work.stale_fence")
        if _as_utc(task.lease_expires_at) <= now:
            raise V5WorkKernelError("v5.work.lease_lost")

    def _locked_attempt(self, workspace_id: str, attempt_id: str) -> WorkAttempt:
        attempt = self.session.scalar(
            select(WorkAttempt)
            .where(
                WorkAttempt.workspace_id == workspace_id,
                WorkAttempt.attempt_id == attempt_id,
            )
            .with_for_update()
        )
        if attempt is None:
            raise V5WorkKernelError("v5.work.attempt_not_found")
        return attempt

    # ------------------------------------------------------------------
    # work.heartbeat
    # ------------------------------------------------------------------
    def heartbeat(
        self,
        *,
        workspace_id: str,
        task_id: str,
        attempt_id: str,
        fencing_token: int,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkTask:
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        attempt = self._locked_attempt(workspace_id, attempt_id)
        self._require_live_lease(task, attempt, fencing_token, now)
        if attempt.state not in ATTEMPT_ACTIVE:
            raise V5WorkKernelError("v5.work.attempt_not_active")

        task.lease_expires_at = now + timedelta(seconds=lease_seconds)
        task.last_heartbeat_at = now
        receipt_id = new_authority_receipt_id()
        self._refresh_task_head(task, receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=task.revision,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type="work.heartbeat_recorded",
            payload={
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "attempt_id": attempt.attempt_id,
                "fencing_token": fencing_token,
                "lease_expires_at": _wire_time(task.lease_expires_at),
            },
            command="work.heartbeat",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )
        return task

    # ------------------------------------------------------------------
    # attempts.start / record-receipt
    # ------------------------------------------------------------------
    def start_attempt(
        self,
        *,
        workspace_id: str,
        task_id: str,
        attempt_id: str,
        fencing_token: int,
        runtime_adapter: str,
        runtime_session: str,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkAttempt:
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        attempt = self._locked_attempt(workspace_id, attempt_id)
        self._require_live_lease(task, attempt, fencing_token, now)
        if attempt.state != "CREATED":
            raise V5WorkKernelError("v5.work.attempt_state_invalid")

        capability = self.session.scalar(
            select(WorkAttemptCapability).where(
                WorkAttemptCapability.workspace_id == workspace_id,
                WorkAttemptCapability.attempt_id == attempt_id,
            )
        )
        if capability is None or capability.consumed_at is not None:
            raise V5WorkKernelError("v5.work.capability_consumed")
        capability.consumed_at = now

        # V4 machine: CREATED --attempt.starting--> STARTING
        # --attempt.started--> RUNNING.  Both hops happen in this one unit of
        # work; neither event may be skipped.
        attempt.state = "STARTING"
        starting_receipt_id = new_authority_receipt_id()
        self._refresh_attempt_head(attempt, starting_receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.starting",
            payload={
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "capability_id": capability.capability_id,
                "runtime_adapter": runtime_adapter,
            },
            command="attempts.start",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=starting_receipt_id,
        )

        attempt.state = "RUNNING"
        attempt.started_at = now
        receipt_id = new_authority_receipt_id()
        self._refresh_attempt_head(attempt, receipt_id)
        self.session.flush()
        base = _binding(
            "WORK_ATTEMPT", attempt.attempt_id, attempt.revision, attempt.record_digest
        )
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.started",
            payload={
                "exact_attempt_binding": base,
                "runtime_session": runtime_session,
            },
            command="attempts.start",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )
        return attempt

    def record_output(
        self,
        *,
        workspace_id: str,
        task_id: str,
        attempt_id: str,
        fencing_token: int,
        output_payload: dict[str, Any],
        stream_complete: bool,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkAttempt:
        if not isinstance(output_payload, dict) or not output_payload:
            raise V5WorkKernelError("v5.work.output_invalid")
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        attempt = self._locked_attempt(workspace_id, attempt_id)
        self._require_live_lease(task, attempt, fencing_token, now)
        if attempt.state != "RUNNING":
            raise V5WorkKernelError("v5.work.attempt_state_invalid")
        attempt.state = "OUTPUT_RECORDED"
        attempt.output_payload = output_payload
        attempt.output_digest = canonical_digest(output_payload)
        receipt_id = new_authority_receipt_id()
        self._refresh_attempt_head(attempt, receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.output_recorded",
            payload={
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "output_digest": attempt.output_digest,
                "stream_complete": bool(stream_complete),
            },
            command="attempts.record-receipt",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )
        return attempt

    # ------------------------------------------------------------------
    # attempts.complete / fail — task outcome follows in the same unit
    # ------------------------------------------------------------------
    def complete_attempt(
        self,
        *,
        workspace_id: str,
        task_id: str,
        attempt_id: str,
        fencing_token: int,
        terminal_receipt_digest: str,
        accepted_proposal_id: str | None = None,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkAttempt:
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        attempt = self._locked_attempt(workspace_id, attempt_id)
        self._require_live_lease(task, attempt, fencing_token, now)
        if attempt.state != "OUTPUT_RECORDED" or not attempt.output_digest:
            raise V5WorkKernelError("v5.work.attempt_state_invalid")
        if accepted_proposal_id is not None:
            proposal = self.session.get(WorkProposal, accepted_proposal_id)
            if (
                proposal is None
                or proposal.workspace_id != workspace_id
                or proposal.status != "ACCEPTED"
            ):
                raise V5WorkKernelError("v5.work.proposal_not_accepted")

        attempt.state = "SUCCEEDED"
        attempt.ended_at = now
        attempt_receipt_id = new_authority_receipt_id()
        self._refresh_attempt_head(attempt, attempt_receipt_id)
        task.state = "COMPLETED"
        task.terminal_reason = "completed"
        task.lease_owner = None
        # fencing tokens are monotonic for the task lifetime: the last
        # issued token is retained so a later claim mints a strictly
    # greater one, and stale writers stay rejected.
        task.lease_expires_at = None
        task_receipt_id = new_authority_receipt_id()
        self._refresh_task_head(task, task_receipt_id)
        self.session.flush()

        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.succeeded",
            payload={
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "output_digest": attempt.output_digest,
                "terminal_receipt_digest": terminal_receipt_digest,
            },
            command="attempts.complete",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=attempt_receipt_id,
        )
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=task.revision,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type="work.completed",
            payload={
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "terminal_attempt_id": attempt.attempt_id,
                "accepted_proposal_id_or_null": accepted_proposal_id,
            },
            command="attempts.complete",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=task_receipt_id,
        )
        return attempt

    def fail_attempt(
        self,
        *,
        workspace_id: str,
        task_id: str,
        attempt_id: str,
        fencing_token: int,
        failure_code: str,
        terminal_receipt_digest: str | None = None,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkAttempt:
        if not failure_code:
            raise V5WorkKernelError("v5.work.failure_code_required")
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        attempt = self._locked_attempt(workspace_id, attempt_id)
        self._require_live_lease(task, attempt, fencing_token, now)
        if attempt.state not in ATTEMPT_FAILABLE:
            raise V5WorkKernelError("v5.work.attempt_state_invalid")

        attempt.state = "FAILED"
        attempt.ended_at = now
        attempt_receipt_id = new_authority_receipt_id()
        self._refresh_attempt_head(attempt, attempt_receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.failed",
            payload={
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "failure_code": failure_code,
                "terminal_receipt_digest_or_null": terminal_receipt_digest,
            },
            command="attempts.fail",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=attempt_receipt_id,
        )

        task.lease_owner = None
        # fencing tokens are monotonic for the task lifetime: the last
        # issued token is retained so a later claim mints a strictly
    # greater one, and stale writers stay rejected.
        task.lease_expires_at = None
        task.current_attempt_id = attempt.attempt_id
        if task.attempt_count < task.max_attempts:
            task.state = "WAITING_RETRY"
            task_receipt_id = new_authority_receipt_id()
            self._refresh_task_head(task, task_receipt_id)
            self.session.flush()
            self._write_fact(
                workspace_id=workspace_id,
                subject_kind="WORK_TASK",
                subject_id=task.task_id,
                subject_revision=task.revision,
                subject_digest=task.record_digest,
                aggregate_type="worker_task",
                event_type="work.retry_scheduled",
                payload={
                    "exact_work_task_binding": _binding(
                        "WORK_TASK", task.task_id, task.revision, task.record_digest
                    ),
                    "failed_attempt_id": attempt.attempt_id,
                    "reason": failure_code,
                },
                command="attempts.fail",
                transaction_id=transaction_id,
                request_id=request_id,
                now=now,
                authority_receipt_id=task_receipt_id,
            )
        else:
            self._exhaust_locked(
                task,
                reason="max_attempts",
                terminal_attempt_id=attempt.attempt_id,
                transaction_id=transaction_id,
                request_id=request_id,
                now=now,
            )
        return attempt

    def _cancel_attempt_locked(
        self,
        task: WorkTask,
        attempt: WorkAttempt,
        *,
        reason: str,
        transaction_id: str,
        request_id: str,
        now: datetime,
    ) -> None:
        """CREATED/STARTING/RUNNING -> CANCEL_REQUESTED -> CANCELLED, two
        legal hops with both events, in one unit of work."""
        attempt.state = "CANCEL_REQUESTED"
        receipt_id = new_authority_receipt_id()
        self._refresh_attempt_head(attempt, receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=task.workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.cancel_requested",
            payload={
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "reason": reason,
            },
            command="attempts.cancel",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )
        self._finish_cancel_attempt_row(attempt, now)
        cancelled_receipt_id = new_authority_receipt_id()
        self._refresh_attempt_head(attempt, cancelled_receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=task.workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.cancelled",
            payload={
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "cancellation_receipt_digest": attempt.record_digest,
            },
            command="attempts.cancel",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=cancelled_receipt_id,
        )

    @staticmethod
    def _finish_cancel_attempt_row(attempt: WorkAttempt, now: datetime) -> None:
        attempt.state = "CANCELLED"
        attempt.ended_at = now

    def _finish_cancel_locked(
        self,
        task: WorkTask,
        attempt: WorkAttempt,
        *,
        transaction_id: str,
        request_id: str,
        now: datetime,
    ) -> None:
        """Finish an in-flight cancellation: attempt CANCEL_REQUESTED ->
        CANCELLED, then task CANCEL_REQUESTED -> CANCELLED."""
        self._finish_cancel_attempt_row(attempt, now)
        attempt_receipt_id = new_authority_receipt_id()
        self._refresh_attempt_head(attempt, attempt_receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=task.workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.cancelled",
            payload={
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "cancellation_receipt_digest": attempt.record_digest,
            },
            command="attempts.cancel",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=attempt_receipt_id,
        )
        task.state = "CANCELLED"
        task.terminal_reason = "cancelled"
        task.lease_owner = None
        task.lease_expires_at = None
        task_receipt_id = new_authority_receipt_id()
        self._refresh_task_head(task, task_receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=task.workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=task.revision,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type="work.cancelled",
            payload={
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "terminal_attempt_id": attempt.attempt_id,
                "cancellation_receipt_digest": attempt.record_digest,
            },
            command="attempts.cancel",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=task_receipt_id,
        )

    def _schedule_retry_locked(
        self,
        task: WorkTask,
        *,
        failed_attempt_id: str | None,
        reason: str,
        transaction_id: str,
        request_id: str,
        now: datetime,
    ) -> None:
        """LEASED -> WAITING_RETRY with the work.retry_scheduled event."""
        task.state = "WAITING_RETRY"
        task.lease_owner = None
        task.lease_expires_at = None
        receipt_id = new_authority_receipt_id()
        self._refresh_task_head(task, receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=task.workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=task.revision,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type="work.retry_scheduled",
            payload={
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "failed_attempt_id": failed_attempt_id,
                "reason": reason,
            },
            command="attempts.fail",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )

    def _exhaust_locked(
        self,
        task: WorkTask,
        *,
        reason: str,
        terminal_attempt_id: str | None,
        transaction_id: str,
        request_id: str,
        now: datetime,
    ) -> None:
        task.state = "EXHAUSTED"
        task.terminal_reason = reason
        task.lease_owner = None
        # fencing tokens are monotonic for the task lifetime: the last
        # issued token is retained so a later claim mints a strictly
    # greater one, and stale writers stay rejected.
        task.lease_expires_at = None
        receipt_id = new_authority_receipt_id()
        self._refresh_task_head(task, receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=task.workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=task.revision,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type="work.exhausted",
            payload={
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "terminal_attempt_id": terminal_attempt_id,
                "attempts_used": task.attempt_count,
                "reason": reason,
            },
            command="work.exhaust",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )

    # ------------------------------------------------------------------
    # work.cancel-request / attempts.cancel — best-effort cancellation
    # ------------------------------------------------------------------
    def cancel_task(
        self,
        *,
        workspace_id: str,
        task_id: str,
        reason: str,
        requested_by_principal: str,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkTask:
        if not reason:
            raise V5WorkKernelError("v5.work.cancel_reason_required")
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        if task.state in TASK_TERMINAL:
            raise V5WorkKernelError("v5.work.task_terminal")
        if task.state == "CANCEL_REQUESTED":
            return task
        # V4 worker_task machine: cancel_requested only from LEASED or
        # WAITING_RETRY.  A QUEUED task cannot be cancelled (it has nothing
        # in flight); BLOCKED_UNKNOWN must be reconciled first.
        if task.state not in ("LEASED", "WAITING_RETRY"):
            raise V5WorkKernelError("v5.work.cancel_not_cancellable")
        task.state = "CANCEL_REQUESTED"
        receipt_id = new_authority_receipt_id()
        self._refresh_task_head(task, receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=task.revision,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type="work.cancel_requested",
            payload={
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "reason": reason,
                "requested_by_principal": requested_by_principal,
            },
            command="work.cancel-request",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )
        return task

    def cancel_attempt(
        self,
        *,
        workspace_id: str,
        task_id: str,
        attempt_id: str,
        reason: str,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkAttempt:
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        attempt = self._locked_attempt(workspace_id, attempt_id)
        if attempt.state not in ATTEMPT_CANCELLABLE:
            raise V5WorkKernelError("v5.work.attempt_state_invalid")
        self._cancel_attempt_locked(
            task,
            attempt,
            reason=reason,
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
        )
        if task.state == "CANCEL_REQUESTED":
            task.state = "CANCELLED"
            task.terminal_reason = "cancelled"
            task.lease_owner = None
            task.lease_expires_at = None
            task_receipt_id = new_authority_receipt_id()
            self._refresh_task_head(task, task_receipt_id)
            self.session.flush()
            self._write_fact(
                workspace_id=workspace_id,
                subject_kind="WORK_TASK",
                subject_id=task.task_id,
                subject_revision=task.revision,
                subject_digest=task.record_digest,
                aggregate_type="worker_task",
                event_type="work.cancelled",
                payload={
                    "exact_work_task_binding": _binding(
                        "WORK_TASK", task.task_id, task.revision, task.record_digest
                    ),
                    "terminal_attempt_id": attempt.attempt_id,
                    "cancellation_receipt_digest": attempt.record_digest,
                },
                command="attempts.cancel",
                transaction_id=transaction_id,
                request_id=request_id,
                now=now,
                authority_receipt_id=task_receipt_id,
            )
        return attempt

    # ------------------------------------------------------------------
    # attempts.mark-unknown / reconcile — the crash-recovery boundary
    # ------------------------------------------------------------------
    def _mark_attempt_unknown_locked(
        self,
        task: WorkTask,
        attempt: WorkAttempt,
        *,
        ambiguity_reason: str,
        transaction_id: str,
        request_id: str,
        now: datetime,
    ) -> None:
        attempt.state = "UNKNOWN"
        attempt_receipt_id = new_authority_receipt_id()
        self._refresh_attempt_head(attempt, attempt_receipt_id)
        task.state = "BLOCKED_UNKNOWN"
        task.lease_owner = None
        # fencing tokens are monotonic for the task lifetime: the last
        # issued token is retained so a later claim mints a strictly
    # greater one, and stale writers stay rejected.
        task.lease_expires_at = None
        task_receipt_id = new_authority_receipt_id()
        self._refresh_task_head(task, task_receipt_id)
        self.session.flush()
        self._write_fact(
            workspace_id=task.workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type="attempt.unknown",
            payload={
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "ambiguity_reason": ambiguity_reason,
                "reconciliation_required": True,
            },
            command="attempts.mark-unknown",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=attempt_receipt_id,
        )
        self._write_fact(
            workspace_id=task.workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=task.revision,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type="work.blocked_unknown",
            payload={
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "unknown_attempt_id": attempt.attempt_id,
                "reconciliation_required": True,
            },
            command="attempts.mark-unknown",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=task_receipt_id,
        )

    def mark_attempt_unknown(
        self,
        *,
        workspace_id: str,
        task_id: str,
        attempt_id: str,
        ambiguity_reason: str,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkAttempt:
        if not ambiguity_reason:
            raise V5WorkKernelError("v5.work.ambiguity_reason_required")
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        attempt = self._locked_attempt(workspace_id, attempt_id)
        if attempt.state not in ATTEMPT_UNKNOWABLE:
            raise V5WorkKernelError("v5.work.attempt_state_invalid")
        self._mark_attempt_unknown_locked(
            task,
            attempt,
            ambiguity_reason=ambiguity_reason,
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
        )
        return attempt

    def reconcile_attempt(
        self,
        *,
        workspace_id: str,
        task_id: str,
        attempt_id: str,
        outcome: str,
        reconciliation_receipt_digest: str,
        accepted_proposal_id: str | None = None,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkAttempt:
        if outcome not in ("succeeded", "failed"):
            raise V5WorkKernelError("v5.work.reconcile_outcome_invalid")
        if not reconciliation_receipt_digest:
            raise V5WorkKernelError("v5.work.reconcile_receipt_required")
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        attempt = self._locked_attempt(workspace_id, attempt_id)
        if task.state != "BLOCKED_UNKNOWN" or attempt.state != "UNKNOWN":
            raise V5WorkKernelError("v5.work.reconcile_not_applicable")

        attempt_receipt_id = new_authority_receipt_id()
        task_receipt_id = new_authority_receipt_id()
        if outcome == "succeeded":
            if not attempt.output_digest:
                raise V5WorkKernelError("v5.work.reconcile_output_missing")
            attempt.state = "SUCCEEDED"
            attempt.ended_at = now
            self._refresh_attempt_head(attempt, attempt_receipt_id)
            attempt_event_type = "attempt.reconciled_succeeded"
            attempt_payload = {
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "reconciliation_receipt_digest": reconciliation_receipt_digest,
                "output_digest": attempt.output_digest,
            }
            task.state = "COMPLETED"
            task.terminal_reason = "reconciled_completed"
            self._refresh_task_head(task, task_receipt_id)
            task_event_type = "work.unknown_reconciled_completed"
            task_payload = {
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "unknown_attempt_id": attempt.attempt_id,
                "reconciliation_receipt_digest": reconciliation_receipt_digest,
                "accepted_proposal_id_or_null": accepted_proposal_id,
            }
        else:
            attempt.state = "FAILED"
            attempt.ended_at = now
            self._refresh_attempt_head(attempt, attempt_receipt_id)
            attempt_event_type = "attempt.reconciled_failed"
            attempt_payload = {
                "exact_attempt_binding": _binding(
                    "WORK_ATTEMPT",
                    attempt.attempt_id,
                    attempt.revision,
                    attempt.record_digest,
                ),
                "reconciliation_receipt_digest": reconciliation_receipt_digest,
                "failure_code": "reconciled_failed",
            }
            # V4 machine: unknown_reconciled_retry only ever lands on
            # WAITING_RETRY.  Exhaustion is a separate hop with its own event.
            task.state = "WAITING_RETRY"
            task_event_type = "work.unknown_reconciled_retry"
            self._refresh_task_head(task, task_receipt_id)
            task_payload = {
                "exact_work_task_binding": _binding(
                    "WORK_TASK", task.task_id, task.revision, task.record_digest
                ),
                "unknown_attempt_id": attempt.attempt_id,
                "reconciliation_receipt_digest": reconciliation_receipt_digest,
            }
        task.lease_owner = None
        # fencing tokens are monotonic for the task lifetime: the last issued
        # token is retained so a later claim mints a strictly greater one and
        # stale writers stay rejected.
        task.lease_expires_at = None
        self.session.flush()
        reconcile_exhausts = (
            outcome == "failed" and task.attempt_count >= task.max_attempts
        )
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_ATTEMPT",
            subject_id=attempt.attempt_id,
            subject_revision=attempt.revision,
            subject_digest=attempt.record_digest,
            aggregate_type="attempt",
            event_type=attempt_event_type,
            payload=attempt_payload,
            command="attempts.reconcile",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=attempt_receipt_id,
        )
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_TASK",
            subject_id=task.task_id,
            subject_revision=task.revision,
            subject_digest=task.record_digest,
            aggregate_type="worker_task",
            event_type=task_event_type,
            payload=task_payload,
            command="attempts.reconcile",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=task_receipt_id,
        )
        if reconcile_exhausts:
            # Second legal hop: WAITING_RETRY --work.exhausted--> EXHAUSTED.
            self._exhaust_locked(
                task,
                reason="max_attempts",
                terminal_attempt_id=attempt.attempt_id,
                transaction_id=transaction_id,
                request_id=request_id,
                now=now,
            )
        return attempt

    # ------------------------------------------------------------------
    # proposals (proposal-controller)
    # ------------------------------------------------------------------
    def submit_proposal(
        self,
        *,
        workspace_id: str,
        task_id: str,
        proposer_principal: str,
        payload: dict[str, Any],
        attempt_id: str | None = None,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkProposal:
        if not isinstance(payload, dict) or not payload:
            raise V5WorkKernelError("v5.work.proposal_invalid")
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        task = self._locked_task(workspace_id, task_id)
        # Post-action proposals are rejected: a terminal task can no longer
        # be influenced, so proposing one is a contract violation, not a noop.
        if task.state in TASK_TERMINAL:
            raise V5WorkKernelError("v5.work.proposal_post_action")
        proposal = WorkProposal(
            proposal_id=new_work_proposal_id(),
            workspace_id=workspace_id,
            revision=1,
            task_id=task_id,
            attempt_id=attempt_id,
            proposer_principal=proposer_principal,
            payload=payload,
            payload_digest=canonical_digest(payload),
            status="SUBMITTED",
            created_at=now,
        )
        receipt_id = new_authority_receipt_id()
        proposal.authority_receipt_id = receipt_id
        proposal.record_digest = canonical_digest(_proposal_snapshot(proposal))
        self.session.add(proposal)
        self.session.flush()
        self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_PROPOSAL",
            subject_id=proposal.proposal_id,
            subject_revision=1,
            subject_digest=proposal.record_digest,
            aggregate_type="proposal",
            event_type="proposal.submitted",
            payload={
                "exact_proposal_binding": _binding(
                    "WORK_PROPOSAL", proposal.proposal_id, 1, proposal.record_digest
                ),
                "proposal_digest": proposal.payload_digest,
                "worker_task_id": task_id,
                "authored_by_principal": proposer_principal,
                "submitted_by_principal": proposer_principal,
                "controlled_action_not_started": True,
            },
            command="proposals.submit",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )
        return proposal

    def decide_proposal(
        self,
        *,
        workspace_id: str,
        proposal_id: str,
        decided_by_principal: str,
        accept: bool,
        downstream_intent: str | None = None,
        downstream_command: str | None = None,
        reason_code: str | None = None,
        rationale: str | None = None,
        transaction_id: str | None = None,
        request_id: str,
    ) -> WorkProposalDecision:
        now = self.clock()
        transaction_id = transaction_id or new_transaction_id()
        proposal = self.session.scalar(
            select(WorkProposal)
            .where(
                WorkProposal.workspace_id == workspace_id,
                WorkProposal.proposal_id == proposal_id,
            )
            .with_for_update()
        )
        if proposal is None:
            raise V5WorkKernelError("v5.work.proposal_not_found")
        if proposal.status != "SUBMITTED":
            raise V5WorkKernelError("v5.work.proposal_already_decided")
        if accept and not (downstream_intent and downstream_command):
            # Ghost-success guard: an accept that cannot name its first
            # downstream owner command is rejected outright.
            raise V5WorkKernelError("v5.work.downstream_intent_required")
        if not accept and not reason_code:
            raise V5WorkKernelError("v5.work.reason_code_required")

        decision = WorkProposalDecision(
            decision_id=new_work_decision_id(),
            workspace_id=workspace_id,
            revision=1,
            proposal_id=proposal_id,
            decision="ACCEPTED" if accept else "REJECTED",
            decided_by_principal=decided_by_principal,
            rationale=rationale,
            downstream_intent=downstream_intent if accept else None,
        )
        receipt_id = new_authority_receipt_id()
        decision.authority_receipt_id = receipt_id
        decision.record_digest = canonical_digest(_decision_snapshot(decision))
        proposal.status = "ACCEPTED" if accept else "REJECTED"
        proposal.decided_at = now
        proposal.revision += 1
        proposal.record_digest = canonical_digest(_proposal_snapshot(proposal))
        self.session.add(decision)
        self.session.flush()

        reaction: WorkReactionLedger | None = None
        downstream_reaction_id: str | None = None
        if accept:
            # The first downstream owner command is submitted in the same
            # transaction; decision without it would be a ghost success.
            reaction = WorkReactionLedger(
                reaction_id=new_work_reaction_id(),
                workspace_id=workspace_id,
                source_event_id="",
                source_event_seq=0,
                owner_command=downstream_command,
                target_aggregate_type="worker_task",
                target_aggregate_id=proposal.task_id,
                status="PENDING",
                created_at=now,
            )
            downstream_reaction_id = reaction.reaction_id
            self.session.add(reaction)
            self.session.flush()

        event = self._write_fact(
            workspace_id=workspace_id,
            subject_kind="WORK_PROPOSAL_DECISION",
            subject_id=decision.decision_id,
            subject_revision=1,
            subject_digest=decision.record_digest,
            aggregate_type="proposal_decision",
            event_type="proposal.accepted" if accept else "proposal.rejected",
            payload=(
                {
                    "exact_proposal_decision_binding": _binding(
                        "WORK_PROPOSAL_DECISION",
                        decision.decision_id,
                        1,
                        decision.record_digest,
                    ),
                    "proposal_id": proposal_id,
                    "proposal_digest": proposal.payload_digest,
                    "downstream_intent": downstream_intent,
                    "downstream_command": downstream_command,
                    "downstream_reaction_id": downstream_reaction_id,
                }
                if accept
                else {
                    "exact_proposal_decision_binding": _binding(
                        "WORK_PROPOSAL_DECISION",
                        decision.decision_id,
                        1,
                        decision.record_digest,
                    ),
                    "proposal_id": proposal_id,
                    "proposal_digest": proposal.payload_digest,
                    "reason_code": reason_code,
                }
            ),
            command="proposals.decide",
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=receipt_id,
        )
        if reaction is not None:
            reaction.source_event_id = event.event_id
            reaction.source_event_seq = event.seq
            reaction.status = "SUBMITTED"
            reaction.submitted_at = now
            self.session.flush()
        return decision
