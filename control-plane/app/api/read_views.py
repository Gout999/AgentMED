"""T8 只读投影端点（纯 GET，不改任何状态；对齐 console/DATA-MAP.md）。"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, get_quality_client
from app.services import read_views

router = APIRouter(tags=["read-views"])

_ENV_CACHE_TTL_SECONDS = 5.0


@router.get("/v1/env")
def get_env(request: Request) -> dict[str, Any]:
    """demo-app active 版本集 digest（经 quality client，5s 缓存）；不可达 → unavailable 不 5xx。"""
    cache = request.app.state.env_cache
    now = time.monotonic()
    if cache.get("payload") is not None and now - cache.get("ts", 0.0) < _ENV_CACHE_TTL_SECONDS:
        return cache["payload"]
    payload = read_views.get_env_status(get_quality_client(request))
    cache["ts"] = now
    cache["payload"] = payload
    return payload


@router.get("/v1/trust/ledger")
def get_trust_ledger(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    return read_views.get_trust_ledger(session)


@router.get("/v1/trust/denials")
def get_trust_denials(session: Session = Depends(get_db_session)) -> dict[str, Any]:
    return read_views.get_trust_denials(session)


@router.get("/v1/cases/{case_id}/events")
def get_case_events(case_id: str, session: Session = Depends(get_db_session)) -> dict[str, Any]:
    result = read_views.get_case_events(session, case_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": f"case {case_id} not found"})
    return result


@router.get("/v1/workorders")
def get_workorders(
    limit: int = Query(default=100, le=500),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return read_views.list_workorders(session, limit=limit)


@router.get("/v1/gates")
def get_gates(
    limit: int = Query(default=100, le=500),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return read_views.list_gates(session, limit=limit)


@router.get("/v1/evidence")
def get_evidence(
    case_id: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    return read_views.list_evidence_refs(session, case_id=case_id, limit=limit)


@router.get("/v1/applications")
def get_applications(
    limit: int = Query(default=100, le=500),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """V5-1A catalog read model for the Console (internal projection, no bearer)."""
    return read_views.list_applications(session, limit=limit)


@router.get("/v1/cases/{case_id}/v5-readiness")
def get_case_v5_readiness(
    case_id: str, session: Session = Depends(get_db_session)
) -> dict[str, Any]:
    """V5-1C case governance read model for the Console (internal projection).

    Returns the application binding, acceptance readiness projection and
    missing-evidence list for a case, with per-record envelope integrity
    revalidation.  Missing or corrupt records project as UNKNOWN/integrity_error
    — never as trusted state.
    """
    result = read_views.case_v5_readiness(session, case_id)
    if result.get("case_id") is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": f"case {case_id} not found"})
    return result
