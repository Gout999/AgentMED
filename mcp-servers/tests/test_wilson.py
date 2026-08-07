"""Wilson 双侧 95% 全向量断言（contracts/wilson/wilson-vectors.json 唯一事实源）。"""
from __future__ import annotations

import json

import pytest

from conftest import WILSON_VECTORS
from trust_ledger.wilson import evaluate, wilson_interval

TOLERANCE = 1e-3


def _vectors() -> list[dict]:
    doc = json.loads(WILSON_VECTORS.read_text(encoding="utf-8"))
    assert len(doc["vectors"]) >= 13, "测试向量不足 13 组"
    return doc["vectors"]


def test_all_13_vectors_pass():
    for v in _vectors():
        lower, upper = wilson_interval(v["successes"], v["trials"], v["z"])
        assert v["confidence"] == 0.95 and v["side"] == "two-sided"
        assert abs(lower - v["expected_lower"]) <= TOLERANCE, (
            f"{v['successes']}/{v['trials']} 下界偏差超容差: {lower} vs {v['expected_lower']}"
        )
        assert abs(upper - v["expected_upper"]) <= TOLERANCE, (
            f"{v['successes']}/{v['trials']} 上界偏差超容差: {upper} vs {v['expected_upper']}"
        )


def test_promotion_eligible_matches_vectors():
    for v in _vectors():
        _, eligible = evaluate(v["successes"], v["trials"])
        assert eligible == v["promotion_eligible"], (
            f"{v['successes']}/{v['trials']}: eligible={eligible} 与向量 {v['promotion_eligible']} 不一致"
        )


def test_mvp_demo_3_of_3_denied():
    """MVP 演示：3/3 → 下界≈0.4385<0.9 → 记账但拒绝晋升。"""
    v = next(x for x in _vectors() if (x["successes"], x["trials"]) == (3, 3))
    lower, _ = wilson_interval(3, 3)
    assert abs(lower - 0.438494) <= TOLERANCE, f"3/3 下界应≈0.438494，实际 {lower}"
    assert lower < 0.9
    assert v["promotion_eligible"] is False


def test_zero_trials_convention():
    """无证据约定全区间 [0,1]，eligible=False。"""
    assert wilson_interval(0, 0) == (0.0, 1.0)
    _, eligible = evaluate(0, 0)
    assert eligible is False


def test_double_sided_not_single_sided():
    """双侧口径硬约束：3/3 若用单侧 z=1.645 下界≈0.51>0.4385，属口径放水，禁止。"""
    lower_twosided, _ = wilson_interval(3, 3, z=1.96)
    lower_onesided, _ = wilson_interval(3, 3, z=1.645)
    assert lower_twosided < 0.9
    assert abs(lower_twosided - 0.438494) <= TOLERANCE
    assert lower_onesided > lower_twosided  # 单侧被抬高，不可用


def test_invalid_counts_raise():
    with pytest.raises(ValueError):
        wilson_interval(-1, 5)
    with pytest.raises(ValueError):
        wilson_interval(6, 5)  # successes > trials


def test_30_of_30_still_denied():
    """统计纪律演示点：三十连胜下界≈0.886<0.9 仍拒。"""
    _, eligible = evaluate(30, 30)
    assert eligible is False
    lower, _ = wilson_interval(30, 30)
    assert abs(lower - 0.886483) <= TOLERANCE


def test_100_of_100_eligible():
    """百连胜下界≈0.963>0.9 → 满足统计条件。"""
    _, eligible = evaluate(100, 100)
    assert eligible is True
