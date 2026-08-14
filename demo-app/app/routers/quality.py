"""Quality API v2 路由（对齐 contracts/quality-api/openapi.yaml）。

写面：stage/canary/promote/rollback —— 异步 202 + operation；CAS + 幂等。
读面：versionsets / operations / logs / feedback。
OAuth token：/oauth/token（client_credentials，签发演示令牌）。
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Optional

from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_read, require_write
from app.chat_service import execute_chat
from app.config import get_settings
from app.db import SessionLocal, get_db
from app.ids import new_trace_id
from app.live_config import VersionSetConfigError, resolve_versionset_config
from app.models import Operation, VersionSet
from app.operations import build_operation_dict, execute_operation, is_expired
from app.read_queries import (
    feedback_entry_dict,
    log_entry_dict,
    query_feedback,
    query_logs,
)
from app.schemas import (
    CanaryRequest,
    LifecycleRequest,
    PromoteRequest,
    RollbackRequest,
    ChatRequest,
    TokenRequest,
    VersionSetContentInput,
)
from app.versionset_service import (
    CASError,
    IdempotencyConflictError,
    IllegalTransitionError,
    build_status,
    create_operation,
    create_versionset,
    get_versionset,
    lifecycle_fingerprint,
    list_versionsets,
    lock_idempotency_key,
    record_operation_idempotency,
    resolve_idempotent_operation,
    validate_cas,
    validate_transition,
)

router = APIRouter(prefix="/v2", tags=["quality-api"])
oauth_router = APIRouter(tags=["oauth"])


# ---------------------------------------------------------------- 错误构造

def _err_body(code: str, message: str, details: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            **({"details": details} if details else {}),
            "trace_id": new_trace_id(),
        }
    }


def build_versionset_dict(vs: VersionSet) -> dict[str, Any]:
    content = vs.content
    content_body = {k: content.get(k) for k in ("prompt", "kb_manifest", "model")}
    return {
        "versionset_id": vs.versionset_id,
        "revision": vs.revision,
        "status": vs.status,
        "content": content_body,
        "digest": content.get("digest", vs.digest),
        "created_at": vs.created_at.isoformat() if vs.created_at else None,
        "labels": vs.labels or {},
    }


# ---------------------------------------------------------------- OAuth

@oauth_router.post("/oauth/token")
def oauth_token(payload: TokenRequest | None = None):
    settings = get_settings()
    if (
        settings.caseloop_read_token
        and settings.caseloop_write_token
        and secrets.compare_digest(settings.caseloop_read_token, settings.caseloop_write_token)
    ):
        raise HTTPException(
            status_code=503,
            detail=_err_body(
                "auth_misconfigured",
                "Quality API read and write credentials must be distinct",
            ),
        )
    if (
        settings.release_controller_client_secret
        and settings.quality_reader_client_secret
        and secrets.compare_digest(
            settings.release_controller_client_secret,
            settings.quality_reader_client_secret,
        )
    ):
        raise HTTPException(
            status_code=503,
            detail=_err_body(
                "auth_misconfigured",
                "Quality API OAuth client credentials must be distinct",
            ),
        )
    if payload is None or payload.grant_type != "client_credentials":
        raise HTTPException(status_code=401, detail=_err_body("unauthorized", "invalid client credentials"))
    cid = payload.client_id or ""
    supplied = payload.client_secret or ""
    if cid == "release-controller":
        expected = settings.release_controller_client_secret
        if not expected:
            raise HTTPException(
                status_code=503,
                detail=_err_body("auth_not_configured", "release-controller OAuth secret is not configured"),
            )
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail=_err_body("unauthorized", "invalid client credentials"))
        return {
            "access_token": settings.caseloop_write_token,
            "token_type": "bearer",
            "scope": "quality:read quality:write",
            "expires_in": 3600,
        }
    if cid in ("quality-reader", "reader"):
        expected = settings.quality_reader_client_secret
        if not expected:
            raise HTTPException(
                status_code=503,
                detail=_err_body("auth_not_configured", "quality-reader OAuth secret is not configured"),
            )
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail=_err_body("unauthorized", "invalid client credentials"))
        return {
            "access_token": settings.caseloop_read_token,
            "token_type": "bearer",
            "scope": "quality:read",
            "expires_in": 3600,
        }
    raise HTTPException(status_code=401, detail=_err_body("unauthorized", "invalid client credentials"))


# ---------------------------------------------------------------- VersionSet 读

@router.get("/versionsets")
def list_versionsets_ep(
    db: Session = Depends(get_db),
    _=Depends(require_read),
    status: Optional[str] = None,
    limit: int = 50,
    cursor: Optional[str] = None,
):
    limit = max(1, min(limit, 200))
    items, next_cursor = list_versionsets(db, status=status, limit=limit, cursor=cursor)
    return {
        "items": [build_versionset_dict(v) for v in items],
        **({"next_cursor": next_cursor} if next_cursor else {}),
    }


@router.get("/versionsets/{vs_id}")
def get_versionset_ep(
    vs_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_read),
):
    vs = get_versionset(db, vs_id)
    if vs is None:
        raise HTTPException(status_code=404, detail=_err_body("not_found", "versionset not found"))
    return JSONResponse(
        content=build_versionset_dict(vs),
        headers={"ETag": f'"{vs.revision}"'},
    )


@router.get("/versionsets/{vs_id}/status")
def get_status_ep(
    vs_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_read),
):
    vs = get_versionset(db, vs_id)
    if vs is None:
        raise HTTPException(status_code=404, detail=_err_body("not_found", "versionset not found"))
    return JSONResponse(
        content=build_status(db, vs),
        headers={"ETag": f'"{vs.revision}"'},
    )


@router.post("/versionsets/{vs_id}/evaluate")
def evaluate_versionset_ep(
    vs_id: str,
    payload: ChatRequest,
    db: Session = Depends(get_db),
    _=Depends(require_read),
):
    """Execute the exact immutable candidate without changing lifecycle state."""

    try:
        config = resolve_versionset_config(db, vs_id)
    except VersionSetConfigError as exc:
        status = 404 if exc.code == "not_found" else 409
        raise HTTPException(status_code=status, detail=_err_body(exc.code, exc.message)) from exc
    return execute_chat(payload, db, config, span_name="gate.candidate.evaluate")


# ---------------------------------------------------------------- 创建

@router.post("/versionsets")
def create_versionset_ep(
    payload: VersionSetContentInput,
    db: Session = Depends(get_db),
    _=Depends(require_write),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
):
    try:
        vs, created = create_versionset(db, payload.model_dump(), idempotency_key)
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=422,
            detail=_err_body(
                "validation_failed", str(exc), {"subcode": "idempotency_key_conflict"}
            ),
        )
    return JSONResponse(
        status_code=201 if created else 200,
        content=build_versionset_dict(vs),
        headers={"ETag": f'"{vs.revision}"'},
    )


# ---------------------------------------------------------------- 生命周期写面

def _schedule_operation(op: Operation, background: BackgroundTasks) -> None:
    def _run(op_id: str) -> None:
        db = SessionLocal()
        try:
            time.sleep(0.2)  # 演示异步语义：pending → succeeded
            execute_operation(db, op_id)
        finally:
            db.close()

    background.add_task(_run, op.operation_id)


def _handle_lifecycle(
    action: str,
    vs_id: str,
    body: dict[str, Any],
    idempotency_key: str,
    if_match: Optional[str],
    db: Session,
    background: BackgroundTasks,
):
    fingerprint = lifecycle_fingerprint(vs_id, action, body)
    lock_idempotency_key(db, idempotency_key)

    # Idempotent replay is resolved before current-state/CAS validation. A
    # terminal operation must remain replayable after it changed the revision.
    try:
        existing = resolve_idempotent_operation(db, idempotency_key, fingerprint)
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=422,
            detail=_err_body(
                "validation_failed", str(exc), {"subcode": "idempotency_key_conflict"}
            ),
        )
    if existing is not None:
        if is_expired(existing):
            if existing.status in ("pending", "running"):
                existing.status = "failed"
                existing.error = {
                    "code": "operation_expired",
                    "message": "pending lifecycle operation expired before execution",
                }
                db.commit()
            raise HTTPException(
                status_code=410,
                detail=_err_body(
                    "operation_expired",
                    "idempotent lifecycle operation expired before completion",
                ),
            )
        if existing.status in ("pending", "running"):
            # BackgroundTasks is not durable across a process crash. Exact-key
            # replay safely re-schedules the same operation; the executor is
            # idempotent and performs a locked second CAS check.
            _schedule_operation(existing, background)
        return JSONResponse(status_code=202, content=build_operation_dict(existing))

    # Serialize acceptance per target. The pending-operation query prevents two
    # different keys with the same If-Match from both being accepted before the
    # asynchronous executor advances the revision.
    vs = db.execute(
        select(VersionSet)
        .where(VersionSet.versionset_id == vs_id)
        .with_for_update()
    ).scalar_one_or_none()
    if vs is None:
        raise HTTPException(status_code=404, detail=_err_body("not_found", "versionset not found"))
    pending = db.execute(
        select(Operation)
        .where(
            Operation.versionset_id == vs_id,
            Operation.status.in_(("pending", "running")),
        )
        .with_for_update()
    ).scalars().first()
    if pending is not None and is_expired(pending):
        pending.status = "failed"
        pending.error = {
            "code": "operation_expired",
            "message": "pending lifecycle operation expired before execution",
        }
        db.flush()
        pending = None
    if pending is not None:
        raise HTTPException(
            status_code=409,
            detail=_err_body(
                "revision_conflict",
                "another lifecycle operation is already pending for this VersionSet",
                {"operation_id": pending.operation_id, "current_revision": vs.revision},
            ),
        )
    try:
        validate_cas(vs, if_match, body.get("expected_revision"))
        validate_transition(vs, action)
    except CASError as exc:
        status = 412 if exc.code == "precondition_failed" else 409
        raise HTTPException(status_code=status, detail=_err_body(exc.code, exc.message, exc.details))
    except IllegalTransitionError as exc:
        raise HTTPException(
            status_code=422,
            detail=_err_body(
                "validation_failed",
                str(exc),
                {
                    "subcode": "illegal_transition",
                    "current_status": exc.current_status,
                    "attempted": exc.attempted,
                },
            ),
        )

    request_payload: dict[str, Any] = {}
    if action == "canary" and body.get("percent") is not None:
        request_payload["percent"] = body["percent"]
    if action == "promote" and body.get("expected_active_digest") is not None:
        request_payload["expected_active_digest"] = body["expected_active_digest"]
    if action == "rollback" and body.get("rollback_to") is not None:
        request_payload["rollback_to"] = body["rollback_to"]

    op = create_operation(db, vs, action, idempotency_key, request=request_payload)
    record_operation_idempotency(db, idempotency_key, fingerprint, op)
    db.commit()
    _schedule_operation(op, background)
    return JSONResponse(status_code=202, content=build_operation_dict(op))


@router.post("/versionsets/{vs_id}/stage")
def stage_ep(
    vs_id: str,
    payload: Optional[LifecycleRequest] = None,
    db: Session = Depends(get_db),
    _=Depends(require_write),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    background: BackgroundTasks = BackgroundTasks(),
):
    return _handle_lifecycle("stage", vs_id, payload.model_dump() if payload else {}, idempotency_key, if_match, db, background)


@router.post("/versionsets/{vs_id}/canary")
def canary_ep(
    vs_id: str,
    payload: CanaryRequest,
    db: Session = Depends(get_db),
    _=Depends(require_write),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    background: BackgroundTasks = BackgroundTasks(),
):
    return _handle_lifecycle("canary", vs_id, payload.model_dump(), idempotency_key, if_match, db, background)


@router.post("/versionsets/{vs_id}/promote")
def promote_ep(
    vs_id: str,
    payload: PromoteRequest,
    db: Session = Depends(get_db),
    _=Depends(require_write),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    background: BackgroundTasks = BackgroundTasks(),
):
    return _handle_lifecycle("promote", vs_id, payload.model_dump(), idempotency_key, if_match, db, background)


@router.post("/versionsets/{vs_id}/rollback")
def rollback_ep(
    vs_id: str,
    payload: RollbackRequest,
    db: Session = Depends(get_db),
    _=Depends(require_write),
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
    background: BackgroundTasks = BackgroundTasks(),
):
    return _handle_lifecycle("rollback", vs_id, payload.model_dump(), idempotency_key, if_match, db, background)


# ---------------------------------------------------------------- operations 读面

@router.get("/operations/{operation_id}")
def get_operation_ep(
    operation_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_read),
):
    op = db.get(Operation, operation_id)
    if op is None:
        raise HTTPException(status_code=404, detail=_err_body("not_found", "operation not found"))
    if is_expired(op):
        raise HTTPException(
            status_code=410,
            detail=_err_body("operation_expired", "operation record expired and purged"),
        )
    return build_operation_dict(op)


# ---------------------------------------------------------------- logs / feedback

@router.get("/logs")
def get_logs_ep(
    db: Session = Depends(get_db),
    _=Depends(require_read),
    from_dt: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    versionset_id: Optional[str] = None,
    request_id: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
):
    limit = max(1, min(limit, 500))
    items, next_cursor = query_logs(
        db,
        from_dt=_parse_dt(from_dt),
        to_dt=_parse_dt(to),
        versionset_id=versionset_id,
        request_id=request_id,
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": [log_entry_dict(x) for x in items],
        **({"next_cursor": next_cursor} if next_cursor else {}),
    }


@router.get("/feedback")
def get_feedback_ep(
    db: Session = Depends(get_db),
    _=Depends(require_read),
    from_dt: Optional[str] = Query(default=None, alias="from"),
    to: Optional[str] = Query(default=None),
    versionset_id: Optional[str] = None,
    rating: Optional[str] = None,
    limit: int = 100,
    cursor: Optional[str] = None,
):
    limit = max(1, min(limit, 500))
    items, next_cursor = query_feedback(
        db,
        from_dt=_parse_dt(from_dt),
        to_dt=_parse_dt(to),
        versionset_id=versionset_id,
        rating=rating,
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": [feedback_entry_dict(x) for x in items],
        **({"next_cursor": next_cursor} if next_cursor else {}),
    }


def _parse_dt(s: Optional[str]):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:  # noqa: BLE001
        return None
