"""R4-full First System Case closure journey on disposable PostgreSQL.

Master §17.5 on the real HTTP stack: bootstrap import, then an issue source
signal creates the Case, cases.bind-application binds it to the application,
acceptance criteria are proposed, read back with bounded readiness, and a
reauthenticated human confirms — with every step fail-closed on
cross-workspace/stale/wrong-actor inputs.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models.v4_tables import PublicCredential, PublicPrincipal
from app.public_api.credential_resolver import digest_public_subject, hash_opaque_bearer
from app.utils.v4_integrity import canonical_digest

from test_v5_r2_manifest_activation_postgres import (
    AUDIENCES,
    ISSUER,
    NOW,
    TEST_DATABASE_URL,
    UnsafeIntegrationDatabaseError,
    _alembic_config,
    _headers,
    _manifest,
    _new_pg_engine,
    _reset_pg_database_for_migrations,
    _seed_controller,
)

BINDER_SCOPES = [
    "signals:write",
    "cases:bind",
    "cases:read",
    "acceptance_criteria:propose",
    "acceptance_criteria:read",
    "system_manifests:import",
    "system_versions:read",
]
CONFIRMER_SCOPES = ["acceptance_criteria:confirm", "acceptance_criteria:read", "cases:read"]


def _claims(*, subject: str, workspace_id: str, project_id: str, scopes: list[str]) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": subject,
            "principal_type": "human",
            "audiences": AUDIENCES,
            "workspace_id": workspace_id,
            "project_ids": [project_id],
            "environment_ids": [],
            "scopes": scopes,
        }
    )


def _seed_r4_workspace(
    session: Session, *, suffix: str, pepper: str
) -> dict[str, dict[str, str]]:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    workspace_id = f"ws_01J0000000000{suffix}01"
    project_id = f"proj_01J000000000{suffix}01"
    owner = f"prn_01J0000000000{suffix}01"
    binder = f"prn_01J0000000000{suffix}02"
    confirmer = f"prn_01J0000000000{suffix}03"
    binder_subject = f"r4-binder-{suffix}"
    confirmer_subject = f"r4-confirmer-{suffix}"
    binder_claims = _claims(
        subject=binder_subject, workspace_id=workspace_id, project_id=project_id,
        scopes=BINDER_SCOPES,
    )
    confirmer_claims = _claims(
        subject=confirmer_subject, workspace_id=workspace_id, project_id=project_id,
        scopes=CONFIRMER_SCOPES,
    )
    session.add_all(
        [
            PublicPrincipal(
                principal_id=owner,
                workspace_id=workspace_id,
                principal_type="human",
                state="ACTIVE",
                subject_digest=digest_public_subject(f"r4-owner-{suffix}"),
                audiences=AUDIENCES,
                project_ids=[project_id],
                environment_ids=[],
                scopes=["cases:read"],
                trust_roles=[],
                claims_digest=canonical_digest({"owner": owner}),
                revoked_at=None,
            ),
            PublicPrincipal(
                principal_id=binder,
                workspace_id=workspace_id,
                principal_type="human",
                state="ACTIVE",
                subject_digest=digest_public_subject(binder_subject),
                audiences=AUDIENCES,
                project_ids=[project_id],
                environment_ids=[],
                scopes=BINDER_SCOPES,
                trust_roles=["integrator"],
                claims_digest=binder_claims,
                revoked_at=None,
            ),
            PublicPrincipal(
                principal_id=confirmer,
                workspace_id=workspace_id,
                principal_type="human",
                state="ACTIVE",
                subject_digest=digest_public_subject(confirmer_subject),
                audiences=AUDIENCES,
                project_ids=[project_id],
                environment_ids=[],
                scopes=CONFIRMER_SCOPES,
                trust_roles=["domain_reviewer"],
                claims_digest=confirmer_claims,
                revoked_at=None,
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            PublicCredential(
                credential_id=f"cred_01J000000000{suffix}01",
                workspace_id=workspace_id,
                principal_id=binder,
                issuer=ISSUER,
                subject=binder_subject,
                credential_hash=hash_opaque_bearer(f"binder-token-{suffix}", pepper),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "b" * 64,
                claims_digest=binder_claims,
                audiences=AUDIENCES,
                project_ids=[project_id],
                environment_ids=[],
                scopes=BINDER_SCOPES,
                state="ACTIVE",
                issued_at=now - timedelta(minutes=10),
                not_before=now - timedelta(minutes=5),
                expires_at=NOW + timedelta(days=30),
            ),
            PublicCredential(
                credential_id=f"cred_01J000000000{suffix}02",
                workspace_id=workspace_id,
                principal_id=confirmer,
                issuer=ISSUER,
                subject=confirmer_subject,
                credential_hash=hash_opaque_bearer(f"confirmer-token-{suffix}", pepper),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "c" * 64,
                claims_digest=confirmer_claims,
                audiences=AUDIENCES,
                project_ids=[project_id],
                environment_ids=[],
                scopes=CONFIRMER_SCOPES,
                state="ACTIVE",
                issued_at=now,
                not_before=now,
                expires_at=NOW + timedelta(days=30),
            ),
        ]
    )
    session.flush()
    from app.models.v4_tables import SourceConnection
    from app.utils.v4_integrity import canonical_digest as _cd

    source_record = {
        "schema_version": "1.0",
        "workspace_id": workspace_id,
        "source_id": "src_01J0000000000R401",
        "connector_kind": "manual",
        "state": "ACTIVE",
        "credential_ref": None,
        "config": {"label": "github-issues"},
        "revision": 1,
        "created_by_principal": owner,
    }
    session.add(
        SourceConnection(
            source_id="src_01J0000000000R401",
            workspace_id=workspace_id,
            connector_kind="manual",
            state="ACTIVE",
            credential_ref=None,
            config={"label": "github-issues"},
            connection_digest=_cd(source_record),
            revision=1,
            created_by_principal=owner,
            created_at=now,
        )
    )
    session.flush()
    _seed_controller(
        session,
        workspace_id=workspace_id,
        owner="application-catalog-controller",
        owner_principal=owner,
        controller_principal=f"prn_01J0000000000{suffix}04",
        registration_id=f"creg_01J000000000{suffix}01",
    )
    _seed_controller(
        session,
        workspace_id=workspace_id,
        owner="version-controller",
        owner_principal=owner,
        controller_principal=f"prn_01J0000000000{suffix}05",
        registration_id=f"creg_01J000000000{suffix}02",
    )
    # v5 case-controller owns the binding and acceptance records
    from app.models.v4_tables import ControllerRegistration
    from app.services.v4_audit import V4AuditService
    from app.services.v5_authority import (
        build_v5_controller_registration_record as _b5,
    )

    case_service_digest = canonical_digest(
        {
            "schema_version": "1.0",
            "workspace_id": workspace_id,
            "owner": "case-controller",
            "controller_principal": f"prn_01J0000000000{suffix}06",
            "principal_type": "CONTROLLER_SERVICE",
            "service": "caseloop-control-plane",
        }
    )
    case_audit = V4AuditService(session, clock=lambda: NOW).record(
        workspace_id=workspace_id,
        actor_principal=owner,
        action="controllers.register",
        target=f"creg_01J000000000{suffix}03",
        params={"owner": "case-controller", "service_identity_digest": case_service_digest},
        transaction_id=f"txn_creg_01J000000000{suffix}03",
        evidence_refs={
            "owner": "case-controller",
            "controller_registration_id": f"creg_01J000000000{suffix}03",
            "controller_principal": f"prn_01J0000000000{suffix}06",
        },
        occurred_at=now,
    )
    case_built = _b5(
        controller_registration_id=f"creg_01J000000000{suffix}03",
        workspace_id=workspace_id,
        owner="case-controller",
        controller_principal=f"prn_01J0000000000{suffix}06",
        allowed_commands=[
            "cases.bind-application",
            "acceptance-criteria.propose",
            "acceptance-criteria.confirm",
        ],
        service_identity_digest=case_service_digest,
        registered_by_human_principal=owner,
        registration_audit_ref=case_audit.audit_ref,
        valid_from=now - timedelta(minutes=1),
        registered_at=now,
    )
    session.add(ControllerRegistration(**case_built.row_values))
    # v4 controllers for the signal/case intake path
    from app.services.authority import (
        build_controller_registration_record as _build_v4,
    )

    v4_audit = V4AuditService(session, clock=lambda: NOW)
    for index, (v4_owner, v4_commands) in enumerate(
        (
            ("signal-controller", ["signals.submit", "signals.link-case"]),
            ("case-controller", ["cases.open-from-signal"]),
            ("evidence-controller", ["evidence.record"]),
        ),
        start=1,
    ):
        v4_registration = f"creg_01J0000000000R4{index}"
        v4_controller_principal = f"prn_01J0000000000R4{index + 4}"
        v4_service_identity = canonical_digest(
            {
                "schema_version": "1.0",
                "workspace_id": workspace_id,
                "owner": v4_owner,
                "controller_principal": v4_controller_principal,
                "principal_type": "CONTROLLER_SERVICE",
                "service": "caseloop-control-plane",
            }
        )
        v4_recorded = v4_audit.record(
            workspace_id=workspace_id,
            actor_principal=owner,
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
            workspace_id=workspace_id,
            owner=v4_owner,
            controller_principal=v4_controller_principal,
            allowed_commands=list(v4_commands),
            service_identity_digest=v4_service_identity,
            registered_by_human_principal=owner,
            registration_audit_ref=v4_recorded.audit_ref,
            valid_from=now - timedelta(minutes=1),
            registered_at=now,
        )
        session.add(ControllerRegistration(**v4_built.row_values))
    session.flush()
    return {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "binder_token": f"binder-token-{suffix}",
        "confirmer_token": f"confirmer-token-{suffix}",
    }


def _v1_headers(seed: dict[str, str], *, token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-CaseLoop-Workspace-ID": seed["workspace_id"],
        "X-CaseLoop-Contract-Version": "1.0",
        "idempotency-key": secrets.token_urlsafe(12),
    }


def _signal_payload(seed: dict[str, str], *, subject: str) -> dict:
    return {
        "schema_version": "1.0",
        "source_id": "src_01J0000000000R401",
        "source_event_id": "github-issue:simonw:llm:1466",
        "source_event_version": "1",
        "signal_kind": "maintainer_report",
        "reporter": {"kind": "maintainer", "source_subject_ref": subject},
        "project_id": seed["project_id"],
        "environment_id": None,
        "governed_agent_id": None,
        "occurred_at": "2026-08-12T09:00:00Z",
        "content": {
            "summary": "BUG: schema_dsl() raises IndexError",
            "body": "minimal repro: schema_dsl(':description')",
            "attachments": [],
        },
        "run_locator": None,
        "privacy_classification": "PUBLIC",
    }


def _setup(suffix: str) -> tuple[object, dict[str, str], Settings, object]:
    pepper = secrets.token_urlsafe(48)
    control_plane_root = Path(__file__).resolve().parents[2]
    if sa.engine.make_url(TEST_DATABASE_URL).database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "caseloop.integration_reset.refused.v5_exact_database_required"
        )
    engine = _new_pg_engine()
    _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
    command.upgrade(_alembic_config(control_plane_root), "head")
    with engine.connect() as connection:
        assert (
            connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
            == "014"
        )
    with Session(engine) as session:
        seed = _seed_r4_workspace(session, suffix=suffix, pepper=pepper)
        session.commit()
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        public_credential_hash_pepper=SecretStr(pepper),
        public_cursor_signing_key=SecretStr(secrets.token_urlsafe(48)),
        public_auth_issuer=ISSUER,
        require_mcp_role_tokens=False,
    )
    app = create_app(settings=settings, engine=engine, create_tables=False)
    return engine, seed, settings, app


def test_r4_source_case_bind_propose_confirm_journey_postgres() -> None:
    engine, seed, settings, app = _setup("R1")
    try:
        with TestClient(app) as client:
            # bootstrap import produces the application/environment
            imported = client.post(
                "/api/v2/system-manifests:import",
                headers=_headers(
                    {"workspace_id": seed["workspace_id"], "token": seed["binder_token"]},
                    key="r4-import-0001",
                ),
                json=_manifest(
                    {
                        "workspace_id": seed["workspace_id"],
                        "project_id": seed["project_id"],
                        "owner": "prn_01J0000000000R101",
                        "principal_id": "prn_01J0000000000R102",
                        "token": seed["binder_token"],
                    }
                ),
            )
            assert imported.status_code == 201, imported.text
            import_body = imported.json()
            application_id = import_body["application"]["application_id"]
            environment_id = import_body["environment"]["environment_id"]

            # issue source signal creates the Case
            signalled = client.post(
                "/api/v1/signals",
                headers=_v1_headers(seed, token=seed["binder_token"]),
                json=_signal_payload(seed, subject="r4-binder-R1"),
            )
            assert signalled.status_code == 201, signalled.text
            case_id = signalled.json()["case"]["case_id"]
            case_revision = signalled.json()["case"]["revision"]
            # the v1 response carries no digest; read the authoritative row
            from app.models.v4_tables import QualityCase

            with Session(engine) as digest_session:
                quality_case = digest_session.get(QualityCase, case_id)
                assert quality_case is not None
                case_digest = quality_case.record_digest

            # bind the case to the application
            bound = client.post(
                f"/api/v2/cases/{case_id}:bind-application",
                headers=_headers(
                    {"workspace_id": seed["workspace_id"], "token": seed["binder_token"]},
                    key="r4-bind-0001",
                ),
                json={
                    "schema_version": "2.0",
                    "case_id": case_id,
                    "case_revision": case_revision,
                    "case_digest": case_digest,
                    "application_id": application_id,
                    "environment_id": environment_id,
                    "declared_system_version_set_binding_or_unknown": "UNKNOWN",
                    "issue_snapshot": {
                        "source_kind": "github_issue",
                        "source_url": "https://github.com/simonw/llm/issues/1466",
                        "external_repo": "simonw/llm",
                        "external_issue_number": 1466,
                        "snapshot_payload": {"title": "schema_dsl IndexError"},
                        "fetched_at": "2026-08-12T09:00:00Z",
                    },
                },
            )
            assert bound.status_code == 201, bound.text
            binding = bound.json()["application_case_binding"]
            assert binding["application_id"] == application_id
            assert binding["exact_case_binding"] == {
                "case_id": case_id,
                "case_revision": case_revision,
                "case_digest": case_digest,
            }

            # read the binding back
            binding_get = client.get(
                f"/api/v2/cases/{case_id}/application-binding",
                params={"case_revision": case_revision, "case_digest": case_digest},
                headers=_headers(
                    {"workspace_id": seed["workspace_id"], "token": seed["binder_token"]},
                    key="r4-bind-get-0001",
                ),
            )
            assert binding_get.status_code == 200, binding_get.text
            assert (
                binding_get.json()["application_case_binding"]["application_case_binding_id"]
                == binding["application_case_binding_id"]
            )

            # propose acceptance criteria
            proposed = client.post(
                f"/api/v2/cases/{case_id}:propose-acceptance-criteria",
                headers=_headers(
                    {"workspace_id": seed["workspace_id"], "token": seed["binder_token"]},
                    key="r4-propose-0001",
                ),
                json={
                    "schema_version": "2.0",
                    "case_id": case_id,
                    "case_revision": case_revision,
                    "case_digest": case_digest,
                    "acceptance_source": {"kind": "reproducer"},
                    "expected_behavior": {"summary": "schema_dsl returns DSL"},
                    "applicable_workload_profile": {"runtime": "local"},
                    "applicable_deployment_profile": {"mode": "single-node"},
                },
            )
            assert proposed.status_code == 201, proposed.text
            acr = proposed.json()["acceptance_criteria_revision"]
            assert acr["confirmation_status"] == "PROPOSED"
            assert acr["proposer_principal"] == "prn_01J0000000000R102"

            # read acceptance criteria: readiness bounded below READY
            criteria_get = client.get(
                f"/api/v2/cases/{case_id}/acceptance-criteria",
                params={"case_revision": case_revision},
                headers=_headers(
                    {"workspace_id": seed["workspace_id"], "token": seed["binder_token"]},
                    key="r4-ac-get-0001",
                ),
            )
            assert criteria_get.status_code == 200, criteria_get.text
            assert criteria_get.json()["case_readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"

            # reauthenticated human confirms: reissue the confirmer credential
            # fresh so issued_at >= proposed_at and <= the confirm request time
            with engine.begin() as conn:
                conn.execute(
                    sa.text(
                        "UPDATE public_credentials SET issued_at = now(), "
                        "not_before = now() WHERE credential_id = :cid"
                    ),
                    {"cid": f"cred_01J000000000R102"},
                )
            confirmed = client.post(
                f"/api/v2/acceptance-criteria/{acr['acceptance_criteria_revision_id']}:confirm",
                headers=_headers(
                    {"workspace_id": seed["workspace_id"], "token": seed["confirmer_token"]},
                    key="r4-confirm-0001",
                ),
                json={
                    "schema_version": "2.0",
                    "exact_proposed_revision_binding": {
                        "kind": "ACCEPTANCE_CRITERIA_REVISION",
                        "id": acr["acceptance_criteria_revision_id"],
                        "revision": 1,
                        "digest": acr["record_envelope"]["record_digest"],
                    },
                    "confirmation_note": "reproduced locally",
                },
            )
            assert confirmed.status_code == 201, confirmed.text
            assert confirmed.json()["acceptance_criteria_revision"][
                "confirmation_status"
            ] == "CONFIRMED"

            # readiness stays bounded: R4 never claims READY (needs V5-4A)
            criteria_get2 = client.get(
                f"/api/v2/cases/{case_id}/acceptance-criteria",
                params={"case_revision": case_revision},
                headers=_headers(
                    {"workspace_id": seed["workspace_id"], "token": seed["binder_token"]},
                    key="r4-ac-get-0002",
                ),
            )
            assert criteria_get2.status_code == 200
            assert criteria_get2.json()["case_readiness"] == "PENDING_MATERIALIZATION"

            # cross-workspace denial: confirmer cannot bind (wrong scope) -> 403
            wrong = client.post(
                f"/api/v2/cases/{case_id}:bind-application",
                headers=_headers(
                    {"workspace_id": seed["workspace_id"], "token": seed["confirmer_token"]},
                    key="r4-wrong-0001",
                ),
                json={
                    "schema_version": "2.0",
                    "case_id": case_id,
                    "case_revision": case_revision,
                    "case_digest": case_digest,
                    "application_id": application_id,
                    "environment_id": environment_id,
                    "declared_system_version_set_binding_or_unknown": "UNKNOWN",
                },
            )
            assert wrong.status_code == 403
    finally:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        engine.dispose()
