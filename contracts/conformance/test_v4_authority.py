"""One-way ControllerRegistration and AuthorityReceipt conformance for v4."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from v4_integrity import compute_record_digest, exact_record_binding
from v4_semantics import validate_semantics


V4_ROOT = Path(__file__).resolve().parents[1] / "v4"
VALID = V4_ROOT / "fixtures" / "valid"


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _bundle() -> dict[str, Any]:
    authority = _json(VALID / "authority-bundle.json")
    return {
        "resolution_contract": _json(VALID / "resolution-contract.json"),
        "candidate_contract": _json(VALID / "candidate-contract.json"),
        "candidate_revision": _json(VALID / "candidate-revision.json"),
        "evaluation_plan": _json(VALID / "evaluation-plan.json"),
        "gate_report": _json(VALID / "gate-report-v4-pass.json"),
        "workorder": _json(VALID / "workorder-v4.json"),
        "agent_manifest": _json(VALID / "agent-manifest.json"),
        "skill_manifest": _json(VALID / "skill-manifest.json"),
        "mcp_manifest": _json(VALID / "mcp-manifest.json"),
        "participation_manifest": _json(VALID / "participation-manifest.json"),
        "worker_task_issuance_snapshot": _json(
            VALID / "worker-task-queued.json"
        ),
        "worker_task_claim_snapshot": _json(
            VALID / "worker-task-leased.json"
        ),
        "worker_task": _json(VALID / "worker-task.json"),
        "attempt_claim_snapshot": _json(VALID / "attempt-created.json"),
        "proposal_attempt_snapshot": _json(
            VALID / "attempt-output-recorded.json"
        ),
        "attempt": _json(VALID / "attempt.json"),
        "capability_lease": _json(VALID / "capability-lease.json"),
        "capability_current_snapshot": _json(
            VALID / "capability-lease-consumed.json"
        ),
        "proposal": _json(VALID / "proposal.json"),
        "proposal_decision": _json(VALID / "proposal-decision.json"),
        "agent_intent": _json(VALID / "agent-intent.json"),
        "trace_evidence_receipt": _json(
            VALID / "trace-evidence-receipt.json"
        ),
        "identity_attempts": [
            _json(VALID / "attempt-running.json"),
            _json(VALID / "attempt-planner-running.json"),
            _json(VALID / "attempt-planner.json"),
            _json(VALID / "attempt-reviewer-running.json"),
            _json(VALID / "attempt-reviewer.json"),
            _json(VALID / "attempt-judge-running.json"),
            _json(VALID / "attempt-judge.json"),
        ],
        "identity_agent_manifests": [
            _json(VALID / "agent-manifest-planner.json"),
            _json(VALID / "agent-manifest-reviewer.json"),
            _json(VALID / "agent-manifest-judge.json"),
        ],
        "model_call_receipts": [
            _json(VALID / "model-call-receipt-generator.json"),
            _json(VALID / "model-call-receipt-planner.json"),
            _json(VALID / "model-call-receipt-reviewer.json"),
            _json(VALID / "model-call-receipt-judge.json"),
        ],
        "resolution_review_receipts": [
            _json(VALID / "resolution-review-receipt.json"),
        ],
        "gate_track_receipts": [
            _json(VALID / "gate-track-receipt-pass-deterministic.json"),
            _json(VALID / "gate-track-receipt-pass-judge.json"),
        ],
        "controller_registrations": authority["registrations"],
        "authority_receipts": authority["receipts"],
        "authority_events": authority["events"],
        "authority_audits": authority["audits"],
    }


def _codes(bundle: dict[str, Any]) -> set[str]:
    return {violation.code for violation in validate_semantics(bundle)}


def _rehash(document: dict[str, Any], digest_field: str) -> None:
    document[digest_field] = compute_record_digest(document, digest_field)


def _refresh_subject_authority(
    bundle: dict[str, Any],
    bundle_key: str,
    record_name: str,
    digest_field: str,
) -> None:
    subject = bundle[bundle_key]
    _rehash(subject, digest_field)
    receipt = next(
        item
        for item in bundle["authority_receipts"]
        if item["authority_receipt_id"] == subject["authority_receipt_id"]
    )
    receipt["subject"] = exact_record_binding(record_name, subject)
    for item in bundle["authority_events"]:
        if item["authority_receipt_id"] == receipt["authority_receipt_id"]:
            item["subject"] = copy.deepcopy(receipt["subject"])
    for item in bundle["authority_audits"]:
        if item["authority_receipt_id"] == receipt["authority_receipt_id"]:
            item["subject"] = copy.deepcopy(receipt["subject"])
    _rehash(receipt, "authority_receipt_digest")


def _attempt_claim_receipt(bundle: dict[str, Any]) -> dict[str, Any]:
    receipt_id = bundle["attempt_claim_snapshot"]["authority_receipt_id"]
    return next(
        item
        for item in bundle["authority_receipts"]
        if item["authority_receipt_id"] == receipt_id
    )


def test_complete_authority_contract_bundle_is_self_consistent() -> None:
    bundle = _bundle()
    assert validate_semantics(bundle) == ()
    authority_fixture = _json(VALID / "authority-bundle.json")
    assert authority_fixture["evidence_facet"] == "contract"
    assert all(
        "authority_receipt_id" in document
        for key, document in bundle.items()
        if isinstance(document, dict)
        and key
        in {
            "resolution_contract",
            "candidate_contract",
            "candidate_revision",
            "evaluation_plan",
            "gate_report",
            "workorder",
            "agent_manifest",
            "skill_manifest",
            "mcp_manifest",
            "participation_manifest",
            "worker_task",
            "attempt",
            "capability_lease",
            "proposal",
            "proposal_decision",
            "agent_intent",
            "trace_evidence_receipt",
        }
    )


def test_capability_issuance_never_references_future_terminal_digest() -> None:
    bundle = _bundle()
    lease = bundle["capability_lease"]
    claim_attempt = bundle["attempt_claim_snapshot"]
    terminal_attempt = bundle["attempt"]
    assert lease["bound_resource"] == exact_record_binding(
        "attempt", claim_attempt
    )
    assert lease["bound_resource"] != exact_record_binding(
        "attempt", terminal_attempt
    )
    assert bundle["proposal"]["authored_by_attempt"] == exact_record_binding(
        "attempt", bundle["proposal_attempt_snapshot"]
    )
    assert bundle["candidate_revision"]["producer_attempt"] == exact_record_binding(
        "attempt", terminal_attempt
    )
    assert bundle["worker_task"]["terminal_attempt"] == exact_record_binding(
        "attempt", terminal_attempt
    )


def test_authority_topology_is_one_way_and_non_recursive() -> None:
    bundle = _bundle()
    subject = bundle["attempt_claim_snapshot"]
    receipt = _attempt_claim_receipt(bundle)
    registration = next(
        item
        for item in bundle["controller_registrations"]
        if item["controller_registration_id"]
        == receipt["controller_registration"]["id"]
    )
    assert set(key for key in subject if key.startswith("authority_")) == {
        "authority_receipt_id"
    }
    assert receipt["subject"] == exact_record_binding("attempt", subject)
    assert "authority_receipt_id" not in registration
    assert "authority_receipt_id" not in receipt or receipt[
        "authority_receipt_id"
    ] == subject["authority_receipt_id"]


@pytest.mark.parametrize(
    "context_key,expected_code",
    [
        ("authority_receipts", "authority.receipt_missing"),
        ("controller_registrations", "authority.registration_missing"),
        ("authority_events", "authority.event_missing"),
        ("authority_audits", "authority.audit_missing"),
    ],
)
def test_authority_context_fails_closed_when_exact_row_is_missing(
    context_key: str, expected_code: str
) -> None:
    bundle = _bundle()
    receipt = _attempt_claim_receipt(bundle)
    if context_key == "authority_receipts":
        target = receipt["authority_receipt_id"]
        bundle[context_key] = [
            item
            for item in bundle[context_key]
            if item["authority_receipt_id"] != target
        ]
    elif context_key == "controller_registrations":
        target = receipt["controller_registration"]["id"]
        bundle[context_key] = [
            item
            for item in bundle[context_key]
            if item["controller_registration_id"] != target
        ]
    elif context_key == "authority_events":
        bundle[context_key] = [
            item
            for item in bundle[context_key]
            if item["event_id"] != receipt["event_id"]
        ]
    else:
        bundle[context_key] = [
            item
            for item in bundle[context_key]
            if item["audit_ref"] != receipt["audit_ref"]
        ]
    assert expected_code in _codes(bundle)


@pytest.mark.parametrize(
    "context_key,mutated_field,expected_code",
    [
        (
            "authority_events",
            "actor_principal",
            "authority.event_id_duplicate",
        ),
        (
            "authority_audits",
            "controller_principal",
            "authority.audit_ref_duplicate",
        ),
    ],
)
def test_authority_event_and_audit_keys_reject_conflicting_duplicates(
    context_key: str,
    mutated_field: str,
    expected_code: str,
) -> None:
    bundle = _bundle()
    duplicate = copy.deepcopy(bundle[context_key][0])
    duplicate[mutated_field] = "prn_conflicting1"
    bundle[context_key].insert(0, duplicate)
    assert expected_code in _codes(bundle)


def test_authority_bundle_rejects_two_digests_for_one_record_identity() -> None:
    bundle = _bundle()
    original = next(
        item
        for item in bundle["model_call_receipts"]
        if item["model_call_receipt_id"] == "mcr_generator01"
    )
    conflicting = copy.deepcopy(original)
    conflicting["request_digest"] = "sha256:" + "7" * 64
    conflicting["authority_receipt_id"] = "arec_modelcallequivocation000"
    _rehash(conflicting, "model_call_receipt_digest")

    original_receipt = next(
        item
        for item in bundle["authority_receipts"]
        if item["authority_receipt_id"] == original["authority_receipt_id"]
    )
    conflicting_receipt = copy.deepcopy(original_receipt)
    conflicting_receipt["authority_receipt_id"] = conflicting[
        "authority_receipt_id"
    ]
    conflicting_receipt["subject"] = exact_record_binding(
        "model_call_receipt", conflicting
    )
    conflicting_receipt["event_id"] = "evt_authorityequivocation01"
    conflicting_receipt["transaction_id"] = "txn_authorityequivocation01"
    conflicting_receipt["audit_ref"] = "audit://aud_authorityequivocation01"
    _rehash(conflicting_receipt, "authority_receipt_digest")

    original_event = next(
        item
        for item in bundle["authority_events"]
        if item["authority_receipt_id"] == original["authority_receipt_id"]
    )
    conflicting_event = copy.deepcopy(original_event)
    conflicting_event.update(
        {
            "event_id": conflicting_receipt["event_id"],
            "transaction_id": conflicting_receipt["transaction_id"],
            "authority_receipt_id": conflicting["authority_receipt_id"],
            "subject": copy.deepcopy(conflicting_receipt["subject"]),
        }
    )
    original_audit = next(
        item
        for item in bundle["authority_audits"]
        if item["authority_receipt_id"] == original["authority_receipt_id"]
    )
    conflicting_audit = copy.deepcopy(original_audit)
    conflicting_audit.update(
        {
            "audit_ref": conflicting_receipt["audit_ref"],
            "transaction_id": conflicting_receipt["transaction_id"],
            "authority_receipt_id": conflicting["authority_receipt_id"],
            "subject": copy.deepcopy(conflicting_receipt["subject"]),
        }
    )

    bundle["model_call_receipts"].append(conflicting)
    bundle["authority_receipts"].append(conflicting_receipt)
    bundle["authority_events"].append(conflicting_event)
    bundle["authority_audits"].append(conflicting_audit)

    codes = _codes(bundle)
    assert "authority.subject_identity_duplicate" in codes
    assert "authority.receipt_subject_identity_duplicate" in codes


@pytest.mark.parametrize(
    "field,value,expected_code",
    [
        ("owner", "gate-controller", "authority.command_event_mapping_mismatch"),
        ("controller_principal", "prn_otherctrl01", "authority.registration_not_authorized"),
        ("command", "attempts.complete", "authority.command_event_mapping_mismatch"),
        ("event_type", "attempt.succeeded", "authority.command_event_mapping_mismatch"),
        ("transaction_id", "txn_substitute1", "authority.event_binding_mismatch"),
    ],
)
def test_receipt_rehash_cannot_hide_wrong_runtime_authority_binding(
    field: str, value: str, expected_code: str
) -> None:
    bundle = _bundle()
    receipt = _attempt_claim_receipt(bundle)
    receipt[field] = value
    _rehash(receipt, "authority_receipt_digest")
    assert expected_code in _codes(bundle)


def test_coordinated_subject_receipt_rehash_still_breaks_downstream_binding() -> None:
    bundle = _bundle()
    bundle["attempt_claim_snapshot"]["claim_binding"]["fencing_token"] = 2
    _refresh_subject_authority(
        bundle,
        "attempt_claim_snapshot",
        "attempt",
        "attempt_digest",
    )
    codes = _codes(bundle)
    assert "identity_chain.capability_claim_fencing_token_mismatch" in codes
    assert "authority.subject_binding_mismatch" not in codes


def test_terminal_digest_rebound_and_fully_rehashed_is_still_a_future_reference() -> None:
    bundle = _bundle()
    bundle["capability_lease"]["bound_resource"] = exact_record_binding(
        "attempt", bundle["attempt"]
    )
    _refresh_subject_authority(
        bundle,
        "capability_lease",
        "capability_lease",
        "grant_digest",
    )
    codes = _codes(bundle)
    assert "identity_chain.capability_resource_digest_mismatch" in codes
    assert "authority.subject_binding_mismatch" not in codes


def test_revoked_registration_cannot_be_rescued_by_rehashing_receipt() -> None:
    bundle = _bundle()
    receipt = _attempt_claim_receipt(bundle)
    registration = next(
        item
        for item in bundle["controller_registrations"]
        if item["controller_registration_id"]
        == receipt["controller_registration"]["id"]
    )
    registration["state"] = "REVOKED"
    _rehash(registration, "registration_digest")
    receipt["controller_registration"] = exact_record_binding(
        "controller_registration", registration
    )
    _rehash(receipt, "authority_receipt_digest")
    assert "authority.registration_not_authorized" in _codes(bundle)


def test_agent_intent_receipt_records_only_and_never_grants_stage4_execution() -> None:
    bundle = _bundle()
    assert validate_semantics(bundle) == ()
    action_lease = copy.deepcopy(bundle["capability_lease"])
    action_lease["grant_kind"] = "ACTION_EXECUTION"
    action_lease["issuance_basis"] = "AUTHORIZED_ACTION"
    assert "capability_lease.action_execution_stage4_deferred" in {
        item.code
        for item in validate_semantics(
            {
                "capability_lease": action_lease,
                "agent_intent": bundle["agent_intent"],
                "controller_registrations": bundle["controller_registrations"],
                "authority_receipts": bundle["authority_receipts"],
                "authority_events": bundle["authority_events"],
                "authority_audits": bundle["authority_audits"],
            }
        )
    }
