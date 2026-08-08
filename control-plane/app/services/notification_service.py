"""Notification 状态机服务（contracts/events/state-machines.yaml#notification）。

对外通知走 outbox 模式：notification.queued 与 outbox 行同事务写入；
失败可重试（指数退避）→ RETRYING；不可重试 / 重试耗尽 → DEAD_LETTERED（必人工）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Aggregate, Outbox
from app.services.audit import AuditService, AuditWriteError
from app.services.event_store import CASConflict, EventStore
from app.services.state_machines import IllegalTransition
from app.utils.ids import new_notification_id, new_outbox_id
from app.utils.jcs import canonical_json_digest


class NotificationServiceError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


class NotificationService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.store = EventStore(session)
        self.audit = AuditService(session, self.settings)

    def queue(
        self,
        *,
        case_id: str,
        channel: str,
        thread_ref: str,
        body_ref: str,
        notification_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """通知入 outbox：notification.queued 与 outbox 行同事务（失败即拒业务）。"""
        case = self.store.get_aggregate("case", case_id)
        if case is None:
            raise NotificationServiceError("not_found", f"case {case_id} not found")
        if case.state != "NOTIFYING":
            raise NotificationServiceError(
                "illegal_transition",
                f"notification may only be queued for a NOTIFYING case, got {case.state}",
                current_state=case.state,
            )
        nid = notification_id or new_notification_id()
        outbox_id = new_outbox_id()
        self.store.append_event(
            aggregate_type="notification",
            aggregate_id=nid,
            event_type="notification.queued",
            payload={
                "case_id": case_id,
                "channel": channel,
                "thread_ref": thread_ref,
                "body_ref": body_ref,
                "outbox_id": outbox_id,
            },
            causation_id="case.resolved",
            correlation_id=case_id,
            actor="controller:notification",
            machine="notification",
            merge_payload={"case_id": case_id, "channel": channel, "thread_ref": thread_ref, "outbox_id": outbox_id},
            outbox={
                "outbox_id": outbox_id,
                "channel": "notification.delivery",
                "event_type": "NOTIFICATION_DELIVERY_REQUESTED",
                "payload": {
                    "notification_id": nid,
                    "case_id": case_id,
                    "channel": channel,
                    "thread_ref": thread_ref,
                    "body_ref": body_ref,
                },
            },
        )
        self.audit.record(
            actor="controller:notification",
            action="notification.queued",
            target=nid,
            params={"case_id": case_id, "channel": channel},
            result="success",
        )
        agg = self.store.get_aggregate("notification", nid)
        return {
            "notification_id": nid,
            "outbox_id": outbox_id,
            "state": agg.state if agg else "QUEUED",
            "revision": agg.revision if agg else 1,
        }

    def acknowledge_delivery(
        self,
        *,
        outbox: Outbox,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply a validated provider ACK and archive its Case atomically."""

        notification_id = str((outbox.payload or {}).get("notification_id") or "")
        agg = self.store.get_aggregate("notification", notification_id)
        if agg is None:
            raise NotificationServiceError("not_found", f"notification {notification_id} not found")
        if outbox.aggregate_id != notification_id or outbox.event_type != "NOTIFICATION_DELIVERY_REQUESTED":
            raise NotificationServiceError("receipt_mismatch", "outbox is not this notification command")
        if (agg.payload or {}).get("outbox_id") != outbox.outbox_id:
            raise NotificationServiceError("receipt_mismatch", "notification is bound to another outbox row")
        receipt_digest = canonical_json_digest(receipt)
        if agg.state == "SENT":
            if (
                (agg.payload or {}).get("receipt_digest") == receipt_digest
                and (agg.payload or {}).get("outbox_id") == outbox.outbox_id
            ):
                return self._view(agg)
            raise NotificationServiceError("idempotency_conflict", "notification already has another receipt")
        if agg.state not in ("QUEUED", "SENDING"):
            raise NotificationServiceError(
                "illegal_transition", f"cannot acknowledge notification from {agg.state}"
            )
        case_id = str((agg.payload or {}).get("case_id") or "")
        case = self.store.get_aggregate("case", case_id)
        if case is None:
            raise NotificationServiceError("not_found", f"case {case_id} not found")
        if case.state != "NOTIFYING":
            raise NotificationServiceError(
                "illegal_transition",
                f"receipt cannot archive case from {case.state}",
                current_state=case.state,
            )
        provider_message_id = receipt.get("provider_message_id")
        if not isinstance(provider_message_id, str) or not provider_message_id:
            raise NotificationServiceError("receipt_mismatch", "provider receipt has no message id")

        self.audit.record(
            actor="controller:notification",
            action="notification.receipt.accepted",
            target=notification_id,
            params={
                "outbox_id": outbox.outbox_id,
                "payload_digest": outbox.payload_digest,
                "receipt_digest": receipt_digest,
            },
            result="success",
            evidence_refs={"provider_message_id": provider_message_id},
        )
        self.audit.record(
            actor="controller:case",
            action="case.archive.intent",
            target=case_id,
            params={"notification_id": notification_id, "receipt_digest": receipt_digest},
            result="pending",
        )
        sent_event = self.store.append_event(
            aggregate_type="notification",
            aggregate_id=notification_id,
            event_type="notification.sent",
            payload={
                "provider_message_id": provider_message_id,
                "provider": receipt.get("provider"),
                "outbox_id": outbox.outbox_id,
                "payload_digest": outbox.payload_digest,
                "receipt_digest": receipt_digest,
            },
            # The provider ACK is the direct cause of delivery success.  Use
            # the canonical receipt digest so a replay cannot substitute a
            # different ACK while preserving the notification command id.
            causation_id=receipt_digest,
            correlation_id=case_id,
            actor="controller:notification",
            expected_revision=agg.revision,
            machine="notification",
            merge_payload={
                "provider_message_id": provider_message_id,
                "provider": receipt.get("provider"),
                "receipt": receipt,
                "receipt_digest": receipt_digest,
            },
        )
        self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="case.closed",
            payload={
                "resolution": "fixed",
                "notification_id": notification_id,
                "notification_receipt_digest": receipt_digest,
            },
            # Archival is only legal after the exact notification.sent event,
            # not merely after the older notification.queued command.
            causation_id=sent_event.event_id,
            correlation_id=case_id,
            actor="controller:case",
            expected_revision=case.revision,
            machine="case",
            merge_payload={
                "resolution": "fixed",
                "notification_id": notification_id,
                "notification_receipt_digest": receipt_digest,
                "archived_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self._record(
            notification_id,
            "notification.sent",
            params={"provider_message_id": provider_message_id, "outbox_id": outbox.outbox_id},
        )
        self.audit.record(
            actor="controller:case",
            action="case.archived",
            target=case_id,
            params={"notification_id": notification_id, "receipt_digest": receipt_digest},
            result="success",
        )
        agg = self.store.get_aggregate("notification", notification_id)
        return self._view(agg)

    def record_delivery_failure(
        self,
        *,
        outbox: Outbox,
        error: str,
        retryable: bool,
        exhausted: bool,
        next_at: Optional[str] = None,
    ) -> None:
        notification_id = str((outbox.payload or {}).get("notification_id") or "")
        agg = self.store.get_aggregate("notification", notification_id)
        if agg is None:
            raise NotificationServiceError("not_found", f"notification {notification_id} not found")
        if agg.state in ("SENT", "DEAD_LETTERED"):
            return
        if agg.state != "QUEUED":
            raise NotificationServiceError(
                "illegal_transition", f"cannot record dispatcher failure from {agg.state}"
            )
        terminal = exhausted or not retryable
        self.audit.record(
            actor="controller:notification",
            action="notification.delivery_failed",
            target=notification_id,
            params={
                "outbox_id": outbox.outbox_id,
                "attempt": outbox.attempts,
                "retryable": retryable,
                "exhausted": exhausted,
            },
            result="denied" if terminal else "retry",
            error_code=error[:64],
        )
        self.store.append_event(
            aggregate_type="notification",
            aggregate_id=notification_id,
            event_type="notification.failed",
            payload={"error": error, "retryable": not terminal, "attempt": outbox.attempts},
            causation_id=outbox.source_event_id,
            correlation_id=str((agg.payload or {}).get("case_id") or notification_id),
            actor="controller:notification",
            expected_revision=agg.revision,
            machine="notification",
            guard="retryable=false" if terminal else "retryable=true",
            merge_payload={"last_error": error, "attempt": outbox.attempts},
        )
        if not terminal:
            retrying = self.store.get_aggregate("notification", notification_id)
            assert retrying is not None
            self.store.append_event(
                aggregate_type="notification",
                aggregate_id=notification_id,
                event_type="notification.retry_scheduled",
                payload={"attempt": outbox.attempts + 1, "next_at": next_at},
                causation_id=outbox.source_event_id,
                correlation_id=str((agg.payload or {}).get("case_id") or notification_id),
                actor="controller:notification",
                expected_revision=retrying.revision,
                machine="notification",
                merge_payload={"next_at": next_at},
            )
            return

        case_id = str((agg.payload or {}).get("case_id") or "")
        case = self.store.get_aggregate("case", case_id)
        if case is None or case.state != "NOTIFYING":
            raise NotificationServiceError(
                "illegal_transition",
                "dead-lettered notification cannot be detached from a NOTIFYING case",
            )
        self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="notification.dead_lettered",
            payload={"notification_id": notification_id, "attempts": outbox.attempts, "error": error},
            causation_id=outbox.source_event_id,
            correlation_id=case_id,
            actor="controller:case",
            expected_revision=case.revision,
            machine="case",
            merge_payload={"notification_dead_lettered": True, "notification_id": notification_id},
        )

    def mark_sent(self, notification_id: str, provider_message_id: str) -> dict[str, Any]:
        """Legacy direct ACK is intentionally disabled on production paths."""

        raise NotificationServiceError(
            "receipt_required",
            "notification delivery must be acknowledged through its outbox-bound provider receipt",
        )

    def mark_failed(
        self,
        notification_id: str,
        *,
        error: str,
        retryable: bool,
        attempt: int,
    ) -> dict[str, Any]:
        agg = self.store.get_aggregate("notification", notification_id)
        if agg is None:
            raise NotificationServiceError("not_found", f"notification {notification_id} not found")
        guard = "retryable=true" if retryable else "retryable=false"
        self.store.append_event(
            aggregate_type="notification",
            aggregate_id=notification_id,
            event_type="notification.failed",
            payload={"error": error, "retryable": retryable, "attempt": attempt},
            causation_id="channel_nack",
            correlation_id=(agg.payload or {}).get("case_id") or notification_id,
            actor="controller:notification",
            expected_revision=agg.revision,
            machine="notification",
            guard=guard,
            merge_payload={"last_error": error, "attempt": attempt, "dead_lettered": not retryable},
        )
        self._record(notification_id, "notification.failed", params={"retryable": retryable, "attempt": attempt})
        agg = self.store.get_aggregate("notification", notification_id)
        return self._view(agg)

    def schedule_retry(self, notification_id: str, *, attempt: int, next_at: str) -> dict[str, Any]:
        agg = self.store.get_aggregate("notification", notification_id)
        if agg is None:
            raise NotificationServiceError("not_found", f"notification {notification_id} not found")
        self.store.append_event(
            aggregate_type="notification",
            aggregate_id=notification_id,
            event_type="notification.retry_scheduled",
            payload={"attempt": attempt, "next_at": next_at},
            causation_id="retry_timer",
            correlation_id=(agg.payload or {}).get("case_id") or notification_id,
            actor="controller:notification",
            expected_revision=agg.revision,
            machine="notification",
            merge_payload={"next_at": next_at, "attempt": attempt},
        )
        self._record(notification_id, "notification.retry_scheduled", params={"attempt": attempt, "next_at": next_at})
        agg = self.store.get_aggregate("notification", notification_id)
        return self._view(agg)

    def dead_letter(self, notification_id: str, *, attempts: int) -> dict[str, Any]:
        agg = self.store.get_aggregate("notification", notification_id)
        if agg is None:
            raise NotificationServiceError("not_found", f"notification {notification_id} not found")
        self.store.append_event(
            aggregate_type="notification",
            aggregate_id=notification_id,
            event_type="notification.dead_lettered",
            payload={"attempts": attempts, "escalated": True},
            causation_id="retry_exhausted",
            correlation_id=(agg.payload or {}).get("case_id") or notification_id,
            actor="controller:notification",
            expected_revision=agg.revision,
            machine="notification",
            merge_payload={"dead_lettered": True, "attempts": attempts},
        )
        self.audit.record(
            actor="controller:notification",
            action="notification.dead_lettered",
            target=notification_id,
            params={"attempts": attempts, "escalated": True},
            result="success",
        )
        agg = self.store.get_aggregate("notification", notification_id)
        return self._view(agg)

    def get(self, notification_id: str) -> dict[str, Any]:
        agg = self.store.get_aggregate("notification", notification_id)
        if agg is None:
            raise NotificationServiceError("not_found", f"notification {notification_id} not found")
        return self._view(agg)

    def list_notifications(self, *, limit: int = 100, cursor: int = 0) -> dict[str, Any]:
        q = (
            select(Aggregate)
            .where(Aggregate.aggregate_type == "notification")
            .order_by(Aggregate.aggregate_id)
        )
        rows = list(self.session.scalars(q.offset(cursor).limit(limit)).all())
        return {
            "items": [self._view(r) for r in rows],
            "next_cursor": cursor + len(rows) if len(rows) == limit else None,
        }

    # ---------- helpers ----------

    def _view(self, agg: Aggregate) -> dict[str, Any]:
        return {
            "notification_id": agg.aggregate_id,
            "state": agg.state,
            "revision": agg.revision,
            "payload": agg.payload,
        }

    def _record(self, notification_id: str, action: str, params: dict[str, Any]) -> None:
        self.audit.record(
            actor="controller:notification",
            action=action,
            target=notification_id,
            params=params,
            result="success",
        )
