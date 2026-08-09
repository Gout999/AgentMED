"""Internal, authenticated export of one B1 authority chain."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db_session, require_internal_write
from app.models.tables import (
    Aggregate,
    Approval,
    Audit,
    ControllerOperation,
    Event,
    GateReportRecord,
    Inbox,
    Outbox,
    OutboxDeliveryReceipt,
    ReleaseClosure,
    TrustLedgerEntry,
    WorkOrder,
)


router = APIRouter(tags=["internal-evidence"])


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _row(row: Any) -> dict[str, Any]:
    return {
        column.name: _jsonable(getattr(row, column.name))
        for column in row.__table__.columns
    }


def _contains_identity(value: Any, identities: set[str]) -> bool:
    if isinstance(value, str):
        return value in identities
    if isinstance(value, dict):
        return any(_contains_identity(item, identities) for item in value.values())
    if isinstance(value, list):
        return any(_contains_identity(item, identities) for item in value)
    return False


def _event_matches_case_evidence(
    row: Event, *, case_id: str, identities: set[str]
) -> bool:
    return (
        row.correlation_id == case_id
        or row.aggregate_id in identities
        or _contains_identity(row.payload, identities)
    )


def _audit_matches_case_evidence(row: Audit, *, identities: set[str]) -> bool:
    return row.target in identities


def _related_demo_fault_operations(session: Session, case_id: str) -> list[Aggregate]:
    """Return only the injection explicitly sealed into the complaint Case.

    VersionSet pairs are deliberately reusable in Phase 1.  Inferring causation
    from a matching VersionSet would therefore make repeated/resumed B1 runs
    ambiguous.  Complaint ingestion validates and persists the exact operation
    id, which is the sole authority for this export.
    """

    case = session.get(Aggregate, ("case", case_id))
    injection_id = (
        (case.payload or {}).get("demo_fault_injection_id") if case is not None else None
    )
    if not isinstance(injection_id, str) or not injection_id:
        return []
    row = session.get(Aggregate, ("demo_fault_injection", injection_id))
    return [row] if row is not None else []


@router.get("/v1/internal/evidence/b1")
def export_b1_evidence(
    case_id: str,
    release_id: str,
    _authority: str = Depends(require_internal_write),
    session: Session = Depends(get_db_session),
) -> dict[str, Any]:
    """Export exact persisted rows for one Case/Release without public exposure."""

    case = session.get(Aggregate, ("case", case_id))
    release = session.get(Aggregate, ("release", release_id))
    if case is None or release is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "case or release aggregate is unavailable"},
        )
    release_payload = release.payload or {}
    workorders = list(
        session.scalars(select(WorkOrder).where(WorkOrder.case_id == case_id)).all()
    )
    workorder_ids = {row.workorder_id for row in workorders}
    if release_payload.get("workorder_id") not in workorder_ids:
        raise HTTPException(
            status_code=422,
            detail={"code": "binding_mismatch", "message": "release is not bound to the Case WorkOrder"},
        )
    gates = list(
        session.scalars(
            select(GateReportRecord).where(GateReportRecord.workorder_id.in_(workorder_ids))
        ).all()
    )
    approvals = list(
        session.scalars(select(Approval).where(Approval.workorder_id.in_(workorder_ids))).all()
    )
    operations = list(
        session.scalars(
            select(ControllerOperation).where(ControllerOperation.release_id == release_id)
        ).all()
    )
    trust_entries = list(
        session.scalars(
            select(TrustLedgerEntry).where(TrustLedgerEntry.action_ref == release_id)
        ).all()
    )
    closure = session.get(ReleaseClosure, release_id)
    inbox = list(session.scalars(select(Inbox).where(Inbox.case_id == case_id)).all())
    demo_fault_operations = _related_demo_fault_operations(session, case_id)

    identities = {
        case_id,
        release_id,
        *workorder_ids,
        *(row.eval_id for row in gates),
        *(row.approval_id for row in approvals),
        *(row.operation_id for row in operations),
        *(row.remote_operation_id for row in operations if row.remote_operation_id),
        *(row.entry_id for row in trust_entries),
        *(row.aggregate_id for row in demo_fault_operations),
    }
    events = [
        row
        for row in session.scalars(select(Event).order_by(Event.created_at, Event.seq)).all()
        if _event_matches_case_evidence(row, case_id=case_id, identities=identities)
    ]
    identities.update(row.event_id for row in events)
    outbox = [
        row
        for row in session.scalars(select(Outbox).order_by(Outbox.created_at)).all()
        if row.aggregate_id in identities
        or row.source_event_id in identities
        or _contains_identity(row.payload, identities)
    ]
    identities.update(row.outbox_id for row in outbox)
    delivery_receipts = list(
        session.scalars(
            select(OutboxDeliveryReceipt).where(
                OutboxDeliveryReceipt.outbox_id.in_({row.outbox_id for row in outbox})
            )
        ).all()
    ) if outbox else []
    identities.update(row.receipt_id for row in delivery_receipts)
    audits = [
        row
        for row in session.scalars(select(Audit).order_by(Audit.ts)).all()
        if _audit_matches_case_evidence(row, identities=identities)
    ]
    trace_refs = sorted(
        {
            *(
                row.trace_id
                for row in events
                if isinstance(row.trace_id, str) and row.trace_id
            ),
            *(row.trace_id for row in audits if row.trace_id),
        }
    )
    approval_grants = []
    for row in approvals:
        approval_grants.append(
            {
                **_jsonable(row.payload or {}),
                "persistence": {
                    "status": row.status,
                    "decision": row.decision,
                    "expiry": _jsonable(row.expiry),
                    "decided_at": _jsonable(row.decided_at),
                    "consumed_at": _jsonable(row.consumed_at),
                },
            }
        )
    return {
        "schema_version": "0.1.0",
        "case_id": case_id,
        "release_id": release_id,
        "case": _row(case),
        "release": _row(release),
        "workorders": [_row(row) for row in workorders],
        "gate_reports": [_row(row) for row in gates],
        "approval_grants": approval_grants,
        "controller_operations": [_row(row) for row in operations],
        "events": [_row(row) for row in events],
        "outbox": [_row(row) for row in outbox],
        "outbox_delivery_receipts": [_row(row) for row in delivery_receipts],
        "audit_events": [_row(row) for row in audits],
        "trust_entries": [_row(row) for row in trust_entries],
        "release_closure": _row(closure) if closure is not None else None,
        "inbox": [_row(row) for row in inbox],
        "demo_fault_operations": [_row(row) for row in demo_fault_operations],
        "trace_references": trace_refs,
    }
