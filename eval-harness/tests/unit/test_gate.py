"""双轨门禁单测：规则轨 / 裁判轨硬校验 / live UNAVAILABLE 不放行 / schema。"""
import json
from pathlib import Path

import pytest

from eval_harness.config import Settings
from eval_harness.gate import GateCandidate, GateRunner, SuiteResult
from eval_harness.report import validate_report


class FakeJudge:
    """假裁判：指定模型 digest 与每探针得分。"""

    def __init__(self, model_digest: str, score_map: dict | None = None):
        self._digest = model_digest
        self.score_map = score_map or {}

    @property
    def model_digest(self) -> str:
        return self._digest

    def score(self, probe, answer: str, **kwargs) -> dict:
        if probe.id in self.score_map:
            return self.score_map[probe.id]
        return {"score": 0.95, "pass": True, "rationale": "mock ok"}


def _candidate(probe_set, answers: dict | None = None, probe_samples: dict | None = None) -> GateCandidate:
    if answers is None:
        if probe_samples is None:
            probe_samples = json.loads(
                (Path(__file__).resolve().parents[2] / "samples" / "b1_probe_responses.json").read_text(encoding="utf-8")
            )
        # 用录制基线答案作为「良基候选」的默认答案（全部探针应通过）
        answers = {
            pid: item["answer"]
            for pid, item in probe_samples["states"]["baseline"].items()
            if pid in probe_set.by_id()
        }
        # 补全缺失探针（若有）
        for pid in probe_set.by_id():
            answers.setdefault(pid, "符合售后政策与产品参数说明。")
    return GateCandidate(
        target_versionset_digest="sha256:" + "b" * 64,
        probe_set_digest="sha256:" + "c" * 64,
        regression_suite_digest="sha256:" + "d" * 64,
        answers=answers,
        provider_origins={
            probe_id: "https://api.stepfun.com/step_plan/v1"
            for probe_id in answers
        },
        athlete_model_digest="sha256:" + "e" * 64,
    )


def _suite(kind: str, *, status: str = "passed", passed: int = 3, failed: int = 0) -> SuiteResult:
    uri = f"file:///tmp/agentmed-gate-{kind}.json"
    return SuiteResult(
        suite=f"{kind}-suite",
        kind=kind,
        status=status,
        n_passed=passed,
        n_failed=failed,
        report_ref=uri,
        report_digest="sha256:" + ("a" if kind == "contract" else "b") * 64,
    )


def _run(runner: GateRunner, candidate: GateCandidate, **over):
    contract = over.pop("contract_result", _suite("contract"))
    replay = over.pop("replay_result", _suite("replay"))
    artifacts = over.pop(
        "artifact_refs",
        [
            {"uri": contract.report_ref, "digest": contract.report_digest},
            {"uri": replay.report_ref, "digest": replay.report_digest},
            {"uri": "file:///tmp/agentmed-gate-candidate.json", "digest": "sha256:" + "c" * 64},
        ],
    )
    return runner.run(
        candidate,
        contract_result=contract,
        replay_result=replay,
        artifact_refs=artifacts,
        **over,
    )


def test_gate_all_pass(settings, probe_set):
    judge = FakeJudge("sha256:" + "f" * 64)
    runner = GateRunner(settings, probe_set, judge=judge)
    report = _run(
        runner,
        _candidate(probe_set),
        live_available=True,
    )
    assert report["schema_version"] == "0.2.0"
    assert report["overall_status"] == "passed"
    assert report["rule_track"]["status"] == "passed"
    assert report["judge_track"]["status"] == "passed"
    assert validate_report(report, "gate-report.schema.json") == []
    # 双轨分开报告
    assert "deterministic_tests" in report and "live_provider_e2e" in report


def test_gate_rule_track_fails_on_probe(settings, probe_set):
    answers = {p.id: "我们不支持退货。" for p in probe_set.probes}  # 全部拒答
    runner = GateRunner(settings, probe_set, judge=FakeJudge("sha256:" + "f" * 64))
    report = _run(runner, _candidate(probe_set, answers), live_available=True)
    assert report["overall_status"] == "failed"
    assert report["rule_track"]["status"] == "failed"
    rule_ids = [c["check_id"] for c in report["rule_track"]["checks"]]
    assert "rule-probe-all-pass" in rule_ids


def test_gate_judge_equals_athlete_rejected(settings, probe_set):
    same = "sha256:" + "e" * 64  # 与 athlete 相同
    judge = FakeJudge(same)
    runner = GateRunner(settings, probe_set, judge=judge)
    report = _run(runner, _candidate(probe_set), live_available=True)
    assert report["judge_track"]["status"] == "error"
    assert report["overall_status"] != "passed"
    assert "__judge_equals_athlete__" in [s["probe_id"] for s in report["judge_track"]["scores"]]


def test_gate_judge_unconfigured_blocks(settings, probe_set):
    runner = GateRunner(settings, probe_set, judge=None)
    report = _run(runner, _candidate(probe_set), live_available=True)
    assert report["judge_track"]["status"] == "error"
    assert report["overall_status"] != "passed"  # 不得自动放行


def test_gate_live_unavailable_blocks(settings, probe_set):
    """live E2E UNAVAILABLE → 不得仅凭确定性轨放行（D-001 #3）。"""
    runner = GateRunner(settings, probe_set, judge=FakeJudge("sha256:" + "f" * 64))
    report = _run(
        runner,
        _candidate(probe_set),
        live_available=False,
    )
    assert report["live_provider_e2e"]["status"] == "skipped"
    assert report["overall_status"] != "passed"
    assert report["overall_status"] == "error"  # 基础设施不可用，转人工
    # 规则轨显式标出 live 不可用（skipped 检查）
    avail = [c for c in report["rule_track"]["checks"] if c["check_id"] == "rule-live-e2e-availability"]
    assert avail and avail[0]["status"] == "skipped"


@pytest.mark.parametrize(
    "provider_origins",
    [
        {},
        {"*": "http://127.0.0.1:9999/v1"},
    ],
)
def test_gate_live_nonofficial_or_missing_provider_origin_blocks(
    settings,
    probe_set,
    provider_origins,
):
    runner = GateRunner(settings, probe_set, judge=FakeJudge("sha256:" + "f" * 64))
    candidate = _candidate(probe_set)
    if provider_origins:
        candidate.provider_origins = {
            probe_id: provider_origins["*"] for probe_id in candidate.answers
        }
    else:
        candidate.provider_origins = {}

    report = _run(runner, candidate, live_available=True)

    assert report["rule_track"]["status"] == "failed"
    assert report["overall_status"] == "failed"
    schema_check = next(
        check
        for check in report["rule_track"]["checks"]
        if check["check_id"] == "rule-schema-compliance"
    )
    assert schema_check["status"] == "failed"
    assert "provider origin" in schema_check["detail"] or "StepFun" in schema_check["detail"]


def test_gate_live_e2e_failure_blocks(settings, probe_set):
    answers = {p.id: "48 小时内发货。" for p in probe_set.probes}  # 不含政策词 → live E2E 失败
    runner = GateRunner(settings, probe_set, judge=FakeJudge("sha256:" + "f" * 64))
    report = _run(runner, _candidate(probe_set, answers), live_available=True)
    assert report["live_provider_e2e"]["status"] == "failed"
    assert report["overall_status"] == "failed"


def test_llm_judge_parse():
    from eval_harness.gate import LLMJudge
    assert LLMJudge._parse('{"score": 0.85, "pass": true, "rationale": "ok"}') == {
        "score": 0.85, "pass": True, "rationale": "ok",
    }
    # markdown 围栏包裹也解析
    assert LLMJudge._parse('```json\n{"score": 0.5, "pass": false, "rationale": "no"}\n```')["score"] == 0.5
    # 非法输出 → None（调用方按 0 分处理）
    assert LLMJudge._parse("I think it's fine") is None
    # score 越界截断
    assert LLMJudge._parse('{"score": 1.5, "pass": true, "rationale": "x"}')["score"] == 1.0
    # 字符串 "false" 不得被 Python bool() 当作 True；非布尔值 fail closed。
    assert LLMJudge._parse('{"score": 0.9, "pass": "false", "rationale": "bad type"}') is None
    assert LLMJudge._parse('{"score": "NaN", "pass": true, "rationale": "bad score"}') is None


def test_gate_judge_scores_recorded(settings, probe_set):
    judge = FakeJudge("sha256:" + "f" * 64, score_map={"cs-001": {"score": 0.4, "pass": False, "rationale": "缺关键承诺"}})
    runner = GateRunner(settings, probe_set, judge=judge)
    report = _run(runner, _candidate(probe_set), live_available=True)
    scores = {s["probe_id"]: s for s in report["judge_track"]["scores"]}
    assert scores["cs-001"]["pass"] is False
    assert report["judge_track"]["status"] == "failed"
    assert report["overall_status"] == "failed"


def test_gate_empty_contract_result_is_error(settings, probe_set):
    runner = GateRunner(settings, probe_set, judge=FakeJudge("sha256:" + "f" * 64))
    report = _run(
        runner,
        _candidate(probe_set),
        contract_result=_suite("contract", passed=0, failed=0),
        live_available=True,
    )
    assert report["deterministic_tests"]["status"] == "error"
    assert report["overall_status"] == "error"


def test_gate_missing_live_response_evidence_is_error(settings, probe_set):
    runner = GateRunner(settings, probe_set, judge=FakeJudge("sha256:" + "f" * 64))
    contract = _suite("contract")
    replay = _suite("replay")
    report = _run(
        runner,
        _candidate(probe_set),
        contract_result=contract,
        replay_result=replay,
        artifact_refs=[
            {"uri": contract.report_ref, "digest": contract.report_digest},
            {"uri": replay.report_ref, "digest": replay.report_digest},
        ],
        live_available=True,
    )
    assert report["deterministic_tests"]["status"] == "error"
    assert report["overall_status"] == "error"


def test_gate_replay_cannot_impersonate_live(settings, probe_set):
    runner = GateRunner(settings, probe_set, judge=FakeJudge("sha256:" + "f" * 64))
    candidate = _candidate(probe_set)
    candidate.source = "replay"
    report = _run(runner, candidate, live_available=True)
    assert report["live_provider_e2e"]["status"] == "error"
    assert report["overall_status"] == "error"


def test_gate_isolated_replay_is_explicit_and_never_claims_live(settings, probe_set):
    runner = GateRunner(settings, probe_set, judge=FakeJudge("sha256:" + "f" * 64))
    candidate = _candidate(probe_set)
    candidate.source = "replay"
    report = _run(
        runner,
        candidate,
        live_available=False,
        policy_profile="isolated-replay",
    )
    assert report["overall_status"] == "passed"
    assert report["rule_track"]["status"] == "passed"
    assert report["live_provider_e2e"] == {
        "status": "skipped",
        "provider": "replay-not-live",
        "suites": [
            {
                "suite": "live-provider-e2e",
                "status": "skipped",
                "n_passed": 0,
                "n_failed": 0,
            }
        ],
    }


def test_gate_live_candidate_cannot_use_isolated_replay_profile(settings, probe_set):
    runner = GateRunner(settings, probe_set, judge=FakeJudge("sha256:" + "f" * 64))
    report = _run(
        runner,
        _candidate(probe_set),
        live_available=False,
        policy_profile="isolated-replay",
    )
    assert report["rule_track"]["status"] == "failed"
    assert report["overall_status"] == "failed"


def test_gate_judge_timeout_is_persistable_error(settings, probe_set):
    class TimeoutJudge(FakeJudge):
        def score(self, probe, answer):
            raise TimeoutError("judge deadline")

    runner = GateRunner(settings, probe_set, judge=TimeoutJudge("sha256:" + "f" * 64))
    report = _run(runner, _candidate(probe_set), live_available=True)
    assert report["schema_version"] == "0.2.0"
    assert report["judge_track"]["status"] == "error"
    assert report["overall_status"] == "error"
    assert validate_report(report, "gate-report.schema.json") == []
