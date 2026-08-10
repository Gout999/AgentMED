"""Coordinated rebinding attacks for v4 review, model and Gate receipts."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from v4_integrity import (
    IMMUTABLE_DIGEST_FIELDS,
    compute_content_digest,
    compute_record_digest,
    exact_record_binding,
    model_independence_key,
    resolution_reviewed_content_digest,
)
from v4_semantics import validate_semantics


V4_ROOT = Path(__file__).resolve().parents[1] / "v4"
SCHEMAS = V4_ROOT / "schemas"
VALID = V4_ROOT / "fixtures" / "valid"
INVALID = V4_ROOT / "fixtures" / "invalid"

ATTACK_FIXTURES = (
    "governance-model-provenance-basis-rebind.json",
    "governance-model-independence-key-forge.json",
    "governance-model-manifest-policy-rebind.json",
    "governance-model-token-total-rebind.json",
    "governance-model-case-rebind.json",
    "governance-model-after-terminal-rebind.json",
    "governance-model-future-attempt-rebind.json",
    "governance-resolution-review-self-rebind.json",
    "governance-resolution-independent-same-key.json",
    "governance-planner-generator-rebind.json",
    "governance-judge-generator-rebind.json",
    "governance-default-credential-only.json",
    "governance-gate-track-plan-rebind.json",
    "governance-gate-report-track-digest-rebind.json",
    "governance-decision-agent-self-approve.json",
    "governance-gate-agent-self-approve.json",
    "governance-workorder-agent-self-approve.json",
    "governance-candidate-event-rebind.json",
)

SINGULAR_SCHEMAS = {
    "resolution_contract": "resolution-contract.schema.json",
    "candidate_contract": "candidate-contract.schema.json",
    "candidate_revision": "candidate-revision.schema.json",
    "evaluation_plan": "evaluation-plan.schema.json",
    "attempt": "attempt.schema.json",
    "attempt_claim_snapshot": "attempt.schema.json",
    "proposal_attempt_snapshot": "attempt.schema.json",
    "worker_task": "worker-task.schema.json",
    "worker_task_claim_snapshot": "worker-task.schema.json",
    "agent_manifest": "agent-manifest.schema.json",
    "capability_lease": "capability-lease.schema.json",
    "proposal": "proposal.schema.json",
    "proposal_decision": "proposal-decision.schema.json",
    "participation_manifest": "participation-manifest.schema.json",
    "workorder": "workorder-v4.schema.json",
    "gate_report": "gate-report-v4.schema.json",
}

COLLECTION_SCHEMAS = {
    "identity_attempts": "attempt.schema.json",
    "identity_agent_manifests": "agent-manifest.schema.json",
    "model_call_receipts": "model-call-receipt.schema.json",
    "resolution_review_receipts": "resolution-review-receipt.schema.json",
    "gate_track_receipts": "gate-track-receipt.schema.json",
    "controller_registrations": "controller-registration.schema.json",
    "authority_receipts": "authority-receipt.schema.json",
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    return Registry().with_resources(
        [
            (_json(path)["$id"], Resource.from_contents(_json(path)))
            for path in sorted(SCHEMAS.glob("*.schema.json"))
        ]
    )


def _bundle() -> dict[str, Any]:
    authority = _json(VALID / "authority-bundle.json")
    return {
        "resolution_contract": _json(VALID / "resolution-contract.json"),
        "candidate_contract": _json(VALID / "candidate-contract.json"),
        "candidate_revision": _json(VALID / "candidate-revision.json"),
        "evaluation_plan": _json(VALID / "evaluation-plan.json"),
        "attempt": _json(VALID / "attempt.json"),
        "attempt_claim_snapshot": _json(VALID / "attempt-created.json"),
        "proposal_attempt_snapshot": _json(VALID / "attempt-output-recorded.json"),
        "worker_task": _json(VALID / "worker-task.json"),
        "worker_task_claim_snapshot": _json(VALID / "worker-task-leased.json"),
        "agent_manifest": _json(VALID / "agent-manifest.json"),
        "capability_lease": _json(VALID / "capability-lease.json"),
        "proposal": _json(VALID / "proposal.json"),
        "proposal_decision": _json(VALID / "proposal-decision.json"),
        "participation_manifest": _json(VALID / "participation-manifest.json"),
        "workorder": _json(VALID / "workorder-v4.json"),
        "gate_report": _json(VALID / "gate-report-v4-pass.json"),
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


def _find(items: list[dict[str, Any]], field: str, value: str) -> dict[str, Any]:
    matches = [item for item in items if item.get(field) == value]
    assert len(matches) == 1
    return matches[0]


def _judge_parts(bundle: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    manifest = _find(
        bundle["identity_agent_manifests"], "agent_manifest_id", "aman_judge0001"
    )
    running = next(
        item
        for item in bundle["identity_attempts"]
        if item["attempt_id"] == "att_judge000001" and item["state"] == "RUNNING"
    )
    terminal = next(
        item
        for item in bundle["identity_attempts"]
        if item["attempt_id"] == "att_judge000001" and item["state"] == "SUCCEEDED"
    )
    receipt = _find(
        bundle["model_call_receipts"],
        "model_call_receipt_id",
        "mcr_judge0001",
    )
    track = next(
        item
        for item in bundle["gate_track_receipts"]
        if item["track"] == "INDEPENDENT_JUDGE"
    )
    return manifest, running, terminal, receipt, track


def _agent_identity(
    attempt: dict[str, Any],
    manifest: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    return {
        "principal_id": attempt["executor_principal"],
        "attempt": exact_record_binding("attempt", attempt),
        "agent_manifest": exact_record_binding("agent_manifest", manifest),
        "model_call_receipt": exact_record_binding("model_call_receipt", receipt),
    }


def _rehash_authorize(
    bundle: dict[str, Any], record_name: str, document: dict[str, Any]
) -> None:
    digest_field = IMMUTABLE_DIGEST_FIELDS[record_name]
    document[digest_field] = compute_record_digest(document, digest_field)
    authority = _find(
        bundle["authority_receipts"],
        "authority_receipt_id",
        document["authority_receipt_id"],
    )
    authority["subject"] = exact_record_binding(record_name, document)
    event = _find(bundle["authority_events"], "event_id", authority["event_id"])
    event["subject"] = copy.deepcopy(authority["subject"])
    audit = _find(bundle["authority_audits"], "audit_ref", authority["audit_ref"])
    audit["subject"] = copy.deepcopy(authority["subject"])
    authority["authority_receipt_digest"] = compute_record_digest(
        authority, "authority_receipt_digest"
    )


def _refresh_report_and_workorder(bundle: dict[str, Any]) -> None:
    report = bundle["gate_report"]
    tracks = {item["track"]: item for item in bundle["gate_track_receipts"]}
    report["track_receipts"] = [
        {
            "track": track,
            "receipt": exact_record_binding("gate_track_receipt", tracks[track]),
        }
        for track in report["required_tracks"]
    ]
    _rehash_authorize(bundle, "gate_report", report)
    workorder = bundle["workorder"]
    workorder["gate_report"] = {
        "id": report["gate_report_id"],
        "digest": report["gate_report_digest"],
    }
    _rehash_authorize(bundle, "workorder", workorder)


def _refresh_judge_execution(bundle: dict[str, Any]) -> None:
    manifest, running, terminal, receipt, track = _judge_parts(bundle)
    _rehash_authorize(bundle, "agent_manifest", manifest)
    running["agent_manifest"] = exact_record_binding("agent_manifest", manifest)
    _rehash_authorize(bundle, "attempt", running)
    model = running["model"]
    receipt.update(
        {
            "attempt_snapshot": exact_record_binding("attempt", running),
            "executor_principal": running["executor_principal"],
            "agent_manifest": exact_record_binding("agent_manifest", manifest),
            "runtime_session_id": running["runtime"]["runtime_session_id"],
            "requested_model": model["requested_model"],
            "resolved_provider": model["resolved_provider"],
            "resolved_model": model["resolved_model"],
            "model_resolution_receipt_digest": model[
                "model_resolution_receipt_digest"
            ],
        }
    )
    _rehash_authorize(bundle, "model_call_receipt", receipt)
    terminal["agent_manifest"] = exact_record_binding("agent_manifest", manifest)
    terminal["previous_snapshot"] = exact_record_binding("attempt", running)
    terminal["model"] = copy.deepcopy(running["model"])
    terminal["model"]["model_call_receipt_digest"] = receipt[
        "model_call_receipt_digest"
    ]
    terminal["model"]["model_call_receipt"] = exact_record_binding(
        "model_call_receipt", receipt
    )
    _rehash_authorize(bundle, "attempt", terminal)
    track["executor"]["execution"] = _agent_identity(terminal, manifest, receipt)
    _rehash_authorize(bundle, "gate_track_receipt", track)
    _refresh_report_and_workorder(bundle)


def _refresh_review(bundle: dict[str, Any]) -> None:
    resolution = bundle["resolution_contract"]
    receipt = bundle["resolution_review_receipts"][0]
    receipt["reviewed_content_digest"] = resolution_reviewed_content_digest(
        resolution
    )
    receipt["review_policy"] = copy.deepcopy(resolution["review_policy"])
    receipt["proposer"] = copy.deepcopy(resolution["proposer"])
    _rehash_authorize(bundle, "resolution_review_receipt", receipt)
    resolution["review_receipt"] = exact_record_binding(
        "resolution_review_receipt", receipt
    )
    _rehash_authorize(bundle, "resolution_contract", resolution)


def _configure_credential_only(
    bundle: dict[str, Any], *, relaxed: bool
) -> None:
    generator_manifest = bundle["agent_manifest"]
    generator_running = _find(
        bundle["identity_attempts"], "attempt_id", "att_generator01"
    )
    generator_receipt = _find(
        bundle["model_call_receipts"],
        "model_call_receipt_id",
        "mcr_generator01",
    )
    judge_manifest, judge_running, _judge_terminal, judge_receipt, _track = (
        _judge_parts(bundle)
    )
    judge_manifest["model_policy"] = copy.deepcopy(
        generator_manifest["model_policy"]
    )
    judge_running["model"] = copy.deepcopy(generator_running["model"])
    provenance = copy.deepcopy(generator_receipt["provider_provenance"])
    provenance["provider_request_id"] = "req-judge-credential-only"
    provenance["credential_ref"] = "credential://stepfun/stage0/judge-relaxed"
    basis = {
        key: copy.deepcopy(provenance[key])
        for key in (
            "provider_org",
            "endpoint_origin",
            "account_project",
            "credential_ref",
            "model_family",
        )
    }
    judge_receipt["provider_provenance"] = provenance
    judge_receipt["independence_basis"] = basis
    judge_receipt["independence_key"] = model_independence_key(basis)

    plan = bundle["evaluation_plan"]
    plan["judge_policy"].update(
        {
            "policy_mode": "LOW_RISK_RELAXED" if relaxed else "CODING_DEFAULT",
            "required_distinct_dimensions": (
                ["credential_ref"] if relaxed else ["provider_org", "model_family"]
            ),
            "relaxation_reason_digest": (
                compute_content_digest({"reason": "low-risk credential isolation"})
                if relaxed
                else None
            ),
        }
    )
    _rehash_authorize(bundle, "evaluation_plan", plan)
    for track in bundle["gate_track_receipts"]:
        track["evaluation_plan"] = exact_record_binding("evaluation_plan", plan)
        _rehash_authorize(bundle, "gate_track_receipt", track)
    report = bundle["gate_report"]
    report["evaluation_plan"] = {
        "id": plan["evaluation_plan_id"],
        "digest": plan["plan_digest"],
    }
    workorder = bundle["workorder"]
    workorder["evaluation_plan"] = copy.deepcopy(report["evaluation_plan"])
    _refresh_judge_execution(bundle)


def _refresh_one_track(bundle: dict[str, Any], track: dict[str, Any]) -> None:
    _rehash_authorize(bundle, "gate_track_receipt", track)
    _refresh_report_and_workorder(bundle)


def _attack_model_provenance_basis_rebind(bundle: dict[str, Any]) -> None:
    receipt = _judge_parts(bundle)[3]
    receipt["independence_basis"]["provider_org"] = "rebound-provider"
    receipt["independence_key"] = model_independence_key(
        receipt["independence_basis"]
    )
    _refresh_judge_execution(bundle)


def _attack_model_independence_key_forge(bundle: dict[str, Any]) -> None:
    receipt = _judge_parts(bundle)[3]
    receipt["independence_key"] = compute_content_digest({"forged": True})
    _refresh_judge_execution(bundle)


def _attack_model_manifest_policy_rebind(bundle: dict[str, Any]) -> None:
    running = _judge_parts(bundle)[1]
    running["model"]["requested_model"] = "qwen-max"
    _refresh_judge_execution(bundle)


def _attack_model_token_total_rebind(bundle: dict[str, Any]) -> None:
    receipt = _judge_parts(bundle)[3]
    usage = receipt["provider_provenance"]["token_usage"]
    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"] + 1
    receipt["usage_digest"] = compute_content_digest(usage)
    _refresh_judge_execution(bundle)


def _attack_model_case_rebind(bundle: dict[str, Any]) -> None:
    receipt = _judge_parts(bundle)[3]
    receipt["case_id"] = "case_rebound001"
    _refresh_judge_execution(bundle)


def _attack_model_after_terminal_rebind(bundle: dict[str, Any]) -> None:
    receipt = _judge_parts(bundle)[3]
    receipt["recorded_at"] = "2026-08-10T02:00:00Z"
    _refresh_judge_execution(bundle)


def _attack_model_future_attempt_rebind(bundle: dict[str, Any]) -> None:
    manifest, _running, terminal, receipt, track = _judge_parts(bundle)
    receipt["attempt_snapshot"] = exact_record_binding("attempt", terminal)
    _rehash_authorize(bundle, "model_call_receipt", receipt)
    track["executor"]["execution"] = _agent_identity(terminal, manifest, receipt)
    _refresh_one_track(bundle, track)


def _attack_resolution_review_self_rebind(bundle: dict[str, Any]) -> None:
    resolution = bundle["resolution_contract"]
    receipt = bundle["resolution_review_receipts"][0]
    receipt["reviewer"]["execution"] = copy.deepcopy(resolution["proposer"])
    _refresh_review(bundle)


def _attack_resolution_independent_same_key(bundle: dict[str, Any]) -> None:
    policy = {
        "mode": "AGENT_INDEPENDENT",
        "required_distinct_dimensions": ["provider_org"],
    }
    bundle["resolution_contract"]["review_policy"] = copy.deepcopy(policy)
    bundle["resolution_review_receipts"][0]["review_policy"] = copy.deepcopy(policy)
    _refresh_review(bundle)


def _attack_planner_generator_rebind(bundle: dict[str, Any]) -> None:
    generator_receipt = _find(
        bundle["model_call_receipts"],
        "model_call_receipt_id",
        "mcr_generator01",
    )
    identity = _agent_identity(
        bundle["attempt"], bundle["agent_manifest"], generator_receipt
    )
    bundle["resolution_contract"]["proposer"] = identity
    _refresh_review(bundle)


def _attack_judge_generator_rebind(bundle: dict[str, Any]) -> None:
    generator_receipt = _find(
        bundle["model_call_receipts"],
        "model_call_receipt_id",
        "mcr_generator01",
    )
    track = _judge_parts(bundle)[4]
    track["executor"]["execution"] = _agent_identity(
        bundle["attempt"], bundle["agent_manifest"], generator_receipt
    )
    _refresh_one_track(bundle, track)


def _attack_default_credential_only(bundle: dict[str, Any]) -> None:
    _configure_credential_only(bundle, relaxed=False)


def _attack_gate_track_plan_rebind(bundle: dict[str, Any]) -> None:
    track = _judge_parts(bundle)[4]
    track["evaluation_plan"]["digest"] = compute_content_digest(
        {"rebound": "evaluation-plan"}
    )
    _refresh_one_track(bundle, track)


def _attack_gate_report_track_digest_rebind(bundle: dict[str, Any]) -> None:
    report = bundle["gate_report"]
    next(
        item
        for item in report["track_receipts"]
        if item["track"] == "INDEPENDENT_JUDGE"
    )["receipt"]["digest"] = compute_content_digest({"rebound": "track"})
    _rehash_authorize(bundle, "gate_report", report)
    bundle["workorder"]["gate_report"] = {
        "id": report["gate_report_id"],
        "digest": report["gate_report_digest"],
    }
    _rehash_authorize(bundle, "workorder", bundle["workorder"])


def _attack_controller_actor(
    bundle: dict[str, Any], record_name: str, field: str
) -> None:
    document = bundle[record_name]
    document[field] = "prn_generator01"
    _rehash_authorize(bundle, record_name, document)


def _attack_candidate_event_rebind(bundle: dict[str, Any]) -> None:
    revision = bundle["candidate_revision"]
    authority = _find(
        bundle["authority_receipts"],
        "authority_receipt_id",
        revision["authority_receipt_id"],
    )
    event = _find(bundle["authority_events"], "event_id", authority["event_id"])
    event["event_id"] = "evt_reboundcandidate"
    authority["event_id"] = event["event_id"]
    authority["authority_receipt_digest"] = compute_record_digest(
        authority, "authority_receipt_digest"
    )


ATTACKS: dict[str, Callable[[dict[str, Any]], None]] = {
    "model_provenance_basis_rebind": _attack_model_provenance_basis_rebind,
    "model_independence_key_forge": _attack_model_independence_key_forge,
    "model_manifest_policy_rebind": _attack_model_manifest_policy_rebind,
    "model_token_total_rebind": _attack_model_token_total_rebind,
    "model_case_rebind": _attack_model_case_rebind,
    "model_after_terminal_rebind": _attack_model_after_terminal_rebind,
    "model_future_attempt_rebind": _attack_model_future_attempt_rebind,
    "resolution_review_self_rebind": _attack_resolution_review_self_rebind,
    "resolution_independent_same_key": _attack_resolution_independent_same_key,
    "planner_generator_rebind": _attack_planner_generator_rebind,
    "judge_generator_rebind": _attack_judge_generator_rebind,
    "default_credential_only": _attack_default_credential_only,
    "gate_track_plan_rebind": _attack_gate_track_plan_rebind,
    "gate_report_track_digest_rebind": _attack_gate_report_track_digest_rebind,
    "decision_agent_self_approve": lambda bundle: _attack_controller_actor(
        bundle, "proposal_decision", "decided_by_principal"
    ),
    "gate_agent_self_approve": lambda bundle: _attack_controller_actor(
        bundle, "gate_report", "created_by_principal"
    ),
    "workorder_agent_self_approve": lambda bundle: _attack_controller_actor(
        bundle, "workorder", "created_by_principal"
    ),
    "candidate_event_rebind": _attack_candidate_event_rebind,
}


def _assert_shape_valid(bundle: dict[str, Any]) -> None:
    registry = _registry()
    for key, schema_name in SINGULAR_SCHEMAS.items():
        errors = list(
            Draft202012Validator(
                _json(SCHEMAS / schema_name),
                registry=registry,
                format_checker=FormatChecker(),
            ).iter_errors(bundle[key])
        )
        assert not errors, f"{key}: " + "; ".join(error.message for error in errors)
    for key, schema_name in COLLECTION_SCHEMAS.items():
        validator = Draft202012Validator(
            _json(SCHEMAS / schema_name),
            registry=registry,
            format_checker=FormatChecker(),
        )
        for index, document in enumerate(bundle[key]):
            errors = list(validator.iter_errors(document))
            assert not errors, f"{key}[{index}]: " + "; ".join(
                error.message for error in errors
            )


def test_base_governance_bundle_is_fully_valid() -> None:
    bundle = _bundle()
    _assert_shape_valid(bundle)
    assert validate_semantics(bundle) == ()


def test_agent_separate_review_allows_same_model_independence_key() -> None:
    bundle = _bundle()
    resolution = bundle["resolution_contract"]
    review = bundle["resolution_review_receipts"][0]
    proposer_id = resolution["proposer"]["model_call_receipt"]["id"]
    reviewer_id = review["reviewer"]["execution"]["model_call_receipt"]["id"]
    proposer = _find(bundle["model_call_receipts"], "model_call_receipt_id", proposer_id)
    reviewer = _find(bundle["model_call_receipts"], "model_call_receipt_id", reviewer_id)
    assert resolution["review_policy"]["mode"] == "AGENT_SEPARATE"
    assert proposer["independence_key"] == reviewer["independence_key"]
    assert validate_semantics(bundle) == ()


def test_low_risk_relaxed_policy_allows_credential_only_independence() -> None:
    bundle = _bundle()
    _configure_credential_only(bundle, relaxed=True)
    generator = _find(
        bundle["model_call_receipts"],
        "model_call_receipt_id",
        "mcr_generator01",
    )
    judge = _find(
        bundle["model_call_receipts"], "model_call_receipt_id", "mcr_judge0001"
    )
    assert generator["independence_basis"]["provider_org"] == judge[
        "independence_basis"
    ]["provider_org"]
    assert generator["independence_basis"]["model_family"] == judge[
        "independence_basis"
    ]["model_family"]
    assert generator["independence_basis"]["credential_ref"] != judge[
        "independence_basis"
    ]["credential_ref"]
    assert generator["independence_key"] != judge["independence_key"]
    _assert_shape_valid(bundle)
    assert validate_semantics(bundle) == ()


def test_model_call_receipt_freezes_real_provider_provenance() -> None:
    receipt = _json(VALID / "model-call-receipt-generator.json")
    provenance = receipt["provider_provenance"]
    assert provenance["protocol"] == "STEPFUN_NATIVE"
    assert provenance["provider_request_id"]
    assert provenance["returned_model"] == receipt["resolved_model"]
    assert provenance["http_status"] == 200
    assert provenance["finish_reason"]
    assert provenance["token_usage"]["total_tokens"] == (
        provenance["token_usage"]["input_tokens"]
        + provenance["token_usage"]["output_tokens"]
    )
    assert receipt["usage_digest"] == compute_content_digest(
        provenance["token_usage"]
    )
    assert provenance["raw_retained"] is False
    assert provenance["call_mode"] == "REPLAY"


@pytest.mark.parametrize("fixture_name", ATTACK_FIXTURES)
def test_shape_valid_coordinated_attack_is_rejected_with_stable_codes(
    fixture_name: str,
) -> None:
    specification = _json(INVALID / fixture_name)
    bundle = _bundle()
    ATTACKS[specification["attack"]](bundle)
    _assert_shape_valid(bundle)
    codes = {item.code for item in validate_semantics(bundle)}
    assert set(specification["expected_invariants"]) <= codes
