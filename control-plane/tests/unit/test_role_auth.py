"""Deterministic least-privilege credentials for projected MCP workers."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.deps import require_internal_write
from app.config import Settings
from app.main import create_app
from app.quality.client import FakeQualityClient


ROLE_TOKENS = {
    "quality-officer": "role-quality-token",
    "collector": "role-collector-token",
    "case-officer": "role-case-token",
    "attributionist": "role-attribution-token",
    "repairer": "role-repair-token",
    "gatekeeper": "role-gatekeeper-token",
}


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite:///:memory:",
        "control_plane_internal_token": "controller-token",
        "approval_authority_token": "approval-token",
        "gate_authority_token": "gate-authority-token",
        "control_plane_role_tokens_json": json.dumps(ROLE_TOKENS),
        "require_mcp_role_tokens": False,
    }
    values.update(overrides)
    return Settings(**values)


def _request(settings: Settings, method: str, path: str):
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        app=SimpleNamespace(state=SimpleNamespace(settings=settings)),
    )


@pytest.mark.parametrize(
    ("role", "method", "path"),
    [
        ("quality-officer", "POST", "/v1/cases/case_1/suggestions"),
        ("case-officer", "POST", "/v1/releases/rel_1/closure-context"),
        ("attributionist", "POST", "/v1/experiments"),
        ("attributionist", "POST", "/v1/experiments/exp_1/trials"),
        ("repairer", "POST", "/v1/release-candidates"),
        ("gatekeeper", "GET", "/v1/approvals/apr_1"),
        ("gatekeeper", "POST", "/v1/releases/rel_1/verification"),
    ],
)
def test_role_token_allows_only_enumerated_route(role, method, path):
    principal = require_internal_write(
        _request(_settings(), method, path),
        f"Bearer {ROLE_TOKENS[role]}",
    )
    assert principal == f"mcp:{role}"


@pytest.mark.parametrize(
    ("role", "method", "path"),
    [
        ("collector", "POST", "/v1/cases/case_1/claim"),
        ("repairer", "POST", "/v1/releases/rel_1/stage"),
        ("gatekeeper", "POST", "/v1/approvals"),
        ("attributionist", "POST", "/v1/experiments/exp_1/admin"),
        ("quality-officer", "POST", "/v1/workorders"),
    ],
)
def test_role_token_denies_cross_role_and_future_routes(role, method, path):
    with pytest.raises(HTTPException) as exc:
        require_internal_write(
            _request(_settings(), method, path),
            f"Bearer {ROLE_TOKENS[role]}",
        )
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "forbidden"


@pytest.mark.parametrize(
    ("authority_field", "endpoint"),
    [
        ("gate_authority_token", "/v1/gate-reports"),
        ("approval_authority_token", "/v1/approvals"),
    ],
)
def test_http_authority_rejects_role_token_alias(
    sqlite_engine,
    authority_field,
    endpoint,
):
    duplicated = "duplicated-authority-token"
    settings = _settings(
        **{
            authority_field: duplicated,
            "control_plane_role_tokens_json": json.dumps({"repairer": duplicated}),
        }
    )
    app = create_app(
        settings=settings,
        quality_client=FakeQualityClient(),
        engine=sqlite_engine,
        create_tables=True,
    )
    with TestClient(app) as client:
        response = client.post(
            endpoint,
            headers={"Authorization": f"Bearer {duplicated}"},
            json={},
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "auth_misconfigured"


def test_deployed_preflight_rejects_incomplete_role_map(sqlite_engine):
    settings = _settings(
        require_mcp_role_tokens=True,
        control_plane_role_tokens_json=json.dumps({"repairer": "only-one-role"}),
    )
    app = create_app(
        settings=settings,
        quality_client=FakeQualityClient(),
        engine=sqlite_engine,
        create_tables=True,
    )
    with pytest.raises(RuntimeError, match="role authority preflight failed"):
        with TestClient(app):
            pass
