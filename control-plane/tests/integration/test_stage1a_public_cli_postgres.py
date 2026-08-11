"""Real Stage 1A proof through Alembic, PostgreSQL, HTTP, and installed CLI.

This test is intentionally serial and destructive only to the explicitly
guarded ``control_plane_test`` database.  It never uses ORM ``create_all`` and
never places the opaque bearer in argv, a profile, or assertion diagnostics.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Iterable
from unittest.mock import patch

from alembic import command
from alembic.config import Config
import httpx
import pytest
from pydantic import SecretStr
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models.tables import Audit, Base, Event, Outbox
from app.models.v4_tables import (
    AgentRunRef,
    AuthorityReceipt,
    PublicCommandIdempotency,
    QualityCase,
    Signal,
    SignalCaseLink,
    SignalContent,
    TraceEvidenceReceipt,
)
from app.services.outbox_relay import OutboxDispatcher
from conftest import (
    TEST_DATABASE_URL,
    UnsafeIntegrationDatabaseError,
    _new_pg_engine,
    _reset_pg_database_for_migrations,
)


pytestmark = pytest.mark.integration

WORKSPACE_ID = "ws_01J00000000000E1"
WRONG_WORKSPACE_ID = "ws_01J00000000000E2"
PROJECT_ID = "proj_01J00000000000E1"
ENVIRONMENT_ID = "env_01J00000000000E1"
SOURCE_ID = "src_01J00000000000E1"
PRINCIPAL_ID = "prn_01J00000000000E1"
REPORTER_SUBJECT = "stage1a-e2e-maintainer"
SOURCE_EVENT_ID = "stage1a-e2e-source-event-0001"
IDEMPOTENCY_KEY = "stage1a-e2e-idempotency-0001"
OCCURRED_AT = "2026-08-10T12:00:00Z"


def _alembic_config(control_plane_root: Path) -> Config:
    config = Config(str(control_plane_root / "alembic.ini"))
    config.set_main_option("script_location", str(control_plane_root / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def _safe_process(
    argv: list[str],
    *,
    label: str,
    env: dict[str, str],
    cwd: Path,
    secrets_to_guard: Iterable[str] = (),
    input_text: str | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    combined = completed.stdout + completed.stderr
    if any(secret and secret in combined for secret in secrets_to_guard):
        pytest.fail(f"{label} emitted credential material; output withheld", pytrace=False)
    return completed


def _one_json_line(value: str, *, label: str) -> dict[str, Any]:
    lines = value.splitlines()
    if len(lines) != 1:
        pytest.fail(f"{label} did not emit exactly one JSON line", pytrace=False)
    try:
        payload = json.loads(lines[0])
    except (json.JSONDecodeError, UnicodeError):
        pytest.fail(f"{label} emitted invalid JSON", pytrace=False)
    if not isinstance(payload, dict):
        pytest.fail(f"{label} JSON was not an object", pytrace=False)
    return payload


def _install_cli(repo_root: Path, tmp_path: Path) -> tuple[Path, str]:
    """Install the local package into a disposable venv and return its script."""

    venv = tmp_path / "installed-cli"
    install_source = tmp_path / "cli-source"
    shutil.copytree(
        repo_root / "cli",
        install_source,
        ignore=shutil.ignore_patterns(
            "build", "dist", ".pytest_cache", "__pycache__", "*.egg-info"
        ),
    )
    install_env = {
        "PATH": os.environ.get("PATH", ""),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
    }
    created = _safe_process(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
        label="cli venv creation",
        env=install_env,
        cwd=repo_root,
        timeout=30,
    )
    if created.returncode != 0:
        pytest.fail("cli venv creation failed; output withheld", pytrace=False)
    python = venv / "bin" / "python"
    installed = _safe_process(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            str(install_source),
        ],
        label="cli installation",
        env=install_env,
        cwd=repo_root,
        timeout=60,
    )
    if installed.returncode != 0:
        pytest.fail("cli installation failed; output withheld", pytrace=False)
    executable = venv / "bin" / "caseloop"
    if not executable.is_file():
        pytest.fail("installed CLI entrypoint is missing", pytrace=False)

    # The package itself comes from the disposable installation.  Runtime-only
    # dependencies are reused from the pinned control-plane venv so this proof
    # stays offline and cannot resolve newer packages from the network.
    pinned_site_packages = str(Path(httpx.__file__).resolve().parents[1])
    return executable, pinned_site_packages


def _bootstrap_payload(
    *, raw_bearer: str, raw_jti: str, now: datetime
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "environment_id": ENVIRONMENT_ID,
        "source": {
            "source_id": SOURCE_ID,
            "connector_kind": "manual",
            "state": "ACTIVE",
            "credential_ref": None,
            "config": {
                "display_name": "Stage 1A installed CLI PostgreSQL proof",
                "provider_origin": "https://caseloop.local",
            },
        },
        "principal": {
            "principal_id": PRINCIPAL_ID,
            "subject": REPORTER_SUBJECT,
        },
        "credential": {
            "credential_id": "cred_01J00000000000E1",
            "bearer_token": raw_bearer,
            "jti": raw_jti,
            "issued_at": (now - timedelta(minutes=2)).isoformat(),
            "not_before": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
        },
        "controllers": {
            "signal": {
                "registration_id": "creg_01J00000000000E1",
                "principal_id": "prn_01J00000000001E1",
            },
            "case": {
                "registration_id": "creg_01J00000000000E2",
                "principal_id": "prn_01J00000000001E2",
            },
            "evidence": {
                "registration_id": "creg_01J00000000000E3",
                "principal_id": "prn_01J00000000001E3",
            },
        },
        "secret_storage_ref": f"keyring://caseloop/test/{WORKSPACE_ID}",
    }


def _loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _await_server(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 12.0
    with httpx.Client(timeout=0.25, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail(
                    f"uvicorn exited before readiness with code {process.returncode}; output withheld",
                    pytrace=False,
                )
            try:
                response = client.get(f"{base_url}/healthz")
                if response.status_code == 200 and response.json().get("status") == "ok":
                    return
            except (httpx.HTTPError, json.JSONDecodeError, ValueError):
                pass
            time.sleep(0.05)
    pytest.fail("uvicorn did not become ready before timeout; output withheld", pytrace=False)


def _run_cli(
    executable: Path,
    argv: list[str],
    *,
    env: dict[str, str],
    repo_root: Path,
    guarded_secrets: Iterable[str],
    expected_exit: int = 0,
) -> dict[str, Any]:
    command_label = " ".join(argv[:2])
    completed = _safe_process(
        [str(executable), *argv],
        label=f"installed CLI {command_label}",
        env=env,
        cwd=repo_root,
        secrets_to_guard=guarded_secrets,
        timeout=25,
    )
    if completed.returncode != expected_exit:
        remote_code = "unavailable"
        try:
            diagnostic = json.loads(completed.stderr)
            candidate = diagnostic.get("error", {}).get("code")
            if isinstance(candidate, str) and candidate.isupper():
                remote_code = candidate
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        pytest.fail(
            f"installed CLI {command_label} exited {completed.returncode}, "
            f"expected {expected_exit}; remote code {remote_code}; output withheld",
            pytrace=False,
        )
    if expected_exit == 0:
        if completed.stderr:
            pytest.fail("successful CLI call wrote stderr; output withheld", pytrace=False)
        return _one_json_line(completed.stdout, label="successful CLI call")
    if completed.stdout:
        pytest.fail("failed CLI call wrote stdout; output withheld", pytrace=False)
    return _one_json_line(completed.stderr, label="failed CLI call")


def _count(session: Any, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_stage1a_public_installed_cli_real_postgres_loopback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    control_plane_root = repo_root / "control-plane"
    configured_database = sa.engine.make_url(TEST_DATABASE_URL).database
    if configured_database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "caseloop.integration_reset.refused.stage1a_exact_database_required"
        )

    raw_bearer = secrets.token_urlsafe(48)
    wrong_bearer = secrets.token_urlsafe(48)
    raw_jti = secrets.token_urlsafe(32)
    public_pepper = secrets.token_urlsafe(48)
    cursor_key = secrets.token_urlsafe(48)
    guarded_secrets = (
        raw_bearer,
        wrong_bearer,
        raw_jti,
        public_pepper,
        cursor_key,
    )
    now = datetime.now(timezone.utc)
    engine = _new_pg_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    server: subprocess.Popen[str] | None = None
    reset_complete = False
    server_output = ""

    server_env = {
        "PATH": os.environ.get("PATH", ""),
        "DATABASE_URL": TEST_DATABASE_URL,
        "PUBLIC_CREDENTIAL_HASH_PEPPER": public_pepper,
        "PUBLIC_CURSOR_SIGNING_KEY": cursor_key,
        "PUBLIC_AUTH_ISSUER": "https://auth.caseloop.dev",
        "REQUIRE_MCP_ROLE_TOKENS": "false",
        "NOTIFICATION_ADAPTER": "disabled",
        "LOG_LEVEL": "ERROR",
        "PYTHONUNBUFFERED": "1",
    }
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        reset_complete = True
        with patch.object(
            Base.metadata,
            "create_all",
            side_effect=AssertionError("stage1a.e2e_must_not_use_create_all"),
        ):
            command.upgrade(_alembic_config(control_plane_root), "head")
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "010"

        bootstrap = _safe_process(
            [sys.executable, "-m", "app.bootstrap.stage1a_local"],
            label="Stage 1A bootstrap",
            env=server_env,
            cwd=control_plane_root,
            secrets_to_guard=guarded_secrets,
            input_text=json.dumps(
                _bootstrap_payload(raw_bearer=raw_bearer, raw_jti=raw_jti, now=now),
                sort_keys=True,
                separators=(",", ":"),
            ),
            timeout=30,
        )
        if bootstrap.returncode != 0 or bootstrap.stderr:
            pytest.fail("Stage 1A bootstrap failed; output withheld", pytrace=False)
        bootstrap_receipt = _one_json_line(bootstrap.stdout, label="Stage 1A bootstrap")
        assert bootstrap_receipt["status"] == "CREATED"
        assert bootstrap_receipt["workspace_id"] == WORKSPACE_ID

        cli, pinned_site_packages = _install_cli(repo_root, tmp_path)
        port = _loopback_port()
        base_url = f"http://127.0.0.1:{port}"
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
        _await_server(base_url, server)

        cli_env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": pinned_site_packages,
            "CASELOOP_API_URL": base_url,
            "CASELOOP_WORKSPACE_ID": WORKSPACE_ID,
            "CASELOOP_PUBLIC_TOKEN": raw_bearer,
            "CASELOOP_SOURCE_ID": SOURCE_ID,
            "CASELOOP_PROJECT_ID": PROJECT_ID,
            "CASELOOP_ENVIRONMENT_ID": ENVIRONMENT_ID,
            "CASELOOP_REPORTER_REF": REPORTER_SUBJECT,
        }

        capabilities = _run_cli(
            cli,
            ["capabilities", "get"],
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert capabilities["workspace_id"] == WORKSPACE_ID
        assert {item["name"] for item in capabilities["data"]["enabled_intents"]} == {
            "capabilities.get",
            "signals.submit",
            "cases.get",
            "cases.timeline",
            "evidence.get",
        }

        submit_argv = [
            "signal",
            "submit",
            "--summary",
            "Maintainer found a deterministic output regression",
            "--body",
            "The installed CLI reproduces the regression on the local agent.",
            "--source-event-id",
            SOURCE_EVENT_ID,
            "--occurred-at",
            OCCURRED_AT,
            "--idempotency-key",
            IDEMPOTENCY_KEY,
        ]
        submitted = _run_cli(
            cli,
            submit_argv,
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert submitted["workspace_id"] == WORKSPACE_ID
        assert submitted["idempotency"]["replayed"] is False
        assert submitted["next_action"] == {
            "code": "CORRELATE_TRACE",
            "command": None,
            "href": None,
        }
        case_id = submitted["case"]["case_id"]
        receipt_id = submitted["evidence"]["receipt_id"]

        case = _run_cli(
            cli,
            ["case", "get", case_id],
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert case["data"]["case_id"] == case_id
        assert case["data"]["signal_refs"] == [submitted["signal"]["signal_id"]]

        timeline = _run_cli(
            cli,
            ["case", "timeline", case_id, "--limit", "50"],
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert timeline["data"]["case_id"] == case_id
        assert [event["event_type"] for event in timeline["data"]["events"]] == [
            "signal.received",
            "case.opened",
            "signal_case_link.linked",
            "evidence.recorded",
        ]

        evidence = _run_cli(
            cli,
            ["evidence", "get", receipt_id],
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert evidence["data"]["receipt"]["receipt_id"] == receipt_id
        assert evidence["data"]["verification_status"] == "VERIFIED"

        replayed = _run_cli(
            cli,
            submit_argv,
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert replayed["idempotency"]["replayed"] is True
        normalized_replay = json.loads(json.dumps(replayed))
        normalized_replay["idempotency"]["replayed"] = False
        assert normalized_replay == submitted
        assert replayed["idempotency"]["receipt"] == submitted["idempotency"]["receipt"]

        wrong_workspace_env = {**cli_env, "CASELOOP_WORKSPACE_ID": WRONG_WORKSPACE_ID}
        wrong_workspace = _run_cli(
            cli,
            ["capabilities", "get"],
            env=wrong_workspace_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
            expected_exit=10,
        )
        assert wrong_workspace["error"]["code"] == "WORKSPACE_ACCESS_DENIED"

        wrong_token_env = {**cli_env, "CASELOOP_PUBLIC_TOKEN": wrong_bearer}
        wrong_token = _run_cli(
            cli,
            ["capabilities", "get"],
            env=wrong_token_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
            expected_exit=10,
        )
        assert wrong_token["error"]["code"] == "TOKEN_INVALID"

        with factory() as session:
            events = list(
                session.scalars(
                    select(Event)
                    .where(
                        Event.workspace_id == WORKSPACE_ID,
                        Event.contract_version == "v4",
                    )
                    .order_by(Event.occurred_at, Event.event_id)
                ).all()
            )
            assert len(events) == 4
            transaction_id = events[0].transaction_id
            assert {event.transaction_id for event in events} == {transaction_id}
            assert _count(session, Signal) == 1
            assert _count(session, SignalContent) == 1
            assert _count(session, QualityCase) == 1
            assert _count(session, SignalCaseLink) == 1
            assert _count(session, TraceEvidenceReceipt) == 1
            assert _count(session, AgentRunRef) == 0

            outbox = list(
                session.scalars(
                    select(Outbox)
                    .where(
                        Outbox.contract_version == "v4",
                        Outbox.channel == "v4.domain.events",
                    )
                    .order_by(Outbox.created_at, Outbox.outbox_id)
                ).all()
            )
            assert len(outbox) == 4
            assert all(
                row.status == "PENDING"
                and row.attempts == 0
                and row.claim_token is None
                and row.claimed_by is None
                for row in outbox
            )
            assert session.scalar(
                select(func.count())
                .select_from(AuthorityReceipt)
                .where(AuthorityReceipt.transaction_id == transaction_id)
            ) == 4
            transaction_audits = list(
                session.scalars(
                    select(Audit)
                    .where(Audit.transaction_id == transaction_id)
                    .order_by(Audit.ts, Audit.audit_id)
                ).all()
            )
            assert len(transaction_audits) == 5
            assert {audit.action for audit in transaction_audits} == {
                "controller.signal.received",
                "controller.case.opened",
                "controller.signal_case_link.linked",
                "controller.evidence.recorded",
                "signals.submit",
            }
            idempotency = list(session.scalars(select(PublicCommandIdempotency)).all())
            assert len(idempotency) == 1
            assert idempotency[0].state == "COMPLETED"
            assert idempotency[0].idempotency_key == IDEMPOTENCY_KEY
            assert idempotency[0].resource_id == submitted["signal"]["signal_id"]
            assert idempotency[0].receipt_digest == submitted["idempotency"]["receipt"]["receipt_digest"]

        dispatcher_settings = Settings(
            database_url=TEST_DATABASE_URL,
            public_credential_hash_pepper=SecretStr(public_pepper),
            public_cursor_signing_key=SecretStr(cursor_key),
            public_auth_issuer="https://auth.caseloop.dev",
            require_mcp_role_tokens=False,
            notification_adapter="disabled",
        )
        dispatcher = OutboxDispatcher(
            factory,
            dispatcher_settings,
            worker_id="test:stage1a-e2e:v3-dispatcher",
        )
        assert dispatcher.dispatch_batch(limit=10) == {
            "claimed": 0,
            "sent": 0,
            "retried": 0,
            "dead": 0,
            "blocked": 0,
        }
        with factory() as session:
            persisted_v4 = list(
                session.scalars(
                    select(Outbox).where(Outbox.contract_version == "v4")
                ).all()
            )
            assert len(persisted_v4) == 4
            assert all(
                row.status == "PENDING" and row.attempts == 0
                for row in persisted_v4
            )
    finally:
        if server is not None:
            if server.poll() is None:
                server.terminate()
            try:
                stdout, stderr = server.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                server.kill()
                stdout, stderr = server.communicate(timeout=5)
            server_output = stdout + stderr
        if any(secret and secret in server_output for secret in guarded_secrets):
            pytest.fail("uvicorn emitted credential material; output withheld", pytrace=False)
        try:
            if reset_complete:
                _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        finally:
            engine.dispose()
