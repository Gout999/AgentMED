"""Frozen AgentMED public v2 case, acceptance, and capability wire models.

Hand-copied from ``control-plane/app/public_api/v5_models.py`` (schema-major-2)
so the CLI can build and validate /api/v2 case-binding and acceptance-criteria
responses without importing the control-plane package.  Any change to that
module must be mirrored here and in ``cli/src/agentmed_cli/client.py``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
)

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
SystemVersionSetId = Annotated[str, Field(pattern=r"^vset_[0-9A-Za-z]{8,64}$")]


class ExactCaseBinding(WireModel):
    case_id: CaseId
    case_revision: Annotated[StrictInt, Field(ge=1)]
    case_digest: Digest


class DeclaredSystemVersionSetBinding(WireModel):
    kind: Literal["SYSTEM_VERSION_SET"]
    id: SystemVersionSetId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class UnknownSystemVersionSetBinding(WireModel):
    kind: Literal["UNKNOWN"]
    reason: Annotated[str, Field(min_length=1, max_length=128)]


SystemVersionSetBindingOrUnknown = Annotated[
    DeclaredSystemVersionSetBinding | UnknownSystemVersionSetBinding,
    Field(discriminator="kind"),
]


class AcceptanceCriteriaRevisionBinding(WireModel):
    kind: Literal["ACCEPTANCE_CRITERIA_REVISION"]
    id: AcceptanceCriteriaRevisionId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class ResolutionContractBindingStatus(WireModel):
    status: Literal["PENDING_MATERIALIZATION"]
    owner: Literal["resolution-contract-controller"]
    materialization_stage: Literal["V5-4"]
    exact_case_binding: ExactCaseBinding


class ReauthenticationCredentialBinding(WireModel):
    kind: Literal["PUBLIC_CREDENTIAL"]
    credential_id: Annotated[str, Field(pattern=r"^cred_[0-9A-Za-z]{8,64}$")]
    principal_id: PrincipalId
    jti_digest: Digest
    claims_digest: Digest
    issued_at: AwareDatetime
    binding_digest: Digest


class ApplicationCaseBindingRecord(WireModel):
    record_envelope: RecordEnvelope
    application_case_binding_id: CaseBindingId
    workspace_id: WorkspaceId
    exact_case_binding: ExactCaseBinding
    application_id: ApplicationId
    environment_id: CatalogEnvironmentId
    declared_system_version_set_binding_or_unknown: SystemVersionSetBindingOrUnknown
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


class AcceptanceCriteriaRevisionRecordBase(WireModel):
    record_envelope: RecordEnvelope
    acceptance_criteria_revision_id: AcceptanceCriteriaRevisionId
    workspace_id: WorkspaceId
    exact_case_binding: ExactCaseBinding
    resolution_contract_binding_status: ResolutionContractBindingStatus
    proposer_principal: PrincipalId
    proposed_at: AwareDatetime
    acceptance_source: dict[str, Any]
    reproducer_input: dict[str, Any] | None = None
    reproducer_environment: dict[str, Any] | None = None
    expected_behavior: dict[str, Any]
    oracle_or_evaluator: dict[str, Any] | None = None
    applicable_workload_profile: dict[str, Any]
    applicable_deployment_profile: dict[str, Any]
    acceptance_digest: Digest

    @model_validator(mode="after")
    def exact_binding_is_immutable(self) -> "AcceptanceCriteriaRevisionRecordBase":
        if (
            self.record_envelope.workspace_id != self.workspace_id
            or self.record_envelope.revision != 1
            or self.resolution_contract_binding_status.exact_case_binding
            != self.exact_case_binding
        ):
            raise ValueError("acceptance revision exact binding mismatch")
        return self


class ProposedAcceptanceCriteriaRevisionRecord(AcceptanceCriteriaRevisionRecordBase):
    confirmation_status: Literal["PROPOSED"]
    confirmer_principal: None = None
    confirmed_at: None = None
    exact_previous_proposed_revision_binding: None = None
    reauthentication_credential_binding: None = None


class ConfirmedAcceptanceCriteriaRevisionRecord(AcceptanceCriteriaRevisionRecordBase):
    confirmation_status: Literal["CONFIRMED"]
    confirmer_principal: PrincipalId
    confirmed_at: AwareDatetime
    exact_previous_proposed_revision_binding: AcceptanceCriteriaRevisionBinding
    reauthentication_credential_binding: ReauthenticationCredentialBinding

    @model_validator(mode="after")
    def confirmation_authority_is_exact(
        self,
    ) -> "ConfirmedAcceptanceCriteriaRevisionRecord":
        if (
            self.exact_previous_proposed_revision_binding.id
            == self.acceptance_criteria_revision_id
            or self.reauthentication_credential_binding.principal_id
            != self.confirmer_principal
        ):
            raise ValueError("confirmed revision authority binding mismatch")
        return self


AcceptanceCriteriaRevisionRecord = Annotated[
    ProposedAcceptanceCriteriaRevisionRecord
    | ConfirmedAcceptanceCriteriaRevisionRecord,
    Field(discriminator="confirmation_status"),
]


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
    exact_case_binding: ExactCaseBinding
    case_readiness: CaseReadiness
    revisions: list[AcceptanceCriteriaRevisionRecord] = Field(default_factory=list)
    next_action: dict[str, Any] | None = None


class AcceptanceCriteriaConfirmResponse(WireModel):
    schema_version: Literal["2.0"]
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    acceptance_criteria_revision: AcceptanceCriteriaRevisionRecord
    idempotency: CaseV5IdempotencyDelivery


V5PublicIntentName = Literal[
    "capabilities.get",
    "applications.register",
    "applications.get",
    "environments.register",
    "environments.get",
    "system-components.register",
    "system-components.get",
    "dependency-edges.record",
    "dependency-edges.get",
    "system-manifests.import",
    "system-versions.get",
    "system-versions.diff",
    "cases.bind-application",
    "case-application-bindings.get",
    "acceptance-criteria.propose",
    "acceptance-criteria.get",
    "acceptance-criteria.confirm",
]


class V5CapabilityPrincipal(WireModel):
    principal_id: PrincipalId
    principal_type: Literal["human", "external_agent", "service", "connector"]
    scopes: list[Annotated[str, Field(min_length=1, max_length=128)]]
    credential_expires_at: AwareDatetime

    @field_validator("scopes")
    @classmethod
    def scopes_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("scopes must be unique")
        return value


class V5EnabledIntent(WireModel):
    name: V5PublicIntentName
    scope: Annotated[str, Field(min_length=1, max_length=128)]
    execution_mode: Literal["synchronous"]
    http: StrictBool
    cli: StrictBool


class V5ServerCapabilitiesData(WireModel):
    server_version: Annotated[str, Field(min_length=1, max_length=128)]
    api_major: StrictInt
    contract_version: Literal["2.0"]
    principal: V5CapabilityPrincipal
    enabled_intents: list[V5EnabledIntent]
    disabled_intents: list[V5PublicIntentName]
    generated_at: AwareDatetime

    @field_validator("api_major")
    @classmethod
    def major_is_two(cls, value: int) -> int:
        if value != 2:
            raise ValueError("api_major must be 2")
        return value

    @field_validator("disabled_intents")
    @classmethod
    def skeletons_remain_undiscoverable(
        cls, value: list[V5PublicIntentName]
    ) -> list[V5PublicIntentName]:
        if value:
            raise ValueError("unimplemented V5 skeletons must remain undiscoverable")
        return value

    @field_validator("enabled_intents")
    @classmethod
    def enabled_intents_are_unique(
        cls, value: list[V5EnabledIntent]
    ) -> list[V5EnabledIntent]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("enabled_intents must be unique")
        return value


class V5ServerCapabilitiesResponse(WireModel):
    schema_version: Literal["2.0"]
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    data: V5ServerCapabilitiesData


__all__ = [
    "AcceptanceCriteriaConfirmResponse",
    "AcceptanceCriteriaGetResponse",
    "AcceptanceCriteriaProposeResponse",
    "AcceptanceCriteriaRevisionId",
    "AcceptanceCriteriaRevisionBinding",
    "AcceptanceCriteriaRevisionRecord",
    "ApplicationBindingGetResponse",
    "ApplicationCaseBindingRecord",
    "CaseBindApplicationResponse",
    "CaseBindingId",
    "CaseId",
    "CaseV5IdempotencyReceipt",
    "ConfirmationStatus",
    "ConfirmedAcceptanceCriteriaRevisionRecord",
    "DeclaredSystemVersionSetBinding",
    "ExactCaseBinding",
    "ProposedAcceptanceCriteriaRevisionRecord",
    "ReauthenticationCredentialBinding",
    "ResolutionContractBindingStatus",
    "SystemVersionSetBindingOrUnknown",
    "UnknownSystemVersionSetBinding",
    "V5CapabilityPrincipal",
    "V5EnabledIntent",
    "V5PublicIntentName",
    "V5ServerCapabilitiesData",
    "V5ServerCapabilitiesResponse",
]
