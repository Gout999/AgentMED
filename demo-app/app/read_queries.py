"""读面查询：GET /v2/logs 与 GET /v2/feedback。

cursor 分页（keyset：按 ts desc, id desc）+ 时间窗 + versionset_id/rating 过滤。
cursor = base64url("ts_iso|row_id")；无更多页时 next_cursor 缺省。
"""
from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChatLog, Feedback


def _encode_cursor(ts: datetime, row_id: int) -> str:
    return base64.urlsafe_b64encode(f"{ts.isoformat()}|{row_id}".encode()).decode()


def _decode_cursor(cursor: str) -> Optional[tuple[datetime, int]]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        ts_s, id_s = raw.split("|", 1)
        return datetime.fromisoformat(ts_s), int(id_s)
    except Exception:  # noqa: BLE001
        return None


def _apply_keyset(q, model, ts_col, cursor: Optional[str]):
    decoded = _decode_cursor(cursor) if cursor else None
    if decoded:
        ts, rid = decoded
        q = q.where((ts_col < ts) | ((ts_col == ts) & (model.id < rid)))
    return q


def _page(db: Session, query, model, ts_col, limit: int):
    rows = db.execute(query.limit(limit + 1)).scalars().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(last.ts, last.id)
    return page, next_cursor


def query_logs(
    db: Session,
    *,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    versionset_id: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
) -> tuple[list[ChatLog], Optional[str]]:
    q = select(ChatLog).order_by(ChatLog.ts.desc(), ChatLog.id.desc())
    if from_dt is not None:
        q = q.where(ChatLog.ts >= from_dt)
    if to_dt is not None:
        q = q.where(ChatLog.ts <= to_dt)
    if versionset_id:
        q = q.where(ChatLog.versionset_id == versionset_id)
    q = _apply_keyset(q, ChatLog, ChatLog.ts, cursor)
    return _page(db, q, ChatLog, ChatLog.ts, limit)


def query_feedback(
    db: Session,
    *,
    from_dt: Optional[datetime] = None,
    to_dt: Optional[datetime] = None,
    versionset_id: Optional[str] = None,
    rating: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
) -> tuple[list[Feedback], Optional[str]]:
    q = select(Feedback).order_by(Feedback.ts.desc(), Feedback.id.desc())
    if from_dt is not None:
        q = q.where(Feedback.ts >= from_dt)
    if to_dt is not None:
        q = q.where(Feedback.ts <= to_dt)
    if versionset_id:
        q = q.where(Feedback.versionset_id == versionset_id)
    if rating:
        q = q.where(Feedback.rating == rating)
    q = _apply_keyset(q, Feedback, Feedback.ts, cursor)
    return _page(db, q, Feedback, Feedback.ts, limit)


def log_entry_dict(log: ChatLog) -> dict[str, Any]:
    return {
        "ts": log.ts.isoformat(),
        "request_id": log.request_id,
        "versionset_id": log.versionset_id,
        "prompt_digest": log.prompt_digest,
        "kb_manifest_digest": log.kb_manifest_digest,
        "model_digest": log.model_digest,
        "status": log.status,
        "latency_ms": log.latency_ms,
        "usage": log.usage or {},
        "trace_id": log.trace_id,
    }


def feedback_entry_dict(fb: Feedback) -> dict[str, Any]:
    return {
        "feedback_id": fb.feedback_id,
        "ts": fb.ts.isoformat(),
        "request_id": fb.request_id,
        "versionset_id": fb.versionset_id,
        "rating": fb.rating,
        "comment": fb.comment,
        "user_ref": fb.user_ref,
        "source": fb.source,
    }
