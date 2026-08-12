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

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.models import Outbox
from app.models.tables import OutboxDeliveryReceipt
from app.models.v5_work_tables import (
    V5_WORK_EVENT_CHANNEL,
    WorkAttempt,
    WorkReactionLedger,
    WorkTask,
)
from app.services.v5_work_kernel import V5WorkKernelError, WorkKernelService
from app.services.v4_audit import V4AuditService
from app.utils.ids import (
    new_outbox_receipt_id,
    new_transaction_id,
    new_work_reaction_id,
    short_token,
)
from app.utils.v4_integrity import canonical_digest

logger = logging.getLogger("control_plane.v5_work_dispatcher")


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


@dataclass(frozen=True)
class WorkDispatchSnapshot:
    outbox_id: str
    payload_digest: str
    attempts: int
    claim_token: str


class WorkReactionDispatcher:
    """PG dispatcher for the versioned Work event channel."""

    def __init__(
        self,
        session: Session,
        *,
        clock: Callable[[], datetime] | None = None,
        max_delivery_attempts: int = 8,
        worker_id: str | None = None,
        claim_ttl_seconds: int = 30,
        retry_initial_seconds: int = 2,
        retry_max_seconds: int = 300,
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.kernel = WorkKernelService(session, clock=self.clock)
        self.max_delivery_attempts = max_delivery_attempts
        self.worker_id = worker_id or f"v5-work-worker-{short_token(12)}"
        self.claim_ttl_seconds = claim_ttl_seconds
        self.retry_initial_seconds = retry_initial_seconds
        self.retry_max_seconds = retry_max_seconds

    # ------------------------------------------------------------------
    # claim: per-aggregate causal order + skip-locked
    # ------------------------------------------------------------------
    def claim_next(self) -> Outbox | None:
        now = self.clock()
        self._dead_letter_exhausted(now)
        predecessor = aliased(Outbox)
        unresolved_predecessor = (
            select(predecessor.outbox_id)
            .where(
                predecessor.channel == V5_WORK_EVENT_CHANNEL,
                predecessor.aggregate_id == Outbox.aggregate_id,
                predecessor.source_event_seq < Outbox.source_event_seq,
                predecessor.status != "SENT",
            )
            .correlate(Outbox)
            .exists()
        )
        statement = (
            select(Outbox)
            .where(
                Outbox.channel == V5_WORK_EVENT_CHANNEL,
                Outbox.attempts < self.max_delivery_attempts,
                or_(
                    and_(
                        Outbox.status == "PENDING",
                        or_(
                            Outbox.next_retry_at.is_(None),
                            Outbox.next_retry_at <= now,
                        ),
                    ),
                    and_(
                        Outbox.status == "PROCESSING",
                        Outbox.claim_expires_at.is_not(None),
                        Outbox.claim_expires_at <= now,
                    ),
                ),
                ~unresolved_predecessor,
            )
            .order_by(Outbox.created_at.asc(), Outbox.outbox_id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = self.session.scalar(statement)
        if row is None:
            return None
        claim_token = f"clm_{short_token(24)}"
        row.status = "PROCESSING"
        row.attempts = int(row.attempts) + 1
        if row.first_attempted_at is None:
            row.first_attempted_at = now
        row.claimed_by = self.worker_id
        row.claim_token = claim_token
        row.claimed_at = now
        row.claim_expires_at = now + timedelta(seconds=self.claim_ttl_seconds)
        row.next_retry_at = None
        self.session.flush()
        return row

    def _dead_letter_exhausted(self, now: datetime) -> None:
        row = self.session.scalar(
            select(Outbox)
            .where(
                Outbox.channel == V5_WORK_EVENT_CHANNEL,
                Outbox.attempts >= self.max_delivery_attempts,
                or_(
                    and_(
                        Outbox.status == "PENDING",
                        or_(
                            Outbox.next_retry_at.is_(None),
                            Outbox.next_retry_at <= now,
                        ),
                    ),
                    and_(
                        Outbox.status == "PROCESSING",
                        Outbox.claim_expires_at.is_not(None),
                        Outbox.claim_expires_at <= now,
                    ),
                ),
            )
            .order_by(Outbox.created_at.asc(), Outbox.outbox_id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is not None:
            self._dead_letter(row, "delivery_attempts_exhausted")

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
        now = self.clock()
        normalized_reaction = (
            "submitted"
            if reaction in ("submitted", "already_recorded")
            else "none"
        )
        receipt = {
            "schema_version": "1.0",
            "status": "consumed",
            "consumer": "v5-work-reaction-dispatcher",
            "outbox_id": row.outbox_id,
            "source_event_id": row.source_event_id,
            "payload_digest": row.payload_digest,
            "reaction": normalized_reaction,
            "reaction_id": reaction_id,
        }
        existing_receipt = self.session.scalar(
            select(OutboxDeliveryReceipt).where(
                OutboxDeliveryReceipt.outbox_id == row.outbox_id
            )
        )
        if existing_receipt is not None:
            if (
                existing_receipt.payload_digest != row.payload_digest
                or existing_receipt.receipt != receipt
            ):
                raise V5WorkDispatcherError("v5.work.dispatcher_receipt_conflict")
        else:
            self.session.add(
                OutboxDeliveryReceipt(
                    receipt_id=new_outbox_receipt_id(),
                    outbox_id=row.outbox_id,
                    source_event_id=row.source_event_id,
                    channel=row.channel,
                    payload_digest=row.payload_digest,
                    receipt=receipt,
                    delivered_at=now,
                )
            )
        V4AuditService(self.session, clock=self.clock).record(
            workspace_id=row.workspace_id,
            actor_principal=self.worker_id,
            action="v5.work.outbox.sent",
            target=row.outbox_id,
            params={
                "source_event_id": row.source_event_id,
                "event_type": row.event_type,
                "attempts": int(row.attempts),
            },
            transaction_id=new_transaction_id(),
            evidence_refs={"receipt_digest": canonical_digest(receipt)},
            occurred_at=now,
        )
        row.status = "SENT"
        row.sent_at = now
        row.last_error = None
        row.next_retry_at = None
        row.receipt = receipt
        self._clear_claim(row)
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
        if task.state == "CANCEL_REQUESTED":
            if attempt is None:
                raise V5WorkDispatcherError("v5.work.dispatcher_target_missing")
            self.kernel.cancel_attempt(
                workspace_id=workspace_id,
                task_id=task.task_id,
                attempt_id=attempt.attempt_id,
                reason="cancel_requested",
                transaction_id=new_transaction_id(),
                request_id=f"rxn_{row.source_event_id}",
            )
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
        row.next_retry_at = None
        self._clear_claim(row)
        self.session.flush()

    def record_failure(self, row: Outbox, exc: Exception) -> str:
        """Persist retry or DEAD after a failed claimed delivery."""

        if row.status == "DEAD":
            return "dead"
        retryable = self._retryable(exc)
        terminal = int(row.attempts) >= self.max_delivery_attempts or not retryable
        error_code = getattr(exc, "code", type(exc).__name__)
        row.last_error = f"{error_code}: {exc}"[:500]
        if terminal:
            row.status = "DEAD"
            row.next_retry_at = None
        else:
            delay = min(
                self.retry_initial_seconds * (2 ** max(0, int(row.attempts) - 1)),
                self.retry_max_seconds,
            )
            row.status = "PENDING"
            row.next_retry_at = self.clock() + timedelta(seconds=delay)
        self._clear_claim(row)
        self.session.flush()
        return "dead" if terminal else "retried"

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, V5WorkKernelError):
            return False
        if isinstance(exc, V5WorkDispatcherError):
            return exc.code not in {
                "v5.work.dispatcher_channel_forbidden",
                "v5.work.dispatcher_payload_mismatch",
                "v5.work.dispatcher_receipt_conflict",
                "v5.work.dispatcher_target_missing",
            }
        return True

    @staticmethod
    def _clear_claim(row: Outbox) -> None:
        row.claimed_by = None
        row.claim_token = None
        row.claimed_at = None
        row.claim_expires_at = None

    def poll_once(self) -> DispatchResult | None:
        """Claim and dispatch one pending Work event, or None when idle."""
        row = self.claim_next()
        if row is None:
            return None
        try:
            return self.dispatch(row)
        except Exception as exc:
            self.record_failure(row, exc)
            raise


class WorkReactionRelay:
    """Commit claims separately, then atomically react and ACK.

    A process crash after claim leaves a leased PROCESSING row.  Another fixed
    worker may reclaim it after the TTL.  The reaction ledger keeps internal
    owner commands idempotent across that redelivery boundary.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] | None = None,
        max_delivery_attempts: int = 8,
        worker_id: str | None = None,
        claim_ttl_seconds: int = 30,
        retry_initial_seconds: int = 2,
        retry_max_seconds: int = 300,
    ) -> None:
        self.session_factory = session_factory
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_delivery_attempts = max_delivery_attempts
        self.worker_id = worker_id or f"v5-work-worker-{short_token(12)}"
        self.claim_ttl_seconds = claim_ttl_seconds
        self.retry_initial_seconds = retry_initial_seconds
        self.retry_max_seconds = retry_max_seconds

    def _dispatcher(self, session: Session) -> WorkReactionDispatcher:
        return WorkReactionDispatcher(
            session,
            clock=self.clock,
            max_delivery_attempts=self.max_delivery_attempts,
            worker_id=self.worker_id,
            claim_ttl_seconds=self.claim_ttl_seconds,
            retry_initial_seconds=self.retry_initial_seconds,
            retry_max_seconds=self.retry_max_seconds,
        )

    def dispatch_batch(self, limit: int = 50) -> dict[str, int]:
        stats = {"claimed": 0, "sent": 0, "retried": 0, "dead": 0, "blocked": 0}
        for _ in range(max(0, limit)):
            snapshot = self._claim_one()
            if snapshot is None:
                break
            stats["claimed"] += 1
            try:
                self._dispatch(snapshot)
                stats["sent"] += 1
            except Exception as exc:  # noqa: BLE001 - persisted below
                try:
                    outcome = self._record_failure(snapshot, exc)
                except Exception:  # noqa: BLE001 - claim remains leased, fail closed
                    logger.exception(
                        "V5 Work failure could not be persisted id=%s",
                        snapshot.outbox_id,
                    )
                    stats["blocked"] += 1
                    continue
                stats[outcome] += 1
        return stats

    def _claim_one(self) -> WorkDispatchSnapshot | None:
        with self.session_factory() as session, session.begin():
            row = self._dispatcher(session).claim_next()
            if row is None:
                return None
            if not row.claim_token:
                raise V5WorkDispatcherError("v5.work.dispatcher_claim_missing")
            return WorkDispatchSnapshot(
                outbox_id=row.outbox_id,
                payload_digest=row.payload_digest,
                attempts=int(row.attempts),
                claim_token=row.claim_token,
            )

    def _dispatch(self, snapshot: WorkDispatchSnapshot) -> None:
        with self.session_factory() as session, session.begin():
            row = self._claimed_row(session, snapshot)
            if row is None:
                return
            self._dispatcher(session).dispatch(row)

    def _record_failure(
        self, snapshot: WorkDispatchSnapshot, exc: Exception
    ) -> str:
        with self.session_factory() as session, session.begin():
            row = self._claimed_row(session, snapshot)
            if row is None:
                return "blocked"
            return self._dispatcher(session).record_failure(row, exc)

    @staticmethod
    def _claimed_row(
        session: Session, snapshot: WorkDispatchSnapshot
    ) -> Outbox | None:
        row = session.scalar(
            select(Outbox)
            .where(Outbox.outbox_id == snapshot.outbox_id)
            .with_for_update()
        )
        if row is None:
            raise V5WorkDispatcherError("v5.work.dispatcher_outbox_missing")
        if row.status == "SENT":
            return None
        if row.status != "PROCESSING" or row.claim_token != snapshot.claim_token:
            raise V5WorkDispatcherError("v5.work.dispatcher_claim_lost")
        if row.payload_digest != snapshot.payload_digest:
            raise V5WorkDispatcherError("v5.work.dispatcher_payload_mismatch")
        return row
