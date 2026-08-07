"""T2 验收五场景（PG 真跑）。

前置：`docker compose -f deploy/compose.yaml up -d postgres`。
1. inbox 去重：重复 event_id 不重复立案
2. lease fencing 防脑裂：旧 fencing token 的写被拒
3. 灰度全链路：draft→canary→promote 与 draft→canary→rollback 两条状态流
4. nonce 重放拒绝
5. 审计写失败 → 写操作返回 503
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.main import create_app
from app.models.tables import Base, Lease
from app.quality.client import FakeQualityClient
from app.services.case_service import CaseService
from app.services.release_service import ReleaseService

from tests.conftest import TEST_DATABASE_URL, make_approval, make_workorder

pytestmark = pytest.mark.integration


def _settings(**kw) -> Settings:
    base = dict(
        database_url=TEST_DATABASE_URL,
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
    )
    base.update(kw)
    return Settings(**base)


# ------------------------------------------------------------------ 场景 1：inbox 去重


def test_scenario_1_inbox_dedup_no_duplicate_filing(pg_session, pg_settings):
    svc = CaseService(pg_session, pg_settings)
    r1 = svc.ingest_complaint(source="webhook", text="手机屏碎了 13800138000", external_id="msg-1")
    pg_session.commit()

    r2 = svc.ingest_complaint(source="webhook", text="手机屏碎了 13800138000", external_id="msg-1")
    pg_session.commit()

    assert r2["duplicate"] is True
    assert r2["case_id"] == r1["case_id"]
    # 只立案一次
    cases = svc.list_cases()
    assert len(cases["items"]) == 1
    agg = svc.store.get_aggregate("case", r1["case_id"])
    assert agg is not None


# ------------------------------------------------------------------ 场景 2：lease fencing 防脑裂


def test_scenario_2_lease_fencing_rejects_stale_token(pg_session, pg_settings):
    svc = CaseService(pg_session, pg_settings)
    case_id = svc.ingest_complaint(source="webhook", text="问题", external_id="msg-2")["case_id"]
    pg_session.commit()

    c1 = svc.claim(case_id, "worker-a")
    stale_token = c1["fencing_token"]
    pg_session.commit()

    # 强制过期 → 另一 worker 重新领单（新 token）
    row = pg_session.get(Lease, case_id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    pg_session.commit()

    svc.reclaim_if_expired(case_id)
    c2 = svc.claim(case_id, "worker-b")
    pg_session.commit()
    assert c2["fencing_token"] != stale_token

    # 旧 token 的写被拒（防脑裂）
    from app.services.case_service import CaseServiceError

    with pytest.raises(CaseServiceError) as exc:
        svc.transition(
            case_id,
            "case.attribution_completed",
            {"verdict": "ATTRIBUTED"},
            fencing_token=stale_token,
            guard="verdict=ATTRIBUTED",
        )
    assert exc.value.code == "lease_lost"
    pg_session.rollback()


# ------------------------------------------------------------------ 场景 3：灰度全链路


def _new_release(svc: ReleaseService, session, case_id: str, seed: int, quality: FakeQualityClient) -> str:
    wo = make_workorder(workorder_id=f"wo_{seed:012d}", nonce=f"00000000-0000-0000-0000-{seed:012d}", case_id=case_id)
    svc.register_workorder(wo)
    session.commit()
    ap = make_approval(wo, f"ap_{seed}")
    svc.grant_approval(ap)
    session.commit()
    rel = svc.start_release(
        workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01"
    )
    session.commit()
    return rel["release_id"]


def test_scenario_3_gray_release_promote_and_rollback(pg_session, pg_settings):
    quality = FakeQualityClient()
    quality.seed_versionset("vs_demo001fixedversionset01", status="draft", revision=1)
    svc = ReleaseService(pg_session, quality, pg_settings)

    case_svc = CaseService(pg_session, pg_settings)
    case_id = case_svc.ingest_complaint(source="webhook", text="问题", external_id="msg-3")["case_id"]
    pg_session.commit()

    # ---- promote 全链路：draft → stage → canary → promote
    rid1 = _new_release(svc, pg_session, case_id, 11, quality)
    st = svc.stage(rid1, idempotency_key="itg-stage-1")
    pg_session.commit()
    assert st["state"] == "STAGING"
    ca = svc.canary(rid1, idempotency_key="itg-canary-1")
    pg_session.commit()
    assert ca["state"] == "CANARYING"
    pr = svc.promote(rid1, idempotency_key="itg-promote-1")
    pg_session.commit()
    assert pr["state"] == "COMPLETED"
    assert quality.get_versionset("vs_demo001fixedversionset01")["status"] == "active"

    # ---- rollback 全链路：draft → stage → canary → rollback
    quality.seed_versionset("vs_demo001fixedversionset01", status="draft", revision=1)  # 重置远端
    rid2 = _new_release(svc, pg_session, case_id, 12, quality)
    svc.stage(rid2, idempotency_key="itg-rb-stage-1")
    pg_session.commit()
    svc.canary(rid2, idempotency_key="itg-rb-canary-1")
    pg_session.commit()
    rb = svc.rollback(rid2, idempotency_key="itg-rb-rollback-1")
    pg_session.commit()
    assert rb["state"] == "ROLLED_BACK"
    assert quality.get_versionset("vs_demo001fixedversionset01")["status"] == "rolled_back"


# ------------------------------------------------------------------ 场景 4：nonce 重放拒绝


def test_scenario_4_nonce_replay_rejected(pg_session, pg_settings):
    quality = FakeQualityClient()
    quality.seed_versionset("vs_demo001fixedversionset01", status="draft", revision=1)
    svc = ReleaseService(pg_session, quality, pg_settings)

    case_svc = CaseService(pg_session, pg_settings)
    case_id = case_svc.ingest_complaint(source="webhook", text="问题", external_id="msg-4")["case_id"]
    pg_session.commit()

    wo = make_workorder(workorder_id="wo_000000000021", nonce="00000000-0000-0000-0000-000000000021", case_id=case_id)
    svc.register_workorder(wo)
    pg_session.commit()
    ap = make_approval(wo, "ap_000000000021")
    svc.grant_approval(ap)
    pg_session.commit()

    svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    pg_session.commit()

    # nonce 已消费 → 重放拒绝
    from app.services.release_service import ReleaseServiceError

    with pytest.raises(ReleaseServiceError) as exc:
        svc.start_release(workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01")
    assert exc.value.code == "nonce_replay"
    pg_session.rollback()


# ------------------------------------------------------------------ 补充：UNKNOWN→reconcile 退避


def test_scenario_6_unknown_reconcile(pg_session, pg_settings):
    """写操作结果不可考 → UNKNOWN；reconcile 以 GET /status 对账收敛（含指数退避循环）。"""
    quality = FakeQualityClient()
    quality.seed_versionset("vs_demo001fixedversionset01", status="draft", revision=1)
    svc = ReleaseService(pg_session, quality, pg_settings)

    case_svc = CaseService(pg_session, pg_settings)
    case_id = case_svc.ingest_complaint(source="webhook", text="问题", external_id="msg-6")["case_id"]
    pg_session.commit()

    wo = make_workorder(workorder_id="wo_000000000031", nonce="00000000-0000-0000-0000-000000000031", case_id=case_id)
    svc.register_workorder(wo)
    pg_session.commit()
    ap = make_approval(wo, "ap_000000000031")
    svc.grant_approval(ap)
    pg_session.commit()
    rid = svc.start_release(
        workorder_id=wo["workorder_id"], approval_id=ap["approval_id"], versionset_id="vs_demo001fixedversionset01"
    )["release_id"]
    pg_session.commit()

    svc.stage(rid, idempotency_key="itg-unk-stage-1")
    pg_session.commit()
    # canary 进入 pending → 轮询超时 → UNKNOWN（远端实际已生效）
    quality.unknown_ops = True
    unk = svc.canary(rid, idempotency_key="itg-unk-canary-1")
    pg_session.commit()
    assert unk["state"] == "UNKNOWN"
    assert unk["status"] == "unknown"

    rc = svc.reconcile_loop(rid, max_attempts=3)
    assert rc["state"] == "CANARYING"
    assert rc["remote_status"] == "canary"
    pg_session.commit()


# ------------------------------------------------------------------ 场景 5：审计写失败 → 503


def test_scenario_5_audit_failure_returns_503(pg_engine):
    """审计写失败 → 写操作返回 503 且业务拒绝（无 case 落库）。"""
    settings = _settings(audit_force_fail=True)
    app = create_app(settings=settings, quality_client=FakeQualityClient(), engine=pg_engine, create_tables=True)
    with TestClient(app) as client:
        resp = client.post(
            "/v1/complaints",
            json={"source": "webhook", "text": "审计失败不应立案", "external_id": "msg-5"},
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body["detail"]["code"] == "audit_unavailable"

        # 业务被拒绝：无 case、无 inbox 残留
        cases = client.get("/v1/cases").json()
        assert cases["items"] == []
