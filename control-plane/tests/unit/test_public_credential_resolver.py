from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from app.models.v4_tables import PublicCredential, PublicPrincipal
from app.public_api.credential_resolver import (
    CredentialResolutionError,
    PublicCredentialResolver,
    digest_public_subject,
    hash_opaque_bearer,
)
from app.utils.v4_integrity import canonical_digest


NOW = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
WORKSPACE_ID = "ws_01J0000000000001"
PROJECT_ID = "proj_01J0000000000001"
ENVIRONMENT_ID = "env_01J0000000000001"
PRINCIPAL_ID = "prn_01J0000000000001"
CREDENTIAL_ID = "cred_01J0000000000001"
TOKEN = "opaque-public-token-with-enough-entropy"
PEPPER = SecretStr("independent-public-test-pepper")
DIGEST_1 = "sha256:" + hashlib.sha256(
    b"maintainer-01J0000000000001"
).hexdigest()
BASE_SCOPES = [
    "signals:write",
    "cases:read",
    "artifacts:read",
    "capabilities:read",
]


def _claims_digest(
    *,
    workspace_id: str = WORKSPACE_ID,
    project_ids: list[str] | None = None,
    environment_ids: list[str] | None = None,
    scopes: list[str] | None = None,
    audiences: list[str] | None = None,
    principal_type: str = "human",
) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": "https://auth.caseloop.dev",
            "subject": "maintainer-01J0000000000001",
            "principal_type": principal_type,
            "audiences": ["caseloop-public-api"] if audiences is None else audiences,
            "workspace_id": workspace_id,
            "project_ids": [PROJECT_ID] if project_ids is None else project_ids,
            "environment_ids": (
                [ENVIRONMENT_ID] if environment_ids is None else environment_ids
            ),
            "scopes": BASE_SCOPES if scopes is None else scopes,
        }
    )


BASE_CLAIMS_DIGEST = _claims_digest()


def test_public_subject_digest_is_shared_with_bootstrap_and_resolver() -> None:
    assert digest_public_subject("maintainer-01J0000000000001") == DIGEST_1


def _seed_credential(
    sqlite_session,
    *,
    token: str = TOKEN,
    principal_state: str = "ACTIVE",
    principal_revoked_at: datetime | None = None,
    credential_revoked_at: datetime | None = None,
    credential_state: str = "ACTIVE",
    issued_at: datetime | None = None,
    not_before: datetime | None = None,
    expires_at: datetime | None = None,
    audiences: list[str] | None = None,
    workspace_id: str = WORKSPACE_ID,
    project_ids: list[str] | None = None,
    environment_ids: list[str] | None = None,
    scopes: list[str] | None = None,
    principal_type: str = "human",
    claims_digest: str | None = None,
) -> None:
    granted_scopes = BASE_SCOPES if scopes is None else scopes
    granted_audiences = ["caseloop-public-api"] if audiences is None else audiences
    granted_projects = [PROJECT_ID] if project_ids is None else project_ids
    granted_environments = (
        [ENVIRONMENT_ID] if environment_ids is None else environment_ids
    )
    stored_claims_digest = claims_digest or _claims_digest(
        workspace_id=workspace_id,
        project_ids=granted_projects,
        environment_ids=granted_environments,
        scopes=granted_scopes,
        audiences=granted_audiences,
        principal_type=principal_type,
    )
    sqlite_session.add(
        PublicPrincipal(
            principal_id=PRINCIPAL_ID,
            workspace_id=workspace_id,
            principal_type=principal_type,
            state=principal_state,
            subject_digest=DIGEST_1,
            audiences=granted_audiences,
            project_ids=granted_projects,
            environment_ids=granted_environments,
            scopes=granted_scopes,
            claims_digest=stored_claims_digest,
            revoked_at=principal_revoked_at,
        )
    )
    sqlite_session.add(
        PublicCredential(
            credential_id=CREDENTIAL_ID,
            workspace_id=workspace_id,
            principal_id=PRINCIPAL_ID,
            issuer="https://auth.caseloop.dev",
            subject="maintainer-01J0000000000001",
            credential_hash=hash_opaque_bearer(token, PEPPER),
            hash_algorithm="hmac-sha256-v1",
            jti_digest=DIGEST_1,
            claims_digest=stored_claims_digest,
            audiences=granted_audiences,
            project_ids=granted_projects,
            environment_ids=granted_environments,
            scopes=granted_scopes,
            state=credential_state,
            issued_at=issued_at or NOW - timedelta(hours=1),
            not_before=not_before or NOW - timedelta(hours=1),
            expires_at=expires_at or NOW + timedelta(hours=1),
            revoked_at=credential_revoked_at,
        )
    )
    sqlite_session.commit()


def _resolver(sqlite_session, *, pepper: SecretStr = PEPPER) -> PublicCredentialResolver:
    return PublicCredentialResolver(
        sqlite_session,
        hash_pepper=pepper,
        expected_issuer="https://auth.caseloop.dev",
    )


def test_opaque_bearer_is_hmac_looked_up_and_bound_to_exact_request_context(
    sqlite_session,
) -> None:
    _seed_credential(sqlite_session)
    resolver = _resolver(sqlite_session)

    with patch(
        "app.public_api.credential_resolver.hmac.compare_digest",
        wraps=__import__("hmac").compare_digest,
    ) as constant_time_compare:
        context = resolver.resolve(
            SecretStr(TOKEN),
            requested_workspace_id=WORKSPACE_ID,
            project_id=PROJECT_ID,
            environment_id=ENVIRONMENT_ID,
            required_scope="signals:write",
            evaluated_at=NOW,
        )

    assert context.principal_id == PRINCIPAL_ID
    assert context.credential_id == CREDENTIAL_ID
    assert context.requested_context.project_id == PROJECT_ID
    assert context.requested_context.environment_id == ENVIRONMENT_ID
    assert context.revoked_at is None
    assert constant_time_compare.call_count >= 1
    assert TOKEN not in repr(context)
    assert TOKEN not in context.model_dump_json()


def test_resolver_can_rebind_an_already_authenticated_context_to_body_grants(
    sqlite_session,
) -> None:
    _seed_credential(sqlite_session)
    resolver = _resolver(sqlite_session)
    base = resolver.resolve(
        SecretStr(TOKEN),
        requested_workspace_id=WORKSPACE_ID,
        required_scope="signals:write",
        evaluated_at=NOW,
    )

    bound = resolver.bind_requested_context(
        base,
        project_id=PROJECT_ID,
        environment_id=ENVIRONMENT_ID,
        required_scope="signals:write",
    )

    assert bound.requested_context.project_id == PROJECT_ID
    assert bound.requested_context.environment_id == ENVIRONMENT_ID


@pytest.mark.parametrize(
    ("seed_kwargs", "resolve_kwargs", "expected_code"),
    [
        ({"credential_revoked_at": NOW}, {}, "TOKEN_REVOKED"),
        ({"principal_revoked_at": NOW}, {}, "TOKEN_REVOKED"),
        (
            {"principal_state": "REVOKED", "principal_revoked_at": NOW},
            {},
            "TOKEN_REVOKED",
        ),
        ({"expires_at": NOW}, {}, "TOKEN_EXPIRED"),
        ({"not_before": NOW + timedelta(seconds=1)}, {}, "TOKEN_NOT_YET_VALID"),
        ({"audiences": ["some-other-api"]}, {}, "AUDIENCE_MISMATCH"),
        ({}, {"requested_workspace_id": "ws_01J0000000000099"}, "WORKSPACE_ACCESS_DENIED"),
        ({}, {"required_scope": "releases:write"}, "SCOPE_FORBIDDEN"),
        ({}, {"project_id": "proj_01J0000000000099"}, "WORKSPACE_ACCESS_DENIED"),
        ({}, {"environment_id": "env_01J0000000000099"}, "WORKSPACE_ACCESS_DENIED"),
    ],
)
def test_resolver_fails_closed_for_revocation_time_audience_and_grant_attacks(
    sqlite_session,
    seed_kwargs: dict[str, object],
    resolve_kwargs: dict[str, object],
    expected_code: str,
) -> None:
    _seed_credential(sqlite_session, **seed_kwargs)
    args: dict[str, object] = {
        "requested_workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "environment_id": ENVIRONMENT_ID,
        "required_scope": "signals:write",
        "evaluated_at": NOW,
    }
    args.update(resolve_kwargs)

    with pytest.raises(CredentialResolutionError) as exc_info:
        _resolver(sqlite_session).resolve(SecretStr(TOKEN), **args)

    assert exc_info.value.code == expected_code
    assert TOKEN not in str(exc_info.value)


def test_unknown_bearer_is_a_secret_safe_token_invalid_error(sqlite_session) -> None:
    _seed_credential(sqlite_session)

    with patch(
        "app.public_api.credential_resolver.hmac.compare_digest",
        wraps=__import__("hmac").compare_digest,
    ) as constant_time_compare:
        with pytest.raises(CredentialResolutionError) as exc_info:
            _resolver(sqlite_session).resolve(
                SecretStr("different-opaque-token"),
                requested_workspace_id=WORKSPACE_ID,
                required_scope="cases:read",
                evaluated_at=NOW,
            )

    assert exc_info.value.code == "TOKEN_INVALID"
    assert "different-opaque-token" not in str(exc_info.value)
    assert constant_time_compare.call_count >= 1


@pytest.mark.parametrize(
    ("field", "drifted_value"),
    [
        ("claims_digest", "sha256:" + "9" * 64),
        ("audiences", ["caseloop-public-api", "drifted-audience"]),
        ("project_ids", []),
        ("environment_ids", []),
        ("scopes", ["cases:read"]),
    ],
)
def test_credential_and_principal_grant_drift_fails_closed(
    sqlite_session, field: str, drifted_value: object
) -> None:
    _seed_credential(sqlite_session)
    credential = sqlite_session.get(PublicCredential, CREDENTIAL_ID)
    assert credential is not None
    setattr(credential, field, drifted_value)
    sqlite_session.commit()

    with pytest.raises(CredentialResolutionError) as exc_info:
        _resolver(sqlite_session).resolve(
            SecretStr(TOKEN),
            requested_workspace_id=WORKSPACE_ID,
            required_scope="cases:read",
            evaluated_at=NOW,
        )

    assert exc_info.value.code == "TOKEN_INVALID"


@pytest.mark.parametrize(
    ("seed_kwargs", "resolve_kwargs"),
    [
        (
            {
                "scopes": [*BASE_SCOPES, "releases:write"],
                "claims_digest": BASE_CLAIMS_DIGEST,
            },
            {"required_scope": "releases:write"},
        ),
        (
            {
                "project_ids": [PROJECT_ID, "proj_01J0000000000099"],
                "claims_digest": BASE_CLAIMS_DIGEST,
            },
            {"project_id": "proj_01J0000000000099"},
        ),
        (
            {
                "environment_ids": [ENVIRONMENT_ID, "env_01J0000000000099"],
                "claims_digest": BASE_CLAIMS_DIGEST,
            },
            {"environment_id": "env_01J0000000000099"},
        ),
        (
            {
                "workspace_id": "ws_01J0000000000099",
                "claims_digest": BASE_CLAIMS_DIGEST,
            },
            {"requested_workspace_id": "ws_01J0000000000099"},
        ),
        (
            {
                "audiences": ["caseloop-public-api", "admin-control-plane"],
                "claims_digest": BASE_CLAIMS_DIGEST,
            },
            {},
        ),
        (
            {
                "principal_type": "service",
                "claims_digest": BASE_CLAIMS_DIGEST,
            },
            {},
        ),
    ],
)
def test_coordinated_identity_and_grant_escalation_with_stale_digest_fails_closed(
    sqlite_session,
    seed_kwargs: dict[str, object],
    resolve_kwargs: dict[str, object],
) -> None:
    _seed_credential(sqlite_session, **seed_kwargs)
    args: dict[str, object] = {
        "requested_workspace_id": WORKSPACE_ID,
        "project_id": PROJECT_ID,
        "environment_id": ENVIRONMENT_ID,
        "required_scope": "signals:write",
        "evaluated_at": NOW,
    }
    args.update(resolve_kwargs)

    with pytest.raises(CredentialResolutionError) as exc_info:
        _resolver(sqlite_session).resolve(SecretStr(TOKEN), **args)

    assert exc_info.value.code == "TOKEN_INVALID"


def test_invalid_persisted_credential_time_order_fails_closed(sqlite_session) -> None:
    _seed_credential(
        sqlite_session,
        issued_at=NOW - timedelta(minutes=30),
        not_before=NOW - timedelta(hours=1),
    )

    with pytest.raises(CredentialResolutionError) as exc_info:
        _resolver(sqlite_session).resolve(
            SecretStr(TOKEN),
            requested_workspace_id=WORKSPACE_ID,
            required_scope="cases:read",
            evaluated_at=NOW,
        )

    assert exc_info.value.code == "TOKEN_INVALID"


def test_empty_public_hash_pepper_fails_closed_without_reusing_internal_tokens(
    sqlite_session,
) -> None:
    _seed_credential(sqlite_session)

    with pytest.raises(CredentialResolutionError) as exc_info:
        _resolver(sqlite_session, pepper=SecretStr("")).resolve(
            SecretStr(TOKEN),
            requested_workspace_id=WORKSPACE_ID,
            required_scope="cases:read",
            evaluated_at=NOW,
        )

    assert exc_info.value.code == "DEPENDENCY_UNAVAILABLE"
