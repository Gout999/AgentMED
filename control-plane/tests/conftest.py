"""pytest 共享夹具。

- sqlite_session：unit 用（内存 SQLite，无外部依赖）
- pg_session / pg_engine：integration 用（compose 起 PG 后可用；不可达则 skip）
- app_client：FastAPI TestClient（SQLite 内存）
"""
from __future__ import annotations

import os
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.main import create_app
from app.models.tables import Base
from app.quality.client import FakeQualityClient
from app.utils.jcs import workorder_hash

TEST_DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane",
)


def make_workorder(
    *,
    workorder_id: str,
    nonce: str,
    case_id: str,
    channel: str = "prompt",
    digest_seed: str = "a",
) -> dict[str, Any]:
    """构造合法 WorkOrder（ASCII-only，JCS 可哈希），hash 由规则重算。"""
    expiry = "2099-01-01T00:00:00+00:00"
    wo: dict[str, Any] = {
        "schema_version": "0.1.0",
        "workorder_id": workorder_id,
        "case_id": case_id,
        "channel": channel,
        "base_versionset_digest": f"sha256:{digest_seed * 64}",
        "target_versionset_digest": f"sha256:{'b' * 64}",
        "input_versions": {
            "prompt_digest": f"sha256:{'c' * 64}",
            "kb_manifest_digest": f"sha256:{'d' * 64}",
            "model_digest": f"sha256:{'e' * 64}",
        },
        "diff": {"format": "unified_diff", "content": "fix output format", "digest": f"sha256:{'f' * 64}"},
        "gate_report_ref": {"uri": "http://gate/1", "digest": f"sha256:{'g' * 64}"},
        "expiry": expiry,
        "nonce": nonce,
        "created_at": "2026-08-08T00:00:00+00:00",
        "created_by": "repairer-1",
        "hash_rule": "jcs-rfc8785+sha256",
    }
    wo["hash"] = workorder_hash(wo)
    return wo


def make_approval(wo: dict[str, Any], approval_id: str) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "workorder_hash": wo["hash"],
        "workorder_id": wo["workorder_id"],
        "nonce": wo["nonce"],
        "expiry": wo["expiry"],
        "approver": {"type": "human", "identity": "human-1"},
        "decision": "approved",
        "decided_at": "2026-08-08T00:00:00+00:00",
    }

# ------------------------------------------------------------------ sqlite（unit）


@pytest.fixture()
def sqlite_engine():
    eng = sa.create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def sqlite_session(sqlite_engine):
    S = sessionmaker(bind=sqlite_engine, autoflush=False, autocommit=False)
    s = S()
    yield s
    s.close()


@pytest.fixture()
def test_settings() -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
    )


# ------------------------------------------------------------------ pg（integration）


def _pg_available() -> bool:
    try:
        eng = sa.create_engine(TEST_DATABASE_URL, connect_args={"connect_timeout": 2})
        conn = eng.connect()
        conn.close()
        eng.dispose()
        return True
    except Exception:  # noqa: BLE001
        return False


def _new_pg_engine():
    return sa.create_engine(TEST_DATABASE_URL, poolclass=sa.pool.NullPool)


@pytest.fixture()
def pg_engine():
    if not _pg_available():
        pytest.skip("Postgres 不可达：先 docker compose -f deploy/compose.yaml up -d postgres")
    eng = _new_pg_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def pg_session(pg_engine):
    S = sessionmaker(bind=pg_engine, autoflush=False, autocommit=False)
    s = S()
    yield s
    s.close()


@pytest.fixture()
def pg_settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
    )


@pytest.fixture()
def pg_client(pg_engine) -> tuple[TestClient, FakeQualityClient]:
    """PG 版 FastAPI client（integration 场景 1–4 的 HTTP 层）。"""
    quality = FakeQualityClient()
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
    )
    app = create_app(settings=settings, quality_client=quality, engine=pg_engine, create_tables=True)
    with TestClient(app) as client:
        yield client, quality


# ------------------------------------------------------------------ FastAPI client


@pytest.fixture()
def app_client(sqlite_engine, test_settings) -> tuple[TestClient, FakeQualityClient]:
    quality = FakeQualityClient()
    app = create_app(
        settings=test_settings,
        quality_client=quality,
        engine=sqlite_engine,
        create_tables=True,
    )
    with TestClient(app) as client:
        yield client, quality


def build_pg_app(*, audit_force_fail: bool = False, quality: FakeQualityClient | None = None) -> TestClient:
    """构建绑定 PG 的 FastAPI app（integration HTTP 测试用）。"""
    if not _pg_available():
        pytest.skip("Postgres 不可达：先 docker compose -f deploy/compose.yaml up -d postgres")
    eng = _new_pg_engine()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        operation_poll_timeout_seconds=0.05,
        reconcile_backoff_initial_seconds=0,
        reconcile_backoff_max_seconds=0,
        audit_force_fail=audit_force_fail,
    )
    q = quality or FakeQualityClient()
    app = create_app(settings=settings, quality_client=q, engine=eng, create_tables=True)
    client = TestClient(app)
    client.__enter__()  # type: ignore[attr-defined]
    return client
