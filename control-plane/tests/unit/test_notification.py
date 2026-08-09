"""Notification 状态机服务单元测试。"""
import pytest

from app.config import Settings
from app.models.tables import Aggregate, ReleaseClosure
from app.services.case_closure_service import CaseClosureService, CaseClosureServiceError
from app.services.event_store import EventStore
from app.services.notification_service import NotificationService, NotificationServiceError

BODY_DIGEST = "sha256:" + "a" * 64

def _svc(session) -> NotificationService:
    return NotificationService(session, Settings())


def _seed_notifying_case(session, case_id: str = "case_x") -> tuple[str, str]:
    release_id = f"rel_{case_id}"
    session.add(
        Aggregate(
            aggregate_type="case",
            aggregate_id=case_id,
            state="RELEASING",
            payload={
                "original_channel": "feishu-mock",
                "original_thread_ref": "t",
            },
            revision=1,
        )
    )
    session.flush()
    event = EventStore(session).append_event(
        aggregate_type="case",
        aggregate_id=case_id,
        event_type="case.resolved",
        payload={"release_id": release_id, "resolution": "fixed"},
        causation_id="evt_release_promoted",
        correlation_id=case_id,
        expected_revision=1,
        machine="case",
        merge_payload={"resolved_release_id": release_id, "resolution": "fixed"},
    )
    return release_id, event.event_id


def _queue(svc, session, *, channel="feishu-mock", thread_ref="t", body_ref="b"):
    release_id, event_id = _seed_notifying_case(session)
    return svc.queue(
        case_id="case_x",
        release_id=release_id,
        causation_id=event_id,
        channel=channel,
        thread_ref=thread_ref,
        body_ref=body_ref,
        body_digest=BODY_DIGEST,
    )


def test_queue_creates_queued_with_outbox(sqlite_session):
    svc = _svc(sqlite_session)
    r = _queue(svc, sqlite_session, thread_ref="t", body_ref="inline:1")
    assert r["state"] == "QUEUED"
    assert r["notification_id"].startswith("notif_")
    assert r["outbox_id"].startswith("obx_")
    from app.models.tables import Outbox

    ob = sqlite_session.query(Outbox).filter_by(aggregate_id=r["notification_id"]).first()
    assert ob is not None
    assert ob.status == "PENDING"


def test_closure_coordinator_rejects_missing_or_swapped_immutable_binding(sqlite_session):
    coordinator = CaseClosureService(sqlite_session, Settings())
    request = {
        "release_id": "rel_closure_guard_001",
        "channel": "feishu:oc_original",
        "thread_ref": "feishu:oc_original:om_original",
        "body_ref": "data:text/plain;base64,eA==",
        "body_digest": BODY_DIGEST,
    }

    with pytest.raises(CaseClosureServiceError) as exc:
        coordinator.resolve_and_queue(**request)
    assert exc.value.code == "closure_missing"

    sqlite_session.add(
        ReleaseClosure(
            release_id=request["release_id"],
            case_id="case_closure_guard_001",
            channel=request["channel"],
            thread_ref=request["thread_ref"],
            body_ref=request["body_ref"],
            body_digest=request["body_digest"],
            status="configured",
        )
    )
    sqlite_session.flush()
    with pytest.raises(CaseClosureServiceError) as exc:
        coordinator.resolve_and_queue(**{**request, "body_ref": "data:text/plain;base64,eQ=="})
    assert exc.value.code == "hash_mismatch"


def test_queue_rejects_channel_other_than_original_complaint(sqlite_session):
    svc = _svc(sqlite_session)
    with pytest.raises(NotificationServiceError) as exc:
        _queue(svc, sqlite_session, channel="feishu-mock:other")
    assert exc.value.code == "hash_mismatch"


def test_queue_rejects_thread_other_than_original_complaint(sqlite_session):
    svc = _svc(sqlite_session)
    with pytest.raises(NotificationServiceError) as exc:
        _queue(svc, sqlite_session, thread_ref="wrong-thread")
    assert exc.value.code == "hash_mismatch"


def test_sent_notification_retry_returns_same_notification(sqlite_session):
    svc = _svc(sqlite_session)
    release_id, event_id = _seed_notifying_case(sqlite_session)
    notification_id = "notif_retry_after_sent_0001"
    first = svc.queue(
        case_id="case_x",
        release_id=release_id,
        causation_id=event_id,
        channel="feishu-mock",
        thread_ref="t",
        body_ref="b",
        body_digest=BODY_DIGEST,
        notification_id=notification_id,
    )
    case = svc.store.get_aggregate("case", "case_x")
    notification = svc.store.get_aggregate("notification", notification_id)
    assert case is not None and notification is not None
    case.state = "CLOSED"
    notification.state = "SENT"
    retried = svc.queue(
        case_id="case_x",
        release_id=release_id,
        causation_id=event_id,
        channel="feishu-mock",
        thread_ref="t",
        body_ref="b",
        body_digest=BODY_DIGEST,
        notification_id=notification_id,
    )
    assert retried["notification_id"] == first["notification_id"]
    assert retried["outbox_id"] == first["outbox_id"]
    assert retried["state"] == "SENT"
    assert retried["duplicate"] is True


def test_direct_mark_sent_requires_outbox_bound_receipt(sqlite_session):
    svc = _svc(sqlite_session)
    nid = _queue(svc, sqlite_session, channel="feishu-mock")["notification_id"]
    with pytest.raises(NotificationServiceError) as exc:
        svc.mark_sent(nid, "msg-1")
    assert exc.value.code == "receipt_required"


def test_retryable_failure_then_retry(sqlite_session):
    svc = _svc(sqlite_session)
    nid = _queue(svc, sqlite_session)["notification_id"]
    r = svc.mark_failed(nid, error="429", retryable=True, attempt=1)
    assert r["state"] == "RETRYING"
    r = svc.schedule_retry(nid, attempt=2, next_at="2099-01-01T00:00:00+00:00")
    assert r["state"] == "QUEUED"


def test_non_retryable_failure_dead_letters(sqlite_session):
    svc = _svc(sqlite_session)
    nid = _queue(svc, sqlite_session)["notification_id"]
    r = svc.mark_failed(nid, error="auth_failed", retryable=False, attempt=1)
    assert r["state"] == "DEAD_LETTERED"
    assert (r["payload"] or {}).get("dead_lettered") is True


def test_dead_letter_after_retries(sqlite_session):
    svc = _svc(sqlite_session)
    nid = _queue(svc, sqlite_session)["notification_id"]
    svc.mark_failed(nid, error="429", retryable=True, attempt=1)
    r = svc.dead_letter(nid, attempts=5)
    assert r["state"] == "DEAD_LETTERED"


def test_get_not_found(sqlite_session):
    svc = _svc(sqlite_session)
    with pytest.raises(NotificationServiceError) as exc:
        svc.get("notif_nope")
    assert exc.value.code == "not_found"
