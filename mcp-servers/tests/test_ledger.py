"""信任账本专项（spec §6 / §3.7 / §9.8；T8）。

必验：3/3 → 下界≈0.4385<0.9 → 拒绝晋升事件；一次动作=一个样本；epoch 滚动；
SUSPENDED 冷却 24h + 人工 reinstate（D-001 Q8，不自动恢复）；R2 永远逐次审批。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from common.errors import STATE_CONFLICT, McpError
from trust_ledger.ledger import TrustLedgerService
from sqlalchemy import text
from trust_ledger.wilson import wilson_interval


def _svc(session, settings):
    return TrustLedgerService(session, settings)


def test_mvp_3_of_3_records_but_denies_promotion(session, settings):
    """MVP 演示：3 次成功动作 → 记账（3/3），但下界≈0.4385<0.9 → 拒绝晋升。"""
    svc = _svc(session, settings)
    last = None
    for i in range(3):
        last = svc.record_outcome(
            risk_class="R1_REVERSIBLE_WRITE",
            action_type="case.triage",
            success=True,
            action_ref=f"op_{i}",
            causation_id=f"evt_{i}",
        )
        session.commit()
    assert last["epoch_successes"] == 3
    assert last["epoch_trials"] == 3
    assert last["promotion"]["decision"] == "denied"
    assert last["promotion"]["eligible"] is False
    assert last["wilson"]["lower"] == pytest.approx(0.438494, abs=1e-3)
    # 拒绝晋升事件已入审计（每次记账低于阈值都算拒绝；3/3 必含；reason 含数字另测）
    audit_rows = session.execute(
        text("SELECT action FROM mcp_audit WHERE action='trust.promotion_rejected'")
    ).all()
    assert len(audit_rows) >= 1


def test_get_state_shows_counts_and_wilson(session, settings):
    svc = _svc(session, settings)
    svc.record_outcome(risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=True, action_ref="op_1")
    session.commit()
    st = svc.get_state("R1_REVERSIBLE_WRITE", "case.triage")
    assert st["trials"] == 1
    assert st["successes"] == 1
    assert st["autonomy_state"] == "ELIGIBLE"
    assert st["LB"] == pytest.approx(0.206543, abs=1e-3)


def test_one_action_one_sample(session, settings):
    """一次动作=一个样本：多条探针只算 1 trial（内部探针数不计入）。"""
    svc = _svc(session, settings)
    # 动作内跑 5 条探针，但只 1 个样本
    svc.record_outcome(
        risk_class="R1_REVERSIBLE_WRITE",
        action_type="case.triage",
        success=True,
        action_ref="op_with_5_probes",
        detail="5 probes, one action",
    )
    session.commit()
    st = svc.get_state("R1_REVERSIBLE_WRITE", "case.triage")
    assert st["trials"] == 1, "探针数不得灌样本"
    assert st["successes"] == 1


def test_failure_suspends_and_rolls_epoch(session, settings):
    svc = _svc(session, settings)
    for i in range(2):
        svc.record_outcome(risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=True, action_ref=f"s{i}")
    session.commit()
    entry = svc.record_outcome(risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=False, action_ref="f1", detail="verification failed")
    session.commit()
    assert entry["outcome"]["status"] == "failure"
    assert entry["autonomy_state_after"] == "SUSPENDED"
    st = svc.get_state("R1_REVERSIBLE_WRITE", "case.triage")
    assert st["autonomy_state"] == "SUSPENDED"
    assert st["epoch"] == 2, "验证失败 → epoch+1"
    assert st["trials"] == 0, "新 epoch 计数清零"
    assert st["suspended_until"] is not None


def test_record_during_suspended_rejected(session, settings):
    svc = _svc(session, settings)
    svc.record_outcome(risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=False, action_ref="f1")
    session.commit()
    with pytest.raises(McpError) as exc:
        svc.record_outcome(risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=True, action_ref="x")
    assert exc.value.error_code == STATE_CONFLICT


def test_reinstate_requires_cooloff_elapsed_and_human(session, settings):
    svc = _svc(session, settings)
    svc.record_outcome(risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=False, action_ref="f1")
    session.commit()
    # 冷却未满 → 拒绝
    with pytest.raises(McpError) as exc:
        svc.reinstate("R1_REVERSIBLE_WRITE", "case.triage", confirmed_by="human:feishu_uid")
    assert exc.value.error_code == STATE_CONFLICT
    # 时间旅行：冷却期满
    row = session.execute(
        text(
            "SELECT * FROM mcp_trust_ledger WHERE risk_class='R1_REVERSIBLE_WRITE' AND action_type='case.triage' ORDER BY epoch DESC LIMIT 1"
        )
    ).mappings().first()
    session.execute(
        text(
            "UPDATE mcp_trust_ledger SET suspended_until=:past WHERE risk_class='R1_REVERSIBLE_WRITE' AND action_type='case.triage' AND epoch=:e"
        ),
        {"past": datetime.now(timezone.utc) - timedelta(hours=1), "e": row["epoch"]},
    )
    session.commit()
    res = svc.reinstate("R1_REVERSIBLE_WRITE", "case.triage", confirmed_by="human:feishu_uid")
    session.commit()
    assert res["autonomy_state"] == "ELIGIBLE"


def test_r2_never_promotes(session, settings):
    """R2_HIGH_IMPACT 永远逐次审批：即使下界>0.9 也不晋升。"""
    svc = _svc(session, settings)
    for _ in range(100):
        svc.record_outcome(risk_class="R2_HIGH_IMPACT", action_type="release.canary_step", success=True, action_ref=f"c{_}")
    session.commit()
    ev = svc.evaluate_promotion("R2_HIGH_IMPACT", "release.canary_step")
    assert ev["eligible"] is False
    assert ev["decision"] == "not_evaluable"
    st = svc.get_state("R2_HIGH_IMPACT", "release.canary_step")
    assert st["autonomy_state"] == "MANUAL"
    assert st["successes"] == 100
    assert wilson_interval(100, 100)[0] > 0.9  # 统计上达标但纪律不放行


def test_request_promotion_and_confirm(session, settings):
    """攒够证据 → 提请（AWAITING_CONFIRMATION）→ 人工确认（AUTO_ENABLED）。"""
    svc = _svc(session, settings)
    for i in range(100):
        svc.record_outcome(risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=True, action_ref=f"ok{i}")
    session.commit()
    st = svc.get_state("R1_REVERSIBLE_WRITE", "case.triage")
    assert st["autonomy_state"] == "AWAITING_CONFIRMATION", "达标后自动生成提请"
    res = svc.request_promotion(
        "R1_REVERSIBLE_WRITE", "case.triage", evidence_table_ref="feishu-mock:approval:evt"
    )
    assert res["autonomy_state"] == "AWAITING_CONFIRMATION"
    confirmed = svc.confirm_promotion("R1_REVERSIBLE_WRITE", "case.triage", confirmed_by="human:feishu_uid")
    session.commit()
    assert confirmed["autonomy_state"] == "AUTO_ENABLED"


def test_request_promotion_denied_when_below_threshold(session, settings):
    svc = _svc(session, settings)
    for i in range(3):
        svc.record_outcome(risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=True, action_ref=f"ok{i}")
    session.commit()
    with pytest.raises(McpError) as exc:
        svc.request_promotion("R1_REVERSIBLE_WRITE", "case.triage", evidence_table_ref="feishu-mock:x")
    assert exc.value.error_code == STATE_CONFLICT
    assert "0.4385" in exc.value.message


def test_evaluate_promotion_reason_contains_numbers(session, settings):
    """拒绝判定 reason 含具体数字（如 3/3 LB=0.4385<0.9）。"""
    svc = _svc(session, settings)
    for i in range(3):
        svc.record_outcome(risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=True, action_ref=f"ok{i}")
    session.commit()
    ev = svc.evaluate_promotion("R1_REVERSIBLE_WRITE", "case.triage")
    assert ev["decision"] == "denied"
    assert "3/3" in ev["reason"] and "LB=" in ev["reason"]
