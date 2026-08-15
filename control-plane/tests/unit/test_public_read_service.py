from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import set_committed_value

from app.models.tables import Audit, Event, Outbox
from app.models.v4_tables import (
    AuthorityReceipt,
    ControllerRegistration,
    PublicPrincipal,
    QualityCase,
    Signal,
    SignalCaseLink,
    SourceConnection,
    TraceEvidenceReceipt as TraceEvidenceReceiptRow,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.public_api.models import (
    CaseResponse,
    CaseTimelineResponse,
    EvidenceResponse,
    ServerCapabilitiesResponse,
    SignalSubmission,
)
from app.services.authority import (
    AuthorityError,
    AuthorityService,
    build_controller_registration_record,
)
from app.services.public_read import PublicReadDenial, PublicReadError, PublicReadService
from app.services.signal_intake import SignalIntakeService
from app.services.v4_audit import V4AuditService
from app.services.v4_event_store import v4_outbox_envelope
from app.utils.v4_integrity import canonical_digest, record_digest


FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "v4" / "fixtures" / "valid"
CONTRACTS = Path(__file__).resolve().parents[3] / "contracts" / "v4"
WORKSPACE_ID = "ws_01J0000000000001"
OTHER_WORKSPACE_ID = "ws_01J0000000000002"
PRINCIPAL_ID = "prn_01J0000000000001"
OTHER_PRINCIPAL_ID = "prn_01J0000000000002"
SOURCE_ID = "src_01J0000000000001"
SIGNAL_ID = "sig_01J0000000000001"
OTHER_SIGNAL_ID = "sig_01J0000000000002"
CASE_ID = "case_01J0000000000001"
OTHER_CASE_ID = "case_01J0000000000002"
RECEIPT_ID = "ter_01J0000000000002"
OTHER_RECEIPT_ID = "ter_01J0000000000009"
REQUEST_ID = "req_01J0000000000004"
CURSOR_KEY = "stage1a-public-read-cursor-key"
DIGEST_B = "sha256:" + "b" * 64
NOW = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
TIMELINE_EVENT_IDS = [
    "evt_ZZZZZZZZ",
    "evt_BBBBBBBB",
    "evt_CCCCCCCC",
    "evt_AAAAAAAA",
]


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _wire_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _seal(payload: dict[str, object], field: str) -> dict[str, object]:
    payload[field] = ""
    payload[field] = record_digest(payload, self_digest_field=field)
    return payload


def _principal(
    *,
    workspace_id: str = WORKSPACE_ID,
    principal_id: str = PRINCIPAL_ID,
    required_scope: str = "cases:read",
    scopes: list[str] | None = None,
) -> AcceptedPrincipalContext:
    payload = _fixture("public-principal-context.json")
    payload["principal_id"] = principal_id
    payload["workspace_id"] = workspace_id
    payload["scopes"] = scopes or [
        "signals:write",
        "cases:read",
        "artifacts:read",
        "capabilities:read",
    ]
    payload["requested_context"] = {
        "workspace_id": workspace_id,
        "project_id": None,
        "environment_id": None,
        "required_scope": required_scope,
    }
    return AcceptedPrincipalContext.model_validate(payload)


def _seed_stage1a_case(sqlite_session) -> None:
    seed_principal = AcceptedPrincipalContext.model_validate(
        _fixture("public-principal-context.json")
    )
    submission = SignalSubmission.model_validate(
        _fixture("public-signal-submission.json")
    )
    sqlite_session.add_all(
        [
            PublicPrincipal(
                principal_id=seed_principal.principal_id,
                workspace_id=seed_principal.workspace_id,
                principal_type=seed_principal.principal_type,
                state="ACTIVE",
                subject_digest=digest_public_subject(seed_principal.subject),
                audiences=seed_principal.audiences,
                project_ids=seed_principal.project_ids,
                environment_ids=seed_principal.environment_ids,
                scopes=seed_principal.scopes,
                claims_digest=seed_principal.claims_digest,
                created_at=NOW,
                revoked_at=None,
            ),
            SourceConnection(
                source_id=SOURCE_ID,
                workspace_id=WORKSPACE_ID,
                connector_kind="manual",
                state="ACTIVE",
                credential_ref=None,
                config={"provider_origin": "https://agentmed.local"},
                connection_digest=canonical_digest(
                    {
                        "schema_version": "1.0",
                        "workspace_id": WORKSPACE_ID,
                        "source_id": SOURCE_ID,
                        "connector_kind": "manual",
                        "state": "ACTIVE",
                        "credential_ref": None,
                        "config": {"provider_origin": "https://agentmed.local"},
                        "revision": 1,
                        "created_by_principal": PRINCIPAL_ID,
                    }
                ),
                revision=1,
                created_by_principal=PRINCIPAL_ID,
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
            workspace_id=WORKSPACE_ID,
            actor_principal=PRINCIPAL_ID,
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
            workspace_id=WORKSPACE_ID,
            owner=owner,
            controller_principal=controller_principal,
            allowed_commands=commands,
            service_identity_digest=service_identity_digest,
            registered_by_human_principal=PRINCIPAL_ID,
            registration_audit_ref=registration_audit.audit_ref,
            valid_from=NOW - timedelta(minutes=1),
            registered_at=NOW,
            contracts_root=CONTRACTS,
        )
        sqlite_session.add(ControllerRegistration(**built.row_values))
    sqlite_session.flush()

    with (
        patch("app.services.signal_intake.new_signal_id", return_value=SIGNAL_ID),
        patch(
            "app.services.signal_intake.new_signal_content_id",
            return_value="sigc_01J0000000000001",
        ),
        patch("app.services.signal_intake.new_case_id", return_value=CASE_ID),
        patch(
            "app.services.signal_intake.new_signal_case_link_id",
            return_value="scl_01J0000000000001",
        ),
        patch(
            "app.services.signal_intake.new_trace_evidence_receipt_id",
            return_value=RECEIPT_ID,
        ),
        patch(
            "app.services.signal_intake.new_authority_receipt_id",
            side_effect=[
                "arec_01J0000000000001",
                "arec_01J0000000000002",
                "arec_01J0000000000003",
                "arec_01J0000000000004",
            ],
        ),
        patch(
            "app.services.signal_intake.new_transaction_id",
            return_value="txn_01J0000000000001",
        ),
        patch(
            "app.services.v4_event_store.new_event_id",
            side_effect=TIMELINE_EVENT_IDS,
        ),
    ):
        SignalIntakeService(
            sqlite_session,
            clock=lambda: NOW,
            contracts_root=CONTRACTS,
        ).submit(
            submission,
            principal=seed_principal,
            idempotency_key="idem-key-stage1a-public-read-seed",
            request_id=REQUEST_ID,
        )


def _seed_second_stage1a_case(sqlite_session) -> None:
    principal = AcceptedPrincipalContext.model_validate(
        _fixture("public-principal-context.json")
    )
    first_submission = _fixture("public-signal-submission.json")
    submission = SignalSubmission.model_validate(
        {
            **first_submission,
            "source_event_id": "maintainer-report-01J0000000000002",
            "content": {
                **first_submission["content"],
                "summary": "Other case",
            },
        }
    )
    with (
        patch(
            "app.services.signal_intake.new_signal_id",
            return_value=OTHER_SIGNAL_ID,
        ),
        patch(
            "app.services.signal_intake.new_signal_content_id",
            return_value="sigc_01J0000000000002",
        ),
        patch(
            "app.services.signal_intake.new_case_id",
            return_value=OTHER_CASE_ID,
        ),
        patch(
            "app.services.signal_intake.new_signal_case_link_id",
            return_value="scl_01J0000000000002",
        ),
        patch(
            "app.services.signal_intake.new_trace_evidence_receipt_id",
            return_value=OTHER_RECEIPT_ID,
        ),
        patch(
            "app.services.signal_intake.new_authority_receipt_id",
            side_effect=[
                "arec_01J0000000000010",
                "arec_01J0000000000011",
                "arec_01J0000000000012",
                "arec_01J0000000000013",
            ],
        ),
        patch(
            "app.services.signal_intake.new_transaction_id",
            return_value="txn_01J0000000000002",
        ),
        patch(
            "app.services.v4_event_store.new_event_id",
            side_effect=[
                "evt_OTHER_SIGNAL",
                "evt_OTHER_CASE",
                "evt_OTHER_LINK",
                "evt_OTHER_EVIDENCE",
            ],
        ),
    ):
        SignalIntakeService(
            sqlite_session,
            clock=lambda: NOW + timedelta(seconds=10),
            contracts_root=CONTRACTS,
        ).submit(
            submission,
            principal=principal,
            idempotency_key="idem-key-stage1a-public-read-second",
            request_id="req_01J0000000000010",
        )


def _rebind_evidence_signal_anchor(
    sqlite_session,
    *,
    signal_id: str,
    signal_digest: str,
) -> tuple[TraceEvidenceReceiptRow, AuthorityReceipt, Event, Audit, Outbox]:
    """Create a self-consistent authority chain with a wrong cross-record anchor."""

    evidence = sqlite_session.get(TraceEvidenceReceiptRow, RECEIPT_ID)
    assert evidence is not None
    evidence_payload = _seal(
        {
            **evidence.receipt_payload,
            "signal_id": signal_id,
            "signal_digest": signal_digest,
        },
        "receipt_digest",
    )
    set_committed_value(evidence, "signal_id", signal_id)
    set_committed_value(evidence, "signal_digest", signal_digest)
    set_committed_value(evidence, "receipt_payload", evidence_payload)
    set_committed_value(evidence, "receipt_digest", evidence_payload["receipt_digest"])

    authority = sqlite_session.get(
        AuthorityReceipt, evidence.authority_receipt_id
    )
    assert authority is not None
    authority_payload = _seal(
        {
            **authority.receipt_payload,
            "subject": {
                **authority.receipt_payload["subject"],
                "digest": evidence.receipt_digest,
            },
        },
        "authority_receipt_digest",
    )
    set_committed_value(authority, "subject_digest", evidence.receipt_digest)
    assert authority.subject_digest == evidence.receipt_digest
    set_committed_value(authority, "receipt_payload", authority_payload)
    set_committed_value(
        authority,
        "authority_receipt_digest",
        authority_payload["authority_receipt_digest"],
    )

    event = sqlite_session.get(Event, authority.event_id)
    assert event is not None
    event_payload = {
        **event.payload,
        "evidence_digest": evidence.receipt_digest,
        "subject_digest": evidence.receipt_digest,
    }
    set_committed_value(event, "payload", event_payload)
    set_committed_value(event, "payload_digest", canonical_digest(event_payload))

    audit = sqlite_session.get(
        Audit, authority.audit_ref.removeprefix("audit://")
    )
    assert audit is not None
    evidence_refs = {
        **audit.evidence_refs,
        "subject_digest": evidence.receipt_digest,
    }
    set_committed_value(audit, "evidence_refs", evidence_refs)
    audit_payload = {
        "schema_version": "1.0",
        "audit_id": audit.audit_id,
        "workspace_id": audit.workspace_id,
        "actor_principal": audit.actor_principal,
        "action": audit.action,
        "target": audit.target,
        "params_digest": audit.params_digest,
        "result": audit.result,
        "error_code": audit.error_code,
        "trace_id": audit.trace_id,
        "transaction_id": audit.transaction_id,
        "evidence_refs": evidence_refs,
        "recorded_at": _wire_time(audit.ts),
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/audit_digest)",
        "audit_digest": audit.audit_digest,
    }
    set_committed_value(
        audit,
        "audit_digest",
        record_digest(audit_payload, self_digest_field="audit_digest"),
    )

    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == event.event_id)
    )
    assert outbox is not None
    envelope = v4_outbox_envelope(event)
    set_committed_value(outbox, "payload", envelope)
    set_committed_value(outbox, "payload_digest", canonical_digest(envelope))
    # SQLAlchemy weak-references clean identity-map entries. Returning every
    # coordinated row keeps this deliberately in-memory adversarial chain from
    # being garbage-collected and transparently reloaded from its original DB row.
    return evidence, authority, event, audit, outbox


def _assert_evidence_record_and_authority_are_self_consistent(
    sqlite_session,
    evidence: TraceEvidenceReceiptRow,
) -> None:
    PublicReadService._validate_evidence_integrity(evidence)
    authority = sqlite_session.get(
        AuthorityReceipt, evidence.authority_receipt_id
    )
    assert authority is not None
    assert authority.subject_kind == "TRACE_EVIDENCE_RECEIPT"
    assert authority.subject_id == evidence.receipt_id
    assert authority.subject_revision is None
    assert authority.subject_digest == evidence.receipt_digest
    AuthorityService(
        sqlite_session, contracts_root=CONTRACTS
    ).validate_receipt_binding(
        authority_receipt_id=evidence.authority_receipt_id,
        workspace_id=evidence.workspace_id,
        subject_kind="TRACE_EVIDENCE_RECEIPT",
        subject_id=evidence.receipt_id,
        subject_revision=None,
        subject_digest=evidence.receipt_digest,
    )


def _event(
    *,
    event_id: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    occurred_at: datetime,
    causation_id: str,
) -> Event:
    return Event(
        event_id=event_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        seq=1,
        event_type=event_type,
        payload={"subject_digest": DIGEST_B},
        causation_id=causation_id,
        correlation_id=CASE_ID,
        actor=f"{aggregate_type}-controller",
        trace_id=None,
        occurred_at=occurred_at,
        created_at=occurred_at,
        contract_version="v4",
        workspace_id=WORKSPACE_ID,
        event_version="1.0",
        transaction_id="txn_01J0000000000001",
        actor_principal=PRINCIPAL_ID,
        payload_digest=DIGEST_B,
    )


def _seed_timeline(sqlite_session) -> list[str]:
    rows = list(
        sqlite_session.scalars(
            select(Event)
            .where(
                Event.contract_version == "v4",
                Event.workspace_id == WORKSPACE_ID,
                Event.correlation_id == CASE_ID,
            )
            .order_by(Event.occurred_at.asc(), Event.event_id.asc())
        ).all()
    )
    assert [row.event_type for row in rows] == [
        "signal.received",
        "case.opened",
        "signal_case_link.linked",
        "evidence.recorded",
    ]
    return [row.event_id for row in rows]


def _assert_audit(sqlite_session, *, action: str, result: str) -> Audit:
    row = sqlite_session.scalars(
        select(Audit)
        .where(Audit.contract_version == "v4", Audit.action == action, Audit.result == result)
        .order_by(Audit.ts.desc(), Audit.audit_id.desc())
    ).first()
    assert row is not None
    assert row.workspace_id == WORKSPACE_ID
    assert row.actor_principal == PRINCIPAL_ID
    assert row.audit_digest.startswith("sha256:")
    return row


def test_get_case_projects_only_workspace_bound_records_and_records_success_audit(
    sqlite_session, monkeypatch
) -> None:
    _seed_stage1a_case(sqlite_session)
    monkeypatch.setattr(
        sqlite_session,
        "commit",
        lambda: pytest.fail("PublicReadService must not commit the request transaction"),
    )

    response = PublicReadService(sqlite_session, CURSOR_KEY).get_case(
        principal=_principal(), request_id=REQUEST_ID, case_id=CASE_ID
    )

    assert isinstance(response, CaseResponse)
    assert response.workspace_id == WORKSPACE_ID
    assert response.data.case_id == CASE_ID
    assert response.data.signal_refs == [SIGNAL_ID]
    assert response.data.run_refs == []
    assert response.data.evidence_summary.status == "UNKNOWN"
    assert response.data.evidence_summary.receipt_id == RECEIPT_ID
    assert response.data.evidence_summary.agent_run_ref_id is None
    assert response.data.next_action.code == "CORRELATE_TRACE"
    assert response.data.next_action.command is None
    audit = _assert_audit(sqlite_session, action="public.cases.get", result="success")
    assert response.audit_ref == f"audit://{audit.audit_id}"


@pytest.mark.parametrize("case_id", [CASE_ID, OTHER_CASE_ID])
def test_case_cross_workspace_and_missing_are_indistinguishable_audited_denials(
    sqlite_session, case_id: str
) -> None:
    _seed_stage1a_case(sqlite_session)
    principal = _principal(
        workspace_id=OTHER_WORKSPACE_ID,
        principal_id=OTHER_PRINCIPAL_ID,
    )

    with pytest.raises(PublicReadDenial) as caught:
        PublicReadService(sqlite_session, CURSOR_KEY).get_case(
            principal=principal,
            request_id=REQUEST_ID,
            case_id=case_id,
        )

    denial = caught.value
    assert denial.code == "RESOURCE_NOT_FOUND"
    assert denial.details == {}
    assert denial.rollback_required is False
    assert denial.audit_ref.startswith("audit://aud_")
    audit_id = denial.audit_ref.removeprefix("audit://")
    audit = sqlite_session.get(Audit, audit_id)
    assert audit is not None
    assert audit.result == "denied"
    assert audit.error_code == "RESOURCE_NOT_FOUND"
    assert audit.workspace_id == OTHER_WORKSPACE_ID
    assert audit.actor_principal == OTHER_PRINCIPAL_ID


def test_evidence_get_verifies_exact_immutable_receipt_and_audits(sqlite_session) -> None:
    _seed_stage1a_case(sqlite_session)
    principal = _principal(required_scope="artifacts:read")

    response = PublicReadService(sqlite_session, CURSOR_KEY).get_evidence(
        principal=principal,
        request_id=REQUEST_ID,
        receipt_id=RECEIPT_ID,
    )

    assert isinstance(response, EvidenceResponse)
    assert response.data.receipt.receipt_id == RECEIPT_ID
    assert response.data.receipt.workspace_id == WORKSPACE_ID
    assert response.data.receipt_digest == response.data.receipt.receipt_digest
    assert response.data.verification_status == "VERIFIED"
    assert response.data.verified_at is not None
    audit = _assert_audit(sqlite_session, action="public.evidence.get", result="success")
    assert response.audit_ref == f"audit://{audit.audit_id}"


def test_evidence_cross_workspace_uses_non_enumerating_audited_denial(sqlite_session) -> None:
    _seed_stage1a_case(sqlite_session)
    principal = _principal(
        workspace_id=OTHER_WORKSPACE_ID,
        principal_id=OTHER_PRINCIPAL_ID,
        required_scope="artifacts:read",
    )

    with pytest.raises(PublicReadDenial) as caught:
        PublicReadService(sqlite_session, CURSOR_KEY).get_evidence(
            principal=principal,
            request_id=REQUEST_ID,
            receipt_id=RECEIPT_ID,
        )

    assert caught.value.code == "RESOURCE_NOT_FOUND"
    assert caught.value.details == {}
    assert caught.value.rollback_required is False
    audit = sqlite_session.get(Audit, caught.value.audit_ref.removeprefix("audit://"))
    assert audit is not None and audit.result == "denied"


def test_capabilities_filters_to_caller_scopes_and_records_success_audit(sqlite_session) -> None:
    principal = _principal(
        required_scope="capabilities:read", scopes=["capabilities:read"]
    )
    implemented = [
        {
            "name": "signals.submit",
            "scope": "signals:write",
            "execution_mode": "synchronous",
            "http": True,
            "cli": True,
        },
        {
            "name": "capabilities.get",
            "scope": "capabilities:read",
            "execution_mode": "synchronous",
            "http": True,
            "cli": True,
        },
    ]

    response = PublicReadService(sqlite_session, CURSOR_KEY).get_capabilities(
        principal=principal,
        request_id=REQUEST_ID,
        server_version="4.0.0-stage1a",
        implemented_intents=implemented,
    )

    assert isinstance(response, ServerCapabilitiesResponse)
    assert [intent.name for intent in response.data.enabled_intents] == ["capabilities.get"]
    assert response.data.principal.scopes == ["capabilities:read"]
    audit = _assert_audit(
        sqlite_session, action="public.capabilities.get", result="success"
    )
    assert response.audit_ref == f"audit://{audit.audit_id}"


def test_timeline_cursor_preserves_order_and_watermark_across_concurrent_appends(
    sqlite_session,
) -> None:
    _seed_stage1a_case(sqlite_session)
    original_ids = _seed_timeline(sqlite_session)
    service = PublicReadService(sqlite_session, CURSOR_KEY)
    principal = _principal()

    first = service.get_case_timeline(
        principal=principal,
        request_id=REQUEST_ID,
        case_id=CASE_ID,
        cursor=None,
        limit=1,
    )
    assert isinstance(first, CaseTimelineResponse)
    assert [event.event_id for event in first.data.events] == [original_ids[0]]
    assert first.data.page.snapshot.watermark_event_id == original_ids[3]
    assert first.data.page.snapshot.order == "occurred_at,event_id"
    assert first.data.page.has_more is True
    assert first.data.page.next_cursor is not None

    appended = _event(
        event_id="evt_01J0000000000004",
        event_type="case.triaged",
        aggregate_type="quality_case",
        aggregate_id=CASE_ID,
        occurred_at=NOW + timedelta(seconds=2),
        causation_id=original_ids[2],
    )
    appended.seq = 2
    sqlite_session.add(appended)
    sqlite_session.flush()

    second = service.get_case_timeline(
        principal=principal,
        request_id="req_01J0000000000005",
        case_id=CASE_ID,
        cursor=first.data.page.next_cursor,
        limit=1,
    )
    third = service.get_case_timeline(
        principal=principal,
        request_id="req_01J0000000000006",
        case_id=CASE_ID,
        cursor=second.data.page.next_cursor,
        limit=1,
    )
    fourth = service.get_case_timeline(
        principal=principal,
        request_id="req_01J0000000000008",
        case_id=CASE_ID,
        cursor=third.data.page.next_cursor,
        limit=1,
    )

    assert [event.event_id for event in second.data.events] == [original_ids[1]]
    assert [event.event_id for event in third.data.events] == [original_ids[2]]
    assert [event.event_id for event in fourth.data.events] == [original_ids[3]]
    assert fourth.data.page.has_more is False
    assert fourth.data.page.next_cursor is None
    returned = {
        event.event_id
        for response in (first, second, third, fourth)
        for event in response.data.events
    }
    assert returned == set(original_ids)
    assert appended.event_id not in returned
    assert {
        first.data.page.snapshot.cursor_scope_digest,
        second.data.page.snapshot.cursor_scope_digest,
        third.data.page.snapshot.cursor_scope_digest,
        fourth.data.page.snapshot.cursor_scope_digest,
    } == {first.data.page.snapshot.cursor_scope_digest}


def test_timeline_tuple_watermark_keeps_earlier_event_even_when_its_id_is_larger(
    sqlite_session,
) -> None:
    _seed_stage1a_case(sqlite_session)
    event_ids = _seed_timeline(sqlite_session)

    response = PublicReadService(sqlite_session, CURSOR_KEY).get_case_timeline(
        principal=_principal(),
        request_id=REQUEST_ID,
        case_id=CASE_ID,
        cursor=None,
        limit=50,
    )

    assert event_ids[0] > event_ids[-1]
    assert response.data.page.snapshot.watermark_event_id == event_ids[-1]
    assert [item.event_id for item in response.data.events] == event_ids


@pytest.mark.parametrize("mode", ["tampered", "principal", "case", "limit"])
def test_timeline_cursor_rejects_tamper_or_scope_rebinding(
    sqlite_session, mode: str
) -> None:
    _seed_stage1a_case(sqlite_session)
    _seed_timeline(sqlite_session)
    service = PublicReadService(sqlite_session, CURSOR_KEY)
    principal = _principal()
    first = service.get_case_timeline(
        principal=principal,
        request_id=REQUEST_ID,
        case_id=CASE_ID,
        cursor=None,
        limit=1,
    )
    cursor = first.data.page.next_cursor
    assert cursor is not None

    if mode == "tampered":
        cursor = cursor[:-1] + ("A" if cursor[-1] != "A" else "B")
    elif mode == "principal":
        principal = _principal(principal_id=OTHER_PRINCIPAL_ID)
    elif mode == "case":
        _seed_second_stage1a_case(sqlite_session)
    limit = 2 if mode == "limit" else 1

    with pytest.raises(PublicReadError) as caught:
        service.get_case_timeline(
            principal=principal,
            request_id="req_01J0000000000007",
            case_id=OTHER_CASE_ID if mode == "case" else CASE_ID,
            cursor=cursor,
            limit=limit,
        )

    assert caught.value.code == "VALIDATION_FAILED"
    assert caught.value.details == {"fields": ["cursor"]}
    assert caught.value.rollback_required is False
    assert caught.value.audit_ref.startswith("audit://aud_")
    audit = sqlite_session.get(
        Audit, caught.value.audit_ref.removeprefix("audit://")
    )
    assert audit is not None
    assert audit.error_code == "VALIDATION_FAILED"


@pytest.mark.parametrize(
    "target",
    ["case", "signal", "link", "evidence"],
)
def test_case_get_fails_closed_on_tampered_projection_or_self_hash(
    sqlite_session, target: str
) -> None:
    _seed_stage1a_case(sqlite_session)
    if target == "case":
        row = sqlite_session.get(QualityCase, CASE_ID)
        set_committed_value(
            row,
            "snapshot_payload",
            {**row.snapshot_payload, "title": "tampered"},
        )
    elif target == "signal":
        row = sqlite_session.get(Signal, SIGNAL_ID)
        set_committed_value(
            row,
            "envelope_payload",
            {**row.envelope_payload, "signal_kind": "runtime_failure"},
        )
    elif target == "link":
        row = sqlite_session.get(SignalCaseLink, "scl_01J0000000000001")
        set_committed_value(
            row,
            "link_payload",
            {**row.link_payload, "case_id": OTHER_CASE_ID},
        )
    else:
        row = sqlite_session.get(TraceEvidenceReceiptRow, RECEIPT_ID)
        set_committed_value(
            row,
            "receipt_payload",
            {**row.receipt_payload, "completeness": "COMPLETE"},
        )

    with pytest.raises(PublicReadError) as caught:
        PublicReadService(sqlite_session, CURSOR_KEY).get_case(
            principal=_principal(), request_id=REQUEST_ID, case_id=CASE_ID
        )

    assert caught.value.code == "INTERNAL_ERROR"
    assert caught.value.rollback_required is True


def test_timeline_without_authoritative_v4_event_fails_closed(sqlite_session) -> None:
    _seed_stage1a_case(sqlite_session)
    case = sqlite_session.get(QualityCase, CASE_ID)
    assert case is not None
    receipt = sqlite_session.get(AuthorityReceipt, case.authority_receipt_id)
    assert receipt is not None
    sqlite_session.execute(delete(Event).where(Event.event_id == receipt.event_id))
    sqlite_session.expire_all()

    with pytest.raises(PublicReadError) as caught:
        PublicReadService(sqlite_session, CURSOR_KEY).get_case_timeline(
            principal=_principal(),
            request_id=REQUEST_ID,
            case_id=CASE_ID,
            cursor=None,
            limit=50,
        )

    assert caught.value.code == "INTERNAL_ERROR"
    assert caught.value.rollback_required is True
    assert not isinstance(caught.value, PublicReadDenial)


@pytest.mark.parametrize("entrypoint", ["case", "timeline", "evidence"])
@pytest.mark.parametrize(
    "broken_node",
    ["receipt", "registration", "event", "audit"],
)
def test_public_reads_fail_closed_on_broken_authority_chain(
    sqlite_session, entrypoint: str, broken_node: str
) -> None:
    _seed_stage1a_case(sqlite_session)
    if entrypoint in {"timeline", "evidence"}:
        projection = sqlite_session.get(TraceEvidenceReceiptRow, RECEIPT_ID)
    else:
        projection = sqlite_session.get(QualityCase, CASE_ID)
    assert projection is not None
    receipt = sqlite_session.get(
        AuthorityReceipt, projection.authority_receipt_id
    )
    assert receipt is not None

    if broken_node == "receipt":
        sqlite_session.execute(
            delete(AuthorityReceipt).where(
                AuthorityReceipt.authority_receipt_id
                == receipt.authority_receipt_id
            )
        )
        sqlite_session.expire_all()
    elif broken_node == "registration":
        registration = sqlite_session.get(
            ControllerRegistration,
            (
                receipt.controller_registration_id,
                receipt.controller_registration_revision,
            ),
        )
        assert registration is not None
        set_committed_value(
            registration,
            "registration_payload",
            {**registration.registration_payload, "owner": "tampered-controller"},
        )
    elif broken_node == "event":
        event = sqlite_session.get(Event, receipt.event_id)
        assert event is not None
        set_committed_value(
            event,
            "payload",
            {**event.payload, "subject_digest": DIGEST_B},
        )
    else:
        audit = sqlite_session.get(
            Audit, receipt.audit_ref.removeprefix("audit://")
        )
        assert audit is not None
        set_committed_value(
            audit,
            "evidence_refs",
            {**audit.evidence_refs, "subject_digest": DIGEST_B},
        )

    service = PublicReadService(sqlite_session, CURSOR_KEY)
    with pytest.raises(PublicReadError) as caught:
        if entrypoint == "case":
            service.get_case(
                principal=_principal(),
                request_id=REQUEST_ID,
                case_id=CASE_ID,
            )
        elif entrypoint == "timeline":
            service.get_case_timeline(
                principal=_principal(),
                request_id=REQUEST_ID,
                case_id=CASE_ID,
                cursor=None,
                limit=50,
            )
        else:
            service.get_evidence(
                principal=_principal(required_scope="artifacts:read"),
                request_id=REQUEST_ID,
                receipt_id=RECEIPT_ID,
            )

    assert caught.value.code == "INTERNAL_ERROR"
    assert caught.value.rollback_required is True
    assert not isinstance(caught.value, PublicReadDenial)
    error_audit = sqlite_session.get(
        Audit, caught.value.audit_ref.removeprefix("audit://")
    )
    assert error_audit is not None
    assert error_audit.result == "error"
    assert error_audit.error_code == "INTERNAL_ERROR"


@pytest.mark.parametrize("subject", ["signal", "link"])
def test_case_get_validates_nested_projection_authority_receipts(
    sqlite_session, subject: str
) -> None:
    _seed_stage1a_case(sqlite_session)
    if subject == "signal":
        projection = sqlite_session.get(Signal, SIGNAL_ID)
    else:
        projection = sqlite_session.get(SignalCaseLink, "scl_01J0000000000001")
    assert projection is not None
    receipt = sqlite_session.get(
        AuthorityReceipt, projection.authority_receipt_id
    )
    assert receipt is not None
    set_committed_value(
        receipt,
        "receipt_payload",
        {**receipt.receipt_payload, "subject": {"kind": "TAMPERED"}},
    )

    with pytest.raises(PublicReadError) as caught:
        PublicReadService(sqlite_session, CURSOR_KEY).get_case(
            principal=_principal(), request_id=REQUEST_ID, case_id=CASE_ID
        )

    assert caught.value.code == "INTERNAL_ERROR"
    assert caught.value.rollback_required is True
    assert not isinstance(caught.value, PublicReadDenial)


@pytest.mark.parametrize("entrypoint", ["case", "evidence", "timeline"])
def test_public_reads_reject_self_consistent_evidence_with_wrong_signal_digest(
    sqlite_session, entrypoint: str
) -> None:
    _seed_stage1a_case(sqlite_session)
    signal = sqlite_session.get(Signal, SIGNAL_ID)
    assert signal is not None and signal.signal_digest != DIGEST_B
    evidence, *_coordinated_chain = _rebind_evidence_signal_anchor(
        sqlite_session,
        signal_id=SIGNAL_ID,
        signal_digest=DIGEST_B,
    )
    _assert_evidence_record_and_authority_are_self_consistent(
        sqlite_session, evidence
    )

    service = PublicReadService(sqlite_session, CURSOR_KEY)
    with pytest.raises(PublicReadError) as caught:
        if entrypoint == "case":
            service.get_case(
                principal=_principal(),
                request_id=REQUEST_ID,
                case_id=CASE_ID,
            )
        elif entrypoint == "evidence":
            service.get_evidence(
                principal=_principal(required_scope="artifacts:read"),
                request_id=REQUEST_ID,
                receipt_id=RECEIPT_ID,
            )
        else:
            service.get_case_timeline(
                principal=_principal(),
                request_id=REQUEST_ID,
                case_id=CASE_ID,
                cursor=None,
                limit=50,
            )

    assert caught.value.code == "INTERNAL_ERROR"
    assert caught.value.rollback_required is True
    assert not isinstance(caught.value, PublicReadDenial)


@pytest.mark.parametrize("entrypoint", ["case", "timeline"])
def test_case_graph_rejects_evidence_rebound_to_another_valid_signal(
    sqlite_session, entrypoint: str
) -> None:
    _seed_stage1a_case(sqlite_session)
    _seed_second_stage1a_case(sqlite_session)
    other_signal = sqlite_session.get(Signal, OTHER_SIGNAL_ID)
    assert other_signal is not None
    evidence, *_coordinated_chain = _rebind_evidence_signal_anchor(
        sqlite_session,
        signal_id=other_signal.signal_id,
        signal_digest=other_signal.signal_digest,
    )
    # Rebinding to another valid signal is no longer locally authoritative:
    # the exact Stage 1A graph rejects it before projection assembly.
    with pytest.raises(AuthorityError):
        _assert_evidence_record_and_authority_are_self_consistent(
            sqlite_session, evidence
        )
    PublicReadService._validate_evidence_signal_binding(evidence, other_signal)

    service = PublicReadService(sqlite_session, CURSOR_KEY)
    with pytest.raises(PublicReadError) as caught:
        if entrypoint == "case":
            service.get_case(
                principal=_principal(),
                request_id=REQUEST_ID,
                case_id=CASE_ID,
            )
        else:
            service.get_case_timeline(
                principal=_principal(),
                request_id=REQUEST_ID,
                case_id=CASE_ID,
                cursor=None,
                limit=50,
            )

    assert caught.value.code == "INTERNAL_ERROR"
    assert caught.value.rollback_required is True
    assert not isinstance(caught.value, PublicReadDenial)
