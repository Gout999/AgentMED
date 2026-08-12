"""V5-2B canonical async public operations over the V5-2A Work Kernel.

The public row binds an AutomationRequest and durable operation id to exactly
one WorkTask.  It never stores a transport-owned terminal state.  Every read
rebuilds OperationState from the current WorkTask/Attempt and validates any
claimed terminal artifact before reporting COMPLETED.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Callable, NoReturn

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.v4_tables import PublicCommandIdempotency, PublicPrincipal
from app.models.v5_tables import AIApplication, ApplicationCaseBinding, Environment
from app.models.v5_work_tables import AutomationRequest, WorkAttempt, WorkTask
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.v5_models import (
    InvestigationStartRequest,
    InvestigationStartResponse,
    OperationArtifact,
    OperationCancelRequest,
    OperationCancelResponse,
    OperationGetResponse,
    OperationListResponse,
    OperationRecord,
    V5IdempotencyReceipt,
)
from app.services.public_idempotency import (
    PublicIdempotencyError,
    PublicIdempotencyService,
)
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from app.services.v4_event_store import V4EventStore, V4EventStoreError
from app.services.v5_authority import V5AuthorityError, V5AuthorityService
from app.services.v5_work_kernel import (
    V5WorkKernelError,
    WorkKernelService,
    validate_work_attempt_head,
    validate_work_task_head,
)
from app.utils.ids import (
    new_authority_receipt_id,
    new_automation_request_id,
    new_idempotency_receipt_id,
    new_transaction_id,
    new_v4_operation_id,
)
from app.utils.v4_integrity import V4IntegrityError, canonical_digest, canonicalize
from app.utils.v5_integrity import assert_v5_record_digest

Clock = Callable[[], datetime]

_START_INTENT = "investigations.start"
_CANCEL_INTENT = "operations.cancel-request"
_OPERATION_STATES = {
    "QUEUED": "SUBMITTED",
    "LEASED": "WORKING",
    "WAITING_RETRY": "WORKING",
    "CANCEL_REQUESTED": "CANCEL_REQUESTED",
    "CANCELLED": "CANCELED",
    "EXHAUSTED": "FAILED",
    "BLOCKED_UNKNOWN": "INPUT_REQUIRED",
}
_TERMINAL_TASK_STATES = frozenset({"COMPLETED", "CANCELLED", "EXHAUSTED"})
_CURSOR_PREFIX = "opcur_"
_CURSOR_SIGNATURE_BYTES = 32


class PublicOperationError(RuntimeError):
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


class PublicOperationReadDenial(PublicOperationError):
    def __init__(self, code: str, *, audit_ref: str, workspace_id: str) -> None:
        super().__init__(
            code,
            audit_ref=audit_ref,
            workspace_id=workspace_id,
        )
        self.rollback_required = False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _automation_snapshot(row: AutomationRequest) -> dict[str, Any]:
    return {
        "automation_request_id": row.automation_request_id,
        "workspace_id": row.workspace_id,
        "operation_id": row.operation_id,
        "task_id": row.task_id,
        "application_case_binding_id": row.application_case_binding_id,
        "case_id": row.case_id,
        "case_revision": row.case_revision,
        "case_digest": row.case_digest,
        "application_id": row.application_id,
        "environment_id": row.environment_id,
        "requester_principal": row.requester_principal,
        "request_digest": row.request_digest,
        "budget_digest": row.budget_digest,
        "revision": row.revision,
        "stop_requested": row.stop_requested,
        "stop_requested_at": (
            _wire_time(row.stop_requested_at)
            if row.stop_requested_at is not None
            else None
        ),
        "stop_reason": row.stop_reason,
        "stop_requested_by_principal": row.stop_requested_by_principal,
    }


class PublicOperationService:
    def __init__(
        self,
        session: Session,
        *,
        cursor_signing_key: str,
        clock: Clock | None = None,
        audit: V4AuditService | None = None,
    ) -> None:
        self.session = session
        self.cursor_key = cursor_signing_key.encode("utf-8")
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.audit = audit or V4AuditService(session, clock=self.clock)
        self.authority = V5AuthorityService(session)
        self.events = V4EventStore(session)
        self.idempotency = PublicIdempotencyService(session)
        self.work = WorkKernelService(session, clock=self.clock)

    # --------------------------------------------------------------- public

    def start_investigation(
        self,
        case_id: str,
        request: InvestigationStartRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str,
    ) -> InvestigationStartResponse:
        self._require_principal(principal, _START_INTENT, "investigations:start")
        binding, application, environment = self._load_case_target(
            principal=principal,
            case_id=case_id,
            case_revision=request.case_revision,
            case_digest=request.case_digest,
            request_id=request_id,
        )
        request_payload = {
            "case_id": case_id,
            **request.model_dump(mode="json"),
        }
        fingerprint = self.idempotency.fingerprint(request_payload)
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=_START_INTENT,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                verify_terminal=lambda row: self._verify_operation_idempotency(
                    row, response_model=InvestigationStartResponse
                ),
            )
        except PublicIdempotencyError as exc:
            raise PublicOperationError(exc.code) from exc
        if lookup.record is not None:
            try:
                return self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=InvestigationStartResponse,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind="automation_request",
                    resource_field="operation",
                    resource_id_field="automation_request_id",
                    expected_state="ACCEPTED",
                )
            except PublicIdempotencyError as exc:
                raise PublicOperationError(exc.code) from exc

        now = _as_utc(self.clock())
        transaction_id = new_transaction_id()
        automation_request_id = new_automation_request_id()
        operation_id = new_v4_operation_id()
        work_key = "public-op-" + hashlib.sha256(
            "\x1f".join(
                (
                    principal.workspace_id,
                    principal.principal_id,
                    _START_INTENT,
                    idempotency_key,
                )
            ).encode("utf-8")
        ).hexdigest()
        work_input = {
            "canonical_intent": _START_INTENT,
            "automation_request_id": automation_request_id,
            "operation_id": operation_id,
            "exact_case_binding": {
                "case_id": case_id,
                "case_revision": request.case_revision,
                "case_digest": request.case_digest,
            },
            "application_case_binding_id": binding.application_case_binding_id,
            "application_id": application.application_id,
            "environment_id": environment.environment_id,
            "instructions": request.instructions,
        }
        try:
            task = self.work.request_task(
                workspace_id=principal.workspace_id,
                task_kind="investigation",
                input_payload=work_input,
                requester_principal=principal.principal_id,
                idempotency_key=work_key,
                max_attempts=request.max_attempts,
                transaction_id=transaction_id,
                request_id=request_id,
            )
        except V5WorkKernelError as exc:
            raise self._map_work_error(exc) from exc

        authority_receipt_id = new_authority_receipt_id()
        row = AutomationRequest(
            automation_request_id=automation_request_id,
            workspace_id=principal.workspace_id,
            operation_id=operation_id,
            task_id=task.task_id,
            application_case_binding_id=binding.application_case_binding_id,
            case_id=case_id,
            case_revision=request.case_revision,
            case_digest=request.case_digest,
            application_id=application.application_id,
            environment_id=environment.environment_id,
            requester_principal=principal.principal_id,
            request_payload=request_payload,
            request_digest=fingerprint,
            budget_digest=canonical_digest(
                {"max_attempts": request.max_attempts}
            ),
            revision=1,
            stop_requested=False,
            record_digest="sha256:" + "0" * 64,
            authority_receipt_id=authority_receipt_id,
            created_at=now,
            updated_at=now,
        )
        row.record_digest = canonical_digest(_automation_snapshot(row))
        self.session.add(row)
        self.session.flush()
        self._write_automation_fact(
            row=row,
            command="automation-requests.start-investigation",
            event_type="automation_request.investigation_submitted",
            payload={
                "exact_automation_request_binding": self._automation_binding(row),
                "exact_work_task_binding": self._work_binding(task),
                "exact_case_binding": work_input["exact_case_binding"],
                "application_id": application.application_id,
                "environment_id": environment.environment_id,
                "request_digest": fingerprint,
                "budget_digest": row.budget_digest,
                "requester_principal": principal.principal_id,
            },
            transaction_id=transaction_id,
            request_id=request_id,
            now=now,
            authority_receipt_id=authority_receipt_id,
        )
        command_audit = self._command_audit(
            principal=principal,
            action=_START_INTENT,
            target=automation_request_id,
            transaction_id=transaction_id,
            request_id=request_id,
            operation_id=operation_id,
            now=now,
        )
        operation = self._operation_record(row, task=task)
        return self._persist_operation_response(
            response_model=InvestigationStartResponse,
            operation=operation,
            principal=principal,
            intent=_START_INTENT,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            request_id=request_id,
            audit_ref=command_audit.audit_ref,
            completed_at=now,
        )

    def get_operation(
        self,
        operation_id: str,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
    ) -> OperationGetResponse:
        self._require_principal(principal, "operations.get", "operations:read")
        row = self._visible_operation(
            principal=principal,
            operation_id=operation_id,
            request_id=request_id,
            action="operations.get",
        )
        operation = self._operation_record(row)
        audit = self._read_audit(
            principal=principal,
            action="operations.get",
            target=operation_id,
            request_id=request_id,
        )
        return OperationGetResponse(
            schema_version="2.0",
            workspace_id=principal.workspace_id,
            request_id=request_id,
            audit_ref=audit.audit_ref,
            operation=operation,
        )

    def list_operations(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        limit: int,
        cursor: str | None,
    ) -> OperationListResponse:
        self._require_principal(principal, "operations.list", "operations:read")
        if not self.cursor_key:
            raise PublicOperationError("INTERNAL_ERROR")
        if not 1 <= limit <= 100:
            self._deny_read(principal, request_id, "operations.list", "limit")
        scope = self._cursor_scope(principal=principal, limit=limit)
        visible = self._visibility_condition(principal)
        base = and_(
            AutomationRequest.workspace_id == principal.workspace_id,
            visible,
        )
        after: str | None = None
        if cursor is None:
            watermark = self.session.scalar(
                select(AutomationRequest.operation_id)
                .join(AIApplication, AIApplication.application_id == AutomationRequest.application_id)
                .where(base)
                .order_by(AutomationRequest.operation_id.desc())
                .limit(1)
            )
        else:
            try:
                decoded = self._decode_cursor(cursor)
            except ValueError:
                self._deny_read(principal, request_id, "operations.list", "cursor")
            if decoded["scope"] != scope:
                self._deny_read(
                    principal, request_id, "operations.list", "cursor_scope"
                )
            watermark = decoded["watermark"]
            after = decoded["after"]
        if watermark is None:
            rows: list[AutomationRequest] = []
        else:
            clauses = [base, AutomationRequest.operation_id <= watermark]
            if after is not None:
                clauses.append(AutomationRequest.operation_id > after)
            rows = list(
                self.session.scalars(
                    select(AutomationRequest)
                    .join(
                        AIApplication,
                        AIApplication.application_id == AutomationRequest.application_id,
                    )
                    .where(*clauses)
                    .order_by(AutomationRequest.operation_id.asc())
                    .limit(limit + 1)
                ).all()
            )
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit and page and watermark is not None:
            next_cursor = self._encode_cursor(
                scope=scope, watermark=watermark, after=page[-1].operation_id
            )
        items = [self._operation_record(row) for row in page]
        audit = self._read_audit(
            principal=principal,
            action="operations.list",
            target="operation:list",
            request_id=request_id,
        )
        return OperationListResponse(
            schema_version="2.0",
            workspace_id=principal.workspace_id,
            request_id=request_id,
            audit_ref=audit.audit_ref,
            items=items,
            next_cursor=next_cursor,
        )

    def request_cancel(
        self,
        operation_id: str,
        request: OperationCancelRequest,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str,
    ) -> OperationCancelResponse:
        self._require_principal(
            principal, _CANCEL_INTENT, "operations:cancel"
        )
        row = self.session.scalar(
            select(AutomationRequest)
            .where(
                AutomationRequest.workspace_id == principal.workspace_id,
                AutomationRequest.operation_id == operation_id,
            )
            .with_for_update()
        )
        if row is None:
            raise PublicOperationError(
                "RESOURCE_NOT_FOUND", workspace_id=principal.workspace_id
            )
        now = _as_utc(self.clock())
        try:
            controller = self.authority.resolve_controller(
                workspace_id=principal.workspace_id,
                subject_kind="AUTOMATION_REQUEST",
                command="automation-requests.request-stop",
                event_type="automation_request.stop_requested",
                recorded_at=now,
            )
        except V5AuthorityError as exc:
            raise PublicOperationError("INTERNAL_ERROR") from exc
        if principal.principal_id not in {
            row.requester_principal,
            controller.controller_principal,
        }:
            raise PublicOperationError(
                "RESOURCE_NOT_FOUND", workspace_id=principal.workspace_id
            )
        request_payload = {
            "operation_id": operation_id,
            **request.model_dump(mode="json"),
        }
        fingerprint = self.idempotency.fingerprint(request_payload)
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=_CANCEL_INTENT,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                verify_terminal=lambda stored: self._verify_operation_idempotency(
                    stored, response_model=OperationCancelResponse
                ),
            )
        except PublicIdempotencyError as exc:
            raise PublicOperationError(exc.code) from exc
        if lookup.record is not None:
            try:
                return self.idempotency.replay_catalog_response(
                    lookup.record,
                    response_model=OperationCancelResponse,
                    receipt_model=V5IdempotencyReceipt,
                    resource_kind="automation_request",
                    resource_field="operation",
                    resource_id_field="automation_request_id",
                    expected_state="ACCEPTED",
                )
            except PublicIdempotencyError as exc:
                raise PublicOperationError(exc.code) from exc
        task = self.session.scalar(
            select(WorkTask)
            .where(
                WorkTask.workspace_id == principal.workspace_id,
                WorkTask.task_id == row.task_id,
            )
            .with_for_update()
        )
        if task is None:
            raise PublicOperationError("INTERNAL_ERROR")
        if task.state in _TERMINAL_TASK_STATES:
            raise PublicOperationError(
                "OPERATION_NOT_CANCELLABLE", workspace_id=principal.workspace_id
            )
        transaction_id = new_transaction_id()
        if not row.stop_requested:
            row.stop_requested = True
            row.stop_requested_at = now
            row.stop_reason = request.reason
            row.stop_requested_by_principal = principal.principal_id
            row.revision += 1
            row.updated_at = now
            authority_receipt_id = new_authority_receipt_id()
            row.authority_receipt_id = authority_receipt_id
            row.record_digest = canonical_digest(_automation_snapshot(row))
            self.session.flush()
            self._write_automation_fact(
                row=row,
                command="automation-requests.request-stop",
                event_type="automation_request.stop_requested",
                payload={
                    "exact_automation_request_binding": self._automation_binding(row),
                    "operation_id": operation_id,
                    "reason": request.reason,
                    "requested_by_principal": principal.principal_id,
                },
                transaction_id=transaction_id,
                request_id=request_id,
                now=now,
                authority_receipt_id=authority_receipt_id,
            )
            if task.state in {"LEASED", "WAITING_RETRY"}:
                try:
                    self.work.cancel_task(
                        workspace_id=principal.workspace_id,
                        task_id=task.task_id,
                        reason=request.reason,
                        requested_by_principal=principal.principal_id,
                        transaction_id=transaction_id,
                        request_id=request_id,
                    )
                except V5WorkKernelError as exc:
                    raise self._map_work_error(exc) from exc
        command_audit = self._command_audit(
            principal=principal,
            action=_CANCEL_INTENT,
            target=row.automation_request_id,
            transaction_id=transaction_id,
            request_id=request_id,
            operation_id=operation_id,
            now=now,
        )
        operation = self._operation_record(row, task=task)
        return self._persist_operation_response(
            response_model=OperationCancelResponse,
            operation=operation,
            principal=principal,
            intent=_CANCEL_INTENT,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            request_id=request_id,
            audit_ref=command_audit.audit_ref,
            completed_at=now,
        )

    # --------------------------------------------------------- authorization

    def _require_principal(
        self,
        principal: AcceptedPrincipalContext,
        action: str,
        required_scope: str,
    ) -> None:
        row = self.session.get(PublicPrincipal, principal.principal_id)
        if (
            row is None
            or row.workspace_id != principal.workspace_id
            or row.state != "ACTIVE"
            or row.principal_type != principal.principal_type
            or row.project_ids != principal.project_ids
            or row.environment_ids != principal.environment_ids
            or row.scopes != principal.scopes
            or required_scope not in principal.scopes
            or principal.requested_context.required_scope != required_scope
            or principal.principal_type
            not in (
                {"human", "external_agent", "service"}
                if action in {_START_INTENT, _CANCEL_INTENT}
                else {"human", "external_agent", "service", "connector"}
            )
        ):
            raise PublicOperationError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )

    def _load_case_target(
        self,
        *,
        principal: AcceptedPrincipalContext,
        case_id: str,
        case_revision: int,
        case_digest: str,
        request_id: str,
    ) -> tuple[ApplicationCaseBinding, AIApplication, Environment]:
        binding = self.session.scalar(
            select(ApplicationCaseBinding).where(
                ApplicationCaseBinding.workspace_id == principal.workspace_id,
                ApplicationCaseBinding.case_id == case_id,
                ApplicationCaseBinding.case_revision == case_revision,
                ApplicationCaseBinding.case_digest == case_digest,
            )
        )
        if binding is None:
            raise PublicOperationError(
                "RESOURCE_NOT_FOUND", workspace_id=principal.workspace_id
            )
        application = self.session.get(AIApplication, binding.application_id)
        environment = self.session.get(Environment, binding.environment_id)
        if (
            application is None
            or environment is None
            or application.workspace_id != principal.workspace_id
            or environment.workspace_id != principal.workspace_id
            or environment.application_id != application.application_id
            or application.project_id not in principal.project_ids
            or environment.environment_id not in principal.environment_ids
            or application.lifecycle_state != "ACTIVE"
            or environment.lifecycle_state != "ACTIVE"
        ):
            raise PublicOperationError(
                "RESOURCE_NOT_FOUND", workspace_id=principal.workspace_id
            )
        try:
            if (
                assert_v5_record_digest(binding.envelope_payload)
                != binding.record_digest
            ):
                raise V4IntegrityError("binding digest mismatch")
        except (TypeError, ValueError, V4IntegrityError) as exc:
            raise PublicOperationError("INTERNAL_ERROR") from exc
        return binding, application, environment

    def _visibility_condition(self, principal: AcceptedPrincipalContext):
        project_visibility = (
            AIApplication.project_id.in_(principal.project_ids)
            if principal.project_ids
            else AIApplication.project_id.in_(["__none__"])
        )
        return or_(
            AutomationRequest.requester_principal == principal.principal_id,
            project_visibility,
        )

    def _visible_operation(
        self,
        *,
        principal: AcceptedPrincipalContext,
        operation_id: str,
        request_id: str,
        action: str,
    ) -> AutomationRequest:
        row = self.session.scalar(
            select(AutomationRequest)
            .join(
                AIApplication,
                AIApplication.application_id == AutomationRequest.application_id,
            )
            .where(
                AutomationRequest.workspace_id == principal.workspace_id,
                AutomationRequest.operation_id == operation_id,
                self._visibility_condition(principal),
            )
        )
        if row is None:
            self._deny_read(principal, request_id, action, "opaque_not_found")
        return row

    def _deny_read(
        self,
        principal: AcceptedPrincipalContext,
        request_id: str,
        action: str,
        reason: str,
    ) -> NoReturn:
        try:
            audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=f"public.v5.{action}.denied",
                target="operation:opaque",
                params={"request_id": request_id, "reason": reason},
                result="denied",
                error_code=(
                    "REQUEST_INVALID"
                    if reason in {"limit", "cursor", "cursor_scope"}
                    else "RESOURCE_NOT_FOUND"
                ),
                trace_id=request_id,
            )
        except V4AuditUnavailable as exc:
            raise PublicOperationError("AUDIT_UNAVAILABLE") from exc
        raise PublicOperationReadDenial(
            "REQUEST_INVALID"
            if reason in {"limit", "cursor", "cursor_scope"}
            else "RESOURCE_NOT_FOUND",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

    # ------------------------------------------------------------- projection

    def _operation_record(
        self, row: AutomationRequest, *, task: WorkTask | None = None
    ) -> OperationRecord:
        task = task or self.session.scalar(
            select(WorkTask).where(
                WorkTask.workspace_id == row.workspace_id,
                WorkTask.task_id == row.task_id,
            )
        )
        if canonical_digest(_automation_snapshot(row)) != row.record_digest:
            raise PublicOperationError("INTERNAL_ERROR")
        if task is None:
            raise PublicOperationError("INTERNAL_ERROR")
        try:
            validate_work_task_head(task)
            self.authority.validate_receipt_binding(
                authority_receipt_id=row.authority_receipt_id,
                workspace_id=row.workspace_id,
                subject_kind="AUTOMATION_REQUEST",
                subject_id=row.automation_request_id,
                subject_revision=row.revision,
                subject_digest=row.record_digest,
            )
            self.authority.validate_receipt_binding(
                authority_receipt_id=task.authority_receipt_id,
                workspace_id=task.workspace_id,
                subject_kind="WORK_TASK",
                subject_id=task.task_id,
                subject_revision=task.revision,
                subject_digest=task.record_digest,
            )
        except V5WorkKernelError as exc:
            raise PublicOperationError("INTERNAL_ERROR") from exc
        except V5AuthorityError as exc:
            raise PublicOperationError("INTERNAL_ERROR") from exc
        attempt = None
        if task.current_attempt_id is not None:
            attempt = self.session.scalar(
                select(WorkAttempt).where(
                    WorkAttempt.workspace_id == row.workspace_id,
                    WorkAttempt.task_id == task.task_id,
                    WorkAttempt.attempt_id == task.current_attempt_id,
                )
            )
            if attempt is None:
                raise PublicOperationError("INTERNAL_ERROR")
            try:
                validate_work_attempt_head(attempt)
                self.authority.validate_receipt_binding(
                    authority_receipt_id=attempt.authority_receipt_id,
                    workspace_id=attempt.workspace_id,
                    subject_kind="WORK_ATTEMPT",
                    subject_id=attempt.attempt_id,
                    subject_revision=attempt.revision,
                    subject_digest=attempt.record_digest,
                )
            except V5WorkKernelError as exc:
                raise PublicOperationError("INTERNAL_ERROR") from exc
            except V5AuthorityError as exc:
                raise PublicOperationError("INTERNAL_ERROR") from exc
        artifact = None
        if task.state == "COMPLETED" and attempt is not None:
            try:
                artifact = OperationArtifact.model_validate(attempt.output_payload)
                binding = artifact.exact_artifact_binding
                if (
                    canonical_digest(attempt.output_payload) != attempt.output_digest
                    # The WorkAttempt receipt authenticates the whole output
                    # document.  Its nested binding authenticates the domain
                    # artifact carried by that output; the current attempt is
                    # exposed separately by exact_current_attempt_binding.
                    or binding.digest != canonical_digest(artifact.payload)
                ):
                    raise ValueError("untrusted operation artifact binding")
                state = "COMPLETED"
            except (ValidationError, TypeError, ValueError):
                artifact = None
                state = "FAILED"
        else:
            state = _OPERATION_STATES.get(task.state)
            if state is None:
                raise PublicOperationError("INTERNAL_ERROR")
        # AutomationRequest owns stop admission.  A queued task has no active
        # attempt for the Work Kernel to cancel yet, but the accepted stop
        # request must still be visible immediately and durably.  It remains a
        # request, never a fabricated terminal cancellation.
        if row.stop_requested and state not in {"COMPLETED", "CANCELED", "FAILED"}:
            state = "CANCEL_REQUESTED"
        updated_values = [row.updated_at, task.updated_at]
        if attempt is not None:
            updated_values.append(attempt.updated_at)
        return OperationRecord(
            operation_id=row.operation_id,
            automation_request_id=row.automation_request_id,
            canonical_intent=_START_INTENT,
            state=state,
            requester_principal=row.requester_principal,
            exact_case_binding={
                "case_id": row.case_id,
                "case_revision": row.case_revision,
                "case_digest": row.case_digest,
            },
            application_id=row.application_id,
            environment_id=row.environment_id,
            exact_work_task_binding=self._work_binding(task),
            exact_current_attempt_binding_or_null=(
                {
                    "kind": "WORK_ATTEMPT",
                    "id": attempt.attempt_id,
                    "revision": attempt.revision,
                    "digest": attempt.record_digest,
                }
                if attempt is not None
                else None
            ),
            cancel_requested=row.stop_requested,
            artifact_or_null=artifact,
            created_at=_as_utc(row.created_at),
            updated_at=max(_as_utc(value) for value in updated_values),
        )

    @staticmethod
    def _automation_binding(row: AutomationRequest) -> dict[str, Any]:
        return {
            "kind": "AUTOMATION_REQUEST",
            "id": row.automation_request_id,
            "revision": row.revision,
            "digest": row.record_digest,
        }

    @staticmethod
    def _work_binding(task: WorkTask) -> dict[str, Any]:
        return {
            "kind": "WORK_TASK",
            "id": task.task_id,
            "revision": task.revision,
            "digest": task.record_digest,
        }

    # ---------------------------------------------------------- fact/audit/id

    def _write_automation_fact(
        self,
        *,
        row: AutomationRequest,
        command: str,
        event_type: str,
        payload: dict[str, Any],
        transaction_id: str,
        request_id: str,
        now: datetime,
        authority_receipt_id: str,
    ) -> None:
        try:
            resolved = self.authority.resolve_controller(
                workspace_id=row.workspace_id,
                subject_kind="AUTOMATION_REQUEST",
                command=command,
                event_type=event_type,
                recorded_at=now,
            )
            event = self.events.append_event(
                workspace_id=row.workspace_id,
                aggregate_type="automation_request",
                aggregate_id=row.automation_request_id,
                event_type=event_type,
                payload=payload,
                causation_id=request_id,
                correlation_id=row.operation_id,
                actor_principal=resolved.controller_principal,
                transaction_id=transaction_id,
                occurred_at=now,
                authority_receipt_id=authority_receipt_id,
            )
            audit = self.audit.record(
                workspace_id=row.workspace_id,
                actor_principal=resolved.controller_principal,
                action=f"controller.{event_type}",
                target=row.automation_request_id,
                # V5 authority validation binds controller audits to the
                # canonical command only.  The operation id is already bound
                # by the target row, event correlation id and evidence refs.
                params={"command": command},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "subject_kind": "AUTOMATION_REQUEST",
                    "subject_id": row.automation_request_id,
                    "subject_revision": row.revision,
                    "subject_digest": row.record_digest,
                    "event_id": event.event_id,
                },
                occurred_at=now,
            )
            self.authority.record_receipt(
                resolved=resolved,
                authority_receipt_id=authority_receipt_id,
                workspace_id=row.workspace_id,
                subject_id=row.automation_request_id,
                subject_revision=row.revision,
                subject_digest=row.record_digest,
                event_id=event.event_id,
                transaction_id=transaction_id,
                audit_ref=audit.audit_ref,
                recorded_at=now,
            )
        except (
            V5AuthorityError,
            V4EventStoreError,
            V4IntegrityError,
            V4AuditUnavailable,
        ) as exc:
            raise PublicOperationError("INTERNAL_ERROR") from exc

    def _command_audit(
        self,
        *,
        principal: AcceptedPrincipalContext,
        action: str,
        target: str,
        transaction_id: str,
        request_id: str,
        operation_id: str,
        now: datetime,
    ):
        try:
            return self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=action,
                target=target,
                params={"operation_id": operation_id},
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "resource_kind": "automation_request",
                    "resource_id": target,
                    "operation_id": operation_id,
                },
                occurred_at=now,
            )
        except V4AuditUnavailable as exc:
            raise PublicOperationError("AUDIT_UNAVAILABLE") from exc

    def _read_audit(
        self,
        *,
        principal: AcceptedPrincipalContext,
        action: str,
        target: str,
        request_id: str,
    ):
        try:
            return self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=f"public.v5.{action}",
                target=target,
                params={"request_id": request_id},
                trace_id=request_id,
            )
        except V4AuditUnavailable as exc:
            raise PublicOperationError("AUDIT_UNAVAILABLE") from exc

    def _persist_operation_response(
        self,
        *,
        response_model: type[InvestigationStartResponse | OperationCancelResponse],
        operation: OperationRecord,
        principal: AcceptedPrincipalContext,
        intent: str,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        audit_ref: str,
        completed_at: datetime,
    ):
        core = {
            "schema_version": "2.0",
            "workspace_id": principal.workspace_id,
            "request_id": request_id,
            "audit_ref": audit_ref,
            "operation": operation.model_dump(mode="json"),
        }
        response_digest = canonical_digest(core)
        receipt_id = new_idempotency_receipt_id()
        receipt_body = {
            "schema_version": "1.0",
            "workspace_id": principal.workspace_id,
            "principal_id": principal.principal_id,
            "intent": intent,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "resource": {
                "kind": "automation_request",
                "id": operation.automation_request_id,
            },
            "operation_id": operation.operation_id,
            "request_id": request_id,
            "audit_ref": audit_ref,
            "status": "ACCEPTED",
            "response_digest": response_digest,
            "created_at": _wire_time(completed_at),
            "idempotency_receipt_id": receipt_id,
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/receipt_digest)",
        }
        receipt = {**receipt_body, "receipt_digest": canonical_digest(receipt_body)}
        response = response_model.model_validate(
            {
                **core,
                "idempotency": {"receipt": receipt, "replayed": False},
            }
        )
        try:
            self.idempotency.store_completed_catalog(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent=intent,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                resource_kind="automation_request",
                resource_id=operation.automation_request_id,
                operation_id=operation.operation_id,
                state="ACCEPTED",
                request_id=request_id,
                audit_ref=audit_ref,
                response_payload=core,
                response_digest=response_digest,
                receipt_payload=receipt,
                receipt_digest=receipt["receipt_digest"],
                idempotency_receipt_id=receipt_id,
                completed_at=completed_at,
                response_model=response_model,
                receipt_model=V5IdempotencyReceipt,
                resource_field="operation",
                resource_id_field="automation_request_id",
            )
        except PublicIdempotencyError as exc:
            raise PublicOperationError(exc.code) from exc
        return response

    def _verify_operation_idempotency(
        self,
        row: PublicCommandIdempotency,
        *,
        response_model: type[InvestigationStartResponse | OperationCancelResponse],
    ) -> None:
        self.idempotency._verify_terminal_catalog(
            row,
            response_model=response_model,
            receipt_model=V5IdempotencyReceipt,
            resource_kind="automation_request",
            resource_field="operation",
            resource_id_field="automation_request_id",
            expected_state="ACCEPTED",
        )

    @staticmethod
    def _map_work_error(exc: V5WorkKernelError) -> PublicOperationError:
        if exc.code == "v5.work.idempotency_conflict":
            return PublicOperationError("IDEMPOTENCY_CONFLICT")
        if exc.code in {
            "v5.work.task_terminal",
            "v5.work.cancel_not_cancellable",
        }:
            return PublicOperationError("OPERATION_NOT_CANCELLABLE")
        return PublicOperationError("INTERNAL_ERROR")

    # --------------------------------------------------------------- cursor

    def _cursor_scope(
        self, *, principal: AcceptedPrincipalContext, limit: int
    ) -> str:
        return canonical_digest(
            {
                "workspace_id": principal.workspace_id,
                "principal_id": principal.principal_id,
                "project_ids": sorted(principal.project_ids),
                "limit": limit,
                "order": "operation_id:asc",
            }
        )

    def _encode_cursor(self, *, scope: str, watermark: str, after: str) -> str:
        raw = canonicalize(
            {"version": 1, "scope": scope, "watermark": watermark, "after": after}
        )
        signature = hmac.new(self.cursor_key, raw, hashlib.sha256).digest()
        token = _CURSOR_PREFIX + base64.urlsafe_b64encode(
            raw + signature
        ).rstrip(b"=").decode("ascii")
        if len(token) > 512:
            raise PublicOperationError("INTERNAL_ERROR")
        return token

    def _decode_cursor(self, cursor: str) -> dict[str, str]:
        try:
            if not cursor.startswith(_CURSOR_PREFIX) or len(cursor) > 512:
                raise ValueError
            encoded = cursor[len(_CURSOR_PREFIX) :]
            combined = base64.b64decode(
                encoded + "=" * (-len(encoded) % 4),
                altchars=b"-_",
                validate=True,
            )
            canonical_encoded = base64.urlsafe_b64encode(combined).rstrip(b"=").decode(
                "ascii"
            )
            if encoded != canonical_encoded:
                raise ValueError
            if len(combined) <= _CURSOR_SIGNATURE_BYTES:
                raise ValueError
            raw = combined[:-_CURSOR_SIGNATURE_BYTES]
            supplied = combined[-_CURSOR_SIGNATURE_BYTES:]
            expected = hmac.new(self.cursor_key, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(supplied, expected):
                raise ValueError
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"version", "scope", "watermark", "after"}
                or payload["version"] != 1
                or not all(
                    isinstance(payload[field], str)
                    for field in ("scope", "watermark", "after")
                )
            ):
                raise ValueError
            return payload
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid operation cursor") from exc


__all__ = [
    "PublicOperationError",
    "PublicOperationReadDenial",
    "PublicOperationService",
]
