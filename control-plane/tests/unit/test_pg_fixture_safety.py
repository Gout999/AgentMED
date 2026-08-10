"""Fail-closed guards for destructive PostgreSQL integration fixtures."""
from __future__ import annotations

from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa

import conftest as fixtures


class _Result:
    def __init__(self, value: str) -> None:
        self._value = value

    def scalar_one(self) -> str:
        return self._value


class _Transaction:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _Connection:
    def __init__(self, current_database: str, *, query_error: Exception | None = None) -> None:
        self.current_database = current_database
        self.query_error = query_error
        self.statements: list[str] = []
        self.transaction = _Transaction()
        self.closed = False

    def begin(self) -> _Transaction:
        return self.transaction

    def execute(self, statement: object) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if self.query_error is not None:
            raise self.query_error
        if "current_database()" in sql:
            return _Result(self.current_database)
        return _Result("")

    def close(self) -> None:
        self.closed = True


class _Engine:
    def __init__(
        self,
        url: str,
        *,
        current_database: str | None = None,
        connect_error: Exception | None = None,
        query_error: Exception | None = None,
    ) -> None:
        self.url = sa.engine.make_url(url)
        self.current_database = current_database or self.url.database or ""
        self.connect_error = connect_error
        self.query_error = query_error
        self.connections: list[_Connection] = []
        self.connect_calls = 0

    def connect(self) -> _Connection:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        connection = _Connection(self.current_database, query_error=self.query_error)
        self.connections.append(connection)
        return connection


def _assert_refused(
    code: str,
    url: str,
    *,
    environ: dict[str, str] | None = None,
    engine: _Engine | None = None,
) -> _Engine:
    target = engine or _Engine(url)
    with pytest.raises(fixtures.UnsafeIntegrationDatabaseError, match=f"^{code}$"):
        fixtures._assert_pg_reset_safe(url, target, environ=environ or {})
    return target


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://user:password@127.0.0.1/control_plane",
        "postgresql+psycopg://user:password@127.0.0.1/contest",
        "postgresql+psycopg://user:password@127.0.0.1/scratchpad",
        "postgresql+psycopg://user:password@127.0.0.1/",
    ],
)
def test_reset_rejects_database_without_explicit_test_or_scratch_token_before_connect(url: str) -> None:
    engine = _assert_refused(
        "caseloop.integration_reset.refused.unsafe_database_name",
        url,
        environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
    )
    assert engine.connect_calls == 0


@pytest.mark.parametrize("value", [None, "", "1", "TRUE", "yes", " true"])
def test_reset_requires_exact_explicit_opt_in_before_connect(value: str | None) -> None:
    environ = {} if value is None else {"CASELOOP_ALLOW_INTEGRATION_RESET": value}
    engine = _assert_refused(
        "caseloop.integration_reset.refused.opt_in_required",
        "postgresql+psycopg://user:password@127.0.0.1/control_plane_test",
        environ=environ,
    )
    assert engine.connect_calls == 0


def test_reset_rejects_non_postgresql_and_malformed_urls_without_connecting() -> None:
    sqlite_engine = _assert_refused(
        "caseloop.integration_reset.refused.postgresql_required",
        "sqlite:///control_plane_test",
        environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
    )
    assert sqlite_engine.connect_calls == 0

    malformed_engine = Mock()
    with pytest.raises(
        fixtures.UnsafeIntegrationDatabaseError,
        match="^caseloop.integration_reset.refused.invalid_database_url$",
    ):
        fixtures._assert_pg_reset_safe(
            "not a database url",
            malformed_engine,
            environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
        )
    malformed_engine.connect.assert_not_called()


def test_reset_rejects_engine_target_mismatch_before_connect() -> None:
    engine = _Engine("postgresql+psycopg://user:password@127.0.0.1/other_test")
    _assert_refused(
        "caseloop.integration_reset.refused.engine_database_mismatch",
        "postgresql+psycopg://user:password@127.0.0.1/control_plane_test",
        environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
        engine=engine,
    )
    assert engine.connect_calls == 0


def test_reset_rejects_connected_database_mismatch() -> None:
    url = "postgresql+psycopg://user:password@127.0.0.1/control_plane_test"
    engine = _Engine(url, current_database="control_plane")
    _assert_refused(
        "caseloop.integration_reset.refused.current_database_mismatch",
        url,
        environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
        engine=engine,
    )
    assert engine.connect_calls == 1
    assert engine.connections[0].transaction.rolled_back is True
    assert engine.connections[0].closed is True


def test_reset_connection_failure_is_a_stable_refusal_not_a_skip() -> None:
    url = "postgresql+psycopg://user:password@127.0.0.1/control_plane_test"
    engine = _Engine(url, connect_error=ConnectionError("host unavailable"))
    error = _assert_refused(
        "caseloop.integration_reset.refused.database_unreachable",
        url,
        environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
        engine=engine,
    )
    assert error.connect_calls == 1


def test_current_database_query_failure_is_stable_and_suppresses_driver_details() -> None:
    password = "driver-must-not-leak-this-password"
    url = f"postgresql+psycopg://user:{password}@127.0.0.1/control_plane_test"
    engine = _Engine(url, query_error=RuntimeError(password))
    with pytest.raises(fixtures.UnsafeIntegrationDatabaseError) as error:
        fixtures._assert_pg_reset_safe(
            url,
            engine,
            environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
        )
    assert str(error.value) == (
        "caseloop.integration_reset.refused.current_database_unavailable"
    )
    assert error.value.__cause__ is None
    assert password not in str(error.value)
    assert engine.connections[0].transaction.rolled_back is True
    assert engine.connections[0].closed is True


@pytest.mark.parametrize(
    "database",
    ["control_plane_test", "test_control_plane", "stage1_scratch", "scratch_stage1"],
)
def test_reset_accepts_explicit_disposable_name_and_validates_current_database(database: str) -> None:
    url = f"postgresql+psycopg://user:password@127.0.0.1/{database}"
    engine = _Engine(url)
    assert (
        fixtures._assert_pg_reset_safe(
            url,
            engine,
            environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
        )
        == database
    )
    assert engine.connect_calls == 1
    connection = engine.connections[0]
    assert connection.statements == ["SELECT current_database()"]
    assert connection.transaction.committed is True
    assert connection.closed is True


def test_refusal_messages_never_echo_credentials() -> None:
    password = "do-not-leak-this-password"
    url = f"postgresql+psycopg://user:{password}@127.0.0.1/production"
    engine = _Engine(url)
    with pytest.raises(fixtures.UnsafeIntegrationDatabaseError) as error:
        fixtures._assert_pg_reset_safe(
            url,
            engine,
            environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
        )
    assert password not in str(error.value)


def test_metadata_reset_validates_before_each_destructive_operation() -> None:
    url = "postgresql+psycopg://user:password@127.0.0.1/control_plane_test"
    engine = _Engine(url)
    environ = {"CASELOOP_ALLOW_INTEGRATION_RESET": "true"}

    with (
        patch.object(fixtures.Base.metadata, "drop_all") as drop_all,
        patch.object(fixtures.Base.metadata, "create_all") as create_all,
    ):
        fixtures._reset_pg_metadata(engine, url, environ=environ)

    assert engine.connect_calls == 2
    drop_all.assert_called_once_with(bind=engine.connections[0])
    create_all.assert_called_once_with(bind=engine.connections[1])
    assert all(
        connection.statements[0] == "SELECT current_database()"
        for connection in engine.connections
    )


def test_migration_reset_validates_on_same_connection_before_schema_reset() -> None:
    url = "postgresql+psycopg://user:password@127.0.0.1/stage1_scratch"
    engine = _Engine(url)
    fixtures._reset_pg_database_for_migrations(
        engine,
        url,
        environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
    )

    assert engine.connect_calls == 1
    assert engine.connections[0].statements == [
        "SELECT current_database()",
        "DROP SCHEMA IF EXISTS public CASCADE",
        "CREATE SCHEMA public",
    ]


def test_unsafe_migration_reset_executes_no_schema_statement() -> None:
    url = "postgresql+psycopg://user:password@127.0.0.1/control_plane"
    engine = _Engine(url)
    with pytest.raises(
        fixtures.UnsafeIntegrationDatabaseError,
        match="^caseloop.integration_reset.refused.unsafe_database_name$",
    ):
        fixtures._reset_pg_database_for_migrations(
            engine,
            url,
            environ={"CASELOOP_ALLOW_INTEGRATION_RESET": "true"},
        )
    assert engine.connections == []
