"""Aggregate ownership must prevent a cross-domain mega state machine."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml


OWNERSHIP = Path(__file__).resolve().parents[1] / "v4" / "aggregate-ownership.yaml"


def _document() -> dict:
    return yaml.safe_load(OWNERSHIP.read_text(encoding="utf-8"))


def test_every_command_has_exactly_one_resource_owner() -> None:
    document = _document()
    command_owners: dict[str, list[str]] = defaultdict(list)
    for resource_name, resource in document["resources"].items():
        assert resource["owner"], resource_name
        for command in resource.get("commands", []):
            command_owners[command].append(resource_name)
    assert command_owners
    assert all(len(owners) == 1 for owners in command_owners.values()), command_owners


def test_projections_accept_no_commands() -> None:
    document = _document()
    projections = {
        name: resource
        for name, resource in document["resources"].items()
        if resource["kind"] == "projection"
    }
    assert set(projections) == {"automation_run_view"}
    assert all(resource["commands"] == [] for resource in projections.values())
    assert document["rules"]["projection_accepts_commands"] is False


def test_domain_facts_have_the_frozen_single_owner() -> None:
    resources = _document()["resources"]
    expected = {
        "signal": "signal-controller",
        "source_connection": "source-controller",
        "source_sync_run": "source-controller",
        "quality_case": "case-controller",
        "automation_request": "automation-request-controller",
        "worker_task": "work-controller",
        "attempt": "work-controller",
        "proposal": "proposal-controller",
        "proposal_decision": "proposal-controller",
        "candidate_revision": "proposal-controller",
        "gate_execution": "gate-controller",
        "external_operation": "scoped-executor-controller",
        "case_resolution_decision": "case-controller",
        "closure_delivery": "closure-controller",
        "coordinator_reaction_ledger": "durable-work-coordinator",
    }
    assert {name: resources[name]["owner"] for name in expected} == expected
    assert "proposal_decision" in resources["proposal"]["explicitly_does_not_own"]
    assert "current_stage" in resources["coordinator_reaction_ledger"]["explicitly_does_not_own"]


def test_automation_request_is_not_a_cross_domain_state_machine() -> None:
    resource = _document()["resources"]["automation_request"]
    assert set(resource["commands"]) == {
        "automation-requests.start-investigation",
        "automation-requests.admit",
        "automation-requests.reject",
        "automation-requests.update-budget",
        "automation-requests.request-stop",
    }
    assert set(resource["events"]) == {
        "automation_request.submitted",
        "automation_request.admitted",
        "automation_request.rejected",
        "automation_request.budget_updated",
        "automation_request.stop_requested",
    }
    assert {
        "current_stage",
        "worker_execution",
        "experiment_lifecycle",
        "gate_result",
        "release_decision",
        "case_resolution",
        "external_operation_result",
    } <= set(resource["explicitly_does_not_own"])


def test_source_doctor_and_rollback_requests_have_domain_specific_owners() -> None:
    resources = _document()["resources"]
    assert "source-sync-runs.request-doctor" in resources["source_sync_run"]["commands"]
    assert resources["source_sync_run"]["owner"] == "source-controller"
    assert "external-operations.request-rollback" in resources["external_operation"]["commands"]
    assert resources["external_operation"]["owner"] == "scoped-executor-controller"


def test_runtime_adapter_and_exporter_cannot_impersonate_workers_or_controllers() -> None:
    components = _document()["components"]
    assert {
        "claim_for_worker",
        "accept_proposal",
        "approve",
        "release",
    } <= set(components["agent_runtime_adapter"]["must_not"])
    assert {
        "dispatch",
        "claim",
        "acknowledge_for_worker",
        "submit_proposal",
        "approve",
        "execute_business_command",
        "write_agent_artifact",
    } <= set(components["exporter"]["must_not"])
    assert components["exporter"]["may"] == [
        "read_authoritative_records",
        "render_evidence",
    ]


def test_agents_propose_but_do_not_own_authoritative_decisions() -> None:
    document = _document()
    assert document["rules"]["agent_output_is_authoritative"] is False
    assert document["rules"]["controller_decision_mutates_proposal"] is False
    assert "mutate_authoritative_decision" in document["components"]["agent_worker"]["must_not"]


def test_authority_receipts_are_non_executing_post_record_proofs() -> None:
    document = _document()
    assert document["rules"]["controller_record_requires_authority_receipt"] is True
    assert document["rules"]["authority_receipt_is_post_record_and_same_transaction"] is True
    assert document["rules"]["authority_receipt_grants_execution"] is False
    assert document["rules"]["subject_stores_authority_receipt_id_only"] is True
    assert document["resources"]["controller_registration"]["kind"] == "trust_root_record"
    assert {
        "capability_grant",
        "approval_decision",
        "execution_authority",
    } <= set(document["resources"]["authority_receipt"]["explicitly_does_not_own"])


def test_record_authority_has_an_exact_owner_command_event_mapping() -> None:
    document = _document()
    resources = document["resources"]
    command_owner = {
        command: resource["owner"]
        for resource in resources.values()
        for command in resource.get("commands", ())
    }
    event_owner = {
        event: resource["owner"]
        for resource in resources.values()
        for event in resource.get("events", ())
    }
    expected_kinds = {
        "RESOLUTION_CONTRACT",
        "RESOLUTION_REVIEW_RECEIPT",
        "CANDIDATE_CONTRACT",
        "CANDIDATE_REVISION",
        "PROPOSAL",
        "PROPOSAL_DECISION",
        "AGENT_INTENT",
        "TRACE_EVIDENCE_RECEIPT",
        "EVALUATION_PLAN",
        "GATE_REPORT",
        "GATE_TRACK_RECEIPT",
        "WORKORDER",
        "CAPABILITY_LEASE",
        "AGENT_MANIFEST",
        "PARTICIPATION_MANIFEST",
        "SKILL_MANIFEST",
        "MCP_MANIFEST",
        "WORKER_TASK",
        "ATTEMPT",
        "MODEL_CALL_RECEIPT",
        "SIGNAL_RECORD",
        "SIGNAL_CASE_LINK",
        "QUALITY_CASE",
        "SOURCE_SYNC_RUN",
    }
    assert set(document["record_authority"]) == expected_kinds
    for kind, mapping in document["record_authority"].items():
        assert resources[mapping["resource"]]["owner"] == mapping["owner"], kind
        for command, events in mapping["command_events"].items():
            assert command_owner[command] == mapping["owner"], (kind, command)
            assert events
            assert all(event_owner[event] == mapping["owner"] for event in events), kind
