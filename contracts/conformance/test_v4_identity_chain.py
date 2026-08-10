"""Fail-closed identity, claim and capability-chain conformance for v4."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from v4_integrity import compute_record_digest, exact_record_binding
from v4_semantics import validate_semantics


V4_ROOT = Path(__file__).resolve().parents[1] / "v4"
SCHEMAS = V4_ROOT / "schemas"
VALID = V4_ROOT / "fixtures" / "valid"
INVALID = V4_ROOT / "fixtures" / "invalid"

DOCUMENT_SCHEMAS = {
    "worker_task": "worker-task.schema.json",
    "worker_task_claim_snapshot": "worker-task.schema.json",
    "attempt": "attempt.schema.json",
    "attempt_claim_snapshot": "attempt.schema.json",
    "proposal_attempt_snapshot": "attempt.schema.json",
    "proposal": "proposal.schema.json",
    "proposal_decision": "proposal-decision.schema.json",
    "agent_manifest": "agent-manifest.schema.json",
    "participation_manifest": "participation-manifest.schema.json",
    "capability_lease": "capability-lease.schema.json",
    "delegation_proposal": "proposal.schema.json",
    "delegation_decision": "proposal-decision.schema.json",
    "candidate_contract": "candidate-contract.schema.json",
}

IDENTITY_CASES = (
    "identity-rebind-workspace.json",
    "identity-rebind-case.json",
    "identity-rebind-worker-task.json",
    "identity-rebind-input.json",
    "identity-rebind-team.json",
    "identity-rebind-team-manifest.json",
    "identity-rebind-role.json",
    "identity-rebind-role-manifest.json",
    "identity-rebind-agent-manifest.json",
    "identity-rebind-principal.json",
    "identity-rebind-participant.json",
    "identity-rebind-participation-manifest.json",
    "identity-rebind-requester.json",
    "identity-terminal-attempt-with-queued-task.json",
    "identity-rebind-attempt-capability.json",
    "identity-attempt-points-to-dispatch-capability.json",
    "identity-rebind-capability-task.json",
    "identity-rebind-capability-attempt.json",
    "identity-rebind-claim-event.json",
    "identity-rebind-claim-lease.json",
    "identity-rebind-claim-fence.json",
    "identity-rebind-claim-principal.json",
    "identity-rebind-native-session.json",
    "identity-rebind-capability-resource.json",
    "identity-runtime-capability-before-claim.json",
    "identity-rebind-permission-ceiling.json",
    "capability-lease-permission-escalation.json",
    "identity-rebind-capability-resolution.json",
    "identity-rebind-runtime-policy.json",
    "identity-required-skill-not-called.json",
    "identity-rebind-decision-proposal-id.json",
    "identity-rebind-decision-proposal-digest.json",
    "identity-rejected-proposal-cannot-complete.json",
    "identity-action-before-decision.json",
    "identity-decision-before-submission.json",
    "delegated-rebind-accepted-proposal.json",
    "delegated-rebind-child-proposal.json",
    "delegated-rebind-parent-attempt.json",
    "delegated-rebind-child-input.json",
    "delegated-rebind-capability-requirements.json",
    "delegated-rebind-decision-proposal.json",
    "delegated-rebind-decision-event.json",
    "delegated-rebind-downstream-event.json",
    "delegated-rebind-transaction.json",
    "delegated-rebind-causation-task.json",
    "delegated-rebind-causation-task-digest.json",
    "delegated-downstream-not-child-work.json",
    "root-dispatch-cannot-own-delegated-task.json",
    "root-dispatch-rebind-issuer.json",
    "root-dispatch-completed-task.json",
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _registry() -> Registry:
    return Registry().with_resources(
        [
            (_json(path)["$id"], Resource.from_contents(_json(path)))
            for path in sorted(SCHEMAS.glob("*.schema.json"))
        ]
    )


def _shape_errors(document_name: str, payload: dict[str, Any]) -> list[Any]:
    return list(
        Draft202012Validator(
            _json(SCHEMAS / DOCUMENT_SCHEMAS[document_name]),
            registry=_registry(),
            format_checker=FormatChecker(),
        ).iter_errors(payload)
    )


def _queued_worker_task() -> dict[str, Any]:
    return _json(VALID / "worker-task-queued.json")


def _proposal_fixture() -> dict[str, Any]:
    """Return the canonical pre-controlled-action Proposal fixture."""
    return _json(VALID / "proposal.json")


def _authority_context() -> dict[str, Any]:
    context = _json(VALID / "authority-bundle.json")
    return {
        "controller_registrations": context["registrations"],
        "authority_receipts": context["receipts"],
        "authority_events": context["events"],
        "authority_audits": context["audits"],
    }


def _identity_bundle() -> dict[str, Any]:
    return {
        "worker_task": _json(VALID / "worker-task.json"),
        "worker_task_claim_snapshot": _json(VALID / "worker-task-leased.json"),
        "attempt": _json(VALID / "attempt.json"),
        "attempt_claim_snapshot": _json(VALID / "attempt-created.json"),
        "proposal_attempt_snapshot": _json(
            VALID / "attempt-output-recorded.json"
        ),
        "proposal": _proposal_fixture(),
        "proposal_decision": _json(VALID / "proposal-decision.json"),
        "candidate_contract": _json(VALID / "candidate-contract.json"),
        "agent_manifest": _json(VALID / "agent-manifest.json"),
        "participation_manifest": _json(VALID / "participation-manifest.json"),
        "capability_lease": _json(VALID / "capability-lease.json"),
        **_authority_context(),
    }


def _root_dispatch_bundle() -> dict[str, Any]:
    return {
        "worker_task": _queued_worker_task(),
        "agent_manifest": _json(VALID / "agent-manifest.json"),
        "participation_manifest": _json(VALID / "participation-manifest.json"),
        "capability_lease": _json(VALID / "capability-lease-root-dispatch.json"),
        **_authority_context(),
    }


def _rehash(document: dict[str, Any], digest_field: str) -> None:
    document[digest_field] = compute_record_digest(document, digest_field)


def _refresh_authority(
    bundle: dict[str, Any],
    document: dict[str, Any],
    record_name: str,
    digest_field: str,
) -> None:
    _rehash(document, digest_field)
    receipt = next(
        item
        for item in bundle["authority_receipts"]
        if item["authority_receipt_id"] == document["authority_receipt_id"]
    )
    receipt["subject"] = exact_record_binding(record_name, document)
    for event in bundle["authority_events"]:
        if event["authority_receipt_id"] == receipt["authority_receipt_id"]:
            event["subject"] = copy.deepcopy(receipt["subject"])
    for audit in bundle["authority_audits"]:
        if audit["authority_receipt_id"] == receipt["authority_receipt_id"]:
            audit["subject"] = copy.deepcopy(receipt["subject"])
    _rehash(receipt, "authority_receipt_digest")


def _delegated_dispatch_bundle() -> dict[str, Any]:
    bundle = _root_dispatch_bundle()
    task = bundle["worker_task"]
    task.update(
        {
            "task_kind": "DELEGATED_RUNTIME",
            "parent_worker_task_id": "wt_parent0001",
            "parent_attempt_id": "att_parent0001",
            "delegation_proposal_id": "prop_delegate01",
        }
    )
    proposal = _proposal_fixture()
    proposal.update(
        {
            "proposal_id": "prop_delegate01",
            "worker_task_id": "wt_parent0001",
            "authored_by_attempt_id": "att_parent0001",
            "authored_by_attempt": {
                **proposal["authored_by_attempt"],
                "id": "att_parent0001",
            },
            "proposal_type": "DELEGATION",
            "candidate_contract": None,
            "delegation": {
                "requested_runtime_kind": "CLAUDE_CODE",
                "requested_role": task["assigned_role"],
                "requested_model": "glm-5.2",
                "capability_requirements_digest": task[
                    "capability_requirements_digest"
                ],
                "child_input_digest": task["input_snapshot_digest"],
            },
        }
    )
    decision = copy.deepcopy(_json(VALID / "proposal-decision.json"))
    _refresh_authority(bundle, task, "worker_task", "task_digest")
    _refresh_authority(bundle, proposal, "proposal", "proposal_digest")
    decision.update(
        {
            "proposal_id": proposal["proposal_id"],
            "proposal_digest": proposal["proposal_digest"],
            "accepted_proposal_id": proposal["proposal_id"],
        }
    )
    decision["downstream_event"].update(
        {
            "event_type": "work.requested",
            "accepted_proposal_id": proposal["proposal_id"],
        }
    )
    _refresh_authority(
        bundle, decision, "proposal_decision", "decision_digest"
    )
    lease = bundle["capability_lease"]
    lease.update(
        {
            "issuance_basis": "ACCEPTED_DELEGATION",
            "accepted_proposal": exact_record_binding("proposal", proposal),
            "dispatch_causation": {
                "decision_event_id": decision["decision_event_id"],
                "downstream_event_id": decision["downstream_event"]["event_id"],
                "transaction_id": decision["transaction_id"],
                "worker_task_snapshot": exact_record_binding(
                    "worker_task", task
                ),
            },
            "bound_resource": exact_record_binding("worker_task", task),
        }
    )
    _refresh_authority(bundle, lease, "capability_lease", "grant_digest")
    bundle["delegation_proposal"] = proposal
    bundle["delegation_decision"] = decision
    return bundle


def _action_execution_lease() -> dict[str, Any]:
    lease = copy.deepcopy(_json(VALID / "capability-lease.json"))
    lease.update(
        {
            "grant_kind": "ACTION_EXECUTION",
            "issuance_basis": "AUTHORIZED_ACTION",
            "audience": "scoped-executor",
            "bound_worker_task_id": None,
            "bound_attempt_id": None,
            "claim_binding": None,
            "dispatch_causation": None,
            "accepted_proposal": {
                "kind": "PROPOSAL",
                "id": "prop_stage0001",
                "revision": None,
                "digest": "sha256:2929292929292929292929292929292929292929292929292929292929292929",
            },
            "bound_agent_intent": {
                "id": "aint_stage0001",
                "digest": "sha256:4545454545454545454545454545454545454545454545454545454545454545",
            },
            "bound_workorder": {
                "id": "wo_stage0001",
                "digest": "sha256:4646464646464646464646464646464646464646464646464646464646464646",
            },
            "authorization_grant": {
                "id": "auth_stage0001",
                "digest": "sha256:4747474747474747474747474747474747474747474747474747474747474747",
            },
        }
    )
    return lease


def _bundle(kind: str) -> dict[str, Any]:
    if kind == "identity":
        return _identity_bundle()
    if kind == "root_dispatch":
        return _root_dispatch_bundle()
    if kind == "delegated_dispatch":
        return _delegated_dispatch_bundle()
    raise AssertionError(kind)


def _apply_specification(bundle: dict[str, Any], specification: dict[str, Any]) -> None:
    for mutation in specification["mutations"]:
        target: Any = bundle[mutation["document"]]
        parts = mutation["path"].split(".")
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        final = parts[-1]
        operation = mutation.get("op", "set")
        if operation == "append":
            target[final].append(mutation["value"])
        elif operation == "set":
            if isinstance(target, list):
                target[int(final)] = mutation["value"]
            else:
                target[final] = mutation["value"]
        else:  # pragma: no cover - every fixture is explicitly enumerated
            raise AssertionError(operation)


def _assert_bundle_shapes(bundle: dict[str, Any]) -> None:
    for document_name, payload in bundle.items():
        if document_name not in DOCUMENT_SCHEMAS:
            continue
        errors = _shape_errors(document_name, payload)
        assert not errors, f"{document_name}: " + "; ".join(
            error.message for error in errors
        )


def test_complete_identity_and_runtime_capability_bundle_passes() -> None:
    bundle = _identity_bundle()
    _assert_bundle_shapes(bundle)
    assert validate_semantics(bundle) == ()


@pytest.mark.parametrize(
    "missing_document",
    (
        "worker_task",
        "worker_task_claim_snapshot",
        "attempt",
        "attempt_claim_snapshot",
        "proposal_attempt_snapshot",
        "proposal",
        "proposal_decision",
        "agent_manifest",
        "participation_manifest",
        "capability_lease",
    ),
)
def test_complete_identity_bundle_fails_closed_when_context_is_missing(
    missing_document: str,
) -> None:
    bundle = _identity_bundle()
    del bundle[missing_document]
    violations = validate_semantics(bundle)
    assert any(
        violation.code == "identity_chain.context_missing"
        and violation.path == (missing_document,)
        for violation in violations
    )


def test_root_dispatch_is_controller_created_without_fake_proposal() -> None:
    bundle = _root_dispatch_bundle()
    _assert_bundle_shapes(bundle)
    assert validate_semantics(bundle) == ()

    forged = copy.deepcopy(bundle["capability_lease"])
    forged["accepted_proposal"] = {
        "kind": "PROPOSAL",
        "id": "prop_forged001",
        "revision": None,
        "digest": "sha256:ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
    }
    assert _shape_errors("capability_lease", forged)


def test_delegated_dispatch_binds_decision_child_task_and_same_transaction() -> None:
    bundle = _delegated_dispatch_bundle()
    _assert_bundle_shapes(bundle)
    assert validate_semantics(bundle) == ()


def test_action_execution_is_shape_valid_but_stage0_semantically_deferred() -> None:
    lease = _action_execution_lease()
    assert not _shape_errors("capability_lease", lease)
    assert "capability_lease.action_execution_stage4_deferred" in {
        violation.code
        for violation in validate_semantics({"capability_lease": lease})
    }


@pytest.mark.parametrize("fixture_name", IDENTITY_CASES)
def test_shape_valid_rebinding_attack_is_rejected_with_stable_code(
    fixture_name: str,
) -> None:
    specification = _json(INVALID / fixture_name)
    bundle = _bundle(specification["bundle"])
    _apply_specification(bundle, specification)
    _assert_bundle_shapes(bundle)
    codes = {violation.code for violation in validate_semantics(bundle)}
    assert specification["expected_invariant"] in codes
