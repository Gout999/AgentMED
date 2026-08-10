"""Stage 1A signal, trace evidence, public auth and authority projections.

Revision ID: 007
Revises: 006
Create Date: 2026-08-10

This is an expand migration.  Existing v3 rows remain valid with NULL v4
context; rows explicitly marked ``contract_version='v4'`` fail closed unless
their workspace, transaction, actor and digest context is complete.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DOWNGRADE_BLOCKED = "007.downgrade_blocked.immutable_v4_records_exist"
_NEW_TABLES = (
    "source_connections",
    "public_principals",
    "public_credentials",
    "public_command_idempotency",
    "signal_contents",
    "signals",
    "quality_cases",
    "signal_case_links",
    "agent_run_refs",
    "trace_evidence_receipts",
    "controller_registrations",
    "authority_receipts",
)


def _add_shared_v4_columns() -> None:
    for table, columns in (
        (
            "aggregates",
            (
                sa.Column("contract_version", sa.String(16), nullable=True),
                sa.Column("workspace_id", sa.String(128), nullable=True),
                sa.Column("transaction_id", sa.String(128), nullable=True),
                sa.Column("actor_principal", sa.String(128), nullable=True),
                sa.Column("record_digest", sa.String(80), nullable=True),
            ),
        ),
        (
            "events",
            (
                sa.Column("contract_version", sa.String(16), nullable=True),
                sa.Column("workspace_id", sa.String(128), nullable=True),
                sa.Column("event_version", sa.String(16), nullable=True),
                sa.Column("transaction_id", sa.String(128), nullable=True),
                sa.Column("actor_principal", sa.String(128), nullable=True),
                sa.Column("payload_digest", sa.String(80), nullable=True),
            ),
        ),
        (
            "audit",
            (
                sa.Column("contract_version", sa.String(16), nullable=True),
                sa.Column("workspace_id", sa.String(128), nullable=True),
                sa.Column("transaction_id", sa.String(128), nullable=True),
                sa.Column("actor_principal", sa.String(128), nullable=True),
                sa.Column("audit_digest", sa.String(80), nullable=True),
            ),
        ),
        (
            "outbox",
            (
                sa.Column("contract_version", sa.String(16), nullable=True),
                sa.Column("workspace_id", sa.String(128), nullable=True),
                sa.Column("aggregate_type", sa.String(64), nullable=True),
                sa.Column("event_version", sa.String(16), nullable=True),
                sa.Column("transaction_id", sa.String(128), nullable=True),
                sa.Column("actor_principal", sa.String(128), nullable=True),
            ),
        ),
    ):
        for column in columns:
            op.add_column(table, column)

    constraints = {
        "aggregates": (
            "ck_aggregates_v4_context",
            "contract_version IS NULL OR (contract_version = 'v4' AND "
            "workspace_id IS NOT NULL AND transaction_id IS NOT NULL AND "
            "actor_principal IS NOT NULL AND record_digest IS NOT NULL)",
        ),
        "events": (
            "ck_events_v4_context",
            "contract_version IS NULL OR (contract_version = 'v4' AND "
            "workspace_id IS NOT NULL AND event_version IS NOT NULL AND "
            "transaction_id IS NOT NULL AND actor_principal IS NOT NULL AND "
            "payload_digest IS NOT NULL)",
        ),
        "audit": (
            "ck_audit_v4_context",
            "contract_version IS NULL OR (contract_version = 'v4' AND "
            "workspace_id IS NOT NULL AND transaction_id IS NOT NULL AND "
            "actor_principal IS NOT NULL AND audit_digest IS NOT NULL)",
        ),
        "outbox": (
            "ck_outbox_v4_context",
            "contract_version IS NULL OR (contract_version = 'v4' AND "
            "workspace_id IS NOT NULL AND aggregate_type IS NOT NULL AND "
            "event_version IS NOT NULL AND transaction_id IS NOT NULL AND "
            "actor_principal IS NOT NULL AND payload_digest IS NOT NULL AND "
            "channel = 'v4.domain.events')",
        ),
    }
    if op.get_bind().dialect.name == "sqlite":
        for table, (name, condition) in constraints.items():
            with op.batch_alter_table(table) as batch:
                batch.create_check_constraint(name, condition)
    else:
        for table, (name, condition) in constraints.items():
            op.create_check_constraint(name, table, condition)

    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("events") as batch:
            batch.drop_constraint("uq_events_agg_seq", type_="unique")
    else:
        op.drop_constraint("uq_events_agg_seq", "events", type_="unique")

    legacy_where = sa.text("contract_version IS NULL")
    v4_where = sa.text("contract_version = 'v4'")
    op.create_index(
        "uq_events_legacy_agg_seq",
        "events",
        ["aggregate_id", "seq"],
        unique=True,
        sqlite_where=legacy_where,
        postgresql_where=legacy_where,
    )
    op.create_index(
        "uq_events_v4_workspace_agg_seq",
        "events",
        ["workspace_id", "aggregate_type", "aggregate_id", "seq"],
        unique=True,
        sqlite_where=v4_where,
        postgresql_where=v4_where,
    )
    op.create_index(
        "ix_events_v4_route",
        "events",
        ["contract_version", "aggregate_type", "event_type"],
    )
    op.create_index(
        "ix_events_v4_timeline",
        "events",
        ["workspace_id", "correlation_id", "occurred_at", "event_id"],
    )
    op.create_index(
        "ix_audit_v4_transaction", "audit", ["workspace_id", "transaction_id"]
    )
    op.create_index(
        "ix_outbox_v4_dispatch",
        "outbox",
        ["contract_version", "channel", "status", "next_retry_at"],
    )


def _create_source_and_auth_tables() -> None:
    op.create_table(
        "source_connections",
        sa.Column("source_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("connector_kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("credential_ref", sa.String(256), nullable=True),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("connection_digest", sa.String(80), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "source_id", name="uq_source_connections_workspace_id"
        ),
        sa.CheckConstraint("connector_kind = 'manual'", name="ck_source_connections_007_manual"),
        sa.CheckConstraint(
            "state IN ('ACTIVE', 'DISABLED')", name="ck_source_connections_state"
        ),
        sa.CheckConstraint(
            "connector_kind <> 'manual' OR credential_ref IS NULL",
            name="ck_source_connections_manual_no_credential",
        ),
    )
    op.create_index(
        "ix_source_connections_workspace_state_kind",
        "source_connections",
        ["workspace_id", "state", "connector_kind"],
    )

    op.create_table(
        "public_principals",
        sa.Column("principal_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("principal_type", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("subject_digest", sa.String(80), nullable=False),
        sa.Column("audiences", sa.JSON(), nullable=False),
        sa.Column("project_ids", sa.JSON(), nullable=False),
        sa.Column("environment_ids", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("claims_digest", sa.String(80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workspace_id", "principal_id", name="uq_public_principal_workspace"
        ),
        sa.CheckConstraint(
            "principal_type IN ('human','external_agent','service','connector')",
            name="ck_public_principal_type",
        ),
        sa.CheckConstraint("state IN ('ACTIVE','REVOKED')", name="ck_public_principal_state"),
    )
    op.create_index(
        "ix_public_principal_workspace_state",
        "public_principals",
        ["workspace_id", "state", "principal_type"],
    )

    op.create_table(
        "public_credentials",
        sa.Column("credential_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("subject", sa.String(256), nullable=False),
        sa.Column("credential_hash", sa.String(80), nullable=False),
        sa.Column("hash_algorithm", sa.String(32), nullable=False),
        sa.Column("jti_digest", sa.String(80), nullable=False),
        sa.Column("claims_digest", sa.String(80), nullable=False),
        sa.Column("audiences", sa.JSON(), nullable=False),
        sa.Column("project_ids", sa.JSON(), nullable=False),
        sa.Column("environment_ids", sa.JSON(), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "principal_id"],
            ["public_principals.workspace_id", "public_principals.principal_id"],
            name="fk_public_credential_principal",
        ),
        sa.UniqueConstraint("credential_hash", name="uq_public_credentials_hash"),
        sa.UniqueConstraint("issuer", "jti_digest", name="uq_public_credentials_issuer_jti"),
        sa.CheckConstraint(
            "hash_algorithm = 'hmac-sha256-v1'",
            name="ck_public_credentials_hash_algorithm",
        ),
        sa.CheckConstraint(
            "credential_hash LIKE 'sha256:%' AND length(credential_hash) = 71",
            name="ck_public_credentials_hash_format",
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE','REVOKED','EXPIRED')", name="ck_public_credentials_state"
        ),
    )
    op.create_index(
        "ix_public_credentials_workspace_principal",
        "public_credentials",
        ["workspace_id", "principal_id", "revoked_at", "expires_at"],
    )

    op.create_table(
        "public_command_idempotency",
        sa.Column("idempotency_record_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("principal_id", sa.String(128), nullable=False),
        sa.Column("intent", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_fingerprint", sa.String(80), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("resource_kind", sa.String(64), nullable=True),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("operation_id", sa.String(128), nullable=True),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("audit_ref", sa.String(256), nullable=True),
        sa.Column("response_payload", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("response_digest", sa.String(80), nullable=True),
        sa.Column("idempotency_receipt_id", sa.String(128), nullable=True),
        sa.Column("receipt_payload", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("receipt_digest", sa.String(80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "principal_id"],
            ["public_principals.workspace_id", "public_principals.principal_id"],
            name="fk_public_idempotency_principal",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "principal_id",
            "intent",
            "idempotency_key",
            name="uq_public_idempotency_scope",
        ),
        sa.UniqueConstraint(
            "idempotency_receipt_id", name="uq_public_idempotency_receipt"
        ),
        sa.CheckConstraint(
            "state IN ('PENDING','ACCEPTED','COMPLETED')", name="ck_public_idempotency_state"
        ),
        sa.CheckConstraint(
            "state = 'PENDING' OR (resource_kind IS NOT NULL AND resource_id IS NOT NULL "
            "AND audit_ref IS NOT NULL AND response_payload IS NOT NULL "
            "AND response_digest IS NOT NULL AND idempotency_receipt_id IS NOT NULL "
            "AND receipt_payload IS NOT NULL AND receipt_digest IS NOT NULL)",
            name="ck_public_idempotency_result",
        ),
    )
    op.create_index(
        "ix_public_idempotency_resource",
        "public_command_idempotency",
        ["workspace_id", "resource_kind", "resource_id"],
    )


def _create_signal_tables() -> None:
    op.create_table(
        "signal_contents",
        sa.Column("signal_content_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("content_digest", sa.String(80), nullable=False),
        sa.Column("content_payload", sa.JSON(), nullable=False),
        sa.Column("privacy_classification", sa.String(32), nullable=False),
        sa.Column("redaction_status", sa.String(32), nullable=False),
        sa.Column("raw_content_persisted", sa.Boolean(), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "workspace_id", "signal_content_id", name="uq_signal_content_workspace"
        ),
        sa.UniqueConstraint("workspace_id", "uri", name="uq_signal_content_uri"),
    )
    op.create_index(
        "ix_signal_content_workspace_digest",
        "signal_contents",
        ["workspace_id", "content_digest"],
    )

    op.create_table(
        "signals",
        sa.Column("signal_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=True),
        sa.Column("environment_id", sa.String(128), nullable=True),
        sa.Column("governed_agent_id", sa.String(128), nullable=True),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_event_id", sa.String(512), nullable=False),
        sa.Column("source_event_version", sa.String(64), nullable=False),
        sa.Column("source_payload_digest", sa.String(80), nullable=False),
        sa.Column("adapter_kind", sa.String(32), nullable=False),
        sa.Column("provider_origin", sa.Text(), nullable=False),
        sa.Column("signal_kind", sa.String(64), nullable=False),
        sa.Column("reporter_kind", sa.String(32), nullable=False),
        sa.Column("reporter_ref", sa.String(256), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_content_id", sa.String(128), nullable=False),
        sa.Column("content_ref", sa.JSON(), nullable=False),
        sa.Column("agent_run_ref_id", sa.String(128), nullable=True),
        sa.Column("privacy", sa.JSON(), nullable=False),
        sa.Column("completeness", sa.String(16), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("untrusted_content", sa.Boolean(), nullable=False),
        sa.Column("envelope_payload", sa.JSON(), nullable=False),
        sa.Column("signal_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source_connections.workspace_id", "source_connections.source_id"],
            name="fk_signal_source_connection",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "signal_content_id"],
            ["signal_contents.workspace_id", "signal_contents.signal_content_id"],
            name="fk_signal_content",
        ),
        sa.UniqueConstraint("workspace_id", "signal_id", name="uq_signal_workspace"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_id",
            "source_event_id",
            name="uq_signal_source_event",
        ),
    )
    op.create_index(
        "ix_signals_workspace_kind_observed",
        "signals",
        ["workspace_id", "signal_kind", "observed_at"],
    )
    op.create_index(
        "ix_signals_workspace_project", "signals", ["workspace_id", "project_id"]
    )
    op.create_index(
        "ix_signals_workspace_agent", "signals", ["workspace_id", "governed_agent_id"]
    )

    op.create_table(
        "quality_cases",
        sa.Column("case_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=True),
        sa.Column("environment_id", sa.String(128), nullable=True),
        sa.Column("governed_agent_id", sa.String(128), nullable=True),
        sa.Column("correlation_status", sa.String(32), nullable=False),
        sa.Column("triage_status", sa.String(32), nullable=False),
        sa.Column("opening_signal_id", sa.String(128), nullable=False),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["workspace_id", "opening_signal_id"],
            ["signals.workspace_id", "signals.signal_id"],
            name="fk_quality_case_opening_signal",
        ),
        sa.UniqueConstraint("workspace_id", "case_id", name="uq_quality_case_workspace"),
        sa.CheckConstraint("state IN ('OPEN','RESOLVED')", name="ck_quality_case_state"),
        sa.CheckConstraint("revision >= 1", name="ck_quality_case_revision"),
    )
    op.create_index(
        "ix_quality_case_workspace_state",
        "quality_cases",
        ["workspace_id", "state", "updated_at"],
    )
    op.create_index(
        "ix_quality_case_workspace_project",
        "quality_cases",
        ["workspace_id", "project_id"],
    )
    op.create_index(
        "ix_quality_case_workspace_agent",
        "quality_cases",
        ["workspace_id", "governed_agent_id"],
    )

    op.create_table(
        "signal_case_links",
        sa.Column("signal_case_link_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("link_payload", sa.JSON(), nullable=False),
        sa.Column("link_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "signal_id"],
            ["signals.workspace_id", "signals.signal_id"],
            name="fk_signal_case_link_signal",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["quality_cases.workspace_id", "quality_cases.case_id"],
            name="fk_signal_case_link_case",
        ),
        sa.UniqueConstraint(
            "workspace_id", "signal_id", "case_id", name="uq_signal_case_link_identity"
        ),
    )
    op.create_index(
        "ix_signal_case_link_case", "signal_case_links", ["workspace_id", "case_id"]
    )
    op.create_index(
        "ix_signal_case_link_signal", "signal_case_links", ["workspace_id", "signal_id"]
    )


def _create_evidence_and_authority_tables() -> None:
    op.create_table(
        "agent_run_refs",
        sa.Column("agent_run_ref_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("governed_agent_id", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deep_link", sa.Text(), nullable=True),
        sa.Column("completeness", sa.String(16), nullable=False),
        sa.Column("missing_fields", sa.JSON(), nullable=False),
        sa.Column("locator_digest", sa.String(80), nullable=False),
        sa.Column("record_payload", sa.JSON(), nullable=False),
        sa.Column("agent_run_ref_digest", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source_connections.workspace_id", "source_connections.source_id"],
            name="fk_agent_run_ref_source",
        ),
        sa.UniqueConstraint(
            "workspace_id", "agent_run_ref_id", name="uq_agent_run_ref_workspace"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_id",
            "locator_digest",
            name="uq_agent_run_ref_locator",
        ),
    )
    op.create_index(
        "ix_agent_run_ref_workspace_agent",
        "agent_run_refs",
        ["workspace_id", "governed_agent_id", "observed_at"],
    )

    op.create_table(
        "trace_evidence_receipts",
        sa.Column("receipt_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("signal_digest", sa.String(80), nullable=False),
        sa.Column("collection_mode", sa.String(32), nullable=False),
        sa.Column("agent_run_ref_id", sa.String(128), nullable=True),
        sa.Column("agent_run_ref_digest", sa.String(80), nullable=True),
        sa.Column("query", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("requested_fields", sa.JSON(), nullable=False),
        sa.Column("field_results", sa.JSON(), nullable=False),
        sa.Column("completeness", sa.String(16), nullable=False),
        sa.Column("artifact_ref", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("source_payload_digest", sa.String(80), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deep_link", sa.Text(), nullable=True),
        sa.Column("failure", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column("receipt_payload", sa.JSON(), nullable=False),
        sa.Column("receipt_digest", sa.String(80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "source_id"],
            ["source_connections.workspace_id", "source_connections.source_id"],
            name="fk_trace_receipt_source",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "signal_id"],
            ["signals.workspace_id", "signals.signal_id"],
            name="fk_trace_receipt_signal",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_run_ref_id"],
            ["agent_run_refs.workspace_id", "agent_run_refs.agent_run_ref_id"],
            name="fk_trace_receipt_run_ref",
        ),
        sa.UniqueConstraint("workspace_id", "receipt_id", name="uq_trace_receipt_workspace"),
        sa.UniqueConstraint("receipt_digest", name="uq_trace_receipt_digest"),
        sa.CheckConstraint(
            "collection_mode <> 'NO_LOCATOR' OR (agent_run_ref_id IS NULL AND "
            "agent_run_ref_digest IS NULL AND query IS NULL AND completeness = 'UNKNOWN' "
            "AND artifact_ref IS NULL AND source_payload_digest IS NULL AND deep_link IS NULL)",
            name="ck_trace_receipt_no_locator",
        ),
    )
    op.create_index(
        "ix_trace_receipt_signal",
        "trace_evidence_receipts",
        ["workspace_id", "signal_id", "collected_at"],
    )
    op.create_index(
        "ix_trace_receipt_run_ref",
        "trace_evidence_receipts",
        ["workspace_id", "agent_run_ref_id"],
    )

    op.create_table(
        "controller_registrations",
        sa.Column("controller_registration_id", sa.String(128), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("previous_snapshot", sa.JSON(), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("controller_principal", sa.String(128), nullable=False),
        sa.Column("allowed_commands", sa.JSON(), nullable=False),
        sa.Column("ownership_contract_digest", sa.String(80), nullable=False),
        sa.Column("event_catalog_digest", sa.String(80), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("service_identity_digest", sa.String(80), nullable=False),
        sa.Column("registered_by_human_principal", sa.String(128), nullable=False),
        sa.Column("registration_audit_ref", sa.String(256), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("registration_payload", sa.JSON(), nullable=False),
        sa.Column("registration_digest", sa.String(80), nullable=False, unique=True),
        sa.UniqueConstraint(
            "workspace_id",
            "controller_registration_id",
            "revision",
            name="uq_controller_registration_workspace_revision",
        ),
        sa.CheckConstraint(
            "state IN ('ACTIVE','REVOKED','EXPIRED')", name="ck_controller_registration_state"
        ),
    )
    op.create_index(
        "ix_controller_registration_workspace_owner",
        "controller_registrations",
        ["workspace_id", "owner", "state"],
    )

    op.create_table(
        "authority_receipts",
        sa.Column("authority_receipt_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("controller_registration_id", sa.String(128), nullable=False),
        sa.Column("controller_registration_revision", sa.Integer(), nullable=False),
        sa.Column("controller_registration_digest", sa.String(80), nullable=False),
        sa.Column("subject_kind", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("subject_revision", sa.Integer(), nullable=True),
        sa.Column("subject_identity_key", sa.String(512), nullable=False),
        sa.Column("subject_digest", sa.String(80), nullable=False),
        sa.Column("resource", sa.String(64), nullable=False),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("controller_principal", sa.String(128), nullable=False),
        sa.Column("command", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("transaction_id", sa.String(128), nullable=False),
        sa.Column("audit_ref", sa.String(256), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("receipt_payload", sa.JSON(), nullable=False),
        sa.Column("authority_receipt_digest", sa.String(80), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "controller_registration_id", "controller_registration_revision"],
            [
                "controller_registrations.workspace_id",
                "controller_registrations.controller_registration_id",
                "controller_registrations.revision",
            ],
            name="fk_authority_receipt_controller",
        ),
        sa.UniqueConstraint(
            "workspace_id", "subject_identity_key", name="uq_authority_receipt_subject_identity"
        ),
        sa.UniqueConstraint("event_id", name="uq_authority_receipt_event"),
        sa.UniqueConstraint(
            "authority_receipt_digest", name="uq_authority_receipt_digest"
        ),
        sa.CheckConstraint(
            "subject_revision IS NULL OR subject_revision >= 1",
            name="ck_authority_receipt_subject_revision",
        ),
        sa.CheckConstraint(
            "subject_identity_key = subject_kind || ':' || subject_id || ':' || "
            "CASE WHEN subject_revision IS NULL THEN 'singleton' "
            "ELSE CAST(subject_revision AS VARCHAR) END",
            name="ck_authority_receipt_subject_identity_key",
        ),
    )
    op.create_index(
        "ix_authority_receipt_transaction",
        "authority_receipts",
        ["workspace_id", "transaction_id"],
    )
    op.create_index(
        "ix_authority_receipt_subject",
        "authority_receipts",
        ["workspace_id", "subject_kind", "subject_id"],
    )
    op.create_index(
        "ix_authority_receipt_controller",
        "authority_receipts",
        ["workspace_id", "controller_registration_id", "controller_registration_revision"],
    )


def upgrade() -> None:
    _add_shared_v4_columns()
    _create_source_and_auth_tables()
    _create_signal_tables()
    _create_evidence_and_authority_tables()


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table in _NEW_TABLES:
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    for table in ("aggregates", "events", "audit", "outbox"):
        if (
            bind.execute(
                sa.text(f"SELECT 1 FROM {table} WHERE contract_version = 'v4' LIMIT 1")
            ).first()
            is not None
        ):
            raise RuntimeError(_DOWNGRADE_BLOCKED)


def _drop_new_tables() -> None:
    for table in reversed(_NEW_TABLES):
        op.drop_table(table)


def _drop_shared_v4_columns() -> None:
    op.drop_index("ix_outbox_v4_dispatch", table_name="outbox")
    op.drop_index("ix_audit_v4_transaction", table_name="audit")
    op.drop_index("ix_events_v4_timeline", table_name="events")
    op.drop_index("ix_events_v4_route", table_name="events")
    op.drop_index("uq_events_v4_workspace_agg_seq", table_name="events")
    op.drop_index("uq_events_legacy_agg_seq", table_name="events")

    columns = {
        "outbox": (
            "actor_principal",
            "transaction_id",
            "event_version",
            "aggregate_type",
            "workspace_id",
            "contract_version",
        ),
        "audit": (
            "audit_digest",
            "actor_principal",
            "transaction_id",
            "workspace_id",
            "contract_version",
        ),
        "events": (
            "payload_digest",
            "actor_principal",
            "transaction_id",
            "event_version",
            "workspace_id",
            "contract_version",
        ),
        "aggregates": (
            "record_digest",
            "actor_principal",
            "transaction_id",
            "workspace_id",
            "contract_version",
        ),
    }
    constraints = {
        "outbox": "ck_outbox_v4_context",
        "audit": "ck_audit_v4_context",
        "events": "ck_events_v4_context",
        "aggregates": "ck_aggregates_v4_context",
    }
    if op.get_bind().dialect.name == "sqlite":
        for table, names in columns.items():
            with op.batch_alter_table(table) as batch:
                batch.drop_constraint(constraints[table], type_="check")
                for name in names:
                    batch.drop_column(name)
        with op.batch_alter_table("events") as batch:
            batch.create_unique_constraint("uq_events_agg_seq", ["aggregate_id", "seq"])
    else:
        for table, names in columns.items():
            op.drop_constraint(constraints[table], table, type_="check")
            for name in names:
                op.drop_column(table, name)
        op.create_unique_constraint(
            "uq_events_agg_seq", "events", ["aggregate_id", "seq"]
        )


def downgrade() -> None:
    _assert_downgrade_safe()
    _drop_new_tables()
    _drop_shared_v4_columns()
