"""Schema-major-2 wire models for the V5 application catalog public boundary.

These models validate transport data only.  They do not resolve credentials,
load authoritative state, or perform database work.  The V5 record envelope
follows ``contracts/v5/domain-model.yaml#record_envelope`` and the frozen
``contracts/v4`` ``models.py`` idempotency receipt format is extended here for
the four catalog intents without touching the frozen file.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AnyUrl,
    AwareDatetime,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    field_validator,
    model_validator,
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
    Digest,
    IdempotencyReceiptId,
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

Slug = Annotated[
    str,
    Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$", min_length=1, max_length=64),
]
LogicalName = Annotated[
    str,
    Field(pattern=r"^[a-z0-9](?:[a-z0-9_-]{0,127})$", min_length=1, max_length=128),
]

LifecycleState = Literal["ACTIVE", "ARCHIVED"]
EnvironmentLifecycleState = Literal["ACTIVE", "RETIRED"]
ComponentLifecycleState = Literal["ACTIVE", "DEPRECATED", "RETIRED"]
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

    @field_validator("owner_principal_ids")
    @classmethod
    def owner_principals_are_unique(cls, value: list[PrincipalId]) -> list[PrincipalId]:
        return _require_unique(value, "owner_principal_ids")


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

    @field_validator("owner_principal_ids")
    @classmethod
    def owner_principals_are_unique(cls, value: list[PrincipalId]) -> list[PrincipalId]:
        return _require_unique(value, "owner_principal_ids")


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


# The V5 idempotency receipt extends the frozen v1 format's intent map with the
# four catalog intents.  The frozen ``models.IdempotencyReceipt`` is not edited;
# this is a schema-major-2 sibling used by the catalog transports.
V5IdempotencyIntent = Literal[
    "applications.register",
    "environments.register",
    "system-components.register",
    "dependency-edges.record",
]


class V5IdempotencyResource(WireModel):
    kind: Literal["ai_application", "environment", "system_component", "dependency_edge"]
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


__all__ = [
    "ApplicationGetResponse",
    "ApplicationId",
    "ApplicationRecord",
    "ApplicationRegisterRequest",
    "ApplicationRegisterResponse",
    "CatalogEnvironmentId",
    "ComponentGetResponse",
    "ComponentId",
    "ComponentRecord",
    "ComponentRegisterRequest",
    "ComponentRegisterResponse",
    "DependencyEdgeGetResponse",
    "DependencyEdgeRecord",
    "DependencyEdgeRecordRequest",
    "DependencyEdgeRecordResponse",
    "EdgeId",
    "EnvironmentGetResponse",
    "EnvironmentRecord",
    "EnvironmentRegisterRequest",
    "EnvironmentRegisterResponse",
    "RecordEnvelope",
    "SchemaVersion2",
    "V5IdempotencyDelivery",
    "V5IdempotencyIntent",
    "V5IdempotencyReceipt",
    "V5IdempotencyResource",
]
