"""V5-1A AI application catalog public HTTP boundary (/api/v2).

The router owns transport parsing, opaque credential resolution and transaction
closure.  Authoritative lifecycle work stays in the injected service; the
router never recreates its state machines or invents audit/evidence records.
The helper set mirrors the frozen ``public_v4`` boundary but pins the contract
version to 2.0 and never falls back to v1.
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
from app.public_api.auth_contract import AcceptedPrincipalContext, HeaderContractViolation
from app.public_api.errors import map_public_error
from app.public_api.v2_contract import PublicV2RequestHeaders
from app.public_api.v5_models import (
    AcceptanceCriteriaConfirmRequest,
    AcceptanceCriteriaConfirmResponse,
    AcceptanceCriteriaGetResponse,
    AcceptanceCriteriaProposeRequest,
    AcceptanceCriteriaProposeResponse,
    ApplicationBindingGetResponse,
    ApplicationGetResponse,
    ApplicationRegisterRequest,
    ApplicationRegisterResponse,
    CaseBindApplicationRequest,
    CaseBindApplicationResponse,
    ComponentGetResponse,
    ComponentRegisterRequest,
    ComponentRegisterResponse,
    DependencyEdgeGetResponse,
    DependencyEdgeRecordRequest,
    DependencyEdgeRecordResponse,
    EnvironmentGetResponse,
    EnvironmentRegisterRequest,
    EnvironmentRegisterResponse,
    SystemManifestImportRequest,
    SystemManifestImportResponse,
    SystemVersionDiffResponse,
    SystemVersionGetResponse,
)
from app.services.acceptance import AcceptanceError, AcceptanceService
from app.services.application_catalog import ApplicationCatalogError, V5ReadDenial as CatalogReadDenial
from app.services.case_binding import (
    CaseBindingError,
    CaseBindingReadDenial as CaseBindingDenial,
    CaseBindingService,
)
from app.services.system_versions import SystemVersionsError, V5ReadDenial

router = APIRouter(prefix="/api/v2", tags=["public-v5-1a-catalog"])

_APPLICATION_ID = re.compile(r"^app_[0-9A-Za-z]{8,64}$")
_ENVIRONMENT_ID = re.compile(r"^env_[0-9A-Za-z]{8,64}$")
_COMPONENT_ID = re.compile(r"^cmp_[0-9A-Za-z]{8,64}$")
_EDGE_ID = re.compile(r"^de_[0-9A-Za-z]{8,64}$")
_VERSION_SET_ID = re.compile(r"^vset_[0-9A-Za-z]{8,64}$")
_CASE_ID = re.compile(r"^case_[0-9A-Za-z]{8,64}$")
_ACCEPTANCE_REVISION_ID = re.compile(r"^acr_[0-9A-Za-z]{8,64}$")
_REQUEST_ID = re.compile(r"^req_[0-9A-Za-z]{8,64}$")
_MAX_BODY_BYTES = 256_000
_PUBLIC_HEADER_NAMES = frozenset(
    {
        "authorization",
        "x-caseloop-workspace-id",
        "x-caseloop-contract-version",
        "x-caseloop-idempotency-key",
        "x-request-id",
        "x-caseloop-client-version",
    }
)
_CONTRACT_VERSION = "2.0"
_ANONYMOUS_AUDIT_ACTOR = "system:public-v2-gate"


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
        headers={"X-CaseLoop-Contract-Version": _CONTRACT_VERSION},
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
    headers = {"X-CaseLoop-Contract-Version": _CONTRACT_VERSION}
    if mapped.status_code == 429 and mapped.envelope.error.retry_after_ms is not None:
        seconds = (mapped.envelope.error.retry_after_ms + 999) // 1000
        headers["Retry-After"] = str(seconds)
    return JSONResponse(
        status_code=mapped.status_code,
        content=mapped.envelope.model_dump(mode="json", exclude_none=False),
        headers=headers,
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

    if (
        allow_read_denial_commit
        and session is not None
        and principal is not None
        and isinstance(exc, (V5ReadDenial, CatalogReadDenial))
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
        headers = {"X-CaseLoop-Contract-Version": _CONTRACT_VERSION}
        return JSONResponse(
            status_code=mapped.status_code,
            content=mapped.envelope.model_dump(mode="json", exclude_none=False),
            headers=headers,
        )

    if session is not None:
        _rollback(session)
    if isinstance(exc, _CommitFailure):
        failure: BaseException = _RouteFailure("AUDIT_UNAVAILABLE")
    elif isinstance(exc, ValidationError):
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


def _catalog_service(request: Request, session: Session) -> Any:
    factory = getattr(request.app.state, "application_catalog_service_factory", None)
    if factory is not None:
        return factory(session)

    from app.services.application_catalog import ApplicationCatalogService

    return ApplicationCatalogService(session)


def _system_versions_service(request: Request, session: Session) -> Any:
    factory = getattr(request.app.state, "system_versions_service_factory", None)
    if factory is not None:
        return factory(session)

    from app.services.system_versions import SystemVersionsService

    return SystemVersionsService(session)


def _case_binding_service(request: Request, session: Session) -> CaseBindingService:
    factory = getattr(request.app.state, "case_binding_service_factory", None)
    if factory is not None:
        return factory(session)

    return CaseBindingService(session)


def _acceptance_service(request: Request, session: Session) -> AcceptanceService:
    factory = getattr(request.app.state, "acceptance_service_factory", None)
    if factory is not None:
        return factory(session)

    return AcceptanceService(session)


def _authenticate(
    request: Request,
    session: Session,
    *,
    headers: PublicV2RequestHeaders,
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


def _validate_path(value: str, pattern: re.Pattern[str], field: str) -> None:
    if pattern.fullmatch(value) is None:
        raise _RouteFailure("VALIDATION_FAILED", {"fields": [field]})


async def _parse_body(request: Request, model: type[ResponseModel]) -> ResponseModel:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise _RouteFailure("UNSUPPORTED_MEDIA_TYPE")
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > _MAX_BODY_BYTES:
                raise _RouteFailure("CONTENT_TOO_LARGE")
        except ValueError:
            raise _RouteFailure("REQUEST_INVALID", {"fields": ["content-length"]}) from None

    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > _MAX_BODY_BYTES:
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
        return model.model_validate(payload)
    except ValidationError as exc:
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in exc.errors()})
        raise _RouteFailure("VALIDATION_FAILED", {"fields": fields}) from None


def _audit_header_violation(
    session: Session | None,
    request: Request,
    request_id: str,
    code: str,
) -> str | None:
    """Attempt a v2 header-rejection audit; return its ref when recorded.

    Only attempted when a workspace context is present; anonymous/authentication
    failures carry no workspace and are mapped without an audit row.
    """

    workspace_id = request.headers.get("x-caseloop-workspace-id")
    if session is None or not isinstance(workspace_id, str) or not workspace_id:
        return None
    if code in {
        "AUTHENTICATION_REQUIRED",
        "TOKEN_INVALID",
        "AUDIENCE_MISMATCH",
        "ISSUER_MISMATCH",
        "SIGNATURE_INVALID",
    }:
        return None
    try:
        from app.services.v4_audit import V4AuditService

        recorded = V4AuditService(session).record(
            workspace_id=workspace_id,
            actor_principal=_ANONYMOUS_AUDIT_ACTOR,
            action="public-v2.header_rejected",
            target="request",
            params={"request_id": request_id, "code": code},
            result="denied",
            error_code=code,
            trace_id=request_id,
        )
        return recorded.audit_ref
    except Exception:
        return None


@router.post(
    "/applications",
    response_model=ApplicationRegisterResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": ApplicationRegisterRequest.model_json_schema()}
            },
        }
    },
)
async def register_application(request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(
            _public_headers(request), mutation=True
        )
        submission = await _parse_body(request, ApplicationRegisterRequest)
        session = _session_for(request)
        principal, resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="applications:manage",
        )
        principal = resolver.bind_requested_context(
            principal,
            project_id=submission.project_id,
            environment_id=None,
            required_scope="applications:manage",
        )
        request.state.public_principal = principal
        service = _catalog_service(request, session)
        result = service.register_application(
            submission,
            principal=principal,
            idempotency_key=headers.idempotency_key,
            request_id=request_id,
        )
        response = ApplicationRegisterResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=201)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.get("/applications/{application_id}", response_model=ApplicationGetResponse)
def get_application(application_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="applications:read"
        )
        _validate_path(application_id, _APPLICATION_ID, "application_id")
        result = _catalog_service(request, session).get_application(
            principal=principal,
            application_id=application_id,
            request_id=request_id,
        )
        response = ApplicationGetResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=200)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.post(
    "/environments",
    response_model=EnvironmentRegisterResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": EnvironmentRegisterRequest.model_json_schema()
                }
            },
        }
    },
)
async def register_environment(request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(
            _public_headers(request), mutation=True
        )
        submission = await _parse_body(request, EnvironmentRegisterRequest)
        session = _session_for(request)
        principal, resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="applications:manage",
        )
        principal = resolver.bind_requested_context(
            principal,
            project_id=None,
            environment_id=None,
            required_scope="applications:manage",
        )
        request.state.public_principal = principal
        service = _catalog_service(request, session)
        result = service.register_environment(
            submission,
            principal=principal,
            idempotency_key=headers.idempotency_key,
            request_id=request_id,
        )
        response = EnvironmentRegisterResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=201)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.get("/environments/{environment_id}", response_model=EnvironmentGetResponse)
def get_environment(environment_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="applications:read"
        )
        _validate_path(environment_id, _ENVIRONMENT_ID, "environment_id")
        result = _catalog_service(request, session).get_environment(
            principal=principal,
            environment_id=environment_id,
            request_id=request_id,
        )
        response = EnvironmentGetResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=200)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.post(
    "/system-components",
    response_model=ComponentRegisterResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": ComponentRegisterRequest.model_json_schema()
                }
            },
        }
    },
)
async def register_component(request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(
            _public_headers(request), mutation=True
        )
        submission = await _parse_body(request, ComponentRegisterRequest)
        session = _session_for(request)
        principal, resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="applications:manage",
        )
        principal = resolver.bind_requested_context(
            principal,
            project_id=None,
            environment_id=None,
            required_scope="applications:manage",
        )
        request.state.public_principal = principal
        service = _catalog_service(request, session)
        result = service.register_component(
            submission,
            principal=principal,
            idempotency_key=headers.idempotency_key,
            request_id=request_id,
        )
        response = ComponentRegisterResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=201)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.get("/system-components/{component_id}", response_model=ComponentGetResponse)
def get_component(component_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="applications:read"
        )
        _validate_path(component_id, _COMPONENT_ID, "component_id")
        result = _catalog_service(request, session).get_component(
            principal=principal,
            component_id=component_id,
            request_id=request_id,
        )
        response = ComponentGetResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=200)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.post(
    "/dependency-edges",
    response_model=DependencyEdgeRecordResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": DependencyEdgeRecordRequest.model_json_schema()
                }
            },
        }
    },
)
async def record_dependency_edge(request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(
            _public_headers(request), mutation=True
        )
        submission = await _parse_body(request, DependencyEdgeRecordRequest)
        session = _session_for(request)
        principal, resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="applications:manage",
        )
        principal = resolver.bind_requested_context(
            principal,
            project_id=None,
            environment_id=None,
            required_scope="applications:manage",
        )
        request.state.public_principal = principal
        service = _catalog_service(request, session)
        result = service.record_dependency_edge(
            submission,
            principal=principal,
            idempotency_key=headers.idempotency_key,
            request_id=request_id,
        )
        response = DependencyEdgeRecordResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=201)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.get("/dependency-edges/{edge_id}", response_model=DependencyEdgeGetResponse)
def get_dependency_edge(edge_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="applications:read"
        )
        _validate_path(edge_id, _EDGE_ID, "edge_id")
        result = _catalog_service(request, session).get_dependency_edge(
            principal=principal,
            edge_id=edge_id,
            request_id=request_id,
        )
        response = DependencyEdgeGetResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=200)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


# ---------------------------------------------------------------------------
# V5-1B trusted manifest import / system versions (system-manifests.import,
# system-versions.get, system-versions.diff).
# ---------------------------------------------------------------------------


@router.post(
    "/system-manifests:import",
    response_model=SystemManifestImportResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": SystemManifestImportRequest.model_json_schema()
                }
            },
        }
    },
)
async def import_system_manifest(request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(
            _public_headers(request), mutation=True
        )
        submission = await _parse_body(request, SystemManifestImportRequest)
        session = _session_for(request)
        principal, resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="system_manifests:import",
        )
        principal = resolver.bind_requested_context(
            principal,
            project_id=None,
            environment_id=None,
            required_scope="system_manifests:import",
        )
        request.state.public_principal = principal
        service = _system_versions_service(request, session)
        result = service.import_manifest(
            submission,
            principal=principal,
            idempotency_key=headers.idempotency_key,
            request_id=request_id,
        )
        response = SystemManifestImportResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=201)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.get(
    "/system-versions/{system_version_set_id}",
    response_model=SystemVersionGetResponse,
)
def get_system_version(system_version_set_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="system_versions:read"
        )
        _validate_path(system_version_set_id, _VERSION_SET_ID, "system_version_set_id")
        result = _system_versions_service(request, session).get_system_version(
            system_version_set_id,
            principal=principal,
            request_id=request_id,
        )
        response = SystemVersionGetResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=200)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.get("/system-versions:diff", response_model=SystemVersionDiffResponse)
def diff_system_versions(
    base_system_version_set_id: str,
    target_system_version_set_id: str,
    request: Request,
) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="system_versions:read"
        )
        _validate_path(
            base_system_version_set_id, _VERSION_SET_ID, "base_system_version_set_id"
        )
        _validate_path(
            target_system_version_set_id, _VERSION_SET_ID, "target_system_version_set_id"
        )
        result = _system_versions_service(request, session).diff_system_versions(
            base_system_version_set_id,
            target_system_version_set_id,
            principal=principal,
            request_id=request_id,
        )
        response = SystemVersionDiffResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=200)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


# ---------------------------------------------------------------------------
# V5-1C application case binding / acceptance criteria
# (cases.bind-application, case-application-bindings.get,
# acceptance-criteria.propose/get/confirm).
# ---------------------------------------------------------------------------


@router.post(
    "/cases/{case_id}:bind-application",
    response_model=CaseBindApplicationResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": CaseBindApplicationRequest.model_json_schema()
                }
            },
        }
    },
)
async def bind_case_application(case_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(
            _public_headers(request), mutation=True
        )
        submission = await _parse_body(request, CaseBindApplicationRequest)
        session = _session_for(request)
        principal, resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="cases:bind",
        )
        principal = resolver.bind_requested_context(
            principal,
            project_id=None,
            environment_id=None,
            required_scope="cases:bind",
        )
        request.state.public_principal = principal
        _validate_path(case_id, _CASE_ID, "case_id")
        if submission.case_id != case_id:
            raise _RouteFailure("VALIDATION_FAILED", {"fields": ["case_id"]})
        service = _case_binding_service(request, session)
        result = service.bind_application(
            submission,
            principal=principal,
            idempotency_key=headers.idempotency_key,
            request_id=request_id,
        )
        response = CaseBindApplicationResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=201)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.get(
    "/cases/{case_id}/application-binding",
    response_model=ApplicationBindingGetResponse,
)
def get_case_application_binding(
    case_id: str,
    case_revision: int,
    case_digest: str,
    request: Request,
) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="cases:read"
        )
        _validate_path(case_id, _CASE_ID, "case_id")
        result = _case_binding_service(request, session).get_binding(
            case_id,
            case_revision=case_revision,
            case_digest=case_digest,
            principal=principal,
            request_id=request_id,
        )
        response = ApplicationBindingGetResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=200)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.post(
    "/cases/{case_id}:propose-acceptance-criteria",
    response_model=AcceptanceCriteriaProposeResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": AcceptanceCriteriaProposeRequest.model_json_schema()
                }
            },
        }
    },
)
async def propose_acceptance_criteria(case_id: str, request: Request) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(
            _public_headers(request), mutation=True
        )
        submission = await _parse_body(request, AcceptanceCriteriaProposeRequest)
        session = _session_for(request)
        principal, resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="acceptance_criteria:propose",
        )
        principal = resolver.bind_requested_context(
            principal,
            project_id=None,
            environment_id=None,
            required_scope="acceptance_criteria:propose",
        )
        request.state.public_principal = principal
        _validate_path(case_id, _CASE_ID, "case_id")
        if submission.case_id != case_id:
            raise _RouteFailure("VALIDATION_FAILED", {"fields": ["case_id"]})
        service = _acceptance_service(request, session)
        result = service.propose(
            submission,
            principal=principal,
            idempotency_key=headers.idempotency_key,
            request_id=request_id,
        )
        response = AcceptanceCriteriaProposeResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=201)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.get(
    "/cases/{case_id}/acceptance-criteria",
    response_model=AcceptanceCriteriaGetResponse,
)
def get_acceptance_criteria(
    case_id: str,
    case_revision: int,
    request: Request,
) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(_public_headers(request))
        session = _session_for(request)
        principal, _resolver = _authenticate(
            request, session, headers=headers, required_scope="acceptance_criteria:read"
        )
        _validate_path(case_id, _CASE_ID, "case_id")
        result = _acceptance_service(request, session).get(
            case_id,
            case_revision=case_revision,
            principal=principal,
            request_id=request_id,
        )
        response = AcceptanceCriteriaGetResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=200)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


@router.post(
    "/acceptance-criteria/{acceptance_criteria_revision_id}:confirm",
    response_model=AcceptanceCriteriaConfirmResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": AcceptanceCriteriaConfirmRequest.model_json_schema()
                }
            },
        }
    },
)
async def confirm_acceptance_criteria(
    acceptance_criteria_revision_id: str, request: Request
) -> JSONResponse:
    request_id = _request_id(request)
    session: Any | None = None
    principal: AcceptedPrincipalContext | None = None
    try:
        headers = PublicV2RequestHeaders.from_headers(
            _public_headers(request), mutation=True
        )
        submission = await _parse_body(request, AcceptanceCriteriaConfirmRequest)
        session = _session_for(request)
        principal, resolver = _authenticate(
            request,
            session,
            headers=headers,
            required_scope="acceptance_criteria:confirm",
        )
        principal = resolver.bind_requested_context(
            principal,
            project_id=None,
            environment_id=None,
            required_scope="acceptance_criteria:confirm",
        )
        request.state.public_principal = principal
        _validate_path(
            acceptance_criteria_revision_id,
            _ACCEPTANCE_REVISION_ID,
            "acceptance_criteria_revision_id",
        )
        service = _acceptance_service(request, session)
        result = service.confirm(
            submission,
            principal=principal,
            idempotency_key=headers.idempotency_key,
            request_id=request_id,
        )
        response = AcceptanceCriteriaConfirmResponse.model_validate(result)
        _commit(session)
        return _json_response(response, status_code=201)
    except HeaderContractViolation as exc:
        if session is not None:
            _rollback(session)
        audit_ref = _audit_header_violation(
            session, request, request_id, getattr(exc, "code", "REQUEST_INVALID")
        )
        return _error_response(
            exc,
            request_id=request_id,
            workspace_id=None,
            keep_audit_ref=False,
        ) if audit_ref is None else _error_response(
            _RouteFailure(
                getattr(exc, "code", "REQUEST_INVALID"),
                audit_ref=audit_ref,
            ),
            request_id=request_id,
        )
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


__all__ = ["router"]
