"""V5-1A AI application catalog projections.

Single-workspace runtime, but every key is workspace/project/environment
scoped so later multi-workspace cuts cannot be forced to reinterpret history.
The immutable JSON envelope payload remains the contract-bearing record; the
projected columns are derived projections for indexed access and console read
models.  V5 records use the nested schema-major-2 ``record_envelope``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.tables import Base

# V5-1A DRAFT field domains.  The frozen V5 contracts do not enumerate these
# value sets yet (field_contract_ref is null); these conservative closed
# domains are the runtime slice's interpretation and are reported as honest
# uncertainty in evidence/v5/stage-1/application-catalog.
CATALOG_LIFECYCLE_STATES = ("REGISTERED", "ACTIVE", "ARCHIVED")
ENVIRONMENT_LIFECYCLE_STATES = ("ACTIVE", "RETIRED")
COMPONENT_LIFECYCLE_STATES = ("REGISTERED", "ACTIVE", "DEPRECATED", "RETIRED")
CRITICALITY_VALUES = ("P0", "P1", "P2", "P3")
DATA_CLASSIFICATION_VALUES = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED")
GOVERNANCE_MODE_VALUES = ("MANAGED", "OBSERVED")
RISK_CLASSIFICATION_VALUES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
PERMISSION_CLASSIFICATION_VALUES = ("READ_ONLY", "READ_WRITE", "ELEVATED")
EFFECT_CLASSIFICATION_VALUES = ("NONE", "LOCAL", "EXTERNAL")
COMPONENT_KIND_VALUES = (
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
)
DATASET_ROLE_VALUES = ("RUNTIME_DATA", "EVALUATION_DATA", "SEALED_HOLDOUT")
DEPENDENCY_RELATION_VALUES = ("DEPENDS_ON", "INVOKES", "DATA_FLOW", "CONTAINS", "REFERENCES")
IDENTITY_ASSURANCE_VALUES = (
    "IMMUTABLE_DIGEST",
    "PROVIDER_VERSION",
    "MUTABLE_ALIAS",
    "OBSERVED_ONLY",
    "UNKNOWN",
)
ATTESTER_TRUST_ROLE_VALUES = ("integrator", "catalog_admin", "trusted_builder")
ATTESTATION_SCOPE_VALUES = ("INITIAL_DESIRED_ASSIGNMENT",)
ASSIGNMENT_TRANSITION_KIND_VALUES = (
    "BOOTSTRAP",
    "SET_DESIRED",
    "FREEZE_EXPOSURE",
    "RESUME_AFTER_ROLLBACK",
    "RETIRE",
)
ASSIGNMENT_LIFECYCLE_STATE_VALUES = ("ACTIVE", "RETIRED")
ASSIGNMENT_EXPOSURE_VALUES = ("EXPOSED", "STOPPED")


class AIApplication(Base):
    __tablename__ = "ai_applications"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "application_id", name="uq_ai_application_workspace"
        ),
        UniqueConstraint(
            "workspace_id",
            "project_id",
            "slug",
            name="uq_ai_application_workspace_project_slug",
        ),
        CheckConstraint(
            "lifecycle_state IN ('REGISTERED','ACTIVE','ARCHIVED')",
            name="ck_ai_application_lifecycle",
        ),
        CheckConstraint(
            "criticality IN ('P0','P1','P2','P3')", name="ck_ai_application_criticality"
        ),
        CheckConstraint(
            "data_classification IN ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')",
            name="ck_ai_application_data_classification",
        ),
        CheckConstraint(
            "governance_mode IN ('MANAGED','OBSERVED')",
            name="ck_ai_application_governance_mode",
        ),
        CheckConstraint("revision >= 1", name="ck_ai_application_revision"),
        Index(
            "ix_ai_application_workspace_lifecycle",
            "workspace_id",
            "lifecycle_state",
            "updated_at",
        ),
        Index("ix_ai_application_workspace_project", "workspace_id", "project_id"),
    )

    application_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    owner_principal_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    criticality: Mapped[str] = mapped_column(String(16), nullable=False)
    data_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    governance_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Environment(Base):
    __tablename__ = "environments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_environment_application",
        ),
        UniqueConstraint(
            "workspace_id", "environment_id", name="uq_environment_workspace"
        ),
        UniqueConstraint(
            "workspace_id",
            "application_id",
            "logical_name",
            name="uq_environment_workspace_application_name",
        ),
        CheckConstraint(
            "lifecycle_state IN ('ACTIVE','RETIRED')", name="ck_environment_lifecycle"
        ),
        CheckConstraint(
            "risk_classification IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="ck_environment_risk_classification",
        ),
        CheckConstraint("revision >= 1", name="ck_environment_revision"),
        Index(
            "ix_environment_workspace_application",
            "workspace_id",
            "application_id",
            "lifecycle_state",
        ),
    )

    environment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    logical_name: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class AIApplicationLifecycleRevision(Base):
    """Append-only authority for every AIApplication lifecycle revision.

    ``ai_applications`` is only the mutable current-head projection.  Exact
    historical bindings must resolve through this table and its independently
    integrity-checked record envelope.
    """

    __tablename__ = "ai_application_lifecycle_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_ai_application_lifecycle_head",
        ),
        UniqueConstraint(
            "record_digest", name="uq_ai_application_lifecycle_record_digest"
        ),
        UniqueConstraint(
            "authority_receipt_id",
            name="uq_ai_application_lifecycle_authority_receipt",
        ),
        CheckConstraint(
            "lifecycle_state IN ('REGISTERED','ACTIVE','ARCHIVED')",
            name="ck_ai_application_lifecycle_revision_state",
        ),
        CheckConstraint(
            "(revision = 1 AND lifecycle_state = 'REGISTERED' "
            "AND exact_previous_application_binding IS NULL) OR "
            "(revision > 1 AND exact_previous_application_binding IS NOT NULL)",
            name="ck_ai_application_lifecycle_revision_shape",
        ),
        Index(
            "ix_ai_application_lifecycle_current",
            "workspace_id",
            "application_id",
            "revision",
            "lifecycle_state",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    application_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    exact_previous_application_binding: Mapped[Optional[dict[str, Any]]] = (
        mapped_column(JSON(none_as_null=True), nullable=True)
    )
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SystemComponent(Base):
    __tablename__ = "system_components"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_system_component_application",
        ),
        UniqueConstraint(
            "workspace_id", "component_id", name="uq_system_component_workspace"
        ),
        UniqueConstraint(
            "workspace_id",
            "application_id",
            "component_id",
            name="uq_system_component_workspace_application_component",
        ),
        UniqueConstraint(
            "workspace_id",
            "application_id",
            "component_kind",
            "logical_name",
            name="uq_system_component_workspace_application_identity",
        ),
        CheckConstraint(
            "lifecycle_state IN ('REGISTERED','ACTIVE','DEPRECATED','RETIRED')",
            name="ck_system_component_lifecycle",
        ),
        CheckConstraint(
            "criticality IN ('P0','P1','P2','P3')", name="ck_system_component_criticality"
        ),
        CheckConstraint(
            "data_classification IN ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')",
            name="ck_system_component_data_classification",
        ),
        CheckConstraint(
            "permission_classification IN ('READ_ONLY','READ_WRITE','ELEVATED')",
            name="ck_system_component_permission_classification",
        ),
        CheckConstraint(
            "effect_classification IN ('NONE','LOCAL','EXTERNAL')",
            name="ck_system_component_effect_classification",
        ),
        CheckConstraint(
            "component_kind IN ('APPLICATION_CODE','AGENT','MODEL_BINDING','PROMPT',"
            "'DATASET','INDEX','EMBEDDING','RETRIEVER','SKILL','MCP_SERVER',"
            "'TOOL_SCHEMA','POLICY','MEMORY_POLICY','RUNTIME_PROFILE','CONNECTOR')",
            name="ck_system_component_kind",
        ),
        CheckConstraint("revision >= 1", name="ck_system_component_revision"),
        Index(
            "ix_system_component_workspace_application",
            "workspace_id",
            "application_id",
            "component_kind",
            "lifecycle_state",
        ),
    )

    component_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    component_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    logical_name: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_principal_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    criticality: Mapped[str] = mapped_column(String(16), nullable=False)
    data_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    permission_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    effect_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SystemComponentLifecycleRevision(Base):
    """Append-only authority for every SystemComponent lifecycle revision."""

    __tablename__ = "system_component_lifecycle_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id", "component_id"],
            [
                "system_components.workspace_id",
                "system_components.application_id",
                "system_components.component_id",
            ],
            name="fk_system_component_lifecycle_head",
        ),
        UniqueConstraint(
            "record_digest", name="uq_system_component_lifecycle_record_digest"
        ),
        UniqueConstraint(
            "authority_receipt_id",
            name="uq_system_component_lifecycle_authority_receipt",
        ),
        CheckConstraint(
            "lifecycle_state IN ('REGISTERED','ACTIVE','DEPRECATED','RETIRED')",
            name="ck_system_component_lifecycle_revision_state",
        ),
        CheckConstraint(
            "(revision = 1 AND lifecycle_state = 'REGISTERED' "
            "AND exact_previous_system_component_binding IS NULL) OR "
            "(revision > 1 AND exact_previous_system_component_binding IS NOT NULL)",
            name="ck_system_component_lifecycle_revision_shape",
        ),
        Index(
            "ix_system_component_lifecycle_current",
            "workspace_id",
            "component_id",
            "revision",
            "lifecycle_state",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    component_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    exact_previous_system_component_binding: Mapped[Optional[dict[str, Any]]] = (
        mapped_column(JSON(none_as_null=True), nullable=True)
    )
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DependencyEdge(Base):
    __tablename__ = "dependency_edges"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_dependency_edge_application",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "application_id", "from_component_id"],
            [
                "system_components.workspace_id",
                "system_components.application_id",
                "system_components.component_id",
            ],
            name="fk_dependency_edge_from_component",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "application_id", "to_component_id"],
            [
                "system_components.workspace_id",
                "system_components.application_id",
                "system_components.component_id",
            ],
            name="fk_dependency_edge_to_component",
        ),
        UniqueConstraint(
            "workspace_id", "edge_id", name="uq_dependency_edge_workspace"
        ),
        UniqueConstraint("record_digest", name="uq_dependency_edge_record_digest"),
        CheckConstraint(
            "relation IN ('DEPENDS_ON','INVOKES','DATA_FLOW','CONTAINS','REFERENCES')",
            name="ck_dependency_edge_relation",
        ),
        CheckConstraint(
            "from_component_id <> to_component_id", name="ck_dependency_edge_no_self"
        ),
        Index(
            "ix_dependency_edge_workspace_application",
            "workspace_id",
            "application_id",
            "from_component_id",
            "to_component_id",
        ),
    )

    edge_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    from_component_id: Mapped[str] = mapped_column(String(128), nullable=False)
    to_component_id: Mapped[str] = mapped_column(String(128), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    edge_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ComponentRevision(Base):
    """Immutable component revision (version-controller owned, schema-major-2).

    The identity_assurance discriminator follows
    ``contracts/v5/domain-model.yaml#identity_assurance``.  ``MUTABLE_ALIAS`` /
    ``OBSERVED_ONLY`` / ``UNKNOWN`` revisions are recorded honestly at their
    lower assurance; exact release against them is disallowed by the contract.
    The table carries the projection columns; the nested envelope payload is
    the contract-bearing record.
    """

    __tablename__ = "component_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id", "component_id"],
            [
                "system_components.workspace_id",
                "system_components.application_id",
                "system_components.component_id",
            ],
            name="fk_component_revision_component",
        ),
        UniqueConstraint(
            "workspace_id", "component_revision_id", name="uq_component_revision_workspace"
        ),
        UniqueConstraint("record_digest", name="uq_component_revision_record_digest"),
        CheckConstraint(
            "identity_assurance IN ('IMMUTABLE_DIGEST','PROVIDER_VERSION',"
            "'MUTABLE_ALIAS','OBSERVED_ONLY','UNKNOWN')",
            name="ck_component_revision_identity_assurance",
        ),
        Index(
            "ix_component_revision_workspace_application",
            "workspace_id",
            "application_id",
            "component_id",
            "created_at",
        ),
    )

    component_revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    component_id: Mapped[str] = mapped_column(String(128), nullable=False)
    component_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_locator: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    identity_assurance: Mapped[str] = mapped_column(String(32), nullable=False)
    configuration_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    exact_provenance_receipt_bindings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )
    declared_version: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    content_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    provider_origin: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    immutable_provider_version_attestation: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    exact_observation_receipt_binding: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    unknown_reason: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    interface_schema_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    permission_manifest_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    dependency_lock_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    dataset_role: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    artifact_refs: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSON, nullable=True)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TopologyRevision(Base):
    """Immutable topology revision (version-controller owned).

    ``exact_edge_revision_bindings`` pins the exact dependency-edge records the
    graph digest was computed over; historical graphs are fixed by this record,
    never by mutating the current catalog ``dependency_edges`` rows.
    """

    __tablename__ = "topology_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_topology_revision_application",
        ),
        UniqueConstraint(
            "workspace_id", "topology_revision_id", name="uq_topology_revision_workspace"
        ),
        UniqueConstraint("record_digest", name="uq_topology_revision_record_digest"),
        UniqueConstraint(
            "workspace_id", "topology_digest", name="uq_topology_revision_workspace_digest"
        ),
        Index(
            "ix_topology_revision_workspace_application",
            "workspace_id",
            "application_id",
            "created_at",
        ),
    )

    topology_revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    component_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    exact_edge_revision_bindings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )
    topology_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    provenance_receipt_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SystemVersionSet(Base):
    """Immutable system version set (version-controller owned).

    The graph digest is exactly bound to the topology revision; the version set
    digest covers application / declared environment / exact component revision
    bindings / exact topology binding / provenance / derived assurance summary.
    ``manifest_digest`` is the trusted-import replay key: a replayed manifest
    with the same digest returns the same record set.
    """

    __tablename__ = "system_version_sets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_system_version_set_application",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "declared_environment_id"],
            ["environments.workspace_id", "environments.environment_id"],
            name="fk_system_version_set_environment",
        ),
        UniqueConstraint(
            "workspace_id",
            "system_version_set_id",
            name="uq_system_version_set_workspace",
        ),
        UniqueConstraint("record_digest", name="uq_system_version_set_record_digest"),
        UniqueConstraint(
            "workspace_id",
            "version_set_digest",
            name="uq_system_version_set_workspace_digest",
        ),
        UniqueConstraint(
            "workspace_id",
            "manifest_digest",
            name="uq_system_version_set_workspace_manifest",
        ),
        Index(
            "ix_system_version_set_workspace_application",
            "workspace_id",
            "application_id",
            "declared_environment_id",
            "created_at",
        ),
    )

    system_version_set_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    declared_environment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    exact_component_revision_bindings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )
    exact_topology_revision_binding: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    identity_assurance_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provenance_receipt_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    version_set_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    manifest_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BootstrapAttestation(Base):
    """Immutable bootstrap attestation (version-controller owned).

    Proves authority to create the initial desired assignment only — never
    observed runtime, external effect, or gate pass.  The attester trust role
    is server-derived; the attested scope is the initial desired assignment.
    """

    __tablename__ = "bootstrap_attestations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_bootstrap_attestation_application",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["environments.workspace_id", "environments.environment_id"],
            name="fk_bootstrap_attestation_environment",
        ),
        UniqueConstraint(
            "workspace_id",
            "bootstrap_attestation_id",
            name="uq_bootstrap_attestation_workspace",
        ),
        UniqueConstraint("record_digest", name="uq_bootstrap_attestation_record_digest"),
        CheckConstraint(
            "attester_trust_role IN ('integrator','catalog_admin','trusted_builder')",
            name="ck_bootstrap_attestation_attester_role",
        ),
        CheckConstraint(
            "attestation_scope IN ('INITIAL_DESIRED_ASSIGNMENT')",
            name="ck_bootstrap_attestation_scope",
        ),
        Index(
            "ix_bootstrap_attestation_workspace_application",
            "workspace_id",
            "application_id",
            "environment_id",
            "created_at",
        ),
    )

    bootstrap_attestation_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    exact_initial_system_version_set_binding: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    attester_principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attester_trust_role: Mapped[str] = mapped_column(String(32), nullable=False)
    attestation_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SystemAssignment(Base):
    """System desired-assignment aggregate (version-controller owned).

    One non-retired assignment aggregate per (workspace, application,
    environment) identity key.  The bootstrap transition fixes generation=1,
    expected_previous_generation=null and an exact BootstrapAttestation
    authority binding; later transitions are CAS (previous generation + 1) with
    exact WorkOrder/ExternalOperation authority and are not part of this slice.
    """

    __tablename__ = "system_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_system_assignment_application",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["environments.workspace_id", "environments.environment_id"],
            name="fk_system_assignment_environment",
        ),
        UniqueConstraint(
            "workspace_id", "assignment_id", name="uq_system_assignment_workspace"
        ),
        CheckConstraint("generation >= 1", name="ck_system_assignment_generation"),
        CheckConstraint("revision >= 1", name="ck_system_assignment_revision"),
        CheckConstraint(
            "transition_kind IN ('BOOTSTRAP','SET_DESIRED','FREEZE_EXPOSURE',"
            "'RESUME_AFTER_ROLLBACK','RETIRE')",
            name="ck_system_assignment_transition_kind",
        ),
        CheckConstraint(
            "lifecycle_state IN ('ACTIVE','RETIRED')",
            name="ck_system_assignment_lifecycle",
        ),
        CheckConstraint(
            "exposure IN ('EXPOSED','STOPPED')", name="ck_system_assignment_exposure"
        ),
        Index(
            "ix_system_assignment_workspace_application",
            "workspace_id",
            "application_id",
            "environment_id",
            "lifecycle_state",
        ),
        # One non-retired assignment aggregate per identity key (CAS guard).
        Index(
            "uq_system_assignment_active_identity",
            "workspace_id",
            "application_id",
            "environment_id",
            unique=True,
            sqlite_where=sa.text("lifecycle_state <> 'RETIRED'"),
            postgresql_where=sa.text("lifecycle_state <> 'RETIRED'"),
        ),
    )

    assignment_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    generation: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False)
    transition_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    exact_previous_assignment_binding_or_null: Mapped[Optional[dict[str, Any]]] = (
        mapped_column(JSON, nullable=True)
    )
    exact_slot_version_set_bindings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, nullable=False
    )
    exposure: Mapped[str] = mapped_column(String(32), nullable=False)
    expected_previous_generation: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    exact_assignment_authority_binding: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    requested_by_external_operation_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ApplicationCaseBinding(Base):
    """Immutable additive link from an S1A QualityCase to an AI application
    (case-controller owned, schema-major-2).

    One binding per exact case identity ``(workspace_id, case_id, case_revision,
    case_digest)``; the same exact case bound to a different target is a
    conflict and rebinding requires a new quality case revision.  The link is
    additive: the S1A signal/case payloads and digests are never rewritten.
    """

    __tablename__ = "application_case_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["quality_cases.workspace_id", "quality_cases.case_id"],
            name="fk_application_case_binding_case",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_application_case_binding_application",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["environments.workspace_id", "environments.environment_id"],
            name="fk_application_case_binding_environment",
        ),
        UniqueConstraint(
            "workspace_id",
            "application_case_binding_id",
            name="uq_application_case_binding_workspace",
        ),
        UniqueConstraint(
            "workspace_id",
            "case_id",
            "case_revision",
            "case_digest",
            name="uq_application_case_binding_exact_case",
        ),
        UniqueConstraint(
            "record_digest", name="uq_application_case_binding_record_digest"
        ),
        CheckConstraint(
            "case_revision >= 1", name="ck_application_case_binding_case_revision"
        ),
        Index(
            "ix_application_case_binding_workspace_case",
            "workspace_id",
            "case_id",
            "case_revision",
            "created_at",
        ),
        Index(
            "ix_application_case_binding_workspace_application",
            "workspace_id",
            "application_id",
            "environment_id",
            "created_at",
        ),
    )

    application_case_binding_id: Mapped[str] = mapped_column(
        String(128), primary_key=True
    )
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    case_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    application_id: Mapped[str] = mapped_column(String(128), nullable=False)
    environment_id: Mapped[str] = mapped_column(String(128), nullable=False)
    declared_system_version_set_binding_or_unknown: Mapped[Optional[dict[str, Any]]] = (
        mapped_column(JSON, nullable=True)
    )
    binding_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AcceptanceCriteriaRevision(Base):
    """Immutable acceptance-criteria revision (case-controller owned).

    A PROPOSED revision is an untrusted draft (no confirmer fields); only a
    reauthenticated human maintainer/domain reviewer may confirm, producing a
    NEW immutable CONFIRMED record that references the prior proposal.  The
    confirmation check constraints make in-place promotion impossible.
    """

    __tablename__ = "acceptance_criteria_revisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["quality_cases.workspace_id", "quality_cases.case_id"],
            name="fk_acceptance_criteria_revision_case",
        ),
        UniqueConstraint(
            "workspace_id",
            "acceptance_criteria_revision_id",
            name="uq_acceptance_criteria_revision_workspace",
        ),
        UniqueConstraint(
            "record_digest", name="uq_acceptance_criteria_revision_record_digest"
        ),
        CheckConstraint(
            "confirmation_status IN ('PROPOSED','CONFIRMED')",
            name="ck_acceptance_criteria_revision_confirmation_status",
        ),
        CheckConstraint(
            "case_revision >= 1", name="ck_acceptance_criteria_revision_case_revision"
        ),
        CheckConstraint(
            "(confirmation_status = 'PROPOSED' AND confirmer_principal IS NULL "
            "AND confirmed_at IS NULL) "
            "OR (confirmation_status = 'CONFIRMED' AND confirmer_principal IS NOT NULL "
            "AND confirmed_at IS NOT NULL)",
            name="ck_acceptance_criteria_revision_status_shape",
        ),
        Index(
            "ix_acceptance_criteria_revision_workspace_case",
            "workspace_id",
            "case_id",
            "case_revision",
            "created_at",
        ),
    )

    acceptance_criteria_revision_id: Mapped[str] = mapped_column(
        String(128), primary_key=True
    )
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    case_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    exact_resolution_contract_binding: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    confirmation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    proposer_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    proposed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    confirmer_principal: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    exact_previous_proposed_revision_binding: Mapped[Optional[dict[str, Any]]] = (
        mapped_column(JSON, nullable=True)
    )
    acceptance_source: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reproducer_input: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    reproducer_environment: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    expected_behavior: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    oracle_or_evaluator: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    applicable_workload_profile: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    applicable_deployment_profile: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False
    )
    acceptance_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IssueSourceSnapshot(Base):
    """Read-only GitHub Issue / manual source snapshot (case-controller owned).

    Stores the fetched issue as data only.  ``instruction_markers_detected``
    flags prompt-injection payloads so downstream code never treats issue text
    as an instruction or acceptance truth; edited/deleted source states are
    annotated from the source provider.
    """

    __tablename__ = "issue_source_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["quality_cases.workspace_id", "quality_cases.case_id"],
            name="fk_issue_source_snapshot_case",
        ),
        UniqueConstraint(
            "workspace_id", "issue_snapshot_id", name="uq_issue_source_snapshot_workspace"
        ),
        UniqueConstraint("snapshot_digest", name="uq_issue_source_snapshot_digest"),
        UniqueConstraint(
            "workspace_id",
            "case_id",
            "external_repo",
            "external_issue_number",
            name="uq_issue_source_snapshot_issue",
        ),
        CheckConstraint(
            "source_kind IN ('github_issue','manual')",
            name="ck_issue_source_snapshot_source_kind",
        ),
        CheckConstraint(
            "external_issue_number >= 1", name="ck_issue_source_snapshot_issue_number"
        ),
        Index(
            "ix_issue_source_snapshot_workspace_issue",
            "workspace_id",
            "case_id",
            "source_kind",
            "created_at",
        ),
    )

    issue_snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    external_repo: Mapped[str] = mapped_column(String(256), nullable=False)
    external_issue_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    snapshot_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    edited_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deleted_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    instruction_markers_detected: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _immutable_write_forbidden(_mapper, _connection, target) -> None:  # type: ignore[no-untyped-def]
    raise RuntimeError(f"v5.immutable_record_update_forbidden:{target.__tablename__}")


for _catalog_model in (Environment, DependencyEdge):
    event.listen(_catalog_model, "before_update", _immutable_write_forbidden)
    event.listen(_catalog_model, "before_delete", _immutable_write_forbidden)

# Application/component current rows advance as projections when a new
# immutable lifecycle revision is appended.  They remain non-deletable, while
# the authoritative history itself is fully append-only.
for _lifecycle_projection_model in (AIApplication, SystemComponent):
    event.listen(
        _lifecycle_projection_model, "before_delete", _immutable_write_forbidden
    )

for _lifecycle_history_model in (
    AIApplicationLifecycleRevision,
    SystemComponentLifecycleRevision,
):
    event.listen(_lifecycle_history_model, "before_update", _immutable_write_forbidden)
    event.listen(_lifecycle_history_model, "before_delete", _immutable_write_forbidden)

# V5-1B immutable records: no update path.  ``SystemAssignment`` is an aggregate
# with legitimate CAS transitions in later slices, so it is intentionally not
# guarded here; its compare-and-swap constraints are enforced by the service.
for _version_model in (
    ComponentRevision,
    TopologyRevision,
    SystemVersionSet,
    BootstrapAttestation,
):
    event.listen(_version_model, "before_update", _immutable_write_forbidden)
    event.listen(_version_model, "before_delete", _immutable_write_forbidden)

# V5-1C immutable case records: no update/delete path (confirmed revisions can
# never be rewritten in place; bindings are additive links).
for _case_model in (
    ApplicationCaseBinding,
    AcceptanceCriteriaRevision,
    IssueSourceSnapshot,
):
    event.listen(_case_model, "before_update", _immutable_write_forbidden)
    event.listen(_case_model, "before_delete", _immutable_write_forbidden)


__all__ = [
    "AIApplication",
    "AIApplicationLifecycleRevision",
    "ASSIGNMENT_EXPOSURE_VALUES",
    "ASSIGNMENT_LIFECYCLE_STATE_VALUES",
    "ASSIGNMENT_TRANSITION_KIND_VALUES",
    "ATTESTATION_SCOPE_VALUES",
    "ATTESTER_TRUST_ROLE_VALUES",
    "AcceptanceCriteriaRevision",
    "ApplicationCaseBinding",
    "BootstrapAttestation",
    "CATALOG_LIFECYCLE_STATES",
    "COMPONENT_KIND_VALUES",
    "COMPONENT_LIFECYCLE_STATES",
    "CRITICALITY_VALUES",
    "ComponentRevision",
    "DATA_CLASSIFICATION_VALUES",
    "DATASET_ROLE_VALUES",
    "DEPENDENCY_RELATION_VALUES",
    "DependencyEdge",
    "EFFECT_CLASSIFICATION_VALUES",
    "ENVIRONMENT_LIFECYCLE_STATES",
    "Environment",
    "GOVERNANCE_MODE_VALUES",
    "IDENTITY_ASSURANCE_VALUES",
    "IssueSourceSnapshot",
    "PERMISSION_CLASSIFICATION_VALUES",
    "RISK_CLASSIFICATION_VALUES",
    "SystemAssignment",
    "SystemComponent",
    "SystemComponentLifecycleRevision",
    "SystemVersionSet",
    "TopologyRevision",
]
