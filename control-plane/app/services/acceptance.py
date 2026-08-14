"""V5-1C acceptance-criteria propose/get/confirm runtime and CaseReadiness.

A PROPOSED acceptance revision is an untrusted draft recorded by any
case-authorized actor (agents may propose).  Only a reauthenticated human
maintainer/domain reviewer may confirm, producing a NEW immutable CONFIRMED
revision that references the prior proposal; the proposal itself never
confirms itself and no record is ever rewritten in place.  Until V5-4
materializes an exact ResolutionContract, even a confirmed acceptance revision
remains non-executable and CaseReadiness stays NEEDS_ACCEPTANCE_CRITERIA.  The
S1A case payload/digest is never touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, NoReturn

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.v4_tables import PublicCredential, PublicPrincipal, QualityCase
from app.models.v5_tables import AcceptanceCriteriaRevision
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.public_api.v5_models import (
    AcceptanceCriteriaConfirmRequest,
    AcceptanceCriteriaConfirmResponse,
    AcceptanceCriteriaGetResponse,
    AcceptanceCriteriaProposeRequest,
    AcceptanceCriteriaProposeResponse,
    AcceptanceCriteriaRevisionRecord,
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
from app.utils.ids import (
    new_acceptance_criteria_revision_id,
    new_authority_receipt_id,
    new_idempotency_receipt_id,
    new_request_id,
    new_transaction_id,
)
from app.utils.v4_integrity import (
    V4IntegrityError,
    assert_record_digest,
    canonical_digest,
    record_digest,
)
from app.utils.v5_integrity import (
    V5_HASH_RULE,
    assert_v5_record_digest,
    v5_record_digest,
)

Clock = Callable[[], datetime]

_PROPOSE_INTENT = "acceptance-criteria.propose"
_CONFIRM_INTENT = "acceptance-criteria.confirm"
_GET_INTENT = "acceptance-criteria.get"
_PROPOSE_SCOPE = "acceptance_criteria:propose"
_CONFIRM_SCOPE = "acceptance_criteria:confirm"
_READ_SCOPE = "acceptance_criteria:read"
_CONFIRM_PRINCIPAL_TYPES = frozenset({"human"})
_CONFIRM_TRUST_ROLES = frozenset({"maintainer", "domain_reviewer"})
_PROPOSE_PRINCIPAL_TYPES = frozenset(
    {"human", "external_agent", "service", "connector"}
)

_RESOLUTION_CONTRACT_BINDING_STATUS = {
    "status": "PENDING_MATERIALIZATION",
    "owner": "resolution-contract-controller",
    "materialization_stage": "V5-4",
}


class AcceptanceError(RuntimeError):
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


class AcceptanceReadDenial(AcceptanceError):
    """Audited pre-write denial that the HTTP boundary may commit by itself."""

    commit_audit_on_denial = True

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
            "CATALOG_CONFLICT",
            "IDEMPOTENCY_CONFLICT",
        }:
            raise ValueError("acceptance audited denials support only policy codes")
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


class AcceptanceService:
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

    # ------------------------------------------------------------ validation

    def _validate_principal_row(
        self, principal: AcceptedPrincipalContext
    ) -> PublicPrincipal:
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
            or not isinstance(row.trust_roles, list)
            or any(
                not isinstance(role, str) or not role
                for role in (row.trust_roles or [])
            )
            or len(set(row.trust_roles or [])) != len(row.trust_roles or [])
        ):
            raise AcceptanceError("TOKEN_INVALID")
        return row

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
        raise AcceptanceReadDenial(
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
        self._deny(
            principal=principal,
            request_id=request_id,
            action=action,
            target=target,
            code="RESOURCE_NOT_FOUND",
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
            raise AcceptanceError(
                "AUDIT_UNAVAILABLE",
                workspace_id=principal.workspace_id,
            ) from exc

    def _deny(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        action: str,
        target: str,
        code: str,
        details: dict[str, object] | None = None,
    ) -> NoReturn:
        audit = self._record_read_audit(
            principal=principal,
            action=action,
            target=target,
            params={"request_id": request_id, "resource_requested": True},
            result="denied",
            error_code=code,
            evidence_refs={"denial_reason": (details or {}).get("reason")},
        )
        raise AcceptanceReadDenial(
            code,
            details=details,
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

    def _authorize_case(
        self,
        *,
        quality_case: QualityCase,
        principal: AcceptedPrincipalContext,
        principal_row: PublicPrincipal,
        request_id: str,
        action: str,
    ) -> None:
        if (
            quality_case.project_id is not None
            and quality_case.project_id not in (principal_row.project_ids or [])
        ) or (
            quality_case.environment_id is not None
            and quality_case.environment_id not in (principal_row.environment_ids or [])
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{quality_case.case_id}",
            )

    def _load_exact_case(
        self,
        *,
        principal: AcceptedPrincipalContext,
        case_id: str,
        case_revision: int,
        case_digest: str,
        request_id: str,
        action: str = _PROPOSE_INTENT,
        lock: bool = False,
    ) -> QualityCase:
        statement = select(QualityCase).where(
            QualityCase.workspace_id == principal.workspace_id,
            QualityCase.case_id == case_id,
        )
        if lock:
            statement = statement.with_for_update()
        quality_case = self.session.scalar(statement)
        if quality_case is None or quality_case.workspace_id != principal.workspace_id:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
            )
        assert quality_case is not None
        try:
            snapshot = quality_case.snapshot_payload
            assert_record_digest(snapshot, self_digest_field="record_digest")
        except (V4IntegrityError, TypeError, ValueError) as exc:
            raise AcceptanceError("INTERNAL_ERROR") from exc
        if (
            quality_case.revision != case_revision
            or quality_case.record_digest != case_digest
            or snapshot.get("record_digest") != case_digest
            or snapshot.get("case_id") != quality_case.case_id
            or snapshot.get("workspace_id") != quality_case.workspace_id
            or snapshot.get("revision") != quality_case.revision
            or snapshot.get("project_id") != quality_case.project_id
            or snapshot.get("environment_id") != quality_case.environment_id
            or snapshot.get("authority_receipt_id")
            != quality_case.authority_receipt_id
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
            )
        return quality_case

    # --------------------------------------------------------------- propose

    def propose(
        self,
        request: AcceptanceCriteriaProposeRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> AcceptanceCriteriaProposeResponse:
        request_id = request_id or new_request_id()
        target = f"quality_case:{request.case_id}"
        principal_row = self._validate_principal_row(principal)
        if principal.principal_type not in _PROPOSE_PRINCIPAL_TYPES:
            self._deny(
                principal=principal,
                request_id=request_id,
                action=_PROPOSE_INTENT,
                target=target,
                code="SCOPE_FORBIDDEN",
                details={"reason": "PROPOSER_PRINCIPAL_TYPE_FORBIDDEN"},
            )
        self._require_scope(
            principal=principal,
            required_scope=_PROPOSE_SCOPE,
            request_id=request_id,
            action=_PROPOSE_INTENT,
            target=target,
        )
        quality_case = self._load_exact_case(
            principal=principal,
            case_id=request.case_id,
            case_revision=request.case_revision,
            case_digest=request.case_digest,
            request_id=request_id,
            action=_PROPOSE_INTENT,
            lock=True,
        )
        self._authorize_case(
            quality_case=quality_case,
            principal=principal,
            principal_row=principal_row,
            request_id=request_id,
            action=_PROPOSE_INTENT,
        )
        body = request.model_dump(mode="json")
        request_fingerprint = self.idempotency.fingerprint(body)
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=_PROPOSE_INTENT,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                verify_terminal=PublicIdempotencyService.verify_terminal_presence,
            )
        except PublicIdempotencyError as exc:
            if exc.code == "IDEMPOTENCY_CONFLICT":
                self._deny(
                    principal=principal,
                    request_id=request_id,
                    action=_PROPOSE_INTENT,
                    target=target,
                    code=exc.code,
                    details={
                        "reason": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"
                    },
                )
            raise AcceptanceError(exc.code) from exc
        if lookup.record is not None:
            try:
                response = self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=AcceptanceCriteriaProposeResponse,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind="acceptance_criteria_revision",
                    resource_field="acceptance_criteria_revision",
                    resource_id_field="acceptance_criteria_revision_id",
                )
                replay_row = self.session.get(
                    AcceptanceCriteriaRevision,
                    response.acceptance_criteria_revision.acceptance_criteria_revision_id,
                )
                if replay_row is None:
                    raise AcceptanceError("INTERNAL_ERROR")
                verified = self._verified_revision_envelope(replay_row)
                if (
                    response.acceptance_criteria_revision.model_dump(mode="json")
                    != verified
                    or replay_row.case_id != request.case_id
                    or replay_row.case_revision != request.case_revision
                    or replay_row.case_digest != request.case_digest
                    or replay_row.confirmation_status != "PROPOSED"
                ):
                    raise AcceptanceError("INTERNAL_ERROR")
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise AcceptanceError(exc.code) from exc
        now = _as_utc(self.clock())
        return self._write_revision(
            intent=_PROPOSE_INTENT,
            event_type="acceptance_criteria.proposed",
            case_id=request.case_id,
            case_revision=request.case_revision,
            case_digest=request.case_digest,
            confirmation_status="PROPOSED",
            proposer_principal=principal.principal_id,
            proposed_at=now,
            confirmer_principal=None,
            confirmed_at=None,
            exact_previous_proposed_revision_binding=None,
            reauthentication_credential_binding=None,
            confirmation_note=None,
            acceptance_source=dict(request.acceptance_source),
            reproducer_input=(
                dict(request.reproducer_input)
                if request.reproducer_input is not None
                else None
            ),
            reproducer_environment=(
                dict(request.reproducer_environment)
                if request.reproducer_environment is not None
                else None
            ),
            expected_behavior=dict(request.expected_behavior),
            oracle_or_evaluator=(
                dict(request.oracle_or_evaluator)
                if request.oracle_or_evaluator is not None
                else None
            ),
            applicable_workload_profile=dict(request.applicable_workload_profile),
            applicable_deployment_profile=dict(request.applicable_deployment_profile),
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            now=now,
        )

    # --------------------------------------------------------------- confirm

    def _fresh_credential_binding(
        self,
        *,
        principal: AcceptedPrincipalContext,
        proposed: AcceptanceCriteriaRevision,
        request_id: str,
    ) -> dict[str, Any]:
        credential = self.session.get(PublicCredential, principal.credential_id)
        credential_matches = (
            credential is not None
            and credential.workspace_id == principal.workspace_id
            and credential.principal_id == principal.principal_id
            and credential.state == "ACTIVE"
            and credential.revoked_at is None
            and credential.subject == principal.subject
            and credential.issuer.rstrip("/") == str(principal.issuer).rstrip("/")
            and credential.jti_digest == principal.jti_digest
            and credential.claims_digest == principal.claims_digest
            and credential.audiences == principal.audiences
            and credential.project_ids == principal.project_ids
            and credential.environment_ids == principal.environment_ids
            and credential.scopes == principal.scopes
            and _as_utc(credential.issued_at) == _as_utc(principal.issued_at)
            and _as_utc(credential.not_before) == _as_utc(principal.not_before)
            and _as_utc(credential.expires_at) == _as_utc(principal.expires_at)
            and _as_utc(credential.not_before)
            <= _as_utc(principal.evaluated_at)
            < _as_utc(credential.expires_at)
        )
        if not credential_matches:
            self._deny(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target=(
                    "acceptance_criteria_revision:"
                    f"{proposed.acceptance_criteria_revision_id}"
                ),
                code="VALIDATION_FAILED",
                details={"reason": "REAUTHENTICATION_CREDENTIAL_BINDING_INVALID"},
            )
        assert credential is not None
        if _as_utc(credential.issued_at) <= _as_utc(proposed.proposed_at):
            self._deny(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target=(
                    "acceptance_criteria_revision:"
                    f"{proposed.acceptance_criteria_revision_id}"
                ),
                code="VALIDATION_FAILED",
                details={"reason": "REAUTHENTICATION_REQUIRED"},
            )
        core = {
            "kind": "PUBLIC_CREDENTIAL",
            "credential_id": credential.credential_id,
            "principal_id": credential.principal_id,
            "jti_digest": credential.jti_digest,
            "claims_digest": credential.claims_digest,
            "issued_at": _wire_time(credential.issued_at),
        }
        return {**core, "binding_digest": canonical_digest(core)}

    def confirm(
        self,
        request: AcceptanceCriteriaConfirmRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
        expected_proposed_revision_id: str | None = None,
    ) -> AcceptanceCriteriaConfirmResponse:
        request_id = request_id or new_request_id()
        exact_binding = request.exact_proposed_revision_binding.model_dump(mode="json")
        target = f"acceptance_criteria_revision:{exact_binding['id']}"
        principal_row = self._validate_principal_row(principal)
        if (
            expected_proposed_revision_id is not None
            and exact_binding["id"] != expected_proposed_revision_id
        ):
            self._deny(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target=target,
                code="VALIDATION_FAILED",
                details={"reason": "PATH_BODY_REVISION_ID_MISMATCH"},
            )
        if principal.principal_type not in _CONFIRM_PRINCIPAL_TYPES:
            self._deny(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target=target,
                code="SCOPE_FORBIDDEN",
                details={"reason": "CONFIRM_HUMAN_ONLY"},
            )
        self._require_scope(
            principal=principal,
            required_scope=_CONFIRM_SCOPE,
            request_id=request_id,
            action=_CONFIRM_INTENT,
            target=target,
        )
        if not _CONFIRM_TRUST_ROLES.intersection(principal_row.trust_roles or []):
            self._deny(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target=target,
                code="SCOPE_FORBIDDEN",
                details={
                    "reason": "CONFIRM_REQUIRES_MAINTAINER_OR_DOMAIN_REVIEWER"
                },
            )
        proposed = self._load_proposed_revision(
            exact_binding=exact_binding,
            principal=principal,
            request_id=request_id,
            lock=True,
        )
        envelope = self._verified_revision_envelope(proposed)
        exact_case = envelope["exact_case_binding"]
        quality_case = self._load_exact_case(
            principal=principal,
            case_id=exact_case["case_id"],
            case_revision=exact_case["case_revision"],
            case_digest=exact_case["case_digest"],
            request_id=request_id,
            action=_CONFIRM_INTENT,
            lock=True,
        )
        self._authorize_case(
            quality_case=quality_case,
            principal=principal,
            principal_row=principal_row,
            request_id=request_id,
            action=_CONFIRM_INTENT,
        )
        if proposed.proposer_principal == principal.principal_id:
            self._deny(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target=target,
                code="VALIDATION_FAILED",
                details={"reason": "PROPOSER_CANNOT_SELF_CONFIRM"},
            )
        credential_binding = self._fresh_credential_binding(
            principal=principal,
            proposed=proposed,
            request_id=request_id,
        )

        body = request.model_dump(mode="json")
        request_fingerprint = self.idempotency.fingerprint(body)
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=_CONFIRM_INTENT,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                verify_terminal=PublicIdempotencyService.verify_terminal_presence,
            )
        except PublicIdempotencyError as exc:
            if exc.code == "IDEMPOTENCY_CONFLICT":
                self._deny(
                    principal=principal,
                    request_id=request_id,
                    action=_CONFIRM_INTENT,
                    target=target,
                    code=exc.code,
                    details={
                        "reason": "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_REQUEST"
                    },
                )
            raise AcceptanceError(exc.code) from exc
        if lookup.record is not None:
            try:
                response = self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=AcceptanceCriteriaConfirmResponse,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind="acceptance_criteria_revision",
                    resource_field="acceptance_criteria_revision",
                    resource_id_field="acceptance_criteria_revision_id",
                )
                replay_row = self.session.get(
                    AcceptanceCriteriaRevision,
                    response.acceptance_criteria_revision.acceptance_criteria_revision_id,
                )
                if replay_row is None:
                    raise AcceptanceError("INTERNAL_ERROR")
                verified = self._verified_revision_envelope(replay_row)
                if (
                    response.acceptance_criteria_revision.model_dump(mode="json")
                    != verified
                    or replay_row.confirmation_status != "CONFIRMED"
                    or replay_row.exact_previous_proposed_revision_id
                    != proposed.acceptance_criteria_revision_id
                    or replay_row.exact_previous_proposed_revision_digest
                    != proposed.record_digest
                ):
                    raise AcceptanceError("INTERNAL_ERROR")
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise AcceptanceError(exc.code) from exc
        existing_confirmation = self.session.scalar(
            select(AcceptanceCriteriaRevision).where(
                AcceptanceCriteriaRevision.workspace_id == principal.workspace_id,
                AcceptanceCriteriaRevision.confirmation_status == "CONFIRMED",
                AcceptanceCriteriaRevision.exact_previous_proposed_revision_id
                == proposed.acceptance_criteria_revision_id,
            )
        )
        if existing_confirmation is not None:
            self._deny(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target=target,
                code="CATALOG_CONFLICT",
                details={"reason": "PROPOSAL_ALREADY_CONFIRMED"},
            )
        # ``evaluated_at`` is produced by the server-side credential resolver.
        # Never persist a confirmation timestamp before the fresh credential
        # was evaluated, even when an injected/test clock lags that resolver.
        now = max(_as_utc(self.clock()), _as_utc(principal.evaluated_at))
        return self._write_revision(
            intent=_CONFIRM_INTENT,
            event_type="acceptance_criteria.confirmed",
            case_id=envelope["exact_case_binding"]["case_id"],
            case_revision=envelope["exact_case_binding"]["case_revision"],
            case_digest=envelope["exact_case_binding"]["case_digest"],
            confirmation_status="CONFIRMED",
            proposer_principal=proposed.proposer_principal,
            proposed_at=_as_utc(proposed.proposed_at),
            confirmer_principal=principal.principal_id,
            confirmed_at=now,
            exact_previous_proposed_revision_binding={
                "kind": "ACCEPTANCE_CRITERIA_REVISION",
                "id": proposed.acceptance_criteria_revision_id,
                "revision": 1,
                "digest": proposed.record_digest,
            },
            reauthentication_credential_binding=credential_binding,
            confirmation_note=request.confirmation_note,
            acceptance_source=envelope["acceptance_source"],
            reproducer_input=envelope.get("reproducer_input"),
            reproducer_environment=envelope.get("reproducer_environment"),
            expected_behavior=envelope["expected_behavior"],
            oracle_or_evaluator=envelope.get("oracle_or_evaluator"),
            applicable_workload_profile=envelope["applicable_workload_profile"],
            applicable_deployment_profile=envelope["applicable_deployment_profile"],
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            now=now,
        )

    def _load_proposed_revision(
        self,
        *,
        exact_binding: dict[str, Any],
        principal: AcceptedPrincipalContext,
        request_id: str,
        lock: bool = False,
    ) -> AcceptanceCriteriaRevision:
        if (
            not isinstance(exact_binding, dict)
            or set(exact_binding) != {"kind", "id", "revision", "digest"}
            or exact_binding.get("kind") != "ACCEPTANCE_CRITERIA_REVISION"
            or exact_binding.get("revision") != 1
            or not isinstance(exact_binding.get("id"), str)
        ):
            self._deny(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target="acceptance_criteria_revision:invalid",
                code="VALIDATION_FAILED",
                details={"reason": "PROPOSED_REVISION_BINDING_INVALID"},
            )
        statement = select(AcceptanceCriteriaRevision).where(
            AcceptanceCriteriaRevision.workspace_id == principal.workspace_id,
            AcceptanceCriteriaRevision.acceptance_criteria_revision_id
            == exact_binding["id"],
        )
        if lock:
            statement = statement.with_for_update()
        revision = self.session.scalar(statement)
        if (
            revision is None
            or revision.workspace_id != principal.workspace_id
            or revision.confirmation_status != "PROPOSED"
            or revision.revision != exact_binding.get("revision")
            or revision.record_digest != exact_binding.get("digest")
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target=f"acceptance_criteria_revision:{exact_binding.get('id')}",
            )
        assert revision is not None
        self._verified_revision_envelope(revision)
        return revision

    # -------------------------------------------------------------- read path

    def get(
        self,
        case_id: str,
        *,
        case_revision: int,
        principal: AcceptedPrincipalContext,
        request_id: str | None = None,
    ) -> AcceptanceCriteriaGetResponse:
        request_id = request_id or new_request_id()
        action = _GET_INTENT
        principal_row = self._validate_principal_row(principal)
        if principal.principal_type not in _PROPOSE_PRINCIPAL_TYPES:
            self._deny(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
                code="SCOPE_FORBIDDEN",
                details={"reason": "READER_PRINCIPAL_TYPE_FORBIDDEN"},
            )
        self._require_scope(
            principal=principal,
            required_scope=_READ_SCOPE,
            request_id=request_id,
            action=action,
            target=f"quality_case:{case_id}",
        )
        current_case = self.session.get(QualityCase, case_id)
        if current_case is None or current_case.workspace_id != principal.workspace_id:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
            )
        assert current_case is not None
        quality_case = self._load_exact_case(
            principal=principal,
            case_id=case_id,
            case_revision=case_revision,
            case_digest=current_case.record_digest,
            request_id=request_id,
            action=action,
        )
        self._authorize_case(
            quality_case=quality_case,
            principal=principal,
            principal_row=principal_row,
            request_id=request_id,
            action=action,
        )
        revisions = list(
            self.session.scalars(
                select(AcceptanceCriteriaRevision)
                .where(
                    AcceptanceCriteriaRevision.workspace_id
                    == principal.workspace_id,
                    AcceptanceCriteriaRevision.case_id == case_id,
                    AcceptanceCriteriaRevision.case_revision == case_revision,
                    AcceptanceCriteriaRevision.case_digest
                    == quality_case.record_digest,
                )
                .order_by(
                    AcceptanceCriteriaRevision.created_at,
                    AcceptanceCriteriaRevision.acceptance_criteria_revision_id,
                )
            ).all()
        )
        exact_case_binding = {
            "case_id": case_id,
            "case_revision": case_revision,
            "case_digest": quality_case.record_digest,
        }
        records: list[dict[str, Any]] = []
        confirmed_records: list[dict[str, Any]] = []
        for revision in revisions:
            record = self._verified_revision_envelope(revision)
            records.append(record)
            if record["confirmation_status"] == "CONFIRMED":
                confirmed_records.append(record)
        # V5-1C has no ResolutionContract record yet.  A correctly confirmed
        # criteria revision is necessary but not sufficient for executable
        # readiness; claiming READY against a pending placeholder would invent
        # an exact authority binding that does not exist until V5-4.
        readiness = "NEEDS_ACCEPTANCE_CRITERIA"
        next_action = (
            {
                "code": "MATERIALIZE_RESOLUTION_CONTRACT",
                "command": None,
                "note": "confirmed acceptance criteria remain non-executable until "
                "V5-4 materializes and exact-binds a ResolutionContract",
            }
            if confirmed_records
            else {
                "code": "CONFIRM_ACCEPTANCE_CRITERIA",
                "command": "case acceptance-criteria confirm",
                "note": "a reauthenticated human maintainer/domain reviewer must "
                "confirm a PROPOSED revision before a gate may start",
            }
        )
        audit = self._record_read_audit(
            principal=principal,
            action=action,
            target=f"quality_case:{case_id}",
            params={
                "request_id": request_id,
                "resource_requested": True,
                "case_revision": case_revision,
            },
            evidence_refs={
                "case_id": case_id,
                "case_revision": case_revision,
                "case_readiness": readiness,
                "revision_count": len(records),
            },
        )
        try:
            return AcceptanceCriteriaGetResponse.model_validate(
                {
                    "schema_version": "2.0",
                    "workspace_id": principal.workspace_id,
                    "request_id": request_id,
                    "audit_ref": audit.audit_ref,
                    "exact_case_binding": exact_case_binding,
                    "case_readiness": readiness,
                    "revisions": records,
                    "next_action": next_action,
                }
            )
        except (TypeError, ValueError) as exc:
            raise AcceptanceError("INTERNAL_ERROR") from exc

    # -------------------------------------------------------------- write core

    def _write_revision(
        self,
        *,
        intent: str,
        event_type: str,
        case_id: str,
        case_revision: int,
        case_digest: str,
        confirmation_status: str,
        proposer_principal: str,
        proposed_at: datetime,
        confirmer_principal: str | None,
        confirmed_at: datetime | None,
        exact_previous_proposed_revision_binding: dict[str, Any] | None,
        reauthentication_credential_binding: dict[str, Any] | None,
        confirmation_note: str | None,
        acceptance_source: dict[str, Any],
        reproducer_input: dict[str, Any] | None,
        reproducer_environment: dict[str, Any] | None,
        expected_behavior: dict[str, Any],
        oracle_or_evaluator: dict[str, Any] | None,
        applicable_workload_profile: dict[str, Any],
        applicable_deployment_profile: dict[str, Any],
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        now: datetime,
    ) -> Any:
        transaction_id = new_transaction_id()
        controller = self._resolve_controller(
            workspace_id=principal.workspace_id,
            event_type=event_type,
            command=intent,
            now=now,
        )
        authority_receipt_id = new_authority_receipt_id()
        revision_id = new_acceptance_criteria_revision_id()
        acceptance_digest = canonical_digest(
            {
                "confirmation_status": confirmation_status,
                "acceptance_source": acceptance_source,
                "reproducer_input": reproducer_input,
                "reproducer_environment": reproducer_environment,
                "expected_behavior": expected_behavior,
                "oracle_or_evaluator": oracle_or_evaluator,
                "applicable_workload_profile": applicable_workload_profile,
                "applicable_deployment_profile": applicable_deployment_profile,
            }
        )
        payload: dict[str, Any] = {
            "acceptance_criteria_revision_id": revision_id,
            "workspace_id": principal.workspace_id,
            "exact_case_binding": {
                "case_id": case_id,
                "case_revision": case_revision,
                "case_digest": case_digest,
            },
            "resolution_contract_binding_status": {
                **_RESOLUTION_CONTRACT_BINDING_STATUS,
                "exact_case_binding": {
                    "case_id": case_id,
                    "case_revision": case_revision,
                    "case_digest": case_digest,
                },
            },
            "confirmation_status": confirmation_status,
            "proposer_principal": proposer_principal,
            "proposed_at": _wire_time(proposed_at),
            "confirmer_principal": confirmer_principal,
            "confirmed_at": _wire_time(confirmed_at) if confirmed_at is not None else None,
            "exact_previous_proposed_revision_binding": (
                exact_previous_proposed_revision_binding
            ),
            "reauthentication_credential_binding": (
                reauthentication_credential_binding
            ),
            "acceptance_source": acceptance_source,
            "reproducer_input": reproducer_input,
            "reproducer_environment": reproducer_environment,
            "expected_behavior": expected_behavior,
            "oracle_or_evaluator": oracle_or_evaluator,
            "applicable_workload_profile": applicable_workload_profile,
            "applicable_deployment_profile": applicable_deployment_profile,
            "acceptance_digest": acceptance_digest,
            "record_envelope": self._envelope(
                workspace_id=principal.workspace_id,
                revision=1,
                recorded_by_principal=principal.principal_id,
                recorded_at=now,
                authority_receipt_id=authority_receipt_id,
            ),
        }
        envelope = payload["record_envelope"]
        digest = v5_record_digest(payload)
        envelope["record_digest"] = digest

        row = AcceptanceCriteriaRevision(
            acceptance_criteria_revision_id=revision_id,
            workspace_id=principal.workspace_id,
            case_id=case_id,
            case_revision=case_revision,
            case_digest=case_digest,
            revision=1,
            resolution_contract_binding_status=payload[
                "resolution_contract_binding_status"
            ],
            confirmation_status=confirmation_status,
            proposer_principal=proposer_principal,
            proposed_at=_as_utc(proposed_at),
            confirmer_principal=confirmer_principal,
            confirmed_at=_as_utc(confirmed_at) if confirmed_at is not None else None,
            exact_previous_proposed_revision_binding=(
                exact_previous_proposed_revision_binding
            ),
            exact_previous_proposed_revision_id=(
                exact_previous_proposed_revision_binding["id"]
                if exact_previous_proposed_revision_binding is not None
                else None
            ),
            exact_previous_proposed_revision_digest=(
                exact_previous_proposed_revision_binding["digest"]
                if exact_previous_proposed_revision_binding is not None
                else None
            ),
            reauthentication_credential_binding=reauthentication_credential_binding,
            acceptance_source=acceptance_source,
            reproducer_input=reproducer_input,
            reproducer_environment=reproducer_environment,
            expected_behavior=expected_behavior,
            oracle_or_evaluator=oracle_or_evaluator,
            applicable_workload_profile=applicable_workload_profile,
            applicable_deployment_profile=applicable_deployment_profile,
            acceptance_digest=acceptance_digest,
            envelope_payload=payload,
            record_digest=digest,
            authority_receipt_id=authority_receipt_id,
            recorded_by_principal=principal.principal_id,
            created_at=now,
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            if confirmation_status == "CONFIRMED":
                self._deny(
                    principal=principal,
                    request_id=request_id,
                    action=intent,
                    target=(
                        "acceptance_criteria_revision:"
                        f"{exact_previous_proposed_revision_binding['id']}"
                    ),
                    code="CATALOG_CONFLICT",
                    details={"reason": "PROPOSAL_ALREADY_CONFIRMED"},
                )
            raise AcceptanceError("CATALOG_CONFLICT") from exc

        event_business: dict[str, Any] = {
            "exact_acceptance_criteria_revision_binding": {
                "kind": "ACCEPTANCE_CRITERIA_REVISION",
                "id": revision_id,
                "revision": 1,
                "digest": digest,
            },
            "exact_case_binding": payload["exact_case_binding"],
            "resolution_contract_binding_status": payload[
                "resolution_contract_binding_status"
            ],
            "confirmation_status": confirmation_status,
            "acceptance_source": acceptance_source,
            "expected_behavior": expected_behavior,
            "applicable_workload_profile": applicable_workload_profile,
            "applicable_deployment_profile": applicable_deployment_profile,
            "acceptance_digest": acceptance_digest,
        }
        if event_type == "acceptance_criteria.proposed":
            event_business["proposer_principal"] = proposer_principal
            event_business["proposed_at"] = _wire_time(proposed_at)
        else:
            assert confirmer_principal is not None and confirmed_at is not None
            event_business["exact_previous_proposed_revision_binding"] = (
                exact_previous_proposed_revision_binding
            )
            event_business["confirmer_principal"] = confirmer_principal
            event_business["confirmed_at"] = _wire_time(confirmed_at)
        event_payload: dict[str, Any] = {
            **event_business,
            "subject_kind": "ACCEPTANCE_CRITERIA_REVISION",
            "subject_id": revision_id,
            "subject_revision": 1,
            "subject_digest": digest,
            "authority_receipt_id": authority_receipt_id,
        }
        try:
            event = self.events.append_event(
                workspace_id=principal.workspace_id,
                aggregate_type="acceptance_criteria_revision",
                aggregate_id=revision_id,
                event_type=event_type,
                payload=event_payload,
                causation_id=request_id,
                correlation_id=case_id,
                actor_principal=controller.controller_principal,
                transaction_id=transaction_id,
                occurred_at=now,
            )
            audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=controller.controller_principal,
                action=f"controller.{event_type}",
                target=revision_id,
                params={"command": intent},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "subject_kind": "ACCEPTANCE_CRITERIA_REVISION",
                    "subject_id": revision_id,
                    "subject_revision": 1,
                    "subject_digest": digest,
                    "event_id": event.event_id,
                },
                occurred_at=now,
            )
            self.authority.record_receipt(
                resolved=controller,
                authority_receipt_id=authority_receipt_id,
                workspace_id=principal.workspace_id,
                subject_id=revision_id,
                subject_revision=1,
                subject_digest=digest,
                event_id=event.event_id,
                transaction_id=transaction_id,
                audit_ref=audit.audit_ref,
                recorded_at=now,
            )
        except (V4EventStoreError, V5AuthorityError) as exc:
            raise AcceptanceError("INTERNAL_ERROR") from exc
        except V4AuditUnavailable as exc:
            raise AcceptanceError("AUDIT_UNAVAILABLE") from exc

        command_audit_params: dict[str, object] = {
            "request_fingerprint": request_fingerprint
        }
        if confirmation_note is not None:
            command_audit_params["confirmation_note"] = confirmation_note
        try:
            command_audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=intent,
                target=revision_id,
                params=command_audit_params,
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "resource_kind": "acceptance_criteria_revision",
                    "resource_id": revision_id,
                    "record_digest": digest,
                },
                occurred_at=now + timedelta(microseconds=1),
            )
        except V4AuditUnavailable as exc:
            raise AcceptanceError("AUDIT_UNAVAILABLE") from exc
        return self._persist_response(
            intent=intent,
            response_model=(
                AcceptanceCriteriaProposeResponse
                if event_type == "acceptance_criteria.proposed"
                else AcceptanceCriteriaConfirmResponse
            ),
            revision_id=revision_id,
            envelope=payload,
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            audit_ref=command_audit.audit_ref,
            completed_at=now + timedelta(microseconds=1),
        )

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

    def _verified_revision_envelope(
        self, revision: AcceptanceCriteriaRevision
    ) -> dict[str, Any]:
        envelope = revision.envelope_payload
        if not isinstance(envelope, dict):
            raise AcceptanceError("INTERNAL_ERROR")
        try:
            verified = assert_v5_record_digest(envelope)
        except V4IntegrityError as exc:
            raise AcceptanceError("INTERNAL_ERROR") from exc
        record_envelope = envelope.get("record_envelope")
        exact_case_binding = {
            "case_id": revision.case_id,
            "case_revision": revision.case_revision,
            "case_digest": revision.case_digest,
        }
        expected_acceptance_digest = canonical_digest(
            {
                "confirmation_status": revision.confirmation_status,
                "acceptance_source": revision.acceptance_source,
                "reproducer_input": revision.reproducer_input,
                "reproducer_environment": revision.reproducer_environment,
                "expected_behavior": revision.expected_behavior,
                "oracle_or_evaluator": revision.oracle_or_evaluator,
                "applicable_workload_profile": revision.applicable_workload_profile,
                "applicable_deployment_profile": (
                    revision.applicable_deployment_profile
                ),
            }
        )
        expected_resolution_status = {
            **_RESOLUTION_CONTRACT_BINDING_STATUS,
            "exact_case_binding": exact_case_binding,
        }
        previous_binding = revision.exact_previous_proposed_revision_binding
        reauth_binding = revision.reauthentication_credential_binding
        if (
            verified != revision.record_digest
            or revision.revision != 1
            or envelope.get("acceptance_criteria_revision_id")
            != revision.acceptance_criteria_revision_id
            or envelope.get("workspace_id") != revision.workspace_id
            or envelope.get("exact_case_binding") != exact_case_binding
            or revision.resolution_contract_binding_status
            != expected_resolution_status
            or envelope.get("resolution_contract_binding_status")
            != revision.resolution_contract_binding_status
            or envelope.get("confirmation_status") != revision.confirmation_status
            or envelope.get("proposer_principal") != revision.proposer_principal
            or envelope.get("proposed_at") != _wire_time(revision.proposed_at)
            or envelope.get("confirmer_principal") != revision.confirmer_principal
            or envelope.get("confirmed_at")
            != (
                _wire_time(revision.confirmed_at)
                if revision.confirmed_at is not None
                else None
            )
            or envelope.get("exact_previous_proposed_revision_binding")
            != previous_binding
            or envelope.get("reauthentication_credential_binding") != reauth_binding
            or envelope.get("acceptance_source") != revision.acceptance_source
            or envelope.get("reproducer_input") != revision.reproducer_input
            or envelope.get("reproducer_environment")
            != revision.reproducer_environment
            or envelope.get("expected_behavior") != revision.expected_behavior
            or envelope.get("oracle_or_evaluator") != revision.oracle_or_evaluator
            or envelope.get("applicable_workload_profile")
            != revision.applicable_workload_profile
            or envelope.get("applicable_deployment_profile")
            != revision.applicable_deployment_profile
            or envelope.get("acceptance_digest") != revision.acceptance_digest
            or revision.acceptance_digest != expected_acceptance_digest
            or not isinstance(record_envelope, dict)
            or record_envelope.get("workspace_id") != revision.workspace_id
            or record_envelope.get("revision") != revision.revision
            or record_envelope.get("record_digest") != revision.record_digest
            or record_envelope.get("authority_receipt_id")
            != revision.authority_receipt_id
            or record_envelope.get("recorded_by_principal")
            != revision.recorded_by_principal
        ):
            raise AcceptanceError("INTERNAL_ERROR")

        if revision.confirmation_status == "PROPOSED":
            if (
                revision.confirmer_principal is not None
                or revision.confirmed_at is not None
                or previous_binding is not None
                or revision.exact_previous_proposed_revision_id is not None
                or revision.exact_previous_proposed_revision_digest is not None
                or reauth_binding is not None
            ):
                raise AcceptanceError("INTERNAL_ERROR")
        elif revision.confirmation_status == "CONFIRMED":
            previous = (
                self.session.get(
                    AcceptanceCriteriaRevision,
                    revision.exact_previous_proposed_revision_id,
                )
                if revision.exact_previous_proposed_revision_id is not None
                else None
            )
            expected_previous_binding = {
                "kind": "ACCEPTANCE_CRITERIA_REVISION",
                "id": revision.exact_previous_proposed_revision_id,
                "revision": 1,
                "digest": revision.exact_previous_proposed_revision_digest,
            }
            if (
                revision.confirmer_principal is None
                or revision.confirmed_at is None
                or previous_binding != expected_previous_binding
                or previous is None
                or previous.workspace_id != revision.workspace_id
                or previous.case_id != revision.case_id
                or previous.case_revision != revision.case_revision
                or previous.case_digest != revision.case_digest
                or previous.revision != 1
                or previous.confirmation_status != "PROPOSED"
                or previous.record_digest
                != revision.exact_previous_proposed_revision_digest
                or previous.proposer_principal != revision.proposer_principal
                or reauth_binding is None
            ):
                raise AcceptanceError("INTERNAL_ERROR")
            self._verified_revision_envelope(previous)
            credential_id = reauth_binding.get("credential_id")
            credential = (
                self.session.get(PublicCredential, credential_id)
                if isinstance(credential_id, str)
                else None
            )
            reauth_core = {
                key: reauth_binding.get(key)
                for key in (
                    "kind",
                    "credential_id",
                    "principal_id",
                    "jti_digest",
                    "claims_digest",
                    "issued_at",
                )
            }
            if (
                credential is None
                or reauth_binding.get("kind") != "PUBLIC_CREDENTIAL"
                or set(reauth_binding) != {*reauth_core, "binding_digest"}
                or reauth_binding.get("principal_id") != revision.confirmer_principal
                or credential.workspace_id != revision.workspace_id
                or credential.principal_id != revision.confirmer_principal
                or credential.jti_digest != reauth_binding.get("jti_digest")
                or credential.claims_digest != reauth_binding.get("claims_digest")
                or _wire_time(credential.issued_at) != reauth_binding.get("issued_at")
                or _as_utc(credential.issued_at) <= _as_utc(revision.proposed_at)
                or reauth_binding.get("binding_digest")
                != canonical_digest(reauth_core)
            ):
                raise AcceptanceError("INTERNAL_ERROR")
        else:
            raise AcceptanceError("INTERNAL_ERROR")
        try:
            self.authority.validate_receipt_binding(
                authority_receipt_id=revision.authority_receipt_id,
                workspace_id=revision.workspace_id,
                subject_kind="ACCEPTANCE_CRITERIA_REVISION",
                subject_id=revision.acceptance_criteria_revision_id,
                subject_revision=revision.revision,
                subject_digest=revision.record_digest,
            )
            validated = AcceptanceCriteriaRevisionRecord.model_validate(envelope)
        except (V5AuthorityError, TypeError, ValueError) as exc:
            raise AcceptanceError("INTERNAL_ERROR") from exc
        return validated.model_dump(mode="json")

    def _resolve_controller(
        self,
        *,
        workspace_id: str,
        event_type: str,
        command: str,
        now: datetime,
    ) -> V5ResolvedController:
        try:
            return self.authority.resolve_controller(
                workspace_id=workspace_id,
                subject_kind="ACCEPTANCE_CRITERIA_REVISION",
                command=command,
                event_type=event_type,
                recorded_at=now,
            )
        except V5AuthorityError as exc:
            raise AcceptanceError("INTERNAL_ERROR") from exc

    # -------------------------------------------------------- response core

    def _persist_response(
        self,
        *,
        intent: str,
        response_model: type[Any],
        revision_id: str,
        envelope: dict[str, Any],
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        audit_ref: str,
        completed_at: datetime,
    ) -> Any:
        core: dict[str, Any] = {
            "schema_version": "2.0",
            "workspace_id": principal.workspace_id,
            "request_id": request_id,
            "audit_ref": audit_ref,
            "acceptance_criteria_revision": envelope,
        }
        response_digest = canonical_digest(core)
        receipt_id = new_idempotency_receipt_id()
        receipt: dict[str, Any] = {
            "schema_version": "1.0",
            "workspace_id": principal.workspace_id,
            "principal_id": principal.principal_id,
            "intent": intent,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "resource": {
                "kind": "acceptance_criteria_revision",
                "id": revision_id,
            },
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
                intent=intent,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                resource_kind="acceptance_criteria_revision",
                resource_id=revision_id,
                request_id=request_id,
                audit_ref=audit_ref,
                response_payload=core,
                response_digest=response_digest,
                receipt_payload=receipt,
                receipt_digest=receipt_digest,
                idempotency_receipt_id=receipt_id,
                completed_at=completed_at,
                response_model=response_model,
                receipt_model=V5IdempotencyReceipt,
                resource_field="acceptance_criteria_revision",
                resource_id_field="acceptance_criteria_revision_id",
            )
        except PublicIdempotencyError as exc:
            raise AcceptanceError(exc.code) from exc
        return response_model.model_validate(
            {**core, "idempotency": {"receipt": receipt, "replayed": False}}
        )


__all__ = [
    "AcceptanceError",
    "AcceptanceReadDenial",
    "AcceptanceService",
]
