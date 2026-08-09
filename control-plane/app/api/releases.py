"""Release Controller REST API。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import (
    get_app_settings,
    get_db_session,
    get_quality_client,
    require_approval_authority,
    require_internal_write,
    require_principal_worker,
)
from app.config import Settings
from app.quality.client import QualityClientProtocol
from app.services.audit import AuditWriteError
from app.services.release_service import ReleaseService, ReleaseServiceError

router = APIRouter(tags=["releases"])


class WorkOrderRegistrationIn(BaseModel):
    """Externally submitted WorkOrder plus its authoritative Case lease."""

    workorder: dict[str, Any]
    worker_id: str = Field(..., min_length=1, max_length=255)
    fencing_token: int = Field(..., gt=0)


class ApprovalIn(BaseModel):
    model_config = {"extra": "allow"}


class StartReleaseIn(BaseModel):
    workorder_id: str
    approval_id: str
    versionset_id: str
    release_id: Optional[str] = None


class CandidateIn(BaseModel):
    case_id: str
    worker_id: str = Field(..., min_length=1, max_length=255)
    fencing_token: int = Field(..., gt=0)
    channel: str
    attribution_report_digest: str
    base_versionset_id: str
    base_versionset_digest: str
    base_revision: int
    target_prompt_digest: str
    content: dict[str, Any]
    proposal_digest: str
    idempotency_key: str = Field(..., min_length=8, max_length=128)


class DemoFaultInjectionIn(BaseModel):
    expected_active_versionset_id: str = Field(..., min_length=1, max_length=128)
    fault_versionset_id: str = Field(..., min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=8, max_length=128)


class DemoFaultRecoveryIn(BaseModel):
    expected_active_fault_versionset_id: str = Field(..., min_length=1, max_length=128)
    restore_versionset_id: str = Field(..., min_length=1, max_length=128)
    quarantine_versionset_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    idempotency_key: str = Field(..., min_length=8, max_length=128)


class ApprovalContextIn(BaseModel):
    action: str
    reason: str = ""


class StepIn(BaseModel):
    idempotency_key: str = Field(..., min_length=8, max_length=128)
    percent: Optional[int] = None
    reason: str = "manual"


class ApprovedStepIn(StepIn):
    approval_id: str = Field(..., min_length=1, max_length=128)


class VerificationIn(BaseModel):
    eval_id: str = Field(..., min_length=1, max_length=128)
    report_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class ClosureContextIn(BaseModel):
    channel: str
    thread_ref: str
    body_ref: str
    body_digest: str = Field(..., pattern=r"^sha256:[0-9a-f]{64}$")


def _raise(exc: ReleaseServiceError) -> None:
    status = {
        "not_found": 404,
        "validation_failed": 422,
        "hash_mismatch": 422,
        "gate_missing": 422,
        "gate_failed": 422,
        "target_mismatch": 422,
        "idempotency_conflict": 409,
        "nonce_replay": 409,
        "approval_expired": 422,
        "illegal_transition": 422,
        "revision_conflict": 409,
        "lease_lost": 409,
        "quality_api_error": 502,
    }.get(exc.code, 400)
    raise HTTPException(
        status_code=status,
        detail={"code": exc.code, "message": exc.message, **exc.extra},
    )


@router.post("/v1/release-candidates")
def create_release_candidate(
    body: CandidateIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    require_principal_worker(_actor, body.worker_id)
    try:
        return _svc(session, quality, settings).create_candidate(**body.model_dump())
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


def _svc(
    session: Session,
    quality: QualityClientProtocol,
    settings: Settings,
) -> ReleaseService:
    return ReleaseService(session, quality, settings)


@router.post("/v1/demo/faults/{fault_id}/inject")
def inject_demo_fault(
    fault_id: str,
    body: DemoFaultInjectionIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    """Inject a disabled-by-default demo fault through controller authority."""

    try:
        return _svc(session, quality, settings).inject_demo_fault(
            fault_id=fault_id,
            **body.model_dump(),
        )
    except AuditWriteError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "audit_unavailable", "message": str(exc)},
        ) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/demo/faults/{fault_id}/recover")
def recover_demo_fault(
    fault_id: str,
    body: DemoFaultRecoveryIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    """Recover an incomplete B1 injection without exposing Quality write authority."""

    try:
        return _svc(session, quality, settings).recover_demo_fault(
            fault_id=fault_id,
            **body.model_dump(),
        )
    except AuditWriteError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "audit_unavailable", "message": str(exc)},
        ) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/workorders")
def register_workorder(
    body: WorkOrderRegistrationIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    require_principal_worker(_actor, body.worker_id)
    try:
        return _svc(session, quality, settings).register_workorder(
            body.workorder,
            worker_id=body.worker_id,
            fencing_token=body.fencing_token,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/approvals")
def grant_approval(
    body: dict[str, Any],
    _actor: str = Depends(require_approval_authority),
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


@router.get("/v1/approvals/{approval_id}")
def get_approval(
    approval_id: str,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    """Read the persisted grant after an independent approval adapter submits it."""

    try:
        return _svc(session, quality, settings).get_approval(approval_id)
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases")
def start_release(
    body: StartReleaseIn,
    _actor: str = Depends(require_internal_write),
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


@router.get("/v1/releases")
def list_releases(
    state: Optional[str] = None,
    limit: int = 100,
    cursor: int = 0,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).list_releases(state=state, limit=min(limit, 500), cursor=cursor)
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


@router.post("/v1/releases/{release_id}/approval-context")
def get_release_approval_context(
    release_id: str,
    body: ApprovalContextIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).action_authorization_context(
            release_id,
            body.action,
            reason=body.reason,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.get("/v1/releases/{release_id}/verification-context")
def get_release_verification_context(
    release_id: str,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).verification_context(release_id)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/closure-context")
def configure_release_closure(
    release_id: str,
    body: ClosureContextIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).configure_closure(
            release_id,
            **body.model_dump(),
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/stage")
def stage_release(
    release_id: str,
    body: StepIn,
    _actor: str = Depends(require_internal_write),
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
    body: ApprovedStepIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).canary(
            release_id,
            percent=body.percent,
            idempotency_key=body.idempotency_key,
            approval_id=body.approval_id,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/promote")
def promote_release(
    release_id: str,
    body: ApprovedStepIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).promote(
            release_id,
            idempotency_key=body.idempotency_key,
            approval_id=body.approval_id,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/verification")
def record_release_verification(
    release_id: str,
    body: VerificationIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).record_verification(
            release_id,
            eval_id=body.eval_id,
            report_hash=body.report_hash,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/rollback")
def rollback_release(
    release_id: str,
    body: ApprovedStepIn,
    _actor: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return _svc(session, quality, settings).rollback(
            release_id,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
            approval_id=body.approval_id,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ReleaseServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/releases/{release_id}/reconcile")
def reconcile_release(
    release_id: str,
    _actor: str = Depends(require_internal_write),
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
