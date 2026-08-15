"""V5-1B /api/v2 system-manifest route tests (TestClient + real resolver)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app
from app.models.v4_tables import PublicCredential
from app.public_api.credential_resolver import hash_opaque_bearer

from test_v5_application_catalog import (
    _claims,
    _seed_principal,
    _seed_v5_controller,
    AUDIENCES,
    CATALOG_PRINCIPAL,
    ISSUER,
    OWNER,
    PROJECT,
    SUBJECT,
    WORKSPACE,
)
from test_v5_system_versions import (
    _seed_version_controller,
    IMPORT_PRINCIPAL,
)

RAW_TOKEN = "route-test-manifest-token-0123456789-abcdef"
PEPPER = "route-test-manifest-pepper"
CURSOR_KEY = "route-test-manifest-cursor"
IMPORT_SCOPES = ["system_manifests:import", "system_versions:read"]


def _manifest_payload() -> dict:
    return {
        "schema_version": "2.0",
        "application": {
            "project_id": PROJECT,
            "slug": "llm-cli",
            "display_name": "LLM CLI",
            "owner_principal_ids": [OWNER],
            "criticality": "P0",
            "data_classification": "INTERNAL",
            "governance_mode": "MANAGED",
        },
        "environment": {"logical_name": "prod", "risk_classification": "MEDIUM"},
        "components": [
            {
                "logical_name": "llm-code",
                "component_kind": "APPLICATION_CODE",
                "owner_principal_ids": [OWNER],
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
        "approver_policy": None,
    }


@pytest.fixture()
def client(sqlite_engine):
    from datetime import datetime

    from app.config import Settings
    from app.services.system_versions import SystemVersionsService

    settings = Settings(
        database_url="sqlite://",
        public_credential_hash_pepper=SecretStr(PEPPER),
        public_cursor_signing_key=SecretStr(CURSOR_KEY),
        public_auth_issuer=ISSUER,
        require_mcp_role_tokens=False,
    )
    _seed_route_identity(sqlite_engine, settings)
    app = create_app(settings=settings, engine=sqlite_engine, create_tables=True)
    fixed_now = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
    app.state.system_versions_service_factory = (
        lambda session: SystemVersionsService(session, clock=lambda: fixed_now)
    )
    context = TestClient(app)
    client = context.__enter__()
    try:
        yield client
    finally:
        context.__exit__(None, None, None)


def _seed_route_identity(engine, settings) -> None:
    from sqlalchemy.orm import Session

    session = Session(engine)
    try:
        _seed_principal(session, principal_id=OWNER, scopes=["signals:write", "cases:read"])
        _seed_principal(
            session,
            principal_id=IMPORT_PRINCIPAL,
            scopes=IMPORT_SCOPES,
        )
        _seed_v5_controller(session)
        _seed_version_controller(session)
        session.add(
            PublicCredential(
                credential_id="cred_01J000000000000A",
                workspace_id=WORKSPACE,
                principal_id=IMPORT_PRINCIPAL,
                issuer=ISSUER,
                subject=SUBJECT,
                credential_hash=hash_opaque_bearer(RAW_TOKEN, PEPPER),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "b" * 64,
                claims_digest=_claims(WORKSPACE, [PROJECT], IMPORT_SCOPES),
                audiences=list(AUDIENCES),
                project_ids=[PROJECT],
                environment_ids=[],
                scopes=IMPORT_SCOPES,
                state="ACTIVE",
                issued_at=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                not_before=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                expires_at=datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc),
            )
        )
        session.commit()
    finally:
        session.close()


def _headers(*, idempotency_key: str | None = "manifest-import-0001") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {RAW_TOKEN}",
        "X-AgentMED-Workspace-ID": WORKSPACE,
        "X-AgentMED-Contract-Version": "2.0",
    }
    if idempotency_key is not None:
        headers["X-AgentMED-Idempotency-Key"] = idempotency_key
    return headers


def test_import_get_diff_roundtrip(client) -> None:
    imported = client.post(
        "/api/v2/system-manifests:import",
        headers=_headers(),
        json=_manifest_payload(),
    )
    assert imported.status_code == 201, imported.text
    body = imported.json()
    assert body["schema_version"] == "2.0"
    assert body["system_assignment"]["transition_kind"] == "BOOTSTRAP"
    assert body["system_assignment"]["generation"] == 1
    assert body["idempotency"]["replayed"] is False
    version_set_id = body["system_version_set"]["system_version_set_id"]
    manifest_digest = body["manifest_digest"]

    got = client.get(
        f"/api/v2/system-versions/{version_set_id}",
        headers=_headers(idempotency_key=None),
    )
    assert got.status_code == 200, got.text
    assert got.json()["system_version_set"]["version_set_digest"] == body["system_version_set"]["version_set_digest"]

    diff = client.get(
        "/api/v2/system-versions:diff",
        params={
            "base_system_version_set_id": version_set_id,
            "target_system_version_set_id": version_set_id,
        },
        headers=_headers(idempotency_key=None),
    )
    assert diff.status_code == 200, diff.text
    diff_body = diff.json()
    assert diff_body["added"] == []
    assert diff_body["removed"] == []
    assert diff_body["changed"] == []

    # same manifest digest under a different key replays the same version set
    replay = client.post(
        "/api/v2/system-manifests:import",
        headers=_headers(idempotency_key="manifest-import-9999"),
        json=_manifest_payload(),
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["idempotency"]["replayed"] is True
    assert replay.json()["system_version_set"]["system_version_set_id"] == version_set_id
    assert replay.json()["manifest_digest"] == manifest_digest


def test_import_requires_idempotency_key(client) -> None:
    response = client.post(
        "/api/v2/system-manifests:import",
        headers=_headers(idempotency_key=None),
        json=_manifest_payload(),
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_import_rejects_scope_missing(client) -> None:
    # A token with only applications:read cannot import (scope not granted).
    from datetime import datetime, timezone

    from sqlalchemy.orm import Session

    from app.models.v4_tables import PublicCredential
    from app.public_api.credential_resolver import hash_opaque_bearer

    session = Session(client.app.state.engine if hasattr(client.app.state, "engine") else None)
    try:
        engine = client.app.state.engine
    except AttributeError:
        engine = None
    session = Session(engine) if engine is not None else Session()
    try:
        session.add(
            PublicCredential(
                credential_id="cred_01J000000000000B",
                workspace_id=WORKSPACE,
                principal_id=CATALOG_PRINCIPAL,
                issuer=ISSUER,
                subject=SUBJECT,
                credential_hash=hash_opaque_bearer("route-token-apps-read-only", PEPPER),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "c" * 64,
                claims_digest=_claims(WORKSPACE, [PROJECT], ["applications:read"]),
                audiences=list(AUDIENCES),
                project_ids=[PROJECT],
                environment_ids=[],
                scopes=["applications:read"],
                state="ACTIVE",
                issued_at=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                not_before=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                expires_at=datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc),
            )
        )
        session.commit()
    finally:
        session.close()

    headers = {
        "Authorization": "Bearer route-token-apps-read-only",
        "X-AgentMED-Workspace-ID": WORKSPACE,
        "X-AgentMED-Contract-Version": "2.0",
        "X-AgentMED-Idempotency-Key": "manifest-import-0001",
    }
    response = client.post("/api/v2/system-manifests:import", headers=headers, json=_manifest_payload())
    # The credential lacks the import scope; the resolver fails closed.
    assert response.status_code == 401
    assert response.json()["error"]["code"] in {"SCOPE_FORBIDDEN", "TOKEN_INVALID"}
