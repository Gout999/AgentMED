"""mcp-case-admin：案件查询/领单/建议/取证（spec §9.3）。

- 只读开放（case.list/get/timeline、app.logs/feedback 代理，已脱敏）。
- 写类工具（case.claim/submit_suggestion/escalate）必须带 lease fencing token 透传。
- 建议写入仅产生"建议事件"，不直接改状态（控制面裁决后迁移）。
"""
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.audit import AuditService  # noqa: E402
from common.config import Settings, get_settings  # noqa: E402
from common.db import get_engine, session_scope  # noqa: E402
from common.errors import McpError, dependency_unavailable, not_found, validation  # noqa: E402
from common.http import HttpClient  # noqa: E402
from common.ids import new_suggestion_id  # noqa: E402
from common.pii import redact_text  # noqa: E402
from common.serverkit import build_server_app  # noqa: E402
from common.tables import Suggestion  # noqa: E402

logger = logging.getLogger(__name__)

mcp = FastMCP("mcp-case-admin")

# kind → 建议事件类型（§10.2 事件目录；控制面据此裁决）
_SUGGESTION_EVENT_TYPE = {
    "triage": "TriageSuggested",
    "attribution": "AttributionSuggested",
    "fix": "WorkOrderDrafted",
    "gate": "GateReported",
    "verify": "VerificationReported",
}

_REDACT_KEYS = {"text", "message", "content", "raw_text", "summary", "complaint", "detail"}


def _settings() -> Settings:
    return get_settings()


def _cp() -> HttpClient:
    s = _settings()
    return HttpClient(s.control_plane_base_url, token=s.control_plane_token)


def _qa() -> HttpClient:
    s = _settings()
    return HttpClient(s.quality_api_base_url, token=s.quality_read_token)


def _redact_deep(value: Any) -> Any:
    """对日志/反馈条目中的用户内容字段做 PII 脱敏（spec §11.2 口径）。"""
    if isinstance(value, dict):
        return {
            k: (redact_text(v).text if k in _REDACT_KEYS and isinstance(v, str) else _redact_deep(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact_deep(v) for v in value]
    return value


# ---------- 案件查询（全员） ----------


@mcp.tool(name="case.list")
def case_list(status: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
    """列案件（ACL：全员）。status 为 Case 状态枚举（如 OPEN/DISPATCHED），limit 默认 50。"""
    params = {"limit": min(max(int(limit), 1), 500)}
    if status:
        params["state"] = status
    try:
        return _cp().get("/v1/cases", params=params)
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"case controller unreachable: {exc}") from exc


@mcp.tool(name="case.get")
def case_get(case_id: str) -> dict[str, Any]:
    """读案件全量（ACL：全员）：状态、revision、payload、event_count、相关 id。"""
    try:
        return _cp().get(f"/v1/cases/{case_id}")
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"case controller unreachable: {exc}") from exc


@mcp.tool(name="case.timeline")
def case_timeline(case_id: str, limit: int = 50) -> dict[str, Any]:
    """案件时间线（ACL：全员）：事件溯源流水（按 seq 倒序）。"""
    try:
        engine = get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT event_id, event_type, seq, actor, occurred_at, payload "
                    "FROM events WHERE aggregate_id=:cid ORDER BY seq DESC LIMIT :limit"
                ),
                {"cid": case_id, "limit": min(max(int(limit), 1), 200)},
            ).mappings().all()
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"event log unavailable: {exc}") from exc
    return {
        "case_id": case_id,
        "timeline": [
            {
                "event_id": r["event_id"],
                "event_type": r["event_type"],
                "seq": r["seq"],
                "actor": r["actor"],
                "occurred_at": str(r["occurred_at"]) if r["occurred_at"] else None,
                "payload": r["payload"] or {},
            }
            for r in rows
        ],
    }


# ---------- 领单（常设/弹性 Worker） ----------


@mcp.tool(name="case.claim")
def case_claim(worker_id: str, case_id: str) -> dict[str, Any]:
    """Worker 领单（ACL：常设/弹性 Worker）。返回 {lease_id, fencing_token, expires_at}；
    冲突/已被领 → STATE_CONFLICT。写操作必须携带 fencing_token。"""
    try:
        return _cp().post(f"/v1/cases/{case_id}/claim", json_body={"worker_id": worker_id})
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"case controller unreachable: {exc}") from exc


# ---------- 建议写入（仅建议事件，不直接改状态） ----------


@mcp.tool(name="case.submit_suggestion")
def case_submit_suggestion(
    case_id: str,
    fencing_token: int,
    kind: str,
    payload: dict[str, Any],
    evidence_refs: Optional[list[str]] = None,
    worker_id: str = "agent",
) -> dict[str, Any]:
    """提交建议（ACL：Worker）。kind ∈ triage|attribution|fix|gate|verify；写操作必须携带
    case.claim 返回的 fencing_token（过期则 LEASE_LOST）。仅产生建议事件，控制面裁决后迁移。"""
    if kind not in _SUGGESTION_EVENT_TYPE:
        raise validation(f"kind must be one of {list(_SUGGESTION_EVENT_TYPE)}")
    if not isinstance(fencing_token, int) or fencing_token <= 0:
        raise validation("fencing_token must be positive int")

    suggestion_id = new_suggestion_id()
    with session_scope() as session:
        audit = AuditService(session)
        audit.record(
            actor=worker_id,
            action="case.suggestion",
            target=case_id,
            params={"suggestion_id": suggestion_id, "kind": kind, "fencing_token": fencing_token},
            result="success",
        )
        session.add(
            Suggestion(
                suggestion_id=suggestion_id,
                case_id=case_id,
                worker_id=worker_id,
                fencing_token=fencing_token,
                kind=kind,
                payload=payload,
                evidence_refs=evidence_refs or [],
            )
        )
    return {
        "accepted": True,
        "event_id": suggestion_id,
        "event_type": _SUGGESTION_EVENT_TYPE[kind],
        "case_id": case_id,
        "note": "suggestion recorded; 控制面裁决后迁移（不直接改状态）",
    }


@mcp.tool(name="case.escalate")
def case_escalate(case_id: str, reason: str, fencing_token: Optional[int] = None) -> dict[str, Any]:
    """升级人工（ACL：全员，经控制面）。写入 case.escalated 事件；如有领单须携带 fencing_token。"""
    body: dict[str, Any] = {"event_type": "case.escalated", "payload": {"reason": reason}, "actor": "agent"}
    if fencing_token is not None:
        body["fencing_token"] = int(fencing_token)
    try:
        return _cp().post(f"/v1/cases/{case_id}/transitions", json_body=body)
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"case controller unreachable: {exc}") from exc


# ---------- 取证（代理 Quality API，已脱敏） ----------


@mcp.tool(name="app.logs")
def app_logs(
    app: str,
    time_range: Optional[dict[str, str]] = None,
    filter: Optional[dict[str, Any]] = None,
    limit: int = 100,
) -> dict[str, Any]:
    """代理 Quality API GET /logs（ACL：采集员/归因师；返回已脱敏）。app 为目标应用名。"""
    params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
    if time_range:
        if time_range.get("from"):
            params["from"] = time_range["from"]
        if time_range.get("to"):
            params["to"] = time_range["to"]
    if filter and filter.get("versionset_id"):
        params["versionset_id"] = filter["versionset_id"]
    try:
        result = _qa().get("/v2/logs", params=params)
    except McpError as exc:
        # 降级：evidence_gap=true，不阻塞流水线（spec §9.3）
        return {"entries": [], "evidence_gap": True, "error_code": exc.error_code, "app": app}
    entries = _redact_deep(result.get("items", []))
    return {"entries": entries, "evidence_gap": False, "app": app}


@mcp.tool(name="app.feedback")
def app_feedback(
    app: str,
    time_range: Optional[dict[str, str]] = None,
    rating: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    """代理 Quality API GET /feedback（ACL：采集员；返回已脱敏）。"""
    params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
    if time_range:
        if time_range.get("from"):
            params["from"] = time_range["from"]
        if time_range.get("to"):
            params["to"] = time_range["to"]
    if rating:
        params["rating"] = rating
    try:
        result = _qa().get("/v2/feedback", params=params)
    except McpError as exc:
        return {"feedback": [], "evidence_gap": True, "error_code": exc.error_code, "app": app}
    feedback = _redact_deep(result.get("items", []))
    return {"feedback": feedback, "evidence_gap": False, "app": app}


def main() -> None:
    import uvicorn

    s = _settings()
    uvicorn.run(build_server_app(mcp), host=s.host, port=s.case_admin_port, log_level=s.log_level.lower())


if __name__ == "__main__":
    main()
