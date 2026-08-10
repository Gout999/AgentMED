"""v4 is an explicit side-by-side contract, never a silent v3 lease widening."""
from __future__ import annotations

from pathlib import Path

import yaml


CONTRACTS = Path(__file__).resolve().parents[1]
OWNERSHIP = CONTRACTS / "v4" / "aggregate-ownership.yaml"
V3_EVENTS = CONTRACTS / "events" / "events.yaml"
V3_STATE_MACHINES = CONTRACTS / "events" / "state-machines.yaml"


def _yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_v3_contract_remains_a_separate_unchanged_seven_aggregate_baseline() -> None:
    events = _yaml(V3_EVENTS)
    machines = _yaml(V3_STATE_MACHINES)
    seven = {"case", "experiment", "changeset", "eval", "release", "notification", "trust"}
    assert events["version"] == "0.1.0"
    assert set(events["aggregates"]) == seven
    assert set(machines["machines"]) == seven
    assert "worker_task" not in events["aggregates"]
    assert "signal" not in events["aggregates"]


def test_v3_case_lease_and_v4_worker_task_lease_can_never_be_active_for_same_work() -> None:
    cutover = _yaml(OWNERSHIP)["cutover"]
    assert cutover["routing_key"] == "work_contract_version"
    assert cutover["no_dual_lease"] is True
    assert cutover["v3_case_lease"]["resource"] == "case"
    assert cutover["v4_worker_task_lease"]["resource"] == "worker_task"
    assert cutover["v4_worker_task_lease"]["status"] == "inactive_until_stage_2"


def test_activation_quiesces_v3_before_enabling_v4() -> None:
    steps = _yaml(OWNERSHIP)["cutover"]["activation_preconditions"]
    assert steps.index("block_new_v3_claims_for_selected_work") < steps.index(
        "verify_inflight_v3_terminal_or_reconciled"
    )
    assert steps.index("verify_inflight_v3_terminal_or_reconciled") < steps.index(
        "verify_no_active_v3_lease_for_selected_work"
    )
    assert steps.index("verify_no_active_v3_lease_for_selected_work") < steps.index(
        "create_v4_worker_task_with_new_identity"
    )
    assert steps.index("create_v4_worker_task_with_new_identity") < steps.index(
        "enable_v4_route_for_selected_work"
    )


def test_v3_recovery_debt_is_a_cutover_gate_with_an_auditable_receipt() -> None:
    cutover = _yaml(OWNERSHIP)["cutover"]
    assert cutover["recovery_gate"] == {
        "requires_inflight_terminal_or_reconciled": True,
        "shadow_only_until_passed": True,
        "required_crash_points": [
            "case_created_before_closure",
            "release_promoted_before_notification",
            "notification_started_before_evidence",
        ],
    }
    assert cutover["cutover_receipt"]["required"] == [
        "routing_key",
        "route_version",
        "prior_lease_authority",
        "new_lease_authority",
        "reconciliation_evidence_digest",
    ]


def test_post_activation_rollback_drains_or_reconciles_without_recreating_v3_lease() -> None:
    rollback = _yaml(OWNERSHIP)["cutover"]["rollback"]
    assert rollback["before_activation"] == "disable_v4_route"
    after = set(rollback["after_activation"])
    assert "stop_new_v4_tasks" in after
    assert "drain_or_reconcile_existing_v4_tasks" in after
    assert "never_recreate_v3_lease_for_migrated_task" in after


def test_public_compatibility_is_versioned_and_requires_expand_backfill_contract() -> None:
    compatibility = _yaml(OWNERSHIP)["cutover"]["compatibility"]
    assert compatibility == {
        "public_major_path": "/api/v1",
        "current_schema_version": "1.0",
        "supported_previous_compatible_minor_count": 1,
        "expand_backfill_contract_required": True,
    }
