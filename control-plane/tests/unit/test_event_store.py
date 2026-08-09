"""事件溯源存储单元测试（懒创建聚合、revision==seq、CAS、replay）。"""
import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.models.tables import Aggregate, Base, Event
from app.services.event_store import CASConflict, EventStore


def test_first_event_lazily_creates_aggregate(sqlite_session):
    store = EventStore(sqlite_session)
    ev = store.append_event(
        aggregate_type="case",
        aggregate_id="case_x",
        event_type="complaint.received",
        payload={"source": "webhook"},
        machine="case",
    )
    agg = store.get_aggregate("case", "case_x")
    assert agg is not None
    assert agg.state == "RECEIVED"
    assert agg.revision == 1
    assert ev.seq == 1
    assert ev.event_id.startswith("evt_")


def test_revision_equals_seq(sqlite_session):
    store = EventStore(sqlite_session)
    store.append_event(
        aggregate_type="case",
        aggregate_id="case_x",
        event_type="complaint.received",
        payload={"source": "webhook"},
        machine="case",
    )
    store.append_event(
        aggregate_type="case",
        aggregate_id="case_x",
        event_type="case.opened",
        payload={"title": "t"},
        machine="case",
        expected_revision=1,
    )
    agg = store.get_aggregate("case", "case_x")
    assert agg.revision == 2
    events = store.list_events("case_x")
    assert [e.seq for e in events] == [1, 2]
    assert agg.state == "OPEN"


def test_cas_conflict(sqlite_session):
    store = EventStore(sqlite_session)
    store.append_event(
        aggregate_type="case",
        aggregate_id="case_x",
        event_type="complaint.received",
        payload={},
        machine="case",
    )
    with pytest.raises(CASConflict):
        store.append_event(
            aggregate_type="case",
            aggregate_id="case_x",
            event_type="case.opened",
            payload={},
            machine="case",
            expected_revision=5,  # 当前实际是 1
        )


def test_append_refreshes_stale_identity_map_before_cas(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'stale-cas.sqlite'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with factory() as creator, creator.begin():
        EventStore(creator).append_event(
            aggregate_type="case",
            aggregate_id="case_stale",
            event_type="complaint.received",
            payload={},
            machine="case",
        )

    stale = factory()
    try:
        cached = EventStore(stale).get_aggregate("case", "case_stale")
        assert cached is not None and cached.revision == 1
        with factory() as concurrent, concurrent.begin():
            EventStore(concurrent).append_event(
                aggregate_type="case",
                aggregate_id="case_stale",
                event_type="case.opened",
                payload={},
                machine="case",
                expected_revision=1,
            )

        with pytest.raises(CASConflict):
            EventStore(stale).append_event(
                aggregate_type="case",
                aggregate_id="case_stale",
                event_type="case.dispatched",
                payload={},
                machine="case",
                expected_revision=1,
            )
        assert cached.revision == 2
        stale.rollback()
    finally:
        stale.close()
        engine.dispose()


def test_first_event_requires_no_expected_revision(sqlite_session):
    store = EventStore(sqlite_session)
    with pytest.raises(CASConflict):
        store.append_event(
            aggregate_type="release",
            aggregate_id="rel_x",
            event_type="release.requested",
            payload={},
            machine="release",
            expected_revision=3,  # 首事件前聚合不存在，expected 必须 None/0
        )


def test_replay_reconstructs_state(sqlite_session):
    store = EventStore(sqlite_session)
    store.append_event(
        aggregate_type="case",
        aggregate_id="case_x",
        event_type="complaint.received",
        payload={"source": "webhook"},
        machine="case",
    )
    store.append_event(
        aggregate_type="case",
        aggregate_id="case_x",
        event_type="case.opened",
        payload={"title": "t"},
        machine="case",
        expected_revision=1,
    )
    store.append_event(
        aggregate_type="case",
        aggregate_id="case_x",
        event_type="case.dispatched",
        payload={"worker_id": "w1"},
        machine="case",
        expected_revision=2,
    )
    state, payload, n = store.replay("case", "case_x", "case")
    assert state == "DISPATCHED"
    assert payload["worker_id"] == "w1"
    assert n == 3


def test_outbox_written_in_same_tx(sqlite_session):
    store = EventStore(sqlite_session)
    store.append_event(
        aggregate_type="case",
        aggregate_id="case_x",
        event_type="case.opened",
        payload={},
        machine="case",
        outbox={"channel": "case.opened", "payload": {"case_id": "case_x"}},
    )
    from app.models.tables import Outbox

    ob = sqlite_session.scalar(select(Outbox).where(Outbox.aggregate_id == "case_x"))
    assert ob is not None
    assert ob.status == "PENDING"
