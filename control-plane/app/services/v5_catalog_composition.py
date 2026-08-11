"""Catalog-owner command port for manifest composition.

This module is the single owner-local implementation for non-lifecycle
Environment and DependencyEdge records created by the manifest workflow.  It
uses the caller-owned Session/UoW and receives composition capabilities as
ports; the manifest coordinator only orchestrates and never constructs catalog
rows directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Audit
from app.models.v5_tables import AIApplication, DependencyEdge, Environment, SystemComponent
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.services.v4_audit import V4AuditIntegrityError, V4AuditUnavailable
from app.services.v5_authority import V5AuthorityError, V5AuthorityService, V5ResolvedController
from app.services.v5_composition import ManifestImportCompositionError
from app.utils.ids import (
    new_authority_receipt_id,
    new_catalog_environment_id,
    new_dependency_edge_id,
)
from app.utils.v4_integrity import canonical_digest
from app.utils.v5_integrity import v5_record_digest


@dataclass(frozen=True)
class ManifestOwnedCatalogRecord:
    subject_id: str
    payload: dict[str, Any]
    digest: str
    authority_receipt_id: str


class V5ManifestCatalogCommandPort:
    """Owner-local composed commands; flush-only and never commits."""

    def __init__(
        self,
        session: Session,
        *,
        authority: V5AuthorityService,
        validate_root: Callable[..., None],
        resolve_controller: Callable[..., V5ResolvedController],
        append_event: Callable[..., Any],
        record_controller_audit: Callable[..., Audit],
        record_envelope: Callable[..., dict[str, Any]],
        exact_binding: Callable[..., dict[str, Any]],
    ) -> None:
        self.session = session
        self.authority = authority
        self._validate_root = validate_root
        self._resolve_controller = resolve_controller
        self._append_event = append_event
        self._record_controller_audit = record_controller_audit
        self._record_envelope = record_envelope
        self._exact_binding = exact_binding

    def _record_owned_catalog_record(
        self,
        *,
        kind: Literal["ENVIRONMENT", "DEPENDENCY_EDGE"],
        subject_id: str,
        payload: dict[str, Any],
        business_payload: dict[str, Any],
        command: str,
        event_type: str,
        aggregate_type: str,
        principal: AcceptedPrincipalContext,
        transaction_id: str,
        request_id: str,
        initiating_audit_ref: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        correlation_id: str,
        recorded_at: datetime,
    ) -> ManifestOwnedCatalogRecord:
        from app.services.v4_event_store import V4EventStoreError

        controller = self._resolve_controller(
            workspace_id=principal.workspace_id,
            subject_kind=kind,
            command=command,
            event_type=event_type,
            recorded_at=recorded_at,
        )
        envelope = payload["record_envelope"]
        envelope["recorded_by_principal"] = controller.controller_principal
        digest = v5_record_digest(payload)
        envelope["record_digest"] = digest
        if kind == "ENVIRONMENT":
            row: Environment | DependencyEdge = Environment(
                environment_id=subject_id,
                workspace_id=principal.workspace_id,
                application_id=payload["application_id"],
                logical_name=payload["logical_name"],
                risk_classification=payload["risk_classification"],
                lifecycle_state=payload["lifecycle_state"],
                revision=1,
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=controller.controller_principal,
                created_at=recorded_at,
                updated_at=recorded_at,
            )
        else:
            row = DependencyEdge(
                edge_id=subject_id,
                workspace_id=principal.workspace_id,
                application_id=payload["application_id"],
                from_component_id=payload["from_component_id"],
                to_component_id=payload["to_component_id"],
                relation=payload["relation"],
                required=payload["required"],
                edge_digest=payload["edge_digest"],
                envelope_payload=payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=controller.controller_principal,
                created_at=recorded_at,
            )
        self.session.add(row)
        try:
            self.session.flush()
            exact_subject = self._exact_binding(
                kind=kind,
                subject_id=subject_id,
                revision=1,
                digest=digest,
            )
            self_field = (
                "exact_environment_binding"
                if kind == "ENVIRONMENT"
                else "exact_dependency_edge_binding"
            )
            major2_payload = {
                self_field: exact_subject,
                **{
                    key: value
                    for key, value in business_payload.items()
                    if key not in {"environment_id", "edge_id"}
                },
            }
            application = self.session.get(AIApplication, payload["application_id"])
            if application is None:
                raise ManifestImportCompositionError(
                    "v5.manifest.catalog_application_missing"
                )
            event = self._append_event(
                controller=controller,
                workspace_id=principal.workspace_id,
                aggregate_type=aggregate_type,
                aggregate_id=subject_id,
                event_type=event_type,
                payload=major2_payload,
                causation_id=request_id,
                correlation_id=correlation_id,
                transaction_id=transaction_id,
                occurred_at=recorded_at,
                authority_receipt_id=envelope["authority_receipt_id"],
                principal=principal,
                project_id=payload.get("project_id") or application.project_id,
                authenticated_request_digest=authenticated_request_digest,
                manifest_digest=manifest_digest,
                idempotency_key=idempotency_key,
                initiating_audit_ref=initiating_audit_ref,
                exact_subject_binding=exact_subject,
            )
            audit = self._record_controller_audit(
                controller=controller,
                event_id=event.event_id,
                subject_kind=kind,
                subject_id=subject_id,
                revision=1,
                digest=digest,
                transaction_id=transaction_id,
                recorded_at=recorded_at,
            )
            self.authority.record_receipt(
                resolved=controller,
                authority_receipt_id=envelope["authority_receipt_id"],
                workspace_id=principal.workspace_id,
                subject_id=subject_id,
                subject_revision=1,
                subject_digest=digest,
                event_id=event.event_id,
                transaction_id=transaction_id,
                audit_ref=audit.audit_ref,
                recorded_at=recorded_at,
            )
        except IntegrityError as exc:
            raise ManifestImportCompositionError("CATALOG_CONFLICT") from exc
        except (V4EventStoreError, V5AuthorityError, V4AuditIntegrityError) as exc:
            raise ManifestImportCompositionError(
                "v5.manifest.composition_failed"
            ) from exc
        except V4AuditUnavailable as exc:
            raise ManifestImportCompositionError("AUDIT_UNAVAILABLE") from exc
        root_audit = self.session.get(
            Audit, initiating_audit_ref.removeprefix("audit://")
        )
        if root_audit is None:
            raise ManifestImportCompositionError(
                "v5.manifest.initiating_audit_invalid"
            )
        return ManifestOwnedCatalogRecord(
            subject_id=subject_id,
            payload=payload,
            digest=digest,
            authority_receipt_id=envelope["authority_receipt_id"],
        )

    def register_environment(
        self,
        *,
        environment: Any,
        application_id: str,
        principal: AcceptedPrincipalContext,
        transaction_id: str,
        request_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_audit_ref: str,
        recorded_at: datetime,
    ) -> ManifestOwnedCatalogRecord:
        application = self.session.get(AIApplication, application_id)
        if (
            application is None
            or application.workspace_id != principal.workspace_id
            or application.lifecycle_state != "ACTIVE"
        ):
            raise ManifestImportCompositionError(
                "v5.manifest.environment_application_not_active"
            )
        self._validate_root(
            principal=principal,
            project_id=application.project_id,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
        )
        environment_id = new_catalog_environment_id()
        payload = {
            "environment_id": environment_id,
            "workspace_id": principal.workspace_id,
            "application_id": application_id,
            "logical_name": environment.logical_name,
            "risk_classification": environment.risk_classification,
            "lifecycle_state": "ACTIVE",
            "record_envelope": self._record_envelope(
                workspace_id=principal.workspace_id,
                revision=1,
                principal_id=principal.principal_id,
                recorded_at=recorded_at,
                receipt_id=new_authority_receipt_id(),
            ),
        }
        return self._record_owned_catalog_record(
            kind="ENVIRONMENT",
            subject_id=environment_id,
            payload=payload,
            business_payload={
                "environment_id": environment_id,
                "application_id": application_id,
                "logical_name": environment.logical_name,
                "lifecycle_state": "ACTIVE",
            },
            command="environments.register",
            event_type="environment.registered",
            aggregate_type="environment",
            principal=principal,
            transaction_id=transaction_id,
            request_id=request_id,
            initiating_audit_ref=initiating_audit_ref,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            correlation_id=application_id,
            recorded_at=recorded_at,
        )

    def record_dependency_edge(
        self,
        *,
        edge: Any,
        application_id: str,
        from_component_id: str,
        to_component_id: str,
        principal: AcceptedPrincipalContext,
        transaction_id: str,
        request_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_audit_ref: str,
        recorded_at: datetime,
    ) -> ManifestOwnedCatalogRecord:
        application = self.session.get(AIApplication, application_id)
        from_component = self.session.get(SystemComponent, from_component_id)
        to_component = self.session.get(SystemComponent, to_component_id)
        if (
            application is None
            or application.workspace_id != principal.workspace_id
            or application.lifecycle_state != "ACTIVE"
            or from_component is None
            or to_component is None
            or from_component.workspace_id != principal.workspace_id
            or to_component.workspace_id != principal.workspace_id
            or from_component.application_id != application_id
            or to_component.application_id != application_id
            or from_component.lifecycle_state != "ACTIVE"
            or to_component.lifecycle_state != "ACTIVE"
        ):
            raise ManifestImportCompositionError("v5.manifest.edge_endpoint_invalid")
        self._validate_root(
            principal=principal,
            project_id=application.project_id,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
        )
        edge_id = new_dependency_edge_id()
        edge_digest = canonical_digest(
            {
                "from_component_id": from_component_id,
                "to_component_id": to_component_id,
                "relation": edge.relation,
                "required": edge.required,
            }
        )
        payload = {
            "edge_id": edge_id,
            "workspace_id": principal.workspace_id,
            "application_id": application_id,
            "from_component_id": from_component_id,
            "to_component_id": to_component_id,
            "relation": edge.relation,
            "required": edge.required,
            "edge_digest": edge_digest,
            "record_envelope": self._record_envelope(
                workspace_id=principal.workspace_id,
                revision=1,
                principal_id=principal.principal_id,
                recorded_at=recorded_at,
                receipt_id=new_authority_receipt_id(),
            ),
        }
        return self._record_owned_catalog_record(
            kind="DEPENDENCY_EDGE",
            subject_id=edge_id,
            payload=payload,
            business_payload={
                "edge_id": edge_id,
                "application_id": application_id,
                "from_component_id": from_component_id,
                "to_component_id": to_component_id,
                "relation": edge.relation,
                "edge_digest": edge_digest,
            },
            command="dependency-edges.record",
            event_type="dependency_edge.recorded",
            aggregate_type="dependency_edge",
            principal=principal,
            transaction_id=transaction_id,
            request_id=request_id,
            initiating_audit_ref=initiating_audit_ref,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            correlation_id=application_id,
            recorded_at=recorded_at,
        )


__all__ = ["ManifestOwnedCatalogRecord", "V5ManifestCatalogCommandPort"]
