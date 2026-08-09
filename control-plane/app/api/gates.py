"""Authoritative GateReport registration and read API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import (
    get_app_settings,
    get_db_session,
    get_quality_client,
    require_gate_authority,
)
from app.config import Settings
from app.quality.client import QualityClientProtocol
from app.services.audit import AuditWriteError
from app.services.gate_service import GateService, GateServiceError

router = APIRouter(tags=["gates"])


def _raise(exc: GateServiceError) -> None:
    status = {
        "not_found": 404,
        "idempotency_conflict": 409,
        "hash_mismatch": 422,
        "target_mismatch": 422,
        "revision_conflict": 409,
        "gate_missing": 422,
        "gate_failed": 422,
        "validation_failed": 422,
    }.get(exc.code, 400)
    raise HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message, **exc.extra})


@router.post("/v1/gate-reports")
def register_gate_report(
    body: dict[str, Any],
    _actor: str = Depends(require_gate_authority),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
    quality: QualityClientProtocol = Depends(get_quality_client),
) -> dict[str, Any]:
    try:
        return GateService(session, settings, quality=quality).register_report(body)
    except AuditWriteError as exc:
        raise HTTPException(status_code=503, detail={"code": "audit_unavailable", "message": str(exc)}) from exc
    except GateServiceError as exc:
        _raise(exc)
    return {}


@router.get("/v1/gate-reports/{eval_id}")
def get_gate_report(
    eval_id: str,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    try:
        return GateService(session, settings).get(eval_id)
    except GateServiceError as exc:
        _raise(exc)
    return {}
