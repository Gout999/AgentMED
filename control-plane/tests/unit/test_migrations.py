"""Migration-chain regression checks for the documented local workflow."""
from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config


def test_sqlite_upgrade_head_allows_multi_stage_gate_binding(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "control.sqlite"
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
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
            == "006"
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
    engine.dispose()
