"""RFC 8785, immutable digest, GateReport, and WorkOrder v4 conformance."""
from __future__ import annotations

import copy
import importlib.metadata
import json
from pathlib import Path
from typing import Any

import pytest

from v4_integrity import (
    IMMUTABLE_DIGEST_FIELDS,
    IntegrityValidationError,
    UnsupportedJCSValue,
    assert_gate_chain,
    assert_record_integrity,
    canonical_json_bytes,
    compute_content_digest,
    exact_record_binding,
    gate_chain_violations,
    gate_report_violations,
    record_integrity_violations,
    revision_chain_violations,
    workorder_chain_violations,
)


V4_ROOT = Path(__file__).resolve().parents[1] / "v4"
VALID = V4_ROOT / "fixtures" / "valid"
INVALID = V4_ROOT / "fixtures" / "invalid"

IMMUTABLE_FIXTURES = {
    "agent_intent": "agent-intent.json",
    "agent_manifest": "agent-manifest.json",
    "agent_run_ref": "agent-run-ref.json",
    "attempt": "attempt.json",
    "authority_receipt": "authority-receipt.json",
    "resolution_contract": "resolution-contract.json",
    "resolution_review_receipt": "resolution-review-receipt.json",
    "candidate_contract": "candidate-contract.json",
    "candidate_revision": "candidate-revision.json",
    "capability_lease": "capability-lease.json",
    "controller_registration": "controller-registration.json",
    "evaluation_plan": "evaluation-plan.json",
    "idempotency_receipt": "idempotency-receipt.json",
    "mcp_manifest": "mcp-manifest.json",
    "model_call_receipt": "model-call-receipt-generator.json",
    "participation_manifest": "participation-manifest.json",
    "proposal": "proposal.json",
    "proposal_decision": "proposal-decision.json",
    "signal_envelope": "signal-envelope.json",
    "skill_manifest": "skill-manifest.json",
    "trace_evidence_receipt": "trace-evidence-receipt.json",
    "worker_task": "worker-task.json",
    "gate_report": "gate-report-v4-pass.json",
    "gate_track_receipt": "gate-track-receipt-pass-judge.json",
    "workorder": "workorder-v4.json",
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _bundle() -> dict[str, dict[str, Any]]:
    return {name: _json(VALID / fixture) for name, fixture in IMMUTABLE_FIXTURES.items()}


def _gate_tracks(status: str) -> list[dict[str, Any]]:
    return [
        _json(VALID / f"gate-track-receipt-{status}-deterministic.json"),
        _json(VALID / f"gate-track-receipt-{status}-judge.json"),
    ]


def _apply_mutations(payload: dict[str, Any], specification: dict[str, Any]) -> None:
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
        else:  # pragma: no cover - fixtures enumerate all operations
            raise AssertionError(mutation["op"])


def _codes(violations: tuple[Any, ...]) -> set[str]:
    return {item.code for item in violations}


def test_pinned_rfc8785_supports_unicode_without_normalization() -> None:
    assert importlib.metadata.version("rfc8785") == "0.1.4"
    value = {"😀": "emoji", "中": "值", "a": "e\u0301"}
    assert canonical_json_bytes(value) == '{"a":"é","中":"值","😀":"emoji"}'.encode()
    assert canonical_json_bytes({"v": "é"}) != canonical_json_bytes(
        {"v": "e\u0301"}
    )


@pytest.mark.parametrize("value", [0.0, -0.0, float("nan"), float("inf")])
def test_jcs_profile_rejects_all_floats(value: float) -> None:
    with pytest.raises(UnsupportedJCSValue):
        canonical_json_bytes({"value": value})


def test_jcs_profile_rejects_lone_surrogate_and_unsafe_integer() -> None:
    with pytest.raises(UnsupportedJCSValue):
        canonical_json_bytes({"value": "\ud800"})
    with pytest.raises(UnsupportedJCSValue):
        canonical_json_bytes({"value": 2**53})


@pytest.mark.parametrize("record_name", sorted(IMMUTABLE_FIXTURES))
def test_every_immutable_fixture_has_a_recomputable_digest(record_name: str) -> None:
    document = _bundle()[record_name]
    digest_field = IMMUTABLE_DIGEST_FIELDS[record_name]
    assert record_integrity_violations(document, digest_field) == ()
    assert_record_integrity(document, digest_field)


@pytest.mark.parametrize(
    "record_name,path,value",
    [
        ("resolution_contract", ("objective",), "substituted objective"),
        ("candidate_contract", ("planned_revision",), 2),
        ("candidate_revision", ("target", "revision"), "substituted-target"),
        ("evaluation_plan", ("visible_suite", "dataset_version"), "9.9.9"),
        ("proposal", ("content_ref", "uri"), "artifact://stage0/other.patch"),
        ("proposal_decision", ("reason_code",), "SUBSTITUTED"),
        ("gate_report", ("completed_at",), "2026-08-10T02:01:00Z"),
        ("workorder", ("nonce",), "nonce_substituted00001"),
    ],
)
def test_content_substitution_is_rejected_by_frozen_digest(
    record_name: str, path: tuple[str, ...], value: Any
) -> None:
    document = copy.deepcopy(_bundle()[record_name])
    target: Any = document
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value
    violations = record_integrity_violations(
        document, IMMUTABLE_DIGEST_FIELDS[record_name]
    )
    assert "integrity.digest_mismatch" in _codes(violations)
    with pytest.raises(IntegrityValidationError):
        assert_record_integrity(document, IMMUTABLE_DIGEST_FIELDS[record_name])


@pytest.mark.parametrize(
    "record_name",
    sorted(
        set(IMMUTABLE_FIXTURES)
        - {
            "resolution_contract",
            "candidate_contract",
            "candidate_revision",
            "evaluation_plan",
            "proposal",
            "proposal_decision",
            "gate_report",
            "workorder",
        }
    ),
)
def test_new_record_self_hash_rejects_coordinated_content_substitution(
    record_name: str,
) -> None:
    document = copy.deepcopy(_bundle()[record_name])
    if "authority_receipt_id" in document:
        document["authority_receipt_id"] = "arec_substitute0001"
    elif record_name == "agent_run_ref":
        document["completeness"] = "UNKNOWN"
    elif record_name == "idempotency_receipt":
        document["response_digest"] = "sha256:" + "f" * 64
    elif record_name == "signal_envelope":
        document["untrusted_content"] = not document["untrusted_content"]
    elif record_name == "controller_registration":
        document["state"] = "REVOKED"
    else:  # pragma: no cover - parameter set is explicit
        raise AssertionError(record_name)
    assert "integrity.digest_mismatch" in _codes(
        record_integrity_violations(
            document, IMMUTABLE_DIGEST_FIELDS[record_name]
        )
    )


def test_content_anchors_are_distinct_from_record_self_hashes() -> None:
    mcp = _json(VALID / "mcp-manifest.json")
    run_ref = _json(VALID / "agent-run-ref.json")
    assert mcp["tool_catalog_digest"] == compute_content_digest(mcp["tools"])
    assert run_ref["locator_digest"] == compute_content_digest(run_ref["locator"])
    assert mcp["manifest_digest"] != mcp["tool_catalog_digest"]
    assert run_ref["agent_run_ref_digest"] != run_ref["locator_digest"]


@pytest.mark.parametrize(
    "record_name,fixtures",
    [
        (
            "worker_task",
            ["worker-task-queued.json", "worker-task-leased.json", "worker-task.json"],
        ),
        (
            "attempt",
            [
                "attempt-created.json",
                "attempt-starting.json",
                "attempt-running.json",
                "attempt-output-recorded.json",
                "attempt.json",
            ],
        ),
        (
            "capability_lease",
            ["capability-lease.json", "capability-lease-consumed.json"],
        ),
    ],
)
def test_historical_snapshot_revisions_are_contiguous_and_exact(
    record_name: str, fixtures: list[str]
) -> None:
    snapshots = [_json(VALID / name) for name in fixtures]
    assert revision_chain_violations(record_name, snapshots[0], None) == ()
    for previous, current in zip(snapshots, snapshots[1:]):
        assert revision_chain_violations(record_name, current, previous) == ()

    substituted = copy.deepcopy(snapshots[-1])
    substituted["previous_snapshot"]["digest"] = "sha256:" + "f" * 64
    assert "revision.previous_binding_mismatch" in _codes(
        revision_chain_violations(record_name, substituted, snapshots[-2])
    )


@pytest.mark.parametrize(
    "fixture",
    [
        "gate-report-v4-pass.json",
        "gate-report-v4-failed.json",
        "gate-report-v4-inconclusive.json",
        "gate-report-v4-error.json",
        "gate-report-v4-unknown.json",
    ],
)
def test_all_terminal_gate_statuses_have_recomputable_non_promoting_evidence(
    fixture: str,
) -> None:
    report = _json(VALID / fixture)
    plan = _json(VALID / "evaluation-plan.json")
    status = fixture.removeprefix("gate-report-v4-").removesuffix(".json")
    receipts = _gate_tracks(status)
    assert record_integrity_violations(report, "gate_report_digest") == ()
    assert gate_report_violations(report, plan, receipts) == ()


def test_exact_pass_gate_chain_is_valid() -> None:
    bundle = _bundle()
    assert gate_chain_violations(
        bundle["gate_report"],
        bundle["resolution_contract"],
        bundle["candidate_contract"],
        bundle["candidate_revision"],
        bundle["evaluation_plan"],
        _gate_tracks("pass"),
    ) == ()
    assert_gate_chain(
        bundle["gate_report"],
        bundle["resolution_contract"],
        bundle["candidate_contract"],
        bundle["candidate_revision"],
        bundle["evaluation_plan"],
        _gate_tracks("pass"),
    )


@pytest.mark.parametrize(
    "fixture",
    [
        "gate-report-v4-pass-missing-track.json",
        "gate-report-v4-pass-duplicate-track.json",
    ],
)
def test_shape_valid_gate_track_substitution_is_rejected_semantically(
    fixture: str,
) -> None:
    specification = _json(INVALID / fixture)
    report = _json(VALID / specification["base_fixture"])
    _apply_mutations(report, specification)
    violations = gate_report_violations(
        report,
        _json(VALID / "evaluation-plan.json"),
        _gate_tracks("pass"),
    )
    assert specification["expected_invariant"] in _codes(violations)
    assert "gate_report.pass_iff_complete" in _codes(violations)


def test_candidate_revision_and_gate_subject_substitution_fail_closed() -> None:
    bundle = _bundle()
    revision = copy.deepcopy(bundle["candidate_revision"])
    revision["revision"] = 2
    assert "gate_report.contract_chain_mismatch" in _codes(
        gate_chain_violations(
            bundle["gate_report"],
            bundle["resolution_contract"],
            bundle["candidate_contract"],
            revision,
            bundle["evaluation_plan"],
            _gate_tracks("pass"),
        )
    )
    report = copy.deepcopy(bundle["gate_report"])
    report["candidate_revision"]["digest"] = "sha256:" + "f" * 64
    assert "gate_report.candidate_revision_mismatch" in _codes(
        gate_chain_violations(
            report,
            bundle["resolution_contract"],
            bundle["candidate_contract"],
            bundle["candidate_revision"],
            bundle["evaluation_plan"],
            _gate_tracks("pass"),
        )
    )


def test_exact_workorder_chain_is_valid() -> None:
    bundle = _bundle()
    assert workorder_chain_violations(
        bundle["workorder"],
        bundle["resolution_contract"],
        bundle["candidate_contract"],
        bundle["candidate_revision"],
        bundle["evaluation_plan"],
        bundle["gate_report"],
        _gate_tracks("pass"),
    ) == ()


@pytest.mark.parametrize(
    "mutations,expected_code",
    [
        ([('workorder', ('workspace_id',), 'ws_other0001')], "workorder.subject_context_mismatch"),
        ([('workorder', ('case_id',), 'case_other0001')], "workorder.subject_context_mismatch"),
        ([('workorder', ('action_scope', 'files'), [{'path': '.env', 'access': 'WRITE'}])], "workorder.action_scope_exceeds_candidate"),
        ([('workorder', ('action_scope', 'processes'), [{'executable': 'curl', 'arguments_digest': 'sha256:' + 'a' * 64}])], "workorder.action_scope_exceeds_candidate"),
        ([('workorder', ('action_scope', 'network'), [{'origin': 'https://example.invalid', 'methods': ['POST']}])], "workorder.action_scope_exceeds_candidate"),
        ([('workorder', ('action_scope', 'mcp_tools'), [{'server': 'unapproved', 'tool': 'write', 'schema_digest': 'sha256:' + 'b' * 64}])], "workorder.action_scope_exceeds_candidate"),
        ([('workorder', ('action_scope', 'cloud_actions'), [{'provider': 'github', 'action': 'PULL_REQUEST_OPEN', 'resource': 'repo'}])], "workorder.action_scope_exceeds_candidate"),
        ([('workorder', ('action_scope', 'secret_refs'), [{'name': 'EVIL_KEY', 'credential_ref': 'credential://unapproved/key'}])], "workorder.action_scope_exceeds_candidate"),
        ([('resolution_contract', ('risk_class',), 'R2_HIGH_IMPACT')], "workorder.risk_downgrade"),
        ([('workorder', ('risk_class',), 'R2_HIGH_IMPACT'), ('workorder', ('required_authorization',), 'POLICY_GRANT')], "workorder.high_risk_requires_human"),
        ([('workorder', ('expires_at',), '2026-08-10T01:00:00Z')], "workorder.invalid_expiry"),
        ([('workorder', ('created_at',), '2026-08-10T01:58:00Z')], "workorder.created_before_gate_terminal"),
        ([('workorder', ('target', 'target_digest'), 'sha256:' + 'f' * 64)], "workorder.target_chain_mismatch"),
        ([('workorder', ('diff_artifact', 'digest'), 'sha256:' + 'f' * 64)], "workorder.diff_artifact_mismatch"),
    ],
)
def test_workorder_scope_risk_expiry_and_artifact_substitutions_fail_closed(
    mutations: list[tuple[str, tuple[str, ...], Any]], expected_code: str
) -> None:
    bundle = copy.deepcopy(_bundle())
    for record_name, path, value in mutations:
        target: Any = bundle[record_name]
        for part in path[:-1]:
            target = target[part]
        target[path[-1]] = value
    violations = workorder_chain_violations(
        bundle["workorder"],
        bundle["resolution_contract"],
        bundle["candidate_contract"],
        bundle["candidate_revision"],
        bundle["evaluation_plan"],
        bundle["gate_report"],
        _gate_tracks("pass"),
    )
    assert expected_code in _codes(violations)


def test_non_pass_gate_cannot_authorize_workorder() -> None:
    bundle = _bundle()
    failed = _json(VALID / "gate-report-v4-failed.json")
    violations = workorder_chain_violations(
        bundle["workorder"],
        bundle["resolution_contract"],
        bundle["candidate_contract"],
        bundle["candidate_revision"],
        bundle["evaluation_plan"],
        failed,
        _gate_tracks("failed"),
    )
    assert "workorder.gate_not_pass" in _codes(violations)


@pytest.mark.parametrize(
    "path,value,expected_code",
    [
        (("started_at",), "2026-08-10T01:50:00Z", "gate_report.started_before_candidate_revision"),
        (("completed_at",), "2026-08-10T01:52:00Z", "gate_report.invalid_terminal_window"),
        (("gate_track_receipts", 0, "completed_at"), "2026-08-10T02:00:00Z", "gate_report.track_outside_execution_window"),
    ],
)
def test_gate_and_receipt_timeline_substitution_fails_closed(
    path: tuple[str | int, ...], value: Any, expected_code: str
) -> None:
    bundle = _bundle()
    report = copy.deepcopy(bundle["gate_report"])
    receipts = copy.deepcopy(_gate_tracks("pass"))
    target: Any = (
        receipts if path and path[0] == "gate_track_receipts" else report
    )
    target_path = path[1:] if path and path[0] == "gate_track_receipts" else path
    for part in target_path[:-1]:
        target = target[part]
    target[target_path[-1]] = value
    violations = gate_chain_violations(
        report,
        bundle["resolution_contract"],
        bundle["candidate_contract"],
        bundle["candidate_revision"],
        bundle["evaluation_plan"],
        receipts,
    )
    assert expected_code in _codes(violations)
