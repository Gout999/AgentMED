"""Executable 006 -> 007 Stage 1A schema compatibility checks."""
from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError


NEW_TABLES = {
    "source_connections",
    "public_principals",
    "public_credentials",
    "public_command_idempotency",
    "signal_contents",
    "signals",
    "quality_cases",
    "signal_case_links",
    "agent_run_refs",
    "trace_evidence_receipts",
    "controller_registrations",
    "authority_receipts",
}

LEGACY_V4_COLUMNS = {
    "aggregates": {"contract_version", "workspace_id", "transaction_id", "actor_principal", "record_digest"},
    "events": {"contract_version", "workspace_id", "event_version", "transaction_id", "actor_principal", "payload_digest"},
    "audit": {"contract_version", "workspace_id", "transaction_id", "actor_principal", "audit_digest"},
    "outbox": {"contract_version", "workspace_id", "aggregate_type", "event_version", "transaction_id", "actor_principal"},
}


def _config(root: Path, database: Path) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    return config


def _engine(database: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{database}")

    @sa.event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record) -> None:  # type: ignore[no-untyped-def]
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def test_upgrade_006_to_007_is_additive_and_preserves_legacy_rows(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "stage1a.sqlite"
    config = _config(root, database)
    command.upgrade(config, "006")
    engine = _engine(database)

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO aggregates (aggregate_type, aggregate_id, state, payload, revision) "
                "VALUES ('case', 'legacy-case', 'OPEN', '{}', 1)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO events (event_id, aggregate_type, aggregate_id, seq, event_type, payload, "
                "causation_id, correlation_id, actor) VALUES "
                "('evt-legacy', 'case', 'legacy-case', 1, 'CASE_OPENED', '{}', 'none', 'legacy-case', 'system')"
            )
        )
    engine.dispose()

    command.upgrade(config, "007")
    engine = _engine(database)
    inspector = sa.inspect(engine)
    assert NEW_TABLES <= set(inspector.get_table_names())
    for table, expected in LEGACY_V4_COLUMNS.items():
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert expected <= set(columns)
        assert all(columns[name]["nullable"] for name in expected)

    signal_uniques = {
        tuple(item["column_names"]) for item in inspector.get_unique_constraints("signals")
    }
    idempotency_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("public_command_idempotency")
    }
    authority_uniques = {
        tuple(item["column_names"])
        for item in inspector.get_unique_constraints("authority_receipts")
    }
    assert ("workspace_id", "source_id", "source_event_id") in signal_uniques
    assert ("workspace_id", "principal_id", "intent", "idempotency_key") in idempotency_uniques
    assert ("workspace_id", "subject_identity_key") in authority_uniques
    authority_checks = {
        item["name"] for item in inspector.get_check_constraints("authority_receipts")
    }
    assert "ck_authority_receipt_subject_identity_key" in authority_checks

    with engine.begin() as connection:
        legacy = connection.execute(
            sa.text(
                "SELECT contract_version, workspace_id, transaction_id, actor_principal, record_digest "
                "FROM aggregates WHERE aggregate_id='legacy-case'"
            )
        ).one()
        assert tuple(legacy) == (None, None, None, None, None)
        assert connection.execute(sa.text("SELECT COUNT(*) FROM events WHERE event_id='evt-legacy'")).scalar_one() == 1
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "007"
    engine.dispose()


def test_v4_event_identity_is_workspace_and_aggregate_type_scoped_without_weakening_v3(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "events.sqlite"
    config = _config(root, database)
    command.upgrade(config, "head")
    engine = _engine(database)

    legacy = {
        "aggregate_type": "case",
        "aggregate_id": "shared-id",
        "seq": 1,
        "event_type": "CASE_OPENED",
        "payload": "{}",
        "causation_id": "none",
        "correlation_id": "shared-id",
        "actor": "system",
    }
    v4 = {
        "contract_version": "v4",
        "workspace_id": "ws_01J0000000000001",
        "event_version": "1.0",
        "transaction_id": "txn_01J0000000000001",
        "actor_principal": "prn_01J0000000000001",
        "payload_digest": _digest("a"),
        "aggregate_type": "signal",
        "aggregate_id": "sig_01J0000000000001",
        "seq": 1,
        "event_type": "signal.received",
        "payload": "{}",
        "causation_id": "cmd_01J0000000000001",
        "correlation_id": "case_01J0000000000001",
        "actor": "signal-controller",
    }
    statement = sa.text(
        """INSERT INTO events (
          event_id, aggregate_type, aggregate_id, seq, event_type, payload,
          causation_id, correlation_id, actor, contract_version, workspace_id,
          event_version, transaction_id, actor_principal, payload_digest
        ) VALUES (
          :event_id, :aggregate_type, :aggregate_id, :seq, :event_type, :payload,
          :causation_id, :correlation_id, :actor, :contract_version, :workspace_id,
          :event_version, :transaction_id, :actor_principal, :payload_digest
        )"""
    )
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO events (
                  event_id, aggregate_type, aggregate_id, seq, event_type, payload,
                  causation_id, correlation_id, actor
                ) VALUES (
                  :event_id, :aggregate_type, :aggregate_id, :seq, :event_type, :payload,
                  :causation_id, :correlation_id, :actor
                )"""
            ),
            {**legacy, "event_id": "evt-legacy-one"},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    """INSERT INTO events (
                      event_id, aggregate_type, aggregate_id, seq, event_type, payload,
                      causation_id, correlation_id, actor
                    ) VALUES (
                      :event_id, :aggregate_type, :aggregate_id, :seq, :event_type, :payload,
                      :causation_id, :correlation_id, :actor
                    )"""
                ),
                {**legacy, "event_id": "evt-legacy-two", "aggregate_type": "other"},
            )
    # The failed statement ended that transaction on PostgreSQL, so use clean
    # transactions for the v4 assertions as well.
    with engine.begin() as connection:
        connection.execute(statement, {**v4, "event_id": "evt-v4-one"})
        connection.execute(
            statement,
            {
                **v4,
                "event_id": "evt-v4-two",
                "workspace_id": "ws_01J0000000000002",
            },
        )
        connection.execute(
            statement,
            {
                **v4,
                "event_id": "evt-v4-three",
                "aggregate_type": "quality_case",
            },
        )
        with pytest.raises(IntegrityError):
            connection.execute(statement, {**v4, "event_id": "evt-v4-duplicate"})
    engine.dispose()


def test_v4_conditional_constraints_and_manual_source_guard_fail_closed(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "constraints.sqlite"
    config = _config(root, database)
    command.upgrade(config, "head")
    engine = _engine(database)

    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    """INSERT INTO events (
                      event_id, aggregate_type, aggregate_id, seq, event_type, payload,
                      causation_id, correlation_id, actor, contract_version
                    ) VALUES ('evt-v4-incomplete', 'signal', 'sig-bad', 1, 'signal.received',
                      '{}', 'cmd-bad', 'case-bad', 'signal-controller', 'v4')"""
                )
            )
    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    """INSERT INTO outbox (
                      outbox_id, aggregate_id, source_event_id, source_event_seq,
                      channel, event_type, payload, payload_digest, status, attempts,
                      contract_version, workspace_id, aggregate_type, event_version,
                      transaction_id, actor_principal
                    ) VALUES (
                      'obx-v4-wrong-channel', 'sig_01J0000000000001', 'evt-v4-outbox', 1,
                      'feishu', 'signal.received', '{}', :digest, 'PENDING', 0,
                      'v4', 'ws_01J0000000000001', 'signal', '1.0',
                      'txn_01J0000000000001', 'prn_01J0000000000001'
                    )"""
                ),
                {"digest": _digest("f")},
            )
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO source_connections (
                  source_id, workspace_id, connector_kind, state,
                  credential_ref, config, connection_digest, revision, created_by_principal
                ) VALUES (
                  'src_01J0000000000001', 'ws_01J0000000000001', 'manual', 'ACTIVE',
                  NULL, '{}', :digest, 1, 'prn_01J0000000000001'
                )"""
            ),
            {"digest": _digest("1")},
        )
        with pytest.raises(IntegrityError):
            connection.execute(
                sa.text(
                    """INSERT INTO source_connections (
                      source_id, workspace_id, connector_kind, state,
                      credential_ref, config, connection_digest, revision, created_by_principal
                    ) VALUES (
                      'src_01J0000000000002', 'ws_01J0000000000001', 'langfuse', 'ACTIVE',
                      'secret://forbidden-in-007', '{}', :digest, 1, 'prn_01J0000000000001'
                    )"""
                ),
                {"digest": _digest("2")},
            )
    engine.dispose()


def test_public_auth_schema_persists_hashes_not_raw_bearer_or_jti(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    database = tmp_path / "auth.sqlite"
    config = _config(root, database)
    command.upgrade(config, "head")
    inspector = sa.inspect(_engine(database))

    credential_columns = {column["name"] for column in inspector.get_columns("public_credentials")}
    principal_columns = {column["name"] for column in inspector.get_columns("public_principals")}
    assert {"credential_hash", "jti_digest", "hash_algorithm"} <= credential_columns
    forbidden = {"bearer", "bearer_token", "raw_bearer", "token", "raw_token", "jti", "raw_jti", "secret"}
    assert forbidden.isdisjoint(credential_columns | principal_columns)


def test_empty_007_can_downgrade_but_immutable_records_block_with_stable_error(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]

    empty_database = tmp_path / "empty.sqlite"
    empty_config = _config(root, empty_database)
    command.upgrade(empty_config, "007")
    command.downgrade(empty_config, "006")
    empty_engine = _engine(empty_database)
    assert NEW_TABLES.isdisjoint(sa.inspect(empty_engine).get_table_names())
    with empty_engine.begin() as connection:
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "006"
    empty_engine.dispose()

    populated_database = tmp_path / "populated.sqlite"
    populated_config = _config(root, populated_database)
    command.upgrade(populated_config, "007")
    populated_engine = _engine(populated_database)
    with populated_engine.begin() as connection:
        connection.execute(
            sa.text(
                """INSERT INTO signal_contents (
                  signal_content_id, workspace_id, uri, media_type, content_digest,
                  content_payload, privacy_classification, redaction_status,
                  raw_content_persisted
                ) VALUES (
                  'sigc_01J0000000000001', 'ws_01J0000000000001',
                  'agentmed-artifact://signal/content', 'application/json', :digest,
                  '{}', 'INTERNAL', 'NOT_REQUIRED', 1
                )"""
            ),
            {"digest": _digest("3")},
        )
    populated_engine.dispose()

    with pytest.raises(
        RuntimeError, match=r"^007\.downgrade_blocked\.immutable_v4_records_exist$"
    ):
        command.downgrade(populated_config, "006")


def test_v4_orm_metadata_contains_migration_tables_and_immutable_guard() -> None:
    from app.models.tables import Base
    from app.models.v4_tables import SignalContent

    assert NEW_TABLES <= set(Base.metadata.tables)
    assert hasattr(Base.metadata.tables["events"].c, "workspace_id")

    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with sa.orm.Session(engine) as session:
        row = SignalContent(
            signal_content_id="sigc_01J0000000000001",
            workspace_id="ws_01J0000000000001",
            uri="agentmed-artifact://signal/content",
            media_type="application/json",
            content_digest=_digest("4"),
            content_payload={"summary": "sealed"},
            privacy_classification="INTERNAL",
            redaction_status="NOT_REQUIRED",
            raw_content_persisted=True,
        )
        session.add(row)
        session.commit()
        row.content_payload = {"summary": "tampered"}
        with pytest.raises(RuntimeError, match=r"v4\.immutable_record_update_forbidden"):
            session.commit()
    engine.dispose()
