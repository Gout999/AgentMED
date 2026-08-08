"""Experiment（归因对照实验）REST API。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_app_settings, get_db_session
from app.config import Settings
from app.services import read_views
from app.services.audit import AuditWriteError
from app.services.experiment_service import ExperimentService, ExperimentServiceError

router = APIRouter(tags=["experiments"])


class ExperimentCreateIn(BaseModel):
    case_id: str
    hypothesis_layer: Optional[str] = None
    protocol_version: str = "five_cell-v1"


class ProtocolFreezeIn(BaseModel):
    probe_set_digest: str
    discovery: list[str]
    hidden_confirmation: list[str]
    unaffected_controls: list[str]
    repetitions: int
    versions: dict[str, str]
    random_seed_ref: str


class ExperimentStartIn(BaseModel):
    runner_id: str
    lease_id: str
    fencing_token: int


class CellCompletedIn(BaseModel):
    cell: str
    arm_order_index: int
    recovery_rate: float
    fencing_token: Optional[int] = None


class VerdictIn(BaseModel):
    verdict: str
    deltas: dict[str, float]
    evidence_bundle_ref: str
    report_ref: str
    attributed_layer: Optional[str] = None


class ReasonIn(BaseModel):
    reason: str


def _raise(exc: ExperimentServiceError) -> None:
    status = {
        "not_found": 404,
        "validation_failed": 422,
        "illegal_transition": 422,
        "revision_conflict": 409,
    }.get(exc.code, 400)
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message, **exc.extra})


def _svc(session: Session, settings: Settings) -> ExperimentService:
    return ExperimentService(session, settings)


@router.post("/v1/experiments")
def create_experiment(
    body: ExperimentCreateIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).create(
            case_id=body.case_id,
            hypothesis_layer=body.hypothesis_layer,
            protocol_version=body.protocol_version,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        _raise(exc)
    return {}


@router.get("/v1/experiments")
def list_experiments(
    state: Optional[str] = None,
    limit: int = 100,
    cursor: int = 0,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    return _svc(session, settings).list_experiments(state=state, limit=min(limit, 500), cursor=cursor)


@router.get("/v1/experiments/{experiment_id}")
def get_experiment(
    experiment_id: str,
    view: Optional[str] = Query(default=None, alias="_view", description="full 时返回事件投影详情（cells/Δ/CI/归因层）"),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        if view == "full":
            result = read_views.get_experiment_full(session, experiment_id)
            if result is None:
                raise ExperimentServiceError("not_found", f"experiment {experiment_id} not found")
            return result
        return _svc(session, settings).get(experiment_id)
    except ExperimentServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/experiments/{experiment_id}/protocol")
def freeze_protocol(
    experiment_id: str,
    body: ProtocolFreezeIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).freeze_protocol(
            experiment_id,
            probe_set_digest=body.probe_set_digest,
            discovery=body.discovery,
            hidden_confirmation=body.hidden_confirmation,
            unaffected_controls=body.unaffected_controls,
            repetitions=body.repetitions,
            versions=body.versions,
            random_seed_ref=body.random_seed_ref,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/experiments/{experiment_id}/start")
def start_experiment(
    experiment_id: str,
    body: ExperimentStartIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).start(
            experiment_id, runner_id=body.runner_id, lease_id=body.lease_id, fencing_token=body.fencing_token
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/experiments/{experiment_id}/cells")
def cell_completed(
    experiment_id: str,
    body: CellCompletedIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).cell_completed(
            experiment_id,
            cell=body.cell,
            arm_order_index=body.arm_order_index,
            recovery_rate=body.recovery_rate,
            fencing_token=body.fencing_token,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/experiments/{experiment_id}/verdict")
def verdict_computed(
    experiment_id: str,
    body: VerdictIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).verdict_computed(
            experiment_id,
            verdict=body.verdict,
            deltas=body.deltas,
            evidence_bundle_ref=body.evidence_bundle_ref,
            report_ref=body.report_ref,
            attributed_layer=body.attributed_layer,
        )
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/experiments/{experiment_id}/escalate-full-factorial")
def escalate_full_factorial(
    experiment_id: str,
    body: ReasonIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).escalate_full_factorial(experiment_id, reason=body.reason)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        _raise(exc)
    return {}


@router.post("/v1/experiments/{experiment_id}/cancel")
def cancel_experiment(
    experiment_id: str,
    body: ReasonIn,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return _svc(session, settings).cancel(experiment_id, reason=body.reason)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except ExperimentServiceError as exc:
        _raise(exc)
    return {}
