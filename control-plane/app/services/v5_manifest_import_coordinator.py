"""R2 composition root for manifest-authorized catalog activation.

The coordinator owns no domain records.  It verifies the persisted initiating
principal and root command audit, then composes catalog-owner commands in the
caller's existing PostgreSQL transaction.  It never commits.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Audit
from app.models.v4_tables import ControllerRegistration, PublicPrincipal
from app.models.v5_tables import AIApplication, DependencyEdge, Environment, SystemComponent
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.services.v4_audit import (
    V4AuditIntegrityError,
    V4AuditService,
    V4AuditUnavailable,
    validate_v4_audit_row,
)
from app.services.v5_authority import V5AuthorityError, V5AuthorityService, V5ResolvedController
from app.utils.ids import (
    new_application_id,
    new_authority_receipt_id,
    new_catalog_environment_id,
    new_dependency_edge_id,
    new_system_component_id,
)
from app.utils.v4_integrity import canonical_digest
from app.utils.v5_integrity import V5_HASH_RULE, v5_record_digest


_ROOT_INTENT = "system-manifests.import"
_IMPORT_SCOPE = "system_manifests:import"
_TRUST_ROLES = frozenset({"integrator", "catalog_admin", "trusted_builder"})
_CAPABILITY_ISSUER = object()


class ManifestImportCompositionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ManifestCatalogRecord:
    subject_id: str
    registered_payload: dict[str, Any]
    activated_payload: dict[str, Any]
    registered_digest: str
    activated_digest: str


@dataclass(frozen=True)
class ManifestOwnedCatalogRecord:
    subject_id: str
    payload: dict[str, Any]
    digest: str
    authority_receipt_id: str


class _ActivationCompositionCapability:
    __slots__ = (
        "_issuer",
        "_session",
        "_transaction",
        "_purpose",
        "_workspace_id",
        "_transaction_id",
        "_authenticated_request_digest",
        "_manifest_digest",
        "_idempotency_key",
        "_initiating_principal_id",
        "_initiating_principal_type",
        "_initiating_subject_digest",
        "_initiating_audiences",
        "_initiating_project_ids",
        "_initiating_environment_ids",
        "_initiating_scopes",
        "_initiating_claims_digest",
        "_initiating_audit_ref",
        "_controller_registration_id",
        "_controller_registration_revision",
        "_controller_registration_digest",
        "_controller_principal",
        "_controller_owner",
        "_subject_kind",
        "_subject_id",
        "_previous_binding",
        "_new_binding",
        "_event_type",
        "_recorded_at",
        "_consumed",
    )

    def __init__(
        self,
        *,
        issuer: object,
        session: Session,
        purpose: Literal["STORAGE_ACTIVATE", "EVENT_ACTIVATE", "EVENT_RECORD"],
        workspace_id: str,
        transaction_id: str,
        authenticated_request_digest: str,
        manifest_digest: str,
        idempotency_key: str,
        initiating_principal: AcceptedPrincipalContext,
        initiating_audit_ref: str,
        controller: V5ResolvedController,
        subject_kind: str,
        subject_id: str,
        previous_binding: dict[str, Any],
        new_binding: dict[str, Any],
        event_type: str,
        recorded_at: datetime,
    ) -> None:
        if issuer is not _CAPABILITY_ISSUER:
            raise ManifestImportCompositionError("v5.manifest.capability_forged")
        transaction = session.get_transaction()
        if transaction is None or not session.in_transaction():
            raise ManifestImportCompositionError("v5.manifest.transaction_required")
        self._issuer = issuer
        self._session = session
        self._transaction = transaction
        self._purpose = purpose
        self._workspace_id = workspace_id
        self._transaction_id = transaction_id
        self._authenticated_request_digest = authenticated_request_digest
        self._manifest_digest = manifest_digest
        self._idempotency_key = idempotency_key
        self._initiating_principal_id = initiating_principal.principal_id
        self._initiating_principal_type = initiating_principal.principal_type
        self._initiating_subject_digest = digest_public_subject(
            initiating_principal.subject
        )
        self._initiating_audiences = list(initiating_principal.audiences)
        self._initiating_project_ids = list(initiating_principal.project_ids)
        self._initiating_environment_ids = list(initiating_principal.environment_ids)
        self._initiating_scopes = list(initiating_principal.scopes)
        self._initiating_claims_digest = initiating_principal.claims_digest
        self._initiating_audit_ref = initiating_audit_ref
        registration = controller.registration
        self._controller_registration_id = registration.controller_registration_id
        self._controller_registration_revision = registration.revision
        self._controller_registration_digest = registration.registration_digest
        self._controller_principal = controller.controller_principal
        self._controller_owner = controller.owner
        self._subject_kind = subject_kind
        self._subject_id = subject_id
        self._previous_binding = dict(previous_binding)
        self._new_binding = dict(new_binding)
        self._event_type = event_type
        self._recorded_at = recorded_at
        self._consumed = False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _validate_root_audit(
    session: Session,
    *,
    audit_ref: str,
    workspace_id: str,
    principal_id: str,
    transaction_id: str,
    authenticated_request_digest: str,
    manifest_digest: str,
    idempotency_key: str,
) -> Audit:
    audit = (
        session.get(Audit, audit_ref.removeprefix("audit://"))
        if audit_ref.startswith("audit://aud_")
        else None
    )
    try:
        return validate_v4_audit_row(
            audit,
            workspace_id=workspace_id,
            actor_principal=principal_id,
            action=_ROOT_INTENT,
            target="",
            params={
                "authenticated_request_digest": authenticated_request_digest,
                "manifest_digest": manifest_digest,
                "idempotency_key": idempotency_key,
            },
            result="success",
            error_code=None,
            transaction_id=transaction_id,
            evidence_refs={"manifest_digest": manifest_digest},
        )
    except V4AuditIntegrityError as exc:
        raise ManifestImportCompositionError("v5.manifest.initiating_audit_invalid") from exc


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


def _consume_activation_composition_capability(
    capability: object,
    *,
    session: Session,
    purpose: Literal["STORAGE_ACTIVATE", "EVENT_ACTIVATE", "EVENT_RECORD"],
    workspace_id: str,
    transaction_id: str,
    subject_kind: str,
    subject_id: str,
    previous_binding: dict[str, Any],
    new_binding: dict[str, Any],
    event_type: str,
    manifest_activation_context: dict[str, Any] | None = None,
) -> None:
    """Consume one exact permit; used only by R1 storage/event gates."""

    if not isinstance(capability, _ActivationCompositionCapability):
        raise ManifestImportCompositionError("v5.manifest.capability_forged")
    if capability._issuer is not _CAPABILITY_ISSUER or capability._consumed:
        raise ManifestImportCompositionError("v5.manifest.capability_consumed_or_forged")
    if (
        capability._session is not session
        or not session.in_transaction()
        or session.get_transaction() is not capability._transaction
        or capability._purpose != purpose
        or capability._workspace_id != workspace_id
        or capability._transaction_id != transaction_id
        or capability._subject_kind != subject_kind
        or capability._subject_id != subject_id
        or capability._previous_binding != previous_binding
        or capability._new_binding != new_binding
        or capability._event_type != event_type
    ):
        raise ManifestImportCompositionError("v5.manifest.capability_binding_mismatch")
    if purpose == "EVENT_ACTIVATE":
        expected_context = {
            "root_intent": _ROOT_INTENT,
            "workflow_owner": "manifest_import_coordinator",
            "authenticated_request_digest": capability._authenticated_request_digest,
            "manifest_digest": capability._manifest_digest,
            "idempotency_key": capability._idempotency_key,
            "workspace_id": capability._workspace_id,
            "initiating_principal_id": capability._initiating_principal_id,
            "initiating_principal_type": capability._initiating_principal_type,
            "initiating_command_audit_ref": capability._initiating_audit_ref,
        }
        if manifest_activation_context != expected_context:
            raise ManifestImportCompositionError("v5.manifest.activation_context_mismatch")

    _validate_root_audit(
        session,
        audit_ref=capability._initiating_audit_ref,
        workspace_id=workspace_id,
        principal_id=capability._initiating_principal_id,
        transaction_id=transaction_id,
        authenticated_request_digest=capability._authenticated_request_digest,
        manifest_digest=capability._manifest_digest,
        idempotency_key=capability._idempotency_key,
    )
    principal = session.get(PublicPrincipal, capability._initiating_principal_id)
    if (
        principal is None
        or principal.workspace_id != workspace_id
        or principal.state != "ACTIVE"
        or principal.revoked_at is not None
        or principal.claims_digest != capability._initiating_claims_digest
        or principal.principal_type != capability._initiating_principal_type
        or principal.subject_digest != capability._initiating_subject_digest
        or principal.audiences != capability._initiating_audiences
        or principal.project_ids != capability._initiating_project_ids
        or principal.environment_ids != capability._initiating_environment_ids
        or principal.scopes != capability._initiating_scopes
        or _IMPORT_SCOPE not in (principal.scopes or [])
        or not set(principal.trust_roles or []) & _TRUST_ROLES
    ):
        raise ManifestImportCompositionError("v5.manifest.principal_not_authorized")
    registration = session.get(
        ControllerRegistration,
        (
            capability._controller_registration_id,
            capability._controller_registration_revision,
        ),
    )
    if (
        registration is None
        or registration.workspace_id != workspace_id
        or registration.owner != capability._controller_owner
        or registration.controller_principal != capability._controller_principal
        or registration.registration_digest != capability._controller_registration_digest
    ):
        raise ManifestImportCompositionError("v5.manifest.controller_invalid")
    try:
        V5AuthorityService(session)._validate_registration_at(
            registration, recorded_at=capability._recorded_at
        )
    except V5AuthorityError as exc:
        raise ManifestImportCompositionError("v5.manifest.controller_invalid") from exc
    capability._consumed = True


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
    ) -> _ActivationCompositionCapability:
        return _ActivationCompositionCapability(
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
        from app.services.v4_event_store import V4EventStore, V4EventStoreError

        controller = self._resolve_catalog_controller(
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
            subject_revision: int | None = 1
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
            subject_revision = 1
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
            event = self.append_record_event(
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
                project_id=(
                    payload.get("project_id")
                    or self.session.get(AIApplication, payload["application_id"]).project_id
                ),
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
                revision=subject_revision,
                digest=digest,
                transaction_id=transaction_id,
                recorded_at=recorded_at,
            )
            self.authority.record_receipt(
                resolved=controller,
                authority_receipt_id=envelope["authority_receipt_id"],
                workspace_id=principal.workspace_id,
                subject_id=subject_id,
                subject_revision=subject_revision,
                subject_digest=digest,
                event_id=event.event_id,
                transaction_id=transaction_id,
                audit_ref=audit.audit_ref,
                recorded_at=recorded_at,
            )
        except IntegrityError as exc:
            raise ManifestImportCompositionError("CATALOG_CONFLICT") from exc
        except (V4EventStoreError, V5AuthorityError, V4AuditIntegrityError) as exc:
            raise ManifestImportCompositionError("v5.manifest.composition_failed") from exc
        except V4AuditUnavailable as exc:
            raise ManifestImportCompositionError("AUDIT_UNAVAILABLE") from exc
        # The root human/service audit must remain durable and exact for every
        # owner-controller subcommand in this composition.
        root_audit = self.session.get(Audit, initiating_audit_ref.removeprefix("audit://"))
        if root_audit is None:
            raise ManifestImportCompositionError("v5.manifest.initiating_audit_invalid")
        return ManifestOwnedCatalogRecord(
            subject_id=subject_id,
            payload=payload,
            digest=digest,
            authority_receipt_id=envelope["authority_receipt_id"],
        )

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
        application = self.session.get(AIApplication, application_id)
        if (
            application is None
            or application.workspace_id != principal.workspace_id
            or application.lifecycle_state != "ACTIVE"
        ):
            raise ManifestImportCompositionError(
                "v5.manifest.environment_application_not_active"
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
        self.validate_root(
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


__all__ = [
    "ManifestCatalogRecord",
    "ManifestOwnedCatalogRecord",
    "ManifestImportCompositionError",
    "V5ManifestImportCoordinator",
]
