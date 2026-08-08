"""T8 read projections over authoritative control-plane state.

Gate compatibility still reads the legacy MCP eval projection while P0-1's
GateReport table is preferred. Trust is no longer read from the disconnected
MCP schema: its source of truth is the transactional control-plane ledger.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    select,
)
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.models.tables import (
    Aggregate,
    Audit,
    Event,
    GateReportRecord,
    TrustLedger,
    TrustLedgerEntry,
)
from app.services.event_store import EventStore
from app.services.gate_service import GateService, GateServiceError

logger = logging.getLogger(__name__)

# ---------- 跨组件表只读映射（不注册进 Base；建表由 mcp-servers migration 负责） ----------

_MCP_META = MetaData()

MCP_EVAL_RUNS = Table(
    "mcp_eval_runs",
    _MCP_META,
    Column("eval_id", String(64), primary_key=True),
    Column("workorder_id", String(128)),
    Column("suite_digest", String(80)),
    Column("status", String(32)),
    Column("report", JSON),
    Column("report_hash", String(64)),
    Column("created_at", DateTime(timezone=True)),
)

# 双侧 95%（contracts/wilson 唯一事实源：z=1.96，双侧口径硬约束）
_WILSON_Z = 1.96
_PROMOTION_THRESHOLD = 0.9


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


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


def _mcp_rows(
    session: Session, table: Table, *, order_by: Any = None, limit: int = 500
) -> tuple[list[Any], Optional[str]]:
    """查询 mcp_* 表；表不存在 → ([] , "source_unavailable")。"""
    q = select(table)
    if order_by is not None:
        cols = order_by if isinstance(order_by, (tuple, list)) else (order_by,)
        q = q.order_by(*cols)
    q = q.limit(limit)
    try:
        rows = list(session.execute(q).mappings().all())
        return rows, None
    except (OperationalError, ProgrammingError):
        session.rollback()
        logger.info("mcp source unavailable for %s", table.name)
        return [], "source_unavailable"


# ------------------------------------------------------------------ 1. demo-app 基线 digest


def get_env_status(quality_client: Any) -> dict[str, Any]:
    """经 quality client 读 active 版本集 digest；不可达/无 active → {"demo_app": "unavailable"}。"""
    try:
        page = quality_client.list_versionsets(status="active", limit=1)
    except Exception as exc:  # noqa: BLE001 —— 网络/鉴权/超时一律按 unavailable 降级（200 不 5xx）
        logger.info("env: quality api unavailable: %s", exc)
        return {"demo_app": "unavailable"}
    items = page.get("items") or []
    if not items:
        return {"demo_app": "unavailable"}
    vs = items[0]
    return {
        "demo_app": {
            "versionset_id": vs.get("versionset_id"),
            "digest": vs.get("digest"),
            "status": vs.get("status"),
            "revision": vs.get("revision"),
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
            }
        )
    return {"items": items}


# ------------------------------------------------------------------ 3/4. case 事件流 + 证据引用


def _is_evidence_key(key: str) -> bool:
    """证据引用字段判定（与 console CaseDetailPage isEvidenceKey 对齐）：排除 app_ref 元数据。"""
    return key != "app_ref" and (key == "text_ref" or "evidence" in key.lower() or "bundle" in key.lower() or key.lower().endswith("_ref") or "digest" in key.lower())


def _evidence_refs(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (payload or {}).items():
        if v is not None and _is_evidence_key(k):
            out[k] = v if not isinstance(v, dict) else str(v)
    return out


def get_case_events(session: Session, case_id: str) -> Optional[dict[str, Any]]:
    """该 case 的事件流（seq 升序），每事件附 payload 证据引用投影。"""
    store = EventStore(session)
    agg = store.get_aggregate("case", case_id)
    if agg is None:
        return None
    events = store.list_events(case_id)
    items = []
    for ev in events:
        payload = ev.payload or {}
        items.append(
            {
                "seq": ev.seq,
                "event_id": ev.event_id,
                "event_type": ev.event_type,
                "actor": ev.actor,
                "occurred_at": ev.occurred_at.isoformat() if ev.occurred_at else None,
                "payload": payload,
                "evidence_refs": _evidence_refs(payload),
            }
        )
    return {
        "case_id": case_id,
        "aggregate_type": "case",
        "items": items,
        "evidence_refs": _evidence_refs(agg.payload or {}),
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


def _workorder_id_from_changeset(changeset_id: str) -> str:
    return changeset_id[3:] if changeset_id.startswith("cs_") else changeset_id


def _changeset_drafted_meta(session: Session, changeset_id: str) -> tuple[Optional[str], Optional[str]]:
    """changeset.drafted 事件 → (author_agent, channel)。"""
    ev = session.scalar(
        select(Event)
        .where(Event.aggregate_id == changeset_id, Event.event_type == "changeset.drafted")
        .order_by(Event.seq.asc())
        .limit(1)
    )
    if ev is None:
        return None, None
    p = ev.payload or {}
    return p.get("author_agent"), p.get("channel")


def list_workorders(session: Session, *, limit: int = 500) -> dict[str, Any]:
    """WorkOrder 列表：changesets 投影 + freeze/提请事件（drafted.author_agent / approval_requested.expiry）。"""
    rows = session.scalars(
        select(Aggregate)
        .where(Aggregate.aggregate_type == "changeset")
        .order_by(Aggregate.aggregate_id)
        .limit(limit)
    ).all()
    items = []
    for agg in rows:
        payload = agg.payload or {}
        requester, channel = _changeset_drafted_meta(session, agg.aggregate_id)
        items.append(
            {
                "workorder_id": payload.get("workorder_ref") or _workorder_id_from_changeset(agg.aggregate_id),
                "changeset_id": agg.aggregate_id,
                "case_id": payload.get("case_id"),
                "hash": payload.get("workorder_hash"),
                "freeze_at": payload.get("expiry"),
                "requester": requester,
                "channel": channel,
                "nonce": payload.get("nonce"),
                "state": agg.state,
            }
        )
    return {"items": items}


# ------------------------------------------------------------------ 7. 门禁报告列表


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
                "status": "completed",
                "created_at": _iso(r.created_at),
            }
        )
    return {"items": items}
