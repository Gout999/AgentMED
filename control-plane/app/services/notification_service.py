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
from app.utils.ids import new_notification_id, new_outbox_id, new_trace_id


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
                "channel": channel,
                "payload": {"notification_id": nid, "case_id": case_id, "thread_ref": thread_ref, "body_ref": body_ref},
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

    def mark_sent(self, notification_id: str, provider_message_id: str) -> dict[str, Any]:
        agg = self.store.get_aggregate("notification", notification_id)
        if agg is None:
            raise NotificationServiceError("not_found", f"notification {notification_id} not found")
        self.store.append_event(
            aggregate_type="notification",
            aggregate_id=notification_id,
            event_type="notification.sent",
            payload={"provider_message_id": provider_message_id},
            causation_id="channel_ack",
            correlation_id=(agg.payload or {}).get("case_id") or notification_id,
            actor="controller:notification",
            expected_revision=agg.revision,
            machine="notification",
            merge_payload={"provider_message_id": provider_message_id},
        )
        self._record(notification_id, "notification.sent", params={"provider_message_id": provider_message_id})
        agg = self.store.get_aggregate("notification", notification_id)
        return self._view(agg)

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
