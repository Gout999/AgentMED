"""Harden V5-1C authorization, exact bindings and snapshot identity.

Revision ID: 011
Revises: 010
Create Date: 2026-08-11

The migration is deliberately fail closed for legacy confirmations: it can
recover their scalar proposal identity from the immutable JSON projection, but
marks the absent fresh-credential proof as ``LEGACY_UNVERIFIED``.  Runtime
validation will therefore never treat a pre-011 confirmation as READY merely
because its status column says CONFIRMED.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_legacy_bindings() -> None:
    bind = op.get_bind()

    application_case_bindings = sa.table(
        "application_case_bindings",
        sa.column("application_case_binding_id", sa.String()),
        sa.column("declared_system_version_set_binding_or_unknown", sa.JSON()),
    )
    # SQLAlchemy's JSON type may persist Python ``None`` as the JSON literal
    # ``null`` rather than SQL NULL.  Selecting and decoding every legacy row
    # is therefore the only portable way to cover both PostgreSQL and SQLite.
    legacy_bindings = bind.execute(
        sa.select(
            application_case_bindings.c.application_case_binding_id,
            application_case_bindings.c.declared_system_version_set_binding_or_unknown,
        )
    ).mappings()
    for row in legacy_bindings:
        if row["declared_system_version_set_binding_or_unknown"] is None:
            bind.execute(
                application_case_bindings.update()
                .where(
                    application_case_bindings.c.application_case_binding_id
                    == row["application_case_binding_id"]
                )
                .values(
                    declared_system_version_set_binding_or_unknown={
                        "kind": "UNKNOWN",
                        "reason": "MIGRATED_UNDECLARED",
                    }
                )
            )

    acceptance = sa.table(
        "acceptance_criteria_revisions",
        sa.column("acceptance_criteria_revision_id", sa.String()),
        sa.column("confirmation_status", sa.String()),
        sa.column("exact_previous_proposed_revision_binding", sa.JSON()),
        sa.column("exact_previous_proposed_revision_id", sa.String()),
        sa.column("exact_previous_proposed_revision_digest", sa.String()),
        sa.column("reauthentication_credential_binding", sa.JSON()),
    )
    confirmed = bind.execute(
        sa.select(
            acceptance.c.acceptance_criteria_revision_id,
            acceptance.c.exact_previous_proposed_revision_binding,
        ).where(acceptance.c.confirmation_status == "CONFIRMED")
    ).mappings()
    for row in confirmed:
        previous = row["exact_previous_proposed_revision_binding"]
        if not isinstance(previous, dict):
            raise RuntimeError("011.legacy_confirmation_previous_binding_missing")
        previous_id = previous.get("id")
        previous_digest = previous.get("digest")
        if not isinstance(previous_id, str) or not isinstance(previous_digest, str):
            raise RuntimeError("011.legacy_confirmation_previous_binding_invalid")
        bind.execute(
            acceptance.update()
            .where(
                acceptance.c.acceptance_criteria_revision_id
                == row["acceptance_criteria_revision_id"]
            )
            .values(
                exact_previous_proposed_revision_id=previous_id,
                exact_previous_proposed_revision_digest=previous_digest,
                reauthentication_credential_binding={
                    "kind": "LEGACY_UNVERIFIED",
                    "reason": "FRESH_CREDENTIAL_NOT_RECORDED_PRE_011",
                },
            )
        )

def _backfill_legacy_issue_snapshots() -> None:
    issue_snapshots = sa.table(
        "issue_source_snapshots",
        sa.column("source_kind", sa.String()),
        sa.column("external_repo", sa.String()),
        sa.column("external_issue_number", sa.BigInteger()),
    )
    op.get_bind().execute(
        issue_snapshots.update()
        .where(issue_snapshots.c.source_kind == "manual")
        .values(external_repo=None, external_issue_number=None)
    )


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    acceptance_exists = bind.execute(
        sa.text("SELECT 1 FROM acceptance_criteria_revisions LIMIT 1")
    ).first()
    snapshots_exist = bind.execute(
        sa.text("SELECT 1 FROM issue_source_snapshots LIMIT 1")
    ).first()
    if acceptance_exists is not None or snapshots_exist is not None:
        raise RuntimeError("011.downgrade_blocked.hardened_v5_1c_records_exist")

    principals = sa.table(
        "public_principals",
        sa.column("principal_id", sa.String()),
        sa.column("trust_roles", sa.JSON()),
    )
    for row in bind.execute(
        sa.select(principals.c.principal_id, principals.c.trust_roles)
    ).mappings():
        if row["trust_roles"]:
            raise RuntimeError("011.downgrade_blocked.trust_roles_exist")


def upgrade() -> None:
    op.add_column(
        "public_principals",
        sa.Column(
            "trust_roles",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )

    op.add_column(
        "application_case_bindings",
        sa.Column(
            "revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "acceptance_criteria_revisions",
        sa.Column(
            "revision",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        "acceptance_criteria_revisions",
        sa.Column("exact_previous_proposed_revision_id", sa.String(128), nullable=True),
    )
    op.add_column(
        "acceptance_criteria_revisions",
        sa.Column(
            "exact_previous_proposed_revision_digest", sa.String(80), nullable=True
        ),
    )
    op.add_column(
        "acceptance_criteria_revisions",
        sa.Column("reauthentication_credential_binding", sa.JSON(), nullable=True),
    )

    _backfill_legacy_bindings()

    with op.batch_alter_table("application_case_bindings") as batch:
        batch.alter_column(
            "declared_system_version_set_binding_or_unknown",
            existing_type=sa.JSON(),
            nullable=False,
        )
        batch.create_check_constraint(
            "ck_application_case_binding_revision", "revision >= 1"
        )

    with op.batch_alter_table("acceptance_criteria_revisions") as batch:
        batch.drop_constraint(
            "ck_acceptance_criteria_revision_status_shape", type_="check"
        )
        batch.alter_column(
            "exact_resolution_contract_binding",
            new_column_name="resolution_contract_binding_status",
            existing_type=sa.JSON(),
            existing_nullable=False,
        )
        batch.create_check_constraint(
            "ck_acceptance_criteria_revision_revision", "revision >= 1"
        )
        batch.create_check_constraint(
            "ck_acceptance_criteria_revision_status_shape",
            "(confirmation_status = 'PROPOSED' AND confirmer_principal IS NULL "
            "AND confirmed_at IS NULL "
            "AND exact_previous_proposed_revision_id IS NULL "
            "AND exact_previous_proposed_revision_digest IS NULL "
            "AND reauthentication_credential_binding IS NULL) "
            "OR (confirmation_status = 'CONFIRMED' "
            "AND confirmer_principal IS NOT NULL AND confirmed_at IS NOT NULL "
            "AND exact_previous_proposed_revision_id IS NOT NULL "
            "AND exact_previous_proposed_revision_digest IS NOT NULL "
            "AND reauthentication_credential_binding IS NOT NULL)",
        )

    op.create_index(
        "uq_acceptance_confirmed_previous_proposal",
        "acceptance_criteria_revisions",
        ["workspace_id", "exact_previous_proposed_revision_id"],
        unique=True,
        sqlite_where=sa.text("confirmation_status = 'CONFIRMED'"),
        postgresql_where=sa.text("confirmation_status = 'CONFIRMED'"),
    )

    # Make the legacy manual-source identity columns nullable before clearing
    # their fake repository/issue values.  Updating them earlier would violate
    # the 010 NOT NULL constraints and make a populated upgrade impossible.
    with op.batch_alter_table("issue_source_snapshots") as batch:
        batch.drop_constraint("uq_issue_source_snapshot_digest", type_="unique")
        batch.drop_constraint("uq_issue_source_snapshot_issue", type_="unique")
        batch.drop_constraint("ck_issue_source_snapshot_issue_number", type_="check")
        batch.alter_column(
            "source_url", existing_type=sa.String(1024), nullable=True
        )
        batch.alter_column(
            "external_repo", existing_type=sa.String(256), nullable=True
        )
        batch.alter_column(
            "external_issue_number", existing_type=sa.BigInteger(), nullable=True
        )

    _backfill_legacy_issue_snapshots()

    with op.batch_alter_table("issue_source_snapshots") as batch:
        batch.create_unique_constraint(
            "uq_issue_source_snapshot_case_digest",
            ["workspace_id", "case_id", "snapshot_digest"],
        )
        batch.create_check_constraint(
            "ck_issue_source_snapshot_identity_shape",
            "(source_kind = 'github_issue' "
            "AND (source_url LIKE 'http://%' OR source_url LIKE 'https://%') "
            "AND external_repo IS NOT NULL AND length(external_repo) >= 1 "
            "AND external_issue_number >= 1) "
            "OR (source_kind = 'manual' "
            "AND external_repo IS NULL AND external_issue_number IS NULL)",
        )


def downgrade() -> None:
    _assert_downgrade_safe()
    op.drop_index(
        "uq_acceptance_confirmed_previous_proposal",
        table_name="acceptance_criteria_revisions",
    )
    with op.batch_alter_table("issue_source_snapshots") as batch:
        batch.drop_constraint(
            "ck_issue_source_snapshot_identity_shape", type_="check"
        )
        batch.drop_constraint(
            "uq_issue_source_snapshot_case_digest", type_="unique"
        )
        batch.alter_column(
            "source_url", existing_type=sa.String(1024), nullable=False
        )
        batch.alter_column(
            "external_repo", existing_type=sa.String(256), nullable=False
        )
        batch.alter_column(
            "external_issue_number", existing_type=sa.BigInteger(), nullable=False
        )
        batch.create_unique_constraint(
            "uq_issue_source_snapshot_digest", ["snapshot_digest"]
        )
        batch.create_unique_constraint(
            "uq_issue_source_snapshot_issue",
            ["workspace_id", "case_id", "external_repo", "external_issue_number"],
        )
        batch.create_check_constraint(
            "ck_issue_source_snapshot_issue_number", "external_issue_number >= 1"
        )

    with op.batch_alter_table("acceptance_criteria_revisions") as batch:
        batch.drop_constraint(
            "ck_acceptance_criteria_revision_status_shape", type_="check"
        )
        batch.drop_constraint(
            "ck_acceptance_criteria_revision_revision", type_="check"
        )
        batch.alter_column(
            "resolution_contract_binding_status",
            new_column_name="exact_resolution_contract_binding",
            existing_type=sa.JSON(),
            existing_nullable=False,
        )
        batch.drop_column("reauthentication_credential_binding")
        batch.drop_column("exact_previous_proposed_revision_digest")
        batch.drop_column("exact_previous_proposed_revision_id")
        batch.drop_column("revision")
        batch.create_check_constraint(
            "ck_acceptance_criteria_revision_status_shape",
            "(confirmation_status = 'PROPOSED' AND confirmer_principal IS NULL "
            "AND confirmed_at IS NULL) "
            "OR (confirmation_status = 'CONFIRMED' "
            "AND confirmer_principal IS NOT NULL AND confirmed_at IS NOT NULL)",
        )

    with op.batch_alter_table("application_case_bindings") as batch:
        batch.drop_constraint("ck_application_case_binding_revision", type_="check")
        batch.alter_column(
            "declared_system_version_set_binding_or_unknown",
            existing_type=sa.JSON(),
            nullable=True,
        )
        batch.drop_column("revision")

    op.drop_column("public_principals", "trust_roles")
