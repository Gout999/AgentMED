"""质量周报生成器（spec §10.5 / T10 I2）。

从实验 / 门禁 / 巡检结果聚合：
- 变异：用例数 / 检出率
- 归因：实验数 / ATTRIBUTED 数 / 归因准确率（对 ground-truth 的命中率）
- 门禁：运行数 / 拦截数 / 拦截率 / 一次通过率
- 信任账本：记账数 / 晋升提请数 / 拒绝数（MVP：记账但拒绝晋升）
- 趋势：多期序列

纯函数，输入为各来源的结果记录，输出 Markdown。字段口径与 spec §10.5 对齐。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WeeklyInput:
    period: str
    mutation_cases_generated: int = 0
    mutation_detected: int = 0
    attribution_experiments: int = 0
    attribution_attributed: int = 0
    attribution_ground_truth_hits: int = 0
    gate_runs: int = 0
    gate_blocked: int = 0
    gate_first_pass: int = 0
    trust_outcomes_recorded: int = 0
    trust_promotion_requests: int = 0
    trust_promotion_rejected: int = 0
    prior_trends: list[dict] = field(default_factory=list)


def _rate(num: int, den: int) -> float:
    return round(num / den, 4) if den else 0.0


def build_weekly_json(data: WeeklyInput) -> dict:
    """机器可消费的周报结构（spec §10.5 字段对齐）。"""
    return {
        "period": data.period,
        "mutation": {
            "cases_generated": data.mutation_cases_generated,
            "detected": data.mutation_detected,
            "detection_rate": _rate(data.mutation_detected, data.mutation_cases_generated),
        },
        "attribution": {
            "experiments": data.attribution_experiments,
            "attributed": data.attribution_attributed,
            "attribution_accuracy": _rate(data.attribution_ground_truth_hits, data.attribution_experiments),
        },
        "gate": {
            "runs": data.gate_runs,
            "blocked": data.gate_blocked,
            "block_rate": _rate(data.gate_blocked, data.gate_runs),
            "first_pass_rate": _rate(data.gate_first_pass, data.gate_runs),
        },
        "trust": {
            "outcomes_recorded": data.trust_outcomes_recorded,
            "promotion_requests": data.trust_promotion_requests,
            "promotion_rejected": data.trust_promotion_rejected,
        },
        "trend": data.prior_trends,
    }


def build_weekly_report(data: WeeklyInput) -> str:
    mutation_rate = _rate(data.mutation_detected, data.mutation_cases_generated)
    attribution_accuracy = _rate(data.attribution_ground_truth_hits, data.attribution_experiments)
    gate_block_rate = _rate(data.gate_blocked, data.gate_runs)
    gate_first_pass_rate = _rate(data.gate_first_pass, data.gate_runs)

    rows = []
    if data.prior_trends:
        rows.append("## 趋势（多期）")
        rows.append("| 期 | 检出率 | 归因准确率 | 门禁拦截率 | 一次通过率 |")
        rows.append("|----|--------|-----------|-----------|-----------|")
        for t in data.prior_trends:
            rows.append(
                f"| {t.get('period', '')} | {t.get('mutation_rate', '')} | "
                f"{t.get('attribution_accuracy', '')} | {t.get('gate_block_rate', '')} | "
                f"{t.get('gate_first_pass_rate', '')} |"
            )
        rows.append("")

    rows += [
        f"# 质量周报 · {data.period}",
        "",
        "## 变异巡检",
        f"- 变异用例数：{data.mutation_cases_generated}",
        f"- 检出：{data.mutation_detected}",
        f"- **检出率：{mutation_rate:.2%}**",
        "",
        "## 归因实验",
        f"- 实验数：{data.attribution_experiments}",
        f"- ATTRIBUTED：{data.attribution_attributed}",
        f"- 归因准确率（对 ground-truth 命中）：{attribution_accuracy:.2%}",
        "",
        "## 评测门禁",
        f"- 门禁运行数：{data.gate_runs}",
        f"- 拦截数（blocked）：{data.gate_blocked}",
        f"- **门禁拦截率：{gate_block_rate:.2%}**",
        f"- **一次通过率：{gate_first_pass_rate:.2%}**",
        "",
        "## 信任账本",
        f"- 记账样本数：{data.trust_outcomes_recorded}",
        f"- 晋升提请：{data.trust_promotion_requests}",
        f"- 晋升拒绝：{data.trust_promotion_rejected}（MVP 口径：记账但拒绝晋升）",
    ]
    return "\n".join(rows)
