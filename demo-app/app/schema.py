"""Migration-state checks and read-only legacy-schema adoption verification."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Engine, Integer, create_engine, inspect, text

DEMO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = DEMO_ROOT / "alembic.ini"
ALEMBIC_DIR = DEMO_ROOT / "alembic"


class SchemaNotReadyError(RuntimeError):
    """The database cannot safely serve this application image."""


class SchemaAdoptionError(RuntimeError):
    """An unversioned legacy schema is not an exact adoption candidate."""


def alembic_config() -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_DIR))
    return config


def expected_heads() -> tuple[str, ...]:
    return tuple(ScriptDirectory.from_config(alembic_config()).get_heads())


def require_current_schema(bind: Engine) -> None:
    """Fail startup unless the database is exactly at this image's Alembic head."""

    expected = expected_heads()
    with bind.connect() as connection:
        current = tuple(MigrationContext.configure(connection).get_current_heads())
    if set(current) != set(expected):
        raise SchemaNotReadyError(
            "demo-app schema is not at the required Alembic head "
            f"(current={current or ('unversioned',)}, expected={expected})"
        )


def compare_schema_type(
    context: MigrationContext,
    inspected_column: Any,
    metadata_column: Any,
    inspected_type: Any,
    metadata_type: Any,
) -> bool | None:
    # SQLite reflects pgvector's VECTOR(1024) affinity as NUMERIC. This branch
    # exists only so the read-only verifier can be unit-tested without claiming
    # PostgreSQL extension evidence.
    if context.dialect.name == "sqlite":
        if isinstance(metadata_type, Vector):
            return False
        if isinstance(metadata_type, BigInteger) and isinstance(
            inspected_type, Integer
        ):
            return False
    return None


def _summarize_diff(diff: Any) -> str:
    if isinstance(diff, tuple) and diff:
        return str(diff[0])
    if isinstance(diff, list) and diff:
        return _summarize_diff(diff[0])
    return type(diff).__name__


def verify_unversioned_schema_for_adoption(database_url: str) -> None:
    """Verify an exact legacy schema without modifying or stamping it.

    This is intentionally stricter than ``alembic stamp``. It rejects an
    already-versioned database, a missing PostgreSQL vector extension, and any
    model/schema drift. A successful return only authorizes a human-controlled
    explicit stamp; it never performs that stamp itself.
    """

    from app import models  # noqa: F401  register all model tables
    from app.db import Base

    verification_engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with verification_engine.connect() as connection:
            inspector = inspect(connection)
            if inspector.has_table("alembic_version"):
                raise SchemaAdoptionError(
                    "database is already Alembic-versioned; use normal migration checks"
                )

            if connection.dialect.name == "postgresql":
                has_vector = connection.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
                ).scalar_one_or_none()
                if has_vector is None:
                    raise SchemaAdoptionError(
                        "PostgreSQL vector extension is missing; adoption refused"
                    )

            context = MigrationContext.configure(
                connection,
                opts={
                    "compare_type": compare_schema_type,
                    "target_metadata": Base.metadata,
                },
            )
            differences = compare_metadata(context, Base.metadata)
            if differences:
                kinds = sorted({_summarize_diff(diff) for diff in differences})
                raise SchemaAdoptionError(
                    "legacy schema differs from the frozen initial migration "
                    f"(difference_kinds={','.join(kinds)})"
                )
    finally:
        verification_engine.dispose()
