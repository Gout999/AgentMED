"""统计量：Wilson score 区间 + Newcombe hybrid score（差值 95%CI）。

口径（spec §4.5 / §6.2 / D-001 Q6）：
- 单比例区间 = Wilson score（双侧 95%，z=1.959964）。
- 两比例差 Δ 的 95%CI = Newcombe hybrid score interval（Method 10，无连续性校正）：
      Wilson(arm)=(l1,u1)，Wilson(C)=(l2,u2)
      LB = Δ − sqrt((p_arm−l1)² + (u2−p_C)²)
      UB = Δ + sqrt((u1−p_arm)² + (p_C−l2)²)
- method 字段如实记录：newcombe_wilson_diff。
- n=0 无证据：单比例约定全区间 [0,1]；差值区间无法定义 → 抛 ValueError。
"""
from __future__ import annotations

import math

# 双侧 95% z 值（spec §6.2）
Z_975 = 1.959964


def wilson_interval(successes: int, trials: int, z: float = Z_975) -> tuple[float, float]:
    """Wilson score 双侧区间，返回 (lower, upper)，截断到 [0,1]。"""
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError(f"非法计数: successes={successes}, trials={trials}")
    if trials == 0:
        return (0.0, 1.0)  # 无证据约定：全区间
    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))
    return (max(0.0, center - margin), min(1.0, center + margin))


def newcombe_wilson_diff(
    p_arm: float,
    n_arm: int,
    p_c: float,
    n_c: int,
    z: float = Z_975,
) -> tuple[float, float]:
    """Newcombe hybrid score：两比例差 (p_arm − p_c) 的 95%CI，返回 (lower, upper)。

    入参为点估计与样本量（recovery rate 及对应调用次数）。
    """
    if n_arm <= 0 or n_c <= 0:
        raise ValueError("两臂样本量都必须 >0 才能计算差值区间")
    if not (0.0 <= p_arm <= 1.0) or not (0.0 <= p_c <= 1.0):
        raise ValueError(f"比例必须 ∈[0,1]: arm={p_arm}, c={p_c}")

    l1, u1 = wilson_interval(round(p_arm * n_arm), n_arm, z)
    l2, u2 = wilson_interval(round(p_c * n_c), n_c, z)
    delta = p_arm - p_c
    lb = delta - math.sqrt((p_arm - l1) ** 2 + (u2 - p_c) ** 2)
    ub = delta + math.sqrt((u1 - p_arm) ** 2 + (p_c - l2) ** 2)
    return (max(-1.0, lb), min(1.0, ub))


def significant_positive(lb: float, delta_min: float) -> bool:
    """恢复显著判据：LB_Δ > δ_min。"""
    return lb > delta_min
