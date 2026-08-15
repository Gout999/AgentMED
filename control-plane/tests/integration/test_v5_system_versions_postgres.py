"""V5-1B PostgreSQL integration: atomic trusted import, idempotent replay,
CLI ``agentmed init`` + ``system-manifest`` end-to-end over a real server.

Mirrors the Stage-1A integration proof: disposable PG database, the real
current Alembic head, real uvicorn server, and the installed ``agentmed`` CLI
speaking /api/v2 with an explicit ``--api-version 2``.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session, sessionmaker

from conftest import (
    TEST_DATABASE_URL,
    UnsafeIntegrationDatabaseError,
    _new_pg_engine,
    _reset_pg_database_for_migrations,
)
from test_stage1a_public_cli_postgres import (
    _alembic_config,
    _await_server,
    _install_cli,
    _loopback_port,
    _one_json_line,
    _run_cli,
    _safe_process,
)
from app.models import Base
from app.models.v4_tables import (
    ControllerRegistration,
    PublicCredential,
    PublicPrincipal,
)
from app.public_api.credential_resolver import hash_opaque_bearer
from app.public_api.v5_models import SystemManifestImportRequest
from app.services.v4_audit import V4AuditService
from app.services.v5_authority import (
    V5_CATALOG_OWNER,
    build_v5_controller_registration_record,
)
from app.services.system_versions import SystemVersionsService
from app.utils.v4_integrity import canonical_digest

pytestmark = pytest.mark.integration

WORKSPACE_ID = "ws_01J00000000000G1"
PROJECT_ID = "proj_01J00000000000G1"
OWNER_PRINCIPAL_ID = "prn_01J00000000000G1"
IMPORT_PRINCIPAL_ID = "prn_01J00000000000GA"
CONTROLLER_PRINCIPAL_ID = "prn_01J00000000000GB"
CATALOG_REGISTRATION_ID = "creg_01J00000000000G1"
VERSION_REGISTRATION_ID = "creg_01J00000000000G2"
CREDENTIAL_ID = "cred_01J00000000000G1"
AUTH_SUBJECT = "v5-1b-e2e-admin"
AUDIENCES = ["caseloop-public-api"]
ISSUER = "https://auth.agentmed.dev"
IMPORT_SCOPES = ["system_manifests:import", "system_versions:read"]


def _claims(scopes: list[str]) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": AUTH_SUBJECT,
            "principal_type": "human",
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE_ID,
            "project_ids": [PROJECT_ID],
            "environment_ids": [],
            "scopes": scopes,
        }
    )


def _seed_auth_and_controllers(
    session: Session, *, raw_bearer: str, now: datetime, pepper: str = "seed-pepper"
) -> None:
    from app.public_api.credential_resolver import digest_public_subject

    owner_subject = "v5-1b-e2e-owner"
    session.add(
        PublicPrincipal(
            principal_id=OWNER_PRINCIPAL_ID,
            workspace_id=WORKSPACE_ID,
            principal_type="human",
            state="ACTIVE",
            subject_digest=digest_public_subject(owner_subject),
            audiences=list(AUDIENCES),
            project_ids=[PROJECT_ID],
            environment_ids=[],
            scopes=["signals:write", "cases:read"],
            claims_digest=canonical_digest(
                {
                    "schema_version": "1.0",
                    "issuer": ISSUER,
                    "subject": owner_subject,
                    "principal_type": "human",
                    "audiences": AUDIENCES,
                    "workspace_id": WORKSPACE_ID,
                    "project_ids": [PROJECT_ID],
                    "environment_ids": [],
                    "scopes": ["signals:write", "cases:read"],
                }
            ),
            revoked_at=None,
        )
    )
    session.add(
        PublicPrincipal(
            principal_id=IMPORT_PRINCIPAL_ID,
            workspace_id=WORKSPACE_ID,
            principal_type="human",
            state="ACTIVE",
            subject_digest=digest_public_subject(AUTH_SUBJECT),
            audiences=list(AUDIENCES),
            project_ids=[PROJECT_ID],
            environment_ids=[],
            scopes=IMPORT_SCOPES,
            trust_roles=["integrator"],
            claims_digest=_claims(IMPORT_SCOPES),
            revoked_at=None,
        )
    )
    session.flush()
    session.add(
        PublicCredential(
            credential_id=CREDENTIAL_ID,
            workspace_id=WORKSPACE_ID,
            principal_id=IMPORT_PRINCIPAL_ID,
            issuer=ISSUER,
            subject=AUTH_SUBJECT,
            credential_hash=hash_opaque_bearer(raw_bearer, pepper),
            hash_algorithm="hmac-sha256-v1",
            jti_digest="sha256:" + "b" * 64,
            claims_digest=_claims(IMPORT_SCOPES),
            audiences=list(AUDIENCES),
            project_ids=[PROJECT_ID],
            environment_ids=[],
            scopes=IMPORT_SCOPES,
            state="ACTIVE",
            issued_at=now - timedelta(minutes=10),
            not_before=now - timedelta(minutes=5),
            expires_at=now + timedelta(days=30),
        )
    )
    for owner_name, registration_id, commands in (
        (
            V5_CATALOG_OWNER,
            CATALOG_REGISTRATION_ID,
            [
                "applications.register",
                "applications.activate",
                "applications.archive",
                "applications.restore",
                "environments.register",
                "environments.retire",
                "environments.restore",
                "system-components.register",
                "system-components.activate",
                "system-components.deprecate",
                "system-components.reactivate",
                "system-components.retire",
                "dependency-edges.record",
            ],
        ),
        (
            "version-controller",
            VERSION_REGISTRATION_ID,
            [
                "component-revisions.record",
                "topology-revisions.record",
                "system-versions.record",
                "bootstrap-attestations.record",
                "system-assignments.record",
            ],
        ),
    ):
        audit = V4AuditService(session, clock=lambda: now)
        recorded = audit.record(
            workspace_id=WORKSPACE_ID,
            actor_principal=OWNER_PRINCIPAL_ID,
            action="controllers.register",
            target=registration_id,
            params={
                "owner": owner_name,
                "service_identity_digest": canonical_digest(
                    {
                        "schema_version": "1.0",
                        "workspace_id": WORKSPACE_ID,
                        "owner": owner_name,
                        "controller_principal": CONTROLLER_PRINCIPAL_ID,
                        "principal_type": "CONTROLLER_SERVICE",
                        "service": "agentmed-control-plane",
                    }
                ),
            },
            transaction_id=f"txn_{registration_id}",
            evidence_refs={
                "owner": owner_name,
                "controller_registration_id": registration_id,
                "controller_principal": CONTROLLER_PRINCIPAL_ID,
            },
            occurred_at=now,
        )
        built = build_v5_controller_registration_record(
            controller_registration_id=registration_id,
            workspace_id=WORKSPACE_ID,
            owner=owner_name,
            controller_principal=CONTROLLER_PRINCIPAL_ID,
            allowed_commands=commands,
            service_identity_digest=canonical_digest(
                {
                    "schema_version": "1.0",
                    "workspace_id": WORKSPACE_ID,
                    "owner": owner_name,
                    "controller_principal": CONTROLLER_PRINCIPAL_ID,
                    "principal_type": "CONTROLLER_SERVICE",
                    "service": "agentmed-control-plane",
                }
            ),
            registered_by_human_principal=OWNER_PRINCIPAL_ID,
            registration_audit_ref=recorded.audit_ref,
            valid_from=now,
            registered_at=now,
        )
        session.add(ControllerRegistration(**built.row_values))
    session.commit()


def _manifest_payload(slug: str = "llm-cli") -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "application": {
            "project_id": PROJECT_ID,
            "slug": slug,
            "display_name": "LLM CLI",
            "owner_principal_ids": [OWNER_PRINCIPAL_ID],
            "criticality": "P0",
            "data_classification": "INTERNAL",
            "governance_mode": "MANAGED",
        },
        "environment": {"logical_name": "prod", "risk_classification": "MEDIUM"},
        "components": [
            {
                "logical_name": "llm-code",
                "component_kind": "APPLICATION_CODE",
                "owner_principal_ids": [OWNER_PRINCIPAL_ID],
                "criticality": "P0",
                "data_classification": "INTERNAL",
                "permission_classification": "READ_WRITE",
                "effect_classification": "LOCAL",
                "revision": {
                    "identity_locator": {"type": "git", "path": "."},
                    "identity_assurance": "IMMUTABLE_DIGEST",
                    "content_digest": "sha256:" + "a" * 64,
                },
            }
        ],
        "dependency_edges": [],
        "approver_policy": {
            "logical_name": "human-approver-policy",
            "component_kind": "POLICY",
            "owner_principal_ids": [OWNER_PRINCIPAL_ID],
            "criticality": "P1",
            "data_classification": "CONFIDENTIAL",
            "permission_classification": "READ_ONLY",
            "effect_classification": "NONE",
            "revision": {
                "identity_locator": {"type": "file", "path": "policies/approvers.yaml"},
                "identity_assurance": "UNKNOWN",
                "unknown_reason": "policy file has no digest source",
            },
        },
    }


def _count(session: Session, model: type[object]) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


def test_v5_1b_pg_import_atomic_rollback_and_replay() -> None:
    control_plane_root = Path(__file__).resolve().parents[2]
    if sa.engine.make_url(TEST_DATABASE_URL).database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "agentmed.integration_reset.refused.v5_1b_exact_database_required"
        )
    engine = _new_pg_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        with patch.object(
            Base.metadata,
            "create_all",
            side_effect=AssertionError("v5_1b.must_not_use_create_all"),
        ):
            alembic_config = _alembic_config(control_plane_root)
            command.upgrade(alembic_config, "head")
        expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == expected_head

        now = datetime.now(timezone.utc)
        session = factory()
        try:
            _seed_auth_and_controllers(
                session, raw_bearer="unused-direct-service-seed", now=now
            )
            service = SystemVersionsService(session, clock=lambda: now)
            manifest = SystemManifestImportRequest.model_validate(
                _manifest_payload()
            )
            from app.public_api.auth_contract import AcceptedPrincipalContext

            principal = AcceptedPrincipalContext.model_validate(
                {
                    "schema_version": "1.0",
                    "principal_id": IMPORT_PRINCIPAL_ID,
                    "principal_type": "human",
                    "issuer": ISSUER,
                    "subject": AUTH_SUBJECT,
                    "audiences": AUDIENCES,
                    "workspace_id": WORKSPACE_ID,
                    "project_ids": [PROJECT_ID],
                    "environment_ids": [],
                    "scopes": IMPORT_SCOPES,
                    "credential_id": CREDENTIAL_ID,
                    "jti_digest": "sha256:" + "a" * 64,
                    "issued_at": now - timedelta(minutes=10),
                    "not_before": now - timedelta(minutes=5),
                    "expires_at": now + timedelta(days=30),
                    "revoked_at": None,
                    "revocation_checked_at": now,
                    "requested_context": {
                        "workspace_id": WORKSPACE_ID,
                        "project_id": PROJECT_ID,
                        "environment_id": None,
                        "required_scope": "system_manifests:import",
                    },
                    "evaluated_at": now,
                    "claims_digest": _claims(IMPORT_SCOPES),
                }
            )

            from app.models.v5_tables import (
                AIApplication,
                BootstrapAttestation,
                ComponentRevision,
                DependencyEdge,
                Environment,
                SystemAssignment,
                SystemComponent,
                SystemVersionSet,
                TopologyRevision,
            )
            from app.services.system_versions import SystemVersionsError

            # atomic rollback first (workspace still empty): a failing command
            # audit leaves zero records
            session2 = factory()
            try:
                from app.services.v4_audit import V4AuditService

                failing = V4AuditService(session2, clock=lambda: now, force_fail=False, fail_on_call=1)
                failing_service = SystemVersionsService(session2, clock=lambda: now, audit_service=failing)

                with pytest.raises(SystemVersionsError) as raised:
                    failing_service.import_manifest(
                        SystemManifestImportRequest.model_validate(
                            _manifest_payload(slug="rollback-app")
                        ),
                        principal=principal,
                        idempotency_key="manifest-rollback-0001",
                        request_id="req_01J00000000000G3",
                    )
                assert raised.value.code == "AUDIT_UNAVAILABLE"
                session2.rollback()
                for model in (
                    AIApplication,
                    SystemComponent,
                    ComponentRevision,
                    SystemVersionSet,
                    BootstrapAttestation,
                    SystemAssignment,
                ):
                    assert _count(session2, model) == 0, model.__name__
            finally:
                session2.close()

            # Two different first manifests race in separate transactions.
            # The workspace advisory xact lock must make the empty check +
            # graph insert one decision: exactly one wins and one conflicts.
            contenders = [
                (
                    manifest,
                    "manifest-import-0001",
                    "req_01J00000000000G1",
                ),
                (
                    SystemManifestImportRequest.model_validate(
                        _manifest_payload(slug="concurrent-app")
                    ),
                    "manifest-concurrent-0001",
                    "req_01J00000000000G5",
                ),
            ]
            barrier = threading.Barrier(2)
            outcomes: list[tuple[int, str, Any]] = []
            outcomes_lock = threading.Lock()

            def _race_import(
                contender_index: int,
                contender_manifest: SystemManifestImportRequest,
                idempotency_key: str,
                request_id: str,
            ) -> None:
                contender_session = factory()
                try:
                    contender_service = SystemVersionsService(
                        contender_session, clock=lambda: now
                    )
                    barrier.wait(timeout=10)
                    try:
                        result = contender_service.import_manifest(
                            contender_manifest,
                            principal=principal,
                            idempotency_key=idempotency_key,
                            request_id=request_id,
                        )
                        contender_session.commit()
                    except SystemVersionsError as exc:
                        contender_session.rollback()
                        outcome: tuple[int, str, Any] = (
                            contender_index,
                            exc.code,
                            None,
                        )
                    except Exception as exc:  # pragma: no cover - diagnostic path
                        contender_session.rollback()
                        outcome = (
                            contender_index,
                            f"UNEXPECTED:{type(exc).__name__}:{exc}",
                            None,
                        )
                    else:
                        outcome = (contender_index, "SUCCESS", result)
                    with outcomes_lock:
                        outcomes.append(outcome)
                finally:
                    contender_session.close()

            threads = [
                threading.Thread(
                    target=_race_import,
                    args=(index, *contender),
                    daemon=True,
                )
                for index, contender in enumerate(contenders)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)
            assert all(not thread.is_alive() for thread in threads)
            assert sorted(status for _index, status, _result in outcomes) == [
                "CATALOG_CONFLICT",
                "SUCCESS",
            ]
            winner_index, _status, imported = next(
                outcome for outcome in outcomes if outcome[1] == "SUCCESS"
            )
            manifest = contenders[winner_index][0]
            assert imported.idempotency.replayed is False
            version_set_id = imported.system_version_set.system_version_set_id

            # same-manifest digest under a different key replays the same set
            replay = service.import_manifest(
                manifest,
                principal=principal,
                idempotency_key="manifest-import-9999",
                request_id="req_01J00000000000G2",
            )
            session.commit()
            assert replay.idempotency.replayed is True
            assert replay.system_version_set.system_version_set_id == version_set_id
            expected_counts = {
                AIApplication: 1,
                Environment: 1,
                SystemComponent: 2,  # llm-code + approver policy
                DependencyEdge: 0,
                ComponentRevision: 2,  # llm-code + approver policy
                TopologyRevision: 1,
                SystemVersionSet: 1,
                BootstrapAttestation: 1,
                SystemAssignment: 1,
            }
            for model, expected in expected_counts.items():
                assert _count(session, model) == expected, model.__name__

            # A second manifest into the same workspace conflicts (one-shot).
            with pytest.raises(SystemVersionsError) as raised:
                service.import_manifest(
                    SystemManifestImportRequest.model_validate(
                        _manifest_payload(slug="second-app")
                    ),
                    principal=principal,
                    idempotency_key="manifest-second-0001",
                    request_id="req_01J00000000000G4",
                )
            assert raised.value.code == "CATALOG_CONFLICT"
            session.rollback()
        finally:
            session.close()
    finally:
        engine.dispose()


def test_v5_1b_cli_init_and_manifest_import_e2e(    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    control_plane_root = repo_root / "control-plane"
    if sa.engine.make_url(TEST_DATABASE_URL).database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "agentmed.integration_reset.refused.v5_1b_e2e_exact_database_required"
        )

    raw_bearer = secrets.token_urlsafe(48)
    public_pepper = secrets.token_urlsafe(48)
    cursor_key = secrets.token_urlsafe(48)
    guarded_secrets = (raw_bearer, public_pepper, cursor_key)
    now = datetime.now(timezone.utc)
    engine = _new_pg_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    server: subprocess.Popen[str] | None = None

    server_env = {
        "PATH": os.environ.get("PATH", ""),
        "DATABASE_URL": TEST_DATABASE_URL,
        "PUBLIC_CREDENTIAL_HASH_PEPPER": public_pepper,
        "PUBLIC_CURSOR_SIGNING_KEY": cursor_key,
        "PUBLIC_AUTH_ISSUER": ISSUER,
        "REQUIRE_MCP_ROLE_TOKENS": "false",
        "NOTIFICATION_ADAPTER": "disabled",
        "LOG_LEVEL": "ERROR",
        "PYTHONUNBUFFERED": "1",
    }
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)

    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        with patch.object(
            Base.metadata,
            "create_all",
            side_effect=AssertionError("v5_1b.e2e_must_not_use_create_all"),
        ):
            command.upgrade(_alembic_config(control_plane_root), "head")
        session = factory()
        try:
            _seed_auth_and_controllers(session, raw_bearer=raw_bearer, now=now, pepper=public_pepper)
        finally:
            session.close()

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
            "AGENTMED_API_URL": base_url,
            "AGENTMED_WORKSPACE_ID": WORKSPACE_ID,
            "AGENTMED_PUBLIC_TOKEN": raw_bearer,
        }

        # Build a small real git workload repo for discovery.
        workload = tmp_path / "llm-workload"
        workload.mkdir()
        _safe_process(
            ["git", "-C", str(workload), "init", "-q", "-b", "main"],
            label="git init",
            env={"PATH": os.environ.get("PATH", "")},
            cwd=workload,
            timeout=30,
        )
        _safe_process(
            ["git", "-C", str(workload), "config", "user.email", "test@agentmed.dev"],
            label="git config",
            env={"PATH": os.environ.get("PATH", "")},
            cwd=workload,
            timeout=30,
        )
        _safe_process(
            ["git", "-C", str(workload), "config", "user.name", "Test"],
            label="git config name",
            env={"PATH": os.environ.get("PATH", "")},
            cwd=workload,
            timeout=30,
        )
        (workload / "pyproject.toml").write_text(
            "[project]\nname = 'llm-workload'\n", encoding="utf-8"
        )
        (workload / "llm.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
        _safe_process(
            ["git", "-C", str(workload), "add", "."],
            label="git add",
            env={"PATH": os.environ.get("PATH", "")},
            cwd=workload,
            timeout=30,
        )
        _safe_process(
            ["git", "-C", str(workload), "commit", "-q", "-m", "initial"],
            label="git commit",
            env={"PATH": os.environ.get("PATH", "")},
            cwd=workload,
            timeout=30,
        )

        init_completed = _safe_process(
            [str(cli), "--api-version", "2", "init", str(workload)],
            label="installed CLI init",
            env=cli_env,
            cwd=repo_root,
            secrets_to_guard=guarded_secrets,
            timeout=25,
        )
        assert init_completed.returncode == 0, "agentmed init failed"
        init_result = _one_json_line(init_completed.stdout, label="init draft")
        assert "_discovery" in init_result
        assert init_result["components"], "init must emit at least APPLICATION_CODE"
        assert any(
            component["component_kind"] == "APPLICATION_CODE"
            for component in init_result["components"]
        )

        manifest_file = tmp_path / "manifest.json"
        draft = dict(init_result)
        draft["application"] = _manifest_payload()["application"]
        draft["environment"] = _manifest_payload()["environment"]
        for component in draft["components"]:
            component["owner_principal_ids"] = [OWNER_PRINCIPAL_ID]
        manifest_file.write_text(
            json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        imported = _run_cli(
            cli,
            [
                "--api-version",
                "2",
                "system-manifest",
                "import",
                "--manifest-file",
                str(manifest_file),
            ],
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert imported["system_assignment"]["transition_kind"] == "BOOTSTRAP"
        assert imported["system_assignment"]["generation"] == 1
        version_set_id = imported["system_version_set"]["system_version_set_id"]

        got = _run_cli(
            cli,
            ["--api-version", "2", "system-manifest", "get", version_set_id],
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert got["system_version_set"]["system_version_set_id"] == version_set_id

        diffed = _run_cli(
            cli,
            [
                "--api-version",
                "2",
                "system-manifest",
                "diff",
                "--base-system-version-set-id",
                version_set_id,
                "--target-system-version-set-id",
                version_set_id,
            ],
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert diffed["changed"] == []
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        engine.dispose()
