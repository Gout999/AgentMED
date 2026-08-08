"""FastAPI 依赖。"""
from __future__ import annotations

from collections.abc import Generator
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.quality.client import FakeQualityClient, QualityAPIClient, QualityClientProtocol


def get_db_session(request: Request) -> Generator[Session, None, None]:
    factory = getattr(request.app.state, "session_factory", None) or get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_app_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", None) or get_settings()


def get_quality_client(request: Request) -> QualityClientProtocol:
    client = getattr(request.app.state, "quality_client", None)
    if client is not None:
        return client
    settings = get_app_settings(request)
    return QualityAPIClient(settings.quality_api_base_url, settings.quality_api_token)


def require_internal_write(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """Authenticate deterministic control-plane mutation callers.

    Read views stay independently available to Console.  Mutation endpoints fail
    closed when the shared internal credential has not been configured.
    """

    settings = get_app_settings(request)
    expected = settings.control_plane_internal_token
    approval_token = settings.approval_authority_token
    if not expected or not approval_token:
        raise HTTPException(
            status_code=503,
            detail={"code": "auth_not_configured", "message": "control-plane authority tokens are not configured"},
        )
    if secrets.compare_digest(expected, approval_token):
        raise HTTPException(
            status_code=503,
            detail={"code": "auth_misconfigured", "message": "control and approval authority tokens must be distinct"},
        )
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "valid internal bearer token required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "internal-controller"


def require_approval_authority(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """Authenticate the human-approval adapter independently from agent/control callers."""

    settings = get_app_settings(request)
    expected = settings.approval_authority_token
    control_token = settings.control_plane_internal_token
    if not expected or not control_token:
        raise HTTPException(
            status_code=503,
            detail={"code": "auth_not_configured", "message": "control-plane authority tokens are not configured"},
        )
    if secrets.compare_digest(expected, control_token):
        raise HTTPException(
            status_code=503,
            detail={"code": "auth_misconfigured", "message": "control and approval authority tokens must be distinct"},
        )
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "valid approval authority bearer token required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "approval-authority"
