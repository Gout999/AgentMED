"""trust-ledger MVP 演示（smoke.sh 调用）：记账但拒绝晋升（spec §6.3 / D-001 #15）。

输出：3/3 → Wilson 下界≈0.4385<0.9 → 拒绝晋升；R2 永远逐次审批；SUSPENDED 冷却+人工 reinstate。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import Settings  # noqa: E402
from common.db import create_all, get_engine, get_session_factory  # noqa: E402
from trust_ledger.ledger import TrustLedgerService  # noqa: E402

URL = "sqlite:///:memory:"


def main() -> int:
    engine = get_engine(URL)
    create_all(URL)
    factory = get_session_factory(URL)
    session = factory()
    svc = TrustLedgerService(session, Settings(database_url=URL))

    # MVP：3/3 成功 → 拒绝晋升
    for i in range(3):
        svc.record_outcome(
            risk_class="R1_REVERSIBLE_WRITE",
            action_type="case.triage",
            success=True,
            action_ref=f"op_{i}",
            causation_id=f"evt_{i}",
        )
    session.commit()
    ev = svc.evaluate_promotion("R1_REVERSIBLE_WRITE", "case.triage")
    print(f"[trust] 3/3 → LB={ev['lower']} decision={ev['decision']} reason={ev['reason']}")
    assert ev["decision"] == "denied", "MVP: 3/3 必须拒绝晋升"

    # R2 永远逐次审批
    for i in range(100):
        svc.record_outcome(
            risk_class="R2_HIGH_IMPACT", action_type="release.canary_step", success=True, action_ref=f"c{i}"
        )
    session.commit()
    r2 = svc.evaluate_promotion("R2_HIGH_IMPACT", "release.canary_step")
    print(f"[trust] R2 100/100 → decision={r2['decision']}（永远逐次审批）")
    assert r2["decision"] == "not_evaluable"

    # 验证失败 → SUSPENDED + 冷却 + epoch 滚动
    svc.record_outcome(
        risk_class="R1_REVERSIBLE_WRITE", action_type="case.triage", success=False, action_ref="f1"
    )
    session.commit()
    st = svc.get_state("R1_REVERSIBLE_WRITE", "case.triage")
    print(f"[trust] 验证失败 → state={st['autonomy_state']} epoch={st['epoch']} suspended_until={st['suspended_until']}")
    assert st["autonomy_state"] == "SUSPENDED" and st["epoch"] == 2

    # 冷却未满 reinstate 拒绝
    try:
        svc.reinstate("R1_REVERSIBLE_WRITE", "case.triage", confirmed_by="human:feishu_uid")
        print("[trust] FAIL: 冷却未满不应 reinstate")
        return 1
    except Exception:
        print("[trust] 冷却未满 → reinstate 拒绝（须人工确认且冷却期满，D-001 Q8）")

    session.close()
    print("[trust] OK: MVP 记账但拒绝晋升 + R2 逐次审批 + SUSPENDED 冷却")
    return 0


if __name__ == "__main__":
    sys.exit(main())
