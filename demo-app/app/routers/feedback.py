"""POST /feedback —— 用户对回答点踩/吐槽。

- comment 入口 PII 脱敏（FeedbackEntry.comment 必须已脱敏，铁律）。
- versionset_id 从 chat_logs 按 request_id 解析（保证反馈可绑定到具体版本）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.ids import new_feedback_id, new_trace_id
from app.models import ChatLog, Feedback
from app.pii import PIIRedactionError, redact_text
from app.read_queries import feedback_entry_dict
from app.schemas import FeedbackPost

router = APIRouter(tags=["feedback"])


def _err(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=404 if code == "not_found" else 422,
        detail={"error": {"code": code, "message": message, "trace_id": new_trace_id()}},
    )


@router.post("/feedback", status_code=201)
def post_feedback(payload: FeedbackPost, db: Session = Depends(get_db)):
    # 解析 versionset_id（绑定到具体哪次回答）
    log = db.execute(
        select(ChatLog).where(ChatLog.request_id == payload.request_id)
    ).scalars().first()
    if log is None:
        # request_id 不存在 → 无法绑定版本，拒收
        raise _err("not_found", f"request_id {payload.request_id} not found in chat logs")

    # PII 入口脱敏
    comment = payload.comment
    try:
        if comment:
            comment = redact_text(comment).text
    except PIIRedactionError as exc:
        raise _err("validation_failed", f"comment 脱敏失败: {exc}")

    fb = Feedback(
        feedback_id=new_feedback_id(),
        request_id=payload.request_id,
        versionset_id=log.versionset_id,
        rating=payload.rating,
        comment=comment,
        user_ref=payload.user_ref,
        source=payload.source,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return feedback_entry_dict(fb)
