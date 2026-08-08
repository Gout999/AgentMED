"""Case Controller 单元测试：投诉接入 / 去重 / 领单 / fencing / 迁移。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.models.tables import Inbox
from app.services.case_service import CaseService, CaseServiceError
from app.services.event_store import EventStore
from app.services.lease import LeaseService


def _settings(**kw) -> Settings:
    base = dict(
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
    )
    base.update(kw)
    return Settings(**base)


def test_ingest_creates_open_case(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    r = svc.ingest_complaint(
        source="webhook",
        text="手机坏了 13800138000",
        external_id="m1",
        title="屏幕问题",
    )
    assert r["duplicate"] is False
    assert r["case_id"].startswith("case_")
    assert r["state"] == "OPEN"
    # revision == 事件数（complaint.received + case.opened = 2）
    assert r["revision"] == 2
    agg = svc.store.get_aggregate("case", r["case_id"])
    assert agg.state == "OPEN"
    assert svc.list_cases()["items"][0]["title"] == "屏幕问题"


def test_ingest_dedup_within_window(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    r1 = svc.ingest_complaint(source="webhook", text="问题A", external_id="m1")
    r2 = svc.ingest_complaint(source="webhook", text="问题A", external_id="m1")
    assert r2["duplicate"] is True
    assert r2["case_id"] == r1["case_id"]
    # 只立案一次
    rows = sqlite_session.query(Inbox).all()
    assert len(rows) == 1
    cases = svc.list_cases()
    assert len(cases["items"]) == 1


def test_ingest_content_fingerprint_without_external_id(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    r1 = svc.ingest_complaint(source="poll", text="  联系 13800138000 处理  ")
    r2 = svc.ingest_complaint(source="poll", text="联系 13800138000 处理")
    # D-001 Q4：归一化后同键
    assert r2["duplicate"] is True
    assert r2["case_id"] == r1["case_id"]


def test_ingest_refile_outside_window(sqlite_session):
    svc = CaseService(sqlite_session, _settings(complaint_dedup_window_hours=24))
    r1 = svc.ingest_complaint(source="webhook", text="旧投诉", external_id="m1")
    # 把 received_at 改到 25h 前 → 去重窗外
    row = sqlite_session.get(Inbox, r1["dedup_key"])
    row.received_at = datetime.now(timezone.utc) - timedelta(hours=25)
    sqlite_session.flush()
    r2 = svc.ingest_complaint(source="webhook", text="新投诉", external_id="m1")
    assert r2["duplicate"] is False
    assert r2["case_id"] != r1["case_id"]
    assert r2["dedup_key"] != r1["dedup_key"]  # 换键新立案
    assert len(svc.list_cases()["items"]) == 2


def test_claim_and_fencing(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    case_id = svc.ingest_complaint(source="webhook", text="x", external_id="m2")["case_id"]
    sqlite_session.flush()

    c1 = svc.claim(case_id, "worker-a")
    assert c1["state"] == "DISPATCHED"
    assert c1["fencing_token"] >= 1

    # 心跳续租 OK
    hb = svc.heartbeat(case_id, "worker-a", c1["fencing_token"])
    assert hb["fencing_token"] == c1["fencing_token"]


def test_stale_fencing_token_write_rejected(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    case_id = svc.ingest_complaint(source="webhook", text="x", external_id="m3")["case_id"]
    sqlite_session.flush()
    c1 = svc.claim(case_id, "worker-a")

    # 强制过期 → 另一 worker 重新领单（新 token）
    from app.models.tables import Lease

    row = sqlite_session.get(Lease, case_id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    sqlite_session.flush()
    svc.reclaim_if_expired(case_id)
    c2 = svc.claim(case_id, "worker-b")
    assert c2["fencing_token"] != c1["fencing_token"]

    # 旧 token 写被拒（防脑裂）
    with pytest.raises(CaseServiceError) as exc:
        svc.transition(
            case_id,
            "case.attribution_completed",
            {"verdict": "ATTRIBUTED"},
            fencing_token=c1["fencing_token"],
            guard="verdict=ATTRIBUTED",
        )
    assert exc.value.code == "lease_lost"


def test_claim_conflict_different_worker(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    case_id = svc.ingest_complaint(source="webhook", text="x", external_id="m4")["case_id"]
    sqlite_session.flush()
    svc.claim(case_id, "worker-a")
    with pytest.raises(CaseServiceError) as exc:
        svc.claim(case_id, "worker-b")
    assert exc.value.code == "lease_conflict"


def test_transition_cas_conflict(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    case_id = svc.ingest_complaint(source="webhook", text="x", external_id="m5")["case_id"]
    sqlite_session.flush()
    with pytest.raises(CaseServiceError) as exc:
        svc.transition(case_id, "case.escalated", {}, expected_revision=999)
    assert exc.value.code == "revision_conflict"


def test_worker_lost_requeues(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    case_id = svc.ingest_complaint(source="webhook", text="x", external_id="m6")["case_id"]
    sqlite_session.flush()
    svc.claim(case_id, "worker-a")
    from app.models.tables import Lease

    row = sqlite_session.get(Lease, case_id)
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    sqlite_session.flush()
    result = svc.reclaim_if_expired(case_id)
    assert result is not None
    assert result["state"] == "OPEN"
