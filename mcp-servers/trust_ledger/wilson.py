"""Wilson score interval（双侧 95%，z=1.96）。

contracts/wilson/ 为唯一事实源；本实现须全过 13 组向量（断言容差 1e-3）。
- 双侧口径是硬约束（plan-v3 T8）；禁用单侧 z=1.645。
- trials=0 约定全区间 [0,1]（无证据就没有信任）。
- 晋升判据：lower > 0.9（严格大于）；R2 永远逐次审批，即使下界 >0.9。
"""
from __future__ import annotations

from dataclasses import dataclass

Z_TWO_SIDED_95 = 1.96
PROMOTION_THRESHOLD = 0.9


@dataclass(frozen=True)
class WilsonInterval:
    confidence: float
    side: str
    z: float
    lower: float
    upper: float


def wilson_interval(successes: int, trials: int, z: float = Z_TWO_SIDED_95) -> tuple[float, float]:
    """Wilson 双侧区间，返回 (lower, upper)，已截断到 [0,1]。"""
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError(f"非法计数: successes={successes}, trials={trials}")
    if trials == 0:
        return (0.0, 1.0)  # 无证据约定：全区间

    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denom
    margin = (z / denom) * ((p * (1 - p) / trials + z2 / (4 * trials * trials)) ** 0.5)
    lower = max(0.0, center - margin)
    upper = min(1.0, center + margin)
    return (lower, upper)


def evaluate(successes: int, trials: int, *, threshold: float = PROMOTION_THRESHOLD) -> tuple[WilsonInterval, bool]:
    """计算区间并按阈值评估晋升 eligible（lower > threshold）。

    promotion_eligible ⇔ wilson_lower > threshold；trials=0 时 eligible=False。
    """
    lower, upper = wilson_interval(successes, trials)
    eligible = trials > 0 and lower > threshold
    interval = WilsonInterval(
        confidence=0.95,
        side="two-sided",
        z=Z_TWO_SIDED_95,
        lower=lower,
        upper=upper,
    )
    return interval, eligible
