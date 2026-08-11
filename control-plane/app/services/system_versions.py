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

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.v4_tables import PublicPrincipal
from app.models.v5_tables import (
    AIApplication,
    BootstrapAttestation,
    ComponentRevision,
    DependencyEdge,
    Environment,
    SystemAssignment,
    SystemComponent,
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
from app.utils.ids import (
    new_application_id,
    new_authority_receipt_id,
    new_bootstrap_attestation_id,
    new_catalog_environment_id,
    new_component_revision_id,
    new_dependency_edge_id,
    new_idempotency_receipt_id,
    new_request_id,
    new_system_assignment_id,
    new_system_component_id,
    new_system_manifest_id,
    new_system_version_set_id,
    new_topology_revision_id,
    new_transaction_id,
)
from app.utils.v4_integrity import canonical_digest, record_digest
from app.utils.v5_integrity import V5_HASH_RULE, v5_record_digest

Clock = Callable[[], datetime]

_IMPORT_INTENT = "system-manifests.import"
_IMPORT_SCOPE = "system_manifests:import"
_READ_SCOPE = "system_versions:read"
_IMPORT_PRINCIPAL_TYPES = frozenset({"human", "service"})
_READ_PRINCIPAL_TYPES = frozenset({"human", "external_agent", "service", "connector"})
_BOOTSTRAP_ATTESTER_ROLE = "integrator"
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
        "dependency_edge", "dependency_edge", False,
    ),
    "COMPONENT_REVISION": _VersionSpec(
        "COMPONENT_REVISION", "component_revision.recorded", "component-revisions.record",
        "component_revision", "component_revision", False,
    ),
    "TOPOLOGY_REVISION": _VersionSpec(
        "TOPOLOGY_REVISION", "topology_revision.recorded", "topology-revisions.record",
        "topology_revision", "topology_revision", False,
    ),
    "SYSTEM_VERSION_SET": _VersionSpec(
        "SYSTEM_VERSION_SET", "system_version_set.recorded", "system-versions.record",
        "system_version_set", "system_version_set", False,
    ),
    "BOOTSTRAP_ATTESTATION": _VersionSpec(
        "BOOTSTRAP_ATTESTATION", "bootstrap_attestation.recorded",
        "bootstrap-attestations.record", "bootstrap_attestation", "bootstrap_attestation", False,
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
        event_payload: dict[str, Any] = {
            **business_payload,
            "subject_kind": kind,
            "subject_id": subject_id,
            "subject_revision": subject_revision,
            "subject_digest": digest,
            "authority_receipt_id": envelope["authority_receipt_id"],
        }
        try:
            event = self.events.append_event(
                workspace_id=workspace_id,
                aggregate_type=spec.aggregate_type,
                aggregate_id=subject_id,
                event_type=spec.event_type,
                payload=event_payload,
                causation_id=request_id,
                correlation_id=correlation_id,
                actor_principal=controller.controller_principal,
                transaction_id=transaction_id,
                occurred_at=now,
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
        if kind == "AI_APPLICATION":
            return AIApplication(
                application_id=payload["application_id"],
                workspace_id=payload["workspace_id"],
                project_id=payload["project_id"],
                slug=payload["slug"],
                display_name=payload["display_name"],
                owner_principal_ids=list(payload["owner_principal_ids"]),
                criticality=payload["criticality"],
                data_classification=payload["data_classification"],
                governance_mode=payload["governance_mode"],
                lifecycle_state=payload["lifecycle_state"],
                revision=1,
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
                updated_at=now,
            )
        if kind == "ENVIRONMENT":
            return Environment(
                environment_id=payload["environment_id"],
                workspace_id=payload["workspace_id"],
                application_id=payload["application_id"],
                logical_name=payload["logical_name"],
                risk_classification=payload["risk_classification"],
                lifecycle_state=payload["lifecycle_state"],
                revision=1,
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
                updated_at=now,
            )
        if kind == "SYSTEM_COMPONENT":
            return SystemComponent(
                component_id=payload["component_id"],
                workspace_id=payload["workspace_id"],
                application_id=payload["application_id"],
                component_kind=payload["component_kind"],
                logical_name=payload["logical_name"],
                owner_principal_ids=list(payload["owner_principal_ids"]),
                criticality=payload["criticality"],
                data_classification=payload["data_classification"],
                permission_classification=payload["permission_classification"],
                effect_classification=payload["effect_classification"],
                dataset_role=payload.get("dataset_role"),
                lifecycle_state=payload["lifecycle_state"],
                revision=1,
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
                updated_at=now,
            )
        if kind == "DEPENDENCY_EDGE":
            return DependencyEdge(
                edge_id=payload["edge_id"],
                workspace_id=payload["workspace_id"],
                application_id=payload["application_id"],
                from_component_id=payload["from_component_id"],
                to_component_id=payload["to_component_id"],
                relation=payload["relation"],
                required=payload["required"],
                edge_digest=payload["edge_digest"],
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
            )
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
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=_IMPORT_INTENT,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                verify_terminal=PublicIdempotencyService.verify_terminal_presence,
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

        self._validate_principal_row(principal)
        self._require_import_principal(principal)
        manifest_digest = canonical_digest(body)

        replay = self._replay_by_manifest_digest(
            workspace_id=principal.workspace_id,
            manifest_digest=manifest_digest,
            principal=principal,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )
        if replay is not None:
            return replay

        self._require_workspace_bootstrap_empty(principal.workspace_id)
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
        self._validate_owner_principals(principal.workspace_id, sorted(set(all_owner_ids)))

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
                params={"request_fingerprint": request_fingerprint},
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

        # 1. application (canonical owner: application-catalog-controller)
        application_id = new_application_id()
        app_payload = {
            "application_id": application_id,
            "workspace_id": workspace_id,
            "project_id": request.application.project_id,
            "slug": request.application.slug,
            "display_name": request.application.display_name,
            "owner_principal_ids": list(request.application.owner_principal_ids),
            "criticality": request.application.criticality,
            "data_classification": request.application.data_classification,
            "governance_mode": request.application.governance_mode,
            "lifecycle_state": "ACTIVE",
            "record_envelope": self._envelope(
                workspace_id=workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=new_authority_receipt_id(),
            ),
        }
        _app_row, app_payload, app_digest = self._write_construct(
            kind="AI_APPLICATION",
            subject_id=application_id,
            workspace_id=workspace_id,
            envelope_payload=app_payload,
            business_payload={
                "application_id": application_id,
                "project_id": request.application.project_id,
                "slug": request.application.slug,
                "lifecycle_state": "ACTIVE",
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
        )

        # 2. environment
        environment_id = new_catalog_environment_id()
        env_payload = {
            "environment_id": environment_id,
            "workspace_id": workspace_id,
            "application_id": application_id,
            "logical_name": request.environment.logical_name,
            "risk_classification": request.environment.risk_classification,
            "lifecycle_state": "ACTIVE",
            "record_envelope": self._envelope(
                workspace_id=workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=new_authority_receipt_id(),
            ),
        }
        _env_row, env_payload, env_digest = self._write_construct(
            kind="ENVIRONMENT",
            subject_id=environment_id,
            workspace_id=workspace_id,
            envelope_payload=env_payload,
            business_payload={
                "environment_id": environment_id,
                "application_id": application_id,
                "logical_name": request.environment.logical_name,
                "lifecycle_state": "ACTIVE",
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
        )

        # 3. components (logical_name -> component_id)
        name_to_component_id: dict[str, str] = {}
        component_payloads: dict[str, dict[str, Any]] = {}
        for component in request.components:
            component_id = new_system_component_id()
            name_to_component_id[component.logical_name] = component_id
            payload = {
                "component_id": component_id,
                "workspace_id": workspace_id,
                "application_id": application_id,
                "component_kind": component.component_kind,
                "logical_name": component.logical_name,
                "owner_principal_ids": list(component.owner_principal_ids),
                "criticality": component.criticality,
                "data_classification": component.data_classification,
                "permission_classification": component.permission_classification,
                "effect_classification": component.effect_classification,
                "dataset_role": component.dataset_role,
                "lifecycle_state": "ACTIVE",
                "record_envelope": self._envelope(
                    workspace_id=workspace_id,
                    revision=1,
                    recorded_by_principal=principal.principal_id,
                    recorded_at=now,
                    authority_receipt_id=new_authority_receipt_id(),
                ),
            }
            _row, payload, _digest = self._write_construct(
                kind="SYSTEM_COMPONENT",
                subject_id=component_id,
                workspace_id=workspace_id,
                envelope_payload=payload,
                business_payload={
                    "component_id": component_id,
                    "application_id": application_id,
                    "component_kind": component.component_kind,
                    "logical_name": component.logical_name,
                    "lifecycle_state": "ACTIVE",
                },
                correlation_id=application_id,
                transaction_id=transaction_id,
                request_id=request_id,
                recorded_at=now,
            )
            component_payloads[component.logical_name] = payload

        # approver policy component + its independent trusted revision (recorded,
        # excluded from the runtime VersionSet bindings and the topology)
        approver_policy_payload: dict[str, Any] | None = None
        approver_policy_component_id: str | None = None
        approver_revision_payload: dict[str, Any] | None = None
        approver_revision_binding: dict[str, Any] | None = None
        if request.approver_policy is not None:
            approver_policy_component_id = new_system_component_id()
            payload = {
                "component_id": approver_policy_component_id,
                "workspace_id": workspace_id,
                "application_id": application_id,
                "component_kind": request.approver_policy.component_kind,
                "logical_name": request.approver_policy.logical_name,
                "owner_principal_ids": list(request.approver_policy.owner_principal_ids),
                "criticality": request.approver_policy.criticality,
                "data_classification": request.approver_policy.data_classification,
                "permission_classification": request.approver_policy.permission_classification,
                "effect_classification": request.approver_policy.effect_classification,
                "dataset_role": request.approver_policy.dataset_role,
                "lifecycle_state": "ACTIVE",
                "record_envelope": self._envelope(
                    workspace_id=workspace_id,
                    revision=1,
                    recorded_by_principal=principal.principal_id,
                    recorded_at=now,
                    authority_receipt_id=new_authority_receipt_id(),
                ),
            }
            _row, payload, _digest = self._write_construct(
                kind="SYSTEM_COMPONENT",
                subject_id=approver_policy_component_id,
                workspace_id=workspace_id,
                envelope_payload=payload,
                business_payload={
                    "component_id": approver_policy_component_id,
                    "application_id": application_id,
                    "component_kind": request.approver_policy.component_kind,
                    "logical_name": request.approver_policy.logical_name,
                    "lifecycle_state": "ACTIVE",
                },
                correlation_id=application_id,
                transaction_id=transaction_id,
                request_id=request_id,
                recorded_at=now,
            )
            approver_policy_payload = payload
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
                    },
                    correlation_id=application_id,
                    transaction_id=transaction_id,
                    request_id=request_id,
                    recorded_at=now,
                )
            )
            approver_revision_binding = {
                "kind": "COMPONENT_REVISION",
                "id": approver_revision_payload["component_revision_id"],
                "revision": None,
                "digest": approver_revision_digest,
            }

        # 4. dependency edges (manifest logical names -> component ids)
        edge_payloads: dict[str, dict[str, Any]] = {}
        for edge in request.dependency_edges:
            edge_id = new_dependency_edge_id()
            from_id = name_to_component_id[edge.from_component]
            to_id = name_to_component_id[edge.to_component]
            edge_digest = canonical_digest(
                {
                    "from_component_id": from_id,
                    "to_component_id": to_id,
                    "relation": edge.relation,
                    "required": edge.required,
                }
            )
            payload = {
                "edge_id": edge_id,
                "workspace_id": workspace_id,
                "application_id": application_id,
                "from_component_id": from_id,
                "to_component_id": to_id,
                "relation": edge.relation,
                "required": edge.required,
                "edge_digest": edge_digest,
                "record_envelope": self._envelope(
                    workspace_id=workspace_id,
                    revision=1,
                    recorded_by_principal=principal.principal_id,
                    recorded_at=now,
                    authority_receipt_id=new_authority_receipt_id(),
                ),
            }
            _row, payload, _digest = self._write_construct(
                kind="DEPENDENCY_EDGE",
                subject_id=edge_id,
                workspace_id=workspace_id,
                envelope_payload=payload,
                business_payload={
                    "edge_id": edge_id,
                    "application_id": application_id,
                    "from_component_id": from_id,
                    "to_component_id": to_id,
                    "relation": edge.relation,
                    "edge_digest": edge_digest,
                },
                correlation_id=application_id,
                transaction_id=transaction_id,
                request_id=request_id,
                recorded_at=now,
            )
            edge_payloads[edge_id] = payload

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
                },
                correlation_id=application_id,
                transaction_id=transaction_id,
                request_id=request_id,
                recorded_at=now,
            )
            revision_payloads[rev_payload["component_revision_id"]] = rev_payload
            revision_bindings.append(
                {
                    "kind": "COMPONENT_REVISION",
                    "id": rev_payload["component_revision_id"],
                    "revision": None,
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
        edge_rows = [
            self.session.get(DependencyEdge, edge_id)
            for edge_id in sorted(edge_payloads)
        ]
        component_ids = sorted(name_to_component_id[name] for name in name_to_component_id)
        topology_id = new_topology_revision_id()
        topology_digest = self._topology_digest([row for row in edge_rows if row is not None])
        edge_bindings = [
            {
                "kind": "DEPENDENCY_EDGE",
                "id": edge_id,
                "revision": None,
                "digest": edge_payloads[edge_id]["edge_digest"],
            }
            for edge_id in sorted(edge_payloads)
        ]
        topology_payload = {
            "topology_revision_id": topology_id,
            "workspace_id": workspace_id,
            "application_id": application_id,
            "component_ids": component_ids,
            "exact_edge_revision_bindings": edge_bindings,
            "topology_digest": topology_digest,
            "provenance_receipt_ids": sorted({app_digest, env_digest}),
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
                "topology_digest": topology_digest,
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
        )

        # 7. system version set (immutable; manifest digest replay key)
        topology_binding = {
            "kind": "TOPOLOGY_REVISION",
            "id": topology_id,
            "revision": None,
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
                "version_set_digest": version_set_digest,
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
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
                "revision": None,
                "digest": version_set_digest_actual,
            },
            "attester_principal_id": principal.principal_id,
            "attester_trust_role": _BOOTSTRAP_ATTESTER_ROLE,
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
                "attester_trust_role": _BOOTSTRAP_ATTESTER_ROLE,
                "attestation_scope": _BOOTSTRAP_ATTESTATION_SCOPE,
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
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
                    "kind": "SYSTEM_VERSION_SET",
                    "id": version_set_id,
                    "revision": None,
                    "digest": version_set_digest_actual,
                }
            ],
            "exposure": "EXPOSED",
            "expected_previous_generation": None,
            "exact_assignment_authority_binding": {
                "binding_kind": "BOOTSTRAP_ATTESTATION",
                "id": attestation_id,
                "revision": None,
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
            },
            correlation_id=application_id,
            transaction_id=transaction_id,
            request_id=request_id,
            recorded_at=now,
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
            "components": [component_payloads[name] for name in name_to_component_id],
            "dependency_edges": [edge_payloads[edge_id] for edge_id in sorted(edge_payloads)],
            "component_revisions": [
                revision_payloads[rev_id] for rev_id in sorted(revision_payloads)
            ],
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
        configuration_digest = self._component_configuration_digest(revision_spec)
        return {
            "component_revision_id": new_component_revision_id(),
            "workspace_id": workspace_id,
            "application_id": application_id,
            "component_id": component_id,
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

    def _require_workspace_bootstrap_empty(self, workspace_id: str) -> None:
        existing = self.session.scalar(
            select(AIApplication.application_id).where(
                AIApplication.workspace_id == workspace_id
            )
        )
        if existing is not None:
            raise SystemVersionsError(
                "CATALOG_CONFLICT",
                details={"reason": "MANIFEST_BOOTSTRAP_ALREADY_EXISTS"},
                workspace_id=workspace_id,
            )

    def _replay_by_manifest_digest(
        self,
        *,
        workspace_id: str,
        manifest_digest: str,
        principal: AcceptedPrincipalContext,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> SystemManifestImportResponse | None:
        version_set = self.session.scalar(
            select(SystemVersionSet).where(
                SystemVersionSet.workspace_id == workspace_id,
                SystemVersionSet.manifest_digest == manifest_digest,
            )
        )
        if version_set is None:
            return None
        response_core = self._reconstruct_manifest_response(
            workspace_id=workspace_id, version_set=version_set, request_id=request_id
        )
        return self._persist_manifest_response(
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            audit_ref=response_core["audit_ref"],
            resource_id=version_set.system_version_set_id,
            response_core=response_core,
            completed_at=_as_utc(self.clock()),
            replayed=True,
        )

    def _reconstruct_manifest_response(
        self,
        *,
        workspace_id: str,
        version_set: SystemVersionSet,
        request_id: str,
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
        assignment = self.session.scalar(
            select(SystemAssignment).where(
                SystemAssignment.workspace_id == workspace_id,
                SystemAssignment.application_id == version_set.application_id,
                SystemAssignment.environment_id == version_set.declared_environment_id,
                SystemAssignment.lifecycle_state == "ACTIVE",
            )
        )
        attestation: BootstrapAttestation | None = None
        if assignment is not None:
            authority = assignment.exact_assignment_authority_binding or {}
            if authority.get("binding_kind") == "BOOTSTRAP_ATTESTATION":
                attestation = self.session.get(BootstrapAttestation, authority.get("id"))
        if attestation is None:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        revision_ids = [
            binding["id"] for binding in version_set.exact_component_revision_bindings
        ]
        revisions = [
            self.session.get(ComponentRevision, rev_id) for rev_id in sorted(revision_ids)
        ]
        if any(row is None for row in revisions):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        component_ids = sorted({rev.component_id for rev in revisions})  # type: ignore[union-attr]
        components = [self.session.get(SystemComponent, cid) for cid in component_ids]
        if any(row is None for row in components):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        topology = self.session.get(
            TopologyRevision, version_set.exact_topology_revision_binding["id"]
        )
        if topology is None or topology.workspace_id != workspace_id:
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        edge_ids = [binding["id"] for binding in topology.exact_edge_revision_bindings]
        edges = [self.session.get(DependencyEdge, edge_id) for edge_id in sorted(edge_ids)]
        if any(row is None for row in edges):
            raise SystemVersionsError(
                "INTERNAL_ERROR", details={"reason": "MANIFEST_REPLAY_UNBOUND"}
            )
        manifest = (version_set.envelope_payload or {}).get("manifest") or {}
        approver_revision: ComponentRevision | None = None
        approver_binding = manifest.get("approver_policy_revision")
        if isinstance(approver_binding, dict) and isinstance(approver_binding.get("id"), str):
            approver_revision = self.session.get(ComponentRevision, approver_binding["id"])
        return {
            "schema_version": "2.0",
            "workspace_id": workspace_id,
            "request_id": request_id,
            "audit_ref": manifest.get("audit_ref"),
            "manifest_id": manifest.get("manifest_id"),
            "manifest_digest": version_set.manifest_digest,
            "application": application.envelope_payload,
            "environment": environment.envelope_payload,
            "components": [row.envelope_payload for row in components],  # type: ignore[union-attr]
            "dependency_edges": [row.envelope_payload for row in edges],  # type: ignore[union-attr]
            "component_revisions": [row.envelope_payload for row in revisions],  # type: ignore[union-attr]
            "topology_revision": topology.envelope_payload,
            "system_version_set": version_set.envelope_payload,
            "bootstrap_attestation": attestation.envelope_payload,
            "system_assignment": assignment.envelope_payload,
            "approver_policy_revision": (
                approver_revision.envelope_payload if approver_revision is not None else None
            ),
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
