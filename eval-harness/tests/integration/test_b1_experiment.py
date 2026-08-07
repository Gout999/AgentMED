"""Live B1 集成：注入 B1 → 跑 5-cell 对照实验 → 裁决必须 = ATTRIBUTED 且故障层=prompt。

运行态纪律：无论成败，本测试结束都复位故障；主控验收后还应跑 reset_state.sh。
结论必须附报告路径 / schema 校验结果（报告纪律）。
"""
import json
from pathlib import Path

import pytest

from eval_harness.client import QualityAPIClient
from eval_harness.experiment import DemoAppB1Driver, ExperimentRunner
from eval_harness.models import ExperimentPlan
from eval_harness.probe_loader import frozen_digest
from eval_harness.report import validate_report

EVAL_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def _client(live_settings):
    return QualityAPIClient(live_settings)


@pytest.fixture(scope="module")
def _runner(live_settings, live_probe_set, _client):
    return ExperimentRunner(_client, live_probe_set, live_settings)


@pytest.mark.live
def test_b1_live_experiment_attributed_prompt(live_settings, live_probe_set, b1_protocol_reps, _client, _runner):
    digest = frozen_digest(live_probe_set)
    plan = ExperimentPlan(
        experiment_id="exp_liveb1000000000000001",
        case_id="case_liveb1000000000000001",
        matrix="five_cell",
        repetitions=b1_protocol_reps,
        confidence=0.95,
        delta_min=live_settings.experiment_delta_min,
        probe_set_digest=digest,
        version_digests={},
        discovery=["cs-001", "cs-002", "cs-003"],
        hidden_confirmation=["cs-004", "cs-005"],
        unaffected_controls=["cs-013", "cs-014", "cs-015", "cs-016"],
        random_seed=20260807,
    )
    try:
        res = _runner.run(plan, DemoAppB1Driver(_client), seed=20260807)
    finally:
        try:
            _client.reset_faults()
        except Exception:
            pass

    # 裁决断言（机器口径）
    assert res.verdict["decision"] == "ATTRIBUTED", res.verdict
    assert res.verdict["attributed_layer"] == "prompt", res.verdict
    assert res.verdict["interaction_detected"] is False

    # 双 schema 校验通过
    bundle_errs = validate_report(res.bundle, "evidence-bundle.schema.json")
    report_errs = validate_report(res.report, "attribution-report.schema.json")
    assert bundle_errs == [], f"evidence-bundle schema: {bundle_errs}"
    assert report_errs == [], f"attribution-report schema: {report_errs}"

    # 证据落盘（供验收复算）
    out = EVAL_ROOT / "evidence" / plan.experiment_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "evidence-bundle.json").write_text(
        json.dumps(res.bundle, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "attribution-report.json").write_text(
        json.dumps(res.report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n证据已落盘: {out}")

    # 恢复后基线 chat 必须真实可用（空白归一化判定，模型可能写「30小时」）
    chat = _client.chat("X200 蓝牙耳机续航多久？")
    assert "30小时" in chat.answer.replace(" ", ""), f"复位后基线 chat 异常: {chat.answer[:120]}"


@pytest.mark.live
def test_b1_live_fault_then_reset_restores_baseline(live_settings, live_probe_set, _client):
    """故障注入后 /chat 应偏离基线；复位后 prompt digest 应回到基线 P0。"""
    r0 = _client.chat("退货的运费谁出？")
    baseline_digest = r0.prompt_digest
    _client.inject_fault("B1")
    try:
        r1 = _client.chat("退货的运费谁出？")
        assert r1.prompt_digest != baseline_digest, "B1 注入后 prompt digest 应偏离基线"
    finally:
        _client.reset_faults()
    r2 = _client.chat("退货的运费谁出？")
    assert r2.prompt_digest == baseline_digest, "复位后 prompt digest 应回到基线"
