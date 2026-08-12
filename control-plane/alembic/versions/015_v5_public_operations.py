"""Add the V5-2B durable public-operation projection binding.

Revision ID: 015
Revises: 014
Create Date: 2026-08-13

The new row is the AutomationRequest owner record plus the immutable public
operation-to-WorkTask link.  It never stores a transport-owned terminal
state; readers derive OperationState from the authoritative WorkTask/Attempt.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DOWNGRADE_BLOCKED = "015.v5_public_operation_facts_prevent_downgrade"


def upgrade() -> None:
    op.create_table(
        "automation_requests",
        sa.Column("automation_request_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("operation_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("application_case_binding_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_revision", sa.BigInteger(), nullable=False),
        sa.Column("case_digest", sa.String(80), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("environment_id", sa.String(128), nullable=False),
        sa.Column("requester_principal", sa.String(128), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("request_digest", sa.String(80), nullable=False),
        sa.Column("budget_digest", sa.String(80), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "stop_requested", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(256), nullable=True),
        sa.Column("stop_requested_by_principal", sa.String(128), nullable=True),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint(
            "automation_request_id", name="pk_automation_requests"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["work_tasks.workspace_id", "work_tasks.task_id"],
            name="fk_automation_request_work_task",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_case_binding_id"],
            [
                "application_case_bindings.workspace_id",
                "application_case_bindings.application_case_binding_id",
            ],
            name="fk_automation_request_case_binding",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_automation_request_application",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["environments.workspace_id", "environments.environment_id"],
            name="fk_automation_request_environment",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "requester_principal"],
            ["public_principals.workspace_id", "public_principals.principal_id"],
            name="fk_automation_request_principal",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "automation_request_id",
            name="uq_automation_request_workspace_request",
        ),
        sa.UniqueConstraint(
            "workspace_id", "operation_id", name="uq_automation_request_operation"
        ),
        sa.UniqueConstraint(
            "workspace_id", "task_id", name="uq_automation_request_task"
        ),
        sa.CheckConstraint(
            "revision >= 1", name="ck_automation_request_revision"
        ),
        sa.CheckConstraint(
            "(stop_requested = false AND stop_requested_at IS NULL "
            "AND stop_reason IS NULL AND stop_requested_by_principal IS NULL) OR "
            "(stop_requested = true AND stop_requested_at IS NOT NULL "
            "AND stop_reason IS NOT NULL AND stop_requested_by_principal IS NOT NULL)",
            name="ck_automation_request_stop_shape",
        ),
    )
    op.create_index(
        "ix_automation_request_visible_page",
        "automation_requests",
        ["workspace_id", "operation_id", "application_id", "requester_principal"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM automation_requests LIMIT 1)")
    ).scalar_one():
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    op.drop_index(
        "ix_automation_request_visible_page", table_name="automation_requests"
    )
    op.drop_table("automation_requests")
