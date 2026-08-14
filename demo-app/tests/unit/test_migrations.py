"""Focused deployment-path tests for demo-app Alembic migrations."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  register metadata
from app.db import Base
from app.models import KBEntry, PromptVersion, TransitionRecord, VersionSet
from app.schema import (
    SchemaAdoptionError,
    SchemaNotReadyError,
    require_current_schema,
    verify_unversioned_schema_for_adoption,
)
from app.seeding import seed_app_state

DEMO_ROOT = Path(__file__).resolve().parents[2]
EXPECTED_TABLES = set(Base.metadata.tables)


def _database_url(path: Path) -> str:
    return f"sqlite:///{path}"


def _config(database_url: str) -> Config:
    config = Config(str(DEMO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(DEMO_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _upgrade(database_url: str) -> None:
    command.upgrade(_config(database_url), "head")


def test_empty_database_upgrade_and_repeated_startup_are_idempotent(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path / "empty.db")
    _upgrade(database_url)
    command.check(_config(database_url))
    engine = create_engine(database_url)
    try:
        assert set(inspect(engine).get_table_names()) == EXPECTED_TABLES | {
            "alembic_version"
        }
        require_current_schema(engine)

        Session = sessionmaker(bind=engine, expire_on_commit=False)
        with Session() as session:
            seed_app_state(session)
            first_counts = (
                len(session.scalars(select(PromptVersion)).all()),
                len(session.scalars(select(KBEntry)).all()),
                len(session.scalars(select(VersionSet)).all()),
                len(session.scalars(select(TransitionRecord)).all()),
            )

        # A restarted container executes upgrade + schema check + seeding again.
        _upgrade(database_url)
        require_current_schema(engine)
        with Session() as session:
            seed_app_state(session)
            second_counts = (
                len(session.scalars(select(PromptVersion)).all()),
                len(session.scalars(select(KBEntry)).all()),
                len(session.scalars(select(VersionSet)).all()),
                len(session.scalars(select(TransitionRecord)).all()),
            )
        assert first_counts == second_counts
        assert first_counts[0] > 0
        assert first_counts[1] > 0
        assert first_counts[2:] == (2, 1)
    finally:
        engine.dispose()


def test_startup_rejects_unversioned_schema(tmp_path: Path) -> None:
    engine = create_engine(_database_url(tmp_path / "unversioned.db"))
    try:
        Base.metadata.create_all(engine)
        with pytest.raises(SchemaNotReadyError, match="unversioned"):
            require_current_schema(engine)
    finally:
        engine.dispose()


def test_initial_migration_downgrade_removes_application_tables(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path / "downgrade.db")
    config = _config(database_url)
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    engine = create_engine(database_url)
    try:
        assert not (set(inspect(engine).get_table_names()) & EXPECTED_TABLES)
    finally:
        engine.dispose()


def test_adoption_verifier_is_read_only_for_exact_legacy_schema(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path / "legacy-exact.db")
    engine = create_engine(database_url)
    try:
        Base.metadata.create_all(engine)
        before = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    environment = dict(os.environ)
    environment["DATABASE_URL"] = database_url
    result = subprocess.run(
        [sys.executable, str(DEMO_ROOT / "scripts" / "verify_schema_adoption.py")],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "no stamp was performed" in result.stdout

    engine = create_engine(database_url)
    try:
        after = set(inspect(engine).get_table_names())
        assert before == after == EXPECTED_TABLES
        assert "alembic_version" not in after
    finally:
        engine.dispose()


def test_adoption_verifier_rejects_schema_drift(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path / "legacy-drift.db")
    engine = create_engine(database_url)
    try:
        Base.metadata.create_all(engine)
        VersionSet.__table__.drop(engine)
    finally:
        engine.dispose()

    with pytest.raises(SchemaAdoptionError, match="differs"):
        verify_unversioned_schema_for_adoption(database_url)


def test_adoption_verifier_rejects_already_versioned_database(tmp_path: Path) -> None:
    database_url = _database_url(tmp_path / "versioned.db")
    _upgrade(database_url)

    with pytest.raises(SchemaAdoptionError, match="already Alembic-versioned"):
        verify_unversioned_schema_for_adoption(database_url)
