"""异步 operation 执行与序列化（stage/canary/promote/rollback）。

- 请求受理时：CAS + 迁移合法性校验同步完成，返回 202 + pending operation。
- 后台执行：apply_transition 真正落库，operation 转 succeeded/failed。
- TTL 24h（Q1 裁决）：过期后 GET 返回 410 operation_expired。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import Operation, VersionSet
from app.versionset_service import apply_transition, now_utc


def execute_operation(db: Session, operation_id: str) -> None:
    """后台任务：执行操作并写终态。幂等：已终态则跳过。"""
    op = db.get(Operation, operation_id)
    if op is None or op.status in ("succeeded", "failed"):
        return
    vs = db.get(VersionSet, op.versionset_id)
    try:
        if vs is None:
            raise LookupError(f"versionset {op.versionset_id} not found")
        apply_transition(db, vs, op.kind, op)
        op.status = "succeeded"
        op.result = {"revision": vs.revision, "status": vs.status}
    except Exception as exc:  # noqa: BLE001 —— 回滚失败等写入 op.error
        op.status = "failed"
        op.error = {"code": "transition_failed", "message": str(exc)}
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
    return op.expires_at is not None and now > op.expires_at
