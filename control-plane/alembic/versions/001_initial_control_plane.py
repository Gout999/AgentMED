"""initial control-plane tables (spec §7)

Revision ID: 001
Revises:
Create Date: 2026-08-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "aggregates",
        sa.Column("aggregate_type", sa.String(32), primary_key=True),
        sa.Column("aggregate_id", sa.String(128), primary_key=True),
        sa.Column("state", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("aggregate_type", sa.String(32), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("causation_id", sa.String(128), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("aggregate_id", "seq", name="uq_events_agg_seq"),
    )
    op.create_index("ix_events_aggregate_type", "events", ["aggregate_type"])
    op.create_index("ix_events_aggregate_id", "events", ["aggregate_id"])

    op.create_table(
        "inbox",
        sa.Column("dedup_key", sa.String(128), primary_key=True),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(256), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=True),
        sa.Column("disposition", sa.String(32), nullable=False),
    )
    op.create_index("ix_inbox_case_id", "inbox", ["case_id"])

    op.create_table(
        "outbox",
        sa.Column("outbox_id", sa.String(64), primary_key=True),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_outbox_aggregate_id", "outbox", ["aggregate_id"])
    op.create_index("ix_outbox_status_retry", "outbox", ["status", "next_retry_at"])

    op.create_table(
        "leases",
        sa.Column("resource_id", sa.String(128), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("lease_id", sa.String(64), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "fencing_counter",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("next_token", sa.BigInteger(), nullable=False),
    )

    op.create_table(
        "workorders",
        sa.Column("workorder_id", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False, unique=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_workorders_case_id", "workorders", ["case_id"])

    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(128), primary_key=True),
        sa.Column("workorder_id", sa.String(128), nullable=False),
        sa.Column("workorder_hash", sa.String(64), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("approver", sa.JSON(), nullable=False),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )
    op.create_index("ix_approvals_workorder_id", "approvals", ["workorder_id"])

    op.create_table(
        "trust_ledger",
        sa.Column("risk_class", sa.String(32), primary_key=True),
        sa.Column("action_type", sa.String(64), primary_key=True),
        sa.Column("epoch", sa.Integer(), primary_key=True),
        sa.Column("successes", sa.Integer(), nullable=False),
        sa.Column("trials", sa.Integer(), nullable=False),
        sa.Column("autonomy_state", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )

    op.create_table(
        "audit",
        sa.Column("audit_id", sa.String(64), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(256), nullable=False),
        sa.Column("params_digest", sa.String(80), nullable=False),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=True),
    )

    op.create_table(
        "controller_operations",
        sa.Column("operation_id", sa.String(64), primary_key=True),
        sa.Column("release_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("remote_operation_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("expected_revision", sa.Integer(), nullable=True),
        sa.Column("request_fingerprint", sa.String(80), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_controller_operations_release_id", "controller_operations", ["release_id"])


def downgrade() -> None:
    op.drop_table("controller_operations")
    op.drop_table("audit")
    op.drop_table("trust_ledger")
    op.drop_table("approvals")
    op.drop_table("workorders")
    op.drop_table("fencing_counter")
    op.drop_table("leases")
    op.drop_table("outbox")
    op.drop_table("inbox")
    op.drop_table("events")
    op.drop_table("aggregates")
