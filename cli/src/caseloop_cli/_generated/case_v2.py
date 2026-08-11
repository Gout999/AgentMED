"""Frozen CaseLoop public v2 case-binding / acceptance-criteria wire models.

Hand-copied from ``control-plane/app/public_api/v5_models.py`` (schema-major-2)
so the CLI can build and validate /api/v2 case-binding and acceptance-criteria
responses without importing the control-plane package.  Any change to that
module must be mirrored here and in ``cli/src/caseloop_cli/client.py``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, Field, StrictBool, StrictInt, model_validator

from .public_v2 import (
    ApplicationId,
    AuditRef,
    CatalogEnvironmentId,
    Digest,
    IdempotencyReceiptId,
    PrincipalId,
    RecordEnvelope,
    RequestId,
    WorkspaceId,
    WireModel,
)

CaseId = Annotated[str, Field(pattern=r"^case_[0-9A-Za-z]{8,64}$")]
CaseBindingId = Annotated[str, Field(pattern=r"^acb_[0-9A-Za-z]{8,64}$")]
AcceptanceCriteriaRevisionId = Annotated[
    str, Field(pattern=r"^acr_[0-9A-Za-z]{8,64}$")
]
ConfirmationStatus = Literal["PROPOSED", "CONFIRMED"]
CaseReadiness = Literal["NEEDS_ACCEPTANCE_CRITERIA", "READY"]


class ApplicationCaseBindingRecord(WireModel):
    record_envelope: RecordEnvelope
    application_case_binding_id: CaseBindingId
    workspace_id: WorkspaceId
    exact_case_binding: dict[str, Any]
    application_id: ApplicationId
    environment_id: CatalogEnvironmentId
    declared_system_version_set_binding_or_unknown: dict[str, Any] | None
    binding_digest: Digest


CaseV5IdempotencyIntent = Literal[
    "cases.bind-application",
    "acceptance-criteria.propose",
    "acceptance-criteria.confirm",
]


class CaseV5IdempotencyResource(WireModel):
    kind: Literal["application_case_binding", "acceptance_criteria_revision"]
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*_[0-9A-Za-z]{8,64}$")]


class CaseV5IdempotencyReceipt(WireModel):
    schema_version: Literal["1.0"]
    workspace_id: WorkspaceId
    principal_id: PrincipalId
    intent: CaseV5IdempotencyIntent
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]
    request_fingerprint: Digest
    resource: CaseV5IdempotencyResource
    operation_id: Annotated[str, Field(pattern=r"^op_[0-9A-Za-z]{8,64}$")] | None
    request_id: RequestId
    audit_ref: AuditRef
    status: Literal["ACCEPTED", "COMPLETED"]
    response_digest: Digest
    created_at: AwareDatetime
    idempotency_receipt_id: IdempotencyReceiptId
    immutable: StrictBool
    hash_rule: Literal["jcs-rfc8785-v1+sha256(excluding:/receipt_digest)"]
    receipt_digest: Digest

    @model_validator(mode="after")
    def intent_binding_is_exact(self) -> "CaseV5IdempotencyReceipt":
        if self.immutable is not True:
            raise ValueError("idempotency receipt must be immutable")
        expected: dict[str, tuple[str, str, bool, str]] = {
            "cases.bind-application": (
                "application_case_binding",
                "acb_",
                False,
                "COMPLETED",
            ),
            "acceptance-criteria.propose": (
                "acceptance_criteria_revision",
                "acr_",
                False,
                "COMPLETED",
            ),
            "acceptance-criteria.confirm": (
                "acceptance_criteria_revision",
                "acr_",
                False,
                "COMPLETED",
            ),
        }
        kind, prefix, operation_required, status = expected[self.intent]
        if self.resource.kind != kind or not self.resource.id.startswith(prefix):
            raise ValueError("idempotency resource does not match intent owner")
        if (self.operation_id is not None) is not operation_required:
            raise ValueError("idempotency operation_id does not match intent execution mode")
        if self.status != status:
            raise ValueError("idempotency status does not match intent execution mode")
        return self


class CaseV5IdempotencyDelivery(WireModel):
    receipt: CaseV5IdempotencyReceipt
    replayed: StrictBool


class CaseBindApplicationResponse(WireModel):
    schema_version: Literal["2.0"]
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    application_case_binding: ApplicationCaseBindingRecord
    idempotency: CaseV5IdempotencyDelivery


class ApplicationBindingGetResponse(WireModel):
    schema_version: Literal["2.0"]
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    application_case_binding: ApplicationCaseBindingRecord


class AcceptanceCriteriaRevisionRecord(WireModel):
    record_envelope: RecordEnvelope
    acceptance_criteria_revision_id: AcceptanceCriteriaRevisionId
    workspace_id: WorkspaceId
    exact_case_binding: dict[str, Any]
    exact_resolution_contract_binding: dict[str, Any]
    confirmation_status: ConfirmationStatus
    proposer_principal: PrincipalId
    proposed_at: AwareDatetime
    confirmer_principal: PrincipalId | None = None
    confirmed_at: AwareDatetime | None = None
    exact_previous_proposed_revision_binding: dict[str, Any] | None = None
    acceptance_source: dict[str, Any]
    reproducer_input: dict[str, Any] | None = None
    reproducer_environment: dict[str, Any] | None = None
    expected_behavior: dict[str, Any]
    oracle_or_evaluator: dict[str, Any] | None = None
    applicable_workload_profile: dict[str, Any]
    applicable_deployment_profile: dict[str, Any]
    acceptance_digest: Digest


class AcceptanceCriteriaProposeResponse(WireModel):
    schema_version: Literal["2.0"]
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    acceptance_criteria_revision: AcceptanceCriteriaRevisionRecord
    idempotency: CaseV5IdempotencyDelivery


class AcceptanceCriteriaGetResponse(WireModel):
    schema_version: Literal["2.0"]
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    exact_case_binding: dict[str, Any]
    case_readiness: CaseReadiness
    revisions: list[AcceptanceCriteriaRevisionRecord] = []
    next_action: dict[str, Any] | None = None


class AcceptanceCriteriaConfirmResponse(WireModel):
    schema_version: Literal["2.0"]
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    acceptance_criteria_revision: AcceptanceCriteriaRevisionRecord
    idempotency: CaseV5IdempotencyDelivery


__all__ = [
    "AcceptanceCriteriaConfirmResponse",
    "AcceptanceCriteriaGetResponse",
    "AcceptanceCriteriaProposeResponse",
    "AcceptanceCriteriaRevisionId",
    "AcceptanceCriteriaRevisionRecord",
    "ApplicationBindingGetResponse",
    "ApplicationCaseBindingRecord",
    "CaseBindApplicationResponse",
    "CaseBindingId",
    "CaseId",
    "CaseV5IdempotencyReceipt",
    "ConfirmationStatus",
]
