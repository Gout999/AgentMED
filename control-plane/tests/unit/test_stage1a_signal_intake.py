"""Stage 1A Signal intake is one authoritative, replay-safe transaction."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import delete
from sqlalchemy import event as sa_event
from sqlalchemy import func, select, update

from app.models.tables import Audit, Event, Outbox
from app.models.v4_tables import (
    AgentRunRef,
    AuthorityReceipt,
    ControllerRegistration,
    PublicCommandIdempotency,
    PublicPrincipal,
    QualityCase,
    Signal,
    SignalCaseLink,
    SignalContent,
    SourceConnection,
    TraceEvidenceReceipt,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.public_api.models import SignalSubmission
from app.services.authority import (
    AuthorityError,
    AuthorityService,
    build_controller_registration_record,
)
from app.services.signal_intake import SignalIntakeError, SignalIntakeService
from app.services.public_idempotency import (
    PublicIdempotencyError,
    PublicIdempotencyService,
)
from app.services.v4_audit import V4AuditService
from app.utils.v4_integrity import assert_record_digest, canonical_digest, record_digest


VALID = Path(__file__).resolve().parents[3] / "contracts" / "v4" / "fixtures" / "valid"
CONTRACTS = Path(__file__).resolve().parents[3] / "contracts" / "v4"
NOW = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
WORKSPACE_ID = "ws_01J0000000000001"
PRINCIPAL_ID = "prn_01J0000000000001"
SOURCE_ID = "src_01J0000000000001"


def _json(name: str) -> dict[str, object]:
    return json.loads((VALID / name).read_text(encoding="utf-8"))


def _count(session, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _canonical_source_record(
    source: SourceConnection | None = None, **overrides: object
) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": "1.0",
        "workspace_id": source.workspace_id if source is not None else WORKSPACE_ID,
        "source_id": source.source_id if source is not None else SOURCE_ID,
        "connector_kind": source.connector_kind if source is not None else "manual",
        "state": source.state if source is not None else "ACTIVE",
        "credential_ref": source.credential_ref if source is not None else None,
        "config": (
            source.config
            if source is not None
            else {"provider_origin": "https://agentmed.local"}
        ),
        "revision": source.revision if source is not None else 1,
        "created_by_principal": (
            source.created_by_principal if source is not None else PRINCIPAL_ID
        ),
    }
    record.update(overrides)
    return record


def _seed_stage1a(sqlite_session) -> tuple[AcceptedPrincipalContext, SignalSubmission]:
    principal = AcceptedPrincipalContext.model_validate(
        _json("public-principal-context.json")
    )
    submission = SignalSubmission.model_validate(
        _json("public-signal-submission.json")
    )
    sqlite_session.add_all(
        [
            PublicPrincipal(
                principal_id=principal.principal_id,
                workspace_id=principal.workspace_id,
                principal_type=principal.principal_type,
                state="ACTIVE",
                subject_digest=digest_public_subject(principal.subject),
                audiences=principal.audiences,
                project_ids=principal.project_ids,
                environment_ids=principal.environment_ids,
                scopes=principal.scopes,
                claims_digest=principal.claims_digest,
                created_at=NOW,
                revoked_at=None,
            ),
            SourceConnection(
                source_id=submission.source_id,
                workspace_id=principal.workspace_id,
                connector_kind="manual",
                state="ACTIVE",
                credential_ref=None,
                config={"provider_origin": "https://agentmed.local"},
                connection_digest=canonical_digest(_canonical_source_record()),
                revision=1,
                created_by_principal=principal.principal_id,
                created_at=NOW,
                updated_at=NOW,
            ),
        ]
    )
    sqlite_session.flush()

    registrations = (
        (
            "ctrlreg_01J0000000000001",
            "signal-controller",
            ["signals.submit", "signals.link-case"],
        ),
        (
            "ctrlreg_01J0000000000002",
            "case-controller",
            ["cases.open-from-signal"],
        ),
        (
            "ctrlreg_01J0000000000003",
            "evidence-controller",
            ["evidence.record"],
        ),
    )
    audit = V4AuditService(sqlite_session, clock=lambda: NOW, force_fail=False)
    for registration_id, owner, commands in registrations:
        controller_principal = f"prn_{owner.replace('-', '')}00000001"
        service_identity_digest = canonical_digest(
            {"owner": owner, "principal": controller_principal}
        )
        registration_audit = audit.record(
            workspace_id=principal.workspace_id,
            actor_principal=principal.principal_id,
            action="controllers.register",
            target=registration_id,
            params={
                "owner": owner,
                "service_identity_digest": service_identity_digest,
            },
            transaction_id=f"txn_bootstrap_{owner}",
            trace_id="req_01J0000000000099",
            evidence_refs={
                "owner": owner,
                "controller_registration_id": registration_id,
                "controller_principal": controller_principal,
            },
            occurred_at=NOW,
        )
        built = build_controller_registration_record(
            controller_registration_id=registration_id,
            workspace_id=principal.workspace_id,
            owner=owner,
            controller_principal=controller_principal,
            allowed_commands=commands,
            service_identity_digest=service_identity_digest,
            registered_by_human_principal=principal.principal_id,
            registration_audit_ref=registration_audit.audit_ref,
            valid_from=NOW - timedelta(minutes=1),
            registered_at=NOW,
            contracts_root=CONTRACTS,
        )
        sqlite_session.add(ControllerRegistration(**built.row_values))
    sqlite_session.commit()
    return principal, submission


def _service(sqlite_session, **kwargs: object) -> SignalIntakeService:
    return SignalIntakeService(
        sqlite_session,
        clock=lambda: NOW,
        contracts_root=CONTRACTS,
        **kwargs,
    )


def _seed_committed_signal_receipt(sqlite_session) -> AuthorityReceipt:
    principal, submission = _seed_stage1a(sqlite_session)
    _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key="idem-key-stage1a-authority-proof",
        request_id="req_01J0000000000001",
    )
    sqlite_session.commit()
    receipt = sqlite_session.scalar(
        select(AuthorityReceipt).where(
            AuthorityReceipt.subject_kind == "SIGNAL_RECORD"
        )
    )
    assert receipt is not None
    return receipt


def _assert_receipt_rejected(sqlite_session, receipt: AuthorityReceipt) -> None:
    with pytest.raises(AuthorityError):
        AuthorityService(
            sqlite_session, contracts_root=CONTRACTS
        ).validate_receipt_binding(
            authority_receipt_id=receipt.authority_receipt_id,
            workspace_id=receipt.workspace_id,
            subject_kind=receipt.subject_kind,
            subject_id=receipt.subject_id,
            subject_revision=receipt.subject_revision,
            subject_digest=receipt.subject_digest,
        )


def _wire_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")


def _reseal_audit(row: Audit) -> None:
    payload = {
        "schema_version": "1.0",
        "audit_id": row.audit_id,
        "workspace_id": row.workspace_id,
        "actor_principal": row.actor_principal,
        "action": row.action,
        "target": row.target,
        "params_digest": row.params_digest,
        "result": row.result,
        "error_code": row.error_code,
        "trace_id": row.trace_id,
        "transaction_id": row.transaction_id,
        "evidence_refs": row.evidence_refs,
        "recorded_at": _wire_time(row.ts),
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/audit_digest)",
        "audit_digest": "",
    }
    row.audit_digest = record_digest(payload, self_digest_field="audit_digest")


def _reseal_idempotency_receipt(payload: dict[str, object]) -> dict[str, object]:
    sealed = {**payload, "receipt_digest": ""}
    sealed["receipt_digest"] = record_digest(
        sealed, self_digest_field="receipt_digest"
    )
    return sealed


def _two_completed_idempotency_rows(sqlite_session):
    principal, submission = _seed_stage1a(sqlite_session)
    _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key="idem-key-stage1a-swap-one",
        request_id="req_01J0000000000001",
    )
    sqlite_session.commit()
    second_submission = SignalSubmission.model_validate(
        {
            **submission.model_dump(mode="json"),
            "source_event_id": "maintainer-report-01J0000000000002",
        }
    )
    _service(sqlite_session).submit(
        second_submission,
        principal=principal,
        idempotency_key="idem-key-stage1a-swap-two",
        request_id="req_01J0000000000002",
    )
    sqlite_session.commit()
    rows = list(
        sqlite_session.scalars(
            select(PublicCommandIdempotency).order_by(
                PublicCommandIdempotency.idempotency_key
            )
        ).all()
    )
    assert len(rows) == 2
    return rows


def _seed_same_key_replay(sqlite_session, *, key: str):
    principal, submission = _seed_stage1a(sqlite_session)
    response = _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key=key,
        request_id="req_01J0000000000001",
    )
    sqlite_session.commit()
    row = sqlite_session.scalar(
        select(PublicCommandIdempotency).where(
            PublicCommandIdempotency.idempotency_key == key
        )
    )
    assert row is not None
    audit = sqlite_session.get(Audit, row.audit_ref.removeprefix("audit://"))
    assert audit is not None
    return principal, submission, response, row, audit


def _assert_same_key_replay_fails(
    sqlite_session,
    *,
    principal: AcceptedPrincipalContext,
    submission: SignalSubmission,
    key: str,
) -> None:
    with pytest.raises(SignalIntakeError, match="INTERNAL_ERROR") as raised:
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key=key,
            request_id="req_01J0000000000099",
        )
    assert raised.value.code == "INTERNAL_ERROR"


def test_submit_persists_complete_no_trace_slice_in_one_transaction(
    sqlite_session,
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)

    with (
        patch.object(sqlite_session, "commit", wraps=sqlite_session.commit) as commit,
        patch.object(sqlite_session, "rollback", wraps=sqlite_session.rollback) as rollback,
    ):
        response = _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-0001",
            request_id="req_01J0000000000001",
        )
        commit.assert_not_called()
        rollback.assert_not_called()

    assert response.case.status == "OPEN"
    assert response.case.disposition == "NEW"
    assert response.evidence.status == "UNKNOWN"
    assert response.evidence.agent_run_ref_id is None
    assert response.next_action.code == "CORRELATE_TRACE"
    assert response.next_action.command is None
    assert response.next_action.href is None
    assert response.idempotency.replayed is False

    assert _count(sqlite_session, SignalContent) == 1
    assert _count(sqlite_session, Signal) == 1
    assert _count(sqlite_session, QualityCase) == 1
    assert _count(sqlite_session, SignalCaseLink) == 1
    assert _count(sqlite_session, TraceEvidenceReceipt) == 1
    assert _count(sqlite_session, AgentRunRef) == 0
    assert _count(sqlite_session, Event) == 4
    assert _count(sqlite_session, Outbox) == 4
    assert _count(sqlite_session, AuthorityReceipt) == 4
    assert _count(sqlite_session, PublicCommandIdempotency) == 1

    events = list(
        sqlite_session.scalars(
            select(Event)
            .where(Event.contract_version == "v4")
            .order_by(Event.occurred_at, Event.event_id)
        ).all()
    )
    assert [row.event_type for row in events] == [
        "signal.received",
        "case.opened",
        "signal_case_link.linked",
        "evidence.recorded",
    ]
    assert len({row.transaction_id for row in events}) == 1
    assert events[1].causation_id == events[0].event_id
    assert events[2].causation_id == events[1].event_id
    assert events[3].causation_id == events[0].event_id
    assert all(row.event_type != "CASE_CREATED" for row in events)

    transaction_id = events[0].transaction_id
    transaction_audits = list(
        sqlite_session.scalars(
            select(Audit).where(Audit.transaction_id == transaction_id)
        ).all()
    )
    receipts = list(
        sqlite_session.scalars(
            select(AuthorityReceipt).where(
                AuthorityReceipt.transaction_id == transaction_id
            )
        ).all()
    )
    assert len(transaction_audits) == 5
    assert len(receipts) == 4
    assert len({row.audit_ref for row in receipts}) == 4
    assert {row.audit_ref for row in receipts} <= {
        f"audit://{row.audit_id}" for row in transaction_audits
    }

    signal = sqlite_session.get(Signal, response.signal.signal_id)
    quality_case = sqlite_session.get(QualityCase, response.case.case_id)
    evidence = sqlite_session.get(
        TraceEvidenceReceipt, response.evidence.receipt_id
    )
    assert signal is not None and quality_case is not None and evidence is not None
    assert_record_digest(signal.envelope_payload, self_digest_field="signal_digest")
    assert_record_digest(quality_case.snapshot_payload, self_digest_field="record_digest")
    assert_record_digest(evidence.receipt_payload, self_digest_field="receipt_digest")
    assert quality_case.snapshot_payload["status"] == quality_case.state
    assert quality_case.snapshot_payload["updated_at"] == "2026-08-10T09:00:00Z"
    # Frozen SignalEnvelope does not carry undeclared fields; the separate row
    # and AuthorityReceipt bind controller authority without violating schema.
    assert "authority_receipt_id" not in signal.envelope_payload

    idem = sqlite_session.scalar(select(PublicCommandIdempotency))
    assert idem is not None
    assert idem.response_payload == response.model_dump(mode="json", exclude={"idempotency"})
    assert canonical_digest(idem.response_payload) == idem.response_digest
    assert response.idempotency.receipt.response_digest == idem.response_digest
    assert response.idempotency.receipt.receipt_digest == idem.receipt_digest


def test_projection_inserts_follow_postgres_fk_dependency_order(sqlite_session) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    statements: list[str] = []
    engine = sqlite_session.get_bind()

    def _capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:
        if statement.lstrip().upper().startswith("INSERT INTO"):
            statements.append(statement.lower())

    sa_event.listen(engine, "before_cursor_execute", _capture)
    try:
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-insert-order",
            request_id="req_01J0000000000001",
        )
    finally:
        sa_event.remove(engine, "before_cursor_execute", _capture)

    projection_order = []
    for statement in statements:
        for table in (
            "signal_contents",
            "signals",
            "quality_cases",
            "signal_case_links",
            "trace_evidence_receipts",
        ):
            if f"into {table}" in statement:
                projection_order.append(table)
    assert projection_order == [
        "signal_contents",
        "signals",
        "quality_cases",
        "signal_case_links",
        "trace_evidence_receipts",
    ]


def test_same_key_replays_original_receipt_and_only_flips_delivery_flag(
    sqlite_session,
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    first = _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key="idem-key-stage1a-replay",
        request_id="req_01J0000000000001",
    )
    sqlite_session.commit()
    counts = {
        model: _count(sqlite_session, model)
        for model in (Signal, QualityCase, Event, Outbox, Audit, AuthorityReceipt)
    }

    replay = _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key="idem-key-stage1a-replay",
        request_id="req_01J0000000000002",
    )

    first_payload = first.model_dump(mode="json")
    replay_payload = replay.model_dump(mode="json")
    assert first_payload["idempotency"]["replayed"] is False
    assert replay_payload["idempotency"]["replayed"] is True
    replay_payload["idempotency"]["replayed"] = False
    assert replay_payload == first_payload
    assert replay.idempotency.receipt == first.idempotency.receipt
    assert all(_count(sqlite_session, model) == count for model, count in counts.items())


def test_same_key_replays_original_duplicate_result_and_audit(sqlite_session) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key="idem-key-duplicate-origin",
        request_id="req_01J0000000000001",
    )
    sqlite_session.commit()
    key = "idem-key-duplicate-replay"
    first = _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key=key,
        request_id="req_01J0000000000002",
    )
    sqlite_session.commit()
    counts = {
        model: _count(sqlite_session, model)
        for model in (Signal, QualityCase, Event, Outbox, Audit, AuthorityReceipt)
    }

    replay = _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key=key,
        request_id="req_01J0000000000099",
    )

    assert first.case.disposition == "DUPLICATE"
    assert replay.case.disposition == "DUPLICATE"
    assert replay.idempotency.replayed is True
    assert replay.idempotency.receipt == first.idempotency.receipt
    assert all(_count(sqlite_session, model) == count for model, count in counts.items())


def test_same_key_replay_rejects_missing_command_audit(sqlite_session) -> None:
    key = "idem-key-replay-missing-audit"
    principal, submission, _response, row, _audit = _seed_same_key_replay(
        sqlite_session, key=key
    )
    sqlite_session.execute(
        delete(Audit).where(
            Audit.audit_id == row.audit_ref.removeprefix("audit://")
        ),
        execution_options={"synchronize_session": False},
    )
    sqlite_session.commit()

    _assert_same_key_replay_fails(
        sqlite_session,
        principal=principal,
        submission=submission,
        key=key,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "audit_digest",
        "workspace",
        "actor",
        "action",
        "target",
        "params",
        "result",
        "error_code",
        "trace_id",
        "evidence_refs",
        "recorded_at",
    ],
)
def test_same_key_replay_rejects_tampered_command_audit(
    sqlite_session, mutation: str
) -> None:
    key = f"idem-key-replay-audit-{mutation.replace('_', '-')}"
    principal, submission, _response, _row, audit = _seed_same_key_replay(
        sqlite_session, key=key
    )
    if mutation == "audit_digest":
        audit.audit_digest = "sha256:" + "f" * 64
    elif mutation == "workspace":
        audit.workspace_id = "ws_01J0000000000099"
        _reseal_audit(audit)
    elif mutation == "actor":
        audit.actor = "prn_01J0000000000099"
        audit.actor_principal = audit.actor
        _reseal_audit(audit)
    elif mutation == "action":
        audit.action = "signals.submit.tampered"
        _reseal_audit(audit)
    elif mutation == "target":
        audit.target = "sig_01J0000000000099"
        _reseal_audit(audit)
    elif mutation == "params":
        audit.params_digest = canonical_digest({"tampered": True})
        _reseal_audit(audit)
    elif mutation == "result":
        audit.result = "duplicate"
        _reseal_audit(audit)
    elif mutation == "error_code":
        audit.error_code = "TAMPERED"
        _reseal_audit(audit)
    elif mutation == "trace_id":
        audit.trace_id = "req_01J0000000000099"
        _reseal_audit(audit)
    elif mutation == "evidence_refs":
        audit.evidence_refs = {**audit.evidence_refs, "case_id": "case_01J0000000000099"}
        _reseal_audit(audit)
    elif mutation == "recorded_at":
        audit.ts = audit.ts + timedelta(seconds=1)
        _reseal_audit(audit)
    sqlite_session.commit()

    _assert_same_key_replay_fails(
        sqlite_session,
        principal=principal,
        submission=submission,
        key=key,
    )


def test_same_key_replay_rejects_shape_valid_swapped_command_audit(
    sqlite_session,
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    first_key = "idem-key-replay-audit-swap-first"
    second_key = "idem-key-replay-audit-swap-second"
    _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key=first_key,
        request_id="req_01J0000000000001",
    )
    sqlite_session.commit()
    _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key=second_key,
        request_id="req_01J0000000000002",
    )
    sqlite_session.commit()
    first = sqlite_session.scalar(
        select(PublicCommandIdempotency).where(
            PublicCommandIdempotency.idempotency_key == first_key
        )
    )
    second = sqlite_session.scalar(
        select(PublicCommandIdempotency).where(
            PublicCommandIdempotency.idempotency_key == second_key
        )
    )
    assert first is not None and second is not None
    assert first.response_payload is not None and first.receipt_payload is not None
    response = {**first.response_payload, "audit_ref": second.audit_ref}
    response_digest = canonical_digest(response)
    receipt = _reseal_idempotency_receipt(
        {
            **first.receipt_payload,
            "audit_ref": second.audit_ref,
            "response_digest": response_digest,
        }
    )
    sqlite_session.execute(
        update(PublicCommandIdempotency)
        .where(
            PublicCommandIdempotency.idempotency_record_id
            == first.idempotency_record_id
        )
        .values(
            audit_ref=second.audit_ref,
            response_payload=response,
            response_digest=response_digest,
            receipt_payload=receipt,
            receipt_digest=receipt["receipt_digest"],
        ),
        execution_options={"synchronize_session": False},
    )
    sqlite_session.commit()

    _assert_same_key_replay_fails(
        sqlite_session,
        principal=principal,
        submission=submission,
        key=first_key,
    )


@pytest.mark.parametrize("projection", ["signal", "case", "link", "evidence"])
def test_same_key_replay_rejects_tampered_authority_graph_projection(
    sqlite_session, projection: str
) -> None:
    key = f"idem-key-replay-graph-tamper-{projection}"
    principal, submission, response, _row, _audit = _seed_same_key_replay(
        sqlite_session, key=key
    )
    signal = sqlite_session.get(Signal, response.signal.signal_id)
    quality_case = sqlite_session.get(QualityCase, response.case.case_id)
    link = sqlite_session.scalar(
        select(SignalCaseLink).where(
            SignalCaseLink.signal_id == response.signal.signal_id
        )
    )
    evidence = sqlite_session.get(
        TraceEvidenceReceipt, response.evidence.receipt_id
    )
    assert signal and quality_case and link and evidence
    rows = {
        "signal": (Signal, signal.signal_id, "signal_id", "envelope_payload", signal.envelope_payload),
        "case": (QualityCase, quality_case.case_id, "case_id", "snapshot_payload", quality_case.snapshot_payload),
        "link": (SignalCaseLink, link.signal_case_link_id, "signal_case_link_id", "link_payload", link.link_payload),
        "evidence": (TraceEvidenceReceipt, evidence.receipt_id, "receipt_id", "receipt_payload", evidence.receipt_payload),
    }
    model, identity, id_field, payload_field, payload = rows[projection]
    sqlite_session.execute(
        update(model)
        .where(getattr(model, id_field) == identity)
        .values({payload_field: {**payload, "schema_version": "9.9"}}),
        execution_options={"synchronize_session": False},
    )
    sqlite_session.commit()

    _assert_same_key_replay_fails(
        sqlite_session,
        principal=principal,
        submission=submission,
        key=key,
    )


@pytest.mark.parametrize(
    "missing_node", ["signal", "case", "link", "evidence", "authority_receipt"]
)
def test_same_key_replay_rejects_deleted_authority_graph_node(
    sqlite_session, missing_node: str
) -> None:
    key = f"idem-key-replay-graph-delete-{missing_node.replace('_', '-')}"
    principal, submission, response, _row, _audit = _seed_same_key_replay(
        sqlite_session, key=key
    )
    signal = sqlite_session.get(Signal, response.signal.signal_id)
    quality_case = sqlite_session.get(QualityCase, response.case.case_id)
    link = sqlite_session.scalar(
        select(SignalCaseLink).where(
            SignalCaseLink.signal_id == response.signal.signal_id
        )
    )
    evidence = sqlite_session.get(
        TraceEvidenceReceipt, response.evidence.receipt_id
    )
    assert signal and quality_case and link and evidence
    if missing_node in {"signal", "case", "link"}:
        sqlite_session.execute(
            delete(SignalCaseLink).where(
                SignalCaseLink.signal_case_link_id == link.signal_case_link_id
            ),
            execution_options={"synchronize_session": False},
        )
    if missing_node == "signal":
        sqlite_session.execute(
            delete(TraceEvidenceReceipt).where(
                TraceEvidenceReceipt.receipt_id == evidence.receipt_id
            ),
            execution_options={"synchronize_session": False},
        )
        sqlite_session.execute(
            delete(QualityCase).where(QualityCase.case_id == quality_case.case_id),
            execution_options={"synchronize_session": False},
        )
        sqlite_session.execute(
            delete(Signal).where(Signal.signal_id == signal.signal_id),
            execution_options={"synchronize_session": False},
        )
    elif missing_node == "case":
        sqlite_session.execute(
            delete(QualityCase).where(QualityCase.case_id == quality_case.case_id),
            execution_options={"synchronize_session": False},
        )
    elif missing_node == "evidence":
        sqlite_session.execute(
            delete(TraceEvidenceReceipt).where(
                TraceEvidenceReceipt.receipt_id == evidence.receipt_id
            ),
            execution_options={"synchronize_session": False},
        )
    elif missing_node == "authority_receipt":
        sqlite_session.execute(
            delete(AuthorityReceipt).where(
                AuthorityReceipt.authority_receipt_id
                == signal.authority_receipt_id
            ),
            execution_options={"synchronize_session": False},
        )
    sqlite_session.commit()

    _assert_same_key_replay_fails(
        sqlite_session,
        principal=principal,
        submission=submission,
        key=key,
    )


def test_same_key_payload_drift_is_conflict(sqlite_session) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key="idem-key-stage1a-conflict",
        request_id="req_01J0000000000001",
    )
    sqlite_session.commit()
    drifted = SignalSubmission.model_validate(
        {
            **submission.model_dump(mode="json"),
            "content": {
                **submission.content.model_dump(mode="json"),
                "body": "different body",
            },
        }
    )

    with pytest.raises(SignalIntakeError, match="IDEMPOTENCY_CONFLICT") as raised:
        _service(sqlite_session).submit(
            drifted,
            principal=principal,
            idempotency_key="idem-key-stage1a-conflict",
            request_id="req_01J0000000000002",
        )
    assert raised.value.code == "IDEMPOTENCY_CONFLICT"


def test_idempotency_replay_rejects_swapped_valid_receipt(sqlite_session) -> None:
    first, second = _two_completed_idempotency_rows(sqlite_session)
    first.receipt_payload = second.receipt_payload
    first.receipt_digest = second.receipt_digest

    with pytest.raises(PublicIdempotencyError, match="INTERNAL_ERROR"):
        PublicIdempotencyService(sqlite_session).replay_signal_response(first)


def test_idempotency_replay_rejects_swapped_valid_base_response(
    sqlite_session,
) -> None:
    first, second = _two_completed_idempotency_rows(sqlite_session)
    first.response_payload = second.response_payload
    first.response_digest = second.response_digest

    with pytest.raises(PublicIdempotencyError, match="INTERNAL_ERROR"):
        PublicIdempotencyService(sqlite_session).replay_signal_response(first)


def test_idempotency_replay_rejects_coordinated_valid_result_swap(
    sqlite_session,
) -> None:
    first, second = _two_completed_idempotency_rows(sqlite_session)
    first.response_payload = second.response_payload
    first.response_digest = second.response_digest
    first.receipt_payload = second.receipt_payload
    first.receipt_digest = second.receipt_digest

    with pytest.raises(PublicIdempotencyError, match="INTERNAL_ERROR"):
        PublicIdempotencyService(sqlite_session).replay_signal_response(first)


def test_idempotency_replay_rejects_coordinated_receipt_and_completion_time(
    sqlite_session,
) -> None:
    first, _second = _two_completed_idempotency_rows(sqlite_session)
    assert first.completed_at is not None and first.receipt_payload is not None
    tampered_at = first.completed_at + timedelta(seconds=1)
    first.completed_at = tampered_at
    first.receipt_payload = _reseal_idempotency_receipt(
        {**first.receipt_payload, "created_at": _wire_time(tampered_at)}
    )
    first.receipt_digest = first.receipt_payload["receipt_digest"]

    with pytest.raises(PublicIdempotencyError, match="INTERNAL_ERROR"):
        PublicIdempotencyService(sqlite_session).replay_signal_response(first)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("workspace_id", "ws_01J0000000000099"),
        ("principal_id", "prn_01J0000000000099"),
        ("intent", "approvals.decide"),
        ("idempotency_key", "idem-key-stage1a-row-drift"),
        ("request_fingerprint", "sha256:" + "f" * 64),
        ("resource_kind", "approval_grant"),
        ("resource_id", "sig_01J0000000000099"),
        ("operation_id", "op_01J0000000000099"),
        ("request_id", "req_01J0000000000099"),
        ("audit_ref", "audit://aud_01J0000000000099"),
        ("idempotency_receipt_id", "idemr_01J0000000000099"),
    ],
)
def test_idempotency_replay_rejects_authoritative_row_field_drift(
    sqlite_session, field: str, replacement: object
) -> None:
    first, _second = _two_completed_idempotency_rows(sqlite_session)
    setattr(first, field, replacement)

    with pytest.raises(PublicIdempotencyError, match="INTERNAL_ERROR"):
        PublicIdempotencyService(sqlite_session).replay_signal_response(first)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("request_id", "req_01J0000000000099"),
        ("audit_ref", "audit://aud_01J0000000000099"),
    ],
)
def test_idempotency_replay_rejects_shape_valid_base_response_rebound_from_row(
    sqlite_session, field: str, replacement: str
) -> None:
    first, _second = _two_completed_idempotency_rows(sqlite_session)
    assert first.response_payload is not None and first.receipt_payload is not None
    response = {**first.response_payload, field: replacement}
    response_digest = canonical_digest(response)
    receipt = _reseal_idempotency_receipt(
        {**first.receipt_payload, "response_digest": response_digest}
    )
    first.response_payload = response
    first.response_digest = response_digest
    first.receipt_payload = receipt
    first.receipt_digest = receipt["receipt_digest"]

    with pytest.raises(PublicIdempotencyError, match="INTERNAL_ERROR"):
        PublicIdempotencyService(sqlite_session).replay_signal_response(first)


def test_cross_key_source_duplicate_reuses_facts_and_payload_drift_fails_closed(
    sqlite_session,
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    first = _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key="idem-key-stage1a-source-1",
        request_id="req_01J0000000000001",
    )
    sqlite_session.commit()
    duplicate = _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key="idem-key-stage1a-source-2",
        request_id="req_01J0000000000002",
    )

    assert duplicate.signal.signal_id == first.signal.signal_id
    assert duplicate.signal.duplicate_of_signal_id == first.signal.signal_id
    assert duplicate.case.case_id == first.case.case_id
    assert duplicate.case.disposition == "DUPLICATE"
    assert duplicate.evidence.receipt_id == first.evidence.receipt_id
    assert _count(sqlite_session, Signal) == 1
    assert _count(sqlite_session, QualityCase) == 1
    assert _count(sqlite_session, Event) == 4
    assert _count(sqlite_session, Outbox) == 4
    assert _count(sqlite_session, AuthorityReceipt) == 4
    assert _count(sqlite_session, PublicCommandIdempotency) == 2

    sqlite_session.commit()
    drifted = SignalSubmission.model_validate(
        {
            **submission.model_dump(mode="json"),
            "content": {
                **submission.content.model_dump(mode="json"),
                "summary": "same source event, different payload",
            },
        }
    )
    with pytest.raises(SignalIntakeError) as raised:
        _service(sqlite_session).submit(
            drifted,
            principal=principal,
            idempotency_key="idem-key-stage1a-source-3",
            request_id="req_01J0000000000003",
        )
    assert raised.value.code == "VALIDATION_FAILED"
    assert raised.value.details == {"reason": "SOURCE_EVENT_PAYLOAD_CONFLICT"}
    assert _count(sqlite_session, PublicCommandIdempotency) == 2


@pytest.mark.parametrize(
    ("projection", "mutation"),
    [
        ("signal", lambda row: {**row.envelope_payload, "signal_kind": "runtime_failure"}),
        ("case", lambda row: {**row.snapshot_payload, "title": "tampered"}),
        ("link", lambda row: {**row.link_payload, "case_id": "case_01J0000000000099"}),
        (
            "evidence",
            lambda row: {**row.receipt_payload, "completeness": "COMPLETE"},
        ),
    ],
)
def test_cross_key_duplicate_rejects_tampered_projection(
    sqlite_session, projection: str, mutation
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    first = _service(sqlite_session).submit(
        submission,
        principal=principal,
        idempotency_key="idem-key-stage1a-tamper-1",
        request_id="req_01J0000000000001",
    )
    sqlite_session.commit()
    signal = sqlite_session.get(Signal, first.signal.signal_id)
    quality_case = sqlite_session.get(QualityCase, first.case.case_id)
    link = sqlite_session.scalar(
        select(SignalCaseLink).where(SignalCaseLink.signal_id == first.signal.signal_id)
    )
    evidence = sqlite_session.get(TraceEvidenceReceipt, first.evidence.receipt_id)
    assert signal and quality_case and link and evidence
    rows = {"signal": signal, "case": quality_case, "link": link, "evidence": evidence}
    fields = {
        "signal": "envelope_payload",
        "case": "snapshot_payload",
        "link": "link_payload",
        "evidence": "receipt_payload",
    }
    row = rows[projection]
    setattr(row, fields[projection], mutation(row))

    with pytest.raises(SignalIntakeError, match="INTERNAL_ERROR"):
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-tamper-2",
            request_id="req_01J0000000000002",
        )
    sqlite_session.rollback()
    assert _count(sqlite_session, PublicCommandIdempotency) == 1


def test_registration_audit_or_payload_tamper_fails_before_business_write(
    sqlite_session,
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    registration = sqlite_session.scalar(
        select(ControllerRegistration).where(
            ControllerRegistration.owner == "signal-controller"
        )
    )
    assert registration is not None
    registration.registration_payload = {
        **registration.registration_payload,
        "service_identity_digest": "sha256:" + "f" * 64,
    }

    with pytest.raises(SignalIntakeError, match="INTERNAL_ERROR"):
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-authority",
            request_id="req_01J0000000000001",
        )
    sqlite_session.rollback()
    assert _count(sqlite_session, Signal) == 0
    assert _count(sqlite_session, Event) == 0
    assert _count(sqlite_session, PublicCommandIdempotency) == 0

    registration = sqlite_session.scalar(
        select(ControllerRegistration).where(
            ControllerRegistration.owner == "signal-controller"
        )
    )
    assert registration is not None
    audit = sqlite_session.get(
        Audit, registration.registration_audit_ref.removeprefix("audit://")
    )
    assert audit is not None
    audit.actor_principal = "prn_01J0000000000099"
    audit.actor = audit.actor_principal
    with pytest.raises(SignalIntakeError, match="INTERNAL_ERROR"):
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-authority-2",
            request_id="req_01J0000000000002",
        )


def test_self_consistent_registration_with_fake_audit_ref_is_not_authority(
    sqlite_session,
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    registration = sqlite_session.scalar(
        select(ControllerRegistration).where(
            ControllerRegistration.owner == "signal-controller"
        )
    )
    assert registration is not None
    payload = {
        **registration.registration_payload,
        "registration_audit_ref": "audit://aud_01J0000000000999",
        "registration_digest": "",
    }
    digest = record_digest(payload, self_digest_field="registration_digest")
    payload["registration_digest"] = digest
    registration.registration_audit_ref = payload["registration_audit_ref"]
    registration.registration_payload = payload
    registration.registration_digest = digest

    with pytest.raises(SignalIntakeError, match="INTERNAL_ERROR"):
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-fake-registration-audit",
            request_id="req_01J0000000000001",
        )
    sqlite_session.rollback()
    assert _count(sqlite_session, Signal) == 0
    assert _count(sqlite_session, Event) == 0


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("audit_digest", "sha256:" + "f" * 64),
        ("action", "controller.signal.tampered"),
        ("result", "failure"),
        ("error_code", "TAMPERED"),
        ("params_digest", canonical_digest({"command": "wrong.command"})),
        ("target", "sig_01J0000000000099"),
    ],
)
def test_authority_rejects_tampered_controller_audit_exact_fields(
    sqlite_session, field: str, replacement: object
) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    audit = sqlite_session.get(Audit, receipt.audit_ref.removeprefix("audit://"))
    assert audit is not None
    setattr(audit, field, replacement)

    _assert_receipt_rejected(sqlite_session, receipt)


def test_authority_rejects_missing_controller_audit(sqlite_session) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    audit = sqlite_session.get(Audit, receipt.audit_ref.removeprefix("audit://"))
    assert audit is not None
    sqlite_session.delete(audit)
    sqlite_session.flush()

    _assert_receipt_rejected(sqlite_session, receipt)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("aggregate_id", "sig_01J0000000000099"),
        ("aggregate_type", "quality_case"),
        ("event_version", "9.9"),
        ("payload_digest", "sha256:" + "f" * 64),
    ],
)
def test_authority_rejects_tampered_event_projection(
    sqlite_session, field: str, replacement: object
) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    event = sqlite_session.get(Event, receipt.event_id)
    assert event is not None
    setattr(event, field, replacement)

    _assert_receipt_rejected(sqlite_session, receipt)


def test_authority_rejects_event_payload_drift_even_when_subject_fields_remain(
    sqlite_session,
) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    event = sqlite_session.get(Event, receipt.event_id)
    assert event is not None
    event.payload = {**event.payload, "source_event_id": "tampered-source-event"}

    _assert_receipt_rejected(sqlite_session, receipt)


def test_authority_rejects_missing_v4_outbox(sqlite_session) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == receipt.event_id)
    )
    assert outbox is not None
    sqlite_session.delete(outbox)
    sqlite_session.flush()

    _assert_receipt_rejected(sqlite_session, receipt)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_event_id", "evt_01J0000000000099"),
        ("channel", "v4.wrong-channel"),
        ("workspace_id", "ws_01J0000000000099"),
        ("aggregate_id", "sig_01J0000000000099"),
        ("aggregate_type", "quality_case"),
        ("event_type", "case.opened"),
        ("event_version", "9.9"),
        ("transaction_id", "txn_01J0000000000099"),
        ("actor_principal", "prn_01J0000000000099"),
        ("source_event_seq", 99),
        ("payload_digest", "sha256:" + "f" * 64),
    ],
)
def test_authority_rejects_missing_or_rebound_outbox_projection(
    sqlite_session, field: str, replacement: object
) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == receipt.event_id)
    )
    assert outbox is not None
    setattr(outbox, field, replacement)

    _assert_receipt_rejected(sqlite_session, receipt)


def test_authority_rejects_outbox_envelope_drift_but_ignores_delivery_status(
    sqlite_session,
) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == receipt.event_id)
    )
    assert outbox is not None
    outbox.payload = {**outbox.payload, "correlation_id": "case_tampered0001"}
    outbox.payload_digest = canonical_digest(outbox.payload)
    _assert_receipt_rejected(sqlite_session, receipt)

    sqlite_session.rollback()
    receipt = sqlite_session.get(AuthorityReceipt, receipt.authority_receipt_id)
    assert receipt is not None
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == receipt.event_id)
    )
    assert outbox is not None
    outbox.status = "SENT"
    outbox.attempts = 9
    AuthorityService(
        sqlite_session, contracts_root=CONTRACTS
    ).validate_receipt_binding(
        authority_receipt_id=receipt.authority_receipt_id,
        workspace_id=receipt.workspace_id,
        subject_kind=receipt.subject_kind,
        subject_id=receipt.subject_id,
        subject_revision=receipt.subject_revision,
        subject_digest=receipt.subject_digest,
    )


def test_authority_rejects_shape_valid_coordinated_receipt_time_reseal(
    sqlite_session,
) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    tampered_at = receipt.recorded_at + timedelta(seconds=1)
    payload = {
        **receipt.receipt_payload,
        "recorded_at": _wire_time(tampered_at),
        "authority_receipt_digest": "",
    }
    digest = record_digest(
        payload, self_digest_field="authority_receipt_digest"
    )
    payload["authority_receipt_digest"] = digest
    receipt.recorded_at = tampered_at
    receipt.receipt_payload = payload
    receipt.authority_receipt_digest = digest

    _assert_receipt_rejected(sqlite_session, receipt)


def test_authority_rejects_shape_valid_single_audit_time_reseal(
    sqlite_session,
) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    audit = sqlite_session.get(Audit, receipt.audit_ref.removeprefix("audit://"))
    assert audit is not None
    audit.ts = audit.ts + timedelta(seconds=1)
    _reseal_audit(audit)

    _assert_receipt_rejected(sqlite_session, receipt)


def test_record_receipt_rejects_time_not_bound_to_existing_controller_chain(
    sqlite_session,
) -> None:
    receipt = _seed_committed_signal_receipt(sqlite_session)
    event = sqlite_session.get(Event, receipt.event_id)
    assert event is not None
    registration = sqlite_session.get(
        ControllerRegistration,
        (receipt.controller_registration_id, receipt.controller_registration_revision),
    )
    assert registration is not None
    sqlite_session.execute(
        delete(AuthorityReceipt).where(
            AuthorityReceipt.authority_receipt_id == receipt.authority_receipt_id
        ),
        execution_options={"synchronize_session": False},
    )
    sqlite_session.flush()
    authority = AuthorityService(sqlite_session, contracts_root=CONTRACTS)
    resolved = authority.resolve_controller(
        workspace_id=receipt.workspace_id,
        subject_kind=receipt.subject_kind,
        command=receipt.command,
        event_type=receipt.event_type,
        recorded_at=event.occurred_at,
    )

    with pytest.raises(AuthorityError):
        authority.record_receipt(
            resolved=resolved,
            authority_receipt_id=receipt.authority_receipt_id,
            workspace_id=receipt.workspace_id,
            subject_id=receipt.subject_id,
            subject_revision=receipt.subject_revision,
            subject_digest=receipt.subject_digest,
            event_id=receipt.event_id,
            transaction_id=receipt.transaction_id,
            audit_ref=receipt.audit_ref,
            recorded_at=event.occurred_at + timedelta(seconds=1),
        )


def test_registration_audit_digest_is_recomputed(sqlite_session) -> None:
    _principal, _submission = _seed_stage1a(sqlite_session)
    registration = sqlite_session.scalar(
        select(ControllerRegistration).where(
            ControllerRegistration.owner == "signal-controller"
        )
    )
    assert registration is not None
    audit = sqlite_session.get(
        Audit, registration.registration_audit_ref.removeprefix("audit://")
    )
    assert audit is not None
    audit.audit_digest = "sha256:" + "f" * 64

    with pytest.raises(AuthorityError):
        AuthorityService(
            sqlite_session, contracts_root=CONTRACTS
        ).resolve_controller(
            workspace_id=registration.workspace_id,
            subject_kind="SIGNAL_RECORD",
            command="signals.submit",
            event_type="signal.received",
            recorded_at=NOW,
        )


def test_audit_failure_rolls_back_the_entire_slice_when_caller_rolls_back(
    sqlite_session,
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    failing_audit = V4AuditService(
        sqlite_session,
        clock=lambda: NOW,
        force_fail=False,
        fail_on_call=3,
    )

    with pytest.raises(SignalIntakeError, match="AUDIT_UNAVAILABLE") as raised:
        _service(sqlite_session, audit_service=failing_audit).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-audit-fail",
            request_id="req_01J0000000000001",
        )
    assert raised.value.rollback_required is True
    sqlite_session.rollback()

    for model in (
        SignalContent,
        Signal,
        QualityCase,
        SignalCaseLink,
        TraceEvidenceReceipt,
        AgentRunRef,
        Event,
        Outbox,
        AuthorityReceipt,
        PublicCommandIdempotency,
    ):
        assert _count(sqlite_session, model) == 0
    assert _count(sqlite_session, Audit) == 3  # bootstrap trust-root audits only


@pytest.mark.parametrize("privacy", ["CONFIDENTIAL", "RESTRICTED"])
def test_protected_raw_content_fails_closed_without_persisting_body(
    sqlite_session, privacy: str
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    protected = SignalSubmission.model_validate(
        {**submission.model_dump(mode="json"), "privacy_classification": privacy}
    )

    with pytest.raises(SignalIntakeError) as raised:
        _service(sqlite_session).submit(
            protected,
            principal=principal,
            idempotency_key=f"idem-key-stage1a-{privacy.lower()}",
            request_id="req_01J0000000000001",
        )
    assert raised.value.code == "VALIDATION_FAILED"
    assert raised.value.details == {"reason": "RAW_CONTENT_PROTECTION_UNAVAILABLE"}
    assert _count(sqlite_session, SignalContent) == 0
    assert _count(sqlite_session, Signal) == 0


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"signal_kind": "runtime_failure"}, "UNSUPPORTED_MANUAL_SIGNAL_KIND"),
        (
            {"reporter": {"kind": "maintainer", "source_subject_ref": "somebody-else"}},
            "REPORTER_BINDING_MISMATCH",
        ),
    ],
)
def test_manual_source_cannot_submit_unsupported_kind_or_impersonate_reporter(
    sqlite_session, change: dict[str, object], reason: str
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    invalid = SignalSubmission.model_validate(
        {**submission.model_dump(mode="json"), **change}
    )

    with pytest.raises(SignalIntakeError) as raised:
        _service(sqlite_session).submit(
            invalid,
            principal=principal,
            idempotency_key="idem-key-stage1a-invalid-manual",
            request_id="req_01J0000000000001",
        )
    assert raised.value.code == "VALIDATION_FAILED"
    assert raised.value.details == {"reason": reason}


def test_source_and_principal_rows_are_exact_authoritative_bindings(
    sqlite_session,
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    principal_row = sqlite_session.get(PublicPrincipal, principal.principal_id)
    assert principal_row is not None
    principal_row.scopes = ["signals:write"]
    with pytest.raises(SignalIntakeError, match="TOKEN_INVALID"):
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-principal",
            request_id="req_01J0000000000001",
        )
    sqlite_session.rollback()

    source = sqlite_session.get(SourceConnection, submission.source_id)
    assert source is not None
    source.state = "DISABLED"
    sqlite_session.commit()
    with pytest.raises(SignalIntakeError, match="RESOURCE_NOT_FOUND"):
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-source-disabled",
            request_id="req_01J0000000000002",
        )


_SOURCE_INTEGRITY_CASES = [
    ("provider_origin", {"config": {"provider_origin": "https://attacker.invalid"}}),
    (
        "config",
        {
            "config": {
                "provider_origin": "https://agentmed.local",
                "display_name": "tampered",
            }
        },
    ),
    ("revision", {"revision": 2}),
    ("creator", {"created_by_principal": "prn_01J0000000000099"}),
    ("workspace", {"workspace_id": "ws_01J0000000000099"}),
    ("status", {"state": "DISABLED"}),
]


def _mutate_source_row(source: SourceConnection, patch: dict[str, object]) -> None:
    for field, value in patch.items():
        setattr(source, field, value)


def _assert_no_intake_writes(sqlite_session) -> None:
    for model in (
        SignalContent,
        Signal,
        QualityCase,
        SignalCaseLink,
        TraceEvidenceReceipt,
        AgentRunRef,
        Event,
        Outbox,
        AuthorityReceipt,
        PublicCommandIdempotency,
    ):
        assert _count(sqlite_session, model) == 0
    assert _count(sqlite_session, Audit) == 3


@pytest.mark.parametrize(("case_name", "source_patch"), _SOURCE_INTEGRITY_CASES)
def test_source_connection_stale_digest_fails_before_any_write(
    sqlite_session, case_name: str, source_patch: dict[str, object]
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    source = sqlite_session.get(SourceConnection, submission.source_id)
    assert source is not None
    _mutate_source_row(source, source_patch)
    sqlite_session.commit()

    with pytest.raises(SignalIntakeError) as raised:
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key=f"idem-key-source-stale-{case_name}",
            request_id="req_01J0000000000001",
        )
    assert raised.value.code in {"INTERNAL_ERROR", "RESOURCE_NOT_FOUND"}
    _assert_no_intake_writes(sqlite_session)


@pytest.mark.parametrize(("case_name", "digest_patch"), _SOURCE_INTEGRITY_CASES)
def test_source_connection_recomputed_wrong_binding_fails_before_any_write(
    sqlite_session, case_name: str, digest_patch: dict[str, object]
) -> None:
    principal, submission = _seed_stage1a(sqlite_session)
    source = sqlite_session.get(SourceConnection, submission.source_id)
    assert source is not None
    source.connection_digest = canonical_digest(
        _canonical_source_record(source, **digest_patch)
    )
    sqlite_session.commit()

    with pytest.raises(SignalIntakeError, match="INTERNAL_ERROR") as raised:
        _service(sqlite_session).submit(
            submission,
            principal=principal,
            idempotency_key=f"idem-key-source-rebound-{case_name}",
            request_id="req_01J0000000000001",
        )
    assert raised.value.code == "INTERNAL_ERROR"
    _assert_no_intake_writes(sqlite_session)


def test_same_workspace_authorized_principal_can_use_shared_source(
    sqlite_session,
) -> None:
    creator, submission = _seed_stage1a(sqlite_session)
    payload = creator.model_dump(mode="json")
    payload.update(
        {
            "principal_id": "prn_01J0000000000099",
            "subject": "maintainer-shared-source",
            "credential_id": "cred_01J0000000000099",
            "jti_digest": canonical_digest({"jti": "shared-source"}),
            "claims_digest": canonical_digest({"claims": "shared-source"}),
        }
    )
    principal = AcceptedPrincipalContext.model_validate(payload)
    sqlite_session.add(
        PublicPrincipal(
            principal_id=principal.principal_id,
            workspace_id=principal.workspace_id,
            principal_type=principal.principal_type,
            state="ACTIVE",
            subject_digest=digest_public_subject(principal.subject),
            audiences=principal.audiences,
            project_ids=principal.project_ids,
            environment_ids=principal.environment_ids,
            scopes=principal.scopes,
            claims_digest=principal.claims_digest,
            created_at=NOW,
            revoked_at=None,
        )
    )
    sqlite_session.flush()
    shared_submission = SignalSubmission.model_validate(
        {
            **submission.model_dump(mode="json"),
            "reporter": {
                "kind": "maintainer",
                "source_subject_ref": principal.subject,
            },
        }
    )

    response = _service(sqlite_session).submit(
        shared_submission,
        principal=principal,
        idempotency_key="idem-key-shared-source-principal",
        request_id="req_01J0000000000099",
    )

    assert response.workspace_id == principal.workspace_id
    assert response.signal.source_event_id == shared_submission.source_event_id
    assert _count(sqlite_session, Signal) == 1


def test_postgres_source_lock_is_independent_of_public_idempotency_key() -> None:
    class _Dialect:
        name = "postgresql"

    class _Bind:
        dialect = _Dialect()

    class _Session:
        def __init__(self) -> None:
            self.calls: list[dict[str, int]] = []

        def get_bind(self):
            return _Bind()

        def execute(self, _statement, params):
            self.calls.append(params)

    fake = _Session()
    service = SignalIntakeService.__new__(SignalIntakeService)
    service.session = fake
    service._lock_source_event(
        workspace_id=WORKSPACE_ID,
        source_id=SOURCE_ID,
        source_event_id="source-event-1",
    )
    service._lock_source_event(
        workspace_id=WORKSPACE_ID,
        source_id=SOURCE_ID,
        source_event_id="source-event-1",
    )
    service._lock_source_event(
        workspace_id=WORKSPACE_ID,
        source_id=SOURCE_ID,
        source_event_id="source-event-2",
    )

    assert fake.calls[0] == fake.calls[1]
    assert fake.calls[0] != fake.calls[2]
