"""Stage 1A PostgreSQL projections for the frozen v4 contracts.

Several tables (SourceConnection, QualityCase, SignalCaseLink and credential
lifecycle) are implementation projections assembled from the approved plan,
event catalog and public wire contract; they are not standalone wire schemas.
The immutable JSON payload remains the contract-bearing record.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.models.tables import Aggregate, Audit, Base, Event, Outbox


def _add_legacy_column(model: type[Base], name: str, type_: sa.types.TypeEngine[Any]) -> None:
    """Add a nullable expand column to the shared v3/v4 table and mapper."""

    table = model.__table__
    if name in table.c:
        return
    column = sa.Column(name, type_, nullable=True)
    table.append_column(column)
    model.__mapper__.add_property(name, column)


def _has_named_schema_item(table: sa.Table, name: str) -> bool:
    return any(getattr(item, "name", None) == name for item in table.constraints) or any(
        item.name == name for item in table.indexes
    )


def _augment_legacy_metadata() -> None:
    for model, columns in (
        (
            Aggregate,
            {
                "contract_version": String(16),
                "workspace_id": String(128),
                "transaction_id": String(128),
                "actor_principal": String(128),
                "record_digest": String(80),
            },
        ),
        (
            Event,
            {
                "contract_version": String(16),
                "workspace_id": String(128),
                "event_version": String(16),
                # Major-2 event-envelope fields are expand-only here.  They
                # remain NULL for every legacy/v4 row, so v3/v4 wire payloads
                # and persisted semantics are not reinterpreted.
                "event_contract_major": Integer(),
                "routing_key": JSON(none_as_null=True),
                "exact_subject_binding": JSON(none_as_null=True),
                "authority_receipt_id": String(128),
                "transaction_id": String(128),
                "actor_principal": String(128),
                "payload_digest": String(80),
            },
        ),
        (
            Audit,
            {
                "contract_version": String(16),
                "workspace_id": String(128),
                "transaction_id": String(128),
                "actor_principal": String(128),
                "audit_digest": String(80),
            },
        ),
        (
            Outbox,
            {
                "contract_version": String(16),
                "workspace_id": String(128),
                "aggregate_type": String(64),
                "event_version": String(16),
                "event_contract_major": Integer(),
                "transaction_id": String(128),
                "actor_principal": String(128),
            },
        ),
    ):
        for name, type_ in columns.items():
            _add_legacy_column(model, name, type_)

    aggregate_table = Aggregate.__table__
    if not _has_named_schema_item(aggregate_table, "ck_aggregates_v4_context"):
        aggregate_table.append_constraint(
            CheckConstraint(
                "contract_version IS NULL OR (contract_version = 'v4' AND "
                "workspace_id IS NOT NULL AND transaction_id IS NOT NULL AND "
                "actor_principal IS NOT NULL AND record_digest IS NOT NULL)",
                name="ck_aggregates_v4_context",
            )
        )

    event_table = Event.__table__
    for constraint in list(event_table.constraints):
        if isinstance(constraint, UniqueConstraint) and constraint.name == "uq_events_agg_seq":
            event_table.constraints.remove(constraint)
    if not _has_named_schema_item(event_table, "ck_events_v4_context"):
        event_table.append_constraint(
            CheckConstraint(
                "(contract_version IS NULL AND event_contract_major IS NULL AND "
                "routing_key IS NULL AND exact_subject_binding IS NULL AND "
                "authority_receipt_id IS NULL) OR "
                "(contract_version IS NOT NULL AND ((contract_version = 'v4' AND "
                "workspace_id IS NOT NULL AND "
                "event_version = '1.0' AND event_contract_major IS NULL AND "
                "routing_key IS NULL AND exact_subject_binding IS NULL AND "
                "authority_receipt_id IS NULL AND transaction_id IS NOT NULL AND "
                "actor_principal IS NOT NULL AND payload_digest IS NOT NULL) OR "
                "(contract_version = 'v5' AND workspace_id IS NOT NULL AND "
                "event_version = '2.0' AND event_contract_major = 2 AND "
                "routing_key IS NOT NULL AND exact_subject_binding IS NOT NULL AND "
                "authority_receipt_id IS NOT NULL AND transaction_id IS NOT NULL AND "
                "actor_principal IS NOT NULL AND payload_digest IS NOT NULL)))",
                name="ck_events_v4_context",
            )
        )
    if not _has_named_schema_item(event_table, "uq_events_legacy_agg_seq"):
        Index(
            "uq_events_legacy_agg_seq",
            event_table.c.aggregate_id,
            event_table.c.seq,
            unique=True,
            sqlite_where=sa.text("contract_version IS NULL"),
            postgresql_where=sa.text("contract_version IS NULL"),
        )
    if not _has_named_schema_item(event_table, "uq_events_v4_workspace_agg_seq"):
        Index(
            "uq_events_v4_workspace_agg_seq",
            event_table.c.workspace_id,
            event_table.c.aggregate_type,
            event_table.c.aggregate_id,
            event_table.c.seq,
            unique=True,
            sqlite_where=sa.text("contract_version = 'v4'"),
            postgresql_where=sa.text("contract_version = 'v4'"),
        )
    if not _has_named_schema_item(event_table, "uq_events_v5_workspace_agg_seq"):
        Index(
            "uq_events_v5_workspace_agg_seq",
            event_table.c.workspace_id,
            event_table.c.aggregate_type,
            event_table.c.aggregate_id,
            event_table.c.seq,
            unique=True,
            sqlite_where=sa.text("contract_version = 'v5'"),
            postgresql_where=sa.text("contract_version = 'v5'"),
        )
    if not _has_named_schema_item(event_table, "ix_events_v4_route"):
        Index(
            "ix_events_v4_route",
            event_table.c.contract_version,
            event_table.c.aggregate_type,
            event_table.c.event_type,
        )
    if not _has_named_schema_item(event_table, "ix_events_v4_timeline"):
        Index(
            "ix_events_v4_timeline",
            event_table.c.workspace_id,
            event_table.c.correlation_id,
            event_table.c.occurred_at,
            event_table.c.event_id,
        )

    audit_table = Audit.__table__
    if not _has_named_schema_item(audit_table, "ck_audit_v4_context"):
        audit_table.append_constraint(
            CheckConstraint(
                "contract_version IS NULL OR (contract_version = 'v4' AND "
                "workspace_id IS NOT NULL AND transaction_id IS NOT NULL AND "
                "actor_principal IS NOT NULL AND audit_digest IS NOT NULL)",
                name="ck_audit_v4_context",
            )
        )
    if not _has_named_schema_item(audit_table, "ix_audit_v4_transaction"):
        Index(
            "ix_audit_v4_transaction",
            audit_table.c.workspace_id,
            audit_table.c.transaction_id,
        )

    outbox_table = Outbox.__table__
    if not _has_named_schema_item(outbox_table, "ck_outbox_v4_context"):
        outbox_table.append_constraint(
            CheckConstraint(
                "(contract_version IS NULL AND event_contract_major IS NULL) OR "
                "(contract_version IS NOT NULL AND ((contract_version = 'v4' AND "
                "workspace_id IS NOT NULL AND "
                "aggregate_type IS NOT NULL AND event_version = '1.0' AND "
                "event_contract_major IS NULL AND transaction_id IS NOT NULL AND "
                "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
                "channel = 'v4.domain.events') OR "
                "(contract_version = 'v5' AND workspace_id IS NOT NULL AND "
                "aggregate_type IS NOT NULL AND event_version = '2.0' AND "
                "event_contract_major = 2 AND transaction_id IS NOT NULL AND "
                "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
                "channel = 'v5.domain.events')))",
                name="ck_outbox_v4_context",
            )
        )
    if not _has_named_schema_item(outbox_table, "ix_outbox_v4_dispatch"):
        Index(
            "ix_outbox_v4_dispatch",
            outbox_table.c.contract_version,
            outbox_table.c.channel,
            outbox_table.c.status,
            outbox_table.c.next_retry_at,
        )


_augment_legacy_metadata()


class SourceConnection(Base):
    __tablename__ = "source_connections"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "source_id", name="uq_source_connections_workspace_id"
        ),
        CheckConstraint("connector_kind = 'manual'", name="ck_source_connections_007_manual"),
        CheckConstraint(
            "state IN ('ACTIVE', 'DISABLED')", name="ck_source_connections_state"
        ),
        CheckConstraint(
            "connector_kind <> 'manual' OR credential_ref IS NULL",
            name="ck_source_connections_manual_no_credential",
        ),
        Index(
            "ix_source_connections_workspace_state_kind",
            "workspace_id",
            "state",
            "connector_kind",
        ),
    )

    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    connector_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    credential_ref: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    connection_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    created_by_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PublicPrincipal(Base):
    __tablename__ = "public_principals"
    __table_args__ = (
        UniqueConstraint("workspace_id", "principal_id", name="uq_public_principal_workspace"),
        CheckConstraint(
            "principal_type IN ('human','external_agent','service','connector')",
            name="ck_public_principal_type",
        ),
        CheckConstraint("state IN ('ACTIVE','REVOKED')", name="ck_public_principal_state"),
        Index("ix_public_principal_workspace_state", "workspace_id", "state", "principal_type"),
    )

    principal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    principal_type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    subject_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    audiences: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    project_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    environment_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    trust_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    claims_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PublicCredential(Base):
    __tablename__ = "public_credentials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "principal_id"],
            ["public_principals.workspace_id", "public_principals.principal_id"],
            name="fk_public_credential_principal",
        ),
        UniqueConstraint("credential_hash", name="uq_public_credentials_hash"),
        UniqueConstraint("issuer", "jti_digest", name="uq_public_credentials_issuer_jti"),
        CheckConstraint(
            "hash_algorithm = 'hmac-sha256-v1'",
            name="ck_public_credentials_hash_algorithm",
        ),
        CheckConstraint(
            "credential_hash LIKE 'sha256:%' AND length(credential_hash) = 71",
            name="ck_public_credentials_hash_format",
        ),
        CheckConstraint("state IN ('ACTIVE','REVOKED','EXPIRED')", name="ck_public_credentials_state"),
        Index(
            "ix_public_credentials_workspace_principal",
            "workspace_id",
            "principal_id",
            "revoked_at",
            "expires_at",
        ),
    )

    credential_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    credential_hash: Mapped[str] = mapped_column(String(80), nullable=False)
    hash_algorithm: Mapped[str] = mapped_column(
        String(32), nullable=False, default="hmac-sha256-v1"
    )
    jti_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    claims_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    audiences: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    project_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    environment_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PublicCommandIdempotency(Base):
    __tablename__ = "public_command_idempotency"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "principal_id"],
            ["public_principals.workspace_id", "public_principals.principal_id"],
            name="fk_public_idempotency_principal",
        ),
        UniqueConstraint(
            "workspace_id",
            "principal_id",
            "intent",
            "idempotency_key",
            name="uq_public_idempotency_scope",
        ),
        UniqueConstraint("idempotency_receipt_id", name="uq_public_idempotency_receipt"),
        CheckConstraint("state IN ('PENDING','ACCEPTED','COMPLETED')", name="ck_public_idempotency_state"),
        CheckConstraint(
            "state = 'PENDING' OR (resource_kind IS NOT NULL AND resource_id IS NOT NULL "
            "AND audit_ref IS NOT NULL AND response_payload IS NOT NULL "
            "AND response_digest IS NOT NULL AND idempotency_receipt_id IS NOT NULL "
            "AND receipt_payload IS NOT NULL AND receipt_digest IS NOT NULL)",
            name="ck_public_idempotency_result",
        ),
        Index("ix_public_idempotency_resource", "workspace_id", "resource_kind", "resource_id"),
    )

    idempotency_record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    principal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    intent: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(80), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    resource_kind: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    operation_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    audit_ref: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    response_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    response_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    idempotency_receipt_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    receipt_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    receipt_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SignalContent(Base):
    __tablename__ = "signal_contents"
    __table_args__ = (
        UniqueConstraint("workspace_id", "signal_content_id", name="uq_signal_content_workspace"),
        UniqueConstraint("workspace_id", "uri", name="uq_signal_content_uri"),
        Index("ix_signal_content_workspace_digest", "workspace_id", "content_digest"),
    )

    signal_content_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    content_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    privacy_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    redaction_status: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_content_persisted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    retention_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source_connections.workspace_id", "source_connections.source_id"],
            name="fk_signal_source_connection",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "signal_content_id"],
            ["signal_contents.workspace_id", "signal_contents.signal_content_id"],
            name="fk_signal_content",
        ),
        UniqueConstraint("workspace_id", "signal_id", name="uq_signal_workspace"),
        UniqueConstraint(
            "workspace_id",
            "source_id",
            "source_event_id",
            name="uq_signal_source_event",
        ),
        Index("ix_signals_workspace_kind_observed", "workspace_id", "signal_kind", "observed_at"),
        Index("ix_signals_workspace_project", "workspace_id", "project_id"),
        Index("ix_signals_workspace_agent", "workspace_id", "governed_agent_id"),
    )

    signal_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    environment_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    governed_agent_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_event_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_payload_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    adapter_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_origin: Mapped[str] = mapped_column(Text, nullable=False)
    signal_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    reporter_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reporter_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal_content_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    agent_run_ref_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    privacy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    untrusted_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    envelope_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    signal_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class QualityCase(Base):
    __tablename__ = "quality_cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "opening_signal_id"],
            ["signals.workspace_id", "signals.signal_id"],
            name="fk_quality_case_opening_signal",
        ),
        UniqueConstraint("workspace_id", "case_id", name="uq_quality_case_workspace"),
        CheckConstraint("state IN ('OPEN','RESOLVED')", name="ck_quality_case_state"),
        CheckConstraint("revision >= 1", name="ck_quality_case_revision"),
        Index("ix_quality_case_workspace_state", "workspace_id", "state", "updated_at"),
        Index("ix_quality_case_workspace_project", "workspace_id", "project_id"),
        Index("ix_quality_case_workspace_agent", "workspace_id", "governed_agent_id"),
    )

    case_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    environment_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    governed_agent_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    correlation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="NEEDS_CORRELATION"
    )
    triage_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNTRIAGED")
    opening_signal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    record_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class SignalCaseLink(Base):
    __tablename__ = "signal_case_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "signal_id"],
            ["signals.workspace_id", "signals.signal_id"],
            name="fk_signal_case_link_signal",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["quality_cases.workspace_id", "quality_cases.case_id"],
            name="fk_signal_case_link_case",
        ),
        UniqueConstraint(
            "workspace_id", "signal_id", "case_id", name="uq_signal_case_link_identity"
        ),
        Index("ix_signal_case_link_case", "workspace_id", "case_id"),
        Index("ix_signal_case_link_signal", "workspace_id", "signal_id"),
    )

    signal_case_link_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    signal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="LINKED")
    link_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    link_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentRunRef(Base):
    __tablename__ = "agent_run_refs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source_connections.workspace_id", "source_connections.source_id"],
            name="fk_agent_run_ref_source",
        ),
        UniqueConstraint("workspace_id", "agent_run_ref_id", name="uq_agent_run_ref_workspace"),
        UniqueConstraint(
            "workspace_id",
            "source_id",
            "locator_digest",
            name="uq_agent_run_ref_locator",
        ),
        Index(
            "ix_agent_run_ref_workspace_agent",
            "workspace_id",
            "governed_agent_id",
            "observed_at",
        ),
    )

    agent_run_ref_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    governed_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deep_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    locator_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    record_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    agent_run_ref_digest: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TraceEvidenceReceipt(Base):
    __tablename__ = "trace_evidence_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source_connections.workspace_id", "source_connections.source_id"],
            name="fk_trace_receipt_source",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "signal_id"],
            ["signals.workspace_id", "signals.signal_id"],
            name="fk_trace_receipt_signal",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "agent_run_ref_id"],
            ["agent_run_refs.workspace_id", "agent_run_refs.agent_run_ref_id"],
            name="fk_trace_receipt_run_ref",
        ),
        UniqueConstraint("workspace_id", "receipt_id", name="uq_trace_receipt_workspace"),
        UniqueConstraint("receipt_digest", name="uq_trace_receipt_digest"),
        CheckConstraint(
            "collection_mode <> 'NO_LOCATOR' OR (agent_run_ref_id IS NULL AND "
            "agent_run_ref_digest IS NULL AND query IS NULL AND completeness = 'UNKNOWN' "
            "AND artifact_ref IS NULL AND source_payload_digest IS NULL AND deep_link IS NULL)",
            name="ck_trace_receipt_no_locator",
        ),
        Index("ix_trace_receipt_signal", "workspace_id", "signal_id", "collected_at"),
        Index("ix_trace_receipt_run_ref", "workspace_id", "agent_run_ref_id"),
    )

    receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(String(128), nullable=False)
    signal_id: Mapped[str] = mapped_column(String(128), nullable=False)
    signal_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    collection_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    agent_run_ref_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    agent_run_ref_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    query: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON(none_as_null=True), nullable=True)
    requested_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    field_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    artifact_ref: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    source_payload_digest: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retention_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deep_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    failure: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    authority_receipt_id: Mapped[str] = mapped_column(String(128), nullable=False)
    receipt_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    receipt_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ControllerRegistration(Base):
    __tablename__ = "controller_registrations"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "controller_registration_id",
            "revision",
            name="uq_controller_registration_workspace_revision",
        ),
        CheckConstraint("state IN ('ACTIVE','REVOKED','EXPIRED')", name="ck_controller_registration_state"),
        Index(
            "ix_controller_registration_workspace_owner",
            "workspace_id",
            "owner",
            "state",
        ),
    )

    controller_registration_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    previous_snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    controller_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    allowed_commands: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    ownership_contract_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    event_catalog_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    service_identity_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    registered_by_human_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    registration_audit_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    registration_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    registration_digest: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)


class AuthorityReceipt(Base):
    __tablename__ = "authority_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "controller_registration_id", "controller_registration_revision"],
            [
                "controller_registrations.workspace_id",
                "controller_registrations.controller_registration_id",
                "controller_registrations.revision",
            ],
            name="fk_authority_receipt_controller",
        ),
        UniqueConstraint(
            "workspace_id",
            "subject_identity_key",
            name="uq_authority_receipt_subject_identity",
        ),
        UniqueConstraint("event_id", name="uq_authority_receipt_event"),
        UniqueConstraint("authority_receipt_digest", name="uq_authority_receipt_digest"),
        CheckConstraint(
            "subject_revision IS NULL OR subject_revision >= 1",
            name="ck_authority_receipt_subject_revision",
        ),
        CheckConstraint(
            "subject_identity_key = subject_kind || ':' || subject_id || ':' || "
            "CASE WHEN subject_revision IS NULL THEN 'singleton' "
            "ELSE CAST(subject_revision AS VARCHAR) END",
            name="ck_authority_receipt_subject_identity_key",
        ),
        Index("ix_authority_receipt_transaction", "workspace_id", "transaction_id"),
        Index("ix_authority_receipt_subject", "workspace_id", "subject_kind", "subject_id"),
        Index(
            "ix_authority_receipt_controller",
            "workspace_id",
            "controller_registration_id",
            "controller_registration_revision",
        ),
    )

    authority_receipt_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    controller_registration_id: Mapped[str] = mapped_column(String(128), nullable=False)
    controller_registration_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    controller_registration_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(128), nullable=False)
    subject_revision: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    subject_identity_key: Mapped[str] = mapped_column(String(512), nullable=False)
    subject_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    resource: Mapped[str] = mapped_column(String(64), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    controller_principal: Mapped[str] = mapped_column(String(128), nullable=False)
    command: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    transaction_id: Mapped[str] = mapped_column(String(128), nullable=False)
    audit_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    receipt_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    authority_receipt_digest: Mapped[str] = mapped_column(String(80), nullable=False)


def _immutable_write_forbidden(_mapper, _connection, target) -> None:  # type: ignore[no-untyped-def]
    raise RuntimeError(f"v4.immutable_record_update_forbidden:{target.__tablename__}")


def _idempotency_update_guard(_mapper, _connection, target) -> None:  # type: ignore[no-untyped-def]
    inspection = sa.inspect(target)
    immutable_identity_fields = (
        "idempotency_record_id",
        "workspace_id",
        "principal_id",
        "intent",
        "idempotency_key",
        "request_fingerprint",
        "request_id",
        "created_at",
        "expires_at",
    )
    if any(
        inspection.attrs[field_name].history.has_changes()
        for field_name in immutable_identity_fields
    ):
        raise RuntimeError("v4.idempotency_identity_update_forbidden")

    state_history = inspection.attrs.state.history
    prior = state_history.deleted[0] if state_history.deleted else target.state
    current = state_history.added[0] if state_history.added else target.state
    if prior == "PENDING" and current in {"ACCEPTED", "COMPLETED"}:
        return
    raise RuntimeError("v4.idempotency_terminal_update_forbidden")


for _immutable_model in (
    SignalContent,
    Signal,
    SignalCaseLink,
    AgentRunRef,
    TraceEvidenceReceipt,
    ControllerRegistration,
    AuthorityReceipt,
):
    event.listen(_immutable_model, "before_update", _immutable_write_forbidden)
    event.listen(_immutable_model, "before_delete", _immutable_write_forbidden)

event.listen(PublicCommandIdempotency, "before_update", _idempotency_update_guard)
event.listen(PublicCommandIdempotency, "before_delete", _immutable_write_forbidden)
