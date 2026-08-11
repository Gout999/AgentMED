"""V5-1C acceptance-criteria propose/get/confirm runtime and CaseReadiness.

A PROPOSED acceptance revision is an untrusted draft recorded by any
case-authorized actor (agents may propose).  Only a reauthenticated human
maintainer/domain reviewer may confirm, producing a NEW immutable CONFIRMED
revision that references the prior proposal; the proposal itself never
confirms itself and no record is ever rewritten in place.  CaseReadiness is a
pure projection: a case with a confirmed revision for its exact binding is
READY, otherwise NEEDS_ACCEPTANCE_CRITERIA — the S1A case payload/digest is
never touched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v4_tables import PublicPrincipal, QualityCase
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
_PROPOSE_PRINCIPAL_TYPES = frozenset(
    {"human", "external_agent", "service", "connector"}
)

# The frozen contract names the confirmer as a human maintainer/domain
# reviewer.  The current runtime has no server-side trust-role registry, so
# that designation is carried by the ``acceptance_criteria:confirm`` scope,
# granted only at credential registration by a human authority.  Reauthentication
# means the confirming credential must have been issued after the proposal was
# recorded (documented as honest uncertainty in evidence/v5/stage-1).
_RESOLUTION_CONTRACT_BINDING = {
    "kind": "RESOLUTION_CONTRACT",
    "revision": None,
    "digest": None,
    "materialization": "DECLARED_BY_CASE",
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
            raise ValueError("acceptance read denials support only non-mutating codes")
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
            raise AcceptanceError("TOKEN_INVALID")

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
        audit = self._record_read_audit(
            principal=principal,
            action=action,
            target=target,
            params={"request_id": request_id, "resource_requested": True},
            result="denied",
            error_code="RESOURCE_NOT_FOUND",
        )
        raise AcceptanceReadDenial(
            "RESOURCE_NOT_FOUND",
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
            raise AcceptanceError(
                "AUDIT_UNAVAILABLE",
                workspace_id=principal.workspace_id,
                rollback_required=True,
            ) from exc

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
                action=_PROPOSE_INTENT,
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
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=_PROPOSE_INTENT,
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
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise AcceptanceError(exc.code) from exc

        self._validate_principal_row(principal)
        if principal.principal_type not in _PROPOSE_PRINCIPAL_TYPES:
            raise AcceptanceError("SCOPE_FORBIDDEN", workspace_id=principal.workspace_id)
        if (
            principal.requested_context.required_scope != _PROPOSE_SCOPE
            or _PROPOSE_SCOPE not in principal.scopes
        ):
            raise AcceptanceError("SCOPE_FORBIDDEN", workspace_id=principal.workspace_id)
        self._load_exact_case(
            principal=principal,
            case_id=request.case_id,
            case_revision=request.case_revision,
            case_digest=request.case_digest,
            request_id=request_id,
        )
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

    def confirm(
        self,
        request: AcceptanceCriteriaConfirmRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> AcceptanceCriteriaConfirmResponse:
        request_id = request_id or new_request_id()
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
                return response  # type: ignore[return-value]
            except PublicIdempotencyError as exc:
                raise AcceptanceError(exc.code) from exc

        self._validate_principal_row(principal)
        if principal.principal_type not in _CONFIRM_PRINCIPAL_TYPES:
            raise AcceptanceError(
                "SCOPE_FORBIDDEN",
                details={"reason": "CONFIRM_HUMAN_ONLY"},
                workspace_id=principal.workspace_id,
            )
        if (
            principal.requested_context.required_scope != _CONFIRM_SCOPE
            or _CONFIRM_SCOPE not in principal.scopes
        ):
            raise AcceptanceError(
                "SCOPE_FORBIDDEN",
                details={"reason": "CONFIRM_REQUIRES_MAINTAINER_OR_DOMAIN_REVIEWER"},
                workspace_id=principal.workspace_id,
            )
        proposed = self._load_proposed_revision(
            exact_binding=request.exact_proposed_revision_binding,
            principal=principal,
            request_id=request_id,
        )
        # Reauthentication: the confirming credential must have been issued
        # after the proposal was recorded (fresh authentication, not the same
        # session that created the draft).
        proposed_at = _as_utc(proposed.proposed_at)
        if _as_utc(principal.issued_at) < proposed_at:
            audit = self._record_read_audit(
                principal=principal,
                action=_CONFIRM_INTENT,
                target=f"acceptance_criteria_revision:{proposed.acceptance_criteria_revision_id}",
                params={"request_id": request_id, "resource_requested": True},
                result="denied",
                error_code="VALIDATION_FAILED",
            )
            raise AcceptanceError(
                "VALIDATION_FAILED",
                details={"reason": "REAUTHENTICATION_REQUIRED"},
                audit_ref=audit.audit_ref,
                workspace_id=principal.workspace_id,
            )
        # An Agent proposal can never confirm itself: the confirmer must be a
        # different principal than the proposer.
        if proposed.proposer_principal == principal.principal_id:
            audit = self._record_read_audit(
                principal=principal,
                action=_CONFIRM_INTENT,
                target=f"acceptance_criteria_revision:{proposed.acceptance_criteria_revision_id}",
                params={"request_id": request_id, "resource_requested": True},
                result="denied",
                error_code="VALIDATION_FAILED",
            )
            raise AcceptanceError(
                "VALIDATION_FAILED",
                details={"reason": "PROPOSER_CANNOT_SELF_CONFIRM"},
                audit_ref=audit.audit_ref,
                workspace_id=principal.workspace_id,
            )
        envelope = self._verified_revision_envelope(proposed)
        now = _as_utc(self.clock())
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
                "revision": None,
                "digest": proposed.record_digest,
            },
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
    ) -> AcceptanceCriteriaRevision:
        if not isinstance(exact_binding, dict) or not isinstance(
            exact_binding.get("id"), str
        ):
            raise AcceptanceError(
                "VALIDATION_FAILED",
                details={"reason": "PROPOSED_REVISION_BINDING_INVALID"},
                workspace_id=principal.workspace_id,
            )
        revision = self.session.get(
            AcceptanceCriteriaRevision, exact_binding["id"]
        )
        if (
            revision is None
            or revision.workspace_id != principal.workspace_id
            or revision.confirmation_status != "PROPOSED"
            or revision.record_digest != exact_binding.get("digest")
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=_CONFIRM_INTENT,
                target=f"acceptance_criteria_revision:{exact_binding.get('id')}",
            )
        assert revision is not None
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
        self._validate_principal_row(principal)
        if principal.principal_type not in _PROPOSE_PRINCIPAL_TYPES:
            raise AcceptanceError("SCOPE_FORBIDDEN", workspace_id=principal.workspace_id)
        self._require_scope(
            principal=principal,
            required_scope=_READ_SCOPE,
            request_id=request_id,
            action=action,
            target=f"quality_case:{case_id}",
        )
        revisions = list(
            self.session.scalars(
                select(AcceptanceCriteriaRevision)
                .where(
                    AcceptanceCriteriaRevision.workspace_id
                    == principal.workspace_id,
                    AcceptanceCriteriaRevision.case_id == case_id,
                    AcceptanceCriteriaRevision.case_revision == case_revision,
                )
                .order_by(
                    AcceptanceCriteriaRevision.created_at,
                    AcceptanceCriteriaRevision.acceptance_criteria_revision_id,
                )
            ).all()
        )
        quality_case = self.session.get(QualityCase, case_id)
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
        if quality_case.revision != case_revision:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
            )
        exact_case_binding = {
            "case_id": case_id,
            "case_revision": case_revision,
            "case_digest": quality_case.record_digest,
        }
        confirmed = [
            revision for revision in revisions if revision.confirmation_status == "CONFIRMED"
        ]
        readiness = "READY" if confirmed else "NEEDS_ACCEPTANCE_CRITERIA"
        next_action = (
            None
            if readiness == "READY"
            else {
                "code": "CONFIRM_ACCEPTANCE_CRITERIA",
                "command": "case acceptance-criteria confirm",
                "note": "a reauthenticated human maintainer/domain reviewer must "
                "confirm a PROPOSED revision before a gate may start",
            }
        )
        records: list[dict[str, Any]] = []
        for revision in revisions:
            try:
                records.append(self._verified_revision_envelope(revision))
            except (V4IntegrityError, TypeError, ValueError) as exc:
                raise AcceptanceError("INTERNAL_ERROR") from exc
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
            "exact_resolution_contract_binding": {
                **_RESOLUTION_CONTRACT_BINDING,
                "case_binding": {
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
            exact_resolution_contract_binding=payload[
                "exact_resolution_contract_binding"
            ],
            confirmation_status=confirmation_status,
            proposer_principal=proposer_principal,
            proposed_at=_as_utc(proposed_at),
            confirmer_principal=confirmer_principal,
            confirmed_at=_as_utc(confirmed_at) if confirmed_at is not None else None,
            exact_previous_proposed_revision_binding=(
                exact_previous_proposed_revision_binding
            ),
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
        self.session.add(row)
        self.session.flush()

        event_business: dict[str, Any] = {
            "exact_acceptance_criteria_revision_binding": {
                "kind": "ACCEPTANCE_CRITERIA_REVISION",
                "id": revision_id,
                "revision": None,
                "digest": digest,
            },
            "exact_case_binding": payload["exact_case_binding"],
            "exact_resolution_contract_binding": payload[
                "exact_resolution_contract_binding"
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
            "subject_revision": None,
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
                    "subject_revision": None,
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
                subject_revision=None,
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

        try:
            command_audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=intent,
                target=revision_id,
                params={"request_fingerprint": request_fingerprint},
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
        if verified != revision.record_digest:
            raise AcceptanceError("INTERNAL_ERROR")
        return envelope

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
