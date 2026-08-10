"""Conformance checks for the v4 event catalog and state machines."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import yaml


V4_ROOT = Path(__file__).resolve().parents[1] / "v4"
EVENTS = V4_ROOT / "events" / "events.yaml"
MACHINES = V4_ROOT / "events" / "state-machines.yaml"
OWNERSHIP = V4_ROOT / "aggregate-ownership.yaml"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _catalog() -> tuple[dict, dict[str, dict]]:
    document = _yaml(EVENTS)
    events: dict[str, dict] = {}
    for aggregate in document["aggregates"].values():
        for event in aggregate["events"]:
            event_type = event["event_type"]
            assert event_type not in events, event_type
            events[event_type] = event
    return document, events


def test_state_machine_transition_keys_survive_yaml_and_reference_real_events() -> None:
    _, catalog = _catalog()
    document = _yaml(MACHINES)
    for name, machine in document["machines"].items():
        assert machine["initial"] in machine["states"], name
        assert set(machine["terminal"]) <= set(machine["states"]), name
        for transition in machine["transitions"]:
            assert set(transition) >= {"from", "on", "to"}, transition
            assert True not in transition, "quote 'on' so PyYAML 1.1 keeps it a string"
            assert transition["from"] in machine["states"], transition
            assert transition["to"] in machine["states"], transition
            assert transition["on"] in catalog, transition


def test_every_state_is_reachable_and_every_nonterminal_can_reach_a_terminal() -> None:
    for name, machine in _yaml(MACHINES)["machines"].items():
        outgoing: dict[str, set[str]] = defaultdict(set)
        for transition in machine["transitions"]:
            outgoing[transition["from"]].add(transition["to"])

        reachable = {machine["initial"]}
        queue = deque(reachable)
        while queue:
            state = queue.popleft()
            for target in outgoing[state] - reachable:
                reachable.add(target)
                queue.append(target)
        assert reachable == set(machine["states"]), name

        terminal = set(machine["terminal"])
        assert all(not outgoing[state] for state in terminal), name
        for state in set(machine["states"]) - terminal:
            seen = {state}
            queue = deque([state])
            while queue and not (seen & terminal):
                current = queue.popleft()
                for target in outgoing[current] - seen:
                    seen.add(target)
                    queue.append(target)
            assert seen & terminal, f"{name}.{state} cannot reach terminal"

        if "UNKNOWN" in machine["states"]:
            unknown_events = {
                transition["on"]
                for transition in machine["transitions"]
                if transition["from"] == "UNKNOWN"
            }
            assert unknown_events and all("reconcile" in event for event in unknown_events)


def test_event_catalog_exactly_matches_aggregate_ownership() -> None:
    event_document, catalog = _catalog()
    ownership = _yaml(OWNERSHIP)["resources"]
    non_projections = {
        name: resource for name, resource in ownership.items() if resource["kind"] != "projection"
    }
    assert set(event_document["aggregates"]) == set(non_projections)
    for name, aggregate in event_document["aggregates"].items():
        assert aggregate["owner"] == ownership[name]["owner"], name
        assert {event["event_type"] for event in aggregate["events"]} == set(
            ownership[name]["events"]
        ), name
    assert all(event["event_version"] == "1.0" for event in catalog.values())


def test_claim_atomically_creates_the_controller_owned_attempt() -> None:
    document, catalog = _catalog()
    claimed = catalog["work.claimed"]
    created = catalog["attempt.created"]
    assert claimed["creates_attempt"] is True
    assert "attempt_id" in claimed["payload_required"]
    assert {"attempt_id", "worker_task_id", "claim_event_id"} <= set(
        created["payload_required"]
    )
    invariant = document["transactional_invariants"]["work_claim"]
    assert invariant == {
        "required_records": [
            "worker_task_claim_snapshot",
            "worker_task_authority_receipt",
            "attempt_created_snapshot",
            "attempt_authority_receipt",
            "capability_issuance_snapshot",
            "capability_authority_receipt",
            "work.claimed",
            "attempt.created",
            "capability.issued",
            "outbox",
            "audit",
        ],
        "same_transaction": True,
        "shared_attempt_id": True,
        "attempt_created_causation_id": "work.claimed.event_id",
        "runtime_adapter_cannot_create_attempt": True,
    }


def test_proposal_acceptance_and_workorder_guards_fail_closed() -> None:
    document, catalog = _catalog()
    proposal = document["transactional_invariants"]["proposal_acceptance"]
    assert proposal["required_records"] == [
        "proposal_decision",
        "proposal_decision_authority_receipt",
        "proposal.accepted",
        "first_downstream_event",
        "outbox",
        "audit",
    ]
    assert proposal["same_transaction"] is True
    assert proposal["downstream_causation_id"] == "proposal.accepted.event_id"
    assert proposal["downstream_accepted_proposal_id"] == (
        "proposal.accepted.accepted_proposal_id"
    )
    assert document["transactional_invariants"][
        "accepted_change_candidate_recording"
    ] == {
        "applies_when": "proposal_type.CHANGE",
        "required_records": [
            "proposal_decision",
            "proposal_decision_authority_receipt",
            "candidate_revision",
            "candidate_revision_authority_receipt",
            "proposal.accepted",
            "candidate_revision.recorded",
            "outbox",
            "audit",
        ],
        "same_transaction": True,
        "candidate_event_id": "proposal_decision.downstream_event.event_id",
        "candidate_event_causation_id": "proposal_decision.decision_event_id",
        "candidate_event_accepted_proposal_id": (
            "proposal_decision.accepted_proposal_id"
        ),
        "candidate_authority_event_id": "candidate_revision.recorded.event_id",
    }

    completed = catalog["gate.completed"]
    assert "status" in completed["payload_required"]
    workorder = document["transactional_invariants"]["workorder_creation"]
    assert workorder == {
        "required_records": [
            "workorder",
            "workorder_authority_receipt",
            "workorder.created",
            "outbox",
            "audit",
        ],
        "same_transaction": True,
        "requires_gate_status": "PASS",
        "gate_completed_status": "PASS",
        "binds_exact_candidate_and_gate_digests": True,
    }
    assert catalog["workorder.created"]["guards"]["gate_status"] == "PASS"


def test_controller_record_events_require_exact_authority_subject_metadata() -> None:
    document, catalog = _catalog()
    assert document["envelope"]["rules"]["controller_record_event_requires"] == [
        "subject_kind",
        "subject_id",
        "subject_revision",
        "subject_digest",
        "authority_receipt_id",
    ]
    assert document["envelope"]["rules"][
        "authority_receipt_and_subject_share_transaction"
    ] is True
    assert {
        "controller.registered",
        "authority.receipt_recorded",
        "capability.issued",
        "attempt.created",
        "work.claimed",
    } <= set(catalog)


def test_candidate_revision_is_controller_recorded_and_gate_bound() -> None:
    document, catalog = _catalog()
    recorded = catalog["candidate_revision.recorded"]
    assert document["aggregates"]["candidate_revision"]["owner"] == "proposal-controller"
    assert {
        "candidate_revision_id",
        "revision_digest",
        "candidate_contract_id",
        "accepted_proposal_id",
        "producer_attempt_id",
        "recorded_by_principal",
    } <= set(recorded["payload_required"])
    assert {
        "candidate_revision_id",
        "candidate_revision_digest",
    } <= set(catalog["gate.started"]["payload_required"])
    assert {
        "candidate_revision_id",
        "candidate_revision_digest",
    } <= set(catalog["workorder.created"]["payload_required"])


def test_review_model_and_gate_receipts_have_non_cyclic_transaction_boundaries() -> None:
    document, catalog = _catalog()
    assert {
        "resolution_review.recorded",
        "attempt.receipt_recorded",
        "gate.track_receipt_recorded",
    } <= set(catalog)
    assert document["transactional_invariants"]["resolution_review_recording"] == {
        "required_records": [
            "resolution_review_receipt",
            "resolution_review_authority_receipt",
            "resolution_review.recorded",
            "outbox",
            "audit",
        ],
        "same_transaction": True,
        "review_receipt_cannot_bind_future_resolution_digest": True,
    }
    assert document["transactional_invariants"]["model_call_receipt_recording"] == {
        "required_records": [
            "model_call_receipt",
            "model_call_authority_receipt",
            "attempt.receipt_recorded",
            "outbox",
            "audit",
        ],
        "same_transaction": True,
        "binds_call_time_nonterminal_attempt_snapshot": True,
        "terminal_attempt_digest_cannot_be_prebound": True,
    }
    assert document["transactional_invariants"]["gate_track_receipt_recording"] == {
        "required_records": [
            "gate_track_receipt",
            "gate_track_authority_receipt",
            "gate.track_receipt_recorded",
            "outbox",
            "audit",
        ],
        "same_transaction": True,
        "gate_report_digest_cannot_be_prebound": True,
    }
    assert document["transactional_invariants"]["gate_report_finalization"] == {
        "required_records": [
            "gate_report",
            "gate_report_authority_receipt",
            "terminal_gate_event",
            "exact_gate_track_receipt_set",
            "outbox",
            "audit",
        ],
        "same_transaction": True,
        "exact_required_track_set": True,
    }
