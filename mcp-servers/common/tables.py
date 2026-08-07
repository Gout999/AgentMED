"""mcp-servers 自有表（mcp_* 前缀，与 control-plane 公共 schema 无冲突）。

生产建表走 migrations/001_init.sql（幂等）；单测用 create_all（SQLite 内存）。
案例库的 pgvector 列（Phase 2 预留）由 migration 对 PG 单独 ALTER 追加，
ORM 层保持跨库可移植（D-001 #12：Phase 1 全文+元数据过滤）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TrustLedger(Base):
    """信任账本：PK (risk_class, action_type, epoch)，原始整数计数（spec §6.3）。"""

    __tablename__ = "mcp_trust_ledger"

    risk_class: Mapped[str] = mapped_column(String(32), primary_key=True)
    action_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, primary_key=True)
    successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trials: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    autonomy_state: Mapped[str] = mapped_column(String(32), nullable=False, default="MANUAL")
    suspended_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_promotion_ref: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class ApprovalGrantRow(Base):
    """审批授权（spec §5.2）：nonce 唯一防重放；pending→consumed/rejected/expired。"""

    __tablename__ = "mcp_approval_grants"

    approval_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workorder_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    workorder_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    decision: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    approver: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    nonce_consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    proof: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    audit_uri: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class AuditRow(Base):
    """权威审计（spec §7.6）：只增不改；写失败即拒业务（§11.4）。"""

    __tablename__ = "mcp_audit"

    audit_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target: Mapped[str] = mapped_column(String(256), nullable=False)
    params_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False, default="success")
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_refs: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)


class NotificationMessage(Base):
    """飞书 mock 群消息日志（双向留痕：出站写操作落 notification 事件）。"""

    __tablename__ = "mcp_notification_messages"
    __table_args__ = (UniqueConstraint("outbox_id", name="uq_mcp_notif_outbox_id"),)

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)  # feishu | feishu-mock | matrix
    room: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_ref: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    msg_ref: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    outbox_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="delivered")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class CasebaseDoc(Base):
    """案例库（spec §7.7 子集）：全文+元数据过滤（Phase 1）；向量列 PG 迁移预留。

    属性 `meta` 映射列 `metadata`（SQLAlchemy Declarative 中 metadata 为保留名）。
    """

    __tablename__ = "mcp_casebase"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class EvalRun(Base):
    """门禁评测运行（异步任务句柄 = eval_id）。"""

    __tablename__ = "mcp_eval_runs"

    eval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workorder_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    suite_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    report: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    report_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class WorkOrderDraft(Base):
    """release-admin 本地 WorkOrder 登记（draft → frozen）。"""

    __tablename__ = "mcp_workorders"

    workorder_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")  # DRAFT | FROZEN
    draft_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    frozen_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    gate_report_ref: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    gate_report_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="agent")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class Suggestion(Base):
    """case-admin 建议事件（只记录，不直接改状态；控制面裁决后迁移）。"""

    __tablename__ = "mcp_suggestions"

    suggestion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    worker_id: Mapped[str] = mapped_column(String(128), nullable=False)
    fencing_token: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # triage|attribution|fix|gate|verify
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="recorded")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ApprovalRequest(Base):
    """release-admin 提请审批记录（本地跟踪；授权决定来自控制面/人工）。"""

    __tablename__ = "mcp_approval_requests"

    approval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workorder_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    workorder_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # pending|approved|rejected|expired（与控制面 approvals.status 同步）
    evidence_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="feishu")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
