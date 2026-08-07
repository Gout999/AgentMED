"""Release Controller 单元测试（FakeQualityClient + SQLite）。

覆盖：WorkOrder hash 校验 / Approval nonce / 灰度全链路（promote|rollback）/
UNKNOWN→reconcile / 幂等 Idempotency-Key。
"""
import pytest

from app.config import Settings
from app.models.tables import Approval, WorkOrder
from app.quality.client import FakeQualityClient
from app.services.audit import AuditWriteError
from app.services.release_service import ReleaseService, ReleaseServiceError

from tests.conftest import make_approval, make_workorder


def _settings(**kw) -> Settings:
    base = dict(
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
    )
    base.update(kw)
    return Settings(**base)


def _svc(session, quality=None, settings=None) -> tuple[ReleaseService, FakeQualityClient]:
    q = quality or FakeQualityClient()
    q.seed_versionset("vs_demo001fixedversionset01", status="draft", revision=1)
    return ReleaseService(session, q, settings or _settings()), q


def _full_release(session, svc, quality, case_id, seed: int):
    """register + grant + start，返回 (release_id, workorder, approval_id)。"""
    nonce = f"00000000-0000-0000-0000-{seed:012d}"
    wo = make_workorder(workorder_id=f"wo_{seed:012d}", nonce=nonce, case_id=case_id)
    svc.register_workorder(wo)
    session.flush()
    ap = make_approval(wo, f"ap_{seed}")
    svc.grant_approval(ap)
    session.flush()
    rel = svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    session.flush()
    return rel["release_id"], wo, ap["approval_id"]


def test_register_workorder_valid_and_duplicate(sqlite_session):
    svc, _ = _svc(sqlite_session)
    wo = make_workorder(workorder_id="wo_abcdefg1", nonce="00000000-0000-0000-0000-000000000001", case_id="case_x")
    r = svc.register_workorder(wo)
    assert r["duplicate"] is False
    r2 = svc.register_workorder(wo)
    assert r2["duplicate"] is True


def test_register_workorder_hash_mismatch(sqlite_session):
    svc, _ = _svc(sqlite_session)
    wo = make_workorder(workorder_id="wo_abcdefg2", nonce="00000000-0000-0000-0000-000000000002", case_id="case_x")
    wo["hash"] = "0" * 64  # 篡改
    with pytest.raises(ReleaseServiceError) as exc:
        svc.register_workorder(wo)
    assert exc.value.code == "hash_mismatch"


def test_grant_approval_nonce_binding(sqlite_session):
    svc, _ = _svc(sqlite_session)
    wo = make_workorder(workorder_id="wo_abcdefg3", nonce="00000000-0000-0000-0000-000000000003", case_id="case_x")
    svc.register_workorder(wo)
    sqlite_session.flush()
    ap = make_approval(wo, "ap_abcdefg3")
    # 篡改 workorder_hash → 拒绝
    bad = {**ap, "workorder_hash": "1" * 64}
    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(bad)
    assert exc.value.code == "hash_mismatch"


def test_grant_approval_nonce_replay(sqlite_session):
    svc, _ = _svc(sqlite_session)
    wo = make_workorder(workorder_id="wo_abcdefg4", nonce="00000000-0000-0000-0000-000000000004", case_id="case_x")
    svc.register_workorder(wo)
    sqlite_session.flush()
    ap = make_approval(wo, "ap_abcdefg4a")
    svc.grant_approval(ap)
    sqlite_session.flush()
    # 同一 nonce 再登记 → nonce_replay
    ap2 = make_approval(wo, "ap_abcdefg4b")
    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(ap2)
    assert exc.value.code == "nonce_replay"


def test_start_release_nonce_consumed_and_replay(sqlite_session):
    svc, q = _svc(sqlite_session)
    case_id = "case_x"
    wo = make_workorder(workorder_id="wo_abcdefg5", nonce="00000000-0000-0000-0000-000000000005", case_id=case_id)
    svc.register_workorder(wo)
    sqlite_session.flush()
    ap = make_approval(wo, "ap_abcdefg5")
    svc.grant_approval(ap)
    sqlite_session.flush()
    r1 = svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    assert r1["state"] == "REQUESTED"
    sqlite_session.flush()
    # nonce 已消费 → 重放拒绝
    with pytest.raises(ReleaseServiceError) as exc:
        svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    assert exc.value.code == "nonce_replay"


def test_promote_full_path(sqlite_session):
    svc, q = _svc(sqlite_session)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 1)
    st = svc.stage(rid, idempotency_key="idem-p-1")
    assert st["state"] == "STAGING"
    sqlite_session.flush()
    ca = svc.canary(rid, idempotency_key="idem-p-2")
    assert ca["state"] == "CANARYING"
    sqlite_session.flush()
    pr = svc.promote(rid, idempotency_key="idem-p-3")
    assert pr["state"] == "COMPLETED"
    sqlite_session.flush()
    # 远端 VS 已 active
    vs = q.get_versionset("vs_demo001fixedversionset01")
    assert vs["status"] == "active"


def test_rollback_full_path(sqlite_session):
    svc, q = _svc(sqlite_session)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 2)
    svc.stage(rid, idempotency_key="idem-rb-1")
    sqlite_session.flush()
    svc.canary(rid, idempotency_key="idem-rb-2")
    sqlite_session.flush()
    rb = svc.rollback(rid, idempotency_key="idem-rb-3")
    assert rb["state"] == "ROLLED_BACK"
    sqlite_session.flush()
    vs = q.get_versionset("vs_demo001fixedversionset01")
    assert vs["status"] == "rolled_back"


def test_idempotency_key_dedupe(sqlite_session):
    svc, q = _svc(sqlite_session)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 3)
    r1 = svc.stage(rid, idempotency_key="idem-dup-1")
    sqlite_session.flush()
    r2 = svc.stage(rid, idempotency_key="idem-dup-1")  # 同 key 重放
    assert r2["duplicate"] is True
    assert r2["operation_id"] == r1["operation_id"]


def test_unknown_then_reconcile(sqlite_session):
    q = FakeQualityClient()
    q.seed_versionset("vs_demo001fixedversionset01", status="draft", revision=1)
    svc, _ = _svc(sqlite_session, quality=q)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 4)
    svc.stage(rid, idempotency_key="idem-un-1")
    sqlite_session.flush()
    # 后续 op 进入 pending → 轮询超时 → UNKNOWN
    q.unknown_ops = True
    unk = svc.canary(rid, idempotency_key="idem-un-2")
    assert unk["state"] == "UNKNOWN"
    assert unk["status"] == "unknown"
    sqlite_session.flush()
    rc = svc.reconcile(rid)
    assert rc["state"] == "CANARYING"
    assert rc["remote_status"] == "canary"
    sqlite_session.flush()


def test_reconcile_loop_converges(sqlite_session):
    q = FakeQualityClient()
    q.seed_versionset("vs_demo001fixedversionset01", status="draft", revision=1)
    svc, _ = _svc(sqlite_session, quality=q)
    case_id = "case_x"
    rid, wo, apid = _full_release(sqlite_session, svc, q, case_id, 5)
    svc.stage(rid, idempotency_key="idem-lp-1")
    sqlite_session.flush()
    q.unknown_ops = True
    svc.canary(rid, idempotency_key="idem-lp-2")
    sqlite_session.flush()
    last = svc.reconcile_loop(rid, max_attempts=3)
    assert last["state"] == "CANARYING"


def test_audit_failure_blocks_release_write(sqlite_session):
    settings = _settings(audit_force_fail=True)
    q = FakeQualityClient()
    q.seed_versionset("vs_demo001fixedversionset01", status="draft", revision=1)
    svc = ReleaseService(sqlite_session, q, settings)
    wo = make_workorder(workorder_id="wo_abcdefg6", nonce="00000000-0000-0000-0000-000000000006", case_id="case_x")
    with pytest.raises(AuditWriteError):
        svc.register_workorder(wo)
    # 审计失败 → 业务拒绝（同事务回滚后 WorkOrder 不落库）
    sqlite_session.rollback()
    assert sqlite_session.get(WorkOrder, wo["workorder_id"]) is None
