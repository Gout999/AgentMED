"""Migration-chain regression checks for the documented local workflow."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def _config(root: Path, database_url: str) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _schema_fingerprint(engine: sa.Engine) -> str:
    """Return a stable schema-only digest for zero-partial-DDL assertions."""

    inspector = sa.inspect(engine)
    payload: dict[str, object] = {}
    for table in sorted(inspector.get_table_names()):
        payload[table] = {
            "columns": sorted(
                (
                    column["name"],
                    str(column["type"]),
                    bool(column["nullable"]),
                    str(column.get("default")),
                )
                for column in inspector.get_columns(table)
            ),
            "checks": sorted(
                (item.get("name") or "", item.get("sqltext") or "")
                for item in inspector.get_check_constraints(table)
            ),
            "indexes": sorted(
                (
                    item.get("name") or "",
                    tuple(item.get("column_names") or ()),
                    bool(item.get("unique")),
                )
                for item in inspector.get_indexes(table)
            ),
            "unique": sorted(
                (
                    item.get("name") or "",
                    tuple(item.get("column_names") or ()),
                )
                for item in inspector.get_unique_constraints(table)
            ),
        }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_sqlite_upgrade_head_allows_multi_stage_gate_binding(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "control.sqlite"
    config = _config(root, f"sqlite:///{database}")
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
            == "013"
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

    assert ("workorder_hash",) not in gate_unique_columns
    assert gate_indexes["ix_gate_reports_workorder_hash"]["unique"] == 0
    assert all(
        outbox_columns[name]["nullable"] is False
        for name in ("source_event_id", "source_event_seq", "event_type", "payload_digest")
    )
    assert outbox_columns["event_contract_major"]["nullable"] is True

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

    # R1 migrations 011/012 add exact lifecycle-history authority and the
    # frozen V5 major-2 event envelope without changing V3/V4 rows.
    assert "trust_roles" in {
        item["name"] for item in inspector.get_columns("public_principals")
    }
    for table in (
        "ai_application_lifecycle_revisions",
        "system_component_lifecycle_revisions",
    ):
        columns = {item["name"] for item in inspector.get_columns(table)}
        assert {
            "workspace_id",
            "revision",
            "lifecycle_state",
            "envelope_payload",
            "record_digest",
            "authority_receipt_id",
            "recorded_by_principal",
            "recorded_at",
        } <= columns
    assert {
        "event_contract_major",
        "routing_key",
        "exact_subject_binding",
        "authority_receipt_id",
    } <= {item["name"] for item in inspector.get_columns("events")}
    event_context = next(
        item["sqltext"]
        for item in inspector.get_check_constraints("events")
        if item["name"] == "ck_events_v4_context"
    )
    outbox_context = next(
        item["sqltext"]
        for item in inspector.get_check_constraints("outbox")
        if item["name"] == "ck_outbox_v4_context"
    )
    for context in (event_context, outbox_context):
        assert "event_version = '1.0'" in context
        assert "event_version = '2.0'" in context
        assert "contract_version IS NOT NULL" in context
    for column in (
        "event_contract_major",
        "routing_key",
        "exact_subject_binding",
        "authority_receipt_id",
    ):
        assert f"{column} IS NULL" in event_context
    assert "event_contract_major IS NULL" in outbox_context

    # 009 downgrade is blocked while a version record exists.
    command.downgrade(config, "009")
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
    # Restore head so the 010 assertions below inspect the real schema.
    command.upgrade(config, "head")

    # V5-1C migration 010: the three case tables exist with the exact-case
    # binding uniqueness, the confirmation-status shape check, and the read-only
    # issue snapshot projection.
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
        "proposer_principal",
        "confirmer_principal",
        "exact_previous_proposed_revision_binding",
        "acceptance_digest",
        "record_digest",
    ):
        assert column in acceptance_columns
    acceptance_checks = {
        item["name"] for item in inspector.get_check_constraints("acceptance_criteria_revisions")
    }
    assert "ck_acceptance_criteria_revision_status_shape" in acceptance_checks
    snapshot_columns = {
        item["name"] for item in inspector.get_columns("issue_source_snapshots")
    }
    assert {"snapshot_digest", "edited_flag", "deleted_flag", "instruction_markers_detected"} <= snapshot_columns

    # 010 downgrade is blocked while a binding record exists.  Move to 010
    # first so the newer R1 guard cannot mask the older migration's contract.
    command.downgrade(config, "010")
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
                  :environment, NULL, :binding_digest, :envelope, :digest, :receipt,
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

    # 008 downgrade is blocked while catalog records exist.  Insert the record
    # only after moving to 008 so R1's stronger legacy/lifecycle guard does not
    # hide the historical migration boundary under test.
    command.downgrade(config, "008")
    with engine.begin() as connection:
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
    with pytest.raises(RuntimeError, match="008.downgrade_blocked"):
        command.downgrade(config, "007")
    engine.dispose()


def test_v5_r1_graph_and_legacy_preflight_precedes_every_ddl() -> None:
    root = Path(__file__).resolve().parents[2]
    script = ScriptDirectory.from_config(_config(root, "sqlite://"))

    assert script.get_heads() == ["013"]
    assert [
        item.revision for item in script.iterate_revisions("013", "009")
    ] == ["013", "012", "011", "010"]

    for filename in (
        "011_v5_lifecycle_authority_foundation.py",
        "012_v5_event_envelope.py",
    ):
        source = (root / "alembic" / "versions" / filename).read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
        upgrade = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
        )
        first = upgrade.body[0]
        assert isinstance(first, ast.Expr), filename
        assert isinstance(first.value, ast.Call), filename
        assert isinstance(first.value.func, ast.Name), filename
        assert first.value.func.id == "_assert_no_legacy_v5_history", filename

    source = (
        root / "alembic" / "versions" / "012_v5_event_envelope.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    downgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )
    first = downgrade.body[0]
    assert isinstance(first, ast.Expr)
    assert isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "_assert_downgrade_safe"


@pytest.mark.parametrize(
    ("table_name", "column_name", "invalid_value"),
    [
        ("events", "event_contract_major", 2),
        ("events", "routing_key", "{}"),
        ("events", "exact_subject_binding", "{}"),
        ("events", "authority_receipt_id", "arec_invalid_legacy"),
        ("outbox", "event_contract_major", 2),
    ],
)
def test_v5_r1_legacy_contract_rejects_major_2_storage_columns(
    tmp_path: Path,
    table_name: str,
    column_name: str,
    invalid_value: object,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / f"r1-legacy-{table_name}-{column_name}.sqlite"
    config = _config(root, f"sqlite:///{database}")
    command.upgrade(config, "head")
    engine = sa.create_engine(f"sqlite:///{database}")

    if table_name == "events":
        statement = sa.text(
            f"""INSERT INTO events (
              event_id, aggregate_type, aggregate_id, seq, event_type, payload,
              causation_id, correlation_id, actor, workspace_id,
              contract_version, event_version, transaction_id, actor_principal,
              payload_digest, {column_name}
            ) VALUES (
              'evt_invalidlegacy01', 'legacy_case', 'case_invalidlegacy01', 1,
              'legacy.updated', '{{}}', 'none', 'legacy-invalid',
              'legacy-controller', 'ws_invalidlegacy01', NULL, '1.0',
              'txn_invalidlegacy01', 'prn_invalidlegacy01', :digest, :invalid
            )"""
        )
    else:
        statement = sa.text(
            f"""INSERT INTO outbox (
              outbox_id, aggregate_id, source_event_id, source_event_seq,
              channel, event_type, payload, payload_digest, status, attempts,
              workspace_id, contract_version, aggregate_type, event_version,
              transaction_id, actor_principal, {column_name}
            ) VALUES (
              'out_invalidlegacy01', 'case_invalidlegacy01',
              'evt_invalidlegacy02', 1, 'v4.domain.events', 'legacy.updated',
              '{{}}', :digest, 'PENDING', 0, 'ws_invalidlegacy01', NULL,
              'legacy_case', '1.0', 'txn_invalidlegacy02',
              'prn_invalidlegacy01', :invalid
            )"""
        )

    with engine.begin() as connection:
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                statement,
                {
                    "digest": "sha256:" + "9" * 64,
                    "invalid": invalid_value,
                },
            )
    engine.dispose()


def test_v5_r1_fresh_schema_accepts_registered_history_and_blocks_downgrade(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "r1-fresh.sqlite"
    config = _config(root, f"sqlite:///{database}")
    command.upgrade(config, "010")

    # A populated V3/V4 audit row is not V5 lifecycle authority and must not
    # turn a safe previous-head database into a false recovery requirement.
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO audit (
                  audit_id, actor, action, target, params_digest, result,
                  error_code, trace_id, evidence_refs
                ) VALUES (
                  'audit-r1-non-v5', 'legacy-controller', 'legacy.read',
                  'legacy:case', :digest, 'ok', NULL, 'trace-r1', NULL
                )"""
            ),
            {"digest": "sha256:" + "0" * 64},
        )

    command.upgrade(config, "head")
    inspector = sa.inspect(engine)
    assert {
        "ai_application_lifecycle_revisions",
        "system_component_lifecycle_revisions",
    } <= set(inspector.get_table_names())
    assert "trust_roles" in {
        item["name"] for item in inspector.get_columns("public_principals")
    }
    assert {
        "REGISTERED",
        "ACTIVE",
        "ARCHIVED",
    } <= set(
        " ".join(
            item.get("sqltext") or ""
            for item in inspector.get_check_constraints("ai_applications")
        ).replace("'", "").replace("(", " ").replace(")", " ").replace(",", " ").split()
    )

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO ai_applications (
                  application_id, workspace_id, project_id, slug, display_name,
                  owner_principal_ids, criticality, data_classification,
                  governance_mode, lifecycle_state, revision, envelope_payload,
                  record_digest, authority_receipt_id, recorded_by_principal
                ) VALUES (
                  :application, :workspace, :project, 'r1-lifecycle', 'R1 lifecycle',
                  '[]', 'P2', 'INTERNAL', 'MANAGED', 'REGISTERED', 1, '{}',
                  :head_digest, :head_receipt, :principal
                )"""
            ),
            {
                "application": "app_01J00000000000R1",
                "workspace": "ws_01J00000000000R1",
                "project": "proj_01J0000000000R1",
                "head_digest": "sha256:" + "1" * 64,
                "head_receipt": "arec_01J0000000000R1",
                "principal": "prn_01J00000000000R1",
            },
        )
        connection.execute(
            sa.text(
                """INSERT INTO ai_application_lifecycle_revisions (
                  workspace_id, application_id, revision, lifecycle_state,
                  exact_previous_application_binding, envelope_payload,
                  record_digest, authority_receipt_id, recorded_by_principal
                ) VALUES (
                  :workspace, :application, 1, 'REGISTERED', NULL, '{}',
                  :digest, :receipt, :principal
                )"""
            ),
            {
                "workspace": "ws_01J00000000000R1",
                "application": "app_01J00000000000R1",
                "digest": "sha256:" + "2" * 64,
                "receipt": "arec_01J0000000000H1",
                "principal": "prn_01J00000000000R1",
            },
        )
        connection.execute(
            sa.text(
                """INSERT INTO ai_application_lifecycle_revisions (
                  workspace_id, application_id, revision, lifecycle_state,
                  exact_previous_application_binding, envelope_payload,
                  record_digest, authority_receipt_id, recorded_by_principal
                ) VALUES (
                  :workspace, :application, 2, 'ACTIVE', :previous, '{}',
                  :digest, :receipt, :principal
                )"""
            ),
            {
                "workspace": "ws_01J00000000000R1",
                "application": "app_01J00000000000R1",
                "previous": '{"kind":"AI_APPLICATION","revision":1}',
                "digest": "sha256:" + "3" * 64,
                "receipt": "arec_01J0000000000H2",
                "principal": "prn_01J00000000000R1",
            },
        )
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    """INSERT INTO ai_application_lifecycle_revisions (
                      workspace_id, application_id, revision, lifecycle_state,
                      exact_previous_application_binding, envelope_payload,
                      record_digest, authority_receipt_id, recorded_by_principal
                    ) VALUES (
                      :workspace, :application, 1, 'ACTIVE', NULL, '{}',
                      :digest, :receipt, :principal
                    )"""
                ),
                {
                    "workspace": "ws_01J00000000000R1",
                    "application": "app_01J00000000000R1",
                    "digest": "sha256:" + "4" * 64,
                    "receipt": "arec_01J0000000000H3",
                    "principal": "prn_01J00000000000R1",
                },
            )

    command.downgrade(config, "012")
    before = _schema_fingerprint(engine)
    with pytest.raises(RuntimeError, match="012.v5_r1_history_prevents_downgrade"):
        command.downgrade(config, "010")
    assert _schema_fingerprint(engine) == before
    with engine.begin() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "012"
        assert connection.execute(
            sa.text("SELECT COUNT(*) FROM ai_application_lifecycle_revisions")
        ).scalar_one() == 2
    engine.dispose()


def _insert_011_downgrade_guard_fact(
    engine: sa.Engine, fact_kind: str
) -> None:
    with engine.begin() as connection:
        if fact_kind == "lifecycle_history":
            connection.execute(
                sa.text(
                    """INSERT INTO ai_applications (
                      application_id, workspace_id, project_id, slug, display_name,
                      owner_principal_ids, criticality, data_classification,
                      governance_mode, lifecycle_state, revision, envelope_payload,
                      record_digest, authority_receipt_id, recorded_by_principal
                    ) VALUES (
                      'app_guard011000001', 'ws_guard0110000001',
                      'proj_guard01100001', 'guard-011', 'Guard 011', '[]', 'P2',
                      'INTERNAL', 'MANAGED', 'REGISTERED', 1, '{}', :head_digest,
                      'arec_guard0110001', 'prn_guard01100001'
                    )"""
                ),
                {"head_digest": "sha256:" + "5" * 64},
            )
            connection.execute(
                sa.text(
                    """INSERT INTO ai_application_lifecycle_revisions (
                      workspace_id, application_id, revision, lifecycle_state,
                      exact_previous_application_binding, envelope_payload,
                      record_digest, authority_receipt_id, recorded_by_principal
                    ) VALUES (
                      'ws_guard0110000001', 'app_guard011000001', 1,
                      'REGISTERED', NULL, '{}', :digest, 'arec_guard0110002',
                      'prn_guard01100001'
                    )"""
                ),
                {"digest": "sha256:" + "6" * 64},
            )
        else:
            connection.execute(
                sa.text(
                    """INSERT INTO public_principals (
                      principal_id, workspace_id, principal_type, state,
                      subject_digest, audiences, project_ids, environment_ids,
                      scopes, claims_digest, trust_roles
                    ) VALUES (
                      'prn_guard01100002', 'ws_guard0110000002', 'service',
                      'ACTIVE', :subject_digest, '[]', '[]', '[]', '[]',
                      :claims_digest, '["TRUST_ADMIN"]'
                    )"""
                ),
                {
                    "subject_digest": "sha256:" + "7" * 64,
                    "claims_digest": "sha256:" + "8" * 64,
                },
            )


def _assert_011_downgrade_guard_preserves_schema(
    *, root: Path, engine: sa.Engine, database_url: str, fact_kind: str
) -> None:
    config = _config(root, database_url)
    command.upgrade(config, "011")
    _insert_011_downgrade_guard_fact(engine, fact_kind)
    before = _schema_fingerprint(engine)

    with pytest.raises(RuntimeError, match="011.downgrade_blocked"):
        command.downgrade(config, "010")

    assert _schema_fingerprint(engine) == before
    with engine.begin() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "011"


@pytest.mark.parametrize("fact_kind", ["lifecycle_history", "trust_roles"])
def test_v5_r1_011_downgrade_guard_preserves_sqlite_schema(
    tmp_path: Path,
    fact_kind: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / f"r1-011-downgrade-{fact_kind}.sqlite"
    engine = sa.create_engine(f"sqlite:///{database}")
    _assert_011_downgrade_guard_preserves_schema(
        root=root,
        engine=engine,
        database_url=f"sqlite:///{database}",
        fact_kind=fact_kind,
    )
    engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CASELOOP_ALLOW_INTEGRATION_RESET") != "true",
    reason="explicit disposable PostgreSQL reset opt-in required",
)
@pytest.mark.parametrize("fact_kind", ["lifecycle_history", "trust_roles"])
def test_v5_r1_011_postgresql_downgrade_guard_preserves_schema(
    fact_kind: str,
) -> None:
    from conftest import (
        TEST_DATABASE_URL,
        _new_pg_engine,
        _reset_pg_database_for_migrations,
    )

    root = Path(__file__).resolve().parents[2]
    engine = _new_pg_engine()
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        _assert_011_downgrade_guard_preserves_schema(
            root=root,
            engine=engine,
            database_url=TEST_DATABASE_URL,
            fact_kind=fact_kind,
        )
    finally:
        try:
            _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        finally:
            engine.dispose()


def _insert_legacy_active_application(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO ai_applications (
                  application_id, workspace_id, project_id, slug, display_name,
                  owner_principal_ids, criticality, data_classification,
                  governance_mode, lifecycle_state, revision, envelope_payload,
                  record_digest, authority_receipt_id, recorded_by_principal
                ) VALUES (
                  'app_legacy00000001', 'ws_legacy000000001', 'proj_legacy0000001',
                  'legacy-active', 'Legacy active', '[]', 'P2', 'INTERNAL',
                  'MANAGED', 'ACTIVE', 1, '{}', :digest, 'arec_legacy0000001',
                  'prn_legacy00000001'
                )"""
            ),
            {"digest": "sha256:" + "a" * 64},
        )


def _insert_v5_authority_receipt(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO controller_registrations (
                  controller_registration_id, revision, workspace_id,
                  previous_snapshot, state, owner, controller_principal,
                  allowed_commands, ownership_contract_digest,
                  event_catalog_digest, valid_from, expires_at,
                  service_identity_digest, registered_by_human_principal,
                  registration_audit_ref, registered_at, registration_payload,
                  registration_digest
                ) VALUES (
                  'creg_receiptonly01', 1, 'ws_receiptonly0001', NULL, 'ACTIVE',
                  'application-catalog-controller', 'prn_receiptonly001', '[]',
                  :ownership_digest, :catalog_digest, '2026-08-11T00:00:00+00:00',
                  NULL, :service_digest, 'prn_receiptonly002',
                  'audit:receipt-only', '2026-08-11T00:00:00+00:00', '{}',
                  :registration_digest
                )"""
            ),
            {
                "ownership_digest": "sha256:" + "a" * 64,
                "catalog_digest": "sha256:" + "b" * 64,
                "service_digest": "sha256:" + "c" * 64,
                "registration_digest": "sha256:" + "d" * 64,
            },
        )
        connection.execute(
            sa.text(
                """INSERT INTO authority_receipts (
                  authority_receipt_id, workspace_id,
                  controller_registration_id, controller_registration_revision,
                  controller_registration_digest, subject_kind, subject_id,
                  subject_revision, subject_identity_key, subject_digest,
                  resource, owner, controller_principal, command, event_type,
                  event_id, transaction_id, audit_ref, recorded_at,
                  receipt_payload, authority_receipt_digest
                ) VALUES (
                  'arec_receiptonly01', 'ws_receiptonly0001',
                  'creg_receiptonly01', 1, :registration_digest,
                  'AI_APPLICATION', 'app_receiptonly01', 1,
                  'AI_APPLICATION:app_receiptonly01:1', :subject_digest,
                  'AI_APPLICATION', 'application-catalog-controller',
                  'prn_receiptonly001', 'applications.register',
                  'application.registered', 'evt_receiptonly001',
                  'txn_receiptonly001', 'audit:receipt-only',
                  '2026-08-11T00:00:00+00:00', '{}', :receipt_digest
                )"""
            ),
            {
                "registration_digest": "sha256:" + "d" * 64,
                "subject_digest": "sha256:" + "e" * 64,
                "receipt_digest": "sha256:" + "f" * 64,
            },
        )


def _insert_legacy_v5_preflight_fact(
    engine: sa.Engine, fact_kind: str
) -> None:
    if fact_kind == "active_application":
        _insert_legacy_active_application(engine)
        return
    if fact_kind == "authority_receipt":
        _insert_v5_authority_receipt(engine)
        return

    with engine.begin() as connection:
        if fact_kind == "system_version_set":
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
                      'svs_preflightonly01', 'ws_preflightonly01',
                      'app_preflightonly01', 'env_preflightonly01', '[]', '{}',
                      '{}', '[]', :version_digest, NULL, '{}', :record_digest,
                      'arec_preflightonly1', 'prn_preflightonly01'
                    )"""
                ),
                {
                    "version_digest": "sha256:" + "1" * 64,
                    "record_digest": "sha256:" + "2" * 64,
                },
            )
        elif fact_kind == "case_binding":
            connection.execute(
                sa.text(
                    """INSERT INTO application_case_bindings (
                      application_case_binding_id, workspace_id, case_id,
                      case_revision, case_digest, application_id, environment_id,
                      declared_system_version_set_binding_or_unknown,
                      binding_digest, envelope_payload, record_digest,
                      authority_receipt_id, recorded_by_principal
                    ) VALUES (
                      'acb_preflightonly01', 'ws_preflightonly02',
                      'case_preflightonly1', 1, :case_digest,
                      'app_preflightonly02', 'env_preflightonly02', NULL,
                      :binding_digest, '{}', :record_digest,
                      'arec_preflightonly2', 'prn_preflightonly02'
                    )"""
                ),
                {
                    "case_digest": "sha256:" + "3" * 64,
                    "binding_digest": "sha256:" + "4" * 64,
                    "record_digest": "sha256:" + "5" * 64,
                },
            )
        else:
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
                      'acr_preflightonly01', 'ws_preflightonly03',
                      'case_preflightonly3', 1, :case_digest, '{}', 'PROPOSED',
                      'prn_preflightonly03', '2026-08-11T00:00:00+00:00',
                      NULL, NULL, NULL, '{}', NULL, NULL, '{}', NULL, '{}', '{}',
                      :acceptance_digest, '{}', :record_digest,
                      'arec_preflightonly3', 'prn_preflightonly03'
                    )"""
                ),
                {
                    "case_digest": "sha256:" + "6" * 64,
                    "acceptance_digest": "sha256:" + "7" * 64,
                    "record_digest": "sha256:" + "8" * 64,
                },
            )


def _assert_legacy_preflight_preserves_schema(
    *,
    root: Path,
    engine: sa.Engine,
    database_url: str,
    fact_kind: str = "active_application",
) -> None:
    config = _config(root, database_url)
    command.upgrade(config, "010")
    _insert_legacy_v5_preflight_fact(engine, fact_kind)
    before = _schema_fingerprint(engine)

    with pytest.raises(
        RuntimeError,
        match="011.legacy_v5_lifecycle_requires_explicit_recovery",
    ):
        command.upgrade(config, "head")

    assert _schema_fingerprint(engine) == before
    inspector = sa.inspect(engine)
    assert "trust_roles" not in {
        item["name"] for item in inspector.get_columns("public_principals")
    }
    assert "ai_application_lifecycle_revisions" not in inspector.get_table_names()
    with engine.begin() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "010"


@pytest.mark.parametrize(
    "fact_kind",
    [
        "active_application",
        "authority_receipt",
        "system_version_set",
        "case_binding",
        "acceptance_reference",
    ],
)
def test_v5_r1_legacy_preflight_has_zero_partial_schema_mutation(
    tmp_path: Path,
    fact_kind: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / f"r1-legacy-{fact_kind}.sqlite"
    engine = sa.create_engine(f"sqlite:///{database}")
    _assert_legacy_preflight_preserves_schema(
        root=root,
        engine=engine,
        database_url=f"sqlite:///{database}",
        fact_kind=fact_kind,
    )
    engine.dispose()


def _insert_post_011_fact(engine: sa.Engine, fact_kind: str) -> None:
    with engine.begin() as connection:
        if fact_kind == "business":
            connection.execute(
                sa.text(
                    """INSERT INTO ai_applications (
                      application_id, workspace_id, project_id, slug, display_name,
                      owner_principal_ids, criticality, data_classification,
                      governance_mode, lifecycle_state, revision, envelope_payload,
                      record_digest, authority_receipt_id, recorded_by_principal
                    ) VALUES (
                      'app_post011000001', 'ws_post0110000001', 'proj_post01100001',
                      'post-011', 'Post 011', '[]', 'P2', 'INTERNAL', 'MANAGED',
                      'REGISTERED', 1, '{}', :digest, 'arec_post01100001',
                      'prn_post011000001'
                    )"""
                ),
                {"digest": "sha256:" + "b" * 64},
            )
        elif fact_kind == "event":
            connection.execute(
                sa.text(
                    """INSERT INTO events (
                      event_id, aggregate_type, aggregate_id, seq, event_type,
                      payload, causation_id, correlation_id, actor, workspace_id,
                      contract_version, event_version, transaction_id,
                      actor_principal, payload_digest
                    ) VALUES (
                      'evt_post011000001', 'ai_application', 'app_post011000001', 1,
                      'application.registered', '{}', 'none', 'post-011',
                      'application-catalog-controller', 'ws_post0110000001', 'v4',
                      '1.0', 'txn_post011000001', 'prn_post011000001', :digest
                    )"""
                ),
                {"digest": "sha256:" + "c" * 64},
            )
        else:
            connection.execute(
                sa.text(
                    """INSERT INTO outbox (
                      outbox_id, aggregate_id, source_event_id, source_event_seq,
                      channel, event_type, payload, payload_digest, status, attempts,
                      workspace_id, contract_version, aggregate_type, event_version,
                      transaction_id, actor_principal
                    ) VALUES (
                      'out_post011000001', 'app_post011000001',
                      'evt_post011000002', 1, 'v4.domain.events',
                      'application.registered', '{}', :digest, 'PENDING', 0,
                      'ws_post0110000001', 'v4', 'ai_application', '1.0',
                      'txn_post011000002', 'prn_post011000001'
                    )"""
                ),
                {"digest": "sha256:" + "d" * 64},
            )


def _assert_event_preflight_preserves_schema(
    *, root: Path, engine: sa.Engine, database_url: str, fact_kind: str
) -> None:
    config = _config(root, database_url)
    command.upgrade(config, "011")
    _insert_post_011_fact(engine, fact_kind)
    before = _schema_fingerprint(engine)
    with pytest.raises(
        RuntimeError,
        match="012.legacy_v5_event_envelope_requires_explicit_recovery",
    ):
        command.upgrade(config, "013")
    assert _schema_fingerprint(engine) == before
    inspector = sa.inspect(engine)
    assert "event_contract_major" not in {
        item["name"] for item in inspector.get_columns("events")
    }
    assert "event_contract_major" not in {
        item["name"] for item in inspector.get_columns("outbox")
    }
    with engine.begin() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "011"


@pytest.mark.parametrize("fact_kind", ["business", "event", "outbox"])
def test_v5_event_preflight_rejects_post_011_facts_before_first_ddl(
    tmp_path: Path,
    fact_kind: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / f"r1-event-preflight-{fact_kind}.sqlite"
    engine = sa.create_engine(f"sqlite:///{database}")
    _assert_event_preflight_preserves_schema(
        root=root,
        engine=engine,
        database_url=f"sqlite:///{database}",
        fact_kind=fact_kind,
    )
    engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CASELOOP_ALLOW_INTEGRATION_RESET") != "true",
    reason="explicit disposable PostgreSQL reset opt-in required",
)
def test_v5_r1_legacy_preflight_preserves_postgresql_schema_fingerprint() -> None:
    from conftest import (
        TEST_DATABASE_URL,
        _new_pg_engine,
        _reset_pg_database_for_migrations,
    )

    root = Path(__file__).resolve().parents[2]
    engine = _new_pg_engine()
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        _assert_legacy_preflight_preserves_schema(
            root=root,
            engine=engine,
            database_url=TEST_DATABASE_URL,
        )
    finally:
        try:
            _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        finally:
            engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CASELOOP_ALLOW_INTEGRATION_RESET") != "true",
    reason="explicit disposable PostgreSQL reset opt-in required",
)
def test_v5_r1_fresh_postgresql_upgrade_reaches_exact_head() -> None:
    from conftest import (
        TEST_DATABASE_URL,
        _new_pg_engine,
        _reset_pg_database_for_migrations,
    )

    root = Path(__file__).resolve().parents[2]
    engine = _new_pg_engine()
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        command.upgrade(_config(root, TEST_DATABASE_URL), "head")
        inspector = sa.inspect(engine)
        assert "event_contract_major" in {
            item["name"] for item in inspector.get_columns("events")
        }
        assert "event_contract_major" in {
            item["name"] for item in inspector.get_columns("outbox")
        }
        assert {
            "ai_application_lifecycle_revisions",
            "system_component_lifecycle_revisions",
        } <= set(inspector.get_table_names())
        with engine.begin() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "013"
    finally:
        try:
            _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        finally:
            engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CASELOOP_ALLOW_INTEGRATION_RESET") != "true",
    reason="explicit disposable PostgreSQL reset opt-in required",
)
@pytest.mark.parametrize("fact_kind", ["business", "event", "outbox"])
def test_v5_event_preflight_rejects_post_011_postgresql_facts_before_first_ddl(
    fact_kind: str,
) -> None:
    from conftest import (
        TEST_DATABASE_URL,
        _new_pg_engine,
        _reset_pg_database_for_migrations,
    )

    root = Path(__file__).resolve().parents[2]
    engine = _new_pg_engine()
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        _assert_event_preflight_preserves_schema(
            root=root,
            engine=engine,
            database_url=TEST_DATABASE_URL,
            fact_kind=fact_kind,
        )
    finally:
        try:
            _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        finally:
            engine.dispose()


def _insert_v5_event_envelope_fact(engine: sa.Engine, fact_kind: str) -> None:
    with engine.begin() as connection:
        if fact_kind == "event":
            connection.execute(
                sa.text(
                    """INSERT INTO events (
                      event_id, aggregate_type, aggregate_id, seq, event_type,
                      payload, causation_id, correlation_id, actor, workspace_id,
                      contract_version, event_version, transaction_id,
                      actor_principal, payload_digest, event_contract_major,
                      routing_key, exact_subject_binding, authority_receipt_id
                    ) VALUES (
                      'evt_r1event000001', 'ai_application', 'app_r1event000001', 1,
                      'application.registered', '{}', 'none', 'r1-event',
                      'application-catalog-controller', 'ws_r1event0000001', 'v5',
                      '2.0', 'txn_r1event000001', 'prn_r1event000001', :digest, 2,
                      '{}', :binding, 'arec_r1event00001'
                    )"""
                ).bindparams(sa.bindparam("binding", type_=sa.JSON)),
                {
                    "digest": "sha256:" + "e" * 64,
                    "binding": {
                        "kind": "AI_APPLICATION",
                        "id": "app_r1event000001",
                        "revision": 1,
                        "digest": "sha256:" + "f" * 64,
                    },
                },
            )
        else:
            connection.execute(
                sa.text(
                    """INSERT INTO outbox (
                      outbox_id, aggregate_id, source_event_id, source_event_seq,
                      channel, event_type, payload, payload_digest, status, attempts,
                      workspace_id, contract_version, aggregate_type, event_version,
                      transaction_id, actor_principal, event_contract_major
                    ) VALUES (
                      'out_r1event000001', 'app_r1event000001',
                      'evt_r1event000002', 1, 'v5.domain.events',
                      'application.registered', '{}', :digest, 'PENDING', 0,
                      'ws_r1event0000001', 'v5', 'ai_application', '2.0',
                      'txn_r1event000002', 'prn_r1event000001', 2
                    )"""
                ),
                {"digest": "sha256:" + "e" * 64},
            )


def _insert_012_downgrade_guard_fact(
    engine: sa.Engine, fact_kind: str
) -> None:
    if fact_kind in {"event", "outbox"}:
        _insert_v5_event_envelope_fact(engine, fact_kind)
    elif fact_kind in {"lifecycle_history", "trust_roles"}:
        _insert_011_downgrade_guard_fact(engine, fact_kind)
    else:
        _insert_legacy_v5_preflight_fact(engine, fact_kind)


def _assert_012_downgrade_preserves_schema(
    *, root: Path, engine: sa.Engine, database_url: str, fact_kind: str
) -> None:
    config = _config(root, database_url)
    command.upgrade(config, "head")
    _insert_012_downgrade_guard_fact(engine, fact_kind)
    # 013 downgrade is a legal additive-column drop (no history rewrite); the
    # 012 guard then must fail closed and preserve the 012 schema exactly.
    command.downgrade(config, "012")
    before = _schema_fingerprint(engine)
    with pytest.raises(RuntimeError, match="012.v5_r1_history_prevents_downgrade"):
        command.downgrade(config, "010")
    assert _schema_fingerprint(engine) == before
    with engine.begin() as connection:
        assert connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar_one() == "012"


@pytest.mark.parametrize(
    "fact_kind",
    [
        "event",
        "outbox",
        "lifecycle_history",
        "trust_roles",
        "authority_receipt",
        "system_version_set",
        "case_binding",
        "acceptance_reference",
    ],
)
def test_v5_r1_012_downgrade_blocks_persisted_r1_fact(
    tmp_path: Path,
    fact_kind: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / f"r1-event-downgrade-{fact_kind}.sqlite"
    engine = sa.create_engine(f"sqlite:///{database}")
    _assert_012_downgrade_preserves_schema(
        root=root,
        engine=engine,
        database_url=f"sqlite:///{database}",
        fact_kind=fact_kind,
    )
    engine.dispose()


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("CASELOOP_ALLOW_INTEGRATION_RESET") != "true",
    reason="explicit disposable PostgreSQL reset opt-in required",
)
@pytest.mark.parametrize(
    "fact_kind",
    [
        "event",
        "outbox",
        "lifecycle_history",
        "trust_roles",
        "authority_receipt",
    ],
)
def test_v5_r1_012_postgresql_downgrade_preserves_schema(
    fact_kind: str,
) -> None:
    from conftest import (
        TEST_DATABASE_URL,
        _new_pg_engine,
        _reset_pg_database_for_migrations,
    )

    root = Path(__file__).resolve().parents[2]
    engine = _new_pg_engine()
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        _assert_012_downgrade_preserves_schema(
            root=root,
            engine=engine,
            database_url=TEST_DATABASE_URL,
            fact_kind=fact_kind,
        )
    finally:
        try:
            _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        finally:
            engine.dispose()
