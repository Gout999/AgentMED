"""B1–B4 故障注入端点（x-internal，演示/测试用）。

- POST /admin/inject/{faultId}：注入 B1–B4（行为与 contracts/fixtures/*.yaml ground-truth 对齐）。
- POST /admin/reset：清除全部注入，恢复基线。
生产部署必须整体移除 /admin 路由（openapi 契约注明）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import faults
from app.auth import require_write
from app.db import get_db
from app.ids import new_trace_id
from app.seeding import B1_FAULT_ID, BASELINE_ID

router = APIRouter(prefix="/admin", tags=["admin"])

VALID_FAULTS = {"B1", "B2", "B3", "B4"}


class FaultInjectionIn(BaseModel):
    expected_active_versionset_id: str = BASELINE_ID
    fault_versionset_id: str = B1_FAULT_ID


class FaultRecoveryIn(BaseModel):
    expected_active_fault_versionset_id: str = B1_FAULT_ID
    restore_versionset_id: str = BASELINE_ID
    quarantine_versionset_id: str | None = None


def _err(status: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": {"code": code, "message": message, "trace_id": new_trace_id()}},
    )


@router.post("/inject/{fault_id}")
def inject_fault(
    fault_id: str,
    body: FaultInjectionIn | None = None,
    db: Session = Depends(get_db),
    _=Depends(require_write),
):
    if fault_id not in VALID_FAULTS:
        raise _err(422, "validation_failed", f"faultId 必须是 {'/'.join(sorted(VALID_FAULTS))}")
    try:
        request = body or FaultInjectionIn()
        payload = faults.inject_fault(
            db,
            fault_id,
            expected_active_versionset_id=request.expected_active_versionset_id,
            fault_versionset_id=request.fault_versionset_id,
        )
    except KeyError as exc:
        raise _err(422, "validation_failed", str(exc))
    return {
        "fault_id": fault_id,
        "injected_at": payload.get("injected_at")
        or datetime.now(timezone.utc).isoformat(),
        "detail": payload.get("detail", ""),
        "ground_truth_ref": payload.get("ground_truth_ref", ""),
        **(
            {
                "previous_versionset_id": payload["previous_versionset_id"],
                "previous_versionset_digest": payload["previous_versionset_digest"],
                "previous_revision": payload["previous_revision"],
                "fault_versionset_id": payload["fault_versionset_id"],
                "fault_versionset_digest": payload["fault_versionset_digest"],
                "fault_revision": payload["fault_revision"],
                "duplicate": bool(payload.get("duplicate", False)),
            }
            if fault_id == "B1"
            else {}
        ),
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


@router.post("/recover/{fault_id}")
def recover_fault(
    fault_id: str,
    body: FaultRecoveryIn,
    db: Session = Depends(get_db),
    _=Depends(require_write),
):
    if fault_id != "B1":
        raise _err(422, "validation_failed", "Phase 1 compensation supports B1 only")
    try:
        receipt = faults.recover_b1(
            db,
            expected_active_fault_versionset_id=body.expected_active_fault_versionset_id,
            restore_versionset_id=body.restore_versionset_id,
            quarantine_versionset_id=body.quarantine_versionset_id,
        )
    except KeyError as exc:
        raise _err(409, "revision_conflict", str(exc))
    return {
        **receipt,
        "recovered_at": datetime.now(timezone.utc).isoformat(),
    }
