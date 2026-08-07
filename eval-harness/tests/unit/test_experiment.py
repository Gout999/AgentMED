"""实验执行器单测：录制样例回放（无 live 网络），验证裁决与双 schema。"""
import json

import pytest

from eval_harness.client import ChatResult
from eval_harness.experiment import ArmDriver, ExperimentRunner
from eval_harness.models import ExperimentPlan
from eval_harness.probe_loader import frozen_digest
from eval_harness.report import validate_report


class _State:
    def __init__(self):
        self.fault = False


class FakeClient:
    """按当前 live 状态（fault 与否）从录制样例返回答案。"""

    def __init__(self, probe_set, samples):
        self.ps = probe_set
        self.samples = samples

    def chat(self, message, **kw):
        for p in self.ps.probes:
            if p.input == message:
                state = "b1_fault" if _STATE.fault else "baseline"
                ans = self.samples["states"][state].get(p.id, {}).get("answer", "")
                pd = "sha256:" + ("b" * 64) if _STATE.fault else "sha256:" + ("a" * 64)
                return ChatResult(
                    request_id="req_fake", answer=ans, versionset_id="vs_baseline0000000001",
                    prompt_digest=pd, kb_manifest_digest="sha256:" + "c" * 64,
                    model_digest="sha256:" + "d" * 64, retrieval=[], raw={},
                )
        raise KeyError(message)


class FakeDriver(ArmDriver):
    def setup(self, arm):
        _STATE.fault = arm in ("C", "RK", "RM")

    def cleanup(self):
        _STATE.fault = False


_STATE = _State()


def _plan(probe_set, digest) -> ExperimentPlan:
    return ExperimentPlan(
        experiment_id="exp_test0000000000000000",
        case_id="case_test0000000000000000",
        matrix="five_cell",
        repetitions=3,
        confidence=0.95,
        delta_min=0.2,
        probe_set_digest=digest,
        version_digests={
            "P0": "sha256:" + "a" * 64, "P1": "sha256:" + "b" * 64,
            "K0": "sha256:" + "c" * 64, "K1": "sha256:" + "c" * 64,
            "M0": "sha256:" + "d" * 64, "M1": "sha256:" + "d" * 64,
        },
        discovery=["cs-001", "cs-002", "cs-003"],
        hidden_confirmation=["cs-004", "cs-005"],
        unaffected_controls=["cs-013", "cs-014", "cs-015", "cs-016"],
        random_seed=42,
    )


def _run(probe_set, samples):
    runner = ExperimentRunner(FakeClient(probe_set, samples), probe_set, __import__("eval_harness.config").config.get_settings())
    digest = frozen_digest(probe_set)
    plan = _plan(probe_set, digest)
    return runner.run(plan, FakeDriver(), seed=42, suppress_digest_capture=True)


def test_b1_experiment_attributed_prompt(probe_set, probe_samples):
    res = _run(probe_set, probe_samples)
    assert res.verdict["decision"] == "ATTRIBUTED"
    assert res.verdict["attributed_layer"] == "prompt"
    # 期望恢复形态：C/RK/RM=0，RP/G=1（fixtures/b1 expected_cell_recovery）
    for arm in ("C", "RK", "RM"):
        assert res.bundle["cells"][arm]["recovery_rate"] == 0.0
    for arm in ("RP", "G"):
        assert res.bundle["cells"][arm]["recovery_rate"] == 1.0
    # unaffected controls 全部通过
    for arm in ("C", "RP", "RK", "RM", "G"):
        assert res.bundle["cells"][arm]["control_pass_rate"] == 1.0


def test_b1_evidence_bundle_schema(probe_set, probe_samples):
    res = _run(probe_set, probe_samples)
    assert validate_report(res.bundle, "evidence-bundle.schema.json") == []


def test_b1_attribution_report_schema(probe_set, probe_samples):
    res = _run(probe_set, probe_samples)
    assert validate_report(res.report, "attribution-report.schema.json") == []
    # deltas 用 estimate 字段（attribution-report 口径）
    assert "estimate" in res.report["deltas"]["prompt"]
    assert "delta" not in res.report["deltas"]["prompt"]
    # evidence_bundle_ref digest 与 bundle 实际 digest 一致
    from eval_harness.report import report_digest
    assert res.report["evidence_bundle_ref"]["digest"] == report_digest(res.bundle)


def test_b1_version_digests_recorded(probe_set, probe_samples):
    res = _run(probe_set, probe_samples)
    # K0==K1、M0==M1（B1 单因素只改 prompt）；P0 != P1
    vd = res.report["version_digests"]
    assert vd["P0"] != vd["P1"]
    assert vd["K0"] == vd["K1"]
    assert vd["M0"] == vd["M1"]
    # 每 cell 的 digest 与 plan 对账
    for arm in ("C", "RP", "RK", "RM", "G"):
        v = res.cells[arm].versions
        assert v["prompt_digest"] and v["prompt_digest"].startswith("sha256:")


def test_repetitions_affect_trials(probe_set, probe_samples):
    runner = ExperimentRunner(FakeClient(probe_set, probe_samples), probe_set, __import__("eval_harness.config").config.get_settings())
    digest = frozen_digest(probe_set)
    plan = _plan(probe_set, digest)
    plan.repetitions = 2
    res = runner.run(plan, FakeDriver(), seed=1, suppress_digest_capture=True)
    assert res.report["cells"]["C"]["n_trials"] == 5 * 2
