"""mcp-eval-runner：门禁评测触发/报告 + 归因实验（spec §9.5）。

- gate.run/report：确定性门禁（规则轨+裁判轨分开报告；live 轨不可达标 UNAVAILABLE/skipped）。
- experiment.*：包装 control-plane /v1/experiments。
- experiment.execute：后台线程驱动 eval-harness 执行完整 5-cell 实验（S0-006 修复）。
- 裁判模型 ≠ 运动员模型（T6 硬约束，digest 不同）。
"""
import logging
import base64
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.audit import AuditService  # noqa: E402
from common.config import Settings, get_settings  # noqa: E402
from common.db import session_scope  # noqa: E402
from common.errors import McpError, dependency_unavailable, forbidden, not_found, validation  # noqa: E402
from common.http import HttpClient  # noqa: E402
from common.ids import new_eval_id  # noqa: E402
from common.jcs import jcs_subset, sha256_hex  # noqa: E402
from common.serverkit import (  # noqa: E402
    ToolDefinitionRegistry,
    build_server_app,
    build_tool_projection,
    validate_projection_runtime,
)
from common.tables import EvalRun, WorkOrderDraft  # noqa: E402

# eval-harness 执行机（monorepo 源码依赖，见 requirements.txt：-e ../eval-harness）。
# 复用完整 5-cell 执行→聚合→裁决→报告链路，禁止重造。
from eval_harness.client import QualityAPIClient  # noqa: E402
from eval_harness.config import Settings as EvalHarnessSettings  # noqa: E402
from eval_harness.digests import canonical_json_bytes, sha256_digest  # noqa: E402
from eval_harness.experiment import ImmutableVersionSetDriver, ExperimentRunner, ProbeRun  # noqa: E402
from eval_harness.gate import GateCandidate, GateRunner, LLMJudge, build_error_gate_report  # noqa: E402
from eval_harness.gate_executor import (  # noqa: E402
    CommandSuiteRunner,
    frozen_gate_suite_digest,
    write_json_artifact,
)
from eval_harness.models import ExperimentPlan  # noqa: E402
from eval_harness.probe_loader import frozen_digest, load_probe_set  # noqa: E402

logger = logging.getLogger(__name__)

mcp = ToolDefinitionRegistry("mcp-eval-runner")
_execution_threads: dict[str, threading.Thread] = {}
_execution_threads_lock = threading.Lock()

def _settings() -> Settings:
    return get_settings()


def _cp() -> HttpClient:
    s = _settings()
    return HttpClient(s.control_plane_base_url, token=s.control_plane_role_token)


def _gate_cp() -> HttpClient:
    s = _settings()
    return HttpClient(s.control_plane_base_url, token=s.gate_authority_token)


def _qa() -> HttpClient:
    """Quality read surface only; this server never receives a write token."""

    s = _settings()
    return HttpClient(s.quality_api_base_url, token=s.quality_read_token)


def _bound_worker_id(supplied: str | None = None) -> str:
    canonical = _settings().mcp_worker_id
    if not canonical:
        raise forbidden("MCP process has no canonical worker identity")
    if supplied is not None and supplied != canonical:
        raise forbidden("caller-supplied runner_id does not match authenticated MCP projection")
    return canonical


# ---------- immutable VersionSet context ----------


@mcp.tool(name="versionset.list")
def versionset_list(status: str | None = None, limit: int = 50) -> dict[str, Any]:
    """List authoritative Quality VersionSets for protocol construction (read-only)."""

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
    """Read exact id/digest/revision and component content without mutation."""

    if not isinstance(versionset_id, str) or not versionset_id.strip():
        raise validation("versionset_id must be non-empty")
    try:
        return _qa().get(f"/v2/versionsets/{versionset_id}")
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"Quality VersionSet unavailable: {exc}") from exc


# ---------- 门禁评测 ----------


@mcp.tool(name="gate.run")
def gate_run(workorder_id: str, suite_digest: str = "") -> dict[str, Any]:
    """Run the real allowlisted gate suites and persist the fail-closed result.

    This synchronous implementation returns `completed`; it never claims queued while already
    completed. Provider/test timeouts are persisted as an ERROR GateReport.
    """
    with session_scope() as session:
        draft = session.get(WorkOrderDraft, workorder_id)
        if draft is None:
            raise not_found(f"workorder {workorder_id} not found")
        if draft.status != "DRAFT":
            raise validation(f"workorder state {draft.status} cannot enter gate")
        context = dict(draft.draft_payload or {})
    return _run_and_register_gate(
        workorder_id=workorder_id,
        context=context,
        suite_digest=suite_digest,
        audit_action="gate.run",
    )


@mcp.tool(name="gate.run_verification")
def gate_run_verification(release_id: str, suite_digest: str = "") -> dict[str, Any]:
    """Run and attach the real post-canary Gate against the frozen release target.

    The target and final WorkOrder binding come only from Release Controller's
    verification context; this path never depends on a mutable local draft.
    """

    try:
        context = _cp().get(f"/v1/releases/{release_id}/verification-context")
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"release verification context unavailable: {exc}") from exc
    workorder_id = context.get("workorder_id")
    if not isinstance(workorder_id, str) or not workorder_id:
        raise validation("release verification context omitted workorder_id")
    result = _run_and_register_gate(
        workorder_id=workorder_id,
        context=context,
        suite_digest=suite_digest,
        audit_action="gate.run_verification",
    )
    try:
        receipt = _cp().post(
            f"/v1/releases/{release_id}/verification",
            json_body={
                "eval_id": result["eval_id"],
                "report_hash": result["report_hash"],
            },
        )
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"release verification registration failed: {exc}") from exc
    return {**result, "release_id": release_id, "verification_receipt": receipt}


@mcp.tool(name="gate.report")
def gate_report(eval_id: str) -> dict[str, Any]:
    """读门禁双轨报告（ACL：全员）：deterministic/live 分列 + verdict + report_hash。"""
    with session_scope() as session:
        run = session.get(EvalRun, eval_id)
        if run is None:
            raise not_found(f"eval {eval_id} not found")
        if run.status != "completed" or run.report is None:
            return {"eval_id": eval_id, "status": run.status, "report": None}
        report = run.report
        recomputed = _report_hash(report)
        if recomputed != run.report_hash:
            raise validation(f"persisted gate report hash mismatch for {eval_id}")
        return {
            "eval_id": eval_id,
            "status": run.status,
            "verdict": report.get("overall_status"),
            "report_hash": run.report_hash,
            "report": report,
        }


# ---------- 归因实验（包装 control-plane） ----------


@mcp.tool(name="experiment.plan")
def experiment_plan(case_id: str, matrix: str = "5cell") -> dict[str, Any]:
    """归因实验计划（ACL：归因师）。matrix ∈ 5cell|full2x2x2；返回 {experiment_id} PLANNED。

    此步骤只创建计划；随后 probe.freeze 必须把六个 component digests、五个精确
    VersionSet 引用与 probes 一并冻结，execute 只能读取这些冻结版本。"""
    if matrix not in ("5cell", "full2x2x2"):
        raise validation("matrix must be 5cell|full2x2x2")
    protocol_version = "five_cell-v1" if matrix == "5cell" else "full_factorial-v1"
    try:
        return _cp().post(
            "/v1/experiments",
            json_body={"case_id": case_id, "protocol_version": protocol_version},
        )
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"experiment controller unreachable: {exc}") from exc


@mcp.tool(name="experiment.run")
def experiment_run(
    experiment_id: str,
    lease_id: str,
    fencing_token: int,
    runner_id: str = "eval-runner",
) -> dict[str, Any]:
    """启动实验（ACL：归因师）。

    runner_id 必须逐字等于此前 `case.claim(worker_id=...)` 的 worker_id；控制面会同时
    核验 lease_id、owner 与 fencing_token，任何一个不匹配均 fail closed。
    """
    runner_id = _bound_worker_id(runner_id)
    try:
        return _cp().post(
            f"/v1/experiments/{experiment_id}/start",
            json_body={
                "runner_id": runner_id,
                "lease_id": lease_id,
                "fencing_token": fencing_token,
            },
        )
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"experiment controller unreachable: {exc}") from exc


@mcp.tool(name="experiment.report")
def experiment_report(experiment_id: str) -> dict[str, Any]:
    """读实验报告（ACL：全员）：原始计数 + Δ + CI + 裁决（§4.7）。"""
    try:
        return _cp().get(f"/v1/experiments/{experiment_id}")
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"experiment controller unreachable: {exc}") from exc


@mcp.tool(name="experiment.execute")
def experiment_execute(experiment_id: str) -> dict[str, Any]:
    """驱动 5-cell 归因实验执行（ACL：归因师；后台异步，立即返回）。

    前置：实验必须处于 RUNNING（已用 Case lease 调 experiment.run）。冻结协议三探针集、
    版本 digest 与五个精确 VersionSet 必须已冻结。

    执行模型：runner 就是你自己——本工具在后台线程里用 eval-harness ExperimentRunner
    跑完整 5-cell 执行→聚合→裁决→报告，逐 cell 回流 POST /v1/experiments/{id}/cells，
    完成后把完整 EvidenceBundle/AttributionReport 连同当前 fencing token 回流
    POST /verdict，由控制面重算统计与 R1–R5 裁决；任何异常回流
    POST /cancel 并在控制面留错误原因。调用本身立即返回 {status:executing}，不要轮询本
    工具；用 experiment.report 轮询直到 state=VERDICT_COMPUTED。
    """
    try:
        exp = _cp().get(f"/v1/experiments/{experiment_id}")
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"experiment controller unreachable: {exc}") from exc
    state = exp.get("state")
    if state != "RUNNING":
        raise validation(
            f"experiment {experiment_id} 当前状态 {state} 不可执行；前置必须是 RUNNING。"
            "正确顺序：experiment.plan → "
            "probe.freeze → GET 回读确认三探针集非空 → experiment.run → experiment.execute。"
        )
    payload = exp.get("payload") or {}
    _require_nonempty_probe_sets(payload)
    with _execution_threads_lock:
        existing = _execution_threads.get(experiment_id)
        if existing is not None and existing.is_alive():
            return {"status": "executing", "experiment_id": experiment_id}

        def run_tracked() -> None:
            try:
                _execute_experiment_background(experiment_id)
            finally:
                current = threading.current_thread()
                with _execution_threads_lock:
                    if _execution_threads.get(experiment_id) is current:
                        _execution_threads.pop(experiment_id, None)

        thread = threading.Thread(
            target=run_tracked,
            name=f"eval-execute-{experiment_id}",
            daemon=True,
        )
        _execution_threads[experiment_id] = thread
        thread.start()
    return {"status": "executing", "experiment_id": experiment_id}


def _wait_for_execution_thread(experiment_id: str, timeout: float = 2.0) -> bool:
    """Test/clean-shutdown hook: wait for a tracked worker to release module state."""

    with _execution_threads_lock:
        thread = _execution_threads.get(experiment_id)
    if thread is None:
        return True
    thread.join(timeout=max(0.0, timeout))
    return not thread.is_alive()


@mcp.tool(name="probe.freeze")
def probe_freeze(
    experiment_id: str,
    probe_set: dict[str, Any],
) -> dict[str, Any]:
    """冻结探针三分集（ACL：归因师；冻结后全员只读）。返回 {probe_set_digest}。

    probe_set 必须顶层平铺三组 probes、repetitions、六个 component digests，以及
    C/RP/RK/RM/G 五个精确 VersionSet 引用。"""
    _validate_probe_set_structure(probe_set)
    try:
        repository_probes = load_probe_set(EvalHarnessSettings().repo_root)
        selected = [
            *probe_set.get("discovery", []),
            *probe_set.get("hidden_confirmation", []),
            *probe_set.get("unaffected_controls", []),
        ]
        repository_probes.subset(selected)
        digest = frozen_digest(repository_probes)
        _seed_from_ref(str(probe_set.get("random_seed_ref") or ""), experiment_id)
    except (KeyError, ValueError, TypeError) as exc:
        raise validation(f"probe_set 与仓库冻结探针/随机种子不一致：{exc}") from exc
    body = {
        "execution_profile": probe_set.get("execution_profile", "live"),
        "probe_set_digest": digest,
        "discovery": probe_set.get("discovery", []),
        "hidden_confirmation": probe_set.get("hidden_confirmation", []),
        "unaffected_controls": probe_set.get("unaffected_controls", []),
        "repetitions": int(probe_set.get("repetitions", 1)),
        "versions": probe_set.get("versions", {}),
        "cell_versionsets": probe_set.get("cell_versionsets", {}),
        "random_seed_ref": probe_set.get("random_seed_ref", ""),
        "confidence": probe_set.get("confidence", 0.95),
    }
    try:
        result = _cp().post(f"/v1/experiments/{experiment_id}/protocol", json_body=body)
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"experiment controller unreachable: {exc}") from exc
    result["probe_set_digest"] = digest
    return result


# ---------- 内部：probe.freeze / experiment.execute 校验 ----------

# probe_set 顶层必须存在的冻结键。
_REQUIRED_PROBE_KEYS = ("discovery", "hidden_confirmation", "unaffected_controls")

# 面向 LLM 的 probe_set 正确结构示例（错误消息里逐条教正确键名）。
_PROBE_SET_EXAMPLE = {
    "discovery": ["cs-001", "cs-002", "cs-003"],
    "hidden_confirmation": ["cs-004", "cs-005"],
    "unaffected_controls": ["cs-013", "cs-014", "cs-015", "cs-016"],
    "repetitions": 3,
    "versions": {"P0": "sha256:<...>", "P1": "sha256:<...>", "K0": "sha256:<...>", "K1": "sha256:<...>", "M0": "sha256:<...>", "M1": "sha256:<...>"},
    "cell_versionsets": {"C": {"versionset_id": "vs_<...>", "digest": "sha256:<...>", "revision": 1}},
}


def _validate_probe_set_structure(probe_set: dict[str, Any]) -> None:
    """probe.freeze 结构校验：三探针集必须存在且为非空数组，杜绝空实验静默冻结。

    错误消息即操作手册：说清缺哪个键/空哪个集，并给正确结构示例（顶层平铺，勿嵌套）。
    """
    problems: list[str] = []
    for key in _REQUIRED_PROBE_KEYS:
        value = probe_set.get(key)
        if not isinstance(value, list):
            problems.append(f"{key} 缺失或不是数组（当前值：{value!r}）")
        elif not value:
            problems.append(f"{key} 为空数组")
    if not isinstance(probe_set.get("versions"), dict):
        problems.append("versions 缺失或不是对象")
    if not isinstance(probe_set.get("cell_versionsets"), dict):
        problems.append("cell_versionsets 缺失或不是对象")
    if problems:
        raise validation(
            "probe_set 结构错误："
            + "；".join(problems)
            + "。正确结构是顶层平铺四个键（discovery / hidden_confirmation / "
            "unaffected_controls / repetitions），不要把探针集再嵌套一层："
            + f"{_PROBE_SET_EXAMPLE}"
            + "。版本及精确 VersionSet 绑定不可省略。"
        )


def _require_nonempty_probe_sets(payload: dict[str, Any]) -> None:
    """experiment.execute 前置校验：冻结协议三探针集非空，空实验没有可执行内容。"""
    empty = [name for name in _REQUIRED_PROBE_KEYS if not payload.get(name)]
    if empty:
        raise validation(
            "experiment.execute 前置校验失败：冻结协议三探针集 "
            f"{'、'.join(empty)} 为空，无法执行（空实验没有可跑的内容）。"
            "请先用 probe.freeze 冻结非空探针集——正确结构是顶层平铺："
            + f"{_PROBE_SET_EXAMPLE}"
            + "。若已冻结，请 GET /v1/experiments/{id} 回读 payload 确认三探针集非空。"
        )


# ---------- 内部：experiment.execute 后台执行 ----------


def _execute_experiment_background(experiment_id: str) -> None:
    """后台执行线程：跑完整 5-cell 实验并回流 cells/verdict 到控制面。

    复用 eval-harness ExperimentRunner（执行→聚合→裁决→报告全链路）；逐 cell 回流
    POST /cells，完成回流 POST /verdict，任何异常回流 POST /cancel 并留错误原因——
    绝不让实验悬挂在 RUNNING。
    """
    try:
        exp = _cp().get(f"/v1/experiments/{experiment_id}")
        trial_receipts = _cp().get(f"/v1/experiments/{experiment_id}/trials")
        payload = exp.get("payload") or {}
        # 前置校验（与 execute 工具一致；线程内数据可能更新，重复校验一次）。
        _require_nonempty_probe_sets(payload)
        plan, probe_set, eh_settings = _build_execution_context(experiment_id, payload)
        mcp_settings = _settings()
        evidence_root = Path(mcp_settings.experiment_evidence_dir)
        if not evidence_root.is_absolute():
            evidence_root = eh_settings.repo_root / evidence_root
        heartbeat_interval = max(
            1.0, float(mcp_settings.experiment_heartbeat_interval_seconds)
        )

        def heartbeat() -> None:
            receipt = _cp().post(
                f"/v1/cases/{payload['case_id']}/heartbeat",
                json_body={
                    "worker_id": payload["runner_id"],
                    "fencing_token": payload["fencing_token"],
                },
            )
            if (
                receipt.get("lease_id") != payload.get("lease_id")
                or receipt.get("fencing_token") != payload.get("fencing_token")
            ):
                raise RuntimeError("Case lease heartbeat receipt does not match frozen runner lease")

        heartbeat()
        heartbeat_stop = threading.Event()
        heartbeat_errors: list[Exception] = []

        def heartbeat_loop() -> None:
            while not heartbeat_stop.wait(heartbeat_interval):
                try:
                    heartbeat()
                except Exception as exc:  # noqa: BLE001 - verdict must fail closed
                    heartbeat_errors.append(exc)
                    heartbeat_stop.set()
                    return

        heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name=f"eval-heartbeat-{experiment_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        client = QualityAPIClient(eh_settings)
        driver = ImmutableVersionSetDriver(
            {
                cell: payload["cell_versionsets"][cell]["versionset_id"]
                for cell in ("C", "RP", "RK", "RM", "G")
            }
        )
        prior_trials = _prior_trial_map(trial_receipts.get("items") or [])

        def checkpoint_trial(arm: str, run: ProbeRun) -> ProbeRun:
            """Persist a process-independent, provider-verified resume receipt."""

            parsed = urlparse(run.output_ref)
            if parsed.scheme != "file" or not parsed.path:
                raise RuntimeError("new attribution trial has no local immutable output artifact")
            artifact_path = Path(unquote(parsed.path))
            if artifact_path.stat().st_size > 2_000_000:
                raise RuntimeError("new attribution trial output exceeds 2 MB")
            raw = json.loads(artifact_path.read_text(encoding="utf-8"))
            if sha256_digest(raw) != run.output_digest:
                raise RuntimeError("new attribution trial output digest changed before checkpoint")
            encoded = base64.b64encode(canonical_json_bytes(raw)).decode("ascii")
            body = {
                "cell": arm,
                "probe_id": run.probe_id,
                "repetition": run.repetition,
                "recovered": run.recovered,
                "output_ref": "data:application/json;base64," + encoded,
                "output_digest": run.output_digest,
                "fencing_token": payload["fencing_token"],
            }
            receipt = _cp().post(
                f"/v1/experiments/{experiment_id}/trials",
                json_body=body,
            )
            authoritative = receipt.get("trial") or {}
            if any(
                authoritative.get(field) != value
                for field, value in body.items()
                if field != "fencing_token"
            ):
                raise RuntimeError("control-plane trial receipt does not match provider output")
            return ProbeRun(
                probe_id=run.probe_id,
                repetition=run.repetition,
                recovered=run.recovered,
                output_ref=authoritative["output_ref"],
                output_digest=authoritative["output_digest"],
                answer=run.answer,
            )
        try:
            result = ExperimentRunner(
                client,
                probe_set,
                eh_settings,
                artifact_dir=evidence_root,
                cell_versionset_refs=payload["cell_versionsets"],
                trial_callback=checkpoint_trial,
            ).run(
                plan,
                driver,
                seed=plan.random_seed,
                suppress_digest_capture=True,
                prior_trials=prior_trials,
            )
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=min(heartbeat_interval, 5.0))
        if heartbeat_errors:
            raise RuntimeError(f"Case lease heartbeat failed: {heartbeat_errors[0]}")
        experiment_dir = evidence_root / experiment_id
        bundle_ref = write_json_artifact(
            experiment_dir / "evidence-bundle.json", result.bundle
        )
        result.report["evidence_bundle_ref"] = bundle_ref
        write_json_artifact(
            experiment_dir / "attribution-report.json", result.report
        )

        # 逐 cell 回流（随机臂序，索引即 arm_order_index）。
        arm_order = _arm_order_from_bundle(result.bundle)
        for index, arm in enumerate(arm_order):
            cell = result.cells[arm]
            _cp().post(
                f"/v1/experiments/{experiment_id}/cells",
                json_body={
                    "cell": arm,
                    "arm_order_index": index,
                    "recovery_rate": _cell_recovery_rate(cell, plan),
                    "fencing_token": payload["fencing_token"],
                },
            )

        verdict = result.verdict.get("decision") or result.report["verdict"]["decision"]
        _cp().post(
            f"/v1/experiments/{experiment_id}/verdict",
            json_body={
                "fencing_token": payload["fencing_token"],
                "evidence_bundle": result.bundle,
                "attribution_report": result.report,
            },
        )
        logger.info("experiment.execute 完成 experiment_id=%s verdict=%s", experiment_id, verdict)
    except Exception as exc:  # noqa: BLE001
        logger.exception("experiment.execute 后台执行失败 experiment_id=%s", experiment_id)
        try:
            _cp().post(
                f"/v1/experiments/{experiment_id}/cancel",
                json_body={
                    "reason": f"eval-runner execute failed: {exc}",
                    "runner_id": payload.get("runner_id"),
                    "lease_id": payload.get("lease_id"),
                    "fencing_token": payload.get("fencing_token"),
                },
            )
        except Exception as cancel_exc:  # noqa: BLE001
            logger.error("experiment.execute 取消失败 experiment_id=%s: %s", experiment_id, cancel_exc)


def _prior_trial_map(items: list[dict[str, Any]]) -> dict[tuple[str, str, int], ProbeRun]:
    """Decode authoritative trial receipts returned by the control-plane read API."""

    result: dict[tuple[str, str, int], ProbeRun] = {}
    for item in items:
        cell = item.get("cell")
        probe_id = item.get("probe_id")
        repetition = item.get("repetition")
        recovered = item.get("recovered")
        output_ref = item.get("output_ref")
        output_digest = item.get("output_digest")
        if (
            cell not in {"C", "RP", "RK", "RM", "G"}
            or not isinstance(probe_id, str)
            or isinstance(repetition, bool)
            or not isinstance(repetition, int)
            or not isinstance(recovered, bool)
            or not isinstance(output_ref, str)
            or not output_ref.startswith("data:application/json;base64,")
            or not isinstance(output_digest, str)
            or not output_digest.startswith("sha256:")
        ):
            raise RuntimeError("control-plane returned a malformed completed trial receipt")
        key = (cell, probe_id, repetition)
        if key in result:
            raise RuntimeError(f"control-plane returned duplicate completed trial {key!r}")
        result[key] = ProbeRun(
            probe_id=probe_id,
            repetition=repetition,
            recovered=recovered,
            output_ref=output_ref,
            output_digest=output_digest,
        )
    return result


def _build_execution_context(experiment_id: str, payload: dict[str, Any]):
    """从冻结协议构造 eval-harness 执行上下文（ProbeSet + ExperimentPlan + 执行机 Settings）。

    探针定义取自 contracts/fixtures/probes-customer-service.yaml；实验只读取协议中冻结的
    精确 VersionSet。ExperimentRunner 捕获的响应 digest 必须与这些冻结引用一致。
    """
    eh_settings = EvalHarnessSettings()
    probe_set = load_probe_set(eh_settings.repo_root)
    plan = ExperimentPlan(
        experiment_id=experiment_id,
        case_id=payload.get("case_id") or experiment_id,
        matrix="five_cell",
        repetitions=int(payload.get("repetitions") or 1),
        confidence=eh_settings.experiment_confidence,
        delta_min=eh_settings.experiment_delta_min,
        probe_set_digest=payload.get("probe_set_digest", ""),
        version_digests=dict(payload.get("versions") or {}),
        discovery=list(payload.get("discovery") or []),
        hidden_confirmation=list(payload.get("hidden_confirmation") or []),
        unaffected_controls=list(payload.get("unaffected_controls") or []),
        random_seed=_seed_from_ref(str(payload.get("random_seed_ref") or ""), experiment_id),
    )
    if frozen_digest(probe_set) != plan.probe_set_digest:
        raise validation("执行时仓库 probe-set digest 已偏离冻结协议")
    probe_set.subset(
        [*plan.discovery, *plan.hidden_confirmation, *plan.unaffected_controls]
    )
    return plan, probe_set, eh_settings


def _seed_from_ref(random_seed_ref: str, experiment_id: str) -> int:
    match = re.fullmatch(rf"seed://{re.escape(experiment_id)}/([0-9]+)", random_seed_ref)
    if match is None:
        raise ValueError(
            f"random_seed_ref 必须是 seed://{experiment_id}/<non-negative-integer>"
        )
    seed = int(match.group(1))
    if seed < 0 or seed > 2**32 - 1:
        raise ValueError("random seed must be between 0 and 2^32-1")
    return seed


def _arm_order_from_bundle(bundle: dict) -> list[str]:
    """从 evidence-bundle 的 random_arm_order（arm@probe 平铺）还原实际臂序。"""
    order: list[str] = []
    for item in bundle.get("protocol", {}).get("random_arm_order", []):
        arm = str(item).split("@", 1)[0]
        if arm not in order:
            order.append(arm)
    return order


def _cell_recovery_rate(cell: Any, plan: ExperimentPlan) -> float:
    """单臂受影响探针恢复率（与 eval-harness 口径一致，∈[0,1]）。"""
    affected = set(plan.affected_probe_ids)
    runs = [r for r in cell.runs if r.probe_id in affected]
    if not runs:
        return 0.0
    return round(sum(1 for r in runs if r.recovered) / len(runs), 4)


# ---------- 内部：真实门禁执行 ----------


def _run_and_register_gate(
    *,
    workorder_id: str,
    context: dict[str, Any],
    suite_digest: str,
    audit_action: str,
) -> dict[str, Any]:
    """Execute one immutable target and register it with the Gate controller."""

    authoritative_suite_digest = frozen_gate_suite_digest(EvalHarnessSettings().repo_root)
    if suite_digest and suite_digest != authoritative_suite_digest:
        raise validation(
            "suite_digest does not match the repository-owned gate suite; omit it to use the authoritative digest"
        )
    suite_digest = authoritative_suite_digest
    eval_id = new_eval_id()
    _validate_gate_context(workorder_id, suite_digest, context)
    report, metadata = _execute_gate(eval_id, workorder_id, suite_digest, context)
    report = _inline_gate_artifacts(report)
    report_hash = _report_hash(report)
    evidence_digest = sha256_digest(report.get("artifact_refs") or [])
    candidate_fields = {
        "workorder_id": workorder_id,
        "target_versionset_id": context["target_versionset_id"],
        "target_versionset_digest": context["target_versionset_digest"],
        "target_revision": context["target_revision"],
        "dataset_id": metadata["dataset_id"],
        "dataset_version": metadata["dataset_version"],
        "dataset_digest": metadata["dataset_digest"],
        "regression_suite_digest": suite_digest,
        "evidence_digest": evidence_digest,
    }
    candidate_digest = sha256_digest(candidate_fields)

    with session_scope() as session:
        session.add(
            EvalRun(
                eval_id=eval_id,
                workorder_id=workorder_id,
                suite_digest=suite_digest,
                target_versionset_id=context["target_versionset_id"],
                target_revision=context["target_revision"],
                dataset_id=metadata["dataset_id"],
                dataset_version=metadata["dataset_version"],
                dataset_digest=metadata["dataset_digest"],
                evidence_digest=evidence_digest,
                candidate_digest=candidate_digest,
                status="completed",
                report=report,
                report_hash=report_hash,
            )
        )
        AuditService(session).record(
            actor="gatekeeper",
            action=audit_action,
            target=eval_id,
            params={"workorder_id": workorder_id, "suite_digest": suite_digest},
            result="success",
            evidence_refs={"evidence_digest": evidence_digest},
        )

    try:
        registered = _gate_cp().post(
            "/v1/gate-reports",
            json_body={
                "report": report,
                "report_hash": report_hash,
                "workorder_id": workorder_id,
                "target_versionset_id": context["target_versionset_id"],
                "target_revision": context["target_revision"],
                "dataset_id": metadata["dataset_id"],
                "dataset_version": metadata["dataset_version"],
                "evidence_digest": evidence_digest,
                "candidate_digest": candidate_digest,
            },
        )
    except McpError as exc:
        with session_scope() as session:
            run = session.get(EvalRun, eval_id)
            if run is not None:
                run.status = "registration_failed"
        raise exc
    except Exception as exc:  # noqa: BLE001
        with session_scope() as session:
            run = session.get(EvalRun, eval_id)
            if run is not None:
                run.status = "registration_failed"
        raise dependency_unavailable(f"gate controller registration failed: {exc}") from exc

    return {
        "eval_id": eval_id,
        "status": "completed",
        "verdict": report["overall_status"],
        "report_hash": report_hash,
        "candidate_digest": registered.get("candidate_digest", candidate_digest),
    }


def _inline_gate_artifacts(report: dict[str, Any]) -> dict[str, Any]:
    """Move live Gate evidence across the process boundary with exact digests."""

    replacements: dict[str, str] = {}
    refs: list[dict[str, str]] = []
    for ref in report.get("artifact_refs") or []:
        uri = str(ref.get("uri") or "")
        parsed = urlparse(uri)
        if parsed.scheme == "data":
            refs.append(ref)
            continue
        if parsed.scheme != "file" or not parsed.path:
            raise RuntimeError("live Gate artifact is not a local immutable file")
        path = Path(unquote(parsed.path)).resolve()
        payload = path.read_bytes()
        if len(payload) > 2_000_000:
            raise RuntimeError("live Gate artifact exceeds 2 MB")
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if digest != ref.get("digest"):
            raise RuntimeError("live Gate artifact digest changed before registration")
        inline_uri = "data:application/json;base64," + base64.b64encode(payload).decode("ascii")
        replacements[uri] = inline_uri
        refs.append({"uri": inline_uri, "digest": digest})
    report["artifact_refs"] = refs
    for track in (report.get("deterministic_tests") or {}, report.get("live_provider_e2e") or {}):
        for suite in track.get("suites") or []:
            old = suite.get("report_ref")
            if old in replacements:
                suite["report_ref"] = replacements[old]
    return report


def _validate_gate_context(workorder_id: str, suite_digest: str, context: dict[str, Any]) -> None:
    if not isinstance(suite_digest, str) or not suite_digest.startswith("sha256:") or len(suite_digest) != 71:
        raise validation("suite_digest must be sha256:<64 hex>")
    required = ("target_versionset_id", "target_versionset_digest", "target_revision")
    missing = [key for key in required if context.get(key) in (None, "")]
    if missing:
        raise validation(f"workorder {workorder_id} missing gate target binding: {missing}")
    if not isinstance(context["target_revision"], int) or context["target_revision"] <= 0:
        raise validation("target_revision must be a positive integer")
    digest = context["target_versionset_digest"]
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise validation("target_versionset_digest must be sha256:<64 hex>")


def _execute_gate(
    eval_id: str,
    workorder_id: str,
    suite_digest: str,
    context: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    settings = _settings()
    eh_settings = EvalHarnessSettings(
        quality_api_base_url=settings.quality_api_base_url,
        read_token=settings.quality_read_token,
    )
    probe_set = load_probe_set(eh_settings.repo_root)
    dataset_digest = frozen_digest(probe_set)
    metadata = {
        "dataset_id": probe_set.probe_set_id,
        "dataset_version": probe_set.version,
        "dataset_digest": dataset_digest,
    }
    evidence_root = Path(settings.gate_evidence_dir)
    if not evidence_root.is_absolute():
        evidence_root = eh_settings.repo_root / evidence_root
    evidence_dir = evidence_root / eval_id
    deadline = time.monotonic() + max(1, settings.gate_evaluation_timeout_seconds)

    def remaining_seconds() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("gate evaluator wall-clock deadline exceeded")
        return remaining

    try:
        executor = CommandSuiteRunner(
            repo_root=eh_settings.repo_root,
            evidence_dir=evidence_dir,
            timeout_seconds=max(1, int(remaining_seconds())),
        )
        contract = executor.run(
            suite="contract-assets",
            kind="contract",
            argv=[
                sys.executable,
                "-m",
                "pytest",
                "contracts/conformance/test_schemas.py",
                "contracts/conformance/test_wilson.py",
                "-q",
            ],
            artifact_name="contract-report.json",
        )
        executor = CommandSuiteRunner(
            repo_root=eh_settings.repo_root,
            evidence_dir=evidence_dir,
            timeout_seconds=max(1, int(remaining_seconds())),
        )
        replay = executor.run(
            suite="frozen-probe-replay",
            kind="replay",
            argv=[
                sys.executable,
                "-m",
                "pytest",
                "eval-harness/tests/unit/test_probe_judge.py",
                "eval-harness/tests/unit/test_digests.py",
                "eval-harness/tests/unit/test_gate.py",
                "-q",
            ],
            artifact_name="replay-report.json",
        )

        # A suite that consumes the wall-clock budget cannot be followed by a
        # provider call and still be considered a completed evaluation.
        remaining_seconds()

        client = QualityAPIClient(eh_settings)
        target = client.get_versionset(
            context["target_versionset_id"],
            timeout_seconds=min(eh_settings.quality_api_timeout_seconds, remaining_seconds()),
        )
        if target.get("versionset_id") != context["target_versionset_id"]:
            raise RuntimeError("Quality API returned a different target VersionSet id")
        if target.get("digest") != context["target_versionset_digest"]:
            raise RuntimeError("target VersionSet digest does not match WorkOrder")
        if target.get("revision") != context["target_revision"]:
            raise RuntimeError("target VersionSet revision does not match WorkOrder")

        answers: dict[str, str] = {}
        provider_origins: dict[str, str] = {}
        response_evidence: list[dict[str, Any]] = []
        athlete_digests: set[str] = set()
        for probe in probe_set.probes:
            result = client.evaluate_versionset(
                context["target_versionset_id"],
                probe.input,
                timeout_seconds=min(eh_settings.quality_api_timeout_seconds, remaining_seconds()),
            )
            if result.status != "ok":
                raise RuntimeError(
                    f"probe {probe.id} provider status is {result.status!r}; candidate evaluation failed"
                )
            if result.versionset_id != context["target_versionset_id"]:
                raise RuntimeError(
                    f"probe {probe.id} executed against {result.versionset_id}, expected {context['target_versionset_id']}"
                )
            target_content = target.get("content") or {}
            expected_digests = {
                "prompt_digest": (target_content.get("prompt") or {}).get("digest"),
                "kb_manifest_digest": (target_content.get("kb_manifest") or {}).get("manifest_digest"),
                "model_digest": (target_content.get("model") or {}).get("digest"),
            }
            actual_digests = {
                "prompt_digest": result.prompt_digest,
                "kb_manifest_digest": result.kb_manifest_digest,
                "model_digest": result.model_digest,
            }
            if actual_digests != expected_digests:
                raise RuntimeError(
                    f"probe {probe.id} candidate component digests do not match target VersionSet"
                )
            if result.model_digest:
                athlete_digests.add(result.model_digest)
            answers[probe.id] = result.answer
            provider_origins[probe.id] = result.provider_origin
            response_evidence.append(
                {
                    "probe_id": probe.id,
                    "request_id": result.request_id,
                    "versionset_id": result.versionset_id,
                    "prompt_digest": result.prompt_digest,
                    "kb_manifest_digest": result.kb_manifest_digest,
                    "model_digest": result.model_digest,
                    "provider_origin": result.provider_origin,
                    "provider_status": result.status,
                    "trace_id": result.trace_id,
                    "answer": result.answer,
                }
            )
        if len(athlete_digests) != 1:
            raise RuntimeError(f"gate probes did not use one athlete model digest: {sorted(athlete_digests)}")

        candidate_payload = {
            "eval_id": eval_id,
            "workorder_id": workorder_id,
            "target_versionset_id": context["target_versionset_id"],
            "target_revision": context["target_revision"],
            "target_versionset_digest": context["target_versionset_digest"],
            "dataset_id": probe_set.probe_set_id,
            "dataset_version": probe_set.version,
            "dataset_digest": dataset_digest,
            "responses": response_evidence,
            "judge_responses": [],
        }
        candidate_artifact = write_json_artifact(
            evidence_dir / "candidate-answers.json", candidate_payload
        )
        judge = None
        if eh_settings.has_stepfun_key and eh_settings.judge_model:
            judge = LLMJudge(
                eh_settings,
                eh_settings.judge_model,
                deadline_monotonic=deadline,
            )
        runner = GateRunner(
            eh_settings,
            probe_set,
            judge=judge,
            frozen_probe_set_digest=dataset_digest,
        )
        report = runner.run(
            GateCandidate(
                target_versionset_digest=context["target_versionset_digest"],
                probe_set_digest=dataset_digest,
                regression_suite_digest=suite_digest,
                answers=answers,
                provider_origins=provider_origins,
                athlete_model_digest=next(iter(athlete_digests)),
                source="live",
            ),
            contract_result=contract.result,
            replay_result=replay.result,
            artifact_refs=[contract.artifact_ref, replay.artifact_ref, candidate_artifact],
            live_available=True,
            eval_id=eval_id,
            report_id=f"gate_{eval_id.removeprefix('eval_')}",
        )
        if judge is None or len(judge.evidence) != len(probe_set.probes):
            raise RuntimeError("live judge did not persist one provider response per frozen probe")
        candidate_payload["judge_responses"] = judge.evidence
        final_candidate_artifact = write_json_artifact(
            evidence_dir / "candidate-answers.json", candidate_payload
        )
        report["artifact_refs"] = [
            final_candidate_artifact if ref.get("uri") == candidate_artifact["uri"] else ref
            for ref in report["artifact_refs"]
        ]
        return report, metadata
    except Exception as exc:  # noqa: BLE001 -- timeout/provider error becomes a persisted ERROR report
        error_artifact = write_json_artifact(
            evidence_dir / "executor-error.json",
            {
                "eval_id": eval_id,
                "workorder_id": workorder_id,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        report = build_error_gate_report(
            eval_id=eval_id,
            report_id=f"gate_{eval_id.removeprefix('eval_')}",
            target_versionset_digest=context["target_versionset_digest"],
            regression_suite_digest=suite_digest,
            probe_set_digest=dataset_digest,
            error_ref=error_artifact["uri"],
            artifact_refs=[error_artifact],
        )
        return report, metadata


def _report_hash(report: dict[str, Any]) -> str:
    body = {k: v for k, v in report.items() if k != "report_hash"}
    try:
        data = jcs_subset(body)
    except (ValueError, TypeError):
        import json

        data = json.dumps(
            body,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    return sha256_hex(data)


SANDBOX_ROOT = Path(__file__).resolve().parents[2] / "var" / "sandbox"


@mcp.tool(name="sandbox.verify")
def sandbox_verify(
    workorder_id: str, probe_path: str, prompt_before_path: str, prompt_after_path: str
) -> dict[str, Any]:
    """沙箱验证（ACL：守门员）：隔离容器回放坏例，修前/修后对照。

    三个文件路径必须在 var/sandbox/ 白名单目录内；结果为 fail-closed 证据：
    verdict=PASS 仅当修前 fail 且修后 pass。证据落 mcp_eval_runs（suite=sandbox-v1）。"""

    def _resolve(raw: str) -> Path:
        p = Path(raw).expanduser().resolve()
        root = SANDBOX_ROOT.resolve()
        if not str(p).startswith(str(root)):
            raise forbidden(f"path outside sandbox root: {raw}")
        if not p.is_file():
            raise not_found(f"file not found: {raw}")
        return p

    with session_scope() as session:
        draft = session.get(WorkOrderDraft, workorder_id)
        if draft is None:
            raise not_found(f"workorder {workorder_id} not found")
    probe = _resolve(probe_path)
    before = _resolve(prompt_before_path)
    after = _resolve(prompt_after_path)
    out = SANDBOX_ROOT / f"{workorder_id}-sandbox-evidence.json"
    runner = Path(__file__).resolve().parents[2] / "scripts" / "sandbox" / "runner.py"
    proc = subprocess.run(
        [
            sys.executable, str(runner),
            "--probe", str(probe),
            "--prompt-before", str(before),
            "--prompt-after", str(after),
            "--out", str(out),
        ],
        capture_output=True, text=True, timeout=600,
    )
    if not out.exists():
        raise dependency_unavailable(
            "sandbox runner produced no evidence: " + (proc.stderr or proc.stdout or "")[:300]
        )
    evidence = json.loads(out.read_text())
    eval_id = new_eval_id()
    with session_scope() as session:
        session.add(
            EvalRun(
                eval_id=eval_id,
                workorder_id=workorder_id,
                suite_digest="sandbox-v1",
                status="completed",
                report=evidence,
                report_hash=sha256_hex(
                    json.dumps(evidence, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ),
                evidence_digest=sha256_hex(out.read_bytes()),
            )
        )
        AuditService(session).record(
            actor="gatekeeper",
            action="sandbox.verify",
            target=eval_id,
            params={"workorder_id": workorder_id},
            result="success",
            evidence_refs={"evidence_path": str(out)},
        )
    return {"eval_id": eval_id, "verdict": evidence.get("verdict"), "report": evidence}


def _profiled_mcp(profile: str) -> FastMCP:
    profiles = {
        "gatekeeper": {
            "gate.run": gate_run,
            "gate.run_verification": gate_run_verification,
            "gate.report": gate_report,
            "sandbox.verify": sandbox_verify,
        },
        "attributionist": {
            "versionset.list": versionset_list,
            "versionset.get": versionset_get,
            "experiment.plan": experiment_plan,
            "experiment.run": experiment_run,
            "experiment.execute": experiment_execute,
            "experiment.report": experiment_report,
            "probe.freeze": probe_freeze,
        },
    }
    return build_tool_projection("mcp-eval-runner", profile, profiles)


def main() -> None:
    import uvicorn

    s = _settings()
    validate_projection_runtime(
        s,
        profile_workers={"gatekeeper": "gatekeeper", "attributionist": "eval-runner"},
        role_token_profiles=frozenset({"gatekeeper", "attributionist"}),
        gate_authority_profiles=frozenset({"gatekeeper"}),
    )
    uvicorn.run(
        build_server_app(
            _profiled_mcp(s.mcp_tool_profile),
            expected_consumer=s.mcp_expected_consumer,
            gateway_backend_token=s.mcp_gateway_backend_token,
            trust_consumer=s.trust_gateway_consumer,
            host=s.host,
        ),
        host=s.host,
        port=s.eval_runner_port,
        log_level=s.log_level.lower(),
    )


if __name__ == "__main__":
    main()
