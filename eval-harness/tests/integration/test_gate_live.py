"""Live 门禁集成：良基候选（基线）应通过双轨（裁判模型可用时）；故障候选必须被拦。

- 规则轨 + 确定性测试 + live E2E 对基线候选 → passed。
- 裁判轨：JUDGE_MODEL 配置且 ≠ 运动员模型时真实打分；否则标记不可用并阻断自动放行。
- 故障候选（B1 注入）必须被规则轨拦截。
"""
import os
import sys
from pathlib import Path

import pytest

from eval_harness.client import QualityAPIClient
from eval_harness.digests import sha256_digest
from eval_harness.gate import GateCandidate, GateRunner, LLMJudge
from eval_harness.gate_executor import CommandSuiteRunner, frozen_gate_suite_digest, write_json_artifact
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


def _collect_answers(client, probe_set, probe_ids, *, versionset_id=None) -> tuple[dict, list]:
    """逐个跑探针并收集答案（串行，遵守 8 RPM 限速）。"""
    answers = {}
    results = []
    for pid in probe_ids:
        probe = probe_set.get(pid)
        r = (
            client.evaluate_versionset(versionset_id, probe.input)
            if versionset_id
            else client.chat(probe.input)
        )
        assert r.status == "ok"
        answers[pid] = r.answer
        results.append(r)
    return answers, results


def _make_judge(settings, athlete_digest):
    judge_model = settings.judge_model.strip()
    if not judge_model:
        return None, "JUDGE_MODEL 未配置 → 裁判轨不可用，转人工（正确阻断行为）"
    if judge_model == settings.stepfun_model:
        return None, f"JUDGE_MODEL={judge_model} 与运动员模型相同 → T6 硬校验拒绝"
    return LLMJudge(settings, judge_model), ""


def _run(runner, candidate, *, evidence_dir: Path, candidate_evidence: dict, live_available: bool):
    """Live test executes the same real contract/replay commands as production gate.run."""
    repo_root = EVAL_ROOT.parent
    contract = CommandSuiteRunner(
        repo_root=repo_root,
        evidence_dir=evidence_dir,
        timeout_seconds=300,
    ).run(
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
    replay = CommandSuiteRunner(
        repo_root=repo_root,
        evidence_dir=evidence_dir,
        timeout_seconds=300,
    ).run(
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
    candidate_ref = write_json_artifact(evidence_dir / "candidate-answers.json", candidate_evidence)
    return runner.run(
        candidate,
        contract_result=contract.result,
        replay_result=replay.result,
        artifact_refs=[
            contract.artifact_ref,
            replay.artifact_ref,
            candidate_ref,
        ],
        live_available=live_available,
    )


@pytest.mark.live
def test_gate_live_baseline_candidate(live_settings, live_probe_set, _client, tmp_path):
    digest = frozen_digest(live_probe_set)
    probe_ids = [p.id for p in live_probe_set.probes]

    active = _client.list_versionsets(status="active", limit=1)["items"][0]
    answers, results = _collect_answers(
        _client,
        live_probe_set,
        probe_ids,
        versionset_id=active["versionset_id"],
    )
    assert {result.versionset_id for result in results} == {active["versionset_id"]}
    assert {result.prompt_digest for result in results} == {active["content"]["prompt"]["digest"]}
    assert {result.kb_manifest_digest for result in results} == {
        active["content"]["kb_manifest"]["manifest_digest"]
    }
    athlete_digest = active["content"]["model"]["digest"]
    candidate = GateCandidate(
        target_versionset_digest=active["digest"],
        probe_set_digest=digest,
        regression_suite_digest=frozen_gate_suite_digest(EVAL_ROOT.parent),
        answers=answers,
        athlete_model_digest=athlete_digest,
    )

    judge, note = _make_judge(live_settings, athlete_digest)
    runner = GateRunner(
        live_settings, live_probe_set, judge=judge,
        frozen_probe_set_digest=digest,
    )
    report = _run(
        runner,
        candidate,
        evidence_dir=tmp_path / "baseline",
        candidate_evidence={
            "target_versionset_id": active["versionset_id"],
            "target_revision": active["revision"],
            "target_versionset_digest": active["digest"],
            "answers": answers,
            "request_ids": [result.request_id for result in results],
        },
        live_available=True,
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
def test_gate_live_faulted_candidate_blocked(live_settings, live_probe_set, _client, tmp_path):
    """Release Controller 预先创建的 B1 候选必须被规则轨拦截。"""
    digest = frozen_digest(live_probe_set)
    probe_ids = ["cs-001", "cs-002", "cs-003", "cs-013", "cs-014"]
    versionset_id = os.environ.get("CASELOOP_B1_FAULT_VERSIONSET_ID", "").strip()
    if not versionset_id:
        pytest.skip(
            "CASELOOP_B1_FAULT_VERSIONSET_ID 未配置；故障候选必须由 Release Controller 预先创建"
        )
    target = _client.get_versionset(versionset_id)
    answers, results = _collect_answers(
        _client,
        live_probe_set,
        probe_ids,
        versionset_id=versionset_id,
    )
    assert {result.versionset_id for result in results} == {versionset_id}
    candidate = GateCandidate(
        target_versionset_digest=target["digest"],
        probe_set_digest=digest,
        regression_suite_digest=frozen_gate_suite_digest(EVAL_ROOT.parent),
        answers=answers,
        athlete_model_digest=(target.get("content") or {}).get("model", {}).get("digest", ""),
    )
    runner = GateRunner(live_settings, live_probe_set, judge=None, frozen_probe_set_digest=digest)
    report = _run(
        runner,
        candidate,
        evidence_dir=tmp_path / "fault",
        candidate_evidence={
            "target_versionset_id": versionset_id,
            "target_revision": target["revision"],
            "target_versionset_digest": target["digest"],
            "answers": answers,
            "request_ids": [result.request_id for result in results],
        },
        live_available=True,
    )
    assert report["rule_track"]["status"] == "failed"
    assert report["overall_status"] != "passed"
    failed_probes = [c["detail"] for c in report["rule_track"]["checks"] if c["check_id"] == "rule-probe-all-pass"]
    assert failed_probes and "cs-001" in failed_probes[0]
