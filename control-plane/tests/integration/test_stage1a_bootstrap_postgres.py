"""Real PostgreSQL proof for the local Stage 1A bootstrap.

Run serially against the explicitly disposable test database.  The shared
reset helper requires ``CASELOOP_ALLOW_INTEGRATION_RESET=true`` and validates
``current_database()`` before setup and cleanup.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.orm import sessionmaker

from app.bootstrap.stage1a_local import (
    Stage1ALocalBootstrapRequest,
    execute_stage1a_local_bootstrap,
)
from app.config import Settings
from app.models.tables import Base
from app.models.v4_tables import ControllerRegistration
from app.services.authority import AuthorityService
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from conftest import (
    TEST_DATABASE_URL,
    _new_pg_engine,
    _reset_pg_database_for_migrations,
)


pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
RAW_BEARER = "pg-bootstrap-bearer-0123456789-DO-NOT-LOG"
RAW_JTI = "pg-bootstrap-jti-0123456789"


def _request() -> Stage1ALocalBootstrapRequest:
    return Stage1ALocalBootstrapRequest.model_validate(
        {
            "schema_version": "1.0",
            "workspace_id": "ws_01J0000000000091",
            "project_id": "proj_01J0000000000091",
            "environment_id": "env_01J0000000000091",
            "source": {
                "source_id": "src_01J0000000000091",
                "connector_kind": "manual",
                "state": "ACTIVE",
                "credential_ref": None,
                "config": {"display_name": "PostgreSQL bootstrap proof"},
            },
            "principal": {
                "principal_id": "prn_01J0000000000091",
                "subject": "pg-maintainer-01J0000000000091",
            },
            "credential": {
                "credential_id": "cred_01J0000000000091",
                "bearer_token": RAW_BEARER,
                "jti": RAW_JTI,
                "issued_at": "2026-08-10T11:59:00Z",
                "not_before": "2026-08-10T11:59:00Z",
                "expires_at": "2027-08-10T12:00:00Z",
            },
            "controllers": {
                "signal": {
                    "registration_id": "creg_01J0000000000091",
                    "principal_id": "prn_01J0000000000191",
                },
                "case": {
                    "registration_id": "creg_01J0000000000092",
                    "principal_id": "prn_01J0000000000192",
                },
                "evidence": {
                    "registration_id": "creg_01J0000000000093",
                    "principal_id": "prn_01J0000000000193",
                },
            },
            "secret_storage_ref": (
                "keyring://caseloop/local/ws_01J0000000000091"
            ),
        }
    )


def _settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        public_credential_hash_pepper=SecretStr(
            "pg-bootstrap-public-pepper-that-is-independent"
        ),
        public_cursor_signing_key=SecretStr(
            "pg-bootstrap-cursor-signing-key-that-is-independent"
        ),
        public_auth_issuer="https://auth.caseloop.dev",
        require_mcp_role_tokens=False,
    )


def _alembic_config(root: Path) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def test_stage1a_local_bootstrap_real_postgres_head_and_atomic_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[2]
    engine = _new_pg_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    request = _request()
    settings = _settings()
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    reset_complete = False

    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        reset_complete = True
        with patch.object(
            Base.metadata,
            "create_all",
            side_effect=AssertionError("stage1a.bootstrap_must_not_use_create_all"),
        ):
            command.upgrade(_alembic_config(root), "head")

            with factory() as session:
                first = execute_stage1a_local_bootstrap(
                    session, request, settings=settings, now=NOW
                )
                session.commit()
            assert first.status == "CREATED"
            assert RAW_BEARER not in first.model_dump_json()
            assert RAW_JTI not in first.model_dump_json()

            with factory() as session:
                assert session.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).scalar_one() == "008"
                assert session.execute(
                    sa.text("SELECT current_database()")
                ).scalar_one().endswith("_test")
                assert session.execute(
                    sa.text("SELECT COUNT(*) FROM source_connections")
                ).scalar_one() == 1
                assert session.execute(
                    sa.text("SELECT COUNT(*) FROM public_principals")
                ).scalar_one() == 1
                assert session.execute(
                    sa.text("SELECT COUNT(*) FROM public_credentials")
                ).scalar_one() == 1
                assert session.execute(
                    sa.text("SELECT COUNT(*) FROM controller_registrations")
                ).scalar_one() == 3
                assert session.execute(
                    sa.text("SELECT COUNT(*) FROM audit")
                ).scalar_one() == 4
                credential_hash, jti_digest = session.execute(
                    sa.text(
                        "SELECT credential_hash, jti_digest FROM public_credentials"
                    )
                ).one()
                assert RAW_BEARER not in credential_hash
                assert RAW_JTI not in jti_digest
                assert len(
                    list(session.scalars(sa.select(ControllerRegistration)).all())
                ) == 3
                assert (
                    AuthorityService(session)
                    .resolve_controller(
                        workspace_id=request.workspace_id,
                        subject_kind="SIGNAL_RECORD",
                        command="signals.submit",
                        event_type="signal.received",
                        recorded_at=NOW,
                    )
                    .controller_principal
                    == request.controllers.signal.principal_id
                )
                session.rollback()

            with factory() as session:
                second = execute_stage1a_local_bootstrap(
                    session, request, settings=settings, now=NOW
                )
                session.commit()
            assert second.status == "REUSED"

            with factory() as session:
                failing_audit = V4AuditService(
                    session, clock=lambda: NOW, fail_on_call=1
                )
                with pytest.raises(V4AuditUnavailable, match="AUDIT_UNAVAILABLE"):
                    execute_stage1a_local_bootstrap(
                        session,
                        request,
                        settings=settings,
                        now=NOW,
                        audit_service=failing_audit,
                    )
                session.rollback()

            with factory() as session:
                # Three immutable registration audits + two successful command
                # audits.  The forced third command-audit failure left no row.
                assert session.execute(
                    sa.text("SELECT COUNT(*) FROM audit")
                ).scalar_one() == 5
                assert session.execute(
                    sa.text("SELECT COUNT(*) FROM controller_registrations")
                ).scalar_one() == 3
                session.rollback()
    finally:
        try:
            if reset_complete:
                _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        finally:
            engine.dispose()
