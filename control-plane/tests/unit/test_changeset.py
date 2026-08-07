"""ChangeSet 状态机服务单元测试。"""
import pytest

from app.config import Settings
from app.services.changeset_service import ChangeSetService, ChangeSetServiceError
from app.services.state_machines import IllegalTransition


def _svc(session) -> ChangeSetService:
    return ChangeSetService(session, Settings())


def test_changeset_approval_flow(sqlite_session):
    svc = _svc(sqlite_session)
    r = svc.create(case_id="case_x", workorder_ref="wo_12345678", workorder_hash="h" * 64, channel="prompt", author_agent="repairer-1")
    cs_id = r["changeset_id"]
    assert r["state"] == "DRAFTED"

    r = svc.attach_gate(cs_id, gate_report_ref="gr://1")
    assert r["state"] == "GATE_ATTACHED"

    r = svc.request_approval(cs_id, workorder_hash="h" * 64, nonce="n1", expiry="2099-01-01T00:00:00+00:00")
    assert r["state"] == "AWAITING_APPROVAL"

    r = svc.approve(cs_id, approval_id="ap_1", approver="human-1", workorder_hash="h" * 64)
    assert r["state"] == "APPROVED"

    r = svc.commit(cs_id, release_id="rel_1")
    assert r["state"] == "COMMITTED"


def test_changeset_reject_flow(sqlite_session):
    svc = _svc(sqlite_session)
    cs_id = svc.create(case_id="case_x", workorder_ref="wo_2", workorder_hash="h" * 64, channel="prompt", author_agent="r")["changeset_id"]
    svc.attach_gate(cs_id, gate_report_ref="gr://1")
    svc.request_approval(cs_id, workorder_hash="h" * 64, nonce="n2", expiry="2099-01-01T00:00:00+00:00")
    r = svc.reject(cs_id, approval_id="ap_2", approver="human-1", reason="no")
    assert r["state"] == "REJECTED"


def test_changeset_expire_from_approved(sqlite_session):
    svc = _svc(sqlite_session)
    cs_id = svc.create(case_id="case_x", workorder_ref="wo_3", workorder_hash="h" * 64, channel="prompt", author_agent="r")["changeset_id"]
    svc.attach_gate(cs_id, gate_report_ref="gr://1")
    svc.request_approval(cs_id, workorder_hash="h" * 64, nonce="n3", expiry="2099-01-01T00:00:00+00:00")
    svc.approve(cs_id, approval_id="ap_3", approver="human-1", workorder_hash="h" * 64)
    r = svc.expire(cs_id, workorder_hash="h" * 64, expiry="2099-01-01T00:00:00+00:00")
    assert r["state"] == "EXPIRED"


def test_changeset_supersede(sqlite_session):
    svc = _svc(sqlite_session)
    cs_id = svc.create(case_id="case_x", workorder_ref="wo_4", workorder_hash="h" * 64, channel="prompt", author_agent="r")["changeset_id"]
    r = svc.supersede(cs_id, replaced_by="cs_new")
    assert r["state"] == "SUPERSEDED"


def test_illegal_transition(sqlite_session):
    svc = _svc(sqlite_session)
    cs_id = svc.create(case_id="case_x", workorder_ref="wo_5", workorder_hash="h" * 64, channel="prompt", author_agent="r")["changeset_id"]
    with pytest.raises(IllegalTransition):
        svc.approve(cs_id, approval_id="ap_x", approver="human-1", workorder_hash="h" * 64)  # DRAFTED 不能直接 approve
