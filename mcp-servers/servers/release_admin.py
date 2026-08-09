"""mcp-release-admin：WorkOrder 登记/门禁/审批/灰度申请（spec §9.4 + T4 任务）。

边界（硬约束）：
- 本 server 不暴露 Quality API 写面；发布执行权在 Release Controller。
- 灰度/回滚仅"申请"：R2_HIGH_IMPACT 动作永远返回"需逐次审批"（T8）。
- WorkOrder hash 绑定全部字段（JCS+SHA-256），FROZEN 后不可变（防掉包）。
"""
import logging
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.audit import AuditService  # noqa: E402
from common.config import Settings, get_settings  # noqa: E402
from common.db import get_engine, session_scope  # noqa: E402
from common.errors import GATE_FAILED, McpError, dependency_unavailable, forbidden, not_found, validation  # noqa: E402
from common.http import HttpClient  # noqa: E402
from common.ids import new_approval_id, new_nonce, new_workorder_id  # noqa: E402
from common.jcs import jcs_subset, params_digest, sha256_hex, workorder_hash  # noqa: E402
from common.serverkit import (  # noqa: E402
    ToolDefinitionRegistry,
    build_server_app,
    build_tool_projection,
    validate_projection_runtime,
)
from common.tables import ApprovalRequest, EvalRun, WorkOrderDraft  # noqa: E402

logger = logging.getLogger(__name__)

mcp = ToolDefinitionRegistry("mcp-release-admin")

_LAYER_TO_CHANNEL = {"prompt": "prompt", "kb": "kb", "model": "model_params", "model_params": "model_params"}

def _settings() -> Settings:
    return get_settings()


def _cp() -> HttpClient:
    s = _settings()
    return HttpClient(s.control_plane_base_url, token=s.control_plane_role_token)


def _qa() -> HttpClient:
    """Quality read surface only; candidate lifecycle writes stay in Release Controller."""

    s = _settings()
    return HttpClient(s.quality_api_base_url, token=s.quality_read_token)


def _bound_worker_id(supplied: Optional[str] = None) -> str:
    canonical = _settings().mcp_worker_id
    if not canonical:
        raise forbidden("MCP process has no canonical worker identity")
    if supplied is not None and supplied != canonical:
        raise forbidden("caller-supplied identity does not match authenticated MCP projection")
    return canonical


# ---------- authoritative VersionSet context (read-only) ----------


@mcp.tool(name="versionset.list")
def versionset_list(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """List VersionSets so repair proposals can bind the active base exactly."""

    params: dict[str, Any] = {"limit": min(max(int(limit), 1), 200)}
    if status:
        params["status"] = status
    try:
        return _qa().get("/v2/versionsets", params=params)
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"Quality VersionSet list unavailable: {exc}") from exc


@mcp.tool(name="versionset.get")
def versionset_get(versionset_id: str) -> dict[str, Any]:
    """Read the exact base component content/digest/revision without write authority."""

    if not isinstance(versionset_id, str) or not versionset_id.strip():
        raise validation("versionset_id must be non-empty")
    try:
        return _qa().get(f"/v2/versionsets/{versionset_id}")
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"Quality VersionSet unavailable: {exc}") from exc


# ---------- Candidate proposal / WorkOrder 登记 ----------


@mcp.tool(name="candidate.create")
def candidate_create(
    case_id: str,
    worker_id: str,
    fencing_token: int,
    channel: str,
    attribution_report_digest: str,
    base_versionset_id: str,
    base_versionset_digest: str,
    base_revision: int,
    target_prompt_digest: str,
    content: dict[str, Any],
    idempotency_key: str,
) -> dict[str, Any]:
    """Submit a single-variable repair proposal to Release Controller.

    This MCP has no Quality write credential.  It computes the immutable
    proposal digest and calls the controller, which alone may create a draft
    VersionSet after checking the authoritative attribution and active base.
    """

    if channel != "prompt":
        raise validation("Phase 1 candidate.create currently supports the attributed prompt channel only")
    if not isinstance(base_revision, int) or isinstance(base_revision, bool) or base_revision <= 0:
        raise validation("base_revision must be a positive integer")
    worker_id = _bound_worker_id(worker_id)
    if not isinstance(fencing_token, int) or isinstance(fencing_token, bool) or fencing_token <= 0:
        raise validation("fencing_token must be a positive integer")
    if not isinstance(idempotency_key, str) or len(idempotency_key) < 8:
        raise validation("idempotency_key must contain at least 8 characters")
    proposal = {
        "case_id": case_id,
        "channel": channel,
        "attribution_report_digest": attribution_report_digest,
        "base_versionset_id": base_versionset_id,
        "base_versionset_digest": base_versionset_digest,
        "base_revision": base_revision,
        "target_prompt_digest": target_prompt_digest,
        "content": content,
    }
    proposal_digest = params_digest(proposal)
    with session_scope() as session:
        AuditService(session).record(
            actor="repairer",
            action="candidate.proposal.submit",
            target=case_id,
            params={
                "proposal_digest": proposal_digest,
                "base_versionset_id": base_versionset_id,
                "base_revision": base_revision,
                "idempotency_key": idempotency_key,
            },
            result="pending",
        )
    try:
        return _cp().post(
            "/v1/release-candidates",
            json_body={
                **proposal,
                "worker_id": worker_id,
                "fencing_token": fencing_token,
                "proposal_digest": proposal_digest,
                "idempotency_key": idempotency_key,
            },
        )
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"release candidate controller unreachable: {exc}") from exc


@mcp.tool(name="workorder.draft")
def workorder_draft(
    case_id: str,
    target: dict[str, Any],
    input_versions: dict[str, str],
    diff: dict[str, Any],
    single_factor_declaration: str,
    base_versionset_digest: Optional[str] = None,
    target_versionset_digest: Optional[str] = None,
    target_versionset_id: Optional[str] = None,
    target_revision: Optional[int] = None,
    created_by: str = "repairer",
) -> dict[str, Any]:
    """起草 WorkOrder（ACL：修复师）。target={app,layer}，layer∈prompt|kb|model；
    单变量纪律：一份工单只允许改动一个通道。返回 {workorder_id, status:DRAFT}。"""
    created_by = _bound_worker_id(created_by)
    layer = (target or {}).get("layer", "")
    channel = _LAYER_TO_CHANNEL.get(layer)
    if channel is None:
        raise validation("target.layer must be one of prompt|kb|model")
    if not isinstance(input_versions, dict) or not all(
        k in input_versions for k in ("prompt_digest", "kb_manifest_digest", "model_digest")
    ):
        raise validation("input_versions requires prompt_digest/kb_manifest_digest/model_digest")
    if "digest" not in (diff or {}):
        raise validation("diff requires digest (sha256 of payload content)")
    if "content" not in diff and "content_ref" not in diff:
        raise validation("diff requires content or content_ref")
    if not target_versionset_id or not isinstance(target_revision, int) or target_revision <= 0:
        raise validation(
            "workorder draft requires target_versionset_id and positive target_revision for gate binding"
        )
    if not target_versionset_digest:
        raise validation("workorder draft requires the exact target_versionset_digest")

    workorder_id = new_workorder_id()
    with session_scope() as session:
        AuditService(session).record(
            actor=created_by,
            action="workorder.draft",
            target=workorder_id,
            params={"case_id": case_id, "channel": channel},
            result="success",
        )
        session.add(
            WorkOrderDraft(
                workorder_id=workorder_id,
                case_id=case_id,
                channel=channel,
                status="DRAFT",
                created_by=created_by,
                draft_payload={
                    "case_id": case_id,
                    "target": target,
                    "input_versions": input_versions,
                    "diff": diff,
                    "single_factor_declaration": single_factor_declaration,
                    "base_versionset_digest": base_versionset_digest,
                    "target_versionset_digest": target_versionset_digest,
                    "target_versionset_id": target_versionset_id,
                    "target_revision": target_revision,
                },
            )
        )
    return {"workorder_id": workorder_id, "status": "DRAFT", "channel": channel}


@mcp.tool(name="gate.submit")
def gate_submit(
    workorder_id: str,
    eval_id: str,
    report_hash: str,
    gatekeeper: str = "gatekeeper",
) -> dict[str, Any]:
    """提交门禁报告（ACL：守门员）。从 eval-runner 读取报告，overall_status 必须 passed，
    否则 GATE_FAILED（WorkOrder 不得进入审批）。"""
    gatekeeper = _bound_worker_id(gatekeeper)
    if len(report_hash) != 64 or any(c not in "0123456789abcdef" for c in report_hash.lower()):
        raise validation("report_hash must be 64 lowercase hex")
    with session_scope() as session:
        draft = session.get(WorkOrderDraft, workorder_id)
        if draft is None:
            raise not_found(f"workorder {workorder_id} not found")
        run = session.get(EvalRun, eval_id)
        if run is None or run.report is None or run.status != "completed":
            raise validation("gate report not found for eval_id+report_hash")
        if run.workorder_id != workorder_id:
            raise validation("gate report belongs to a different workorder_id")
        recomputed = _gate_report_hash(run.report)
        if run.report_hash != report_hash or recomputed != report_hash:
            raise validation("gate report content hash mismatch")
        dp = draft.draft_payload or {}
        if run.target_versionset_id != dp.get("target_versionset_id"):
            raise validation("gate target_versionset_id does not match WorkOrder draft")
        if run.target_revision != dp.get("target_revision"):
            raise validation("gate target_revision does not match WorkOrder draft")
        if (run.report.get("subject") or {}).get("target_versionset_digest") != dp.get("target_versionset_digest"):
            raise validation("gate target digest does not match WorkOrder draft")
        statuses = [
            (run.report.get("rule_track") or {}).get("status"),
            (run.report.get("judge_track") or {}).get("status"),
            (run.report.get("deterministic_tests") or {}).get("status"),
            (run.report.get("live_provider_e2e") or {}).get("status"),
        ]
        status = run.report.get("overall_status")
        if status != "passed" or any(item != "passed" for item in statuses):
            raise McpError(GATE_FAILED, f"gate report status={status} tracks={statuses}; all must be passed")

        try:
            authoritative = _cp().get(f"/v1/gate-reports/{eval_id}")
        except McpError as exc:
            raise exc
        except Exception as exc:  # noqa: BLE001
            raise dependency_unavailable(f"gate controller unreachable: {exc}") from exc
        if (
            authoritative.get("report_hash") != report_hash
            or authoritative.get("workorder_id") != workorder_id
            or authoritative.get("candidate_digest") != run.candidate_digest
            or authoritative.get("evidence_digest") != run.evidence_digest
            or authoritative.get("overall_status") != "passed"
        ):
            raise validation("authoritative GateReport binding mismatch")
        draft.gate_report_ref = f"eval://{eval_id}"
        draft.gate_report_digest = f"sha256:{report_hash}"
        AuditService(session).record(
            actor=gatekeeper,
            action="workorder.gate_attached",
            target=workorder_id,
            params={"eval_id": eval_id, "report_hash": report_hash, "gate_status": "passed"},
            result="success",
        )
    return {
        "workorder_id": workorder_id,
        "gate_report_ref": f"eval://{eval_id}",
        "gate_report_digest": f"sha256:{report_hash}",
        "gate_status": "passed",
    }


@mcp.tool(name="workorder.freeze")
def workorder_freeze(workorder_id: str, fencing_token: int) -> dict[str, Any]:
    """定稿 WorkOrder（ACL：修复师，写操作须带 fencing_token 透传）。前置：gate.submit 已过。
    计算 hash 后登记到控制面（POST /v1/workorders），FROZEN 后任何字段不可改。"""
    if not isinstance(fencing_token, int) or isinstance(fencing_token, bool) or fencing_token <= 0:
        raise validation("fencing_token must be a positive integer")

    with session_scope() as session:
        draft = session.scalar(
            select(WorkOrderDraft)
            .where(WorkOrderDraft.workorder_id == workorder_id)
            .with_for_update()
        )
        if draft is None:
            raise not_found(f"workorder {workorder_id} not found")
        if draft.status == "FROZEN":
            return {
                "workorder_id": workorder_id,
                "hash": draft.hash,
                "status": "FROZEN",
                "duplicate": True,
            }
        if draft.status == "DRAFT":
            if not draft.gate_report_ref or not draft.gate_report_digest:
                raise McpError(GATE_FAILED, "gate report must be attached before freeze")
            payload = _build_workorder_payload(draft, session)
            # hash 绑定全部字段（含 hash_rule，不含 hash 自身；control-plane 同口径）
            payload["hash_rule"] = "jcs-rfc8785+sha256"
            payload["hash"] = workorder_hash(payload)
            # Commit an immutable local intent before crossing the network.
            # A lost response must retry this exact nonce/timestamps/hash, never
            # manufacture another WorkOrder under the same id.
            draft.status = "FREEZE_PENDING"
            draft.frozen_payload = payload
            draft.hash = payload["hash"]
            AuditService(session).record(
                actor=draft.created_by,
                action="workorder.freeze.intent",
                target=workorder_id,
                params={
                    "hash": payload["hash"],
                    "workorder_hash": payload["hash"],
                    "fencing_token": fencing_token,
                },
                result="pending",
            )
        elif draft.status == "FREEZE_PENDING":
            payload = dict(draft.frozen_payload or {})
            if (
                not payload
                or draft.hash != payload.get("hash")
                or workorder_hash(payload) != draft.hash
            ):
                raise validation("persisted WorkOrder freeze intent failed integrity validation")
        else:
            raise validation(f"workorder state {draft.status} cannot be frozen")
        case_id = draft.case_id
        worker_id = draft.created_by

    # 登记到控制面（权威 WorkOrder 留档 + changeset 创建）。控制面会在同一
    # transaction 内锁定 Case lease、验证 owner/token，再写入 WorkOrder。
    try:
        reg = _cp().post(
            "/v1/workorders",
            json_body={
                "workorder": payload,
                "worker_id": worker_id,
                "fencing_token": fencing_token,
            },
        )
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"release controller unreachable: {exc}") from exc

    # HTTP 2xx alone is not authority.  Freeze the local draft only after the
    # controller echoes the exact immutable identity it persisted.  A missing,
    # malformed, or lost receipt leaves the immutable FREEZE_PENDING intent for
    # exact retry/reconciliation.
    if (
        not isinstance(reg, dict)
        or reg.get("workorder_id") != workorder_id
        or reg.get("hash") != payload["hash"]
        or not isinstance(reg.get("duplicate"), bool)
    ):
        raise dependency_unavailable("release controller returned an invalid WorkOrder receipt")

    with session_scope() as session:
        draft = session.scalar(
            select(WorkOrderDraft)
            .where(WorkOrderDraft.workorder_id == workorder_id)
            .with_for_update()
        )
        if draft is None:
            raise not_found(f"workorder {workorder_id} disappeared during freeze")
        if (
            draft.hash != payload["hash"]
            or draft.frozen_payload != payload
            or draft.status not in ("FREEZE_PENDING", "FROZEN")
        ):
            raise validation("WorkOrder freeze intent changed during controller registration")
        if draft.status == "FROZEN":
            return {
                "workorder_id": workorder_id,
                "hash": draft.hash,
                "status": "FROZEN",
                "duplicate": True,
                "case_id": draft.case_id,
            }
        draft.status = "FROZEN"
        AuditService(session).record(
            actor=draft.created_by,
            action="workorder.freeze",
            target=workorder_id,
            params={"hash": payload["hash"], "workorder_hash": payload["hash"]},
            result="success",
        )
    return {
        "workorder_id": workorder_id,
        "hash": payload["hash"],
        "status": "FROZEN",
        "duplicate": reg["duplicate"],
        "case_id": payload["case_id"],
    }


@mcp.tool(name="workorder.get")
def workorder_get(workorder_id: str) -> dict[str, Any]:
    """读 WorkOrder（ACL：全员）：全量 + hash + 状态。"""
    with session_scope() as session:
        draft = session.get(WorkOrderDraft, workorder_id)
        if draft is None:
            raise not_found(f"workorder {workorder_id} not found")
        return {
            "workorder_id": workorder_id,
            "case_id": draft.case_id,
            "channel": draft.channel,
            "status": draft.status,
            "hash": draft.hash,
            "draft": draft.draft_payload,
            "frozen_payload": draft.frozen_payload,
            "gate_report_ref": draft.gate_report_ref,
            "gate_report_digest": draft.gate_report_digest,
            "created_by": draft.created_by,
        }


# ---------- 审批 ----------


@mcp.tool(name="approval.request")
def approval_request(
    workorder_id: str,
    evidence_summary: str,
    channel: str = "feishu",
) -> dict[str, Any]:
    """提请审批（ACL：守门员）。前置校验 GATE_PASSED（FROZEN 且门禁已过），否则 GATE_FAILED。
    经控制面 approval-request 迁移 changeset；返回 {approval_id, status:pending}。"""
    with session_scope() as session:
        draft = session.get(WorkOrderDraft, workorder_id)
        if draft is None:
            raise not_found(f"workorder {workorder_id} not found")
        if draft.status != "FROZEN" or not draft.frozen_payload:
            raise McpError(GATE_FAILED, "workorder not frozen; gate must pass before approval request")
        if not draft.gate_report_ref:
            raise McpError(GATE_FAILED, "gate report missing")
        fp = draft.frozen_payload
        workorder_hash_val = draft.hash
        nonce = fp.get("nonce")
        expiry = fp.get("expiry")
        if not workorder_hash_val or not nonce or not expiry:
            raise validation("frozen workorder missing hash/nonce/expiry")

        approval_id = new_approval_id()
        # 控制面 changeset 迁移（权威状态）：DRAFTED → GATE_ATTACHED → AWAITING_APPROVAL
        cs_id = f"cs_{workorder_id}"
        try:
            # 1) 附门禁（幂等：已 GATE_ATTACHED 时忽略 illegal_transition）
            try:
                _cp().post(
                    f"/v1/changesets/{cs_id}/gate",
                    json_body={
                        "eval_id": draft.gate_report_ref.removeprefix("eval://"),
                        "report_hash": draft.gate_report_digest.removeprefix("sha256:"),
                    },
                )
            except McpError as exc:
                if exc.error_code != "STATE_CONFLICT":
                    raise exc
            # 2) 提请审批
            _cp().post(
                f"/v1/changesets/{cs_id}/approval-request",
                json_body={
                    "workorder_hash": workorder_hash_val,
                    "nonce": nonce,
                    "expiry": expiry,
                    "channel": channel,
                },
            )
        except McpError as exc:
            raise exc
        except Exception as exc:  # noqa: BLE001
            raise dependency_unavailable(f"release controller unreachable: {exc}") from exc

        session.add(
            ApprovalRequest(
                approval_id=approval_id,
                workorder_id=workorder_id,
                workorder_hash=workorder_hash_val,
                nonce=nonce,
                status="pending",
                evidence_summary=evidence_summary,
                channel=channel,
            )
        )
        AuditService(session).record(
            actor="gatekeeper",
            action="workorder.approval_requested",
            target=approval_id,
            params={"workorder_id": workorder_id, "workorder_hash": workorder_hash_val, "channel": channel},
            result="success",
        )
    return {"approval_id": approval_id, "status": "pending", "workorder_id": workorder_id}


@mcp.tool(name="approval.status")
def approval_status(approval_id: str) -> dict[str, Any]:
    """审批状态查询（ACL：全员）：{status, decided_by, decided_at}。状态以控制面 approvals 为准，
    本地提请记录兜底 pending。"""
    status = "pending"
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    with session_scope() as session:
        req = session.get(ApprovalRequest, approval_id)
        if req is not None:
            status = req.status
        # 控制面审批授权表（权威）
        try:
            row = session.execute(
                text(
                    "SELECT status, decision, approver, decided_at, expiry "
                    "FROM approvals WHERE approval_id=:aid"
                ),
                {"aid": approval_id},
            ).mappings().first()
            if row is not None:
                status = row["status"] or status
                approver = row["approver"] or {}
                decided_by = approver.get("identity") if isinstance(approver, dict) else None
                decided_at = str(row["decided_at"]) if row["decided_at"] else None
        except Exception:  # noqa: BLE001  control-plane approvals 表可能未迁移
            pass
    return {
        "approval_id": approval_id,
        "status": status,
        "decided_by": decided_by,
        "decided_at": decided_at,
    }


# ---------- 发布进度（只读旁观） ----------


@mcp.tool(name="release.get")
def release_get(release_id: Optional[str] = None, case_id: Optional[str] = None) -> dict[str, Any]:
    """Release 状态机现状 + operation 对账信息（ACL：全员）。按 release_id 或 case_id 查询。"""
    if release_id:
        try:
            return _cp().get(f"/v1/releases/{release_id}")
        except McpError as exc:
            raise exc
    if case_id:
        try:
            engine = get_engine()
            with engine.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT aggregate_id, state, revision, payload FROM aggregates "
                        "WHERE aggregate_type='release' AND payload->>'case_id'=:cid"
                    ),
                    {"cid": case_id},
                ).mappings().all()
            if not rows:
                raise not_found(f"no release for case {case_id}")
            return {
                "releases": [
                    {"release_id": r["aggregate_id"], "state": r["state"], "revision": r["revision"]}
                    for r in rows
                ],
            }
        except McpError as exc:
            raise exc
        except Exception as exc:  # noqa: BLE001
            raise dependency_unavailable(f"release controller unreachable: {exc}") from exc
    raise validation("release_id or case_id required")


# ---------- 内部 ----------


def _build_workorder_payload(draft: WorkOrderDraft, session: Any) -> dict[str, Any]:
    """由 draft + 门禁引用构建完整 WorkOrder payload（含 nonce/expiry；hash 由调用方补算）。"""
    dp = draft.draft_payload
    now = datetime.now(timezone.utc)
    base_digest = dp.get("base_versionset_digest") or _derive_digest(dp.get("input_versions", {}))
    target_digest = dp.get("target_versionset_digest") or _derive_target_digest(dp)
    gate_digest = draft.gate_report_digest  # sha256:<hex>
    if gate_digest.startswith("sha256:"):
        gate_digest_hex = gate_digest[len("sha256:"):]
    else:
        gate_digest_hex = gate_digest
    return {
        "schema_version": "0.1.0",
        "workorder_id": draft.workorder_id,
        "case_id": draft.case_id,
        "channel": draft.channel,
        "base_versionset_digest": base_digest,
        "target_versionset_digest": target_digest,
        "input_versions": dp["input_versions"],
        "diff": dp["diff"],
        "gate_report_ref": {
            "uri": draft.gate_report_ref,
            "digest": f"sha256:{gate_digest_hex}",
        },
        "expiry": (now + timedelta(minutes=_settings().approval_ttl_minutes)).isoformat(),
        "nonce": new_nonce(),
        "created_at": now.isoformat(),
        "created_by": draft.created_by,
    }


def _derive_digest(input_versions: dict[str, Any]) -> str:
    try:
        data = jcs_subset(input_versions)
    except (ValueError, TypeError):
        data = str(input_versions).encode("utf-8")
    return f"sha256:{sha256_hex(data)}"


def _derive_target_digest(dp: dict[str, Any]) -> str:
    input_digest = _derive_digest(dp.get("input_versions", {}))
    diff_digest = (dp.get("diff") or {}).get("digest", "sha256:" + "0" * 64)
    combined = f"{input_digest}|{diff_digest}".encode("utf-8")
    return f"sha256:{sha256_hex(combined)}"


def _gate_report_hash(report: dict[str, Any]) -> str:
    body = {key: value for key, value in report.items() if key != "report_hash"}
    try:
        data = jcs_subset(body)
    except (ValueError, TypeError):
        data = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return sha256_hex(data)


def _profiled_mcp(profile: str) -> FastMCP:
    profiles = {
        "repairer": {
            "versionset.list": versionset_list,
            "versionset.get": versionset_get,
            "candidate.create": candidate_create,
            "workorder.draft": workorder_draft,
            "workorder.freeze": workorder_freeze,
            "workorder.get": workorder_get,
            "release.get": release_get,
        },
        "gatekeeper": {
            "workorder.get": workorder_get,
            "gate.submit": gate_submit,
            "approval.request": approval_request,
            "approval.status": approval_status,
            "release.get": release_get,
        },
    }
    return build_tool_projection("mcp-release-admin", profile, profiles)


def main() -> None:
    import uvicorn

    s = _settings()
    validate_projection_runtime(
        s,
        profile_workers={"repairer": "repairer", "gatekeeper": "gatekeeper"},
        role_token_profiles=frozenset({"repairer", "gatekeeper"}),
    )
    uvicorn.run(
        build_server_app(
            _profiled_mcp(s.mcp_tool_profile),
            expected_consumer=s.mcp_expected_consumer,
            gateway_backend_token=s.mcp_gateway_backend_token,
        ),
        host=s.host,
        port=s.release_admin_port,
        log_level=s.log_level.lower(),
    )


if __name__ == "__main__":
    main()
