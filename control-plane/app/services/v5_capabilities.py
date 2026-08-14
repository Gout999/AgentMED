"""Audited V5 public capability discovery.

This service advertises only the stage slices that are implemented by both an
HTTP route and the explicit-major CLI.  It never derives capabilities from the
broader V5 target registry or exposes later-stage skeletons.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.v5_capability_models import (
    V5CapabilityPrincipal,
    V5EnabledIntent,
    V5ServerCapabilitiesData,
    V5ServerCapabilitiesResponse,
)
from app.services.v4_audit import V4AuditService, V4AuditUnavailable


_ALL_PRINCIPAL_TYPES = ("human", "external_agent", "service", "connector")
_HUMAN_OR_SERVICE = ("human", "service")

IMPLEMENTED_V5_PUBLIC_INTENTS: tuple[dict[str, object], ...] = (
    {
        "name": "capabilities.get",
        "scope": "capabilities:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "applications.register",
        "scope": "applications:manage",
        "principal_types": _HUMAN_OR_SERVICE,
    },
    {
        "name": "applications.get",
        "scope": "applications:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "environments.register",
        "scope": "applications:manage",
        "principal_types": _HUMAN_OR_SERVICE,
    },
    {
        "name": "environments.get",
        "scope": "applications:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "system-components.register",
        "scope": "applications:manage",
        "principal_types": _HUMAN_OR_SERVICE,
    },
    {
        "name": "system-components.get",
        "scope": "applications:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "dependency-edges.record",
        "scope": "applications:manage",
        "principal_types": _HUMAN_OR_SERVICE,
    },
    {
        "name": "dependency-edges.get",
        "scope": "applications:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "system-manifests.import",
        "scope": "system_manifests:import",
        "principal_types": _HUMAN_OR_SERVICE,
    },
    {
        "name": "system-versions.get",
        "scope": "system_versions:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "system-versions.diff",
        "scope": "system_versions:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "cases.bind-application",
        "scope": "cases:bind",
        "principal_types": _HUMAN_OR_SERVICE,
    },
    {
        "name": "case-application-bindings.get",
        "scope": "cases:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "acceptance-criteria.propose",
        "scope": "acceptance_criteria:propose",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "acceptance-criteria.get",
        "scope": "acceptance_criteria:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "acceptance-criteria.confirm",
        "scope": "acceptance_criteria:confirm",
        "principal_types": ("human",),
    },
)


class V5CapabilitiesError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        audit_ref: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        self.code = code
        self.details: dict[str, object] = {}
        self.audit_ref = audit_ref
        self.workspace_id = workspace_id
        self.rollback_required = True
        super().__init__(code)


class V5CapabilitiesService:
    def __init__(
        self,
        session: Session,
        *,
        audit_service: V4AuditService | None = None,
        clock=None,
    ) -> None:
        self.session = session
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.audit = audit_service or V4AuditService(session, clock=self.clock)

    def get_capabilities(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        server_version: str,
        implemented_intents: Sequence[dict[str, object]] = IMPLEMENTED_V5_PUBLIC_INTENTS,
    ) -> V5ServerCapabilitiesResponse:
        if (
            "capabilities:read" not in principal.scopes
            or principal.requested_context.workspace_id != principal.workspace_id
            or principal.requested_context.required_scope != "capabilities:read"
        ):
            raise V5CapabilitiesError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )

        try:
            enabled = [
                V5EnabledIntent.model_validate(
                    {
                        "name": raw["name"],
                        "scope": raw["scope"],
                        "execution_mode": "synchronous",
                        "http": True,
                        "cli": True,
                    }
                )
                for raw in implemented_intents
                if raw.get("scope") in principal.scopes
                and principal.principal_type in raw.get("principal_types", ())
            ]
            data = V5ServerCapabilitiesData(
                server_version=server_version,
                api_major=2,
                contract_version="2.0",
                principal=V5CapabilityPrincipal(
                    principal_id=principal.principal_id,
                    principal_type=principal.principal_type,
                    scopes=principal.scopes,
                    credential_expires_at=principal.expires_at,
                ),
                enabled_intents=enabled,
                disabled_intents=[],
                generated_at=self.clock(),
            )
        except (TypeError, ValueError, ValidationError) as exc:
            raise V5CapabilitiesError(
                "INTERNAL_ERROR", workspace_id=principal.workspace_id
            ) from exc

        try:
            audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action="public.v5.capabilities.get",
                target="public_server:v5_capabilities",
                params={
                    "request_id": request_id,
                    "server_version": server_version,
                    "enabled_intents": [intent.name for intent in enabled],
                },
                result="success",
                trace_id=request_id,
            )
        except V4AuditUnavailable as exc:
            raise V5CapabilitiesError(
                "AUDIT_UNAVAILABLE", workspace_id=principal.workspace_id
            ) from exc

        try:
            return V5ServerCapabilitiesResponse(
                schema_version="2.0",
                workspace_id=principal.workspace_id,
                request_id=request_id,
                audit_ref=audit.audit_ref,
                data=data,
            )
        except ValidationError as exc:
            raise V5CapabilitiesError(
                "INTERNAL_ERROR",
                audit_ref=audit.audit_ref,
                workspace_id=principal.workspace_id,
            ) from exc


__all__ = [
    "IMPLEMENTED_V5_PUBLIC_INTENTS",
    "V5CapabilitiesError",
    "V5CapabilitiesService",
]
