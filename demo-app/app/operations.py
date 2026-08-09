"""异步 operation 执行与序列化（stage/canary/promote/rollback）。

- 请求受理时：CAS + 迁移合法性校验同步完成，返回 202 + pending operation。
- 后台执行：apply_transition 真正落库，operation 转 succeeded/failed。
- TTL 24h（Q1 裁决）：过期后 GET 返回 410 operation_expired。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Operation, VersionSet
from app.versionset_service import apply_transition, now_utc


# One transaction-scoped lock serializes every lifecycle operation that can
# replace the globally active VersionSet.  Per-candidate row locks are
# insufficient because two different candidates do not contend on the same row.
_ACTIVE_LIFECYCLE_LOCK_ID = int.from_bytes(b"CASELOOP", byteorder="big", signed=True)


def _lock_active_lifecycle(db: Session, kind: str) -> None:
    if kind not in ("promote", "rollback"):
        return
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": _ACTIVE_LIFECYCLE_LOCK_ID},
    )


def execute_operation(db: Session, operation_id: str) -> None:
    """Execute one accepted operation with a second CAS check under row lock."""
    op = db.get(Operation, operation_id)
    if op is None or op.status in ("succeeded", "failed"):
        return
    try:
        _lock_active_lifecycle(db, op.kind)
        vs = db.execute(
            select(VersionSet)
            .where(VersionSet.versionset_id == op.versionset_id)
            .with_for_update()
        ).scalar_one_or_none()
        if vs is None:
            raise LookupError(f"versionset {op.versionset_id} not found")
        receipt = apply_transition(db, vs, op.kind, op)
        op.status = "succeeded"
        op.result = {"revision": vs.revision, "status": vs.status, **receipt}
        op.error = None
        op.updated_at = now_utc()
        db.commit()
    except Exception as exc:  # noqa: BLE001 —— 回滚失败等写入 op.error
        # Never commit a partially applied transition together with a failed
        # operation record. Re-open a clean transaction for the failure receipt.
        db.rollback()
        op = db.get(Operation, operation_id)
        if op is None or op.status in ("succeeded", "failed"):
            return
        op.status = "failed"
        code = getattr(exc, "code", "transition_failed")
        op.error = {"code": code, "message": str(exc)}
        op.updated_at = now_utc()
        db.commit()


def build_operation_dict(op: Operation) -> dict[str, Any]:
    d: dict[str, Any] = {
        "operation_id": op.operation_id,
        "kind": op.kind,
        "status": op.status,
        "idempotency_key": op.idempotency_key,
        "versionset_id": op.versionset_id,
        "created_at": op.created_at.isoformat() if op.created_at else None,
        "updated_at": op.updated_at.isoformat() if op.updated_at else None,
        "expires_at": op.expires_at.isoformat() if op.expires_at else None,
    }
    if op.result is not None:
        d["result"] = op.result
    if op.error is not None:
        d["error"] = op.error
    return d


def is_expired(op: Operation, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    expiry = op.expires_at
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry is not None and now > expiry
