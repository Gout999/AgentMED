"""统计口径单测：Wilson 全向量对拍 contracts/wilson + Newcombe hybrid 差值区间。"""
import json

import pytest

from eval_harness.stats import newcombe_wilson_diff, significant_positive, wilson_interval

TOL = 1e-3


@pytest.fixture(scope="module")
def wilson_vectors(contracts_dir):
    doc = json.loads((contracts_dir / "wilson" / "wilson-vectors.json").read_text(encoding="utf-8"))
    return doc["vectors"]


def test_wilson_all_vectors(wilson_vectors):
    """与 contracts/wilson/wilson-vectors.json 全部向量对拍（容差 1e-3）。"""
    for v in wilson_vectors:
        lower, upper = wilson_interval(v["successes"], v["trials"], z=v["z"])
        assert abs(lower - v["expected_lower"]) <= TOL, f"{v['successes']}/{v['trials']} LB 偏差"
        assert abs(upper - v["expected_upper"]) <= TOL, f"{v['successes']}/{v['trials']} UB 偏差"


def test_wilson_mvp_demo_3of3():
    lower, upper = wilson_interval(3, 3, z=1.96)
    assert abs(lower - 0.438494) <= TOL
    assert lower < 0.9
    assert upper == 1.0


def test_wilson_zero_trials():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_invalid_counts():
    with pytest.raises(ValueError):
        wilson_interval(5, 3)


def test_newcombe_known_value():
    """p_arm=1.0(n=15) vs p_c=0.0(n=15)，z=1.96 → LB≈0.7117, UB=1.0。"""
    lb, ub = newcombe_wilson_diff(1.0, 15, 0.0, 15, z=1.96)
    assert abs(lb - 0.711665) <= TOL
    assert ub == 1.0


def test_newcombe_zero_delta_ci_covers_zero():
    """p=0.0 vs p=0.0 → Δ=0，CI 对称覆盖 0（约 [-0.204, 0.204]）。"""
    lb, ub = newcombe_wilson_diff(0.0, 15, 0.0, 15)
    assert abs(lb - (-0.2039)) <= TOL
    assert abs(ub - 0.2039) <= TOL
    assert lb <= 0 <= ub


def test_newcombe_requires_samples():
    with pytest.raises(ValueError):
        newcombe_wilson_diff(0.0, 0, 0.0, 15)


def test_significant_positive_threshold():
    assert significant_positive(0.25, 0.2) is True
    assert significant_positive(0.2, 0.2) is False  # 严格大于
    assert significant_positive(0.1, 0.2) is False


def test_newcombe_ci_within_unit():
    lb, ub = newcombe_wilson_diff(1.0, 5, 0.0, 5)
    assert -1.0 <= lb <= ub <= 1.0
