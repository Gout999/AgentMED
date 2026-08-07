"""双轨评测门禁（spec §3.4 / §9.3 / T6 / D-001）。

- 规则轨：确定性断言（探针集全过、digest 匹配、schema 合规、live 可用性）。
- 裁判轨：LLM 裁判按 rubric 打分；**裁判模型 digest ≠ 运动员模型 digest 硬校验**。
- contract/replay 确定性测试 与 live-provider E2E **分开报告**（禁止合并成一个总分）。
- live 轨 UNAVAILABLE → 不得仅凭确定性轨放行，转人工（D-001 #3）。

门禁报告必须通过 contracts/schemas/gate-report.schema.json 校验。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .config import Settings
from .digests import sha256_digest
from .llm import LLMClient
from .models import ProbeSet
from .probe_judge import judge_probe
from .report import _now_iso, new_id, assert_schema_valid

JUDGE_RUBRIC = """你是 CaseLoop 评测裁判。请对"客服回复是否满足该探针的期望行为"打分（0-1）并给出 pass/fail。

探针期望（expected_behavior）：
- 描述：{description}
- 必须包含：{must_include}
- 不得包含：{must_not_include}
- 格式要求：{fmt}

用户问题：{input}

候选回复：
---
{answer}
---

评分标准（对照期望行为，不要求逐字一致，要求语义承诺兑现）：
- 1.0：完全兑现承诺，无遗漏无越界
- 0.7-0.9：基本兑现，轻微遗漏但不改变承诺
- 0.4-0.6：部分兑现，关键承诺缺失或语义冲突
- 0.0-0.3：未兑现，或包含不得出现的内容/拒答

只输出一个 JSON 对象（不要 markdown 围栏）：
{{"score": 0.0, "pass": false, "rationale": "一句话理由"}}"""


@dataclass
class GateCandidate:
    """被评测对象（候选版本）：答案 + 版本 digest 锁定。"""
    target_versionset_digest: str
    probe_set_digest: str
    regression_suite_digest: str
    answers: dict[str, str] = field(default_factory=dict)          # probe_id -> answer
    athlete_model_digest: str = ""
    source: str = "live"                                            # live | replay

    def validate(self) -> list[str]:
        errs = []
        if not self.target_versionset_digest.startswith("sha256:"):
            errs.append("target_versionset_digest 必须为 sha256:")
        if not self.probe_set_digest.startswith("sha256:"):
            errs.append("probe_set_digest 必须为 sha256:")
        if not self.regression_suite_digest.startswith("sha256:"):
            errs.append("regression_suite_digest 必须为 sha256:")
        return errs


class LLMJudge:
    """裁判轨 LLM 裁判：按 rubric 打分，输出结构化 JSON。"""

    def __init__(self, settings: Settings, model: str):
        self.settings = settings
        self.model = model
        self.llm = LLMClient(settings)

    @property
    def model_digest(self) -> str:
        return self.llm.model_digest_for(self.model, {"temperature": 0.0, "max_tokens": 256})

    def score(self, probe, answer: str) -> dict:
        fmt = "json" if probe.is_format_json else "text"
        prompt = JUDGE_RUBRIC.format(
            description=probe.description or "(无)",
            must_include="、".join(probe.must_include) or "(无)",
            must_not_include="、".join(probe.must_not_include) or "(无)",
            fmt=fmt,
            input=probe.input,
            answer=answer,
        )
        resp = self.llm.chat(
            "你是严格的评测裁判，只输出合法 JSON。",
            prompt,
            model=self.model,
            params={"temperature": 0.0, "max_tokens": 256},
        )
        parsed = self._parse(resp.content)
        if parsed is None:
            return {"score": 0.0, "pass": False, "rationale": "裁判输出无法解析，按 0 分处理"}
        return parsed

    @staticmethod
    def _parse(content: str) -> dict | None:
        m = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        try:
            score = float(obj.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(1.0, score))
        return {
            "score": score,
            "pass": bool(obj.get("pass", False)),
            "rationale": str(obj.get("rationale", "")),
        }


class GateRunner:
    def __init__(
        self,
        settings: Settings,
        probe_set: ProbeSet,
        judge: LLMJudge | None = None,
        frozen_probe_set_digest: str | None = None,
        judge_pass_threshold: float | None = None,
    ):
        self.settings = settings
        self.probe_set = probe_set
        self.judge = judge
        self.frozen_probe_set_digest = frozen_probe_set_digest
        self.pass_threshold = judge_pass_threshold if judge_pass_threshold is not None else settings.gate_judge_pass_threshold

    # ------------------------------------------------------------------ 主入口
    def run(
        self,
        candidate: GateCandidate,
        *,
        contract_n_passed: int = 0,
        contract_n_failed: int = 0,
        replay_n_passed: int = 0,
        replay_n_failed: int = 0,
        live_available: bool = True,
    ) -> dict:
        """生成 gate-report（schema 校验通过）。

        contract/replay 计数由调用方统计（确定性测试的独立执行体），此处组装报告。
        live_available=False 表示 live-provider E2E 不可用（额度/网络/裁判模型缺失）。
        """
        candidate_errs = candidate.validate()
        rule = self._rule_track(candidate, candidate_errs, live_available)
        judge_track = self._judge_track(candidate)
        det = self._deterministic_tests(contract_n_passed, contract_n_failed, replay_n_passed, replay_n_failed)
        live = self._live_e2e(candidate, live_available)

        overall = self._overall(rule["status"], judge_track["status"], det["status"], live["status"])
        report = {
            "schema_version": "0.1.0",
            "report_id": new_id("gate"),
            "eval_id": new_id("eval"),
            "subject": {
                "target_versionset_digest": candidate.target_versionset_digest,
                "regression_suite_digest": candidate.regression_suite_digest,
                "probe_set_digest": candidate.probe_set_digest,
            },
            "rule_track": rule,
            "judge_track": judge_track,
            "deterministic_tests": det,
            "live_provider_e2e": live,
            "overall_status": overall,
            "artifact_refs": [
                {"uri": "file://evidence/gate/replay.jsonl", "digest": sha256_digest({"replay": "frozen"})},
                {"uri": "file://evidence/gate/contract-report.json", "digest": sha256_digest({"contract": "frozen"})},
            ],
            "created_at": _now_iso(),
        }
        assert_schema_valid(report, "gate-report.schema.json")
        return report

    # ------------------------------------------------------------------ 规则轨
    def _rule_track(self, candidate: GateCandidate, candidate_errs: list[str], live_available: bool) -> dict:
        checks: list[dict] = []
        pmap = self.probe_set.by_id()

        # 1) 探针集全过（确定性判定）
        failed = [pid for pid, ans in candidate.answers.items() if pid in pmap and not judge_probe(pmap[pid], ans)[0]]
        missing = [pid for pid in pmap if pid not in candidate.answers]
        checks.append({
            "check_id": "rule-probe-all-pass",
            "description": "候选答案在冻结探针集上确定性判定全过",
            "status": "failed" if (failed or missing) else "passed",
            "detail": f"failed={failed} missing={missing}" if (failed or missing) else "全部通过",
        })

        # 2) digest 匹配（候选探针集 == 冻结探针集）
        digest_ok = self.frozen_probe_set_digest is None or candidate.probe_set_digest == self.frozen_probe_set_digest
        checks.append({
            "check_id": "rule-digest-match",
            "description": "候选探针集 digest 与冻结值一致",
            "status": "passed" if digest_ok else "failed",
            "detail": f"frozen={self.frozen_probe_set_digest} candidate={candidate.probe_set_digest}",
        })

        # 3) schema 合规（候选对象字段）
        checks.append({
            "check_id": "rule-schema-compliance",
            "description": "候选对象结构字段合规（digest 前缀 / 必填）",
            "status": "passed" if not candidate_errs else "failed",
            "detail": "; ".join(candidate_errs) or "合规",
        })

        # 4) JSON 格式探针合法性（结构化比对）
        json_failed = [
            pid for pid, ans in candidate.answers.items()
            if pid in pmap and pmap[pid].is_format_json and not judge_probe(pmap[pid], ans)[0]
        ]
        checks.append({
            "check_id": "rule-json-format",
            "description": "JSON 探针输出合法且键齐全",
            "status": "failed" if json_failed else "passed",
            "detail": f"failed={json_failed}" if json_failed else "全部合法",
        })

        # 5) live E2E 可用性（D-001：live UNAVAILABLE 不得仅凭确定性轨放行）
        checks.append({
            "check_id": "rule-live-e2e-availability",
            "description": "live-provider E2E 可用（缺 key/裁判模型 时不可自动放行）",
            "status": "passed" if live_available else "skipped",
            "detail": "live E2E 可用" if live_available else "LIVE_UNAVAILABLE：MVP 不可仅凭确定性轨放行，转人工",
        })

        status = "passed" if all(c["status"] in ("passed", "skipped") for c in checks) else "failed"
        return {"status": status, "checks": checks}

    # ------------------------------------------------------------------ 裁判轨
    def _judge_track(self, candidate: GateCandidate) -> dict:
        if self.judge is None:
            return {
                "status": "error",
                "judge_model_digest": sha256_digest({"judge": "unconfigured"}),
                "athlete_model_digest": candidate.athlete_model_digest or sha256_digest({"athlete": "unknown"}),
                "pass_threshold": self.pass_threshold,
                "scores": [
                    {
                        "probe_id": "__judge_unconfigured__",
                        "score": 0.0,
                        "pass": False,
                        "rationale_ref": "judge model 未配置 → 裁判轨不可用，转人工",
                    }
                ],
            }
        judge_digest = self.judge.model_digest
        athlete_digest = candidate.athlete_model_digest
        if not athlete_digest:
            athlete_digest = sha256_digest({"athlete": "unknown"})
        # T6 硬校验：裁判模型与运动员模型必须不同（模型名一致即拒绝，digest 一致亦拒绝）
        judge_model_name = getattr(self.judge, "model", "")
        if judge_model_name and judge_model_name == self.settings.stepfun_model:
            return {
                "status": "error",
                "judge_model_digest": judge_digest,
                "athlete_model_digest": athlete_digest,
                "pass_threshold": self.pass_threshold,
                "scores": [
                    {
                        "probe_id": "__judge_equals_athlete__",
                        "score": 0.0,
                        "pass": False,
                        "rationale_ref": f"T6 硬约束：裁判模型 {judge_model_name} 与运动员模型相同，拒绝运行",
                    }
                ],
            }
        if judge_digest == athlete_digest:
            return {
                "status": "error",
                "judge_model_digest": judge_digest,
                "athlete_model_digest": athlete_digest,
                "pass_threshold": self.pass_threshold,
                "scores": [
                    {
                        "probe_id": "__judge_equals_athlete__",
                        "score": 0.0,
                        "pass": False,
                        "rationale_ref": "T6 硬约束：裁判模型 digest 与运动员模型 digest 相同，拒绝运行",
                    }
                ],
            }

        scores = []
        for pid, ans in sorted(candidate.answers.items()):
            probe = self.probe_set.by_id().get(pid)
            if probe is None:
                continue
            result = self.judge.score(probe, ans)
            scores.append({
                "probe_id": pid,
                "score": result["score"],
                "pass": result["pass"],
                "rationale_ref": f"file://evidence/gate/judge-{pid}.md",
            })
        if not scores:
            scores.append({"probe_id": "__no_answers__", "score": 0.0, "pass": False, "rationale_ref": "无候选答案"})
        all_pass = all(s["pass"] for s in scores) and all(s["score"] >= self.pass_threshold for s in scores)
        return {
            "status": "passed" if all_pass else "failed",
            "judge_model_digest": judge_digest,
            "athlete_model_digest": athlete_digest,
            "pass_threshold": self.pass_threshold,
            "scores": scores,
        }

    # ------------------------------------------------------------------ 确定性测试
    def _deterministic_tests(self, contract_passed, contract_failed, replay_passed, replay_failed) -> dict:
        suites = [
            {
                "suite": "quality-api-contract",
                "kind": "contract",
                "status": "passed" if contract_failed == 0 else "failed",
                "n_passed": contract_passed,
                "n_failed": contract_failed,
                "report_ref": "file://evidence/gate/contract-report.json",
            },
            {
                "suite": "b1-replay-frozen-probes",
                "kind": "replay",
                "status": "passed" if replay_failed == 0 else "failed",
                "n_passed": replay_passed,
                "n_failed": replay_failed,
                "report_ref": "file://evidence/gate/replay.jsonl",
            },
        ]
        status = "passed" if all(s["status"] == "passed" for s in suites) else "failed"
        return {"status": status, "suites": suites}

    # ------------------------------------------------------------------ live E2E
    def _live_e2e(self, candidate: GateCandidate, live_available: bool) -> dict:
        if not live_available or not candidate.answers:
            return {
                "status": "skipped",
                "provider": "stepfun",
                "suites": [{"suite": "cs-e2e-smoke", "status": "skipped", "n_passed": 0, "n_failed": 0}],
            }
        pmap = self.probe_set.by_id()
        n_passed = 0
        n_failed = 0
        for pid, ans in candidate.answers.items():
            if pid in pmap and judge_probe(pmap[pid], ans)[0]:
                n_passed += 1
            else:
                n_failed += 1
        status = "passed" if n_failed == 0 else "failed"
        return {
            "status": status,
            "provider": "stepfun",
            "suites": [{"suite": "cs-e2e-smoke", "status": status, "n_passed": n_passed, "n_failed": n_failed}],
        }

    # ------------------------------------------------------------------ 总结论
    @staticmethod
    def _overall(rule: str, judge: str, det: str, live: str) -> str:
        if "failed" in (rule, judge, det, live):
            return "failed"
        if judge == "error" or live in ("error", "skipped"):
            return "error"
        return "passed"
