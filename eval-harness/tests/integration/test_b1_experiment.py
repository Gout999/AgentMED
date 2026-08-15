"""Live B1 attribution against Release-Controller-created immutable VersionSets."""
import json
import os
from pathlib import Path

import pytest

from eval_harness.client import QualityAPIClient
from eval_harness.experiment import ImmutableVersionSetDriver, ExperimentRunner
from eval_harness.models import ExperimentPlan
from eval_harness.probe_loader import frozen_digest
from eval_harness.report import validate_report

EVAL_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def _client(live_settings):
    return QualityAPIClient(live_settings)


@pytest.mark.live
def test_b1_live_experiment_attributed_prompt(live_settings, live_probe_set, b1_protocol_reps, _client):
    bad_versionset_id = os.environ.get("AGENTMED_B1_BAD_VERSIONSET_ID")
    good_versionset_id = os.environ.get("AGENTMED_B1_GOOD_VERSIONSET_ID")
    if not bad_versionset_id or not good_versionset_id:
        pytest.skip("AGENTMED_B1_BAD_VERSIONSET_ID and AGENTMED_B1_GOOD_VERSIONSET_ID are required")
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
    driver = ImmutableVersionSetDriver(
        {
            "C": bad_versionset_id,
            "RP": good_versionset_id,
            "RK": bad_versionset_id,
            "RM": bad_versionset_id,
            "G": good_versionset_id,
        }
    )
    bad_ref = _client.get_versionset(bad_versionset_id)
    good_ref = _client.get_versionset(good_versionset_id)
    runner = ExperimentRunner(
        _client,
        live_probe_set,
        live_settings,
        cell_versionset_refs={
            "C": bad_ref,
            "RP": good_ref,
            "RK": bad_ref,
            "RM": bad_ref,
            "G": good_ref,
        },
    )
    res = runner.run(plan, driver, seed=20260807)

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

    # 精确 known-good candidate 必须真实可用（不依赖 active pointer）。
    chat = _client.evaluate_versionset(good_versionset_id, "X200 蓝牙耳机续航多久？")
    assert "30小时" in chat.answer.replace(" ", ""), f"复位后基线 chat 异常: {chat.answer[:120]}"


@pytest.mark.live
def test_b1_live_exact_candidates_do_not_mutate_active(live_settings, live_probe_set, _client):
    bad_versionset_id = os.environ.get("AGENTMED_B1_BAD_VERSIONSET_ID")
    good_versionset_id = os.environ.get("AGENTMED_B1_GOOD_VERSIONSET_ID")
    if not bad_versionset_id or not good_versionset_id:
        pytest.skip("immutable B1 VersionSet ids are required")
    active_before = (_client.list_versionsets(status="active").get("items") or [])
    bad = _client.evaluate_versionset(bad_versionset_id, "退货的运费谁出？")
    good = _client.evaluate_versionset(good_versionset_id, "退货的运费谁出？")
    active_after = (_client.list_versionsets(status="active").get("items") or [])
    assert bad.prompt_digest != good.prompt_digest
    assert active_after == active_before
