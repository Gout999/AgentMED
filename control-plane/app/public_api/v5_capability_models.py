"""Closed wire models for V5 capability discovery.

Capability discovery is deliberately an allowlist: only public intents with a
real HTTP route and CLI command may appear here.  Future V5 skeletons remain
undiscoverable until their own delivery stage activates them.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

from app.public_api.models import (
    AuditRef,
    PrincipalId,
    RequestId,
    WorkspaceId,
    WireModel,
    _require_unique,
)


V5PublicIntentName = Literal[
    "capabilities.get",
    "applications.register",
    "applications.get",
    "environments.register",
    "environments.get",
    "system-components.register",
    "system-components.get",
    "dependency-edges.record",
    "dependency-edges.get",
    "system-manifests.import",
    "system-versions.get",
    "system-versions.diff",
    "cases.bind-application",
    "case-application-bindings.get",
    "acceptance-criteria.propose",
    "acceptance-criteria.get",
    "acceptance-criteria.confirm",
]


class V5CapabilityPrincipal(WireModel):
    principal_id: PrincipalId
    principal_type: Literal["human", "external_agent", "service", "connector"]
    scopes: list[Annotated[str, Field(min_length=1, max_length=128)]]
    credential_expires_at: AwareDatetime

    @field_validator("scopes")
    @classmethod
    def scopes_are_unique(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "scopes")


class V5EnabledIntent(WireModel):
    name: V5PublicIntentName
    scope: Annotated[str, Field(min_length=1, max_length=128)]
    execution_mode: Literal["synchronous"]
    http: StrictBool
    cli: StrictBool

    @model_validator(mode="after")
    def transports_are_real(self) -> "V5EnabledIntent":
        if self.http is not True or self.cli is not True:
            raise ValueError("advertised V5 intents require http=true and cli=true")
        return self


class V5ServerCapabilitiesData(WireModel):
    server_version: Annotated[str, Field(min_length=1, max_length=128)]
    api_major: StrictInt
    contract_version: Literal["2.0"]
    principal: V5CapabilityPrincipal
    enabled_intents: list[V5EnabledIntent]
    disabled_intents: list[V5PublicIntentName]
    generated_at: AwareDatetime

    @field_validator("api_major")
    @classmethod
    def major_is_two(cls, value: int) -> int:
        if value != 2:
            raise ValueError("api_major must be 2")
        return value

    @field_validator("disabled_intents")
    @classmethod
    def skeletons_remain_undiscoverable(
        cls, value: list[V5PublicIntentName]
    ) -> list[V5PublicIntentName]:
        if value:
            raise ValueError("unimplemented V5 skeletons must remain undiscoverable")
        return value

    @field_validator("enabled_intents")
    @classmethod
    def enabled_intents_are_unique(
        cls, value: list[V5EnabledIntent]
    ) -> list[V5EnabledIntent]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("enabled_intents names must be unique")
        return value


class V5ServerCapabilitiesResponse(WireModel):
    schema_version: Literal["2.0"]
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    data: V5ServerCapabilitiesData


__all__ = [
    "V5CapabilityPrincipal",
    "V5EnabledIntent",
    "V5PublicIntentName",
    "V5ServerCapabilitiesData",
    "V5ServerCapabilitiesResponse",
]
