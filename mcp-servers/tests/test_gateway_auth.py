"""The projected MCP backend is not a second unauthenticated entry point."""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from common.config import Settings
from common.serverkit import TrustedGatewayOnly, validate_projection_runtime


def _ok(_request):
    return JSONResponse({"ok": True})


def _app():
    return TrustedGatewayOnly(
        Starlette(
            routes=[
                Route("/mcp", _ok),
                Route("/api/messages", _ok),
                Route("/healthz", _ok),
            ]
        ),
        expected_consumer="worker-repairer",
        backend_token="private-backend-token",
    )


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-mse-consumer": "worker-repairer"},
        {"x-caseloop-gateway-token": "private-backend-token"},
        {
            "x-caseloop-gateway-token": "wrong",
            "x-mse-consumer": "worker-repairer",
        },
        {
            "x-caseloop-gateway-token": "private-backend-token",
            "x-mse-consumer": "worker-gatekeeper",
        },
    ],
)
def test_mcp_backend_rejects_missing_or_wrong_gateway_identity(headers):
    response = TestClient(_app()).get("/mcp", headers=headers)
    assert response.status_code == 403
    assert response.json() == {
        "error_code": "FORBIDDEN",
        "message": "MCP backend accepts only its authenticated gateway projection",
        "retryable": False,
    }


def test_mcp_backend_rejects_duplicate_security_headers():
    response = TestClient(_app()).get(
        "/mcp",
        headers=[
            ("x-caseloop-gateway-token", "private-backend-token"),
            ("x-caseloop-gateway-token", "private-backend-token"),
            ("x-mse-consumer", "worker-repairer"),
        ],
    )
    assert response.status_code == 403


def test_mcp_backend_accepts_exact_gateway_projection_and_keeps_health_readable():
    client = TestClient(_app())
    response = client.get(
        "/mcp",
        headers={
            "x-caseloop-gateway-token": "private-backend-token",
            "x-mse-consumer": "worker-repairer",
        },
    )
    assert response.status_code == 200
    assert client.get("/healthz").status_code == 200


def test_non_mcp_domain_data_route_is_not_a_direct_backend_bypass():
    client = TestClient(_app())
    assert client.get("/api/messages").status_code == 403
    assert client.get(
        "/api/messages",
        headers={
            "x-caseloop-gateway-token": "private-backend-token",
            "x-mse-consumer": "worker-repairer",
        },
    ).status_code == 200


def _runtime(**overrides) -> Settings:
    values = {
        "mcp_tool_profile": "repairer",
        "mcp_worker_id": "repairer",
        "mcp_expected_consumer": "worker-repairer",
        "mcp_gateway_backend_token": "backend-secret",
        "control_plane_role_token": "repairer-role-token",
        "gate_authority_token": "",
    }
    values.update(overrides)
    return Settings(**values)


def test_projection_runtime_accepts_exact_minimum_secret_set():
    validate_projection_runtime(
        _runtime(),
        profile_workers={"repairer": "repairer"},
        role_token_profiles=frozenset({"repairer"}),
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"mcp_worker_id": "gatekeeper"},
        {"mcp_expected_consumer": "worker-gatekeeper"},
        {"mcp_gateway_backend_token": ""},
        {"control_plane_role_token": ""},
        {"gate_authority_token": "excess-gate-token"},
    ],
)
def test_projection_runtime_fails_closed_on_identity_or_secret_drift(overrides):
    with pytest.raises(RuntimeError):
        validate_projection_runtime(
            _runtime(**overrides),
            profile_workers={"repairer": "repairer"},
            role_token_profiles=frozenset({"repairer"}),
        )
