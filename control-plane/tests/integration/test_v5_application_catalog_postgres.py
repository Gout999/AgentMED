"""V5-1A application catalog three-in-one: Alembic + PostgreSQL + installed CLI.

Mirrors the Stage-1A integration proof: disposable PG database, real migration
chain, real uvicorn server, and the real installed ``caseloop`` CLI speaking
``/api/v2`` with an explicit ``--api-version 2``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from conftest import (
    TEST_DATABASE_URL,
    UnsafeIntegrationDatabaseError,
    _new_pg_engine,
    _reset_pg_database_for_migrations,
)
from test_stage1a_public_cli_postgres import (
    _await_server,
    _install_cli,
    _loopback_port,
    _safe_process,
)
from app.models import Base
from app.models.v4_tables import (
    AuthorityReceipt,
    PublicCommandIdempotency,
)
from app.models.v5_tables import (
    AIApplication,
    DependencyEdge,
    Environment,
    SystemComponent,
)

pytestmark = pytest.mark.integration

WORKSPACE_ID = "ws_01J00000000000F1"
WRONG_WORKSPACE_ID = "ws_01J00000000000F2"
PROJECT_ID = "proj_01J00000000000F1"
OWNER_PRINCIPAL_ID = "prn_01J00000000000F1"
CATALOG_PRINCIPAL_ID = "prn_01J00000000000FA"
CONTROLLER_PRINCIPAL_ID = "prn_01J00000000000FB"
CATALOG_SUBJECT = "v5-catalog-e2e-admin"
CONTROLLER_REGISTRATION_ID = "creg_01J00000000000F1"
CREDENTIAL_ID = "cred_01J00000000000F1"


def _alembic_config(control_plane_root: Path) -> Config:
    config = Config(str(control_plane_root / "alembic.ini"))
    config.set_main_option("script_location", str(control_plane_root / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def _bootstrap_payload(
    *, raw_bearer: str, raw_jti: str, now: datetime
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "owner_principal": {
            "principal_id": OWNER_PRINCIPAL_ID,
            "subject": "v5-catalog-e2e-owner",
        },
        "principal": {
            "principal_id": CATALOG_PRINCIPAL_ID,
            "subject": CATALOG_SUBJECT,
        },
        "credential": {
            "credential_id": CREDENTIAL_ID,
            "bearer_token": raw_bearer,
            "jti": raw_jti,
            "issued_at": (now - timedelta(minutes=10)).isoformat(),
            "not_before": (now - timedelta(minutes=5)).isoformat(),
            "expires_at": (now + timedelta(days=30)).isoformat(),
        },
        "controller": {
            "registration_id": CONTROLLER_REGISTRATION_ID,
            "principal_id": CONTROLLER_PRINCIPAL_ID,
        },
        "secret_storage_ref": f"keyring://caseloop/test/{WORKSPACE_ID}",
    }


def _run_cli(
    cli: Path,
    *,
    env: dict[str, str],
    argv: list[str],
    pinned_site_packages: str,
    guarded_secrets: Iterable[str] = (),
    expected_exit: int = 0,
) -> dict[str, Any]:
    run_env = dict(
        env,
        PYTHONPATH=pinned_site_packages,
    )
    completed = _safe_process(
        [str(cli), *argv],
        label="caseloop cli",
        env=run_env,
        cwd=Path.cwd(),
        timeout=30,
        secrets_to_guard=list(guarded_secrets),
    )
    assert completed.returncode == expected_exit
    if expected_exit == 0:
        payload = json.loads(completed.stdout.strip())
        assert isinstance(payload, dict)
        return payload
    return json.loads(completed.stderr.strip())


def test_v5_catalog_installed_cli_real_postgres_loopback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    control_plane_root = repo_root / "control-plane"
    configured_database = sa.engine.make_url(TEST_DATABASE_URL).database
    if configured_database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "caseloop.integration_reset.refused.v5_exact_database_required"
        )

    raw_bearer = secrets.token_urlsafe(48)
    raw_jti = secrets.token_urlsafe(32)
    public_pepper = secrets.token_urlsafe(48)
    cursor_key = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    guarded_secrets = [raw_bearer, raw_jti]

    engine = _new_pg_engine()
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        engine.dispose()

        # Alembic only: create_all must never be the deployment path.
        with patch.object(
            Base.metadata,
            "create_all",
            side_effect=AssertionError("v5.e2e_must_not_use_create_all"),
        ):
            command.upgrade(_alembic_config(control_plane_root), "head")
        assert (
            engine.connect()
            .execute(sa.text("SELECT version_num FROM alembic_version"))
            .scalar_one()
            == "008"
        )

        cli, pinned_site_packages = _install_cli(repo_root, tmp_path)

        server_env = dict(
            os.environ,
            DATABASE_URL=TEST_DATABASE_URL,
            PUBLIC_CREDENTIAL_HASH_PEPPER=public_pepper,
            PUBLIC_CURSOR_SIGNING_KEY=cursor_key,
            PUBLIC_AUTH_ISSUER="https://auth.caseloop.dev",
            REQUIRE_MCP_ROLE_TOKENS="false",
            NOTIFICATION_ADAPTER="disabled",
            LOG_LEVEL="ERROR",
            PYTHONUNBUFFERED="1",
        )
        port = _loopback_port()
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "error",
                "--no-access-log",
            ],
            cwd=control_plane_root,
            env=server_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            _await_server(base_url, server)

            bootstrap = _safe_process(
                [
                    sys.executable,
                    "-m",
                    "app.bootstrap.v5_catalog_local",
                ],
                label="v5 catalog bootstrap",
                env=server_env,
                cwd=control_plane_root,
                input_text=json.dumps(
                    _bootstrap_payload(raw_bearer=raw_bearer, raw_jti=raw_jti, now=now),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                secrets_to_guard=guarded_secrets,
                timeout=30,
            )
            assert bootstrap.returncode == 0
            bootstrap_payload = json.loads(bootstrap.stdout.strip())
            assert bootstrap_payload["status"] == "CREATED"
            assert bootstrap_payload["controller"]["owner"] == "application-catalog-controller"

            cli_env = {
                "CASELOOP_API_URL": base_url,
                "CASELOOP_WORKSPACE_ID": WORKSPACE_ID,
                "CASELOOP_PUBLIC_TOKEN": raw_bearer,
            }

            # v2 commands require the explicit flag.
            api_required = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "application",
                    "register",
                    "--project-id",
                    PROJECT_ID,
                    "--slug",
                    "case-review-assistant",
                    "--display-name",
                    "Case Review Assistant",
                    "--owner-principal-id",
                    OWNER_PRINCIPAL_ID,
                    "--criticality",
                    "P1",
                    "--data-classification",
                    "INTERNAL",
                    "--governance-mode",
                    "MANAGED",
                    "--idempotency-key",
                    "v5-app-register-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
                expected_exit=2,
            )
            assert api_required["error"]["code"] == "API_VERSION_REQUIRED"
            # v1 commands reject the v2 flag.
            major_mismatch = _run_cli(
                cli,
                env=cli_env,
                argv=["--api-version", "2", "capabilities", "get"],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
                expected_exit=2,
            )
            assert major_mismatch["error"]["code"] == "API_MAJOR_MISMATCH"

            app_payload = _run_cli(
                    cli,
                    env=cli_env,
                    argv=[
                        "--api-version",
                        "2",
                        "application",
                        "register",
                        "--project-id",
                        PROJECT_ID,
                        "--slug",
                        "case-review-assistant",
                        "--display-name",
                        "Case Review Assistant",
                        "--owner-principal-id",
                        OWNER_PRINCIPAL_ID,
                        "--criticality",
                        "P1",
                        "--data-classification",
                        "INTERNAL",
                        "--governance-mode",
                        "MANAGED",
                        "--idempotency-key",
                        "v5-app-register-0001",
                    ],
                    pinned_site_packages=pinned_site_packages,
                    guarded_secrets=guarded_secrets,
                )
            assert app_payload["schema_version"] == "2.0"
            assert app_payload["workspace_id"] == WORKSPACE_ID
            assert app_payload["idempotency"]["replayed"] is False
            application_id = app_payload["application"]["application_id"]
            application_digest = app_payload["application"]["record_envelope"]["record_digest"]
            assert application_id.startswith("app_")
            assert application_digest.startswith("sha256:")

            # Replay the same key: same record, replayed flag, no second row.
            replay_payload = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "application",
                    "register",
                    "--project-id",
                    PROJECT_ID,
                    "--slug",
                    "case-review-assistant",
                    "--display-name",
                    "Case Review Assistant",
                    "--owner-principal-id",
                    OWNER_PRINCIPAL_ID,
                    "--criticality",
                    "P1",
                    "--data-classification",
                    "INTERNAL",
                    "--governance-mode",
                    "MANAGED",
                    "--idempotency-key",
                    "v5-app-register-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            assert replay_payload["idempotency"]["replayed"] is True
            assert (
                replay_payload["application"]["application_id"] == application_id
            )
            assert (
                replay_payload["application"]["record_envelope"]["record_digest"]
                == application_digest
            )

            got_app = _run_cli(
                cli,
                env=cli_env,
                argv=["--api-version", "2", "application", "get", application_id],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            assert got_app["application"]["application_id"] == application_id
            assert (
                got_app["application"]["record_envelope"]["record_digest"]
                == application_digest
            )

            env_payload = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "environment",
                    "register",
                    "--application-id",
                    application_id,
                    "--logical-name",
                    "production",
                    "--risk-classification",
                    "HIGH",
                    "--idempotency-key",
                    "v5-env-register-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            environment_id = env_payload["environment"]["environment_id"]
            assert environment_id.startswith("env_")

            comp_payload = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "system-component",
                    "register",
                    "--application-id",
                    application_id,
                    "--component-kind",
                    "AGENT",
                    "--logical-name",
                    "triage-agent",
                    "--owner-principal-id",
                    OWNER_PRINCIPAL_ID,
                    "--criticality",
                    "P1",
                    "--data-classification",
                    "INTERNAL",
                    "--permission-classification",
                    "READ_WRITE",
                    "--effect-classification",
                    "LOCAL",
                    "--idempotency-key",
                    "v5-component-register-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            component_a = comp_payload["component"]["component_id"]
            comp_b_payload = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "system-component",
                    "register",
                    "--application-id",
                    application_id,
                    "--component-kind",
                    "SKILL",
                    "--logical-name",
                    "triage-skill",
                    "--owner-principal-id",
                    OWNER_PRINCIPAL_ID,
                    "--criticality",
                    "P2",
                    "--data-classification",
                    "INTERNAL",
                    "--permission-classification",
                    "READ_ONLY",
                    "--effect-classification",
                    "NONE",
                    "--idempotency-key",
                    "v5-component-register-0002",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            component_b = comp_b_payload["component"]["component_id"]

            edge_payload = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "dependency-edge",
                    "record",
                    "--application-id",
                    application_id,
                    "--from-component-id",
                    component_a,
                    "--to-component-id",
                    component_b,
                    "--relation",
                    "INVOKES",
                    "--required",
                    "--idempotency-key",
                    "v5-edge-record-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            edge_id = edge_payload["edge"]["edge_id"]
            assert edge_payload["edge"]["required"] is True

            got_edge = _run_cli(
                cli,
                env=cli_env,
                argv=["--api-version", "2", "dependency-edge", "get", edge_id],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            assert got_edge["edge"]["edge_id"] == edge_id

            # A back edge would close a cycle.
            cycle_error = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "dependency-edge",
                    "record",
                    "--application-id",
                    application_id,
                    "--from-component-id",
                    component_b,
                    "--to-component-id",
                    component_a,
                    "--relation",
                    "INVOKES",
                    "--idempotency-key",
                    "v5-edge-record-0002",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
                expected_exit=2,
            )
            assert cycle_error["error"]["code"] == "VALIDATION_FAILED"

            # Cross-workspace is denied.
            wrong_env = dict(cli_env, CASELOOP_WORKSPACE_ID=WRONG_WORKSPACE_ID)
            denied = _run_cli(
                cli,
                env=wrong_env,
                argv=["--api-version", "2", "application", "get", application_id],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
                expected_exit=10,
            )
            assert denied["error"]["code"] in {
                "WORKSPACE_ACCESS_DENIED",
                "TOKEN_INVALID",
            }

            # ---- DB invariants -------------------------------------------------
            session_factory = sa.orm.sessionmaker(
                bind=engine, autoflush=False, autocommit=False
            )
            with session_factory() as session:
                assert session.scalar(sa.select(sa.func.count()).select_from(AIApplication)) == 1
                assert session.scalar(sa.select(sa.func.count()).select_from(Environment)) == 1
                assert session.scalar(sa.select(sa.func.count()).select_from(SystemComponent)) == 2
                assert session.scalar(sa.select(sa.func.count()).select_from(DependencyEdge)) == 1
                from app.models.tables import Event, Outbox

                events = session.scalars(
                    sa.select(Event).where(Event.contract_version == "v4")
                ).all()
                assert len(events) == 5
                assert {row.event_type for row in events} == {
                    "application.registered",
                    "environment.registered",
                    "system_component.registered",
                    "dependency_edge.recorded",
                }
                assert session.scalar(sa.select(sa.func.count()).select_from(Outbox)) == 5
                receipts = session.scalars(
                    sa.select(AuthorityReceipt).where(
                        AuthorityReceipt.subject_kind.in_(
                            ["AI_APPLICATION", "ENVIRONMENT", "SYSTEM_COMPONENT", "DEPENDENCY_EDGE"]
                        )
                    )
                ).all()
                assert len(receipts) == 5
                idempotency_rows = session.scalars(
                    sa.select(PublicCommandIdempotency).where(
                        PublicCommandIdempotency.intent.in_(
                            [
                                "applications.register",
                                "environments.register",
                                "system-components.register",
                                "dependency-edges.record",
                            ]
                        )
                    )
                ).all()
                assert len(idempotency_rows) == 5
                assert all(row.state == "COMPLETED" for row in idempotency_rows)
                app_row = session.get(AIApplication, application_id)
                assert app_row is not None
                assert (
                    app_row.envelope_payload["record_envelope"]["record_digest"]
                    == application_digest
                )
                app_command = session.scalars(
                    sa.select(PublicCommandIdempotency).where(
                        PublicCommandIdempotency.intent == "applications.register"
                    )
                ).one()
                assert app_command.resource_id == application_id
                assert app_command.resource_kind == "ai_application"

                # ---- same-key concurrency: one row, one replay ------------------
                barrier = threading.Barrier(2)
                outcomes: list[dict[str, Any]] = []
                lock = threading.Lock()

                def concurrent_register() -> None:
                    barrier.wait(timeout=10)
                    result = _run_cli(
                        cli,
                        env=cli_env,
                        argv=[
                            "--api-version",
                            "2",
                            "application",
                            "register",
                            "--project-id",
                            PROJECT_ID,
                            "--slug",
                            "concurrent-app",
                            "--display-name",
                            "Concurrent App",
                            "--owner-principal-id",
                            OWNER_PRINCIPAL_ID,
                            "--criticality",
                            "P2",
                            "--data-classification",
                            "INTERNAL",
                            "--governance-mode",
                            "MANAGED",
                            "--idempotency-key",
                            "v5-app-register-concurrent",
                        ],
                        pinned_site_packages=pinned_site_packages,
                        guarded_secrets=guarded_secrets,
                    )
                    with lock:
                        outcomes.append(result)

                threads = [
                    threading.Thread(target=concurrent_register),
                    threading.Thread(target=concurrent_register),
                ]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=30)
                assert len(outcomes) == 2
                replayed_flags = {item["idempotency"]["replayed"] for item in outcomes}
                assert replayed_flags == {True, False}
                concurrent_ids = {item["application"]["application_id"] for item in outcomes}
                assert len(concurrent_ids) == 1
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    finally:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        engine.dispose()
