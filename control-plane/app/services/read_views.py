"""T8 read projections over authoritative control-plane state."""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    Aggregate,
    Audit,
    Event,
    GateReportRecord,
    TrustLedger,
    TrustLedgerEntry,
    WorkOrder,
)
from app.services.case_service import (
    quality_case_event_filter,
    quality_case_integrity_error,
)
from app.services.event_store import EventStore
from app.services.gate_service import GateService, GateServiceError
from app.utils.jcs import workorder_hash as compute_workorder_hash

logger = logging.getLogger(__name__)

# 双侧 95%（contracts/wilson 唯一事实源：z=1.96，双侧口径硬约束）
_WILSON_Z = 1.96
_PROMOTION_THRESHOLD = 0.9
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_REF_KEYS = {
    "audit_ref",
    "body_ref",
    "content_ref",
    "gate_report_ref",
    "msg_ref",
    "output_ref",
    "random_seed_ref",
    "rationale_ref",
    "report_ref",
    "text_ref",
}


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _workorder_integrity_error(workorder: WorkOrder) -> Optional[str]:
    payload = workorder.payload or {}
    if (
        payload.get("workorder_id") != workorder.workorder_id
        or payload.get("case_id") != workorder.case_id
        or payload.get("channel") != workorder.channel
    ):
        return "workorder_projection_mismatch"
    try:
        recomputed = compute_workorder_hash(payload)
    except (TypeError, ValueError):
        return "workorder_not_canonical"
    if (
        recomputed != workorder.hash
        or payload.get("hash") != workorder.hash
    ):
        return "workorder_hash_mismatch"
    return None


def _wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    """Wilson 双侧 95% 区间，返回 (lower, upper) 已截断 [0,1]、round 6。"""
    if trials <= 0:
        return (0.0, 1.0)
    p = successes / trials
    z2 = _WILSON_Z * _WILSON_Z
    denom = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denom
    margin = (_WILSON_Z / denom) * ((p * (1 - p) / trials + z2 / (4 * trials * trials)) ** 0.5)
    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return (round(lower, 6), round(upper, 6))


# ------------------------------------------------------------------ 1. demo-app 基线 digest


def get_env_status(quality_client: Any) -> dict[str, Any]:
    """经 quality client 读 active 版本集 digest；不可达/无 active → {"demo_app": "unavailable"}。"""
    try:
        page = quality_client.list_versionsets(status="active", limit=1)
    except Exception as exc:  # noqa: BLE001 —— 网络/鉴权/超时一律按 unavailable 降级（200 不 5xx）
        logger.info("env: quality api unavailable: %s", exc)
        return {"demo_app": "unavailable"}
    if not isinstance(page, dict):
        return {"demo_app": "unavailable"}
    items = page.get("items")
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        return {"demo_app": "unavailable"}
    vs = items[0]
    versionset_id = vs.get("versionset_id")
    digest = vs.get("digest")
    status = vs.get("status")
    revision = vs.get("revision")
    if (
        not isinstance(versionset_id, str)
        or not versionset_id.strip()
        or not isinstance(digest, str)
        or _SHA256_DIGEST.fullmatch(digest) is None
        or status != "active"
        or not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision <= 0
    ):
        logger.error("env: malformed active VersionSet projection")
        return {"demo_app": "unavailable"}
    return {
        "demo_app": {
            "versionset_id": versionset_id,
            "digest": digest,
            "status": status,
            "revision": revision,
        }
    }


# ------------------------------------------------------------------ 2. 信任账本


def get_trust_ledger(session: Session) -> dict[str, Any]:
    """账本网格：risk_class × action_type × epoch → Wilson 下界/状态/原始计数。"""
    rows = list(
        session.scalars(
            select(TrustLedger).order_by(
                TrustLedger.risk_class,
                TrustLedger.action_type,
                TrustLedger.epoch,
            )
        ).all()
    )
    items = []
    for r in rows:
        successes = int(r.successes or 0)
        trials = int(r.trials or 0)
        lb, ub = _wilson_interval(successes, trials)
        payload = r.payload or {}
        items.append(
            {
                "risk_class": r.risk_class,
                "action_type": r.action_type,
                "epoch": r.epoch,
                "successes": successes,
                "trials": trials,
                "autonomy_state": r.autonomy_state,
                "LB": lb,
                "UB": ub,
                "promotion_eligible": bool(payload.get("promotion_eligible", False)),
                "suspended_until": payload.get("suspended_until"),
                "pending_promotion_ref": payload.get("pending_promotion_ref"),
                "sample_rule": payload.get("sample_rule"),
                "last_action_ref": payload.get("last_action_ref"),
                "updated_at": _iso(r.updated_at),
            }
        )
    return {"items": items}


def get_trust_denials(session: Session) -> dict[str, Any]:
    """Authoritative promotion denials joined to immutable Trust entries."""
    rows = list(
        session.scalars(
            select(Audit)
            .where(Audit.action == "trust.promotion_denied")
            .order_by(Audit.ts.desc())
        ).all()
    )
    items = []
    for r in rows:
        action_type, _, risk_class = r.target.rpartition(":")
        entry_id = (r.evidence_refs or {}).get("trust_entry_id")
        entry = session.get(TrustLedgerEntry, entry_id) if entry_id else None
        lower = None
        if entry is not None:
            lower = ((entry.payload or {}).get("wilson") or {}).get("lower")
        reason = None
        if entry is not None and lower is not None:
            reason = (
                f"{entry.successes}/{entry.trials} LB={lower:.4f}<{_PROMOTION_THRESHOLD}"
                if lower <= _PROMOTION_THRESHOLD
                else "R2 requires per-action approval"
            )
        items.append(
            {
                "audit_id": r.audit_id,
                "ts": _iso(r.ts),
                "actor": r.actor,
                "action": r.action,
                "target": r.target,
                "risk_class": risk_class,
                "action_type": action_type,
                "result": r.result,
                "trace_id": r.trace_id,
                "reason": reason,
                "successes": entry.successes if entry is not None else None,
                "trials": entry.trials if entry is not None else None,
                "trust_entry_id": entry_id,
                "action_ref": entry.action_ref if entry is not None else None,
            }
        )
    return {"items": items}


# ------------------------------------------------------------------ 3/4. case 事件流 + 证据引用


def _is_evidence_key(key: str) -> bool:
    """Identify artifact/evidence bindings without treating domain IDs as artifacts."""
    lowered = key.lower()
    return (
        lowered in _EVIDENCE_REF_KEYS
        or "evidence" in lowered
        or "bundle" in lowered
        or "artifact" in lowered
        or "digest" in lowered
        or "receipt" in lowered
    )


def _evidence_refs(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (payload or {}).items():
        if v is not None and _is_evidence_key(k):
            out[k] = v
    return out


def get_case_events(session: Session, case_id: str) -> Optional[dict[str, Any]]:
    """该 case 的事件流（seq 升序），每事件附 payload 证据引用投影。"""
    store = EventStore(session)
    agg = store.get_aggregate("case", case_id)
    if agg is not None:
        events = store.list_events(case_id)
        aggregate_evidence_refs = _evidence_refs(agg.payload or {})
    else:
        from app.models.v4_tables import QualityCase

        quality_case = session.get(QualityCase, case_id)
        if quality_case is None:
            return None
        events = list(
            session.scalars(
                select(Event)
                .where(*quality_case_event_filter(quality_case))
                .order_by(Event.occurred_at, Event.event_id)
            ).all()
        )
        integrity_error = quality_case_integrity_error(quality_case)
        aggregate_evidence_refs = (
            _evidence_refs(quality_case.snapshot_payload or {})
            if integrity_error is None
            else {
                "integrity_status": "integrity_error",
                "integrity_error": integrity_error,
            }
        )
    items = []
    for ev in events:
        payload = ev.payload or {}
        items.append(
            {
                "seq": ev.seq,
                "event_id": ev.event_id,
                "event_type": ev.event_type,
                "actor": ev.actor,
                "causation_id": ev.causation_id,
                "correlation_id": ev.correlation_id,
                "trace_id": ev.trace_id,
                "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
                "payload": payload,
                "evidence_refs": _evidence_refs(payload),
            }
        )
    return {
        "case_id": case_id,
        "aggregate_type": "case",
        "items": items,
        "evidence_refs": aggregate_evidence_refs,
    }


# ------------------------------------------------------------------ 5. 实验详情全投影


def get_experiment_full(session: Session, experiment_id: str) -> Optional[dict[str, Any]]:
    """实验详情全投影：5-cell 各臂恢复率 + 每层 Δ + 裁决/归因层（从事件 payload 投影）。"""
    store = EventStore(session)
    agg = store.get_aggregate("experiment", experiment_id)
    if agg is None:
        return None
    events = store.list_events(experiment_id)
    cells: list[dict[str, Any]] = []
    deltas: Optional[dict[str, float]] = None
    verdict: Optional[str] = None
    attributed_layer: Optional[str] = None
    evidence_bundle_ref: Optional[str] = None
    report_ref: Optional[str] = None
    for ev in events:
        p = ev.payload or {}
        if ev.event_type == "experiment.cell_completed":
            cells.append(
                {
                    "cell": p.get("cell"),
                    "arm_order_index": p.get("arm_order_index"),
                    "recovery_rate": p.get("recovery_rate"),
                }
            )
        elif ev.event_type == "experiment.verdict_computed":
            deltas = p.get("deltas")
            verdict = p.get("verdict")
            attributed_layer = p.get("attributed_layer")
            evidence_bundle_ref = p.get("evidence_bundle_ref")
            report_ref = p.get("report_ref")
    return {
        "experiment_id": experiment_id,
        "state": agg.state,
        "revision": agg.revision,
        "payload": agg.payload,
        "cells": cells,
        "deltas": deltas,
        # events 无方差/样本量，95%CI 不可得 → null（诚实标注，不伪造）
        "confidence_intervals": None,
        "verdict": verdict,
        "attributed_layer": attributed_layer,
        "evidence_bundle_ref": evidence_bundle_ref,
        "report_ref": report_ref,
    }


# ------------------------------------------------------------------ 6. workorder 列表


def list_workorders(session: Session, *, limit: int = 500) -> dict[str, Any]:
    """Read immutable WorkOrders first; ChangeSet is only lifecycle metadata."""
    rows = session.scalars(
        select(WorkOrder).order_by(WorkOrder.created_at.desc(), WorkOrder.workorder_id).limit(limit)
    ).all()
    gates = GateService(session)
    items = []
    for row in rows:
        payload = row.payload or {}
        workorder_integrity_error = _workorder_integrity_error(row)
        changeset_id = f"cs_{row.workorder_id}"
        changeset = session.get(
            Aggregate,
            {"aggregate_type": "changeset", "aggregate_id": changeset_id},
        )
        projection_warning = None
        if changeset is not None:
            projected_hash = (changeset.payload or {}).get("workorder_hash")
            if projected_hash not in (None, row.hash):
                projection_warning = "changeset_hash_mismatch"
        gate_integrity_status = "verified"
        gate_integrity_error = None
        gate_binding_digest = None
        gate_target_revision = None
        gate_target_versionset_id = None
        try:
            if workorder_integrity_error is not None:
                raise GateServiceError(
                    "hash_mismatch", "WorkOrder integrity validation failed"
                )
            gate = gates.validate_for_workorder(row)
            gate_binding_digest = gate.binding_digest
            gate_target_revision = gate.target_revision
            gate_target_versionset_id = gate.target_versionset_id
        except GateServiceError as exc:
            gate_integrity_status = "integrity_error"
            gate_integrity_error = exc.code
        trusted_payload = payload if workorder_integrity_error is None else {}
        items.append(
            {
                "workorder_id": row.workorder_id,
                "changeset_id": changeset_id,
                "case_id": row.case_id if workorder_integrity_error is None else None,
                "hash": row.hash,
                "freeze_at": trusted_payload.get("expiry"),
                "requester": trusted_payload.get("created_by"),
                "channel": row.channel if workorder_integrity_error is None else "UNKNOWN",
                "nonce": trusted_payload.get("nonce"),
                "state": (
                    changeset.state
                    if changeset is not None and workorder_integrity_error is None
                    else "UNKNOWN"
                ),
                "gate_report_ref": trusted_payload.get("gate_report_ref"),
                "target_versionset_digest": trusted_payload.get("target_versionset_digest"),
                "created_at": _iso(row.created_at),
                "projection_warning": projection_warning,
                "workorder_integrity_status": (
                    "verified" if workorder_integrity_error is None else "integrity_error"
                ),
                "workorder_integrity_error": workorder_integrity_error,
                "gate_integrity_status": gate_integrity_status,
                "gate_integrity_error": gate_integrity_error,
                "gate_binding_digest": gate_binding_digest,
                "gate_target_revision": gate_target_revision,
                "gate_target_versionset_id": gate_target_versionset_id,
            }
        )
    return {"items": items}


# ------------------------------------------------------------------ 7. 门禁报告列表


def _gate_binding_status(
    session: Session,
    gates: GateService,
    row: GateReportRecord,
) -> tuple[str, Optional[str]]:
    """Validate the GateReport-to-WorkOrder relationship, not only its body."""

    workorder = session.get(WorkOrder, row.workorder_id)
    if row.authorization_digest is not None:
        # GateService.get validates this authorization digest against the
        # initial bound WorkOrder. Recheck row projection integrity explicitly.
        if workorder is None:
            return "UNKNOWN", "workorder_missing"
        integrity_error = _workorder_integrity_error(workorder)
        if integrity_error is not None:
            return "UNKNOWN", integrity_error
        return "VERIFIED", None

    has_binding = row.workorder_hash is not None or row.binding_digest is not None
    if not has_binding and workorder is None:
        return "UNBOUND", None
    if not has_binding:
        return "UNKNOWN", "gate_binding_missing"
    if row.workorder_hash is None or row.binding_digest is None:
        return "UNKNOWN", "gate_binding_incomplete"
    if workorder is None:
        return "UNKNOWN", "workorder_missing"
    integrity_error = _workorder_integrity_error(workorder)
    if integrity_error is not None:
        return "UNKNOWN", integrity_error
    try:
        bound = gates.validate_for_workorder(workorder)
    except GateServiceError as exc:
        return "UNKNOWN", exc.code
    if bound.eval_id != row.eval_id:
        return "UNKNOWN", "gate_reference_mismatch"
    return "VERIFIED", None


def list_gates(session: Session, *, limit: int = 500) -> dict[str, Any]:
    """门禁报告列表：控制面权威 gate_reports，不再信任 MCP 自报状态。"""
    rows = session.scalars(
        select(GateReportRecord).order_by(GateReportRecord.created_at.desc()).limit(limit)
    ).all()
    gates = GateService(session)
    items = []
    for r in rows:
        try:
            view = gates.get(r.eval_id)
        except GateServiceError as exc:
            # A read projection must not turn corrupted authoritative state into a
            # plausible PASS.  Preserve only stable row identity for diagnosis and
            # make the uncertainty explicit to Console consumers.
            logger.error(
                "GateReport integrity validation failed during projection",
                extra={"eval_id": r.eval_id, "error_code": exc.code},
            )
            items.append(
                {
                    "eval_id": r.eval_id,
                    "workorder_id": r.workorder_id,
                    "workorder_hash": r.workorder_hash,
                    "report_id": r.report_id,
                    "rule_track": "error",
                    "judge_track": "error",
                    "deterministic_tests": "error",
                    "live_provider_e2e": "error",
                    "verdict": "error",
                    "report_hash": r.report_hash,
                    "binding_digest": r.binding_digest,
                    "target_versionset_id": r.target_versionset_id,
                    "target_revision": r.target_revision,
                    "dataset_id": r.dataset_id,
                    "dataset_version": r.dataset_version,
                    "evidence_digest": r.evidence_digest,
                    "status": "integrity_error",
                    "integrity_error": exc.code,
                    "binding_status": "UNKNOWN",
                    "binding_error": exc.code,
                    "created_at": _iso(r.created_at),
                }
            )
            continue

        binding_status, binding_error = _gate_binding_status(session, gates, r)
        if binding_status == "UNKNOWN":
            logger.error(
                "GateReport binding validation failed during projection",
                extra={"eval_id": r.eval_id, "error_code": binding_error},
            )
            items.append(
                {
                    "eval_id": r.eval_id,
                    "workorder_id": r.workorder_id,
                    "workorder_hash": r.workorder_hash,
                    "report_id": r.report_id,
                    "rule_track": "error",
                    "judge_track": "error",
                    "deterministic_tests": "error",
                    "live_provider_e2e": "error",
                    "verdict": "error",
                    "report_hash": r.report_hash,
                    "binding_digest": r.binding_digest,
                    "target_versionset_id": r.target_versionset_id,
                    "target_revision": r.target_revision,
                    "dataset_id": r.dataset_id,
                    "dataset_version": r.dataset_version,
                    "evidence_digest": r.evidence_digest,
                    "status": "integrity_error",
                    "integrity_error": binding_error,
                    "binding_status": binding_status,
                    "binding_error": binding_error,
                    "created_at": _iso(r.created_at),
                }
            )
            continue

        report = view["report"]
        items.append(
            {
                "eval_id": view["eval_id"],
                "workorder_id": view["workorder_id"],
                "workorder_hash": view["workorder_hash"],
                "report_id": view["report_id"],
                "rule_track": (report.get("rule_track") or {}).get("status"),
                "judge_track": (report.get("judge_track") or {}).get("status"),
                "deterministic_tests": (report.get("deterministic_tests") or {}).get("status"),
                "live_provider_e2e": (report.get("live_provider_e2e") or {}).get("status"),
                "verdict": view["overall_status"],
                "report_hash": view["report_hash"],
                "binding_digest": view["binding_digest"],
                "target_versionset_id": view["target_versionset_id"],
                "target_revision": view["target_revision"],
                "dataset_id": view["dataset_id"],
                "dataset_version": view["dataset_version"],
                "evidence_digest": view["evidence_digest"],
                "status": "completed" if binding_status == "VERIFIED" else "unbound",
                "binding_status": binding_status,
                "binding_error": binding_error,
                "created_at": _iso(r.created_at),
            }
        )
    return {"items": items}


# ------------------------------------------------------------------ 8. evidence references


def _evidence_parts(
    key: str, value: Any
) -> tuple[Optional[str], Optional[str], bool]:
    """Return recorded reference/digest and flag malformed digest claims."""

    if isinstance(value, dict):
        reference = value.get("uri") or value.get("ref") or value.get("content_ref")
        raw_digest = value.get("digest")
        digest = str(raw_digest) if raw_digest is not None else None
        malformed_digest = digest is not None and _SHA256_DIGEST.fullmatch(digest) is None
        return (
            str(reference) if reference is not None else None,
            None if malformed_digest else digest,
            malformed_digest,
        )
    if isinstance(value, str):
        if "digest" in key.lower():
            valid = _SHA256_DIGEST.fullmatch(value) is not None
            return None, value if valid else None, not valid
        return value, None, False
    return None, None, False


def _evidence_item(
    *,
    source_type: str,
    source_id: str,
    case_id: Optional[str],
    key: str,
    value: Any,
    recorded_at: Any,
    trace_id: Optional[str] = None,
    source_integrity_error: Optional[str] = None,
) -> dict[str, Any]:
    reference, digest, malformed_digest = _evidence_parts(key, value)
    if source_integrity_error is not None or malformed_digest:
        binding_status = "UNKNOWN"
    else:
        binding_status = (
            "BOUND"
            if reference is not None and digest is not None
            else "DIGEST_RECORDED"
            if digest is not None
            else "REFERENCE_RECORDED"
            if reference is not None
            else "UNKNOWN"
        )
    integrity_status = (
        "source_integrity_error"
        if source_integrity_error is not None
        else "invalid_digest"
        if malformed_digest
        else "recorded"
    )
    return {
        "evidence_id": f"{source_type}:{source_id}:{key}",
        "source_type": source_type,
        "source_id": source_id,
        "case_id": case_id,
        "kind": key,
        "reference": reference,
        "digest": digest,
        "binding_status": binding_status,
        "integrity_status": integrity_status,
        "integrity_error": source_integrity_error,
        # The control plane records bindings but owns no artifact-content
        # fetcher yet. Never present an un-opened URI as verified content.
        "artifact_status": "UNKNOWN",
        "recorded_at": _iso(recorded_at),
        "trace_id": trace_id,
    }


def _iter_evidence_refs(payload: dict[str, Any]) -> Iterable[tuple[str, Any]]:
    for key, value in (payload or {}).items():
        if value is not None and _is_evidence_key(key):
            yield key, value


def list_evidence_refs(
    session: Session,
    *,
    case_id: Optional[str] = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Project recorded evidence bindings without claiming artifact availability."""

    items: list[dict[str, Any]] = []
    gate_service = GateService(session)
    events_query = select(Event).order_by(Event.occurred_at.desc(), Event.event_id).limit(limit)
    if case_id:
        events_query = events_query.where(Event.correlation_id == case_id)
        legacy_case = session.get(
            Aggregate,
            {"aggregate_type": "case", "aggregate_id": case_id},
        )
        if legacy_case is not None:
            events_query = events_query.where(Event.contract_version.is_(None))
        else:
            from app.models.v4_tables import QualityCase

            quality_case = session.get(QualityCase, case_id)
            if quality_case is None:
                events_query = events_query.where(Event.event_id == "")
            else:
                events_query = events_query.where(
                    Event.workspace_id == quality_case.workspace_id,
                    Event.contract_version.in_(("v4", "v5")),
                )
    for event in session.scalars(events_query).all():
        for key, value in _iter_evidence_refs(event.payload or {}):
            items.append(
                _evidence_item(
                    source_type="event",
                    source_id=event.event_id,
                    case_id=event.correlation_id if event.correlation_id.startswith("case_") else None,
                    key=key,
                    value=value,
                    recorded_at=event.occurred_at,
                    trace_id=event.trace_id,
                )
            )

    workorders_query = select(WorkOrder).order_by(WorkOrder.created_at.desc()).limit(limit)
    if case_id:
        workorders_query = workorders_query.where(WorkOrder.case_id == case_id)
    for workorder in session.scalars(workorders_query).all():
        payload = workorder.payload or {}
        workorder_integrity_error = _workorder_integrity_error(workorder)
        if workorder_integrity_error is not None:
            items.append(
                _evidence_item(
                    source_type="workorder",
                    source_id=workorder.workorder_id,
                    case_id=None,
                    key="workorder_integrity",
                    value={"workorder_id": workorder.workorder_id},
                    recorded_at=workorder.created_at,
                    source_integrity_error=workorder_integrity_error,
                )
            )
            continue
        gate_integrity_error = None
        try:
            gate_service.validate_for_workorder(workorder)
        except GateServiceError as exc:
            gate_integrity_error = exc.code
        for key in ("base_versionset_digest", "target_versionset_digest", "gate_report_ref"):
            value = payload.get(key)
            if value is not None:
                items.append(
                    _evidence_item(
                        source_type="workorder",
                        source_id=workorder.workorder_id,
                        case_id=workorder.case_id,
                        key=key,
                        value=value,
                        recorded_at=workorder.created_at,
                        source_integrity_error=(
                            gate_integrity_error if key == "gate_report_ref" else None
                        ),
                    )
                )
        diff = payload.get("diff")
        if isinstance(diff, dict):
            items.append(
                _evidence_item(
                    source_type="workorder",
                    source_id=workorder.workorder_id,
                    case_id=workorder.case_id,
                    key="diff",
                    value=diff,
                    recorded_at=workorder.created_at,
                )
            )

    gates_query = select(GateReportRecord).order_by(GateReportRecord.created_at.desc()).limit(limit)
    if case_id:
        workorder_ids = select(WorkOrder.workorder_id).where(WorkOrder.case_id == case_id)
        gates_query = gates_query.where(GateReportRecord.workorder_id.in_(workorder_ids))
    gate_rows = list(session.scalars(gates_query).all())
    gate_workorder_ids = {gate.workorder_id for gate in gate_rows}
    gate_case_ids: dict[str, str] = {}
    if gate_workorder_ids:
        workorders = session.scalars(
            select(WorkOrder).where(WorkOrder.workorder_id.in_(gate_workorder_ids))
        ).all()
        gate_case_ids = {
            workorder.workorder_id: workorder.case_id
            for workorder in workorders
            if _workorder_integrity_error(workorder) is None
        }
    for gate in gate_rows:
        gate_case_id = gate_case_ids.get(gate.workorder_id)
        try:
            gate_view = gate_service.get(gate.eval_id)
        except GateServiceError as exc:
            logger.error(
                "GateReport integrity validation failed during evidence projection",
                extra={"eval_id": gate.eval_id, "error_code": exc.code},
            )
            items.append(
                _evidence_item(
                    source_type="gate",
                    source_id=gate.eval_id,
                    case_id=gate_case_id,
                    key="gate_report_integrity",
                    value={"eval_id": gate.eval_id},
                    recorded_at=gate.created_at,
                    source_integrity_error=exc.code,
                )
            )
            continue

        binding_status, binding_error = _gate_binding_status(session, gate_service, gate)
        if binding_status != "VERIFIED":
            items.append(
                _evidence_item(
                    source_type="gate",
                    source_id=gate.eval_id,
                    case_id=gate_case_id,
                    key="gate_report_binding",
                    value={"eval_id": gate.eval_id},
                    recorded_at=gate.created_at,
                    source_integrity_error=binding_error or "gate_unbound",
                )
            )
            continue

        items.append(
            _evidence_item(
                source_type="gate",
                source_id=gate.eval_id,
                case_id=gate_case_id,
                key="evidence_digest",
                value=gate_view["evidence_digest"],
                recorded_at=gate.created_at,
            )
        )
        for index, artifact in enumerate((gate_view["report"] or {}).get("artifact_refs") or []):
            items.append(
                _evidence_item(
                    source_type="gate",
                    source_id=gate.eval_id,
                    case_id=gate_case_id,
                    key=f"artifact_ref_{index}",
                    value=artifact,
                    recorded_at=gate.created_at,
                )
            )

    items.sort(key=lambda item: item.get("recorded_at") or "", reverse=True)
    return {
        "items": items[:limit],
        "artifact_store": "unavailable",
        "warning": "artifact_content_unavailable",
    }


# ------------------------------------------------------- 8. V5 AI application catalog


def list_applications(
    session: Session, *, limit: int = 500
) -> dict[str, Any]:
    """Read-only V5 catalog projection for the Console Applications page.

    Revalidates every envelope digest on read.  A row whose digest or envelope
    is invalid is projected as ``integrity_error``/``unknown`` — never as a
    trusted application — and the response carries a partial warning.
    """

    from app.models.v5_tables import AIApplication, Environment, SystemComponent
    from app.utils.v5_integrity import assert_v5_record_digest

    rows = session.scalars(
        select(AIApplication)
        .order_by(AIApplication.created_at.desc(), AIApplication.application_id)
        .limit(limit)
    ).all()
    items = []
    partial = False
    for row in rows:
        envelope = row.envelope_payload
        integrity_status = "verified"
        integrity_error = None
        trusted = None
        try:
            verified_digest = assert_v5_record_digest(envelope)
            if verified_digest != row.record_digest:
                raise ValueError("projected record_digest mismatch")
            trusted = envelope
        except Exception as exc:  # noqa: BLE001 - fail-closed projection
            partial = True
            integrity_error = (
                f"v5.application_record_integrity_error:{type(exc).__name__}"
            )
            integrity_status = "integrity_error"
        environment_count = int(
            session.scalar(
                select(func.count()).select_from(Environment).where(
                    Environment.workspace_id == row.workspace_id,
                    Environment.application_id == row.application_id,
                )
            )
            or 0
        )
        component_count = int(
            session.scalar(
                select(func.count()).select_from(SystemComponent).where(
                    SystemComponent.workspace_id == row.workspace_id,
                    SystemComponent.application_id == row.application_id,
                )
            )
            or 0
        )
        items.append(
            {
                "application_id": row.application_id,
                "project_id": row.project_id,
                "slug": row.slug if trusted is not None else "UNKNOWN",
                "display_name": row.display_name if trusted is not None else "UNKNOWN",
                "owner_principal_ids": (
                    list(trusted.get("owner_principal_ids", []))
                    if trusted is not None
                    else []
                ),
                "criticality": row.criticality if trusted is not None else "UNKNOWN",
                "data_classification": (
                    row.data_classification if trusted is not None else "UNKNOWN"
                ),
                "governance_mode": row.governance_mode if trusted is not None else "UNKNOWN",
                "lifecycle_state": row.lifecycle_state if trusted is not None else "UNKNOWN",
                "revision": row.revision,
                "record_digest": row.record_digest,
                "recorded_by_principal": row.recorded_by_principal,
                "environment_count": environment_count,
                "component_count": component_count,
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
                "integrity_status": integrity_status,
                "integrity_error": integrity_error,
            }
        )
    result: dict[str, Any] = {"items": items}
    if partial:
        result["warning"] = "partial_integrity_errors"
    return result


# ------------------------------------------------------- 9. V5-1C case governance


def _wire_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_integrity_reason(exc: Exception) -> str:
    message = str(exc)
    if re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", message):
        return message
    return type(exc).__name__


def _expected_v5_record_envelope(row: Any) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "workspace_id": row.workspace_id,
        "revision": row.revision,
        "recorded_by_principal": row.recorded_by_principal,
        "recorded_at": _wire_time(row.created_at),
        "immutable": True,
        "hash_rule": (
            "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"
        ),
        "record_digest": row.record_digest,
        "authority_receipt_id": row.authority_receipt_id,
    }


def _binding_integrity_error(
    session: Session,
    row: Any,
    quality_case: Any,
    authority: Any,
) -> str | None:
    from app.models.v5_tables import SystemVersionSet
    from app.utils.v4_integrity import canonical_digest
    from app.utils.v5_integrity import assert_v5_record_digest

    try:
        payload = row.envelope_payload or {}
        if assert_v5_record_digest(payload) != row.record_digest:
            raise ValueError("record_digest_projection_mismatch")
        binding_digest = canonical_digest(
            {
                "application_id": row.application_id,
                "environment_id": row.environment_id,
                "declared_system_version_set_binding_or_unknown": (
                    row.declared_system_version_set_binding_or_unknown
                ),
            }
        )
        expected = {
            "application_case_binding_id": row.application_case_binding_id,
            "workspace_id": row.workspace_id,
            "exact_case_binding": {
                "case_id": row.case_id,
                "case_revision": row.case_revision,
                "case_digest": row.case_digest,
            },
            "application_id": row.application_id,
            "environment_id": row.environment_id,
            "declared_system_version_set_binding_or_unknown": (
                row.declared_system_version_set_binding_or_unknown
            ),
            "binding_digest": binding_digest,
            "record_envelope": _expected_v5_record_envelope(row),
        }
        if payload != expected or row.binding_digest != binding_digest:
            raise ValueError("envelope_scalar_projection_mismatch")
        declared = row.declared_system_version_set_binding_or_unknown
        if not isinstance(declared, dict):
            raise ValueError("declared_version_binding_invalid")
        if declared.get("kind") == "UNKNOWN":
            if (
                set(declared) != {"kind", "reason"}
                or not isinstance(declared.get("reason"), str)
                or not declared["reason"].strip()
            ):
                raise ValueError("declared_version_unknown_invalid")
        elif declared.get("kind") == "SYSTEM_VERSION_SET":
            if set(declared) != {"kind", "id", "revision", "digest"}:
                raise ValueError("declared_version_binding_invalid")
            version_set = session.get(SystemVersionSet, declared.get("id"))
            if (
                version_set is None
                or version_set.workspace_id != row.workspace_id
                or version_set.application_id != row.application_id
                or version_set.declared_environment_id != row.environment_id
                or version_set.record_digest != declared.get("digest")
                or assert_v5_record_digest(version_set.envelope_payload)
                != declared.get("digest")
                or (version_set.envelope_payload or {})
                .get("record_envelope", {})
                .get("revision")
                != declared.get("revision")
            ):
                raise ValueError("declared_version_binding_mismatch")
            authority.validate_receipt_binding(
                authority_receipt_id=version_set.authority_receipt_id,
                workspace_id=version_set.workspace_id,
                subject_kind="SYSTEM_VERSION_SET",
                subject_id=version_set.system_version_set_id,
                subject_revision=declared["revision"],
                subject_digest=version_set.record_digest,
            )
        else:
            raise ValueError("declared_version_kind_unknown")
        if (
            row.workspace_id != quality_case.workspace_id
            or row.case_id != quality_case.case_id
            or row.case_revision != quality_case.revision
            or row.case_digest != quality_case.record_digest
        ):
            raise ValueError("current_case_binding_mismatch")
        authority.validate_receipt_binding(
            authority_receipt_id=row.authority_receipt_id,
            workspace_id=row.workspace_id,
            subject_kind="APPLICATION_CASE_BINDING",
            subject_id=row.application_case_binding_id,
            subject_revision=row.revision,
            subject_digest=row.record_digest,
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed read projection
        return f"v5.binding_integrity_error:{_safe_integrity_reason(exc)}"
    return None


def _acceptance_executable(row: Any) -> bool:
    resolution_status = row.resolution_contract_binding_status
    # V5-1C records only PENDING_MATERIALIZATION for the V5-4-owned
    # ResolutionContract.  A human confirmation is authoritative acceptance
    # input, but it is not yet an executable gate contract.
    if (
        not isinstance(resolution_status, dict)
        or resolution_status.get("status") == "PENDING_MATERIALIZATION"
    ):
        return False
    required_objects = (
        row.acceptance_source,
        row.reproducer_input,
        row.reproducer_environment,
        row.expected_behavior,
        row.oracle_or_evaluator,
        row.applicable_workload_profile,
        row.applicable_deployment_profile,
    )
    if any(not isinstance(value, dict) or not value for value in required_objects):
        return False
    expected_kind = str(row.expected_behavior.get("kind", "")).strip().lower()
    if expected_kind in {"maintainer_review_required", "placeholder", "unknown"}:
        return False
    for profile in (
        row.applicable_workload_profile,
        row.applicable_deployment_profile,
    ):
        if str(profile.get("name", "")).strip().lower() in {"", "unknown"}:
            return False
    return True


def _missing_trace_evidence_fields(receipts: Iterable[Any]) -> list[str]:
    """Return requested fields that do not have an explicit OBSERVED result.

    ``requested_fields`` describes the query, not its failures. Treating that
    list itself as missing incorrectly reports successfully observed fields in
    a partial receipt. Malformed or absent results remain fail-closed and are
    reported as missing.
    """

    missing: list[str] = []
    for row in receipts:
        observed: set[str] = set()
        field_results = row.field_results if isinstance(row.field_results, list) else []
        for result in field_results:
            if (
                isinstance(result, dict)
                and isinstance(result.get("name"), str)
                and result.get("status") == "OBSERVED"
            ):
                observed.add(result["name"])
        requested = row.requested_fields if isinstance(row.requested_fields, list) else []
        for field in requested:
            if isinstance(field, str) and field not in observed and field not in missing:
                missing.append(field)
    return missing


def _acceptance_integrity_error(
    session: Session,
    row: Any,
    quality_case: Any,
    authority: Any,
    *,
    validate_previous: bool = True,
) -> str | None:
    from app.models.v5_tables import AcceptanceCriteriaRevision
    from app.utils.v4_integrity import canonical_digest
    from app.utils.v5_integrity import assert_v5_record_digest

    try:
        payload = row.envelope_payload or {}
        if assert_v5_record_digest(payload) != row.record_digest:
            raise ValueError("record_digest_projection_mismatch")
        acceptance_digest = canonical_digest(
            {
                "confirmation_status": row.confirmation_status,
                "acceptance_source": row.acceptance_source,
                "reproducer_input": row.reproducer_input,
                "reproducer_environment": row.reproducer_environment,
                "expected_behavior": row.expected_behavior,
                "oracle_or_evaluator": row.oracle_or_evaluator,
                "applicable_workload_profile": row.applicable_workload_profile,
                "applicable_deployment_profile": row.applicable_deployment_profile,
            }
        )
        exact_case_binding = {
            "case_id": row.case_id,
            "case_revision": row.case_revision,
            "case_digest": row.case_digest,
        }
        expected_resolution_status = {
            "status": "PENDING_MATERIALIZATION",
            "owner": "resolution-contract-controller",
            "materialization_stage": "V5-4",
            "exact_case_binding": exact_case_binding,
        }
        if row.resolution_contract_binding_status != expected_resolution_status:
            raise ValueError("resolution_contract_status_mismatch")
        expected = {
            "acceptance_criteria_revision_id": row.acceptance_criteria_revision_id,
            "workspace_id": row.workspace_id,
            "exact_case_binding": exact_case_binding,
            "resolution_contract_binding_status": expected_resolution_status,
            "confirmation_status": row.confirmation_status,
            "proposer_principal": row.proposer_principal,
            "proposed_at": _wire_time(row.proposed_at),
            "confirmer_principal": row.confirmer_principal,
            "confirmed_at": (
                _wire_time(row.confirmed_at) if row.confirmed_at is not None else None
            ),
            "exact_previous_proposed_revision_binding": (
                row.exact_previous_proposed_revision_binding
            ),
            "reauthentication_credential_binding": (
                row.reauthentication_credential_binding
            ),
            "acceptance_source": row.acceptance_source,
            "reproducer_input": row.reproducer_input,
            "reproducer_environment": row.reproducer_environment,
            "expected_behavior": row.expected_behavior,
            "oracle_or_evaluator": row.oracle_or_evaluator,
            "applicable_workload_profile": row.applicable_workload_profile,
            "applicable_deployment_profile": row.applicable_deployment_profile,
            "acceptance_digest": acceptance_digest,
            "record_envelope": _expected_v5_record_envelope(row),
        }
        if payload != expected or row.acceptance_digest != acceptance_digest:
            raise ValueError("envelope_scalar_projection_mismatch")
        if (
            row.workspace_id != quality_case.workspace_id
            or row.case_id != quality_case.case_id
            or row.case_revision != quality_case.revision
            or row.case_digest != quality_case.record_digest
        ):
            raise ValueError("current_case_binding_mismatch")
        if row.confirmation_status == "PROPOSED":
            if (
                row.confirmer_principal is not None
                or row.confirmed_at is not None
                or row.exact_previous_proposed_revision_binding is not None
                or row.exact_previous_proposed_revision_id is not None
                or row.exact_previous_proposed_revision_digest is not None
                or row.reauthentication_credential_binding is not None
            ):
                raise ValueError("proposed_confirmation_shape_mismatch")
        elif row.confirmation_status == "CONFIRMED":
            if row.confirmer_principal is None or row.confirmed_at is None:
                raise ValueError("confirmed_confirmation_shape_mismatch")
            previous = row.exact_previous_proposed_revision_binding
            if not isinstance(previous, dict) or set(previous) != {
                "kind",
                "id",
                "revision",
                "digest",
            }:
                raise ValueError("previous_proposal_binding_missing")
            if (
                previous.get("kind") != "ACCEPTANCE_CRITERIA_REVISION"
                or previous.get("revision") != 1
                or row.exact_previous_proposed_revision_id != previous.get("id")
                or row.exact_previous_proposed_revision_digest
                != previous.get("digest")
            ):
                raise ValueError("previous_proposal_binding_invalid")
            if validate_previous:
                prior = session.get(AcceptanceCriteriaRevision, previous.get("id"))
                if (
                    prior is None
                    or prior.confirmation_status != "PROPOSED"
                    or prior.record_digest != previous.get("digest")
                    or prior.revision != previous.get("revision")
                    or prior.workspace_id != row.workspace_id
                    or prior.case_id != row.case_id
                    or prior.case_revision != row.case_revision
                    or prior.case_digest != row.case_digest
                ):
                    raise ValueError("previous_proposal_binding_mismatch")
                prior_error = _acceptance_integrity_error(
                    session,
                    prior,
                    quality_case,
                    authority,
                    validate_previous=False,
                )
                if prior_error is not None:
                    raise ValueError("previous_proposal_integrity_error")
                reauthentication = row.reauthentication_credential_binding
                if not isinstance(reauthentication, dict) or set(reauthentication) != {
                    "kind",
                    "credential_id",
                    "principal_id",
                    "jti_digest",
                    "claims_digest",
                    "issued_at",
                    "binding_digest",
                }:
                    raise ValueError("reauthentication_binding_missing")
                binding_body = {
                    key: reauthentication[key]
                    for key in reauthentication
                    if key != "binding_digest"
                }
                if (
                    reauthentication.get("kind") != "PUBLIC_CREDENTIAL"
                    or reauthentication.get("principal_id")
                    != row.confirmer_principal
                    or canonical_digest(binding_body)
                    != reauthentication.get("binding_digest")
                ):
                    raise ValueError("reauthentication_binding_mismatch")
                issued_at_value = reauthentication.get("issued_at")
                if not isinstance(issued_at_value, str):
                    raise ValueError("reauthentication_issued_at_invalid")
                issued_at = datetime.fromisoformat(
                    issued_at_value.replace("Z", "+00:00")
                )
                if issued_at.tzinfo is None:
                    raise ValueError("reauthentication_issued_at_invalid")
                if (
                    issued_at.astimezone(timezone.utc)
                    <= prior.proposed_at.replace(
                        tzinfo=prior.proposed_at.tzinfo or timezone.utc
                    ).astimezone(timezone.utc)
                    or issued_at.astimezone(timezone.utc)
                    > row.confirmed_at.replace(
                        tzinfo=row.confirmed_at.tzinfo or timezone.utc
                    ).astimezone(timezone.utc)
                ):
                    raise ValueError("reauthentication_not_fresh")
        else:
            raise ValueError("confirmation_status_unknown")
        authority.validate_receipt_binding(
            authority_receipt_id=row.authority_receipt_id,
            workspace_id=row.workspace_id,
            subject_kind="ACCEPTANCE_CRITERIA_REVISION",
            subject_id=row.acceptance_criteria_revision_id,
            subject_revision=row.revision,
            subject_digest=row.record_digest,
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed read projection
        return f"v5.acceptance_integrity_error:{_safe_integrity_reason(exc)}"
    return None


def _issue_snapshot_projection(snapshot: Any) -> tuple[dict[str, Any] | None, str | None]:
    from app.utils.v4_integrity import canonical_digest

    try:
        payload = snapshot.snapshot_payload or {}
        stored_digest = payload.get("snapshot_digest")
        digest_input = dict(payload)
        digest_input["snapshot_digest"] = ""
        recomputed = canonical_digest(digest_input)
        if (
            stored_digest != recomputed
            or snapshot.snapshot_digest != recomputed
            or payload.get("schema_version") != "2.0"
            or payload.get("immutable") is not True
            or payload.get("hash_rule")
            != "jcs-rfc8785-v1+sha256(excluding:/snapshot_digest)"
            or payload.get("source_kind") != snapshot.source_kind
            or payload.get("source_url") != snapshot.source_url
            or payload.get("external_repo") != snapshot.external_repo
            or payload.get("external_issue_number") != snapshot.external_issue_number
            or payload.get("edited_flag") is not snapshot.edited_flag
            or payload.get("deleted_flag") is not snapshot.deleted_flag
            or payload.get("instruction_markers_detected")
            is not snapshot.instruction_markers_detected
            or payload.get("fetched_at") != _wire_time(snapshot.fetched_at)
        ):
            raise ValueError("snapshot_digest_or_projection_mismatch")
        return (
            {
                "issue_snapshot_id": snapshot.issue_snapshot_id,
                "source_kind": snapshot.source_kind,
                "source_url": snapshot.source_url,
                "external_repo": snapshot.external_repo,
                "external_issue_number": snapshot.external_issue_number,
                "title": payload.get("title"),
                "edited_flag": snapshot.edited_flag,
                "deleted_flag": snapshot.deleted_flag,
                "instruction_markers_detected": snapshot.instruction_markers_detected,
                "snapshot_digest": snapshot.snapshot_digest,
            },
            None,
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed read projection
        return None, (
            "v5.issue_snapshot_integrity_error:"
            f"{_safe_integrity_reason(exc)}"
        )


def case_v5_readiness(session: Session, case_id: str) -> dict[str, Any]:
    """V5-1C read-only case governance projection for the Console.

    Application binding, acceptance readiness (NEEDS_ACCEPTANCE_CRITERIA /
    READY / UNKNOWN), missing-evidence list and the read-only issue snapshot. Every
    v5 envelope is revalidated on read; corrupt rows project as
    integrity_error/UNKNOWN, never as trusted state.
    """

    from app.models.v4_tables import QualityCase, TraceEvidenceReceipt
    from app.models.v5_tables import (
        AcceptanceCriteriaRevision,
        ApplicationCaseBinding,
        IssueSourceSnapshot,
    )
    from app.services.v5_authority import V5AuthorityService

    quality_case = session.get(QualityCase, case_id)
    if quality_case is None:
        return {"case_id": None}
    authority = V5AuthorityService(session)
    case_error = quality_case_integrity_error(quality_case)
    case_integrity = "verified" if case_error is None else "integrity_error"
    binding: dict[str, Any] | None = None
    binding_integrity = "unknown"
    binding_error: str | None = None
    current_bindings = list(
        session.scalars(
            select(ApplicationCaseBinding)
            .where(
                ApplicationCaseBinding.workspace_id == quality_case.workspace_id,
                ApplicationCaseBinding.case_id == case_id,
                ApplicationCaseBinding.case_revision == quality_case.revision,
            )
            .order_by(
                ApplicationCaseBinding.created_at,
                ApplicationCaseBinding.application_case_binding_id,
            )
        ).all()
    )
    current_binding = current_bindings[0] if len(current_bindings) == 1 else None
    if len(current_bindings) > 1:
        binding_integrity = "integrity_error"
        binding_error = "v5.binding_integrity_error:current_binding_cardinality"
    if current_binding is not None:
        binding_error = _binding_integrity_error(
            session, current_binding, quality_case, authority
        )
        if binding_error is None:
            binding_integrity = "verified"
            envelope = current_binding.envelope_payload
            binding = {
                "application_case_binding_id": current_binding.application_case_binding_id,
                "application_id": current_binding.application_id,
                "environment_id": current_binding.environment_id,
                "exact_case_binding": envelope["exact_case_binding"],
                "declared_system_version_set_binding_or_unknown": envelope.get(
                    "declared_system_version_set_binding_or_unknown"
                ),
                "record_digest": current_binding.record_digest,
            }
        else:
            binding_integrity = "integrity_error"

    revisions = list(
        session.scalars(
            select(AcceptanceCriteriaRevision)
            .where(
                AcceptanceCriteriaRevision.workspace_id == quality_case.workspace_id,
                AcceptanceCriteriaRevision.case_id == case_id,
                AcceptanceCriteriaRevision.case_revision == quality_case.revision,
            )
            .order_by(
                AcceptanceCriteriaRevision.created_at,
                AcceptanceCriteriaRevision.acceptance_criteria_revision_id,
            )
        ).all()
    )
    acceptance_errors: list[str] = []
    trusted_proposals = 0
    trusted_confirmed = 0
    executable_confirmed = 0
    for row in revisions:
        error = _acceptance_integrity_error(
            session, row, quality_case, authority
        )
        if error is not None:
            acceptance_errors.append(error)
            continue
        if row.confirmation_status == "PROPOSED":
            trusted_proposals += 1
        elif row.confirmation_status == "CONFIRMED":
            trusted_confirmed += 1
            if _acceptance_executable(row):
                executable_confirmed += 1
    acceptance_integrity = (
        "integrity_error" if acceptance_errors else "verified"
    )
    acceptance_error = acceptance_errors[0] if acceptance_errors else None

    evidence_rows = list(
        session.scalars(
            select(TraceEvidenceReceipt).where(
                TraceEvidenceReceipt.workspace_id == quality_case.workspace_id,
                TraceEvidenceReceipt.signal_id == quality_case.opening_signal_id,
            )
        ).all()
    )
    missing_evidence = _missing_trace_evidence_fields(evidence_rows)

    issue_snapshot: dict[str, Any] | None = None
    issue_snapshot_integrity = "missing"
    issue_snapshot_error: str | None = None
    snapshot = session.scalar(
        select(IssueSourceSnapshot)
        .where(
            IssueSourceSnapshot.workspace_id == quality_case.workspace_id,
            IssueSourceSnapshot.case_id == case_id,
        )
        .order_by(
            IssueSourceSnapshot.created_at.desc(),
            IssueSourceSnapshot.issue_snapshot_id.desc(),
        )
    )
    if snapshot is not None:
        issue_snapshot, issue_snapshot_error = _issue_snapshot_projection(snapshot)
        issue_snapshot_integrity = (
            "verified" if issue_snapshot_error is None else "integrity_error"
        )

    if (
        case_error is not None
        or binding_integrity != "verified"
        or acceptance_errors
        or issue_snapshot_error is not None
    ):
        readiness = "UNKNOWN"
    elif executable_confirmed > 0:
        readiness = "READY"
    else:
        readiness = "NEEDS_ACCEPTANCE_CRITERIA"

    return {
        "case_id": case_id,
        "case_revision": quality_case.revision,
        "case_integrity_status": case_integrity,
        "case_integrity_error": case_error,
        "application_binding": binding,
        "binding_integrity_status": binding_integrity,
        "binding_integrity_error": binding_error,
        "case_readiness": readiness,
        "acceptance_integrity_status": acceptance_integrity,
        "acceptance_integrity_error": acceptance_error,
        "acceptance_proposal_count": trusted_proposals,
        "confirmed_acceptance_count": trusted_confirmed,
        "executable_acceptance_count": executable_confirmed,
        "missing_evidence": missing_evidence,
        "issue_snapshot": issue_snapshot,
        "issue_snapshot_integrity_status": issue_snapshot_integrity,
        "issue_snapshot_integrity_error": issue_snapshot_error,
    }
