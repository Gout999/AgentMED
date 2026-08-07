"""R1–R5 顺序裁决单测：ATTRIBUTED / INCONCLUSIVE / CONFOUNDED 全路径。"""
import pytest

from eval_harness.adjudicate import CellStats, adjudicate

DELTA_MIN = 0.2


def cell(rec, n, ctrl=1.0, hidden_rec=0.0, hidden_n=0) -> CellStats:
    return CellStats(
        recovery_rate=rec,
        n_trials=n,
        control_pass_rate=ctrl,
        hidden_recovery_rate=hidden_rec,
        hidden_trials=hidden_n,
    )


def test_r4_attributed_prompt():
    """B1 标准形态：仅 RP 恢复，hidden 复现 → ATTRIBUTED/prompt。"""
    cells = {
        "C": cell(0.0, 15, hidden_rec=0.0, hidden_n=6),
        "RP": cell(1.0, 15, hidden_rec=1.0, hidden_n=6),
        "RK": cell(0.0, 15),
        "RM": cell(0.0, 15),
        "G": cell(1.0, 15),
    }
    v = adjudicate(cells, DELTA_MIN)
    assert v.decision == "ATTRIBUTED"
    assert v.attributed_layer == "prompt"
    assert v.hidden_confirmation_reproduced is True
    assert v.full_factorial_required is False


def test_r1_env_untrusted():
    cells = {
        "C": cell(0.0, 15),
        "RP": cell(1.0, 15, hidden_rec=1.0, hidden_n=6),
        "RK": cell(0.0, 15),
        "RM": cell(0.0, 15),
        "G": cell(1.0, 15, ctrl=0.8),  # 对照臂失败
    }
    v = adjudicate(cells, DELTA_MIN)
    assert v.decision == "INCONCLUSIVE"
    assert v.reason_code == "ENV_UNTRUSTED"


def test_r2_baseline_not_restored():
    cells = {
        "C": cell(0.0, 15),
        "RP": cell(1.0, 15, hidden_rec=1.0, hidden_n=6),
        "RK": cell(0.0, 15),
        "RM": cell(0.0, 15),
        "G": cell(0.0, 15),  # 基线臂未恢复
    }
    v = adjudicate(cells, DELTA_MIN)
    assert v.decision == "INCONCLUSIVE"
    assert v.reason_code == "BASELINE_NOT_RESTORED"


def test_r3_confounded_two_arms():
    cells = {
        "C": cell(0.0, 15),
        "RP": cell(1.0, 15),
        "RK": cell(1.0, 15),  # 第二臂同恢复
        "RM": cell(0.0, 15),
        "G": cell(1.0, 15),
    }
    v = adjudicate(cells, DELTA_MIN)
    assert v.decision == "CONFOUNDED"
    assert v.interaction_detected is True
    assert v.full_factorial_required is True
    assert v.attributed_layer is None


def test_r3_confounded_no_arm():
    cells = {
        "C": cell(0.0, 15),
        "RP": cell(0.0, 15),
        "RK": cell(0.0, 15),
        "RM": cell(0.0, 15),
        "G": cell(1.0, 15),  # G 已恢复但单因素臂无一恢复 → 纯交互嫌疑
    }
    v = adjudicate(cells, DELTA_MIN)
    assert v.decision == "CONFOUNDED"
    assert v.full_factorial_required is True


def test_r5_confirmation_mismatch():
    """RP 在 discovery 显著但 hidden 未复现 → INCONCLUSIVE/CONFIRMATION_MISMATCH。"""
    # RP affected：discovery 9/9 + hidden 1/6 = 10/15
    # hidden-only：RP 1/6 vs C 0/6 → Δ_hidden>0 但 LB≤0
    cells = {
        "C": cell(0.0, 15, hidden_rec=0.0, hidden_n=6),
        "RP": cell(10 / 15, 15, hidden_rec=1 / 6, hidden_n=6),
        "RK": cell(0.0, 15),
        "RM": cell(0.0, 15),
        "G": cell(1.0, 15),
    }
    v = adjudicate(cells, DELTA_MIN)
    assert v.decision == "INCONCLUSIVE"
    assert v.reason_code == "CONFIRMATION_MISMATCH"


def test_attributed_requires_hidden_reproduced():
    """单臂显著但 hidden 未复现绝不能 ATTRIBUTED。"""
    cells = {
        "C": cell(0.0, 15, hidden_rec=0.0, hidden_n=6),
        "RP": cell(10 / 15, 15, hidden_rec=0.0, hidden_n=6),
        "RK": cell(0.0, 15),
        "RM": cell(0.0, 15),
        "G": cell(1.0, 15),
    }
    v = adjudicate(cells, DELTA_MIN)
    assert v.decision != "ATTRIBUTED"
