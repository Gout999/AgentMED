"""V5-1A AI application catalog transactions.

Replicates the Stage-1A intake orchestration for the four catalog resources:
JCS record digest (nested schema-major-2 ``record_envelope``), preallocated
authority receipt, event + outbox in the same transaction, controller audit,
public-command idempotency with a PG advisory lock, and a command audit that
must fail the whole business transaction if it cannot be recorded.  The service
only flushes; the caller owns commit/rollback.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, NoReturn, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tables import Audit
from app.models.v4_tables import PublicPrincipal
from app.models.v5_tables import (
    AIApplication,
    DependencyEdge,
    Environment,
    SystemComponent,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.public_api.v5_models import (
    ApplicationGetResponse,
    ApplicationRecord,
    ApplicationRegisterRequest,
    ApplicationRegisterResponse,
    ComponentGetResponse,
    ComponentRecord,
    ComponentRegisterRequest,
    ComponentRegisterResponse,
    DependencyEdgeGetResponse,
    DependencyEdgeRecord,
    DependencyEdgeRecordRequest,
    DependencyEdgeRecordResponse,
    EnvironmentGetResponse,
    EnvironmentRecord,
    EnvironmentRegisterRequest,
    EnvironmentRegisterResponse,
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
from app.services.v5_authority import (
    V5AuthorityError,
    V5AuthorityService,
    V5ResolvedController,
)
from app.services.v5_lifecycle_authority import (
    V5LifecycleAuthorityError,
    V5LifecycleAuthorityService,
)
from app.utils.ids import (
    new_application_id,
    new_authority_receipt_id,
    new_catalog_environment_id,
    new_dependency_edge_id,
    new_idempotency_receipt_id,
    new_request_id,
    new_system_component_id,
    new_transaction_id,
)
from app.utils.v4_integrity import V4IntegrityError, canonical_digest, record_digest
from app.utils.v5_integrity import V5_HASH_RULE, assert_v5_record_digest, v5_record_digest

Clock = Callable[[], datetime]
PRINCIPAL_RE = "prn_[0-9A-Za-z]{8,64}"

_INTENT_SCOPE = "applications:manage"
_READ_SCOPE = "applications:read"
_REGISTER_PRINCIPAL_TYPES = frozenset({"human", "service"})
_CATALOG_TRUST_ROLES = frozenset({"integrator", "catalog_admin"})
_READ_PRINCIPAL_TYPES = frozenset({"human", "external_agent", "service", "connector"})


class ApplicationCatalogError(RuntimeError):
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


class V5ReadDenial(ApplicationCatalogError):
    """Audited read-only denial that the HTTP boundary may commit by itself."""

    def __init__(
        self,
        code: str,
        *,
        audit_ref: str,
        workspace_id: str,
        details: dict[str, object] | None = None,
    ) -> None:
        if code not in {
            "RESOURCE_NOT_FOUND",
            "SCOPE_FORBIDDEN",
            "VALIDATION_FAILED",
        }:
            raise ValueError("v5 read denials support only non-mutating denial codes")
        super().__init__(
            code,
            details=details,
            audit_ref=audit_ref,
            workspace_id=workspace_id,
        )
        self.rollback_required = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class _CatalogSpec:
    intent: str
    event_type: str
    aggregate_type: str
    subject_kind: str
    subject_revisioned: bool
    resource_kind: str
    resource_field: str
    resource_id_field: str
    response_model: type[BaseModel]
    get_response_model: type[BaseModel]
    business_fields: tuple[str, ...]


_SPECS: dict[str, _CatalogSpec] = {
    "application": _CatalogSpec(
        intent="applications.register",
        event_type="application.registered",
        aggregate_type="ai_application",
        subject_kind="AI_APPLICATION",
        subject_revisioned=True,
        resource_kind="ai_application",
        resource_field="application",
        resource_id_field="application_id",
        response_model=ApplicationRegisterResponse,
        get_response_model=ApplicationGetResponse,
        business_fields=("application_id", "project_id", "slug", "lifecycle_state"),
    ),
    "environment": _CatalogSpec(
        intent="environments.register",
        event_type="environment.registered",
        aggregate_type="environment",
        subject_kind="ENVIRONMENT",
        subject_revisioned=True,
        resource_kind="environment",
        resource_field="environment",
        resource_id_field="environment_id",
        response_model=EnvironmentRegisterResponse,
        get_response_model=EnvironmentGetResponse,
        business_fields=(
            "environment_id",
            "application_id",
            "logical_name",
            "lifecycle_state",
        ),
    ),
    "component": _CatalogSpec(
        intent="system-components.register",
        event_type="system_component.registered",
        aggregate_type="system_component",
        subject_kind="SYSTEM_COMPONENT",
        subject_revisioned=True,
        resource_kind="system_component",
        resource_field="component",
        resource_id_field="component_id",
        response_model=ComponentRegisterResponse,
        get_response_model=ComponentGetResponse,
        business_fields=(
            "component_id",
            "application_id",
            "component_kind",
            "logical_name",
            "lifecycle_state",
        ),
    ),
    "edge": _CatalogSpec(
        intent="dependency-edges.record",
        event_type="dependency_edge.recorded",
        aggregate_type="dependency_edge",
        subject_kind="DEPENDENCY_EDGE",
        subject_revisioned=True,
        resource_kind="dependency_edge",
        resource_field="edge",
        resource_id_field="edge_id",
        response_model=DependencyEdgeRecordResponse,
        get_response_model=DependencyEdgeGetResponse,
        business_fields=(
            "edge_id",
            "application_id",
            "from_component_id",
            "to_component_id",
            "relation",
            "edge_digest",
        ),
    ),
}


class ApplicationCatalogService:
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

    # ------------------------------------------------------------------ utils

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
            raise ApplicationCatalogError("TOKEN_INVALID")

    def _require_catalog_trust_role(
        self, principal: AcceptedPrincipalContext
    ) -> None:
        row = self.session.get(PublicPrincipal, principal.principal_id)
        if row is None or not set(row.trust_roles or []) & _CATALOG_TRUST_ROLES:
            raise ApplicationCatalogError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )

    def _require_mutation_principal(
        self, principal: AcceptedPrincipalContext
    ) -> None:
        if principal.principal_type not in _REGISTER_PRINCIPAL_TYPES:
            raise ApplicationCatalogError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )

    def _require_read_principal(self, principal: AcceptedPrincipalContext) -> None:
        if principal.principal_type not in _READ_PRINCIPAL_TYPES:
            raise ApplicationCatalogError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )

    def _deny_not_found(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        action: str,
        target: str,
    ) -> NoReturn:
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
            details={},
        )

    def _require_scope(
        self,
        *,
        principal: AcceptedPrincipalContext,
        required_scope: str,
        request_id: str,
        action: str,
        target: str,
    ) -> None:
        if (
            required_scope in principal.scopes
            and principal.requested_context.workspace_id == principal.workspace_id
            and principal.requested_context.required_scope == required_scope
        ):
            return
        audit = self._record_read_audit(
            principal=principal,
            action=action,
            target=target,
            params={"request_id": request_id, "required_scope": required_scope},
            result="denied",
            error_code="SCOPE_FORBIDDEN",
        )
        raise V5ReadDenial(
            "SCOPE_FORBIDDEN",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

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
            raise ApplicationCatalogError(
                "AUDIT_UNAVAILABLE",
                workspace_id=principal.workspace_id,
                rollback_required=True,
            ) from exc

    def _application_accessible(
        self, principal: AcceptedPrincipalContext, application: AIApplication
    ) -> bool:
        return (
            application.workspace_id == principal.workspace_id
            and application.project_id in principal.project_ids
        )

    def _load_application_for_mutation(
        self,
        *,
        principal: AcceptedPrincipalContext,
        application_id: str,
        request_id: str,
        spec: _CatalogSpec,
    ) -> AIApplication:
        application = self.session.get(AIApplication, application_id)
        if application is None or application.workspace_id != principal.workspace_id:
            self._deny_mutation_reference(
                principal=principal,
                request_id=request_id,
                spec=spec,
                application_id=application_id,
            )
        assert application is not None
        if not self._application_accessible(principal, application):
            self._deny_mutation_reference(principal, request_id, spec, application_id)
        return application

    def _deny_mutation_reference(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        spec: _CatalogSpec,
        application_id: str,
    ) -> NoReturn:
        audit = self._record_read_audit(
            principal=principal,
            action=spec.intent,
            target=f"ai_application:{application_id}",
            params={"request_id": request_id, "resource_requested": True},
            result="denied",
            error_code="RESOURCE_NOT_FOUND",
        )
        raise ApplicationCatalogError(
            "RESOURCE_NOT_FOUND",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

    def _load_application_for_read(
        self,
        *,
        principal: AcceptedPrincipalContext,
        application_id: str,
        request_id: str,
        action: str,
    ) -> AIApplication:
        application = self.session.get(AIApplication, application_id)
        if application is None or application.workspace_id != principal.workspace_id:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"ai_application:{application_id}",
            )
        assert application is not None
        return application

    # ---------------------------------------------------------- register paths

    def register_application(
        self,
        request: ApplicationRegisterRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> ApplicationRegisterResponse:
        spec = _SPECS["application"]
        request_id = request_id or new_request_id()
        body = request.model_dump(mode="json")
        request_fingerprint = self.idempotency.fingerprint(body)
        self._validate_principal_row(principal)
        self._require_mutation_principal(principal)
        self._require_catalog_trust_role(principal)
        if (
            principal.requested_context.required_scope != _INTENT_SCOPE
            or _INTENT_SCOPE not in principal.scopes
        ):
            raise ApplicationCatalogError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )
        if request.project_id not in principal.project_ids:
            raise ApplicationCatalogError(
                "WORKSPACE_ACCESS_DENIED", workspace_id=principal.workspace_id
            )
        self._validate_owner_principals(
            principal.workspace_id, request.owner_principal_ids
        )
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=spec.intent,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                verify_terminal=PublicIdempotencyService.verify_terminal_presence,
            )
        except PublicIdempotencyError as exc:
            raise ApplicationCatalogError(exc.code) from exc
        if lookup.record is not None:
            try:
                response = self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=spec.response_model,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind=spec.resource_kind,
                    resource_field=spec.resource_field,
                    resource_id_field=spec.resource_id_field,
                )
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise ApplicationCatalogError(exc.code) from exc

        existing = self.session.scalar(
            select(AIApplication).where(
                AIApplication.workspace_id == principal.workspace_id,
                AIApplication.project_id == request.project_id,
                AIApplication.slug == request.slug,
            )
        )
        if existing is not None:
            raise ApplicationCatalogError(
                "CATALOG_CONFLICT",
                details={"reason": "SLUG_ALREADY_REGISTERED"},
                workspace_id=principal.workspace_id,
            )

        application_id = new_application_id()
        now = _as_utc(self.clock())
        payload = self._build_application_envelope(
            request=request,
            application_id=application_id,
            workspace_id=principal.workspace_id,
            principal=principal,
            authority_receipt_id=None,
            recorded_at=now,
        )
        return self._write_catalog_record(
            spec=spec,
            subject_id=application_id,
            subject_revision=1,
            envelope_payload=payload,
            business_payload={
                "application_id": application_id,
                "project_id": request.project_id,
                "slug": request.slug,
                "lifecycle_state": "REGISTERED",
            },
            correlation_id=application_id,
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            resource_id=application_id,
            recorded_at=now,
        )

    def register_environment(
        self,
        request: EnvironmentRegisterRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> EnvironmentRegisterResponse:
        spec = _SPECS["environment"]
        request_id = request_id or new_request_id()
        body = request.model_dump(mode="json")
        request_fingerprint = self.idempotency.fingerprint(body)
        self._validate_principal_row(principal)
        self._require_mutation_principal(principal)
        self._require_catalog_trust_role(principal)
        if (
            principal.requested_context.required_scope != _INTENT_SCOPE
            or _INTENT_SCOPE not in principal.scopes
        ):
            raise ApplicationCatalogError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )
        application = self._load_application_for_mutation(
            principal=principal,
            application_id=request.application_id,
            request_id=request_id,
            spec=spec,
        )
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=spec.intent,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                verify_terminal=PublicIdempotencyService.verify_terminal_presence,
            )
        except PublicIdempotencyError as exc:
            raise ApplicationCatalogError(exc.code) from exc
        if lookup.record is not None:
            try:
                response = self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=spec.response_model,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind=spec.resource_kind,
                    resource_field=spec.resource_field,
                    resource_id_field=spec.resource_id_field,
                )
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise ApplicationCatalogError(exc.code) from exc

        existing = self.session.scalar(
            select(Environment).where(
                Environment.workspace_id == principal.workspace_id,
                Environment.application_id == request.application_id,
                Environment.logical_name == request.logical_name,
            )
        )
        if existing is not None:
            raise ApplicationCatalogError(
                "CATALOG_CONFLICT",
                details={"reason": "ENVIRONMENT_NAME_ALREADY_REGISTERED"},
                workspace_id=principal.workspace_id,
            )

        environment_id = new_catalog_environment_id()
        now = _as_utc(self.clock())
        authority_receipt_id = new_authority_receipt_id()
        payload: dict[str, Any] = {
            "environment_id": environment_id,
            "workspace_id": principal.workspace_id,
            "application_id": request.application_id,
            "logical_name": request.logical_name,
            "risk_classification": request.risk_classification,
            "lifecycle_state": "ACTIVE",
            "record_envelope": self._envelope(
                workspace_id=principal.workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=authority_receipt_id,
            ),
        }
        return self._write_catalog_record(
            spec=spec,
            subject_id=environment_id,
            subject_revision=1,
            envelope_payload=payload,
            business_payload={
                "environment_id": environment_id,
                "application_id": request.application_id,
                "logical_name": request.logical_name,
                "lifecycle_state": "ACTIVE",
            },
            correlation_id=request.application_id,
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            resource_id=environment_id,
            recorded_at=now,
        )

    def register_component(
        self,
        request: ComponentRegisterRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> ComponentRegisterResponse:
        spec = _SPECS["component"]
        request_id = request_id or new_request_id()
        body = request.model_dump(mode="json")
        request_fingerprint = self.idempotency.fingerprint(body)
        self._validate_principal_row(principal)
        self._require_mutation_principal(principal)
        self._require_catalog_trust_role(principal)
        if (
            principal.requested_context.required_scope != _INTENT_SCOPE
            or _INTENT_SCOPE not in principal.scopes
        ):
            raise ApplicationCatalogError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )
        application = self._load_application_for_mutation(
            principal=principal,
            application_id=request.application_id,
            request_id=request_id,
            spec=spec,
        )
        if application.lifecycle_state != "ACTIVE":
            raise ApplicationCatalogError(
                "CATALOG_CONFLICT",
                details={"reason": "APPLICATION_NOT_ACTIVE"},
                workspace_id=principal.workspace_id,
            )
        self._validate_owner_principals(
            principal.workspace_id, request.owner_principal_ids
        )
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=spec.intent,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                verify_terminal=PublicIdempotencyService.verify_terminal_presence,
            )
        except PublicIdempotencyError as exc:
            raise ApplicationCatalogError(exc.code) from exc
        if lookup.record is not None:
            try:
                response = self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=spec.response_model,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind=spec.resource_kind,
                    resource_field=spec.resource_field,
                    resource_id_field=spec.resource_id_field,
                )
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise ApplicationCatalogError(exc.code) from exc

        existing = self.session.scalar(
            select(SystemComponent).where(
                SystemComponent.workspace_id == principal.workspace_id,
                SystemComponent.application_id == request.application_id,
                SystemComponent.component_kind == request.component_kind,
                SystemComponent.logical_name == request.logical_name,
            )
        )
        if existing is not None:
            raise ApplicationCatalogError(
                "CATALOG_CONFLICT",
                details={"reason": "COMPONENT_IDENTITY_ALREADY_REGISTERED"},
                workspace_id=principal.workspace_id,
            )

        component_id = new_system_component_id()
        now = _as_utc(self.clock())
        authority_receipt_id = new_authority_receipt_id()
        payload: dict[str, Any] = {
            "component_id": component_id,
            "workspace_id": principal.workspace_id,
            "application_id": request.application_id,
            "component_kind": request.component_kind,
            "logical_name": request.logical_name,
            "owner_principal_ids": list(request.owner_principal_ids),
            "criticality": request.criticality,
            "data_classification": request.data_classification,
            "permission_classification": request.permission_classification,
            "effect_classification": request.effect_classification,
            "dataset_role": request.dataset_role,
            "lifecycle_state": "REGISTERED",
            "exact_previous_system_component_binding_or_null": None,
            "record_envelope": self._envelope(
                workspace_id=principal.workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=authority_receipt_id,
            ),
        }
        return self._write_catalog_record(
            spec=spec,
            subject_id=component_id,
            subject_revision=1,
            envelope_payload=payload,
            business_payload={
                "component_id": component_id,
                "application_id": request.application_id,
                "component_kind": request.component_kind,
                "logical_name": request.logical_name,
                "lifecycle_state": "REGISTERED",
            },
            correlation_id=request.application_id,
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            resource_id=component_id,
            recorded_at=now,
        )

    def record_dependency_edge(
        self,
        request: DependencyEdgeRecordRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> DependencyEdgeRecordResponse:
        spec = _SPECS["edge"]
        request_id = request_id or new_request_id()
        body = request.model_dump(mode="json")
        request_fingerprint = self.idempotency.fingerprint(body)
        self._validate_principal_row(principal)
        self._require_mutation_principal(principal)
        self._require_catalog_trust_role(principal)
        if (
            principal.requested_context.required_scope != _INTENT_SCOPE
            or _INTENT_SCOPE not in principal.scopes
        ):
            raise ApplicationCatalogError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )
        application = self._load_application_for_mutation(
            principal=principal,
            application_id=request.application_id,
            request_id=request_id,
            spec=spec,
        )
        from_ok = self._component_belongs(
            principal.workspace_id, request.application_id, request.from_component_id
        )
        to_ok = self._component_belongs(
            principal.workspace_id, request.application_id, request.to_component_id
        )
        if not from_ok or not to_ok:
            audit = self._record_read_audit(
                principal=principal,
                action=spec.intent,
                target=f"ai_application:{request.application_id}",
                params={"request_id": request_id, "resource_requested": True},
                result="denied",
                error_code="RESOURCE_NOT_FOUND",
            )
            raise ApplicationCatalogError(
                "RESOURCE_NOT_FOUND",
                audit_ref=audit.audit_ref,
                workspace_id=principal.workspace_id,
            )
        if request.from_component_id == request.to_component_id:
            raise ApplicationCatalogError(
                "VALIDATION_FAILED",
                details={"reason": "SELF_DEPENDENCY"},
                workspace_id=principal.workspace_id,
            )
        self._lock_dependency_graph(
            workspace_id=principal.workspace_id,
            application_id=request.application_id,
        )
        if self._would_create_cycle(
            workspace_id=principal.workspace_id,
            application_id=request.application_id,
            from_id=request.from_component_id,
            to_id=request.to_component_id,
        ):
            raise ApplicationCatalogError(
                "VALIDATION_FAILED",
                details={"reason": "GRAPH_CYCLE"},
                workspace_id=principal.workspace_id,
            )
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=spec.intent,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                verify_terminal=PublicIdempotencyService.verify_terminal_presence,
            )
        except PublicIdempotencyError as exc:
            raise ApplicationCatalogError(exc.code) from exc
        if lookup.record is not None:
            try:
                response = self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=spec.response_model,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind=spec.resource_kind,
                    resource_field=spec.resource_field,
                    resource_id_field=spec.resource_id_field,
                )
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise ApplicationCatalogError(exc.code) from exc

        edge_id = new_dependency_edge_id()
        now = _as_utc(self.clock())
        authority_receipt_id = new_authority_receipt_id()
        edge_digest = canonical_digest(
            {
                "from_component_id": request.from_component_id,
                "to_component_id": request.to_component_id,
                "relation": request.relation,
                "required": request.required,
            }
        )
        payload: dict[str, Any] = {
            "edge_id": edge_id,
            "workspace_id": principal.workspace_id,
            "application_id": request.application_id,
            "from_component_id": request.from_component_id,
            "to_component_id": request.to_component_id,
            "relation": request.relation,
            "required": request.required,
            "edge_digest": edge_digest,
            "record_envelope": self._envelope(
                workspace_id=principal.workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=authority_receipt_id,
            ),
        }
        return self._write_catalog_record(
            spec=spec,
            subject_id=edge_id,
            subject_revision=1,
            envelope_payload=payload,
            business_payload={
                "edge_id": edge_id,
                "application_id": request.application_id,
                "from_component_id": request.from_component_id,
                "to_component_id": request.to_component_id,
                "relation": request.relation,
                "edge_digest": edge_digest,
            },
            correlation_id=request.application_id,
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            resource_id=edge_id,
            recorded_at=now,
        )

    def _component_belongs(
        self, workspace_id: str, application_id: str, component_id: str
    ) -> bool:
        row = self.session.get(SystemComponent, component_id)
        return bool(
            row is not None
            and row.workspace_id == workspace_id
            and row.application_id == application_id
        )

    def _would_create_cycle(
        self, *, workspace_id: str, application_id: str, from_id: str, to_id: str
    ) -> bool:
        rows = list(
            self.session.scalars(
                select(DependencyEdge).where(
                    DependencyEdge.workspace_id == workspace_id,
                    DependencyEdge.application_id == application_id,
                )
            ).all()
        )
        adjacency: dict[str, list[str]] = defaultdict(list)
        for edge in rows:
            adjacency[edge.from_component_id].append(edge.to_component_id)
        stack = [to_id]
        seen: set[str] = set()
        while stack:
            node = stack.pop()
            if node == from_id:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(adjacency.get(node, []))
        return False

    def _lock_dependency_graph(self, *, workspace_id: str, application_id: str) -> None:
        if self.session.get_bind().dialect.name != "postgresql":
            return
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": f"v5:dependency-graph:{workspace_id}:{application_id}"},
        )

    def _validate_owner_principals(
        self, workspace_id: str, owner_principal_ids: list[str]
    ) -> None:
        for principal_id in owner_principal_ids:
            row = self.session.get(PublicPrincipal, principal_id)
            if (
                row is None
                or row.workspace_id != workspace_id
                or row.state != "ACTIVE"
            ):
                raise ApplicationCatalogError(
                    "VALIDATION_FAILED",
                    details={"reason": "OWNER_PRINCIPAL_UNKNOWN"},
                    workspace_id=workspace_id,
                )

    # ------------------------------------------------------------ read paths

    def get_application(
        self,
        application_id: str,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str | None = None,
    ) -> ApplicationGetResponse:
        request_id = request_id or new_request_id()
        action = "applications.get"
        self._validate_principal_row(principal)
        self._require_read_principal(principal)
        self._require_scope(
            principal=principal,
            required_scope=_READ_SCOPE,
            request_id=request_id,
            action=action,
            target=f"ai_application:{application_id}",
        )
        application = self._load_application_for_read(
            principal=principal,
            application_id=application_id,
            request_id=request_id,
            action=action,
        )
        # Resource visibility is checked after the workspace scope: a reader in
        # the same workspace but without the application's project grant must
        # receive the same audited OPAQUE_NOT_FOUND as a missing resource.
        self._assert_application_readable(
            principal, application.application_id, action, request_id
        )
        envelope = self.verify_authoritative_record(
            row=application,
            subject_kind="AI_APPLICATION",
            id_field="application_id",
            scalar_fields=(
                "project_id",
                "slug",
                "display_name",
                "owner_principal_ids",
                "criticality",
                "data_classification",
                "governance_mode",
                "lifecycle_state",
            ),
            lifecycle_history=True,
        )
        return self._read_response(
            spec=_SPECS["application"],
            principal=principal,
            request_id=request_id,
            target=application_id,
            envelope=envelope,
        )

    def get_environment(
        self,
        environment_id: str,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str | None = None,
    ) -> EnvironmentGetResponse:
        request_id = request_id or new_request_id()
        action = "environments.get"
        self._validate_principal_row(principal)
        self._require_read_principal(principal)
        self._require_scope(
            principal=principal,
            required_scope=_READ_SCOPE,
            request_id=request_id,
            action=action,
            target=f"environment:{environment_id}",
        )
        row = self.session.get(Environment, environment_id)
        if row is None or row.workspace_id != principal.workspace_id:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"environment:{environment_id}",
            )
        assert row is not None
        self._assert_application_readable(principal, row.application_id, action, request_id)
        envelope = self.verify_authoritative_record(
            row=row,
            subject_kind="ENVIRONMENT",
            id_field="environment_id",
            scalar_fields=(
                "application_id",
                "logical_name",
                "risk_classification",
                "lifecycle_state",
            ),
            lifecycle_history=False,
        )
        return self._read_response(
            spec=_SPECS["environment"],
            principal=principal,
            request_id=request_id,
            target=environment_id,
            envelope=envelope,
        )

    def get_component(
        self,
        component_id: str,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str | None = None,
    ) -> ComponentGetResponse:
        request_id = request_id or new_request_id()
        action = "system-components.get"
        self._validate_principal_row(principal)
        self._require_read_principal(principal)
        self._require_scope(
            principal=principal,
            required_scope=_READ_SCOPE,
            request_id=request_id,
            action=action,
            target=f"system_component:{component_id}",
        )
        row = self.session.get(SystemComponent, component_id)
        if row is None or row.workspace_id != principal.workspace_id:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"system_component:{component_id}",
            )
        assert row is not None
        self._assert_application_readable(principal, row.application_id, action, request_id)
        envelope = self.verify_authoritative_record(
            row=row,
            subject_kind="SYSTEM_COMPONENT",
            id_field="component_id",
            scalar_fields=(
                "application_id",
                "component_kind",
                "logical_name",
                "owner_principal_ids",
                "criticality",
                "data_classification",
                "permission_classification",
                "effect_classification",
                "dataset_role",
                "lifecycle_state",
            ),
            lifecycle_history=True,
        )
        return self._read_response(
            spec=_SPECS["component"],
            principal=principal,
            request_id=request_id,
            target=component_id,
            envelope=envelope,
        )

    def get_dependency_edge(
        self,
        edge_id: str,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str | None = None,
    ) -> DependencyEdgeGetResponse:
        request_id = request_id or new_request_id()
        action = "dependency-edges.get"
        self._validate_principal_row(principal)
        self._require_read_principal(principal)
        self._require_scope(
            principal=principal,
            required_scope=_READ_SCOPE,
            request_id=request_id,
            action=action,
            target=f"dependency_edge:{edge_id}",
        )
        row = self.session.get(DependencyEdge, edge_id)
        if row is None or row.workspace_id != principal.workspace_id:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"dependency_edge:{edge_id}",
            )
        assert row is not None
        self._assert_application_readable(principal, row.application_id, action, request_id)
        envelope = self.verify_authoritative_record(
            row=row,
            subject_kind="DEPENDENCY_EDGE",
            id_field="edge_id",
            scalar_fields=(
                "application_id",
                "from_component_id",
                "to_component_id",
                "relation",
                "required",
                "edge_digest",
            ),
            lifecycle_history=False,
        )
        return self._read_response(
            spec=_SPECS["edge"],
            principal=principal,
            request_id=request_id,
            target=edge_id,
            envelope=envelope,
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

    def _verified_envelope(self, envelope: Any, stored_digest: str) -> dict[str, Any]:
        if not isinstance(envelope, dict):
            raise ApplicationCatalogError("INTERNAL_ERROR")
        try:
            verified = assert_v5_record_digest(envelope)
        except V4IntegrityError as exc:
            raise ApplicationCatalogError("INTERNAL_ERROR") from exc
        if verified != stored_digest:
            raise ApplicationCatalogError("INTERNAL_ERROR")
        return envelope

    def verify_authoritative_record(
        self,
        *,
        row: Any,
        subject_kind: str,
        id_field: str,
        scalar_fields: tuple[str, ...],
        lifecycle_history: bool,
    ) -> dict[str, Any]:
        envelope = self._verified_envelope(row.envelope_payload, row.record_digest)
        record_envelope = envelope.get("record_envelope")
        revision = (
            row.revision
            if lifecycle_history or subject_kind == "ENVIRONMENT"
            else 1 if subject_kind == "DEPENDENCY_EDGE" else None
        )
        envelope_revision = getattr(row, "revision", 1)
        if (
            not isinstance(record_envelope, dict)
            or envelope.get(id_field) != getattr(row, id_field)
            or envelope.get("workspace_id") != row.workspace_id
            or record_envelope.get("revision") != envelope_revision
            or record_envelope.get("record_digest") != row.record_digest
            or record_envelope.get("authority_receipt_id") != row.authority_receipt_id
            or record_envelope.get("recorded_by_principal")
            != row.recorded_by_principal
            or any(envelope.get(field) != getattr(row, field) for field in scalar_fields)
        ):
            raise ApplicationCatalogError("INTERNAL_ERROR")
        if subject_kind == "DEPENDENCY_EDGE" and row.edge_digest != canonical_digest(
            {
                "from_component_id": row.from_component_id,
                "to_component_id": row.to_component_id,
                "relation": row.relation,
                "required": row.required,
            }
        ):
            raise ApplicationCatalogError("INTERNAL_ERROR")
        try:
            if lifecycle_history:
                binding = {
                    "kind": subject_kind,
                    "id": getattr(row, id_field),
                    "revision": row.revision,
                    "digest": row.record_digest,
                }
                self.authority.validate_exact_lifecycle_binding(
                    workspace_id=row.workspace_id,
                    binding=binding,
                    require_current=True,
                    require_active=row.lifecycle_state == "ACTIVE",
                    application_id=(
                        row.application_id
                        if subject_kind == "SYSTEM_COMPONENT"
                        else None
                    ),
                )
                self.authority.validate_receipt_binding(
                    authority_receipt_id=row.authority_receipt_id,
                    workspace_id=row.workspace_id,
                    subject_kind=subject_kind,
                    subject_id=getattr(row, id_field),
                    subject_revision=row.revision,
                    subject_digest=row.record_digest,
                    lifecycle_history=True,
                )
                if row.revision > 1:
                    previous_field = (
                        "exact_previous_application_binding"
                        if subject_kind == "AI_APPLICATION"
                        else "exact_previous_system_component_binding"
                    )
                    previous = envelope.get(previous_field)
                    if not isinstance(previous, dict):
                        raise V5AuthorityError("v5.authority.lifecycle_previous_missing")
                    previous_row = self.authority.validate_exact_lifecycle_binding(
                        workspace_id=row.workspace_id,
                        binding=previous,
                        application_id=(
                            row.application_id
                            if subject_kind == "SYSTEM_COMPONENT"
                            else None
                        ),
                    )
                    self.authority.validate_receipt_binding(
                        authority_receipt_id=previous_row.authority_receipt_id,
                        workspace_id=row.workspace_id,
                        subject_kind=subject_kind,
                        subject_id=getattr(row, id_field),
                        subject_revision=previous_row.revision,
                        subject_digest=previous_row.record_digest,
                        lifecycle_history=True,
                    )
            else:
                self.authority.validate_receipt_binding(
                    authority_receipt_id=row.authority_receipt_id,
                    workspace_id=row.workspace_id,
                    subject_kind=subject_kind,
                    subject_id=getattr(row, id_field),
                    subject_revision=revision,
                    subject_digest=row.record_digest,
                )
        except V5AuthorityError as exc:
            raise ApplicationCatalogError("INTERNAL_ERROR") from exc
        return envelope

    def _read_response(
        self,
        *,
        spec: _CatalogSpec,
        principal: AcceptedPrincipalContext,
        request_id: str,
        target: str,
        envelope: dict[str, Any],
    ):
        audit = self._record_read_audit(
            principal=principal,
            action=spec.intent.replace(".register", ".get").replace(
                ".record", ".get"
            ),
            target=f"{spec.resource_kind}:{target}",
            params={"request_id": request_id, "resource_requested": True},
            evidence_refs={
                "resource_kind": spec.resource_kind,
                "resource_id": target,
                "record_digest": envelope["record_envelope"]["record_digest"],
            },
        )
        try:
            return spec.get_response_model.model_validate(
                {
                    "schema_version": "2.0",
                    "workspace_id": principal.workspace_id,
                    "request_id": request_id,
                    "audit_ref": audit.audit_ref,
                    spec.resource_field: envelope,
                }
            )
        except ValidationError as exc:
            raise ApplicationCatalogError("INTERNAL_ERROR") from exc

    # ------------------------------------------------------------- envelope

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

    def _build_application_envelope(
        self,
        *,
        request: ApplicationRegisterRequest,
        application_id: str,
        workspace_id: str,
        principal: AcceptedPrincipalContext,
        authority_receipt_id: str | None,
        recorded_at: datetime | None,
    ) -> dict[str, Any]:
        now = recorded_at or _as_utc(self.clock())
        return {
            "application_id": application_id,
            "workspace_id": workspace_id,
            "project_id": request.project_id,
            "slug": request.slug,
            "display_name": request.display_name,
            "owner_principal_ids": list(request.owner_principal_ids),
            "criticality": request.criticality,
            "data_classification": request.data_classification,
            "governance_mode": request.governance_mode,
            "lifecycle_state": "REGISTERED",
            "exact_previous_application_binding_or_null": None,
            "record_envelope": self._envelope(
                workspace_id=workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=(
                    authority_receipt_id or new_authority_receipt_id()
                ),
            ),
        }

    # ------------------------------------------------------------ write core

    def _write_catalog_record(
        self,
        *,
        spec: _CatalogSpec,
        subject_id: str,
        subject_revision: int | None,
        envelope_payload: dict[str, Any],
        business_payload: dict[str, Any],
        correlation_id: str,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        resource_id: str,
        recorded_at: datetime | None = None,
    ) -> Any:
        now = _as_utc(recorded_at or self.clock())
        transaction_id = new_transaction_id()
        controller = self._resolve_controller(spec, principal.workspace_id, now)
        envelope = envelope_payload["record_envelope"]
        envelope["recorded_by_principal"] = controller.controller_principal
        digest = v5_record_digest(envelope_payload)
        envelope["record_digest"] = digest

        lifecycle_registration = spec.subject_kind in {
            "AI_APPLICATION",
            "SYSTEM_COMPONENT",
        }
        try:
            if lifecycle_registration:
                V5LifecycleAuthorityService(
                    self.session
                ).append_registration_revision(
                    kind=spec.subject_kind,
                    envelope_payload=envelope_payload,
                )
            else:
                row = self._build_projection_row(
                    spec=spec,
                    subject_id=subject_id,
                    envelope_payload=envelope_payload,
                    digest=digest,
                    now=now,
                )
                self.session.add(row)
                self.session.flush()
        except V5LifecycleAuthorityError as exc:
            raise ApplicationCatalogError("INTERNAL_ERROR") from exc
        except IntegrityError as exc:
            # Concurrent duplicate identity: the advisory-locked idempotency
            # lookup cannot see the other transaction's uncommitted row, so the
            # unique constraint is the authoritative fail-closed guard.
            raise ApplicationCatalogError(
                "CATALOG_CONFLICT",
                details={"reason": "DUPLICATE_CATALOG_IDENTITY"},
                workspace_id=principal.workspace_id,
            ) from exc

        if lifecycle_registration:
            exact_binding = {
                "kind": spec.subject_kind,
                "id": subject_id,
                "revision": 1,
                "digest": digest,
            }
            if spec.subject_kind == "AI_APPLICATION":
                event_payload = {
                    "exact_previous_application_binding_or_null": None,
                    "exact_application_binding": exact_binding,
                    "project_id": envelope_payload["project_id"],
                    "slug": envelope_payload["slug"],
                    "lifecycle_state": "REGISTERED",
                }
            else:
                event_payload = {
                    "exact_previous_system_component_binding_or_null": None,
                    "exact_system_component_binding": exact_binding,
                    "application_id": envelope_payload["application_id"],
                    "component_kind": envelope_payload["component_kind"],
                    "logical_name": envelope_payload["logical_name"],
                    "lifecycle_state": "REGISTERED",
                }
        elif spec.subject_kind == "ENVIRONMENT":
            event_payload = {
                "exact_environment_binding": {
                    "kind": "ENVIRONMENT",
                    "id": subject_id,
                    "revision": 1,
                    "digest": digest,
                },
                "application_id": envelope_payload["application_id"],
                "logical_name": envelope_payload["logical_name"],
                "lifecycle_state": envelope_payload["lifecycle_state"],
            }
        elif spec.subject_kind == "DEPENDENCY_EDGE":
            event_payload = {
                "exact_dependency_edge_binding": {
                    "kind": "DEPENDENCY_EDGE",
                    "id": subject_id,
                    "revision": 1,
                    "digest": digest,
                },
                "application_id": envelope_payload["application_id"],
                "from_component_id": envelope_payload["from_component_id"],
                "to_component_id": envelope_payload["to_component_id"],
                "relation": envelope_payload["relation"],
                "edge_digest": envelope_payload["edge_digest"],
            }
        else:
            event_payload = {
                **business_payload,
                "subject_kind": spec.subject_kind,
                "subject_id": subject_id,
                "subject_revision": subject_revision,
                "subject_digest": digest,
                "authority_receipt_id": envelope["authority_receipt_id"],
            }
        try:
            event = self.events.append_event(
                workspace_id=principal.workspace_id,
                aggregate_type=spec.aggregate_type,
                aggregate_id=subject_id,
                event_type=spec.event_type,
                payload=event_payload,
                causation_id=request_id,
                correlation_id=correlation_id,
                actor_principal=controller.controller_principal,
                transaction_id=transaction_id,
                occurred_at=now,
                authority_receipt_id=(
                    envelope["authority_receipt_id"]
                    if lifecycle_registration
                    or spec.subject_kind in {"ENVIRONMENT", "DEPENDENCY_EDGE"}
                    else None
                ),
            )
            audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=controller.controller_principal,
                action=f"controller.{spec.event_type}",
                target=subject_id,
                params={"command": spec.intent},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "subject_kind": spec.subject_kind,
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
                workspace_id=principal.workspace_id,
                subject_id=subject_id,
                subject_revision=subject_revision,
                subject_digest=digest,
                event_id=event.event_id,
                transaction_id=transaction_id,
                audit_ref=audit.audit_ref,
                recorded_at=now,
                lifecycle_history=lifecycle_registration,
            )
        except (V4EventStoreError, V5AuthorityError) as exc:
            raise ApplicationCatalogError("INTERNAL_ERROR") from exc
        except V4AuditUnavailable as exc:
            raise ApplicationCatalogError("AUDIT_UNAVAILABLE") from exc

        try:
            command_audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=spec.intent,
                target=subject_id,
                params={"request_fingerprint": request_fingerprint},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "resource_kind": spec.resource_kind,
                    "resource_id": resource_id,
                    "record_digest": digest,
                },
                occurred_at=now + timedelta(microseconds=1),
            )
        except V4AuditUnavailable as exc:
            raise ApplicationCatalogError("AUDIT_UNAVAILABLE") from exc
        return self._persist_response(
            spec=spec,
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            audit_ref=command_audit.audit_ref,
            resource_id=resource_id,
            envelope_payload=envelope_payload,
            completed_at=now + timedelta(microseconds=1),
        )

    def _resolve_controller(
        self, spec: _CatalogSpec, workspace_id: str, now: datetime
    ) -> V5ResolvedController:
        try:
            return self.authority.resolve_controller(
                workspace_id=workspace_id,
                subject_kind=spec.subject_kind,
                command=spec.intent,
                event_type=spec.event_type,
                recorded_at=now,
            )
        except V5AuthorityError as exc:
            raise ApplicationCatalogError("INTERNAL_ERROR") from exc

    def _build_projection_row(
        self,
        *,
        spec: _CatalogSpec,
        subject_id: str,
        envelope_payload: dict[str, Any],
        digest: str,
        now: datetime,
    ) -> Any:
        envelope = envelope_payload["record_envelope"]
        if spec.subject_kind == "AI_APPLICATION":
            return AIApplication(
                application_id=subject_id,
                workspace_id=envelope_payload["workspace_id"],
                project_id=envelope_payload["project_id"],
                slug=envelope_payload["slug"],
                display_name=envelope_payload["display_name"],
                owner_principal_ids=list(envelope_payload["owner_principal_ids"]),
                criticality=envelope_payload["criticality"],
                data_classification=envelope_payload["data_classification"],
                governance_mode=envelope_payload["governance_mode"],
                lifecycle_state=envelope_payload["lifecycle_state"],
                revision=1,
                envelope_payload=envelope_payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
                updated_at=now,
            )
        if spec.subject_kind == "ENVIRONMENT":
            return Environment(
                environment_id=subject_id,
                workspace_id=envelope_payload["workspace_id"],
                application_id=envelope_payload["application_id"],
                logical_name=envelope_payload["logical_name"],
                risk_classification=envelope_payload["risk_classification"],
                lifecycle_state=envelope_payload["lifecycle_state"],
                revision=1,
                envelope_payload=envelope_payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
                updated_at=now,
            )
        if spec.subject_kind == "SYSTEM_COMPONENT":
            return SystemComponent(
                component_id=subject_id,
                workspace_id=envelope_payload["workspace_id"],
                application_id=envelope_payload["application_id"],
                component_kind=envelope_payload["component_kind"],
                logical_name=envelope_payload["logical_name"],
                owner_principal_ids=list(envelope_payload["owner_principal_ids"]),
                criticality=envelope_payload["criticality"],
                data_classification=envelope_payload["data_classification"],
                permission_classification=envelope_payload[
                    "permission_classification"
                ],
                effect_classification=envelope_payload["effect_classification"],
                dataset_role=envelope_payload.get("dataset_role"),
                lifecycle_state=envelope_payload["lifecycle_state"],
                revision=1,
                envelope_payload=envelope_payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
                updated_at=now,
            )
        if spec.subject_kind == "DEPENDENCY_EDGE":
            return DependencyEdge(
                edge_id=subject_id,
                workspace_id=envelope_payload["workspace_id"],
                application_id=envelope_payload["application_id"],
                from_component_id=envelope_payload["from_component_id"],
                to_component_id=envelope_payload["to_component_id"],
                relation=envelope_payload["relation"],
                required=envelope_payload["required"],
                edge_digest=envelope_payload["edge_digest"],
                envelope_payload=envelope_payload,
                record_digest=digest,
                authority_receipt_id=envelope["authority_receipt_id"],
                recorded_by_principal=envelope["recorded_by_principal"],
                created_at=now,
            )
        raise ApplicationCatalogError("INTERNAL_ERROR")

    def _persist_response(
        self,
        *,
        spec: _CatalogSpec,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        audit_ref: str,
        resource_id: str,
        envelope_payload: dict[str, Any],
        completed_at: datetime,
    ) -> Any:
        core: dict[str, Any] = {
            "schema_version": "2.0",
            "workspace_id": principal.workspace_id,
            "request_id": request_id,
            "audit_ref": audit_ref,
            spec.resource_field: envelope_payload,
        }
        response_digest = canonical_digest(core)
        receipt_id = new_idempotency_receipt_id()
        receipt: dict[str, Any] = {
            "schema_version": "1.0",
            "workspace_id": principal.workspace_id,
            "principal_id": principal.principal_id,
            "intent": spec.intent,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "resource": {"kind": spec.resource_kind, "id": resource_id},
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
                intent=spec.intent,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                resource_kind=spec.resource_kind,
                resource_id=resource_id,
                request_id=request_id,
                audit_ref=audit_ref,
                response_payload=core,
                response_digest=response_digest,
                receipt_payload=receipt,
                receipt_digest=receipt_digest,
                idempotency_receipt_id=receipt_id,
                completed_at=completed_at,
                response_model=spec.response_model,
                receipt_model=V5IdempotencyReceipt,
                resource_field=spec.resource_field,
                resource_id_field=spec.resource_id_field,
            )
        except PublicIdempotencyError as exc:
            raise ApplicationCatalogError(exc.code) from exc
        return spec.response_model.model_validate(
            {**core, "idempotency": {"receipt": receipt, "replayed": False}}
        )


__all__ = [
    "ApplicationCatalogError",
    "ApplicationCatalogService",
    "PRINCIPAL_RE",
    "V5ReadDenial",
]
