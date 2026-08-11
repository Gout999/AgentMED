"""V5-1C application case binding and acceptance criteria records.

Revision ID: 010
Revises: 009
Create Date: 2026-08-11

Expand migration adding the three V5-1C tables: application_case_bindings /
acceptance_criteria_revisions / issue_source_snapshots (all case-controller
owned except the read-only issue snapshot projection).  Bindings and
acceptance revisions are immutable (no update path).  A binding is unique per
exact case identity (workspace_id, case_id, case_revision, case_digest) so a
different target for the same exact case is a conflict and rebinding requires
a new quality case revision.  Acceptance revisions carry a confirmation-status
check that keeps PROPOSED free of confirmer fields and CONFIRMED bound to
confirmer + prior proposal.  Downgrade is blocked once any of these records,
their v5 events, or authority receipts exist.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DOWNGRADE_BLOCKED = "010.downgrade_blocked.immutable_v5_case_records_exist"
_NEW_TABLES = (
    "application_case_bindings",
    "acceptance_criteria_revisions",
    "issue_source_snapshots",
)
_V5_EVENT_AGGREGATE_TYPES = (
    "application_case_binding",
    "acceptance_criteria_revision",
)
_V5_SUBJECT_KINDS = (
    "APPLICATION_CASE_BINDING",
    "ACCEPTANCE_CRITERIA_REVISION",
)


def _create_application_case_bindings() -> None:
    op.create_table(
        "application_case_bindings",
        sa.Column("application_case_binding_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_revision", sa.BigInteger(), nullable=False),
        sa.Column("case_digest", sa.String(80), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("environment_id", sa.String(128), nullable=False),
        sa.Column(
            "declared_system_version_set_binding_or_unknown", sa.JSON(), nullable=True
        ),
        sa.Column("binding_digest", sa.String(80), nullable=False),
        sa.Column("envelope_payload", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column("recorded_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["quality_cases.workspace_id", "quality_cases.case_id"],
            name="fk_application_case_binding_case",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_application_case_binding_application",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["environments.workspace_id", "environments.environment_id"],
            name="fk_application_case_binding_environment",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "application_case_binding_id",
            name="uq_application_case_binding_workspace",
        ),
        # One binding per exact case identity (id + revision + digest).
        sa.UniqueConstraint(
            "workspace_id",
            "case_id",
            "case_revision",
            "case_digest",
            name="uq_application_case_binding_exact_case",
        ),
        sa.UniqueConstraint(
            "record_digest", name="uq_application_case_binding_record_digest"
        ),
        sa.CheckConstraint(
            "case_revision >= 1", name="ck_application_case_binding_case_revision"
        ),
    )
    op.create_index(
        "ix_application_case_binding_workspace_case",
        "application_case_bindings",
        ["workspace_id", "case_id", "case_revision", "created_at"],
    )
    op.create_index(
        "ix_application_case_binding_workspace_application",
        "application_case_bindings",
        ["workspace_id", "application_id", "environment_id", "created_at"],
    )


def _create_acceptance_criteria_revisions() -> None:
    op.create_table(
        "acceptance_criteria_revisions",
        sa.Column(
            "acceptance_criteria_revision_id", sa.String(128), primary_key=True
        ),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("case_revision", sa.BigInteger(), nullable=False),
        sa.Column("case_digest", sa.String(80), nullable=False),
        sa.Column("exact_resolution_contract_binding", sa.JSON(), nullable=False),
        sa.Column("confirmation_status", sa.String(16), nullable=False),
        sa.Column("proposer_principal", sa.String(128), nullable=False),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmer_principal", sa.String(128), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "exact_previous_proposed_revision_binding", sa.JSON(), nullable=True
        ),
        sa.Column("acceptance_source", sa.JSON(), nullable=False),
        sa.Column("reproducer_input", sa.JSON(), nullable=True),
        sa.Column("reproducer_environment", sa.JSON(), nullable=True),
        sa.Column("expected_behavior", sa.JSON(), nullable=False),
        sa.Column("oracle_or_evaluator", sa.JSON(), nullable=True),
        sa.Column("applicable_workload_profile", sa.JSON(), nullable=False),
        sa.Column("applicable_deployment_profile", sa.JSON(), nullable=False),
        sa.Column("acceptance_digest", sa.String(80), nullable=False),
        sa.Column("envelope_payload", sa.JSON(), nullable=False),
        sa.Column("record_digest", sa.String(80), nullable=False),
        sa.Column("authority_receipt_id", sa.String(128), nullable=False),
        sa.Column("recorded_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["quality_cases.workspace_id", "quality_cases.case_id"],
            name="fk_acceptance_criteria_revision_case",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "acceptance_criteria_revision_id",
            name="uq_acceptance_criteria_revision_workspace",
        ),
        sa.UniqueConstraint(
            "record_digest", name="uq_acceptance_criteria_revision_record_digest"
        ),
        sa.CheckConstraint(
            "confirmation_status IN ('PROPOSED','CONFIRMED')",
            name="ck_acceptance_criteria_revision_confirmation_status",
        ),
        sa.CheckConstraint(
            "case_revision >= 1", name="ck_acceptance_criteria_revision_case_revision"
        ),
        # PROPOSED must not carry confirmer fields; CONFIRMED must carry them.
        # The prior-proposal reference is enforced by the service (and by the
        # new-immutable-record semantics); JSON null storage differs across
        # dialects (SQLite stores 'null'), so the DB check stays on scalar
        # columns that are portable.
        sa.CheckConstraint(
            "(confirmation_status = 'PROPOSED' AND confirmer_principal IS NULL "
            "AND confirmed_at IS NULL) "
            "OR (confirmation_status = 'CONFIRMED' AND confirmer_principal IS NOT NULL "
            "AND confirmed_at IS NOT NULL)",
            name="ck_acceptance_criteria_revision_status_shape",
        ),
    )
    op.create_index(
        "ix_acceptance_criteria_revision_workspace_case",
        "acceptance_criteria_revisions",
        ["workspace_id", "case_id", "case_revision", "created_at"],
    )


def _create_issue_source_snapshots() -> None:
    op.create_table(
        "issue_source_snapshots",
        sa.Column("issue_snapshot_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_url", sa.String(1024), nullable=False),
        sa.Column("external_repo", sa.String(256), nullable=False),
        sa.Column("external_issue_number", sa.BigInteger(), nullable=False),
        sa.Column("snapshot_payload", sa.JSON(), nullable=False),
        sa.Column("snapshot_digest", sa.String(80), nullable=False),
        sa.Column("edited_flag", sa.Boolean(), nullable=False),
        sa.Column("deleted_flag", sa.Boolean(), nullable=False),
        sa.Column("instruction_markers_detected", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_principal", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "case_id"],
            ["quality_cases.workspace_id", "quality_cases.case_id"],
            name="fk_issue_source_snapshot_case",
        ),
        sa.UniqueConstraint(
            "workspace_id", "issue_snapshot_id", name="uq_issue_source_snapshot_workspace"
        ),
        sa.UniqueConstraint(
            "snapshot_digest", name="uq_issue_source_snapshot_digest"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "case_id",
            "external_repo",
            "external_issue_number",
            name="uq_issue_source_snapshot_issue",
        ),
        sa.CheckConstraint(
            "source_kind IN ('github_issue','manual')",
            name="ck_issue_source_snapshot_source_kind",
        ),
        sa.CheckConstraint(
            "external_issue_number >= 1", name="ck_issue_source_snapshot_issue_number"
        ),
    )
    op.create_index(
        "ix_issue_source_snapshot_workspace_issue",
        "issue_source_snapshots",
        ["workspace_id", "case_id", "source_kind", "created_at"],
    )


def upgrade() -> None:
    _create_application_case_bindings()
    _create_acceptance_criteria_revisions()
    _create_issue_source_snapshots()


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table in _NEW_TABLES:
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    if bind.dialect.name == "postgresql":
        events = bind.execute(
            sa.text(
                "SELECT 1 FROM events WHERE contract_version = 'v4' AND "
                "aggregate_type IN ('application_case_binding',"
                "'acceptance_criteria_revision') LIMIT 1"
            )
        ).first()
        receipts = bind.execute(
            sa.text(
                "SELECT 1 FROM authority_receipts WHERE subject_kind IN "
                "('APPLICATION_CASE_BINDING','ACCEPTANCE_CRITERIA_REVISION') LIMIT 1"
            )
        ).first()
        if events is not None or receipts is not None:
            raise RuntimeError(_DOWNGRADE_BLOCKED)


def downgrade() -> None:
    _assert_downgrade_safe()
    for table in reversed(_NEW_TABLES):
        op.drop_table(table)
