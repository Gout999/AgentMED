"""R2 catalog plus one-shot R3-bootstrap contract and adversarial parity.

The public manifest intent produces one complete first graph.  Standalone
version record, a second version, version GET/diff, and activation transports
remain outside this slice.
"""
from __future__ import annotations

from pathlib import Path
import sys
import types

import yaml


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "v5"
FIXTURE = V5 / "fixtures" / "application-catalog.yaml"
sys.path.insert(0, str(ROOT.parent / "control-plane"))

from app.public_api.v5_capability_models import (  # noqa: E402
    V5EnabledIntent,
    V5ServerCapabilitiesData,
    V5ServerCapabilitiesResponse,
)

# ``v5_models`` defines transport-only Pydantic models but currently imports
# value-domain constants from the SQLAlchemy table module.  The constants are
# not referenced by the transport module; isolate that incidental database
# dependency so the contract suite can validate the real model classes in its
# intentionally small environment.
_models_package = types.ModuleType("app.models")
_models_package.__path__ = []  # type: ignore[attr-defined]
_v5_tables = types.ModuleType("app.models.v5_tables")
for _constant in (
    "COMPONENT_KIND_VALUES",
    "CRITICALITY_VALUES",
    "DATA_CLASSIFICATION_VALUES",
    "DATASET_ROLE_VALUES",
    "DEPENDENCY_RELATION_VALUES",
    "EFFECT_CLASSIFICATION_VALUES",
    "GOVERNANCE_MODE_VALUES",
    "PERMISSION_CLASSIFICATION_VALUES",
    "RISK_CLASSIFICATION_VALUES",
):
    setattr(_v5_tables, _constant, ())
sys.modules["app.models"] = _models_package
sys.modules["app.models.v5_tables"] = _v5_tables
from app.public_api.v5_models import (  # noqa: E402
    ApplicationGetResponse,
    ApplicationListResponse,
    ApplicationRegisterRequest,
    ApplicationRegisterResponse,
    ComponentGetResponse,
    ComponentRegisterRequest,
    ComponentRegisterResponse,
    DependencyEdgeGetResponse,
    DependencyEdgeRecordRequest,
    DependencyEdgeRecordResponse,
    EnvironmentGetResponse,
    EnvironmentRegisterRequest,
    EnvironmentRegisterResponse,
    SystemManifestImportRequest,
    SystemManifestImportResponse,
)
sys.modules.pop("app.models.v5_tables", None)
sys.modules.pop("app.models", None)


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict:
    mapping: dict = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ValueError(f"duplicate YAML key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load(name: str) -> dict:
    return yaml.load(
        (V5 / name).read_text(encoding="utf-8"), Loader=_UniqueKeyLoader
    )


def _fixture() -> dict:
    return yaml.load(FIXTURE.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)


def _intent(registry: dict, name: str) -> dict:
    return next(row for row in registry["intents"] if row["name"] == name)


def _required_and_optional_fields(model: type) -> tuple[set[str], set[str]]:
    required = {
        name for name, field in model.model_fields.items() if field.is_required()
    }
    return required, set(model.model_fields) - required


def test_r2_overlay_reports_candidate_runtime_without_done_or_pass_claim() -> None:
    documents = {
        name: _load(name)
        for name in (
            "aggregate-ownership.yaml",
            "compatibility.yaml",
            "domain-model.yaml",
            "events.yaml",
            "intent-registry.yaml",
            "schema-profiles.yaml",
            "state-machines.yaml",
        )
    }
    for name, document in documents.items():
        assert document["runtime_status"] == "NOT_IMPLEMENTED", name
        overlay = document["r2_application_catalog_contract"]
        assert overlay["stage"] == "R2"
        assert overlay["contract_status"] == "FROZEN_FOR_IMPLEMENTATION"
        assert overlay["runtime_status"] == "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
        assert "DONE" not in str(overlay)
        assert "PASS" not in str(overlay)

    assert documents["events.yaml"]["r1_authority_foundation"][
        "production_activated_event_routes"
    ] == "DISABLED"
    assert documents["schema-profiles.yaml"]["r1_authority_foundation"][
        "production_direct_revision_2_append"
    ] == "DENY_ALL"
    compatibility = documents["compatibility.yaml"]
    assert compatibility["first_slice"]["status"] == "DRAFT_FROZEN_WIRE_CONTRACT_ONLY"
    assert compatibility["v5_target_surface"]["route_status"] == "DISABLED"
    assert compatibility["r2_application_catalog_contract"][
        "preserves_v5_0_historical_freeze"
    ] is True
    assert compatibility["r2_application_catalog_contract"][
        "public_transport_runtime_status"
    ] == "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
    assert compatibility["r2_application_catalog_contract"]["system_manifest_modes"][
        "R3_FULL"
    ] == {
        "standalone_system_versions_record": "NOT_IMPLEMENTED",
        "second_system_version_set": "NOT_IMPLEMENTED",
        "system_versions_get": "NOT_IMPLEMENTED",
        "system_versions_diff": "NOT_IMPLEMENTED",
        "semantic_diff": "NOT_IMPLEMENTED",
    }


def test_catalog_manifest_machine_parity_is_exact_and_excludes_r3_r4() -> None:
    ownership = _load("aggregate-ownership.yaml")["r2_application_catalog_contract"]
    compatibility = _load("compatibility.yaml")["r2_application_catalog_contract"]
    domain = _load("domain-model.yaml")["r2_application_catalog_contract"]
    events = _load("events.yaml")["r2_application_catalog_contract"]
    states = _load("state-machines.yaml")["r2_application_catalog_contract"]
    fixture = _fixture()["r2_contract"]

    expected_order = [
        "applications.register",
        "applications.activate",
        "environments.register",
        "system-components.register",
        "system-components.activate",
        "dependency-edges.record",
        "component-revisions.record",
        "topology-revisions.record",
        "system-versions.record",
        "bootstrap-attestations.record",
        "system-assignments.record",
    ]
    assert ownership["internal_workflow"]["canonical_command_order"] == expected_order
    assert fixture["bootstrap_system_manifest"]["canonical_command_order"] == (
        expected_order
    )
    assert domain["authoritative_resources"] == [
        "ai_application",
        "environment",
        "system_component",
        "dependency_edge",
    ]
    assert events["internal_catalog_manifest_writer"]["required_event_types"] == [
        "application.registered",
        "application.activated",
        "environment.registered",
        "system_component.registered",
        "system_component.activated",
        "dependency_edge.recorded",
        "component_revision.recorded",
        "topology_revision.recorded",
        "system_version_set.recorded",
        "bootstrap_attestation.recorded",
        "system_assignment.recorded",
    ]
    assert states["bootstrap_system_manifest"]["transition_scope"] == {
        "ai_application": "REGISTERED_REVISION_1_TO_ACTIVE_REVISION_2",
        "system_component": "REGISTERED_REVISION_1_TO_ACTIVE_REVISION_2",
    }
    coupled = {
        "component_revision",
        "topology_revision",
        "system_version_set",
        "bootstrap_attestation",
        "system_assignment",
    }
    excluded = {
        "application_case_binding",
        "acceptance_criteria_revision",
    }
    assert set(ownership["r3_bootstrap_coupled_resources"]) == coupled
    assert set(domain["r3_bootstrap_coupled_resources"]) == coupled
    assert set(fixture["r3_bootstrap_coupled_resources"]) == coupled
    assert set(ownership["excluded_later_stage_resources"]) == excluded
    assert set(domain["forbidden_in_r2_catalog_phase"]) == excluded
    assert set(fixture["excluded_later_stage_resources"]) == excluded
    assert set(ownership["excluded_r3_full_capabilities"]) == {
        "standalone_system_versions_record",
        "second_system_version_set",
        "system_versions_get",
        "system_versions_diff",
        "semantic_diff",
    }
    assert ownership["internal_workflow"]["producer_compatibility_split"] == {
        "r2_r3_bootstrap": "COMPLETE_ONE_SHOT_FIRST_GRAPH_PRODUCER",
        "r3_full": "STANDALONE_RECORD_SECOND_VERSION_GET_AND_DIFF",
    }
    private_composition = compatibility["private_bootstrap_manifest_composition"]
    assert private_composition["public_route"] == "NONE"
    assert private_composition["invoked_only_by_public_root_intent"] == (
        "system-manifests.import"
    )
    assert private_composition["catalog_only_public_sub_intent"] == "FORBIDDEN"


def test_bootstrap_component_revision_binding_and_atomic_replay_are_exact() -> None:
    ownership = _load("aggregate-ownership.yaml")["r2_application_catalog_contract"]
    domain = _load("domain-model.yaml")["r2_application_catalog_contract"]
    events = _load("events.yaml")["r2_application_catalog_contract"]
    registry = _load("intent-registry.yaml")
    profiles = _load("schema-profiles.yaml")
    states = _load("state-machines.yaml")["r2_application_catalog_contract"]
    fixture = _fixture()["r2_contract"]

    expected_binding = "CURRENT_AUTHORITATIVE_ACTIVE_REVISION_2"
    assert ownership["internal_workflow"]["component_revision_binding_rule"][
        "exact_system_component_binding"
    ] == expected_binding
    assert domain["bootstrap_system_manifest"]["component_revision_exact_component_binding"][
        "required_lifecycle_state"
    ] == "ACTIVE"
    assert domain["bootstrap_system_manifest"]["component_revision_exact_component_binding"][
        "initial_bootstrap_required_revision"
    ] == 2
    assert domain["bootstrap_system_manifest"]["proves"] == (
        "INITIAL_DECLARED_VERSION_AND_DESIRED_ASSIGNMENT_ONLY"
    )
    assert "observed_runtime_or_external_effect" in domain["does_not_prove"]
    assert events["internal_catalog_manifest_writer"][
        "component_revision_bootstrap_producer_guards"
    ]["exact_system_component_binding"] == expected_binding
    assert registry["r2_application_catalog_contract"]["bootstrap_system_manifest"][
        "component_revision_exact_component_binding"
    ] == expected_binding
    assert _intent(registry, "system-manifests.import")["bootstrap_only_contract"][
        "component_revision_exact_component_binding"
    ] == expected_binding
    assert profiles["r2_wire_profiles"]["bootstrap_system_manifests_import"][
        "component_revision_binding_rule"
    ]["exact_system_component_binding"] == expected_binding
    assert states["bootstrap_system_manifest"]["component_revision_binding_guard"][
        "exact_system_component_binding"
    ] == expected_binding
    assert fixture["bootstrap_system_manifest"]["component_revision_binding_rule"][
        "exact_system_component_binding"
    ] == expected_binding

    required_response = set(
        profiles["r2_wire_profiles"]["bootstrap_system_manifests_import"][
            "response_required_fields"
        ]
    )
    fixture_response = fixture["bootstrap_system_manifest"]["wire_response"]
    assert required_response <= set(fixture_response)
    assert profiles["r2_wire_profiles"]["bootstrap_system_manifests_import"][
        "same_manifest_replay"
    ] == {
        "precondition": "SAME_IDEMPOTENCY_KEY_AND_SAME_CANONICAL_BODY",
        "response": "IDENTICAL_TERMINAL_ATOMIC_RESPONSE",
        "new_revisions_events_outbox_audits_receipts_and_facts": "ZERO",
        "different_key_even_same_manifest_digest": "CONFLICT_WITH_ZERO_NEW_FACTS",
    }
    assert fixture["bootstrap_system_manifest"]["same_manifest_replay"] == {
        "precondition": "SAME_IDEMPOTENCY_KEY_AND_SAME_CANONICAL_BODY",
        "expected_response": "IDENTICAL_TERMINAL_ATOMIC_RESPONSE",
        "new_revisions_events_outbox_audits_receipts_and_facts": "ZERO",
        "different_key_even_same_manifest_digest": "CONFLICT_WITH_ZERO_NEW_FACTS",
    }
    bootstrap = ownership["internal_workflow"]["bootstrap_only_constraints"]
    assert bootstrap[
        "replay_requires_same_idempotency_key_and_same_canonical_body"
    ] is True
    assert bootstrap["same_key_same_body_replay_creates_zero_new_facts"] is True
    assert bootstrap["different_key_even_same_manifest_digest"] == (
        "CONFLICT_WITH_ZERO_NEW_FACTS"
    )

    compatibility = _load("compatibility.yaml")["r2_application_catalog_contract"][
        "public_bootstrap_manifest"
    ]
    domain_bootstrap = domain["bootstrap_system_manifest"]
    event_bootstrap = events["internal_catalog_manifest_writer"][
        "bootstrap_only_constraints"
    ]
    registry_bootstrap = registry["r2_application_catalog_contract"][
        "bootstrap_system_manifest"
    ]["same_manifest_replay"]
    state_bootstrap = states["bootstrap_system_manifest"]
    assert compatibility["different_key_even_same_manifest_digest"] == (
        "CONFLICT_WITH_ZERO_NEW_FACTS"
    )
    assert domain_bootstrap["different_key_even_same_manifest_digest"] == (
        "CONFLICT_WITH_ZERO_NEW_FACTS"
    )
    assert event_bootstrap["different_key_even_same_manifest_digest"] == (
        "CONFLICT_WITH_ZERO_NEW_FACTS"
    )
    assert registry_bootstrap == {
        "precondition": "SAME_IDEMPOTENCY_KEY_AND_SAME_CANONICAL_BODY",
        "result": "IDENTICAL_TERMINAL_ATOMIC_RESPONSE_WITH_ZERO_NEW_FACTS",
        "different_key_even_same_manifest_digest": "CONFLICT_WITH_ZERO_NEW_FACTS",
    }
    assert state_bootstrap["different_key_even_same_manifest_digest"] == (
        "CONFLICT_WITH_ZERO_NEW_FACTS"
    )


def test_public_register_is_registered_but_internal_manifest_result_is_active() -> None:
    ownership = _load("aggregate-ownership.yaml")["r2_application_catalog_contract"]
    compatibility = _load("compatibility.yaml")["r2_application_catalog_contract"]
    domain = _load("domain-model.yaml")["r2_application_catalog_contract"]
    registry = _load("intent-registry.yaml")["r2_application_catalog_contract"]
    profiles = _load("schema-profiles.yaml")
    fixture = _fixture()["r2_contract"]

    registered = {"revision": 1, "lifecycle_state": "REGISTERED"}
    active = {"revision": 2, "lifecycle_state": "ACTIVE"}
    assert ownership["public_register"]["response_lifecycle"] == registered
    assert ownership["public_register"]["silently_activates"] is False
    assert compatibility["public_application_register_response"]["lifecycle"] == (
        registered
    )
    assert compatibility["public_application_register_response"][
        "activation_side_effect"
    ] == "FORBIDDEN"
    assert domain["response_contracts"]["applications_register"] == registered
    assert registry["intent_overlays"]["applications.register"][
        "response_lifecycle"
    ] == registered
    assert registry["intent_overlays"]["applications.register"][
        "silently_invokes_activation"
    ] is False
    assert profiles["r2_wire_profiles"]["applications_register"][
        "application_lifecycle_constraint"
    ] == {"record_envelope.revision": 1, "lifecycle_state": "REGISTERED"}
    public_response = fixture["public_register"]["expected_response"]
    assert public_response["application"]["lifecycle_state"] == (
        "REGISTERED"
    )
    assert public_response["application"]["record_envelope"]["revision"] == 1
    assert public_response["application"][
        "exact_previous_application_binding_or_null"
    ] is None

    assert ownership["internal_workflow"]["response_lifecycle"][
        "ai_application"
    ] == active
    assert compatibility["private_bootstrap_manifest_composition"]["returns"][
        "application_lifecycle"
    ] == active
    assert domain["response_contracts"]["bootstrap_system_manifest"][
        "ai_application"
    ] == active
    assert registry["bootstrap_system_manifest"]["response_lifecycle"][
        "ai_application"
    ] == active
    result = fixture["bootstrap_system_manifest"]["wire_response"]
    assert result["application"]["lifecycle_state"] == "ACTIVE"
    assert result["application"]["record_envelope"]["revision"] == 2
    assert {
        row["record_envelope"]["revision"] for row in result["components"]
    } == {2}
    assert result["system_version_set"]["record_envelope"]["revision"] == 1
    assert result["system_assignment"]["generation"] == 1


def test_dual_authority_and_atomic_artifact_set_are_machine_aligned() -> None:
    ownership = _load("aggregate-ownership.yaml")["r2_application_catalog_contract"]
    events = _load("events.yaml")["r2_application_catalog_contract"]
    profiles = _load("schema-profiles.yaml")
    fixture = _fixture()["r2_contract"]
    workflow = ownership["internal_workflow"]

    assert workflow["owner"] == "manifest_import_coordinator"
    assert workflow["authenticated_root_intent"] == "system-manifests.import"
    assert workflow["caller_surface"] == "INTERNAL_ONLY"
    assert workflow["mode"] == "single_local_postgresql_unit_of_work"
    assert workflow["authority_layers"] == {
        "lifecycle_subject_and_receipt_actor": "application-catalog-controller",
        "root_command_audit_actor": "exact_authenticated_initiating_principal",
        "both_layers_required_same_transaction": True,
        "neither_layer_may_substitute_for_the_other": True,
    }
    assert workflow["exact_authority_context_fields"] == [
        "authenticated_request_digest",
        "manifest_digest",
        "idempotency_key",
        "workspace_id",
        "initiating_principal_id",
        "initiating_principal_type",
    ]
    required = set(fixture["bootstrap_system_manifest"][
        "required_atomic_artifacts"
    ])
    assert required == (
        set(workflow["per_subject_atomic_artifacts"])
        | set(workflow["root_atomic_artifacts"])
        | set(workflow["bootstrap_graph_atomic_artifacts"])
    )
    writer = events["internal_catalog_manifest_writer"]
    assert writer["event_outbox_subject_receipt_and_controller_audit_share_transaction"] is True
    assert writer["initiating_principal_audit_shares_root_transaction"] is True
    activation = profiles["common"]["lifecycle_activation_transaction_v5"]
    assert activation["r2_contract_status"] == (
        "FROZEN_FOR_INTERNAL_CATALOG_MANIFEST_COORDINATOR"
    )
    assert activation["r2_runtime_status"] == (
        "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
    )
    assert activation["transaction_constraints"][
        "all_required_artifacts_share_transaction_id"
    ] is True


def test_exactly_eleven_r2_plus_bootstrap_intents_are_contract_activated() -> None:
    registry = _load("intent-registry.yaml")
    overlay = registry["r2_application_catalog_contract"]
    by_name = {row["name"]: row for row in registry["intents"]}

    expected_intents = [
        "capabilities.get",
        "applications.register",
        "applications.get",
        "applications.list",
        "environments.register",
        "environments.get",
        "system-components.register",
        "system-components.get",
        "dependency-edges.record",
        "dependency-edges.get",
        "system-manifests.import",
    ]
    assert overlay["activated_contract_intents"] == expected_intents
    assert set(overlay["intent_overlays"]) == set(expected_intents)
    assert registry["activation_flags"] == {
        "http_routes": False,
        "cli_commands": False,
        "sdk_methods": False,
        "capability_discovery": False,
        "mcp_tools": False,
        "a2a_agent_card": False,
        "a2a_transport": False,
        "a2a_methods": False,
    }
    profile_names = {
        "capabilities.get": "capabilities_get",
        "applications.register": "applications_register",
        "applications.get": "applications_get",
        "applications.list": "applications_list",
        "environments.register": "environments_register",
        "environments.get": "environments_get",
        "system-components.register": "system_components_register",
        "system-components.get": "system_components_get",
        "dependency-edges.record": "dependency_edges_record",
        "dependency-edges.get": "dependency_edges_get",
        "system-manifests.import": "bootstrap_system_manifests_import",
    }
    for name in overlay["activated_contract_intents"]:
        assert by_name[name]["delivery_slice"] in {"V5-0C", "V5-1A", "V5-1B"}
        assert by_name[name]["implementation_status"] == (
            "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
        )
        assert by_name[name]["field_contract_ref"] == (
            "contracts/v5/schema-profiles.yaml#r2_wire_profiles/"
            + profile_names[name]
        )
        assert overlay["intent_overlays"][name]["wire_contract_status"] == (
            "FROZEN_R2_R3_BOOTSTRAP" if name == "system-manifests.import" else "FROZEN_R2"
        )

    full_manifest = by_name["system-manifests.import"]
    assert full_manifest["delivery_slice"] == "V5-1B"
    assert full_manifest["r2_delivery_mode"] == "R3_BOOTSTRAP_COUPLED_TO_R2"
    assert full_manifest["implementation_status"] == (
        "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
    )
    assert full_manifest["bootstrap_only_contract"]["initial_system_version_set_count"] == 1
    assert full_manifest["bootstrap_only_contract"]["standalone_system_versions_record"] == (
        "FORBIDDEN"
    )
    assert overlay["bootstrap_system_manifest"]["public_intent"] is True
    assert overlay["bootstrap_system_manifest"]["initial_system_version_set_count"] == 1
    assert overlay["r3_full_boundary"] == {
        "standalone_system_versions_record": "NOT_IMPLEMENTED",
        "second_system_version_set": "NOT_IMPLEMENTED",
        "system_versions_get": "NOT_IMPLEMENTED",
        "system_versions_diff": "NOT_IMPLEMENTED",
        "semantic_diff": "NOT_IMPLEMENTED",
    }
    assert set(overlay["excluded_intent_stages"]) >= {"V5-1C", "V5-3", "V5-4"}
    assert {
        "system-versions.record",
        "system-versions.get",
        "system-versions.diff",
    }.isdisjoint(overlay["activated_contract_intents"])
    # 非 R2 intent 保持 NOT_IMPLEMENTED，唯一例外是 R3-full 激活的三个
    # system-versions intent 与 R4-full 激活的五个 first-system-case intent
    # （均为 IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER）。
    r3_full_intents = set(registry["r3_full_contract"]["activated_contract_intents"])
    r4_full_intents = set(registry["r4_full_contract"]["activated_contract_intents"])
    v5_2b_intents = set(
        registry["v5_2b_public_operation_contract"]["activated_contract_intents"]
    )
    activated_exceptions = r3_full_intents | r4_full_intents | v5_2b_intents
    assert all(
        row["implementation_status"]
        == (
            "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
            if row["name"] in activated_exceptions
            else "NOT_IMPLEMENTED"
        )
        for row in registry["intents"]
        if row["name"] not in expected_intents
    )

    assert by_name["environments.register"]["http"]["path"] == (
        "/api/v2/environments"
    )
    assert by_name["system-components.register"]["http"]["path"] == (
        "/api/v2/system-components"
    )
    assert by_name["dependency-edges.record"]["http"]["path"] == (
        "/api/v2/dependency-edges"
    )
    assert overlay["cli_utility_boundary"] == {
        "system-manifest.validate": {
            "classification": "LOCAL_FILE_VALIDATION_UTILITY",
            "public_intent": False,
            "transport": "NONE",
            "capability_discovery": "FORBIDDEN",
        },
        "init_or_repository_discovery": {
            "owner_stage": "R3_FULL",
            "r2_cli_visibility": "HIDDEN",
            "capability_discovery": "FORBIDDEN",
        },
    }


def test_r2_wire_profiles_match_runtime_models_and_fixtures_validate() -> None:
    profiles = _load("schema-profiles.yaml")["r2_wire_profiles"]
    fixture = _fixture()

    response_models = {
        "capabilities_get": V5ServerCapabilitiesResponse,
        "applications_register": ApplicationRegisterResponse,
        "applications_get": ApplicationGetResponse,
        "applications_list": ApplicationListResponse,
        "environments_register": EnvironmentRegisterResponse,
        "environments_get": EnvironmentGetResponse,
        "system_components_register": ComponentRegisterResponse,
        "system_components_get": ComponentGetResponse,
        "dependency_edges_record": DependencyEdgeRecordResponse,
        "dependency_edges_get": DependencyEdgeGetResponse,
        "bootstrap_system_manifests_import": SystemManifestImportResponse,
    }
    for profile_name, model in response_models.items():
        required, optional = _required_and_optional_fields(model)
        profile = profiles[profile_name]
        assert set(profile["response_required_fields"]) == required, profile_name
        assert set(profile.get("response_optional_fields", [])) == optional, profile_name
        assert profile["additional_properties"] is False, profile_name

    assert set(profiles["capabilities_get"]["data_required_fields"]) == set(
        V5ServerCapabilitiesData.model_fields
    )
    assert set(
        profiles["capabilities_get"]["enabled_intent_required_fields"]
    ) == set(V5EnabledIntent.model_fields)

    request_models = {
        "applications_register": ApplicationRegisterRequest,
        "environments_register": EnvironmentRegisterRequest,
        "system_components_register": ComponentRegisterRequest,
        "dependency_edges_record": DependencyEdgeRecordRequest,
        "bootstrap_system_manifests_import": SystemManifestImportRequest,
    }
    for profile_name, model in request_models.items():
        required, optional = _required_and_optional_fields(model)
        profile = profiles[profile_name]
        assert set(profile["request_required_fields"]) == required, profile_name
        assert set(profile.get("request_optional_fields", [])) == optional, profile_name

    for row in fixture["applications"]:
        ApplicationRegisterRequest.model_validate(row["register_request"])
    for row in fixture["environments"]:
        EnvironmentRegisterRequest.model_validate(row["register_request"])
    for row in fixture["components"]:
        ComponentRegisterRequest.model_validate(row["register_request"])
    for row in fixture["dependency_edges"]:
        DependencyEdgeRecordRequest.model_validate(row["record_request"])

    r2 = fixture["r2_contract"]
    ApplicationRegisterResponse.model_validate(
        r2["public_register"]["expected_response"]
    )
    ApplicationListResponse.model_validate(
        r2["applications_list"]["expected_response"]
    )
    manifest = r2["bootstrap_system_manifest"]
    SystemManifestImportRequest.model_validate(manifest["wire_request"])
    SystemManifestImportResponse.model_validate(manifest["wire_response"])
    assert "manifest_digest" not in manifest["wire_request"]
    response = manifest["wire_response"]
    version_set = response["system_version_set"]
    assert {
        binding["revision"]
        for binding in version_set["exact_component_revision_bindings"]
    } == {1}
    assert version_set["exact_topology_revision_binding"]["revision"] == 1
    assert response["bootstrap_attestation"][
        "exact_initial_system_version_set_binding"
    ]["revision"] == 1
    assignment = response["system_assignment"]
    assert {
        binding["revision"]
        for binding in assignment["exact_slot_version_set_bindings"]
    } == {1}
    assert assignment["exact_assignment_authority_binding"]["revision"] == 1


def test_application_list_and_capabilities_are_scoped_closed_and_console_safe() -> None:
    registry = _load("intent-registry.yaml")
    overlay = registry["r2_application_catalog_contract"]
    profiles = _load("schema-profiles.yaml")["r2_wire_profiles"]
    fixture = _fixture()["r2_contract"]

    expected_intents = overlay["activated_contract_intents"]
    capability = profiles["capabilities_get"]
    assert len(expected_intents) == 11
    # capabilities_get 的 contract allowlist 已随 V5-2B 扩展为 23
    # （11 个 R2 + 三个 system-versions + 五个 first-system-case），R2 overlay
    # 的历史 11 个 allowlist 本身保持不变。
    r3_full_intents = registry["r3_full_contract"]["activated_contract_intents"]
    r4_full_intents = registry["r4_full_contract"]["activated_contract_intents"]
    v5_2b_intents = registry["v5_2b_public_operation_contract"]["activated_contract_intents"]
    assert capability["contract_allowlist_exact_count"] == (
        len(expected_intents)
        + len(r3_full_intents)
        + len(r4_full_intents)
        + len(v5_2b_intents)
    )
    assert capability["contract_allowlist_exact_names"] == (
        expected_intents + r3_full_intents + r4_full_intents + v5_2b_intents
    )
    assert capability["scope_filtered"] is True
    assert capability["response_intents"] == (
        "INTERSECTION_OF_CONTRACT_ALLOWLIST_AND_AUTHENTICATED_SCOPE_AUTHORIZED_INTENTS"
    )
    assert capability["unauthorized_intent_names_omitted"] is True
    assert capability["additional_properties"] is False
    assert fixture["capability_discovery"] == {
        "contract_allowlist_exactly_matches_activated_contract_intents": True,
        "contract_allowlist_count": 11,
        "response_intents": (
            "INTERSECTION_OF_CONTRACT_ALLOWLIST_AND_AUTHENTICATED_SCOPE_AUTHORIZED_INTENTS"
        ),
        "unauthorized_intent_names_omitted": True,
    }

    intent = _intent(registry, "applications.list")
    assert intent["scope"] == "applications:read"
    assert intent["authorization_condition"] == (
        "workspace_and_required_project_scoped_application_authorized_reader"
    )
    assert intent["http"] == {
        "method": "GET",
        "path": "/api/v2/applications",
        "query_parameters": {
            "required": ["project_id"],
            "optional": ["cursor", "limit"],
        },
        "operation_id": "listApplications",
    }
    assert intent["cli"] == "application list"
    assert intent["pagination"] == {
        "cursor": "server_issued_opaque",
        "visibility_filter_before_page_and_count": True,
    }
    assert overlay["intent_overlays"]["applications.list"][
        "unauthorized_resource_visibility"
    ] == "OPAQUE_OMISSION_WITHOUT_EXISTENCE_DIGEST_OR_COUNT_LEAK"

    list_profile = profiles["applications_list"]
    assert list_profile["request_required_fields"] == ["project_id"]
    assert list_profile["request_optional_fields"] == ["cursor", "limit"]
    assert list_profile["workspace_source"] == "AUTHENTICATED_HEADER_CONTEXT"
    assert list_profile["response_required_fields"] == [
        "schema_version",
        "workspace_id",
        "request_id",
        "audit_ref",
        "items",
        "next_cursor",
    ]
    assert list_profile["item_required_fields"] == [
        "application",
        "environments",
        "system_components",
        "dependency_edges",
    ]
    assert list_profile["item_field_refs"] == {
        "application": (
            "contracts/v5/schema-profiles.yaml#r2_wire_record_shapes/ApplicationRecord"
        ),
        "environments": (
            "list[contracts/v5/schema-profiles.yaml#r2_wire_record_shapes/EnvironmentRecord]"
        ),
        "system_components": (
            "list[contracts/v5/schema-profiles.yaml#r2_wire_record_shapes/SystemComponentRecord]"
        ),
        "dependency_edges": (
            "list[contracts/v5/schema-profiles.yaml#r2_wire_record_shapes/DependencyEdgeRecord]"
        ),
    }
    record_shapes = _load("schema-profiles.yaml")["r2_wire_record_shapes"]
    assert set(record_shapes) == {
        "ApplicationRecord",
        "EnvironmentRecord",
        "SystemComponentRecord",
        "DependencyEdgeRecord",
    }
    assert all(shape["additional_properties"] is False for shape in record_shapes.values())
    assert record_shapes["ApplicationRecord"]["authority_profile_ref"].endswith(
        "#profiles/ai_application"
    )
    assert record_shapes["SystemComponentRecord"]["authority_profile_ref"].endswith(
        "#profiles/system_component"
    )
    assert record_shapes["EnvironmentRecord"]["required_fields"] == [
        "record_envelope",
        "environment_id",
        "workspace_id",
        "application_id",
        "logical_name",
        "risk_classification",
        "lifecycle_state",
    ]
    assert record_shapes["DependencyEdgeRecord"]["required_fields"] == [
        "record_envelope",
        "edge_id",
        "workspace_id",
        "application_id",
        "from_component_id",
        "to_component_id",
        "relation",
        "required",
        "edge_digest",
    ]
    assert list_profile["required_scope"] == "applications:read"
    assert list_profile["workspace_and_object_authorization_before_visibility"] is True
    assert list_profile["visibility_filter_before_page_and_count"] is True
    assert list_profile["cursor_profile"] == (
        "SERVER_ISSUED_OPAQUE_WORKSPACE_SCOPE_BOUND"
    )
    assert list_profile["forged_expired_or_cross_scope_cursor"] == (
        "REQUEST_INVALID_ZERO_ITEMS"
    )
    assert list_profile["unauthorized_collection_member_visibility"] == (
        "OMIT_WITHOUT_EXISTENCE_DIGEST_OR_COUNT_LEAK"
    )
    assert list_profile["unauthorized_items_are_omitted_without_count_leak"] is True
    assert all(
        list_profile[key] is False
        for key in (
            "request_additional_properties",
            "response_additional_properties",
            "item_additional_properties",
            "additional_properties",
        )
    )

    list_fixture = fixture["applications_list"]
    assert list_fixture["request"] == {
        "project_id": "proj_01J0000000000001",
        "cursor": None,
        "limit": 50,
    }
    response = list_fixture["expected_response"]
    assert set(response) == {
        "schema_version",
        "workspace_id",
        "request_id",
        "audit_ref",
        "items",
        "next_cursor",
    }
    assert response["schema_version"] == "2.0"
    assert response["workspace_id"] == "ws_01J0000000000001"
    assert len(response["items"]) == 1
    item = response["items"][0]
    assert set(item) == {
        "application",
        "environments",
        "system_components",
        "dependency_edges",
    }
    application = item["application"]
    assert application["project_id"] == list_fixture["request"]["project_id"]
    assert application["lifecycle_state"] == "ACTIVE"
    assert application["record_envelope"]["revision"] == 2
    assert application["exact_previous_application_binding"]["revision"] == 1
    assert application["record_envelope"]["record_digest"] == (
        "sha256:2222222222222222222222222222222222222222222222222222222222222222"
    )
    nested = (
        item["environments"]
        + item["system_components"]
        + item["dependency_edges"]
    )
    assert nested
    assert all(row["workspace_id"] == response["workspace_id"] for row in nested)
    assert all(
        row["application_id"] == application["application_id"] for row in nested
    )
    assert all(
        row["record_envelope"]["immutable"] is True
        and row["record_envelope"]["workspace_id"] == response["workspace_id"]
        and row["record_envelope"]["authority_receipt_id"].startswith("arec_")
        for row in [application, *nested]
    )
    assert all(
        component["lifecycle_state"] == "ACTIVE"
        and component["record_envelope"]["revision"] == 2
        and component["exact_previous_system_component_binding"]["revision"] == 1
        for component in item["system_components"]
    )
    component_ids = {
        component["component_id"] for component in item["system_components"]
    }
    assert all(
        edge["from_component_id"] in component_ids
        and edge["to_component_id"] in component_ids
        for edge in item["dependency_edges"]
    )
    assert list_fixture["authorization"] == {
        "scope": "applications:read",
        "project_id_required": True,
        "requested_project_must_be_authorized_and_equal_application_project": True,
        "workspace_filter_before_page_and_count": True,
        "unauthorized_resource_visibility": (
            "OPAQUE_OMISSION_WITHOUT_EXISTENCE_DIGEST_OR_COUNT_LEAK"
        ),
        "unauthorized_items_omitted_without_count_leak": True,
        "cursor": "SERVER_ISSUED_OPAQUE_WORKSPACE_SCOPE_BOUND",
    }
    expected_console = {
        "public_intents": ["applications.list", "applications.get"],
        "forbidden_internal_endpoint": "/v1/applications",
        "credential_source": "UI_MEMORY_ONLY",
        "credential_in_bundle_static_config_log_or_persistence": "FORBIDDEN",
    }
    assert list_fixture["console"] == expected_console
    assert overlay["console_read_contract"] == {
        "public_intents": ["applications.list", "applications.get"],
        "forbidden_internal_dependency": "/v1/applications",
        "credential_source": "UI_MEMORY_ONLY",
        "credential_in_bundle_static_config_log_or_persistence": "FORBIDDEN",
    }


def test_standalone_activation_is_forbidden_on_every_transport() -> None:
    expected = {
        "http": "FORBIDDEN",
        "cli": "FORBIDDEN",
        "sdk": "FORBIDDEN",
        "mcp": "FORBIDDEN",
        "a2a": "FORBIDDEN",
        "capability_discovery": "FORBIDDEN",
    }
    ownership = _load("aggregate-ownership.yaml")["r2_application_catalog_contract"]
    compatibility = _load("compatibility.yaml")["r2_application_catalog_contract"]
    events = _load("events.yaml")["r2_application_catalog_contract"]
    registry = _load("intent-registry.yaml")["r2_application_catalog_contract"]
    fixture = _fixture()["r2_contract"]
    assert ownership["standalone_activation_transports"] == expected
    assert compatibility["standalone_activation_transports"] == expected
    assert events["standalone_activation_event_routes"] == expected
    assert registry["standalone_activation"] | {"public_intent_exists": False} == (
        {"public_intent_exists": False, **expected}
    )
    assert fixture["standalone_activation_transports"] == expected
    public_names = {row["name"] for row in _load("intent-registry.yaml")["intents"]}
    assert "applications.activate" not in public_names
    assert "system-components.activate" not in public_names


def test_adversarial_fixture_closes_every_composition_bypass() -> None:
    outcomes = {
        row["name"]: row["expected"] for row in _fixture()["r2_negative_cases"]
    }
    assert outcomes == {
        "direct_application_activation": "DENY_AND_AUDIT_ZERO_PARTIAL_ROWS",
        "direct_component_activation": "DENY_AND_AUDIT_ZERO_PARTIAL_ROWS",
        "syntactic_manifest_context_only": "DENY_AND_AUDIT_ZERO_PARTIAL_ROWS",
        "audit_uri_without_row": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "controller_receipt_without_initiating_audit": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "initiating_audit_without_controller_receipt": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "cross_transaction_authority": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "forged_permit": "DENY_AND_AUDIT_ZERO_PARTIAL_ROWS",
        "outbox_failure": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "same_key_different_manifest": "CONFLICT_AND_AUDIT_ZERO_NEW_REVISIONS",
        "different_key_same_body_and_manifest_digest": (
            "CONFLICT_AND_AUDIT_ZERO_NEW_FACTS"
        ),
        "standalone_system_versions_record": "DENY_AND_AUDIT_ZERO_PARTIAL_ROWS",
        "second_workspace_bootstrap": "CONFLICT_AND_AUDIT_ZERO_NEW_REVISIONS",
        "bootstrap_claims_diff": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "unscoped_application_list": "DENY_AND_AUDIT_ZERO_ITEMS",
        "forged_or_cross_scope_cursor": "REQUEST_INVALID_ZERO_ITEMS",
    }


def test_event_and_state_machine_activation_is_internal_only_and_not_runtime_proof() -> None:
    events = _load("events.yaml")
    states = _load("state-machines.yaml")
    for event in (
        events["ai_application"]["events"]["application.activated"],
        events["system_component"]["events"]["system_component.activated"],
    ):
        assert event["r2_internal_writer_contract_status"] == (
            "ENABLED_ONLY_BEHIND_COORDINATOR"
        )
        assert event["r2_internal_writer_runtime_status"] == (
            "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
        )
        assert event["standalone_event_route_status"] == "FORBIDDEN"

    activation_transitions = []
    for machine_name, event_name in (
        ("ai_application", "application.activated"),
        ("system_component", "system_component.activated"),
    ):
        transition = next(
            row
            for row in states["machines"][machine_name]["transitions"]
            if row["on"] == event_name
        )
        activation_transitions.append(transition)
    assert all(
        row["r2_internal_coordinator_contract_status"] == "FROZEN"
        and row["r2_runtime_status"] == "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
        and row["standalone_transition_call"] == "FORBIDDEN"
        for row in activation_transitions
    )
