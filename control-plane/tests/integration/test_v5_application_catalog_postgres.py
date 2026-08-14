"""V5-1A/B/C first-case journey: Alembic + PostgreSQL + installed CLI.

The fixture migrates a disposable PostgreSQL database, starts the real API,
and drives the installed ``caseloop`` CLI through manifest import, an
environment-bound Signal and case binding, acceptance proposal, and a
separately reauthenticated owner confirmation. Credential issuance/rotation
stays on the local management entrypoint; it is never exposed as public HTTP.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any, Iterable
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
import sqlalchemy as sa

from app.models import Audit, Base, Event, Outbox
from app.models.v4_tables import (
    AuthorityReceipt,
    PublicCommandIdempotency,
    PublicCredential,
    PublicPrincipal,
    QualityCase,
    SourceConnection,
)
from app.models.v5_tables import (
    AIApplication,
    AcceptanceCriteriaRevision,
    ApplicationCaseBinding,
    DependencyEdge,
    Environment,
    SystemComponent,
    SystemVersionSet,
)
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

pytestmark = pytest.mark.integration

WORKSPACE_ID = "ws_01J00000000000F1"
PROJECT_ID = "proj_01J00000000000F1"
OWNER_PRINCIPAL_ID = "prn_01J00000000000F1"
OPERATOR_PRINCIPAL_ID = "prn_01J00000000000FA"
OPERATOR_SUBJECT = "v5-first-case-e2e-operator"
OWNER_SUBJECT = "v5-first-case-e2e-owner"
INITIAL_CREDENTIAL_ID = "cred_01J00000000000F1"
ROTATED_CREDENTIAL_ID = "cred_01J00000000000F2"
OWNER_CREDENTIAL_ID = "cred_01J00000000000F3"
SOURCE_ID = "src_01J00000000000F1"


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
            "subject": OWNER_SUBJECT,
        },
        "principal": {
            "principal_id": OPERATOR_PRINCIPAL_ID,
            "subject": OPERATOR_SUBJECT,
        },
        "credential": {
            "credential_id": INITIAL_CREDENTIAL_ID,
            "bearer_token": raw_bearer,
            "jti": raw_jti,
            "issued_at": (now - timedelta(minutes=10)).isoformat(),
            "not_before": (now - timedelta(minutes=5)).isoformat(),
            "expires_at": (now + timedelta(days=30)).isoformat(),
        },
        "source": {
            "source_id": SOURCE_ID,
            "connector_kind": "manual",
            "state": "ACTIVE",
            "credential_ref": None,
            "config": {"provider_origin": "https://caseloop.local"},
        },
        "controller": {
            "registration_id": "creg_01J00000000000F1",
            "principal_id": "prn_01J00000000000FB",
        },
        "version_controller": {
            "registration_id": "creg_01J00000000000F2",
            "principal_id": "prn_01J00000000000FC",
        },
        "case_controller": {
            "registration_id": "creg_01J00000000000F3",
            "principal_id": "prn_01J00000000000FD",
        },
        "intake_controllers": {
            "signal": {
                "registration_id": "creg_01J00000000000F4",
                "principal_id": "prn_01J00000000000FE",
            },
            "case": {
                "registration_id": "creg_01J00000000000F5",
                "principal_id": "prn_01J00000000000FF",
            },
            "evidence": {
                "registration_id": "creg_01J00000000000F6",
                "principal_id": "prn_01J00000000000FG",
            },
        },
        "secret_storage_ref": f"keyring://caseloop/test/{WORKSPACE_ID}",
    }


def _manifest_payload(*, resolved_at: datetime) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "application": {
            "project_id": PROJECT_ID,
            "slug": "case-review-assistant",
            "display_name": "Case Review Assistant",
            "owner_principal_ids": [OWNER_PRINCIPAL_ID],
            "criticality": "P0",
            "data_classification": "INTERNAL",
            "governance_mode": "MANAGED",
        },
        "environment": {
            "logical_name": "local",
            "risk_classification": "MEDIUM",
        },
        "components": [
            {
                "logical_name": "control-plane",
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
                    "artifact_refs": [
                        {
                            "kind": "git_commit",
                            "ref": "v5-first-case-integration",
                            "digest": "sha256:" + "b" * 64,
                        }
                    ],
                },
            },
            {
                "logical_name": "model-binding",
                "component_kind": "MODEL_BINDING",
                "owner_principal_ids": [OWNER_PRINCIPAL_ID],
                "criticality": "P1",
                "data_classification": "INTERNAL",
                "permission_classification": "READ_ONLY",
                "effect_classification": "NONE",
                "revision": {
                    "identity_locator": {
                        "type": "provider",
                        "name": "local-model-alias",
                    },
                    "identity_assurance": "MUTABLE_ALIAS",
                    "provider_origin": "https://caseloop.local",
                    "resolved_at": resolved_at.isoformat(),
                },
            },
        ],
        "dependency_edges": [
            {
                "from_component": "control-plane",
                "to_component": "model-binding",
                "relation": "INVOKES",
                "required": True,
            }
        ],
        "approver_policy": {
            "logical_name": "local-approver-policy",
            "component_kind": "POLICY",
            "owner_principal_ids": [OWNER_PRINCIPAL_ID],
            "criticality": "P1",
            "data_classification": "CONFIDENTIAL",
            "permission_classification": "READ_ONLY",
            "effect_classification": "NONE",
            "revision": {
                "identity_locator": {
                    "type": "file",
                    "path": "policies/local-approvers.yaml",
                },
                "identity_assurance": "UNKNOWN",
                "unknown_reason": "integration fixture has no immutable policy source",
            },
        },
    }


def _operator_rotation_payload(
    *,
    imported: dict[str, Any],
    raw_bearer: str,
    raw_jti: str,
    issued_at: datetime,
) -> dict[str, Any]:
    environment = imported["environment"]
    return {
        "schema_version": "1.0",
        "operation": "operator_environment_rotation",
        "workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "principal": {
            "principal_id": OPERATOR_PRINCIPAL_ID,
            "subject": OPERATOR_SUBJECT,
        },
        "previous_credential_id": INITIAL_CREDENTIAL_ID,
        "credential": {
            "credential_id": ROTATED_CREDENTIAL_ID,
            "bearer_token": raw_bearer,
            "jti": raw_jti,
            "issued_at": issued_at.isoformat(),
            "not_before": issued_at.isoformat(),
            "expires_at": (issued_at + timedelta(days=30)).isoformat(),
        },
        "exact_environment_binding": {
            "kind": "ENVIRONMENT",
            "id": environment["environment_id"],
            "revision": environment["record_envelope"]["revision"],
            "digest": environment["record_envelope"]["record_digest"],
        },
        "secret_storage_ref": (
            f"keyring://caseloop/test/{WORKSPACE_ID}/operator-environment"
        ),
    }


def _owner_reauthentication_payload(
    *,
    proposed: dict[str, Any],
    raw_bearer: str,
    raw_jti: str,
    issued_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "operation": "owner_reauthentication",
        "workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "operator_principal_id": OPERATOR_PRINCIPAL_ID,
        "owner_principal": {
            "principal_id": OWNER_PRINCIPAL_ID,
            "subject": OWNER_SUBJECT,
        },
        "credential": {
            "credential_id": OWNER_CREDENTIAL_ID,
            "bearer_token": raw_bearer,
            "jti": raw_jti,
            "issued_at": issued_at.isoformat(),
            "not_before": issued_at.isoformat(),
            "expires_at": (issued_at + timedelta(minutes=30)).isoformat(),
        },
        "exact_proposed_revision_binding": {
            "kind": "ACCEPTANCE_CRITERIA_REVISION",
            "id": proposed["acceptance_criteria_revision_id"],
            "revision": proposed["record_envelope"]["revision"],
            "digest": proposed["record_envelope"]["record_digest"],
        },
        "secret_storage_ref": (
            f"keyring://caseloop/test/{WORKSPACE_ID}/owner-reauthentication"
        ),
    }


def _run_local_bootstrap(
    control_plane_root: Path,
    *,
    env: dict[str, str],
    payload: dict[str, Any],
    guarded_secrets: Iterable[str],
    label: str,
) -> dict[str, Any]:
    completed = _safe_process(
        [sys.executable, "-m", "app.bootstrap.v5_catalog_local"],
        label=label,
        env=env,
        cwd=control_plane_root,
        input_text=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        secrets_to_guard=list(guarded_secrets),
        timeout=30,
    )
    assert completed.returncode == 0
    response = json.loads(completed.stdout.strip())
    assert isinstance(response, dict)
    return response


def _run_cli(
    cli: Path,
    *,
    env: dict[str, str],
    argv: list[str],
    pinned_site_packages: str,
    guarded_secrets: Iterable[str],
) -> dict[str, Any]:
    completed = _safe_process(
        [str(cli), *argv],
        label="caseloop cli",
        env=dict(env, PYTHONPATH=pinned_site_packages),
        cwd=Path.cwd(),
        timeout=30,
        secrets_to_guard=list(guarded_secrets),
    )
    assert completed.returncode == 0
    response = json.loads(completed.stdout.strip())
    assert isinstance(response, dict)
    return response


def _count(session: sa.orm.Session, model: type[object]) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


def test_v5_first_case_installed_cli_real_postgres_loopback(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    control_plane_root = repo_root / "control-plane"
    configured_database = sa.engine.make_url(TEST_DATABASE_URL).database
    if configured_database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "caseloop.integration_reset.refused.v5_exact_database_required"
        )

    initial_bearer = secrets.token_urlsafe(48)
    initial_jti = secrets.token_urlsafe(32)
    public_pepper = secrets.token_urlsafe(48)
    cursor_key = secrets.token_urlsafe(48)
    guarded_secrets = [initial_bearer, initial_jti]
    bootstrap_now = datetime.now(timezone.utc)

    engine = _new_pg_engine()
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        engine.dispose()

        alembic_config = _alembic_config(control_plane_root)
        with patch.object(
            Base.metadata,
            "create_all",
            side_effect=AssertionError("v5.e2e_must_not_use_create_all"),
        ):
            command.upgrade(alembic_config, "head")
        local_head = ScriptDirectory.from_config(alembic_config).get_current_head()
        assert local_head is not None
        with engine.connect() as connection:
            assert (
                connection.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalar_one()
                == local_head
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

            initial = _run_local_bootstrap(
                control_plane_root,
                env=server_env,
                payload=_bootstrap_payload(
                    raw_bearer=initial_bearer,
                    raw_jti=initial_jti,
                    now=bootstrap_now,
                ),
                guarded_secrets=guarded_secrets,
                label="v5 first-case initial bootstrap",
            )
            assert initial["status"] == "CREATED"
            assert len(initial["controllers"]) == 6
            assert initial["principal"]["trust_roles"] == [
                "catalog_admin",
                "integrator",
            ]
            assert initial["owner_principal"]["trust_roles"] == [
                "maintainer",
                "domain_reviewer",
            ]

            cli_env = {
                "CASELOOP_API_URL": base_url,
                "CASELOOP_WORKSPACE_ID": WORKSPACE_ID,
                "CASELOOP_PUBLIC_TOKEN": initial_bearer,
            }
            manifest_file = tmp_path / "v5-first-case-manifest.json"
            manifest_file.write_text(
                json.dumps(
                    _manifest_payload(resolved_at=datetime.now(timezone.utc)),
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            imported = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "system-manifest",
                    "import",
                    "--manifest-file",
                    str(manifest_file),
                    "--idempotency-key",
                    "v5-first-case-import-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            application_id = imported["application"]["application_id"]
            environment_id = imported["environment"]["environment_id"]
            version_set_id = imported["system_version_set"][
                "system_version_set_id"
            ]
            assert imported["idempotency"]["replayed"] is False
            assert imported["bootstrap_attestation"]["attester_trust_role"] == (
                "integrator"
            )

            rotated_bearer = secrets.token_urlsafe(48)
            rotated_jti = secrets.token_urlsafe(32)
            guarded_secrets.extend([rotated_bearer, rotated_jti])
            rotation_request = _operator_rotation_payload(
                imported=imported,
                raw_bearer=rotated_bearer,
                raw_jti=rotated_jti,
                issued_at=datetime.now(timezone.utc),
            )
            rotation = _run_local_bootstrap(
                control_plane_root,
                env=server_env,
                payload=rotation_request,
                guarded_secrets=guarded_secrets,
                label="v5 operator environment rotation",
            )
            assert rotation["status"] == "CREATED"
            assert rotation["previous_credential_id"] == INITIAL_CREDENTIAL_ID
            assert rotation["exact_environment_binding"]["id"] == environment_id
            rotation_replay = _run_local_bootstrap(
                control_plane_root,
                env=server_env,
                payload=rotation_request,
                guarded_secrets=guarded_secrets,
                label="v5 operator environment rotation replay",
            )
            assert rotation_replay["status"] == "REUSED"
            assert (
                rotation_replay["rotation_binding_digest"]
                == rotation["rotation_binding_digest"]
            )
            cli_env["CASELOOP_PUBLIC_TOKEN"] = rotated_bearer

            signal = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "signal",
                    "submit",
                    "--source-id",
                    SOURCE_ID,
                    "--summary",
                    "The bounded tool was not selected",
                    "--body",
                    "Fresh PostgreSQL first-case journey",
                    "--reporter-ref",
                    OPERATOR_SUBJECT,
                    "--project-id",
                    PROJECT_ID,
                    "--environment-id",
                    environment_id,
                    "--privacy",
                    "INTERNAL",
                    "--source-event-id",
                    "v5-first-case-pg-event-0001",
                    "--occurred-at",
                    datetime.now(timezone.utc).isoformat(),
                    "--idempotency-key",
                    "v5-first-case-signal-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            case_id = signal["case"]["case_id"]
            assert signal["case"]["disposition"] == "NEW"

            readiness = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "case",
                    "acceptance-criteria",
                    "get",
                    case_id,
                    "--case-revision",
                    "1",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            exact_case = readiness["exact_case_binding"]
            assert readiness["case_readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"

            bound = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "case",
                    "bind-application",
                    case_id,
                    "--application-id",
                    application_id,
                    "--environment-id",
                    environment_id,
                    "--case-revision",
                    str(exact_case["case_revision"]),
                    "--case-digest",
                    exact_case["case_digest"],
                    "--system-version-set-id",
                    version_set_id,
                    "--idempotency-key",
                    "v5-first-case-bind-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            assert bound["application_case_binding"]["environment_id"] == (
                environment_id
            )
            assert bound["application_case_binding"][
                "declared_system_version_set_binding_or_unknown"
            ]["id"] == version_set_id

            acceptance_json = json.dumps(
                {
                    "acceptance_source": {
                        "kind": "manual",
                        "title": "Wrong tool selected",
                    },
                    "expected_behavior": {
                        "summary": "The bounded tool must be selected."
                    },
                    "applicable_workload_profile": {
                        "name": "local-once",
                        "concurrency": "SINGLE",
                    },
                    "applicable_deployment_profile": {
                        "name": "local-shadow",
                        "kind": "DEVELOPMENT",
                    },
                },
                sort_keys=True,
            )
            proposed_response = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "case",
                    "acceptance-criteria",
                    "propose",
                    case_id,
                    "--case-revision",
                    str(exact_case["case_revision"]),
                    "--case-digest",
                    exact_case["case_digest"],
                    "--acceptance-json",
                    acceptance_json,
                    "--idempotency-key",
                    "v5-first-case-propose-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            proposed = proposed_response["acceptance_criteria_revision"]
            assert proposed["confirmation_status"] == "PROPOSED"

            owner_bearer = secrets.token_urlsafe(48)
            owner_jti = secrets.token_urlsafe(32)
            guarded_secrets.extend([owner_bearer, owner_jti])
            owner_request = _owner_reauthentication_payload(
                proposed=proposed,
                raw_bearer=owner_bearer,
                raw_jti=owner_jti,
                issued_at=datetime.now(timezone.utc),
            )
            owner_reauthentication = _run_local_bootstrap(
                control_plane_root,
                env=server_env,
                payload=owner_request,
                guarded_secrets=guarded_secrets,
                label="v5 owner reauthentication",
            )
            assert owner_reauthentication["status"] == "CREATED"
            assert owner_reauthentication["operator_principal_id"] == (
                OPERATOR_PRINCIPAL_ID
            )
            assert owner_reauthentication["exact_environment_binding"]["id"] == (
                environment_id
            )
            assert owner_reauthentication["credential"]["credential_id"] == (
                OWNER_CREDENTIAL_ID
            )
            cli_env["CASELOOP_PUBLIC_TOKEN"] = owner_bearer

            confirmed_response = _run_cli(
                cli,
                env=cli_env,
                argv=[
                    "--api-version",
                    "2",
                    "case",
                    "acceptance-criteria",
                    "confirm",
                    proposed["acceptance_criteria_revision_id"],
                    "--case-id",
                    case_id,
                    "--case-revision",
                    str(exact_case["case_revision"]),
                    "--proposed-revision-digest",
                    proposed["record_envelope"]["record_digest"],
                    "--confirmation-note",
                    "Independent owner reauthentication completed.",
                    "--idempotency-key",
                    "v5-first-case-confirm-0001",
                ],
                pinned_site_packages=pinned_site_packages,
                guarded_secrets=guarded_secrets,
            )
            confirmed = confirmed_response["acceptance_criteria_revision"]
            assert confirmed["confirmation_status"] == "CONFIRMED"
            assert confirmed["proposer_principal"] == OPERATOR_PRINCIPAL_ID
            assert confirmed["confirmer_principal"] == OWNER_PRINCIPAL_ID
            assert confirmed["reauthentication_credential_binding"][
                "credential_id"
            ] == OWNER_CREDENTIAL_ID

            owner_replay = _run_local_bootstrap(
                control_plane_root,
                env=server_env,
                payload=owner_request,
                guarded_secrets=guarded_secrets,
                label="v5 owner reauthentication replay",
            )
            assert owner_replay["status"] == "REUSED"
            assert (
                owner_replay["issuance_binding_digest"]
                == owner_reauthentication["issuance_binding_digest"]
            )

            session_factory = sa.orm.sessionmaker(
                bind=engine, autoflush=False, autocommit=False
            )
            with session_factory() as session:
                assert _count(session, SourceConnection) == 1
                assert _count(session, AIApplication) == 1
                assert _count(session, Environment) == 1
                assert _count(session, SystemComponent) == 3
                assert _count(session, DependencyEdge) == 1
                assert _count(session, SystemVersionSet) == 1
                assert _count(session, QualityCase) == 1
                assert _count(session, ApplicationCaseBinding) == 1
                assert _count(session, AcceptanceCriteriaRevision) == 2
                assert _count(session, PublicCredential) == 3
                assert _count(session, Event) > 0
                assert _count(session, Outbox) > 0

                operator = session.get(PublicPrincipal, OPERATOR_PRINCIPAL_ID)
                owner = session.get(PublicPrincipal, OWNER_PRINCIPAL_ID)
                initial_credential = session.get(
                    PublicCredential, INITIAL_CREDENTIAL_ID
                )
                rotated_credential = session.get(
                    PublicCredential, ROTATED_CREDENTIAL_ID
                )
                owner_credential = session.get(
                    PublicCredential, OWNER_CREDENTIAL_ID
                )
                assert operator is not None and owner is not None
                assert initial_credential is not None
                assert rotated_credential is not None
                assert owner_credential is not None
                assert operator.trust_roles == ["catalog_admin", "integrator"]
                assert owner.trust_roles == ["maintainer", "domain_reviewer"]
                assert "capabilities:read" in operator.scopes
                assert "capabilities:read" in owner.scopes
                assert operator.environment_ids == [environment_id]
                assert owner.environment_ids == [environment_id]
                assert initial_credential.state == "REVOKED"
                assert initial_credential.environment_ids == []
                assert initial_credential.claims_digest == initial["credential"][
                    "claims_digest"
                ]
                assert rotated_credential.state == "ACTIVE"
                assert rotated_credential.environment_ids == [environment_id]
                assert owner_credential.state == "ACTIVE"
                assert owner_credential.environment_ids == [environment_id]
                assert len(
                    {
                        initial_credential.credential_hash,
                        rotated_credential.credential_hash,
                        owner_credential.credential_hash,
                    }
                ) == 3

                confirmed_row = session.scalar(
                    sa.select(AcceptanceCriteriaRevision).where(
                        AcceptanceCriteriaRevision.confirmation_status == "CONFIRMED"
                    )
                )
                assert confirmed_row is not None
                assert confirmed_row.confirmer_principal == OWNER_PRINCIPAL_ID
                assert confirmed_row.reauthentication_credential_binding is not None
                assert confirmed_row.reauthentication_credential_binding[
                    "credential_id"
                ] == OWNER_CREDENTIAL_ID

                completed_intents = {
                    row.intent
                    for row in session.scalars(
                        sa.select(PublicCommandIdempotency).where(
                            PublicCommandIdempotency.state == "COMPLETED"
                        )
                    ).all()
                }
                assert {
                    "system-manifests.import",
                    "signals.submit",
                    "cases.bind-application",
                    "acceptance-criteria.propose",
                    "acceptance-criteria.confirm",
                }.issubset(completed_intents)
                receipt_kinds = {
                    row.subject_kind
                    for row in session.scalars(sa.select(AuthorityReceipt)).all()
                }
                assert {
                    "AI_APPLICATION",
                    "ENVIRONMENT",
                    "SYSTEM_VERSION_SET",
                    "APPLICATION_CASE_BINDING",
                    "ACCEPTANCE_CRITERIA_REVISION",
                }.issubset(receipt_kinds)

                audit_payload = json.dumps(
                    [
                        {
                            "params_digest": row.params_digest,
                            "evidence_refs": row.evidence_refs,
                        }
                        for row in session.scalars(sa.select(Audit)).all()
                    ],
                    sort_keys=True,
                )
                assert all(secret not in audit_payload for secret in guarded_secrets)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
    finally:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        engine.dispose()
