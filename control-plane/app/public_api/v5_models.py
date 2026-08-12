"""Schema-major-2 wire models for the V5 application catalog public boundary.

These models validate transport data only.  They do not resolve credentials,
load authoritative state, or perform database work.  The V5 record envelope
follows ``contracts/v5/domain-model.yaml#record_envelope`` and the frozen
``contracts/v4`` ``models.py`` idempotency receipt format is extended here for
the four catalog intents without touching the frozen file.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    AwareDatetime,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
    model_serializer,
)

from app.models.v5_tables import (
    COMPONENT_KIND_VALUES,
    CRITICALITY_VALUES,
    DATA_CLASSIFICATION_VALUES,
    DATASET_ROLE_VALUES,
    DEPENDENCY_RELATION_VALUES,
    EFFECT_CLASSIFICATION_VALUES,
    GOVERNANCE_MODE_VALUES,
    PERMISSION_CLASSIFICATION_VALUES,
    RISK_CLASSIFICATION_VALUES,
)
from app.public_api.models import (
    AuditRef,
    CaseId,
    Digest,
    IdempotencyReceiptId,
    OperationId,
    PrincipalId,
    ProjectId,
    RequestId,
    WorkspaceId,
    WireModel,
    _require_unique,
)

SchemaVersion2 = Literal["2.0"]
ApplicationId = Annotated[str, Field(pattern=r"^app_[0-9A-Za-z]{8,64}$")]
CatalogEnvironmentId = Annotated[str, Field(pattern=r"^env_[0-9A-Za-z]{8,64}$")]
ComponentId = Annotated[str, Field(pattern=r"^cmp_[0-9A-Za-z]{8,64}$")]
EdgeId = Annotated[str, Field(pattern=r"^de_[0-9A-Za-z]{8,64}$")]
ComponentRevisionId = Annotated[str, Field(pattern=r"^crv_[0-9A-Za-z]{8,64}$")]
TopologyRevisionId = Annotated[str, Field(pattern=r"^tpr_[0-9A-Za-z]{8,64}$")]
SystemVersionSetId = Annotated[str, Field(pattern=r"^vset_[0-9A-Za-z]{8,64}$")]
BootstrapAttestationId = Annotated[str, Field(pattern=r"^batt_[0-9A-Za-z]{8,64}$")]
AssignmentId = Annotated[str, Field(pattern=r"^asg_[0-9A-Za-z]{8,64}$")]
SystemManifestId = Annotated[str, Field(pattern=r"^smf_[0-9A-Za-z]{8,64}$")]
AuthorityReceiptId = Annotated[str, Field(pattern=r"^arec_[0-9A-Za-z]{8,64}$")]

Slug = Annotated[
    str,
    Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$", min_length=1, max_length=64),
]
LogicalName = Annotated[
    str,
    Field(pattern=r"^[a-z0-9](?:[a-z0-9_-]{0,127})$", min_length=1, max_length=128),
]

LifecycleState = Literal["REGISTERED", "ACTIVE", "ARCHIVED"]
EnvironmentLifecycleState = Literal["ACTIVE", "RETIRED"]
ComponentLifecycleState = Literal[
    "REGISTERED", "ACTIVE", "DEPRECATED", "RETIRED"
]
Criticality = Literal["P0", "P1", "P2", "P3"]
DataClassification = Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
GovernanceMode = Literal["MANAGED", "OBSERVED"]
RiskClassification = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
PermissionClassification = Literal["READ_ONLY", "READ_WRITE", "ELEVATED"]
EffectClassification = Literal["NONE", "LOCAL", "EXTERNAL"]
ComponentKind = Literal[
    "APPLICATION_CODE",
    "AGENT",
    "MODEL_BINDING",
    "PROMPT",
    "DATASET",
    "INDEX",
    "EMBEDDING",
    "RETRIEVER",
    "SKILL",
    "MCP_SERVER",
    "TOOL_SCHEMA",
    "POLICY",
    "MEMORY_POLICY",
    "RUNTIME_PROFILE",
    "CONNECTOR",
]
DatasetRole = Literal["RUNTIME_DATA", "EVALUATION_DATA", "SEALED_HOLDOUT"]
DependencyRelation = Literal["DEPENDS_ON", "INVOKES", "DATA_FLOW", "CONTAINS", "REFERENCES"]
IdentityAssurance = Literal[
    "IMMUTABLE_DIGEST",
    "PROVIDER_VERSION",
    "MUTABLE_ALIAS",
    "OBSERVED_ONLY",
    "UNKNOWN",
]
AttesterTrustRole = Literal["integrator", "catalog_admin", "trusted_builder"]
AttestationScope = Literal["INITIAL_DESIRED_ASSIGNMENT"]
AssignmentTransitionKind = Literal[
    "BOOTSTRAP",
    "SET_DESIRED",
    "FREEZE_EXPOSURE",
    "RESUME_AFTER_ROLLBACK",
    "RETIRE",
]
AssignmentLifecycleState = Literal["ACTIVE", "RETIRED"]
AssignmentExposure = Literal["EXPOSED", "STOPPED"]


class ApplicationRegisterRequest(WireModel):
    schema_version: SchemaVersion2
    project_id: ProjectId
    slug: Slug
    display_name: Annotated[str, Field(min_length=1, max_length=256)]
    owner_principal_ids: Annotated[list[PrincipalId], Field(min_length=1, max_length=32)]
    criticality: Criticality
    data_classification: DataClassification
    governance_mode: GovernanceMode

    @field_validator("owner_principal_ids")
    @classmethod
    def owner_principals_are_unique(cls, value: list[PrincipalId]) -> list[PrincipalId]:
        return _require_unique(value, "owner_principal_ids")


class EnvironmentRegisterRequest(WireModel):
    schema_version: SchemaVersion2
    application_id: ApplicationId
    logical_name: LogicalName
    risk_classification: RiskClassification


class ComponentRegisterRequest(WireModel):
    schema_version: SchemaVersion2
    application_id: ApplicationId
    component_kind: ComponentKind
    logical_name: LogicalName
    owner_principal_ids: Annotated[list[PrincipalId], Field(min_length=1, max_length=32)]
    criticality: Criticality
    data_classification: DataClassification
    permission_classification: PermissionClassification
    effect_classification: EffectClassification
    dataset_role: DatasetRole | None = None

    @field_validator("owner_principal_ids")
    @classmethod
    def owner_principals_are_unique(cls, value: list[PrincipalId]) -> list[PrincipalId]:
        return _require_unique(value, "owner_principal_ids")


class DependencyEdgeRecordRequest(WireModel):
    schema_version: SchemaVersion2
    application_id: ApplicationId
    from_component_id: ComponentId
    to_component_id: ComponentId
    relation: DependencyRelation
    required: StrictBool

    @model_validator(mode="after")
    def edge_is_not_self(self) -> "DependencyEdgeRecordRequest":
        if self.from_component_id == self.to_component_id:
            raise ValueError("dependency edge cannot connect a component to itself")
        return self


class RecordEnvelope(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    revision: Annotated[StrictInt, Field(ge=1)]
    recorded_by_principal: PrincipalId
    recorded_at: AwareDatetime
    immutable: StrictBool
    hash_rule: Literal["jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"]
    record_digest: Digest
    authority_receipt_id: Annotated[str, Field(pattern=r"^arec_[0-9A-Za-z]{8,64}$")]

    @model_validator(mode="after")
    def envelope_is_immutable(self) -> "RecordEnvelope":
        if self.immutable is not True:
            raise ValueError("v5 record envelope must be immutable")
        return self


class ExactApplicationBinding(WireModel):
    kind: Literal["AI_APPLICATION"]
    id: ApplicationId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest

    def __getitem__(self, key: str):
        return getattr(self, key)


class ExactSystemComponentBinding(WireModel):
    kind: Literal["SYSTEM_COMPONENT"]
    id: ComponentId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest

    def __getitem__(self, key: str):
        return getattr(self, key)


class ApplicationRecord(WireModel):
    record_envelope: RecordEnvelope
    application_id: ApplicationId
    workspace_id: WorkspaceId
    project_id: ProjectId
    slug: Slug
    display_name: Annotated[str, Field(min_length=1, max_length=256)]
    owner_principal_ids: Annotated[list[PrincipalId], Field(min_length=1, max_length=32)]
    criticality: Criticality
    data_classification: DataClassification
    governance_mode: GovernanceMode
    lifecycle_state: LifecycleState
    exact_previous_application_binding_or_null: None = None
    exact_previous_application_binding: ExactApplicationBinding | None = None

    @field_validator("owner_principal_ids")
    @classmethod
    def owner_principals_are_unique(cls, value: list[PrincipalId]) -> list[PrincipalId]:
        return _require_unique(value, "owner_principal_ids")

    @model_validator(mode="after")
    def previous_binding_matches_revision(self) -> "ApplicationRecord":
        fields = self.model_fields_set
        if self.record_envelope.revision == 1:
            if (
                self.lifecycle_state != "REGISTERED"
                or "exact_previous_application_binding_or_null" not in fields
                or "exact_previous_application_binding" in fields
            ):
                raise ValueError("registered application requires the null previous binding")
        elif (
            "exact_previous_application_binding" not in fields
            or self.exact_previous_application_binding is None
            or "exact_previous_application_binding_or_null" in fields
        ):
            raise ValueError("application transition requires an exact previous binding")
        return self

    @model_serializer(mode="wrap")
    def serialize_revision_shape(self, handler):
        payload = handler(self)
        if self.record_envelope.revision == 1:
            payload.pop("exact_previous_application_binding", None)
        else:
            payload.pop("exact_previous_application_binding_or_null", None)
        return payload


class EnvironmentRecord(WireModel):
    record_envelope: RecordEnvelope
    environment_id: CatalogEnvironmentId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    logical_name: LogicalName
    risk_classification: RiskClassification
    lifecycle_state: EnvironmentLifecycleState


class ComponentRecord(WireModel):
    record_envelope: RecordEnvelope
    component_id: ComponentId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    component_kind: ComponentKind
    logical_name: LogicalName
    owner_principal_ids: Annotated[list[PrincipalId], Field(min_length=1, max_length=32)]
    criticality: Criticality
    data_classification: DataClassification
    permission_classification: PermissionClassification
    effect_classification: EffectClassification
    dataset_role: DatasetRole | None
    lifecycle_state: ComponentLifecycleState
    exact_previous_system_component_binding_or_null: None = None
    exact_previous_system_component_binding: ExactSystemComponentBinding | None = None

    @field_validator("owner_principal_ids")
    @classmethod
    def owner_principals_are_unique(cls, value: list[PrincipalId]) -> list[PrincipalId]:
        return _require_unique(value, "owner_principal_ids")

    @model_validator(mode="after")
    def previous_binding_matches_revision(self) -> "ComponentRecord":
        fields = self.model_fields_set
        if self.record_envelope.revision == 1:
            if (
                self.lifecycle_state != "REGISTERED"
                or "exact_previous_system_component_binding_or_null" not in fields
                or "exact_previous_system_component_binding" in fields
            ):
                raise ValueError("registered component requires the null previous binding")
        elif (
            "exact_previous_system_component_binding" not in fields
            or self.exact_previous_system_component_binding is None
            or "exact_previous_system_component_binding_or_null" in fields
        ):
            raise ValueError("component transition requires an exact previous binding")
        return self

    @model_serializer(mode="wrap")
    def serialize_revision_shape(self, handler):
        payload = handler(self)
        if self.record_envelope.revision == 1:
            payload.pop("exact_previous_system_component_binding", None)
        else:
            payload.pop("exact_previous_system_component_binding_or_null", None)
        return payload


class DependencyEdgeRecord(WireModel):
    record_envelope: RecordEnvelope
    edge_id: EdgeId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    from_component_id: ComponentId
    to_component_id: ComponentId
    relation: DependencyRelation
    required: StrictBool
    edge_digest: Digest


ApplicationListCursor = Annotated[
    str,
    Field(pattern=r"^cur_[0-9A-Za-z_-]{8,512}$"),
]


class ApplicationListItem(WireModel):
    application: ApplicationRecord
    environments: list[EnvironmentRecord]
    system_components: list[ComponentRecord]
    dependency_edges: list[DependencyEdgeRecord]

    @model_validator(mode="after")
    def graph_bindings_match_application(self) -> "ApplicationListItem":
        application = self.application
        component_ids = {item.component_id for item in self.system_components}
        children = [
            *self.environments,
            *self.system_components,
            *self.dependency_edges,
        ]
        if any(
            item.workspace_id != application.workspace_id
            or item.application_id != application.application_id
            for item in children
        ):
            raise ValueError("application list graph child binding mismatch")
        if any(
            edge.from_component_id not in component_ids
            or edge.to_component_id not in component_ids
            for edge in self.dependency_edges
        ):
            raise ValueError("application list edge endpoint is not in the graph")
        return self


class ApplicationListResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    items: list[ApplicationListItem]
    next_cursor: ApplicationListCursor | None

    @model_validator(mode="after")
    def items_match_workspace_and_are_unique(self) -> "ApplicationListResponse":
        ids = [item.application.application_id for item in self.items]
        if any(
            item.application.workspace_id != self.workspace_id for item in self.items
        ) or len(ids) != len(set(ids)):
            raise ValueError("application list item binding mismatch")
        return self


# The V5 idempotency receipt extends the frozen v1 format's intent map with the
# catalog intents.  The frozen ``models.IdempotencyReceipt`` is not edited;
# this is a schema-major-2 sibling used by the catalog transports.
V5IdempotencyIntent = Literal[
    "applications.register",
    "environments.register",
    "system-components.register",
    "dependency-edges.record",
    "system-manifests.import",
    "system-versions.record",
    "cases.bind-application",
    "acceptance-criteria.propose",
    "acceptance-criteria.confirm",
    "investigations.start",
    "operations.cancel-request",
]


class V5IdempotencyResource(WireModel):
    kind: Literal[
        "ai_application",
        "environment",
        "system_component",
        "dependency_edge",
        "system_version_set",
        "application_case_binding",
        "acceptance_criteria_revision",
        "automation_request",
    ]
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*_[0-9A-Za-z]{8,64}$")]


class V5IdempotencyReceipt(WireModel):
    schema_version: Literal["1.0"]
    workspace_id: WorkspaceId
    principal_id: PrincipalId
    intent: V5IdempotencyIntent
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]
    request_fingerprint: Digest
    resource: V5IdempotencyResource
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
    def intent_binding_is_exact(self) -> "V5IdempotencyReceipt":
        if self.immutable is not True:
            raise ValueError("idempotency receipt must be immutable")
        expected: dict[str, tuple[str, str, bool, str]] = {
            "applications.register": ("ai_application", "app_", False, "COMPLETED"),
            "environments.register": ("environment", "env_", False, "COMPLETED"),
            "system-components.register": ("system_component", "cmp_", False, "COMPLETED"),
            "dependency-edges.record": ("dependency_edge", "de_", False, "COMPLETED"),
            "system-manifests.import": ("system_version_set", "vset_", False, "COMPLETED"),
            "system-versions.record": ("system_version_set", "vset_", False, "COMPLETED"),
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
            "investigations.start": (
                "automation_request",
                "arq_",
                True,
                "ACCEPTED",
            ),
            "operations.cancel-request": (
                "automation_request",
                "arq_",
                True,
                "ACCEPTED",
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


class V5IdempotencyDelivery(WireModel):
    receipt: V5IdempotencyReceipt
    replayed: StrictBool


AutomationRequestId = Annotated[str, Field(pattern=r"^arq_[0-9A-Za-z]{8,64}$")]
WorkTaskId = Annotated[str, Field(pattern=r"^task_[0-9A-Za-z]{8,64}$")]
WorkAttemptId = Annotated[str, Field(pattern=r"^att_[0-9A-Za-z]{8,64}$")]
OperationCursor = Annotated[str, Field(pattern=r"^opcur_[0-9A-Za-z_-]{8,512}$")]
OperationState = Literal[
    "SUBMITTED",
    "WORKING",
    "INPUT_REQUIRED",
    "AUTH_REQUIRED",
    "CANCEL_REQUESTED",
    "CANCELED",
    "REJECTED",
    "FAILED",
    "COMPLETED",
]


class InvestigationStartRequest(WireModel):
    schema_version: SchemaVersion2
    case_revision: Annotated[StrictInt, Field(ge=1)]
    case_digest: Digest
    instructions: Annotated[str, Field(min_length=1, max_length=4000)] | None = None
    max_attempts: Annotated[StrictInt, Field(ge=1, le=10)] = 3


class ExactOperationCaseBinding(WireModel):
    case_id: CaseId
    case_revision: Annotated[StrictInt, Field(ge=1)]
    case_digest: Digest


class ExactWorkTaskBinding(WireModel):
    kind: Literal["WORK_TASK"]
    id: WorkTaskId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class ExactWorkAttemptBinding(WireModel):
    kind: Literal["WORK_ATTEMPT"]
    id: WorkAttemptId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class ExactDomainArtifactBinding(WireModel):
    kind: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*_[0-9A-Za-z]{8,128}$")]
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class OperationArtifact(WireModel):
    artifact_kind: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
    schema_major: Annotated[StrictInt, Field(ge=1)]
    domain_verdict: Annotated[str, Field(min_length=1, max_length=64)]
    evidence_completeness: Literal["COMPLETE", "PARTIAL", "UNKNOWN"]
    exact_artifact_binding: ExactDomainArtifactBinding
    payload: dict[str, Any]

    @model_validator(mode="after")
    def artifact_kind_matches_binding(self) -> "OperationArtifact":
        if self.artifact_kind != self.exact_artifact_binding.kind:
            raise ValueError("artifact kind does not match exact binding")
        return self


class OperationRecord(WireModel):
    operation_id: OperationId
    automation_request_id: AutomationRequestId
    canonical_intent: Literal["investigations.start"]
    state: OperationState
    requester_principal: PrincipalId
    exact_case_binding: ExactOperationCaseBinding
    application_id: ApplicationId
    environment_id: CatalogEnvironmentId
    exact_work_task_binding: ExactWorkTaskBinding
    exact_current_attempt_binding_or_null: ExactWorkAttemptBinding | None
    cancel_requested: StrictBool
    artifact_or_null: OperationArtifact | None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def terminal_artifact_semantics(self) -> "OperationRecord":
        if (self.state == "COMPLETED") != (self.artifact_or_null is not None):
            raise ValueError("only completed operations carry a trusted artifact")
        return self


class InvestigationStartResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    operation: OperationRecord
    idempotency: V5IdempotencyDelivery


class OperationGetResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    operation: OperationRecord


class OperationListResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    items: list[OperationRecord]
    next_cursor: OperationCursor | None

    @model_validator(mode="after")
    def items_are_workspace_safe_and_unique(self) -> "OperationListResponse":
        ids = [item.operation_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("operation list contains duplicate ids")
        return self


class OperationCancelRequest(WireModel):
    schema_version: SchemaVersion2
    reason: Annotated[str, Field(min_length=1, max_length=256)]


class OperationCancelResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    operation: OperationRecord
    idempotency: V5IdempotencyDelivery


class ApplicationRegisterResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    application: ApplicationRecord
    idempotency: V5IdempotencyDelivery


class ApplicationGetResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    application: ApplicationRecord


class EnvironmentRegisterResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    environment: EnvironmentRecord
    idempotency: V5IdempotencyDelivery


class EnvironmentGetResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    environment: EnvironmentRecord


class ComponentRegisterResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    component: ComponentRecord
    idempotency: V5IdempotencyDelivery


class ComponentGetResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    component: ComponentRecord


class DependencyEdgeRecordResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    edge: DependencyEdgeRecord
    idempotency: V5IdempotencyDelivery


class DependencyEdgeGetResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    edge: DependencyEdgeRecord


# ----------------------------------------------------------------------------
# V5-1B trusted manifest import / immutable system versions.
#
# The manifest wire shape is a DRAFT runtime interpretation (field_contract_ref
# is null in the frozen contracts); it is documented as honest uncertainty in
# evidence/v5/stage-1/system-version.  Discriminator fields follow
# contracts/v5/domain-model.yaml#identity_assurance.  ``approver_policy`` is a
# trusted human approver POLICY revision that is recorded but never enters the
# runtime SystemVersionSet bindings.
# ----------------------------------------------------------------------------


class ManifestApplication(WireModel):
    project_id: ProjectId
    slug: Slug
    display_name: Annotated[str, Field(min_length=1, max_length=256)]
    owner_principal_ids: Annotated[list[PrincipalId], Field(min_length=1, max_length=32)]
    criticality: Criticality
    data_classification: DataClassification
    governance_mode: GovernanceMode

    @field_validator("owner_principal_ids")
    @classmethod
    def owner_principals_are_unique(cls, value: list[PrincipalId]) -> list[PrincipalId]:
        return _require_unique(value, "owner_principal_ids")


class ManifestEnvironment(WireModel):
    logical_name: LogicalName
    risk_classification: RiskClassification


class ManifestRevisionSpec(WireModel):
    identity_locator: dict[str, Any]
    identity_assurance: IdentityAssurance
    content_digest: Digest | None = None
    declared_version: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    provider_origin: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    resolved_at: AwareDatetime | None = None
    immutable_provider_version_attestation: dict[str, Any] | None = None
    exact_observation_receipt_binding: dict[str, Any] | None = None
    unknown_reason: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    interface_schema_digest: Digest | None = None
    permission_manifest_digest: Digest | None = None
    dependency_lock_digest: Digest | None = None
    artifact_refs: list[dict[str, Any]] | None = None
    exact_provenance_receipt_bindings: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def identity_assurance_discriminator_is_closed(self) -> "ManifestRevisionSpec":
        if self.identity_assurance == "IMMUTABLE_DIGEST" and self.content_digest is None:
            raise ValueError("IMMUTABLE_DIGEST requires content_digest")
        if self.identity_assurance == "PROVIDER_VERSION" and not (
            self.provider_origin
            and self.declared_version
            and self.immutable_provider_version_attestation is not None
            and self.resolved_at is not None
        ):
            raise ValueError(
                "PROVIDER_VERSION requires provider_origin, declared_version, "
                "immutable_provider_version_attestation and resolved_at"
            )
        if self.identity_assurance == "MUTABLE_ALIAS" and not (
            self.provider_origin and self.resolved_at is not None
        ):
            raise ValueError("MUTABLE_ALIAS requires provider_origin and resolved_at")
        if self.identity_assurance == "OBSERVED_ONLY" and not (
            self.exact_observation_receipt_binding is not None and self.resolved_at is not None
        ):
            raise ValueError(
                "OBSERVED_ONLY requires exact_observation_receipt_binding and resolved_at"
            )
        if self.identity_assurance == "UNKNOWN" and not self.unknown_reason:
            raise ValueError("UNKNOWN requires unknown_reason")
        if self.identity_assurance != "IMMUTABLE_DIGEST" and self.content_digest is not None:
            raise ValueError("content_digest is only valid for IMMUTABLE_DIGEST")
        return self


class ManifestComponent(WireModel):
    logical_name: LogicalName
    component_kind: ComponentKind
    owner_principal_ids: Annotated[list[PrincipalId], Field(min_length=1, max_length=32)]
    criticality: Criticality
    data_classification: DataClassification
    permission_classification: PermissionClassification
    effect_classification: EffectClassification
    dataset_role: DatasetRole | None = None
    revision: ManifestRevisionSpec

    @field_validator("owner_principal_ids")
    @classmethod
    def owner_principals_are_unique(cls, value: list[PrincipalId]) -> list[PrincipalId]:
        return _require_unique(value, "owner_principal_ids")


class ManifestEdge(WireModel):
    from_component: LogicalName
    to_component: LogicalName
    relation: DependencyRelation
    required: StrictBool

    @model_validator(mode="after")
    def edge_is_not_self(self) -> "ManifestEdge":
        if self.from_component == self.to_component:
            raise ValueError("dependency edge cannot connect a component to itself")
        return self


class SystemManifestImportRequest(WireModel):
    schema_version: SchemaVersion2
    application: ManifestApplication
    environment: ManifestEnvironment
    components: Annotated[list[ManifestComponent], Field(min_length=1, max_length=256)]
    dependency_edges: list[ManifestEdge] = []
    approver_policy: ManifestComponent | None = None

    @model_validator(mode="after")
    def manifest_is_self_consistent(self) -> "SystemManifestImportRequest":
        names = [component.logical_name for component in self.components]
        if len(names) != len(set(names)):
            raise ValueError("manifest component logical names must be unique")
        if not any(
            component.component_kind == "APPLICATION_CODE" for component in self.components
        ):
            raise ValueError("first manifest requires at least one APPLICATION_CODE component")
        if self.approver_policy is not None and self.approver_policy.component_kind != "POLICY":
            raise ValueError("approver_policy must be a POLICY component")
        if self.approver_policy is not None and (
            self.approver_policy.logical_name in names
            or any(
                self.approver_policy.logical_name == component.logical_name
                for component in self.components
            )
        ):
            raise ValueError("approver_policy logical name must be distinct from components")
        by_name = set(names)
        adjacency: dict[str, list[str]] = {name: [] for name in names}
        for edge in self.dependency_edges:
            if edge.from_component not in by_name or edge.to_component not in by_name:
                raise ValueError("manifest edge references an unknown component")
            adjacency[edge.from_component].append(edge.to_component)

        def _cycles(start: str) -> bool:
            stack = list(adjacency[start])
            seen: set[str] = set()
            while stack:
                node = stack.pop()
                if node == start:
                    return True
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(adjacency.get(node, []))
            return False

        for name in names:
            if _cycles(name):
                raise ValueError("manifest dependency graph must be acyclic")
        return self


ExactEvidenceId = Annotated[
    str, Field(pattern=r"^[a-z][a-z0-9]*_[0-9A-Za-z]{8,64}$")
]


class ExactV4EvidenceBinding(WireModel):
    contract_major: Literal[1]
    kind: Literal[
        "TRACE_EVIDENCE_RECEIPT",
        "MODEL_CALL_RECEIPT",
        "RESOLUTION_REVIEW_RECEIPT",
    ]
    id: ExactEvidenceId
    revision: Annotated[StrictInt, Field(ge=1)] | None
    digest: Digest


class ExactV5EvidenceBinding(WireModel):
    kind: Literal[
        "SYSTEM_EPISODE_SNAPSHOT",
        "OBSERVED_STATE_SNAPSHOT",
        "OPERATION_EXECUTION_RECEIPT",
        "EXTERNAL_EFFECT_RECEIPT",
    ]
    id: ExactEvidenceId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


ExactEvidenceBinding = ExactV4EvidenceBinding | ExactV5EvidenceBinding


class ExactDependencyEdgeBinding(WireModel):
    kind: Literal["DEPENDENCY_EDGE"]
    id: EdgeId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class ExactComponentRevisionBinding(WireModel):
    kind: Literal["COMPONENT_REVISION"]
    id: ComponentRevisionId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class ExactTopologyRevisionBinding(WireModel):
    kind: Literal["TOPOLOGY_REVISION"]
    id: TopologyRevisionId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class ExactSystemVersionSetBinding(WireModel):
    kind: Literal["SYSTEM_VERSION_SET"]
    id: SystemVersionSetId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class ExactSystemAssignmentBinding(WireModel):
    kind: Literal["SYSTEM_ASSIGNMENT"]
    id: AssignmentId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class ExactSlotVersionSetBinding(ExactSystemVersionSetBinding):
    slot: Literal["PRIMARY"]


class ExactBootstrapAttestationAuthorityBinding(WireModel):
    binding_kind: Literal["BOOTSTRAP_ATTESTATION"]
    id: BootstrapAttestationId
    revision: Annotated[StrictInt, Field(ge=1)]
    digest: Digest


class IdentityAssuranceEntry(WireModel):
    component_revision_id: ComponentRevisionId
    component_id: ComponentId
    identity_assurance: IdentityAssurance


class IdentityAssuranceSummary(WireModel):
    component_assurances: Annotated[
        list[IdentityAssuranceEntry], Field(min_length=1)
    ]

    @model_validator(mode="after")
    def component_assurances_are_unique(self) -> "IdentityAssuranceSummary":
        revision_ids = [item.component_revision_id for item in self.component_assurances]
        component_ids = [item.component_id for item in self.component_assurances]
        if len(revision_ids) != len(set(revision_ids)) or len(component_ids) != len(
            set(component_ids)
        ):
            raise ValueError("identity assurance summary bindings must be unique")
        return self


class ComponentRevisionRecord(WireModel):
    record_envelope: RecordEnvelope
    component_revision_id: ComponentRevisionId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    component_id: ComponentId
    exact_system_component_binding: ExactSystemComponentBinding
    component_kind: ComponentKind
    logical_name: LogicalName
    identity_locator: dict[str, Any]
    identity_assurance: IdentityAssurance
    configuration_digest: Digest
    exact_provenance_receipt_bindings: list[ExactEvidenceBinding]
    declared_version: str | None = None
    content_digest: Digest | None = None
    provider_origin: str | None = None
    resolved_at: AwareDatetime | None = None
    immutable_provider_version_attestation: dict[str, Any] | None = None
    exact_observation_receipt_binding: ExactV5EvidenceBinding | None = None
    unknown_reason: str | None = None
    interface_schema_digest: Digest | None = None
    permission_manifest_digest: Digest | None = None
    dependency_lock_digest: Digest | None = None
    dataset_role: DatasetRole | None = None
    artifact_refs: list[dict[str, Any]] | None = None


class TopologyRevisionRecord(WireModel):
    record_envelope: RecordEnvelope
    topology_revision_id: TopologyRevisionId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    component_ids: list[ComponentId]
    exact_edge_revision_bindings: list[ExactDependencyEdgeBinding]
    topology_digest: Digest
    provenance_receipt_ids: list[AuthorityReceiptId]


class SystemVersionSetRecord(WireModel):
    record_envelope: RecordEnvelope
    system_version_set_id: SystemVersionSetId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    declared_environment_id: CatalogEnvironmentId
    exact_component_revision_bindings: list[ExactComponentRevisionBinding]
    exact_topology_revision_binding: ExactTopologyRevisionBinding
    identity_assurance_summary: IdentityAssuranceSummary
    provenance_receipt_ids: list[AuthorityReceiptId]
    version_set_digest: Digest
    manifest_digest: Digest | None = None
    manifest: dict[str, Any] | None = None


class RecordedSystemVersionSet(SystemVersionSetRecord):
    """D2-frozen standalone-record wire shape: adds exact previous lineage.

    Bootstrap-created first version sets carry a NULL previous binding;
    standalone records of the second and later version sets must bind the
    exact previous authoritative version set (CAS lineage).
    """

    exact_previous_system_version_set_binding_or_null: (
        ExactSystemVersionSetBinding | None
    ) = None


class SystemVersionRecordRequest(WireModel):
    """D2-frozen standalone record request.

    References only existing authority-valid objects: ACTIVE application and
    environment, exact component revision bindings, exact topology revision
    binding, and (from the second version set on) the exact previous version
    set binding.  Server derives ``identity_assurance_summary`` and
    ``version_set_digest``.
    """

    schema_version: SchemaVersion2
    application_id: ApplicationId
    environment_id: CatalogEnvironmentId
    exact_component_revision_bindings: Annotated[
        list[ExactComponentRevisionBinding], Field(min_length=1, max_length=256)
    ]
    exact_topology_revision_binding: ExactTopologyRevisionBinding
    exact_previous_system_version_set_binding_or_null: (
        ExactSystemVersionSetBinding | None
    ) = None


class SystemVersionRecordResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    system_version_set: RecordedSystemVersionSet
    idempotency: V5IdempotencyDelivery


class BootstrapAttestationRecord(WireModel):
    record_envelope: RecordEnvelope
    bootstrap_attestation_id: BootstrapAttestationId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    environment_id: CatalogEnvironmentId
    exact_initial_system_version_set_binding: ExactSystemVersionSetBinding
    attester_principal_id: PrincipalId
    attester_trust_role: AttesterTrustRole
    attestation_scope: AttestationScope


class SystemAssignmentRecord(WireModel):
    record_envelope: RecordEnvelope
    assignment_id: AssignmentId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    environment_id: CatalogEnvironmentId
    generation: Annotated[StrictInt, Field(ge=1)]
    lifecycle_state: Literal["ACTIVE"]
    transition_kind: Literal["BOOTSTRAP"]
    exact_previous_assignment_binding_or_null: None
    exact_slot_version_set_bindings: Annotated[
        list[ExactSlotVersionSetBinding], Field(min_length=1, max_length=1)
    ]
    exposure: Literal["EXPOSED"]
    expected_previous_generation: None
    exact_assignment_authority_binding: ExactBootstrapAttestationAuthorityBinding
    requested_by_external_operation_id: Annotated[
        str, Field(pattern=r"^op_[0-9A-Za-z]{8,64}$")
    ] | None = None

    @model_validator(mode="after")
    def r2_bootstrap_assignment_is_exact(self) -> "SystemAssignmentRecord":
        if self.generation != 1 or self.requested_by_external_operation_id is not None:
            raise ValueError("R2 bootstrap assignment must be initial and local")
        return self


class SystemManifestImportResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    manifest_id: SystemManifestId
    manifest_digest: Digest
    application: ApplicationRecord
    environment: EnvironmentRecord
    components: list[ComponentRecord]
    dependency_edges: list[DependencyEdgeRecord]
    component_revisions: list[ComponentRevisionRecord]
    topology_revision: TopologyRevisionRecord
    system_version_set: SystemVersionSetRecord
    bootstrap_attestation: BootstrapAttestationRecord
    system_assignment: SystemAssignmentRecord
    approver_policy_revision: ComponentRevisionRecord | None = None
    idempotency: V5IdempotencyDelivery


class SystemVersionGetResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    system_version_set: RecordedSystemVersionSet


class ChangedComponent(WireModel):
    component_id: ComponentId
    from_binding: ExactComponentRevisionBinding
    to_binding: ExactComponentRevisionBinding


class TopologyChange(WireModel):
    kind: Literal["EDGE_ADDED", "EDGE_REMOVED", "EDGE_REVISION_CHANGED"]
    from_edge_binding_or_null: ExactDependencyEdgeBinding | None = None
    to_edge_binding_or_null: ExactDependencyEdgeBinding | None = None


class AssuranceDelta(WireModel):
    identity_assurance_changes: list[str] = []


class VersionDiff(WireModel):
    """D2-frozen deterministic diff between two exact version sets."""

    added: list[ExactComponentRevisionBinding] = []
    removed: list[ExactComponentRevisionBinding] = []
    changed: list[ChangedComponent] = []
    topology_changes: list[TopologyChange] = []
    assurance_delta: AssuranceDelta = AssuranceDelta()
    deterministic: Literal[True] = True


class SystemVersionDiffResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    source_binding: ExactSystemVersionSetBinding
    target_binding: ExactSystemVersionSetBinding
    diff: VersionDiff


# ----------------------------------------------------------------------------
# V5-1C application case binding / acceptance criteria wire models.
#
# DRAFT runtime interpretation (field_contract_ref null in the frozen
# contracts).  The binding is an additive link to an immutable S1A case; the
# acceptance proposal is untrusted and never self-confirmable.  The resolution
# contract runtime is a later slice, so its exact binding is recorded with an
# honest ``materialization: DECLARED_BY_CASE`` marker until V5-4 materializes
# real ResolutionContract records.
# ----------------------------------------------------------------------------

CaseBindingId = Annotated[str, Field(pattern=r"^acb_[0-9A-Za-z]{8,64}$")]
AcceptanceCriteriaRevisionId = Annotated[
    str, Field(pattern=r"^acr_[0-9A-Za-z]{8,64}$")
]
IssueSnapshotId = Annotated[str, Field(pattern=r"^iss_[0-9A-Za-z]{8,64}$")]
ExactCaseBinding = dict[str, Any]
ExactResolutionContractBinding = dict[str, Any]
SystemVersionSetBindingOrUnknown = (
    dict[str, Any] | Literal["UNKNOWN"] | None
)
ConfirmationStatus = Literal["PROPOSED", "CONFIRMED"]
CaseReadiness = Literal["NEEDS_ACCEPTANCE_CRITERIA", "PENDING_MATERIALIZATION", "READY"]


class IssueSnapshotRequest(WireModel):
    source_kind: Literal["github_issue", "manual"]
    source_url: Annotated[str, Field(min_length=1, max_length=1024)]
    external_repo: Annotated[str, Field(min_length=1, max_length=256)]
    external_issue_number: Annotated[StrictInt, Field(ge=1)]
    snapshot_payload: dict[str, Any]
    edited_flag: StrictBool = False
    deleted_flag: StrictBool = False
    fetched_at: AwareDatetime

    @model_validator(mode="after")
    def source_url_is_http(self) -> "IssueSnapshotRequest":
        if not self.source_url.startswith(("https://", "http://")):
            raise ValueError("issue source url must be http(s)")
        return self


class CaseBindApplicationRequest(WireModel):
    schema_version: SchemaVersion2
    case_id: CaseId
    case_revision: Annotated[StrictInt, Field(ge=1)]
    case_digest: Digest
    application_id: ApplicationId
    environment_id: CatalogEnvironmentId
    declared_system_version_set_binding_or_unknown: SystemVersionSetBindingOrUnknown = None
    issue_snapshot: IssueSnapshotRequest | None = None


class ApplicationCaseBindingRecord(WireModel):
    record_envelope: RecordEnvelope
    application_case_binding_id: CaseBindingId
    workspace_id: WorkspaceId
    exact_case_binding: ExactCaseBinding
    application_id: ApplicationId
    environment_id: CatalogEnvironmentId
    declared_system_version_set_binding_or_unknown: SystemVersionSetBindingOrUnknown
    binding_digest: Digest


class CaseBindApplicationResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    application_case_binding: ApplicationCaseBindingRecord
    idempotency: V5IdempotencyDelivery


class ApplicationBindingGetResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    application_case_binding: ApplicationCaseBindingRecord


class AcceptanceCriteriaProposeRequest(WireModel):
    schema_version: SchemaVersion2
    case_id: CaseId
    case_revision: Annotated[StrictInt, Field(ge=1)]
    case_digest: Digest
    acceptance_source: dict[str, Any]
    reproducer_input: dict[str, Any] | None = None
    reproducer_environment: dict[str, Any] | None = None
    expected_behavior: dict[str, Any]
    oracle_or_evaluator: dict[str, Any] | None = None
    applicable_workload_profile: dict[str, Any]
    applicable_deployment_profile: dict[str, Any]


class AcceptanceCriteriaRevisionRecord(WireModel):
    record_envelope: RecordEnvelope
    acceptance_criteria_revision_id: AcceptanceCriteriaRevisionId
    workspace_id: WorkspaceId
    exact_case_binding: ExactCaseBinding
    exact_resolution_contract_binding: ExactResolutionContractBinding
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
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    acceptance_criteria_revision: AcceptanceCriteriaRevisionRecord
    idempotency: V5IdempotencyDelivery


class AcceptanceCriteriaGetResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    exact_case_binding: ExactCaseBinding
    case_readiness: CaseReadiness
    revisions: list[AcceptanceCriteriaRevisionRecord] = []
    next_action: dict[str, Any] | None = None


class AcceptanceCriteriaConfirmRequest(WireModel):
    schema_version: SchemaVersion2
    exact_proposed_revision_binding: dict[str, Any]
    confirmation_note: Annotated[str, Field(max_length=2000)] | None = None


class AcceptanceCriteriaConfirmResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    acceptance_criteria_revision: AcceptanceCriteriaRevisionRecord
    idempotency: V5IdempotencyDelivery


__all__ = [
    "AcceptanceCriteriaConfirmRequest",
    "AcceptanceCriteriaConfirmResponse",
    "AcceptanceCriteriaGetResponse",
    "AcceptanceCriteriaProposeRequest",
    "AcceptanceCriteriaProposeResponse",
    "AcceptanceCriteriaRevisionId",
    "AcceptanceCriteriaRevisionRecord",
    "ApplicationBindingGetResponse",
    "ApplicationCaseBindingRecord",
    "ApplicationGetResponse",
    "ApplicationId",
    "ApplicationListCursor",
    "ApplicationListItem",
    "ApplicationListResponse",
    "ApplicationRecord",
    "ApplicationRegisterRequest",
    "ApplicationRegisterResponse",
    "AssignmentExposure",
    "AssignmentId",
    "AssignmentLifecycleState",
    "AssignmentTransitionKind",
    "AttesterTrustRole",
    "AttestationScope",
    "BootstrapAttestationId",
    "BootstrapAttestationRecord",
    "CaseBindApplicationRequest",
    "CaseBindApplicationResponse",
    "CaseBindingId",
    "CaseReadiness",
    "CatalogEnvironmentId",
    "ComponentGetResponse",
    "ComponentId",
    "ComponentRecord",
    "ComponentRegisterRequest",
    "ComponentRegisterResponse",
    "ComponentRevisionId",
    "ComponentRevisionRecord",
    "DependencyEdgeGetResponse",
    "DependencyEdgeRecord",
    "DependencyEdgeRecordRequest",
    "DependencyEdgeRecordResponse",
    "EdgeId",
    "EnvironmentGetResponse",
    "EnvironmentRecord",
    "EnvironmentRegisterRequest",
    "EnvironmentRegisterResponse",
    "ExactApplicationBinding",
    "ExactSystemComponentBinding",
    "IdentityAssurance",
    "IssueSnapshotId",
    "IssueSnapshotRequest",
    "InvestigationStartRequest",
    "InvestigationStartResponse",
    "ManifestApplication",
    "ManifestComponent",
    "ManifestEdge",
    "ManifestEnvironment",
    "ManifestRevisionSpec",
    "RecordEnvelope",
    "OperationArtifact",
    "OperationCancelRequest",
    "OperationCancelResponse",
    "OperationGetResponse",
    "OperationListResponse",
    "OperationRecord",
    "OperationState",
    "SchemaVersion2",
    "SystemAssignmentRecord",
    "SystemManifestId",
    "SystemManifestImportRequest",
    "SystemManifestImportResponse",
    "SystemVersionDiffResponse",
    "SystemVersionGetResponse",
    "SystemVersionSetId",
    "SystemVersionSetRecord",
    "TopologyRevisionId",
    "TopologyRevisionRecord",
    "V5IdempotencyDelivery",
    "V5IdempotencyIntent",
    "V5IdempotencyReceipt",
    "V5IdempotencyResource",
    "VersionDiffItem",
]
