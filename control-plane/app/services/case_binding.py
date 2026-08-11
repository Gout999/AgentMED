"""V5-1C ApplicationCaseBinding transaction (cases.bind-application).

An additive link from an immutable S1A QualityCase to an AI application /
environment.  The S1A signal/case payloads and digests are never rewritten;
the binding is a new immutable record with its own digest, authority receipt,
event + outbox, and public-command idempotency, all in one transaction.  One
binding per exact case identity (case_id + case_revision + case_digest): the
same target replays idempotently, a different target conflicts, and rebinding
requires a new quality case revision.  The service only flushes; the caller
owns commit/rollback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, NoReturn

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.v4_tables import PublicPrincipal, QualityCase
from app.models.v5_tables import AIApplication, ApplicationCaseBinding, Environment
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.public_api.v5_models import (
    ApplicationBindingGetResponse,
    ApplicationCaseBindingRecord,
    CaseBindApplicationRequest,
    CaseBindApplicationResponse,
    V5IdempotencyReceipt,
)
from app.services.issue_source import (
    IssueSourceError,
    IssueSourceService,
    normalize_issue_snapshot,
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
from app.utils.ids import (
    new_application_case_binding_id,
    new_authority_receipt_id,
    new_idempotency_receipt_id,
    new_request_id,
    new_transaction_id,
)
from app.utils.v4_integrity import V4IntegrityError, assert_record_digest, canonical_digest
from app.utils.v5_integrity import (
    V5_HASH_RULE,
    assert_v5_record_digest,
    v5_record_digest,
)

Clock = Callable[[], datetime]

_INTENT = "cases.bind-application"
_SCOPE = "cases:bind"
_READ_SCOPE = "cases:read"
_BIND_PRINCIPAL_TYPES = frozenset({"human", "service"})


class CaseBindingError(RuntimeError):
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


class CaseBindingReadDenial(CaseBindingError):
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
            raise ValueError("case-binding read denials support only non-mutating codes")
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


class CaseBindingService:
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
        issue_source_service: IssueSourceService | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or _utc_now
        self.audit = audit_service or V4AuditService(session, clock=self.clock)
        self.events = event_store or V4EventStore(session)
        self.authority = authority_service or V5AuthorityService(
            session, contracts_root=contracts_root
        )
        self.idempotency = idempotency_service or PublicIdempotencyService(session)
        self.issue_source = issue_source_service or IssueSourceService(session)

    # ------------------------------------------------------------ validation

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
            raise CaseBindingError("TOKEN_INVALID")

    def _require_mutation_principal(self, principal: AcceptedPrincipalContext) -> None:
        if principal.principal_type not in _BIND_PRINCIPAL_TYPES:
            raise CaseBindingError("SCOPE_FORBIDDEN", workspace_id=principal.workspace_id)

    def _require_read_principal(self, principal: AcceptedPrincipalContext) -> None:
        if principal.principal_type not in {
            "human",
            "external_agent",
            "service",
            "connector",
        }:
            raise CaseBindingError("SCOPE_FORBIDDEN", workspace_id=principal.workspace_id)

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
            raise CaseBindingError(
                "AUDIT_UNAVAILABLE",
                workspace_id=principal.workspace_id,
                rollback_required=True,
            ) from exc

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
        raise CaseBindingReadDenial(
            "SCOPE_FORBIDDEN",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
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
        raise CaseBindingReadDenial(
            "RESOURCE_NOT_FOUND",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

    # ------------------------------------------------------------- bind write

    def bind_application(
        self,
        request: CaseBindApplicationRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> CaseBindApplicationResponse:
        request_id = request_id or new_request_id()
        body = request.model_dump(mode="json")
        request_fingerprint = self.idempotency.fingerprint(body)
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=_INTENT,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                verify_terminal=PublicIdempotencyService.verify_terminal_presence,
            )
        except PublicIdempotencyError as exc:
            raise CaseBindingError(exc.code) from exc
        if lookup.record is not None:
            try:
                response = self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=CaseBindApplicationResponse,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind="application_case_binding",
                    resource_field="application_case_binding",
                    resource_id_field="application_case_binding_id",
                )
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise CaseBindingError(exc.code) from exc

        self._validate_principal_row(principal)
        self._require_mutation_principal(principal)
        if (
            principal.requested_context.required_scope != _SCOPE
            or _SCOPE not in principal.scopes
        ):
            raise CaseBindingError("SCOPE_FORBIDDEN", workspace_id=principal.workspace_id)

        quality_case = self._load_exact_case(
            principal=principal,
            case_id=request.case_id,
            case_revision=request.case_revision,
            case_digest=request.case_digest,
            request_id=request_id,
        )
        application, environment = self._load_target(
            principal=principal,
            application_id=request.application_id,
            environment_id=request.environment_id,
            request_id=request_id,
        )
        existing = self.session.scalar(
            select(ApplicationCaseBinding).where(
                ApplicationCaseBinding.workspace_id == principal.workspace_id,
                ApplicationCaseBinding.case_id == request.case_id,
                ApplicationCaseBinding.case_revision == request.case_revision,
                ApplicationCaseBinding.case_digest == request.case_digest,
            )
        )
        if existing is not None:
            # Same exact case, same target: idempotent replay of the same
            # binding record.  Same exact case, different target: conflict —
            # never silently overwritten; rebinding needs a new case revision.
            same_target = (
                existing.application_id == request.application_id
                and existing.environment_id == request.environment_id
            )
            if not same_target:
                audit = self._record_read_audit(
                    principal=principal,
                    action=_INTENT,
                    target=f"quality_case:{request.case_id}",
                    params={"request_id": request_id, "resource_requested": True},
                    result="denied",
                    error_code="CATALOG_CONFLICT",
                )
                raise CaseBindingError(
                    "CATALOG_CONFLICT",
                    details={"reason": "DIFFERENT_TARGET_FOR_SAME_EXACT_CASE"},
                    audit_ref=audit.audit_ref,
                    workspace_id=principal.workspace_id,
                )
            now = _as_utc(self.clock())
            transaction_id = new_transaction_id()
            envelope = self._verified_binding_envelope(existing)
            return self._complete_with_existing_binding(
                binding=existing,
                envelope=envelope,
                principal=principal,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                request_id=request_id,
                now=now,
                transaction_id=transaction_id,
                replayed=True,
            )

        now = _as_utc(self.clock())
        authority_receipt_id = new_authority_receipt_id()
        binding_digest = canonical_digest(
            {
                "application_id": request.application_id,
                "environment_id": request.environment_id,
                "declared_system_version_set_binding_or_unknown": (
                    request.declared_system_version_set_binding_or_unknown
                ),
            }
        )
        issue_snapshot = None
        if request.issue_snapshot is not None:
            issue_snapshot = normalize_issue_snapshot(
                request.issue_snapshot.snapshot_payload,
                source_kind=request.issue_snapshot.source_kind,
                source_url=request.issue_snapshot.source_url,
                external_repo=request.issue_snapshot.external_repo,
                external_issue_number=request.issue_snapshot.external_issue_number,
                fetched_at=request.issue_snapshot.fetched_at,
            )
        payload: dict[str, Any] = {
            "application_case_binding_id": new_application_case_binding_id(),
            "workspace_id": principal.workspace_id,
            "exact_case_binding": {
                "case_id": request.case_id,
                "case_revision": request.case_revision,
                "case_digest": request.case_digest,
            },
            "application_id": request.application_id,
            "environment_id": request.environment_id,
            "declared_system_version_set_binding_or_unknown": (
                request.declared_system_version_set_binding_or_unknown
            ),
            "binding_digest": binding_digest,
            "record_envelope": self._envelope(
                workspace_id=principal.workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=authority_receipt_id,
            ),
        }
        return self._write_binding_record(
            payload=payload,
            issue_snapshot=issue_snapshot,
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            now=now,
        )

    def _load_exact_case(
        self,
        *,
        principal: AcceptedPrincipalContext,
        case_id: str,
        case_revision: int,
        case_digest: str,
        request_id: str,
    ) -> QualityCase:
        quality_case = self.session.get(QualityCase, case_id)
        if quality_case is None or quality_case.workspace_id != principal.workspace_id:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=_INTENT,
                target=f"quality_case:{case_id}",
            )
        assert quality_case is not None
        try:
            snapshot = quality_case.snapshot_payload
            assert_record_digest(snapshot, self_digest_field="record_digest")
        except (V4IntegrityError, TypeError, ValueError) as exc:
            raise CaseBindingError("INTERNAL_ERROR") from exc
        if (
            quality_case.revision != case_revision
            or quality_case.record_digest != case_digest
            or snapshot.get("record_digest") != case_digest
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=_INTENT,
                target=f"quality_case:{case_id}",
            )
        return quality_case

    def _load_target(
        self,
        *,
        principal: AcceptedPrincipalContext,
        application_id: str,
        environment_id: str,
        request_id: str,
    ) -> tuple[AIApplication, Environment]:
        application = self.session.get(AIApplication, application_id)
        environment = self.session.get(Environment, environment_id)
        target_ok = (
            application is not None
            and environment is not None
            and application.workspace_id == principal.workspace_id
            and environment.workspace_id == principal.workspace_id
            and environment.application_id == application_id
            and application.project_id in principal.project_ids
        )
        if not target_ok:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=_INTENT,
                target=f"ai_application:{application_id}",
            )
        assert application is not None and environment is not None
        return application, environment

    # ---------------------------------------------------------- write core

    def _write_binding_record(
        self,
        *,
        payload: dict[str, Any],
        issue_snapshot: dict[str, Any] | None,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        now: datetime,
    ) -> CaseBindApplicationResponse:
        transaction_id = new_transaction_id()
        controller = self._resolve_controller(principal.workspace_id, now)
        envelope = payload["record_envelope"]
        digest = v5_record_digest(payload)
        envelope["record_digest"] = digest

        binding_id = payload["application_case_binding_id"]
        row = ApplicationCaseBinding(
            application_case_binding_id=binding_id,
            workspace_id=payload["workspace_id"],
            case_id=payload["exact_case_binding"]["case_id"],
            case_revision=payload["exact_case_binding"]["case_revision"],
            case_digest=payload["exact_case_binding"]["case_digest"],
            application_id=payload["application_id"],
            environment_id=payload["environment_id"],
            declared_system_version_set_binding_or_unknown=payload[
                "declared_system_version_set_binding_or_unknown"
            ],
            binding_digest=payload["binding_digest"],
            envelope_payload=payload,
            record_digest=digest,
            authority_receipt_id=envelope["authority_receipt_id"],
            recorded_by_principal=envelope["recorded_by_principal"],
            created_at=now,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise CaseBindingError(
                "CATALOG_CONFLICT",
                details={"reason": "DUPLICATE_EXACT_CASE_BINDING"},
                workspace_id=principal.workspace_id,
            ) from exc

        event_payload: dict[str, Any] = {
            "exact_application_case_binding": {
                "kind": "APPLICATION_CASE_BINDING",
                "id": binding_id,
                "revision": None,
                "digest": digest,
            },
            "exact_case_binding": payload["exact_case_binding"],
            "application_id": payload["application_id"],
            "environment_id": payload["environment_id"],
            "declared_system_version_set_binding_or_unknown": payload[
                "declared_system_version_set_binding_or_unknown"
            ],
            "subject_kind": "APPLICATION_CASE_BINDING",
            "subject_id": binding_id,
            "subject_revision": None,
            "subject_digest": digest,
            "authority_receipt_id": envelope["authority_receipt_id"],
        }
        try:
            event = self.events.append_event(
                workspace_id=principal.workspace_id,
                aggregate_type="application_case_binding",
                aggregate_id=binding_id,
                event_type="case.application_bound",
                payload=event_payload,
                causation_id=request_id,
                correlation_id=payload["exact_case_binding"]["case_id"],
                actor_principal=controller.controller_principal,
                transaction_id=transaction_id,
                occurred_at=now,
            )
            audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=controller.controller_principal,
                action="controller.case.application_bound",
                target=binding_id,
                params={"command": _INTENT},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "subject_kind": "APPLICATION_CASE_BINDING",
                    "subject_id": binding_id,
                    "subject_revision": None,
                    "subject_digest": digest,
                    "event_id": event.event_id,
                },
                occurred_at=now,
            )
            self.authority.record_receipt(
                resolved=controller,
                authority_receipt_id=envelope["authority_receipt_id"],
                workspace_id=principal.workspace_id,
                subject_id=binding_id,
                subject_revision=None,
                subject_digest=digest,
                event_id=event.event_id,
                transaction_id=transaction_id,
                audit_ref=audit.audit_ref,
                recorded_at=now,
            )
        except (V4EventStoreError, V5AuthorityError) as exc:
            raise CaseBindingError("INTERNAL_ERROR") from exc
        except V4AuditUnavailable as exc:
            raise CaseBindingError("AUDIT_UNAVAILABLE") from exc

        # A read-only issue snapshot rides along as data (never an instruction);
        # recorded in the same transaction, linked by case id.
        if issue_snapshot is not None:
            fetched_at = datetime.fromisoformat(
                issue_snapshot["fetched_at"].replace("Z", "+00:00")
            )
            self.issue_source.record_snapshot(
                workspace_id=principal.workspace_id,
                case_id=payload["exact_case_binding"]["case_id"],
                canonical_snapshot=issue_snapshot,
                recorded_by_principal=principal.principal_id,
                fetched_at=_as_utc(fetched_at),
            )

        try:
            command_audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=_INTENT,
                target=binding_id,
                params={"request_fingerprint": request_fingerprint},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "resource_kind": "application_case_binding",
                    "resource_id": binding_id,
                    "record_digest": digest,
                },
                occurred_at=now + timedelta(microseconds=1),
            )
        except V4AuditUnavailable as exc:
            raise CaseBindingError("AUDIT_UNAVAILABLE") from exc
        return self._persist_response(
            binding_id=binding_id,
            envelope=payload,
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            audit_ref=command_audit.audit_ref,
            completed_at=now + timedelta(microseconds=1),
        )

    def _complete_with_existing_binding(
        self,
        *,
        binding: ApplicationCaseBinding,
        envelope: dict[str, Any],
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        now: datetime,
        transaction_id: str,
        replayed: bool,
    ) -> CaseBindApplicationResponse:
        try:
            command_audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=_INTENT,
                target=binding.application_case_binding_id,
                params={"request_fingerprint": request_fingerprint},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "resource_kind": "application_case_binding",
                    "resource_id": binding.application_case_binding_id,
                    "record_digest": binding.record_digest,
                    "replayed": replayed,
                },
                occurred_at=now,
            )
        except V4AuditUnavailable as exc:
            raise CaseBindingError("AUDIT_UNAVAILABLE") from exc
        return self._persist_response(
            binding_id=binding.application_case_binding_id,
            envelope=envelope,
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            audit_ref=command_audit.audit_ref,
            completed_at=now,
        )

    # -------------------------------------------------------------- read path

    def get_binding(
        self,
        case_id: str,
        *,
        case_revision: int,
        case_digest: str,
        principal: AcceptedPrincipalContext,
        request_id: str | None = None,
    ) -> ApplicationBindingGetResponse:
        request_id = request_id or new_request_id()
        action = "case-application-bindings.get"
        self._validate_principal_row(principal)
        self._require_read_principal(principal)
        self._require_scope(
            principal=principal,
            required_scope=_READ_SCOPE,
            request_id=request_id,
            action=action,
            target=f"quality_case:{case_id}",
        )
        binding = self.session.scalar(
            select(ApplicationCaseBinding).where(
                ApplicationCaseBinding.workspace_id == principal.workspace_id,
                ApplicationCaseBinding.case_id == case_id,
                ApplicationCaseBinding.case_revision == case_revision,
                ApplicationCaseBinding.case_digest == case_digest,
            )
        )
        if binding is None:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
            )
        assert binding is not None
        # Opaque: a reader in the same workspace without the application's
        # project grant sees the same audited not-found as a missing binding.
        application = self.session.get(AIApplication, binding.application_id)
        if (
            application is None
            or application.workspace_id != principal.workspace_id
            or application.project_id not in principal.project_ids
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"ai_application:{binding.application_id}",
            )
        envelope = self._verified_binding_envelope(binding)
        audit = self._record_read_audit(
            principal=principal,
            action=action,
            target=f"application_case_binding:{binding.application_case_binding_id}",
            params={"request_id": request_id, "resource_requested": True},
            evidence_refs={
                "resource_kind": "application_case_binding",
                "resource_id": binding.application_case_binding_id,
                "record_digest": binding.record_digest,
            },
        )
        try:
            return ApplicationBindingGetResponse.model_validate(
                {
                    "schema_version": "2.0",
                    "workspace_id": principal.workspace_id,
                    "request_id": request_id,
                    "audit_ref": audit.audit_ref,
                    "application_case_binding": envelope,
                }
            )
        except (TypeError, ValueError) as exc:
            raise CaseBindingError("INTERNAL_ERROR") from exc

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

    def _verified_binding_envelope(self, binding: ApplicationCaseBinding) -> dict[str, Any]:
        envelope = binding.envelope_payload
        if not isinstance(envelope, dict):
            raise CaseBindingError("INTERNAL_ERROR")
        try:
            verified = assert_v5_record_digest(envelope)
        except V4IntegrityError as exc:
            raise CaseBindingError("INTERNAL_ERROR") from exc
        if verified != binding.record_digest:
            raise CaseBindingError("INTERNAL_ERROR")
        return envelope

    def _resolve_controller(
        self, workspace_id: str, now: datetime
    ) -> V5ResolvedController:
        try:
            return self.authority.resolve_controller(
                workspace_id=workspace_id,
                subject_kind="APPLICATION_CASE_BINDING",
                command=_INTENT,
                event_type="case.application_bound",
                recorded_at=now,
            )
        except V5AuthorityError as exc:
            raise CaseBindingError("INTERNAL_ERROR") from exc

    # -------------------------------------------------------- response core

    def _persist_response(
        self,
        *,
        binding_id: str,
        envelope: dict[str, Any],
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        audit_ref: str,
        completed_at: datetime,
    ) -> CaseBindApplicationResponse:
        core: dict[str, Any] = {
            "schema_version": "2.0",
            "workspace_id": principal.workspace_id,
            "request_id": request_id,
            "audit_ref": audit_ref,
            "application_case_binding": envelope,
        }
        response_digest = canonical_digest(core)
        receipt_id = new_idempotency_receipt_id()
        receipt: dict[str, Any] = {
            "schema_version": "1.0",
            "workspace_id": principal.workspace_id,
            "principal_id": principal.principal_id,
            "intent": _INTENT,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "resource": {"kind": "application_case_binding", "id": binding_id},
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
        receipt_digest = _record_digest(receipt, self_digest_field="receipt_digest")
        receipt["receipt_digest"] = receipt_digest
        try:
            self.idempotency.store_completed_catalog(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=_INTENT,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                resource_kind="application_case_binding",
                resource_id=binding_id,
                request_id=request_id,
                audit_ref=audit_ref,
                response_payload=core,
                response_digest=response_digest,
                receipt_payload=receipt,
                receipt_digest=receipt_digest,
                idempotency_receipt_id=receipt_id,
                completed_at=completed_at,
                response_model=CaseBindApplicationResponse,
                receipt_model=V5IdempotencyReceipt,
                resource_field="application_case_binding",
                resource_id_field="application_case_binding_id",
            )
        except PublicIdempotencyError as exc:
            raise CaseBindingError(exc.code) from exc
        return CaseBindApplicationResponse.model_validate(
            {**core, "idempotency": {"receipt": receipt, "replayed": False}}
        )


def _record_digest(record: dict[str, Any], *, self_digest_field: str) -> str:
    from app.utils.v4_integrity import record_digest

    return record_digest(record, self_digest_field=self_digest_field)


__all__ = [
    "CaseBindingError",
    "CaseBindingReadDenial",
    "CaseBindingService",
]
