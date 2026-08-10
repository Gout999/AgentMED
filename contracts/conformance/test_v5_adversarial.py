"""V5-0B acceptance supplements: adversarial fixtures and machine-checked semantics.

独立验收 Agent 补充（验收方补充，2026-08-11）：

- acceptance-criteria propose/confirm（confirm 仅 human maintainer/domain reviewer）
- NEEDS_ACCEPTANCE_CRITERIA readiness 投影
- workload/deployment profile 枚举、Judge 校准绑定、workload-baseline 阈值规则
- VerifiedCandidate 只读投影
- verification-only PASS 禁产 WorkOrder 的对抗 fixture（含机检喂入）
- v3/v4/V5 no-duplicate-lifecycle（machine 名不相交）
- first-system-case 对抗 fixture 逐条被合同规则背书（把 fixture 喂给校验逻辑）
- A2A task state 是 transport state，不冒充领域成功

所有断言针对 contracts/v5/*.yaml 的真实字段，不引入 runtime 语义。
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "v5"
FIRST_CASE_FIXTURE = V5 / "fixtures" / "first-system-case.yaml"
VERIFICATION_ONLY_FIXTURE = V5 / "fixtures" / "verification-only-pass-to-workorder.yaml"


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


def _fixture(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)


def test_adversarial_fixtures_have_no_duplicate_yaml_keys() -> None:
    for path in (FIRST_CASE_FIXTURE, VERIFICATION_ONLY_FIXTURE):
        _fixture(path)


def test_acceptance_criteria_confirm_restricted_to_human_maintainer_or_domain_reviewer() -> None:
    domain = _load("domain-model.yaml")
    ownership = _load("aggregate-ownership.yaml")
    events = _load("events.yaml")

    provenance = domain["acceptance_provenance"]["acceptance_criteria_revision"]
    assert provenance["agent_can_propose"] is True
    assert provenance["agent_can_confirm"] is False
    assert provenance["service_can_confirm"] is False
    assert provenance["connector_can_confirm"] is False
    assert provenance["confirmer_must_be_human_maintainer_or_domain_reviewer"] is True
    assert provenance["proposed_revision_is_untrusted"] is True
    assert provenance["confirmed_revision_is_authoritative"] is True
    assert "proposal_does_not_confirm_itself" in provenance["invariants"]
    assert "only_human_maintainer_or_domain_reviewer_can_confirm" in provenance["invariants"]
    assert "agent_or_service_or_connector_principal_cannot_confirm" in provenance["invariants"]

    resource = domain["resources"]["acceptance_criteria_revision"]
    assert resource["owner"] == "case-controller"
    assert resource["subordinate_to"] == "resolution_contract"
    assert resource["does_not_create_new_case_lifecycle"] is True
    status = resource["confirmation_status_union"]["variants"]
    assert status["PROPOSED"]["is_untrusted"] is True
    assert status["PROPOSED"]["is_authoritative"] is False
    assert set(status["PROPOSED"]["forbids"]) >= {"confirmer_principal", "confirmed_at"}
    confirmed = status["CONFIRMED"]
    assert confirmed["requires_confirmer_is_human_maintainer_or_domain_reviewer"] is True
    assert "proposer_principal_equals_confirmer" in confirmed["forbids"]
    assert confirmed["is_authoritative"] is True

    owned = ownership["resources"]["acceptance_criteria_revision"]
    assert owned["owner"] == "case-controller"
    assert set(owned["commands"]) == {"acceptance-criteria.propose", "acceptance-criteria.confirm"}
    assert set(owned["events"]) == {"acceptance_criteria.proposed", "acceptance_criteria.confirmed"}
    assert owned["subordinate_to"] == "resolution_contract"
    assert ownership["record_authority"]["ACCEPTANCE_CRITERIA_REVISION"]["owner"] == "case-controller"

    proposed = events["acceptance_criteria_revision"]["events"]["acceptance_criteria.proposed"]
    confirmed_ev = events["acceptance_criteria_revision"]["events"]["acceptance_criteria.confirmed"]
    assert proposed["constants"]["confirmation_status"] == "PROPOSED"
    assert "proposer_is_agent_service_or_connector_not_human_maintainer_or_domain_reviewer" in proposed["guards"]
    assert confirmed_ev["constants"]["confirmation_status"] == "CONFIRMED"
    assert "confirmer_is_human_maintainer_or_domain_reviewer" in confirmed_ev["guards"]
    assert "confirmer_is_not_the_proposer" in confirmed_ev["guards"]


def test_case_readiness_is_projection_and_blocks_gate_until_confirmed_acceptance() -> None:
    domain = _load("domain-model.yaml")
    ownership = _load("aggregate-ownership.yaml")

    readiness = domain["resources"]["case_readiness"]
    assert readiness["kind"] == "projection"
    assert readiness["commands"] == []
    assert readiness["uses_record_envelope"] is False
    assert set(readiness["readiness_values"]) == {"NEEDS_ACCEPTANCE_CRITERIA", "READY"}
    assert "needs_acceptance_criteria_is_projection_not_case_lifecycle" in readiness["invariants"]
    assert "needs_acceptance_criteria_does_not_rewrite_quality_case_payload_or_digest" in readiness["invariants"]
    assert "needs_acceptance_criteria_blocks_gate_pass_but_allows_case_investigation" in readiness["invariants"]

    provenance = domain["acceptance_provenance"]
    assert provenance["case_readiness_is_projection_not_case_lifecycle"] is True
    assert provenance["case_readiness_values"] == ["NEEDS_ACCEPTANCE_CRITERIA", "READY"]
    assert provenance["needs_acceptance_criteria_blocks_gate_pass"] is True
    assert provenance["needs_acceptance_criteria_allows_case_investigation"] is True
    assert provenance["needs_acceptance_criteria_does_not_rewrite_quality_case_payload_or_digest"] is True
    assert provenance["does_not_create_new_case_lifecycle"] is True

    owned = ownership["resources"]["case_readiness"]
    assert owned["kind"] == "projection"
    assert owned["commands"] == []


def test_badcase_and_regression_asset_chain_to_confirmed_acceptance() -> None:
    domain = _load("domain-model.yaml")
    events = _load("events.yaml")

    badcase = domain["resources"]["badcase_spec"]
    assert badcase["kind"] == "immutable_record"
    assert badcase["owner"] == "case-controller"
    assert badcase["subordinate_to"] == "resolution_contract"
    assert badcase["does_not_create_new_case_lifecycle"] is True
    assert "exact_confirmed_acceptance_criteria_binding" in badcase["required_fields"]
    assert "badcase_requires_confirmed_acceptance_criteria_revision" in badcase["invariants"]
    assert "badcase_does_not_create_new_case_lifecycle" in badcase["invariants"]

    asset = domain["resources"]["regression_asset"]
    assert asset["kind"] == "immutable_record"
    assert "exact_confirmed_acceptance_criteria_binding" in asset["required_fields"]
    assert "exact_badcase_spec_binding" in asset["required_fields"]
    assert "regression_asset_is_closure_artifact_from_confirmed_badcase" in asset["invariants"]

    badcase_ev = events["badcase_spec"]["events"]["badcase_spec.recorded"]
    assert "badcase_requires_confirmed_acceptance_criteria_revision" in badcase_ev["guards"]
    assert badcase_ev["event_version"] == "2.0"
    asset_ev = events["regression_asset"]["events"]["regression_asset.recorded"]
    assert "regression_asset_is_closure_artifact_from_confirmed_badcase" in asset_ev["guards"]
    assert asset_ev["event_version"] == "2.0"


def test_workload_and_deployment_profiles_are_frozen_enums() -> None:
    domain = _load("domain-model.yaml")

    workloads = domain["workload_profiles"]
    assert workloads["values"] == ["CODE_CHANGE", "AI_BEHAVIOR_CHANGE"]
    code_change = workloads["code_change"]
    assert code_change["deterministic_fail_to_pass"] is True
    assert code_change["repo_tests_and_regression"] is True
    assert code_change["sealed_holdout_and_anti_overfit"] is True
    assert code_change["judge_applicability_when_deterministic_sufficient"] == "N_A"
    assert code_change["judge_n_a_is_not_error"] is True
    ai_behavior = workloads["ai_behavior_change"]
    assert ai_behavior["paired_base_candidate_repetitions"] is True
    assert ai_behavior["failure_distribution"] is True
    assert ai_behavior["effect_and_interval"] is True
    assert ai_behavior["unaffected_controls"] is True
    assert ai_behavior["repetitions_and_thresholds_from_workload_baseline"] is True
    assert ai_behavior["no_global_uniform_repetitions_or_thresholds"] is True
    assert ai_behavior["judge_applicability_without_calibration"] == "advisory"
    assert ai_behavior["judge_applicability_with_calibration_binding"] == "required"
    assert "global_uniform_repetitions_or_thresholds_are_forbidden" in workloads["invariants"]
    assert "demo_constants_are_not_frozen_as_universal_quality_guarantee" in workloads["invariants"]

    deployments = domain["deployment_profiles"]
    assert deployments["values"] == ["LIBRARY_OR_OFFLINE", "DEPLOYED_SERVICE"]
    library = deployments["library_or_offline"]
    assert library["endpoint"] == "verified_candidate"
    assert library["not_deployed"] is True
    assert library["release_is_n_a"] is True
    assert library["does_not_enter_release_observed_rollback"] is True
    assert library["does_not_fabricate_shadow_runtime"] is True
    deployed = deployments["deployed_service"]
    assert deployed["enters_release_observed_rollback"] is True
    assert deployed["release_requires_full_authority_chain"] is True
    assert deployed["candidate_verification_pass_is_not_release_authority"] is True
    assert "library_or_offline_does_not_fabricate_shadow_runtime" in deployments["invariants"]
    assert "deployed_service_requires_release_authorization_not_candidate_verification" in deployments["invariants"]


def test_judge_calibration_and_workload_baseline_rules() -> None:
    domain = _load("domain-model.yaml")

    judge = domain["judge_policy"]
    assert set(judge["applicability_values"]) == {"N_A", "advisory", "required"}
    assert judge["required_requires_calibration_binding"] is True
    assert judge["uncalibrated_judge_cannot_be_required"] is True
    assert judge["calibration_binding_binds_evaluator_rubric_and_model_version"] is True
    assert judge["judge_independent_from_candidate_producer"] is True
    assert "required_judge_requires_calibration_binding" in judge["invariants"]
    assert "uncalibrated_judge_is_advisory_only" in judge["invariants"]

    bundle = domain["resources"]["evaluation_bundle"]
    assert "calibration_binding" in bundle["required_fields"]
    assert {"workload_baseline_repetitions", "workload_baseline_thresholds"} <= set(
        bundle["optional_fields"]
    )
    assert "non_deterministic_repetitions_and_thresholds_derive_from_workload_baseline_not_global_constant" in bundle["invariants"]
    assert "required_judge_requires_calibration_binding" in bundle["invariants"]
    assert "uncalibrated_judge_is_advisory_only" in bundle["invariants"]


def test_verified_candidate_is_read_only_projection_and_not_release_authority() -> None:
    domain = _load("domain-model.yaml")
    ownership = _load("aggregate-ownership.yaml")

    verified = domain["resources"]["verified_candidate"]
    assert verified["kind"] == "projection"
    assert verified["commands"] == []
    assert verified["uses_record_envelope"] is False
    assert "verified_candidate_is_read_only_projection_of_candidate_and_verification_pass" in verified["invariants"]
    assert "verified_candidate_does_not_own_lifecycle_or_commands" in verified["invariants"]
    assert "verified_candidate_does_not_claim_deployment" in verified["invariants"]
    assert "reusing_verified_candidate_as_release_authority_is_forbidden" in verified["invariants"]
    assert "release_requires_new_release_authorization_gate_not_prior_verification_pass" in verified["invariants"]

    owned = ownership["resources"]["verified_candidate"]
    assert owned["kind"] == "projection"
    assert owned["commands"] == []
    assert owned["owner"] == "projection-builder"

    purposes = domain["gate_purposes"]
    assert purposes["values"] == ["CANDIDATE_VERIFICATION", "RELEASE_AUTHORIZATION"]
    assert purposes["candidate_verification"]["pass_produces"] == ["verified_candidate", "regression_asset"]
    assert purposes["release_authorization"]["cannot_be_reused_from_prior_verification_pass"] is True


def test_verification_only_pass_cannot_create_workorder_with_adversarial_fixture() -> None:
    domain = _load("domain-model.yaml")
    ownership = _load("aggregate-ownership.yaml")
    fixture = _fixture(VERIFICATION_ONLY_FIXTURE)

    # --- contract rules ---
    purposes = domain["gate_purposes"]
    candidate_verification = purposes["candidate_verification"]
    assert candidate_verification["cannot_create_workorder"] is True
    assert candidate_verification["cannot_claim_deployment"] is True
    assert candidate_verification["does_not_bind_release_plan"] is True
    release_authorization = purposes["release_authorization"]
    assert release_authorization["exact_binds_pre_gate_sealed_release_plan"] is True
    assert release_authorization["pass_is_only_path_to_workorder"] is True
    assert "verification_only_pass_cannot_create_workorder" in purposes["invariants"]
    assert "only_release_authorization_pass_creates_workorder" in purposes["invariants"]
    assert "release_authorization_requires_pre_gate_sealed_release_plan" in purposes["invariants"]
    assert "verified_candidate_cannot_be_renamed_to_release_authorization" in purposes["invariants"]

    workflows = ownership["gate_controller_same_owner_workflows"]
    to_workorder = workflows["gate_terminal_pass_to_workorder"]
    assert to_workorder["command"] == "workorders.create-from-pass"
    assert to_workorder["source_condition"] == "gate_purpose_RELEASE_AUTHORIZATION_and_exact_PASS"
    assert to_workorder["forbidden_when_gate_purpose"] == "CANDIDATE_VERIFICATION"
    to_verified = workflows["candidate_verification_pass_to_verified_candidate"]
    assert to_verified["source_condition"] == "gate_purpose_CANDIDATE_VERIFICATION_and_exact_PASS"
    assert to_verified["forbidden_command"] == "workorders.create-from-pass"
    assert to_verified["produces"] == ["verified_candidate", "regression_asset"]
    assert to_verified["does_not_claim_deployment"] is True
    assert to_verified["deployment_status_for_library_or_offline"] == "NOT_DEPLOYED"

    states = _load("state-machines.yaml")
    assert states["cross_domain_rules"]["gate_non_pass_cannot_create_system_workorder"] is True

    # --- adversarial fixture 内容与期望 ---
    assert fixture["preconditions"]["evaluation_purpose"] == "CANDIDATE_VERIFICATION"
    assert fixture["preconditions"]["release_plan_sealed"] is False
    assert fixture["attempted"]["command"] == "workorders.create-from-pass"
    assert fixture["attempted"]["gate_purpose_at_attempt_time"] == "CANDIDATE_VERIFICATION"
    assert fixture["expected_outcome"]["result"] == "REJECT_AND_AUDIT"
    assert fixture["expected_outcome"]["legal_reaction_instead"]["deployment_status"] == "NOT_DEPLOYED"
    assert "workorder_created" in fixture["expected_outcome"]["forbidden_downstream"]
    assert "prior_verification_pass_reused_as_release_authority" in fixture["expected_outcome"]["forbidden_downstream"]

    # --- 把 fixture 的 contract_grounding 喂给真实合同逐字段比对（机检） ---
    grounding = fixture["contract_grounding"]
    gw = grounding["aggregate_ownership"]["gate_controller_same_owner_workflows"]["gate_terminal_pass_to_workorder"]
    assert gw["command"] == to_workorder["command"]
    assert gw["source_condition"] == to_workorder["source_condition"]
    assert gw["forbidden_when_gate_purpose"] == to_workorder["forbidden_when_gate_purpose"]
    gv = grounding["aggregate_ownership"]["gate_controller_same_owner_workflows"][
        "candidate_verification_pass_to_verified_candidate"
    ]
    assert gv["source_condition"] == to_verified["source_condition"]
    assert gv["forbidden_command"] == to_verified["forbidden_command"]
    assert gv["produces"] == to_verified["produces"]
    assert gv["does_not_claim_deployment"] == to_verified["does_not_claim_deployment"]
    assert gv["deployment_status_for_library_or_offline"] == to_verified["deployment_status_for_library_or_offline"]

    gp = grounding["domain_model"]["gate_purposes"]
    assert gp["values"] == purposes["values"]
    for key in ("pass_produces", "cannot_create_workorder", "cannot_claim_deployment", "does_not_bind_release_plan"):
        assert gp["candidate_verification"][key] == purposes["candidate_verification"][key], key
    for key in ("pass_is_only_path_to_workorder", "exact_binds_pre_gate_sealed_release_plan"):
        assert gp["release_authorization"][key] == purposes["release_authorization"][key], key
    # fixture 依赖的 invariant 必须全部真实存在于合同（子集包含）
    assert set(gp["invariants"]) <= set(purposes["invariants"])

    gv_domain = grounding["domain_model"]["resources"]["verified_candidate"]
    assert gv_domain["kind"] == domain["resources"]["verified_candidate"]["kind"]
    assert gv_domain["commands"] == domain["resources"]["verified_candidate"]["commands"]
    assert set(gv_domain["invariants"]) <= set(domain["resources"]["verified_candidate"]["invariants"])

    gs = grounding["schema_profiles"]["system_workorder"]["invariants"]
    profiles = _load("schema-profiles.yaml")
    assert set(gs) <= set(profiles["profiles"]["system_workorder"]["invariants"])
    gg = grounding["schema_profiles"]["system_gate_report"]["invariants"]
    assert set(gg) <= set(profiles["profiles"]["system_gate_report"]["invariants"])

    gsc = grounding["state_machines"]["cross_domain_rules"]
    assert gsc["gate_non_pass_cannot_create_system_workorder"] == states["cross_domain_rules"][
        "gate_non_pass_cannot_create_system_workorder"
    ]

    assert fixture["runtime_status"] == "NOT_RUN"
    assert "workorder_creation_path_implemented" in fixture["does_not_prove"]


def test_v5_state_machines_do_not_duplicate_v4_machines() -> None:
    states = _load("state-machines.yaml")
    v4_states = yaml.safe_load(
        (ROOT / "v4" / "events" / "state-machines.yaml").read_text(encoding="utf-8")
    )
    v4_machines = set(v4_states["machines"])
    v5_machines = set(states["machines"])

    assert v5_machines.isdisjoint(v4_machines)
    imported = set(states["imports"]["imported_without_redefinition"])
    assert imported <= v4_machines
    assert imported.isdisjoint(v5_machines)
    assert states["imports"]["imported_without_redefinition"]
    assert states["cross_domain_rules"]["external_operation_machine_is_imported_not_copied"] is True

    ownership = _load("aggregate-ownership.yaml")
    assert ownership["imports"]["v4"]["no_state_machine_copy"] is True
    assert ownership["imports"]["v4"]["mode"] == "semantic_reuse_with_schema_major_routing"


def test_first_system_case_negative_cases_are_backed_by_contract_rules() -> None:
    fixture = _fixture(FIRST_CASE_FIXTURE)
    registry = _load("intent-registry.yaml")
    ownership = _load("aggregate-ownership.yaml")
    domain = _load("domain-model.yaml")
    events = _load("events.yaml")

    intents = {intent["name"]: intent for intent in registry["intents"]}
    import_intent = intents["system-manifests.import"]
    assert import_intent["transaction_semantics"] == "ALL_OR_NOTHING_LOCAL_POSTGRES"
    assert import_intent["external_side_effects"] == "FORBIDDEN"
    assert import_intent["trust_roles_source"] == "authenticated_server_registration"
    assert registry["principal_types"]["trust_roles_are_server_derived"] is True
    assert registry["principal_types"]["caller_payload_may_assert_trust_role"] is False
    assert fixture["preconditions"]["authenticated_principal"]["trust_role_source"] == "server_registration"
    assert registry["authorization_defaults"]["unauthorized_resource_response"] == "OPAQUE_NOT_FOUND"
    assert registry["authorization_defaults"]["resource_visibility_is_checked_after_workspace_scope"] is True
    assert intents["cases.bind-application"]["authorization_condition"] == (
        "case_application_and_environment_authorized_binder"
    )
    assert intents["cases.bind-application"]["unauthorized_result"] == "OPAQUE_NOT_FOUND"
    assert intents["cases.bind-application"]["same_workspace_required"] is True
    coordinator = ownership["components"]["manifest_import_coordinator"]
    assert coordinator["failure_semantics"]["rollback"] == "ROLLBACK_ENTIRE_LOCAL_BUSINESS_TRANSACTION"
    assert ownership["rules"]["authority_receipt_required_for_new_authoritative_record"] is True
    assignment = domain["resources"]["system_assignment"]
    assert assignment["database_uniqueness_rule"] == "one_non_retired_assignment_aggregate_per_identity_key"
    assert assignment["bootstrap"]["proves_desired_only"] is True
    recorded = events["system_assignment"]["events"]["system_assignment.recorded"]
    assert "one_assignment_aggregate_per_workspace_application_environment" in recorded["guards"]

    by_name = {case["name"]: case for case in fixture["negative_cases"]}
    expected = {
        "missing_component_revision": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "caller_self_reports_integrator": "DENY_AND_AUDIT",
        "cross_workspace_component": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "case_binder_cannot_read_application": "OPAQUE_NOT_FOUND_AND_AUDIT",
        "case_and_application_cross_workspace": "OPAQUE_NOT_FOUND_AND_AUDIT",
        "authority_receipt_failure": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "bootstrap_claims_observed": "REJECT",
        "parallel_assignment_bootstrap": "CONFLICT_AND_ROLLBACK_ENTIRE_TRANSACTION",
    }
    assert set(by_name) == set(expected), set(by_name) ^ set(expected)
    for name, outcome in expected.items():
        case = by_name[name]
        assert case["expected"] == outcome, name
        assert case["mutation"], name
    assert fixture["preconditions"]["authoritative_v5_domain_tables"] == "EMPTY"
    assert fixture["preconditions"]["full_database_empty"] is False
    assert fixture["preconditions"]["seeded_v4_trust_roots"]


def test_a2a_task_state_is_transport_state_not_domain_success() -> None:
    registry = _load("intent-registry.yaml")
    target = registry["agent_protocol_target"]
    assert target["domain_task_state_is_transport_state"] is False
    a2a = target["a2a"]
    assert a2a["forbidden_domain_translation"] == [
        "task_completed_to_gate_pass",
        "task_completed_to_release_success",
    ]
    assert a2a["task_completed_can_contain_domain_non_pass_artifact"] is True
    assert a2a["send_message_creates_server_task"] is True
    assert a2a["method_mapping"]["SendMessage"]["canonical_async_intents"] == [
        "investigations.start",
        "evaluations.start",
    ]
    assert a2a["auth_required_grants_approval_capability_or_scope"] is False
    mcp = target["mcp"]
    assert mcp["approval_and_execute_tools_forbidden"] is True
    assert mcp["tool_call_cancel_is_domain_terminal"] is False


def test_non_pass_cannot_be_rewritten_to_pass_and_unknown_requires_reconcile() -> None:
    domain = _load("domain-model.yaml")
    events = _load("events.yaml")
    states = _load("state-machines.yaml")

    non_inference = domain["fact_class_non_inference"]
    assert non_inference["non_pass_cannot_be_rewritten_to_pass_in_place"] is True
    assert non_inference["unknown_precedes_retry"] is True
    assert non_inference["unknown_requires_reconcile_before_retry"] is True
    assert non_inference["adapter_receipt_alone_does_not_constitute_release_success"] is True
    assert non_inference["direct_restore_or_resume_is_not_a_second_rollback_authority"] is True
    assert "non_pass_cannot_be_rewritten_to_pass_in_place" in non_inference["invariants"]

    gate_events = events["system_gate_execution"]["events"]
    # 只有 gate.completed / gate.reconciled_pass 能携带 PASS；非 PASS 终态是独立事件，不能改写为 PASS
    assert gate_events["gate.completed"]["constants"]["gate_verdict"] == "PASS"
    assert gate_events["gate.failed"]["constants"]["gate_verdict"] == "FAILED"
    assert gate_events["gate.inconclusive"]["constants"]["gate_verdict"] == "INCONCLUSIVE"
    assert gate_events["gate.error"]["constants"]["gate_verdict"] == "ERROR"
    assert gate_events["gate.unknown"]["constants"]["reconciliation_required"] is True
    assert gate_events["gate.reconciled_pass"]["payload_required"] == [  # 独立 reconcile 事件，不能由非 PASS 改写
        "gate_execution_id",
        "exact_system_gate_report_binding",
    ]

    op_events = events["system_external_operation"]["events"]
    assert op_events["external_operation.unknown"]["constants"]["reconciliation_required"] is True
    assert op_events["external_operation.reconciled"]["allowed_reconciled_status"] == ["SUCCEEDED", "FAILED"]
    assert "SUCCEEDED" not in op_events["external_operation.unknown"]["constants"]

    profile = states["schema_major_2_external_operation_profile"]
    assert profile["unknown_before_retry"] == "reconcile_required"
    assert profile["adapter_receipt_alone_can_succeed"] is False
    assert profile["reconcile_to_success_reuses_full_success_guard"] is True
    assert states["cross_domain_rules"]["external_operation_unknown_requires_reconcile_before_retry"] is True
    assert states["cross_domain_rules"]["gate_non_pass_cannot_create_system_workorder"] is True


def test_evaluation_purpose_enum_is_new_vocabulary_everywhere() -> None:
    """F2 机检：evaluation purpose 枚举只含新词，旧词 PRE/POST_RELEASE 不得作为 purpose 出现。

    结构级检查三个携带 evaluation_purpose 的 profile 均为
    [CANDIDATE_VERIFICATION, RELEASE_AUTHORIZATION]，且 exact_release_plan_binding
    必须通过 release_plan_binding_union 条件化（CANDIDATE_VERIFICATION 禁止、
    RELEASE_AUTHORIZATION 必须），不得再出现在 required_fields 无条件列表里；
    文件级扫描 schema-profiles.yaml / events.yaml 任何 evaluation_purpose_values
    行均不得含旧词（events 没有该键，domain-model 的 phase 值不在此扫描范围）。
    """
    profiles = _load("schema-profiles.yaml")
    for name in ("system_evaluation_plan", "system_gate_report", "system_gate_track_receipt"):
        profile = profiles["profiles"][name]
        assert profile["evaluation_purpose_values"] == [
            "CANDIDATE_VERIFICATION",
            "RELEASE_AUTHORIZATION",
        ], name
        assert "exact_release_plan_binding" not in profile["required_fields"], name
        union = profile["release_plan_binding_union"]
        assert union["discriminator"] == "evaluation_purpose", name
        variants = union["variants"]
        assert variants["CANDIDATE_VERIFICATION"]["does_not_bind_release_plan"] is True, name
        assert "exact_release_plan_binding" in variants["CANDIDATE_VERIFICATION"]["forbidden_fields"], name
        assert variants["RELEASE_AUTHORIZATION"]["required"] is True, name
        assert "exact_release_plan_binding" in variants["RELEASE_AUTHORIZATION"]["required_fields"], name
        assert variants["RELEASE_AUTHORIZATION"]["exact_binds_pre_gate_sealed_release_plan"] is True, name

    for name in ("schema-profiles.yaml", "events.yaml"):
        for line_number, line in enumerate((V5 / name).read_text(encoding="utf-8").splitlines(), 1):
            if "evaluation_purpose_values" in line:
                assert "PRE_RELEASE" not in line and "POST_RELEASE" not in line, (
                    f"{name}:{line_number}: {line.strip()}"
                )

    # events 层同步条件化：plan_frozen 不得无条件携带 exact_release_plan_binding
    plan_frozen = _load("events.yaml")["system_evaluation_plan"]["events"]["evaluation.plan_frozen"]
    assert "exact_release_plan_binding" not in plan_frozen["payload_required"]
    event_union = plan_frozen["release_plan_binding_union"]["variants"]
    assert event_union["CANDIDATE_VERIFICATION"]["payload_forbidden"] == ["exact_release_plan_binding"]
    assert event_union["RELEASE_AUTHORIZATION"]["payload_required"] == ["exact_release_plan_binding"]
    assert event_union["RELEASE_AUTHORIZATION"]["exact_binds_pre_gate_sealed_release_plan"] is True
