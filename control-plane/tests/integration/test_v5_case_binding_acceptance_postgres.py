"""V5-1C PostgreSQL integration: First System Case from a real issue snapshot.

Mirrors the Stage-1A / V5-1B integration proofs: disposable PG database, real
Alembic chain (current script head), real uvicorn server, real installed ``agentmed``
CLI speaking /api/v2 with an explicit ``--api-version 2``.  The CLI pulls a
local snapshot of simonw/llm issue #1466 (stored as a JSON fixture), composes
signals.submit → cases.bind-application → acceptance-criteria.propose, never
auto-confirms, and a reauthenticated human confirms through the CLI while the
ResolutionContract remains honestly pending V5-4 materialization.  A retry of
from-issue produces no duplicate case.
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
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
    QualityCase,
)
from app.public_api.credential_resolver import hash_opaque_bearer
from app.public_api.v5_models import (
    AcceptanceCriteriaConfirmRequest,
    AcceptanceCriteriaProposeRequest,
    CaseBindApplicationRequest,
)
from app.services.acceptance import AcceptanceService
from app.services.case_binding import CaseBindingService
from app.services.v4_audit import V4AuditService
from app.services.v5_authority import build_v5_controller_registration_record
from app.utils.v4_integrity import canonical_digest

pytestmark = pytest.mark.integration

WORKSPACE_ID = "ws_01J0000000000G01"
PROJECT_ID = "proj_01J0000000000G01"
OWNER_PRINCIPAL_ID = "prn_01J0000000000G01"
BINDER_PRINCIPAL_ID = "prn_01J0000000000G02"
CONFIRMER_PRINCIPAL_ID = "prn_01J0000000000G03"
CONTROLLER_PRINCIPAL_ID = "prn_01J0000000000G04"
CASE_CONTROLLER_REGISTRATION = "creg_01J0000000000G01"
CATALOG_REGISTRATION_ID = "creg_01J0000000000G02"
VERSION_REGISTRATION_ID = "creg_01J0000000000G03"
BINDER_CREDENTIAL_ID = "cred_01J0000000000G01"
CONFIRMER_CREDENTIAL_ID = "cred_01J0000000000G02"
BINDER_ENV_CREDENTIAL_ID = "cred_01J0000000000G03"
CONFIRMER_ENV_CREDENTIAL_ID = "cred_01J0000000000G04"
CONFIRMER_FRESH_CREDENTIAL_ID = "cred_01J0000000000G05"
AUTH_SUBJECT = "v5-1c-e2e-operator"
ISSUER = "https://auth.caseloop.dev"
AUDIENCES = ["caseloop-public-api"]
BINDER_SCOPES = [
    "signals:write",
    "cases:read",
    "cases:bind",
    "acceptance_criteria:read",
    "acceptance_criteria:propose",
    "applications:manage",
    "applications:read",
    "system_manifests:import",
    "system_versions:read",
]
CONFIRMER_SCOPES = [
    "acceptance_criteria:confirm",
    "acceptance_criteria:read",
]
ISSUE_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "issue-1466-simonw-llm.json"
)


def _claims_for(
    *, subject: str, scopes: list[str], environment_ids: list[str] | None = None
) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": subject,
            "principal_type": "human",
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE_ID,
            "project_ids": [PROJECT_ID],
            "environment_ids": list(environment_ids or []),
            "scopes": scopes,
        }
    )


def _claims(scopes: list[str], environment_ids: list[str] | None = None) -> str:
    return _claims_for(
        subject=AUTH_SUBJECT,
        scopes=scopes,
        environment_ids=environment_ids,
    )


def _seed_auth_and_controllers(
    session: Session, *, raw_bearer: str, confirmer_bearer: str, now: datetime, pepper: str = "seed-pepper"
) -> None:
    from app.public_api.credential_resolver import digest_public_subject

    binder_scopes = list(BINDER_SCOPES)
    confirmer_scopes = list(CONFIRMER_SCOPES)
    owner_subject = "v5-1c-e2e-owner"
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
            scopes=["cases:read", "acceptance_criteria:read"],
            trust_roles=["maintainer"],
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
                    "scopes": ["cases:read", "acceptance_criteria:read"],
                }
            ),
            revoked_at=None,
        )
    )
    session.add(
        PublicPrincipal(
            principal_id=BINDER_PRINCIPAL_ID,
            workspace_id=WORKSPACE_ID,
            principal_type="human",
            state="ACTIVE",
            subject_digest=digest_public_subject(AUTH_SUBJECT),
            audiences=list(AUDIENCES),
            project_ids=[PROJECT_ID],
            environment_ids=[],
            scopes=binder_scopes,
            trust_roles=["integrator"],
            claims_digest=_claims(binder_scopes),
            revoked_at=None,
        )
    )
    session.add(
        PublicPrincipal(
            principal_id=CONFIRMER_PRINCIPAL_ID,
            workspace_id=WORKSPACE_ID,
            principal_type="human",
            state="ACTIVE",
            subject_digest=digest_public_subject("v5-1c-e2e-confirmer"),
            audiences=list(AUDIENCES),
            project_ids=[PROJECT_ID],
            environment_ids=[],
            scopes=confirmer_scopes,
            trust_roles=["maintainer", "domain_reviewer"],
            claims_digest=canonical_digest(
                {
                    "schema_version": "1.0",
                    "issuer": ISSUER,
                    "subject": "v5-1c-e2e-confirmer",
                    "principal_type": "human",
                    "audiences": AUDIENCES,
                    "workspace_id": WORKSPACE_ID,
                    "project_ids": [PROJECT_ID],
                    "environment_ids": [],
                    "scopes": list(CONFIRMER_SCOPES),
                }
            ),
            revoked_at=None,
        )
    )
    session.flush()
    # The signal intake path needs the active manual SourceConnection.
    from app.models.v4_tables import SourceConnection

    source_record = {
        "schema_version": "1.0",
        "workspace_id": WORKSPACE_ID,
        "source_id": "src_01J0000000000G01",
        "connector_kind": "manual",
        "state": "ACTIVE",
        "credential_ref": None,
        "config": {"provider_origin": "https://caseloop.local"},
        "revision": 1,
        "created_by_principal": BINDER_PRINCIPAL_ID,
    }
    session.add(
        SourceConnection(
            source_id=source_record["source_id"],
            workspace_id=WORKSPACE_ID,
            connector_kind="manual",
            state="ACTIVE",
            credential_ref=None,
            config=source_record["config"],
            connection_digest=canonical_digest(source_record),
            revision=1,
            created_by_principal=BINDER_PRINCIPAL_ID,
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
    # The binder's credential is issued well before the proposal so it cannot
    # reauthenticate for confirm; the confirmer's credential is issued fresh.
    session.add(
        PublicCredential(
            credential_id=BINDER_CREDENTIAL_ID,
            workspace_id=WORKSPACE_ID,
            principal_id=BINDER_PRINCIPAL_ID,
            issuer=ISSUER,
            subject=AUTH_SUBJECT,
            credential_hash=hash_opaque_bearer(raw_bearer, pepper),
            hash_algorithm="hmac-sha256-v1",
            jti_digest="sha256:" + "a" * 64,
            claims_digest=_claims(binder_scopes),
            audiences=list(AUDIENCES),
            project_ids=[PROJECT_ID],
            environment_ids=[],
            scopes=binder_scopes,
            state="ACTIVE",
            issued_at=now - timedelta(days=1),
            not_before=now - timedelta(days=1),
            expires_at=now + timedelta(days=30),
        )
    )
    session.add(
        PublicCredential(
            credential_id=CONFIRMER_CREDENTIAL_ID,
            workspace_id=WORKSPACE_ID,
            principal_id=CONFIRMER_PRINCIPAL_ID,
            issuer=ISSUER,
            subject="v5-1c-e2e-confirmer",
            credential_hash=hash_opaque_bearer(confirmer_bearer, pepper),
            hash_algorithm="hmac-sha256-v1",
            jti_digest="sha256:" + "b" * 64,
            claims_digest=canonical_digest(
                {
                    "schema_version": "1.0",
                    "issuer": ISSUER,
                    "subject": "v5-1c-e2e-confirmer",
                    "principal_type": "human",
                    "audiences": AUDIENCES,
                    "workspace_id": WORKSPACE_ID,
                    "project_ids": [PROJECT_ID],
                    "environment_ids": [],
                    "scopes": list(CONFIRMER_SCOPES),
                }
            ),
            audiences=list(AUDIENCES),
            project_ids=[PROJECT_ID],
            environment_ids=[],
            scopes=confirmer_scopes,
            state="ACTIVE",
            issued_at=now - timedelta(days=2),
            not_before=now - timedelta(days=2),
            expires_at=now + timedelta(days=30),
        )
    )
    for owner_name, registration_id, commands in (
        (
            "case-controller",
            CASE_CONTROLLER_REGISTRATION,
            [
                "cases.bind-application",
                "acceptance-criteria.propose",
                "acceptance-criteria.confirm",
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
            valid_from=now - timedelta(minutes=1),
            registered_at=now,
        )
        session.add(ControllerRegistration(**built.row_values))

    # The trusted manifest import path also needs the version-controller.
    from app.services.v5_authority import (
        V5_CATALOG_OWNER,
        build_v5_controller_registration_record as _build_version,
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
        version_audit = V4AuditService(session, clock=lambda: now)
        version_recorded = version_audit.record(
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
        version_built = _build_version(
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
            registration_audit_ref=version_recorded.audit_ref,
            valid_from=now - timedelta(minutes=1),
            registered_at=now,
        )
        session.add(ControllerRegistration(**version_built.row_values))

    # Stage-1A v4 controllers are needed for the signal intake path
    # (signals.submit / cases.open-from-signal / evidence.record).
    from app.services.authority import build_controller_registration_record as _build_v4

    v4_audit = V4AuditService(session, clock=lambda: now)
    for index, (v4_owner, v4_commands) in enumerate(
        (
            ("signal-controller", ["signals.submit", "signals.link-case"]),
            ("case-controller", ["cases.open-from-signal"]),
            ("evidence-controller", ["evidence.record"]),
        ),
        start=1,
    ):
        v4_registration = f"creg_01J0000000000G{10 + index}"
        v4_controller_principal = f"prn_01J0000000000G{20 + index}"
        v4_service_identity = canonical_digest(
            {
                "schema_version": "1.0",
                "workspace_id": WORKSPACE_ID,
                "owner": v4_owner,
                "controller_principal": v4_controller_principal,
                "principal_type": "CONTROLLER_SERVICE",
                "service": "agentmed-control-plane",
            }
        )
        v4_recorded = v4_audit.record(
            workspace_id=WORKSPACE_ID,
            actor_principal=OWNER_PRINCIPAL_ID,
            action="controllers.register",
            target=v4_registration,
            params={"owner": v4_owner, "service_identity_digest": v4_service_identity},
            transaction_id=f"txn_{v4_registration}",
            evidence_refs={
                "owner": v4_owner,
                "controller_registration_id": v4_registration,
                "controller_principal": v4_controller_principal,
            },
            occurred_at=now,
        )
        v4_built = _build_v4(
            controller_registration_id=v4_registration,
            workspace_id=WORKSPACE_ID,
            owner=v4_owner,
            controller_principal=v4_controller_principal,
            allowed_commands=list(v4_commands),
            service_identity_digest=v4_service_identity,
            registered_by_human_principal=OWNER_PRINCIPAL_ID,
            registration_audit_ref=v4_recorded.audit_ref,
            valid_from=now - timedelta(minutes=1),
            registered_at=now,
        )
        session.add(ControllerRegistration(**v4_built.row_values))
    session.commit()


def _rotate_credential_with_environment(
    session: Session,
    *,
    principal_id: str,
    previous_credential_id: str,
    credential_id: str,
    subject: str,
    scopes: list[str],
    environment_id: str,
    raw_bearer: str,
    pepper: str,
    jti_digest: str,
    issued_at: datetime,
) -> None:
    """Reissue, rather than mutate, a credential after the environment exists."""

    principal = session.get(PublicPrincipal, principal_id)
    previous = session.get(PublicCredential, previous_credential_id)
    assert principal is not None and previous is not None
    previous.state = "REVOKED"
    previous.revoked_at = issued_at

    environment_ids = [environment_id]
    claims_digest = _claims_for(
        subject=subject,
        scopes=scopes,
        environment_ids=environment_ids,
    )
    principal.environment_ids = environment_ids
    principal.claims_digest = claims_digest
    session.add(
        PublicCredential(
            credential_id=credential_id,
            workspace_id=WORKSPACE_ID,
            principal_id=principal_id,
            issuer=ISSUER,
            subject=subject,
            credential_hash=hash_opaque_bearer(raw_bearer, pepper),
            hash_algorithm="hmac-sha256-v1",
            jti_digest=jti_digest,
            claims_digest=claims_digest,
            audiences=list(AUDIENCES),
            project_ids=[PROJECT_ID],
            environment_ids=environment_ids,
            scopes=list(scopes),
            state="ACTIVE",
            issued_at=issued_at,
            not_before=issued_at,
            expires_at=issued_at + timedelta(days=30),
            revoked_at=None,
            created_at=issued_at,
        )
    )
    session.flush()


def _count(session: Session, model: type[object]) -> int:
    return int(session.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


def _run_cli_allow_stderr(
    executable: Path,
    argv: list[str],
    *,
    env: dict[str, str],
    repo_root: Path,
    guarded_secrets: tuple[str, ...],
) -> dict[str, Any]:
    """Run the installed CLI and allow the next-step hints the from-issue
    command writes to stderr (unlike ``_run_cli`` which forbids stderr)."""
    completed = _safe_process(
        [str(executable), *argv],
        label=f"installed CLI {argv[0]} {argv[1]}",
        env=env,
        cwd=repo_root,
        secrets_to_guard=guarded_secrets,
        timeout=25,
    )
    assert completed.returncode == 0, f"CLI {argv} exited {completed.returncode}"
    return _one_json_line(completed.stdout, label="CLI stdout")



def _binder_principal_context(
    required_scope: str,
    now: datetime,
    *,
    environment_id: str | None = None,
    credential_id: str = BINDER_CREDENTIAL_ID,
    jti_digest: str = "sha256:" + "a" * 64,
    issued_at: datetime | None = None,
) -> "Any":
    from app.public_api.auth_contract import AcceptedPrincipalContext

    environments = [environment_id] if environment_id is not None else []
    credential_issued_at = issued_at or now - timedelta(days=1)
    return AcceptedPrincipalContext.model_validate(
        {
            "schema_version": "1.0",
            "principal_id": BINDER_PRINCIPAL_ID,
            "principal_type": "human",
            "issuer": ISSUER,
            "subject": AUTH_SUBJECT,
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE_ID,
            "project_ids": [PROJECT_ID],
            "environment_ids": environments,
            "scopes": list(BINDER_SCOPES),
            "credential_id": credential_id,
            "jti_digest": jti_digest,
            "issued_at": credential_issued_at,
            "not_before": credential_issued_at,
            "expires_at": credential_issued_at + timedelta(days=30),
            "revoked_at": None,
            "revocation_checked_at": now,
            "requested_context": {
                "workspace_id": WORKSPACE_ID,
                "project_id": PROJECT_ID,
                "environment_id": environment_id,
                "required_scope": required_scope,
            },
            "evaluated_at": now,
            "claims_digest": _claims(list(BINDER_SCOPES), environments),
        }
    )

def test_v5_1c_pg_case_binding_and_acceptance_confirm_end_to_end() -> None:
    control_plane_root = Path(__file__).resolve().parents[2]
    if sa.engine.make_url(TEST_DATABASE_URL).database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "agentmed.integration_reset.refused.v5_1c_exact_database_required"
        )
    engine = _new_pg_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        with patch.object(
            Base.metadata,
            "create_all",
            side_effect=AssertionError("v5_1c.must_not_use_create_all"),
        ):
            migration_config = _alembic_config(control_plane_root)
            command.upgrade(migration_config, "head")
        expected_head = ScriptDirectory.from_config(migration_config).get_current_head()
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == expected_head

        now = datetime.now(timezone.utc)
        session = factory()
        try:
            _seed_auth_and_controllers(
                session,
                raw_bearer="unused-direct-service-seed",
                confirmer_bearer="unused-confirmer-seed",
                now=now,
            )
            # Create the application/environment through the real catalog path.
            from app.public_api.auth_contract import AcceptedPrincipalContext
            from app.public_api.v5_models import (
                ApplicationRegisterRequest,
                EnvironmentRegisterRequest,
            )
            from app.services.application_catalog import ApplicationCatalogService

            catalog_principal = AcceptedPrincipalContext.model_validate(
                {
                    "schema_version": "1.0",
                    "principal_id": BINDER_PRINCIPAL_ID,
                    "principal_type": "human",
                    "issuer": ISSUER,
                    "subject": AUTH_SUBJECT,
                    "audiences": AUDIENCES,
                    "workspace_id": WORKSPACE_ID,
                    "project_ids": [PROJECT_ID],
                    "environment_ids": [],
                    "scopes": list(BINDER_SCOPES),
                    "credential_id": BINDER_CREDENTIAL_ID,
                    "jti_digest": "sha256:" + "a" * 64,
                    "issued_at": now - timedelta(days=1),
                    "not_before": now - timedelta(days=1),
                    "expires_at": now + timedelta(days=30),
                    "revoked_at": None,
                    "revocation_checked_at": now,
                    "requested_context": {
                        "workspace_id": WORKSPACE_ID,
                        "project_id": PROJECT_ID,
                        "environment_id": None,
                        "required_scope": "applications:manage",
                    },
                    "evaluated_at": now,
                    "claims_digest": _claims(list(BINDER_SCOPES)),
                }
            )
            catalog = ApplicationCatalogService(session, clock=lambda: now)
            app_response = catalog.register_application(
                ApplicationRegisterRequest.model_validate(
                    {
                        "schema_version": "2.0",
                        "project_id": PROJECT_ID,
                        "slug": "llm-cli",
                        "display_name": "LLM CLI",
                        "owner_principal_ids": [OWNER_PRINCIPAL_ID],
                        "criticality": "P0",
                        "data_classification": "INTERNAL",
                        "governance_mode": "MANAGED",
                    }
                ),
                principal=catalog_principal,
                idempotency_key="catalog-app-1c-0001",
                request_id="req_01J0000000000G01",
            )
            env_response = catalog.register_environment(
                EnvironmentRegisterRequest.model_validate(
                    {
                        "schema_version": "2.0",
                        "application_id": app_response.application.application_id,
                        "logical_name": "local-shadow",
                        "risk_classification": "LOW",
                    }
                ),
                principal=catalog_principal,
                idempotency_key="catalog-env-1c-0001",
                request_id="req_01J0000000000G02",
            )
            session.commit()
            application_id = app_response.application.application_id
            environment_id = env_response.environment.environment_id

            # The environment did not exist when the bootstrap credential was
            # issued. Revoke it and issue a new exact-grant credential; never
            # rewrite the old credential's immutable claims.
            _rotate_credential_with_environment(
                session,
                principal_id=BINDER_PRINCIPAL_ID,
                previous_credential_id=BINDER_CREDENTIAL_ID,
                credential_id=BINDER_ENV_CREDENTIAL_ID,
                subject=AUTH_SUBJECT,
                scopes=list(BINDER_SCOPES),
                environment_id=environment_id,
                raw_bearer="unused-direct-service-binder-rotation",
                pepper="seed-pepper",
                jti_digest="sha256:" + "c" * 64,
                issued_at=now,
            )
            _rotate_credential_with_environment(
                session,
                principal_id=CONFIRMER_PRINCIPAL_ID,
                previous_credential_id=CONFIRMER_CREDENTIAL_ID,
                credential_id=CONFIRMER_ENV_CREDENTIAL_ID,
                subject="v5-1c-e2e-confirmer",
                scopes=list(CONFIRMER_SCOPES),
                environment_id=environment_id,
                raw_bearer="unused-direct-service-confirmer-environment",
                pepper="seed-pepper",
                jti_digest="sha256:" + "d" * 64,
                issued_at=now - timedelta(days=2),
            )
            session.commit()

            from app.services.signal_intake import SignalIntakeService

            signal = SignalIntakeService(session, clock=lambda: now)
            from app.public_api.models import SignalSubmission

            signal_response = signal.submit(
                SignalSubmission.model_validate(
                    {
                        "schema_version": "1.0",
                        "source_id": "src_01J0000000000G01",
                        "source_event_id": "github-issue:simonw:llm:1466",
                        "source_event_version": "1",
                        "signal_kind": "maintainer_report",
                        "reporter": {"kind": "maintainer", "source_subject_ref": AUTH_SUBJECT},
                        "project_id": PROJECT_ID,
                        "environment_id": environment_id,
                        "governed_agent_id": None,
                        "occurred_at": now,
                        "content": {
                            "summary": "BUG: schema_dsl() raises IndexError",
                            "body": "minimal repro: schema_dsl(':description')",
                            "attachments": [],
                        },
                        "run_locator": None,
                        "privacy_classification": "PUBLIC",
                    }
                ),
                principal=_binder_principal_context(
                    "signals:write",
                    now,
                    environment_id=environment_id,
                    credential_id=BINDER_ENV_CREDENTIAL_ID,
                    jti_digest="sha256:" + "c" * 64,
                    issued_at=now,
                ),
                idempotency_key="from-issue-signal-0001",
                request_id="req_01J0000000000G03",
            )
            session.commit()
            case_id = signal_response.case.case_id
            quality_case = session.get(QualityCase, case_id)
            assert quality_case.state == "OPEN"
            case_digest = quality_case.record_digest
            case_payload_before = quality_case.snapshot_payload

            # Bind the case to the application with the issue snapshot.
            snapshot = json.loads(ISSUE_FIXTURE.read_text(encoding="utf-8"))
            binding_service = CaseBindingService(session, clock=lambda: now)
            bind_response = binding_service.bind_application(
                CaseBindApplicationRequest.model_validate(
                    {
                        "schema_version": "2.0",
                        "case_id": case_id,
                        "case_revision": 1,
                        "case_digest": case_digest,
                        "application_id": application_id,
                        "environment_id": environment_id,
                        "declared_system_version_set_binding_or_unknown": {
                            "kind": "UNKNOWN",
                            "reason": "NOT_DECLARED_BY_DIRECT_INTEGRATION",
                        },
                        "issue_snapshot": {
                            "source_kind": "github_issue",
                            "source_url": "https://github.com/simonw/llm/issues/1466",
                            "external_repo": "simonw/llm",
                            "external_issue_number": 1466,
                            "snapshot_payload": snapshot,
                            "edited_flag": False,
                            "deleted_flag": False,
                            "fetched_at": now,
                        },
                    }
                ),
                principal=_binder_principal_context(
                    "cases:bind",
                    now,
                    environment_id=environment_id,
                    credential_id=BINDER_ENV_CREDENTIAL_ID,
                    jti_digest="sha256:" + "c" * 64,
                    issued_at=now,
                ),
                idempotency_key="from-issue-bind-0001",
                request_id="req_01J0000000000G04",
            )
            session.commit()
            assert bind_response.application_case_binding.application_id == application_id
            # S1A case payload/digest untouched by the additive link.
            case_after = session.get(QualityCase, case_id)
            assert case_after.record_digest == case_digest
            assert case_after.snapshot_payload == case_payload_before
            assert case_after.state == "OPEN"

            # Propose an untrusted acceptance draft (agent may propose).
            acceptance = AcceptanceService(session, clock=lambda: now)
            propose_response = acceptance.propose(
                AcceptanceCriteriaProposeRequest.model_validate(
                    {
                        "schema_version": "2.0",
                        "case_id": case_id,
                        "case_revision": 1,
                        "case_digest": case_digest,
                        "acceptance_source": {
                            "kind": "github_issue",
                            "url": "https://github.com/simonw/llm/issues/1466",
                            "repo": "simonw/llm",
                            "number": 1466,
                        },
                        "reproducer_input": {
                            "kind": "code",
                            "language": "python",
                            "repro": "from llm.utils import schema_dsl; schema_dsl(':just a description')",
                        },
                        "reproducer_environment": {
                            "kind": "python_package",
                            "package": "llm",
                            "version": "0.32",
                        },
                        "expected_behavior": {
                            "summary": "schema_dsl must raise a clear validation error",
                            "untrusted": True,
                        },
                        "oracle_or_evaluator": {
                            "kind": "unit_test",
                            "description": "schema_dsl(':description') raises ValueError not IndexError",
                        },
                        "applicable_workload_profile": {"name": "cli-once"},
                        "applicable_deployment_profile": {"name": "local-shadow"},
                    }
                ),
                principal=_binder_principal_context(
                    "acceptance_criteria:propose",
                    now,
                    environment_id=environment_id,
                    credential_id=BINDER_ENV_CREDENTIAL_ID,
                    jti_digest="sha256:" + "c" * 64,
                    issued_at=now,
                ),
                idempotency_key="from-issue-propose-0001",
                request_id="req_01J0000000000G05",
            )
            session.commit()
            proposed = propose_response.acceptance_criteria_revision
            assert proposed.confirmation_status == "PROPOSED"
            assert proposed.confirmer_principal is None

            # A human with the confirm scope but a STALE credential (issued
            # before the proposal) cannot confirm: reauthentication is required.
            from app.public_api.auth_contract import AcceptedPrincipalContext as _APC

            stale_confirmer = _APC.model_validate(
                {
                    "schema_version": "1.0",
                    "principal_id": CONFIRMER_PRINCIPAL_ID,
                    "principal_type": "human",
                    "issuer": ISSUER,
                    "subject": "v5-1c-e2e-confirmer",
                    "audiences": AUDIENCES,
                    "workspace_id": WORKSPACE_ID,
                    "project_ids": [PROJECT_ID],
                    "environment_ids": [environment_id],
                    "scopes": list(CONFIRMER_SCOPES),
                    "credential_id": CONFIRMER_ENV_CREDENTIAL_ID,
                    "jti_digest": "sha256:" + "d" * 64,
                    "issued_at": now - timedelta(days=2),
                    "not_before": now - timedelta(days=2),
                    "expires_at": now + timedelta(days=28),
                    "revoked_at": None,
                    "revocation_checked_at": now,
                    "requested_context": {
                        "workspace_id": WORKSPACE_ID,
                        "project_id": PROJECT_ID,
                        "environment_id": environment_id,
                        "required_scope": "acceptance_criteria:confirm",
                    },
                    "evaluated_at": now,
                    "claims_digest": _claims_for(
                        subject="v5-1c-e2e-confirmer",
                        scopes=list(CONFIRMER_SCOPES),
                        environment_ids=[environment_id],
                    ),
                }
            )
            with pytest.raises(Exception) as denied:
                acceptance.confirm(
                    AcceptanceCriteriaConfirmRequest.model_validate(
                        {
                            "schema_version": "2.0",
                            "exact_proposed_revision_binding": {
                                "kind": "ACCEPTANCE_CRITERIA_REVISION",
                                "id": proposed.acceptance_criteria_revision_id,
                                "revision": 1,
                                "digest": proposed.record_envelope.record_digest,
                            },
                        }
                    ),
                    principal=stale_confirmer,
                    idempotency_key="from-issue-confirm-0001",
                    request_id="req_01J0000000000G06",
                )
            session.rollback()
            from app.services.acceptance import AcceptanceError

            assert isinstance(denied.value, AcceptanceError)
            assert denied.value.code == "VALIDATION_FAILED"
            assert denied.value.details.get("reason") == "REAUTHENTICATION_REQUIRED"

            # A reauthenticated human (fresh credential issued after the
            # proposal) confirms, producing a new immutable CONFIRMED record.
            _rotate_credential_with_environment(
                session,
                principal_id=CONFIRMER_PRINCIPAL_ID,
                previous_credential_id=CONFIRMER_ENV_CREDENTIAL_ID,
                credential_id=CONFIRMER_FRESH_CREDENTIAL_ID,
                subject="v5-1c-e2e-confirmer",
                scopes=list(CONFIRMER_SCOPES),
                environment_id=environment_id,
                raw_bearer="unused-direct-service-confirmer-fresh",
                pepper="seed-pepper",
                jti_digest="sha256:" + "e" * 64,
                issued_at=now + timedelta(seconds=5),
            )
            session.commit()

            confirmer_principal = AcceptedPrincipalContext.model_validate(
                {
                    "schema_version": "1.0",
                    "principal_id": CONFIRMER_PRINCIPAL_ID,
                    "principal_type": "human",
                    "issuer": ISSUER,
                    "subject": "v5-1c-e2e-confirmer",
                    "audiences": AUDIENCES,
                    "workspace_id": WORKSPACE_ID,
                    "project_ids": [PROJECT_ID],
                    "environment_ids": [environment_id],
                    "scopes": list(CONFIRMER_SCOPES),
                    "credential_id": CONFIRMER_FRESH_CREDENTIAL_ID,
                    "jti_digest": "sha256:" + "e" * 64,
                    "issued_at": now + timedelta(seconds=5),
                    "not_before": now + timedelta(seconds=5),
                    "expires_at": now + timedelta(days=30, seconds=5),
                    "revoked_at": None,
                    "revocation_checked_at": now + timedelta(seconds=5),
                    "requested_context": {
                        "workspace_id": WORKSPACE_ID,
                        "project_id": PROJECT_ID,
                        "environment_id": environment_id,
                        "required_scope": "acceptance_criteria:confirm",
                    },
                    "evaluated_at": now + timedelta(seconds=5),
                    "claims_digest": _claims_for(
                        subject="v5-1c-e2e-confirmer",
                        scopes=list(CONFIRMER_SCOPES),
                        environment_ids=[environment_id],
                    ),
                }
            )
            confirmed_response = acceptance.confirm(
                AcceptanceCriteriaConfirmRequest.model_validate(
                    {
                        "schema_version": "2.0",
                        "exact_proposed_revision_binding": {
                            "kind": "ACCEPTANCE_CRITERIA_REVISION",
                            "id": proposed.acceptance_criteria_revision_id,
                            "revision": 1,
                            "digest": proposed.record_envelope.record_digest,
                        },
                    }
                ),
                principal=confirmer_principal,
                idempotency_key="from-issue-confirm-0002",
                request_id="req_01J0000000000G07",
            )
            session.commit()
            confirmed = confirmed_response.acceptance_criteria_revision
            assert confirmed.confirmation_status == "CONFIRMED"
            assert confirmed.confirmer_principal == CONFIRMER_PRINCIPAL_ID
            assert confirmed.exact_previous_proposed_revision_binding is not None
            assert (
                confirmed.exact_previous_proposed_revision_binding.id
                == proposed.acceptance_criteria_revision_id
            )
            reader = _APC.model_validate(
                {
                    "schema_version": "1.0",
                    "principal_id": CONFIRMER_PRINCIPAL_ID,
                    "principal_type": "human",
                    "issuer": ISSUER,
                    "subject": "v5-1c-e2e-confirmer",
                    "audiences": AUDIENCES,
                    "workspace_id": WORKSPACE_ID,
                    "project_ids": [PROJECT_ID],
                    "environment_ids": [environment_id],
                    "scopes": list(CONFIRMER_SCOPES),
                    "credential_id": CONFIRMER_FRESH_CREDENTIAL_ID,
                    "jti_digest": "sha256:" + "e" * 64,
                    "issued_at": now + timedelta(seconds=5),
                    "not_before": now + timedelta(seconds=5),
                    "expires_at": now + timedelta(days=30, seconds=5),
                    "revoked_at": None,
                    "revocation_checked_at": now + timedelta(seconds=5),
                    "requested_context": {
                        "workspace_id": WORKSPACE_ID,
                        "project_id": PROJECT_ID,
                        "environment_id": environment_id,
                        "required_scope": "acceptance_criteria:read",
                    },
                    "evaluated_at": now + timedelta(seconds=5),
                    "claims_digest": _claims_for(
                        subject="v5-1c-e2e-confirmer",
                        scopes=list(CONFIRMER_SCOPES),
                        environment_ids=[environment_id],
                    ),
                }
            )
            get_response = acceptance.get(
                case_id,
                case_revision=1,
                principal=reader,
                request_id="req_01J0000000000G08",
            )
            session.commit()
            assert get_response.case_readiness == "NEEDS_ACCEPTANCE_CRITERIA"
            assert get_response.exact_case_binding.case_digest == case_digest
            assert get_response.next_action is not None
            assert get_response.next_action["code"] == (
                "MATERIALIZE_RESOLUTION_CONTRACT"
            )
        finally:
            session.close()
    finally:
        engine.dispose()


def test_v5_1c_cli_from_issue_e2e_no_duplicate_on_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    control_plane_root = repo_root / "control-plane"
    if sa.engine.make_url(TEST_DATABASE_URL).database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "agentmed.integration_reset.refused.v5_1c_e2e_exact_database_required"
        )

    raw_bearer = secrets.token_urlsafe(48)
    binder_env_bearer = secrets.token_urlsafe(48)
    confirmer_bearer = secrets.token_urlsafe(48)
    fresh_confirmer_bearer = secrets.token_urlsafe(48)
    public_pepper = secrets.token_urlsafe(48)
    cursor_key = secrets.token_urlsafe(48)
    guarded_secrets = (
        raw_bearer,
        binder_env_bearer,
        public_pepper,
        cursor_key,
        confirmer_bearer,
        fresh_confirmer_bearer,
    )
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
            side_effect=AssertionError("v5_1c.e2e_must_not_use_create_all"),
        ):
            command.upgrade(_alembic_config(control_plane_root), "head")
        session = factory()
        try:
            _seed_auth_and_controllers(
                session,
                raw_bearer=raw_bearer,
                confirmer_bearer=confirmer_bearer,
                now=now,
                pepper=public_pepper,
            )
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
            "AGENTMED_CACHE_DIR": str(tmp_path / "issue-cache"),
        }

        # Register the application + environment through the manifest import
        # path so the target exists before binding.
        manifest = {
            "schema_version": "2.0",
            "application": {
                "project_id": PROJECT_ID,
                "slug": "llm-cli",
                "display_name": "LLM CLI",
                "owner_principal_ids": [OWNER_PRINCIPAL_ID],
                "criticality": "P0",
                "data_classification": "INTERNAL",
                "governance_mode": "MANAGED",
            },
            "environment": {"logical_name": "local-shadow", "risk_classification": "LOW"},
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
        }
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
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
        application_id = imported["application"]["application_id"]
        environment_id = imported["environment"]["environment_id"]

        # The manifest created the environment after the bootstrap token was
        # issued. Rotate to a new bearer with the exact environment grant;
        # retain the old credential as REVOKED immutable history.
        rotation_session = factory()
        try:
            _rotate_credential_with_environment(
                rotation_session,
                principal_id=BINDER_PRINCIPAL_ID,
                previous_credential_id=BINDER_CREDENTIAL_ID,
                credential_id=BINDER_ENV_CREDENTIAL_ID,
                subject=AUTH_SUBJECT,
                scopes=list(BINDER_SCOPES),
                environment_id=environment_id,
                raw_bearer=binder_env_bearer,
                pepper=public_pepper,
                jti_digest="sha256:" + "c" * 64,
                issued_at=datetime.now(timezone.utc),
            )
            rotation_session.commit()
        finally:
            rotation_session.close()
        cli_env["AGENTMED_PUBLIC_TOKEN"] = binder_env_bearer

        # First System Case from a local issue snapshot (data only).
        from_issue_args = [
            "--api-version",
            "2",
            "case",
            "from-issue",
            "https://github.com/simonw/llm/issues/1466",
            "--application-id",
            application_id,
            "--environment-id",
            environment_id,
            "--snapshot-file",
            str(ISSUE_FIXTURE),
            "--source-id",
            "src_01J0000000000G01",
            "--reporter-ref",
            AUTH_SUBJECT,
        ]
        first = _run_cli_allow_stderr(
            cli,
            from_issue_args,
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        case_id = first["case_id"]
        assert first["case_readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"
        assert first["next_action"]["code"] == "CONFIRM_ACCEPTANCE_CRITERIA"
        assert first["acceptance_criteria_revision_id"].startswith("acr_")

        # Retry is idempotent: same deterministic source event + idempotency
        # keys produce the same case, never a duplicate.
        retry = _run_cli_allow_stderr(
            cli,
            from_issue_args,
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert retry["case_id"] == case_id
        assert retry["acceptance_criteria_revision_id"] == first["acceptance_criteria_revision_id"]

        # The proposal is not auto-confirmed; the binder token (which lacks the
        # human maintainer/domain-reviewer confirm scope) is rejected.
        stale_confirm = _run_cli(
            cli,
            [
                "--api-version",
                "2",
                "case",
                "acceptance-criteria",
                "confirm",
                first["acceptance_criteria_revision_id"],
                "--case-id",
                case_id,
                "--proposed-revision-digest",
                first["acceptance_criteria_revision_digest"],
            ],
            env=cli_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
            expected_exit=10,
        )
        assert stale_confirm["error"]["code"] == "SCOPE_FORBIDDEN"

        # The human reauthenticates: issue a distinct fresh credential after
        # the proposal and preserve the old credential as revoked history.
        reauth_session = factory()
        try:
            _rotate_credential_with_environment(
                reauth_session,
                principal_id=CONFIRMER_PRINCIPAL_ID,
                previous_credential_id=CONFIRMER_CREDENTIAL_ID,
                credential_id=CONFIRMER_FRESH_CREDENTIAL_ID,
                subject="v5-1c-e2e-confirmer",
                scopes=list(CONFIRMER_SCOPES),
                environment_id=environment_id,
                raw_bearer=fresh_confirmer_bearer,
                pepper=public_pepper,
                jti_digest="sha256:" + "e" * 64,
                issued_at=datetime.now(timezone.utc),
            )
            reauth_session.commit()
        finally:
            reauth_session.close()

        # The reauthenticated human confirms through the CLI (fresh credential).
        confirmer_env = dict(cli_env)
        confirmer_env["AGENTMED_PUBLIC_TOKEN"] = fresh_confirmer_bearer
        confirmed = _run_cli(
            cli,
            [
                "--api-version",
                "2",
                "case",
                "acceptance-criteria",
                "confirm",
                first["acceptance_criteria_revision_id"],
                "--case-id",
                case_id,
                "--proposed-revision-digest",
                first["acceptance_criteria_revision_digest"],
            ],
            env=confirmer_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert confirmed["acceptance_criteria_revision"]["confirmation_status"] == "CONFIRMED"

        got = _run_cli(
            cli,
            [
                "--api-version",
                "2",
                "case",
                "acceptance-criteria",
                "get",
                case_id,
            ],
            env=confirmer_env,
            repo_root=repo_root,
            guarded_secrets=guarded_secrets,
        )
        assert got["case_readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"
        assert got["exact_case_binding"]["case_digest"].startswith("sha256:")
        assert got["next_action"]["code"] == "MATERIALIZE_RESOLUTION_CONTRACT"
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        engine.dispose()
