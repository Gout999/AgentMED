"""Generated frozen AgentMED public v2 success wire models. Do not hand edit.

Hand-copied from ``control-plane/app/public_api/v5_models.py`` (schema-major-2)
so the CLI can validate /api/v2 responses without importing the control-plane
package.  Any change to that module must be mirrored here.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


SchemaVersion = Literal["2.0"]
WorkspaceId = Annotated[str, Field(pattern=r"^ws_[0-9A-Za-z]{8,64}$")]
ProjectId = Annotated[str, Field(pattern=r"^proj_[0-9A-Za-z]{8,64}$")]
PrincipalId = Annotated[str, Field(pattern=r"^prn_[0-9A-Za-z]{8,64}$")]
ApplicationId = Annotated[str, Field(pattern=r"^app_[0-9A-Za-z]{8,64}$")]
CatalogEnvironmentId = Annotated[str, Field(pattern=r"^env_[0-9A-Za-z]{8,64}$")]
ComponentId = Annotated[str, Field(pattern=r"^cmp_[0-9A-Za-z]{8,64}$")]
EdgeId = Annotated[str, Field(pattern=r"^de_[0-9A-Za-z]{8,64}$")]
RequestId = Annotated[str, Field(pattern=r"^req_[0-9A-Za-z]{8,64}$")]
AuditRef = Annotated[str, Field(pattern=r"^audit://aud_[0-9A-Za-z]{8,64}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
IdempotencyReceiptId = Annotated[str, Field(pattern=r"^idemr_[0-9A-Za-z]{8,64}$")]
AuthorityReceiptId = Annotated[str, Field(pattern=r"^arec_[0-9A-Za-z]{8,64}$")]
OperationId = Annotated[str, Field(pattern=r"^op_[0-9A-Za-z]{8,64}$")]


class RecordEnvelope(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    revision: Annotated[StrictInt, Field(ge=1)]
    recorded_by_principal: PrincipalId
    recorded_at: AwareDatetime
    immutable: StrictBool
    hash_rule: Literal["jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"]
    record_digest: Digest
    authority_receipt_id: AuthorityReceiptId

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
    slug: Annotated[str, Field(min_length=1, max_length=64)]
    display_name: Annotated[str, Field(min_length=1, max_length=256)]
    owner_principal_ids: Annotated[list[PrincipalId], Field(min_length=1, max_length=32)]
    criticality: Literal["P0", "P1", "P2", "P3"]
    data_classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    governance_mode: Literal["MANAGED", "OBSERVED"]
    lifecycle_state: Literal["ACTIVE", "ARCHIVED"]


class EnvironmentRecord(WireModel):
    record_envelope: RecordEnvelope
    environment_id: CatalogEnvironmentId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    logical_name: Annotated[str, Field(min_length=1, max_length=128)]
    risk_classification: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    lifecycle_state: Literal["ACTIVE", "RETIRED"]


class ComponentRecord(WireModel):
    record_envelope: RecordEnvelope
    component_id: ComponentId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    component_kind: str
    logical_name: Annotated[str, Field(min_length=1, max_length=128)]
    owner_principal_ids: Annotated[list[PrincipalId], Field(min_length=1, max_length=32)]
    criticality: Literal["P0", "P1", "P2", "P3"]
    data_classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    permission_classification: Literal["READ_ONLY", "READ_WRITE", "ELEVATED"]
    effect_classification: Literal["NONE", "LOCAL", "EXTERNAL"]
    dataset_role: Literal["RUNTIME_DATA", "EVALUATION_DATA", "SEALED_HOLDOUT"] | None
    lifecycle_state: Literal["ACTIVE", "DEPRECATED", "RETIRED"]


class DependencyEdgeRecord(WireModel):
    record_envelope: RecordEnvelope
    edge_id: EdgeId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    from_component_id: ComponentId
    to_component_id: ComponentId
    relation: Literal["DEPENDS_ON", "INVOKES", "DATA_FLOW", "CONTAINS", "REFERENCES"]
    required: StrictBool
    edge_digest: Digest


V5IdempotencyIntent = Literal[
    "applications.register",
    "environments.register",
    "system-components.register",
    "dependency-edges.record",
    "system-manifests.import",
]


class V5IdempotencyResource(WireModel):
    kind: Literal[
        "ai_application",
        "environment",
        "system_component",
        "dependency_edge",
        "system_version_set",
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
    operation_id: OperationId | None
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
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    application: ApplicationRecord
    idempotency: V5IdempotencyDelivery


class ApplicationGetResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    application: ApplicationRecord


class EnvironmentRegisterResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    environment: EnvironmentRecord
    idempotency: V5IdempotencyDelivery


class EnvironmentGetResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    environment: EnvironmentRecord


class ComponentRegisterResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    component: ComponentRecord
    idempotency: V5IdempotencyDelivery


class ComponentGetResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    component: ComponentRecord


class DependencyEdgeRecordResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    edge: DependencyEdgeRecord
    idempotency: V5IdempotencyDelivery


class DependencyEdgeGetResponse(WireModel):
    schema_version: SchemaVersion
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    edge: DependencyEdgeRecord


__all__ = [
    "ApplicationGetResponse",
    "ApplicationId",
    "ApplicationRecord",
    "ApplicationRegisterResponse",
    "CatalogEnvironmentId",
    "ComponentGetResponse",
    "ComponentId",
    "ComponentRecord",
    "ComponentRegisterResponse",
    "DependencyEdgeGetResponse",
    "DependencyEdgeRecord",
    "DependencyEdgeRecordResponse",
    "EdgeId",
    "EnvironmentGetResponse",
    "EnvironmentRecord",
    "EnvironmentRegisterResponse",
    "RecordEnvelope",
    "SchemaVersion",
    "V5IdempotencyDelivery",
    "V5IdempotencyReceipt",
    "WireModel",
]
