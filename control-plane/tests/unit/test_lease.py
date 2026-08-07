"""Lease + fencing token 单元测试（D-001: 60s 心跳续租；旧 token 写拒绝）。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.services.lease import LeaseConflict, LeaseLost, LeaseService


def _settings(**kw) -> Settings:
    base = dict(lease_ttl_seconds=60)
    base.update(kw)
    return Settings(**base)


def test_claim_issues_monotonic_fencing_token(sqlite_session):
    svc = LeaseService(sqlite_session, _settings())
    l1 = svc.claim("res-1", "worker-a")
    token1 = int(l1.fencing_token)
    l2 = svc.claim("res-2", "worker-b")
    assert int(l2.fencing_token) > token1  # 全局单调递增


def test_claim_same_owner_reissues_token(sqlite_session):
    svc = LeaseService(sqlite_session, _settings())
    l1 = svc.claim("res-1", "worker-a")
    token1 = int(l1.fencing_token)
    lease_id1 = l1.lease_id
    l2 = svc.claim("res-1", "worker-a")
    assert int(l2.fencing_token) != token1  # 换发新 fencing token
    assert l2.lease_id != lease_id1


def test_claim_conflict_different_owner(sqlite_session):
    svc = LeaseService(sqlite_session, _settings())
    svc.claim("res-1", "worker-a")
    with pytest.raises(LeaseConflict):
        svc.claim("res-1", "worker-b")


def test_claim_after_expiry_different_owner(sqlite_session):
    svc = LeaseService(sqlite_session, _settings(lease_ttl_seconds=-1))  # 立即过期
    l1 = svc.claim("res-1", "worker-a")
    token1 = int(l1.fencing_token)
    l2 = svc.claim("res-1", "worker-b")  # 过期回收，允许
    assert int(l2.fencing_token) > token1


def test_heartbeat_with_stale_token_rejected(sqlite_session):
    svc = LeaseService(sqlite_session, _settings())
    l1 = svc.claim("res-1", "worker-a")
    token1 = int(l1.fencing_token)
    # 直接操纵 expires_at 模拟过期
    from app.models.tables import Lease as LeaseRow

    row = svc.session.get(LeaseRow, "res-1")
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    svc.session.flush()
    l2 = svc.claim("res-1", "worker-b")
    assert int(l2.fencing_token) != token1
    with pytest.raises(LeaseLost):
        svc.heartbeat("res-1", "worker-a", token1)  # 旧 token 被拒
    with pytest.raises(LeaseLost):
        svc.check_fencing("res-1", token1)


def test_check_fencing_ok_with_current_token(sqlite_session):
    svc = LeaseService(sqlite_session, _settings())
    l1 = svc.claim("res-1", "worker-a")
    assert svc.check_fencing("res-1", int(l1.fencing_token)).owner_id == "worker-a"


def test_is_expired_when_no_lease(sqlite_session):
    svc = LeaseService(sqlite_session, _settings())
    assert svc.is_expired("no-such-resource") is True
