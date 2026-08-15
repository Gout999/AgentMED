"""Real PostgreSQL concurrency gate for the Stage 1A Signal transaction.

Run serially against exactly ``control_plane_test``.  Setup and final cleanup
use the shared destructive-reset guard, which additionally requires the caller
to set ``AGENTMED_ALLOW_INTEGRATION_RESET=true`` and verifies
``current_database()`` before touching the schema.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest
from pydantic import SecretStr
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.bootstrap.stage1a_local import (
    Stage1ALocalBootstrapRequest,
    execute_stage1a_local_bootstrap,
)
from app.config import Settings
from app.models.tables import Audit, Base, Event, Outbox
from app.models.v4_tables import (
    AgentRunRef,
    AuthorityReceipt,
    PublicCommandIdempotency,
    QualityCase,
    Signal,
    SignalCaseLink,
    SignalContent,
    TraceEvidenceReceipt,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import PublicCredentialResolver
from app.public_api.models import SignalSubmission, SignalSubmissionResponse
from app.services.signal_intake import SignalIntakeError, SignalIntakeService
from conftest import (
    TEST_DATABASE_URL,
    UnsafeIntegrationDatabaseError,
    _new_pg_engine,
    _reset_pg_database_for_migrations,
)


pytestmark = pytest.mark.integration

NOW = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
WORKSPACE_ID = "ws_01J00000000000C1"
PROJECT_ID = "proj_01J00000000000C1"
ENVIRONMENT_ID = "env_01J00000000000C1"
SOURCE_ID = "src_01J00000000000C1"
PRINCIPAL_ID = "prn_01J00000000000C1"
SUBJECT = "stage1a-concurrency-maintainer"
RAW_BEARER = "stage1a-concurrency-bearer-0123456789-DO-NOT-LOG"
RAW_JTI = "stage1a-concurrency-jti-0123456789"
BOOTSTRAP_AUDIT_ACTIONS = {"controllers.register", "stage1a_local.bootstrap"}


@dataclass(frozen=True)
class _Outcome:
    response: SignalSubmissionResponse | None = None
    error_code: str | None = None
    error_details: dict[str, object] | None = None


def _alembic_config(root: Path) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def _settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        public_credential_hash_pepper=SecretStr(
            "stage1a-concurrency-public-pepper-independent"
        ),
        public_cursor_signing_key=SecretStr(
            "stage1a-concurrency-cursor-key-independent"
        ),
        public_auth_issuer="https://auth.caseloop.dev",
        require_mcp_role_tokens=False,
    )


def _bootstrap_request() -> Stage1ALocalBootstrapRequest:
    return Stage1ALocalBootstrapRequest.model_validate(
        {
            "schema_version": "1.0",
            "workspace_id": WORKSPACE_ID,
            "project_id": PROJECT_ID,
            "environment_id": ENVIRONMENT_ID,
            "source": {
                "source_id": SOURCE_ID,
                "connector_kind": "manual",
                "state": "ACTIVE",
                "credential_ref": None,
                "config": {
                    "display_name": "Stage 1A PostgreSQL concurrency gate",
                    "provider_origin": "https://caseloop.local",
                },
            },
            "principal": {"principal_id": PRINCIPAL_ID, "subject": SUBJECT},
            "credential": {
                "credential_id": "cred_01J00000000000C1",
                "bearer_token": RAW_BEARER,
                "jti": RAW_JTI,
                "issued_at": (NOW - timedelta(minutes=2)).isoformat(),
                "not_before": (NOW - timedelta(minutes=1)).isoformat(),
                "expires_at": (NOW + timedelta(days=365)).isoformat(),
            },
            "controllers": {
                "signal": {
                    "registration_id": "creg_01J00000000000C1",
                    "principal_id": "prn_01J00000000001C1",
                },
                "case": {
                    "registration_id": "creg_01J00000000000C2",
                    "principal_id": "prn_01J00000000001C2",
                },
                "evidence": {
                    "registration_id": "creg_01J00000000000C3",
                    "principal_id": "prn_01J00000000001C3",
                },
            },
            "secret_storage_ref": f"keyring://agentmed/test/{WORKSPACE_ID}",
        }
    )


def _submission(*, source_event_id: str, summary: str) -> SignalSubmission:
    return SignalSubmission.model_validate(
        {
            "schema_version": "1.0",
            "source_id": SOURCE_ID,
            "source_event_id": source_event_id,
            "source_event_version": "1",
            "signal_kind": "maintainer_report",
            "reporter": {
                "kind": "maintainer",
                "source_subject_ref": SUBJECT,
            },
            "project_id": PROJECT_ID,
            "environment_id": ENVIRONMENT_ID,
            "governed_agent_id": "ga_01J00000000000C1",
            "occurred_at": NOW.isoformat(),
            "content": {"summary": summary, "body": summary, "attachments": []},
            "run_locator": None,
            "privacy_classification": "INTERNAL",
        }
    )


def _count(session: sa.orm.Session, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _clear_business_slice(engine: sa.Engine) -> None:
    """Retain bootstrap trust roots while clearing the preceding scenario."""

    with engine.begin() as connection:
        for table in (
            "public_command_idempotency",
            "authority_receipts",
            "outbox",
            "events",
            "trace_evidence_receipts",
            "signal_case_links",
            "quality_cases",
            "signals",
            "signal_contents",
        ):
            connection.execute(sa.text(f"DELETE FROM {table}"))
        connection.execute(
            sa.text(
                "DELETE FROM audit "
                "WHERE action NOT IN ('controllers.register', 'stage1a_local.bootstrap')"
            )
        )


def _run_pair(
    factory: sessionmaker,
    *,
    principal: AcceptedPrincipalContext,
    left_submission: SignalSubmission,
    right_submission: SignalSubmission,
    left_key: str,
    right_key: str,
) -> tuple[_Outcome, _Outcome]:
    barrier = Barrier(2, timeout=10)

    def invoke(
        submission: SignalSubmission, idempotency_key: str, request_id: str
    ) -> _Outcome:
        with factory() as session:
            barrier.wait()
            try:
                response = SignalIntakeService(
                    session, clock=lambda: NOW
                ).submit(
                    submission,
                    principal=principal,
                    idempotency_key=idempotency_key,
                    request_id=request_id,
                )
                session.commit()
                return _Outcome(response=response)
            except SignalIntakeError as exc:
                session.rollback()
                return _Outcome(error_code=exc.code, error_details=exc.details)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="stage1a-pg") as pool:
        left = pool.submit(
            invoke, left_submission, left_key, "req_01J00000000000C1"
        )
        right = pool.submit(
            invoke, right_submission, right_key, "req_01J00000000000C2"
        )
        return left.result(timeout=20), right.result(timeout=20)


def _assert_one_authoritative_slice(
    factory: sessionmaker, *, expected_idempotency: int, expected_business_audits: int
) -> None:
    with factory() as session:
        assert _count(session, SignalContent) == 1
        assert _count(session, Signal) == 1
        assert _count(session, QualityCase) == 1
        assert _count(session, SignalCaseLink) == 1
        assert _count(session, TraceEvidenceReceipt) == 1
        assert _count(session, AgentRunRef) == 0
        assert _count(session, Event) == 4
        assert _count(session, Outbox) == 4
        assert _count(session, AuthorityReceipt) == 4
        assert _count(session, PublicCommandIdempotency) == expected_idempotency
        assert (
            int(
                session.scalar(
                    select(func.count())
                    .select_from(Audit)
                    .where(Audit.action.not_in(BOOTSTRAP_AUDIT_ACTIONS))
                )
                or 0
            )
            == expected_business_audits
        )
        events = list(
            session.scalars(
                select(Event)
                .where(Event.contract_version == "v4")
                .order_by(Event.occurred_at, Event.event_id)
            ).all()
        )
        assert [event.event_type for event in events] == [
            "signal.received",
            "case.opened",
            "signal_case_link.linked",
            "evidence.recorded",
        ]
        assert len({event.transaction_id for event in events}) == 1
        outbox = list(session.scalars(select(Outbox)).all())
        assert all(
            row.contract_version == "v4"
            and row.channel == "v4.domain.events"
            and row.status == "PENDING"
            and row.attempts == 0
            for row in outbox
        )


def test_stage1a_signal_concurrency_real_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured_database = sa.engine.make_url(TEST_DATABASE_URL).database
    if configured_database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "agentmed.integration_reset.refused.stage1a_exact_database_required"
        )
    root = Path(__file__).resolve().parents[2]
    engine = _new_pg_engine()
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    settings = _settings()
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    reset_complete = False

    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        reset_complete = True
        with patch.object(
            Base.metadata,
            "create_all",
            side_effect=AssertionError("stage1a.concurrency_must_use_alembic"),
        ):
            alembic_config = _alembic_config(root)
            command.upgrade(alembic_config, "head")
        expected_head = ScriptDirectory.from_config(
            alembic_config
        ).get_current_head()
        with factory() as session:
            assert session.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == expected_head
            execute_stage1a_local_bootstrap(
                session,
                _bootstrap_request(),
                settings=settings,
                now=NOW,
            )
            session.commit()
        with factory() as session:
            principal = PublicCredentialResolver(
                session,
                hash_pepper=settings.public_credential_hash_pepper,
                expected_issuer=settings.public_auth_issuer,
            ).resolve(
                SecretStr(RAW_BEARER),
                requested_workspace_id=WORKSPACE_ID,
                required_scope="signals:write",
                project_id=PROJECT_ID,
                environment_id=ENVIRONMENT_ID,
                evaluated_at=NOW,
            )
            session.rollback()

        # Same public key and exact payload: one winner, one replay of the
        # exact original response/receipt, and only one fact slice.
        exact = _submission(
            source_event_id="stage1a-concurrency-same-key-exact",
            summary="same key exact payload",
        )
        outcomes = _run_pair(
            factory,
            principal=principal,
            left_submission=exact,
            right_submission=exact,
            left_key="stage1a-concurrency-idem-same-exact",
            right_key="stage1a-concurrency-idem-same-exact",
        )
        responses = [outcome.response for outcome in outcomes]
        assert all(response is not None for response in responses)
        assert sorted(response.idempotency.replayed for response in responses if response) == [
            False,
            True,
        ]
        assert responses[0] is not None and responses[1] is not None
        normalized = responses[1].model_dump(mode="json")
        normalized["idempotency"]["replayed"] = responses[0].idempotency.replayed
        assert normalized == responses[0].model_dump(mode="json")
        assert responses[0].idempotency.receipt == responses[1].idempotency.receipt
        _assert_one_authoritative_slice(
            factory, expected_idempotency=1, expected_business_audits=5
        )
        _clear_business_slice(engine)

        # Same public key but payload drift: whichever fingerprint wins is
        # durable; its competitor sees the immutable-key conflict.
        outcomes = _run_pair(
            factory,
            principal=principal,
            left_submission=_submission(
                source_event_id="stage1a-concurrency-same-key-drift",
                summary="same key payload A",
            ),
            right_submission=_submission(
                source_event_id="stage1a-concurrency-same-key-drift",
                summary="same key payload B",
            ),
            left_key="stage1a-concurrency-idem-same-drift",
            right_key="stage1a-concurrency-idem-same-drift",
        )
        assert len([outcome for outcome in outcomes if outcome.response is not None]) == 1
        assert [outcome.error_code for outcome in outcomes].count(
            "IDEMPOTENCY_CONFLICT"
        ) == 1
        _assert_one_authoritative_slice(
            factory, expected_idempotency=1, expected_business_audits=5
        )
        _clear_business_slice(engine)

        # Different public keys, exact connector event: source-event lock
        # serializes the absent-row decision; the second command is DUPLICATE.
        exact = _submission(
            source_event_id="stage1a-concurrency-cross-key-exact",
            summary="cross key exact payload",
        )
        outcomes = _run_pair(
            factory,
            principal=principal,
            left_submission=exact,
            right_submission=exact,
            left_key="stage1a-concurrency-cross-key-exact-A",
            right_key="stage1a-concurrency-cross-key-exact-B",
        )
        responses = [outcome.response for outcome in outcomes if outcome.response]
        assert {response.case.disposition for response in responses} == {
            "NEW",
            "DUPLICATE",
        }
        assert len({response.signal.signal_id for response in responses}) == 1
        assert len({response.case.case_id for response in responses}) == 1
        _assert_one_authoritative_slice(
            factory, expected_idempotency=2, expected_business_audits=6
        )
        _clear_business_slice(engine)

        # Different public keys, drifting connector payload: only the winner
        # leaves any rows.  The losing transaction has no idempotency/audit
        # residue and reports the stable source-event conflict reason.
        outcomes = _run_pair(
            factory,
            principal=principal,
            left_submission=_submission(
                source_event_id="stage1a-concurrency-cross-key-drift",
                summary="cross key payload A",
            ),
            right_submission=_submission(
                source_event_id="stage1a-concurrency-cross-key-drift",
                summary="cross key payload B",
            ),
            left_key="stage1a-concurrency-cross-key-drift-A",
            right_key="stage1a-concurrency-cross-key-drift-B",
        )
        assert len([outcome for outcome in outcomes if outcome.response is not None]) == 1
        failures = [outcome for outcome in outcomes if outcome.error_code is not None]
        assert len(failures) == 1
        assert failures[0].error_code == "VALIDATION_FAILED"
        assert failures[0].error_details == {
            "reason": "SOURCE_EVENT_PAYLOAD_CONFLICT"
        }
        _assert_one_authoritative_slice(
            factory, expected_idempotency=1, expected_business_audits=5
        )
    finally:
        try:
            if reset_complete:
                _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        finally:
            engine.dispose()
