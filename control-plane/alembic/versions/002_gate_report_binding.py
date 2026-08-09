"""authoritative GateReport persistence and WorkOrder binding

Revision ID: 002
Revises: 001
Create Date: 2026-08-08
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gate_reports",
        sa.Column("eval_id", sa.String(64), primary_key=True),
        sa.Column("report_id", sa.String(128), nullable=False, unique=True),
        sa.Column("workorder_id", sa.String(128), nullable=False),
        sa.Column("workorder_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("target_versionset_id", sa.String(128), nullable=False),
        sa.Column("target_versionset_digest", sa.String(80), nullable=False),
        sa.Column("target_revision", sa.BigInteger(), nullable=False),
        sa.Column("dataset_id", sa.String(128), nullable=False),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("dataset_digest", sa.String(80), nullable=False),
        sa.Column("evidence_digest", sa.String(80), nullable=False),
        sa.Column("candidate_digest", sa.String(80), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("binding_digest", sa.String(80), nullable=True),
        sa.Column("authorization_digest", sa.String(80), nullable=True),
        sa.Column("overall_status", sa.String(32), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_gate_reports_workorder_id", "gate_reports", ["workorder_id"])
    op.add_column(
        "controller_operations",
        sa.Column("approval_id", sa.String(128), nullable=True),
    )
    op.create_index(
        "ix_controller_operations_approval_id",
        "controller_operations",
        ["approval_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_controller_operations_approval_id", table_name="controller_operations")
    op.drop_column("controller_operations", "approval_id")
    op.drop_index("ix_gate_reports_workorder_id", table_name="gate_reports")
    op.drop_table("gate_reports")
