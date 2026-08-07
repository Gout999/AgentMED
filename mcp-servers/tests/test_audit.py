"""审计权威源与失败即拒（spec §7.6 / §11.4）：写库失败即抛业务 503，不放行。"""
from __future__ import annotations

import pytest

from common.audit import AuditService, AuditWriteError
from common.config import Settings


def test_audit_record_success(session, settings):
    svc = AuditService(session, settings)
    row = svc.record(
        actor="repairer",
        action="workorder.draft",
        target="wo_test",
        params={"case_id": "case_test"},
        result="success",
    )
    session.commit()
    assert row.audit_id.startswith("aud_")
    assert row.params_digest.startswith("sha256:")
    assert row.trace_id.startswith("tr_")


def test_audit_write_failure_raises_not_passthrough(session):
    """AUDIT_FORCE_FAIL 模拟写库失败 → 抛 AuditWriteError（上层映射 503），业务不放行。"""
    failing = Settings(
        database_url="sqlite:///:memory:",
        AUDIT_FORCE_FAIL=True,
        audit_jsonl_path="/tmp/caseloop-mcp-test-audit.jsonl",
    )
    svc = AuditService(session, failing)
    with pytest.raises(AuditWriteError):
        svc.record(
            actor="repairer",
            action="workorder.draft",
            target="wo_test",
            params={"x": 1},
        )


def test_audit_write_failure_rejects_business_write(session):
    """审计失败即拒业务：业务写操作（含审计）整体不落库，不放行。

    用 AUDIT_FORCE_FAIL 模拟审计写库失败：record 抛 AuditWriteError → 业务拒绝，
    且业务数据（示例 draft 行）不持久化。
    """
    failing = Settings(
        database_url="sqlite:///:memory:",
        AUDIT_FORCE_FAIL=True,
        audit_jsonl_path="/tmp/caseloop-mcp-test-audit.jsonl",
    )
    svc = AuditService(session, failing)
    # 模拟一个"业务写 + 审计"的原子事务：审计失败 → 业务不提交
    from common.tables import Suggestion

    suggestion = Suggestion(suggestion_id="sug_test_force_fail", case_id="case_test", worker_id="w")
    session.add(suggestion)
    try:
        svc.record(actor="w", action="case.suggestion", target="case_test", params={})
        raise AssertionError("audit write should have failed")
    except AuditWriteError:
        session.rollback()  # 业务事务回滚（不放行）
    assert session.get(Suggestion, "sug_test_force_fail") is None, "审计失败后业务不得落库"


def test_audit_error_code_and_evidence_refs(session, settings):
    svc = AuditService(session, settings)
    row = svc.record(
        actor="controller:trust-ledger",
        action="trust.promotion_rejected",
        target="case.triage:R1_REVERSIBLE_WRITE",
        params={"reason": "3/3 LB=0.4385<0.9", "trials": 3, "successes": 3},
        result="denied",
        error_code="PROMOTION_DENIED",
        evidence_refs={"proof": {"method": "server_recorded"}},
    )
    session.commit()
    assert row.result == "denied"
    assert row.error_code == "PROMOTION_DENIED"
    assert row.evidence_refs == {"proof": {"method": "server_recorded"}}
