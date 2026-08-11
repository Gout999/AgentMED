"""V5-1B immutable system versions and trusted manifest import.

Revision ID: 009
Revises: 008
Create Date: 2026-08-11

Expand migration adding the five V5-1B tables: component_revisions /
topology_revisions / system_version_sets / bootstrap_attestations /
system_assignments (all version-controller owned).  Version sets are
immutable (no update path); assignments carry the CAS constraints (one
non-retired aggregate per workspace/application/environment identity key).
The trusted one-shot manifest import constructs these plus the V5-1A catalog
objects in one local PostgreSQL transaction (ALL_OR_NOTHING).  Downgrade is
blocked once any of these records, their v5 events, or authority receipts
exist.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_DOWNGRADE_BLOCKED = "009.downgrade_blocked.immutable_v5_version_records_exist"
_NEW_TABLES = (
    "component_revisions",
    "topology_revisions",
    "system_version_sets",
    "bootstrap_attestations",
    "system_assignments",
)
_V5_EVENT_AGGREGATE_TYPES = (
    "component_revision",
    "topology_revision",
    "system_version_set",
    "bootstrap_attestation",
    "system_assignment",
)
_V5_SUBJECT_KINDS = (
    "COMPONENT_REVISION",
    "TOPOLOGY_REVISION",
    "SYSTEM_VERSION_SET",
    "BOOTSTRAP_ATTESTATION",
    "SYSTEM_ASSIGNMENT",
)


def _create_component_revisions() -> None:
    op.create_table(
        "component_revisions",
        sa.Column("component_revision_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("component_id", sa.String(128), nullable=False),
        sa.Column("component_kind", sa.String(64), nullable=False),
        sa.Column("identity_locator", sa.JSON(), nullable=False),
        sa.Column("identity_assurance", sa.String(32), nullable=False),
        sa.Column("configuration_digest", sa.String(80), nullable=False),
        sa.Column("exact_provenance_receipt_bindings", sa.JSON(), nullable=False),
        sa.Column("declared_version", sa.String(256), nullable=True),
        sa.Column("content_digest", sa.String(80), nullable=True),
        sa.Column("provider_origin", sa.String(512), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("immutable_provider_version_attestation", sa.JSON(), nullable=True),
        sa.Column("exact_observation_receipt_binding", sa.JSON(), nullable=True),
        sa.Column("unknown_reason", sa.String(512), nullable=True),
        sa.Column("interface_schema_digest", sa.String(80), nullable=True),
        sa.Column("permission_manifest_digest", sa.String(80), nullable=True),
        sa.Column("dependency_lock_digest", sa.String(80), nullable=True),
        sa.Column("dataset_role", sa.String(32), nullable=True),
        sa.Column("artifact_refs", sa.JSON(), nullable=True),
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
            ["workspace_id", "application_id", "component_id"],
            [
                "system_components.workspace_id",
                "system_components.application_id",
                "system_components.component_id",
            ],
            name="fk_component_revision_component",
        ),
        sa.UniqueConstraint(
            "workspace_id", "component_revision_id", name="uq_component_revision_workspace"
        ),
        sa.UniqueConstraint(
            "record_digest", name="uq_component_revision_record_digest"
        ),
        sa.CheckConstraint(
            "identity_assurance IN ('IMMUTABLE_DIGEST','PROVIDER_VERSION',"
            "'MUTABLE_ALIAS','OBSERVED_ONLY','UNKNOWN')",
            name="ck_component_revision_identity_assurance",
        ),
    )
    op.create_index(
        "ix_component_revision_workspace_application",
        "component_revisions",
        ["workspace_id", "application_id", "component_id", "created_at"],
    )


def _create_topology_revisions() -> None:
    op.create_table(
        "topology_revisions",
        sa.Column("topology_revision_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("component_ids", sa.JSON(), nullable=False),
        sa.Column("exact_edge_revision_bindings", sa.JSON(), nullable=False),
        sa.Column("topology_digest", sa.String(80), nullable=False),
        sa.Column("provenance_receipt_ids", sa.JSON(), nullable=False),
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
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_topology_revision_application",
        ),
        sa.UniqueConstraint(
            "workspace_id", "topology_revision_id", name="uq_topology_revision_workspace"
        ),
        sa.UniqueConstraint(
            "record_digest", name="uq_topology_revision_record_digest"
        ),
        sa.UniqueConstraint(
            "workspace_id", "topology_digest", name="uq_topology_revision_workspace_digest"
        ),
    )
    op.create_index(
        "ix_topology_revision_workspace_application",
        "topology_revisions",
        ["workspace_id", "application_id", "created_at"],
    )


def _create_system_version_sets() -> None:
    op.create_table(
        "system_version_sets",
        sa.Column("system_version_set_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("declared_environment_id", sa.String(128), nullable=False),
        sa.Column("exact_component_revision_bindings", sa.JSON(), nullable=False),
        sa.Column("exact_topology_revision_binding", sa.JSON(), nullable=False),
        sa.Column("identity_assurance_summary", sa.JSON(), nullable=False),
        sa.Column("provenance_receipt_ids", sa.JSON(), nullable=False),
        sa.Column("version_set_digest", sa.String(80), nullable=False),
        sa.Column("manifest_digest", sa.String(80), nullable=True),
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
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_system_version_set_application",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "declared_environment_id"],
            ["environments.workspace_id", "environments.environment_id"],
            name="fk_system_version_set_environment",
        ),
        sa.UniqueConstraint(
            "workspace_id", "system_version_set_id", name="uq_system_version_set_workspace"
        ),
        sa.UniqueConstraint(
            "record_digest", name="uq_system_version_set_record_digest"
        ),
        sa.UniqueConstraint(
            "workspace_id", "version_set_digest", name="uq_system_version_set_workspace_digest"
        ),
        sa.UniqueConstraint(
            "workspace_id", "manifest_digest", name="uq_system_version_set_workspace_manifest"
        ),
    )
    op.create_index(
        "ix_system_version_set_workspace_application",
        "system_version_sets",
        ["workspace_id", "application_id", "declared_environment_id", "created_at"],
    )


def _create_bootstrap_attestations() -> None:
    op.create_table(
        "bootstrap_attestations",
        sa.Column("bootstrap_attestation_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("environment_id", sa.String(128), nullable=False),
        sa.Column("exact_initial_system_version_set_binding", sa.JSON(), nullable=False),
        sa.Column("attester_principal_id", sa.String(128), nullable=False),
        sa.Column("attester_trust_role", sa.String(32), nullable=False),
        sa.Column("attestation_scope", sa.String(64), nullable=False),
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
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_bootstrap_attestation_application",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["environments.workspace_id", "environments.environment_id"],
            name="fk_bootstrap_attestation_environment",
        ),
        sa.UniqueConstraint(
            "workspace_id", "bootstrap_attestation_id", name="uq_bootstrap_attestation_workspace"
        ),
        sa.UniqueConstraint(
            "record_digest", name="uq_bootstrap_attestation_record_digest"
        ),
        sa.CheckConstraint(
            "attester_trust_role IN ('integrator','catalog_admin','trusted_builder')",
            name="ck_bootstrap_attestation_attester_role",
        ),
        sa.CheckConstraint(
            "attestation_scope IN ('INITIAL_DESIRED_ASSIGNMENT')",
            name="ck_bootstrap_attestation_scope",
        ),
    )
    op.create_index(
        "ix_bootstrap_attestation_workspace_application",
        "bootstrap_attestations",
        ["workspace_id", "application_id", "environment_id", "created_at"],
    )


def _create_system_assignments() -> None:
    op.create_table(
        "system_assignments",
        sa.Column("assignment_id", sa.String(128), primary_key=True),
        sa.Column("workspace_id", sa.String(128), nullable=False),
        sa.Column("application_id", sa.String(128), nullable=False),
        sa.Column("environment_id", sa.String(128), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False),
        sa.Column("lifecycle_state", sa.String(32), nullable=False),
        sa.Column("transition_kind", sa.String(32), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("exact_previous_assignment_binding_or_null", sa.JSON(), nullable=True),
        sa.Column("exact_slot_version_set_bindings", sa.JSON(), nullable=False),
        sa.Column("exposure", sa.String(32), nullable=False),
        sa.Column("expected_previous_generation", sa.BigInteger(), nullable=True),
        sa.Column("exact_assignment_authority_binding", sa.JSON(), nullable=False),
        sa.Column("requested_by_external_operation_id", sa.String(128), nullable=True),
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "application_id"],
            ["ai_applications.workspace_id", "ai_applications.application_id"],
            name="fk_system_assignment_application",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "environment_id"],
            ["environments.workspace_id", "environments.environment_id"],
            name="fk_system_assignment_environment",
        ),
        sa.UniqueConstraint(
            "workspace_id", "assignment_id", name="uq_system_assignment_workspace"
        ),
        sa.CheckConstraint("generation >= 1", name="ck_system_assignment_generation"),
        sa.CheckConstraint("revision >= 1", name="ck_system_assignment_revision"),
        sa.CheckConstraint(
            "transition_kind IN ('BOOTSTRAP','SET_DESIRED','FREEZE_EXPOSURE',"
            "'RESUME_AFTER_ROLLBACK','RETIRE')",
            name="ck_system_assignment_transition_kind",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('ACTIVE','RETIRED')", name="ck_system_assignment_lifecycle"
        ),
        sa.CheckConstraint(
            "exposure IN ('EXPOSED','STOPPED')", name="ck_system_assignment_exposure"
        ),
    )
    op.create_index(
        "ix_system_assignment_workspace_application",
        "system_assignments",
        ["workspace_id", "application_id", "environment_id", "lifecycle_state"],
    )
    # One non-retired assignment aggregate per identity key (CAS guard).
    op.create_index(
        "uq_system_assignment_active_identity",
        "system_assignments",
        ["workspace_id", "application_id", "environment_id"],
        unique=True,
        postgresql_where=sa.text("lifecycle_state <> 'RETIRED'"),
        sqlite_where=sa.text("lifecycle_state <> 'RETIRED'"),
    )


def upgrade() -> None:
    _create_component_revisions()
    _create_topology_revisions()
    _create_system_version_sets()
    _create_bootstrap_attestations()
    _create_system_assignments()


def _assert_downgrade_safe() -> None:
    bind = op.get_bind()
    for table in _NEW_TABLES:
        if bind.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError(_DOWNGRADE_BLOCKED)
    if bind.dialect.name == "postgresql":
        events = bind.execute(
            sa.text(
                "SELECT 1 FROM events WHERE contract_version = 'v4' AND "
                "aggregate_type IN ('component_revision','topology_revision',"
                "'system_version_set','bootstrap_attestation','system_assignment') "
                "LIMIT 1"
            )
        ).first()
        receipts = bind.execute(
            sa.text(
                "SELECT 1 FROM authority_receipts WHERE subject_kind IN "
                "('COMPONENT_REVISION','TOPOLOGY_REVISION','SYSTEM_VERSION_SET',"
                "'BOOTSTRAP_ATTESTATION','SYSTEM_ASSIGNMENT') LIMIT 1"
            )
        ).first()
        if events is not None or receipts is not None:
            raise RuntimeError(_DOWNGRADE_BLOCKED)


def downgrade() -> None:
    _assert_downgrade_safe()
    op.drop_index(
        "uq_system_assignment_active_identity",
        table_name="system_assignments",
    )
    for table in reversed(_NEW_TABLES):
        op.drop_table(table)
