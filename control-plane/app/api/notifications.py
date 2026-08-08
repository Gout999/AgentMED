"""Notification REST API。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_db_session, require_internal_write
from app.config import Settings
from app.services.audit import AuditWriteError
from app.services.notification_service import NotificationService, NotificationServiceError

router = APIRouter(tags=["notifications"])


class NotificationQueueIn(BaseModel):
    case_id: str
    channel: str
    thread_ref: str
    body_ref: str
    notification_id: Optional[str] = None


def _raise(exc: NotificationServiceError) -> None:
    status = {
        "not_found": 404,
        "validation_failed": 422,
        "illegal_transition": 422,
        "revision_conflict": 409,
    }.get(exc.code, 400)
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message, **exc.extra})


def _svc(session: Session, settings: Settings) -> NotificationService:
    return NotificationService(session, settings)


@router.post("/v1/notifications")
def queue_notification(
    body: NotificationQueueIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    _authority: str = Depends(require_internal_write),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).queue(
            case_id=body.case_id,
            channel=body.channel,
            thread_ref=body.thread_ref,
            body_ref=body.body_ref,
            notification_id=body.notification_id,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except NotificationServiceError as exc:
        _raise(exc)
    return {}


@router.get("/v1/notifications")
def list_notifications(
    limit: int = 100,
    cursor: int = 0,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    return _svc(session, settings).list_notifications(limit=min(limit, 500), cursor=cursor)


@router.get("/v1/notifications/{notification_id}")
def get_notification(
    notification_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).get(notification_id)
    except NotificationServiceError as exc:
        _raise(exc)
    return {}
