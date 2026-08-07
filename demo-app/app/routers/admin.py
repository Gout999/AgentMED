"""B1–B4 故障注入端点（x-internal，演示/测试用）。

- POST /admin/inject/{faultId}：注入 B1–B4（行为与 contracts/fixtures/*.yaml ground-truth 对齐）。
- POST /admin/reset：清除全部注入，恢复基线。
生产部署必须整体移除 /admin 路由（openapi 契约注明）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import faults
from app.auth import require_write
from app.db import get_db
from app.ids import new_trace_id

router = APIRouter(prefix="/admin", tags=["admin"])

VALID_FAULTS = {"B1", "B2", "B3", "B4"}


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "trace_id": new_trace_id()}},
    )


@router.post("/inject/{fault_id}")
def inject_fault(
    fault_id: str,
    db: Session = Depends(get_db),
    _=Depends(require_write),
):
    if fault_id not in VALID_FAULTS:
        raise _err(422, "validation_failed", f"faultId 必须是 {'/'.join(sorted(VALID_FAULTS))}")
    try:
        payload = faults.inject_fault(db, fault_id)
    except KeyError as exc:
        raise _err(422, "validation_failed", str(exc))
    return {
        "fault_id": fault_id,
        "injected_at": datetime.now(timezone.utc).isoformat(),
        "detail": payload.get("detail", ""),
        "ground_truth_ref": payload.get("ground_truth_ref", ""),
    }


@router.post("/reset")
def reset_faults(
    db: Session = Depends(get_db),
    _=Depends(require_write),
):
    cleared = faults.reset_faults(db)
    return {
        "reset_at": datetime.now(timezone.utc).isoformat(),
        "cleared": cleared,
    }
