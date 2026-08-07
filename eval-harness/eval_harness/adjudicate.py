"""三态裁决规则（spec §4.6 / T4）——确定性代码，LLM 不参与裁决。

R1–R5 按序判定，先命中先生效：
  R1  unaffected controls 任一 cell 失败            → INCONCLUSIVE（ENV_UNTRUSTED）
  R2  G 臂恢复不显著（LB_Δ(G) ≤ δ_min）              → INCONCLUSIVE（BASELINE_NOT_RESTORED）
  R3  RP/RK/RM 中 ≥2 臂恢复显著，或均不显著          → CONFOUNDED（强制 2³ 全因子）
  R4  恰好 1 臂恢复显著 且 hidden confirmation 同向复现 → ATTRIBUTED（该层）
  R5  单臂显著但 hidden 未复现                       → INCONCLUSIVE（CONFIRMATION_MISMATCH）

「恢复显著」= LB_Δ > δ_min。hidden confirmation「同向复现」= hidden-only Δ>0 且其
Newcombe LB>0（方向为正且 CI 下界跨过 0）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .stats import newcombe_wilson_diff, significant_positive, wilson_interval

# 臂 → 层
ARM_TO_LAYER = {"RP": "prompt", "RK": "kb", "RM": "model_params"}
SINGLE_FACTOR_ARMS = ("RP", "RK", "RM")


@dataclass
class CellStats:
    """单臂汇总（受影响探针 + 对照）。"""
    recovery_rate: float
    n_trials: int
    control_pass_rate: float
    # hidden-only 统计（用于 R4/R5）
    hidden_recovery_rate: float = 0.0
    hidden_trials: int = 0


@dataclass
class ArmEffect:
    """单臂相对 C 的效应量。"""
    arm: str
    delta: float
    ci95_lower: float
    ci95_upper: float
    significant: bool
    hidden_delta: float = 0.0
    hidden_ci_lower: float = 0.0
    hidden_reproduced: bool = False


@dataclass
class Verdict:
    decision: str                     # ATTRIBUTED | INCONCLUSIVE | CONFOUNDED
    attributed_layer: str | None
    interaction_detected: bool
    full_factorial_required: bool
    reason_code: str | None = None    # ENV_UNTRUSTED / BASELINE_NOT_RESTORED / CONFIRMATION_MISMATCH / INTERACTION_UNRESOLVED / None
    rationale: str = ""
    hidden_confirmation_reproduced: bool = False
    supplement_attempts: int = 0
    escalated_to_human: bool = False
    effects: dict[str, ArmEffect] = field(default_factory=dict)

    def as_evidence_verdict(self) -> dict:
        return {
            "decision": self.decision,
            "rationale": self.rationale,
            **({"attributed_layer": self.attributed_layer} if self.decision == "ATTRIBUTED" else {"attributed_layer": None}),
            "hidden_confirmation_reproduced": self.hidden_confirmation_reproduced,
        }

    def as_report_verdict(self) -> dict:
        return {
            "decision": self.decision,
            "attributed_layer": self.attributed_layer,
            "interaction_detected": self.interaction_detected,
            "full_factorial_required": self.full_factorial_required,
            "rationale": self.rationale,
        }


def _effect(arm: str, arm_cell: CellStats, c_cell: CellStats, delta_min: float) -> ArmEffect:
    delta = arm_cell.recovery_rate - c_cell.recovery_rate
    lb, ub = newcombe_wilson_diff(
        arm_cell.recovery_rate, arm_cell.n_trials,
        c_cell.recovery_rate, c_cell.n_trials,
    )
    # hidden-only 复现统计
    hidden_delta = 0.0
    hidden_lb = 0.0
    hidden_reproduced = False
    if arm_cell.hidden_trials > 0 and c_cell.hidden_trials > 0:
        hidden_delta = arm_cell.hidden_recovery_rate - c_cell.hidden_recovery_rate
        hidden_lb, _ = newcombe_wilson_diff(
            arm_cell.hidden_recovery_rate, arm_cell.hidden_trials,
            c_cell.hidden_recovery_rate, c_cell.hidden_trials,
        )
        hidden_reproduced = hidden_delta > 0 and hidden_lb > 0
    return ArmEffect(
        arm=arm,
        delta=delta,
        ci95_lower=lb,
        ci95_upper=ub,
        significant=significant_positive(lb, delta_min),
        hidden_delta=hidden_delta,
        hidden_ci_lower=hidden_lb,
        hidden_reproduced=hidden_reproduced,
    )


def adjudicate(
    cells: dict[str, CellStats],
    delta_min: float,
) -> Verdict:
    """执行 R1–R5 顺序裁决。cells 键必须含 C/RP/RK/RM/G。"""
    c = cells["C"]
    effects = {arm: _effect(arm, cells[arm], c, delta_min) for arm in SINGLE_FACTOR_ARMS}
    g_effect = _effect("G", cells["G"], c, delta_min)

    def _verdict(**kw) -> Verdict:
        base = dict(
            effects=effects,
            attributed_layer=None,
            interaction_detected=False,
            full_factorial_required=False,
            hidden_confirmation_reproduced=False,
        )
        base.update(kw)
        return Verdict(**base)

    # R1：unaffected controls 任一 cell 失败 → 环境不可信
    for arm_name, cell in cells.items():
        if cell.control_pass_rate < 1.0:
            return _verdict(
                decision="INCONCLUSIVE",
                reason_code="ENV_UNTRUSTED",
                rationale=(
                    f"R1 命中：{arm_name} 臂 unaffected controls 通过率 "
                    f"{cell.control_pass_rate:.3f} < 1.0 → 实验环境不可信，本轮作废"
                ),
            )

    # R2：G 臂未恢复 → 基线不可复现
    if not g_effect.significant:
        return _verdict(
            decision="INCONCLUSIVE",
            reason_code="BASELINE_NOT_RESTORED",
            rationale=(
                f"R2 命中：G 臂 Δ={g_effect.delta:.3f} "
                f"95%CI[{g_effect.ci95_lower:.3f},{g_effect.ci95_upper:.3f}]，"
                f"LB={g_effect.ci95_lower:.3f} ≤ δ_min={delta_min} → 已知良好基线都复现不了，实验不可信"
            ),
        )

    sig_arms = [arm for arm in SINGLE_FACTOR_ARMS if effects[arm].significant]
    n_sig = len(sig_arms)

    # R3：多臂同恢复 或 均不恢复 → 交互嫌疑
    if n_sig >= 2 or n_sig == 0:
        return _verdict(
            decision="CONFOUNDED",
            interaction_detected=True,
            full_factorial_required=True,
            rationale=(
                f"R3 命中：单因素臂恢复显著数={n_sig}（{'多臂同恢复' if n_sig >= 2 else '均不恢复'}）"
                f"→ 存在层间交互嫌疑，强制展开 2³ 全因子（spec §4.6 / plan-v3 §2.3.2）"
            ),
        )

    # R4：恰好 1 臂显著 + hidden 复现 → ATTRIBUTED
    arm = sig_arms[0]
    eff = effects[arm]
    if eff.hidden_reproduced:
        layer = ARM_TO_LAYER[arm]
        return _verdict(
            decision="ATTRIBUTED",
            attributed_layer=layer,
            hidden_confirmation_reproduced=True,
            rationale=(
                f"R4 命中：仅 {arm} 臂恢复显著（Δ={eff.delta:.3f} "
                f"95%CI[{eff.ci95_lower:.3f},{eff.ci95_upper:.3f}]，LB={eff.ci95_lower:.3f}>δ_min={delta_min}），"
                f"且 hidden confirmation 组同向复现（hidden Δ={eff.hidden_delta:.3f}，LB={eff.hidden_ci_lower:.3f}>0）"
                f"→ 故障层={layer}"
            ),
        )

    # R5：单臂显著但 hidden 未复现 → 过拟合嫌疑
    return _verdict(
        decision="INCONCLUSIVE",
        reason_code="CONFIRMATION_MISMATCH",
        rationale=(
            f"R5 命中：仅 {arm} 臂恢复显著（LB={eff.ci95_lower:.3f}>δ_min），"
            f"但 hidden confirmation 未同向复现（hidden Δ={eff.hidden_delta:.3f}，LB={eff.hidden_ci_lower:.3f}≤0）"
            f"→ 结论疑似过拟合 discovery 组，补实验或升级人工"
        ),
    )
