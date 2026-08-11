"""Frozen CaseLoop public v2 manifest / system-version wire models.

Hand-copied from ``control-plane/app/public_api/v5_models.py`` (schema-major-2)
so the CLI can build and validate /api/v2 system-manifest payloads without
importing the control-plane package.  Any change to that module must be
mirrored here and in ``cli/src/caseloop_cli/client.py``.
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
    ApplicationRecord,
    AuditRef,
    AuthorityReceiptId,
    CatalogEnvironmentId,
    ComponentId,
    ComponentRecord,
    DependencyEdgeRecord,
    Digest,
    EdgeId,
    EnvironmentRecord,
    ExactSystemComponentBinding,
    PrincipalId,
    ProjectId,
    RecordEnvelope,
    RequestId,
    V5IdempotencyDelivery,
    WireModel,
    WorkspaceId,
)

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
LogicalName = Annotated[
    str,
    Field(pattern=r"^[a-z0-9](?:[a-z0-9_-]{0,127})$", min_length=1, max_length=128),
]
SchemaVersion2 = Literal["2.0"]


def _require_unique(values: list[str], field_name: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be unique")
    return values

ComponentRevisionId = Annotated[str, Field(pattern=r"^crv_[0-9A-Za-z]{8,64}$")]
TopologyRevisionId = Annotated[str, Field(pattern=r"^tpr_[0-9A-Za-z]{8,64}$")]
SystemVersionSetId = Annotated[str, Field(pattern=r"^vset_[0-9A-Za-z]{8,64}$")]
BootstrapAttestationId = Annotated[str, Field(pattern=r"^batt_[0-9A-Za-z]{8,64}$")]
AssignmentId = Annotated[str, Field(pattern=r"^asg_[0-9A-Za-z]{8,64}$")]
SystemManifestId = Annotated[str, Field(pattern=r"^smf_[0-9A-Za-z]{8,64}$")]

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


class ManifestApplication(WireModel):
    project_id: ProjectId
    slug: Annotated[
        str,
        Field(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$", min_length=1, max_length=64),
    ]
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
        if self.approver_policy is not None and self.approver_policy.logical_name in names:
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
    system_version_set: SystemVersionSetRecord


class VersionDiffItem(WireModel):
    component_id: ComponentId
    logical_name: LogicalName
    base_digest: Digest | None
    target_digest: Digest | None
    diff_kind: Literal[
        "DIGEST_CHANGED",
        "DEPENDENCY_SUBSTITUTION",
        "PERMISSION_EXPANSION",
        "ADDED",
        "REMOVED",
    ]
    details: dict[str, Any] = {}


class SystemVersionDiffResponse(WireModel):
    schema_version: SchemaVersion2
    workspace_id: WorkspaceId
    request_id: RequestId
    audit_ref: AuditRef
    base_system_version_set_id: SystemVersionSetId
    target_system_version_set_id: SystemVersionSetId
    added: list[VersionDiffItem] = []
    removed: list[VersionDiffItem] = []
    changed: list[VersionDiffItem] = []
    dependency_substitutions: list[VersionDiffItem] = []
    policy_permission_expansions: list[VersionDiffItem] = []
