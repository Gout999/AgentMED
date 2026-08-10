"""Regression tests for physical isolation of the legacy v3 outbox worker."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models.tables import Outbox
from app.services.outbox_relay import OutboxDispatcher
from app.utils.jcs import canonical_json_digest


def _factory(engine):
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        audit_jsonl_path=str(tmp_path / "audit.jsonl"),
        outbox_retry_initial_seconds=0,
        outbox_retry_max_seconds=0,
        outbox_claim_ttl_seconds=5,
    )


def _row(
    *,
    outbox_id: str,
    aggregate_id: str,
    source_event_seq: int,
    channel: str,
    status: str = "PENDING",
    contract_version: str | None = None,
) -> Outbox:
    source_event_id = f"evt_{outbox_id}"
    payload = {
        "aggregate_id": aggregate_id,
        "aggregate_seq": source_event_seq,
        "source_event_id": source_event_id,
    }
    values = {
        "outbox_id": outbox_id,
        "aggregate_id": aggregate_id,
        "source_event_id": source_event_id,
        "source_event_seq": source_event_seq,
        "channel": channel,
        "event_type": "signal.received" if contract_version == "v4" else "CASE_CREATED",
        "payload": payload,
        "payload_digest": canonical_json_digest(payload),
        "status": status,
        "attempts": 2 if status == "DEAD" else 0,
        "contract_version": contract_version,
    }
    if contract_version == "v4":
        values.update(
            workspace_id="ws_outbox_isolation",
            aggregate_type="signal",
            event_version="1.0",
            transaction_id=f"txn_{outbox_id}",
            actor_principal="principal:outbox-isolation",
        )
    return Outbox(**values)


def test_v3_dispatcher_never_claims_v4_or_nonlegacy_channel_rows(
    sqlite_engine, tmp_path
) -> None:
    factory = _factory(sqlite_engine)
    rows = [
        _row(
            outbox_id="obx_v4_pending_only",
            aggregate_id="sig_v4_only",
            source_event_seq=1,
            channel="v4.domain.events",
            contract_version="v4",
        ),
        _row(
            outbox_id="obx_v4_dead_only",
            aggregate_id="sig_v4_dead_only",
            source_event_seq=1,
            channel="v4.domain.events",
            status="DEAD",
            contract_version="v4",
        ),
        _row(
            outbox_id="obx_null_nonlegacy",
            aggregate_id="legacy_nonlegacy_channel",
            source_event_seq=1,
            channel="v4.domain.events",
        ),
    ]
    with factory() as session, session.begin():
        session.add_all(rows)

    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path),
        worker_id="test:v3-isolation:no-claim",
    )

    assert dispatcher.dispatch_batch(limit=50) == {
        "claimed": 0,
        "sent": 0,
        "retried": 0,
        "dead": 0,
        "blocked": 0,
    }

    with factory() as session:
        persisted = list(session.scalars(select(Outbox).order_by(Outbox.outbox_id)))
        assert [(row.outbox_id, row.status, row.attempts) for row in persisted] == [
            ("obx_null_nonlegacy", "PENDING", 0),
            ("obx_v4_dead_only", "DEAD", 2),
            ("obx_v4_pending_only", "PENDING", 0),
        ]
        assert all(row.claim_token is None for row in persisted)
        assert all(row.claimed_by is None for row in persisted)


def test_v4_pending_and_dead_predecessors_do_not_block_later_v3_delivery(
    sqlite_engine, tmp_path
) -> None:
    factory = _factory(sqlite_engine)
    aggregate_id = "shared_identifier_across_contracts"
    with factory() as session, session.begin():
        session.add_all(
            [
                _row(
                    outbox_id="obx_v4_pending_predecessor",
                    aggregate_id=aggregate_id,
                    source_event_seq=1,
                    channel="v4.domain.events",
                    contract_version="v4",
                ),
                _row(
                    outbox_id="obx_v4_dead_predecessor",
                    aggregate_id=aggregate_id,
                    source_event_seq=2,
                    channel="v4.domain.events",
                    status="DEAD",
                    contract_version="v4",
                ),
                _row(
                    outbox_id="obx_v3_candidate",
                    aggregate_id=aggregate_id,
                    source_event_seq=3,
                    channel="domain.events",
                ),
            ]
        )

    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path),
        worker_id="test:v3-isolation:predecessor",
    )
    snapshot = dispatcher._claim_one()

    assert snapshot is not None
    assert snapshot.outbox_id == "obx_v3_candidate"
    with factory() as session:
        v4_pending = session.get(Outbox, "obx_v4_pending_predecessor")
        v4_dead = session.get(Outbox, "obx_v4_dead_predecessor")
        v3 = session.get(Outbox, "obx_v3_candidate")
        assert (v4_pending.status, v4_pending.attempts, v4_pending.claim_token) == (
            "PENDING",
            0,
            None,
        )
        assert (v4_dead.status, v4_dead.attempts, v4_dead.claim_token) == (
            "DEAD",
            2,
            None,
        )
        assert v3.status == "PROCESSING"
        assert v3.attempts == 1
        assert v3.claim_token == snapshot.claim_token


def test_nonlegacy_null_contract_predecessor_does_not_poison_v3_ordering(
    sqlite_engine, tmp_path
) -> None:
    factory = _factory(sqlite_engine)
    aggregate_id = "legacy_aggregate_with_foreign_channel"
    with factory() as session, session.begin():
        session.add_all(
            [
                _row(
                    outbox_id="obx_foreign_channel_predecessor",
                    aggregate_id=aggregate_id,
                    source_event_seq=1,
                    channel="v4.domain.events",
                    status="DEAD",
                ),
                _row(
                    outbox_id="obx_legacy_after_foreign",
                    aggregate_id=aggregate_id,
                    source_event_seq=2,
                    channel="domain.events",
                ),
            ]
        )

    dispatcher = OutboxDispatcher(
        factory,
        _settings(tmp_path),
        worker_id="test:v3-isolation:foreign-channel",
    )
    snapshot = dispatcher._claim_one()

    assert snapshot is not None
    assert snapshot.outbox_id == "obx_legacy_after_foreign"
    with factory() as session:
        predecessor = session.get(Outbox, "obx_foreign_channel_predecessor")
        assert (predecessor.status, predecessor.attempts, predecessor.claim_token) == (
            "DEAD",
            2,
            None,
        )
