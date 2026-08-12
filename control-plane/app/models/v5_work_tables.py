"""V5-2A Durable Work Kernel tables (D-016, Master §6 2A-1).

The Work aggregates keep their V4 semantic owners and state machines
(contracts/v4/events/state-machines.yaml, reused without redefinition per
contracts/v5/state-machines.yaml#v5_2a_work_kernel_contract) while every
domain event they emit uses the major-2 envelope on the existing ``events``
and ``outbox`` tables.  No new event table is introduced.

These tables are the mutable aggregate projections.  The immutable history of
record is the event stream plus the authority receipts, audits and outbox
rows written in the same PostgreSQL unit of work.  A projection row may
advance through its state machine; it may never be deleted, and replay of a
projection alone is never transition authority.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.tables import Base

WORK_TASK_STATES = (
    "QUEUED",
    "LEASED",
    "WAITING_RETRY",
    "CANCEL_REQUESTED",
    "COMPLETED",
    "CANCELLED",
    "EXHAUSTED",
    "BLOCKED_UNKNOWN",
)

ATTEMPT_STATES = (
    "CREATED",
    "STARTING",
    "RUNNING",
    "OUTPUT_RECORDED",
    "CANCEL_REQUESTED",
    "SUCCEEDED",
    "FAILED",
    "TIMED_OUT",
    "CANCELLED",
    "UNKNOWN",
)

PROPOSAL_STATUSES = ("SUBMITTED", "ACCEPTED", "REJECTED")
PROPOSAL_DECISIONS = ("ACCEPTED", "REJECTED")
REACTION_STATUSES = ("PENDING", "SUBMITTED", "REJECTED")

# Outbox channel for the versioned Work event stream (D-016).  The legacy
# fixed worker deliberately ignores this channel until the V5 dispatcher
# claims it; mixing channels silently is forbidden by contract.
V5_WORK_EVENT_CHANNEL = "v5.work.events"

_WORK_TASK_STATE_SQL = "(" + ",".join(f"'{s}'" for s in WORK_TASK_STATES) + ")"
_ATTEMPT_STATE_SQL = "(" + ",".join(f"'{s}'" for s in ATTEMPT_STATES) + ")"
_PROPOSAL_STATUS_SQL = "(" + ",".join(f"'{s}'" for s in PROPOSAL_STATUSES) + ")"
_DECISION_SQL = "(" + ",".join(f"'{s}'" for s in PROPOSAL_DECISIONS) + ")"
_REACTION_STATUS_SQL = "(" + ",".join(f"'{s}'" for s in REACTION_STATUSES) + ")"


class WorkTask(Base):
    """worker_task aggregate projection (V4 machine: QUEUED/LEASED/...).

    The lease columns are the task-scoped fencing authority: every claim
    increments ``lease_fencing_token`` by one, so a stale writer holding an
    older token is rejected, never silently ignored.  A task carries a lease
    exactly when it is in ``LEASED``.
    """

    __tablename__ = "work_tasks"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "task_id",
            name="uq_work_task_workspace_task",
        ),
        UniqueConstraint(
            "workspace_id",
            "idempotency_key",
            name="uq_work_task_workspace_idempotency",
        ),
        CheckConstraint(
            f"state IN {_WORK_TASK_STATE_SQL}", name="ck_work_task_state"
        ),
        CheckConstraint(
            "max_attempts >= 1 AND attempt_count >= 0 AND attempt_count <= max_attempts",
            name="ck_work_task_attempt_bounds",
        ),
        CheckConstraint(
            "(state = 'LEASED' AND lease_owner IS NOT NULL AND "
            "lease_fencing_token IS NOT NULL AND lease_expires_at IS NOT NULL AND "
            "current_attempt_id IS NOT NULL) OR "
            "(state <> 'LEASED')",
            name="ck_work_task_lease_shape",
        ),
        Index(
            "ix_work_task_claimable",
            "workspace_id",
            "state",
            "lease_expires_at",
        ),
        Index(
            "ix_work_task_workspace_state",
            "workspace_id",
            "state",
            "created_at",
        ),
    )

    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    task_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    requester_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    current_attempt_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    lease_owner: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    lease_fencing_token: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_heartbeat_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    request_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(80), nullable=True
    )
    terminal_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    record_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    authority_receipt_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkAttempt(Base):
    """attempt aggregate projection (V4 machine: CREATED/.../UNKNOWN).

    ``fence_token`` is the lease token this attempt was created under; writes
    carrying any other token are stale and must be rejected.  ``UNKNOWN`` is
    not terminal: reconcile (guarded by a trustworthy terminal receipt) must
    precede any retry, per the V4 machine.
    """

    __tablename__ = "work_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["work_tasks.workspace_id", "work_tasks.task_id"],
            name="fk_work_attempt_task",
        ),
        UniqueConstraint(
            "workspace_id",
            "task_id",
            "attempt_number",
            name="uq_work_attempt_task_number",
        ),
        CheckConstraint(
            f"state IN {_ATTEMPT_STATE_SQL}", name="ck_work_attempt_state"
        ),
        CheckConstraint(
            "attempt_number >= 1", name="ck_work_attempt_number_positive"
        ),
        CheckConstraint(
            "(state IN ('SUCCEEDED','FAILED','TIMED_OUT','CANCELLED') AND "
            "ended_at IS NOT NULL) OR "
            "(state NOT IN ('SUCCEEDED','FAILED','TIMED_OUT','CANCELLED') AND "
            "ended_at IS NULL)",
            name="ck_work_attempt_terminal_shape",
        ),
        Index(
            "ix_work_attempt_task_state",
            "workspace_id",
            "task_id",
            "state",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="CREATED")
    worker_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    output_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    receipt_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON, nullable=True
    )
    fallback_of_attempt_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    record_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    authority_receipt_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkAttemptCapability(Base):
    """Attempt-scoped runtime capability issued inside the claim transaction.

    Master §6 2A-2: ``work.claim`` issues an attempt-scoped runtime
    capability in the same unit of work as the attempt row, event, audit,
    receipt and outbox rows.  This is deliberately not the full V4
    capability_lease aggregate (whose 37-field contract is a later stage):
    no capability domain events are emitted by V5-2A, so the frozen event
    catalog stays exact.
    """

    __tablename__ = "work_attempt_capabilities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["attempt_id"],
            ["work_attempts.attempt_id"],
            name="fk_work_attempt_capability_attempt",
        ),
        UniqueConstraint(
            "workspace_id",
            "attempt_id",
            name="uq_work_attempt_capability_attempt",
        ),
        CheckConstraint(
            "expires_at > issued_at", name="ck_work_attempt_capability_expiry"
        ),
        CheckConstraint(
            "(consumed_at IS NULL) OR (consumed_at >= issued_at)",
            name="ck_work_attempt_capability_consumed",
        ),
    )

    capability_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    fence_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capability_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    issued_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkProposal(Base):
    """proposal aggregate (owner: proposal-controller).

    A proposal is advisory input to the Work loop; only the owning
    controller may turn it into a decision.  Post-action proposals are
    rejected by the service against this table's terminal decision state.
    """

    __tablename__ = "work_proposals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "task_id"],
            ["work_tasks.workspace_id", "work_tasks.task_id"],
            name="fk_work_proposal_task",
        ),
        UniqueConstraint(
            "workspace_id",
            "proposal_id",
            name="uq_work_proposal_workspace_proposal",
        ),
        CheckConstraint(
            f"status IN {_PROPOSAL_STATUS_SQL}", name="ck_work_proposal_status"
        ),
        CheckConstraint(
            "(status = 'SUBMITTED' AND decided_at IS NULL) OR "
            "(status IN ('ACCEPTED','REJECTED') AND decided_at IS NOT NULL)",
            name="ck_work_proposal_decision_shape",
        ),
        Index(
            "ix_work_proposal_task_status",
            "workspace_id",
            "task_id",
            "status",
        ),
    )

    proposal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    task_id: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    proposer_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUBMITTED")
    record_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    authority_receipt_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkProposalDecision(Base):
    """proposal_decision aggregate (owner: proposal-controller).

    An accept decision is only valid when written in the same transaction as
    its first downstream owner command, the decision event, the audit row and
    the outbox row (D-016 ghost-success rule).  ``downstream_intent`` records
    that command; a NULL downstream intent on an ACCEPTED row is a contract
    violation caught by the service layer, so the CHECK below keeps it
    structurally impossible.
    """

    __tablename__ = "work_proposal_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "proposal_id"],
            ["work_proposals.workspace_id", "work_proposals.proposal_id"],
            name="fk_work_proposal_decision_proposal",
        ),
        UniqueConstraint(
            "workspace_id",
            "proposal_id",
            name="uq_work_proposal_decision_proposal",
        ),
        CheckConstraint(
            f"decision IN {_DECISION_SQL}", name="ck_work_proposal_decision_value"
        ),
        CheckConstraint(
            "(decision = 'ACCEPTED' AND downstream_intent IS NOT NULL) OR "
            "(decision = 'REJECTED')",
            name="ck_work_proposal_decision_downstream",
        ),
    )

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    proposal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    rationale: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    downstream_intent: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    record_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    authority_receipt_id: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WorkReactionLedger(Base):
    """Reaction ledger: a reaction may only submit the next owner command.

    Rows are append-only in effect (PENDING -> SUBMITTED/REJECTED once), one
    row per (source event, owner command) pair so consumer retries are
    idempotent.  A reaction never writes domain success; the row records
    intent submission, nothing more.
    """

    __tablename__ = "work_reaction_ledger"
    __table_args__ = (
        UniqueConstraint(
            "source_event_id",
            "owner_command",
            name="uq_work_reaction_event_command",
        ),
        CheckConstraint(
            f"status IN {_REACTION_STATUS_SQL}", name="ck_work_reaction_status"
        ),
        CheckConstraint(
            "(status = 'PENDING' AND submitted_at IS NULL) OR "
            "(status IN ('SUBMITTED','REJECTED') AND submitted_at IS NOT NULL)",
            name="ck_work_reaction_shape",
        ),
        Index(
            "ix_work_reaction_target",
            "workspace_id",
            "target_aggregate_id",
            "status",
        ),
    )

    reaction_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner_command: Mapped[str] = mapped_column(String(128), nullable=False)
    target_aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    submitted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


def _work_immutable_delete_forbidden(_mapper, _connection, target) -> None:  # type: ignore[no-untyped-def]
    raise RuntimeError(f"v5.work.delete_forbidden:{target.__tablename__}")


# Work aggregate projections advance through their state machines, so update
# is legitimate; deletion never is.  The immutable record of what happened
# lives in the events/outbox/audit/receipt rows written in the same unit of
# work, not in these projections.
for _work_model in (
    WorkTask,
    WorkAttempt,
    WorkAttemptCapability,
    WorkProposal,
    WorkProposalDecision,
    WorkReactionLedger,
):
    event.listen(
        _work_model, "before_delete", _work_immutable_delete_forbidden
    )
