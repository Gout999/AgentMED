"""Pure public-header parsing and accepted-principal validation.

This module intentionally stops before credential lookup.  The opaque bearer is
held as ``SecretStr`` only long enough for a later resolver to consume it; it is
excluded from dumps and reprs and no raw token identifier is accepted in the
resolved principal context.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import AnyUrl, AwareDatetime, Field, SecretStr, ValidationError, field_validator, model_validator

from .models import (
    Digest,
    EnvironmentId,
    PrincipalId,
    ProjectId,
    RequestId,
    Scope,
    SchemaVersion,
    WireModel,
    WorkspaceId,
    _require_unique,
)


class HeaderContractViolation(ValueError):
    """Secret-safe, machine-readable failure from public header parsing."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class PublicRequestHeaders(WireModel):
    """Validated public transport context before credential resolution."""

    bearer_token: SecretStr = Field(repr=False, exclude=True)
    requested_workspace_id: WorkspaceId
    contract_version: Literal["1.0"]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)] | None = None
    request_id: RequestId | None = None
    client_version: Annotated[str, Field(min_length=1, max_length=128)] | None = None

    @classmethod
    def from_headers(
        cls, headers: Mapping[str, str], *, mutation: bool = False
    ) -> "PublicRequestHeaders":
        normalized: dict[str, str] = {}
        for raw_name, value in headers.items():
            name = raw_name.lower()
            if name in normalized:
                raise HeaderContractViolation(
                    "REQUEST_INVALID", f"duplicate header: {raw_name.lower()}"
                )
            normalized[name] = value

        authorization = normalized.get("authorization")
        if authorization is None:
            raise HeaderContractViolation(
                "AUTHENTICATION_REQUIRED", "A bearer credential is required."
            )
        bearer = re.fullmatch(r"Bearer ([^\s]+)", authorization, flags=re.IGNORECASE)
        if bearer is None:
            raise HeaderContractViolation(
                "TOKEN_INVALID", "The bearer credential header is invalid."
            )

        workspace_id = normalized.get("x-caseloop-workspace-id")
        if workspace_id is None:
            raise HeaderContractViolation(
                "REQUEST_INVALID", "X-CaseLoop-Workspace-ID is required."
            )
        contract_version = normalized.get("x-caseloop-contract-version")
        if contract_version is None:
            raise HeaderContractViolation(
                "REQUEST_INVALID", "X-CaseLoop-Contract-Version is required."
            )
        if contract_version != "1.0":
            raise HeaderContractViolation(
                "CONTRACT_VERSION_UNSUPPORTED", "The requested public contract version is unsupported."
            )

        idempotency_key = normalized.get("idempotency-key")
        if mutation and idempotency_key is None:
            raise HeaderContractViolation(
                "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key is required for mutations."
            )

        try:
            return cls(
                bearer_token=SecretStr(bearer.group(1)),
                requested_workspace_id=workspace_id,
                contract_version=contract_version,
                idempotency_key=idempotency_key,
                request_id=normalized.get("x-request-id"),
                client_version=normalized.get("x-caseloop-client-version"),
            )
        except ValidationError as exc:
            # Pydantic messages contain field metadata, never the excluded token.
            fields = sorted({str(item["loc"][0]) for item in exc.errors()})
            raise HeaderContractViolation(
                "REQUEST_INVALID", f"Invalid public request header fields: {', '.join(fields)}."
            ) from None


class RequestedPrincipalContext(WireModel):
    workspace_id: WorkspaceId
    project_id: ProjectId | None
    environment_id: EnvironmentId | None
    required_scope: Scope


class AcceptedPrincipalContext(WireModel):
    """Exact server-resolved authorization context accepted for one request."""

    schema_version: SchemaVersion
    principal_id: PrincipalId
    principal_type: Literal["human", "external_agent", "service", "connector"]
    issuer: AnyUrl
    subject: Annotated[str, Field(min_length=1, max_length=256)]
    audiences: Annotated[list[Annotated[str, Field(min_length=1, max_length=128)]], Field(min_length=1)]
    workspace_id: WorkspaceId
    project_ids: list[ProjectId]
    environment_ids: list[EnvironmentId]
    scopes: Annotated[list[Scope], Field(min_length=1)]
    credential_id: Annotated[str, Field(pattern=r"^cred_[0-9A-Za-z]{8,64}$")]
    jti_digest: Digest
    issued_at: AwareDatetime
    not_before: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: None
    revocation_checked_at: AwareDatetime
    requested_context: RequestedPrincipalContext
    evaluated_at: AwareDatetime
    claims_digest: Digest

    @field_validator("audiences", "project_ids", "environment_ids", "scopes")
    @classmethod
    def grants_are_unique(cls, value: list[Any]) -> list[Any]:
        return _require_unique(value, "accepted principal grant")

    @model_validator(mode="after")
    def accepted_context_is_bound(self) -> "AcceptedPrincipalContext":
        if "caseloop-public-api" not in self.audiences:
            raise ValueError("accepted principal audience does not include caseloop-public-api")
        if not (self.not_before <= self.evaluated_at < self.expires_at):
            raise ValueError("accepted principal is outside not-before/expiry bounds")
        requested = self.requested_context
        if requested.workspace_id != self.workspace_id:
            raise ValueError("requested workspace does not match accepted workspace")
        if requested.project_id is not None and requested.project_id not in self.project_ids:
            raise ValueError("requested project is not present in accepted grants")
        if (
            requested.environment_id is not None
            and requested.environment_id not in self.environment_ids
        ):
            raise ValueError("requested environment is not present in accepted grants")
        if requested.required_scope not in self.scopes:
            raise ValueError("required scope is not present in accepted grants")
        return self


__all__ = [
    "AcceptedPrincipalContext",
    "HeaderContractViolation",
    "PublicRequestHeaders",
    "RequestedPrincipalContext",
]
