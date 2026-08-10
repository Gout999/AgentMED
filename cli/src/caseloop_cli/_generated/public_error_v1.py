"""Generated frozen CaseLoop public v1 error wire model."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AnyUrl, Field, StrictBool, StrictInt, model_validator

from .public_v1 import AuditRef, OperationId, RequestId, SchemaVersion, WireModel, WorkspaceId


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
            raise ValueError("AUDIT_UNAVAILABLE state is inconsistent")
        return self


class PublicErrorEnvelope(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId | None
    workspace_resolved: StrictBool
    error: PublicErrorDetail

    @model_validator(mode="after")
    def workspace_state_is_truthful(self) -> "PublicErrorEnvelope":
        if self.workspace_resolved != (self.workspace_id is not None):
            raise ValueError("workspace binding is inconsistent")
        if self.error.code in AUTHENTICATION_CODES and (
            self.workspace_resolved or self.workspace_id is not None
        ):
            raise ValueError("authentication errors cannot resolve workspace")
        return self


__all__ = ["AUTHENTICATION_CODES", "PublicErrorEnvelope"]
