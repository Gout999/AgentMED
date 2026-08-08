"""mcp-eval-runner：门禁评测触发/报告 + 归因实验（spec §9.5）。

- gate.run/report：确定性门禁（规则轨+裁判轨分开报告；live 轨不可达标 UNAVAILABLE/skipped）。
- experiment.*：包装 control-plane /v1/experiments。
- experiment.execute：后台线程驱动 eval-harness 执行完整 5-cell 实验（S0-006 修复）。
- 裁判模型 ≠ 运动员模型（T6 硬约束，digest 不同）。
"""
import logging
import json
import sys
import threading
import time
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.audit import AuditService  # noqa: E402
from common.config import Settings, get_settings  # noqa: E402
from common.db import session_scope  # noqa: E402
from common.errors import McpError, dependency_unavailable, not_found, validation  # noqa: E402
from common.http import HttpClient  # noqa: E402
from common.ids import new_eval_id  # noqa: E402
from common.jcs import jcs_subset, sha256_hex  # noqa: E402
from common.serverkit import build_server_app  # noqa: E402
from common.tables import EvalRun, WorkOrderDraft  # noqa: E402

# eval-harness 执行机（monorepo 源码依赖，见 requirements.txt：-e ../eval-harness）。
# 复用完整 5-cell 执行→聚合→裁决→报告链路，禁止重造。
from eval_harness.client import QualityAPIClient  # noqa: E402
from eval_harness.config import Settings as EvalHarnessSettings  # noqa: E402
from eval_harness.digests import sha256_digest  # noqa: E402
from eval_harness.experiment import DemoAppB1Driver, ExperimentRunner  # noqa: E402
from eval_harness.gate import GateCandidate, GateRunner, LLMJudge, build_error_gate_report  # noqa: E402
from eval_harness.gate_executor import (  # noqa: E402
    CommandSuiteRunner,
    frozen_gate_suite_digest,
    write_json_artifact,
)
from eval_harness.models import ExperimentPlan  # noqa: E402
from eval_harness.probe_loader import frozen_digest, load_probe_set  # noqa: E402

logger = logging.getLogger(__name__)

mcp = FastMCP("mcp-eval-runner")

def _settings() -> Settings:
    return get_settings()


def _cp() -> HttpClient:
    s = _settings()
    return HttpClient(s.control_plane_base_url, token=s.control_plane_token)


# ---------- 门禁评测 ----------


@mcp.tool(name="gate.run")
def gate_run(workorder_id: str, suite_digest: str = "") -> dict[str, Any]:
    """Run the real allowlisted gate suites and persist the fail-closed result.

    This synchronous implementation returns `completed`; it never claims queued while already
    completed. Provider/test timeouts are persisted as an ERROR GateReport.
    """
    eval_id = new_eval_id()
    with session_scope() as session:
        draft = session.get(WorkOrderDraft, workorder_id)
        if draft is None:
            raise not_found(f"workorder {workorder_id} not found")
        if draft.status != "DRAFT":
            raise validation(f"workorder state {draft.status} cannot enter gate")
        context = dict(draft.draft_payload or {})

    authoritative_suite_digest = frozen_gate_suite_digest(EvalHarnessSettings().repo_root)
    if suite_digest and suite_digest != authoritative_suite_digest:
        raise validation(
            "suite_digest does not match the repository-owned gate suite; omit it to use the authoritative digest"
        )
    suite_digest = authoritative_suite_digest
    _validate_gate_context(workorder_id, suite_digest, context)
    report, metadata = _execute_gate(eval_id, workorder_id, suite_digest, context)
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
            action="gate.run",
            target=eval_id,
            params={"workorder_id": workorder_id, "suite_digest": suite_digest},
            result="success",
            evidence_refs={"evidence_digest": evidence_digest},
        )

    # The deterministic control plane is the authoritative Eval/Gate state source.
    try:
        registered = _cp().post(
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

    version_refs 不在计划期固化——版本 digest 由 experiment.execute 执行时从实际 /chat
    响应现场捕获（对账口径，见 eval-harness _capture_version_digests）。"""
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
def experiment_run(experiment_id: str) -> dict[str, Any]:
    """启动实验（ACL：归因师）。控制面 runner 领单后执行；返回 {status:running}。"""
    try:
        return _cp().post(
            f"/v1/experiments/{experiment_id}/start",
            json_body={"runner_id": "eval-runner", "lease_id": "", "fencing_token": 0},
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

    前置：实验必须处于 PROTOCOL_FROZEN 或 RUNNING（RUNNING = 已调 experiment.run 领单的常态；
    两者都合法）。冻结协议三探针集必须非空，否则 validation 报错并给出正确结构指引。

    执行模型：runner 就是你自己——本工具在后台线程里用 eval-harness ExperimentRunner
    跑完整 5-cell 执行→聚合→裁决→报告，逐 cell 回流 POST /v1/experiments/{id}/cells，
    完成回流 POST /verdict（verdict + 每层 Δ + attributed_layer）；任何异常回流
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
    if state not in ("PROTOCOL_FROZEN", "RUNNING"):
        raise validation(
            f"experiment {experiment_id} 当前状态 {state} 不可执行；前置必须是 PROTOCOL_FROZEN "
            "（探针已冻结）或 RUNNING（已调 experiment.run）。正确顺序：experiment.plan → "
            "probe.freeze → GET 回读确认三探针集非空 → experiment.run → experiment.execute。"
        )
    payload = exp.get("payload") or {}
    _require_nonempty_probe_sets(payload)
    thread = threading.Thread(
        target=_execute_experiment_background,
        args=(experiment_id,),
        name=f"eval-execute-{experiment_id}",
        daemon=True,
    )
    thread.start()
    return {"status": "executing", "experiment_id": experiment_id}


@mcp.tool(name="probe.freeze")
def probe_freeze(
    experiment_id: str,
    probe_set: dict[str, Any],
) -> dict[str, Any]:
    """冻结探针三分集（ACL：归因师；冻结后全员只读）。返回 {probe_set_digest}。

    probe_set 必须顶层平铺 discovery / hidden_confirmation / unaffected_controls 三个
    非空数组（+ 可选 repetitions）；versions 可省略（版本由 execute 现场捕获 digest）。"""
    _validate_probe_set_structure(probe_set)
    try:
        digest_bytes = jcs_subset(probe_set)
    except (ValueError, TypeError) as exc:
        raise validation(f"probe_set 需 JCS 可序列化（ASCII）：{exc}") from exc
    digest = f"sha256:{sha256_hex(digest_bytes)}"
    body = {
        "probe_set_digest": digest,
        "discovery": probe_set.get("discovery", []),
        "hidden_confirmation": probe_set.get("hidden_confirmation", []),
        "unaffected_controls": probe_set.get("unaffected_controls", []),
        "repetitions": int(probe_set.get("repetitions", 1)),
        "versions": probe_set.get("versions", {}),
        "random_seed_ref": probe_set.get("random_seed_ref", ""),
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

# probe_set 顶层必须存在的三个探针集键（versions 允许缺省/空）。
_REQUIRED_PROBE_KEYS = ("discovery", "hidden_confirmation", "unaffected_controls")

# 面向 LLM 的 probe_set 正确结构示例（错误消息里逐条教正确键名）。
_PROBE_SET_EXAMPLE = {
    "discovery": ["cs-001", "cs-002", "cs-003"],
    "hidden_confirmation": ["cs-004", "cs-005"],
    "unaffected_controls": ["cs-013", "cs-014", "cs-015", "cs-016"],
    "repetitions": 5,
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
    if problems:
        raise validation(
            "probe_set 结构错误："
            + "；".join(problems)
            + "。正确结构是顶层平铺四个键（discovery / hidden_confirmation / "
            "unaffected_controls / repetitions），不要把探针集再嵌套一层："
            + f"{_PROBE_SET_EXAMPLE}"
            + "。versions 可省略（版本由 experiment.execute 执行时现场捕获 digest）。"
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
        payload = exp.get("payload") or {}
        # 前置校验（与 execute 工具一致；线程内数据可能更新，重复校验一次）。
        _require_nonempty_probe_sets(payload)
        # execute 允许 PROTOCOL_FROZEN（agent 未 run）或 RUNNING；未 run 先推进状态。
        if exp.get("state") != "RUNNING":
            _cp().post(
                f"/v1/experiments/{experiment_id}/start",
                json_body={"runner_id": "eval-runner", "lease_id": "", "fencing_token": 0},
            )
        plan, probe_set, eh_settings = _build_execution_context(experiment_id, payload)
        client = QualityAPIClient(eh_settings)
        driver = DemoAppB1Driver(client)
        result = ExperimentRunner(client, probe_set, eh_settings).run(plan, driver)

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
                    "fencing_token": 0,
                },
            )

        # 回流 verdict：verdict + 每层 Δ + attributed_layer（最高 Δ 层）。
        report = result.report
        deltas = {
            "prompt": report["deltas"]["prompt"]["estimate"],
            "kb": report["deltas"]["kb"]["estimate"],
            "model_params": report["deltas"]["model_params"]["estimate"],
        }
        verdict = result.verdict.get("decision") or report["verdict"]["decision"]
        _cp().post(
            f"/v1/experiments/{experiment_id}/verdict",
            json_body={
                "verdict": verdict,
                "deltas": deltas,
                "evidence_bundle_ref": result.bundle.get("bundle_id", f"eval://{experiment_id}/evidence-bundle"),
                "report_ref": result.report.get("report_id", f"eval://{experiment_id}/attribution-report"),
                "attributed_layer": result.verdict.get("attributed_layer"),
            },
        )
        logger.info("experiment.execute 完成 experiment_id=%s verdict=%s", experiment_id, verdict)
    except Exception as exc:  # noqa: BLE001
        logger.exception("experiment.execute 后台执行失败 experiment_id=%s", experiment_id)
        try:
            _cp().post(
                f"/v1/experiments/{experiment_id}/cancel",
                json_body={"reason": f"eval-runner execute failed: {exc}"},
            )
        except Exception as cancel_exc:  # noqa: BLE001
            logger.error("experiment.execute 取消失败 experiment_id=%s: %s", experiment_id, cancel_exc)


def _build_execution_context(experiment_id: str, payload: dict[str, Any]):
    """从冻结协议构造 eval-harness 执行上下文（ProbeSet + ExperimentPlan + 执行机 Settings）。

    探针定义取自 contracts/fixtures/probes-customer-service.yaml；实验用的版本 digest 由
    ExperimentRunner._capture_version_digests 现场捕获（对账口径）。
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
        version_digests={},  # 版本现场捕获，不在计划期固化
        discovery=list(payload.get("discovery") or []),
        hidden_confirmation=list(payload.get("hidden_confirmation") or []),
        unaffected_controls=list(payload.get("unaffected_controls") or []),
        random_seed=None,
    )
    return plan, probe_set, eh_settings


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
            response_evidence.append(
                {
                    "probe_id": probe.id,
                    "request_id": result.request_id,
                    "versionset_id": result.versionset_id,
                    "prompt_digest": result.prompt_digest,
                    "kb_manifest_digest": result.kb_manifest_digest,
                    "model_digest": result.model_digest,
                    "provider_status": result.status,
                    "trace_id": result.trace_id,
                    "answer": result.answer,
                }
            )
        if len(athlete_digests) != 1:
            raise RuntimeError(f"gate probes did not use one athlete model digest: {sorted(athlete_digests)}")

        candidate_artifact = write_json_artifact(
            evidence_dir / "candidate-answers.json",
            {
                "eval_id": eval_id,
                "workorder_id": workorder_id,
                "target_versionset_id": context["target_versionset_id"],
                "target_revision": context["target_revision"],
                "target_versionset_digest": context["target_versionset_digest"],
                "dataset_id": probe_set.probe_set_id,
                "dataset_version": probe_set.version,
                "dataset_digest": dataset_digest,
                "responses": response_evidence,
            },
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


def main() -> None:
    import uvicorn

    s = _settings()
    uvicorn.run(build_server_app(mcp), host=s.host, port=s.eval_runner_port, log_level=s.log_level.lower())


if __name__ == "__main__":
    main()
