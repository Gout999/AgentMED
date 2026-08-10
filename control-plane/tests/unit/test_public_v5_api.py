"""V5-1A /api/v2 public route tests (TestClient + real credential resolver)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app
from app.models.v4_tables import PublicCredential
from app.public_api.credential_resolver import hash_opaque_bearer

from test_v5_application_catalog import (
    _FIXTURE,
    _claims,
    _seed_principal,
    _seed_v5_controller,
    AUDIENCES,
    CATALOG_PRINCIPAL,
    ISSUER,
    OTHER_PROJECT,
    OWNER,
    PROJECT,
    SUBJECT,
    WORKSPACE,
)

RAW_TOKEN = "route-test-catalog-token-0123456789-abcdef"
FOREIGN_READER_TOKEN = "route-test-foreign-reader-token-0123456789"
PEPPER = "route-test-catalog-pepper"
CURSOR_KEY = "route-test-catalog-cursor"


@pytest.fixture()
def client(sqlite_engine):
    from datetime import datetime

    from app.config import Settings
    from app.services.application_catalog import ApplicationCatalogService

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
    app.state.application_catalog_service_factory = (
        lambda session: ApplicationCatalogService(session, clock=lambda: fixed_now)
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
        _seed_principal(session, principal_id=CATALOG_PRINCIPAL)
        _seed_v5_controller(session)
        session.add(
            PublicCredential(
                credential_id="cred_01J000000000000A",
                workspace_id=WORKSPACE,
                principal_id=CATALOG_PRINCIPAL,
                issuer=ISSUER,
                subject=SUBJECT,
                credential_hash=hash_opaque_bearer(RAW_TOKEN, PEPPER),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "b" * 64,
                claims_digest=_claims(WORKSPACE, [PROJECT], ["applications:manage", "applications:read"]),
                audiences=list(AUDIENCES),
                project_ids=[PROJECT],
                environment_ids=[],
                scopes=["applications:manage", "applications:read"],
                state="ACTIVE",
                issued_at=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                not_before=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                expires_at=datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc),
                revoked_at=None,
            )
        )
        # A same-workspace reader granted only OTHER_PROJECT (cross-project
        # visibility probe for applications.get).
        foreign_reader_id = "prn_01J00000000000F2"
        _seed_principal(
            session,
            principal_id=foreign_reader_id,
            scopes=["applications:read"],
            project_ids=[OTHER_PROJECT],
        )
        session.add(
            PublicCredential(
                credential_id="cred_01J000000000000B",
                workspace_id=WORKSPACE,
                principal_id=foreign_reader_id,
                issuer=ISSUER,
                subject=SUBJECT,
                credential_hash=hash_opaque_bearer(FOREIGN_READER_TOKEN, PEPPER),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "c" * 64,
                claims_digest=_claims(WORKSPACE, [OTHER_PROJECT], ["applications:read"]),
                audiences=list(AUDIENCES),
                project_ids=[OTHER_PROJECT],
                environment_ids=[],
                scopes=["applications:read"],
                state="ACTIVE",
                issued_at=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                not_before=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                expires_at=datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc),
                revoked_at=None,
            )
        )
        session.commit()
    finally:
        session.close()


def _headers(*, mutation: bool = False, contract_version: str = "2.0") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {RAW_TOKEN}",
        "X-CaseLoop-Workspace-ID": WORKSPACE,
        "X-CaseLoop-Contract-Version": contract_version,
        "X-Request-ID": "req_01J000000000000A",
        "Content-Type": "application/json",
    }
    if mutation:
        headers["X-CaseLoop-Idempotency-Key"] = "app-register-0001"
    return headers


def _app_body() -> bytes:
    return json.dumps(_FIXTURE["applications"][0]["register_request"]).encode("utf-8")


def test_register_application_v2_success(client) -> None:
    response = client.post(
        "/api/v2/applications",
        headers=_headers(mutation=True),
        content=_app_body(),
    )
    assert response.status_code == 201
    assert response.headers["x-caseloop-contract-version"] == "2.0"
    body = response.json()
    assert body["schema_version"] == "2.0"
    assert body["workspace_id"] == WORKSPACE
    assert body["application"]["record_envelope"]["immutable"] is True
    assert body["idempotency"]["replayed"] is False
    assert body["idempotency"]["receipt"]["intent"] == "applications.register"


def test_register_application_replay_returns_same_record(client) -> None:
    first = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    assert first.status_code == 201
    replay = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    assert replay.status_code == 201
    assert replay.json()["idempotency"]["replayed"] is True
    normalized = dict(replay.json())
    normalized["idempotency"]["replayed"] = False
    assert normalized == first.json()


def test_get_application_v2(client) -> None:
    registered = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    application_id = registered.json()["application"]["application_id"]
    response = client.get(
        f"/api/v2/applications/{application_id}", headers=_headers()
    )
    assert response.status_code == 200
    assert response.json()["application"]["application_id"] == application_id


def test_get_application_cross_project_is_opaque_not_found(client) -> None:
    """applications.get visibility: same workspace, other-project grant -> 404."""
    registered = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    application_id = registered.json()["application"]["application_id"]
    foreign_headers = {
        "Authorization": f"Bearer {FOREIGN_READER_TOKEN}",
        "X-CaseLoop-Workspace-ID": WORKSPACE,
        "X-CaseLoop-Contract-Version": "2.0",
        "X-Request-ID": "req_01J000000000000D",
        "Content-Type": "application/json",
    }
    response = client.get(
        f"/api/v2/applications/{application_id}", headers=foreign_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_v2_missing_application_returns_404(client) -> None:
    response = client.get(
        "/api/v2/applications/app_01J0000000000ZZZ", headers=_headers()
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_wrong_contract_version_is_request_invalid(client) -> None:
    for version in ("1.0", "3.0", None):
        headers = _headers(mutation=True, contract_version=version)
        if version is None:
            headers.pop("X-CaseLoop-Contract-Version")
        response = client.post(
            "/api/v2/applications",
            headers=headers,
            content=_app_body(),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "REQUEST_INVALID"


def test_missing_idempotency_key_is_rejected(client) -> None:
    headers = _headers(mutation=True)
    headers.pop("X-CaseLoop-Idempotency-Key")
    response = client.post("/api/v2/applications", headers=headers, content=_app_body())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_missing_bearer_is_authentication_required(client) -> None:
    headers = _headers(mutation=True)
    headers.pop("Authorization")
    response = client.post("/api/v2/applications", headers=headers, content=_app_body())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_duplicate_slug_is_catalog_conflict(client) -> None:
    first = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    assert first.status_code == 201
    headers = _headers(mutation=True)
    headers["X-CaseLoop-Idempotency-Key"] = "app-register-0002"
    response = client.post("/api/v2/applications", headers=headers, content=_app_body())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CATALOG_CONFLICT"


def test_cross_workspace_request_is_denied(client) -> None:
    headers = _headers(mutation=True)
    headers["X-CaseLoop-Workspace-ID"] = "ws_01J0000000000999"
    response = client.post("/api/v2/applications", headers=headers, content=_app_body())
    assert response.status_code in (401, 403)
    assert response.json()["error"]["code"] in {
        "WORKSPACE_ACCESS_DENIED",
        "TOKEN_INVALID",
        "SCOPE_FORBIDDEN",
    }


def test_environment_and_component_registration_v2(client) -> None:
    app_body = json.loads(_app_body())
    registered = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    application_id = registered.json()["application"]["application_id"]

    env_headers = _headers(mutation=True)
    env_headers["X-CaseLoop-Idempotency-Key"] = "env-register-0001"
    env_body = json.dumps(
        {
            "schema_version": "2.0",
            "application_id": application_id,
            "logical_name": "production",
            "risk_classification": "HIGH",
        }
    ).encode("utf-8")
    env = client.post("/api/v2/environments", headers=env_headers, content=env_body)
    assert env.status_code == 201
    assert env.json()["environment"]["logical_name"] == "production"

    component_headers = _headers(mutation=True)
    component_headers["X-CaseLoop-Idempotency-Key"] = "component-register-0001"
    component_body = json.dumps(
        {
            "schema_version": "2.0",
            "application_id": application_id,
            "component_kind": "AGENT",
            "logical_name": "triage-agent",
            "owner_principal_ids": [OWNER],
            "criticality": "P1",
            "data_classification": "INTERNAL",
            "permission_classification": "READ_WRITE",
            "effect_classification": "LOCAL",
        }
    ).encode("utf-8")
    component = client.post(
        "/api/v2/system-components", headers=component_headers, content=component_body
    )
    assert component.status_code == 201
    assert component.json()["component"]["component_kind"] == "AGENT"
