"""R3-full second-VersionSet journey on disposable PostgreSQL.

Master §17.4 runtime acceptance on the real stack: one-shot bootstrap, then a
standalone ``system-versions.record`` creating the second immutable version
set, GET of both, a non-trivial deterministic diff, same-key replay with zero
new facts, tamper rejection fail-closed, and concurrent CAS with exactly one
canonical winner.
"""
from __future__ import annotations

import secrets
import threading
from collections import Counter
from datetime import timedelta, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models.v5_tables import SystemVersionSet
from app.public_api.credential_resolver import digest_public_subject, hash_opaque_bearer
from app.models.v4_tables import PublicCredential, PublicPrincipal
from app.utils.v4_integrity import canonical_digest
from app.services.system_versions import SystemVersionsService

from test_v5_r2_manifest_activation_postgres import (
    AUDIENCES,
    ISSUER,
    NOW,
    TEST_DATABASE_URL,
    UnsafeIntegrationDatabaseError,
    _alembic_config,
    _claims,
    _controller_commands,
    _headers,
    _manifest,
    _new_pg_engine,
    _reset_pg_database_for_migrations,
    _seed_controller,
)

SCOPES_R3 = [
    "system_manifests:import",
    "system_versions:read",
    "system_versions:record",
    "applications:manage",
    "applications:read",
]


def _claims_r3(*, subject: str, workspace_id: str, project_id: str) -> str:
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
            "scopes": SCOPES_R3,
        }
    )


def _seed_workspace_r3(
    session: Session,
    *,
    suffix: str,
    raw_token: str,
    pepper: str,
) -> dict[str, str]:
    workspace_id = f"ws_01J0000000000{suffix}01"
    project_id = f"proj_01J000000000{suffix}01"
    owner = f"prn_01J0000000000{suffix}01"
    principal_id = f"prn_01J0000000000{suffix}02"
    subject = f"r3-importer-{suffix}"
    claims = _claims_r3(subject=subject, workspace_id=workspace_id, project_id=project_id)
    session.add_all(
        [
            PublicPrincipal(
                principal_id=owner,
                workspace_id=workspace_id,
                principal_type="human",
                state="ACTIVE",
                subject_digest=digest_public_subject(f"r3-owner-{suffix}"),
                audiences=AUDIENCES,
                project_ids=[project_id],
                environment_ids=[],
                scopes=["cases:read"],
                trust_roles=[],
                claims_digest=canonical_digest({"owner": owner}),
                revoked_at=None,
            ),
            PublicPrincipal(
                principal_id=principal_id,
                workspace_id=workspace_id,
                principal_type="human",
                state="ACTIVE",
                subject_digest=digest_public_subject(subject),
                audiences=AUDIENCES,
                project_ids=[project_id],
                environment_ids=[],
                scopes=SCOPES_R3,
                trust_roles=["integrator"],
                claims_digest=claims,
                revoked_at=None,
            ),
        ]
    )
    session.flush()
    session.add(
        PublicCredential(
            credential_id=f"cred_01J000000000{suffix}01",
            workspace_id=workspace_id,
            principal_id=principal_id,
            issuer=ISSUER,
            subject=subject,
            credential_hash=hash_opaque_bearer(raw_token, pepper),
            hash_algorithm="hmac-sha256-v1",
            jti_digest="sha256:" + suffix.lower()[0] * 64,
            claims_digest=claims,
            audiences=AUDIENCES,
            project_ids=[project_id],
            environment_ids=[],
            scopes=SCOPES_R3,
            state="ACTIVE",
            issued_at=NOW - timedelta(minutes=10),
            not_before=NOW - timedelta(minutes=5),
            expires_at=NOW + timedelta(days=30),
        )
    )
    session.flush()
    _seed_controller(
        session,
        workspace_id=workspace_id,
        owner="application-catalog-controller",
        owner_principal=owner,
        controller_principal=f"prn_01J0000000000{suffix}03",
        registration_id=f"creg_01J000000000{suffix}01",
    )
    _seed_controller(
        session,
        workspace_id=workspace_id,
        owner="version-controller",
        owner_principal=owner,
        controller_principal=f"prn_01J0000000000{suffix}04",
        registration_id=f"creg_01J000000000{suffix}02",
    )
    session.flush()
    return {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "owner": owner,
        "principal_id": principal_id,
        "token": raw_token,
    }


def _record_payload(import_body: dict, *, subset: bool) -> dict:
    version_set = import_body["system_version_set"]
    revisions = version_set["exact_component_revision_bindings"]
    if subset:
        revisions = revisions[:1]
    return {
        "schema_version": "2.0",
        "application_id": import_body["application"]["application_id"],
        "environment_id": import_body["environment"]["environment_id"],
        "exact_component_revision_bindings": revisions,
        "exact_topology_revision_binding": version_set["exact_topology_revision_binding"],
        "exact_previous_system_version_set_binding_or_null": {
            "kind": "SYSTEM_VERSION_SET",
            "id": version_set["system_version_set_id"],
            "revision": 1,
            "digest": version_set["record_envelope"]["record_digest"],
        },
    }


def _setup(pepper: str, suffix: str) -> tuple[Settings, dict[str, str]]:
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
        seed = _seed_workspace_r3(
            session, suffix=suffix, raw_token=secrets.token_urlsafe(48), pepper=pepper
        )
        session.commit()
    return engine, seed


def test_r3_second_version_set_journey_postgres() -> None:
    pepper = secrets.token_urlsafe(48)
    engine, seed = _setup(pepper, "A1")
    try:
        with TestClient(create_app_from_engine(engine, seed, pepper)) as client:
            imported = client.post(
                "/api/v2/system-manifests:import",
                headers=_headers(seed, key="r3-import-0001"),
                json=_manifest(seed),
            )
            assert imported.status_code == 201, imported.text
            import_body = imported.json()
            base_id = import_body["system_version_set"]["system_version_set_id"]

            recorded = client.post(
                "/api/v2/system-versions",
                headers=_headers(seed, key="r3-record-0001"),
                json=_record_payload(import_body, subset=True),
            )
            assert recorded.status_code == 201, recorded.text
            second = recorded.json()
            second_id = second["system_version_set"]["system_version_set_id"]
            assert second_id != base_id
            assert second["system_version_set"]["record_envelope"]["revision"] == 1
            assert second["system_version_set"][
                "exact_previous_system_version_set_binding_or_null"
            ] == {
                "kind": "SYSTEM_VERSION_SET",
                "id": base_id,
                "revision": 1,
                "digest": import_body["system_version_set"]["record_envelope"][
                    "record_digest"
                ],
            }
            assert second["idempotency"]["replayed"] is False

            # GET both
            for version_id in (base_id, second_id):
                got = client.get(
                    f"/api/v2/system-versions/{version_id}",
                    headers=_headers(seed, key="r3-get-0001"),
                )
                assert got.status_code == 200, got.text
                assert got.json()["system_version_set"]["system_version_set_id"] == version_id

            # non-trivial deterministic diff
            diff = client.get(
                "/api/v2/system-versions:diff",
                params={
                    "source_version_set_id": base_id,
                    "target_version_set_id": second_id,
                },
                headers=_headers(seed, key="r3-diff-0001"),
            )
            assert diff.status_code == 200, diff.text
            diff_body = diff.json()["diff"]
            assert diff_body["deterministic"] is True
            removed_ids = {b["id"] for b in diff_body["removed"]}
            assert removed_ids == {
                import_body["system_version_set"]["exact_component_revision_bindings"][
                    1
                ]["id"]
            }
            assert len(diff_body["added"]) == 0
            assert diff_body["changed"] == []
            assert diff_body["topology_changes"] == []
            # the removed component's identity assurance disappears with it
            changes = diff_body["assurance_delta"]["identity_assurance_changes"]
            assert len(changes) == len(diff_body["removed"])
            assert all(change.endswith("-> None") for change in changes)

            # same-key replay: identical terminal response, zero new facts
            replayed = client.post(
                "/api/v2/system-versions",
                headers=_headers(seed, key="r3-record-0001"),
                json=_record_payload(import_body, subset=True),
            )
            assert replayed.status_code == 201, replayed.text
            assert replayed.json()["idempotency"]["replayed"] is True
            assert (
                replayed.json()["system_version_set"]["system_version_set_id"]
                == second_id
            )
    finally:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        engine.dispose()


def test_r3_concurrent_record_cas_exactly_one_winner_postgres() -> None:
    pepper = secrets.token_urlsafe(48)
    engine, seed = _setup(pepper, "B1")
    try:
        with TestClient(create_app_from_engine(engine, seed, pepper)) as client:
            imported = client.post(
                "/api/v2/system-manifests:import",
                headers=_headers(seed, key="r3-import-0002"),
                json=_manifest(seed),
            )
            assert imported.status_code == 201, imported.text
            import_body = imported.json()
            # subset differs from the bootstrap digest so the second version
            # set is a real new record (full bindings would collide with the
            # bootstrap digest unique constraint).
            payload = _record_payload(import_body, subset=True)

            barrier = threading.Barrier(2)
            results: list[tuple[int, dict]] = []
            lock = threading.Lock()

            def record_once(key: str) -> None:
                barrier.wait(timeout=10)
                response = client.post(
                    "/api/v2/system-versions",
                    headers=_headers(seed, key=key),
                    json=payload,
                )
                with lock:
                    results.append((response.status_code, response.json()))

            threads = [
                threading.Thread(target=record_once, args=(f"r3-race-{i}",))
                for i in range(2)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            codes = Counter(code for code, _ in results)
            assert codes[201] == 1, results
            assert codes[409] == 1, results
            # exactly one second version set row
            with Session(engine) as session:
                count = session.scalar(
                    sa.select(sa.func.count())
                    .select_from(SystemVersionSet)
                    .where(SystemVersionSet.workspace_id == seed["workspace_id"])
                )
                assert count == 2
    finally:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        engine.dispose()


def test_r3_tampered_version_set_fails_closed_postgres() -> None:
    pepper = secrets.token_urlsafe(48)
    engine, seed = _setup(pepper, "C1")
    try:
        with TestClient(create_app_from_engine(engine, seed, pepper)) as client:
            imported = client.post(
                "/api/v2/system-manifests:import",
                headers=_headers(seed, key="r3-import-0003"),
                json=_manifest(seed),
            )
            assert imported.status_code == 201, imported.text
            import_body = imported.json()
            base_id = import_body["system_version_set"]["system_version_set_id"]

            # tamper: rewrite the persisted record digest via raw SQL
            # (the ORM immutable guard correctly blocks in-place updates,
            # mirroring how a tampered database would look)
            with engine.begin() as connection:
                connection.execute(
                    sa.text(
                        "UPDATE system_version_sets SET record_digest = :digest "
                        "WHERE system_version_set_id = :id"
                    ),
                    {"digest": "sha256:" + "0" * 64, "id": base_id},
                )

            got = client.get(
                f"/api/v2/system-versions/{base_id}",
                headers=_headers(seed, key="r3-tamper-0001"),
            )
            assert got.status_code == 500
            assert got.json()["error"]["code"] == "INTERNAL_ERROR"
    finally:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        engine.dispose()


def create_app_from_engine(engine, seed, pepper):
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        public_credential_hash_pepper=SecretStr(pepper),
        public_cursor_signing_key=SecretStr(secrets.token_urlsafe(48)),
        public_auth_issuer=ISSUER,
        require_mcp_role_tokens=False,
    )
    app = create_app(settings=settings, engine=engine, create_tables=False)
    app.state.system_versions_service_factory = lambda session: SystemVersionsService(
        session, clock=lambda: NOW
    )
    return app
