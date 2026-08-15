from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.public_api.models import (
    CaseResponse,
    CaseTimelineResponse,
    EvidenceResponse,
    ServerCapabilitiesResponse,
    SignalSubmission,
    SignalSubmissionResponse,
    TraceEvidenceReceipt,
)


FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "v4" / "fixtures" / "valid"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_signal_submission_accepts_the_frozen_no_trace_fixture() -> None:
    submission = SignalSubmission.model_validate(_fixture("public-signal-submission.json"))

    assert submission.run_locator is None
    assert submission.signal_kind == "maintainer_report"
    assert "workspace_id" not in submission.model_fields
    assert "principal_id" not in submission.model_fields


@pytest.mark.parametrize("authority_field", ["workspace_id", "principal_id", "scopes"])
def test_signal_submission_rejects_body_authority(authority_field: str) -> None:
    payload = _fixture("public-signal-submission.json")
    payload[authority_field] = "caller-controlled"

    with pytest.raises(ValidationError):
        SignalSubmission.model_validate(payload)


def test_signal_submission_rejects_duplicate_attachments() -> None:
    payload = _fixture("public-signal-submission.json")
    artifact = {
        "uri": "artifact://trace/input",
        "digest": "sha256:" + "a" * 64,
        "media_type": "application/json",
    }
    payload["content"]["attachments"] = [artifact, artifact]

    with pytest.raises(ValidationError, match="unique"):
        SignalSubmission.model_validate(payload)


def test_signal_submission_response_accepts_the_frozen_no_trace_fixture() -> None:
    response = SignalSubmissionResponse.model_validate(
        _fixture("public-signal-submission-response.json")
    )

    assert response.case.status == "OPEN"
    assert response.case.correlation_status == "NEEDS_CORRELATION"
    assert response.case.triage_status == "UNTRIAGED"
    assert response.evidence.status == "UNKNOWN"
    assert response.evidence.agent_run_ref_id is None
    assert response.missing_fields == response.evidence.missing_fields


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("case", "status", "RESOLVED"),
        ("case", "correlation_status", "CORRELATED"),
        ("case", "triage_status", "TRIAGED"),
        ("evidence", "status", "PARTIAL"),
    ],
)
def test_no_trace_response_rejects_non_correlation_states(
    section: str, field: str, value: str
) -> None:
    payload = copy.deepcopy(_fixture("public-signal-submission-response.json"))
    payload[section][field] = value

    with pytest.raises(ValidationError, match="no-trace"):
        SignalSubmissionResponse.model_validate(payload)


def test_no_trace_response_requires_top_level_missing_fields_to_match_evidence() -> None:
    payload = _fixture("public-signal-submission-response.json")
    payload["missing_fields"] = ["trace.input"]

    with pytest.raises(ValidationError, match="missing_fields"):
        SignalSubmissionResponse.model_validate(payload)


def test_signal_response_rejects_unknown_fields() -> None:
    payload = _fixture("public-signal-submission-response.json")
    payload["success"] = True

    with pytest.raises(ValidationError):
        SignalSubmissionResponse.model_validate(payload)


def test_server_capabilities_accepts_only_frozen_intents() -> None:
    payload = _fixture("public-server-capabilities-response.json")
    response = ServerCapabilitiesResponse.model_validate(payload)

    assert {intent.name for intent in response.data.enabled_intents} == {
        "signals.submit",
        "cases.get",
        "cases.timeline",
        "evidence.get",
        "sources.capabilities",
        "sources.doctor",
        "source-sync-runs.get",
        "capabilities.get",
    }

    payload["data"]["enabled_intents"].append(
        {
            "name": "investigations.start",
            "scope": "runs:start",
            "execution_mode": "asynchronous",
            "http": True,
            "cli": True,
        }
    )
    with pytest.raises(ValidationError):
        ServerCapabilitiesResponse.model_validate(payload)


def test_case_response_validates_the_exact_s1a_shape() -> None:
    payload = {
        "schema_version": "1.0",
        "workspace_id": "ws_01J0000000000001",
        "request_id": "req_01J0000000000001",
        "audit_ref": "audit://aud_01J0000000000001",
        "data": {
            "case_id": "case_01J0000000000001",
            "status": "OPEN",
            "revision": 1,
            "title": "Maintainer report needs trace correlation",
            "project_id": "proj_01J0000000000001",
            "environment_id": "env_01J0000000000001",
            "governed_agent_id": "ga_01J0000000000001",
            "correlation_status": "NEEDS_CORRELATION",
            "triage_status": "UNTRIAGED",
            "signal_refs": ["sig_01J0000000000001"],
            "run_refs": [],
            "evidence_summary": {
                "status": "UNKNOWN",
                "receipt_id": "ter_01J0000000000002",
                "receipt_digest": "sha256:" + "a" * 64,
                "agent_run_ref_id": None,
                "missing_fields": ["trace.input"],
            },
            "input_summary": None,
            "output_summary": None,
            "opened_at": "2026-08-10T09:00:00Z",
            "updated_at": "2026-08-10T09:00:02Z",
            "resolved_at": None,
            "resolution_ref": None,
            "next_action": {
                "code": "CORRELATE_TRACE",
                "command": "case correlate",
                "href": "https://agentmed.local/api/v1/cases/case_01J0000000000001",
            },
        },
    }

    assert CaseResponse.model_validate(payload).data.run_refs == []
    payload["data"]["signal_refs"].append("sig_01J0000000000001")
    with pytest.raises(ValidationError, match="unique"):
        CaseResponse.model_validate(payload)


def test_case_timeline_validates_cursor_snapshot_binding_shape() -> None:
    event = {
        "event_id": "evt_01J0000000000001",
        "event_type": "case.opened",
        "event_version": "1.0",
        "occurred_at": "2026-08-10T09:00:00Z",
        "causation_id": None,
        "correlation_id": "corr-01J0000000000001",
        "actor_principal_id": "prn_01J0000000000001",
        "transaction_id": "txn_01J0000000000001",
        "payload_ref": {
            "uri": "artifact://events/case-opened",
            "digest": "sha256:" + "b" * 64,
            "media_type": "application/json",
        },
        "payload_digest": "sha256:" + "b" * 64,
        "redaction_status": "NOT_REQUIRED",
    }
    payload = {
        "schema_version": "1.0",
        "workspace_id": "ws_01J0000000000001",
        "request_id": "req_01J0000000000001",
        "audit_ref": "audit://aud_01J0000000000001",
        "data": {
            "case_id": "case_01J0000000000001",
            "events": [event],
            "page": {
                "limit": 50,
                "next_cursor": None,
                "has_more": False,
                "snapshot": {
                    "watermark_event_id": "evt_01J0000000000001",
                    "order": "occurred_at,event_id",
                    "filter_digest": "sha256:" + "c" * 64,
                    "cursor_scope_digest": "sha256:" + "d" * 64,
                },
            },
        },
    }

    assert CaseTimelineResponse.model_validate(payload).data.page.limit == 50
    payload["data"]["events"].append(copy.deepcopy(event))
    with pytest.raises(ValidationError, match="unique"):
        CaseTimelineResponse.model_validate(payload)


@pytest.mark.parametrize(
    "fixture_name",
    ["trace-evidence-receipt.json", "trace-evidence-receipt-no-locator.json"],
)
def test_trace_evidence_receipt_accepts_frozen_source_and_no_locator_fixtures(
    fixture_name: str,
) -> None:
    receipt = TraceEvidenceReceipt.model_validate(_fixture(fixture_name))
    assert receipt.receipt_digest.startswith("sha256:")


def test_observed_field_result_serialization_does_not_invent_null_reason() -> None:
    receipt = TraceEvidenceReceipt.model_validate(_fixture("trace-evidence-receipt.json"))
    serialized = receipt.model_dump(mode="json")

    observed = next(item for item in serialized["field_results"] if item["status"] == "OBSERVED")
    assert "reason_digest" not in observed


def test_no_locator_receipt_cannot_invent_an_agent_run() -> None:
    payload = _fixture("trace-evidence-receipt-no-locator.json")
    payload["agent_run_ref_id"] = "arr_01J0000000000001"
    payload["agent_run_ref_digest"] = "sha256:" + "e" * 64

    with pytest.raises(ValidationError, match="NO_LOCATOR"):
        TraceEvidenceReceipt.model_validate(payload)


def test_evidence_response_binds_outer_and_inner_receipt_digest() -> None:
    receipt = _fixture("trace-evidence-receipt-no-locator.json")
    payload = {
        "schema_version": "1.0",
        "workspace_id": "ws_01J0000000000001",
        "request_id": "req_01J0000000000001",
        "audit_ref": "audit://aud_01J0000000000001",
        "data": {
            "receipt_kind": "TRACE_EVIDENCE_RECEIPT",
            "receipt": receipt,
            "receipt_digest": receipt["receipt_digest"],
            "verification_status": "VERIFIED",
            "verified_at": "2026-08-10T09:00:03Z",
            "superseded_by": None,
        },
    }

    assert EvidenceResponse.model_validate(payload).data.receipt.collection_mode == "NO_LOCATOR"
    payload["data"]["receipt_digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValidationError, match="bind"):
        EvidenceResponse.model_validate(payload)


def test_wire_booleans_and_integers_do_not_coerce_strings_or_numbers() -> None:
    response = _fixture("public-signal-submission-response.json")
    response["idempotency"]["replayed"] = "false"
    with pytest.raises(ValidationError):
        SignalSubmissionResponse.model_validate(response)

    capabilities = _fixture("public-server-capabilities-response.json")
    capabilities["data"]["public_api_major"] = "1"
    with pytest.raises(ValidationError):
        ServerCapabilitiesResponse.model_validate(capabilities)
