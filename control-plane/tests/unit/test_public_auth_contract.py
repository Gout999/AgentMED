from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.public_api.auth_contract import (
    AcceptedPrincipalContext,
    HeaderContractViolation,
    PublicRequestHeaders,
)


FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "v4" / "fixtures" / "valid"


def _principal() -> dict[str, object]:
    return json.loads(
        (FIXTURES / "public-principal-context.json").read_text(encoding="utf-8")
    )


def test_accepted_principal_context_accepts_the_frozen_fixture() -> None:
    context = AcceptedPrincipalContext.model_validate(_principal())

    assert context.requested_context.workspace_id == context.workspace_id
    assert context.requested_context.required_scope in context.scopes
    assert context.revoked_at is None


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("evaluated_at",), "2026-08-10T18:00:00Z"),
        (("requested_context", "workspace_id"), "ws_01J0000000000099"),
        (("requested_context", "project_id"), "proj_01J0000000000099"),
        (("requested_context", "environment_id"), "env_01J0000000000099"),
        (("requested_context", "required_scope"), "releases:write"),
        (("audiences",), ["some-other-api"]),
    ],
)
def test_accepted_principal_context_fails_closed_on_cross_field_attack(
    path: tuple[str, ...], value: object
) -> None:
    payload = copy.deepcopy(_principal())
    target = payload
    for segment in path[:-1]:
        target = target[segment]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        AcceptedPrincipalContext.model_validate(payload)


@pytest.mark.parametrize("raw_field", ["jti", "raw_jti", "token", "authorization"])
def test_accepted_principal_context_never_accepts_raw_credentials(raw_field: str) -> None:
    payload = _principal()
    payload[raw_field] = "must-not-escape"

    with pytest.raises(ValidationError):
        AcceptedPrincipalContext.model_validate(payload)


def test_public_request_headers_parse_opaque_bearer_without_echoing_it() -> None:
    raw_token = "opaque-super-secret-token"
    parsed = PublicRequestHeaders.from_headers(
        {
            "Authorization": f"Bearer {raw_token}",
            "X-CaseLoop-Workspace-ID": "ws_01J0000000000001",
            "X-CaseLoop-Contract-Version": "1.0",
            "Idempotency-Key": "signal-submit-0001",
            "X-Request-ID": "req_01J0000000000002",
            "X-CaseLoop-Client-Version": "agentmed-cli/1.0.0",
        },
        mutation=True,
    )

    assert parsed.requested_workspace_id == "ws_01J0000000000001"
    assert parsed.idempotency_key == "signal-submit-0001"
    assert parsed.bearer_token.get_secret_value() == raw_token
    assert raw_token not in repr(parsed)
    assert raw_token not in parsed.model_dump_json()
    assert "authorization" not in parsed.model_dump()


def test_public_request_headers_are_case_insensitive() -> None:
    parsed = PublicRequestHeaders.from_headers(
        {
            "authorization": "Bearer opaque-token",
            "x-caseloop-workspace-id": "ws_01J0000000000001",
            "x-caseloop-contract-version": "1.0",
        }
    )

    assert parsed.contract_version == "1.0"


@pytest.mark.parametrize(
    ("headers", "mutation", "error_code"),
    [
        ({}, False, "AUTHENTICATION_REQUIRED"),
        (
            {
                "Authorization": "Basic abc",
                "X-CaseLoop-Workspace-ID": "ws_01J0000000000001",
                "X-CaseLoop-Contract-Version": "1.0",
            },
            False,
            "TOKEN_INVALID",
        ),
        (
            {
                "Authorization": "Bearer opaque-token",
                "X-CaseLoop-Workspace-ID": "ws_01J0000000000001",
                "X-CaseLoop-Contract-Version": "2.0",
            },
            False,
            "CONTRACT_VERSION_UNSUPPORTED",
        ),
        (
            {
                "Authorization": "Bearer opaque-token",
                "X-CaseLoop-Workspace-ID": "ws_01J0000000000001",
                "X-CaseLoop-Contract-Version": "1.0",
            },
            True,
            "IDEMPOTENCY_KEY_REQUIRED",
        ),
    ],
)
def test_public_request_header_failures_are_machine_readable_and_secret_safe(
    headers: dict[str, str], mutation: bool, error_code: str
) -> None:
    with pytest.raises(HeaderContractViolation) as exc_info:
        PublicRequestHeaders.from_headers(headers, mutation=mutation)

    assert exc_info.value.code == error_code
    assert "opaque-token" not in str(exc_info.value)


def test_duplicate_case_insensitive_headers_fail_closed() -> None:
    with pytest.raises(HeaderContractViolation, match="duplicate"):
        PublicRequestHeaders.from_headers(
            {
                "Authorization": "Bearer one-token",
                "authorization": "Bearer other-token",
                "X-CaseLoop-Workspace-ID": "ws_01J0000000000001",
                "X-CaseLoop-Contract-Version": "1.0",
            }
        )
