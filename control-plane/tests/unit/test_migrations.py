"""Migration-chain regression checks for the documented local workflow."""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


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
    expected_head = ScriptDirectory.from_config(config).get_current_head()

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
            == expected_head
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
    # The failed downgrade already removed later empty stages; restore head so
    # the V5-1C hardening and event-envelope assertions inspect the real schema.
    command.upgrade(config, "head")

    # V5-1C migrations 010/011: the three case tables carry exact revisions,
    # closed declared-version identity, fresh-credential proof and append-only
    # source-snapshot identity.
    binding_columns = {
        item["name"] for item in inspector.get_columns("application_case_bindings")
    }
    for column in (
        "application_case_binding_id",
        "case_id",
        "case_revision",
        "case_digest",
        "application_id",
        "environment_id",
        "revision",
        "declared_system_version_set_binding_or_unknown",
        "binding_digest",
        "envelope_payload",
        "record_digest",
        "authority_receipt_id",
    ):
        assert column in binding_columns
    binding_unique = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("application_case_bindings")
    }
    assert ("workspace_id", "case_id", "case_revision", "case_digest") in binding_unique
    acceptance_columns = {
        item["name"] for item in inspector.get_columns("acceptance_criteria_revisions")
    }
    for column in (
        "acceptance_criteria_revision_id",
        "confirmation_status",
        "revision",
        "resolution_contract_binding_status",
        "proposer_principal",
        "confirmer_principal",
        "exact_previous_proposed_revision_binding",
        "exact_previous_proposed_revision_id",
        "exact_previous_proposed_revision_digest",
        "reauthentication_credential_binding",
        "acceptance_digest",
        "record_digest",
    ):
        assert column in acceptance_columns
    acceptance_checks = {
        item["name"] for item in inspector.get_check_constraints("acceptance_criteria_revisions")
    }
    assert "ck_acceptance_criteria_revision_status_shape" in acceptance_checks
    acceptance_indexes = {
        item["name"]: item
        for item in inspector.get_indexes("acceptance_criteria_revisions")
    }
    assert acceptance_indexes["uq_acceptance_confirmed_previous_proposal"][
        "unique"
    ] == 1
    snapshot_columns = {
        item["name"]: item
        for item in inspector.get_columns("issue_source_snapshots")
    }
    assert {
        "snapshot_digest",
        "edited_flag",
        "deleted_flag",
        "instruction_markers_detected",
    } <= snapshot_columns.keys()
    assert snapshot_columns["source_url"]["nullable"] is True
    assert snapshot_columns["external_repo"]["nullable"] is True
    assert snapshot_columns["external_issue_number"]["nullable"] is True
    snapshot_unique = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("issue_source_snapshots")
    }
    assert ("workspace_id", "case_id", "snapshot_digest") in snapshot_unique
    assert ("snapshot_digest",) not in snapshot_unique
    assert "trust_roles" in {
        item["name"] for item in inspector.get_columns("public_principals")
    }

    # V5 event migration 012 persists the frozen major-2 identity envelope.
    event_columns = {item["name"] for item in inspector.get_columns("events")}
    assert {
        "event_contract_major",
        "routing_key",
        "exact_subject_binding",
        "authority_receipt_id",
    } <= event_columns

    # 010 downgrade is blocked while a binding record exists.
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO application_case_bindings (
                  application_case_binding_id, workspace_id, case_id, case_revision,
                  case_digest, application_id, environment_id,
                  declared_system_version_set_binding_or_unknown, binding_digest,
                  envelope_payload, record_digest, authority_receipt_id,
                  recorded_by_principal
                ) VALUES (
                  :id, :workspace, :case, :case_revision, :case_digest, :application,
                  :environment, :declared_binding, :binding_digest, :envelope, :digest, :receipt,
                  :principal
                )"""
            ),
            {
                "id": "acb_01J0000000000001",
                "workspace": "ws_01J0000000000001",
                "case": "case_01J0000000000001",
                "case_revision": 1,
                "case_digest": "sha256:" + "a" * 64,
                "application": "app_01J0000000000001",
                "environment": "env_01J0000000000001",
                "declared_binding": '{"kind":"UNKNOWN","reason":"MIGRATION_GUARD"}',
                "binding_digest": "sha256:" + "b" * 64,
                "envelope": "{}",
                "digest": "sha256:" + "c" * 64,
                "receipt": "arec_01J0000000000001",
                "principal": "prn_01J0000000000001",
            },
        )
    with pytest.raises(RuntimeError, match="010.downgrade_blocked"):
        command.downgrade(config, "009")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "DELETE FROM application_case_bindings "
                "WHERE application_case_binding_id = :id"
            ),
            {"id": "acb_01J0000000000001"},
        )

    # 008 downgrade is blocked while catalog records exist.
    with pytest.raises(RuntimeError, match="008.downgrade_blocked"):
        command.downgrade(config, "007")
    engine.dispose()


def test_v5_1c_hardening_migrates_json_null_and_manual_source_rows(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "legacy-v5-1c.sqlite"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "010")

    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO application_case_bindings (
                  application_case_binding_id, workspace_id, case_id, case_revision,
                  case_digest, application_id, environment_id,
                  declared_system_version_set_binding_or_unknown, binding_digest,
                  envelope_payload, record_digest, authority_receipt_id,
                  recorded_by_principal
                ) VALUES (
                  :id, :workspace, :case, 1, :case_digest, :application,
                  :environment, 'null', :binding_digest, '{}', :record_digest,
                  :receipt, :principal
                )"""
            ),
            {
                "id": "acb_01J00000000000AA",
                "workspace": "ws_01J00000000000AA",
                "case": "case_01J00000000000AA",
                "case_digest": "sha256:" + "1" * 64,
                "application": "app_01J00000000000AA",
                "environment": "env_01J00000000000AA",
                "binding_digest": "sha256:" + "2" * 64,
                "record_digest": "sha256:" + "3" * 64,
                "receipt": "arec_01J00000000000AA",
                "principal": "prn_01J00000000000AA",
            },
        )
        connection.execute(
            sa.text(
                """INSERT INTO acceptance_criteria_revisions (
                  acceptance_criteria_revision_id, workspace_id, case_id,
                  case_revision, case_digest, exact_resolution_contract_binding,
                  confirmation_status, proposer_principal, proposed_at,
                  confirmer_principal, confirmed_at,
                  exact_previous_proposed_revision_binding, acceptance_source,
                  reproducer_input, reproducer_environment, expected_behavior,
                  oracle_or_evaluator, applicable_workload_profile,
                  applicable_deployment_profile, acceptance_digest,
                  envelope_payload, record_digest, authority_receipt_id,
                  recorded_by_principal
                ) VALUES (
                  :id, :workspace, :case, 1, :case_digest, '{}', 'PROPOSED',
                  :principal, :proposed_at, NULL, NULL, 'null', '{}', 'null',
                  'null', '{}', 'null', '{}', '{}', :acceptance_digest, '{}',
                  :record_digest, :receipt, :principal
                )"""
            ),
            {
                "id": "acr_01J00000000000AA",
                "workspace": "ws_01J00000000000AA",
                "case": "case_01J00000000000AA",
                "case_digest": "sha256:" + "1" * 64,
                "principal": "prn_01J00000000000AA",
                "proposed_at": "2026-08-11 10:00:00",
                "acceptance_digest": "sha256:" + "4" * 64,
                "record_digest": "sha256:" + "5" * 64,
                "receipt": "arec_01J00000000000AB",
            },
        )
        connection.execute(
            sa.text(
                """INSERT INTO issue_source_snapshots (
                  issue_snapshot_id, workspace_id, case_id, source_kind,
                  source_url, external_repo, external_issue_number,
                  snapshot_payload, snapshot_digest, edited_flag, deleted_flag,
                  instruction_markers_detected, fetched_at, recorded_by_principal
                ) VALUES (
                  :id, :workspace, :case, 'manual', 'manual://legacy',
                  'legacy/manual', 1, '{}', :digest, 0, 0, 0, :fetched_at,
                  :principal
                )"""
            ),
            {
                "id": "isnap_01J0000000000AA",
                "workspace": "ws_01J00000000000AA",
                "case": "case_01J00000000000AA",
                "digest": "sha256:" + "6" * 64,
                "fetched_at": "2026-08-11 10:00:00",
                "principal": "prn_01J00000000000AA",
            },
        )

    command.upgrade(config, "head")

    bindings = sa.table(
        "application_case_bindings",
        sa.column("application_case_binding_id", sa.String()),
        sa.column("declared_system_version_set_binding_or_unknown", sa.JSON()),
        sa.column("revision", sa.BigInteger()),
    )
    snapshots = sa.table(
        "issue_source_snapshots",
        sa.column("issue_snapshot_id", sa.String()),
        sa.column("source_url", sa.String()),
        sa.column("external_repo", sa.String()),
        sa.column("external_issue_number", sa.BigInteger()),
    )
    acceptance = sa.table(
        "acceptance_criteria_revisions",
        sa.column("acceptance_criteria_revision_id", sa.String()),
        sa.column("revision", sa.BigInteger()),
        sa.column("exact_previous_proposed_revision_id", sa.String()),
        sa.column("reauthentication_credential_binding", sa.JSON()),
    )
    with engine.begin() as connection:
        binding = connection.execute(sa.select(bindings)).mappings().one()
        assert binding["declared_system_version_set_binding_or_unknown"] == {
            "kind": "UNKNOWN",
            "reason": "MIGRATED_UNDECLARED",
        }
        assert binding["revision"] == 1
        snapshot = connection.execute(sa.select(snapshots)).mappings().one()
        assert snapshot["source_url"] == "manual://legacy"
        assert snapshot["external_repo"] is None
        assert snapshot["external_issue_number"] is None
        proposal = connection.execute(sa.select(acceptance)).mappings().one()
        assert proposal["revision"] == 1
        assert proposal["exact_previous_proposed_revision_id"] is None
        assert proposal["reauthentication_credential_binding"] is None

    with pytest.raises(RuntimeError, match="011.downgrade_blocked"):
        command.downgrade(config, "010")
    engine.dispose()
