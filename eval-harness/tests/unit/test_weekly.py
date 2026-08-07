"""质量周报生成器单测：指标口径与 Markdown 结构。"""
from eval_harness.weekly import WeeklyInput, build_weekly_json, build_weekly_report


def test_weekly_metrics_computed():
    data = WeeklyInput(
        period="2026-W32",
        mutation_cases_generated=40,
        mutation_detected=33,
        attribution_experiments=12,
        attribution_attributed=9,
        attribution_ground_truth_hits=10,
        gate_runs=15,
        gate_blocked=4,
        gate_first_pass=11,
        trust_outcomes_recorded=21,
        trust_promotion_requests=1,
        trust_promotion_rejected=1,
    )
    md = build_weekly_report(data)
    assert "质量周报 · 2026-W32" in md
    assert "变异用例数：40" in md
    assert "检出率：82.50%" in md          # 33/40
    assert "归因准确率" in md and "83.33%" in md  # 10/12
    assert "门禁拦截率：26.67%" in md      # 4/15
    assert "一次通过率：73.33%" in md      # 11/15
    assert "晋升拒绝：1" in md


def test_weekly_zero_denominator_safe():
    md = build_weekly_report(WeeklyInput(period="2026-W32"))
    assert "检出率：0.00%" in md
    assert "门禁拦截率：0.00%" in md


def test_weekly_trend_rows():
    data = WeeklyInput(period="2026-W33", prior_trends=[
        {"period": "2026-W31", "mutation_rate": "0.80", "attribution_accuracy": "0.75",
         "gate_block_rate": "0.20", "gate_first_pass_rate": "0.80"},
    ])
    md = build_weekly_report(data)
    assert "## 趋势（多期）" in md
    assert "2026-W31" in md


def test_weekly_json_shape():
    data = WeeklyInput(
        period="2026-W32",
        mutation_cases_generated=40, mutation_detected=33,
        attribution_experiments=12, attribution_attributed=9, attribution_ground_truth_hits=10,
        gate_runs=15, gate_blocked=4, gate_first_pass=11,
        trust_outcomes_recorded=21, trust_promotion_requests=1, trust_promotion_rejected=1,
    )
    obj = build_weekly_json(data)
    assert obj["mutation"]["detection_rate"] == 0.825
    assert obj["gate"]["block_rate"] == 0.2667
    assert obj["gate"]["first_pass_rate"] == 0.7333
    assert obj["attribution"]["attribution_accuracy"] == 0.8333
    assert obj["trust"]["promotion_rejected"] == 1
    assert obj["period"] == "2026-W32"
