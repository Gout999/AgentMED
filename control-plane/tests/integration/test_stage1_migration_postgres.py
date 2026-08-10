"""Real PostgreSQL proof for the Stage 1 additive Alembic path.

Run serially against an explicitly disposable database only.  The shared reset
guard requires ``CASELOOP_ALLOW_INTEGRATION_RESET=true`` and verifies
``current_database()`` before either setup or cleanup.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.models.tables import Base
from conftest import (
    TEST_DATABASE_URL,
    _new_pg_engine,
    _reset_pg_database_for_migrations,
)


pytestmark = pytest.mark.integration


def _alembic_config(root: Path) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def test_stage1_upgrade_006_to_007_and_head_on_real_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    root = Path(__file__).resolve().parents[2]
    engine = _new_pg_engine()
    config = _alembic_config(root)
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    reset_complete = False

    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        reset_complete = True
        # A Stage 1 migration proof must exercise Alembic.  If the path tries
        # to substitute ORM metadata creation, fail immediately.
        with patch.object(
            Base.metadata,
            "create_all",
            side_effect=AssertionError("stage1.pg_migration_must_not_use_create_all"),
        ):
            command.upgrade(config, "006")
            with engine.begin() as connection:
                assert connection.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalar_one() == "006"
                connection.execute(
                    sa.text(
                        "INSERT INTO aggregates "
                        "(aggregate_type, aggregate_id, state, payload, revision) "
                        "VALUES ('case', 'legacy-stage1-pg', 'OPEN', '{}', 1)"
                    )
                )

            command.upgrade(config, "007")
            with engine.begin() as connection:
                assert connection.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalar_one() == "007"

            command.upgrade(config, "head")

        head = ScriptDirectory.from_config(config).get_current_head()
        assert head is not None
        inspector = sa.inspect(engine)
        assert {
            "signals",
            "quality_cases",
            "trace_evidence_receipts",
            "authority_receipts",
        } <= set(inspector.get_table_names())
        with engine.begin() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == head
            assert connection.execute(
                sa.text(
                    "SELECT COUNT(*) FROM aggregates "
                    "WHERE aggregate_type='case' AND aggregate_id='legacy-stage1-pg'"
                )
            ).scalar_one() == 1
    finally:
        try:
            if reset_complete:
                _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        finally:
            engine.dispose()
