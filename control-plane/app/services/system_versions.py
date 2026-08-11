"""V5-1B immutable system versions and trusted manifest import.

Implements the V5-1B runtime slice over the frozen ``contracts/v5``
ownership model (the five 1B subjects are version-controller owned; the four
V5-1A catalog kinds keep their application-catalog-controller owner):
component revisions with identity assurance, immutable system version sets
with exact topology binding, semantic diff, the trusted one-shot manifest
import (ALL_OR_NOTHING in one local PostgreSQL transaction, idempotent replay
by key and by manifest digest), the independent trusted human approver POLICY
revision import (recorded, never part of the runtime VersionSet), and the
bootstrap assignment (generation=1, previous=null, exact BootstrapAttestation
authority).

Like the V5-1A catalog service this only flushes; the caller owns commit /
rollback.  The manifest wire shape is a DRAFT runtime interpretation
(``field_contract_ref`` is null) and is reported as honest uncertainty in
``evidence/v5/stage-1/system-version``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Audit, Event, Outbox
from app.models.v4_tables import AuthorityReceipt, PublicPrincipal
from app.models.v5_tables import (
    AIApplication,
    AIApplicationLifecycleRevision,
    BootstrapAttestation,
    ComponentRevision,
    DependencyEdge,
    Environment,
    SystemAssignment,
    SystemComponent,
    SystemComponentLifecycleRevision,
    SystemVersionSet,
    TopologyRevision,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.public_api.v5_models import (
    SystemManifestImportRequest,
    SystemManifestImportResponse,
    SystemVersionDiffResponse,
    SystemVersionGetResponse,
    V5IdempotencyReceipt,
)
from app.services.public_idempotency import (
    PublicIdempotencyError,
    PublicIdempotencyService,
)
from app.services.v4_audit import (
    V4AuditIntegrityError,
    V4AuditService,
    V4AuditUnavailable,
)
from app.services.v4_event_store import V4EventStore, V4EventStoreError
from app.services.v5_authority import V5AuthorityError, V5AuthorityService
from app.services.v5_manifest_import_coordinator import (
    ManifestImportCompositionError,
    V5ManifestImportCoordinator,
)
from app.utils.ids import (
    new_authority_receipt_id,
    new_bootstrap_attestation_id,
    new_component_revision_id,
    new_idempotency_receipt_id,
    new_request_id,
    new_system_assignment_id,
    new_system_manifest_id,
    new_system_version_set_id,
    new_topology_revision_id,
    new_transaction_id,
)
from app.utils.v4_integrity import V4IntegrityError, canonical_digest, record_digest
from app.utils.v5_integrity import V5_HASH_RULE, assert_v5_record_digest, v5_record_digest

Clock = Callable[[], datetime]

_IMPORT_INTENT = "system-manifests.import"
_IMPORT_SCOPE = "system_manifests:import"
_READ_SCOPE = "system_versions:read"
_IMPORT_PRINCIPAL_TYPES = frozenset({"human", "service"})
_READ_PRINCIPAL_TYPES = frozenset({"human", "external_agent", "service", "connector"})
_BOOTSTRAP_ATTESTATION_SCOPE = "INITIAL_DESIRED_ASSIGNMENT"


class SystemVersionsError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        details: dict[str, object] | None = None,
        audit_ref: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        self.code = code
        self.details = details or {}
        self.audit_ref = audit_ref
        self.workspace_id = workspace_id
        self.rollback_required = True
        super().__init__(code)


class V5ReadDenial(SystemVersionsError):
    """Audited read-only denial that the HTTP boundary may commit by itself."""

    def __init__(
        self,
        code: str,
        *,
        audit_ref: str,
        workspace_id: str,
        details: dict[str, object] | None = None,
    ) -> None:
        if code not in {"RESOURCE_NOT_FOUND", "SCOPE_FORBIDDEN", "VALIDATION_FAILED"}:
            raise ValueError("v5 read denials support only non-mutating denial codes")
        super().__init__(code, details=details, audit_ref=audit_ref, workspace_id=workspace_id)
        self.rollback_required = False


@dataclass(frozen=True)
class _VersionSpec:
    subject_kind: str
    event_type: str
    command: str
    aggregate_type: str
    resource_kind: str
    subject_revisioned: bool


_SPECS: dict[str, _VersionSpec] = {
    "AI_APPLICATION": _VersionSpec(
        "AI_APPLICATION", "application.registered", "applications.register",
        "ai_application", "ai_application", True,
    ),
    "ENVIRONMENT": _VersionSpec(
        "ENVIRONMENT", "environment.registered", "environments.register",
        "environment", "environment", True,
    ),
    "SYSTEM_COMPONENT": _VersionSpec(
        "SYSTEM_COMPONENT", "system_component.registered", "system-components.register",
        "system_component", "system_component", True,
    ),
    "DEPENDENCY_EDGE": _VersionSpec(
        "DEPENDENCY_EDGE", "dependency_edge.recorded", "dependency-edges.record",
        "dependency_edge", "dependency_edge", True,
    ),
    "COMPONENT_REVISION": _VersionSpec(
        "COMPONENT_REVISION", "component_revision.recorded", "component-revisions.record",
        "component_revision", "component_revision", True,
    ),
    "TOPOLOGY_REVISION": _VersionSpec(
        "TOPOLOGY_REVISION", "topology_revision.recorded", "topology-revisions.record",
        "topology_revision", "topology_revision", True,
    ),
    "SYSTEM_VERSION_SET": _VersionSpec(
        "SYSTEM_VERSION_SET", "system_version_set.recorded", "system-versions.record",
        "system_version_set", "system_version_set", True,
    ),
    "BOOTSTRAP_ATTESTATION": _VersionSpec(
        "BOOTSTRAP_ATTESTATION", "bootstrap_attestation.recorded",
        "bootstrap-attestations.record", "bootstrap_attestation", "bootstrap_attestation", True,
    ),
    "SYSTEM_ASSIGNMENT": _VersionSpec(
        "SYSTEM_ASSIGNMENT", "system_assignment.recorded", "system-assignments.record",
        "system_assignment", "system_assignment", True,
    ),
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


class SystemVersionsService:
    def __init__(
        self,
        session: Session,
        *,
        clock: Clock | None = None,
        contracts_root: str | Path | None = None,
        audit_service: V4AuditService | None = None,
        event_store: V4EventStore | None = None,
        authority_service: V5AuthorityService | None = None,
        idempotency_service: PublicIdempotencyService | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or _utc_now
        self.audit = audit_service or V4AuditService(session, clock=self.clock)
        self.events = event_store or V4EventStore(session)
        self.authority = authority_service or V5AuthorityService(
            session, contracts_root=contracts_root
        )
        self.idempotency = idempotency_service or PublicIdempotencyService(session)

    # ---------------------------------------------------------------- utilities

    def _validate_principal_row(self, principal: AcceptedPrincipalContext) -> None:
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
            raise SystemVersionsError("TOKEN_INVALID", workspace_id=principal.workspace_id)

    def _record_read_audit(
        self,
        *,
        principal: AcceptedPrincipalContext,
        action: str,
        target: str,
        params: dict[str, object],
        result: str = "success",
        error_code: str | None = None,
        evidence_refs: dict[str, Any] | None = None,
    ):
        try:
            return self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=action,
                target=target,
                params=params,
                result=result,
                error_code=error_code,
                trace_id=params.get("request_id"),
                evidence_refs=evidence_refs,
            )
        except V4AuditUnavailable as exc:
            raise SystemVersionsError(
                "AUDIT_UNAVAILABLE",
                workspace_id=principal.workspace_id,
                rollback_required=True,
            ) from exc

    def _deny_not_found(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        action: str,
        target: str,
    ) -> None:
        audit = self._record_read_audit(
            principal=principal,
            action=action,
            target=target,
            params={"request_id": request_id, "resource_requested": True},
            result="denied",
            error_code="RESOURCE_NOT_FOUND",
        )
        raise V5ReadDenial(
            "RESOURCE_NOT_FOUND",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

    def _require_read_scope(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        action: str,
        target: str,
    ) -> None:
        if _READ_SCOPE in principal.scopes:
            return
        audit = self._record_read_audit(
            principal=principal,
            action=action,
            target=target,
            params={"request_id": request_id, "required_scope": _READ_SCOPE},
            result="denied",
            error_code="SCOPE_FORBIDDEN",
        )
        raise V5ReadDenial(
            "SCOPE_FORBIDDEN",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

    def _require_import_principal(self, principal: AcceptedPrincipalContext) -> None:
        if (
            principal.principal_type not in _IMPORT_PRINCIPAL_TYPES
            or _IMPORT_SCOPE not in principal.scopes
        ):
            raise SystemVersionsError("SCOPE_FORBIDDEN", workspace_id=principal.workspace_id)

    def _validate_owner_principals(
        self, workspace_id: str, owner_principal_ids: list[str]
    ) -> None:
        for principal_id in owner_principal_ids:
            row = self.session.get(PublicPrincipal, principal_id)
            if row is None or row.workspace_id != workspace_id or row.state != "ACTIVE":
                raise SystemVersionsError(
                    "VALIDATION_FAILED",
                    details={"reason": "OWNER_PRINCIPAL_UNKNOWN"},
                    workspace_id=workspace_id,
                )

    # ------------------------------------------------------------------- digests

    def _component_configuration_digest(self, revision: Any) -> str:
        payload: dict[str, Any] = {
            "identity_locator": revision.identity_locator,
            "identity_assurance": revision.identity_assurance,
            "content_digest": revision.content_digest,
            "declared_version": revision.declared_version,
            "provider_origin": revision.provider_origin,
            "resolved_at": (
                _wire_time(revision.resolved_at) if revision.resolved_at is not None else None
            ),
            "immutable_provider_version_attestation": (
                revision.immutable_provider_version_attestation
            ),
            "exact_observation_receipt_binding": revision.exact_observation_receipt_binding,
            "unknown_reason": revision.unknown_reason,
            "interface_schema_digest": revision.interface_schema_digest,
            "permission_manifest_digest": revision.permission_manifest_digest,
            "dependency_lock_digest": revision.dependency_lock_digest,
            "artifact_refs": revision.artifact_refs,
            "exact_provenance_receipt_bindings": revision.exact_provenance_receipt_bindings,
        }
        return canonical_digest(payload)

    @staticmethod
    def _assurance_summary(entries: list[dict[str, str]]) -> dict[str, Any]:
        return {
            "component_assurances": sorted(
                entries, key=lambda item: (item["component_revision_id"], item["component_id"])
            )
        }

    @staticmethod
    def _topology_digest(edge_rows: list[Any]) -> str:
        canonical_edges = sorted(
            (
                {
                    "from_component_id": edge.from_component_id,
                    "to_component_id": edge.to_component_id,
                    "relation": edge.relation,
                    "required": bool(edge.required),
                    "edge_digest": edge.edge_digest,
                }
                for edge in edge_rows
            ),
            key=lambda item: (
                item["from_component_id"], item["to_component_id"], item["relation"],
            ),
        )
        return canonical_digest({"edges": canonical_edges})

    @staticmethod
    def _version_set_digest(
        *,
        application_id: str,
        declared_environment_id: str,
        component_bindings: list[dict[str, Any]],
        topology_binding: dict[str, Any],
        provenance_receipt_ids: list[str],
        assurance_summary: dict[str, Any],
    ) -> str:
        payload = {
            "application_id": application_id,
            "declared_environment_id": declared_environment_id,
            "exact_component_revision_bindings": sorted(
                component_bindings, key=lambda item: item["id"]
            ),
            "exact_topology_revision_binding": topology_binding,
            "provenance_receipt_ids": sorted(set(provenance_receipt_ids)),
            "identity_assurance_summary": assurance_summary,
        }
        return canonical_digest(payload)

    # --------------------------------------------------------------- write core

    def _write_construct(
        self,
        *,
        kind: str,
        subject_id: str,
        workspace_id: str,
        envelope_payload: dict[str, Any],
        business_payload: dict[str, Any],
        correlation_id: str,
        transaction_id: str,
        request_id: str,
        recorded_at: datetime,
        manifest_coordinator: V5ManifestImportCoordinator,
        principal: AcceptedPrincipalContext,
        project_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_audit_ref: str,
    ) -> tuple[Any, dict[str, Any], str]:
        spec = _SPECS[kind]
        now = _as_utc(recorded_at)
        try:
            controller = self.authority.resolve_controller(
                workspace_id=workspace_id,
                subject_kind=kind,
                command=spec.command,
                event_type=spec.event_type,
                recorded_at=now,
            )
        except V5AuthorityError as exc:
            raise SystemVersionsError("INTERNAL_ERROR", workspace_id=workspace_id) from exc

        envelope = envelope_payload["record_envelope"]
        digest = v5_record_digest(envelope_payload)
        envelope["record_digest"] = digest

        row = self._build_projection_row(kind, envelope_payload, digest, now)
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise SystemVersionsError(
                "CATALOG_CONFLICT",
                details={"reason": "DUPLICATE_CATALOG_IDENTITY"},
                workspace_id=workspace_id,
            ) from exc

        subject_revision = 1 if spec.subject_revisioned else None
        self_binding_fields = {
            "COMPONENT_REVISION": "exact_component_revision_binding",
            "TOPOLOGY_REVISION": "exact_topology_revision_binding",
            "SYSTEM_VERSION_SET": "exact_system_version_set_binding",
            "BOOTSTRAP_ATTESTATION": "exact_bootstrap_attestation_binding",
            "SYSTEM_ASSIGNMENT": "exact_assignment_binding",
        }
        exact_subject_binding = {
            "kind": kind,
            "id": subject_id,
            "revision": 1,
            "digest": digest,
        }
        major2_business_fields = {
            "COMPONENT_REVISION": {
                "exact_system_component_binding", "component_kind",
                "identity_assurance", "configuration_digest",
            },
            "TOPOLOGY_REVISION": {
                "application_id", "exact_edge_revision_bindings", "topology_digest",
            },
            "SYSTEM_VERSION_SET": {
                "application_id", "declared_environment_id",
                "exact_component_revision_bindings", "exact_topology_revision_binding",
                "version_set_digest",
            },
            "BOOTSTRAP_ATTESTATION": {
                "application_id", "environment_id",
                "exact_initial_system_version_set_binding", "attester_principal_id",
                "attester_trust_role", "attestation_scope",
            },
            "SYSTEM_ASSIGNMENT": {
                "exact_bootstrap_attestation_binding",
                "exact_initial_system_version_set_binding", "application_id",
                "environment_id", "generation", "exposure",
            },
        }
        event_payload: dict[str, Any] = {
            self_binding_fields[kind]: exact_subject_binding,
            **{
                key: value
                for key, value in business_payload.items()
                if key in major2_business_fields[kind]
            },
        }
        try:
            event = manifest_coordinator.append_record_event(
                controller=controller,
                workspace_id=workspace_id,
                aggregate_type=spec.aggregate_type,
                aggregate_id=subject_id,
                event_type=spec.event_type,
                payload=event_payload,
                causation_id=request_id,
                correlation_id=correlation_id,
                transaction_id=transaction_id,
                occurred_at=now,
                authority_receipt_id=envelope["authority_receipt_id"],
                principal=principal,
                project_id=project_id,
                authenticated_request_digest=authenticated_request_digest,
                manifest_digest=manifest_digest,
                idempotency_key=idempotency_key,
                initiating_audit_ref=initiating_audit_ref,
                exact_subject_binding=exact_subject_binding,
            )
            audit = self.audit.record(
                workspace_id=workspace_id,
                actor_principal=controller.controller_principal,
                action=f"controller.{spec.event_type}",
                target=subject_id,
                params={"command": spec.command},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "subject_kind": kind,
                    "subject_id": subject_id,
                    "subject_revision": subject_revision,
                    "subject_digest": digest,
                    "event_id": event.event_id,
                },
                occurred_at=now,
            )
            self.authority.record_receipt(
                resolved=controller,
                authority_receipt_id=envelope["authority_receipt_id"],
                workspace_id=workspace_id,
                subject_id=subject_id,
                subject_revision=subject_revision,
                subject_digest=digest,
                event_id=event.event_id,
                transaction_id=transaction_id,
                audit_ref=audit.audit_ref,
                recorded_at=now,
            )
        except (V4EventStoreError, V5AuthorityError, V4AuditIntegrityError) as exc:
            raise SystemVersionsError("INTERNAL_ERROR", workspace_id=workspace_id) from exc
        except V4AuditUnavailable as exc:
            raise SystemVersionsError("AUDIT_UNAVAILABLE", workspace_id=workspace_id) from exc
        return row, envelope_payload, digest

    def _build_projection_row(
        self, kind: str, payload: dict[str, Any], digest: str, now: datetime
    ) -> Any:
        envelope = payload["record_envelope"]
        # The four V5-1A catalog kinds (AI_APPLICATION, ENVIRONMENT,
        # SYSTEM_COMPONENT, DEPENDENCY_EDGE) are application-catalog-controller
        # owned and never reach this version-controller write path: the only
        # caller (_write_construct) is invoked with the five 1B subject kinds.
        if kind == "COMPONENT_REVISION":
            return ComponentRevision(
                component_revision_id=payload["component_revision_id"],
                workspace_id=payload["workspace_id"],
                application_id=payload["application_id"],
                component_id=payload["component_id"],
                component_kind=payload["component_kind"],
                identity_locator=payload["identity_locator"],
                identity_assurance=payload["identity_assurance"],
                configuration_digest=payload["configuration_digest"],
                exact_provenance_receipt_bindings=list(
                    payload["exact_provenance_receipt_bindings"]
                ),
                declared_version=payload.get("declared_version"),
                content_digest=payload.get("content_digest"),
                provider_origin=payload.get("provider_origin"),
                resolved_at=(
                    _as_utc(datetime.fromisoformat(payload["resolved_at"]))
                    if isinstance(payload.get("resolved_at"), str)
                    else payload.get("resolved_at")
                ),
                immutable_provider_version_attestation=payload.get(
                    "immutable_provider_version_attestation"
                ),
                exact_observation_receipt_binding=payload.get(
                    "exact_observation_receipt_binding"
                ),
                unknown_reason=payload.get("unknown_reason"),
                interface_schema_digest=payload.get("interface_schema_digest"),
                permission_manifest_digest=payload.get("permission_manifest_digest"),
                dependency_lock_digest=payload.get("dependency_lock_digest"),
                dataset_role=payload.get("dataset_role"),
                artifact_refs=payload.get("artifact_refs"),
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
            )
        if kind == "TOPOLOGY_REVISION":
            return TopologyRevision(
                topology_revision_id=payload["topology_revision_id"],
                workspace_id=payload["workspace_id"],
                application_id=payload["application_id"],
                component_ids=list(payload["component_ids"]),
                exact_edge_revision_bindings=list(payload["exact_edge_revision_bindings"]),
                topology_digest=payload["topology_digest"],
                provenance_receipt_ids=list(payload["provenance_receipt_ids"]),
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
            )
        if kind == "SYSTEM_VERSION_SET":
            return SystemVersionSet(
                system_version_set_id=payload["system_version_set_id"],
                workspace_id=payload["workspace_id"],
                application_id=payload["application_id"],
                declared_environment_id=payload["declared_environment_id"],
                exact_component_revision_bindings=list(
                    payload["exact_component_revision_bindings"]
                ),
                exact_topology_revision_binding=payload["exact_topology_revision_binding"],
                identity_assurance_summary=payload["identity_assurance_summary"],
                provenance_receipt_ids=list(payload["provenance_receipt_ids"]),
                version_set_digest=payload["version_set_digest"],
                manifest_digest=payload.get("manifest_digest"),
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
            )
        if kind == "BOOTSTRAP_ATTESTATION":
            return BootstrapAttestation(
                bootstrap_attestation_id=payload["bootstrap_attestation_id"],
                workspace_id=payload["workspace_id"],
                application_id=payload["application_id"],
                environment_id=payload["environment_id"],
                exact_initial_system_version_set_binding=payload[
                    "exact_initial_system_version_set_binding"
                ],
                attester_principal_id=payload["attester_principal_id"],
                attester_trust_role=payload["attester_trust_role"],
                attestation_scope=payload["attestation_scope"],
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
            )
        if kind == "SYSTEM_ASSIGNMENT":
            return SystemAssignment(
                assignment_id=payload["assignment_id"],
                workspace_id=payload["workspace_id"],
                application_id=payload["application_id"],
                environment_id=payload["environment_id"],
                generation=payload["generation"],
                lifecycle_state=payload["lifecycle_state"],
                transition_kind=payload["transition_kind"],
                revision=1,
                exact_previous_assignment_binding_or_null=payload.get(
                    "exact_previous_assignment_binding_or_null"
                ),
                exact_slot_version_set_bindings=list(payload["exact_slot_version_set_bindings"]),
                exposure=payload["exposure"],
                expected_previous_generation=payload.get("expected_previous_generation"),
                exact_assignment_authority_binding=payload["exact_assignment_authority_binding"],
                requested_by_external_operation_id=payload.get(
                    "requested_by_external_operation_id"
                ),
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
                updated_at=now,
            )
        raise SystemVersionsError("INTERNAL_ERROR", details={"kind": kind})

    @staticmethod
    def _envelope(
        *,
        workspace_id: str,
        revision: int,
        recorded_by_principal: str,
        recorded_at: datetime,
        authority_receipt_id: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "workspace_id": workspace_id,
            "revision": revision,
            "recorded_by_principal": recorded_by_principal,
            "recorded_at": _wire_time(recorded_at),
            "immutable": True,
            "hash_rule": V5_HASH_RULE,
            "record_digest": "",
            "authority_receipt_id": authority_receipt_id,
        }

    # ------------------------------------------------------------- manifest import

    def import_manifest(
        self,
        request: SystemManifestImportRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> SystemManifestImportResponse:
        request_id = request_id or new_request_id()
        body = request.model_dump(mode="json")
        request_fingerprint = self.idempotency.fingerprint(body)
        self._validate_principal_row(principal)
        self._require_import_principal(principal)
        manifest_coordinator = V5ManifestImportCoordinator(
            self.session,
            audit_service=self.audit,
            authority_service=self.authority,
        )
        try:
            persisted_import_principal = manifest_coordinator.validate_current_authorization(
                principal=principal,
                project_id=request.application.project_id,
            )
        except ManifestImportCompositionError as exc:
            raise SystemVersionsError(
                exc.code, workspace_id=principal.workspace_id
            ) from exc
        attester_role = next(
            role
            for role in ("integrator", "catalog_admin", "trusted_builder")
            if role in (persisted_import_principal.trust_roles or [])
        )
        all_owner_ids = [
            principal_id
            for entry in (
                request.application.owner_principal_ids,
                *(component.owner_principal_ids for component in request.components),
                *(
                    [request.approver_policy.owner_principal_ids]
                    if request.approver_policy is not None
                    else []
                ),
            )
            for principal_id in entry
        ]
        self._validate_owner_principals(
            principal.workspace_id, sorted(set(all_owner_ids))
        )
        self._lock_manifest_workspace(principal.workspace_id)
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=_IMPORT_INTENT,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                verify_terminal=self._verify_manifest_terminal,
            )
        except PublicIdempotencyError as exc:
            raise SystemVersionsError(exc.code, workspace_id=principal.workspace_id) from exc
        if lookup.record is not None:
            try:
                response = self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=SystemManifestImportResponse,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind="system_version_set",
                    resource_field="system_version_set",
                    resource_id_field="system_version_set_id",
                )
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise SystemVersionsError(exc.code, workspace_id=principal.workspace_id) from exc

        manifest_digest = canonical_digest(body)
        self._require_workspace_bootstrap_empty(principal.workspace_id)

        workspace_id = principal.workspace_id
        transaction_id = new_transaction_id()
        now = _as_utc(self.clock())

        # manifest-level command audit is recorded first inside the transaction;
        # its ref is embedded in the version set envelope's manifest section so
        # a same-digest replay can return the exact original audit binding.
        try:
            command_audit = self.audit.record(
                workspace_id=workspace_id,
                actor_principal=principal.principal_id,
                action=_IMPORT_INTENT,
                target="",
                params={
                    "authenticated_request_digest": request_fingerprint,
                    "manifest_digest": manifest_digest,
                    "idempotency_key": idempotency_key,
                },
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={"manifest_digest": manifest_digest},
                occurred_at=now,
            )
        except V4AuditUnavailable as exc:
            raise SystemVersionsError("AUDIT_UNAVAILABLE", workspace_id=workspace_id) from exc

        manifest_id = new_system_manifest_id()
        manifest_section: dict[str, Any] = {
            "manifest_id": manifest_id,
            "manifest_digest": manifest_digest,
            "audit_ref": command_audit.audit_ref,
        }

        try:
            manifest_coordinator.validate_root(
                principal=principal,
                project_id=request.application.project_id,
                transaction_id=transaction_id,
                authenticated_request_digest=request_fingerprint,
                manifest_digest=manifest_digest,
                idempotency_key=idempotency_key,
                initiating_audit_ref=command_audit.audit_ref,
            )
        except ManifestImportCompositionError as exc:
            raise SystemVersionsError(
                exc.code, workspace_id=workspace_id
            ) from exc

        # 1. application (canonical owner: application-catalog-controller)
        try:
            application_record = manifest_coordinator.register_and_activate_application(
                application=request.application,
                principal=principal,
                transaction_id=transaction_id,
                request_id=request_id,
                authenticated_request_digest=request_fingerprint,
                manifest_digest=manifest_digest,
                idempotency_key=idempotency_key,
                initiating_audit_ref=command_audit.audit_ref,
                recorded_at=now,
            )
        except ManifestImportCompositionError as exc:
            raise SystemVersionsError(
                exc.code, workspace_id=workspace_id
            ) from exc
        application_id = application_record.subject_id
        app_payload = application_record.activated_payload
        app_digest = application_record.activated_digest

        # 2. environment (canonical owner: application-catalog-controller)
        try:
            environment_record = manifest_coordinator.register_environment(
                environment=request.environment,
                application_id=application_id,
                principal=principal,
                transaction_id=transaction_id,
                request_id=request_id,
                authenticated_request_digest=request_fingerprint,
                manifest_digest=manifest_digest,
                idempotency_key=idempotency_key,
                initiating_audit_ref=command_audit.audit_ref,
                recorded_at=now,
            )
        except ManifestImportCompositionError as exc:
            raise SystemVersionsError(exc.code, workspace_id=workspace_id) from exc
        environment_id = environment_record.subject_id
        env_payload = environment_record.payload

        # 3. components (logical_name -> component_id)
        name_to_component_id: dict[str, str] = {}
        component_payloads: dict[str, dict[str, Any]] = {}
        for component in request.components:
            try:
                component_record = manifest_coordinator.register_and_activate_component(
                    component=component,
                    application_id=application_id,
                    principal=principal,
                    transaction_id=transaction_id,
                    request_id=request_id,
                    authenticated_request_digest=request_fingerprint,
                    manifest_digest=manifest_digest,
                    idempotency_key=idempotency_key,
                    initiating_audit_ref=command_audit.audit_ref,
                    recorded_at=now,
                )
            except ManifestImportCompositionError as exc:
                raise SystemVersionsError(
                    exc.code, workspace_id=workspace_id
                ) from exc
            name_to_component_id[component.logical_name] = component_record.subject_id
            component_payloads[component.logical_name] = component_record.activated_payload

        # approver policy component + its independent trusted revision (recorded,
        # excluded from the runtime VersionSet bindings and the topology)
        approver_policy_payload: dict[str, Any] | None = None
        approver_policy_component_id: str | None = None
        approver_revision_payload: dict[str, Any] | None = None
        approver_revision_binding: dict[str, Any] | None = None
        if request.approver_policy is not None:
            try:
                approver_record = manifest_coordinator.register_and_activate_component(
                    component=request.approver_policy,
                    application_id=application_id,
                    principal=principal,
                    transaction_id=transaction_id,
                    request_id=request_id,
                    authenticated_request_digest=request_fingerprint,
                    manifest_digest=manifest_digest,
                    idempotency_key=idempotency_key,
                    initiating_audit_ref=command_audit.audit_ref,
                    recorded_at=now,
                )
            except ManifestImportCompositionError as exc:
                raise SystemVersionsError(
                    exc.code, workspace_id=workspace_id
                ) from exc
            approver_policy_component_id = approver_record.subject_id
            approver_policy_payload = approver_record.activated_payload
            approver_revision_payload = self._build_component_revision_payload(
                workspace_id=workspace_id,
                application_id=application_id,
                component_id=approver_policy_component_id,
                component_kind=request.approver_policy.component_kind,
                logical_name=request.approver_policy.logical_name,
                revision_spec=request.approver_policy.revision,
                principal=principal,
                now=now,
            )
            _rev_row, approver_revision_payload, approver_revision_digest = (
                self._write_construct(
                    kind="COMPONENT_REVISION",
                    subject_id=approver_revision_payload["component_revision_id"],
                    workspace_id=workspace_id,
                    envelope_payload=approver_revision_payload,
                    business_payload={
                        "component_revision_id": approver_revision_payload[
                            "component_revision_id"
                        ],
                        "component_id": approver_policy_component_id,
                        "component_kind": request.approver_policy.component_kind,
                        "identity_assurance": request.approver_policy.revision.identity_assurance,
                        "configuration_digest": approver_revision_payload["configuration_digest"],
                        "exact_system_component_binding": approver_revision_payload[
                            "exact_system_component_binding"
                        ],
                    },
                    correlation_id=application_id,
                    transaction_id=transaction_id,
                    request_id=request_id,
                    recorded_at=now,
                    manifest_coordinator=manifest_coordinator,
                    principal=principal,
                    project_id=request.application.project_id,
                    authenticated_request_digest=request_fingerprint,
                    manifest_digest=manifest_digest,
                    idempotency_key=idempotency_key,
                    initiating_audit_ref=command_audit.audit_ref,
                )
            )
            approver_revision_binding = {
                "kind": "COMPONENT_REVISION",
                "id": approver_revision_payload["component_revision_id"],
                "revision": 1,
                "digest": approver_revision_digest,
            }

        # 4. dependency edges (manifest logical names -> component ids)
        edge_payloads: dict[str, dict[str, Any]] = {}
        edge_record_digests: dict[str, str] = {}
        for edge in request.dependency_edges:
            from_id = name_to_component_id[edge.from_component]
            to_id = name_to_component_id[edge.to_component]
            try:
                edge_record = manifest_coordinator.record_dependency_edge(
                    edge=edge,
                    application_id=application_id,
                    from_component_id=from_id,
                    to_component_id=to_id,
                    principal=principal,
                    transaction_id=transaction_id,
                    request_id=request_id,
                    authenticated_request_digest=request_fingerprint,
                    manifest_digest=manifest_digest,
                    idempotency_key=idempotency_key,
                    initiating_audit_ref=command_audit.audit_ref,
                    recorded_at=now,
                )
            except ManifestImportCompositionError as exc:
                raise SystemVersionsError(exc.code, workspace_id=workspace_id) from exc
            edge_payloads[edge_record.subject_id] = edge_record.payload
            edge_record_digests[edge_record.subject_id] = edge_record.digest

        # 5. component revisions (bound into the version set)
        revision_payloads: dict[str, dict[str, Any]] = {}
        revision_bindings: list[dict[str, Any]] = []
        assurance_entries: list[dict[str, str]] = []
        for component in request.components:
            component_id = name_to_component_id[component.logical_name]
            rev_payload = self._build_component_revision_payload(
                workspace_id=workspace_id,
                application_id=application_id,
                component_id=component_id,
                component_kind=component.component_kind,
                logical_name=component.logical_name,
                revision_spec=component.revision,
                principal=principal,
                now=now,
            )
            _row, rev_payload, rev_digest = self._write_construct(
                kind="COMPONENT_REVISION",
                subject_id=rev_payload["component_revision_id"],
                workspace_id=workspace_id,
                envelope_payload=rev_payload,
                business_payload={
                    "component_revision_id": rev_payload["component_revision_id"],
                    "component_id": component_id,
                    "component_kind": component.component_kind,
                    "identity_assurance": component.revision.identity_assurance,
                    "configuration_digest": rev_payload["configuration_digest"],
                    "exact_system_component_binding": rev_payload[
                        "exact_system_component_binding"
                    ],
                },
                correlation_id=application_id,
                transaction_id=transaction_id,
                request_id=request_id,
                recorded_at=now,
                manifest_coordinator=manifest_coordinator,
                principal=principal,
                project_id=request.application.project_id,
                authenticated_request_digest=request_fingerprint,
                manifest_digest=manifest_digest,
                idempotency_key=idempotency_key,
                initiating_audit_ref=command_audit.audit_ref,
            )
            revision_payloads[rev_payload["component_revision_id"]] = rev_payload
            revision_bindings.append(
                {
                    "kind": "COMPONENT_REVISION",
                    "id": rev_payload["component_revision_id"],
                    "revision": 1,
                    "digest": rev_digest,
                }
            )
            assurance_entries.append(
                {
                    "component_revision_id": rev_payload["component_revision_id"],
                    "component_id": component_id,
                    "identity_assurance": component.revision.identity_assurance,
                }
            )

        # 6. topology revision (exact edge bindings; graph digest)
        component_name_by_id = {
            component_id: logical_name
            for logical_name, component_id in name_to_component_id.items()
        }
        canonical_edge_ids = sorted(
            edge_payloads,
            key=lambda edge_id: (
                component_name_by_id[edge_payloads[edge_id]["from_component_id"]],
                component_name_by_id[edge_payloads[edge_id]["to_component_id"]],
                edge_payloads[edge_id]["relation"],
                edge_payloads[edge_id]["required"],
                edge_id,
            ),
        )
        edge_rows = [
            self.session.get(DependencyEdge, edge_id)
            for edge_id in canonical_edge_ids
        ]
        component_ids = sorted(name_to_component_id[name] for name in name_to_component_id)
        topology_id = new_topology_revision_id()
        topology_digest = self._topology_digest([row for row in edge_rows if row is not None])
        edge_bindings = [
            {
                "kind": "DEPENDENCY_EDGE",
                "id": edge_id,
                "revision": 1,
                "digest": edge_record_digests[edge_id],
            }
            for edge_id in canonical_edge_ids
        ]
        topology_payload = {
            "topology_revision_id": topology_id,
            "workspace_id": workspace_id,
            "application_id": application_id,
            "component_ids": component_ids,
            "exact_edge_revision_bindings": edge_bindings,
            "topology_digest": topology_digest,
            "provenance_receipt_ids": sorted(
                {
                    app_payload["record_envelope"]["authority_receipt_id"],
                    environment_record.authority_receipt_id,
                }
            ),
            "record_envelope": self._envelope(
                workspace_id=workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=new_authority_receipt_id(),
            ),
        }
        _trow, topology_payload, topology_digest_actual = self._write_construct(
            kind="TOPOLOGY_REVISION",
            subject_id=topology_id,
            workspace_id=workspace_id,
            envelope_payload=topology_payload,
            business_payload={
                "topology_revision_id": topology_id,
                "application_id": application_id,
                "exact_edge_revision_bindings": edge_bindings,
                "topology_digest": topology_digest,
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
            manifest_coordinator=manifest_coordinator,
            principal=principal,
            project_id=request.application.project_id,
            authenticated_request_digest=request_fingerprint,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=command_audit.audit_ref,
        )

        # 7. system version set (immutable; manifest digest replay key)
        topology_binding = {
            "kind": "TOPOLOGY_REVISION",
            "id": topology_id,
            "revision": 1,
            "digest": topology_digest_actual,
        }
        assurance_summary = self._assurance_summary(assurance_entries)
        version_set_id = new_system_version_set_id()
        version_set_digest = self._version_set_digest(
            application_id=application_id,
            declared_environment_id=environment_id,
            component_bindings=revision_bindings,
            topology_binding=topology_binding,
            provenance_receipt_ids=[],
            assurance_summary=assurance_summary,
        )
        version_set_payload = {
            "system_version_set_id": version_set_id,
            "workspace_id": workspace_id,
            "application_id": application_id,
            "declared_environment_id": environment_id,
            "exact_component_revision_bindings": sorted(
                revision_bindings, key=lambda item: item["id"]
            ),
            "exact_topology_revision_binding": topology_binding,
            "identity_assurance_summary": assurance_summary,
            "provenance_receipt_ids": [],
            "version_set_digest": version_set_digest,
            "manifest_digest": manifest_digest,
            "manifest": {
                **manifest_section,
                "approver_policy_revision": approver_revision_binding,
            },
            "record_envelope": self._envelope(
                workspace_id=workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=new_authority_receipt_id(),
            ),
        }
        _vrow, version_set_payload, version_set_digest_actual = self._write_construct(
            kind="SYSTEM_VERSION_SET",
            subject_id=version_set_id,
            workspace_id=workspace_id,
            envelope_payload=version_set_payload,
            business_payload={
                "system_version_set_id": version_set_id,
                "application_id": application_id,
                "declared_environment_id": environment_id,
                "exact_component_revision_bindings": sorted(
                    revision_bindings, key=lambda item: item["id"]
                ),
                "exact_topology_revision_binding": topology_binding,
                "version_set_digest": version_set_digest,
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
            manifest_coordinator=manifest_coordinator,
            principal=principal,
            project_id=request.application.project_id,
            authenticated_request_digest=request_fingerprint,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=command_audit.audit_ref,
        )

        # 8. bootstrap attestation (exact initial version set binding)
        attestation_id = new_bootstrap_attestation_id()
        attestation_payload = {
            "bootstrap_attestation_id": attestation_id,
            "workspace_id": workspace_id,
            "application_id": application_id,
            "environment_id": environment_id,
            "exact_initial_system_version_set_binding": {
                "kind": "SYSTEM_VERSION_SET",
                "id": version_set_id,
                "revision": 1,
                "digest": version_set_digest_actual,
            },
            "attester_principal_id": principal.principal_id,
            "attester_trust_role": attester_role,
            "attestation_scope": _BOOTSTRAP_ATTESTATION_SCOPE,
            "record_envelope": self._envelope(
                workspace_id=workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=new_authority_receipt_id(),
            ),
        }
        _arow, attestation_payload, attestation_digest = self._write_construct(
            kind="BOOTSTRAP_ATTESTATION",
            subject_id=attestation_id,
            workspace_id=workspace_id,
            envelope_payload=attestation_payload,
            business_payload={
                "bootstrap_attestation_id": attestation_id,
                "application_id": application_id,
                "environment_id": environment_id,
                "attester_principal_id": principal.principal_id,
                "attester_trust_role": attester_role,
                "attestation_scope": _BOOTSTRAP_ATTESTATION_SCOPE,
                "exact_initial_system_version_set_binding": attestation_payload[
                    "exact_initial_system_version_set_binding"
                ],
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
            manifest_coordinator=manifest_coordinator,
            principal=principal,
            project_id=request.application.project_id,
            authenticated_request_digest=request_fingerprint,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=command_audit.audit_ref,
        )

        # 9. bootstrap assignment (generation=1, previous=null, exact authority)
        assignment_id = new_system_assignment_id()
        assignment_payload = {
            "assignment_id": assignment_id,
            "workspace_id": workspace_id,
            "application_id": application_id,
            "environment_id": environment_id,
            "generation": 1,
            "lifecycle_state": "ACTIVE",
            "transition_kind": "BOOTSTRAP",
            "exact_previous_assignment_binding_or_null": None,
            "exact_slot_version_set_bindings": [
                {
                    "slot": "PRIMARY",
                    "kind": "SYSTEM_VERSION_SET",
                    "id": version_set_id,
                    "revision": 1,
                    "digest": version_set_digest_actual,
                }
            ],
            "exposure": "EXPOSED",
            "expected_previous_generation": None,
            "exact_assignment_authority_binding": {
                "binding_kind": "BOOTSTRAP_ATTESTATION",
                "id": attestation_id,
                "revision": 1,
                "digest": attestation_digest,
            },
            "requested_by_external_operation_id": None,
            "record_envelope": self._envelope(
                workspace_id=workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=new_authority_receipt_id(),
            ),
        }
        _srow, assignment_payload, assignment_digest = self._write_construct(
            kind="SYSTEM_ASSIGNMENT",
            subject_id=assignment_id,
            workspace_id=workspace_id,
            envelope_payload=assignment_payload,
            business_payload={
                "assignment_id": assignment_id,
                "application_id": application_id,
                "environment_id": environment_id,
                "generation": 1,
                "exposure": "EXPOSED",
                "exact_bootstrap_attestation_binding": {
                    "kind": "BOOTSTRAP_ATTESTATION",
                    "id": attestation_id,
                    "revision": 1,
                    "digest": attestation_digest,
                },
                "exact_initial_system_version_set_binding": {
                    "kind": "SYSTEM_VERSION_SET",
                    "id": version_set_id,
                    "revision": 1,
                    "digest": version_set_digest_actual,
                },
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
            manifest_coordinator=manifest_coordinator,
            principal=principal,
            project_id=request.application.project_id,
            authenticated_request_digest=request_fingerprint,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=command_audit.audit_ref,
        )

        response_core: dict[str, Any] = {
            "schema_version": "2.0",
            "workspace_id": workspace_id,
            "request_id": request_id,
            "audit_ref": command_audit.audit_ref,
            "manifest_id": manifest_id,
            "manifest_digest": manifest_digest,
            "application": app_payload,
            "environment": env_payload,
            "components": [
                component_payloads[name] for name in sorted(component_payloads)
            ],
            "dependency_edges": [
                edge_payloads[edge_id] for edge_id in canonical_edge_ids
            ],
            "component_revisions": sorted(
                revision_payloads.values(), key=lambda payload: payload["logical_name"]
            ),
            "topology_revision": topology_payload,
            "system_version_set": version_set_payload,
            "bootstrap_attestation": attestation_payload,
            "system_assignment": assignment_payload,
            "approver_policy_revision": approver_revision_payload,
        }
        return self._persist_manifest_response(
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            audit_ref=command_audit.audit_ref,
            resource_id=version_set_id,
            response_core=response_core,
            completed_at=now,
        )

    def _build_component_revision_payload(
        self,
        *,
        workspace_id: str,
        application_id: str,
        component_id: str,
        component_kind: str,
        logical_name: str,
        revision_spec: Any,
        principal: AcceptedPrincipalContext,
        now: datetime,
    ) -> dict[str, Any]:
        component = self.session.get(SystemComponent, component_id)
        if (
            component is None
            or component.workspace_id != workspace_id
            or component.application_id != application_id
            or component.lifecycle_state != "ACTIVE"
        ):
            raise SystemVersionsError(
                "v5.component_revision.active_component_binding_required",
                workspace_id=workspace_id,
            )
        exact_system_component_binding = {
            "kind": "SYSTEM_COMPONENT",
            "id": component_id,
            "revision": component.revision,
            "digest": component.record_digest,
        }
        try:
            self.authority.validate_exact_lifecycle_binding(
                workspace_id=workspace_id,
                binding=exact_system_component_binding,
                require_current=True,
                require_active=True,
                application_id=application_id,
            )
        except V5AuthorityError as exc:
            raise SystemVersionsError(
                "v5.component_revision.active_component_binding_required",
                workspace_id=workspace_id,
            ) from exc
        configuration_digest = self._component_configuration_digest(revision_spec)
        return {
            "component_revision_id": new_component_revision_id(),
            "workspace_id": workspace_id,
            "application_id": application_id,
            "component_id": component_id,
            "exact_system_component_binding": exact_system_component_binding,
            "component_kind": component_kind,
            "logical_name": logical_name,
            "identity_locator": revision_spec.identity_locator,
            "identity_assurance": revision_spec.identity_assurance,
            "configuration_digest": configuration_digest,
            "exact_provenance_receipt_bindings": list(
                revision_spec.exact_provenance_receipt_bindings or []
            ),
            "declared_version": revision_spec.declared_version,
            "content_digest": revision_spec.content_digest,
            "provider_origin": revision_spec.provider_origin,
            "resolved_at": (
                _wire_time(revision_spec.resolved_at)
                if revision_spec.resolved_at is not None
                else None
            ),
            "immutable_provider_version_attestation": (
                revision_spec.immutable_provider_version_attestation
            ),
            "exact_observation_receipt_binding": (
                revision_spec.exact_observation_receipt_binding
            ),
            "unknown_reason": revision_spec.unknown_reason,
            "interface_schema_digest": revision_spec.interface_schema_digest,
            "permission_manifest_digest": revision_spec.permission_manifest_digest,
            "dependency_lock_digest": revision_spec.dependency_lock_digest,
            "dataset_role": None,
            "artifact_refs": revision_spec.artifact_refs,
            "record_envelope": self._envelope(
                workspace_id=workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=new_authority_receipt_id(),
            ),
        }

    def _validate_persisted_component_revision_binding(
        self,
        row: ComponentRevision,
        *,
        workspace_id: str,
    ) -> None:
        payload = row.envelope_payload
        if not isinstance(payload, dict):
            raise SystemVersionsError(
                "INTERNAL_ERROR",
                details={"reason": "COMPONENT_REVISION_ENVELOPE_INVALID"},
            )
        try:
            digest = assert_v5_record_digest(payload)
        except (V4IntegrityError, AttributeError, TypeError) as exc:
            raise SystemVersionsError(
                "INTERNAL_ERROR",
                details={"reason": "COMPONENT_REVISION_ENVELOPE_INVALID"},
            ) from exc
        binding = payload.get("exact_system_component_binding")
        if (
            digest != row.record_digest
            or payload.get("component_revision_id") != row.component_revision_id
            or payload.get("workspace_id") != row.workspace_id
            or payload.get("application_id") != row.application_id
            or payload.get("component_id") != row.component_id
            or row.workspace_id != workspace_id
            or not isinstance(binding, dict)
            or set(binding) != {"kind", "id", "revision", "digest"}
            or binding.get("kind") != "SYSTEM_COMPONENT"
            or binding.get("id") != row.component_id
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR",
                details={"reason": "COMPONENT_REVISION_BINDING_MISMATCH"},
            )
        try:
            self.authority.validate_exact_lifecycle_binding(
                workspace_id=workspace_id,
                binding=binding,
                require_active=True,
                application_id=row.application_id,
            )
        except V5AuthorityError as exc:
            raise SystemVersionsError(
                "INTERNAL_ERROR",
                details={"reason": "COMPONENT_REVISION_BINDING_MISMATCH"},
            ) from exc

    def _require_workspace_bootstrap_empty(self, workspace_id: str) -> None:
        authoritative_models = (
            AIApplication,
            AIApplicationLifecycleRevision,
            Environment,
            SystemComponent,
            SystemComponentLifecycleRevision,
            DependencyEdge,
            ComponentRevision,
            TopologyRevision,
            SystemVersionSet,
            BootstrapAttestation,
            SystemAssignment,
        )
        for model in authoritative_models:
            if self.session.scalar(
                select(model).where(model.workspace_id == workspace_id).limit(1)
            ) is not None:
                raise SystemVersionsError(
                    "CATALOG_CONFLICT",
                    details={"reason": "MANIFEST_BOOTSTRAP_ALREADY_EXISTS"},
                    workspace_id=workspace_id,
                )

    def _lock_manifest_workspace(self, workspace_id: str) -> None:
        if self.session.get_bind().dialect.name != "postgresql":
            return
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"v5:manifest-bootstrap:{workspace_id}"},
        )

    def _validate_version_set_component_revision_bindings(
        self,
        version_set: SystemVersionSet,
        *,
        workspace_id: str,
    ) -> list[ComponentRevision]:
        payload = version_set.envelope_payload
        try:
            payload_digest = assert_v5_record_digest(payload)
        except (V4IntegrityError, AttributeError, TypeError) as exc:
            raise SystemVersionsError(
                "INTERNAL_ERROR",
                details={"reason": "SYSTEM_VERSION_SET_ENVELOPE_INVALID"},
            ) from exc
        if (
            payload_digest != version_set.record_digest
            or payload.get("workspace_id") != version_set.workspace_id
            or payload.get("application_id") != version_set.application_id
            or payload.get("system_version_set_id")
            != version_set.system_version_set_id
            or payload.get("exact_component_revision_bindings")
            != version_set.exact_component_revision_bindings
            or payload.get("version_set_digest") != version_set.version_set_digest
            or self._version_set_digest(
                application_id=version_set.application_id,
                declared_environment_id=version_set.declared_environment_id,
                component_bindings=version_set.exact_component_revision_bindings,
                topology_binding=version_set.exact_topology_revision_binding,
                provenance_receipt_ids=version_set.provenance_receipt_ids,
                assurance_summary=version_set.identity_assurance_summary,
            )
            != version_set.version_set_digest
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR",
                details={"reason": "SYSTEM_VERSION_SET_BINDING_MISMATCH"},
            )
        revisions: list[ComponentRevision] = []
        for binding in version_set.exact_component_revision_bindings:
            if (
                not isinstance(binding, dict)
                or set(binding) != {"kind", "id", "revision", "digest"}
                or binding.get("kind") != "COMPONENT_REVISION"
                or binding.get("revision") != 1
                or not isinstance(binding.get("id"), str)
            ):
                raise SystemVersionsError(
                    "INTERNAL_ERROR",
                    details={"reason": "COMPONENT_REVISION_BINDING_MISMATCH"},
                )
            revision = self.session.get(ComponentRevision, binding["id"])
            if (
                revision is None
                or revision.workspace_id != workspace_id
                or revision.application_id != version_set.application_id
                or revision.record_digest != binding.get("digest")
            ):
                raise SystemVersionsError(
                    "INTERNAL_ERROR",
                    details={"reason": "COMPONENT_REVISION_BINDING_MISMATCH"},
                )
            self._validate_persisted_component_revision_binding(
                revision, workspace_id=workspace_id
            )
            revisions.append(revision)
        return revisions

    def _validate_receipt_backed_record(
        self,
        *,
        row: Any,
        subject_kind: str,
        id_attr: str,
        subject_revision: int | None,
        scalar_fields: tuple[str, ...],
    ) -> tuple[dict[str, Any], AuthorityReceipt]:
        payload = row.envelope_payload
        try:
            digest = assert_v5_record_digest(payload)
        except (V4IntegrityError, AttributeError, TypeError) as exc:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_GRAPH_RECORD_INVALID"}
            ) from exc
        envelope = payload.get("record_envelope") if isinstance(payload, dict) else None
        if (
            not isinstance(envelope, dict)
            or digest != row.record_digest
            or payload.get(id_attr) != getattr(row, id_attr)
            or payload.get("workspace_id") != row.workspace_id
            or envelope.get("record_digest") != row.record_digest
            or envelope.get("authority_receipt_id") != row.authority_receipt_id
            or envelope.get("recorded_by_principal") != row.recorded_by_principal
            or any(payload.get(field) != getattr(row, field) for field in scalar_fields)
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_GRAPH_RECORD_MISMATCH"}
            )
        try:
            receipt = self.authority.validate_receipt_binding(
                authority_receipt_id=row.authority_receipt_id,
                workspace_id=row.workspace_id,
                subject_kind=subject_kind,
                subject_id=getattr(row, id_attr),
                subject_revision=subject_revision,
                subject_digest=row.record_digest,
            )
        except V5AuthorityError as exc:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_GRAPH_AUTHORITY_INVALID"}
            ) from exc
        return payload, receipt

    def _validate_lifecycle_graph_subject(
        self,
        *,
        row: AIApplication | SystemComponent,
        subject_kind: str,
        id_attr: str,
        scalar_fields: tuple[str, ...],
    ) -> tuple[
        dict[str, Any], tuple[AuthorityReceipt, AuthorityReceipt], dict[str, Any]
    ]:
        payload = row.envelope_payload
        try:
            digest = assert_v5_record_digest(payload)
        except (V4IntegrityError, AttributeError, TypeError) as exc:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_LIFECYCLE_INVALID"}
            ) from exc
        envelope = payload.get("record_envelope") if isinstance(payload, dict) else None
        if (
            not isinstance(envelope, dict)
            or row.lifecycle_state != "ACTIVE"
            or row.revision != 2
            or digest != row.record_digest
            or payload.get(id_attr) != getattr(row, id_attr)
            or payload.get("workspace_id") != row.workspace_id
            or envelope.get("revision") != 2
            or envelope.get("record_digest") != row.record_digest
            or envelope.get("authority_receipt_id") != row.authority_receipt_id
            or envelope.get("recorded_by_principal") != row.recorded_by_principal
            or any(payload.get(field) != getattr(row, field) for field in scalar_fields)
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_LIFECYCLE_MISMATCH"}
            )
        current_binding = {
            "kind": subject_kind,
            "id": getattr(row, id_attr),
            "revision": 2,
            "digest": row.record_digest,
        }
        previous_field = (
            "exact_previous_application_binding"
            if subject_kind == "AI_APPLICATION"
            else "exact_previous_system_component_binding"
        )
        previous = payload.get(previous_field)
        try:
            self.authority.validate_exact_lifecycle_binding(
                workspace_id=row.workspace_id,
                binding=current_binding,
                require_current=True,
                require_active=True,
                application_id=(
                    row.application_id if subject_kind == "SYSTEM_COMPONENT" else None
                ),
            )
            if not isinstance(previous, dict):
                raise V5AuthorityError("v5.authority.lifecycle_previous_missing")
            previous_row = self.authority.validate_exact_lifecycle_binding(
                workspace_id=row.workspace_id,
                binding=previous,
                application_id=(
                    row.application_id if subject_kind == "SYSTEM_COMPONENT" else None
                ),
            )
            current_receipt = self.authority.validate_receipt_binding(
                authority_receipt_id=row.authority_receipt_id,
                workspace_id=row.workspace_id,
                subject_kind=subject_kind,
                subject_id=getattr(row, id_attr),
                subject_revision=2,
                subject_digest=row.record_digest,
                lifecycle_history=True,
            )
            previous_receipt = self.authority.validate_receipt_binding(
                authority_receipt_id=previous_row.authority_receipt_id,
                workspace_id=row.workspace_id,
                subject_kind=subject_kind,
                subject_id=getattr(row, id_attr),
                subject_revision=1,
                subject_digest=previous_row.record_digest,
                lifecycle_history=True,
            )
        except V5AuthorityError as exc:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_LIFECYCLE_AUTHORITY_INVALID"}
            ) from exc
        event = self.session.get(Event, current_receipt.event_id)
        context = (event.payload or {}).get("manifest_activation_context") if event else None
        if not isinstance(context, dict):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_ACTIVATION_CONTEXT_INVALID"}
            )
        return payload, (previous_receipt, current_receipt), context

    @staticmethod
    def _require_exact_binding(
        binding: Any,
        *,
        kind: str,
        subject_id: str,
        revision: int | None,
        digest: str,
    ) -> None:
        if binding != {
            "kind": kind,
            "id": subject_id,
            "revision": revision,
            "digest": digest,
        }:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_GRAPH_BINDING_MISMATCH"}
            )

    def _verify_manifest_terminal(self, row: Any) -> None:
        PublicIdempotencyService.verify_terminal_presence(row)
        if row.resource_kind != "system_version_set" or not isinstance(
            row.resource_id, str
        ):
            raise PublicIdempotencyError("INTERNAL_ERROR")
        version_set = self.session.get(SystemVersionSet, row.resource_id)
        if version_set is None or version_set.workspace_id != row.workspace_id:
            raise PublicIdempotencyError("INTERNAL_ERROR")
        try:
            authoritative = self._reconstruct_manifest_response(
                workspace_id=row.workspace_id,
                version_set=version_set,
                request_id=row.request_id,
                expected_principal_id=row.principal_id,
            )
            if row.response_payload != authoritative:
                raise SystemVersionsError(
                    "INTERNAL_ERROR",
                    details={"reason": "MANIFEST_TERMINAL_GRAPH_MISMATCH"},
                )
        except SystemVersionsError as exc:
            raise PublicIdempotencyError("INTERNAL_ERROR") from exc

    def _reconstruct_manifest_response(
        self,
        *,
        workspace_id: str,
        version_set: SystemVersionSet,
        request_id: str,
        expected_principal_id: str,
    ) -> dict[str, Any]:
        application = self.session.get(AIApplication, version_set.application_id)
        environment = self.session.get(Environment, version_set.declared_environment_id)
        if (
            application is None
            or environment is None
            or application.workspace_id != workspace_id
            or environment.workspace_id != workspace_id
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        graph_receipts: dict[str, AuthorityReceipt] = {}

        def remember_receipts(*receipts: AuthorityReceipt) -> None:
            for receipt in receipts:
                if receipt.authority_receipt_id in graph_receipts:
                    raise SystemVersionsError(
                        "INTERNAL_ERROR",
                        details={"reason": "MANIFEST_AUTHORITY_CARDINALITY_INVALID"},
                    )
                graph_receipts[receipt.authority_receipt_id] = receipt

        app_payload, app_receipts, app_context = self._validate_lifecycle_graph_subject(
            row=application,
            subject_kind="AI_APPLICATION",
            id_attr="application_id",
            scalar_fields=(
                "project_id", "slug", "display_name", "owner_principal_ids",
                "criticality", "data_classification", "governance_mode",
                "lifecycle_state",
            ),
        )
        env_payload, env_receipt = self._validate_receipt_backed_record(
            row=environment,
            subject_kind="ENVIRONMENT",
            id_attr="environment_id",
            subject_revision=1,
            scalar_fields=(
                "application_id", "logical_name", "risk_classification",
                "lifecycle_state",
            ),
        )
        remember_receipts(*app_receipts, env_receipt)
        app_receipt = app_receipts[-1]
        if environment.application_id != application.application_id:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )

        version_payload, version_receipt = self._validate_receipt_backed_record(
            row=version_set,
            subject_kind="SYSTEM_VERSION_SET",
            id_attr="system_version_set_id",
            subject_revision=1,
            scalar_fields=(
                "application_id", "declared_environment_id",
                "exact_component_revision_bindings",
                "exact_topology_revision_binding", "identity_assurance_summary",
                "provenance_receipt_ids", "version_set_digest", "manifest_digest",
            ),
        )
        remember_receipts(version_receipt)
        manifest = version_payload.get("manifest")
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {
                "manifest_id", "manifest_digest", "audit_ref",
                "approver_policy_revision",
            }
            or manifest.get("manifest_digest") != version_set.manifest_digest
            or not isinstance(manifest.get("manifest_id"), str)
            or not isinstance(manifest.get("audit_ref"), str)
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_METADATA_INVALID"}
            )

        revisions = sorted(
            self._validate_version_set_component_revision_bindings(
                version_set, workspace_id=workspace_id
            ),
            key=lambda row: row.envelope_payload.get("logical_name", ""),
        )
        revision_payloads: list[dict[str, Any]] = []
        components: list[SystemComponent] = []
        component_payloads: list[dict[str, Any]] = []
        activation_contexts = [app_context]
        for revision in revisions:
            revision_payload, revision_receipt = self._validate_receipt_backed_record(
                row=revision,
                subject_kind="COMPONENT_REVISION",
                id_attr="component_revision_id",
                subject_revision=1,
                scalar_fields=(
                    "application_id", "component_id", "component_kind",
                    "identity_locator", "identity_assurance", "configuration_digest",
                    "exact_provenance_receipt_bindings", "declared_version",
                    "content_digest", "provider_origin", "unknown_reason",
                    "interface_schema_digest", "permission_manifest_digest",
                    "dependency_lock_digest", "dataset_role", "artifact_refs",
                ),
            )
            component = self.session.get(SystemComponent, revision.component_id)
            if component is None or component.application_id != application.application_id:
                raise SystemVersionsError(
                    "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
                )
            component_payload, component_receipts, context = (
                self._validate_lifecycle_graph_subject(
                    row=component,
                    subject_kind="SYSTEM_COMPONENT",
                    id_attr="component_id",
                    scalar_fields=(
                        "application_id", "component_kind", "logical_name",
                        "owner_principal_ids", "criticality", "data_classification",
                        "permission_classification", "effect_classification",
                        "dataset_role", "lifecycle_state",
                    ),
                )
            )
            remember_receipts(revision_receipt, *component_receipts)
            self._require_exact_binding(
                revision_payload.get("exact_system_component_binding"),
                kind="SYSTEM_COMPONENT",
                subject_id=component.component_id,
                revision=2,
                digest=component.record_digest,
            )
            revision_payloads.append(revision_payload)
            components.append(component)
            component_payloads.append(component_payload)
            activation_contexts.append(context)

        approver_revision: ComponentRevision | None = None
        approver_payload: dict[str, Any] | None = None
        approver_binding = manifest.get("approver_policy_revision")
        approver_component: SystemComponent | None = None
        if approver_binding is not None:
            if not isinstance(approver_binding, dict) or not isinstance(
                approver_binding.get("id"), str
            ):
                raise SystemVersionsError(
                    "INTERNAL_ERROR", details={"reason": "MANIFEST_APPROVER_INVALID"}
                )
            approver_revision = self.session.get(ComponentRevision, approver_binding["id"])
            if approver_revision is None:
                raise SystemVersionsError(
                    "INTERNAL_ERROR", details={"reason": "MANIFEST_APPROVER_INVALID"}
                )
            self._require_exact_binding(
                approver_binding,
                kind="COMPONENT_REVISION",
                subject_id=approver_revision.component_revision_id,
                revision=1,
                digest=approver_revision.record_digest,
            )
            self._validate_persisted_component_revision_binding(
                approver_revision, workspace_id=workspace_id
            )
            approver_payload, approver_receipt = self._validate_receipt_backed_record(
                row=approver_revision,
                subject_kind="COMPONENT_REVISION",
                id_attr="component_revision_id",
                subject_revision=1,
                scalar_fields=(
                    "application_id", "component_id", "component_kind",
                    "identity_locator", "identity_assurance", "configuration_digest",
                    "exact_provenance_receipt_bindings", "declared_version",
                    "content_digest", "provider_origin", "unknown_reason",
                    "interface_schema_digest", "permission_manifest_digest",
                    "dependency_lock_digest", "dataset_role", "artifact_refs",
                ),
            )
            approver_component = self.session.get(
                SystemComponent, approver_revision.component_id
            )
            if approver_component is None:
                raise SystemVersionsError(
                    "INTERNAL_ERROR", details={"reason": "MANIFEST_APPROVER_INVALID"}
                )
            _, approver_component_receipts, approver_context = (
                self._validate_lifecycle_graph_subject(
                    row=approver_component,
                    subject_kind="SYSTEM_COMPONENT",
                    id_attr="component_id",
                    scalar_fields=(
                        "application_id", "component_kind", "logical_name",
                        "owner_principal_ids", "criticality", "data_classification",
                        "permission_classification", "effect_classification",
                        "dataset_role", "lifecycle_state",
                    ),
                )
            )
            remember_receipts(approver_receipt, *approver_component_receipts)
            activation_contexts.append(approver_context)

        topology_binding = version_set.exact_topology_revision_binding
        if not isinstance(topology_binding, dict) or not isinstance(
            topology_binding.get("id"), str
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_TOPOLOGY_INVALID"}
            )
        topology = self.session.get(TopologyRevision, topology_binding["id"])
        if topology is None or topology.workspace_id != workspace_id:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        self._require_exact_binding(
            topology_binding,
            kind="TOPOLOGY_REVISION",
            subject_id=topology.topology_revision_id,
            revision=1,
            digest=topology.record_digest,
        )
        topology_payload, topology_receipt = self._validate_receipt_backed_record(
            row=topology,
            subject_kind="TOPOLOGY_REVISION",
            id_attr="topology_revision_id",
            subject_revision=1,
            scalar_fields=(
                "application_id", "component_ids", "exact_edge_revision_bindings",
                "topology_digest", "provenance_receipt_ids",
            ),
        )
        remember_receipts(topology_receipt)
        if topology.component_ids != sorted(component.component_id for component in components):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_TOPOLOGY_COMPONENT_MISMATCH"}
            )
        edges: list[DependencyEdge] = []
        edge_payloads: list[dict[str, Any]] = []
        for binding in topology.exact_edge_revision_bindings:
            if not isinstance(binding, dict) or not isinstance(binding.get("id"), str):
                raise SystemVersionsError(
                    "INTERNAL_ERROR", details={"reason": "MANIFEST_EDGE_BINDING_INVALID"}
                )
            edge = self.session.get(DependencyEdge, binding["id"])
            if edge is None:
                raise SystemVersionsError(
                    "INTERNAL_ERROR", details={"reason": "MANIFEST_EDGE_BINDING_INVALID"}
                )
            self._require_exact_binding(
                binding,
                kind="DEPENDENCY_EDGE",
                subject_id=edge.edge_id,
                revision=1,
                digest=edge.record_digest,
            )
            edge_payload, edge_receipt = self._validate_receipt_backed_record(
                row=edge,
                subject_kind="DEPENDENCY_EDGE",
                id_attr="edge_id",
                subject_revision=1,
                scalar_fields=(
                    "application_id", "from_component_id", "to_component_id",
                    "relation", "required", "edge_digest",
                ),
            )
            remember_receipts(edge_receipt)
            expected_edge_digest = canonical_digest(
                {
                    "from_component_id": edge.from_component_id,
                    "to_component_id": edge.to_component_id,
                    "relation": edge.relation,
                    "required": edge.required,
                }
            )
            if (
                edge.application_id != application.application_id
                or edge.edge_digest != expected_edge_digest
                or edge.from_component_id not in topology.component_ids
                or edge.to_component_id not in topology.component_ids
            ):
                raise SystemVersionsError(
                    "INTERNAL_ERROR", details={"reason": "MANIFEST_EDGE_INVALID"}
                )
            edges.append(edge)
            edge_payloads.append(edge_payload)
        if (
            self._topology_digest(edges) != topology.topology_digest
            or topology.provenance_receipt_ids
            != sorted({app_receipt.authority_receipt_id, env_receipt.authority_receipt_id})
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_TOPOLOGY_INVALID"}
            )

        assignments = list(
            self.session.scalars(
                select(SystemAssignment).where(
                SystemAssignment.workspace_id == workspace_id,
                SystemAssignment.application_id == version_set.application_id,
                SystemAssignment.environment_id == version_set.declared_environment_id,
                SystemAssignment.lifecycle_state == "ACTIVE",
                )
            ).all()
        )
        if len(assignments) != 1:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        assignment = assignments[0]
        assignment_payload, assignment_receipt = self._validate_receipt_backed_record(
            row=assignment,
            subject_kind="SYSTEM_ASSIGNMENT",
            id_attr="assignment_id",
            subject_revision=1,
            scalar_fields=(
                "application_id", "environment_id", "generation", "lifecycle_state",
                "transition_kind", "exact_previous_assignment_binding_or_null",
                "exact_slot_version_set_bindings", "exposure",
                "expected_previous_generation", "exact_assignment_authority_binding",
                "requested_by_external_operation_id",
            ),
        )
        remember_receipts(assignment_receipt)
        expected_slot = {
            "slot": "PRIMARY",
            "kind": "SYSTEM_VERSION_SET",
            "id": version_set.system_version_set_id,
            "revision": 1,
            "digest": version_set.record_digest,
        }
        if (
            assignment.generation != 1
            or assignment.transition_kind != "BOOTSTRAP"
            or assignment.exact_previous_assignment_binding_or_null is not None
            or assignment.expected_previous_generation is not None
            or assignment.exposure != "EXPOSED"
            or assignment.requested_by_external_operation_id is not None
            or assignment.exact_slot_version_set_bindings != [expected_slot]
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_ASSIGNMENT_INVALID"}
            )
        authority_binding = assignment.exact_assignment_authority_binding
        if not isinstance(authority_binding, dict) or not isinstance(
            authority_binding.get("id"), str
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        attestation = self.session.get(BootstrapAttestation, authority_binding["id"])
        if attestation is None:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        expected_authority = {
            "binding_kind": "BOOTSTRAP_ATTESTATION",
            "id": attestation.bootstrap_attestation_id,
            "revision": 1,
            "digest": attestation.record_digest,
        }
        if authority_binding != expected_authority:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_ATTESTATION_BINDING_INVALID"}
            )
        attestation_payload, attestation_receipt = self._validate_receipt_backed_record(
            row=attestation,
            subject_kind="BOOTSTRAP_ATTESTATION",
            id_attr="bootstrap_attestation_id",
            subject_revision=1,
            scalar_fields=(
                "application_id", "environment_id",
                "exact_initial_system_version_set_binding", "attester_principal_id",
                "attester_trust_role", "attestation_scope",
            ),
        )
        remember_receipts(attestation_receipt)
        self._require_exact_binding(
            attestation.exact_initial_system_version_set_binding,
            kind="SYSTEM_VERSION_SET",
            subject_id=version_set.system_version_set_id,
            revision=1,
            digest=version_set.record_digest,
        )
        principal_row = self.session.get(PublicPrincipal, expected_principal_id)
        if (
            attestation.attester_principal_id != expected_principal_id
            or principal_row is None
            or attestation.attester_trust_role not in (principal_row.trust_roles or [])
            or attestation.attestation_scope != _BOOTSTRAP_ATTESTATION_SCOPE
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_ATTESTER_INVALID"}
            )

        principal_row = self.session.get(PublicPrincipal, expected_principal_id)
        if principal_row is None or not activation_contexts:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_ROOT_AUDIT_INVALID"}
            )
        root_context = activation_contexts[0]
        if (
            any(context != root_context for context in activation_contexts)
            or root_context.get("root_intent") != _IMPORT_INTENT
            or root_context.get("workflow_owner") != "manifest_import_coordinator"
            or root_context.get("authenticated_request_digest")
            != version_set.manifest_digest
            or root_context.get("manifest_digest") != version_set.manifest_digest
            or root_context.get("workspace_id") != workspace_id
            or root_context.get("initiating_principal_id") != expected_principal_id
            or root_context.get("initiating_principal_type")
            != principal_row.principal_type
            or root_context.get("initiating_command_audit_ref") != manifest["audit_ref"]
            or not isinstance(root_context.get("idempotency_key"), str)
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_ACTIVATION_CONTEXT_INVALID"}
            )
        try:
            root_audit = V5ManifestImportCoordinator(
                self.session, audit_service=self.audit, authority_service=self.authority
            ).validate_persisted_root_audit(
                audit_ref=manifest["audit_ref"],
                workspace_id=workspace_id,
                principal_id=expected_principal_id,
                manifest_digest=version_set.manifest_digest,
                authenticated_request_digest=root_context[
                    "authenticated_request_digest"
                ],
                idempotency_key=root_context["idempotency_key"],
            )
        except ManifestImportCompositionError as exc:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_ROOT_AUDIT_INVALID"}
            ) from exc
        expected_receipt_count = (
            7
            + (3 * len(revisions))
            + len(edges)
            + (3 if approver_revision is not None else 0)
        )
        graph_events: dict[str, Event] = {}
        graph_outboxes: dict[str, Outbox] = {}
        controller_audits: dict[str, Audit] = {}
        for receipt in graph_receipts.values():
            event = self.session.get(Event, receipt.event_id)
            audit = (
                self.session.get(Audit, receipt.audit_ref.removeprefix("audit://"))
                if receipt.audit_ref.startswith("audit://aud_")
                else None
            )
            outboxes = list(
                self.session.scalars(
                    select(Outbox).where(Outbox.source_event_id == receipt.event_id)
                ).all()
            )
            if event is None or audit is None or len(outboxes) != 1:
                raise SystemVersionsError(
                    "INTERNAL_ERROR",
                    details={"reason": "MANIFEST_TRANSACTION_GRAPH_INVALID"},
                )
            if (
                event.event_id in graph_events
                or audit.audit_id in controller_audits
                or outboxes[0].outbox_id in graph_outboxes
            ):
                raise SystemVersionsError(
                    "INTERNAL_ERROR",
                    details={"reason": "MANIFEST_AUTHORITY_CARDINALITY_INVALID"},
                )
            graph_events[event.event_id] = event
            controller_audits[audit.audit_id] = audit
            graph_outboxes[outboxes[0].outbox_id] = outboxes[0]
        if (
            len(graph_receipts) != expected_receipt_count
            or len(graph_events) != expected_receipt_count
            or len(graph_outboxes) != expected_receipt_count
            or len(controller_audits) != expected_receipt_count
            or {
                root_audit.transaction_id,
                *(receipt.transaction_id for receipt in graph_receipts.values()),
                *(event.transaction_id for event in graph_events.values()),
                *(outbox.transaction_id for outbox in graph_outboxes.values()),
                *(audit.transaction_id for audit in controller_audits.values()),
            }
            != {root_audit.transaction_id}
        ):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_TRANSACTION_GRAPH_INVALID"}
            )
        component_name_by_id = {
            component.component_id: component.logical_name for component in components
        }
        edge_payloads.sort(
            key=lambda payload: (
                component_name_by_id[payload["from_component_id"]],
                component_name_by_id[payload["to_component_id"]],
                payload["relation"],
                payload["required"],
                payload["edge_id"],
            )
        )
        return {
            "schema_version": "2.0",
            "workspace_id": workspace_id,
            "request_id": request_id,
            "audit_ref": manifest.get("audit_ref"),
            "manifest_id": manifest.get("manifest_id"),
            "manifest_digest": version_set.manifest_digest,
            "application": app_payload,
            "environment": env_payload,
            "components": component_payloads,
            "dependency_edges": edge_payloads,
            "component_revisions": revision_payloads,
            "topology_revision": topology_payload,
            "system_version_set": version_payload,
            "bootstrap_attestation": attestation_payload,
            "system_assignment": assignment_payload,
            "approver_policy_revision": approver_payload,
        }

    def _persist_manifest_response(
        self,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        audit_ref: str,
        resource_id: str,
        response_core: dict[str, Any],
        completed_at: datetime,
        replayed: bool = False,
    ) -> SystemManifestImportResponse:
        response_digest = canonical_digest(response_core)
        receipt_id = new_idempotency_receipt_id()
        receipt: dict[str, Any] = {
            "schema_version": "1.0",
            "workspace_id": principal.workspace_id,
            "principal_id": principal.principal_id,
            "intent": _IMPORT_INTENT,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "resource": {"kind": "system_version_set", "id": resource_id},
            "operation_id": None,
            "request_id": request_id,
            "audit_ref": audit_ref,
            "status": "COMPLETED",
            "response_digest": response_digest,
            "created_at": _wire_time(completed_at),
            "idempotency_receipt_id": receipt_id,
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/receipt_digest)",
            "receipt_digest": "",
        }
        receipt_digest = record_digest(receipt, self_digest_field="receipt_digest")
        receipt["receipt_digest"] = receipt_digest
        try:
            self.idempotency.store_completed_catalog(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=_IMPORT_INTENT,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                resource_kind="system_version_set",
                resource_id=resource_id,
                request_id=request_id,
                audit_ref=audit_ref,
                response_payload=response_core,
                response_digest=response_digest,
                receipt_payload=receipt,
                receipt_digest=receipt_digest,
                idempotency_receipt_id=receipt_id,
                completed_at=completed_at,
                response_model=SystemManifestImportResponse,
                receipt_model=V5IdempotencyReceipt,
                resource_field="system_version_set",
                resource_id_field="system_version_set_id",
            )
        except PublicIdempotencyError as exc:
            raise SystemVersionsError(exc.code, workspace_id=principal.workspace_id) from exc
        return SystemManifestImportResponse.model_validate(
            {**response_core, "idempotency": {"receipt": receipt, "replayed": replayed}}
        )

    # ------------------------------------------------------------------- reads

    def get_system_version(
        self,
        system_version_set_id: str,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str | None = None,
    ) -> SystemVersionGetResponse:
        request_id = request_id or new_request_id()
        action = "system-versions.get"
        self._validate_principal_row(principal)
        if principal.principal_type not in _READ_PRINCIPAL_TYPES:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"system_version_set:{system_version_set_id}",
            )
        self._require_read_scope(
            principal=principal, request_id=request_id, action=action,
            target=f"system_version_set:{system_version_set_id}",
        )
        row = self.session.get(SystemVersionSet, system_version_set_id)
        if row is None or row.workspace_id != principal.workspace_id:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"system_version_set:{system_version_set_id}",
            )
        self._assert_application_readable(
            principal, row.application_id, action, request_id
        )
        audit = self._record_read_audit(
            principal=principal,
            action=action,
            target=f"system_version_set:{system_version_set_id}",
            params={"request_id": request_id, "resource_requested": True},
            evidence_refs={
                "resource_kind": "system_version_set",
                "resource_id": system_version_set_id,
                "record_digest": row.record_digest,
            },
        )
        return SystemVersionGetResponse.model_validate(
            {
                "schema_version": "2.0",
                "workspace_id": principal.workspace_id,
                "request_id": request_id,
                "audit_ref": audit.audit_ref,
                "system_version_set": row.envelope_payload,
            }
        )

    def diff_system_versions(
        self,
        base_system_version_set_id: str,
        target_system_version_set_id: str,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str | None = None,
    ) -> SystemVersionDiffResponse:
        request_id = request_id or new_request_id()
        action = "system-versions.diff"
        self._validate_principal_row(principal)
        if principal.principal_type not in _READ_PRINCIPAL_TYPES:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target="system_version_set:diff",
            )
        self._require_read_scope(
            principal=principal, request_id=request_id, action=action,
            target="system_version_set:diff",
        )
        base = self.session.get(SystemVersionSet, base_system_version_set_id)
        target = self.session.get(SystemVersionSet, target_system_version_set_id)
        if (
            base is None
            or target is None
            or base.workspace_id != principal.workspace_id
            or target.workspace_id != principal.workspace_id
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target="system_version_set:diff",
            )
        self._assert_application_readable(principal, base.application_id, action, request_id)
        self._assert_application_readable(principal, target.application_id, action, request_id)

        added, removed, changed, substitutions, expansions = self._semantic_diff(base, target)
        audit = self._record_read_audit(
            principal=principal,
            action=action,
            target="system_version_set:diff",
            params={
                "request_id": request_id,
                "base_system_version_set_id": base_system_version_set_id,
                "target_system_version_set_id": target_system_version_set_id,
            },
            evidence_refs={
                "base_system_version_set_id": base_system_version_set_id,
                "target_system_version_set_id": target_system_version_set_id,
            },
        )
        return SystemVersionDiffResponse.model_validate(
            {
                "schema_version": "2.0",
                "workspace_id": principal.workspace_id,
                "request_id": request_id,
                "audit_ref": audit.audit_ref,
                "base_system_version_set_id": base_system_version_set_id,
                "target_system_version_set_id": target_system_version_set_id,
                "added": added,
                "removed": removed,
                "changed": changed,
                "dependency_substitutions": substitutions,
                "policy_permission_expansions": expansions,
            }
        )

    def _assert_application_readable(
        self,
        principal: AcceptedPrincipalContext,
        application_id: str,
        action: str,
        request_id: str,
    ) -> None:
        application = self.session.get(AIApplication, application_id)
        if (
            application is None
            or application.workspace_id != principal.workspace_id
            or application.project_id not in principal.project_ids
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"ai_application:{application_id}",
            )

    def _semantic_diff(
        self, base: SystemVersionSet, target: SystemVersionSet
    ) -> tuple[
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        def _load_revisions(version_set: SystemVersionSet) -> dict[str, ComponentRevision]:
            rows = {
                binding["id"]: self.session.get(ComponentRevision, binding["id"])
                for binding in version_set.exact_component_revision_bindings
            }
            return {
                rev_id: row
                for rev_id, row in rows.items()
                if row is not None
            }

        base_revs = _load_revisions(base)
        target_revs = _load_revisions(target)
        base_by_component = {rev.component_id: rev for rev in base_revs.values()}
        target_by_component = {rev.component_id: rev for rev in target_revs.values()}
        components = {
            component_id: self.session.get(SystemComponent, component_id)
            for component_id in set(base_by_component) | set(target_by_component)
        }
        base_components = {
            component_id: row
            for component_id, row in components.items()
            if row is not None and component_id in base_by_component
        }
        target_components = {
            component_id: row
            for component_id, row in components.items()
            if row is not None and component_id in target_by_component
        }

        added: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        changed: list[dict[str, Any]] = []
        for component_id in sorted(set(target_by_component) - set(base_by_component)):
            component = target_components[component_id]
            added.append(
                {
                    "component_id": component_id,
                    "logical_name": component.logical_name,
                    "base_digest": None,
                    "target_digest": target_by_component[component_id].configuration_digest,
                    "diff_kind": "ADDED",
                    "details": {},
                }
            )
        for component_id in sorted(set(base_by_component) - set(target_by_component)):
            component = base_components[component_id]
            removed.append(
                {
                    "component_id": component_id,
                    "logical_name": component.logical_name,
                    "base_digest": base_by_component[component_id].configuration_digest,
                    "target_digest": None,
                    "diff_kind": "REMOVED",
                    "details": {},
                }
            )
        for component_id in sorted(set(base_by_component) & set(target_by_component)):
            base_rev = base_by_component[component_id]
            target_rev = target_by_component[component_id]
            component = base_components[component_id]
            if base_rev.configuration_digest != target_rev.configuration_digest:
                changed.append(
                    {
                        "component_id": component_id,
                        "logical_name": component.logical_name,
                        "base_digest": base_rev.configuration_digest,
                        "target_digest": target_rev.configuration_digest,
                        "diff_kind": "DIGEST_CHANGED",
                        "details": {
                            "identity_assurance": target_rev.identity_assurance,
                            "component_kind": target_rev.component_kind,
                        },
                    }
                )

        def _load_edges(version_set: SystemVersionSet) -> list[DependencyEdge]:
            topology = self.session.get(
                TopologyRevision, version_set.exact_topology_revision_binding["id"]
            )
            if topology is None:
                return []
            return [
                row
                for row in (
                    self.session.get(DependencyEdge, binding["id"])
                    for binding in topology.exact_edge_revision_bindings
                )
                if row is not None
            ]

        base_edges = _load_edges(base)
        target_edges = _load_edges(target)
        base_edge_map: dict[tuple[str, str], DependencyEdge] = {
            (edge.from_component_id, edge.relation): edge for edge in base_edges
        }
        target_edge_map: dict[tuple[str, str], DependencyEdge] = {
            (edge.from_component_id, edge.relation): edge for edge in target_edges
        }
        substitutions: list[dict[str, Any]] = []
        for (from_id, relation) in sorted(set(base_edge_map) & set(target_edge_map)):
            base_edge = base_edge_map[(from_id, relation)]
            target_edge = target_edge_map[(from_id, relation)]
            if base_edge.to_component_id != target_edge.to_component_id:
                from_component = base_components.get(from_id)
                substitutions.append(
                    {
                        "component_id": from_id,
                        "logical_name": (
                            from_component.logical_name if from_component is not None else from_id
                        ),
                        "base_digest": base_edge.edge_digest,
                        "target_digest": target_edge.edge_digest,
                        "diff_kind": "DEPENDENCY_SUBSTITUTION",
                        "details": {
                            "relation": relation,
                            "from_component_id": from_id,
                            "base_to_component_id": base_edge.to_component_id,
                            "target_to_component_id": target_edge.to_component_id,
                        },
                    }
                )

        expansions: list[dict[str, Any]] = []
        for component_id in sorted(set(base_by_component) & set(target_by_component)):
            base_rev = base_by_component[component_id]
            target_rev = target_by_component[component_id]
            component = base_components[component_id]
            if component.component_kind != "POLICY":
                continue
            base_permission = base_rev.permission_manifest_digest
            target_permission = target_rev.permission_manifest_digest
            if base_permission != target_permission:
                expansions.append(
                    {
                        "component_id": component_id,
                        "logical_name": component.logical_name,
                        "base_digest": base_permission,
                        "target_digest": target_permission,
                        "diff_kind": "PERMISSION_EXPANSION",
                        "details": {
                            "component_kind": "POLICY",
                            "component_permission_classification": (
                                component.permission_classification
                            ),
                        },
                    }
                )
        return added, removed, changed, substitutions, expansions


__all__ = [
    "SystemVersionsError",
    "SystemVersionsService",
    "V5ReadDenial",
]
