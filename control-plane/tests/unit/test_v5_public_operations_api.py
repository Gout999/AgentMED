"""V5-2B HTTP proof with the real credential resolver and transaction seam."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models.v4_tables import PublicCredential, PublicPrincipal
from app.public_api.credential_resolver import hash_opaque_bearer
from app.services.public_operations import PublicOperationService
from v5_public_operation_fixtures import (
    AUDIENCES,
    CASE,
    ENVIRONMENT,
    ISSUER,
    NOW,
    PRINCIPAL,
    PROJECT,
    SCOPES,
    SUBJECT,
    WORKSPACE,
    seed_public_operation_world,
)

RAW_TOKEN = "v5-2b-route-token-0123456789abcdef"
PEPPER = "v5-2b-route-pepper"
CURSOR_KEY = "v5-2b-route-cursor-key"


def _headers(*, key: str | None = None, request_id: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {RAW_TOKEN}",
        "X-CaseLoop-Workspace-ID": WORKSPACE,
        "X-CaseLoop-Contract-Version": "2.0",
        "X-Request-ID": request_id,
        "Content-Type": "application/json",
    }
    if key is not None:
        headers["X-CaseLoop-Idempotency-Key"] = key
    return headers


def test_http_start_reconnect_list_and_cancel_are_exact_v2_transactions(
    sqlite_engine,
) -> None:
    with Session(sqlite_engine) as session:
        case_digest = seed_public_operation_world(session)
        principal = session.get(PublicPrincipal, PRINCIPAL)
        assert principal is not None
        session.add(
            PublicCredential(
                credential_id="cred_01J0000000000P01",
                workspace_id=WORKSPACE,
                principal_id=PRINCIPAL,
                issuer=ISSUER,
                subject=SUBJECT,
                credential_hash=hash_opaque_bearer(RAW_TOKEN, PEPPER),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "1" * 64,
                claims_digest=principal.claims_digest,
                audiences=list(AUDIENCES),
                project_ids=[PROJECT],
                environment_ids=[ENVIRONMENT],
                scopes=list(SCOPES),
                state="ACTIVE",
                issued_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                not_before=datetime(2020, 1, 1, tzinfo=timezone.utc),
                expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
                revoked_at=None,
            )
        )
        session.commit()

    settings = Settings(
        database_url="sqlite://",
        public_credential_hash_pepper=SecretStr(PEPPER),
        public_cursor_signing_key=SecretStr(CURSOR_KEY),
        public_auth_issuer=ISSUER,
        require_mcp_role_tokens=False,
    )
    app = create_app(settings=settings, engine=sqlite_engine, create_tables=True)
    app.state.public_operation_service_factory = lambda session: PublicOperationService(
        session, cursor_signing_key=CURSOR_KEY, clock=lambda: NOW
    )
    with TestClient(app) as client:
        started = client.post(
            f"/api/v2/cases/{CASE}:investigate",
            headers=_headers(
                key="http-investigation-0001",
                request_id="req_01J0000000000R01",
            ),
            json={
                "schema_version": "2.0",
                "case_revision": 1,
                "case_digest": case_digest,
                "instructions": "HTTP durable investigation",
                "max_attempts": 2,
            },
        )
        assert started.status_code == 202
        assert started.headers["x-caseloop-contract-version"] == "2.0"
        operation_id = started.json()["operation"]["operation_id"]
        assert started.json()["idempotency"]["receipt"]["status"] == "ACCEPTED"

        restored = client.get(
            f"/api/v2/operations/{operation_id}",
            headers=_headers(request_id="req_01J0000000000R02"),
        )
        assert restored.status_code == 200
        assert restored.json()["operation"]["state"] == "SUBMITTED"

        listed = client.get(
            "/api/v2/operations?limit=1",
            headers=_headers(request_id="req_01J0000000000R03"),
        )
        assert listed.status_code == 200
        assert [item["operation_id"] for item in listed.json()["items"]] == [
            operation_id
        ]

        canceled = client.post(
            f"/api/v2/operations/{operation_id}:cancel",
            headers=_headers(
                key="http-operation-cancel-0001",
                request_id="req_01J0000000000R04",
            ),
            json={"schema_version": "2.0", "reason": "HTTP operator stop"},
        )
        assert canceled.status_code == 202
        assert canceled.json()["operation"]["state"] == "CANCEL_REQUESTED"
        assert canceled.json()["operation"]["cancel_requested"] is True
