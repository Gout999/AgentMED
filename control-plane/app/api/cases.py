"""Case Controller REST API。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_db_session
from app.config import Settings
from app.services.audit import AuditWriteError
from app.services.case_service import CaseService, CaseServiceError
from app.services.outbox_relay import OutboxRelay

router = APIRouter(tags=["cases"])


class ComplaintIn(BaseModel):
    source: str = Field(..., description="webhook | poll")
    text: str
    external_id: Optional[str] = None
    channel: str = "feishu-mock:default:"
    complainant_ref: str = "anon"
    attachments: list[str] = Field(default_factory=list)
    app_ref: str = "demo-app"
    title: Optional[str] = None
    auto_open: bool = True


class ClaimIn(BaseModel):
    worker_id: str


class HeartbeatIn(BaseModel):
    worker_id: str
    fencing_token: int


class TransitionIn(BaseModel):
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    expected_revision: Optional[int] = None
    fencing_token: Optional[int] = None
    actor: str = "system"
    guard: Optional[str] = None


def _raise(exc: CaseServiceError) -> None:
    status = {
        "not_found": 404,
        "validation_failed": 422,
        "pii_redaction_failed": 422,
        "illegal_transition": 422,
        "revision_conflict": 409,
        "lease_conflict": 409,
        "lease_lost": 409,
    }.get(exc.code, 400)
    body: dict[str, Any] = {"error": {"code": exc.code, "message": exc.message, **exc.extra}}
    if exc.code == "illegal_transition" and "current_state" in exc.extra:
        body["error"]["current_state"] = exc.extra["current_state"]
    raise HTTPException(status_code=status, detail=body["error"])


@router.post("/v1/complaints")
def post_complaint(
    body: ComplaintIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    svc = CaseService(session, settings)
    try:
        result = svc.ingest_complaint(
            source=body.source,
            text=body.text,
            external_id=body.external_id,
            channel=body.channel,
            complainant_ref=body.complainant_ref,
            attachments=body.attachments,
            app_ref=body.app_ref,
            title=body.title,
            auto_open=body.auto_open,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except CaseServiceError as exc:
        _raise(exc)
    return result


@router.get("/v1/cases/{case_id}")
def get_case(
    case_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    svc = CaseService(session, settings)
    try:
        return svc.get_case(case_id)
    except CaseServiceError as exc:
        _raise(exc)
    return {}  # pragma: no cover


@router.post("/v1/cases/{case_id}/claim")
def claim_case(
    case_id: str,
    body: ClaimIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    svc = CaseService(session, settings)
    try:
        return svc.claim(case_id, body.worker_id)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except CaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/cases/{case_id}/heartbeat")
def heartbeat_case(
    case_id: str,
    body: HeartbeatIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    svc = CaseService(session, settings)
    try:
        return svc.heartbeat(case_id, body.worker_id, body.fencing_token)
    except CaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/cases/{case_id}/reclaim")
def reclaim_case(
    case_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """lease 过期回收（看门狗/人工触发）。"""
    svc = CaseService(session, settings)
    try:
        result = svc.reclaim_if_expired(case_id)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except CaseServiceError as exc:
        _raise(exc)
    if result is None:
        raise HTTPException(status_code=409, detail={"code": "not_expired", "message": "lease not expired or not dispatched"})
    return result


@router.post("/v1/cases/{case_id}/transitions")
def transition_case(
    case_id: str,
    body: TransitionIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    svc = CaseService(session, settings)
    try:
        return svc.transition(
            case_id,
            body.event_type,
            body.payload,
            expected_revision=body.expected_revision,
            fencing_token=body.fencing_token,
            actor=body.actor,
            guard=body.guard,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except CaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/outbox/relay")
def relay_outbox(
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    n = OutboxRelay(session).drain()
    return {"sent": n}
