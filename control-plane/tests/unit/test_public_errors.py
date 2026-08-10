from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.public_api.errors import (
    PublicErrorEnvelope,
    map_public_error,
)


FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "v4" / "fixtures" / "valid"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    [
        "public-error.json",
        "public-error-audit-unavailable.json",
        "public-error-auth-before-workspace.json",
    ],
)
def test_public_error_accepts_frozen_fixtures(name: str) -> None:
    assert PublicErrorEnvelope.model_validate(_fixture(name)).schema_version == "1.0"


def test_pre_auth_error_cannot_claim_a_workspace_or_audit() -> None:
    payload = _fixture("public-error-auth-before-workspace.json")
    payload["workspace_id"] = "ws_01J0000000000001"
    payload["workspace_resolved"] = True

    with pytest.raises(ValidationError, match="authentication"):
        PublicErrorEnvelope.model_validate(payload)


def test_recorded_audit_requires_a_real_reference() -> None:
    payload = _fixture("public-error.json")
    payload["error"]["audit_ref"] = None

    with pytest.raises(ValidationError, match="audit_ref"):
        PublicErrorEnvelope.model_validate(payload)


def test_audit_unavailable_never_invents_an_audit_reference() -> None:
    payload = _fixture("public-error-audit-unavailable.json")
    payload["error"]["audit_ref"] = "audit://aud_01J0000000000009"

    with pytest.raises(ValidationError, match="AUDIT_UNAVAILABLE"):
        PublicErrorEnvelope.model_validate(payload)


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("AUTHENTICATION_REQUIRED", 401),
        ("TOKEN_INVALID", 401),
        ("SCOPE_FORBIDDEN", 403),
        ("REQUEST_INVALID", 400),
        ("RESOURCE_NOT_FOUND", 404),
        ("IDEMPOTENCY_CONFLICT", 409),
        ("CONTRACT_VERSION_UNSUPPORTED", 412),
        ("VALIDATION_FAILED", 422),
        ("RATE_LIMITED", 429),
        ("DEPENDENCY_UNAVAILABLE", 503),
        ("AUDIT_UNAVAILABLE", 503),
    ],
)
def test_error_code_mapping_is_stable(code: str, status_code: int) -> None:
    mapped = map_public_error(
        code,
        request_id="req_01J0000000000001",
        workspace_id="ws_01J0000000000001",
        audit_ref="audit://aud_01J0000000000001",
        retry_after_ms=1000 if code in {"RATE_LIMITED", "DEPENDENCY_UNAVAILABLE", "AUDIT_UNAVAILABLE"} else None,
    )

    assert mapped.status_code == status_code
    assert mapped.envelope.error.code == code
    if code in {"AUTHENTICATION_REQUIRED", "TOKEN_INVALID"}:
        assert mapped.envelope.workspace_id is None
        assert mapped.envelope.workspace_resolved is False
        assert mapped.envelope.error.audit_ref is None


def test_unknown_internal_code_maps_to_safe_internal_error() -> None:
    mapped = map_public_error(
        "DATABASE_PASSWORD_LEAK",
        request_id="req_01J0000000000001",
        workspace_id="ws_01J0000000000001",
        audit_ref="audit://aud_01J0000000000001",
    )

    assert mapped.status_code == 500
    assert mapped.envelope.error.code == "INTERNAL_ERROR"
    assert "DATABASE_PASSWORD_LEAK" not in mapped.envelope.error.message


@pytest.mark.parametrize(
    "details",
    [
        {"token": "secret"},
        {"nested": {"authorization": "Bearer secret"}},
        {"traceback": "internal stack"},
        {"provider_message": "raw upstream body"},
    ],
)
def test_error_mapping_rejects_sensitive_or_raw_details(details: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="unsafe error detail"):
        map_public_error(
            "DEPENDENCY_UNAVAILABLE",
            request_id="req_01J0000000000001",
            workspace_id="ws_01J0000000000001",
            audit_ref="audit://aud_01J0000000000001",
            details=details,
            retry_after_ms=1000,
        )
