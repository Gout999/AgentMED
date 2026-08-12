"""Add the V5-2A Durable Work Kernel tables.

Revision ID: 014
Revises: 013
Create Date: 2026-08-12

D-016 / Master §6 2A-1: the Work aggregates (worker_task, attempt, proposal,
proposal_decision) get their first runtime persistence.  The migration adds
six new tables (work_tasks, work_attempts, work_attempt_capabilities,
work_proposals, work_proposal_decisions, work_reaction_ledger) and widens
exactly one existing check: the v5 branch of ``ck_outbox_v4_context`` now
accepts the dedicated ``v5.work.events`` channel alongside
``v5.domain.events`` (D-016 routes Work events there).  No existing column,
row, event, receipt or audit is rewritten; the widened check only admits an
additional channel value.

The tables are the mutable aggregate projections (state machines advance in
place; delete is forbidden at the ORM layer).  Fencing is task-scoped: every
claim increments ``work_tasks.lease_fencing_token`` by one and stale-token
writes are rejected by the service.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_TABLES = (
    "work_tasks",
    "work_attempts",
    "work_attempt_capabilities",
    "work_proposals",
    "work_proposal_decisions",
    "work_reaction_ledger",
)

_DOWNGRADE_BLOCKED = "014.v5_work_facts_prevent_downgrade"


def _create_work_tasks() -> None:
    op.create_table(
        "work_tasks",
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("task_kind", sa.String(64), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("input_digest", sa.String(80), nullable=False),
        sa.Column("requester_principal", sa.String(128), nullable=False),
        sa.Column(
            "state", sa.String(32), nullable=False, server_default="QUEUED"
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("current_attempt_id", sa.String(128), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_fencing_token", sa.BigInteger(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("request_fingerprint", sa.String(80), nullable=True),
        sa.Column("terminal_reason", sa.String(64), nullable=True),
        sa.Column("record_digest", sa.String(80), nullable=True),
        sa.Column("authority_receipt_id", sa.String(128), nullable=True),
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
        sa.PrimaryKeyConstraint("task_id", name="pk_work_tasks"),
        sa.UniqueConstraint(
            "workspace_id", "task_id", name="uq_work_task_workspace_task"
        ),
        sa.UniqueConstraint(
            "workspace_id", "idempotency_key", name="uq_work_task_workspace_idempotency"
        ),
        sa.CheckConstraint(
            "state IN ('QUEUED','LEASED','WAITING_RETRY','CANCEL_REQUESTED',"
            "'COMPLETED','CANCELLED','EXHAUSTED','BLOCKED_UNKNOWN')",
            name="ck_work_task_state",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 AND attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_work_task_attempt_bounds",
        ),
        sa.CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND "
            "lease_fencing_token IS NOT NULL AND lease_expires_at IS NOT NULL AND "
            "current_attempt_id IS NOT NULL) OR (state <> 'LEASED')",
            name="ck_work_task_lease_shape",
        ),
    )
    op.create_index(
        "ix_work_task_claimable",
        "work_tasks",
        ["workspace_id", "state", "lease_expires_at"],
    )
    op.create_index(
        "ix_work_task_workspace_state",
        "work_tasks",
        ["workspace_id", "state", "created_at"],
    )


def _create_work_attempts() -> None:
    op.create_table(
        "work_attempts",
        sa.Column("attempt_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column(
            "state", sa.String(32), nullable=False, server_default="CREATED"
        ),
        sa.Column("worker_identity", sa.String(128), nullable=False),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("output_payload", sa.JSON(), nullable=True),
        sa.Column("output_digest", sa.String(80), nullable=True),
        sa.Column("receipt_payload", sa.JSON(), nullable=True),
        sa.Column("fallback_of_attempt_id", sa.String(128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("record_digest", sa.String(80), nullable=True),
        sa.Column("authority_receipt_id", sa.String(128), nullable=True),
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
        sa.PrimaryKeyConstraint("attempt_id", name="pk_work_attempts"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["work_tasks.workspace_id", "work_tasks.task_id"],
            name="fk_work_attempt_task",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "task_id",
            "attempt_number",
            name="uq_work_attempt_task_number",
        ),
        sa.CheckConstraint(
            "state IN ('CREATED','STARTING','RUNNING','OUTPUT_RECORDED',"
            "'CANCEL_REQUESTED','SUCCEEDED','FAILED','TIMED_OUT','CANCELLED','UNKNOWN')",
            name="ck_work_attempt_state",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name="ck_work_attempt_number_positive"
        ),
        sa.CheckConstraint(
            "(state IN ('SUCCEEDED','FAILED','TIMED_OUT','CANCELLED') AND "
            "ended_at IS NOT NULL) OR "
            "(state NOT IN ('SUCCEEDED','FAILED','TIMED_OUT','CANCELLED') AND "
            "ended_at IS NULL)",
            name="ck_work_attempt_terminal_shape",
        ),
    )
    op.create_index(
        "ix_work_attempt_task_state",
        "work_attempts",
        ["workspace_id", "task_id", "state"],
    )


def _create_work_attempt_capabilities() -> None:
    op.create_table(
        "work_attempt_capabilities",
        sa.Column("capability_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("attempt_id", sa.String(128), nullable=False),
        sa.Column("scope", sa.String(64), nullable=False),
        sa.Column("fence_token", sa.BigInteger(), nullable=False),
        sa.Column("capability_digest", sa.String(80), nullable=False),
        sa.Column("issued_by_principal", sa.String(128), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("capability_id", name="pk_work_attempt_capabilities"),
        sa.ForeignKeyConstraint(
            ["attempt_id"],
            ["work_attempts.attempt_id"],
            name="fk_work_attempt_capability_attempt",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "attempt_id",
            name="uq_work_attempt_capability_attempt",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at", name="ck_work_attempt_capability_expiry"
        ),
        sa.CheckConstraint(
            "(consumed_at IS NULL) OR (consumed_at >= issued_at)",
            name="ck_work_attempt_capability_consumed",
        ),
    )


def _create_work_proposals() -> None:
    op.create_table(
        "work_proposals",
        sa.Column("proposal_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("attempt_id", sa.String(128), nullable=True),
        sa.Column("proposer_principal", sa.String(128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_digest", sa.String(80), nullable=False),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="SUBMITTED"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("record_digest", sa.String(80), nullable=True),
        sa.Column("authority_receipt_id", sa.String(128), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("proposal_id", name="pk_work_proposals"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["work_tasks.workspace_id", "work_tasks.task_id"],
            name="fk_work_proposal_task",
        ),
        sa.UniqueConstraint(
            "workspace_id", "proposal_id", name="uq_work_proposal_workspace_proposal"
        ),
        sa.CheckConstraint(
            "status IN ('SUBMITTED','ACCEPTED','REJECTED')",
            name="ck_work_proposal_status",
        ),
        sa.CheckConstraint(
            "(status = 'SUBMITTED' AND decided_at IS NULL) OR "
            "(status IN ('ACCEPTED','REJECTED') AND decided_at IS NOT NULL)",
            name="ck_work_proposal_decision_shape",
        ),
    )
    op.create_index(
        "ix_work_proposal_task_status",
        "work_proposals",
        ["workspace_id", "task_id", "status"],
    )


def _create_work_proposal_decisions() -> None:
    op.create_table(
        "work_proposal_decisions",
        sa.Column("decision_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("proposal_id", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decided_by_principal", sa.String(128), nullable=False),
        sa.Column("rationale", sa.String(1024), nullable=True),
        sa.Column("downstream_intent", sa.String(128), nullable=True),
        sa.Column("record_digest", sa.String(80), nullable=True),
        sa.Column("authority_receipt_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("decision_id", name="pk_work_proposal_decisions"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "proposal_id"],
            ["work_proposals.workspace_id", "work_proposals.proposal_id"],
            name="fk_work_proposal_decision_proposal",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "proposal_id",
            name="uq_work_proposal_decision_proposal",
        ),
        sa.CheckConstraint(
            "decision IN ('ACCEPTED','REJECTED')",
            name="ck_work_proposal_decision_value",
        ),
        sa.CheckConstraint(
            "(decision = 'ACCEPTED' AND downstream_intent IS NOT NULL) OR "
            "(decision = 'REJECTED')",
            name="ck_work_proposal_decision_downstream",
        ),
    )


def _create_work_reaction_ledger() -> None:
    op.create_table(
        "work_reaction_ledger",
        sa.Column("reaction_id", sa.String(128), nullable=False),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("source_event_id", sa.String(64), nullable=False),
        sa.Column("source_event_seq", sa.BigInteger(), nullable=False),
        sa.Column("owner_command", sa.String(128), nullable=False),
        sa.Column("target_aggregate_type", sa.String(64), nullable=False),
        sa.Column("target_aggregate_id", sa.String(128), nullable=False),
        sa.Column(
            "status", sa.String(32), nullable=False, server_default="PENDING"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("reaction_id", name="pk_work_reaction_ledger"),
        sa.UniqueConstraint(
            "source_event_id",
            "owner_command",
            name="uq_work_reaction_event_command",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','SUBMITTED','REJECTED')",
            name="ck_work_reaction_status",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND submitted_at IS NULL) OR "
            "(status IN ('SUBMITTED','REJECTED') AND submitted_at IS NOT NULL)",
            name="ck_work_reaction_shape",
        ),
    )
    op.create_index(
        "ix_work_reaction_target",
        "work_reaction_ledger",
        ["workspace_id", "target_aggregate_id", "status"],
    )


_OUTBOX_CONTEXT_WITH_WORK = (
    "(contract_version IS NULL AND event_contract_major IS NULL) OR "
    "(contract_version IS NOT NULL AND ((contract_version = 'v4' AND "
    "workspace_id IS NOT NULL AND aggregate_type IS NOT NULL AND "
    "event_version = '1.0' AND event_contract_major IS NULL AND "
    "transaction_id IS NOT NULL AND actor_principal IS NOT NULL AND "
    "payload_digest IS NOT NULL AND channel = 'v4.domain.events') OR "
    "(contract_version = 'v5' AND workspace_id IS NOT NULL AND "
    "aggregate_type IS NOT NULL AND event_version = '2.0' AND "
    "event_contract_major = 2 AND transaction_id IS NOT NULL AND "
    "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
    "channel IN ('v5.domain.events', 'v5.work.events'))))"
)

_OUTBOX_CONTEXT_WITHOUT_WORK = (
    "(contract_version IS NULL AND event_contract_major IS NULL) OR "
    "(contract_version IS NOT NULL AND ((contract_version = 'v4' AND "
    "workspace_id IS NOT NULL AND aggregate_type IS NOT NULL AND "
    "event_version = '1.0' AND event_contract_major IS NULL AND "
    "transaction_id IS NOT NULL AND actor_principal IS NOT NULL AND "
    "payload_digest IS NOT NULL AND channel = 'v4.domain.events') OR "
    "(contract_version = 'v5' AND workspace_id IS NOT NULL AND "
    "aggregate_type IS NOT NULL AND event_version = '2.0' AND "
    "event_contract_major = 2 AND transaction_id IS NOT NULL AND "
    "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
    "channel = 'v5.domain.events')))"
)


def upgrade() -> None:
    _create_work_tasks()
    _create_work_attempts()
    _create_work_attempt_capabilities()
    _create_work_proposals()
    _create_work_proposal_decisions()
    _create_work_reaction_ledger()
    # D-016 routes Work events to the dedicated ``v5.work.events`` outbox
    # channel; the v5 branch of the outbox context constraint must accept it.
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_v4_context", type_="check")
        batch.create_check_constraint(
            "ck_outbox_v4_context", _OUTBOX_CONTEXT_WITH_WORK
        )


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table in _NEW_TABLES:
        if (
            bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first()
            is not None
        ):
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    if (
        bind.execute(
            sa.text("SELECT 1 FROM outbox WHERE channel = 'v5.work.events' LIMIT 1")
        ).first()
        is not None
    ):
        raise RuntimeError(_DOWNGRADE_BLOCKED)


def downgrade() -> None:
    _assert_downgrade_safe()
    with op.batch_alter_table("outbox") as batch:
        batch.drop_constraint("ck_outbox_v4_context", type_="check")
        batch.create_check_constraint(
            "ck_outbox_v4_context", _OUTBOX_CONTEXT_WITHOUT_WORK
        )
    for table in reversed(_NEW_TABLES):
        op.drop_table(table)
