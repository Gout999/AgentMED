"""Generated frozen CaseLoop public v2 success wire models. Do not hand edit.

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
    field_validator,
    model_serializer,
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
Slug = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$",
        min_length=1,
        max_length=64,
    ),
]
LogicalName = Annotated[
    str,
    Field(
        pattern=r"^[a-z0-9](?:[a-z0-9_-]{0,127})$",
        min_length=1,
        max_length=128,
    ),
]
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
    criticality: Literal["P0", "P1", "P2", "P3"]
    data_classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    governance_mode: Literal["MANAGED", "OBSERVED"]
    lifecycle_state: Literal["REGISTERED", "ACTIVE", "ARCHIVED"]
    exact_previous_application_binding_or_null: None = None
    exact_previous_application_binding: ExactApplicationBinding | None = None

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
    risk_classification: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    lifecycle_state: Literal["ACTIVE", "RETIRED"]


class ComponentRecord(WireModel):
    record_envelope: RecordEnvelope
    component_id: ComponentId
    workspace_id: WorkspaceId
    application_id: ApplicationId
    component_kind: ComponentKind
    logical_name: LogicalName
    owner_principal_ids: Annotated[list[PrincipalId], Field(min_length=1, max_length=32)]
    criticality: Literal["P0", "P1", "P2", "P3"]
    data_classification: Literal["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]
    permission_classification: Literal["READ_ONLY", "READ_WRITE", "ELEVATED"]
    effect_classification: Literal["NONE", "LOCAL", "EXTERNAL"]
    dataset_role: Literal["RUNTIME_DATA", "EVALUATION_DATA", "SEALED_HOLDOUT"] | None
    lifecycle_state: Literal["REGISTERED", "ACTIVE", "DEPRECATED", "RETIRED"]
    exact_previous_system_component_binding_or_null: None = None
    exact_previous_system_component_binding: ExactSystemComponentBinding | None = None

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
    relation: Literal["DEPENDS_ON", "INVOKES", "DATA_FLOW", "CONTAINS", "REFERENCES"]
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
    schema_version: SchemaVersion
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


V5PublicIntentName = Literal[
    "capabilities.get",
    "applications.register",
    "applications.get",
    "applications.list",
    "environments.register",
    "environments.get",
    "system-components.register",
    "system-components.get",
    "dependency-edges.record",
    "dependency-edges.get",
    "system-manifests.import",
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
    execution_mode: Literal["synchronous", "synchronous_local_transaction"]
    http: StrictBool
    cli: StrictBool

    @model_validator(mode="after")
    def transports_are_real(self) -> "V5EnabledIntent":
        if self.http is not True or self.cli is not True:
            raise ValueError("advertised V5 intents require http=true and cli=true")
        expected_mode = (
            "synchronous_local_transaction"
            if self.name == "system-manifests.import"
            else "synchronous"
        )
        if self.execution_mode != expected_mode:
            raise ValueError("advertised V5 intent execution_mode mismatch")
        return self


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
    "ApplicationListCursor",
    "ApplicationListItem",
    "ApplicationListResponse",
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
    "ExactApplicationBinding",
    "ExactSystemComponentBinding",
    "RecordEnvelope",
    "SchemaVersion",
    "V5IdempotencyDelivery",
    "V5IdempotencyReceipt",
    "V5CapabilityPrincipal",
    "V5EnabledIntent",
    "V5PublicIntentName",
    "V5ServerCapabilitiesData",
    "V5ServerCapabilitiesResponse",
    "WireModel",
]
