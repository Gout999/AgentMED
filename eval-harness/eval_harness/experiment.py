"""5-cell 对照实验执行器（spec §4 / T4）。

职责（全部确定性，裁决由非 LLM 代码给出）：
- C/RP/RK/RM/G 五臂配置生成（基线版本 digest + 单因子替换）。
- 随机臂序（seed 可复现，记录到 protocol.random_arm_order）。
- 每 cell 每探针重复 n 次；unaffected controls 必跑。
- 计算每层 Δ 效应量 + 95%CI（newcombe_wilson_diff）。
- R1–R5 顺序裁决 → ATTRIBUTED / INCONCLUSIVE / CONFOUNDED。
- INCONCLUSIVE 保持冻结协议不变，由控制面开启同一 Experiment 的新 epoch 或升级人工；
  当前 runner 不实现 epoch 编排并因此 fail closed。CONFOUNDED 输出强制全因子建议。
- 产出 evidence-bundle + attribution-report（双 schema 校验）。

版本对账：每 cell 的 prompt/kb/model digest 取自实际 chat 响应（/logs 口径），
保证「实验用的版本」与「线上跑的版本」一致。
"""
from __future__ import annotations

import random
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .adjudicate import CellStats, adjudicate
from .client import QualityAPIClient
from .config import Settings
from .digests import canonical_json_bytes, digest_of_bytes
from .models import ExperimentPlan, ProbeSet
from .probe_judge import judge_probe
from .report import _now_iso, new_id
from .stats import newcombe_wilson_diff


class ArmDriver:
    """Map an experiment arm to an immutable read-side evaluation target."""

    def setup(self, arm: str) -> None:
        raise NotImplementedError

    def cleanup(self) -> None:
        raise NotImplementedError

    def evaluate(self, client: QualityAPIClient, message: str):
        """Evaluate without mutating Quality state."""
        return client.chat(message)


class ImmutableVersionSetDriver(ArmDriver):
    """Read-only arm driver bound to five exact immutable VersionSets."""

    def __init__(self, arm_versionsets: dict[str, str]):
        required = {"C", "RP", "RK", "RM", "G"}
        if set(arm_versionsets) != required or any(
            not isinstance(value, str) or not value.startswith("vs_")
            for value in arm_versionsets.values()
        ):
            raise ValueError("arm_versionsets must bind exactly C/RP/RK/RM/G to VersionSet ids")
        self.arm_versionsets = dict(arm_versionsets)
        self.current_versionset_id: str | None = None

    def setup(self, arm: str) -> None:
        self.current_versionset_id = self.arm_versionsets[arm]

    def cleanup(self) -> None:
        self.current_versionset_id = None

    def evaluate(self, client: QualityAPIClient, message: str):
        if self.current_versionset_id is None:
            raise RuntimeError("experiment arm was not selected")
        return client.evaluate_versionset(self.current_versionset_id, message)


@dataclass
class ProbeRun:
    probe_id: str
    repetition: int
    recovered: bool
    output_ref: str
    output_digest: str
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

    def __init__(
        self,
        client: QualityAPIClient,
        probe_set: ProbeSet,
        settings: Settings,
        *,
        artifact_dir: Path | None = None,
        progress_callback: Callable[[], None] | None = None,
        cell_versionset_refs: dict[str, dict] | None = None,
        trial_callback: Callable[[str, ProbeRun], ProbeRun | None] | None = None,
    ):
        self.client = client
        self.probe_set = probe_set
        self.settings = settings
        self.artifact_dir = artifact_dir.resolve() if artifact_dir is not None else None
        self.progress_callback = progress_callback
        self.trial_callback = trial_callback
        self.cell_versionset_refs = dict(cell_versionset_refs or {})
        if self.cell_versionset_refs:
            required = {"C", "RP", "RK", "RM", "G"}
            if set(self.cell_versionset_refs) != required:
                raise ValueError("cell_versionset_refs must bind exactly C/RP/RK/RM/G")
            for arm, ref in self.cell_versionset_refs.items():
                if (
                    not isinstance(ref, dict)
                    or not isinstance(ref.get("versionset_id"), str)
                    or not isinstance(ref.get("digest"), str)
                    or not isinstance(ref.get("revision"), int)
                ):
                    raise ValueError(f"cell_versionset_refs.{arm} is incomplete")

    # ------------------------------------------------------------------ 执行
    def run(
        self,
        plan: ExperimentPlan,
        driver: ArmDriver,
        *,
        seed: int | None = None,
        suppress_digest_capture: bool = False,
        prior_trials: dict[tuple[str, str, int], ProbeRun] | None = None,
    ) -> ExperimentResult:
        """执行一次完整冻结实验；INCONCLUSIVE 不会在原地改写协议。

        suppress_digest_capture：离线/录制回放时跳过对 /chat 的版本采集
        （调用方需预先在 plan.version_digests 填好）。
        """
        if plan.matrix != "five_cell":
            raise NotImplementedError(
                "MVP 仅 five_cell 最小矩阵；full_factorial_2x2x2 为 CONFOUNDED 后的强制扩展（Phase 2）"
            )

        if not plan.affected_probe_ids:
            raise ValueError("实验计划缺少 affected 探针（discovery + hidden confirmation）")

        rng_seed = (
            seed
            if seed is not None
            else plan.random_seed
            if plan.random_seed is not None
            else random.SystemRandom().randint(0, 2**32 - 1)
        )

        if not suppress_digest_capture:
            self._capture_version_digests(plan, driver)

        cells, arm_order, probe_order = self._run_round(
            plan,
            driver,
            rng_seed,
            prior_trials=dict(prior_trials or {}),
        )
        stats = self._aggregate(plan, cells)
        verdict = adjudicate(stats, plan.delta_min)
        verdict.supplement_attempts = 0
        if verdict.decision == "INCONCLUSIVE":
            # Protocol fields are immutable once the control plane freezes an
            # Experiment.  Mutating repetitions here used to make the final
            # report disagree with that authority.  A supplement must be a new
            # epoch of the same Experiment with a new ProtocolFreeze, never a
            # hidden in-place retry.  This runner does not orchestrate epochs.
            verdict.escalated_to_human = True
            if verdict.reason_code == "ENV_UNTRUSTED":
                verdict.rationale += (
                    " ｜ 当前冻结协议保持不变；先恢复可信环境，再由控制面开启同一 Experiment "
                    "的新 epoch 并重新冻结 repetitions/probes；当前 runner 不支持 epoch 编排，"
                    "因此 fail closed 并升级人工"
                )
            else:
                verdict.rationale += (
                    " ｜ 当前冻结协议保持不变；补实验须由控制面开启同一 Experiment 的新 epoch "
                    "并重新冻结 repetitions/probes；当前 runner 不支持 epoch 编排，因此 fail closed "
                    "并升级人工"
                )
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
        r0 = self._evaluate(driver, probe.input)
        plan.version_digests["P0"] = r0.prompt_digest
        plan.version_digests["K0"] = r0.kb_manifest_digest
        plan.version_digests["M0"] = r0.model_digest
        driver.setup("C")
        r1 = self._evaluate(driver, probe.input)
        plan.version_digests["P1"] = r1.prompt_digest
        plan.version_digests["K1"] = r1.kb_manifest_digest
        plan.version_digests["M1"] = r1.model_digest
        driver.cleanup()

    def _run_round(
        self,
        plan: ExperimentPlan,
        driver: ArmDriver,
        seed: int,
        *,
        prior_trials: dict[tuple[str, str, int], ProbeRun],
    ) -> tuple[dict[str, CellResult], list[str], list[str]]:
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
        expected_keys = {
            (arm, probe_id, repetition)
            for arm in ("C", "RP", "RK", "RM", "G")
            for probe_id in affected_order + control_order
            for repetition in range(1, plan.repetitions + 1)
        }
        unexpected = set(prior_trials) - expected_keys
        if unexpected:
            raise ValueError(
                f"prior trial set contains an entry outside the frozen protocol: {sorted(unexpected)[0]!r}"
            )

        cells: dict[str, CellResult] = {}
        for arm in arm_order:
            driver.setup(arm)
            version_keys = {
                "C": ("P1", "K1", "M1"),
                "RP": ("P0", "K1", "M1"),
                "RK": ("P1", "K0", "M1"),
                "RM": ("P1", "K1", "M0"),
                "G": ("P0", "K0", "M0"),
            }[arm]
            expected_versions = {
                "prompt_digest": plan.version_digests.get(version_keys[0]),
                "kb_manifest_digest": plan.version_digests.get(version_keys[1]),
                "model_digest": plan.version_digests.get(version_keys[2]),
            }
            cell = CellResult(
                arm=arm,
                versions=(
                    expected_versions
                    if all(isinstance(value, str) and value for value in expected_versions.values())
                    else {}
                ),
                seed=seed,
            )
            for probe_id in affected_order + control_order:
                probe = self.probe_set.get(probe_id)
                for rep in range(1, plan.repetitions + 1):
                    key = (arm, probe_id, rep)
                    prior = prior_trials.get(key)
                    if prior is not None:
                        if (
                            prior.probe_id != probe_id
                            or prior.repetition != rep
                            or not isinstance(prior.recovered, bool)
                            or not isinstance(prior.output_ref, str)
                            or not prior.output_ref
                            or not isinstance(prior.output_digest, str)
                            or not prior.output_digest
                        ):
                            raise ValueError(f"prior trial {key!r} is malformed")
                        cell.runs.append(prior)
                        continue
                    result = self._evaluate(driver, probe.input)
                    frozen_ref = self.cell_versionset_refs.get(arm)
                    if frozen_ref and result.versionset_id != frozen_ref["versionset_id"]:
                        raise RuntimeError(
                            f"arm {arm} executed {result.versionset_id}, expected {frozen_ref['versionset_id']}"
                        )
                    actual_versions = {
                        "prompt_digest": result.prompt_digest,
                        "kb_manifest_digest": result.kb_manifest_digest,
                        "model_digest": result.model_digest,
                    }
                    if cell.versions and actual_versions != cell.versions:
                        raise RuntimeError(
                            f"arm {arm} provider result differs from the frozen component digests"
                        )
                    if not cell.versions:
                        cell.versions = actual_versions
                    passed, _ = judge_probe(probe, result.answer)
                    output_ref, output_digest = self._persist_probe_output(
                        plan=plan,
                        arm=arm,
                        probe_id=probe_id,
                        repetition=rep,
                        recovered=passed,
                        result=result,
                    )
                    run = ProbeRun(
                        probe_id=probe_id,
                        repetition=rep,
                        recovered=passed,
                        output_ref=output_ref,
                        output_digest=output_digest,
                        answer=result.answer,
                    )
                    if self.trial_callback is not None:
                        replacement = self.trial_callback(arm, run)
                        if replacement is not None:
                            if (
                                replacement.probe_id != run.probe_id
                                or replacement.repetition != run.repetition
                                or replacement.recovered is not run.recovered
                                or replacement.answer != run.answer
                            ):
                                raise RuntimeError(
                                    "trial callback changed provider result identity or decision"
                                )
                            run = replacement
                    cell.runs.append(run)
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
                            "output_digest": r.output_digest,
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

    def _evaluate(self, driver: ArmDriver, message: str):
        if self.progress_callback is not None:
            self.progress_callback()
        return driver.evaluate(self.client, message)

    def _persist_probe_output(
        self,
        *,
        plan: ExperimentPlan,
        arm: str,
        probe_id: str,
        repetition: int,
        recovered: bool,
        result,
    ) -> tuple[str, str]:
        artifact = {
            "experiment_id": plan.experiment_id,
            "case_id": plan.case_id,
            "arm": arm,
            "probe_id": probe_id,
            "repetition": repetition,
            "recovered": recovered,
            "request_id": result.request_id,
            "answer": result.answer,
            "status": result.status,
            "versionset_id": result.versionset_id,
            "versionset_digest": (
                self.cell_versionset_refs.get(arm, {}).get("digest")
            ),
            "versionset_revision": (
                self.cell_versionset_refs.get(arm, {}).get("revision")
            ),
            "prompt_digest": result.prompt_digest,
            "kb_manifest_digest": result.kb_manifest_digest,
            "model_digest": result.model_digest,
            "provider_origin": result.provider_origin,
            "retrieval": result.retrieval,
            "trace_id": result.trace_id,
            "raw": result.raw,
        }
        digest = digest_of_bytes(canonical_json_bytes(artifact))
        if self.artifact_dir is None:
            return (
                f"unpersisted://{plan.experiment_id}/{arm}/{probe_id}-rep{repetition}.json",
                digest,
            )
        path = self.artifact_dir / plan.experiment_id / "probe-outputs" / arm / f"{probe_id}-rep{repetition}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path.resolve().as_uri(), digest

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
