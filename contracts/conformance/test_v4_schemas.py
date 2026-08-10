"""Self-contained conformance checks for the versioned v4 schema slice."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


V4_ROOT = Path(__file__).resolve().parents[1] / "v4"
SCHEMAS = V4_ROOT / "schemas"
VALID = V4_ROOT / "fixtures" / "valid"
INVALID = V4_ROOT / "fixtures" / "invalid"

VALID_FIXTURES = {
    "agent-intent.json": "agent-intent.schema.json",
    "agent-manifest.json": "agent-manifest.schema.json",
    "agent-manifest-planner.json": "agent-manifest.schema.json",
    "agent-manifest-reviewer.json": "agent-manifest.schema.json",
    "agent-manifest-judge.json": "agent-manifest.schema.json",
    "public-error.json": "public-error.schema.json",
    "idempotency-receipt.json": "idempotency-receipt.schema.json",
    "signal-envelope.json": "signal-envelope.schema.json",
    "agent-run-ref.json": "agent-run-ref.schema.json",
    "trace-evidence-receipt.json": "trace-evidence-receipt.schema.json",
    "attempt.json": "attempt.schema.json",
    "attempt-created.json": "attempt.schema.json",
    "attempt-starting.json": "attempt.schema.json",
    "attempt-running.json": "attempt.schema.json",
    "attempt-output-recorded.json": "attempt.schema.json",
    "attempt-planner-running.json": "attempt.schema.json",
    "attempt-planner.json": "attempt.schema.json",
    "attempt-reviewer-running.json": "attempt.schema.json",
    "attempt-reviewer.json": "attempt.schema.json",
    "attempt-judge-running.json": "attempt.schema.json",
    "attempt-judge.json": "attempt.schema.json",
    "candidate-contract.json": "candidate-contract.schema.json",
    "candidate-revision.json": "candidate-revision.schema.json",
    "capability-lease.json": "capability-lease.schema.json",
    "capability-lease-consumed.json": "capability-lease.schema.json",
    "capability-lease-root-dispatch.json": "capability-lease.schema.json",
    "controller-registration.json": "controller-registration.schema.json",
    "authority-receipt.json": "authority-receipt.schema.json",
    "evaluation-plan.json": "evaluation-plan.schema.json",
    "gate-report-v4-pass.json": "gate-report-v4.schema.json",
    "gate-report-v4-failed.json": "gate-report-v4.schema.json",
    "gate-report-v4-inconclusive.json": "gate-report-v4.schema.json",
    "gate-report-v4-error.json": "gate-report-v4.schema.json",
    "gate-report-v4-unknown.json": "gate-report-v4.schema.json",
    "gate-track-receipt-pass-deterministic.json": "gate-track-receipt.schema.json",
    "gate-track-receipt-pass-judge.json": "gate-track-receipt.schema.json",
    "gate-track-receipt-failed-deterministic.json": "gate-track-receipt.schema.json",
    "gate-track-receipt-failed-judge.json": "gate-track-receipt.schema.json",
    "gate-track-receipt-inconclusive-deterministic.json": "gate-track-receipt.schema.json",
    "gate-track-receipt-inconclusive-judge.json": "gate-track-receipt.schema.json",
    "gate-track-receipt-error-deterministic.json": "gate-track-receipt.schema.json",
    "gate-track-receipt-error-judge.json": "gate-track-receipt.schema.json",
    "gate-track-receipt-unknown-deterministic.json": "gate-track-receipt.schema.json",
    "gate-track-receipt-unknown-judge.json": "gate-track-receipt.schema.json",
    "mcp-manifest.json": "mcp-manifest.schema.json",
    "participation-manifest.json": "participation-manifest.schema.json",
    "proposal-decision.json": "proposal-decision.schema.json",
    "proposal.json": "proposal.schema.json",
    "resolution-contract.json": "resolution-contract.schema.json",
    "resolution-review-receipt.json": "resolution-review-receipt.schema.json",
    "model-call-receipt-generator.json": "model-call-receipt.schema.json",
    "model-call-receipt-planner.json": "model-call-receipt.schema.json",
    "model-call-receipt-reviewer.json": "model-call-receipt.schema.json",
    "model-call-receipt-judge.json": "model-call-receipt.schema.json",
    "skill-manifest.json": "skill-manifest.schema.json",
    "worker-task.json": "worker-task.schema.json",
    "worker-task-queued.json": "worker-task.schema.json",
    "worker-task-leased.json": "worker-task.schema.json",
    "workorder-v4.json": "workorder-v4.schema.json",
}

INVALID_FIXTURES = {
    "public-error-missing-retryable.json": "public-error.schema.json",
    "idempotency-receipt-bad-fingerprint.json": "idempotency-receipt.schema.json",
    "signal-envelope-missing-source-event-id.json": "signal-envelope.schema.json",
    "agent-run-ref-complete-with-missing-fields.json": "agent-run-ref.schema.json",
    "trace-evidence-receipt-complete-with-missing-fields.json": "trace-evidence-receipt.schema.json",
}

STRUCTURAL_MUTATION_FIXTURES = {
    "agent-intent-after-action.json": "agent-intent.schema.json",
    "agent-manifest-silent-fallback.json": "agent-manifest.schema.json",
    "attempt-fallback-without-parent.json": "attempt.schema.json",
    "attempt-succeeded-with-truncated-stream.json": "attempt.schema.json",
    "evaluation-plan-generator-holdout-access.json": "evaluation-plan.schema.json",
    "gate-report-v4-pass-with-failed-track.json": "gate-report-v4.schema.json",
    "mcp-manifest-missing-output-schema.json": "mcp-manifest.schema.json",
    "proposal-after-action.json": "proposal.schema.json",
    "proposal-decision-without-downstream.json": "proposal-decision.schema.json",
    "skill-manifest-expanded-without-approval.json": "skill-manifest.schema.json",
    "worker-task-delegated-without-parent.json": "worker-task.schema.json",
    "workorder-v4-failed-gate.json": "workorder-v4.schema.json",
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    resources = []
    for path in sorted(SCHEMAS.glob("*.schema.json")):
        schema = _json(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(
        _json(SCHEMAS / schema_name),
        registry=_registry(),
        format_checker=FormatChecker(),
    )


def _mutated_fixture(name: str) -> dict[str, Any]:
    specification = _json(INVALID / name)
    payload = _json(VALID / specification["base_fixture"])
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
                target[int(final)]
            else:
                del target[final]
        else:  # pragma: no cover - fixtures are enumerated below
            raise AssertionError(f"unsupported mutation: {mutation['op']}")
    return payload


def test_every_v4_schema_is_draft_2020_12_and_has_stable_id() -> None:
    paths = sorted(SCHEMAS.glob("*.schema.json"))
    assert {path.name for path in paths} == {
        "agent-run-ref.schema.json",
        "agent-intent.schema.json",
        "agent-manifest.schema.json",
        "attempt.schema.json",
        "authority-receipt.schema.json",
        "candidate-contract.schema.json",
        "candidate-revision.schema.json",
        "capability-lease.schema.json",
        "common-defs.schema.json",
        "controller-registration.schema.json",
        "evaluation-plan.schema.json",
        "gate-report-v4.schema.json",
        "gate-track-receipt.schema.json",
        "idempotency-receipt.schema.json",
        "mcp-manifest.schema.json",
        "model-call-receipt.schema.json",
        "participation-manifest.schema.json",
        "proposal-decision.schema.json",
        "proposal.schema.json",
        "public-error.schema.json",
        "resolution-contract.schema.json",
        "resolution-review-receipt.schema.json",
        "signal-envelope.schema.json",
        "skill-manifest.schema.json",
        "trace-evidence-receipt.schema.json",
        "worker-task.schema.json",
        "workorder-v4.schema.json",
    }
    for path in paths:
        schema = _json(path)
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["$id"] == f"https://caseloop.dev/contracts/v4/schemas/{path.name}"
        Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("fixture_name,schema_name", sorted(VALID_FIXTURES.items()))
def test_positive_fixture_validates(fixture_name: str, schema_name: str) -> None:
    errors = list(_validator(schema_name).iter_errors(_json(VALID / fixture_name)))
    assert not errors, "\n".join(
        f"{list(error.absolute_path)}: {error.message}" for error in errors
    )


@pytest.mark.parametrize("fixture_name,schema_name", sorted(INVALID_FIXTURES.items()))
def test_negative_fixture_is_rejected(fixture_name: str, schema_name: str) -> None:
    errors = list(_validator(schema_name).iter_errors(_json(INVALID / fixture_name)))
    assert errors, f"negative fixture {fixture_name} unexpectedly passed {schema_name}"


@pytest.mark.parametrize(
    "fixture_name,schema_name", sorted(STRUCTURAL_MUTATION_FIXTURES.items())
)
def test_structural_mutation_fixture_is_rejected(
    fixture_name: str, schema_name: str
) -> None:
    errors = list(_validator(schema_name).iter_errors(_mutated_fixture(fixture_name)))
    assert errors, f"negative mutation {fixture_name} unexpectedly passed {schema_name}"


def test_resolution_requires_a_distinct_agent_review_execution() -> None:
    valid = _json(VALID / "resolution-contract.json")
    receipt = _json(VALID / "resolution-review-receipt.json")
    assert valid["review_policy"] == {
        "mode": "AGENT_SEPARATE",
        "required_distinct_dimensions": [],
    }
    assert valid["review_receipt"]["id"] == receipt["resolution_review_receipt_id"]
    assert (
        valid["proposer"]["attempt"]["id"]
        != receipt["reviewer"]["execution"]["attempt"]["id"]
    )


def test_candidate_contract_contains_only_pre_build_facts() -> None:
    candidate = _json(VALID / "candidate-contract.json")
    assert candidate["planned_revision"] == 1
    assert {
        "candidate_artifact",
        "diff_artifact",
        "target",
        "producer_attempt_id",
        "proposed_by_attempt_id",
        "reviewed_by_attempt_id",
    }.isdisjoint(candidate)
    revision = _json(VALID / "candidate-revision.json")
    assert revision["candidate_id"] == candidate["candidate_id"]
    assert revision["revision"] == candidate["planned_revision"]
    assert revision["base"] == candidate["base"]


def test_attempt_fallback_and_pre_controlled_action_order_are_explicit() -> None:
    attempt = _json(VALID / "attempt.json")
    proposal = attempt["pre_controlled_action"]
    assert proposal["submitted_at"] < proposal["controlled_action_started_at"]
    assert attempt["terminal_receipt"] == {
        "process_exit_code": 0,
        "stream_complete": True,
        "structured_output_valid": True,
        "receipt_digest": attempt["terminal_receipt"]["receipt_digest"],
    }
    fallback = _mutated_fixture("attempt-fallback-without-parent.json")
    assert fallback["fallback_of_attempt_id"] is None


def test_proposal_has_one_content_digest_and_decision_is_causally_bound() -> None:
    proposal = _json(VALID / "proposal.json")
    assert "content_digest" not in proposal
    decision = _json(VALID / "proposal-decision.json")
    downstream = decision["downstream_event"]
    assert decision["proposal_id"] == decision["accepted_proposal_id"]
    assert downstream["accepted_proposal_id"] == decision["accepted_proposal_id"]
    assert downstream["causation_id"] == decision["decision_event_id"]
    assert downstream["transaction_id"] == decision["transaction_id"]


def test_agent_manifest_required_skills_were_actually_called() -> None:
    manifest = _json(VALID / "agent-manifest.json")
    attempt = _json(VALID / "attempt.json")
    required = {
        skill["manifest_digest"]
        for skill in manifest["skills"]
        if skill["required_for_success"]
    }
    called = {
        usage["skill_manifest_digest"]
        for usage in attempt["skill_usage"]
        if usage["called"]
    }
    assert required <= called
    fallback_names = {
        model["model"] for model in manifest["model_policy"]["allowed_fallback_models"]
    }
    assert manifest["model_policy"]["primary_model"] not in fallback_names


def test_participation_manifest_records_real_business_identities() -> None:
    manifest = _json(VALID / "participation-manifest.json")
    assert manifest["mode"] == "SINGLE_AGENT"
    assert len(manifest["participants"]) == 1
    invalid = _mutated_fixture("participation-manifest-single-with-two-agents.json")
    assert invalid["mode"] == "SINGLE_AGENT" and len(invalid["participants"]) != 1


def test_effective_permissions_are_the_exact_four_way_intersection() -> None:
    def canonical(items: list[dict[str, Any]]) -> set[str]:
        return {json.dumps(item, sort_keys=True, separators=(",", ":")) for item in items}

    for fixture_name, expected in (
        ("capability-lease.json", True),
        ("capability-lease-permission-escalation.json", False),
    ):
        lease = (
            _json(VALID / fixture_name)
            if expected
            else _mutated_fixture(fixture_name)
        )
        valid = True
        for surface_name, effective_items in lease["effective_permissions"].items():
            effective = canonical(effective_items)
            sources = [
                canonical(source[surface_name])
                for source in lease["source_permissions"].values()
            ]
            valid = valid and all(effective <= source for source in sources)
        assert valid is expected


def test_workorder_is_bound_to_exact_frozen_chain_and_pass() -> None:
    workorder = _json(VALID / "workorder-v4.json")
    candidate = _json(VALID / "candidate-contract.json")
    revision = _json(VALID / "candidate-revision.json")
    plan = _json(VALID / "evaluation-plan.json")
    gate = _json(VALID / "gate-report-v4-pass.json")
    assert workorder["gate_status"] == "PASS"
    assert workorder["candidate_id"] == candidate["candidate_id"]
    assert workorder["candidate_contract"]["digest"] == candidate["contract_digest"]
    assert workorder["candidate_revision"]["digest"] == revision["revision_digest"]
    assert workorder["candidate_artifact"] == revision["candidate_artifact"]
    assert workorder["diff_artifact"] == revision["diff_artifact"]
    assert workorder["evaluation_plan"]["digest"] == plan["plan_digest"]
    assert workorder["gate_report"]["digest"] == gate["gate_report_digest"]


def test_evidence_facets_are_canonical_and_not_combinable() -> None:
    facets = _json(SCHEMAS / "common-defs.schema.json")["$defs"]["evidence_facet"]["enum"]
    assert facets == [
        "contract",
        "replay",
        "domain-provider-live",
        "agentteams-native",
        "claude-runtime-live",
        "agent-causal",
        "repo-sandbox",
        "human-authorized-external",
        "production-canary",
    ]
    assert all("+" not in facet and "," not in facet and "_" not in facet for facet in facets)


def test_signal_fixture_has_one_content_digest_and_keeps_source_identity_separate() -> None:
    signal = _json(VALID / "signal-envelope.json")
    assert signal["content_ref"]["digest"].startswith("sha256:")
    assert "content_digest" not in signal, "do not duplicate a digest that can diverge"
    assert signal["source"]["source_event_id"] != signal["signal_id"]
    assert "case_id" not in signal, "immutable source fact must not own Case linkage"
    assert signal["untrusted_content"] is True


def test_trace_receipt_reports_each_requested_field_once() -> None:
    receipt = _json(VALID / "trace-evidence-receipt.json")
    results = receipt["field_results"]
    names = [result["name"] for result in results]
    observed = {result["name"] for result in results if result["status"] == "OBSERVED"}
    missing = {result["name"] for result in results if result["status"] == "MISSING"}
    assert len(names) == len(set(names)), "a requested field must have one terminal result"
    assert observed.isdisjoint(missing)
    assert observed | missing == set(names)
    assert all(
        "reason_digest" in result
        for result in results
        if result["status"] == "MISSING"
    )
    assert receipt["completeness"] == "PARTIAL"
    assert receipt["failure"] is None

    duplicate = _mutated_fixture("trace-evidence-receipt-duplicate-field-name.json")
    duplicate_names = [result["name"] for result in duplicate["field_results"]]
    assert len(duplicate_names) != len(set(duplicate_names))


def test_contract_fixtures_never_contain_credentials() -> None:
    forbidden = {"api_key", "secret", "password", "authorization", "access_token"}

    def keys(value: Any) -> set[str]:
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()

    for path in sorted((V4_ROOT / "fixtures").glob("**/*.json")):
        assert forbidden.isdisjoint(keys(_json(path))), path
