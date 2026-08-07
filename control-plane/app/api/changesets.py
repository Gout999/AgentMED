"""ChangeSet（修复变更集）REST API。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_db_session
from app.config import Settings
from app.services.audit import AuditWriteError
from app.services.changeset_service import ChangeSetService, ChangeSetServiceError

router = APIRouter(tags=["changesets"])


class ChangeSetCreateIn(BaseModel):
    case_id: str
    workorder_ref: str
    workorder_hash: str
    channel: str
    author_agent: str
    changeset_id: Optional[str] = None


class GateAttachIn(BaseModel):
    gate_report_ref: str
    gate_status: str = "passed"


class ApprovalRequestIn(BaseModel):
    workorder_hash: str
    nonce: str
    expiry: str
    channel: str = "feishu"


class ApproveIn(BaseModel):
    approval_id: str
    approver: str
    workorder_hash: str


class RejectIn(BaseModel):
    approval_id: str
    approver: str
    reason: str


class ExpireIn(BaseModel):
    workorder_hash: str
    expiry: str


class CommitIn(BaseModel):
    release_id: str


class SupersedeIn(BaseModel):
    replaced_by: str


def _raise(exc: ChangeSetServiceError) -> None:
    status = {
        "not_found": 404,
        "validation_failed": 422,
        "illegal_transition": 422,
        "revision_conflict": 409,
    }.get(exc.code, 400)
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message, **exc.extra})


def _svc(session: Session, settings: Settings) -> ChangeSetService:
    return ChangeSetService(session, settings)


@router.post("/v1/changesets")
def create_changeset(
    body: ChangeSetCreateIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).create(
            case_id=body.case_id,
            workorder_ref=body.workorder_ref,
            workorder_hash=body.workorder_hash,
            channel=body.channel,
            author_agent=body.author_agent,
            changeset_id=body.changeset_id,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ChangeSetServiceError as exc:
        _raise(exc)
    return {}


@router.get("/v1/changesets")
def list_changesets(
    state: Optional[str] = None,
    limit: int = 100,
    cursor: int = 0,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    return _svc(session, settings).list_changesets(state=state, limit=min(limit, 500), cursor=cursor)


@router.get("/v1/changesets/{changeset_id}")
def get_changeset(
    changeset_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).get(changeset_id)
    except ChangeSetServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/changesets/{changeset_id}/gate")
def attach_gate(
    changeset_id: str,
    body: GateAttachIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).attach_gate(changeset_id, gate_report_ref=body.gate_report_ref, gate_status=body.gate_status)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ChangeSetServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/changesets/{changeset_id}/approval-request")
def request_approval(
    changeset_id: str,
    body: ApprovalRequestIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).request_approval(
            changeset_id,
            workorder_hash=body.workorder_hash,
            nonce=body.nonce,
            expiry=body.expiry,
            channel=body.channel,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ChangeSetServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/changesets/{changeset_id}/approve")
def approve_changeset(
    changeset_id: str,
    body: ApproveIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).approve(
            changeset_id, approval_id=body.approval_id, approver=body.approver, workorder_hash=body.workorder_hash
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ChangeSetServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/changesets/{changeset_id}/reject")
def reject_changeset(
    changeset_id: str,
    body: RejectIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).reject(
            changeset_id, approval_id=body.approval_id, approver=body.approver, reason=body.reason
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ChangeSetServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/changesets/{changeset_id}/expire")
def expire_changeset(
    changeset_id: str,
    body: ExpireIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).expire(changeset_id, workorder_hash=body.workorder_hash, expiry=body.expiry)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ChangeSetServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/changesets/{changeset_id}/commit")
def commit_changeset(
    changeset_id: str,
    body: CommitIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).commit(changeset_id, release_id=body.release_id)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ChangeSetServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/changesets/{changeset_id}/supersede")
def supersede_changeset(
    changeset_id: str,
    body: SupersedeIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).supersede(changeset_id, replaced_by=body.replaced_by)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ChangeSetServiceError as exc:
        _raise(exc)
    return {}
