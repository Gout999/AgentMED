"""FastAPI 依赖。"""
from __future__ import annotations

from collections.abc import Generator
import json
import re
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.quality.client import FakeQualityClient, QualityAPIClient, QualityClientProtocol


_ROLE_WRITE_RULES: dict[str, tuple[tuple[str, str], ...]] = {
    "quality-officer": (
        ("POST", r"^/v1/cases/[^/]+/(?:claim|heartbeat|lease-check|suggestions|transitions)$"),
    ),
    "collector": (),
    "case-officer": (
        ("POST", r"^/v1/releases/[^/]+/closure-context$"),
    ),
    "attributionist": (
        ("POST", r"^/v1/cases/[^/]+/(?:claim|heartbeat|lease-check)$"),
        ("POST", r"^/v1/experiments$"),
        (
            "POST",
            r"^/v1/experiments/[^/]+/(?:protocol|start|cells|trials|verdict|escalate-full-factorial|cancel)$",
        ),
    ),
    "repairer": (
        ("POST", r"^/v1/cases/[^/]+/(?:claim|heartbeat|lease-check)$"),
        ("POST", r"^/v1/release-candidates$"),
        ("POST", r"^/v1/workorders$"),
    ),
    "gatekeeper": (
        ("POST", r"^/v1/changesets/[^/]+/(?:gate|approval-request)$"),
        ("GET", r"^/v1/approvals/[^/]+$"),
        ("GET", r"^/v1/releases/[^/]+/verification-context$"),
        ("POST", r"^/v1/releases/[^/]+/verification$"),
    ),
}

_ROLE_WORKER_IDS: dict[str, frozenset[str]] = {
    "quality-officer": frozenset({"quality-officer"}),
    "attributionist": frozenset({"eval-runner"}),
    "repairer": frozenset({"repairer"}),
}


def _role_tokens(settings: Settings) -> dict[str, str]:
    try:
        parsed = json.loads(getattr(settings, "control_plane_role_tokens_json", "{}") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "auth_misconfigured", "message": "role token JSON is invalid"},
        ) from exc
    if not isinstance(parsed, dict) or any(
        role not in _ROLE_WRITE_RULES or not isinstance(token, str) or not token
        for role, token in parsed.items()
    ):
        raise HTTPException(
            status_code=503,
            detail={"code": "auth_misconfigured", "message": "role token map is invalid"},
        )
    values = list(parsed.values())
    peers = [
        getattr(settings, "control_plane_internal_token", ""),
        getattr(settings, "approval_authority_token", ""),
        getattr(settings, "gate_authority_token", ""),
        *values,
    ]
    configured = [token for token in peers if token]
    if len(configured) != len(set(configured)):
        raise HTTPException(
            status_code=503,
            detail={"code": "auth_misconfigured", "message": "all authority and role tokens must be distinct"},
        )
    return {str(role): str(token) for role, token in parsed.items()}


def validate_authority_config(
    settings: Settings,
    *,
    require_all_role_tokens: bool = False,
) -> dict[str, str]:
    """Validate the complete authority namespace from one shared code path.

    Every authority dependency calls this before comparing a supplied bearer
    token.  A role credential can therefore never become a gate or approval
    credential through a duplicated deployment secret.
    """

    required = (
        getattr(settings, "control_plane_internal_token", ""),
        getattr(settings, "approval_authority_token", ""),
        getattr(settings, "gate_authority_token", ""),
    )
    if any(not token for token in required):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "auth_not_configured",
                "message": "control-plane authority tokens are not configured",
            },
        )
    role_tokens = _role_tokens(settings)
    if require_all_role_tokens and set(role_tokens) != set(_ROLE_WRITE_RULES):
        missing = sorted(set(_ROLE_WRITE_RULES) - set(role_tokens))
        unexpected = sorted(set(role_tokens) - set(_ROLE_WRITE_RULES))
        raise HTTPException(
            status_code=503,
            detail={
                "code": "auth_misconfigured",
                "message": "deployed MCP role token map must contain every fixed role",
                "missing_roles": missing,
                "unexpected_roles": unexpected,
            },
        )
    return role_tokens


def require_principal_worker(authority: str, worker_id: str) -> None:
    """Bind self-reported lease identity to the authenticated MCP principal."""

    if not authority.startswith("mcp:"):
        return
    role = authority.removeprefix("mcp:")
    if worker_id not in _ROLE_WORKER_IDS.get(role, frozenset()):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden",
                "message": f"authenticated {role} principal cannot act as worker_id={worker_id}",
            },
        )


def get_db_session(request: Request) -> Generator[Session, None, None]:
    factory = getattr(request.app.state, "session_factory", None)
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
    role_tokens = validate_authority_config(settings)
    expected = settings.control_plane_internal_token
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "valid internal bearer token required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if secrets.compare_digest(supplied, expected):
        return "internal-controller"
    matched_role = next(
        (role for role, token in role_tokens.items() if secrets.compare_digest(supplied, token)),
        None,
    )
    if matched_role is None:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "valid internal bearer token required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    method = request.method.upper()
    path = request.url.path
    if not any(method == allowed_method and re.fullmatch(pattern, path) for allowed_method, pattern in _ROLE_WRITE_RULES[matched_role]):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "forbidden",
                "message": f"authenticated {matched_role} principal cannot call {method} {path}",
            },
        )
    return f"mcp:{matched_role}"


def require_approval_authority(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """Authenticate the human-approval adapter independently from agent/control callers."""

    settings = get_app_settings(request)
    validate_authority_config(settings)
    expected = settings.approval_authority_token
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "valid approval authority bearer token required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "approval-authority"


def require_gate_authority(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """Authenticate the fixed Gatekeeper separately from other control callers."""

    settings = get_app_settings(request)
    validate_authority_config(settings)
    expected = settings.gate_authority_token
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "valid gate authority bearer token required"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return "gatekeeper-authority"
