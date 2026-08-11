"""Migration-chain regression checks for the documented local workflow."""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def test_sqlite_upgrade_head_allows_multi_stage_gate_binding(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "control.sqlite"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "006")

    historical_engine = sa.create_engine(f"sqlite:///{database}")
    with historical_engine.begin() as connection:
        assert (
            connection.execute(sa.text("select version_num from alembic_version")).scalar_one()
            == "006"
        )
    historical_engine.dispose()

    command.upgrade(config, "head")

    engine = sa.create_engine(f"sqlite:///{database}")
    inspector = sa.inspect(engine)
    gate_unique_columns = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("gate_reports")
    }
    gate_indexes = {item["name"]: item for item in inspector.get_indexes("gate_reports")}
    outbox_columns = {
        item["name"]: item for item in inspector.get_columns("outbox")
    }
    with engine.begin() as connection:
        assert (
            connection.execute(sa.text("select version_num from alembic_version")).scalar_one()
            == "009"
        )
        base = {
            "workorder_id": "wo-multi-stage",
            "workorder_hash": "a" * 64,
            "target_versionset_id": "vs-target",
            "target_versionset_digest": "sha256:" + "b" * 64,
            "target_revision": 1,
            "dataset_id": "dataset",
            "dataset_version": "1",
            "dataset_digest": "sha256:" + "c" * 64,
            "evidence_digest": "sha256:" + "d" * 64,
            "candidate_digest": "sha256:" + "e" * 64,
            "overall_status": "passed",
            "report": "{}",
        }
        insert = sa.text(
            """INSERT INTO gate_reports (
              eval_id, report_id, workorder_id, workorder_hash, target_versionset_id,
              target_versionset_digest, target_revision, dataset_id, dataset_version,
              dataset_digest, evidence_digest, candidate_digest, report_hash,
              overall_status, report
            ) VALUES (
              :eval_id, :report_id, :workorder_id, :workorder_hash, :target_versionset_id,
              :target_versionset_digest, :target_revision, :dataset_id, :dataset_version,
              :dataset_digest, :evidence_digest, :candidate_digest, :report_hash,
              :overall_status, :report
            )"""
        )
        for stage in ("initial", "post"):
            connection.execute(
                insert,
                {
                    **base,
                    "eval_id": f"eval-{stage}",
                    "report_id": f"report-{stage}",
                    "report_hash": ("1" if stage == "initial" else "2") * 64,
                },
            )

        # A catalog record must block the 008 downgrade.
        connection.execute(
            sa.text(
                """INSERT INTO ai_applications (
                  application_id, workspace_id, project_id, slug, display_name,
                  owner_principal_ids, criticality, data_classification,
                  governance_mode, lifecycle_state, revision, envelope_payload,
                  record_digest, authority_receipt_id, recorded_by_principal
                ) VALUES (
                  :application_id, :workspace_id, :project_id, :slug, :display_name,
                  :owner_principal_ids, :criticality, :data_classification,
                  :governance_mode, :lifecycle_state, :revision, :envelope_payload,
                  :record_digest, :authority_receipt_id, :recorded_by_principal
                )"""
            ),
            {
                "application_id": "app_01J0000000000001",
                "workspace_id": "ws_01J0000000000001",
                "project_id": "proj_01J0000000000001",
                "slug": "migration-guard",
                "display_name": "Migration guard application",
                "owner_principal_ids": '["prn_01J0000000000001"]',
                "criticality": "P2",
                "data_classification": "INTERNAL",
                "governance_mode": "MANAGED",
                "lifecycle_state": "ACTIVE",
                "revision": 1,
                "envelope_payload": "{}",
                "record_digest": "sha256:" + "a" * 64,
                "authority_receipt_id": "arec_01J0000000000001",
                "recorded_by_principal": "prn_01J0000000000001",
            },
        )

    assert ("workorder_hash",) not in gate_unique_columns
    assert gate_indexes["ix_gate_reports_workorder_hash"]["unique"] == 0
    assert all(
        outbox_columns[name]["nullable"] is False
        for name in ("source_event_id", "source_event_seq", "event_type", "payload_digest")
    )

    # V5-1A migration 008: the four catalog tables exist with workspace-scoped
    # unique identity and digest columns.
    application_columns = {item["name"] for item in inspector.get_columns("ai_applications")}
    for column in (
        "application_id",
        "workspace_id",
        "project_id",
        "slug",
        "record_digest",
        "authority_receipt_id",
        "envelope_payload",
    ):
        assert column in application_columns
    environment_columns = {item["name"] for item in inspector.get_columns("environments")}
    assert {"environment_id", "application_id", "logical_name"} <= environment_columns
    component_columns = {item["name"] for item in inspector.get_columns("system_components")}
    assert {"component_id", "component_kind", "logical_name"} <= component_columns
    edge_columns = {item["name"] for item in inspector.get_columns("dependency_edges")}
    assert {"edge_id", "from_component_id", "to_component_id", "edge_digest"} <= edge_columns
    application_unique = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints("ai_applications")
    }
    assert ("workspace_id", "project_id", "slug") in application_unique
    component_unique = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints("system_components")
    }
    assert ("workspace_id", "application_id", "component_kind", "logical_name") in component_unique

    # V5-1B migration 009: the five version tables exist with the version-set
    # digest/manifest digest uniqueness and the assignment CAS partial index.
    for table in (
        "component_revisions",
        "topology_revisions",
        "system_version_sets",
        "bootstrap_attestations",
        "system_assignments",
    ):
        columns = {item["name"] for item in inspector.get_columns(table)}
        assert {"envelope_payload", "record_digest", "authority_receipt_id"} <= columns, table
    version_set_unique = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints("system_version_sets")
    }
    assert ("workspace_id", "version_set_digest") in version_set_unique
    assert ("workspace_id", "manifest_digest") in version_set_unique
    assignment_indexes = {
        item["name"]: item for item in inspector.get_indexes("system_assignments")
    }
    assert "uq_system_assignment_active_identity" in assignment_indexes

    # 009 downgrade is blocked while a version record exists.
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO system_version_sets (
                  system_version_set_id, workspace_id, application_id,
                  declared_environment_id, exact_component_revision_bindings,
                  exact_topology_revision_binding, identity_assurance_summary,
                  provenance_receipt_ids, version_set_digest, manifest_digest,
                  envelope_payload, record_digest, authority_receipt_id,
                  recorded_by_principal
                ) VALUES (
                  :id, :workspace, :application, :environment, :bindings, :topology,
                  :summary, :provenance, :digest, NULL, :envelope, :digest,
                  :receipt, :principal
                )"""
            ),
            {
                "id": "vset_01J0000000000001",
                "workspace": "ws_01J0000000000001",
                "application": "app_01J0000000000001",
                "environment": "env_01J0000000000001",
                "bindings": "[]",
                "topology": "{}",
                "summary": "{}",
                "provenance": "[]",
                "digest": "sha256:" + "a" * 64,
                "envelope": "{}",
                "receipt": "arec_01J0000000000001",
                "principal": "prn_01J0000000000001",
            },
        )
    with pytest.raises(RuntimeError, match="009.downgrade_blocked"):
        command.downgrade(config, "008")
    with engine.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM system_version_sets WHERE system_version_set_id = :id"),
            {"id": "vset_01J0000000000001"},
        )

    # 008 downgrade is blocked while catalog records exist.
    with pytest.raises(RuntimeError, match="008.downgrade_blocked"):
        command.downgrade(config, "007")
    engine.dispose()
