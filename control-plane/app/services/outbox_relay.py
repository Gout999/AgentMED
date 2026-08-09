"""Transactional outbox dispatcher with leases, receipts, and retry safety."""
from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.config import Settings, get_settings
from app.models.tables import Outbox, OutboxDeliveryReceipt, ReleaseClosure
from app.notifications.adapters import (
    DisabledNotificationAdapter,
    FeishuLiveAdapter,
    FeishuMockAdapter,
    NotificationAdapter,
    NotificationDeliveryError,
    OFFICIAL_FEISHU_BASE_URL,
)
from app.services.audit import AuditService, AuditWriteError
from app.services.case_closure_service import CaseClosureService, CaseClosureServiceError
from app.services.notification_service import NotificationService, NotificationServiceError
from app.services.trust_service import TrustService, TrustServiceError
from app.utils.ids import new_outbox_receipt_id, short_token
from app.utils.jcs import canonical_json_digest

logger = logging.getLogger("control_plane.outbox")

DOMAIN_EVENT_TYPES = {
    "CASE_CREATED",
    "ATTRIBUTION_DECIDED",
    "GATE_COMPLETED",
    "RELEASE_STARTED",
    "RELEASE_PROMOTED",
    "RELEASE_ROLLED_BACK",
    "RELEASE_UNKNOWN",
    "NOTIFICATION_SENT",
    "CASE_ARCHIVED",
}
TRUST_EVENT_TYPES = {"RELEASE_PROMOTED", "RELEASE_ROLLED_BACK", "RELEASE_UNKNOWN"}


class OutboxDeliveryError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool):
        self.code = code
        self.message = message
        self.retryable = retryable
        super().__init__(message)


@dataclass(frozen=True)
class OutboxSnapshot:
    outbox_id: str
    aggregate_id: str
    source_event_id: str
    channel: str
    event_type: str
    payload: dict[str, Any]
    payload_digest: str
    attempts: int
    claim_token: str
    first_attempted_at: datetime


class DomainEventConsumer:
    """Deterministic in-process consumer for the frozen domain event catalog."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def consume(self, session: Session, row: Outbox) -> dict[str, Any]:
        if row.event_type not in DOMAIN_EVENT_TYPES:
            raise OutboxDeliveryError(
                "unknown_domain_event",
                f"domain event {row.event_type} is not in the frozen catalog",
                retryable=False,
            )
        envelope = row.payload or {}
        if (
            envelope.get("domain_event_type") != row.event_type
            or envelope.get("source_event_id") != row.source_event_id
            or envelope.get("aggregate_id") != row.aggregate_id
            or envelope.get("aggregate_seq") != row.source_event_seq
        ):
            raise OutboxDeliveryError(
                "envelope_mismatch",
                "domain event envelope does not match its outbox identity",
                retryable=False,
            )
        if row.event_type in TRUST_EVENT_TYPES:
            trust = TrustService(session, self.settings).consume_release_event(envelope)
            if row.event_type == "RELEASE_UNKNOWN":
                return trust
            closure = session.scalar(
                select(ReleaseClosure)
                .where(ReleaseClosure.release_id == row.aggregate_id)
                .with_for_update()
            )
            if closure is None:
                if row.event_type == "RELEASE_ROLLED_BACK":
                    return {
                        "status": "consumed",
                        "consumer": "trust-ledger",
                        "trust": trust,
                        "case_closure": "not_configured",
                    }
                raise OutboxDeliveryError(
                    "closure_context_missing",
                    "promoted Release has no durable notification continuation",
                    retryable=False,
                )
            try:
                queued = CaseClosureService(session, self.settings).resolve_and_queue(
                    release_id=row.aggregate_id,
                    channel=closure.channel,
                    thread_ref=closure.thread_ref,
                    body_ref=closure.body_ref,
                    body_digest=closure.body_digest,
                )
            except CaseClosureServiceError as exc:
                raise OutboxDeliveryError(exc.code, exc.message, retryable=False) from exc
            closure.status = "queued"
            closure.notification_id = queued["notification"]["notification_id"]
            closure.queued_at = datetime.now(timezone.utc)
            return {
                "status": "consumed",
                "consumer": "trust-ledger+case-closure",
                "trust": trust,
                "notification_id": closure.notification_id,
                "case_id": closure.case_id,
            }
        return {
            "status": "consumed",
            "consumer": "domain-event-journal",
            "domain_event_type": row.event_type,
            "source_event_id": row.source_event_id,
        }


def notification_adapter_from_settings(settings: Settings) -> NotificationAdapter:
    if settings.notification_adapter == "feishu-mock":
        return FeishuMockAdapter()
    if (
        settings.notification_adapter == "feishu-live"
        and settings.feishu_base_url.rstrip("/") != OFFICIAL_FEISHU_BASE_URL
    ):
        raise ValueError(
            "feishu-live requires the official https://open.feishu.cn provider origin"
        )
    if (
        settings.notification_adapter == "feishu-live"
        and settings.feishu_app_id
        and settings.feishu_app_secret
    ):
        return FeishuLiveAdapter(
            app_id=settings.feishu_app_id,
            app_secret=settings.feishu_app_secret,
            base_url=settings.feishu_base_url,
            timeout_seconds=settings.feishu_timeout_seconds,
        )
    return DisabledNotificationAdapter()


class OutboxDispatcher:
    """Claim, deliver, and acknowledge outbox messages without fake success.

    Claim is committed before delivery. External adapters receive the stable
    outbox id as their idempotency key. ACK, domain side effects, immutable
    receipt, and audit are committed together. A crash after provider success
    therefore retries the same key instead of inventing success.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings | None = None,
        *,
        notification_adapter: NotificationAdapter | None = None,
        domain_consumer: DomainEventConsumer | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.notification_adapter = notification_adapter or notification_adapter_from_settings(
            self.settings
        )
        self.domain_consumer = domain_consumer or DomainEventConsumer(self.settings)
        self.worker_id = worker_id or f"outbox-worker-{short_token(12)}"

    def dispatch_batch(self, limit: int = 50) -> dict[str, int]:
        stats = {"claimed": 0, "sent": 0, "retried": 0, "dead": 0, "blocked": 0}
        for _ in range(max(0, limit)):
            try:
                snapshot = self._claim_one()
            except AuditWriteError:
                stats["blocked"] += 1
                break
            if snapshot is None:
                break
            stats["claimed"] += 1
            try:
                self._dispatch(snapshot)
                stats["sent"] += 1
            except Exception as exc:  # noqa: BLE001 - classified below and persisted
                retryable = self._retryable(exc)
                try:
                    outcome = self._record_failure(snapshot, exc, retryable=retryable)
                except Exception:  # noqa: BLE001 - audit outage leaves the claim leased, fail closed
                    logger.exception("outbox failure could not be audited id=%s", snapshot.outbox_id)
                    stats["blocked"] += 1
                    continue
                stats[outcome] += 1
        return stats

    def _claim_one(self) -> OutboxSnapshot | None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as session, session.begin():
            predecessor = aliased(Outbox)
            unresolved_predecessor = (
                select(predecessor.outbox_id)
                .where(
                    predecessor.aggregate_id == Outbox.aggregate_id,
                    predecessor.source_event_seq < Outbox.source_event_seq,
                    predecessor.event_type != "LEGACY_UNATTRIBUTED",
                    predecessor.status != "SENT",
                )
                .correlate(Outbox)
                .exists()
            )
            statement = (
                select(Outbox)
                .where(
                    or_(
                        and_(
                            Outbox.status == "PENDING",
                            or_(Outbox.next_retry_at.is_(None), Outbox.next_retry_at <= now),
                        ),
                        and_(
                            Outbox.status == "PROCESSING",
                            Outbox.claim_expires_at.is_not(None),
                            Outbox.claim_expires_at <= now,
                        ),
                    ),
                    # Preserve per-aggregate causal order across workers.  A
                    # later event may not overtake an earlier pending,
                    # processing, or dead delivery.  Poisoned predecessors
                    # therefore freeze the aggregate until human repair.
                    ~unresolved_predecessor,
                )
                .order_by(Outbox.created_at.asc(), Outbox.outbox_id.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            row = session.scalar(statement)
            if row is None:
                return None
            claim_token = f"clm_{short_token(24)}"
            AuditService(session, self.settings).record(
                actor=self.worker_id,
                action="outbox.delivery.claimed",
                target=row.outbox_id,
                params={
                    "source_event_id": row.source_event_id,
                    "event_type": row.event_type,
                    "attempt": int(row.attempts) + 1,
                },
                result="pending",
            )
            row.status = "PROCESSING"
            row.attempts = int(row.attempts) + 1
            if row.first_attempted_at is None:
                row.first_attempted_at = now
            row.claimed_by = self.worker_id
            row.claim_token = claim_token
            row.claimed_at = now
            row.claim_expires_at = now + timedelta(seconds=self.settings.outbox_claim_ttl_seconds)
            row.next_retry_at = None
            session.flush()
            return OutboxSnapshot(
                outbox_id=row.outbox_id,
                aggregate_id=row.aggregate_id,
                source_event_id=row.source_event_id,
                channel=row.channel,
                event_type=row.event_type,
                payload=copy.deepcopy(row.payload or {}),
                payload_digest=row.payload_digest,
                attempts=int(row.attempts),
                claim_token=claim_token,
                first_attempted_at=row.first_attempted_at,
            )

    def _dispatch(self, snapshot: OutboxSnapshot) -> None:
        if canonical_json_digest(snapshot.payload) != snapshot.payload_digest:
            raise OutboxDeliveryError(
                "payload_digest_mismatch",
                "outbox payload no longer matches its persisted digest",
                retryable=False,
            )
        if snapshot.channel == "domain.events":
            self._complete_internal(snapshot)
            return
        if snapshot.channel == "notification.delivery":
            window = getattr(
                self.notification_adapter, "idempotency_window_seconds", None
            )
            first_attempted_at = snapshot.first_attempted_at
            if first_attempted_at.tzinfo is None:
                # SQLite discards timezone offsets for DateTime values. Treat
                # the persisted value as UTC so the provider dedup window is
                # still enforced instead of crashing or retrying ambiguously.
                first_attempted_at = first_attempted_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - first_attempted_at).total_seconds()
            if (
                snapshot.attempts > 1
                and isinstance(window, (int, float))
                and age >= float(window)
            ):
                raise OutboxDeliveryError(
                    "provider_idempotency_window_expired",
                    "notification delivery outcome is ambiguous beyond the provider dedup window",
                    retryable=False,
                )
            receipt = self.notification_adapter.deliver(
                outbox_id=snapshot.outbox_id,
                payload=snapshot.payload,
                payload_digest=snapshot.payload_digest,
            )
            self._validate_provider_receipt(snapshot, receipt)
            self._complete_notification(snapshot, receipt)
            return
        raise OutboxDeliveryError(
            "unknown_channel", f"no dispatcher for channel {snapshot.channel}", retryable=False
        )

    def _complete_internal(self, snapshot: OutboxSnapshot) -> None:
        with self.session_factory() as session, session.begin():
            row = self._claimed_row(session, snapshot)
            if row is None:
                return
            try:
                receipt = self.domain_consumer.consume(session, row)
            except TrustServiceError as exc:
                raise OutboxDeliveryError(exc.code, exc.message, retryable=False) from exc
            self._ack(session, row, snapshot, receipt)

    def _complete_notification(
        self, snapshot: OutboxSnapshot, provider_receipt: dict[str, Any]
    ) -> None:
        with self.session_factory() as session, session.begin():
            row = self._claimed_row(session, snapshot)
            if row is None:
                return
            NotificationService(session, self.settings).acknowledge_delivery(
                outbox=row,
                receipt=provider_receipt,
            )
            self._ack(session, row, snapshot, provider_receipt)

    def _ack(
        self,
        session: Session,
        row: Outbox,
        snapshot: OutboxSnapshot,
        receipt: dict[str, Any],
    ) -> None:
        normalized = {
            **receipt,
            "outbox_id": snapshot.outbox_id,
            "source_event_id": snapshot.source_event_id,
            "payload_digest": snapshot.payload_digest,
        }
        status = normalized.get("status")
        if status not in ("sent", "consumed"):
            raise OutboxDeliveryError(
                "invalid_receipt", "dispatcher receipt is not terminal success", retryable=False
            )
        AuditService(session, self.settings).record(
            actor=self.worker_id,
            action="outbox.delivery.sent",
            target=row.outbox_id,
            params={
                "source_event_id": row.source_event_id,
                "event_type": row.event_type,
                "payload_digest": row.payload_digest,
                "attempts": row.attempts,
            },
            result="success",
            evidence_refs={"receipt_digest": canonical_json_digest(normalized)},
        )
        existing = session.scalar(
            select(OutboxDeliveryReceipt).where(OutboxDeliveryReceipt.outbox_id == row.outbox_id)
        )
        if existing is not None:
            if existing.payload_digest != row.payload_digest or existing.receipt != normalized:
                raise OutboxDeliveryError(
                    "receipt_conflict", "outbox already has a different receipt", retryable=False
                )
        else:
            session.add(
                OutboxDeliveryReceipt(
                    receipt_id=new_outbox_receipt_id(),
                    outbox_id=row.outbox_id,
                    source_event_id=row.source_event_id,
                    channel=row.channel,
                    payload_digest=row.payload_digest,
                    receipt=normalized,
                    delivered_at=datetime.now(timezone.utc),
                )
            )
        row.status = "SENT"
        row.sent_at = datetime.now(timezone.utc)
        row.last_error = None
        row.receipt = normalized
        self._clear_claim(row)
        session.flush()

    def _record_failure(
        self,
        snapshot: OutboxSnapshot,
        exc: Exception,
        *,
        retryable: bool,
    ) -> str:
        now = datetime.now(timezone.utc)
        exhausted = snapshot.attempts >= self.settings.outbox_max_attempts
        terminal = exhausted or not retryable
        delay = min(
            self.settings.outbox_retry_initial_seconds * (2 ** max(0, snapshot.attempts - 1)),
            self.settings.outbox_retry_max_seconds,
        )
        next_retry = now + timedelta(seconds=delay)
        error_code = getattr(exc, "code", type(exc).__name__)
        error = f"{error_code}: {exc}"[:500]
        with self.session_factory() as session, session.begin():
            row = self._claimed_row(session, snapshot)
            if row is None:
                return "blocked"
            AuditService(session, self.settings).record(
                actor=self.worker_id,
                action="outbox.delivery.dead" if terminal else "outbox.delivery.retry_scheduled",
                target=row.outbox_id,
                params={
                    "source_event_id": row.source_event_id,
                    "event_type": row.event_type,
                    "attempts": row.attempts,
                    "retryable": retryable,
                    "next_retry_at": None if terminal else next_retry.isoformat(),
                },
                result="denied" if terminal else "retry",
                error_code=error_code[:64],
            )
            if row.channel == "notification.delivery":
                NotificationService(session, self.settings).record_delivery_failure(
                    outbox=row,
                    error=error,
                    retryable=retryable,
                    exhausted=exhausted,
                    next_at=None if terminal else next_retry.isoformat(),
                )
            row.last_error = error
            row.status = "DEAD" if terminal else "PENDING"
            row.next_retry_at = None if terminal else next_retry
            self._clear_claim(row)
            session.flush()
        return "dead" if terminal else "retried"

    @staticmethod
    def _claimed_row(
        session: Session, snapshot: OutboxSnapshot
    ) -> Outbox | None:
        row = session.scalar(
            select(Outbox).where(Outbox.outbox_id == snapshot.outbox_id).with_for_update()
        )
        if row is None:
            raise OutboxDeliveryError(
                "outbox_missing", f"outbox {snapshot.outbox_id} disappeared", retryable=False
            )
        if row.status == "SENT":
            return None
        if row.status != "PROCESSING" or row.claim_token != snapshot.claim_token:
            raise OutboxDeliveryError(
                "claim_lost", f"outbox claim {snapshot.outbox_id} is no longer owned", retryable=True
            )
        if row.payload_digest != snapshot.payload_digest:
            raise OutboxDeliveryError(
                "payload_digest_mismatch", "outbox digest changed after claim", retryable=False
            )
        return row

    @staticmethod
    def _clear_claim(row: Outbox) -> None:
        row.claimed_by = None
        row.claim_token = None
        row.claimed_at = None
        row.claim_expires_at = None

    @staticmethod
    def _validate_provider_receipt(
        snapshot: OutboxSnapshot, receipt: dict[str, Any]
    ) -> None:
        if not isinstance(receipt, dict):
            raise OutboxDeliveryError(
                "invalid_receipt", "notification adapter returned no receipt", retryable=False
            )
        if (
            receipt.get("status") != "sent"
            or receipt.get("outbox_id") != snapshot.outbox_id
            or receipt.get("payload_digest") != snapshot.payload_digest
            or not isinstance(receipt.get("provider"), str)
            or not receipt.get("provider")
            or not isinstance(receipt.get("provider_message_id"), str)
            or not receipt.get("provider_message_id")
            or (
                receipt.get("provider") == "feishu"
                and receipt.get("provider_origin") != OFFICIAL_FEISHU_BASE_URL
            )
            or receipt.get("thread_ref") != snapshot.payload.get("thread_ref")
            or receipt.get("body_digest") != snapshot.payload.get("body_digest")
        ):
            raise OutboxDeliveryError(
                "invalid_receipt",
                "notification receipt is not bound to the exact outbox payload",
                retryable=False,
            )
        sent_at = receipt.get("sent_at")
        if not isinstance(sent_at, str):
            raise OutboxDeliveryError(
                "invalid_receipt", "notification receipt has no sent_at timestamp", retryable=False
            )
        try:
            parsed = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OutboxDeliveryError(
                "invalid_receipt", "notification receipt sent_at is invalid", retryable=False
            ) from exc
        if parsed.tzinfo is None:
            raise OutboxDeliveryError(
                "invalid_receipt", "notification receipt sent_at must be timezone-aware", retryable=False
            )

    @staticmethod
    def _retryable(exc: Exception) -> bool:
        if isinstance(exc, (NotificationDeliveryError, OutboxDeliveryError)):
            return exc.retryable
        if isinstance(exc, NotificationServiceError):
            return False
        if isinstance(exc, AuditWriteError):
            return True
        return True


class OutboxRelay:
    """Compatibility wrapper requiring an explicit session factory.

    The prior session-bound logging relay was removed because it could mark
    messages SENT without a receipt. New code should use ``OutboxDispatcher``.
    """

    def __init__(self, session_factory: sessionmaker[Session], settings: Settings | None = None):
        self.dispatcher = OutboxDispatcher(session_factory, settings)

    def drain(self, limit: int = 50) -> dict[str, int]:
        return self.dispatcher.dispatch_batch(limit)
