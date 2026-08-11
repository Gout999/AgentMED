"""Audited R2 public capability discovery with an exact transport allowlist."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.models.v4_tables import PublicPrincipal
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.public_api.v5_capability_models import (
    V5CapabilityPrincipal,
    V5EnabledIntent,
    V5ServerCapabilitiesData,
    V5ServerCapabilitiesResponse,
)
from app.services.v4_audit import V4AuditService, V4AuditUnavailable


_ALL_PRINCIPAL_TYPES = ("human", "external_agent", "service", "connector")
_HUMAN_OR_SERVICE = ("human", "service")
_CATALOG_TRUST_ROLES = ("integrator", "catalog_admin")
_MANIFEST_TRUST_ROLES = ("integrator", "catalog_admin", "trusted_builder")

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
        "trust_roles": _CATALOG_TRUST_ROLES,
    },
    {
        "name": "applications.get",
        "scope": "applications:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "applications.list",
        "scope": "applications:read",
        "principal_types": _ALL_PRINCIPAL_TYPES,
    },
    {
        "name": "environments.register",
        "scope": "applications:manage",
        "principal_types": _HUMAN_OR_SERVICE,
        "trust_roles": _CATALOG_TRUST_ROLES,
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
        "trust_roles": _CATALOG_TRUST_ROLES,
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
        "trust_roles": _CATALOG_TRUST_ROLES,
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
        "trust_roles": _MANIFEST_TRUST_ROLES,
        "execution_mode": "synchronous_local_transaction",
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

    def _persisted_trust_roles(
        self, principal: AcceptedPrincipalContext
    ) -> frozenset[str]:
        row = self.session.get(PublicPrincipal, principal.principal_id)
        if (
            row is None
            or row.workspace_id != principal.workspace_id
            or row.state != "ACTIVE"
            or row.revoked_at is not None
            or row.claims_digest != principal.claims_digest
            or row.principal_type != principal.principal_type
            or row.subject_digest != digest_public_subject(principal.subject)
            or row.audiences != principal.audiences
            or row.project_ids != principal.project_ids
            or row.environment_ids != principal.environment_ids
            or row.scopes != principal.scopes
        ):
            raise V5CapabilitiesError(
                "TOKEN_INVALID", workspace_id=principal.workspace_id
            )
        return frozenset(row.trust_roles or [])

    def get_capabilities(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        server_version: str,
        implemented_intents: Sequence[
            dict[str, object]
        ] = IMPLEMENTED_V5_PUBLIC_INTENTS,
    ) -> V5ServerCapabilitiesResponse:
        if (
            "capabilities:read" not in principal.scopes
            or principal.requested_context.workspace_id != principal.workspace_id
            or principal.requested_context.required_scope != "capabilities:read"
        ):
            raise V5CapabilitiesError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )
        trust_roles = self._persisted_trust_roles(principal)

        try:
            enabled = [
                V5EnabledIntent.model_validate(
                    {
                        "name": raw["name"],
                        "scope": raw["scope"],
                        "execution_mode": raw.get("execution_mode", "synchronous"),
                        "http": True,
                        "cli": True,
                    }
                )
                for raw in implemented_intents
                if raw.get("scope") in principal.scopes
                and principal.principal_type in raw.get("principal_types", ())
                and (
                    not raw.get("trust_roles")
                    or bool(trust_roles.intersection(raw.get("trust_roles", ())))
                )
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
