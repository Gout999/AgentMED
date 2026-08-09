"""Authoritative control-plane ORM (casebase remains in its separate database)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class Aggregate(Base):
    """七状态机权威状态（投影；事件可重放）。"""

    __tablename__ = "aggregates"

    aggregate_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    aggregate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Event(Base):
    """事件溯源流水（只增不改）。"""

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("aggregate_id", "seq", name="uq_events_agg_seq"),)

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # 信封字段
    causation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="none")
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    trace_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Inbox(Base):
    """投诉接入去重。"""

    __tablename__ = "inbox"

    dedup_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # webhook | poll
    external_id: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    case_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)  # FILED | MERGED | DUPLICATE


class Outbox(Base):
    """可靠外发（与状态迁移同事务）。"""

    __tablename__ = "outbox"
    __table_args__ = (
        UniqueConstraint(
            "source_event_id",
            "channel",
            "event_type",
            name="uq_outbox_source_channel_event",
        ),
        Index("ix_outbox_status_retry", "status", "next_retry_at"),
        Index("ix_outbox_claim_expiry", "status", "claim_expires_at"),
        Index(
            "ix_outbox_aggregate_sequence_status",
            "aggregate_id",
            "source_event_seq",
            "status",
        ),
    )

    outbox_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_event_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_attempted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    receipt: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class OutboxDeliveryReceipt(Base):
    """Append-only dispatcher acknowledgement bound to one exact outbox payload."""

    __tablename__ = "outbox_delivery_receipts"
    __table_args__ = (
        Index("ix_outbox_delivery_receipts_outbox_id", "outbox_id"),
    )

    receipt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    outbox_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    receipt: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Lease(Base):
    """Worker 领单租约 + fencing token。"""

    __tablename__ = "leases"

    resource_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    lease_id: Mapped[str] = mapped_column(String(64), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerSuggestionReceipt(Base):
    """Idempotency anchor for one lease-authorized worker suggestion."""

    __tablename__ = "worker_suggestion_receipts"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FencingCounter(Base):
    """全局 fencing token 发号器（单行表）。"""

    __tablename__ = "fencing_counter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    next_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)


class WorkOrder(Base):
    """WorkOrder 留档（payload 不可改）。"""

    __tablename__ = "workorders"

    workorder_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GateReportRecord(Base):
    """Authoritative immutable GateReport plus post-freeze WorkOrder binding.

    GateReport content is hashed before the final WorkOrder exists. Because the WorkOrder hash
    itself includes the GateReport digest, storing the final WorkOrder hash inside that report
    would create an impossible hash cycle. The control plane therefore seals both immutable
    hashes in `binding_digest` when the WorkOrder is registered.
    """

    __tablename__ = "gate_reports"
    __table_args__ = (Index("ix_gate_reports_workorder_hash", "workorder_hash"),)

    eval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    workorder_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # Initial and post-canary reports intentionally bind to the same immutable
    # WorkOrder.  The eval/report identities remain unique; this projection is
    # a non-unique lookup key for the complete multi-stage gate chain.
    workorder_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    target_versionset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    target_versionset_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    target_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dataset_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    candidate_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    report_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    binding_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    authorization_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    overall_status: Mapped[str] = mapped_column(String(32), nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    bound_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class Approval(Base):
    """审批授权：nonce 唯一，pending→consumed/rejected/expired。"""

    __tablename__ = "approvals"

    approval_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workorder_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    workorder_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # pending | consumed | rejected | expired
    decision: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    approver: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TrustLedger(Base):
    """信任账本：PK (risk_class, action_type, epoch)。"""

    __tablename__ = "trust_ledger"

    risk_class: Mapped[str] = mapped_column(String(32), primary_key=True)
    action_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, primary_key=True)
    successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trials: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    autonomy_state: Mapped[str] = mapped_column(String(32), nullable=False, default="MANUAL")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TrustLedgerEntry(Base):
    """Immutable one-action/one-sample Trust entry consumed from a domain event."""

    __tablename__ = "trust_ledger_entries"
    __table_args__ = (
        UniqueConstraint(
            "risk_class",
            "action_type",
            "action_ref",
            name="uq_trust_entry_action",
        ),
        Index("ix_trust_ledger_entries_source_event_id", "source_event_id"),
    )

    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    risk_class: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    epoch: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    successes: Mapped[int] = mapped_column(Integer, nullable=False)
    trials: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Audit(Base):
    """权威审计（写失败即拒业务）。"""

    __tablename__ = "audit"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(256), nullable=False)
    params_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_refs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class ControllerOperation(Base):
    """控制面侧异步 operation 跟踪（写 Quality API 后本地记录，TTL 24h）。"""

    __tablename__ = "controller_operations"

    operation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    release_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # stage|canary|promote|rollback
    remote_operation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # pending|succeeded|failed|unknown|expired (UNKNOWN is resolved only by reconcile)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    approval_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    expected_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReleaseClosure(Base):
    """Immutable terminal-release notification continuation configured before action."""

    __tablename__ = "release_closures"

    release_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    body_ref: Mapped[str] = mapped_column(Text, nullable=False)
    body_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="configured")
    notification_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True)
    configured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    queued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
