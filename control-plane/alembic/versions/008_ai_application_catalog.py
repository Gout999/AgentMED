"""V5-1A AI application catalog projections.

Revision ID: 008
Revises: 007
Create Date: 2026-08-11

This is an expand migration.  It adds the four V5 application-catalog tables
(ai_applications / environments / system_components / dependency_edges) for the
single-workspace runtime slice.  Every key is workspace/project/environment
scoped.  Downgrade is blocked once any catalog record or any v5 event /
authority receipt exists.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DOWNGRADE_BLOCKED = "008.downgrade_blocked.immutable_v5_catalog_records_exist"
_NEW_TABLES = (
    "ai_applications",
    "environments",
    "system_components",
    "dependency_edges",
)
_V5_EVENT_AGGREGATE_TYPES = (
    "ai_application",
    "environment",
    "system_component",
    "dependency_edge",
)
_V5_SUBJECT_KINDS = (
    "AI_APPLICATION",
    "ENVIRONMENT",
    "SYSTEM_COMPONENT",
    "DEPENDENCY_EDGE",
)


def _create_ai_applications() -> None:
    op.create_table(
        "ai_applications",
        sa.Column("application_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("slug", sa.String(128), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("owner_principal_ids", sa.JSON(), nullable=False),
        sa.Column("criticality", sa.String(16), nullable=False),
        sa.Column("data_classification", sa.String(32), nullable=False),
        sa.Column("governance_mode", sa.String(32), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("envelope_payload", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column("recorded_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "application_id", name="uq_ai_application_workspace"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "project_id",
            "slug",
            name="uq_ai_application_workspace_project_slug",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('ACTIVE','ARCHIVED')", name="ck_ai_application_lifecycle"
        ),
        sa.CheckConstraint(
            "criticality IN ('P0','P1','P2','P3')", name="ck_ai_application_criticality"
        ),
        sa.CheckConstraint(
            "data_classification IN ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')",
            name="ck_ai_application_data_classification",
        ),
        sa.CheckConstraint(
            "governance_mode IN ('MANAGED','OBSERVED')",
            name="ck_ai_application_governance_mode",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_ai_application_revision"),
    )
    op.create_index(
        "ix_ai_application_workspace_lifecycle",
        "ai_applications",
        ["workspace_id", "lifecycle_state", "updated_at"],
    )
    op.create_index(
        "ix_ai_application_workspace_project",
        "ai_applications",
        ["workspace_id", "project_id"],
    )


def _create_environments() -> None:
    op.create_table(
        "environments",
        sa.Column("environment_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("logical_name", sa.String(128), nullable=False),
        sa.Column("risk_classification", sa.String(32), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("envelope_payload", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column("recorded_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_environment_application",
        ),
        sa.UniqueConstraint(
            "workspace_id", "environment_id", name="uq_environment_workspace"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "application_id",
            "logical_name",
            name="uq_environment_workspace_application_name",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('ACTIVE','RETIRED')", name="ck_environment_lifecycle"
        ),
        sa.CheckConstraint(
            "risk_classification IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="ck_environment_risk_classification",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_environment_revision"),
    )
    op.create_index(
        "ix_environment_workspace_application",
        "environments",
        ["workspace_id", "application_id", "lifecycle_state"],
    )


def _create_system_components() -> None:
    op.create_table(
        "system_components",
        sa.Column("component_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("component_kind", sa.String(64), nullable=False),
        sa.Column("logical_name", sa.String(128), nullable=False),
        sa.Column("owner_principal_ids", sa.JSON(), nullable=False),
        sa.Column("criticality", sa.String(16), nullable=False),
        sa.Column("data_classification", sa.String(32), nullable=False),
        sa.Column("permission_classification", sa.String(32), nullable=False),
        sa.Column("effect_classification", sa.String(32), nullable=False),
        sa.Column("dataset_role", sa.String(32), nullable=True),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("envelope_payload", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column("recorded_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_system_component_application",
        ),
        sa.UniqueConstraint(
            "workspace_id", "component_id", name="uq_system_component_workspace"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "application_id",
            "component_id",
            name="uq_system_component_workspace_application_component",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "application_id",
            "component_kind",
            "logical_name",
            name="uq_system_component_workspace_application_identity",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('ACTIVE','DEPRECATED','RETIRED')",
            name="ck_system_component_lifecycle",
        ),
        sa.CheckConstraint(
            "criticality IN ('P0','P1','P2','P3')", name="ck_system_component_criticality"
        ),
        sa.CheckConstraint(
            "data_classification IN ('PUBLIC','INTERNAL','CONFIDENTIAL','RESTRICTED')",
            name="ck_system_component_data_classification",
        ),
        sa.CheckConstraint(
            "permission_classification IN ('READ_ONLY','READ_WRITE','ELEVATED')",
            name="ck_system_component_permission_classification",
        ),
        sa.CheckConstraint(
            "effect_classification IN ('NONE','LOCAL','EXTERNAL')",
            name="ck_system_component_effect_classification",
        ),
        sa.CheckConstraint(
            "component_kind IN ('APPLICATION_CODE','AGENT','MODEL_BINDING','PROMPT',"
            "'DATASET','INDEX','EMBEDDING','RETRIEVER','SKILL','MCP_SERVER',"
            "'TOOL_SCHEMA','POLICY','MEMORY_POLICY','RUNTIME_PROFILE','CONNECTOR')",
            name="ck_system_component_kind",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_system_component_revision"),
    )
    op.create_index(
        "ix_system_component_workspace_application",
        "system_components",
        ["workspace_id", "application_id", "component_kind", "lifecycle_state"],
    )


def _create_dependency_edges() -> None:
    op.create_table(
        "dependency_edges",
        sa.Column("edge_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("from_component_id", sa.String(128), nullable=False),
        sa.Column("to_component_id", sa.String(128), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("edge_digest", sa.String(80), nullable=False),
        sa.Column("envelope_payload", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column("recorded_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_dependency_edge_application",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id", "from_component_id"],
            [
                "system_components.workspace_id",
                "system_components.application_id",
                "system_components.component_id",
            ],
            name="fk_dependency_edge_from_component",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id", "to_component_id"],
            [
                "system_components.workspace_id",
                "system_components.application_id",
                "system_components.component_id",
            ],
            name="fk_dependency_edge_to_component",
        ),
        sa.UniqueConstraint(
            "workspace_id", "edge_id", name="uq_dependency_edge_workspace"
        ),
        sa.UniqueConstraint("record_digest", name="uq_dependency_edge_record_digest"),
        sa.CheckConstraint(
            "relation IN ('DEPENDS_ON','INVOKES','DATA_FLOW','CONTAINS','REFERENCES')",
            name="ck_dependency_edge_relation",
        ),
        sa.CheckConstraint(
            "from_component_id <> to_component_id", name="ck_dependency_edge_no_self"
        ),
    )
    op.create_index(
        "ix_dependency_edge_workspace_application",
        "dependency_edges",
        ["workspace_id", "application_id", "from_component_id", "to_component_id"],
    )


def upgrade() -> None:
    _create_ai_applications()
    _create_environments()
    _create_system_components()
    _create_dependency_edges()


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table in _NEW_TABLES:
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    if bind.dialect.name == "postgresql":
        events = bind.execute(
            sa.text(
                "SELECT 1 FROM events WHERE contract_version = 'v4' AND "
                "aggregate_type IN ('ai_application','environment',"
                "'system_component','dependency_edge') LIMIT 1"
            )
        ).first()
        receipts = bind.execute(
            sa.text(
                "SELECT 1 FROM authority_receipts WHERE subject_kind IN "
                "('AI_APPLICATION','ENVIRONMENT','SYSTEM_COMPONENT','DEPENDENCY_EDGE') "
                "LIMIT 1"
            )
        ).first()
        if events is not None or receipts is not None:
            raise RuntimeError(_DOWNGRADE_BLOCKED)


def downgrade() -> None:
    _assert_downgrade_safe()
    for table in reversed(_NEW_TABLES):
        op.drop_table(table)
