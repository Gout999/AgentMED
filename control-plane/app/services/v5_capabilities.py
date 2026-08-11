"""Audited R2 public capability discovery with an exact transport allowlist."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Sequence

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


# Frozen R2 fallback defaults (C3).  The C1 activated-operation manifest
# (contracts/v5/generated/operation-manifest.json) is the authoritative
# derivation source for the allowlist and the trust-role sets below; these
# legacy literals apply only when the manifest omits the optional
# allowed_principal_types / required_trust_roles_any_of fields, preserving
# the pre-C3 hardcoded values byte-for-byte.
_ALL_PRINCIPAL_TYPES = ("human", "external_agent", "service", "connector")
_HUMAN_OR_SERVICE = ("human", "service")
_LEGACY_CATALOG_TRUST_ROLES = ("integrator", "catalog_admin")
_LEGACY_MANIFEST_TRUST_ROLES = ("integrator", "catalog_admin", "trusted_builder")


class V5CapabilitiesManifestError(RuntimeError):
    """Fail-closed boundary for loading the C1 activated-operation manifest."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class V5OperationManifest:
    catalog_trust_roles: tuple[str, ...]
    manifest_trust_roles: tuple[str, ...]
    implemented_intents: tuple[dict[str, object], ...]


def _manifest_invalid(detail: str) -> V5CapabilitiesManifestError:
    return V5CapabilitiesManifestError(
        f"v5.capabilities.operation_manifest_invalid: {detail}"
    )


def _operation_manifest_path(explicit: str | Path | None = None) -> Path:
    """Discover the C1 operation manifest under the v5 contracts root.

    Mirrors ``v5_authority.discover_v5_contracts_root``: the repository root
    is derived from this module's location, with the same deployment
    candidates, and the first candidate that actually carries the manifest
    wins; otherwise fail closed.
    """
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    module_repo = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            module_repo / "contracts" / "v5",
            Path("/srv/contracts/v5"),
            Path("/app/contracts/v5"),
        ]
    )
    for root in candidates:
        manifest = root / "generated" / "operation-manifest.json"
        if manifest.is_file():
            return manifest.resolve()
    raise V5CapabilitiesManifestError(
        "v5.capabilities.operation_manifest_unavailable"
    )


def _validated_operation(operation: object) -> dict[str, Any]:
    if not isinstance(operation, dict):
        raise _manifest_invalid("operations entries must be objects")
    name = operation.get("intent")
    scope = operation.get("scope")
    execution_mode = operation.get("execution_mode")
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(scope, str)
        or not scope
        or not isinstance(execution_mode, str)
        or not execution_mode
    ):
        raise _manifest_invalid("intent/scope/execution_mode are required strings")
    allowed = operation.get("allowed_principal_types")
    if allowed is not None and (
        not isinstance(allowed, list)
        or not allowed
        or not all(isinstance(item, str) and item for item in allowed)
    ):
        raise _manifest_invalid(
            f"{name}: allowed_principal_types must be a non-empty string list"
        )
    roles = operation.get("required_trust_roles_any_of")
    if roles is not None and (
        not isinstance(roles, list)
        or not roles
        or not all(isinstance(item, str) and item for item in roles)
    ):
        raise _manifest_invalid(
            f"{name}: required_trust_roles_any_of must be a non-empty string list"
        )
    return operation


def _resolve_principal_default(kind: object, name: str) -> tuple[str, ...]:
    if kind == "mutation":
        return _HUMAN_OR_SERVICE
    if kind == "query":
        return _ALL_PRINCIPAL_TYPES
    raise _manifest_invalid(
        f"{name}: cannot resolve allowed_principal_types default without kind"
    )


def _derive_trust_roles(
    operations: list[dict[str, Any]],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    catalog: list[str] = []
    manifest_roles: tuple[str, ...] | None = None
    for operation in operations:
        name = operation["intent"]
        roles = operation.get("required_trust_roles_any_of")
        if roles is None:
            continue
        if name == "system-manifests.import":
            manifest_roles = tuple(roles)
        elif operation.get("kind") == "mutation":
            for role in roles:
                if role not in catalog:
                    catalog.append(role)
    catalog_roles = tuple(catalog) or _LEGACY_CATALOG_TRUST_ROLES
    return catalog_roles, manifest_roles or _LEGACY_MANIFEST_TRUST_ROLES


def _derive_intent(
    operation: dict[str, Any],
    *,
    catalog_trust_roles: tuple[str, ...],
    manifest_trust_roles: tuple[str, ...],
) -> dict[str, object]:
    name = operation["intent"]
    allowed = operation.get("allowed_principal_types")
    if allowed is None:
        principal_types = _resolve_principal_default(
            operation.get("kind"), name
        )
    else:
        principal_types = tuple(allowed)
    intent: dict[str, object] = {
        "name": name,
        "scope": operation["scope"],
        "execution_mode": operation["execution_mode"],
        "principal_types": principal_types,
    }
    roles = operation.get("required_trust_roles_any_of")
    if roles is None:
        kind = operation.get("kind")
        if name == "system-manifests.import":
            intent["trust_roles"] = manifest_trust_roles
        elif kind == "mutation":
            intent["trust_roles"] = catalog_trust_roles
        elif kind is None:
            raise _manifest_invalid(
                f"{name}: cannot resolve trust-role default without kind"
            )
    else:
        intent["trust_roles"] = tuple(roles)
    return intent


@lru_cache(maxsize=8)
def _load_operation_manifest_cached(manifest_path: str) -> V5OperationManifest:
    path = Path(manifest_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise V5CapabilitiesManifestError(
            "v5.capabilities.operation_manifest_unavailable"
        ) from exc
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise _manifest_invalid("manifest is not valid JSON") from exc
    if not isinstance(document, dict):
        raise _manifest_invalid("manifest root must be an object")
    operations = document.get("operations")
    if not isinstance(operations, list) or not operations:
        raise _manifest_invalid("operations must be a non-empty list")
    if document.get("activated_intent_count") != len(operations):
        raise _manifest_invalid(
            "activated_intent_count does not match operations length"
        )
    validated = [_validated_operation(operation) for operation in operations]
    names = [operation["intent"] for operation in validated]
    if len(names) != len(set(names)):
        raise _manifest_invalid("duplicate activated intent names")
    catalog_trust_roles, manifest_trust_roles = _derive_trust_roles(validated)
    implemented_intents = tuple(
        _derive_intent(
            operation,
            catalog_trust_roles=catalog_trust_roles,
            manifest_trust_roles=manifest_trust_roles,
        )
        for operation in validated
    )
    return V5OperationManifest(
        catalog_trust_roles=catalog_trust_roles,
        manifest_trust_roles=manifest_trust_roles,
        implemented_intents=implemented_intents,
    )


def load_v5_operation_manifest(
    explicit: str | Path | None = None,
) -> V5OperationManifest:
    """Load and derive the R2 capability allowlist from the C1 manifest.

    Mirrors ``v5_authority.load_v5_contract_catalog``: repo-root discovery
    with the same candidate roots, an lru_cache keyed on the resolved path,
    and fail-closed errors when the manifest is missing or inconsistent.
    """
    return _load_operation_manifest_cached(str(_operation_manifest_path(explicit)))


_LOADED_OPERATION_MANIFEST = load_v5_operation_manifest()

_CATALOG_TRUST_ROLES: tuple[str, ...] = (
    _LOADED_OPERATION_MANIFEST.catalog_trust_roles
)
_MANIFEST_TRUST_ROLES: tuple[str, ...] = (
    _LOADED_OPERATION_MANIFEST.manifest_trust_roles
)

IMPLEMENTED_V5_PUBLIC_INTENTS: tuple[dict[str, object], ...] = (
    _LOADED_OPERATION_MANIFEST.implemented_intents
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
    "V5CapabilitiesManifestError",
    "V5CapabilitiesService",
    "V5OperationManifest",
    "load_v5_operation_manifest",
]
