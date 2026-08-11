from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json

import pytest
import sqlalchemy as sa
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.bootstrap.stage1a_local import (
    BootstrapError,
    Stage1ALocalBootstrapRequest,
    execute_stage1a_local_bootstrap,
    main,
    verify_stage1a_alembic_head,
)
from app.config import Settings
from app.models import Audit
from app.models.v4_tables import (
    ControllerRegistration,
    PublicCredential,
    PublicPrincipal,
    SourceConnection,
)
from app.services.authority import build_controller_registration_record
from app.services.authority import AuthorityService
from app.services.v4_audit import V4AuditService
from app.utils.v4_integrity import canonical_digest


NOW = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
RAW_BEARER = "local-bootstrap-bearer-0123456789-DO-NOT-LOG"
RAW_JTI = "local-bootstrap-jti-0123456789"


def _request_dict() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "workspace_id": "ws_01J0000000000001",
        "project_id": "proj_01J0000000000001",
        "environment_id": "env_01J0000000000001",
        "source": {
            "source_id": "src_01J0000000000001",
            "connector_kind": "manual",
            "state": "ACTIVE",
            "credential_ref": None,
            "config": {"display_name": "Local maintainer reports"},
        },
        "principal": {
            "principal_id": "prn_01J0000000000001",
            "subject": "local-maintainer-01J0000000000001",
        },
        "credential": {
            "credential_id": "cred_01J0000000000001",
            "bearer_token": RAW_BEARER,
            "jti": RAW_JTI,
            "issued_at": "2026-08-10T11:59:00Z",
            "not_before": "2026-08-10T11:59:00Z",
            "expires_at": "2027-08-10T12:00:00Z",
        },
        "controllers": {
            "signal": {
                "registration_id": "creg_01J0000000000001",
                "principal_id": "prn_01J0000000000011",
            },
            "case": {
                "registration_id": "creg_01J0000000000002",
                "principal_id": "prn_01J0000000000012",
            },
            "evidence": {
                "registration_id": "creg_01J0000000000003",
                "principal_id": "prn_01J0000000000013",
            },
        },
        "secret_storage_ref": "keyring://caseloop/local/ws_01J0000000000001",
    }


def _request(**changes: object) -> Stage1ALocalBootstrapRequest:
    payload = _request_dict()
    payload.update(changes)
    return Stage1ALocalBootstrapRequest.model_validate(payload)


def _settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "sqlite:///:memory:",
        "public_credential_hash_pepper": SecretStr(
            "bootstrap-public-pepper-that-is-independent"
        ),
        "public_cursor_signing_key": SecretStr(
            "bootstrap-cursor-signing-key-that-is-independent"
        ),
        "public_auth_issuer": "https://auth.caseloop.dev",
        "require_mcp_role_tokens": False,
    }
    values.update(changes)
    return Settings(**values)


def _run(sqlite_session, request=None, *, settings=None, audit_service=None):
    try:
        receipt = execute_stage1a_local_bootstrap(
            sqlite_session,
            request or _request(),
            settings=settings or _settings(),
            now=NOW,
            schema_verifier=lambda _session: None,
            audit_service=audit_service,
        )
        sqlite_session.commit()
        return receipt
    except Exception:
        sqlite_session.rollback()
        raise


def _count(session, model) -> int:
    return len(list(session.scalars(select(model)).all()))


def test_first_run_creates_exact_manual_authority_and_only_hashed_credential(
    sqlite_session,
) -> None:
    request = _request()
    receipt = _run(sqlite_session, request)

    assert receipt.status == "CREATED"
    assert receipt.workspace_id == request.workspace_id
    assert receipt.secret_storage_ref == request.secret_storage_ref
    assert RAW_BEARER not in receipt.model_dump_json()
    assert RAW_JTI not in receipt.model_dump_json()

    source = sqlite_session.get(SourceConnection, request.source.source_id)
    principal = sqlite_session.get(PublicPrincipal, request.principal.principal_id)
    credential = sqlite_session.get(PublicCredential, request.credential.credential_id)
    assert source is not None
    assert source.connector_kind == "manual"
    assert source.state == "ACTIVE"
    assert source.credential_ref is None
    assert principal is not None
    assert principal.principal_type == "human"
    assert principal.scopes == [
        "artifacts:read",
        "capabilities:read",
        "cases:read",
        "signals:write",
    ]
    assert credential is not None
    assert credential.credential_hash.startswith("sha256:")
    assert credential.jti_digest.startswith("sha256:")
    assert RAW_BEARER not in credential.credential_hash
    assert RAW_JTI not in credential.jti_digest

    registrations = list(
        sqlite_session.scalars(
            select(ControllerRegistration).order_by(ControllerRegistration.owner)
        ).all()
    )
    assert len(registrations) == 3
    assert {row.owner: row.allowed_commands for row in registrations} == {
        "case-controller": ["cases.open-from-signal"],
        "evidence-controller": ["evidence.record"],
        "signal-controller": ["signals.link-case", "signals.submit"],
    }
    assert all(row.controller_principal != principal.principal_id for row in registrations)
    assert not set(row.controller_principal for row in registrations) & set(
        sqlite_session.scalars(select(PublicPrincipal.principal_id)).all()
    )
    assert _count(sqlite_session, Audit) == 4

    persisted = []
    for model in (
        SourceConnection,
        PublicPrincipal,
        PublicCredential,
        ControllerRegistration,
        Audit,
    ):
        for row in sqlite_session.scalars(select(model)).all():
            persisted.append(
                {
                    attribute.key: getattr(row, attribute.key)
                    for attribute in sa.inspect(row).mapper.column_attrs
                }
            )
    serialized_persistence = json.dumps(persisted, default=str, sort_keys=True)
    assert RAW_BEARER not in serialized_persistence
    assert RAW_JTI not in serialized_persistence

    authority = AuthorityService(sqlite_session)
    assert (
        authority.resolve_controller(
            workspace_id=request.workspace_id,
            subject_kind="SIGNAL_RECORD",
            command="signals.submit",
            event_type="signal.received",
            recorded_at=NOW,
        ).controller_principal
        == request.controllers.signal.principal_id
    )
    assert (
        authority.resolve_controller(
            workspace_id=request.workspace_id,
            subject_kind="QUALITY_CASE",
            command="cases.open-from-signal",
            event_type="case.opened",
            recorded_at=NOW,
        ).controller_principal
        == request.controllers.case.principal_id
    )
    assert (
        authority.resolve_controller(
            workspace_id=request.workspace_id,
            subject_kind="TRACE_EVIDENCE_RECEIPT",
            command="evidence.record",
            event_type="evidence.recorded",
            recorded_at=NOW,
        ).controller_principal
        == request.controllers.evidence.principal_id
    )


def test_exact_rerun_reuses_business_and_registration_records(sqlite_session) -> None:
    first = _run(sqlite_session)
    original_registration_audits = {
        item.owner: item.registration_audit_ref for item in first.controllers
    }

    second = _run(sqlite_session)

    assert second.status == "REUSED"
    assert _count(sqlite_session, SourceConnection) == 1
    assert _count(sqlite_session, PublicPrincipal) == 1
    assert _count(sqlite_session, PublicCredential) == 1
    assert _count(sqlite_session, ControllerRegistration) == 3
    # Registration audits are immutable and reused; each invocation adds only
    # a fresh command audit for the attempted bootstrap.
    assert _count(sqlite_session, Audit) == 5
    assert {
        item.owner: item.registration_audit_ref for item in second.controllers
    } == original_registration_audits


@pytest.mark.parametrize("failure_call", [1, 4])
def test_any_registration_or_command_audit_failure_rolls_back_every_write(
    sqlite_session, failure_call: int
) -> None:
    audit = V4AuditService(
        sqlite_session,
        clock=lambda: NOW,
        force_fail=False,
        fail_on_call=failure_call,
    )

    with pytest.raises(Exception, match="AUDIT_UNAVAILABLE"):
        _run(sqlite_session, audit_service=audit)

    assert _count(sqlite_session, SourceConnection) == 0
    assert _count(sqlite_session, PublicPrincipal) == 0
    assert _count(sqlite_session, PublicCredential) == 0
    assert _count(sqlite_session, ControllerRegistration) == 0
    assert _count(sqlite_session, Audit) == 0


def test_existing_source_or_credential_drift_fails_closed_without_new_audit(
    sqlite_session,
) -> None:
    _run(sqlite_session)
    source = sqlite_session.get(SourceConnection, "src_01J0000000000001")
    assert source is not None
    source.config = {"display_name": "drifted"}
    sqlite_session.commit()
    audits_before = _count(sqlite_session, Audit)

    with pytest.raises(BootstrapError) as exc_info:
        _run(sqlite_session)

    assert exc_info.value.code == "bootstrap.source_drift"
    assert _count(sqlite_session, Audit) == audits_before


def test_multiple_active_controller_registrations_fail_closed(sqlite_session) -> None:
    _run(sqlite_session)
    extra_audit = V4AuditService(sqlite_session, clock=lambda: NOW).record(
        workspace_id="ws_01J0000000000001",
        actor_principal="prn_01J0000000000001",
        action="controllers.register",
        target="creg_01J0000000000099",
        params={
            "owner": "signal-controller",
            "service_identity_digest": canonical_digest({"extra": True}),
        },
        transaction_id="txn_01J0000000000099",
    )
    built = build_controller_registration_record(
        controller_registration_id="creg_01J0000000000099",
        workspace_id="ws_01J0000000000001",
        owner="signal-controller",
        controller_principal="prn_01J0000000000099",
        allowed_commands=["signals.submit", "signals.link-case"],
        service_identity_digest=canonical_digest({"extra": True}),
        registered_by_human_principal="prn_01J0000000000001",
        registration_audit_ref=extra_audit.audit_ref,
        valid_from=NOW,
        registered_at=NOW,
    )
    sqlite_session.add(ControllerRegistration(**built.row_values))
    sqlite_session.commit()
    audits_before = _count(sqlite_session, Audit)

    with pytest.raises(BootstrapError) as exc_info:
        _run(sqlite_session)

    assert exc_info.value.code == "bootstrap.multiple_active_controller_registrations"
    assert _count(sqlite_session, Audit) == audits_before


@pytest.mark.parametrize(
    "settings",
    [
        _settings(public_credential_hash_pepper=SecretStr("")),
        _settings(public_cursor_signing_key=SecretStr("")),
        _settings(
            public_credential_hash_pepper=SecretStr("same-public-secret"),
            public_cursor_signing_key=SecretStr("same-public-secret"),
        ),
        _settings(
            public_credential_hash_pepper=SecretStr("reused-internal-token"),
            control_plane_internal_token="reused-internal-token",
        ),
    ],
)
def test_missing_or_reused_public_secret_configuration_writes_nothing(
    sqlite_session, settings: Settings
) -> None:
    with pytest.raises(BootstrapError) as exc_info:
        _run(sqlite_session, settings=settings)

    assert exc_info.value.code == "bootstrap.public_secret_configuration_invalid"
    assert _count(sqlite_session, Audit) == 0


@pytest.mark.parametrize(
    "source_patch",
    [
        {"connector_kind": "langfuse"},
        {"state": "DISABLED"},
        {"credential_ref": "env://TOKEN"},
        {"config": {"nested": {"api_key": "must-not-be-stored"}}},
        {"config": {"authorization": "must-not-be-stored"}},
        {"config": {"display_name": RAW_BEARER}},
    ],
)
def test_nonmanual_or_secret_bearing_source_configuration_is_rejected(
    source_patch: dict[str, object],
) -> None:
    payload = _request_dict()
    source = dict(payload["source"])
    source.update(source_patch)
    payload["source"] = source

    with pytest.raises((ValidationError, BootstrapError)):
        request = Stage1ALocalBootstrapRequest.model_validate(payload)
        # Secret-like keys are a semantic check because arbitrary safe manual
        # source metadata remains allowed by the wire model.
        execute_stage1a_local_bootstrap(
            object(),  # type: ignore[arg-type]
            request,
            settings=_settings(),
            now=NOW,
            schema_verifier=lambda _session: None,
        )


def test_request_repr_masks_bearer_and_jti() -> None:
    request = _request()

    assert RAW_BEARER not in repr(request)
    assert RAW_JTI not in repr(request)
    assert "**********" in repr(request)


def test_alembic_revision_must_be_at_head_and_include_007(sqlite_session) -> None:
    sqlite_session.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
    sqlite_session.execute(sa.text("INSERT INTO alembic_version VALUES ('006')"))
    sqlite_session.commit()

    with pytest.raises(BootstrapError) as exc_info:
        verify_stage1a_alembic_head(sqlite_session, require_postgresql=False)
    assert exc_info.value.code == "bootstrap.schema_revision_not_ready"

    from app.bootstrap.stage1a_local import _alembic_script

    current_head = next(iter(_alembic_script().get_heads()))
    sqlite_session.execute(
        sa.text("UPDATE alembic_version SET version_num = :head"),
        {"head": current_head},
    )
    sqlite_session.commit()
    verify_stage1a_alembic_head(sqlite_session, require_postgresql=False)

    with pytest.raises(BootstrapError) as postgres_required:
        verify_stage1a_alembic_head(sqlite_session)
    assert postgres_required.value.code == "bootstrap.postgresql_required"


def test_cli_never_emits_secret_even_when_executor_exception_contains_it() -> None:
    stdin = io.StringIO(json.dumps(_request_dict()))
    stdout = io.StringIO()
    stderr = io.StringIO()

    def fail(_request):
        raise RuntimeError(f"do not echo {RAW_BEARER} or {RAW_JTI}")

    exit_code = main(
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        executor=fail,
    )

    assert exit_code == 1
    output = stdout.getvalue() + stderr.getvalue()
    assert RAW_BEARER not in output
    assert RAW_JTI not in output
    assert json.loads(stdout.getvalue())["error"]["code"] == "bootstrap.internal_error"


def test_cli_success_returns_only_nonsecret_receipt(sqlite_session) -> None:
    stdin = io.StringIO(json.dumps(_request_dict()))
    stdout = io.StringIO()
    stderr = io.StringIO()

    def run(request):
        return _run(sqlite_session, request)

    exit_code = main(stdin=stdin, stdout=stdout, stderr=stderr, executor=run)

    assert exit_code == 0
    output = stdout.getvalue() + stderr.getvalue()
    assert RAW_BEARER not in output
    assert RAW_JTI not in output
    parsed = json.loads(stdout.getvalue())
    assert parsed["status"] == "CREATED"
    assert parsed["credential"]["credential_id"] == "cred_01J0000000000001"


@pytest.mark.parametrize(
    "mutate_json",
    [
        lambda raw: raw.replace(
            '"workspace_id": "ws_01J0000000000001"',
            '"workspace_id": "ws_01J0000000000001", '
            '"workspace_id": "ws_01J0000000000001"',
            1,
        ),
        lambda raw: raw.replace(
            f'"bearer_token": "{RAW_BEARER}"',
            f'"bearer_token": "{RAW_BEARER}", '
            f'"bearer_token": "duplicate-{RAW_BEARER}"',
            1,
        ),
        lambda raw: raw.replace(
            '"display_name": "Local maintainer reports"',
            '"display_name": NaN',
            1,
        ),
        lambda raw: raw.replace(
            '"display_name": "Local maintainer reports"',
            '"display_name": Infinity',
            1,
        ),
        lambda raw: raw.replace(
            '"display_name": "Local maintainer reports"',
            '"display_name": -Infinity',
            1,
        ),
    ],
    ids=[
        "duplicate-top-level-key",
        "duplicate-secret-key",
        "nan",
        "positive-infinity",
        "negative-infinity",
    ],
)
def test_cli_strict_json_rejects_duplicates_and_nonfinite_constants_without_secret_echo(
    mutate_json,
) -> None:
    raw = mutate_json(json.dumps(_request_dict()))
    stdout = io.StringIO()
    stderr = io.StringIO()
    executor_called = False

    def must_not_run(_request):
        nonlocal executor_called
        executor_called = True
        raise AssertionError("strict JSON rejection must happen before execution")

    exit_code = main(
        stdin=io.StringIO(raw),
        stdout=stdout,
        stderr=stderr,
        executor=must_not_run,
    )

    assert exit_code == 1
    assert executor_called is False
    output = stdout.getvalue() + stderr.getvalue()
    assert RAW_BEARER not in output
    assert RAW_JTI not in output
    assert json.loads(stdout.getvalue())["error"]["code"] == "bootstrap.request_invalid"
