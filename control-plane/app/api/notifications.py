"""Notification REST API。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_db_session
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


class SentIn(BaseModel):
    provider_message_id: str


class FailedIn(BaseModel):
    error: str
    retryable: bool
    attempt: int


class RetryIn(BaseModel):
    attempt: int
    next_at: str


class DeadLetterIn(BaseModel):
    attempts: int


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


@router.post("/v1/notifications/{notification_id}/sent")
def mark_sent(
    notification_id: str,
    body: SentIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).mark_sent(notification_id, body.provider_message_id)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except NotificationServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/notifications/{notification_id}/failed")
def mark_failed(
    notification_id: str,
    body: FailedIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).mark_failed(
            notification_id, error=body.error, retryable=body.retryable, attempt=body.attempt
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except NotificationServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/notifications/{notification_id}/retry")
def schedule_retry(
    notification_id: str,
    body: RetryIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).schedule_retry(notification_id, attempt=body.attempt, next_at=body.next_at)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except NotificationServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/notifications/{notification_id}/dead-letter")
def dead_letter(
    notification_id: str,
    body: DeadLetterIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).dead_letter(notification_id, attempts=body.attempts)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except NotificationServiceError as exc:
        _raise(exc)
    return {}
