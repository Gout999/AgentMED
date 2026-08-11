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
    "(contract_version IS NULL AND event_contract_major IS NULL AND "
    "routing_key IS NULL AND exact_subject_binding IS NULL AND "
    "authority_receipt_id IS NULL) OR "
    "(contract_version IS NOT NULL AND ((contract_version = 'v4' AND "
    "workspace_id IS NOT NULL AND "
    "event_version = '1.0' AND transaction_id IS NOT NULL AND "
    "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
    "event_contract_major IS NULL AND routing_key IS NULL AND "
    "exact_subject_binding IS NULL AND authority_receipt_id IS NULL) OR "
    "(contract_version = 'v5' AND workspace_id IS NOT NULL AND "
    "event_version = '2.0' AND transaction_id IS NOT NULL AND "
    "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
    "event_contract_major = 2 AND routing_key IS NOT NULL AND "
    "exact_subject_binding IS NOT NULL AND authority_receipt_id IS NOT NULL)))"
)

_OUTBOX_CONTEXT = (
    "(contract_version IS NULL AND event_contract_major IS NULL) OR "
    "(contract_version IS NOT NULL AND ((contract_version = 'v4' AND "
    "workspace_id IS NOT NULL AND "
    "aggregate_type IS NOT NULL AND event_version = '1.0' AND "
    "transaction_id IS NOT NULL AND actor_principal IS NOT NULL AND "
    "payload_digest IS NOT NULL AND channel = 'v4.domain.events' AND "
    "event_contract_major IS NULL) OR "
    "(contract_version = 'v5' AND workspace_id IS NOT NULL AND "
    "aggregate_type IS NOT NULL AND event_version = '2.0' AND "
    "transaction_id IS NOT NULL AND actor_principal IS NOT NULL AND "
    "payload_digest IS NOT NULL AND channel = 'v5.domain.events' AND "
    "event_contract_major = 2)))"
)


_V5_TABLES = (
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
    "ai_application_lifecycle_revisions",
    "system_component_lifecycle_revisions",
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

_DOWNGRADE_BLOCKED = "012.v5_r1_history_prevents_downgrade"


def _sql_values(values: tuple[str, ...]) -> str:
    return ",".join(f"'{value}'" for value in values)


def _assert_no_legacy_v5_history() -> None:
    bind = op.get_bind()
    for table_name in _V5_TABLES:
        if bind.execute(
            sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")
        ).first() is not None:
            raise RuntimeError(
                "012.legacy_v5_event_envelope_requires_explicit_recovery"
            )
    if bind.execute(
        sa.text(
            "SELECT 1 FROM events WHERE aggregate_type IN ("
            f"{_sql_values(_V5_AGGREGATE_TYPES)}) LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError("012.legacy_v5_event_envelope_requires_explicit_recovery")
    if bind.execute(
        sa.text(
            "SELECT 1 FROM outbox WHERE aggregate_type IN ("
            f"{_sql_values(_V5_AGGREGATE_TYPES)}) LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError("012.legacy_v5_event_envelope_requires_explicit_recovery")
    count = bind.scalar(
        sa.text(
            "SELECT COUNT(*) FROM authority_receipts "
            "WHERE subject_kind IN (" f"{_sql_values(_V5_SUBJECT_KINDS)})"
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
    op.add_column(
        "outbox", sa.Column("event_contract_major", sa.Integer(), nullable=True)
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


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table_name in _V5_TABLES:
        if bind.execute(
            sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")
        ).first() is not None:
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    if bind.execute(
        sa.text(
            "SELECT 1 FROM events WHERE contract_version = 'v5' OR "
            "aggregate_type IN (" f"{_sql_values(_V5_AGGREGATE_TYPES)}) LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    if bind.execute(
        sa.text(
            "SELECT 1 FROM outbox WHERE contract_version = 'v5' OR "
            "aggregate_type IN (" f"{_sql_values(_V5_AGGREGATE_TYPES)}) LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError(_DOWNGRADE_BLOCKED)
    if bind.execute(
        sa.text(
            "SELECT 1 FROM authority_receipts WHERE subject_kind IN ("
            f"{_sql_values(_V5_SUBJECT_KINDS)}) LIMIT 1"
        )
    ).first() is not None:
        raise RuntimeError(_DOWNGRADE_BLOCKED)

    principals = sa.table(
        "public_principals",
        sa.column("principal_id", sa.String()),
        sa.column("trust_roles", sa.JSON()),
    )
    for row in bind.execute(
        sa.select(principals.c.principal_id, principals.c.trust_roles)
    ).mappings():
        if row["trust_roles"]:
            raise RuntimeError(_DOWNGRADE_BLOCKED)


def downgrade() -> None:
    _assert_downgrade_safe()

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
        batch.drop_column("event_contract_major")

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
