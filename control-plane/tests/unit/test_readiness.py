"""Focused liveness/readiness checks for the deployed control-plane surface."""
from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import _expected_database_heads, create_app
from app.quality.client import FakeQualityClient


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite:///:memory:",
        "control_plane_internal_token": "readiness-internal-token",
        "approval_authority_token": "readiness-approval-token",
        "gate_authority_token": "readiness-gate-token",
        "control_plane_role_tokens_json": json.dumps(
            {"repairer": "readiness-repairer-token"}
        ),
        "require_mcp_role_tokens": False,
        "public_credential_hash_pepper": SecretStr("readiness-public-pepper"),
        "public_cursor_signing_key": SecretStr("readiness-public-cursor-key"),
    }
    values.update(overrides)
    return Settings(**values)


def _stamp_current_head(engine: sa.Engine) -> None:
    heads = _expected_database_heads()
    assert heads
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        for head in sorted(heads):
            connection.execute(
                sa.text("INSERT INTO alembic_version (version_num) VALUES (:head)"),
                {"head": head},
            )


def _client(sqlite_engine: sa.Engine, settings: Settings) -> TestClient:
    app = create_app(
        settings=settings,
        quality_client=FakeQualityClient(),
        engine=sqlite_engine,
    )
    return TestClient(app)


def test_liveness_is_independent_from_readiness(sqlite_engine):
    with _client(sqlite_engine, _settings()) as client:
        live = client.get("/healthz")
        not_ready = client.get("/readyz")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert not_ready.status_code == 503
    assert not_ready.json()["checks"] == {
        "database": "ok",
        "migration": "mismatch",
        "public_auth": "configured",
    }


def test_readiness_fails_when_database_is_unavailable(tmp_path):
    database_path = tmp_path / "missing-parent" / "control-plane.sqlite"
    engine = sa.create_engine(f"sqlite:///{database_path}")
    try:
        settings = _settings(database_url=f"sqlite:///{database_path}")
        with _client(engine, settings) as client:
            live = client.get("/healthz")
            not_ready = client.get("/readyz")
    finally:
        engine.dispose()

    assert live.status_code == 200
    assert not_ready.status_code == 503
    assert not_ready.json()["checks"] == {
        "database": "unavailable",
        "migration": "unknown",
        "public_auth": "configured",
    }


def test_readiness_requires_current_migration_and_independent_public_secrets(
    sqlite_engine,
):
    _stamp_current_head(sqlite_engine)
    with _client(sqlite_engine, _settings()) as client:
        ready = client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    assert ready.json()["checks"] == {
        "database": "ok",
        "migration": "current",
        "public_auth": "configured",
    }


def test_readiness_rejects_reused_public_secret(sqlite_engine):
    _stamp_current_head(sqlite_engine)
    shared = SecretStr("shared-public-secret")
    settings = _settings(
        public_credential_hash_pepper=shared,
        public_cursor_signing_key=shared,
    )
    with _client(sqlite_engine, settings) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"] == {
        "database": "ok",
        "migration": "current",
        "public_auth": "misconfigured",
    }
    assert "shared-public-secret" not in response.text


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "public_credential_hash_pepper": SecretStr(
                "readiness-internal-token"
            )
        },
        {
            "public_cursor_signing_key": SecretStr(
                "readiness-repairer-token"
            )
        },
    ],
)
def test_readiness_rejects_public_secret_reused_from_internal_namespace(
    sqlite_engine, overrides
):
    _stamp_current_head(sqlite_engine)
    with _client(sqlite_engine, _settings(**overrides)) as client:
        response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["checks"]["public_auth"] == "misconfigured"
    assert "readiness-internal-token" not in response.text
    assert "readiness-repairer-token" not in response.text
