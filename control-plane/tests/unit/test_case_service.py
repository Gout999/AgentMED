"""Case Controller 单元测试：投诉接入 / 去重 / 领单 / fencing / 迁移。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.models.tables import Event, Inbox, Lease
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
        channel="feishu:oc_contract",
        title="屏幕问题",
    )
    assert r["duplicate"] is False
    assert r["case_id"].startswith("case_")
    assert r["state"] == "OPEN"
    # revision == 事件数（complaint.received + case.opened = 2）
    assert r["revision"] == 2
    agg = svc.store.get_aggregate("case", r["case_id"])
    assert agg.state == "OPEN"
    assert (agg.payload or {})["channel"] == "feishu:oc_contract"
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


def test_ingest_dedup_rejects_provider_provenance_drift(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    provenance = {
        "provider_origin": "https://open.feishu.cn",
        "provider_create_time": "1786212345000",
        "source_text_digest": "sha256:" + "a" * 64,
    }
    first = svc.ingest_complaint(
        source="webhook",
        text="同一條 provider 訊息",
        external_id="om_exact",
        **provenance,
    )
    replay = svc.ingest_complaint(
        source="webhook",
        text="同一條 provider 訊息",
        external_id="om_exact",
        **provenance,
    )
    assert replay["duplicate"] is True
    assert replay["case_id"] == first["case_id"]

    with pytest.raises(CaseServiceError) as exc:
        svc.ingest_complaint(
            source="webhook",
                text="同一 message id 被換內容",
                external_id="om_exact",
                provider_origin=provenance["provider_origin"],
                provider_create_time=provenance["provider_create_time"],
            source_text_digest="sha256:" + "b" * 64,
        )

    assert exc.value.code == "idempotency_conflict"
    assert sqlite_session.query(Inbox).count() == 1
    assert len(svc.list_cases()["items"]) == 1


def test_ingest_content_fingerprint_without_external_id(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    r1 = svc.ingest_complaint(
        source="poll", text="  联系 13800138000 处理  ", thread_ref="poll-thread-1"
    )
    r2 = svc.ingest_complaint(
        source="poll", text="联系 13800138000 处理", thread_ref="poll-thread-1"
    )
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

    checked = svc.validate_active_lease(case_id, "worker-a", c1["fencing_token"])
    assert checked["active"] is True
    assert checked["lease_id"] == c1["lease_id"]

    with pytest.raises(CaseServiceError) as exc:
        svc.validate_active_lease(case_id, "worker-b", c1["fencing_token"])
    assert exc.value.code == "lease_lost"


def _suggestion_events(session, case_id: str) -> list[Event]:
    return list(
        session.query(Event)
        .filter(
            Event.aggregate_type == "case",
            Event.aggregate_id == case_id,
            Event.event_type == "case.suggestion_recorded",
        )
        .all()
    )


def test_submit_suggestion_atomically_binds_active_owner_and_token(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    case_id = svc.ingest_complaint(
        source="webhook", text="x", external_id="suggestion-valid"
    )["case_id"]
    claim = svc.claim(case_id, "repairer-1")

    result = svc.submit_suggestion(
        case_id=case_id,
        worker_id="repairer-1",
        fencing_token=claim["fencing_token"],
        idempotency_key="suggestion-valid-001",
        kind="fix",
        payload={"candidate": "prompt-only"},
        evidence_refs=["evidence://proposal"],
    )

    assert result["accepted"] is True
    events = _suggestion_events(sqlite_session, case_id)
    assert len(events) == 1
    assert events[0].payload["worker_id"] == "repairer-1"
    assert events[0].payload["fencing_token"] == claim["fencing_token"]


def test_submit_suggestion_retry_is_idempotent_and_key_rebind_fails(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    case_id = svc.ingest_complaint(
        source="webhook", text="x", external_id="suggestion-idempotent"
    )["case_id"]
    claim = svc.claim(case_id, "repairer-1")
    request = {
        "case_id": case_id,
        "worker_id": "repairer-1",
        "fencing_token": claim["fencing_token"],
        "idempotency_key": "suggestion-idempotent-001",
        "kind": "fix",
        "payload": {"candidate": "prompt-only"},
        "evidence_refs": ["evidence://proposal"],
    }

    first = svc.submit_suggestion(**request)
    retry = svc.submit_suggestion(**request)

    assert first["duplicate"] is False
    assert retry == {**first, "duplicate": True}
    assert len(_suggestion_events(sqlite_session, case_id)) == 1

    with pytest.raises(CaseServiceError) as exc:
        svc.submit_suggestion(**{**request, "payload": {"candidate": "swapped"}})
    assert exc.value.code == "idempotency_conflict"
    assert len(_suggestion_events(sqlite_session, case_id)) == 1


@pytest.mark.parametrize(
    ("worker_id", "token_offset"),
    [("other-worker", 0), ("repairer-1", 1)],
)
def test_submit_suggestion_rejects_wrong_owner_or_stale_token_without_write(
    sqlite_session, worker_id, token_offset
):
    svc = CaseService(sqlite_session, _settings())
    case_id = svc.ingest_complaint(
        source="webhook", text="x", external_id=f"suggestion-{worker_id}-{token_offset}"
    )["case_id"]
    claim = svc.claim(case_id, "repairer-1")

    with pytest.raises(CaseServiceError) as exc:
        svc.submit_suggestion(
            case_id=case_id,
            worker_id=worker_id,
            fencing_token=claim["fencing_token"] + token_offset,
            idempotency_key=f"suggestion-reject-{worker_id}-{token_offset}",
            kind="fix",
            payload={"candidate": "must-not-persist"},
            evidence_refs=[],
        )

    assert exc.value.code == "lease_lost"
    assert _suggestion_events(sqlite_session, case_id) == []


def test_submit_suggestion_rejects_expired_lease_without_write(sqlite_session):
    svc = CaseService(sqlite_session, _settings())
    case_id = svc.ingest_complaint(
        source="webhook", text="x", external_id="suggestion-expired"
    )["case_id"]
    claim = svc.claim(case_id, "repairer-1")
    lease = sqlite_session.get(Lease, case_id)
    lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    sqlite_session.flush()

    with pytest.raises(CaseServiceError) as exc:
        svc.submit_suggestion(
            case_id=case_id,
            worker_id="repairer-1",
            fencing_token=claim["fencing_token"],
            idempotency_key="suggestion-expired-001",
            kind="fix",
            payload={"candidate": "must-not-persist"},
            evidence_refs=[],
        )

    assert exc.value.code == "lease_lost"
    assert _suggestion_events(sqlite_session, case_id) == []


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


def test_lease_check_api_validates_owner_and_token_without_renewing(app_client):
    client, _ = app_client
    complaint = client.post(
        "/v1/complaints",
        json={"source": "webhook", "text": "lease check", "external_id": "lease-api-1"},
    ).json()
    case_id = complaint["case_id"]
    claim = client.post(
        f"/v1/cases/{case_id}/claim",
        json={"worker_id": "repairer"},
    ).json()

    valid = client.post(
        f"/v1/cases/{case_id}/lease-check",
        json={"worker_id": "repairer", "fencing_token": claim["fencing_token"]},
    )
    stale = client.post(
        f"/v1/cases/{case_id}/lease-check",
        json={"worker_id": "other-worker", "fencing_token": claim["fencing_token"]},
    )

    assert valid.status_code == 200
    assert valid.json()["lease_id"] == claim["lease_id"]
    assert datetime.fromisoformat(valid.json()["expires_at"]).replace(
        tzinfo=None
    ) == datetime.fromisoformat(claim["expires_at"]).replace(tzinfo=None)
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "lease_lost"


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
