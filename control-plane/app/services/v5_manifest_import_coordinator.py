"""R2 composition root for manifest-authorized catalog activation.

The coordinator owns no domain records.  It verifies the persisted initiating
principal and root command audit, then composes catalog-owner commands in the
caller's existing PostgreSQL transaction.  It never commits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models import Audit
from app.models.v4_tables import PublicPrincipal
from app.models.v5_tables import AIApplication
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from app.services.v5_authority import V5AuthorityError, V5AuthorityService, V5ResolvedController
from app.utils.ids import (
    new_application_id,
    new_authority_receipt_id,
    new_system_component_id,
)
from app.utils.v5_integrity import V5_HASH_RULE, v5_record_digest
from app.services.v5_catalog_composition import (
    ManifestOwnedCatalogRecord,
    V5ManifestCatalogCommandPort,
)

from app.services.v5_composition import (
    ActivationCompositionCapability,
    ManifestImportCompositionError,
    consume_activation_composition_capability,
    _CAPABILITY_ISSUER,
    _IMPORT_SCOPE,
    _ROOT_INTENT,
    _TRUST_ROLES,
    _validate_root_audit,
)

# Legacy private aliases: the capability primitives now live in
# app.services.v5_composition; these names keep resolving for any external
# consumer of this coordinator module.
_ActivationCompositionCapability = ActivationCompositionCapability
_consume_activation_composition_capability = consume_activation_composition_capability


@dataclass(frozen=True)
class ManifestCatalogRecord:
    subject_id: str
    registered_payload: dict[str, Any]
    activated_payload: dict[str, Any]
    registered_digest: str
    activated_digest: str


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _validate_persisted_principal(
    session: Session,
    *,
    principal: AcceptedPrincipalContext,
    project_id: str,
) -> PublicPrincipal:
    row = session.get(PublicPrincipal, principal.principal_id)
    if (
        row is None
        or row.workspace_id != principal.workspace_id
        or row.state != "ACTIVE"
        or row.revoked_at is not None
        or row.principal_type != principal.principal_type
        or row.principal_type not in {"human", "service"}
        or row.subject_digest != digest_public_subject(principal.subject)
        or row.claims_digest != principal.claims_digest
        or row.audiences != principal.audiences
        or row.project_ids != principal.project_ids
        or row.environment_ids != principal.environment_ids
        or row.scopes != principal.scopes
        or _IMPORT_SCOPE not in row.scopes
        or principal.requested_context.required_scope != _IMPORT_SCOPE
        or principal.requested_context.workspace_id != row.workspace_id
        or principal.requested_context.project_id not in {None, project_id}
        or project_id not in row.project_ids
        or not set(row.trust_roles or []) & _TRUST_ROLES
    ):
        raise ManifestImportCompositionError("v5.manifest.principal_not_authorized")
    return row


class V5ManifestImportCoordinator:
    def __init__(
        self,
        session: Session,
        *,
        audit_service: V4AuditService | None = None,
        authority_service: V5AuthorityService | None = None,
    ) -> None:
        self.session = session
        self.audit = audit_service or V4AuditService(session)
        self.authority = authority_service or V5AuthorityService(session)
        self.catalog_commands = V5ManifestCatalogCommandPort(
            session,
            authority=self.authority,
            validate_root=self.validate_root,
            resolve_controller=self._resolve_catalog_controller,
            append_event=self.append_record_event,
            record_controller_audit=self._record_controller_audit,
            record_envelope=self._record_envelope,
            exact_binding=self._exact_binding,
        )

    def validate_current_authorization(
        self,
        *,
        principal: AcceptedPrincipalContext,
        project_id: str,
    ) -> PublicPrincipal:
        """Recheck server-owned import authority before any idempotency replay."""

        return _validate_persisted_principal(
            self.session, principal=principal, project_id=project_id
        )

    def validate_root(
        self,
        *,
        principal: AcceptedPrincipalContext,
        project_id: str,
        transaction_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_audit_ref: str,
    ) -> None:
        _validate_persisted_principal(
            self.session, principal=principal, project_id=project_id
        )
        self.session.flush()
        _validate_root_audit(
            self.session,
            audit_ref=initiating_audit_ref,
            workspace_id=principal.workspace_id,
            principal_id=principal.principal_id,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
        )

    def validate_persisted_root_audit(
        self,
        *,
        audit_ref: str,
        workspace_id: str,
        principal_id: str,
        manifest_digest: str,
        authenticated_request_digest: str,
        idempotency_key: str,
    ) -> Audit:
        audit = (
            self.session.get(Audit, audit_ref.removeprefix("audit://"))
            if isinstance(audit_ref, str) and audit_ref.startswith("audit://aud_")
            else None
        )
        if (
            audit is None
            or authenticated_request_digest != manifest_digest
            or not isinstance(idempotency_key, str)
            or not idempotency_key
        ):
            raise ManifestImportCompositionError(
                "v5.manifest.initiating_audit_invalid"
            )
        return _validate_root_audit(
            self.session,
            audit_ref=audit_ref,
            workspace_id=workspace_id,
            principal_id=principal_id,
            transaction_id=audit.transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _record_envelope(
        *,
        workspace_id: str,
        revision: int,
        principal_id: str,
        recorded_at: datetime,
        receipt_id: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "workspace_id": workspace_id,
            "revision": revision,
            "recorded_by_principal": principal_id,
            "recorded_at": _wire_time(recorded_at),
            "immutable": True,
            "hash_rule": V5_HASH_RULE,
            "record_digest": "",
            "authority_receipt_id": receipt_id,
        }

    @staticmethod
    def _exact_binding(
        *, kind: str, subject_id: str, revision: int, digest: str
    ) -> dict[str, Any]:
        return {"kind": kind, "id": subject_id, "revision": revision, "digest": digest}

    def _issue(
        self,
        *,
        purpose: Literal["STORAGE_ACTIVATE", "EVENT_ACTIVATE", "EVENT_RECORD"],
        controller: V5ResolvedController,
        principal: AcceptedPrincipalContext,
        transaction_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_audit_ref: str,
        subject_kind: str,
        subject_id: str,
        previous_binding: dict[str, Any],
        new_binding: dict[str, Any],
        event_type: str,
        recorded_at: datetime,
    ) -> ActivationCompositionCapability:
        return ActivationCompositionCapability(
            issuer=_CAPABILITY_ISSUER,
            session=self.session,
            purpose=purpose,
            workspace_id=principal.workspace_id,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_principal=principal,
            initiating_audit_ref=initiating_audit_ref,
            controller=controller,
            subject_kind=subject_kind,
            subject_id=subject_id,
            previous_binding=previous_binding,
            new_binding=new_binding,
            event_type=event_type,
            recorded_at=recorded_at,
        )

    def _record_controller_audit(
        self,
        *,
        controller: V5ResolvedController,
        event_id: str,
        subject_kind: str,
        subject_id: str,
        revision: int | None,
        digest: str,
        transaction_id: str,
        recorded_at: datetime,
    ) -> Audit:
        return self.audit.record(
            workspace_id=controller.registration.workspace_id,
            actor_principal=controller.controller_principal,
            action=f"controller.{controller.event_type}",
            target=subject_id,
            params={"command": controller.command},
            transaction_id=transaction_id,
            evidence_refs={
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "subject_revision": revision,
                "subject_digest": digest,
                "event_id": event_id,
            },
            occurred_at=recorded_at,
        )

    def _resolve_catalog_controller(
        self,
        *,
        workspace_id: str,
        subject_kind: str,
        command: str,
        event_type: str,
        recorded_at: datetime,
    ) -> V5ResolvedController:
        try:
            return self.authority.resolve_controller(
                workspace_id=workspace_id,
                subject_kind=subject_kind,
                command=command,
                event_type=event_type,
                recorded_at=recorded_at,
            )
        except V5AuthorityError as exc:
            raise ManifestImportCompositionError(
                "v5.manifest.controller_invalid"
            ) from exc

    def append_record_event(
        self,
        *,
        controller: V5ResolvedController,
        workspace_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        causation_id: str,
        correlation_id: str,
        transaction_id: str,
        occurred_at: datetime,
        authority_receipt_id: str,
        principal: AcceptedPrincipalContext,
        project_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_audit_ref: str,
        exact_subject_binding: dict[str, Any],
    ) -> Any:
        from app.services.v4_event_store import V4EventStore

        self.validate_root(
            principal=principal,
            project_id=project_id,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
        )
        permit = self._issue(
            purpose="EVENT_RECORD",
            controller=controller,
            principal=principal,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
            subject_kind=controller.subject_kind,
            subject_id=aggregate_id,
            previous_binding={},
            new_binding=exact_subject_binding,
            event_type=event_type,
            recorded_at=occurred_at,
        )
        return V4EventStore(self.session).append_composed_manifest_record_event(
            workspace_id=workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
            actor_principal=controller.controller_principal,
            transaction_id=transaction_id,
            occurred_at=occurred_at,
            authority_receipt_id=authority_receipt_id,
            composition_capability=permit,
        )

    def _compose_lifecycle_revision(self, **kwargs: Any) -> ManifestCatalogRecord:
        from app.services.v4_event_store import V4EventStoreError
        from app.services.v5_lifecycle_authority import V5LifecycleAuthorityError

        try:
            return self._record_lifecycle_revision(**kwargs)
        except ManifestImportCompositionError:
            raise
        except V4AuditUnavailable as exc:
            raise ManifestImportCompositionError("AUDIT_UNAVAILABLE") from exc
        except (V4EventStoreError, V5AuthorityError, V5LifecycleAuthorityError) as exc:
            raise ManifestImportCompositionError(
                "v5.manifest.composition_failed"
            ) from exc

    def _record_lifecycle_revision(
        self,
        *,
        kind: Literal["AI_APPLICATION", "SYSTEM_COMPONENT"],
        aggregate_type: Literal["ai_application", "system_component"],
        subject_id: str,
        registered_payload: dict[str, Any],
        activated_payload: dict[str, Any],
        register_command: str,
        register_event: str,
        activate_command: str,
        activate_event: str,
        registered_event_payload: dict[str, Any],
        principal: AcceptedPrincipalContext,
        transaction_id: str,
        request_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_audit_ref: str,
        recorded_at: datetime,
        correlation_id: str,
    ) -> ManifestCatalogRecord:
        # Local imports prevent the R1 gates from depending on a public issuer.
        from app.services.v4_event_store import V4EventStore
        from app.services.v5_lifecycle_authority import V5LifecycleAuthorityService

        lifecycle = V5LifecycleAuthorityService(self.session)
        registered = lifecycle.append_registration_revision(
            kind=kind, envelope_payload=registered_payload
        )
        registered_digest = registered.history.record_digest
        registered_binding = self._exact_binding(
            kind=kind, subject_id=subject_id, revision=1, digest=registered_digest
        )
        register_controller = self._resolve_catalog_controller(
            workspace_id=principal.workspace_id,
            subject_kind=kind,
            command=register_command,
            event_type=register_event,
            recorded_at=recorded_at,
        )
        event_store = V4EventStore(self.session)
        register_event_row = event_store.append_event(
            workspace_id=principal.workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=subject_id,
            event_type=register_event,
            payload=registered_event_payload,
            causation_id=request_id,
            correlation_id=correlation_id,
            actor_principal=register_controller.controller_principal,
            transaction_id=transaction_id,
            occurred_at=recorded_at,
            authority_receipt_id=registered_payload["record_envelope"][
                "authority_receipt_id"
            ],
        )
        register_audit = self._record_controller_audit(
            controller=register_controller,
            event_id=register_event_row.event_id,
            subject_kind=kind,
            subject_id=subject_id,
            revision=1,
            digest=registered_digest,
            transaction_id=transaction_id,
            recorded_at=recorded_at,
        )
        self.authority.record_receipt(
            resolved=register_controller,
            authority_receipt_id=registered_payload["record_envelope"][
                "authority_receipt_id"
            ],
            workspace_id=principal.workspace_id,
            subject_id=subject_id,
            subject_revision=1,
            subject_digest=registered_digest,
            event_id=register_event_row.event_id,
            transaction_id=transaction_id,
            audit_ref=register_audit.audit_ref,
            recorded_at=recorded_at,
            lifecycle_history=True,
        )

        activated_digest = v5_record_digest(activated_payload)
        activated_payload["record_envelope"]["record_digest"] = activated_digest
        activated_binding = self._exact_binding(
            kind=kind, subject_id=subject_id, revision=2, digest=activated_digest
        )
        activate_controller = self._resolve_catalog_controller(
            workspace_id=principal.workspace_id,
            subject_kind=kind,
            command=activate_command,
            event_type=activate_event,
            recorded_at=recorded_at,
        )
        storage_permit = self._issue(
            purpose="STORAGE_ACTIVATE",
            controller=activate_controller,
            principal=principal,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
            subject_kind=kind,
            subject_id=subject_id,
            previous_binding=registered_binding,
            new_binding=activated_binding,
            event_type=activate_event,
            recorded_at=recorded_at,
        )
        lifecycle.append_composed_activation_revision(
            kind=kind,
            envelope_payload=activated_payload,
            transaction_id=transaction_id,
            composition_capability=storage_permit,
        )
        manifest_context = {
            "root_intent": _ROOT_INTENT,
            "workflow_owner": "manifest_import_coordinator",
            "authenticated_request_digest": authenticated_request_digest,
            "manifest_digest": manifest_digest,
            "idempotency_key": idempotency_key,
            "workspace_id": principal.workspace_id,
            "initiating_principal_id": principal.principal_id,
            "initiating_principal_type": principal.principal_type,
            "initiating_command_audit_ref": initiating_audit_ref,
        }
        activation_event_payload = {
            (
                "exact_previous_application_binding"
                if kind == "AI_APPLICATION"
                else "exact_previous_system_component_binding"
            ): registered_binding,
            (
                "exact_application_binding"
                if kind == "AI_APPLICATION"
                else "exact_system_component_binding"
            ): activated_binding,
            "lifecycle_state": "ACTIVE",
            "manifest_activation_context": manifest_context,
            "initiating_command_audit_ref": initiating_audit_ref,
        }
        event_permit = self._issue(
            purpose="EVENT_ACTIVATE",
            controller=activate_controller,
            principal=principal,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
            subject_kind=kind,
            subject_id=subject_id,
            previous_binding=registered_binding,
            new_binding=activated_binding,
            event_type=activate_event,
            recorded_at=recorded_at,
        )
        activation_event = event_store.append_composed_activation_event(
            workspace_id=principal.workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=subject_id,
            event_type=activate_event,
            payload=activation_event_payload,
            causation_id=request_id,
            correlation_id=correlation_id,
            actor_principal=activate_controller.controller_principal,
            transaction_id=transaction_id,
            occurred_at=recorded_at,
            authority_receipt_id=activated_payload["record_envelope"][
                "authority_receipt_id"
            ],
            composition_capability=event_permit,
        )
        activation_audit = self._record_controller_audit(
            controller=activate_controller,
            event_id=activation_event.event_id,
            subject_kind=kind,
            subject_id=subject_id,
            revision=2,
            digest=activated_digest,
            transaction_id=transaction_id,
            recorded_at=recorded_at,
        )
        self.authority.record_receipt(
            resolved=activate_controller,
            authority_receipt_id=activated_payload["record_envelope"][
                "authority_receipt_id"
            ],
            workspace_id=principal.workspace_id,
            subject_id=subject_id,
            subject_revision=2,
            subject_digest=activated_digest,
            event_id=activation_event.event_id,
            transaction_id=transaction_id,
            audit_ref=activation_audit.audit_ref,
            recorded_at=recorded_at,
            lifecycle_history=True,
        )
        return ManifestCatalogRecord(
            subject_id=subject_id,
            registered_payload=registered_payload,
            activated_payload=activated_payload,
            registered_digest=registered_digest,
            activated_digest=activated_digest,
        )

    def register_and_activate_application(
        self,
        *,
        application: Any,
        principal: AcceptedPrincipalContext,
        transaction_id: str,
        request_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_audit_ref: str,
        recorded_at: datetime,
    ) -> ManifestCatalogRecord:
        self.validate_root(
            principal=principal,
            project_id=application.project_id,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
        )
        register_controller = self._resolve_catalog_controller(
            workspace_id=principal.workspace_id,
            subject_kind="AI_APPLICATION",
            command="applications.register",
            event_type="application.registered",
            recorded_at=recorded_at,
        )
        activate_controller = self._resolve_catalog_controller(
            workspace_id=principal.workspace_id,
            subject_kind="AI_APPLICATION",
            command="applications.activate",
            event_type="application.activated",
            recorded_at=recorded_at,
        )
        application_id = new_application_id()
        common = {
            "application_id": application_id,
            "workspace_id": principal.workspace_id,
            "project_id": application.project_id,
            "slug": application.slug,
            "display_name": application.display_name,
            "owner_principal_ids": list(application.owner_principal_ids),
            "criticality": application.criticality,
            "data_classification": application.data_classification,
            "governance_mode": application.governance_mode,
        }
        registered_payload = {
            **common,
            "lifecycle_state": "REGISTERED",
            "exact_previous_application_binding_or_null": None,
            "record_envelope": self._record_envelope(
                workspace_id=principal.workspace_id,
                revision=1,
                principal_id=register_controller.controller_principal,
                recorded_at=recorded_at,
                receipt_id=new_authority_receipt_id(),
            ),
        }
        registered_digest = v5_record_digest(registered_payload)
        registered_payload["record_envelope"]["record_digest"] = registered_digest
        previous = self._exact_binding(
            kind="AI_APPLICATION",
            subject_id=application_id,
            revision=1,
            digest=registered_digest,
        )
        activated_payload = {
            **common,
            "lifecycle_state": "ACTIVE",
            "exact_previous_application_binding": previous,
            "record_envelope": self._record_envelope(
                workspace_id=principal.workspace_id,
                revision=2,
                principal_id=activate_controller.controller_principal,
                recorded_at=recorded_at,
                receipt_id=new_authority_receipt_id(),
            ),
        }
        return self._compose_lifecycle_revision(
            kind="AI_APPLICATION",
            aggregate_type="ai_application",
            subject_id=application_id,
            registered_payload=registered_payload,
            activated_payload=activated_payload,
            register_command="applications.register",
            register_event="application.registered",
            activate_command="applications.activate",
            activate_event="application.activated",
            registered_event_payload={
                "exact_previous_application_binding_or_null": None,
                "exact_application_binding": previous,
                "project_id": application.project_id,
                "slug": application.slug,
                "lifecycle_state": "REGISTERED",
            },
            principal=principal,
            transaction_id=transaction_id,
            request_id=request_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
            recorded_at=recorded_at,
            correlation_id=application_id,
        )

    def register_and_activate_component(
        self,
        *,
        component: Any,
        application_id: str,
        principal: AcceptedPrincipalContext,
        transaction_id: str,
        request_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_audit_ref: str,
        recorded_at: datetime,
    ) -> ManifestCatalogRecord:
        application = self.session.get(AIApplication, application_id)
        if (
            application is None
            or application.workspace_id != principal.workspace_id
            or application.lifecycle_state != "ACTIVE"
        ):
            raise ManifestImportCompositionError(
                "v5.manifest.component_application_not_active"
            )
        self.validate_root(
            principal=principal,
            project_id=application.project_id,
            transaction_id=transaction_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
        )
        register_controller = self._resolve_catalog_controller(
            workspace_id=principal.workspace_id,
            subject_kind="SYSTEM_COMPONENT",
            command="system-components.register",
            event_type="system_component.registered",
            recorded_at=recorded_at,
        )
        activate_controller = self._resolve_catalog_controller(
            workspace_id=principal.workspace_id,
            subject_kind="SYSTEM_COMPONENT",
            command="system-components.activate",
            event_type="system_component.activated",
            recorded_at=recorded_at,
        )
        component_id = new_system_component_id()
        common = {
            "component_id": component_id,
            "workspace_id": principal.workspace_id,
            "application_id": application_id,
            "component_kind": component.component_kind,
            "logical_name": component.logical_name,
            "owner_principal_ids": list(component.owner_principal_ids),
            "criticality": component.criticality,
            "data_classification": component.data_classification,
            "permission_classification": component.permission_classification,
            "effect_classification": component.effect_classification,
            "dataset_role": component.dataset_role,
        }
        registered_payload = {
            **common,
            "lifecycle_state": "REGISTERED",
            "exact_previous_system_component_binding_or_null": None,
            "record_envelope": self._record_envelope(
                workspace_id=principal.workspace_id,
                revision=1,
                principal_id=register_controller.controller_principal,
                recorded_at=recorded_at,
                receipt_id=new_authority_receipt_id(),
            ),
        }
        registered_digest = v5_record_digest(registered_payload)
        registered_payload["record_envelope"]["record_digest"] = registered_digest
        previous = self._exact_binding(
            kind="SYSTEM_COMPONENT",
            subject_id=component_id,
            revision=1,
            digest=registered_digest,
        )
        activated_payload = {
            **common,
            "lifecycle_state": "ACTIVE",
            "exact_previous_system_component_binding": previous,
            "record_envelope": self._record_envelope(
                workspace_id=principal.workspace_id,
                revision=2,
                principal_id=activate_controller.controller_principal,
                recorded_at=recorded_at,
                receipt_id=new_authority_receipt_id(),
            ),
        }
        return self._compose_lifecycle_revision(
            kind="SYSTEM_COMPONENT",
            aggregate_type="system_component",
            subject_id=component_id,
            registered_payload=registered_payload,
            activated_payload=activated_payload,
            register_command="system-components.register",
            register_event="system_component.registered",
            activate_command="system-components.activate",
            activate_event="system_component.activated",
            registered_event_payload={
                "exact_previous_system_component_binding_or_null": None,
                "exact_system_component_binding": previous,
                "application_id": application_id,
                "component_kind": component.component_kind,
                "logical_name": component.logical_name,
                "lifecycle_state": "REGISTERED",
            },
            principal=principal,
            transaction_id=transaction_id,
            request_id=request_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
            recorded_at=recorded_at,
            correlation_id=application_id,
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
        return self.catalog_commands.register_environment(
            environment=environment,
            application_id=application_id,
            principal=principal,
            transaction_id=transaction_id,
            request_id=request_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
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
        return self.catalog_commands.record_dependency_edge(
            edge=edge,
            application_id=application_id,
            from_component_id=from_component_id,
            to_component_id=to_component_id,
            principal=principal,
            transaction_id=transaction_id,
            request_id=request_id,
            authenticated_request_digest=authenticated_request_digest,
            manifest_digest=manifest_digest,
            idempotency_key=idempotency_key,
            initiating_audit_ref=initiating_audit_ref,
            recorded_at=recorded_at,
        )


__all__ = [
    "ManifestCatalogRecord",
    "ManifestOwnedCatalogRecord",
    "ManifestImportCompositionError",
    "V5ManifestImportCoordinator",
]
