"""Quality lifecycle idempotency and asynchronous CAS regression tests."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import faults
from app.live_config import VersionSetConfigError, canary_bucket, select_routed_versionset
from app.models import FaultState, IdempotencyRecord, Operation, TransitionRecord, VersionSet
from app.operations import execute_operation
from app.routers import admin as admin_router
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
        FaultState.__table__,
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


def _routing_key_for_bucket(*, below: int | None = None, at_least: int | None = None) -> str:
    for index in range(10_000):
        value = f"routing-key-{index}"
        bucket = canary_bucket(value)
        if below is not None and bucket < below:
            return value
        if at_least is not None and bucket >= at_least:
            return value
    raise AssertionError("unable to find deterministic routing bucket")


def test_canary_percent_routes_real_requests_deterministically(db):
    active = VersionSet(
        versionset_id="vs_active_routing",
        revision=2,
        status="active",
        content={"digest": "sha256:" + "a" * 64},
        digest="sha256:" + "a" * 64,
    )
    candidate = VersionSet(
        versionset_id="vs_canary_routing",
        revision=3,
        status="canary",
        canary_percent=10,
        content={"digest": "sha256:" + "b" * 64},
        digest="sha256:" + "b" * 64,
    )
    db.add_all([active, candidate])
    db.commit()

    canary_key = _routing_key_for_bucket(below=10)
    baseline_key = _routing_key_for_bucket(at_least=10)

    assert select_routed_versionset(db, canary_key).versionset_id == candidate.versionset_id
    assert select_routed_versionset(db, canary_key).versionset_id == candidate.versionset_id
    assert select_routed_versionset(db, baseline_key).versionset_id == active.versionset_id
    assert select_routed_versionset(db, None).versionset_id == active.versionset_id


def test_ambiguous_canary_state_fails_closed(db):
    db.add_all(
        [
            VersionSet(
                versionset_id="vs_active_ambiguous",
                revision=2,
                status="active",
                content={"digest": "sha256:" + "a" * 64},
                digest="sha256:" + "a" * 64,
            ),
            VersionSet(
                versionset_id="vs_canary_ambiguous_a",
                revision=3,
                status="canary",
                canary_percent=10,
                content={"digest": "sha256:" + "b" * 64},
                digest="sha256:" + "b" * 64,
            ),
            VersionSet(
                versionset_id="vs_canary_ambiguous_b",
                revision=3,
                status="canary",
                canary_percent=10,
                content={"digest": "sha256:" + "c" * 64},
                digest="sha256:" + "c" * 64,
            ),
        ]
    )
    db.commit()

    with pytest.raises(VersionSetConfigError, match="more than one canary"):
        select_routed_versionset(db, "stable-session")


def test_b1_injection_uses_versionset_lifecycle_and_reset(db, monkeypatch):
    monkeypatch.setattr(db, "add_all", lambda _rows: None)
    common = {
        "kb_manifest": {"manifest_digest": "sha256:" + "c" * 64},
        "model": {"digest": "sha256:" + "d" * 64},
    }
    good = VersionSet(
        versionset_id="vs_baseline0000000001",
        revision=1,
        status="active",
        content={"prompt": {"version": "v1.4.2"}, **common},
        digest="sha256:" + "a" * 64,
        canary_percent=100,
    )
    bad = VersionSet(
        versionset_id="vs_b1fault000000000001",
        revision=1,
        status="draft",
        content={"prompt": {"version": "v1.4.3"}, **common},
        digest="sha256:" + "b" * 64,
        canary_percent=0,
    )
    candidate = VersionSet(
        versionset_id="vs_b1candidate00000001",
        revision=3,
        status="canary",
        content={"prompt": {"version": "v1.4.2"}, **common},
        digest="sha256:" + "e" * 64,
        canary_percent=10,
    )
    db.add(good)
    db.add(bad)
    db.add(candidate)
    db.commit()

    receipt = admin_router.inject_fault(
        "B1", admin_router.FaultInjectionIn(), db=db, _=None
    )
    assert receipt["previous_versionset_id"] == good.versionset_id
    assert receipt["fault_versionset_id"] == bad.versionset_id
    assert good.status == "superseded" and bad.status == "active"
    duplicate_injection = admin_router.inject_fault(
        "B1", admin_router.FaultInjectionIn(), db=db, _=None
    )
    assert duplicate_injection["duplicate"] is True
    assert duplicate_injection["injected_at"] == receipt["injected_at"]

    recovered = faults.recover_b1(
        db, quarantine_versionset_id=candidate.versionset_id
    )
    assert recovered["duplicate"] is False
    assert recovered["restored_versionset_id"] == good.versionset_id
    assert good.status == "active" and bad.status == "draft"
    assert recovered["quarantined_versionset_id"] == candidate.versionset_id
    assert candidate.status == "rolled_back" and candidate.canary_percent == 0
    assert faults.recover_b1(
        db, quarantine_versionset_id=candidate.versionset_id
    )["duplicate"] is True

    faults.inject_fault(db, "B1")
    assert faults.reset_faults(db) == ["B1"]
    assert good.status == "active" and bad.status == "draft"


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
