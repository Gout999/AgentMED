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
CATALOG_LIFECYCLE_STATES = ("ACTIVE", "ARCHIVED")
ENVIRONMENT_LIFECYCLE_STATES = ("ACTIVE", "RETIRED")
COMPONENT_LIFECYCLE_STATES = ("ACTIVE", "DEPRECATED", "RETIRED")
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
            "lifecycle_state IN ('ACTIVE','ARCHIVED')", name="ck_ai_application_lifecycle"
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
            "lifecycle_state IN ('ACTIVE','DEPRECATED','RETIRED')",
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


def _immutable_write_forbidden(_mapper, _connection, target) -> None:  # type: ignore[no-untyped-def]
    raise RuntimeError(f"v5.immutable_record_update_forbidden:{target.__tablename__}")


for _catalog_model in (
    AIApplication,
    Environment,
    SystemComponent,
    DependencyEdge,
):
    event.listen(_catalog_model, "before_update", _immutable_write_forbidden)
    event.listen(_catalog_model, "before_delete", _immutable_write_forbidden)


__all__ = [
    "AIApplication",
    "CATALOG_LIFECYCLE_STATES",
    "COMPONENT_KIND_VALUES",
    "COMPONENT_LIFECYCLE_STATES",
    "CRITICALITY_VALUES",
    "DATA_CLASSIFICATION_VALUES",
    "DATASET_ROLE_VALUES",
    "DEPENDENCY_RELATION_VALUES",
    "DependencyEdge",
    "EFFECT_CLASSIFICATION_VALUES",
    "ENVIRONMENT_LIFECYCLE_STATES",
    "Environment",
    "GOVERNANCE_MODE_VALUES",
    "PERMISSION_CLASSIFICATION_VALUES",
    "RISK_CLASSIFICATION_VALUES",
    "SystemComponent",
]
