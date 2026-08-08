"""PostgreSQL proof for lifecycle acceptance serialization and executor CAS."""
from __future__ import annotations

import os
import threading
import uuid
from datetime import timedelta

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from app.models import IdempotencyRecord, Operation, TransitionRecord, VersionSet
from app.operations import execute_operation
from app.routers import quality as quality_router
from app.routers.quality import _handle_lifecycle
from app import versionset_service
from app.versionset_service import now_utc

pytestmark = pytest.mark.integration


def test_postgres_concurrent_same_revision_accepts_only_one_operation(monkeypatch):
    database_url = os.environ.get("DEMO_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("DEMO_TEST_DATABASE_URL is required for PostgreSQL concurrency proof")

    base_engine = create_engine(database_url, pool_pre_ping=True)
    schema = f"demo_lifecycle_{uuid.uuid4().hex[:12]}"
    with base_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base_engine.execution_options(schema_translate_map={None: schema})
    tables = (
        VersionSet.__table__,
        Operation.__table__,
        IdempotencyRecord.__table__,
        TransitionRecord.__table__,
    )
    try:
        for table in tables:
            table.create(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        seed = factory()
        try:
            seed.add(
                VersionSet(
                    versionset_id="vs_pg_concurrent",
                    revision=1,
                    status="draft",
                    content={"digest": "sha256:" + "a" * 64},
                    digest="sha256:" + "a" * 64,
                )
            )
            seed.commit()
        finally:
            seed.close()

        # Simulate the durable acceptance window without executing either
        # background task until both callers have returned.
        monkeypatch.setattr(quality_router, "_schedule_operation", lambda *_args: None)
        barrier = threading.Barrier(2)
        outcomes: list[int] = []
        outcome_lock = threading.Lock()

        def accept(key: str) -> None:
            session = factory()
            try:
                barrier.wait(timeout=5)
                _handle_lifecycle(
                    "stage",
                    "vs_pg_concurrent",
                    {"expected_revision": 1},
                    key,
                    None,
                    session,
                    BackgroundTasks(),
                )
                status = 202
            except HTTPException as exc:
                status = exc.status_code
                session.rollback()
            finally:
                session.close()
            with outcome_lock:
                outcomes.append(status)

        threads = [
            threading.Thread(target=accept, args=("pg-concurrent-a",)),
            threading.Thread(target=accept, args=("pg-concurrent-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert sorted(outcomes) == [202, 409]
        verify = factory()
        try:
            assert verify.scalar(select(func.count()).select_from(Operation)) == 1
            operation = verify.scalar(select(Operation))
            assert operation is not None and operation.status == "pending"
            versionset = verify.get(VersionSet, "vs_pg_concurrent")
            assert versionset.revision == 1 and versionset.status == "draft"
            execute_operation(verify, operation.operation_id)
            versionset = verify.get(VersionSet, "vs_pg_concurrent")
            assert versionset.revision == 2 and versionset.status == "staged"
        finally:
            verify.close()
    finally:
        with base_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base_engine.dispose()


def test_postgres_concurrent_promotes_across_candidates_preserve_single_active(
    monkeypatch,
):
    database_url = os.environ.get("DEMO_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("DEMO_TEST_DATABASE_URL is required for PostgreSQL concurrency proof")

    base_engine = create_engine(database_url, pool_pre_ping=True)
    schema = f"demo_promote_{uuid.uuid4().hex[:12]}"
    with base_engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    engine = base_engine.execution_options(schema_translate_map={None: schema})
    tables = (
        VersionSet.__table__,
        Operation.__table__,
        IdempotencyRecord.__table__,
        TransitionRecord.__table__,
    )
    try:
        for table in tables:
            table.create(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        now = now_utc()
        seed = factory()
        try:
            seed.add_all(
                [
                    VersionSet(
                        versionset_id="vs_active_baseline",
                        revision=1,
                        status="active",
                        content={"digest": "sha256:" + "a" * 64},
                        digest="sha256:" + "a" * 64,
                    ),
                    VersionSet(
                        versionset_id="vs_candidate_a",
                        revision=3,
                        status="canary",
                        content={"digest": "sha256:" + "b" * 64},
                        digest="sha256:" + "b" * 64,
                    ),
                    VersionSet(
                        versionset_id="vs_candidate_b",
                        revision=3,
                        status="canary",
                        content={"digest": "sha256:" + "c" * 64},
                        digest="sha256:" + "c" * 64,
                    ),
                    Operation(
                        operation_id="op_promote_candidate_a",
                        kind="promote",
                        status="pending",
                        idempotency_key="promote-candidate-a",
                        versionset_id="vs_candidate_a",
                        request={
                            "_expected_revision": 3,
                            "_expected_status": "canary",
                            "expected_active_digest": "sha256:" + "a" * 64,
                        },
                        created_at=now,
                        updated_at=now,
                        expires_at=now + timedelta(hours=1),
                    ),
                    Operation(
                        operation_id="op_promote_candidate_b",
                        kind="promote",
                        status="pending",
                        idempotency_key="promote-candidate-b",
                        versionset_id="vs_candidate_b",
                        request={
                            "_expected_revision": 3,
                            "_expected_status": "canary",
                            "expected_active_digest": "sha256:" + "a" * 64,
                        },
                        created_at=now,
                        updated_at=now,
                        expires_at=now + timedelta(hours=1),
                    ),
                ]
            )
            seed.commit()
        finally:
            seed.close()

        # Hold the first executor after it has acquired the global active-set
        # lock. The second executor must not reach get_active_versionset until
        # the first transaction commits.
        original_get_active = versionset_service.get_active_versionset
        first_entered = threading.Event()
        release_first = threading.Event()
        second_entered = threading.Event()
        call_lock = threading.Lock()
        calls = 0

        def observed_get_active(session):
            nonlocal calls
            with call_lock:
                call_index = calls
                calls += 1
            if call_index == 0:
                first_entered.set()
                assert release_first.wait(timeout=5)
            else:
                second_entered.set()
            return original_get_active(session)

        monkeypatch.setattr(versionset_service, "get_active_versionset", observed_get_active)
        barrier = threading.Barrier(2)

        def execute(operation_id: str) -> None:
            session = factory()
            try:
                barrier.wait(timeout=5)
                execute_operation(session, operation_id)
            finally:
                session.close()

        threads = [
            threading.Thread(target=execute, args=("op_promote_candidate_a",)),
            threading.Thread(target=execute, args=("op_promote_candidate_b",)),
        ]
        for thread in threads:
            thread.start()
        assert first_entered.wait(timeout=5)
        assert not second_entered.wait(timeout=0.25)
        release_first.set()
        for thread in threads:
            thread.join(timeout=10)
            assert not thread.is_alive()
        assert second_entered.is_set()

        verify = factory()
        try:
            active = verify.scalars(
                select(VersionSet).where(VersionSet.status == "active")
            ).all()
            assert len(active) == 1
            assert active[0].versionset_id in {"vs_candidate_a", "vs_candidate_b"}
            operations = verify.scalars(select(Operation).order_by(Operation.operation_id)).all()
            assert sorted(operation.status for operation in operations) == ["failed", "succeeded"]
            failed = next(operation for operation in operations if operation.status == "failed")
            assert failed.error["code"] == "revision_conflict"
            failed_candidate = verify.get(VersionSet, failed.versionset_id)
            assert failed_candidate.status == "canary"
            assert failed_candidate.revision == 3
        finally:
            verify.close()
    finally:
        with base_engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        base_engine.dispose()
