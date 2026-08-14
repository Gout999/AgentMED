"""Current V5-1A/1B/1C runtime overlay without rewriting V5-0C history."""
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "v5"
REPO = ROOT.parent


def _load(name: str) -> dict:
    return yaml.safe_load((V5 / name).read_text(encoding="utf-8"))


ACTIVE = {
    "capabilities.get": ("GET", "/api/v2/capabilities", "capabilities get"),
    "applications.register": ("POST", "/api/v2/applications", "application register"),
    "applications.get": (
        "GET",
        "/api/v2/applications/{application_id}",
        "application get",
    ),
    "environments.register": ("POST", "/api/v2/environments", "environment register"),
    "environments.get": (
        "GET",
        "/api/v2/environments/{environment_id}",
        "environment get",
    ),
    "system-components.register": (
        "POST",
        "/api/v2/system-components",
        "system-component register",
    ),
    "system-components.get": (
        "GET",
        "/api/v2/system-components/{component_id}",
        "system-component get",
    ),
    "dependency-edges.record": (
        "POST",
        "/api/v2/dependency-edges",
        "dependency-edge record",
    ),
    "dependency-edges.get": (
        "GET",
        "/api/v2/dependency-edges/{edge_id}",
        "dependency-edge get",
    ),
    "system-manifests.import": (
        "POST",
        "/api/v2/system-manifests:import",
        "system-manifest import",
    ),
    "system-versions.get": (
        "GET",
        "/api/v2/system-versions/{system_version_set_id}",
        "system-manifest get",
    ),
    "system-versions.diff": (
        "GET",
        "/api/v2/system-versions:diff",
        "system-manifest diff",
    ),
    "cases.bind-application": (
        "POST",
        "/api/v2/cases/{case_id}:bind-application",
        "case bind-application",
    ),
    "case-application-bindings.get": (
        "GET",
        "/api/v2/cases/{case_id}/application-binding{?case_revision,case_digest}",
        "case application-binding get",
    ),
    "acceptance-criteria.propose": (
        "POST",
        "/api/v2/cases/{case_id}:propose-acceptance-criteria",
        "case acceptance-criteria propose",
    ),
    "acceptance-criteria.get": (
        "GET",
        "/api/v2/cases/{case_id}/acceptance-criteria{?case_revision}",
        "case acceptance-criteria get",
    ),
    "acceptance-criteria.confirm": (
        "POST",
        "/api/v2/acceptance-criteria/{acceptance_criteria_revision_id}:confirm",
        "case acceptance-criteria confirm",
    ),
}


def test_runtime_overlay_is_exact_current_17_not_historical_first_10() -> None:
    registry = _load("intent-registry.yaml")
    compatibility = _load("compatibility.yaml")
    fixture = _load("fixtures/first-wire-slice.yaml")
    by_name = {item["name"]: item for item in registry["intents"]}

    assert fixture["historical_freeze"] is True
    assert fixture["slice"]["count"] == 10
    assert registry["first_slice_wire_contract"]["count"] == 10
    assert registry["first_slice_wire_contract"]["transport_generation_all_disabled"] is True

    overlay = registry["runtime_overlay"]
    assert overlay["status"] == "PARTIALLY_IMPLEMENTED"
    assert overlay["through_delivery_slice"] == "V5-1C"
    assert set(overlay["enabled_intents"]) == set(ACTIVE)
    assert compatibility["current_runtime_overlay"]["public_http_and_cli_intents"] == (
        overlay["enabled_intents"]
    )

    for name, (method, path, cli) in ACTIVE.items():
        intent = by_name[name]
        assert intent["wire_status"] == "FROZEN", name
        assert intent["implementation_status"] == "IMPLEMENTED", name
        assert intent["http"] == {
            "method": method,
            "path": path,
            "operation_id": intent["http"]["operation_id"],
        }
        assert intent["cli"] == cli, name
        assert intent["cli_requires_explicit_api_major"] is True
        refs = intent["field_contract_ref"]
        assert isinstance(refs, dict) and set(refs) == {"request", "response"}
        for ref in refs.values():
            source_path, separator, symbol = ref.partition("#")
            assert separator and source_path and symbol, (name, ref)
            assert (REPO / source_path).is_file(), (name, ref)


def test_standalone_record_and_v5_2_plus_remain_deferred() -> None:
    registry = _load("intent-registry.yaml")
    overlay = registry["runtime_overlay"]
    by_name = {item["name"]: item for item in registry["intents"]}
    deferred = set(overlay["explicitly_not_implemented"])

    assert "capabilities.get" not in deferred
    assert by_name["capabilities.get"]["implementation_status"] == "IMPLEMENTED"
    assert by_name["system-versions.record"]["implementation_status"] == (
        "NOT_IMPLEMENTED"
    )
    assert by_name["system-versions.record"]["cli"] is None
    assert overlay["capability_discovery_enabled"] is True
    assert overlay["standalone_system_versions_record_route_enabled"] is False
    assert overlay["standalone_system_versions_record_cli_enabled"] is False

    later = {
        name
        for name, item in by_name.items()
        if item["delivery_slice"].startswith(("V5-2", "V5-3", "V5-4", "V5-5"))
    }
    assert later
    assert later <= deferred
    for name in later:
        assert by_name[name]["wire_status"] == "DRAFT", name
        assert by_name[name]["implementation_status"] == "NOT_IMPLEMENTED", name
        assert by_name[name]["field_contract_ref"] is None, name


def test_partial_overlay_migration_and_transport_boundary_agree() -> None:
    compatibility = _load("compatibility.yaml")
    registry = _load("intent-registry.yaml")
    domain = _load("domain-model.yaml")
    events = _load("events.yaml")
    schemas = _load("schema-profiles.yaml")

    assert {
        compatibility["migration"]["current_head"],
        registry["runtime_overlay"]["migration_head"],
        domain["runtime_overlay"]["migration_head"],
        events["runtime_overlay"]["migration_head"],
        schemas["runtime_overlay"]["migration_head"],
    } == {"012"}
    assert compatibility["v5_target_surface"]["route_status"] == (
        "PARTIALLY_ENABLED_BY_EXPLICIT_ALLOWLIST"
    )
    assert compatibility["v5_target_surface"]["discovery_status"] == (
        "ENABLED_BY_AUDITED_ALLOWLIST"
    )
    assert compatibility["current_runtime_overlay"]["capability_discovery"] == (
        "ENABLED_AUDITED_ALLOWLIST"
    )
    assert compatibility["implementation_gate"]["capability_discovery_authorized"] is True
    assert registry["activation_flags"]["http_routes"] is True
    assert registry["activation_flags"]["cli_commands"] is True
    assert registry["activation_flags"]["capability_discovery"] is True
    assert set(registry["runtime_overlay"]["enabled_transport_kinds"]) == {
        "http",
        "cli",
        "capability_discovery",
    }
    assert set(registry["runtime_overlay"]["disabled_transport_kinds"]) == {
        "sdk",
        "mcp",
        "a2a",
    }
    for transport in (
        "sdk_methods",
        "mcp_tools",
        "a2a_agent_card",
        "a2a_transport",
        "a2a_methods",
    ):
        assert registry["activation_flags"][transport] is False


def test_implemented_major_2_events_use_exact_named_bindings() -> None:
    events = _load("events.yaml")
    expected = {
        "ai_application": ("application.registered", "exact_application_binding", "application_id"),
        "environment": ("environment.registered", "exact_environment_binding", "environment_id"),
        "system_component": (
            "system_component.registered",
            "exact_system_component_binding",
            "component_id",
        ),
        "dependency_edge": (
            "dependency_edge.recorded",
            "exact_dependency_edge_binding",
            "edge_id",
        ),
        "component_revision": (
            "component_revision.recorded",
            "exact_component_revision_binding",
            "component_revision_id",
        ),
        "topology_revision": (
            "topology_revision.recorded",
            "exact_topology_revision_binding",
            "topology_revision_id",
        ),
        "system_version_set": (
            "system_version_set.recorded",
            "exact_system_version_set_binding",
            "system_version_set_id",
        ),
        "bootstrap_attestation": (
            "bootstrap_attestation.recorded",
            "exact_bootstrap_attestation_binding",
            "bootstrap_attestation_id",
        ),
        "system_assignment": (
            "system_assignment.recorded",
            "exact_assignment_binding",
            "assignment_id",
        ),
    }
    implemented = set(events["runtime_overlay"]["implemented_event_types"])
    for family, (event_type, exact_binding, identity) in expected.items():
        event = events[family]["events"][event_type]
        assert {exact_binding, identity} <= set(event["payload_required"]), event_type
        assert event_type in implemented

    acceptance = events["acceptance_criteria_revision"]
    pending = acceptance["resolution_contract_binding_status_contract"]
    assert pending["constants"] == {
        "status": "PENDING_MATERIALIZATION",
        "owner": "resolution-contract-controller",
        "materialization_stage": "V5-4",
    }
    assert pending["exact_resolution_contract_binding_forbidden"] is True
    for event_type in ("acceptance_criteria.proposed", "acceptance_criteria.confirmed"):
        payload = acceptance["events"][event_type]["payload_required"]
        assert "exact_acceptance_criteria_revision_binding" in payload
        assert "resolution_contract_binding_status" in payload
        assert "exact_resolution_contract_binding" not in payload
        assert event_type in implemented


def test_confirmed_pending_acceptance_cannot_project_ready() -> None:
    domain = _load("domain-model.yaml")
    compatibility = _load("compatibility.yaml")
    fixture = _load("fixtures/acceptance-wire.yaml")
    readiness = domain["resources"]["case_readiness"]

    assert readiness["target_readiness_values"] == [
        "NEEDS_ACCEPTANCE_CRITERIA",
        "READY",
    ]
    assert readiness["current_runtime_readiness_values"] == [
        "NEEDS_ACCEPTANCE_CRITERIA"
    ]
    assert "pending_resolution_contract_materialization_cannot_project_ready" in (
        readiness["invariants"]
    )
    assert fixture["confirm"]["expected"]["confirmation_status"] == "CONFIRMED"
    assert fixture["confirm"]["expected"]["is_executable_contract"] is False
    assert fixture["confirm"]["expected"]["readiness_after_confirm"] == (
        "NEEDS_ACCEPTANCE_CRITERIA"
    )
    boundary = compatibility["current_runtime_overlay"]["acceptance_runtime_boundary"]
    assert boundary["resolution_contract_binding_status"] == "PENDING_MATERIALIZATION"
    assert boundary["confirmed_acceptance_is_executable"] is False
    assert boundary["ready_before_v5_4"] is False


def test_known_lifecycle_mismatch_is_explicit_not_reported_as_implemented() -> None:
    states = _load("state-machines.yaml")
    overlay = states["runtime_overlay"]
    for machine, missing_event in (
        ("ai_application_machine", "application.activated"),
        ("system_component_machine", "system_component.activated"),
    ):
        mismatch = overlay[machine]["known_frozen_mismatch"]
        assert mismatch["frozen_created_state"] == "REGISTERED"
        assert mismatch["current_runtime_created_state"] == "ACTIVE"
        assert mismatch["missing_event"] == missing_event
        assert overlay[machine]["full_machine_implemented"] is False
    assignment = overlay["system_assignment_machine"]
    assert assignment["implemented_scope"] == "BOOTSTRAP_ONLY"
    assert assignment["update_freeze_resume_retire_transitions"] == "NOT_IMPLEMENTED"
