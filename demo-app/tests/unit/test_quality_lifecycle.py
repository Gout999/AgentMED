"""Quality lifecycle idempotency and asynchronous CAS regression tests."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import IdempotencyRecord, Operation, TransitionRecord, VersionSet
from app.operations import execute_operation
from app.routers import quality as quality_router
from app.routers.quality import _handle_lifecycle


@pytest.fixture()
def db(monkeypatch):
    # SQLite cannot autoincrement this PostgreSQL BIGINT PK. Transition history
    # itself is covered by PostgreSQL integration/conformance tests; these unit
    # tests isolate idempotency and the second CAS check.
    monkeypatch.setattr("app.versionset_service._record_transition", lambda *_args: None)
    engine = create_engine("sqlite:///:memory:")
    for table in (
        VersionSet.__table__,
        Operation.__table__,
        IdempotencyRecord.__table__,
        TransitionRecord.__table__,
    ):
        table.create(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _seed(db, versionset_id: str) -> VersionSet:
    row = VersionSet(
        versionset_id=versionset_id,
        revision=1,
        status="draft",
        content={"digest": "sha256:" + "a" * 64},
        digest="sha256:" + "a" * 64,
    )
    db.add(row)
    db.commit()
    return row


def _accept(db, versionset_id: str, key: str):
    return _handle_lifecycle(
        "stage",
        versionset_id,
        {"expected_revision": 1},
        key,
        None,
        db,
        BackgroundTasks(),
    )


def _body(response) -> dict:
    return json.loads(response.body)


def test_terminal_lifecycle_replay_returns_original_operation_before_cas(db):
    _seed(db, "vs_replay")
    first = _accept(db, "vs_replay", "idem-replay")
    operation_id = _body(first)["operation_id"]
    execute_operation(db, operation_id)
    assert db.get(VersionSet, "vs_replay").revision == 2

    replay = _accept(db, "vs_replay", "idem-replay")

    assert _body(replay)["operation_id"] == operation_id
    assert _body(replay)["status"] == "succeeded"
    assert db.get(VersionSet, "vs_replay").revision == 2


def test_pending_operation_blocks_second_key_with_same_revision(db):
    _seed(db, "vs_pending")
    _accept(db, "vs_pending", "idem-first")

    with pytest.raises(HTTPException) as exc:
        _accept(db, "vs_pending", "idem-second")

    assert exc.value.status_code == 409
    assert exc.value.detail["error"]["code"] == "revision_conflict"


def test_idempotency_key_is_bound_to_versionset_target(db):
    _seed(db, "vs_target_a")
    _seed(db, "vs_target_b")
    _accept(db, "vs_target_a", "idem-cross-target")

    with pytest.raises(HTTPException) as exc:
        _accept(db, "vs_target_b", "idem-cross-target")

    assert exc.value.status_code == 422
    assert exc.value.detail["error"]["details"]["subcode"] == "idempotency_key_conflict"


def test_executor_rechecks_accepted_revision_before_mutation(db):
    versionset = _seed(db, "vs_executor_cas")
    accepted = _accept(db, versionset.versionset_id, "idem-executor-cas")
    operation_id = _body(accepted)["operation_id"]
    versionset.revision = 2
    db.commit()

    execute_operation(db, operation_id)

    operation = db.get(Operation, operation_id)
    assert operation.status == "failed"
    assert operation.error["code"] == "revision_conflict"
    assert db.get(VersionSet, versionset.versionset_id).status == "draft"
    assert db.get(VersionSet, versionset.versionset_id).revision == 2


def test_pending_exact_replay_reschedules_same_operation_after_crash(db, monkeypatch):
    _seed(db, "vs_recover_pending")
    monkeypatch.setattr(quality_router, "_schedule_operation", lambda *_args: None)
    first = _accept(db, "vs_recover_pending", "idem-recover-pending")
    operation_id = _body(first)["operation_id"]
    assert db.get(Operation, operation_id).status == "pending"

    scheduled: list[str] = []

    def execute_now(operation, _background):
        scheduled.append(operation.operation_id)
        execute_operation(db, operation.operation_id)

    monkeypatch.setattr(quality_router, "_schedule_operation", execute_now)
    replay = _accept(db, "vs_recover_pending", "idem-recover-pending")

    assert scheduled == [operation_id]
    assert _body(replay)["operation_id"] == operation_id
    assert db.get(Operation, operation_id).status == "succeeded"
    assert db.get(VersionSet, "vs_recover_pending").revision == 2


def test_expired_pending_operation_fails_and_no_longer_blocks_target(db, monkeypatch):
    _seed(db, "vs_expired_pending")
    monkeypatch.setattr(quality_router, "_schedule_operation", lambda *_args: None)
    first = _accept(db, "vs_expired_pending", "idem-expired-pending")
    operation = db.get(Operation, _body(first)["operation_id"])
    operation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        _accept(db, "vs_expired_pending", "idem-expired-pending")
    assert exc.value.status_code == 410
    assert operation.status == "failed"

    replacement = _accept(db, "vs_expired_pending", "idem-after-expired")
    assert _body(replacement)["operation_id"] != operation.operation_id


def test_promote_executor_requires_the_approved_active_baseline_digest(db):
    baseline = VersionSet(
        versionset_id="vs_active_bound",
        revision=1,
        status="active",
        content={"digest": "sha256:" + "a" * 64},
        digest="sha256:" + "a" * 64,
    )
    candidate = VersionSet(
        versionset_id="vs_promote_bound",
        revision=3,
        status="canary",
        content={"digest": "sha256:" + "b" * 64},
        digest="sha256:" + "b" * 64,
    )
    now = datetime.now(timezone.utc)
    operation = Operation(
        operation_id="op_promote_bound",
        kind="promote",
        status="pending",
        idempotency_key="idem-promote-bound",
        versionset_id=candidate.versionset_id,
        request={
            "_expected_revision": 3,
            "_expected_status": "canary",
            "expected_active_digest": baseline.digest,
        },
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    db.add_all([baseline, candidate, operation])
    db.commit()

    execute_operation(db, operation.operation_id)

    assert operation.status == "succeeded"
    assert candidate.status == "active"
    assert baseline.status == "superseded"


def test_promote_executor_fails_closed_when_active_baseline_drifted(db):
    baseline = VersionSet(
        versionset_id="vs_active_drifted",
        revision=1,
        status="active",
        content={"digest": "sha256:" + "c" * 64},
        digest="sha256:" + "c" * 64,
    )
    candidate = VersionSet(
        versionset_id="vs_promote_drifted",
        revision=3,
        status="canary",
        content={"digest": "sha256:" + "b" * 64},
        digest="sha256:" + "b" * 64,
    )
    now = datetime.now(timezone.utc)
    operation = Operation(
        operation_id="op_promote_drifted",
        kind="promote",
        status="pending",
        idempotency_key="idem-promote-drifted",
        versionset_id=candidate.versionset_id,
        request={
            "_expected_revision": 3,
            "_expected_status": "canary",
            "expected_active_digest": "sha256:" + "a" * 64,
        },
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(hours=1),
    )
    db.add_all([baseline, candidate, operation])
    db.commit()

    execute_operation(db, operation.operation_id)

    assert operation.status == "failed"
    assert operation.error["code"] == "revision_conflict"
    assert candidate.status == "canary"
    assert candidate.revision == 3
    assert baseline.status == "active"
