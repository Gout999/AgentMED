"""Outbox relay：异步投递（MVP sink = logging）。"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import Outbox

logger = logging.getLogger("control_plane.outbox")


class LoggingSink:
    """默认投递目标：结构化日志。"""

    def deliver(self, item: Outbox) -> None:
        logger.info(
            "outbox.deliver id=%s channel=%s aggregate=%s payload_keys=%s",
            item.outbox_id,
            item.channel,
            item.aggregate_id,
            list((item.payload or {}).keys()),
        )


class OutboxRelay:
    def __init__(self, session: Session, sink: LoggingSink | None = None):
        self.session = session
        self.sink = sink or LoggingSink()

    def drain(self, limit: int = 50) -> int:
        """投递 PENDING 消息；成功标记 SENT。返回投递条数。"""
        now = datetime.now(timezone.utc)
        rows = list(
            self.session.scalars(
                select(Outbox)
                .where(Outbox.status == "PENDING")
                .order_by(Outbox.created_at.asc())
                .limit(limit)
            ).all()
        )
        sent = 0
        for item in rows:
            item.status = "SENDING"
            item.attempts = int(item.attempts) + 1
            self.session.flush()
            try:
                self.sink.deliver(item)
                item.status = "SENT"
                item.sent_at = now
                item.last_error = None
                sent += 1
            except Exception as exc:  # noqa: BLE001
                item.status = "PENDING"
                item.last_error = str(exc)[:500]
                logger.exception("outbox deliver failed id=%s", item.outbox_id)
        self.session.flush()
        return sent
