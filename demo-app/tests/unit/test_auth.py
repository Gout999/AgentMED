"""Quality API credentials fail closed and write tokens require a client secret."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import auth
from app.routers import quality
from app.schemas import TokenRequest


def test_bearer_auth_fails_closed_when_tokens_are_not_configured(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(caseloop_read_token="", caseloop_write_token=""),
    )
    with pytest.raises(HTTPException) as exc:
        auth.require_scopes(SimpleNamespace(), "Bearer anything")
    assert exc.value.status_code == 503


def test_bearer_auth_rejects_equal_read_and_write_tokens(monkeypatch):
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: SimpleNamespace(caseloop_read_token="same", caseloop_write_token="same"),
    )
    with pytest.raises(HTTPException) as exc:
        auth.require_scopes(SimpleNamespace(), "Bearer same")
    assert exc.value.status_code == 503
    assert exc.value.detail["error"]["code"] == "auth_misconfigured"


def test_release_controller_oauth_requires_exact_secret(monkeypatch):
    settings = SimpleNamespace(
        release_controller_client_secret="server-secret",
        quality_reader_client_secret="reader-secret",
        caseloop_write_token="write-token",
        caseloop_read_token="read-token",
    )
    monkeypatch.setattr(quality, "get_settings", lambda: settings)

    with pytest.raises(HTTPException) as exc:
        quality.oauth_token(
            TokenRequest(
                grant_type="client_credentials",
                client_id="release-controller",
                client_secret="wrong",
            )
        )
    assert exc.value.status_code == 401

    issued = quality.oauth_token(
        TokenRequest(
            grant_type="client_credentials",
            client_id="release-controller",
            client_secret="server-secret",
        )
    )
    assert issued["access_token"] == "write-token"
    assert "quality:write" in issued["scope"]
