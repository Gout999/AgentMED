"""Release Controller REST API。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_db_session, get_quality_client
from app.config import Settings
from app.quality.client import QualityClientProtocol
from app.services.audit import AuditWriteError
from app.services.release_service import ReleaseService, ReleaseServiceError

router = APIRouter(tags=["releases"])


class WorkOrderIn(BaseModel):
    # 接受完整 WorkOrder JSON
    model_config = {"extra": "allow"}


class ApprovalIn(BaseModel):
    model_config = {"extra": "allow"}


class StartReleaseIn(BaseModel):
    workorder_id: str
    approval_id: str
    versionset_id: str
    release_id: Optional[str] = None


class StepIn(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    percent: Optional[int] = None
    reason: str = "manual"


def _raise(exc: ReleaseServiceError) -> None:
    status = {
        "not_found": 404,
        "validation_failed": 422,
        "hash_mismatch": 422,
        "nonce_replay": 409,
        "approval_expired": 422,
        "illegal_transition": 422,
        "revision_conflict": 409,
        "quality_api_error": 502,
    }.get(exc.code, 400)
    raise HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": exc.message, **exc.extra},
    )


def _svc(
    session: Session,
    quality: QualityClientProtocol,
    settings: Settings,
) -> ReleaseService:
    return ReleaseService(session, quality, settings)


@router.post("/v1/workorders")
def register_workorder(
    body: dict[str, Any],
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).register_workorder(body)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/approvals")
def grant_approval(
    body: dict[str, Any],
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).grant_approval(body)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases")
def start_release(
    body: StartReleaseIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).start_release(
            workorder_id=body.workorder_id,
            approval_id=body.approval_id,
            versionset_id=body.versionset_id,
            release_id=body.release_id,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.get("/v1/releases/{release_id}")
def get_release(
    release_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).get_release(release_id)
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/stage")
def stage_release(
    release_id: str,
    body: StepIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).stage(release_id, idempotency_key=body.idempotency_key)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/canary")
def canary_release(
    release_id: str,
    body: StepIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).canary(
            release_id, percent=body.percent, idempotency_key=body.idempotency_key
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/promote")
def promote_release(
    release_id: str,
    body: StepIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).promote(release_id, idempotency_key=body.idempotency_key)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/rollback")
def rollback_release(
    release_id: str,
    body: StepIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).rollback(
            release_id, reason=body.reason, idempotency_key=body.idempotency_key
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/reconcile")
def reconcile_release(
    release_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).reconcile(release_id)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.get("/v1/operations/{operation_id}")
def get_operation(
    operation_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).get_operation(operation_id)
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}
