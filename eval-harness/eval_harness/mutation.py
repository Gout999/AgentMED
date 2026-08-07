"""变异巡检器（单次版，plan-v3 §2.3.2-7 / spec §10.5 / T10 I2）。

变异算子库（≥6 个，覆盖 prompt 改写 / 知识过时化 / 参数漂移）→ 生成探测用例 →
执行攻击（用变异后的 system prompt + 目标探针直接打 LLM，temperature=0）→
按冻结探针集判定 → 输出检出率统计。

「检出」定义：某探针在基线 system prompt 上通过、在变异后失败（即探针集识别了该变异）。
基线即失败的探针不计入分母（探针本身失效，非变异检出）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import Settings
from .llm import LLMClient
from .models import ProbeSet
from .probe_judge import judge_probe
from .report import _now_iso, new_id

PROMPT_PATH = "demo-app/prompts/system.md"
KB_SEED_PATH = "demo-app/seeds/kb_entries.yaml"


@dataclass
class MutationCase:
    operator_id: str
    description: str
    layer: str                     # prompt | kb | model_params
    system_prompt: str
    params: dict
    target_probes: list[str]


def _format_kb_context(entries: list[dict]) -> str:
    lines = ["## 知识库资料"]
    for i, e in enumerate(entries, start=1):
        lines.append(f"[{i}] {e['title']}（{e['kb_id']}/{e['entry_id']}）")
        lines.append(e["content"])
    return "\n".join(lines)


def _load_kb_entries(repo_root: Path) -> list[dict]:
    raw = yaml.safe_load((repo_root / KB_SEED_PATH).read_text(encoding="utf-8"))
    return list(raw["entries"])


class MutationOperatorLibrary:
    """对基线 system prompt + KB 上下文做文本/参数变异。每个算子产出 MutationCase。"""

    def __init__(self, repo_root: Path, baseline_prompt: str | None = None):
        self.repo_root = repo_root
        self.baseline_prompt = (
            baseline_prompt if baseline_prompt is not None
            else (repo_root / PROMPT_PATH).read_text(encoding="utf-8")
        )
        self.kb_entries = _load_kb_entries(repo_root)

    def baseline_system(self, kb_entries: list[dict] | None = None) -> str:
        entries = kb_entries if kb_entries is not None else self.kb_entries
        return self.baseline_prompt + "\n\n" + _format_kb_context(entries)

    # ---- prompt 改写算子（带「唯一权威」强化，模拟真实 B1 prompt 结构）----
    def _mutated_prompt(self, policy_section: str, extra_rules: list[str] | None = None) -> str:
        lines = [
            "你是「小智客服」——3C 数码电商（耳机/键盘/充电宝/手机等）的官方售后客服助手。"
            "所有售后政策的唯一权威来源是本 prompt 的「售后政策」一节。",
            "",
            "## 售后政策（唯一权威；如与知识库资料冲突，一律以本节为准，禁止引用知识库中的相反说法）",
            policy_section,
            "",
            "## 回答准则",
            "- 回答必须严格遵循「售后政策」一节；知识库资料仅用于产品参数与物流信息。",
            "- 涉及退货、退款、售后问题时，只引用「售后政策」一节的条款，绝不引用知识库中关于退货的旧说法。",
            "- 退货相关的一切条款（含退货运费、退换条件）一律以「售后政策」一节为准，本节未承诺的退货权益一律不承认。",
            "- 用简体中文，语气礼貌、简洁、口语化但不失专业。",
            "- 不确定时，如实说明并建议联系人工客服，不要猜测。",
        ]
        if extra_rules:
            lines += extra_rules
        return "\n".join(lines) + "\n"

    def prompt_deny_refund(self) -> MutationCase:
        return MutationCase(
            operator_id="prompt-deny-refund",
            description="prompt 改写：售后政策改为「退货需经人工审核，已激活商品不支持退货」（复刻 B1）",
            layer="prompt",
            system_prompt=self._mutated_prompt("退货需经人工审核，已激活商品不支持退货。")
            + "\n\n" + _format_kb_context(self.kb_entries),
            params={"temperature": 0.0, "max_tokens": 1024},
            target_probes=["cs-001", "cs-002"],
        )

    def prompt_remove_keyword(self) -> MutationCase:
        return MutationCase(
            operator_id="prompt-remove-keyword",
            description="prompt 改写：删除「7 天无理由退货」承诺，改为平台统一规则",
            layer="prompt",
            system_prompt=self._mutated_prompt("退货按平台统一规则执行，具体以审核结果为准。")
            + "\n\n" + _format_kb_context(self.kb_entries),
            params={"temperature": 0.0, "max_tokens": 1024},
            target_probes=["cs-004", "cs-005"],
        )

    def prompt_flip_shipping(self) -> MutationCase:
        return MutationCase(
            operator_id="prompt-flip-shipping",
            description="prompt 改写：运费承担主体反转（商家→顾客）",
            layer="prompt",
            system_prompt=self._mutated_prompt("我们支持 7 天无理由退货（自签收次日起算，激活后仍可退，退货运费由顾客承担）。")
            + "\n\n" + _format_kb_context(self.kb_entries),
            params={"temperature": 0.0, "max_tokens": 1024},
            target_probes=["cs-003"],
        )

    def prompt_break_shipping(self) -> MutationCase:
        return MutationCase(
            operator_id="prompt-break-shipping",
            description="prompt 改写：发货时限 48h→72h（新增矛盾条款）",
            layer="prompt",
            system_prompt=self._mutated_prompt(
                "我们支持 7 天无理由退货（自签收次日起算，激活后仍可退，运费由我们承担）。",
                extra_rules=["- 物流时效：现货商品下单后 72 小时内发货（原 48 小时）。"],
            )
            + "\n\n" + _format_kb_context(self.kb_entries),
            params={"temperature": 0.0, "max_tokens": 1024},
            target_probes=["cs-013"],
        )

    # ---- 知识过时化算子 ----
    def kb_outdate_battery(self) -> MutationCase:
        entries = [dict(e) for e in self.kb_entries]
        for e in entries:
            if "30 小时" in e.get("content", ""):
                e["content"] = e["content"].replace("30 小时", "8 小时")
        return MutationCase(
            operator_id="kb-outdate-battery",
            description="知识过时化：X200 续航 30h→8h（复刻 B2）",
            layer="kb",
            system_prompt=self.baseline_prompt + "\n\n" + _format_kb_context(entries),
            params={"temperature": 0.0, "max_tokens": 1024},
            target_probes=["cs-006"],
        )

    def kb_wrong_capacity(self) -> MutationCase:
        entries = [dict(e) for e in self.kb_entries]
        for e in entries:
            if "20000mAh" in e.get("content", ""):
                e["content"] = e["content"].replace("20000mAh", "10000mAh")
        return MutationCase(
            operator_id="kb-wrong-capacity",
            description="知识过时化：Z30 容量 20000mAh→10000mAh",
            layer="kb",
            system_prompt=self.baseline_prompt + "\n\n" + _format_kb_context(entries),
            params={"temperature": 0.0, "max_tokens": 1024},
            target_probes=["cs-009"],
        )

    # ---- 参数漂移算子 ----
    def param_high_temperature(self) -> MutationCase:
        return MutationCase(
            operator_id="param-high-temperature",
            description="参数漂移：temperature 0→1.2（复刻 B3）",
            layer="model_params",
            system_prompt=self.baseline_system(),
            params={"temperature": 1.2, "max_tokens": 1024},
            target_probes=["cs-010", "cs-012"],
        )

    def param_cap_max_tokens(self) -> MutationCase:
        return MutationCase(
            operator_id="param-cap-max-tokens",
            description="参数漂移：max_tokens 1024→64",
            layer="model_params",
            system_prompt=self.baseline_system(),
            params={"temperature": 0.0, "max_tokens": 64},
            target_probes=["cs-012"],
        )

    def all_cases(self) -> list[MutationCase]:
        return [
            self.prompt_deny_refund(),
            self.prompt_remove_keyword(),
            self.prompt_flip_shipping(),
            self.prompt_break_shipping(),
            self.kb_outdate_battery(),
            self.kb_wrong_capacity(),
            self.param_high_temperature(),
            self.param_cap_max_tokens(),
        ]


@dataclass
class PatrolResult:
    patrol_id: str
    run_at: str
    summary: dict
    cases: list[dict] = field(default_factory=list)

    def to_markdown(self) -> str:
        s = self.summary
        lines = [
            f"# 变异巡检报告 {self.patrol_id}",
            f"- 运行时间：{self.run_at}",
            f"- 变异用例：{s['total_cases']}（检出 {s['detected']} / 漏检 {s['missed']} / "
            f"未生效排除 {s.get('no_effect_excluded', 0)} / 基线失效排除 {s['baseline_fail_excluded']}）",
            f"- **检出率（生效用例口径）：{s['detection_rate']:.2%}**",
            "",
            "| 算子 | 层 | 目标探针 | 检出 | 生效 |",
            "|------|----|---------|------|------|",
        ]
        for c in self.cases:
            lines.append(
                f"| {c['operator_id']} | {c['layer']} | {c['target_probe']} | "
                f"{'✓' if c['detected'] else '✗' if c['mutant_passed'] else '−'} | "
                f"{'✓' if c['took_effect'] else '✗'} |"
            )
        return "\n".join(lines)


class MutationPatrol:
    def __init__(self, settings: Settings, probe_set: ProbeSet, llm: LLMClient | None = None):
        self.settings = settings
        self.probe_set = probe_set
        self.llm = llm or LLMClient(settings)

    def run(self, repo_root: Path) -> PatrolResult:
        library = MutationOperatorLibrary(repo_root)
        pmap = self.probe_set.by_id()
        # 基线通过性 + 基线答案文本（每个目标探针先在基线 system prompt 上打一遍）
        baseline_sys = library.baseline_system()
        baseline_status: dict[str, bool] = {}
        baseline_answers: dict[str, str] = {}
        for probe in self.probe_set.probes:
            resp = self.llm.chat(baseline_sys, probe.input, params={"temperature": 0.0, "max_tokens": 1024})
            baseline_status[probe.id] = judge_probe(probe, resp.content)[0]
            baseline_answers[probe.id] = resp.content

        cases_out: list[dict] = []
        detected = missed = no_effect = baseline_fail = 0
        for case in library.all_cases():
            for pid in case.target_probes:
                probe = pmap[pid]
                resp = self.llm.chat(case.system_prompt, probe.input, params=case.params)
                mutant_passed = judge_probe(probe, resp.content)[0]
                base_ok = baseline_status.get(pid, False)
                base_answer = baseline_answers.get(pid, "")
                # 变异是否真正改变行为：变异后回答与基线回答不同才算生效
                took_effect = resp.content.strip() != base_answer.strip()
                is_detected = base_ok and not mutant_passed
                cases_out.append({
                    "operator_id": case.operator_id,
                    "description": case.description,
                    "layer": case.layer,
                    "target_probe": pid,
                    "baseline_passed": base_ok,
                    "mutant_passed": mutant_passed,
                    "took_effect": took_effect,
                    "detected": is_detected,
                })
                if not base_ok:
                    baseline_fail += 1
                elif not took_effect:
                    no_effect += 1
                elif is_detected:
                    detected += 1
                else:
                    missed += 1

        total = detected + missed
        summary = {
            "total_cases": len(cases_out),
            "detected": detected,
            "missed": missed,
            "no_effect_excluded": no_effect,
            "baseline_fail_excluded": baseline_fail,
            # 检出率分母 = 生效且基线通过的用例（no-op 不计入，避免把「变异未生效」当漏检）
            "detection_rate": round(detected / total, 4) if total else 0.0,
        }
        return PatrolResult(patrol_id=new_id("patrol"), run_at=_now_iso(), summary=summary, cases=cases_out)
