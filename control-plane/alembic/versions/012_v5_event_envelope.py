"""Persist the frozen V5 major-2 event envelope.

Revision ID: 012
Revises: 011
Create Date: 2026-08-11

Previously implemented V5 controller records were written without the complete
frozen major-2 event envelope.  Those events are immutable and cannot be
relabelled or silently supplemented by a migration.  The upgrade therefore
fails closed if any implemented V5 authority history already exists;
disposable development databases must be rebuilt and any durable environment
needs an explicit, evidence-backed recovery procedure.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EVENT_CONTEXT = (
    "contract_version IS NULL OR "
    "(contract_version = 'v4' AND workspace_id IS NOT NULL AND "
    "event_version IS NOT NULL AND transaction_id IS NOT NULL AND "
    "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
    "event_contract_major IS NULL AND routing_key IS NULL AND "
    "exact_subject_binding IS NULL AND authority_receipt_id IS NULL) OR "
    "(contract_version = 'v5' AND workspace_id IS NOT NULL AND "
    "event_version IS NOT NULL AND transaction_id IS NOT NULL AND "
    "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
    "event_contract_major = 2 AND routing_key IS NOT NULL AND "
    "exact_subject_binding IS NOT NULL AND authority_receipt_id IS NOT NULL)"
)

_OUTBOX_CONTEXT = (
    "contract_version IS NULL OR "
    "(contract_version = 'v4' AND workspace_id IS NOT NULL AND "
    "aggregate_type IS NOT NULL AND event_version IS NOT NULL AND "
    "transaction_id IS NOT NULL AND actor_principal IS NOT NULL AND "
    "payload_digest IS NOT NULL AND channel = 'v4.domain.events') OR "
    "(contract_version = 'v5' AND workspace_id IS NOT NULL AND "
    "aggregate_type IS NOT NULL AND event_version IS NOT NULL AND "
    "transaction_id IS NOT NULL AND actor_principal IS NOT NULL AND "
    "payload_digest IS NOT NULL AND channel = 'v5.domain.events')"
)


def _assert_no_legacy_v5_history() -> None:
    count = op.get_bind().scalar(
        sa.text(
            "SELECT COUNT(*) FROM authority_receipts "
            "WHERE subject_kind IN ("
            "'AI_APPLICATION','ENVIRONMENT','SYSTEM_COMPONENT','DEPENDENCY_EDGE',"
            "'COMPONENT_REVISION','TOPOLOGY_REVISION','SYSTEM_VERSION_SET',"
            "'BOOTSTRAP_ATTESTATION','SYSTEM_ASSIGNMENT',"
            "'APPLICATION_CASE_BINDING','ACCEPTANCE_CRITERIA_REVISION')"
        )
    )
    if int(count or 0) != 0:
        raise RuntimeError("012.legacy_v5_event_envelope_requires_explicit_recovery")


def upgrade() -> None:
    _assert_no_legacy_v5_history()

    op.add_column(
        "events", sa.Column("event_contract_major", sa.Integer(), nullable=True)
    )
    op.add_column("events", sa.Column("routing_key", sa.JSON(), nullable=True))
    op.add_column(
        "events", sa.Column("exact_subject_binding", sa.JSON(), nullable=True)
    )
    op.add_column(
        "events", sa.Column("authority_receipt_id", sa.String(128), nullable=True)
    )

    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("ck_events_v4_context", type_="check")
        batch.create_check_constraint("ck_events_v4_context", _EVENT_CONTEXT)

    op.create_index(
        "uq_events_v5_workspace_agg_seq",
        "events",
        ["workspace_id", "aggregate_type", "aggregate_id", "seq"],
        unique=True,
        sqlite_where=sa.text("contract_version = 'v5'"),
        postgresql_where=sa.text("contract_version = 'v5'"),
    )

    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_v4_context", type_="check")
        batch.create_check_constraint("ck_outbox_v4_context", _OUTBOX_CONTEXT)


def downgrade() -> None:
    count = op.get_bind().scalar(
        sa.text("SELECT COUNT(*) FROM events WHERE contract_version = 'v5'")
    )
    if int(count or 0) != 0:
        raise RuntimeError("012.v5_event_history_prevents_downgrade")

    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_v4_context", type_="check")
        batch.create_check_constraint(
            "ck_outbox_v4_context",
            "contract_version IS NULL OR (contract_version = 'v4' AND "
            "workspace_id IS NOT NULL AND aggregate_type IS NOT NULL AND "
            "event_version IS NOT NULL AND transaction_id IS NOT NULL AND "
            "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
            "channel = 'v4.domain.events')",
        )

    op.drop_index("uq_events_v5_workspace_agg_seq", table_name="events")
    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("ck_events_v4_context", type_="check")
        batch.create_check_constraint(
            "ck_events_v4_context",
            "contract_version IS NULL OR (contract_version = 'v4' AND "
            "workspace_id IS NOT NULL AND event_version IS NOT NULL AND "
            "transaction_id IS NOT NULL AND actor_principal IS NOT NULL AND "
            "payload_digest IS NOT NULL)",
        )
        batch.drop_column("authority_receipt_id")
        batch.drop_column("exact_subject_binding")
        batch.drop_column("routing_key")
        batch.drop_column("event_contract_major")
