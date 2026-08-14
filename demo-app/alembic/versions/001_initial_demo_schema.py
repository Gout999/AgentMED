"""Create the initial demo-app schema and PostgreSQL vector extension.

Revision ID: 001
Revises:
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _identity_type() -> sa.TypeEngine:
    # PostgreSQL remains BIGINT. INTEGER gives SQLite test databases genuine
    # autoincrement semantics so the full startup seeding path can be replayed.
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "prompt_versions",
        sa.Column("prompt_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("prompt_id", "version"),
    )
    op.create_table(
        "kb_entries",
        sa.Column("id", _identity_type(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.String(length=128), nullable=False),
        sa.Column("kb_id", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("keywords", sa.JSON(), nullable=False),
        sa.Column("slug", sa.String(length=256), nullable=True),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("embedding", Vector(dim=1024), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kb_id", "entry_id", name="uq_kb_identity"),
    )
    op.create_table(
        "versionsets",
        sa.Column("versionset_id", sa.String(length=80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(length=80), nullable=False),
        sa.Column("canary_percent", sa.Integer(), nullable=True),
        sa.Column("canary_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("labels", sa.JSON(), nullable=True),
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
        sa.PrimaryKeyConstraint("versionset_id"),
    )
    op.create_table(
        "transitions",
        sa.Column("id", _identity_type(), autoincrement=True, nullable=False),
        sa.Column("versionset_id", sa.String(length=80), nullable=False),
        sa.Column("from_status", sa.String(length=32), nullable=False),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("operation_id", sa.String(length=80), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_transitions_versionset_id"),
        "transitions",
        ["versionset_id"],
        unique=False,
    )
    op.create_table(
        "operations",
        sa.Column("operation_id", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("versionset_id", sa.String(length=80), nullable=False),
        sa.Column("request", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("operation_id"),
    )
    op.create_table(
        "idempotency",
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("fingerprint", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("idempotency_key"),
    )
    op.create_table(
        "chat_logs",
        sa.Column("id", _identity_type(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("versionset_id", sa.String(length=80), nullable=True),
        sa.Column("prompt_digest", sa.String(length=80), nullable=False),
        sa.Column("kb_manifest_digest", sa.String(length=80), nullable=False),
        sa.Column("model_digest", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("usage", sa.JSON(), nullable=True),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_chat_logs_request_id"),
        "chat_logs",
        ["request_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_chat_logs_ts"), "chat_logs", ["ts"], unique=False
    )
    op.create_index(
        op.f("ix_chat_logs_versionset_id"),
        "chat_logs",
        ["versionset_id"],
        unique=False,
    )
    op.create_table(
        "feedback",
        sa.Column("id", _identity_type(), autoincrement=True, nullable=False),
        sa.Column("feedback_id", sa.String(length=80), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("versionset_id", sa.String(length=80), nullable=True),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("user_ref", sa.String(length=128), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feedback_id"),
    )
    op.create_index(
        op.f("ix_feedback_request_id"),
        "feedback",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_feedback_ts"), "feedback", ["ts"], unique=False
    )
    op.create_index(
        op.f("ix_feedback_versionset_id"),
        "feedback",
        ["versionset_id"],
        unique=False,
    )
    op.create_table(
        "fault_state",
        sa.Column("fault_id", sa.String(length=8), nullable=False),
        sa.Column(
            "injected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("fault_id"),
    )


def downgrade() -> None:
    op.drop_table("fault_state")
    op.drop_index(op.f("ix_feedback_versionset_id"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_ts"), table_name="feedback")
    op.drop_index(op.f("ix_feedback_request_id"), table_name="feedback")
    op.drop_table("feedback")
    op.drop_index(op.f("ix_chat_logs_versionset_id"), table_name="chat_logs")
    op.drop_index(op.f("ix_chat_logs_ts"), table_name="chat_logs")
    op.drop_index(op.f("ix_chat_logs_request_id"), table_name="chat_logs")
    op.drop_table("chat_logs")
    op.drop_table("idempotency")
    op.drop_table("operations")
    op.drop_index(op.f("ix_transitions_versionset_id"), table_name="transitions")
    op.drop_table("transitions")
    op.drop_table("versionsets")
    op.drop_table("kb_entries")
    op.drop_table("prompt_versions")
    # The vector extension may be shared by other schemas and is deliberately
    # retained. A later forward migration may remove it after an ownership audit.
