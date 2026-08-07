"""Notification 状态机服务单元测试。"""
import pytest

from app.config import Settings
from app.services.notification_service import NotificationService, NotificationServiceError


def _svc(session) -> NotificationService:
    return NotificationService(session, Settings())


def test_queue_creates_queued_with_outbox(sqlite_session):
    svc = _svc(sqlite_session)
    r = svc.queue(case_id="case_x", channel="feishu", thread_ref="feishu:c:r", body_ref="inline:1")
    assert r["state"] == "QUEUED"
    assert r["notification_id"].startswith("notif_")
    assert r["outbox_id"].startswith("obx_")
    from app.models.tables import Outbox

    ob = sqlite_session.query(Outbox).filter_by(aggregate_id=r["notification_id"]).first()
    assert ob is not None
    assert ob.status == "PENDING"


def test_mark_sent(sqlite_session):
    svc = _svc(sqlite_session)
    nid = svc.queue(case_id="case_x", channel="feishu", thread_ref="t", body_ref="b")["notification_id"]
    r = svc.mark_sent(nid, "msg-1")
    assert r["state"] == "SENT"


def test_retryable_failure_then_retry(sqlite_session):
    svc = _svc(sqlite_session)
    nid = svc.queue(case_id="case_x", channel="feishu", thread_ref="t", body_ref="b")["notification_id"]
    r = svc.mark_failed(nid, error="429", retryable=True, attempt=1)
    assert r["state"] == "RETRYING"
    r = svc.schedule_retry(nid, attempt=2, next_at="2099-01-01T00:00:00+00:00")
    assert r["state"] == "QUEUED"


def test_non_retryable_failure_dead_letters(sqlite_session):
    svc = _svc(sqlite_session)
    nid = svc.queue(case_id="case_x", channel="feishu", thread_ref="t", body_ref="b")["notification_id"]
    r = svc.mark_failed(nid, error="auth_failed", retryable=False, attempt=1)
    assert r["state"] == "DEAD_LETTERED"
    assert (r["payload"] or {}).get("dead_lettered") is True


def test_dead_letter_after_retries(sqlite_session):
    svc = _svc(sqlite_session)
    nid = svc.queue(case_id="case_x", channel="feishu", thread_ref="t", body_ref="b")["notification_id"]
    svc.mark_failed(nid, error="429", retryable=True, attempt=1)
    r = svc.dead_letter(nid, attempts=5)
    assert r["state"] == "DEAD_LETTERED"


def test_get_not_found(sqlite_session):
    svc = _svc(sqlite_session)
    with pytest.raises(NotificationServiceError) as exc:
        svc.get("notif_nope")
    assert exc.value.code == "not_found"
