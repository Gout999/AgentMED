"""5-cell 对照实验执行器（spec §4 / T4）。

职责（全部确定性，裁决由非 LLM 代码给出）：
- C/RP/RK/RM/G 五臂配置生成（基线版本 digest + 单因子替换）。
- 随机臂序（seed 可复现，记录到 protocol.random_arm_order）。
- 每 cell 每探针重复 n 次；unaffected controls 必跑。
- 计算每层 Δ 效应量 + 95%CI（newcombe_wilson_diff）。
- R1–R5 顺序裁决 → ATTRIBUTED / INCONCLUSIVE / CONFOUNDED。
- INCONCLUSIVE 自动补实验（上限 2 次后升级）；CONFOUNDED 输出强制全因子建议。
- 产出 evidence-bundle + attribution-report（双 schema 校验）。

版本对账：每 cell 的 prompt/kb/model digest 取自实际 chat 响应（/logs 口径），
保证「实验用的版本」与「线上跑的版本」一致。
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from .adjudicate import CellStats, adjudicate
from .client import QualityAPIClient
from .config import Settings
from .digests import canonical_json_bytes, digest_of_bytes
from .models import ExperimentPlan, ProbeSet
from .probe_judge import judge_probe
from .report import _now_iso, new_id
from .stats import newcombe_wilson_diff


class ArmDriver:
    """臂驱动器：把抽象臂配置映射为 live 状态。子类按被治理应用实现。"""

    def setup(self, arm: str) -> None:
        raise NotImplementedError

    def cleanup(self) -> None:
        raise NotImplementedError


class DemoAppB1Driver(ArmDriver):
    """demo-app B1 实验的 live 驱动器：C/RK/RM 臂 = 注入 B1（P1），RP/G 臂 = 复位（P0）。

    说明：B1 仅改 prompt 层，KB/manifest 与 model 均未变更，故 K0==K1、M0==M1；
    RK（回滚 KB）与 RM（回滚模型）在实际内容上等同 C 臂——这正是「单因子替换」在
    单一故障下的忠实呈现（Δ_kb=Δ_model=0 由实验数据自然给出）。
    """

    ARM_FAULT = {"C": "B1", "RK": "B1", "RM": "B1", "RP": None, "G": None}

    def __init__(self, client: QualityAPIClient):
        self.client = client

    def setup(self, arm: str) -> None:
        fault = self.ARM_FAULT[arm]
        if fault:
            self.client.inject_fault(fault)
        else:
            self.client.reset_faults()

    def cleanup(self) -> None:
        self.client.reset_faults()


@dataclass
class ProbeRun:
    probe_id: str
    repetition: int
    recovered: bool
    output_ref: str
    answer: str = ""


@dataclass
class CellResult:
    arm: str
    versions: dict
    runs: list[ProbeRun] = field(default_factory=list)
    seed: int = 0


@dataclass
class ExperimentResult:
    plan: ExperimentPlan
    cells: dict[str, CellResult]
    bundle: dict
    report: dict
    verdict: dict
    supplement_attempts: int = 0


class ExperimentRunner:
    """执行实验并产出报告。run() 可传入 seed 复现臂序。"""

    def __init__(self, client: QualityAPIClient, probe_set: ProbeSet, settings: Settings):
        self.client = client
        self.probe_set = probe_set
        self.settings = settings

    # ------------------------------------------------------------------ 执行
    def run(
        self,
        plan: ExperimentPlan,
        driver: ArmDriver,
        *,
        seed: int | None = None,
        suppress_digest_capture: bool = False,
    ) -> ExperimentResult:
        """执行一次完整实验（含 INCONCLUSIVE 补实验循环）。

        suppress_digest_capture：离线/录制回放时跳过对 /chat 的版本采集
        （调用方需预先在 plan.version_digests 填好）。
        """
        if plan.matrix != "five_cell":
            raise NotImplementedError(
                "MVP 仅 five_cell 最小矩阵；full_factorial_2x2x2 为 CONFOUNDED 后的强制扩展（Phase 2）"
            )

        if not plan.affected_probe_ids:
            raise ValueError("实验计划缺少 affected 探针（discovery + hidden confirmation）")

        base_reps = plan.repetitions
        max_supp = self.settings.experiment_max_supplements
        rng_seed = seed if seed is not None else random.SystemRandom().randint(0, 2**32 - 1)

        if not suppress_digest_capture:
            self._capture_version_digests(plan, driver)

        attempts = 0
        while True:
            cells, arm_order, probe_order = self._run_round(plan, driver, rng_seed)
            stats = self._aggregate(plan, cells)
            verdict = adjudicate(stats, plan.delta_min)

            if verdict.decision != "INCONCLUSIVE" or verdict.reason_code == "ENV_UNTRUSTED":
                break
            if attempts >= max_supp:
                verdict.escalated_to_human = True
                verdict.rationale += (
                    f" ｜ 已补实验 {attempts} 次达上限，升级人工（D-001：INCONCLUSIVE 超 2 次 → 人工）"
                )
                break
            attempts += 1
            plan.repetitions = base_reps + 2 * attempts
            verdict.rationale += f" ｜ 补实验 #{attempts}（repetitions {base_reps}→{plan.repetitions}）"

        verdict.supplement_attempts = attempts
        try:
            driver.cleanup()
        except Exception:
            pass  # 清理失败不掩盖实验结论；调用方应按运行态纪律手动 reset

        bundle = self._build_bundle(plan, cells, probe_order, rng_seed, verdict)
        report = self._build_report(plan, bundle, verdict, cells)
        return ExperimentResult(plan=plan, cells=cells, bundle=bundle, report=report, verdict=verdict.as_report_verdict())

    # ------------------------------------------------------------------ 子步骤
    def _capture_version_digests(self, plan: ExperimentPlan, driver: ArmDriver) -> None:
        """采集 P0/K0/M0（基线）与 P1/K1/M1（故障）digest（各一次 /chat）。"""
        probe = self.probe_set.get(plan.discovery[0])
        driver.setup("G")
        r0 = self.client.chat(probe.input)
        plan.version_digests["P0"] = r0.prompt_digest
        plan.version_digests["K0"] = r0.kb_manifest_digest
        plan.version_digests["M0"] = r0.model_digest
        driver.setup("C")
        r1 = self.client.chat(probe.input)
        plan.version_digests["P1"] = r1.prompt_digest
        plan.version_digests["K1"] = r1.kb_manifest_digest
        plan.version_digests["M1"] = r1.model_digest
        driver.cleanup()

    def _run_round(self, plan: ExperimentPlan, driver: ArmDriver, seed: int) -> tuple[dict[str, CellResult], list[str], list[str]]:
        """随机臂序执行一轮。返回 (cells, arm_order, probe_order)。

        probe_order = 实际执行顺序的 `arm@probe_id` 列表（先 discovery 后 hidden，组内随机），
        供 evidence-bundle 的 protocol.random_arm_order 记录（可复现臂序）。
        """
        rng = random.Random(seed)
        arm_order = ["C", "RP", "RK", "RM", "G"]
        rng.shuffle(arm_order)

        discovery_order = list(plan.discovery)
        rng.shuffle(discovery_order)
        hidden_order = list(plan.hidden_confirmation)
        rng.shuffle(hidden_order)
        affected_order = discovery_order + hidden_order
        control_order = list(plan.unaffected_controls)
        rng.shuffle(control_order)

        cells: dict[str, CellResult] = {}
        for arm in arm_order:
            driver.setup(arm)
            cell = CellResult(arm=arm, versions={}, seed=seed)
            first = True
            for probe_id in affected_order + control_order:
                probe = self.probe_set.get(probe_id)
                for rep in range(1, plan.repetitions + 1):
                    result = self.client.chat(probe.input)
                    if first:
                        cell.versions = {
                            "prompt_digest": result.prompt_digest,
                            "kb_manifest_digest": result.kb_manifest_digest,
                            "model_digest": result.model_digest,
                        }
                        first = False
                    passed, _ = judge_probe(probe, result.answer)
                    cell.runs.append(
                        ProbeRun(
                            probe_id=probe_id,
                            repetition=rep,
                            recovered=passed,
                            output_ref=f"file://evidence/{plan.experiment_id}/{arm}/{probe_id}-rep{rep}.txt",
                            answer=result.answer,
                        )
                    )
            cells[arm] = cell
        probe_order = [f"{arm}@{pid}" for arm in arm_order for pid in affected_order]
        return cells, arm_order, probe_order

    def _aggregate(self, plan: ExperimentPlan, cells: dict[str, CellResult]) -> dict[str, CellStats]:
        affected = set(plan.affected_probe_ids)
        hidden_ids = set(plan.hidden_confirmation)
        result: dict[str, CellStats] = {}
        for arm, cell in cells.items():
            aff_runs = [r for r in cell.runs if r.probe_id in affected]
            ctl_runs = [r for r in cell.runs if r.probe_id not in affected]
            hidden_runs = [r for r in cell.runs if r.probe_id in hidden_ids]
            result[arm] = CellStats(
                recovery_rate=(sum(1 for r in aff_runs if r.recovered) / len(aff_runs)) if aff_runs else 0.0,
                n_trials=len(aff_runs),
                control_pass_rate=(sum(1 for r in ctl_runs if r.recovered) / len(ctl_runs)) if ctl_runs else 0.0,
                hidden_recovery_rate=(sum(1 for r in hidden_runs if r.recovered) / len(hidden_runs)) if hidden_runs else 0.0,
                hidden_trials=len(hidden_runs),
            )
        return result

    # ------------------------------------------------------------------ 报告
    def _build_bundle(self, plan, cells, probe_order, seed, verdict) -> dict:
        return {
            "schema_version": "0.1.0",
            "bundle_id": new_id("eb"),
            "experiment_id": plan.experiment_id,
            "case_id": plan.case_id,
            "protocol": {
                "matrix": plan.matrix,
                "repetitions": plan.repetitions,
                "random_arm_order": probe_order,
                "random_seed_ref": f"seed://{plan.experiment_id}/{seed}",
                "frozen_at": _now_iso(),
                "confidence": plan.confidence,
            },
            "probe_set": {
                "probe_set_digest": plan.probe_set_digest,
                "discovery": list(plan.discovery),
                "hidden_confirmation": list(plan.hidden_confirmation),
                "unaffected_controls": list(plan.unaffected_controls),
            },
            "cells": {
                arm: {
                    "versions": cell.versions,
                    "results": [
                        {
                            "probe_id": r.probe_id,
                            "repetition": r.repetition,
                            "recovered": r.recovered,
                            "output_ref": r.output_ref,
                        }
                        for r in cell.runs
                    ],
                    "recovery_rate": round(self._rate(cell, plan), 4),
                    "control_pass_rate": round(self._ctrl_rate(cell, plan), 4),
                }
                for arm, cell in cells.items()
            },
            "effects": self._effects_block(cells, plan),
            "verdict": verdict.as_evidence_verdict(),
            "created_at": _now_iso(),
        }

    def _build_report(self, plan, bundle, verdict, cells) -> dict:
        return {
            "schema_version": "0.1.0",
            "report_id": new_id("attr"),
            "experiment_id": plan.experiment_id,
            "case_id": plan.case_id,
            "probe_set_digest": plan.probe_set_digest,
            "version_digests": dict(plan.version_digests),
            "cells": {
                arm: {
                    "recovery_rate": round(self._rate(cells[arm], plan), 4),
                    "n_probes": len(plan.affected_probe_ids),
                    "n_trials": len(plan.affected_probe_ids) * plan.repetitions,
                    "control_pass_rate": round(self._ctrl_rate(cells[arm], plan), 4),
                }
                for arm in ["C", "RP", "RK", "RM", "G"]
            },
            "deltas": {
                "prompt": {
                    "estimate": bundle["effects"]["prompt"]["delta"],
                    "ci95_lower": bundle["effects"]["prompt"]["ci95_lower"],
                    "ci95_upper": bundle["effects"]["prompt"]["ci95_upper"],
                },
                "kb": {
                    "estimate": bundle["effects"]["kb"]["delta"],
                    "ci95_lower": bundle["effects"]["kb"]["ci95_lower"],
                    "ci95_upper": bundle["effects"]["kb"]["ci95_upper"],
                },
                "model_params": {
                    "estimate": bundle["effects"]["model_params"]["delta"],
                    "ci95_lower": bundle["effects"]["model_params"]["ci95_lower"],
                    "ci95_upper": bundle["effects"]["model_params"]["ci95_upper"],
                },
                "method": bundle["effects"]["method"],
            },
            "verdict": verdict.as_report_verdict(),
            "evidence_bundle_ref": {
                "uri": f"file://evidence/{plan.experiment_id}/evidence-bundle.json",
                "digest": digest_of_bytes(canonical_json_bytes(bundle)),
            },
            "generated_at": _now_iso(),
        }

    # ---- helpers ----
    @staticmethod
    def _rate(cell: CellResult, plan: ExperimentPlan) -> float:
        affected = set(plan.affected_probe_ids)
        runs = [r for r in cell.runs if r.probe_id in affected]
        return (sum(1 for r in runs if r.recovered) / len(runs)) if runs else 0.0

    @staticmethod
    def _ctrl_rate(cell: CellResult, plan: ExperimentPlan) -> float:
        affected = set(plan.affected_probe_ids)
        runs = [r for r in cell.runs if r.probe_id not in affected]
        return (sum(1 for r in runs if r.recovered) / len(runs)) if runs else 0.0

    def _effects_block(self, cells: dict[str, CellResult], plan: ExperimentPlan) -> dict:
        c_rate = self._rate(cells["C"], plan)
        n = len(plan.affected_probe_ids) * plan.repetitions
        out: dict = {}
        for arm, key in [("RP", "prompt"), ("RK", "kb"), ("RM", "model_params")]:
            p = self._rate(cells[arm], plan)
            lb, ub = newcombe_wilson_diff(p, n, c_rate, n)
            out[key] = {
                "delta": round(p - c_rate, 4),
                "ci95_lower": round(lb, 4),
                "ci95_upper": round(ub, 4),
                "significant": (lb > plan.delta_min),
            }
        out["method"] = "newcombe_wilson_diff"
        return out
