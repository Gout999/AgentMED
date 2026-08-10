"""Unified, secret-safe public error envelope and transport mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import AnyUrl, Field, StrictBool, StrictInt, model_validator

from .models import AuditRef, OperationId, RequestId, SchemaVersion, WireModel, WorkspaceId


AUTHENTICATION_CODES = frozenset(
    {
        "AUTHENTICATION_REQUIRED",
        "TOKEN_INVALID",
        "TOKEN_EXPIRED",
        "TOKEN_NOT_YET_VALID",
        "TOKEN_REVOKED",
        "AUDIENCE_MISMATCH",
        "ISSUER_MISMATCH",
        "SIGNATURE_INVALID",
    }
)


class PublicErrorDetail(WireModel):
    code: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
    message: Annotated[str, Field(min_length=1, max_length=500)]
    retryable: StrictBool
    retry_after_ms: Annotated[StrictInt, Field(ge=0)] | None = None
    request_id: RequestId
    operation_id: OperationId | None = None
    audit_ref: AuditRef | None
    audit_status: Literal["RECORDED", "UNAVAILABLE", "NOT_APPLICABLE"]
    details: dict[str, Any]
    help_url: AnyUrl | None = None

    @model_validator(mode="after")
    def audit_state_is_truthful(self) -> "PublicErrorDetail":
        if self.audit_status == "RECORDED" and self.audit_ref is None:
            raise ValueError("RECORDED audit status requires audit_ref")
        if self.audit_status != "RECORDED" and self.audit_ref is not None:
            raise ValueError("audit_ref must be null when audit was not recorded")
        if self.code == "AUDIT_UNAVAILABLE" and not (
            self.audit_status == "UNAVAILABLE"
            and self.audit_ref is None
            and self.retryable
        ):
            raise ValueError(
                "AUDIT_UNAVAILABLE requires UNAVAILABLE, null audit_ref, and retryable=true"
            )
        return self


class PublicErrorEnvelope(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId | None
    workspace_resolved: StrictBool
    error: PublicErrorDetail

    @model_validator(mode="after")
    def workspace_state_is_truthful(self) -> "PublicErrorEnvelope":
        if self.workspace_resolved != (self.workspace_id is not None):
            raise ValueError("workspace_id presence must match workspace_resolved")
        if self.error.code in AUTHENTICATION_CODES and (
            self.workspace_resolved or self.workspace_id is not None
        ):
            raise ValueError("authentication errors cannot claim a resolved workspace")
        return self


@dataclass(frozen=True)
class ErrorSpec:
    status_code: int
    message: str
    retryable: bool = False
    default_retry_after_ms: int | None = None


ERROR_SPECS: dict[str, ErrorSpec] = {
    "AUTHENTICATION_REQUIRED": ErrorSpec(401, "A bearer credential is required."),
    "TOKEN_INVALID": ErrorSpec(401, "The bearer credential is invalid."),
    "TOKEN_EXPIRED": ErrorSpec(401, "The bearer credential has expired."),
    "TOKEN_NOT_YET_VALID": ErrorSpec(401, "The bearer credential is not yet valid."),
    "TOKEN_REVOKED": ErrorSpec(401, "The bearer credential has been revoked."),
    "AUDIENCE_MISMATCH": ErrorSpec(401, "The bearer credential audience is not accepted."),
    "ISSUER_MISMATCH": ErrorSpec(401, "The bearer credential issuer is not accepted."),
    "SIGNATURE_INVALID": ErrorSpec(401, "The bearer credential signature is invalid."),
    "SCOPE_FORBIDDEN": ErrorSpec(403, "The accepted principal lacks the required scope."),
    "WORKSPACE_ACCESS_DENIED": ErrorSpec(403, "The accepted principal cannot access this workspace."),
    "REQUEST_INVALID": ErrorSpec(400, "The request does not satisfy the public contract."),
    "IDEMPOTENCY_KEY_REQUIRED": ErrorSpec(400, "Idempotency-Key is required for this mutation."),
    "RESOURCE_NOT_FOUND": ErrorSpec(404, "The requested resource was not found."),
    "IDEMPOTENCY_CONFLICT": ErrorSpec(409, "The idempotency key is already bound to different request content."),
    "CONTRACT_VERSION_UNSUPPORTED": ErrorSpec(412, "The requested public contract version is unsupported."),
    "CONTENT_TOO_LARGE": ErrorSpec(413, "The request content exceeds the accepted limit."),
    "UNSUPPORTED_MEDIA_TYPE": ErrorSpec(415, "The request media type is unsupported."),
    "VALIDATION_FAILED": ErrorSpec(422, "The request is structurally valid but semantically unacceptable."),
    "RATE_LIMITED": ErrorSpec(429, "The request rate limit was exceeded.", True, 1000),
    "DEPENDENCY_UNAVAILABLE": ErrorSpec(503, "A required dependency is unavailable; verify the connection and retry.", True, 1000),
    "AUDIT_UNAVAILABLE": ErrorSpec(503, "The authoritative audit transaction could not be committed.", True, 1000),
    "INTERNAL_ERROR": ErrorSpec(500, "An internal error prevented the request from completing."),
}


_UNSAFE_DETAIL_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "exception",
        "jti",
        "password",
        "provider_message",
        "raw",
        "raw_jti",
        "raw_token",
        "secret",
        "stack",
        "traceback",
        "token",
        "upstream_response",
    }
)


def _validate_safe_details(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in _UNSAFE_DETAIL_KEYS:
                location = ".".join((*path, str(key)))
                raise ValueError(f"unsafe error detail key: {location}")
            _validate_safe_details(item, (*path, str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_safe_details(item, (*path, str(index)))


@dataclass(frozen=True)
class MappedPublicError:
    status_code: int
    envelope: PublicErrorEnvelope

    @property
    def headers(self) -> dict[str, str]:
        headers = {"X-CaseLoop-Contract-Version": "1.0"}
        if self.status_code == 429 and self.envelope.error.retry_after_ms is not None:
            seconds = (self.envelope.error.retry_after_ms + 999) // 1000
            headers["Retry-After"] = str(seconds)
        return headers


def map_public_error(
    code: str,
    *,
    request_id: str,
    workspace_id: str | None = None,
    operation_id: str | None = None,
    audit_ref: str | None = None,
    details: dict[str, Any] | None = None,
    retry_after_ms: int | None = None,
) -> MappedPublicError:
    """Map a stable domain code to the exact public envelope.

    Unknown codes collapse to ``INTERNAL_ERROR`` so internal exception names or
    dependency text never cross the public boundary.
    """

    public_code = code if code in ERROR_SPECS else "INTERNAL_ERROR"
    spec = ERROR_SPECS[public_code]
    safe_details = {} if details is None else details
    _validate_safe_details(safe_details)

    if public_code in AUTHENTICATION_CODES:
        workspace_id = None
        operation_id = None
        audit_ref = None
        audit_status: Literal["RECORDED", "UNAVAILABLE", "NOT_APPLICABLE"] = "NOT_APPLICABLE"
    elif public_code == "AUDIT_UNAVAILABLE":
        audit_ref = None
        audit_status = "UNAVAILABLE"
    elif audit_ref is not None:
        audit_status = "RECORDED"
    else:
        audit_status = "NOT_APPLICABLE"

    retry_delay = retry_after_ms
    if spec.retryable and retry_delay is None:
        retry_delay = spec.default_retry_after_ms
    if not spec.retryable:
        retry_delay = None

    envelope = PublicErrorEnvelope(
        schema_version="1.0",
        workspace_id=workspace_id,
        workspace_resolved=workspace_id is not None,
        error=PublicErrorDetail(
            code=public_code,
            message=spec.message,
            retryable=spec.retryable,
            retry_after_ms=retry_delay,
            request_id=request_id,
            operation_id=operation_id,
            audit_ref=audit_ref,
            audit_status=audit_status,
            details=safe_details,
            help_url=f"https://docs.caseloop.dev/errors/{public_code}",
        ),
    )
    return MappedPublicError(status_code=spec.status_code, envelope=envelope)


__all__ = [
    "AUTHENTICATION_CODES",
    "ERROR_SPECS",
    "MappedPublicError",
    "PublicErrorDetail",
    "PublicErrorEnvelope",
    "map_public_error",
]
