"""V5-2A Work event channel dispatcher (D-016, Master §6 2A-3).

Consumes the dedicated ``v5.work.events`` outbox channel.  A reaction may
only submit the next owner command — it never writes domain success.  The
reaction ledger is the idempotency boundary: one row per
(source_event_id, owner_command), so a redelivered event cannot mint a
second reaction.  Per-aggregate causal order is preserved by refusing to
process an event while an earlier event of the same aggregate is undelivered.

The legacy fixed worker owns only ``LEGACY_OUTBOX_CHANNELS`` and never
claims this channel; this dispatcher owns ``v5.work.events`` and never
touches the legacy lanes.  That separation is the explicit disposition of
the legacy worker's deliberate ignorance of the V5 Work channel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Outbox
from app.models.v5_work_tables import (
    V5_WORK_EVENT_CHANNEL,
    WorkAttempt,
    WorkReactionLedger,
    WorkTask,
)
from app.services.v5_work_kernel import V5WorkKernelError, WorkKernelService
from app.utils.ids import new_transaction_id, new_work_reaction_id
from app.utils.v4_integrity import canonical_digest


class V5WorkDispatcherError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DispatchResult:
    outbox_id: str
    event_type: str
    reaction: str  # "none" | "submitted" | "already_recorded"
    reaction_id: str | None


class WorkReactionDispatcher:
    """PG dispatcher for the versioned Work event channel."""

    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] | None = None,
        max_delivery_attempts: int = 8,
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.kernel = WorkKernelService(session, clock=self.clock)
        self.max_delivery_attempts = max_delivery_attempts

    # ------------------------------------------------------------------
    # claim: per-aggregate causal order + skip-locked
    # ------------------------------------------------------------------
    def claim_next(self) -> Outbox | None:
        statement = (
            select(Outbox)
            .where(
                Outbox.channel == V5_WORK_EVENT_CHANNEL,
                Outbox.status == "PENDING",
            )
            .order_by(Outbox.created_at.asc(), Outbox.outbox_id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = self.session.scalar(statement)
        if row is None:
            return None
        earlier_pending = self.session.scalar(
            select(Outbox.outbox_id)
            .where(
                Outbox.channel == V5_WORK_EVENT_CHANNEL,
                Outbox.aggregate_id == row.aggregate_id,
                Outbox.source_event_seq < row.source_event_seq,
                Outbox.status.in_(("PENDING", "PROCESSING")),
                Outbox.outbox_id != row.outbox_id,
            )
            .limit(1)
        )
        if earlier_pending is not None:
            return None
        now = self.clock()
        row.status = "PROCESSING"
        row.attempts = int(row.attempts) + 1
        row.claimed_at = now
        self.session.flush()
        return row

    # ------------------------------------------------------------------
    # dispatch one claimed row
    # ------------------------------------------------------------------
    def dispatch(self, row: Outbox) -> DispatchResult:
        if row.channel != V5_WORK_EVENT_CHANNEL:
            raise V5WorkDispatcherError("v5.work.dispatcher_channel_forbidden")
        if canonical_digest(row.payload) != row.payload_digest:
            self._dead_letter(row, "payload_digest_mismatch")
            raise V5WorkDispatcherError("v5.work.dispatcher_payload_mismatch")
        reaction, reaction_id = self._react(row)
        row.status = "SENT"
        row.sent_at = self.clock()
        self.session.flush()
        return DispatchResult(
            outbox_id=row.outbox_id,
            event_type=row.event_type,
            reaction=reaction,
            reaction_id=reaction_id,
        )

    def _react(self, row: Outbox) -> tuple[str, str | None]:
        """Map a Work event to its single allowed follow-up owner command."""
        event_type = row.event_type
        if event_type == "work.cancel_requested":
            return self._react_cancel_requested(row)
        # Every other Work event carries no 2A reaction.  They are delivered
        # and acked; downstream stages (2B+) own richer reactions.
        return "none", None

    def _react_cancel_requested(self, row: Outbox) -> tuple[str, str | None]:
        # The outbox envelope's exact_subject_binding is the task binding for
        # work.cancel_requested; no need to unwrap the inner event payload.
        binding = (row.payload or {}).get("exact_subject_binding") or {}
        task_id = binding.get("id")
        workspace_id = row.workspace_id
        if not isinstance(task_id, str) or not task_id:
            raise V5WorkDispatcherError("v5.work.dispatcher_payload_mismatch")
        existing = self.session.scalar(
            select(WorkReactionLedger).where(
                WorkReactionLedger.source_event_id == row.source_event_id,
                WorkReactionLedger.owner_command == "attempts.cancel",
            )
        )
        if existing is not None:
            return "already_recorded", existing.reaction_id
        task = self.session.scalar(
            select(WorkTask).where(
                WorkTask.workspace_id == workspace_id,
                WorkTask.task_id == task_id,
            )
        )
        if task is None:
            raise V5WorkDispatcherError("v5.work.dispatcher_target_missing")
        now = self.clock()
        # Submit the next owner command when there is a live attempt to
        # cancel.  If the task already settled, the reaction is recorded as
        # submitted with no-op effect — it may not resurrect anything.
        attempt = (
            self.session.get(WorkAttempt, task.current_attempt_id)
            if task.current_attempt_id
            else None
        )
        submitted_command = "attempts.cancel"
        if (
            task.state == "CANCEL_REQUESTED"
            and attempt is not None
            and attempt.state in ("CREATED", "STARTING", "RUNNING")
        ):
            try:
                self.kernel.cancel_attempt(
                    workspace_id=workspace_id,
                    task_id=task.task_id,
                    attempt_id=attempt.attempt_id,
                    reason="cancel_requested",
                    transaction_id=new_transaction_id(),
                    request_id=f"rxn_{row.source_event_id}",
                )
            except V5WorkKernelError:
                # Best-effort cancellation (V4 failure semantics): the task
                # may have already advanced; the reaction row still records
                # that the command was submitted.
                pass
        reaction = WorkReactionLedger(
            reaction_id=new_work_reaction_id(),
            workspace_id=workspace_id,
            source_event_id=row.source_event_id,
            source_event_seq=row.source_event_seq,
            owner_command=submitted_command,
            target_aggregate_type="worker_task",
            target_aggregate_id=task_id,
            status="SUBMITTED",
            created_at=now,
            submitted_at=now,
        )
        self.session.add(reaction)
        self.session.flush()
        return "submitted", reaction.reaction_id

    def _dead_letter(self, row: Outbox, reason: str) -> None:
        row.status = "DEAD"
        row.last_error = reason
        self.session.flush()

    def poll_once(self) -> DispatchResult | None:
        """Claim and dispatch one pending Work event, or None when idle."""
        row = self.claim_next()
        if row is None:
            return None
        return self.dispatch(row)
