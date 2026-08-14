"""双轨评测门禁（spec §3.4 / §9.3 / T6 / D-001）。

- 规则轨：确定性断言（探针集全过、digest 匹配、schema 合规、live 可用性）。
- 裁判轨：LLM 裁判按 rubric 打分；**裁判模型 digest ≠ 运动员模型 digest 硬校验**。
- contract/replay 确定性测试 与 live-provider E2E **分开报告**（禁止合并成一个总分）。
- live 轨 UNAVAILABLE → 不得仅凭确定性轨放行，转人工（D-001 #3）。

门禁报告必须通过 contracts/schemas/gate-report.schema.json 校验。
"""
from __future__ import annotations

import json
import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .config import Settings
from .digests import sha256_digest
from .models import ProbeSet
from .probe_judge import judge_probe
from .report import _now_iso, new_id, assert_schema_valid

if TYPE_CHECKING:
    from .llm import LLMClient

OFFICIAL_STEPFUN_BASE_URL = "https://api.stepfun.com/step_plan/v1"

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

评分标准（语义承诺兑现为准；"必须包含/不得包含"已由确定性规则轨单独校验，
你只复核规则未覆盖的语义问题）：
- 1.0：完全兑现描述中的语义承诺，无遗漏无越界
- 0.7-0.9：基本兑现，轻微遗漏但不改变承诺
- 0.4-0.6：部分兑现，关键承诺缺失或语义冲突
- 0.0-0.3：未兑现，或包含不得出现的内容/拒答
- 判 fail 必须指出候选回复中可逐字指认的具体缺陷（引原文片段）；
  无法核验的表述差异（例如对照一份你看不到的原文要求"逐字一致"）不得作为 fail 理由。

只输出一个 JSON 对象（不要 markdown 围栏）：
{{"score": 0.0, "pass": false, "rationale": "一句话理由"}}"""


@dataclass
class GateCandidate:
    """被评测对象（候选版本）：答案 + 版本 digest 锁定。"""
    target_versionset_digest: str
    probe_set_digest: str
    regression_suite_digest: str
    answers: dict[str, str] = field(default_factory=dict)          # probe_id -> answer
    provider_origins: dict[str, str] = field(default_factory=dict) # probe_id -> authoritative origin
    athlete_model_digest: str = ""
    source: str = "live"                                            # live | replay

    def validate(self) -> list[str]:
        errs = []
        digest_re = re.compile(r"^sha256:[0-9a-f]{64}$")
        if not digest_re.fullmatch(self.target_versionset_digest):
            errs.append("target_versionset_digest 必须为 sha256:")
        if not digest_re.fullmatch(self.probe_set_digest):
            errs.append("probe_set_digest 必须为 sha256:")
        if not digest_re.fullmatch(self.regression_suite_digest):
            errs.append("regression_suite_digest 必须为 sha256:")
        if self.source not in ("live", "replay"):
            errs.append("source 必须为 live|replay")
        if self.source == "live":
            if set(self.provider_origins) != set(self.answers):
                errs.append("live 候选必须为每条答案绑定 provider origin")
            elif any(
                origin != OFFICIAL_STEPFUN_BASE_URL
                for origin in self.provider_origins.values()
            ):
                errs.append("live 候选必须来自官方 StepFun endpoint")
        return errs


@dataclass(frozen=True)
class SuiteResult:
    """由实际 suite 执行器产生的不可变结果摘要。

    GateRunner 只负责判定，不能让调用者用四个裸计数冒充执行结果。生产调用方必须把
    stdout/stderr 报告落为 artifact，并同时提供其内容 digest。测试可构造该对象作为
    明确的 contract/replay 替身。
    """

    suite: str
    kind: str
    status: str
    n_passed: int
    n_failed: int
    report_ref: str
    report_digest: str

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.kind not in ("contract", "replay"):
            errors.append(f"kind={self.kind!r} 非 contract|replay")
        if self.status not in ("passed", "failed", "error"):
            errors.append(f"status={self.status!r} 非 passed|failed|error")
        if self.n_passed < 0 or self.n_failed < 0:
            errors.append("测试计数不得为负")
        if self.n_passed + self.n_failed <= 0:
            errors.append("测试结果为空（0/0 不得视为通过）")
        if self.status == "passed" and self.n_failed != 0:
            errors.append("passed suite 的 n_failed 必须为 0")
        if self.status == "failed" and self.n_failed <= 0:
            errors.append("failed suite 的 n_failed 必须大于 0")
        if not self.report_ref:
            errors.append("report_ref 缺失")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.report_digest):
            errors.append("report_digest 必须为 sha256:<64 hex>")
        return errors


class LLMJudge:
    """裁判轨 LLM 裁判：按 rubric 打分，输出结构化 JSON。"""

    # 裁判打分参数（model_digest property 与 score() 必须共用同一组）。
    # max_tokens=2048：reasoning 模型（glm-5.2 / step-3.x-flash）思考链也消耗
    # completion token，1024 会烧满导致 content 为空 → 解析失败按 0 分
    # （实测 cs-011 裁判空回复误杀）。
    SCORE_PARAMS = {"temperature": 0.0, "max_tokens": 2048}

    def __init__(self, settings: Settings, model: str, *, deadline_monotonic: float | None = None):
        # Keep contract/replay tooling importable without the live-provider SDK.
        # The provider dependency is loaded only when a real LLM judge is used.
        from .llm import LLMClient

        self.settings = settings
        self.model = model
        self.deadline_monotonic = deadline_monotonic
        if settings.has_judge_provider_override:
            self.llm = LLMClient(
                settings,
                api_key=settings.judge_api_key,
                base_url=settings.judge_base_url,
                provider=settings.judge_provider,
            )
        else:
            self.llm = LLMClient(settings)
        self.evidence: list[dict[str, Any]] = []

    @property
    def model_digest(self) -> str:
        return self.llm.model_digest_for(self.model, self.SCORE_PARAMS)

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
            params=self.SCORE_PARAMS,
            deadline_monotonic=self.deadline_monotonic,
        )
        parsed = self._parse(resp.content)
        if parsed is None:
            parsed = {"score": 0.0, "pass": False, "rationale": "裁判输出无法解析，按 0 分处理"}
        self.evidence.append(
            {
                "probe_id": probe.id,
                "provider_request_id": resp.request_id,
                "model_digest": resp.model_digest,
                "answer_digest": "sha256:" + hashlib.sha256(answer.encode("utf-8")).hexdigest(),
                "raw_response": resp.content,
                "raw_response_digest": "sha256:"
                + hashlib.sha256(resp.content.encode("utf-8")).hexdigest(),
                "parsed": parsed,
                "usage": resp.usage,
            }
        )
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
        pass_value = obj.get("pass")
        if not isinstance(pass_value, bool):
            return None
        try:
            score = float(obj.get("score", 0.0))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(score):
            return None
        score = max(0.0, min(1.0, score))
        return {
            "score": score,
            "pass": pass_value,
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
        contract_result: SuiteResult,
        replay_result: SuiteResult,
        artifact_refs: list[dict[str, str]],
        live_available: bool = True,
        policy_profile: str = "live",
        eval_id: str | None = None,
        report_id: str | None = None,
    ) -> dict:
        """生成 gate-report（schema 校验通过）。

        contract/replay 必须由独立执行体产生 SuiteResult，并提供真实 artifact digest。
        live_available=False 表示 live-provider E2E 不可用（额度/网络/裁判模型缺失）。
        """
        if policy_profile not in ("live", "isolated-replay"):
            raise ValueError(f"unsupported gate policy profile: {policy_profile!r}")
        candidate_errs = candidate.validate()
        if policy_profile == "isolated-replay" and candidate.source != "replay":
            candidate_errs.append("isolated-replay profile requires candidate.source=replay")
        rule = self._rule_track(
            candidate,
            candidate_errs,
            live_available,
            policy_profile=policy_profile,
        )
        judge_track = self._judge_track(candidate)
        det = self._deterministic_tests(contract_result, replay_result)

        artifact_errors = self._validate_artifact_refs(
            artifact_refs,
            required={(contract_result.report_ref, contract_result.report_digest), (replay_result.report_ref, replay_result.report_digest)},
        )
        suite_refs = {
            (contract_result.report_ref, contract_result.report_digest),
            (replay_result.report_ref, replay_result.report_digest),
        }
        candidate_refs = [
            ref
            for ref in artifact_refs
            if (ref.get("uri", ""), ref.get("digest", "")) not in suite_refs
        ]
        if candidate.source == "live" and not candidate_refs:
            artifact_errors.append("live candidate response evidence is missing")
        live = self._live_e2e(
            candidate,
            live_available,
            report_ref=candidate_refs[0].get("uri") if candidate_refs else None,
            policy_profile=policy_profile,
        )
        if artifact_errors:
            det = {
                **det,
                "status": "error",
                "suites": [
                    *det["suites"],
                    {
                        "suite": "artifact-integrity",
                        "kind": "contract",
                        "status": "error",
                        "n_passed": 0,
                        "n_failed": 1,
                        "report_ref": "evidence://gate/artifact-integrity",
                    },
                ],
            }

        overall = self._overall(
            rule["status"],
            judge_track["status"],
            det["status"],
            live["status"],
            policy_profile=policy_profile,
        )
        report = {
            "schema_version": "0.2.0",
            "policy_profile": policy_profile,
            "report_id": report_id or new_id("gate"),
            "eval_id": eval_id or new_id("eval"),
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
            "artifact_refs": artifact_refs,
            "created_at": _now_iso(),
        }
        assert_schema_valid(report, "gate-report.schema.json")
        return report

    # ------------------------------------------------------------------ 规则轨
    def _rule_track(
        self,
        candidate: GateCandidate,
        candidate_errs: list[str],
        live_available: bool,
        *,
        policy_profile: str,
    ) -> dict:
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
        if policy_profile == "isolated-replay":
            checks.append({
                "check_id": "rule-policy-profile",
                "description": "本报告明确限定为隔离 contract/replay 证据，不冒充 live-provider E2E",
                "status": "passed",
                "detail": "policy_profile=isolated-replay; live-provider status is reported separately as skipped",
            })
        else:
            checks.append({
                "check_id": "rule-live-e2e-availability",
                "description": "live-provider E2E 可用（缺 key/裁判模型 时不可自动放行）",
                "status": "passed" if live_available else "skipped",
                "detail": "live E2E 可用" if live_available else "LIVE_UNAVAILABLE：MVP 不可仅凭确定性轨放行，转人工",
            })

        if any(c["status"] == "failed" for c in checks):
            status = "failed"
        elif any(c["status"] == "skipped" for c in checks):
            status = "error"
        else:
            status = "passed"
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
            try:
                result = self.judge.score(probe, ans)
            except Exception as exc:  # noqa: BLE001 -- provider timeout/error must persist as ERROR
                return {
                    "status": "error",
                    "judge_model_digest": judge_digest,
                    "athlete_model_digest": athlete_digest,
                    "pass_threshold": self.pass_threshold,
                    "scores": [
                        {
                            "probe_id": pid,
                            "score": 0.0,
                            "pass": False,
                            "rationale_ref": f"evidence://judge-error/{type(exc).__name__}",
                        }
                    ],
                }
            scores.append({
                "probe_id": pid,
                "score": result["score"],
                "pass": result["pass"],
                "rationale_ref": f"evidence://gate/judge/{pid}",
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
    def _deterministic_tests(self, contract_result: SuiteResult, replay_result: SuiteResult) -> dict:
        suites = []
        aggregate_status = "passed"
        for expected_kind, result in (("contract", contract_result), ("replay", replay_result)):
            errors = result.validate()
            if result.kind != expected_kind:
                errors.append(f"期望 kind={expected_kind}，实际 {result.kind}")
            status = "error" if errors else result.status
            if status == "error":
                aggregate_status = "error"
            elif status == "failed" and aggregate_status != "error":
                aggregate_status = "failed"
            suites.append(
                {
                    "suite": result.suite,
                    "kind": expected_kind,
                    "status": status,
                    "n_passed": result.n_passed,
                    "n_failed": max(result.n_failed, 1 if errors else 0),
                    "report_ref": result.report_ref,
                }
            )
        status = aggregate_status
        return {"status": status, "suites": suites}

    # ------------------------------------------------------------------ live E2E
    def _live_e2e(
        self,
        candidate: GateCandidate,
        live_available: bool,
        *,
        report_ref: str | None,
        policy_profile: str,
    ) -> dict:
        if policy_profile == "isolated-replay":
            return {
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
        if not live_available or not candidate.answers:
            return {
                "status": "skipped",
                "provider": "stepfun",
                "suites": [{"suite": "cs-e2e-smoke", "status": "skipped", "n_passed": 0, "n_failed": 0}],
            }
        if candidate.source != "live":
            return {
                "status": "error",
                "provider": "replay",
                "suites": [
                    {
                        "suite": "cs-e2e-smoke",
                        "status": "error",
                        "n_passed": 0,
                        "n_failed": max(1, len(candidate.answers)),
                        "report_ref": "evidence://gate/replay-cannot-satisfy-live",
                    }
                ],
            }
        pmap = self.probe_set.by_id()
        n_passed = 0
        n_failed = 0
        for pid, ans in candidate.answers.items():
            if pid in pmap and judge_probe(pmap[pid], ans)[0]:
                n_passed += 1
            else:
                n_failed += 1
        if n_passed == 0 and n_failed == 0:
            status = "error"
            n_failed = 1
        else:
            status = "passed" if n_failed == 0 else "failed"
        return {
            "status": status,
            "provider": "stepfun",
            "suites": [
                {
                    "suite": "cs-e2e-smoke",
                    "status": status,
                    "n_passed": n_passed,
                    "n_failed": n_failed,
                    "report_ref": report_ref or "evidence://gate/missing-live-artifact",
                }
            ],
        }

    # ------------------------------------------------------------------ 总结论
    @staticmethod
    def _overall(
        rule: str,
        judge: str,
        det: str,
        live: str,
        *,
        policy_profile: str = "live",
    ) -> str:
        statuses = (rule, judge, det) if policy_profile == "isolated-replay" else (rule, judge, det, live)
        if any(status not in ("passed", "failed", "error", "skipped") for status in statuses):
            return "error"
        if "error" in statuses or "skipped" in statuses:
            return "error"
        if "failed" in statuses:
            return "failed"
        return "passed" if all(status == "passed" for status in statuses) else "error"

    @staticmethod
    def _validate_artifact_refs(
        refs: list[dict[str, str]], *, required: set[tuple[str, str]]
    ) -> list[str]:
        errors: list[str] = []
        if not refs:
            return ["artifact_refs 为空"]
        observed: set[tuple[str, str]] = set()
        for ref in refs:
            uri = ref.get("uri", "")
            digest = ref.get("digest", "")
            if not uri:
                errors.append("artifact uri 缺失")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                errors.append(f"artifact digest 非法: {digest!r}")
            observed.add((uri, digest))
        missing = required - observed
        if missing:
            errors.append(f"suite artifact 缺失: {sorted(missing)!r}")
        return errors


def build_error_gate_report(
    *,
    eval_id: str,
    target_versionset_digest: str,
    regression_suite_digest: str,
    probe_set_digest: str,
    error_ref: str,
    artifact_refs: list[dict[str, str]],
    report_id: str | None = None,
) -> dict[str, Any]:
    """把执行器 timeout/exception 变成可持久化、schema-valid 的 fail-closed 报告。"""

    report = {
        "schema_version": "0.2.0",
        "policy_profile": "live",
        "report_id": report_id or new_id("gate"),
        "eval_id": eval_id,
        "subject": {
            "target_versionset_digest": target_versionset_digest,
            "regression_suite_digest": regression_suite_digest,
            "probe_set_digest": probe_set_digest,
        },
        "rule_track": {
            "status": "error",
            "checks": [
                {
                    "check_id": "gate-executor-error",
                    "status": "failed",
                    "description": "门禁执行器未能完成，按 fail-closed 处理",
                    "detail": error_ref,
                }
            ],
        },
        "judge_track": {
            "status": "error",
            "judge_model_digest": sha256_digest({"judge": "unavailable"}),
            "athlete_model_digest": sha256_digest({"athlete": "unknown"}),
            "pass_threshold": 1.0,
            "scores": [
                {
                    "probe_id": "__evaluator_error__",
                    "score": 0.0,
                    "pass": False,
                    "rationale_ref": error_ref,
                }
            ],
        },
        "deterministic_tests": {
            "status": "error",
            "suites": [
                {
                    "suite": "gate-executor-contract",
                    "kind": "contract",
                    "status": "error",
                    "n_passed": 0,
                    "n_failed": 1,
                    "report_ref": error_ref,
                },
                {
                    "suite": "gate-executor-replay",
                    "kind": "replay",
                    "status": "error",
                    "n_passed": 0,
                    "n_failed": 1,
                    "report_ref": error_ref,
                },
            ],
        },
        "live_provider_e2e": {
            "status": "error",
            "provider": "stepfun",
            "suites": [
                {
                    "suite": "cs-e2e-smoke",
                    "status": "error",
                    "n_passed": 0,
                    "n_failed": 1,
                    "report_ref": error_ref,
                }
            ],
        },
        "overall_status": "error",
        "artifact_refs": artifact_refs,
        "created_at": _now_iso(),
    }
    assert_schema_valid(report, "gate-report.schema.json")
    return report
