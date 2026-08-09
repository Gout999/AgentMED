"""durable release-to-notification closure continuation

Revision ID: 004
Revises: 003
Create Date: 2026-08-09
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "worker_suggestion_receipts",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(80), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index(
        "ix_worker_suggestion_receipts_case_id",
        "worker_suggestion_receipts",
        ["case_id"],
    )
    op.create_table(
        "release_closures",
        sa.Column("release_id", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("channel", sa.String(128), nullable=False),
        sa.Column("thread_ref", sa.String(256), nullable=False),
        sa.Column("body_ref", sa.Text(), nullable=False),
        sa.Column("body_digest", sa.String(80), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="configured"),
        sa.Column("notification_id", sa.String(128), nullable=True, unique=True),
        sa.Column(
            "configured_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_release_closures_case_id", "release_closures", ["case_id"])


def downgrade() -> None:
    op.drop_index("ix_release_closures_case_id", table_name="release_closures")
    op.drop_table("release_closures")
    op.drop_index(
        "ix_worker_suggestion_receipts_case_id",
        table_name="worker_suggestion_receipts",
    )
    op.drop_table("worker_suggestion_receipts")
