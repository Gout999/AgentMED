"""Wilson 双侧区间参考实现 + 全向量断言（contracts/wilson/wilson-vectors.json）。

口径（contracts/wilson/README.md 为唯一事实源）：
- 双侧 95%，z=1.96；trials=0 约定全区间 [0,1]（无证据）。
- 一次动作=一个样本（动作内多条探针不重复计数）。
- 晋升判据：lower > 0.9；MVP 演示口径「记账但拒绝晋升」（3/3 下界≈0.438）。
"""
import json

import pytest

from conftest import WILSON_VECTORS

TOLERANCE = 1e-3


def wilson_interval(successes: int, trials: int, z: float = 1.96):
    """Wilson score interval（双侧）参考实现。任何实现若与本函数在
    全部测试向量上偏差 >1e-3，即判定统计口径不合契约。"""
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("非法计数")
    if trials == 0:
        return (0.0, 1.0)  # 无证据约定：全区间
    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    center = (p + z2 / (2 * trials)) / denom
    margin = (z / denom) * ((p * (1 - p) / trials + z2 / (4 * trials * trials)) ** 0.5)
    return (max(0.0, center - margin), min(1.0, center + margin))


@pytest.fixture(scope="module")
def vectors() -> list[dict]:
    doc = json.loads(WILSON_VECTORS.read_text(encoding="utf-8"))
    assert len(doc["vectors"]) >= 10, "测试向量不足 10 组"
    return doc["vectors"]


def test_all_vectors(vectors):
    for v in vectors:
        lower, upper = wilson_interval(v["successes"], v["trials"], v["z"])
        assert v["confidence"] == 0.95 and v["side"] == "two-sided", "口径必须双侧 95%"
        assert abs(lower - v["expected_lower"]) <= TOLERANCE, \
            f"{v['successes']}/{v['trials']} 下界偏差超容差: {lower} vs {v['expected_lower']}"
        assert abs(upper - v["expected_upper"]) <= TOLERANCE, \
            f"{v['successes']}/{v['trials']} 上界偏差超容差: {upper} vs {v['expected_upper']}"


def test_mvp_demo_case_3_of_3_denied(vectors):
    """MVP 演示用例：3/3 → 下界≈0.438<0.9 → 记账但拒绝晋升。"""
    v = next(v for v in vectors if (v["successes"], v["trials"]) == (3, 3))
    lower, _ = wilson_interval(v["successes"], v["trials"])
    assert abs(lower - 0.438494) <= TOLERANCE, f"3/3 下界应≈0.438494，实际 {lower}"
    assert lower < 0.9, "3/3 下界必须 <0.9（拒绝晋升）"
    assert v["promotion_eligible"] is False


def test_promotion_threshold_semantics(vectors):
    """晋升判据一致性：eligible ⇔ 复算下界>0.9；且 30/30 仍被拒、100/100 放行。"""
    by_key = {(v["successes"], v["trials"]): v for v in vectors}
    for (s, n), v in by_key.items():
        lower, _ = wilson_interval(s, n)
        expected = (lower > 0.9) if n > 0 else False
        assert v["promotion_eligible"] == expected, \
            f"{s}/{n}: 向量中 eligible={v['promotion_eligible']} 与复算 {expected} 不一致"
    assert by_key[(30, 30)]["promotion_eligible"] is False, "30/30 下界仍 <0.9（统计纪律演示点）"
    assert by_key[(100, 100)]["promotion_eligible"] is True, "100/100 下界 >0.9"
    assert by_key[(0, 0)]["promotion_eligible"] is False, "无证据不得晋升"


def test_zero_trials_convention():
    """trials=0 → 全区间 [0,1]（无证据约定）。"""
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_interval_bounds_within_unit(vectors):
    """所有区间必须落在 [0,1] 且 lower ≤ upper。"""
    for v in vectors:
        lower, upper = wilson_interval(v["successes"], v["trials"])
        assert 0.0 <= lower <= upper <= 1.0, \
            f"{v['successes']}/{v['trials']}: 区间越界 [{lower}, {upper}]"
