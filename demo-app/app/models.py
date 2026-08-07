"""demo_app 库 ORM 表。

- 被治理对象的运行数据：kb_entries（pgvector 1024 预留）、prompt_versions、
  chat_logs、feedback
- Quality API v2 资源：versionsets、transitions、operations、idempotency
- 故障注入状态：fault_state
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    DateTime,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PromptVersion(Base):
    """prompt 模板 git 版本化注册表（文件 + 版本元数据 -> 内容 + digest）。"""

    __tablename__ = "prompt_versions"

    prompt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    digest: Mapped[str] = mapped_column(String(80), nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KBEntry(Base):
    """知识库条目（3C 数码：售后政策/产品参数/物流规则）。

    embedding 列 Phase 2 启用（D-001 #12：向量维度 1024 预留，检索先用全文+元数据过滤）。
    """

    __tablename__ = "kb_entries"
    __table_args__ = (UniqueConstraint("kb_id", "entry_id", name="uq_kb_identity"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entry_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kb_id: Mapped[str] = mapped_column(String(64), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)  # after_sales|product|logistics
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    slug: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    version: Mapped[str] = mapped_column(String(64), nullable=False, default="1.0.0")
    digest: Mapped[str] = mapped_column(String(80), nullable=False)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class VersionSet(Base):
    """Quality API v2 VersionSet：内容不可变，只有生命周期状态可迁移。"""

    __tablename__ = "versionsets"

    versionset_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    digest: Mapped[str] = mapped_column(String(80), nullable=False)
    canary_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    canary_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    labels: Mapped[Optional[dict[str, str]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TransitionRecord(Base):
    """生命周期迁移历史（VersionSetStatus.history）。"""

    __tablename__ = "transitions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    versionset_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    from_status: Mapped[str] = mapped_column(String(32), nullable=False)
    to_status: Mapped[str] = mapped_column(String(32), nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    operation_id: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="release-controller")


class Operation(Base):
    """异步写操作（stage/canary/promote/rollback），TTL 24h（Q1 裁决）。"""

    __tablename__ = "operations"

    operation_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    versionset_id: Mapped[str] = mapped_column(String(80), nullable=False)
    request: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyRecord(Base):
    """Idempotency-Key 记录：同 key + 同指纹 → 返回同一资源/operation。"""

    __tablename__ = "idempotency"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)  # versionset|operation
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChatLog(Base):
    """每次 /chat 一条（GET /v2/logs 读面）。"""

    __tablename__ = "chat_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    versionset_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    prompt_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    kb_manifest_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    model_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    usage: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)


class Feedback(Base):
    """用户反馈（POST /feedback 落库；GET /v2/feedback 读面）。"""

    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    feedback_id: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    request_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    versionset_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    rating: Mapped[str] = mapped_column(String(16), nullable=False)  # positive|negative|neutral
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="in_app")


class FaultState(Base):
    """故障注入状态（B1–B4）。snapshot 保存恢复基线所需原始数据。"""

    __tablename__ = "fault_state"

    fault_id: Mapped[str] = mapped_column(String(8), primary_key=True)
    injected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
