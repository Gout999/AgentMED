"""R2 composition primitives for manifest-authorized catalog activation.

Capability issuance/consumption primitives for the manifest-import
composition, extracted from the R2 coordinator (C3 capability/import-cycle
elimination).  The storage/event gates consume capabilities through this
module; it imports no other coordinator-level ``app.services`` module, so the
module-level import graph stays acyclic.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from sqlalchemy.orm import Session

from app.models import Audit
from app.models.v4_tables import ControllerRegistration, PublicPrincipal
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.services.v4_audit import V4AuditIntegrityError, validate_v4_audit_row
from app.services.v5_authority import V5AuthorityError, V5AuthorityService, V5ResolvedController


_ROOT_INTENT = "system-manifests.import"
_IMPORT_SCOPE = "system_manifests:import"
_TRUST_ROLES = frozenset({"integrator", "catalog_admin", "trusted_builder"})
_CAPABILITY_ISSUER = object()


class ManifestImportCompositionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ActivationCompositionCapability:
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


def consume_activation_composition_capability(
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

    if not isinstance(capability, ActivationCompositionCapability):
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
