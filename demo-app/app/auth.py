"""Quality API scope 校验（quality:read / quality:write）。

令牌经环境变量配置（CASELOOP_READ_TOKEN / CASELOOP_WRITE_TOKEN）；
任一未配置时整个 Quality API 授权面 fail closed。
写面仅 Release Controller 持有；持 write 的调用方同时视为有读权限（openapi 契约）。
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request

from app.config import get_settings
from app.ids import new_trace_id

SCOPES_READ = frozenset({"quality:read"})
SCOPES_WRITE = frozenset({"quality:read", "quality:write"})


def _err(status: int, code: str, message: str, details: dict | None = None) -> HTTPException:
    body = {
        "error": {
            "code": code,
            "message": message,
            **({"details": details} if details else {}),
            "trace_id": new_trace_id(),
        }
    }
    return HTTPException(status_code=status, detail=body)


def require_scopes(request: Request, authorization: str | None = Header(default=None)) -> set[str]:
    settings = get_settings()
    if not settings.caseloop_read_token or not settings.caseloop_write_token:
        raise _err(503, "auth_not_configured", "Quality API bearer tokens are not configured")
    if secrets.compare_digest(settings.caseloop_read_token, settings.caseloop_write_token):
        raise _err(
            503,
            "auth_misconfigured",
            "Quality API read and write credentials must be distinct",
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise _err(401, "unauthorized", "missing or invalid token")
    token = authorization[len("Bearer "):].strip()
    if token and secrets.compare_digest(token, settings.caseloop_write_token):
        return set(SCOPES_WRITE)
    if token and secrets.compare_digest(token, settings.caseloop_read_token):
        return set(SCOPES_READ)
    raise _err(401, "unauthorized", "missing or invalid token")


def require_read(
    request: Request, authorization: str | None = Header(default=None)
) -> set[str]:
    scopes = require_scopes(request, authorization)
    if not (scopes & SCOPES_READ):
        raise _err(403, "forbidden", "scope quality:read required")
    return scopes


def require_write(
    request: Request, authorization: str | None = Header(default=None)
) -> set[str]:
    scopes = require_scopes(request, authorization)
    if "quality:write" not in scopes:
        raise _err(
            403,
            "forbidden",
            "scope quality:write required; write surface is restricted to Release Controller",
        )
    return scopes
