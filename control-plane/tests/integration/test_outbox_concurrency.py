"""PostgreSQL proof that concurrent dispatchers do not double-count Trust."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models.tables import Outbox, OutboxDeliveryReceipt, TrustLedger, TrustLedgerEntry
from app.services.event_store import EventStore
from app.services.outbox_relay import OutboxDispatcher
from app.services.trust_service import TrustService
from app.services.trust_service import ACTION_TYPE, RISK_CLASS

pytestmark = pytest.mark.integration


def test_concurrent_dispatchers_claim_once_and_count_each_release_once(pg_engine, tmp_path):
    factory = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False, future=True)
    settings = Settings(
        database_url=str(pg_engine.url),
        audit_jsonl_path=str(tmp_path / "audit.jsonl"),
        outbox_retry_initial_seconds=0,
        outbox_retry_max_seconds=0,
        outbox_claim_ttl_seconds=5,
    )
    with factory() as session, session.begin():
        session.add(
            TrustLedger(
                risk_class=RISK_CLASS,
                action_type=ACTION_TYPE,
                epoch=1,
                successes=0,
                trials=0,
                autonomy_state="MANUAL",
                payload={"sample_rule": "one_action_one_sample"},
            )
        )
        store = EventStore(session)
        for index in range(8):
            store.append_event(
                aggregate_type="release",
                aggregate_id=f"release_concurrent_{index}",
                event_type="release.promoted",
                payload={"operation_id": f"quality_op_{index}"},
                new_state="COMPLETED",
            )

    class TrustOnlyConsumer:
        def consume(self, session, row):
            return TrustService(session, settings).consume_release_event(row.payload or {})

    def run(worker_id: str):
        return OutboxDispatcher(
            factory,
            settings,
            worker_id=worker_id,
            domain_consumer=TrustOnlyConsumer(),
        ).dispatch_batch(limit=20)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ("worker:a", "worker:b")))

    assert sum(item["sent"] for item in results) == 8
    assert sum(item["dead"] + item["blocked"] for item in results) == 0
    with factory() as session:
        ledger = session.get(
            TrustLedger,
            {"risk_class": RISK_CLASS, "action_type": ACTION_TYPE, "epoch": 1},
        )
        assert ledger is not None
        assert ledger.successes == 8 and ledger.trials == 8
        assert session.scalar(select(func.count()).select_from(TrustLedgerEntry)) == 8
        assert session.scalar(select(func.count()).select_from(OutboxDeliveryReceipt)) == 8
        assert session.scalar(
            select(func.count()).select_from(Outbox).where(Outbox.status == "SENT")
        ) == 8


def test_concurrent_dispatchers_preserve_unknown_before_promote(pg_engine, tmp_path):
    factory = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False, future=True)
    settings = Settings(
        database_url=str(pg_engine.url),
        audit_jsonl_path=str(tmp_path / "audit-ordering.jsonl"),
        outbox_retry_initial_seconds=0,
        outbox_retry_max_seconds=0,
        outbox_claim_ttl_seconds=5,
    )
    with factory() as session, session.begin():
        session.add(
            TrustLedger(
                risk_class=RISK_CLASS,
                action_type=ACTION_TYPE,
                epoch=1,
                successes=0,
                trials=0,
                autonomy_state="MANUAL",
                payload={"sample_rule": "one_action_one_sample"},
            )
        )
        store = EventStore(session)
        store.append_event(
            aggregate_type="release",
            aggregate_id="release_pg_ordered_unknown",
            event_type="release.unknown_detected",
            payload={"operation_id": "op_pg_unknown"},
            new_state="UNKNOWN",
        )
        store.append_event(
            aggregate_type="release",
            aggregate_id="release_pg_ordered_unknown",
            event_type="release.promoted",
            payload={"operation_id": "op_pg_promoted"},
            new_state="COMPLETED",
        )

    workers = [
        OutboxDispatcher(factory, settings, worker_id="worker:order:a"),
        OutboxDispatcher(factory, settings, worker_id="worker:order:b"),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        snapshots = list(pool.map(lambda worker: worker._claim_one(), workers))
    claimed = [snapshot for snapshot in snapshots if snapshot is not None]
    assert len(claimed) == 1
    assert claimed[0].event_type == "RELEASE_UNKNOWN"

    workers[0]._dispatch(claimed[0])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda worker: worker.dispatch_batch(limit=2), workers))
    assert sum(item["dead"] for item in results) == 1
    assert sum(item["sent"] for item in results) == 0

    with factory() as session:
        ledger = session.get(
            TrustLedger,
            {"risk_class": RISK_CLASS, "action_type": ACTION_TYPE, "epoch": 1},
        )
        assert ledger is not None
        assert ledger.autonomy_state == "BLOCKED_UNKNOWN"
        assert ledger.successes == 0 and ledger.trials == 0
        rows = list(
            session.scalars(
                select(Outbox)
                .where(Outbox.aggregate_id == "release_pg_ordered_unknown")
                .order_by(Outbox.source_event_seq)
            )
        )
        assert [row.event_type for row in rows] == ["RELEASE_UNKNOWN", "RELEASE_PROMOTED"]
        assert [row.status for row in rows] == ["SENT", "DEAD"]
