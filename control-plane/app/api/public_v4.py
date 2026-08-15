"""Frozen Stage 1A public HTTP boundary.

The router owns transport parsing, opaque credential resolution and transaction
closure.  Authoritative lifecycle work stays in the injected services; the
router never recreates their state machines or invents audit/evidence records.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, SecretStr, ValidationError
from sqlalchemy.orm import Session

from app import __version__
from app.db import get_session_factory
from app.public_api.auth_contract import (
    AcceptedPrincipalContext,
    HeaderContractViolation,
    PublicRequestHeaders,
)
from app.public_api.errors import map_public_error
from app.public_api.models import (
    CaseResponse,
    CaseTimelineResponse,
    EvidenceResponse,
    ServerCapabilitiesResponse,
    SignalSubmission,
    SignalSubmissionResponse,
)
from app.services.public_read import PublicReadDenial


router = APIRouter(prefix="/api/v1", tags=["public-v4-stage1a"])

_CASE_ID = re.compile(r"^case_[0-9A-Za-z]{8,64}$")
_RECEIPT_ID = re.compile(r"^ter_[0-9A-Za-z]{8,64}$")
_CURSOR = re.compile(r"^cur_[0-9A-Za-z_-]{8,512}$")
_REQUEST_ID = re.compile(r"^req_[0-9A-Za-z]{8,64}$")
_MAX_SIGNAL_BODY_BYTES = 256_000
_PUBLIC_HEADER_NAMES = frozenset(
    {
        "authorization",
        "x-agentmed-workspace-id",
        "x-agentmed-contract-version",
        "idempotency-key",
        "x-request-id",
        "x-agentmed-client-version",
    }
)


IMPLEMENTED_S1A_INTENTS: tuple[dict[str, object], ...] = (
    {
        "name": "signals.submit",
        "scope": "signals:write",
        "execution_mode": "synchronous",
        "http": True,
        "cli": True,
    },
    {
        "name": "cases.get",
        "scope": "cases:read",
        "execution_mode": "synchronous",
        "http": True,
        "cli": True,
    },
    {
        "name": "cases.timeline",
        "scope": "cases:read",
        "execution_mode": "synchronous",
        "http": True,
        "cli": True,
    },
    {
        "name": "evidence.get",
        "scope": "artifacts:read",
        "execution_mode": "synchronous",
        "http": True,
        "cli": True,
    },
    {
        "name": "capabilities.get",
        "scope": "capabilities:read",
        "execution_mode": "synchronous",
        "http": True,
        "cli": True,
    },
)


@dataclass(frozen=True)
class _RouteFailure(Exception):
    code: str
    details: dict[str, object] | None = None
    audit_ref: str | None = None


class _CommitFailure(Exception):
    """Secret-safe marker distinguishing commit failure from service defects."""


class _InvalidJson(ValueError):
    """Stable marker for duplicate keys or non-standard JSON constants."""


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


def _new_request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id")
    return supplied if supplied is not None and _REQUEST_ID.fullmatch(supplied) else _new_request_id()


def _public_headers(request: Request) -> dict[str, str]:
    """Preserve duplicate detection for authority-bearing ASGI headers."""

    parsed: dict[str, str] = {}
    for raw_name, raw_value in request.scope.get("headers", []):
        name = raw_name.decode("latin-1").lower()
        if name not in _PUBLIC_HEADER_NAMES:
            continue
        if name in parsed:
            raise HeaderContractViolation("REQUEST_INVALID", f"duplicate header: {name}")
        parsed[name] = raw_value.decode("latin-1")
    return parsed


def _json_response(model: BaseModel, *, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=model.model_dump(mode="json", exclude_none=False),
        headers={"X-AgentMED-Contract-Version": "1.0"},
    )


def _error_response(
    failure: BaseException,
    *,
    request_id: str,
    workspace_id: str | None = None,
    keep_audit_ref: bool = False,
) -> JSONResponse:
    code = str(getattr(failure, "code", "INTERNAL_ERROR"))
    details = getattr(failure, "details", None)
    safe_details = details if isinstance(details, dict) else {}
    audit_ref = getattr(failure, "audit_ref", None) if keep_audit_ref else None
    try:
        mapped = map_public_error(
            code,
            request_id=request_id,
            workspace_id=workspace_id,
            audit_ref=audit_ref if isinstance(audit_ref, str) else None,
            details=safe_details,
        )
    except (TypeError, ValueError, ValidationError):
        mapped = map_public_error(
            "INTERNAL_ERROR",
            request_id=request_id,
            workspace_id=workspace_id,
            details={},
        )
    return JSONResponse(
        status_code=mapped.status_code,
        content=mapped.envelope.model_dump(mode="json", exclude_none=False),
        headers=mapped.headers,
    )


def _rollback(session: Any) -> None:
    try:
        session.rollback()
    except Exception:
        pass


def _close(session: Any) -> None:
    try:
        session.close()
    except Exception:
        pass


def _commit(session: Any) -> None:
    try:
        session.commit()
    except Exception as exc:
        raise _CommitFailure from exc


def _handle_failure(
    session: Any | None,
    exc: Exception,
    *,
    request_id: str,
    principal: AcceptedPrincipalContext | None,
    allow_read_denial_commit: bool = False,
) -> JSONResponse:
    error_workspace = getattr(exc, "workspace_id", None)
    workspace_id = (
        principal.workspace_id
        if principal is not None
        else error_workspace if isinstance(error_workspace, str) else None
    )

    # A PublicReadDenial is a typed audit-only outcome.  Its service has made
    # no business mutation, so committing here persists exactly the denial
    # audit that its returned reference identifies.
    if (
        allow_read_denial_commit
        and session is not None
        and principal is not None
        and isinstance(exc, PublicReadDenial)
    ):
        code = getattr(exc, "code", None)
        audit_ref = getattr(exc, "audit_ref", None)
        details = getattr(exc, "details", None)
        if (
            code
            not in {"RESOURCE_NOT_FOUND", "SCOPE_FORBIDDEN", "VALIDATION_FAILED"}
            or not isinstance(audit_ref, str)
            or not isinstance(details, dict)
        ):
            _rollback(session)
            return _error_response(
                _RouteFailure("INTERNAL_ERROR"),
                request_id=request_id,
                workspace_id=workspace_id,
            )
        # Validate the exact public envelope before making the denial audit
        # durable.  A malformed code/ref/details must never cause a commit.
        try:
            mapped = map_public_error(
                code,
                request_id=request_id,
                workspace_id=workspace_id,
                audit_ref=audit_ref,
                details=details,
            )
        except (TypeError, ValueError, ValidationError):
            _rollback(session)
            return _error_response(
                _RouteFailure("INTERNAL_ERROR"),
                request_id=request_id,
                workspace_id=workspace_id,
            )
        try:
            _commit(session)
        except _CommitFailure:
            _rollback(session)
            return _error_response(
                _RouteFailure("AUDIT_UNAVAILABLE"),
                request_id=request_id,
                workspace_id=workspace_id,
            )
        return JSONResponse(
            status_code=mapped.status_code,
            content=mapped.envelope.model_dump(mode="json", exclude_none=False),
            headers=mapped.headers,
        )

    if session is not None:
        _rollback(session)
    if isinstance(exc, _CommitFailure):
        failure: BaseException = _RouteFailure("AUDIT_UNAVAILABLE")
    elif isinstance(exc, ValidationError):
        failure = _RouteFailure("INTERNAL_ERROR")
    elif (
        allow_read_denial_commit
        and not isinstance(exc, PublicReadDenial)
        and getattr(exc, "rollback_required", True) is False
    ):
        # Audit-only commit is a closed typed channel.  An arbitrary exception
        # cannot opt in by copying PublicReadDenial attributes.
        failure = _RouteFailure("INTERNAL_ERROR")
    elif session is None and not hasattr(exc, "code"):
        failure = _RouteFailure("DEPENDENCY_UNAVAILABLE")
    else:
        failure = exc
    return _error_response(
        failure,
        request_id=request_id,
        workspace_id=workspace_id,
    )


def _session_for(request: Request) -> Session:
    factory = getattr(request.app.state, "session_factory", None) or get_session_factory()
    return factory()


def _secret_text(value: object) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return str(value or "")


def _independent_public_secret(
    settings: Any,
    value: object,
    *,
    other_public_secret: object,
) -> bool:
    candidate = _secret_text(value)
    if not candidate:
        return False
    peers = [
        _secret_text(other_public_secret),
        str(getattr(settings, "control_plane_internal_token", "") or ""),
        str(getattr(settings, "approval_authority_token", "") or ""),
        str(getattr(settings, "gate_authority_token", "") or ""),
    ]
    try:
        role_tokens = json.loads(
            getattr(settings, "control_plane_role_tokens_json", "{}") or "{}"
        )
        if isinstance(role_tokens, dict):
            peers.extend(str(item) for item in role_tokens.values() if isinstance(item, str))
    except json.JSONDecodeError:
        return False
    return not any(
        peer and secrets.compare_digest(candidate, peer)
        for peer in peers
    )


def _credential_resolver(request: Request, session: Session) -> Any:
    factory = getattr(request.app.state, "public_credential_resolver_factory", None)
    if factory is not None:
        return factory(session)

    from app.public_api.credential_resolver import PublicCredentialResolver

    settings = request.app.state.settings
    if not _independent_public_secret(
        settings,
        settings.public_credential_hash_pepper,
        other_public_secret=settings.public_cursor_signing_key,
    ):
        raise _RouteFailure("DEPENDENCY_UNAVAILABLE")
    return PublicCredentialResolver(
        session,
        hash_pepper=settings.public_credential_hash_pepper,
        expected_issuer=settings.public_auth_issuer,
    )


def _signal_service(request: Request, session: Session) -> Any:
    factory = getattr(request.app.state, "signal_intake_service_factory", None)
    if factory is not None:
        return factory(session)

    from app.services.signal_intake import SignalIntakeService

    return SignalIntakeService(session)


def _read_service(request: Request, session: Session) -> Any:
    settings = request.app.state.settings
    signing_key = settings.public_cursor_signing_key
    if isinstance(signing_key, SecretStr):
        signing_key = signing_key.get_secret_value()
    if not _independent_public_secret(
        settings,
        signing_key,
        other_public_secret=settings.public_credential_hash_pepper,
    ):
        raise _RouteFailure("DEPENDENCY_UNAVAILABLE")

    factory = getattr(request.app.state, "public_read_service_factory", None)
    if factory is not None:
        return factory(session, signing_key)

    from app.services.public_read import PublicReadService

    return PublicReadService(session, signing_key)


def _authenticate(
    request: Request,
    session: Session,
    *,
    headers: PublicRequestHeaders,
    required_scope: str,
) -> tuple[AcceptedPrincipalContext, Any]:
    resolver = _credential_resolver(request, session)
    principal = resolver.resolve(
        headers.bearer_token,
        requested_workspace_id=headers.requested_workspace_id,
        required_scope=required_scope,
    )
    request.state.public_principal = principal
    return principal, resolver


def _validate_response(model_type: type[ResponseModel], value: Any) -> ResponseModel:
    return model_type.model_validate(value)


def _validate_path(value: str, pattern: re.Pattern[str], field: str) -> None:
    if pattern.fullmatch(value) is None:
        raise _RouteFailure("VALIDATION_FAILED", {"fields": [field]})


async def _parse_signal_submission(request: Request) -> SignalSubmission:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise _RouteFailure("UNSUPPORTED_MEDIA_TYPE")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_SIGNAL_BODY_BYTES:
                raise _RouteFailure("CONTENT_TOO_LARGE")
        except ValueError:
            raise _RouteFailure("REQUEST_INVALID", {"fields": ["content-length"]}) from None

    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _MAX_SIGNAL_BODY_BYTES:
            raise _RouteFailure("CONTENT_TOO_LARGE")
        raw.extend(chunk)

    def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise _InvalidJson
            value[key] = item
        return value

    def reject_constant(_value: str) -> None:
        raise _InvalidJson

    try:
        payload = json.loads(
            raw,
            object_pairs_hook=closed_object,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _InvalidJson):
        raise _RouteFailure("REQUEST_INVALID", {"fields": ["body"]}) from None
    try:
        return SignalSubmission.model_validate(payload)
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in exc.errors()})
        raise _RouteFailure("VALIDATION_FAILED", {"fields": fields}) from None


def _timeline_params(request: Request) -> tuple[str | None, int]:
    if len(request.query_params.getlist("cursor")) > 1 or len(request.query_params.getlist("limit")) > 1:
        raise _RouteFailure("VALIDATION_FAILED", {"fields": ["query"]})
    cursor = request.query_params.get("cursor")
    if cursor is not None and _CURSOR.fullmatch(cursor) is None:
        raise _RouteFailure("VALIDATION_FAILED", {"fields": ["cursor"]})
    raw_limit = request.query_params.get("limit", "50")
    try:
        limit = int(raw_limit)
    except ValueError:
        raise _RouteFailure("VALIDATION_FAILED", {"fields": ["limit"]}) from None
    if not 1 <= limit <= 200:
        raise _RouteFailure("VALIDATION_FAILED", {"fields": ["limit"]})
    return cursor, limit


@router.get("/capabilities", response_model=ServerCapabilitiesResponse)
def get_capabilities(request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicRequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="capabilities:read",
        )
        service = _read_service(request, session)
        result = service.get_capabilities(
            principal=principal,
            request_id=request_id,
            server_version=f"{__version__}+v4-stage1a",
            implemented_intents=[dict(item) for item in IMPLEMENTED_S1A_INTENTS],
        )
        response = _validate_response(ServerCapabilitiesResponse, result)
        _commit(session)
        return _json_response(response, status_code=200)
    except Exception as exc:
        return _handle_failure(
            session,
            exc,
            request_id=request_id,
            principal=principal,
        )
    finally:
        if session is not None:
            _close(session)


@router.post(
    "/signals",
    response_model=SignalSubmissionResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": SignalSubmission.model_json_schema()}},
        }
    },
)
async def submit_signal(request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicRequestHeaders.from_headers(
            _public_headers(request), mutation=True
        )
        submission = await _parse_signal_submission(request)
        session = _session_for(request)
        principal, resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="signals:write",
        )
        principal = resolver.bind_requested_context(
            principal,
            project_id=submission.project_id,
            environment_id=submission.environment_id,
            required_scope="signals:write",
        )
        request.state.public_principal = principal
        service = _signal_service(request, session)
        result = service.submit(
            submission,
            principal=principal,
            idempotency_key=headers.idempotency_key,
            request_id=request_id,
        )
        response = _validate_response(SignalSubmissionResponse, result)
        _commit(session)
        return _json_response(response, status_code=201)
    except Exception as exc:
        return _handle_failure(
            session,
            exc,
            request_id=request_id,
            principal=principal,
        )
    finally:
        if session is not None:
            _close(session)


@router.get("/cases/{case_id}", response_model=CaseResponse)
def get_case(case_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicRequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="cases:read"
        )
        _validate_path(case_id, _CASE_ID, "case_id")
        result = _read_service(request, session).get_case(
            principal=principal,
            request_id=request_id,
            case_id=case_id,
        )
        response = _validate_response(CaseResponse, result)
        _commit(session)
        return _json_response(response, status_code=200)
    except Exception as exc:
        return _handle_failure(
            session,
            exc,
            request_id=request_id,
            principal=principal,
            allow_read_denial_commit=True,
        )
    finally:
        if session is not None:
            _close(session)


@router.get("/cases/{case_id}/timeline", response_model=CaseTimelineResponse)
def get_case_timeline(case_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicRequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="cases:read"
        )
        _validate_path(case_id, _CASE_ID, "case_id")
        cursor, limit = _timeline_params(request)
        result = _read_service(request, session).get_case_timeline(
            principal=principal,
            request_id=request_id,
            case_id=case_id,
            cursor=cursor,
            limit=limit,
        )
        response = _validate_response(CaseTimelineResponse, result)
        _commit(session)
        return _json_response(response, status_code=200)
    except Exception as exc:
        return _handle_failure(
            session,
            exc,
            request_id=request_id,
            principal=principal,
            allow_read_denial_commit=True,
        )
    finally:
        if session is not None:
            _close(session)


@router.get("/evidence/{receipt_id}", response_model=EvidenceResponse)
def get_evidence(receipt_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicRequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="artifacts:read",
        )
        _validate_path(receipt_id, _RECEIPT_ID, "receipt_id")
        result = _read_service(request, session).get_evidence(
            principal=principal,
            request_id=request_id,
            receipt_id=receipt_id,
        )
        response = _validate_response(EvidenceResponse, result)
        _commit(session)
        return _json_response(response, status_code=200)
    except Exception as exc:
        return _handle_failure(
            session,
            exc,
            request_id=request_id,
            principal=principal,
            allow_read_denial_commit=True,
        )
    finally:
        if session is not None:
            _close(session)


__all__ = ["IMPLEMENTED_S1A_INTENTS", "router"]
