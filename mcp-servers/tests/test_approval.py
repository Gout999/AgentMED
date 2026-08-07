"""ApprovalGrant 安全件专项：防掉包防重放（spec §5.2 / §11.1）。

必验：nonce 重放拒、expiry 过期拒、hash 不匹配拒、proof server_recorded + audit URI、
TTL 30min 强制。zeroops 静态 token 可重放方案已废弃。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from common.approval import ApprovalService
from common.jcs import workorder_hash
from common.tables import ApprovalGrantRow
from common.errors import (
    APPROVAL_EXPIRED,
    APPROVAL_MISMATCH,
    APPROVAL_REPLAYED,
    McpError,
    VALIDATION_FAILED,
)
from tests.helpers import make_workorder, nonce


def _grant(session, settings, *, workorder=None, expiry=None, approver_type="human") -> tuple[dict, str]:
    wo = workorder or make_workorder(nonce())
    approver = {"type": approver_type, "identity": "feishu:ou_test"}
    svc = ApprovalService(session, settings)
    result = svc.grant(
        approval_id=f"appr_test_{wo['hash'][:12]}",
        workorder_id=wo["workorder_id"],
        workorder_hash=wo["hash"],
        nonce=wo["nonce"],
        expiry=expiry or (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
        approver=approver,
        decision="approved",
        decided_at=datetime.now(timezone.utc).isoformat(),
    )
    session.commit()
    return result, wo


def test_valid_approval_passes(session, settings):
    wo = make_workorder(nonce())
    _, wo = _grant(session, settings, workorder=wo)
    verdict = ApprovalService(session, settings).validate_for_release(
        approval_id=f"appr_test_{wo['hash'][:12]}",
        presented_workorder=wo,
    )
    assert verdict["valid"] is True
    assert verdict["workorder_hash"] == wo["hash"]
    assert verdict["proof"]["method"] == "server_recorded"
    assert verdict["audit_uri"].startswith("audit://")


def test_nonce_replay_rejected_on_second_consume(session, settings):
    """nonce 一次性：消费后再出示必须拒绝（APPROVAL_REPLAYED）。"""
    wo = make_workorder(nonce())
    approval_id = f"appr_test_{wo['hash'][:12]}"
    _grant(session, settings, workorder=wo)
    svc = ApprovalService(session, settings)
    assert svc.validate_for_release(approval_id=approval_id, presented_workorder=wo)["valid"] is True
    assert svc.consume_nonce(approval_id=approval_id, nonce=wo["nonce"]) is True
    session.commit()
    with pytest.raises(McpError) as exc:
        svc.validate_for_release(approval_id=approval_id, presented_workorder=wo)
    assert exc.value.error_code == APPROVAL_REPLAYED


def test_nonce_replay_rejected_on_second_grant(session, settings):
    """同一 nonce 二次登记 grant → APPROVAL_REPLAYED。"""
    wo = make_workorder(nonce())
    _grant(session, settings, workorder=wo)
    with pytest.raises(McpError) as exc:
        _grant(session, settings, workorder=wo)
    assert exc.value.error_code == APPROVAL_REPLAYED


def test_consume_nonce_is_atomic(session, settings):
    """原子消费：rowcount=1 才成功；二次消费 rowcount=0 → False。"""
    wo = make_workorder(nonce())
    approval_id = f"appr_test_{wo['hash'][:12]}"
    _grant(session, settings, workorder=wo)
    svc = ApprovalService(session, settings)
    assert svc.consume_nonce(approval_id=approval_id, nonce=wo["nonce"]) is True
    session.commit()
    assert svc.consume_nonce(approval_id=approval_id, nonce=wo["nonce"]) is False


def test_expired_approval_rejected(session, settings):
    """expiry 过期 → APPROVAL_EXPIRED（validate_for_release 路径）。"""
    wo = make_workorder(nonce())
    approval_id = f"appr_test_{wo['hash'][:12]}"
    _grant(session, settings, workorder=wo)
    svc = ApprovalService(session, settings)
    # 时间旅行：把 expiry 拨到过去
    row = session.get(ApprovalGrantRow, approval_id)
    row.expiry = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.commit()
    with pytest.raises(McpError) as exc:
        svc.validate_for_release(approval_id=approval_id, presented_workorder=wo)
    assert exc.value.error_code == APPROVAL_EXPIRED


def test_hash_mismatch_rejected(session, settings):
    """审批即批 hash：WorkOrder 内容掉包（diff 改一个字节）→ APPROVAL_MISMATCH。"""
    wo = make_workorder(nonce())
    approval_id = f"appr_test_{wo['hash'][:12]}"
    _grant(session, settings, workorder=wo)
    tampered = dict(wo)
    tampered["diff"] = dict(wo["diff"], content_ref="minio://case-loop/evil.diff")
    tampered["hash"] = workorder_hash(tampered)
    with pytest.raises(McpError) as exc:
        ApprovalService(session, settings).validate_for_release(
            approval_id=approval_id, presented_workorder=tampered
        )
    assert exc.value.error_code == APPROVAL_MISMATCH


def test_ttl_exceeded_at_grant_rejected(session, settings):
    """TTL 30min 强制：expiry 超过 now+30min → VALIDATION_FAILED。"""
    wo = make_workorder(nonce())
    with pytest.raises(McpError) as exc:
        _grant(
            session,
            settings,
            workorder=wo,
            expiry=(datetime.now(timezone.utc) + timedelta(minutes=31)).isoformat(),
        )
    assert exc.value.error_code == VALIDATION_FAILED


def test_approver_must_be_human(session, settings):
    """LLM 永远不是权限权威源：approver.type 必须 human。"""
    wo = make_workorder(nonce())
    with pytest.raises(McpError) as exc:
        _grant(session, settings, workorder=wo, approver_type="llm")
    assert exc.value.error_code == VALIDATION_FAILED


def test_proof_and_audit_uri_required(session, settings):
    """Q7：server_recorded proof + audit URI 缺一不可。"""
    wo = make_workorder(nonce())
    svc = ApprovalService(session, settings)
    with pytest.raises(McpError) as exc:
        svc.grant(
            approval_id=f"appr_test2_{wo['hash'][:12]}",
            workorder_id=wo["workorder_id"],
            workorder_hash=wo["hash"],
            nonce=wo["nonce"],
            expiry=(datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
            approver={"type": "human", "identity": "feishu:ou_t"},
            decision="approved",
            decided_at=datetime.now(timezone.utc).isoformat(),
            proof={"method": "hmac_sha256", "ref": "sig:abc"},
        )
    assert exc.value.error_code == VALIDATION_FAILED
