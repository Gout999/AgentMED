"""transactional outbox receipts and authoritative Trust entries

Revision ID: 003
Revises: 002
Create Date: 2026-08-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("outbox", sa.Column("source_event_id", sa.String(64), nullable=True))
    op.add_column("outbox", sa.Column("source_event_seq", sa.BigInteger(), nullable=True))
    op.add_column("outbox", sa.Column("event_type", sa.String(64), nullable=True))
    op.add_column("outbox", sa.Column("payload_digest", sa.String(80), nullable=True))
    op.add_column("outbox", sa.Column("claimed_by", sa.String(128), nullable=True))
    op.add_column("outbox", sa.Column("claim_token", sa.String(64), nullable=True))
    op.add_column("outbox", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbox", sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("outbox", sa.Column("receipt", sa.JSON(), nullable=True))

    # Pre-003 rows were logging-only and have no causal identity. Preserve them
    # as explicit legacy records rather than inventing a successful receipt.
    op.execute(
        sa.text(
            """
        UPDATE outbox
           SET source_event_id = outbox_id,
               source_event_seq = 0,
               event_type = 'LEGACY_UNATTRIBUTED',
               payload_digest = :zero_digest,
               status = CASE WHEN status IN ('SENT', 'SENDING') THEN 'DEAD' ELSE status END,
               last_error = CASE
                 WHEN status IN ('SENT', 'SENDING') THEN 'pre-003 logging delivery has no verifiable receipt'
                 ELSE last_error
               END
        """
        ).bindparams(zero_digest="sha256:" + "0" * 64)
    )
    if op.get_bind().dialect.name == "sqlite":
        # Replay/dev uses SQLite.  Batch mode is required for constraint and
        # nullability changes because SQLite has no ALTER COLUMN operation.
        with op.batch_alter_table("outbox") as batch:
            batch.alter_column(
                "source_event_id", existing_type=sa.String(64), nullable=False
            )
            batch.alter_column(
                "source_event_seq", existing_type=sa.BigInteger(), nullable=False
            )
            batch.alter_column(
                "event_type", existing_type=sa.String(64), nullable=False
            )
            batch.alter_column(
                "payload_digest", existing_type=sa.String(80), nullable=False
            )
            batch.create_unique_constraint(
                "uq_outbox_source_channel_event",
                ["source_event_id", "channel", "event_type"],
            )
    else:
        op.alter_column("outbox", "source_event_id", nullable=False)
        op.alter_column("outbox", "source_event_seq", nullable=False)
        op.alter_column("outbox", "event_type", nullable=False)
        op.alter_column("outbox", "payload_digest", nullable=False)
        op.create_unique_constraint(
            "uq_outbox_source_channel_event",
            "outbox",
            ["source_event_id", "channel", "event_type"],
        )
    op.create_index("ix_outbox_source_event_id", "outbox", ["source_event_id"])
    op.create_index("ix_outbox_claim_expiry", "outbox", ["status", "claim_expires_at"])
    op.create_index(
        "ix_outbox_aggregate_sequence_status",
        "outbox",
        ["aggregate_id", "source_event_seq", "status"],
    )

    op.create_table(
        "outbox_delivery_receipts",
        sa.Column("receipt_id", sa.String(64), primary_key=True),
        sa.Column("outbox_id", sa.String(64), nullable=False, unique=True),
        sa.Column("source_event_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("payload_digest", sa.String(80), nullable=False),
        sa.Column("receipt", sa.JSON(), nullable=False),
        sa.Column(
            "delivered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_outbox_delivery_receipts_outbox_id",
        "outbox_delivery_receipts",
        ["outbox_id"],
    )
    op.create_index(
        "ix_outbox_delivery_receipts_source_event_id",
        "outbox_delivery_receipts",
        ["source_event_id"],
    )

    op.create_table(
        "trust_ledger_entries",
        sa.Column("entry_id", sa.String(64), primary_key=True),
        sa.Column("source_event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("risk_class", sa.String(32), nullable=False),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("action_ref", sa.String(128), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("successes", sa.Integer(), nullable=False),
        sa.Column("trials", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "risk_class",
            "action_type",
            "action_ref",
            name="uq_trust_entry_action",
        ),
    )
    op.create_index(
        "ix_trust_ledger_entries_source_event_id",
        "trust_ledger_entries",
        ["source_event_id"],
    )
    op.create_index(
        "ix_trust_ledger_entries_risk_class",
        "trust_ledger_entries",
        ["risk_class"],
    )
    op.create_index(
        "ix_trust_ledger_entries_action_type",
        "trust_ledger_entries",
        ["action_type"],
    )
def downgrade() -> None:
    op.drop_index("ix_trust_ledger_entries_action_type", table_name="trust_ledger_entries")
    op.drop_index("ix_trust_ledger_entries_risk_class", table_name="trust_ledger_entries")
    op.drop_index("ix_trust_ledger_entries_source_event_id", table_name="trust_ledger_entries")
    op.drop_table("trust_ledger_entries")
    op.drop_index(
        "ix_outbox_delivery_receipts_source_event_id",
        table_name="outbox_delivery_receipts",
    )
    op.drop_index(
        "ix_outbox_delivery_receipts_outbox_id",
        table_name="outbox_delivery_receipts",
    )
    op.drop_table("outbox_delivery_receipts")
    op.drop_index("ix_outbox_aggregate_sequence_status", table_name="outbox")
    op.drop_index("ix_outbox_claim_expiry", table_name="outbox")
    op.drop_index("ix_outbox_source_event_id", table_name="outbox")
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("outbox") as batch:
            batch.drop_constraint("uq_outbox_source_channel_event", type_="unique")
            for column in (
                "receipt",
                "claim_expires_at",
                "claimed_at",
                "claim_token",
                "claimed_by",
                "payload_digest",
                "event_type",
                "source_event_id",
                "source_event_seq",
            ):
                batch.drop_column(column)
    else:
        op.drop_constraint("uq_outbox_source_channel_event", "outbox", type_="unique")
        for column in (
            "receipt",
            "claim_expires_at",
            "claimed_at",
            "claim_token",
            "claimed_by",
            "payload_digest",
            "event_type",
            "source_event_id",
            "source_event_seq",
        ):
            op.drop_column("outbox", column)
