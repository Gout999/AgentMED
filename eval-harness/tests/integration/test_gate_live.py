"""Live 门禁集成：良基候选（基线）应通过双轨（裁判模型可用时）；故障候选必须被拦。

- 规则轨 + 确定性测试 + live E2E 对基线候选 → passed。
- 裁判轨：JUDGE_MODEL 配置且 ≠ 运动员模型时真实打分；否则标记不可用并阻断自动放行。
- 故障候选（B1 注入）必须被规则轨拦截。
"""
import json
from pathlib import Path

import pytest

from eval_harness.client import QualityAPIClient
from eval_harness.digests import sha256_digest
from eval_harness.gate import GateCandidate, GateRunner, LLMJudge
from eval_harness.probe_loader import frozen_digest
from eval_harness.report import validate_report

EVAL_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def _client(live_settings):
    return QualityAPIClient(live_settings)


def _athlete_digest(settings) -> str:
    """用 eval-harness 同口径计算运动员模型 digest（与裁判 digest 可比对）。"""
    return sha256_digest({
        "provider": "stepfun",
        "model": settings.stepfun_model,
        "params": {"temperature": 0.0, "max_tokens": 1024},
    })


def _collect_answers(client, probe_set, probe_ids) -> dict:
    """逐个跑探针并收集答案（串行，遵守 8 RPM 限速）。"""
    answers = {}
    for pid in probe_ids:
        probe = probe_set.get(pid)
        r = client.chat(probe.input)
        answers[pid] = r.answer
    return answers


def _make_judge(settings, athlete_digest):
    judge_model = settings.judge_model.strip()
    if not judge_model:
        return None, "JUDGE_MODEL 未配置 → 裁判轨不可用，转人工（正确阻断行为）"
    if judge_model == settings.stepfun_model:
        return None, f"JUDGE_MODEL={judge_model} 与运动员模型相同 → T6 硬校验拒绝"
    return LLMJudge(settings, judge_model), ""


@pytest.mark.live
def test_gate_live_baseline_candidate(live_settings, live_probe_set, _client):
    digest = frozen_digest(live_probe_set)
    probe_ids = [p.id for p in live_probe_set.probes]

    athlete_digest = _athlete_digest(live_settings)
    answers = _collect_answers(_client, live_probe_set, probe_ids)
    candidate = GateCandidate(
        target_versionset_digest="sha256:" + "f" * 64,
        probe_set_digest=digest,
        regression_suite_digest="sha256:" + "e" * 64,
        answers=answers,
        athlete_model_digest=athlete_digest,
    )

    judge, note = _make_judge(live_settings, athlete_digest)
    runner = GateRunner(
        live_settings, live_probe_set, judge=judge,
        frozen_probe_set_digest=digest,
    )
    report = runner.run(
        candidate,
        contract_n_passed=12, contract_n_failed=0,
        replay_n_passed=16, replay_n_failed=0,
        live_available=bool(live_settings.has_stepfun_key),
    )
    assert validate_report(report, "gate-report.schema.json") == []
    # 规则轨 + 确定性 + live E2E 全过（良基候选）
    assert report["rule_track"]["status"] == "passed"
    assert report["deterministic_tests"]["status"] == "passed"
    assert report["live_provider_e2e"]["status"] == "passed"
    if judge is None:
        # 无裁判模型 → 不得自动放行（D-001 #3 / T6）
        assert report["overall_status"] != "passed", note
    else:
        assert report["overall_status"] == "passed"
    print(f"\n裁判轨状态: {report['judge_track']['status']}；overall={report['overall_status']}；{note}")


@pytest.mark.live
def test_gate_live_faulted_candidate_blocked(live_settings, live_probe_set, _client):
    """B1 故障候选：规则轨必须拦截（overall != passed）。"""
    digest = frozen_digest(live_probe_set)
    probe_ids = ["cs-001", "cs-002", "cs-003", "cs-013", "cs-014"]
    _client.inject_fault("B1")
    try:
        answers = _collect_answers(_client, live_probe_set, probe_ids)
    finally:
        _client.reset_faults()
    candidate = GateCandidate(
        target_versionset_digest="sha256:" + "f" * 64,
        probe_set_digest=digest,
        regression_suite_digest="sha256:" + "e" * 64,
        answers=answers,
        athlete_model_digest="sha256:" + "a" * 64,
    )
    runner = GateRunner(live_settings, live_probe_set, judge=None, frozen_probe_set_digest=digest)
    report = runner.run(candidate, live_available=bool(live_settings.has_stepfun_key))
    assert report["rule_track"]["status"] == "failed"
    assert report["overall_status"] != "passed"
    failed_probes = [c["detail"] for c in report["rule_track"]["checks"] if c["check_id"] == "rule-probe-all-pass"]
    assert failed_probes and "cs-001" in failed_probes[0]
