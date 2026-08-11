"""Add the V5 lifecycle revision authority foundation.

Revision ID: 011
Revises: 010
Create Date: 2026-08-11

The pre-011 runtime wrote AIApplication and SystemComponent directly as ACTIVE
revision 1.  D-014 rejects reinterpreting or synthesizing authority for those
records.  This migration therefore performs a read-only legacy preflight before
the first DDL statement and requires explicit recovery for any existing V5
record, event, outbox item, or AuthorityReceipt.

The migration is schema-only: it admits REGISTERED in the current-head
projections, creates append-only lifecycle revision tables, and persists the
server-owned trust roles used by the R1 authority layer.  It never backfills a
legacy lifecycle fact.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_LEGACY_V5_TABLES = (
    "ai_applications",
    "environments",
    "system_components",
    "dependency_edges",
    "component_revisions",
    "topology_revisions",
    "system_version_sets",
    "bootstrap_attestations",
    "system_assignments",
    "application_case_bindings",
    "acceptance_criteria_revisions",
    "issue_source_snapshots",
)

_V5_AGGREGATE_TYPES = (
    "ai_application",
    "environment",
    "system_component",
    "dependency_edge",
    "component_revision",
    "topology_revision",
    "system_version_set",
    "bootstrap_attestation",
    "system_assignment",
    "application_case_binding",
    "acceptance_criteria_revision",
)

_V5_SUBJECT_KINDS = (
    "AI_APPLICATION",
    "ENVIRONMENT",
    "SYSTEM_COMPONENT",
    "DEPENDENCY_EDGE",
    "COMPONENT_REVISION",
    "TOPOLOGY_REVISION",
    "SYSTEM_VERSION_SET",
    "BOOTSTRAP_ATTESTATION",
    "SYSTEM_ASSIGNMENT",
    "APPLICATION_CASE_BINDING",
    "ACCEPTANCE_CRITERIA_REVISION",
)

_RECOVERY_REQUIRED = "011.legacy_v5_lifecycle_requires_explicit_recovery"
_DOWNGRADE_BLOCKED = "011.downgrade_blocked.lifecycle_authority_exists"


def _sql_values(values: tuple[str, ...]) -> str:
    """Render a closed, module-owned value set for migration-only SQL."""

    return ",".join(f"'{value}'" for value in values)


def _has_any_row(table_name: str) -> bool:
    return op.get_bind().execute(
        sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")
    ).first() is not None


def _v5_identity_history_exists() -> bool:
    bind = op.get_bind()
    if any(_has_any_row(table_name) for table_name in _LEGACY_V5_TABLES):
        return True
    if bind.execute(
        sa.text(
            "SELECT 1 FROM events WHERE aggregate_type IN ("
            f"{_sql_values(_V5_AGGREGATE_TYPES)}) LIMIT 1"
        )
    ).first() is not None:
        return True
    if bind.execute(
        sa.text(
            "SELECT 1 FROM outbox WHERE aggregate_type IN ("
            f"{_sql_values(_V5_AGGREGATE_TYPES)}) LIMIT 1"
        )
    ).first() is not None:
        return True
    return bind.execute(
        sa.text(
            "SELECT 1 FROM authority_receipts WHERE subject_kind IN ("
            f"{_sql_values(_V5_SUBJECT_KINDS)}) LIMIT 1"
        )
    ).first() is not None


def _assert_no_legacy_v5_history() -> None:
    if _v5_identity_history_exists():
        raise RuntimeError(_RECOVERY_REQUIRED)


def _create_application_lifecycle_history() -> None:
    op.create_table(
        "ai_application_lifecycle_revisions",
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("exact_previous_application_binding", sa.JSON(), nullable=True),
        sa.Column("envelope_payload", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column("recorded_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_ai_application_lifecycle_head",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "application_id",
            "revision",
            name="pk_ai_application_lifecycle_revision",
        ),
        sa.UniqueConstraint(
            "record_digest", name="uq_ai_application_lifecycle_record_digest"
        ),
        sa.UniqueConstraint(
            "authority_receipt_id",
            name="uq_ai_application_lifecycle_authority_receipt",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('REGISTERED','ACTIVE','ARCHIVED')",
            name="ck_ai_application_lifecycle_revision_state",
        ),
        sa.CheckConstraint(
            "(revision = 1 AND lifecycle_state = 'REGISTERED' "
            "AND exact_previous_application_binding IS NULL) OR "
            "(revision > 1 AND exact_previous_application_binding IS NOT NULL)",
            name="ck_ai_application_lifecycle_revision_shape",
        ),
    )
    op.create_index(
        "ix_ai_application_lifecycle_current",
        "ai_application_lifecycle_revisions",
        ["workspace_id", "application_id", "revision", "lifecycle_state"],
    )


def _create_component_lifecycle_history() -> None:
    op.create_table(
        "system_component_lifecycle_revisions",
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("component_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column(
            "exact_previous_system_component_binding", sa.JSON(), nullable=True
        ),
        sa.Column("envelope_payload", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column("recorded_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id", "component_id"],
            [
                "system_components.workspace_id",
                "system_components.application_id",
                "system_components.component_id",
            ],
            name="fk_system_component_lifecycle_head",
        ),
        sa.PrimaryKeyConstraint(
            "workspace_id",
            "component_id",
            "revision",
            name="pk_system_component_lifecycle_revision",
        ),
        sa.UniqueConstraint(
            "record_digest", name="uq_system_component_lifecycle_record_digest"
        ),
        sa.UniqueConstraint(
            "authority_receipt_id",
            name="uq_system_component_lifecycle_authority_receipt",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('REGISTERED','ACTIVE','DEPRECATED','RETIRED')",
            name="ck_system_component_lifecycle_revision_state",
        ),
        sa.CheckConstraint(
            "(revision = 1 AND lifecycle_state = 'REGISTERED' "
            "AND exact_previous_system_component_binding IS NULL) OR "
            "(revision > 1 AND exact_previous_system_component_binding IS NOT NULL)",
            name="ck_system_component_lifecycle_revision_shape",
        ),
    )
    op.create_index(
        "ix_system_component_lifecycle_current",
        "system_component_lifecycle_revisions",
        ["workspace_id", "component_id", "revision", "lifecycle_state"],
    )


def _assert_downgrade_safe() -> None:
    if _has_any_row("ai_application_lifecycle_revisions") or _has_any_row(
        "system_component_lifecycle_revisions"
    ):
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    if _v5_identity_history_exists():
        raise RuntimeError(_DOWNGRADE_BLOCKED)

    principals = sa.table(
        "public_principals",
        sa.column("principal_id", sa.String()),
        sa.column("trust_roles", sa.JSON()),
    )
    for row in op.get_bind().execute(
        sa.select(principals.c.principal_id, principals.c.trust_roles)
    ).mappings():
        if row["trust_roles"]:
            raise RuntimeError(_DOWNGRADE_BLOCKED)


def upgrade() -> None:
    # D-014 requires this read-only inventory before the first schema mutation.
    _assert_no_legacy_v5_history()

    op.add_column(
        "public_principals",
        sa.Column(
            "trust_roles",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )

    with op.batch_alter_table("ai_applications") as batch:
        batch.drop_constraint("ck_ai_application_lifecycle", type_="check")
        batch.create_check_constraint(
            "ck_ai_application_lifecycle",
            "lifecycle_state IN ('REGISTERED','ACTIVE','ARCHIVED')",
        )

    with op.batch_alter_table("system_components") as batch:
        batch.drop_constraint("ck_system_component_lifecycle", type_="check")
        batch.create_check_constraint(
            "ck_system_component_lifecycle",
            "lifecycle_state IN ('REGISTERED','ACTIVE','DEPRECATED','RETIRED')",
        )

    _create_application_lifecycle_history()
    _create_component_lifecycle_history()


def downgrade() -> None:
    _assert_downgrade_safe()

    op.drop_index(
        "ix_system_component_lifecycle_current",
        table_name="system_component_lifecycle_revisions",
    )
    op.drop_table("system_component_lifecycle_revisions")
    op.drop_index(
        "ix_ai_application_lifecycle_current",
        table_name="ai_application_lifecycle_revisions",
    )
    op.drop_table("ai_application_lifecycle_revisions")

    with op.batch_alter_table("system_components") as batch:
        batch.drop_constraint("ck_system_component_lifecycle", type_="check")
        batch.create_check_constraint(
            "ck_system_component_lifecycle",
            "lifecycle_state IN ('ACTIVE','DEPRECATED','RETIRED')",
        )

    with op.batch_alter_table("ai_applications") as batch:
        batch.drop_constraint("ck_ai_application_lifecycle", type_="check")
        batch.create_check_constraint(
            "ck_ai_application_lifecycle",
            "lifecycle_state IN ('ACTIVE','ARCHIVED')",
        )

    op.drop_column("public_principals", "trust_roles")
