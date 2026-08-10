"""Executable negative cases for cross-field and cross-document v4 semantics."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from v4_semantics import (
    SemanticValidationError,
    assert_semantically_valid,
    public_principal_context_violations,
    validate_semantics,
)


V4_ROOT = Path(__file__).resolve().parents[1] / "v4"
SCHEMAS = V4_ROOT / "schemas"
VALID = V4_ROOT / "fixtures" / "valid"
INVALID = V4_ROOT / "fixtures" / "invalid"

SEMANTIC_CASES = {
    "attempt-action-before-proposal.json": ("attempt", "attempt.schema.json"),
    "candidate-revision-planned-revision-mismatch.json": (
        "candidate_revision",
        "candidate-revision.schema.json",
    ),
    "candidate-contract-forbidden-scope-overlap.json": (
        "candidate_contract",
        "candidate-contract.schema.json",
    ),
    "candidate-contract-change-surface-exceeds-resolution.json": (
        "candidate_contract",
        "candidate-contract.schema.json",
    ),
    "candidate-contract-action-scope-exceeds-resolution.json": (
        "candidate_contract",
        "candidate-contract.schema.json",
    ),
    "candidate-contract-budget-exceeds-resolution.json": (
        "candidate_contract",
        "candidate-contract.schema.json",
    ),
    "candidate-revision-recorder-mismatch.json": (
        "candidate_revision",
        "candidate-revision.schema.json",
    ),
    "candidate-revision-recorded-before-attempt-terminal.json": (
        "candidate_revision",
        "candidate-revision.schema.json",
    ),
    "evaluation-plan-criteria-digest-mismatch.json": (
        "evaluation_plan",
        "evaluation-plan.schema.json",
    ),
    "participation-manifest-single-with-two-agents.json": (
        "participation_manifest",
        "participation-manifest.schema.json",
    ),
    "proposal-decision-causation-mismatch.json": (
        "proposal_decision",
        "proposal-decision.schema.json",
    ),
    "proposal-decision-transaction-mismatch.json": (
        "proposal_decision",
        "proposal-decision.schema.json",
    ),
    "proposal-parent-submits-child.json": ("proposal", "proposal.schema.json"),
    "proposal-agent-manifest-mismatch.json": ("proposal", "proposal.schema.json"),
    "candidate-revision-output-artifact-mismatch.json": (
        "candidate_revision",
        "candidate-revision.schema.json",
    ),
    "proposal-candidate-contract-mismatch.json": ("proposal", "proposal.schema.json"),
    "proposal-case-mismatch.json": ("proposal", "proposal.schema.json"),
    "proposal-input-snapshot-mismatch.json": ("proposal", "proposal.schema.json"),
    "proposal-output-artifact-mismatch.json": ("proposal", "proposal.schema.json"),
    "proposal-worker-task-mismatch.json": ("proposal", "proposal.schema.json"),
    "proposal-workspace-mismatch.json": ("proposal", "proposal.schema.json"),
    "resolution-contract-same-reviewer.json": (
        "resolution_contract",
        "resolution-contract.schema.json",
    ),
    "resolution-required-criterion-uncovered.json": (
        "resolution_contract",
        "resolution-contract.schema.json",
    ),
    "gate-report-v4-pass-missing-track.json": (
        "gate_report",
        "gate-report-v4.schema.json",
    ),
    "gate-report-v4-pass-duplicate-track.json": (
        "gate_report",
        "gate-report-v4.schema.json",
    ),
    "trace-evidence-receipt-duplicate-field-name.json": (
        "trace_evidence_receipt",
        "trace-evidence-receipt.schema.json",
    ),
    "workorder-v4-candidate-digest-mismatch.json": (
        "workorder",
        "workorder-v4.schema.json",
    ),
    "workorder-v4-base-digest-mismatch.json": (
        "workorder",
        "workorder-v4.schema.json",
    ),
    "workorder-v4-base-revision-mismatch.json": (
        "workorder",
        "workorder-v4.schema.json",
    ),
    "workorder-v4-diff-artifact-mismatch.json": (
        "workorder",
        "workorder-v4.schema.json",
    ),
    "workorder-v4-gate-digest-mismatch.json": (
        "workorder",
        "workorder-v4.schema.json",
    ),
    "workorder-v4-resolution-digest-mismatch.json": (
        "workorder",
        "workorder-v4.schema.json",
    ),
    "workorder-v4-target-repository-mismatch.json": (
        "workorder",
        "workorder-v4.schema.json",
    ),
    "workorder-v4-network-scope-exceeds-candidate.json": (
        "workorder",
        "workorder-v4.schema.json",
    ),
    "worker-task-team-mismatch.json": ("worker_task", "worker-task.schema.json"),
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    resources = []
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = _json(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def _shape_errors(schema_name: str, payload: dict[str, Any]) -> list[Any]:
    return list(
        Draft202012Validator(
            _json(SCHEMAS / schema_name),
            registry=_registry(),
            format_checker=FormatChecker(),
        ).iter_errors(payload)
    )


def _apply_mutations(specification: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(_json(VALID / specification["base_fixture"]))
    for mutation in specification["mutations"]:
        parts = mutation["path"].split(".")
        target: Any = payload
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        final = parts[-1]
        if mutation["op"] == "set":
            if isinstance(target, list):
                target[int(final)] = mutation["value"]
            else:
                target[final] = mutation["value"]
        elif mutation["op"] == "append":
            target[final].append(mutation["value"])
        elif mutation["op"] == "remove":
            if isinstance(target, list):
                del target[int(final)]
            else:
                del target[final]
        else:  # pragma: no cover - every fixture is enumerated above
            raise AssertionError(mutation["op"])
    return payload


def _valid_bundle() -> dict[str, Any]:
    authority = _json(VALID / "authority-bundle.json")
    return {
        "resolution_contract": _json(VALID / "resolution-contract.json"),
        "candidate_contract": _json(VALID / "candidate-contract.json"),
        "candidate_revision": _json(VALID / "candidate-revision.json"),
        "evaluation_plan": _json(VALID / "evaluation-plan.json"),
        "attempt": _json(VALID / "attempt.json"),
        "attempt_claim_snapshot": _json(VALID / "attempt-created.json"),
        "proposal_attempt_snapshot": _json(
            VALID / "attempt-output-recorded.json"
        ),
        "worker_task": _json(VALID / "worker-task.json"),
        "worker_task_claim_snapshot": _json(VALID / "worker-task-leased.json"),
        "agent_manifest": _json(VALID / "agent-manifest.json"),
        "capability_lease": _json(VALID / "capability-lease.json"),
        "proposal": _json(VALID / "proposal.json"),
        "proposal_decision": _json(VALID / "proposal-decision.json"),
        "participation_manifest": _json(VALID / "participation-manifest.json"),
        "trace_evidence_receipt": _json(VALID / "trace-evidence-receipt.json"),
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


def test_complete_valid_bundle_passes_reusable_semantic_validator() -> None:
    bundle = _valid_bundle()
    assert validate_semantics(bundle) == ()
    assert_semantically_valid(bundle)


@pytest.mark.parametrize("fixture_name", sorted(SEMANTIC_CASES))
def test_shape_valid_negative_fixture_is_rejected_semantically(
    fixture_name: str,
) -> None:
    target, schema_name = SEMANTIC_CASES[fixture_name]
    specification = _json(INVALID / fixture_name)
    mutated = _apply_mutations(specification)

    shape_errors = _shape_errors(schema_name, mutated)
    assert not shape_errors, "semantic negative must remain JSON-Schema-valid: " + "; ".join(
        error.message for error in shape_errors
    )

    bundle = _valid_bundle()
    bundle[target] = mutated
    violations = validate_semantics(bundle)
    codes = {violation.code for violation in violations}
    assert specification["expected_invariant"] in codes

    with pytest.raises(SemanticValidationError) as raised:
        assert_semantically_valid(bundle)
    assert specification["expected_invariant"] in {
        violation.code for violation in raised.value.violations
    }


def test_workorder_semantics_fail_closed_without_related_documents() -> None:
    violations = validate_semantics({"workorder": _json(VALID / "workorder-v4.json")})
    assert [
        violation.code
        for violation in violations
        if violation.code == "workorder.semantic_context_missing"
    ] == [
        "workorder.semantic_context_missing",
        "workorder.semantic_context_missing",
        "workorder.semantic_context_missing",
        "workorder.semantic_context_missing",
        "workorder.semantic_context_missing",
    ]
    assert {
        violation.path
        for violation in violations
        if violation.code == "workorder.semantic_context_missing"
    } == {
        ("resolution_contract",),
        ("candidate_contract",),
        ("candidate_revision",),
        ("evaluation_plan",),
        ("gate_report",),
    }


def test_proposal_semantics_fail_closed_without_task_and_candidate_context() -> None:
    bundle = {
        "attempt": _json(VALID / "attempt.json"),
        "proposal": _json(VALID / "proposal.json"),
    }
    assert {
        "proposal.worker_task_context_missing",
        "proposal.candidate_contract_context_missing",
        "identity_chain.context_missing",
    } <= {violation.code for violation in validate_semantics(bundle)}


def test_public_principal_context_passes_executable_semantic_validator() -> None:
    context = _json(VALID / "public-principal-context.json")
    assert public_principal_context_violations(context) == ()
    assert validate_semantics({"public_principal_context": context}) == ()
    context["requested_context"]["project_id"] = None
    context["requested_context"]["environment_id"] = None
    assert public_principal_context_violations(context) == ()


@pytest.mark.parametrize(
    "mutation,expected_code",
    [
        (
            ("evaluated_at", "2026-08-10T18:00:00Z"),
            "public_principal_context.outside_validity",
        ),
        (
            ("requested_context.workspace_id", "ws_01J0000000000099"),
            "public_principal_context.workspace_mismatch",
        ),
        (
            ("requested_context.project_id", "proj_01J0000000000099"),
            "public_principal_context.project_id_not_granted",
        ),
        (
            ("requested_context.environment_id", "env_01J0000000000099"),
            "public_principal_context.environment_id_not_granted",
        ),
        (
            ("requested_context.required_scope", "releases:write"),
            "public_principal_context.required_scope_not_granted",
        ),
        (
            ("revoked_at", "2026-08-10T08:59:00Z"),
            "public_principal_context.revoked_credential_accepted",
        ),
        (
            ("audiences", ["some-other-api"]),
            "public_principal_context.audience_not_accepted",
        ),
        (
            ("jti", "raw-token-id-must-not-escape"),
            "public_principal_context.raw_credential_exposed",
        ),
    ],
)
def test_public_principal_context_semantic_attacks_fail_closed(
    mutation: tuple[str, Any], expected_code: str
) -> None:
    context = copy.deepcopy(_json(VALID / "public-principal-context.json"))
    path, value = mutation
    if "." in path:
        parent, field = path.split(".", 1)
        context[parent][field] = value
    else:
        context[path] = value
    codes = {
        violation.code
        for violation in validate_semantics({"public_principal_context": context})
    }
    assert expected_code in codes


@pytest.mark.parametrize(
    "field,value",
    [("contract_version", "v3"), ("aggregate_type", "quality_case")],
)
def test_authority_event_routing_tuple_tamper_fails_semantics(
    field: str, value: str
) -> None:
    bundle = _valid_bundle()
    bundle["authority_events"] = copy.deepcopy(bundle["authority_events"])
    receipt_id = bundle["trace_evidence_receipt"]["authority_receipt_id"]
    event = next(
        item
        for item in bundle["authority_events"]
        if item["authority_receipt_id"] == receipt_id
    )
    event[field] = value
    assert "authority.event_binding_mismatch" in {
        violation.code for violation in validate_semantics(bundle)
    }
