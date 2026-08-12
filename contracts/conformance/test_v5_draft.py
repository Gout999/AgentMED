"""V5 preparation contracts are coherent, strict, and intentionally disabled."""
from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "v5"
V4_OWNERSHIP = ROOT / "v4" / "aggregate-ownership.yaml"
FIRST_CASE_FIXTURE = V5 / "fixtures" / "first-system-case.yaml"


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently keeping the last value."""


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
    return yaml.safe_load((V5 / name).read_text(encoding="utf-8"))


def _reachable_states(machine: dict) -> set[str]:
    graph: dict[str, set[str]] = defaultdict(set)
    for transition in machine["transitions"]:
        graph[transition["from"]].add(transition["to"])
    reached = {machine["initial"]}
    queue = deque(reached)
    while queue:
        source = queue.popleft()
        for target in graph[source]:
            if target not in reached:
                reached.add(target)
                queue.append(target)
    return reached


def test_v5_yaml_has_no_duplicate_mapping_keys() -> None:
    paths = sorted(V5.glob("*.yaml")) + [FIRST_CASE_FIXTURE]
    for path in paths:
        yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)


def test_all_v5_yaml_is_explicitly_draft_and_not_implemented() -> None:
    paths = sorted(V5.glob("*.yaml"))
    assert {path.name for path in paths} == {
        "aggregate-ownership.yaml",
        "compatibility.yaml",
        "domain-model.yaml",
        "events.yaml",
        "intent-registry.yaml",
        "schema-profiles.yaml",
        "state-machines.yaml",
    }
    for path in paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert document["status"] == "draft_target_contract", path
        assert document["runtime_status"] == "NOT_IMPLEMENTED", path


def test_runtime_versions_evaluation_assets_and_dataset_roles_are_independent() -> None:
    document = _load("domain-model.yaml")
    resources = document["resources"]
    version_set = resources["system_version_set"]
    assert "evaluation_bundle_id" in version_set["forbidden_fields"]
    assert "application_catalog_revision" in version_set["forbidden_fields"]
    assert "duplicate_top_level_policy_bundle" in version_set["forbidden_fields"]
    assert "exact_topology_revision_binding" in version_set["required_fields"]
    assert document["dataset_roles"]["runtime_and_governing_evaluation_assets_must_be_disjoint"] is True
    assert "runtime_dataset_and_governing_evaluation_assets_are_disjoint" in (
        resources["evaluation_bundle"]["invariants"]
    )
    assert {
        "required_evidence_facets_are_subset_of_canonical_facets",
        "bundle_has_no_gate_verdict",
    } <= set(resources["evaluation_bundle"]["invariants"])


def test_identity_assurance_summary_is_derived_and_raw_bindings_are_rechecked() -> None:
    identity = _load("domain-model.yaml")["identity_assurance"]
    assert set(identity["exact_release_disallowed_when_required_component_is"]) == {
        "MUTABLE_ALIAS",
        "OBSERVED_ONLY",
        "UNKNOWN",
    }
    assert identity["summary_rule"] == {
        "derived_deterministically_from_exact_component_revision_bindings": True,
        "caller_supplied_summary_is_not_authority": True,
        "release_controller_rechecks_each_raw_binding": True,
    }


def test_desired_observed_and_effect_are_separate_facts() -> None:
    resources = _load("domain-model.yaml")["resources"]
    assert resources["system_assignment"]["fact_class"] == "DECLARED_CONFIGURATION"
    assert resources["observed_state_snapshot"]["fact_class"] == "OBSERVED_RUNTIME"
    assert resources["external_effect_receipt"]["fact_class"] == "EXTERNAL_EFFECT"
    assert "observation_is_independent_from_assignment_readback" in (
        resources["observed_state_snapshot"]["invariants"]
    )
    assert set(resources["operation_execution_receipt"]["does_not_prove"]) >= {
        "desired_assignment_changed",
        "runtime_observed_match",
        "external_effect_verified",
    }
    origin = resources["external_effect_receipt"]["origin_union"]
    assert set(origin["variants"]) == {"CASELOOP_EXTERNAL_OPERATION", "GOVERNED_SYSTEM_EPISODE"}
    assert "exact_system_episode_snapshot_binding" in (
        origin["variants"]["GOVERNED_SYSTEM_EPISODE"]["requires"]
    )


def test_episode_view_is_mutable_projection_and_gate_evidence_is_snapshot() -> None:
    resources = _load("domain-model.yaml")["resources"]
    view = resources["system_episode_view"]
    snapshot = resources["system_episode_snapshot"]
    assert view["kind"] == "projection"
    assert view["commands"] == []
    assert set(view["required_projection_fields"]) >= {
        "projection_revision",
        "as_of",
        "source_event_watermark",
        "exact_system_assignment_binding",
    }
    assert snapshot["kind"] == "immutable_record"
    assert snapshot["uses_record_envelope"] is True
    assert "gate_and_attribution_bind_snapshot_not_mutable_view" in snapshot["invariants"]
    profile = _load("schema-profiles.yaml")["profiles"]["system_evaluation_plan"]
    assert "exact_required_episode_snapshot_bindings" in profile["required_fields"]
    assert "mutable_episode_view_is_forbidden_as_gate_evidence" in profile["invariants"]


def test_every_authoritative_v5_resource_uses_common_envelope_and_v5_receipt() -> None:
    domain = _load("domain-model.yaml")
    ownership = _load("aggregate-ownership.yaml")
    profiles = _load("schema-profiles.yaml")
    required_envelope = {
        "schema_version",
        "workspace_id",
        "revision",
        "recorded_by_principal",
        "recorded_at",
        "immutable",
        "hash_rule",
        "record_digest",
        "authority_receipt_id",
    }
    assert set(domain["record_envelope"]["required_fields"]) == required_envelope
    for mapping in ownership["record_authority"].values():
        resource = domain["resources"][mapping["resource"]]
        assert resource["kind"] != "projection"
        assert resource["uses_record_envelope"] is True
    for resource in domain["resources"].values():
        if resource["kind"] == "projection":
            assert resource["uses_record_envelope"] is False
    exact_kinds = set(profiles["common"]["exact_record_binding_v5"]["kinds"])
    assert set(ownership["record_authority"]) <= exact_kinds
    assert set(ownership["schema_major_2_lifecycle_authority"]) <= exact_kinds
    assert set(ownership["schema_major_2_subordinate_record_authority"]) <= exact_kinds
    receipt = profiles["common"]["authority_receipt_v5"]
    assert receipt["field_refs"]["subject"] == "exact_record_binding_v5"
    assert receipt["additional_properties"] is False


def test_closed_v4_schemas_use_strict_major_2_successors_not_inheritance() -> None:
    domain = _load("domain-model.yaml")
    profiles = _load("schema-profiles.yaml")
    expected = {
        "system_candidate_revision",
        "system_evaluation_plan",
        "system_gate_execution",
        "system_gate_track_receipt",
        "system_gate_report",
        "system_workorder",
        "system_recovery_workorder",
        "system_approval_grant",
        "system_capability_lease",
        "system_external_operation",
    }
    assert expected <= set(profiles["profiles"])
    assert profiles["strictness"]["closed_v4_schema_extension"] == "FORBIDDEN"
    for name in expected:
        profile = profiles["profiles"][name]
        assert profile["additional_properties"] is False
        if name == "system_approval_grant":
            assert profile["adds_business_state_machine"] is True
            assert profile["defines_missing_v4_target_state_machine"] is True
        else:
            assert profile["adds_business_state_machine"] is False
        assert domain["resources"][name]["kind"] == "schema_major_2_profile"
        assert domain["resources"][name]["extends_closed_v4_json_schema"] is False
    serialized = (V5 / "domain-model.yaml").read_text(encoding="utf-8")
    assert "kind: subtype" not in serialized
    assert "kind: v4_record_extension" not in serialized


def test_schema_major_profiles_keep_v4_logical_owners() -> None:
    ownership = _load("aggregate-ownership.yaml")
    v4 = yaml.safe_load(V4_OWNERSHIP.read_text(encoding="utf-8"))
    for name, profile in ownership["schema_major_2_lifecycle_profiles"].items():
        logical = profile["logical_resource"].removeprefix("v4.")
        if logical == "gate_track_receipt":
            assert profile["owner"] == "gate-controller"
        else:
            assert profile["owner"] == v4["resources"][logical]["owner"]
        if name == "system_approval_grant":
            assert profile["defines_missing_v4_target_state_machine"] is True
        else:
            assert profile.get("adds_business_state_machine", False) is False
    assert ownership["imports"]["v4"]["closed_json_schemas_are_not_inherited"] is True
    assert ownership["imports"]["v4"]["implementation_boundary"][
        "exact_executable_chain_currently_ends_at"
    ] == "v4.workorder"


def test_release_plan_is_frozen_before_gate_without_approval_cycle() -> None:
    domain = _load("domain-model.yaml")
    ownership = _load("aggregate-ownership.yaml")
    plan = domain["resources"]["release_plan"]
    assert ownership["resources"]["release_plan"]["owner"] == "proposal-controller"
    assert ownership["resources"]["release_plan"]["commands"] == ["release-plans.freeze"]
    assert set(plan["forbidden_fields"]) == {
        "exact_workorder_binding",
        "exact_approval_binding",
        "exact_policy_binding",
        "nonce",
        "expires_at",
    }
    assert "candidate_release_is_frozen_before_gate" in plan["invariants"]
    assert set(plan["plan_kind_union"]["variants"]) == {
        "CANDIDATE_RELEASE",
        "BREAK_GLASS_RECOVERY",
    }
    gate = _load("schema-profiles.yaml")["profiles"]["system_gate_report"]
    workorder = _load("schema-profiles.yaml")["profiles"]["system_workorder"]
    # release plan binding 是 purpose 条件化：仅 RELEASE_AUTHORIZATION 必须 exact 绑定 pre-Gate ReleasePlan，
    # CANDIDATE_VERIFICATION 明确禁止绑定（candidate_verification.does_not_bind_release_plan）
    assert "exact_release_plan_binding" not in gate["required_fields"]
    binding = gate["release_plan_binding_union"]["variants"]
    assert binding["RELEASE_AUTHORIZATION"]["required"] is True
    assert "exact_release_plan_binding" in binding["RELEASE_AUTHORIZATION"]["required_fields"]
    assert binding["RELEASE_AUTHORIZATION"]["exact_binds_pre_gate_sealed_release_plan"] is True
    assert binding["CANDIDATE_VERIFICATION"]["does_not_bind_release_plan"] is True
    assert "exact_release_plan_binding" in binding["CANDIDATE_VERIFICATION"]["forbidden_fields"]
    # WorkOrder 只在 release-authorizing PASS 后创建，仍无条件 exact 绑定 ReleasePlan
    assert "exact_release_plan_binding" in workorder["required_fields"]
    assert workorder["p0_constants"]["required_authorization"] == "HUMAN_APPROVAL"


def test_system_external_operation_has_exact_request_and_success_guards() -> None:
    events = _load("events.yaml")
    requested = events["system_external_operation"]["events"]["external_operation.requested"]
    succeeded = events["system_external_operation"]["events"]["external_operation.succeeded"]
    assert set(requested["payload_required"]) >= {
        "operation_kind",
        "exact_system_workorder_or_recovery_workorder_binding",
        "exact_human_approval_grant_binding",
        "exact_release_plan_binding",
        "exact_target_system_version_set_binding",
        "expected_assignment_generation",
        "nonce",
        "expires_at",
    }
    assert succeeded["adapter_receipt_alone_is_sufficient"] is False
    assert {
        "assignment_generation_is_exact_target_generation",
        "observed_snapshot_is_independent_complete_and_match",
        "no_required_child_operation_or_effect_is_unknown",
        "every_frozen_effect_requirement_is_covered_exactly_once_by_an_allowed_success_status",
        "failed_partial_not_observed_or_unknown_required_effect_cannot_succeed",
    } <= set(succeeded["guards"])
    state = _load("state-machines.yaml")["schema_major_2_external_operation_profile"]
    assert state["adds_states"] == []
    assert state["adapter_receipt_alone_can_succeed"] is False
    assert state["ambiguity_transition"] == "external_operation.unknown"


def test_assignment_bootstrap_update_and_rollback_have_one_authority_path() -> None:
    domain = _load("domain-model.yaml")
    ownership = _load("aggregate-ownership.yaml")
    states = _load("state-machines.yaml")
    assignment = domain["resources"]["system_assignment"]
    assert {key: assignment["bootstrap"][key] for key in (
        "generation",
        "expected_previous_generation",
        "exact_authority_kind",
        "trusted_principal_required",
        "proves_desired_only",
    )} == {
        "generation": 1,
        "expected_previous_generation": None,
        "exact_authority_kind": "BOOTSTRAP_ATTESTATION",
        "trusted_principal_required": True,
        "proves_desired_only": True,
    }
    assert assignment["update"]["generation_rule"] == "previous_plus_one"
    assert assignment["update"]["exact_authority_kinds"] == ["SYSTEM_EXTERNAL_OPERATION"]
    assert assignment["update"]["external_operation_root_required"] is True
    commands = ownership["resources"]["system_assignment"]["commands"]
    assert "system-assignments.emergency-restore" not in commands
    serialized = (V5 / "aggregate-ownership.yaml").read_text(encoding="utf-8")
    assert "emergency_restored" not in serialized
    machine = states["machines"]["system_assignment"]
    resume = next(t for t in machine["transitions"] if t["on"] == "system_assignment.resumed")
    assert "independent_observed_match" in resume["guard"]
    assert states["rollback_rules"]["direct_assignment_restore_event_exists"] is False
    assert states["rollback_rules"]["rollback_root"] == "system_external_operation"


def test_new_commands_have_one_owner_and_projections_accept_no_commands() -> None:
    ownership = _load("aggregate-ownership.yaml")
    command_owners: dict[str, list[str]] = defaultdict(list)
    for resource_name, resource in ownership["resources"].items():
        for command in resource.get("commands", []):
            command_owners[command].append(resource_name)
        if resource["kind"] == "projection":
            assert resource["commands"] == []
    assert command_owners
    assert all(len(owners) == 1 for owners in command_owners.values())
    assert ownership["resources"]["system_assignment"]["owner"] == "version-controller"
    assert ownership["rules"]["executor_can_mutate_system_assignment"] is False
    assert "mutate_system_assignment_directly" in (
        ownership["components"]["release_execution_manager"]["must_not"]
    )
    assert "author_or_modify_release_plan" in (
        ownership["components"]["release_execution_manager"]["must_not"]
    )


def test_new_record_authority_maps_exact_owner_command_and_event() -> None:
    ownership = _load("aggregate-ownership.yaml")
    resources = ownership["resources"]
    commands = {
        command: (name, resource["owner"])
        for name, resource in resources.items()
        for command in resource.get("commands", [])
    }
    events = {
        event: (name, resource["owner"])
        for name, resource in resources.items()
        for event in resource.get("events", [])
    }
    mapped_commands: set[str] = set()
    for mapping in ownership["record_authority"].values():
        resource_name = mapping["resource"]
        assert resources[resource_name]["owner"] == mapping["owner"]
        for command, mapped_events in mapping["command_events"].items():
            assert commands[command] == (resource_name, mapping["owner"])
            assert mapped_events
            assert all(events[event] == (resource_name, mapping["owner"]) for event in mapped_events)
            mapped_commands.add(command)
    authoritative_commands = {
        command
        for resource in resources.values()
        if resource["kind"] != "projection"
        for command in resource.get("commands", [])
    }
    assert mapped_commands == authoritative_commands


def test_cross_owner_release_reactions_return_receipts_to_root_operation() -> None:
    reactions = _load("aggregate-ownership.yaml")["authorized_cross_owner_reactions"]
    request = reactions["release_assignment_request"]
    assert request["target_owner"] == "version-controller"
    assert request["target_command"] == "system-assignments.set-desired"
    assert request["target_revalidates_exact_authority"] is True
    assert reactions["assignment_receipt_return"]["target_command"] == (
        "system-external-operations.attach-assignment-receipt"
    )
    assert reactions["observed_verification_request"]["requires_independent_target_observation"] is True
    assert reactions["observation_receipt_return"]["target_command"] == (
        "system-external-operations.attach-observation-receipt"
    )
    assert reactions["resume_after_verified_rollback"][
        "target_requires_exact_known_good_observed_match"
    ] is True


def test_mutable_state_machines_are_reachable_and_consume_all_aggregate_events() -> None:
    ownership = _load("aggregate-ownership.yaml")
    states = _load("state-machines.yaml")
    machines = states["machines"]
    assert set(machines) == {
        "ai_application",
        "environment",
        "system_component",
        "system_assignment",
        "system_approval_grant",
    }
    for name, machine in machines.items():
        assert machine["initial"] in machine["states"], name
        assert set(machine["terminal"]) <= set(machine["states"]), name
        assert _reachable_states(machine) == set(machine["states"]), name
        consumed_events = {machine["created_by_event"]} | {
            transition["on"] for transition in machine["transitions"]
        }
        if name == "system_approval_grant":
            mapping = ownership["schema_major_2_lifecycle_authority"]["SYSTEM_APPROVAL_GRANT"]
            mapped_events = {
                event for event_list in mapping["command_events"].values() for event in event_list
            }
            assert mapped_events == consumed_events
        else:
            assert ownership["resources"][name]["kind"] == "aggregate"
            assert set(ownership["resources"][name]["events"]) == consumed_events
    assert set(states["resources_without_business_state_machine"]).isdisjoint(machines)
    assert states["cross_domain_rules"]["external_operation_machine_is_imported_not_copied"] is True
    assert states["projection_vocabulary"]["evaluation_run_state"]["owns_state_machine"] is False


def test_draft_intents_disable_every_transport_and_target_owned_commands() -> None:
    registry = _load("intent-registry.yaml")
    ownership = _load("aggregate-ownership.yaml")
    v4 = yaml.safe_load(V4_OWNERSHIP.read_text(encoding="utf-8"))
    r2_intents = set(
        registry["r2_application_catalog_contract"]["activated_contract_intents"]
    )
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

    owned_commands: set[str] = set()
    for prefix, resources in (("", ownership["resources"]), ("v4.", v4["resources"])):
        for resource_name, resource in resources.items():
            for command in resource.get("commands", []):
                owned_commands.add(f"{prefix}{resource_name}:{command}")
    for mapping in ownership["schema_major_2_lifecycle_authority"].values():
        for command in mapping["command_events"]:
            owned_commands.add(f'{mapping["profile_resource"]}:{command}')
    for resource_name, extension in ownership["v4_additive_command_profile"].items():
        for command in extension["commands"]:
            owned_commands.add(f"v4.{resource_name}:{command}")

    names: list[str] = []
    operation_ids: list[str] = []
    for intent in registry["intents"]:
        names.append(intent["name"])
        operation_ids.append(intent["http"]["operation_id"])
        assert intent["contract_major"] == 2
        if intent["name"] in r2_intents:
            assert intent["wire_status"] == (
                "FROZEN_R2_R3_BOOTSTRAP"
                if intent["name"] == "system-manifests.import"
                else "FROZEN_R2"
            )
            assert intent["implementation_status"] == (
                "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
            )
            assert intent["field_contract_ref"].startswith(
                "contracts/v5/schema-profiles.yaml#r2_wire_profiles/"
            )
        else:
            assert intent["wire_status"] in (
                "DRAFT",
                "FROZEN_FOR_IMPLEMENTATION",
                "FROZEN_R3",
                "FROZEN_R4",
            )
            if intent["wire_status"] in ("FROZEN_R3", "FROZEN_R4"):
                assert intent["implementation_status"] == (
                    "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
                )
            else:
                assert intent["implementation_status"] == "NOT_IMPLEMENTED"
            if intent["wire_status"] == "DRAFT":
                assert intent["field_contract_ref"] is None
            elif intent["wire_status"] == "FROZEN_R4":
                # R4 first-system-case intents freeze the wire contract on the
                # generated schema namespace without a schema-profiles field
                # contract ref (V5-0C/V5-1C slices predate the r2/d2 profile
                # convention).
                assert intent["field_contract_ref"] is None
            else:
                assert intent["field_contract_ref"].startswith(
                    "contracts/v5/schema-profiles.yaml#d2_wire_profiles/"
                )
        assert intent["http"]["path"].startswith("/api/v2/")
        assert intent["cli_requires_explicit_api_major"] is True
        if intent["kind"] == "mutation":
            assert intent["idempotency"] == "required"
            targets = intent.get("workflow_targets") or [intent["command_target"]]
            for target in targets:
                assert f'{target["resource"]}:{target["command"]}' in owned_commands, intent["name"]
        else:
            assert intent["kind"] == "query"
            assert intent["idempotency"] == "none"
            assert "command_target" not in intent
    assert len(names) == len(set(names))
    assert len(operation_ids) == len(set(operation_ids))


def test_v2_cli_and_http_require_explicit_matching_major() -> None:
    selection = _load("intent-registry.yaml")["api_major_selection"]
    assert selection["default_api_major"] == 1
    assert selection["v2_requires_explicit_selection"] is True
    assert selection["cli"]["flag"] == "--api-version 2"
    assert "profile_field" not in selection["cli"]
    assert selection["cli"]["silent_default_change"] == "FORBIDDEN"
    assert selection["http"]["url_major_must_match_request_header_major"] is True
    assert selection["http"]["request_header"] == "X-CaseLoop-Contract-Version"
    assert selection["http"]["response_header"] == "X-CaseLoop-Contract-Version"
    assert selection["http"]["v2_required_request_value"] == "2.0"
    assert selection["http"]["v2_missing_or_other_value_error"] == "REQUEST_INVALID"
    assert selection["http"]["v2_never_falls_back_to_v1"] is True
    assert selection["http"]["v1_existing_1_0_header_contract"] == "UNCHANGED"
    assert selection["http"]["mismatched_or_downgraded_major"] == "REJECT_AND_AUDIT"


def test_public_principals_cannot_self_attest_approve_or_execute_internal_intents() -> None:
    registry = _load("intent-registry.yaml")
    intents = {intent["name"]: intent for intent in registry["intents"]}
    assert registry["principal_types"]["caller_payload_may_assert_trust_role"] is False
    assert "external_agent" not in intents["applications.register"]["allowed_principal_types"]
    assert "external_agent" not in intents["system-manifests.import"]["allowed_principal_types"]
    assert intents["approvals.decide"]["allowed_principal_types"] == ["human"]
    assert intents["approvals.decide"]["reauthentication"] == "required"
    assert intents["candidates.submit"]["records_untrusted_proposal_not_candidate_revision"] is True
    assert intents["releases.request"]["request_only_no_execute_authority"] is True
    public = set(registry["principal_types"]["public"])
    for policy in registry["internal_intent_authorization"].values():
        assert public.isdisjoint(policy["allowed_principal_types"])
        assert set(policy["allowed_principal_types"]) <= set(registry["principal_types"]["internal"])


def test_operation_visibility_and_transport_state_do_not_leak_or_equivocate() -> None:
    registry = _load("intent-registry.yaml")
    intents = {intent["name"]: intent for intent in registry["intents"]}
    assert intents["operations.get"]["authorization_condition"] == (
        "request_owner_or_application_authorized_reader"
    )
    assert intents["operations.get"]["unauthorized_result"] == "OPAQUE_NOT_FOUND"
    assert intents["operations.list"]["visibility_filter_before_count_and_pagination"] is True
    operation = registry["operation_projection_contract"]
    assert operation["creates_business_state_machine"] is False
    assert operation["semantics"]["domain_non_pass_can_be_completed"] is True
    assert operation["semantics"]["failed"] == "no_trustworthy_domain_artifact_was_produced"


def test_a2a_target_uses_official_methods_and_exact_operation_mapping() -> None:
    registry = _load("intent-registry.yaml")
    a2a = registry["agent_protocol_target"]["a2a"]
    assert a2a["protocolVersion"] == "1.0"
    assert a2a["activation_flag_refs"] == ["a2a_agent_card", "a2a_transport", "a2a_methods"]
    assert a2a["activation_requires_all_referenced_flags_true"] is True
    assert a2a["methods"] == ["SendMessage", "GetTask", "ListTasks", "CancelTask"]
    assert a2a["method_mapping"]["GetTask"] == "operations.get"
    assert a2a["method_mapping"]["ListTasks"] == "operations.list"
    assert a2a["method_mapping"]["CancelTask"] == "operations.cancel-request"
    assert a2a["visibility_inherits_canonical_operation_authorization"] is True
    assert a2a["operation_state_mapping"]["CANCEL_REQUESTED"] == "TASK_STATE_WORKING"
    assert a2a["operation_state_mapping"]["COMPLETED"] == "TASK_STATE_COMPLETED"
    assert a2a["task_completed_can_contain_domain_non_pass_artifact"] is True
    assert a2a["method_mapping"]["SendMessage"]["intent_discriminator_field"] == "caseloop_intent"
    assert a2a["task_id_issued_by_server"] is True
    assert a2a["auth_required_grants_approval_capability_or_scope"] is False
    serialized = (V5 / "intent-registry.yaml").read_text(encoding="utf-8")
    assert "tasks.create" not in serialized


def test_mcp_tasks_are_optional_and_tools_are_allowlisted_non_authoritative() -> None:
    mcp = _load("intent-registry.yaml")["agent_protocol_target"]["mcp"]
    assert mcp["activation_flag_ref"] == "mcp_tools"
    assert mcp["tasks_extension"] == "OPTIONAL_NEGOTIATED_NOT_BASELINE"
    assert mcp["baseline_async_result"] == "operation_id_then_operations_get"
    assert mcp["tools_filtered_by_authenticated_scope"] is True
    assert mcp["tool_annotations_are_authority"] is False
    assert mcp["approval_and_execute_tools_forbidden"] is True
    assert mcp["structured_content_required"] is True
    assert mcp["mutation_idempotency_required"] is True
    assert mcp["disconnect_or_transport_cancel_effect"] == "DETACH_WAIT_ONLY"
    assert mcp["durable_operation_cancel_requires_tool"] == "caseloop_operation_cancel"
    allowed = {tool["intent"] for tool in mcp["tool_allowlist"].values()}
    assert "approvals.decide" not in allowed
    assert "releases.request" not in allowed


def test_v1_history_is_additively_linked_and_quality_case_is_not_frozen() -> None:
    compatibility = _load("compatibility.yaml")
    current = compatibility["current_implemented_public_slice"]
    assert current["api_prefix"] == "/api/v1"
    assert compatibility["v5_target_surface"]["api_prefix"] == "/api/v2"
    assert "quality_case" in current["history_preserved_aggregates"]
    assert "quality_case" not in current["immutable_records"]
    assert current["quality_case_semantics"]["prior_payload_event_and_digest_may_be_rewritten"] is False
    assert compatibility["history_rules"] == {
        "rewrite_existing_payload": False,
        "recompute_existing_digest": False,
        "reinterpret_governed_agent_id_as_application_id": False,
        "rewrite_existing_authority_receipt": False,
        "upgrade_historical_evidence_facet": False,
    }
    assert compatibility["route_authority"]["one_owner_per_routing_key"] is True
    assert compatibility["route_authority"]["same_fact_dual_write_forbidden"] is True
    assert compatibility["implementation_gate"]["implementation_authorized"] is False


def test_first_wire_slice_is_reachable_from_empty_v5_domain_with_seeded_trust_roots() -> None:
    registry = _load("intent-registry.yaml")
    intents = {intent["name"]: intent for intent in registry["intents"]}
    fixture = yaml.safe_load(FIRST_CASE_FIXTURE.read_text(encoding="utf-8"))
    import_intent = intents["system-manifests.import"]
    assert import_intent["delivery_slice"] == "V5-1B"
    assert import_intent["transaction_semantics"] == "ALL_OR_NOTHING_LOCAL_POSTGRES"
    assert import_intent["external_side_effects"] == "FORBIDDEN"
    assert [target["command"] for target in import_intent["workflow_targets"]] == (
        fixture["expected_command_order"][:-1]
    )
    assert fixture["preconditions"]["authoritative_v5_domain_tables"] == "EMPTY"
    assert fixture["preconditions"]["full_database_empty"] is False
    assert fixture["preconditions"]["seeded_v4_trust_roots"]
    assert intents["cases.bind-application"]["command_target"]["command"] == (
        fixture["expected_command_order"][-1]
    )
    binding_write = intents["cases.bind-application"]
    assert binding_write["authorization_condition"] == (
        "case_application_and_environment_authorized_binder"
    )
    assert binding_write["same_workspace_required"] is True
    assert binding_write["unauthorized_result"] == "OPAQUE_NOT_FOUND"
    assert "case-application-bindings.get" in intents
    binding_read = intents["case-application-bindings.get"]
    assert binding_read["required_query_fields"] == ["case_revision", "case_digest"]
    assert binding_read["different_target_for_same_exact_case"] == "CONFLICT"
    binding = _load("domain-model.yaml")["resources"]["application_case_binding"]
    assert binding["uniqueness_key"] == ["workspace_id", "case_id", "case_revision", "case_digest"]
    assert "rebinding_requires_a_new_quality_case_revision" in binding["invariants"]
    assert fixture["expected_assertions"]["assignment_proves"] == "DESIRED_ONLY"
    assert fixture["expected_assertions"]["observed_state_created"] is False
    assert fixture["expected_assertions"]["release_success_created"] is False
    manifest = fixture["manifest_import"]
    policy_components = [
        component for component in manifest["components"] if component["component_kind"] == "POLICY"
    ]
    assert len(policy_components) == 1
    assert manifest["trusted_approver_policy_revision_id"] == policy_components[0]["revision_id"]
    assert manifest["trusted_approver_policy_revision_id"] not in manifest[
        "system_version_set_component_revision_ids"
    ]
    assert fixture["expected_assertions"][
        "trusted_approver_policy_is_imported_outside_runtime_version_set"
    ] is True
    assert {case["expected"] for case in fixture["negative_cases"]} >= {
        "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "DENY_AND_AUDIT",
    }


def test_record_digest_and_cross_major_binding_spaces_are_unambiguous() -> None:
    domain = _load("domain-model.yaml")
    common = _load("schema-profiles.yaml")["common"]
    assert domain["record_envelope"]["constants"]["hash_rule"] == (
        "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"
    )
    assert domain["record_envelope"]["digest_scope"] == (
        "full_canonical_record_including_record_envelope_and_payload"
    )
    assert common["canonical_record_digest"][
        "exact_binding_digest_must_equal_target_record_digest"
    ] is True
    v5_kinds = set(common["exact_record_binding_v5"]["kinds"])
    assert {"QUALITY_CASE", "GATE_TRACK_RECEIPT", "CONTROLLER_REGISTRATION", "AUTHORITY_RECEIPT"}.isdisjoint(
        v5_kinds
    )
    bridge = common["exact_record_binding_v4_bridge"]
    assert bridge["constants"]["contract_major"] == 1
    assert bridge["revision"] == "integer_min_1_or_null_for_v4_singleton_immutable_records"
    assert {"QUALITY_CASE", "CANDIDATE_CONTRACT", "PROPOSAL", "ATTEMPT", "CONTROLLER_REGISTRATION"} <= set(
        bridge["kinds"]
    )
    receipt = common["authority_receipt_v5"]
    assert receipt["field_refs"]["controller_registration"].endswith("CONTROLLER_REGISTRATION")
    assert receipt["field_refs"]["subject"] == "exact_record_binding_v5"


def test_candidate_and_gate_bind_typed_v4_or_v5_records_without_closed_schema_reinterpretation() -> None:
    profiles = _load("schema-profiles.yaml")
    candidate = profiles["profiles"]["system_candidate_revision"]
    assert candidate["field_refs"]["exact_case_binding"].endswith("QUALITY_CASE")
    assert candidate["field_refs"]["exact_candidate_contract_binding"].endswith("CANDIDATE_CONTRACT")
    assert candidate["field_refs"]["producer_attempt_binding"].endswith("ATTEMPT")
    track = profiles["profiles"]["system_gate_track_receipt"]
    assert track["field_refs"]["exact_gate_execution_binding"].endswith("SYSTEM_GATE_EXECUTION")
    assert track["field_refs"]["exact_evidence_bindings"] == "list[exact_evidence_binding_union]"
    evidence_union = profiles["common"]["exact_evidence_binding_union"]
    assert evidence_union["mutable_projection_binding"] == "FORBIDDEN"
    evaluator_variants = track["evaluator_attestation_union"]["variants"]
    assert {"RULE_ENGINE", "DETERMINISTIC_RUNNER", "SANDBOX_RUNNER", "AGENT_JUDGE"} == set(
        evaluator_variants
    )
    for kind in ("RULE_ENGINE", "DETERMINISTIC_RUNNER", "SANDBOX_RUNNER"):
        assert "exact_attempt_binding" in evaluator_variants[kind]["requires"]
        assert "exact_execution_receipt_binding" not in evaluator_variants[kind]["requires"]


def test_identity_assurance_is_discriminated_and_rechecked_at_every_release_boundary() -> None:
    domain = _load("domain-model.yaml")
    identity = domain["identity_assurance"]
    variants = identity["discriminator_union"]["variants"]
    assert "content_digest" in variants["IMMUTABLE_DIGEST"]["requires"]
    assert "immutable_provider_version_attestation" in variants["PROVIDER_VERSION"]["requires"]
    assert set(identity["revalidation_points"]) == {
        "system_gate_before_pass",
        "system_workorder_before_creation",
        "system_external_operation_before_request",
        "version_controller_before_assignment_update",
    }
    profiles = _load("schema-profiles.yaml")["profiles"]
    assert "raw_component_identity_assurance_is_revalidated_before_pass" in profiles[
        "system_gate_report"
    ]["invariants"]
    assert "raw_component_identity_assurance_is_revalidated_before_creation" in profiles[
        "system_workorder"
    ]["invariants"]
    requested = _load("events.yaml")["system_external_operation"]["events"][
        "external_operation.requested"
    ]
    assert "every_required_component_identity_assurance_is_revalidated" in requested["guards"]


def test_gate_execution_is_the_only_gate_lifecycle_root_and_subordinates_are_atomic() -> None:
    ownership = _load("aggregate-ownership.yaml")
    lifecycle = ownership["schema_major_2_lifecycle_authority"]
    subordinate = ownership["schema_major_2_subordinate_record_authority"]
    assert "SYSTEM_GATE_EXECUTION" in lifecycle
    assert {"SYSTEM_GATE_REPORT", "SYSTEM_GATE_TRACK_RECEIPT"} == set(subordinate)
    assert all(item["same_transaction_root"] == "SYSTEM_GATE_EXECUTION" for item in subordinate.values())
    transactions = ownership["gate_controller_same_owner_transactions"]
    assert transactions["rules"]["gate_execution_is_only_business_lifecycle_root"] is True
    assert transactions["track_receipt_and_execution_attach"]["atomicity"] == (
        "ALL_OR_NOTHING_LOCAL_POSTGRES"
    )
    assert transactions["terminal_execution_and_report"]["atomicity"] == (
        "ALL_OR_NOTHING_LOCAL_POSTGRES"
    )
    assert transactions["terminal_execution_and_report"]["event_condition"] == (
        "terminal_event_excluding_gate_unknown"
    )
    unknown = transactions["unknown_execution_without_report"]
    assert unknown["event_condition"] == "gate.unknown"
    assert "system_gate_report" in unknown["forbidden_records"]


def test_major_2_gate_and_external_operation_events_cover_imported_state_transitions() -> None:
    v4_states = yaml.safe_load(
        (ROOT / "v4" / "events" / "state-machines.yaml").read_text(encoding="utf-8")
    )["machines"]
    events = _load("events.yaml")
    gate_events = set(events["system_gate_execution"]["events"])
    operation_events = set(events["system_external_operation"]["events"])
    assert {transition["on"] for transition in v4_states["gate_execution"]["transitions"]} <= gate_events
    assert {transition["on"] for transition in v4_states["external_operation"]["transitions"]} <= operation_events
    ownership = _load("aggregate-ownership.yaml")["schema_major_2_lifecycle_authority"]
    mapped_gate = {
        event
        for event_list in ownership["SYSTEM_GATE_EXECUTION"]["command_events"].values()
        for event in event_list
    }
    mapped_operation = {
        event
        for event_list in ownership["SYSTEM_EXTERNAL_OPERATION"]["command_events"].values()
        for event in event_list
    }
    assert mapped_gate == {transition["on"] for transition in v4_states["gate_execution"]["transitions"]}
    assert mapped_operation >= {transition["on"] for transition in v4_states["external_operation"]["transitions"]}


def test_approval_decision_and_rollback_authority_cannot_rebind_or_reuse_old_grants() -> None:
    profiles = _load("schema-profiles.yaml")["profiles"]
    approval = profiles["system_approval_grant"]
    assert set(approval["authority_kind_union"]["variants"]) == {
        "HUMAN_APPROVAL",
        "HUMAN_EMERGENCY_APPROVAL",
    }
    for status in ("APPROVED", "DENIED"):
        assert "exact_previous_requested_grant_binding" in approval["lifecycle_status_union"][
            "variants"
        ][status]["requires"]
    assert "decision_revision_carries_forward_authority_workorder_plan_target_nonce_and_expiry_unchanged" in approval[
        "invariants"
    ]
    assert {"exact_approver_policy_binding", "approval_assignee_principal_or_group"} <= set(
        approval["required_fields"]
    )
    assert approval["second_grant_for_same_workorder_and_nonce"] == "CONFLICT_AND_AUDIT"
    decided = _load("events.yaml")["system_approval_grant"]["events"]["approval.decided"]
    assert "exact_decided_approval_grant_binding" in decided["payload_required"]
    rollback = _load("domain-model.yaml")["resources"]["rollback_authority"]
    assert rollback["presealed_known_good_from_prior_release_plan_is_evidence_not_authority"] is True
    assert "reuse_original_release_approval_or_nonce" in rollback["forbidden_paths"]
    rollback_intent = {
        item["name"]: item for item in _load("intent-registry.yaml")["intents"]
    }["releases.rollback-request"]
    assert [target["command"] for target in rollback_intent["workflow_targets"]] == [
        "release-plans.freeze",
        "workorders.create-break-glass-recovery",
        "approvals.request",
    ]


def test_capability_consumption_and_operation_start_are_one_recoverable_transaction() -> None:
    profiles = _load("schema-profiles.yaml")["profiles"]
    capability = profiles["system_capability_lease"]
    assert "exact_workorder_or_recovery_workorder_binding" in capability["required_fields"]
    consumed = capability["lifecycle_status_union"]["variants"]["CONSUMED"]
    assert {"exact_previous_active_capability_binding", "consumed_by_operation_id"} <= set(
        consumed["requires"]
    )
    operation = profiles["system_external_operation"]
    assert {"exact_issued_capability_lease_binding", "exact_consumed_capability_lease_binding"} <= set(
        operation["required_fields"]
    )
    transaction = _load("events.yaml")["same_transaction_sets"][
        "capability_consumption_and_operation_creation"
    ]
    assert transaction["atomicity"] == "ALL_OR_NOTHING_LOCAL_POSTGRES"
    assert transaction["immutable_operation_revision_chain"] == (
        "PENDING_revision_then_RUNNING_revision"
    )
    assert {"pending_operation_authority_receipt", "running_operation_authority_receipt"} <= set(
        transaction["required"]
    )
    state = _load("state-machines.yaml")["schema_major_2_external_operation_profile"]
    assert state["request_and_start_share_capability_consumption_transaction"] is True
    assert state["committed_current_state_may_remain_pending_after_transaction"] is False
    assert capability["second_lease_for_same_approved_grant_and_nonce"] == (
        "RETURN_SAME_LEASE_OR_CONFLICT"
    )
    reaction = _load("aggregate-ownership.yaml")["authorized_cross_owner_reactions"][
        "approved_grant_issues_capability"
    ]
    assert reaction["source_exact_binding_field"] == "exact_decided_approval_grant_binding"
    assert reaction["current_row_rebinding_forbidden"] is True


def test_post_release_gate_and_reconcile_use_the_same_full_success_guard() -> None:
    events = _load("events.yaml")
    operation_events = events["system_external_operation"]["events"]
    succeeded = operation_events["external_operation.succeeded"]
    reconciled = operation_events["external_operation.reconciled"]
    assert "exact_post_release_gate_report_binding" in succeeded["payload_required"]
    assert "exact_post_release_gate_is_complete_and_pass" in succeeded["guards"]
    assert reconciled["reconciled_status_union"]["SUCCEEDED"]["guards_identical_to"] == (
        "external_operation.succeeded"
    )
    reactions = _load("aggregate-ownership.yaml")["authorized_cross_owner_reactions"]
    assert reactions["post_release_gate_request"]["target_command"] == "evaluations.freeze-plan"
    assert reactions["post_release_gate_receipt_return"]["target_command"] == (
        "system-external-operations.attach-post-release-gate-receipt"
    )


def test_observed_and_episode_evidence_exactly_bind_assignment_revision_and_origin_operation() -> None:
    resources = _load("domain-model.yaml")["resources"]
    observed = resources["observed_state_snapshot"]
    assert "exact_compared_assignment_binding" in observed["required_fields"]
    assert {"observation_origin_kind", "exact_observation_origin_binding"} <= set(
        observed["required_fields"]
    )
    assert "compared_assignment_id" not in observed["required_fields"]
    episode = resources["system_episode_snapshot"]
    assert "exact_system_assignment_binding" in episode["required_fields"]
    resume = _load("aggregate-ownership.yaml")["authorized_cross_owner_reactions"][
        "resume_after_verified_rollback"
    ]
    assert resume["source_terminal_condition"] == "terminal_SUCCEEDED_with_full_success_guard"
    assert resume["target_requires_exact_current_assignment_binding"] is True
    assignment = resources["system_assignment"]
    assert assignment["aggregate_identity_key"] == ["workspace_id", "application_id", "environment_id"]
    assert assignment["database_uniqueness_rule"] == (
        "one_non_retired_assignment_aggregate_per_identity_key"
    )


def test_release_chain_mapped_events_have_explicit_major_2_payload_contracts() -> None:
    events = _load("events.yaml")
    envelope = events["event_envelope_v5"]
    assert "exact_subject_binding" in envelope["required_fields"]
    assert envelope["subject_binding_is_the_post_event_immutable_revision"] is True
    assert envelope["outbox_carries_envelope_without_rebinding_current_state"] is True
    explicit = {
        event_name
        for section in events.values()
        if isinstance(section, dict) and isinstance(section.get("events"), dict)
        for event_name, contract in section["events"].items()
        if contract.get("event_version") == "2.0"
    }
    expected = {
        "candidate_revision.recorded",
        "release_plan.frozen",
        "evaluation.plan_frozen",
        "workorder.created",
        "workorder.recovery_created",
        "approval.requested",
        "approval.decided",
        "capability.issued",
        "capability.consumed",
        "gate.started",
        "gate.track_receipt_recorded",
        "external_operation.requested",
        "external_operation.started",
        "external_operation.succeeded",
        "external_operation.failed",
        "external_operation.unknown",
        "external_operation.reconciled",
    }
    assert expected <= explicit
    ownership = _load("aggregate-ownership.yaml")
    mapped = {
        event
        for section_name in (
            "record_authority",
            "schema_major_2_lifecycle_authority",
            "schema_major_2_subordinate_record_authority",
        )
        for mapping in ownership[section_name].values()
        for event_list in mapping["command_events"].values()
        for event in event_list
    }
    assert mapped <= explicit


def test_internal_release_commands_have_canonical_owner_and_adapter_is_not_a_domain_owner() -> None:
    registry = _load("intent-registry.yaml")
    ownership = _load("aggregate-ownership.yaml")
    owner_by_command: dict[str, set[str]] = defaultdict(set)
    for section_name in (
        "record_authority",
        "schema_major_2_lifecycle_authority",
        "schema_major_2_subordinate_record_authority",
    ):
        for mapping in ownership[section_name].values():
            for command in mapping["command_events"]:
                owner_by_command[command].add(mapping["owner"])
    release_commands = {
        command
        for command in registry["internal_intent_authorization"]
        if command.startswith(("gates.", "gate-track-", "capabilities.", "external-operations.", "system-external-operations."))
    }
    for command in release_commands:
        policy = registry["internal_intent_authorization"][command]
        assert owner_by_command[command] == {policy["owner"]}, command
    adapter = registry["sealed_adapter_dispatch"]
    assert adapter["is_canonical_domain_command"] is False
    assert adapter["adapter_may_write_control_plane"] is False
    assert adapter["adapter_result_never_sets_domain_terminal_state_directly"] is True


def test_competition_profile_requires_cli_but_not_generic_protocol_runtime() -> None:
    competition = _load("intent-registry.yaml")["competition_profile"]
    assert competition == {
        "real_external_agent_via_cli_is_sufficient_for_agent_native_transport_proof": True,
        "explicit_cli_api_major_required": 2,
        "public_mcp_runtime_required": False,
        "public_a2a_runtime_required": False,
        "generic_operations_list_required": False,
        "generic_cancel_required": False,
        "competition_slice_is_delivery_sequence_not_product_scope_reduction": True,
    }


def test_v5_2a_work_authority_section_reuses_v4_owners_and_frozen_events() -> None:
    """The V5-2A work authority section (D-016) is structurally closed:
    kinds are registered, owners match the V4 logical owners, every mapped
    command/event exists in the V4 ownership contract, and the command set
    matches the frozen v5_2a_work_kernel_contract catalog exactly."""
    ownership = _load("aggregate-ownership.yaml")
    profiles = _load("schema-profiles.yaml")
    events_v5 = _load("events.yaml")
    v4 = yaml.safe_load(V4_OWNERSHIP.read_text(encoding="utf-8"))

    section = ownership["schema_major_2_work_authority"]
    exact_kinds = set(profiles["common"]["exact_record_binding_v5"]["kinds"])
    assert set(section) <= exact_kinds

    frozen_catalog = events_v5["v5_2a_work_kernel_contract"]["frozen_event_catalog"]
    frozen_events = {
        event for events in frozen_catalog.values() for event in events
    }

    v4_resources = v4["resources"]
    seen_events: set[str] = set()
    for kind, mapping in section.items():
        logical = mapping["resource"]
        v4_resource = v4_resources[logical]
        assert mapping["owner"] == v4_resource["owner"]
        owner_commands = {
            command
            for resource in v4_resources.values()
            if resource["owner"] == mapping["owner"]
            for command in resource.get("commands", [])
        }
        for command, mapped_events in mapping["command_events"].items():
            # The command must be registered on a V4 resource of the same
            # owner; a same-owner command may emit across aggregates inside
            # one unit of work (e.g. work.claim also emits attempt.created).
            assert command in owner_commands
            assert mapped_events
            for event in mapped_events:
                assert event in v4_resource["events"]
                seen_events.add(event)
    assert seen_events == frozen_events
    assert len(frozen_events) == 27


def test_v5_2a_work_events_use_dedicated_channel_and_major_2_envelope() -> None:
    """Every frozen Work event resolves to a major-2 V5 route on the
    dedicated work channel, with the V4 owner reused."""
    events_v5 = _load("events.yaml")
    ownership = _load("aggregate-ownership.yaml")
    contract = events_v5["v5_2a_work_kernel_contract"]
    assert contract["contract_status"] == "FROZEN_FOR_IMPLEMENTATION"
    assert contract["activation_status"] == "NOT_ACTIVATED"
    assert contract["schema_major_routing"]["mode"] == (
        "semantic_reuse_with_schema_major_routing"
    )
    assert set(contract["transports"].values()) == {"FORBIDDEN"}
    reused = contract["reused_v4_semantic_owners"]
    v4 = yaml.safe_load(V4_OWNERSHIP.read_text(encoding="utf-8"))
    for logical, owner in reused.items():
        assert v4["resources"][logical]["owner"] == owner
    work_section = ownership["schema_major_2_work_authority"]
    assert {mapping["resource"] for mapping in work_section.values()} == set(
        contract["frozen_event_catalog"]
    )
