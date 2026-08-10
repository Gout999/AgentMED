"""V5-0C first slice acceptance: ApplicationCaseBinding read-back/rebind,
trusted atomic bootstrap import, acceptance-criteria propose/get/confirm wire,
principal allowlist adversarial, and onboarding orchestration.

所有断言针对 contracts/v5/*.yaml 的真实字段，并把 contracts/v5/fixtures/ 的
contract_grounding 喂给合同规则逐字段比对（机检），不引入 runtime 语义。
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "v5"

FIXTURES = {
    name: V5 / "fixtures" / f"{name}.yaml"
    for name in (
        "binding-readback",
        "bootstrap-import-atomic",
        "acceptance-wire",
        "principal-allowlist-adversarial",
        "onboarding-orchestration",
    )
}


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


def _fixture(name: str) -> dict:
    return yaml.load(FIXTURES[name].read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)


def test_binding_idempotent_replay_and_conflict() -> None:
    registry = _load("intent-registry.yaml")
    domain = _load("domain-model.yaml")
    fixture = _fixture("binding-readback")
    binding = domain["resources"]["application_case_binding"]

    scenarios = {s["name"]: s for s in fixture["scenarios"]}
    replay = scenarios["idempotent_replay_same_key_same_request"]
    conflict = scenarios["same_key_different_request_conflict"]

    assert replay["expected"] == "RETURN_SAME_RECORD_AND_RESPONSE"
    assert conflict["expected"] == "CONFLICT_AND_AUDIT"
    assert binding["uniqueness_key"] == ["workspace_id", "case_id", "case_revision", "case_digest"]
    assert binding["exact_case_binding_fields"] == ["case_id", "case_revision", "case_digest"]
    assert "same_exact_case_binding_and_same_target_is_idempotent" in binding["invariants"]
    assert "same_exact_case_binding_and_different_target_is_conflict" in binding["invariants"]

    bind_write = next(i for i in registry["intents"] if i["name"] == "cases.bind-application")
    assert bind_write["kind"] == "mutation"
    assert bind_write["idempotency"] == "required"
    assert bind_write["command_target"] == {"resource": "application_case_binding",
                                            "command": "cases.bind-application"}

    # fixture grounding 机检
    grounding = fixture["contract_grounding"]
    assert grounding["aggregate_ownership"]["record_authority"]["APPLICATION_CASE_BINDING"]["owner"] == "case-controller"
    for key in ("additive_link_to_immutable_s1a_case", "exact_case_fields", "same_exact_case_same_target",
                "same_exact_case_different_target", "rebinding_requires_new_quality_case_revision",
                "conflict_is_never_silently_overwritten_by_latest",
                "write_response_lost_read_back_path", "read_back_returns_exact_case_binding"):
        assert grounding["compatibility"]["application_case_binding_contract"][key] == (
            _load("compatibility.yaml")["application_case_binding_contract"][key]
        ), key
    events = _load("events.yaml")
    payload = events["application_case_binding"]["events"]["case.application_bound"]["payload_required"]
    assert set(grounding["events"]["application_case_binding"]["events"]["case.application_bound"]["payload_required"]) <= set(payload)


def test_binding_readback_after_lost_write_is_authoritative() -> None:
    domain = _load("domain-model.yaml")
    registry = _load("intent-registry.yaml")
    fixture = _fixture("binding-readback")
    binding = domain["resources"]["application_case_binding"]

    readback = next(s for s in fixture["scenarios"] if s["name"] == "readback_after_lost_write_response")
    assert readback["query_fields"] == ["case_revision", "case_digest"]
    assert readback["expected"] == "AUTHORITATIVE_READ_BACK"
    assert "returns_exact_case_binding_fields" in readback["asserts"]
    assert "response_is_authoritative_after_write_without_live_response" in readback["asserts"]

    read_intent = next(i for i in registry["intents"] if i["name"] == "case-application-bindings.get")
    assert read_intent["required_query_fields"] == ["case_revision", "case_digest"]
    assert read_intent["unique_result_key"] == ["workspace_id", "case_id", "case_revision", "case_digest"]
    assert read_intent["different_target_for_same_exact_case"] == "CONFLICT"

    assert "exact_case_binding" in binding["required_fields"]
    assert "original_signal_payload_is_unchanged" in binding["invariants"]
    assert "original_case_payload_is_unchanged" in binding["invariants"]
    assert "original_digest_is_not_recomputed" in binding["invariants"]
    # 断线回读路径必须只读：read intent 是 query，不产生写副作用
    assert read_intent["kind"] == "query"
    assert read_intent["idempotency"] == "none"


def test_binding_rebind_requires_new_case_revision_and_adversarial_rejected() -> None:
    domain = _load("domain-model.yaml")
    registry = _load("intent-registry.yaml")
    fixture = _fixture("binding-readback")
    binding = domain["resources"]["application_case_binding"]

    rebind = next(s for s in fixture["scenarios"] if s["name"] == "rebind_requires_new_case_revision")
    assert rebind["same_exact_case"] is True
    assert rebind["different_target"] is True
    assert rebind["expected"] == "CONFLICT_AND_AUDIT"
    assert "rebinding_requires_a_new_quality_case_revision" in binding["invariants"]
    assert "conflict_is_never_silently_overwritten_by_latest" in _load(
        "compatibility.yaml"
    )["application_case_binding_contract"]

    adversarial = {a["name"]: a for a in fixture["adversarial"]}
    assert adversarial["cross_workspace_reference"]["expected"] == "REJECT_AND_AUDIT"
    assert adversarial["caller_self_reports_owner"]["expected"] == "DENY_AND_AUDIT"
    assert adversarial["route_downgrade"]["expected"] == "REJECT_AND_AUDIT"
    assert adversarial["binder_cannot_read_application"]["expected"] == "OPAQUE_NOT_FOUND_AND_AUDIT"
    assert adversarial["case_and_application_cross_workspace"]["expected"] == "OPAQUE_NOT_FOUND_AND_AUDIT"

    bind_write = next(i for i in registry["intents"] if i["name"] == "cases.bind-application")
    assert bind_write["same_workspace_required"] is True
    assert bind_write["unauthorized_result"] == "OPAQUE_NOT_FOUND"
    assert bind_write["authorization_condition"] == "case_application_and_environment_authorized_binder"


def test_bootstrap_import_is_one_atomic_transaction() -> None:
    fixture = _fixture("bootstrap-import-atomic")
    compatibility = _load("compatibility.yaml")
    ownership = _load("aggregate-ownership.yaml")
    registry = _load("intent-registry.yaml")

    assert fixture["preconditions"]["authoritative_v5_domain_tables"] == "EMPTY"
    assert fixture["preconditions"]["full_database_empty"] is False
    assert fixture["preconditions"]["seeded_v4_trust_roots"]
    assert fixture["preconditions"]["existing_v4_case"]["case_id"]

    expected_constructs = fixture["one_local_transaction_constructs"]
    assert expected_constructs == [
        "AI_APPLICATION", "ENVIRONMENT", "SYSTEM_COMPONENT", "COMPONENT_REVISION",
        "TOPOLOGY_REVISION", "SYSTEM_VERSION_SET", "BOOTSTRAP_ATTESTATION",
        "SYSTEM_ASSIGNMENT",
    ]
    assert compatibility["bootstrap_import_contract"]["constructs_in_one_local_postgresql_transaction"] == (
        expected_constructs
    )
    assert compatibility["bootstrap_import_contract"]["atomicity"] == "ALL_OR_NOTHING_LOCAL_POSTGRES"
    assert fixture["atomicity"]["mode"] == compatibility["bootstrap_import_contract"]["atomicity"]
    assert fixture["atomicity"]["any_step_failure_rolls_back_every_record"] is True
    # binding 不属于 manifest 事务：由 cases.bind-application 在 import 之后单独创建
    assert fixture["after_import"] == ["APPLICATION_CASE_BINDING"]

    coordinator = ownership["components"]["manifest_import_coordinator"]
    assert coordinator["failure_semantics"]["local_database_atomicity"] == "ALL_OR_NOTHING"
    assert coordinator["failure_semantics"]["rollback"] == "ROLLBACK_ENTIRE_LOCAL_BUSINESS_TRANSACTION"
    assert coordinator["failure_semantics"]["retry"] == "IDEMPOTENT_REPLAY_OF_SAME_MANIFEST_DIGEST"

    import_intent = next(i for i in registry["intents"] if i["name"] == "system-manifests.import")
    assert import_intent["transaction_semantics"] == "ALL_OR_NOTHING_LOCAL_POSTGRES"
    assert import_intent["external_side_effects"] == "FORBIDDEN"
    # 机检：fixture 引用的 coordinator/import 规则必须与真实合同一致
    grounding = fixture["contract_grounding"]
    g_coord = grounding["aggregate_ownership"]["components"]["manifest_import_coordinator"]
    for key, expected in g_coord["failure_semantics"].items():
        assert coordinator["failure_semantics"][key] == expected, key
    assert set(g_coord["must_not"]) <= set(coordinator["must_not"])
    assert grounding["intent_registry"]["intents"]["system-manifests.import"]["transaction_semantics"] == (
        import_intent["transaction_semantics"]
    )


def test_bootstrap_import_negative_cases_rollback_and_silent_prereq_forbidden() -> None:
    fixture = _fixture("bootstrap-import-atomic")
    registry = _load("intent-registry.yaml")
    domain = _load("domain-model.yaml")

    by_name = {case["name"]: case for case in fixture["negative_cases"]}
    expected = {
        "missing_component_revision": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "caller_self_reports_integrator": "DENY_AND_AUDIT",
        "cross_workspace_component": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "authority_receipt_failure": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "bootstrap_claims_observed": "REJECT",
        "parallel_assignment_bootstrap": "CONFLICT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "system_versions_record_silently_creates_prerequisites": "REJECT_AND_AUDIT",
        "importer_role_not_allowlisted": "DENY_AND_AUDIT",
    }
    assert set(by_name) == set(expected), set(by_name) ^ set(expected)
    for name, outcome in expected.items():
        assert by_name[name]["expected"] == outcome, name

    # system-versions.record 只能 record system_version_set，不得暗中创建前置对象
    record_intent = next(i for i in registry["intents"] if i["name"] == "system-versions.record")
    assert record_intent["command_target"] == {"resource": "system_version_set",
                                               "command": "system-versions.record"}
    assert _load("compatibility.yaml")["bootstrap_import_contract"][
        "system_versions_record_may_not_silently_create_prerequisites"
    ] is True

    assignment = domain["resources"]["system_assignment"]
    assert assignment["database_uniqueness_rule"] == "one_non_retired_assignment_aggregate_per_identity_key"
    assert assignment["bootstrap"]["proves_desired_only"] is True


def test_manifest_import_principal_allowlist() -> None:
    registry = _load("intent-registry.yaml")
    fixture = _fixture("principal-allowlist-adversarial")
    contract = registry["first_slice_wire_contract"]["principal_allowlist_contract"]

    import_intent = next(i for i in registry["intents"] if i["name"] == "system-manifests.import")
    assert import_intent["allowed_principal_types"] == ["human", "service"]
    assert import_intent["required_trust_roles_any_of"] == ["integrator", "catalog_admin", "trusted_builder"]
    assert import_intent["trust_roles_source"] == "authenticated_server_registration"
    assert "external_agent" not in import_intent["allowed_principal_types"]

    assert fixture["allowed"]["manifest_import_roles"] == contract["manifest_import_allowed_roles"]
    assert fixture["allowed"]["manifest_import_roles"] == import_intent["required_trust_roles_any_of"]
    assert fixture["allowed"]["external_agent_manifest_import"] is False
    assert contract["trust_roles_are_server_derived"] is True
    assert contract["caller_payload_may_assert_trust_role"] is False

    grounding = fixture["contract_grounding"]["intent_registry"]
    assert grounding["principal_types"] == registry["principal_types"]
    assert grounding["authorization_defaults"] == registry["authorization_defaults"]


def test_external_agent_cannot_self_report_owner_deployed_observed_effect() -> None:
    registry = _load("intent-registry.yaml")
    domain = _load("domain-model.yaml")
    fixture = _fixture("principal-allowlist-adversarial")

    cases = {case["name"]: case for case in fixture["cases"]}
    assert cases["external_agent_self_reports_owner"]["expected"] == "DENY_AND_AUDIT"
    assert cases["external_agent_self_declares_deployed_version"]["expected"] == "DENY_AND_AUDIT"
    assert cases["external_agent_self_attests_observed_state"]["expected"] == "DENY_AND_AUDIT"
    assert cases["external_agent_self_reports_external_effect"]["expected"] == "DENY_AND_AUDIT"
    assert cases["caller_payload_asserts_trust_role"]["expected"] == "DENY_AND_AUDIT"
    assert cases["manifest_import_by_unlisted_role"]["expected"] == "DENY_AND_AUDIT"
    assert cases["unlisted_principal_type"]["expected"] == "DENY_AND_AUDIT"
    assert cases["internal_scope_by_public_principal"]["expected"] == "DENY_AND_AUDIT"

    # 结构级背书：external_agent 被禁止的公开 scope 必须包含 observed/effect attest
    forbidden = registry["forbidden_public_scopes"]["external_agent"]
    assert "observed_state:attest:trusted" in forbidden
    assert "external_effects:attest:trusted" in forbidden
    # external_agent 不在任何 internal intent 的 allowed 集合中
    public = set(registry["principal_types"]["public"])
    for policy in registry["internal_intent_authorization"].values():
        assert public.isdisjoint(policy["allowed_principal_types"])

    # owner / deployed / observed / effect 四类 fact 在 domain 层均禁止 caller 自报
    provider = domain["resources"]["provider_version_attestation"]
    assert "caller_payload_cannot_self_attest_provider_version" in provider["invariants"]
    observed = domain["resources"]["observed_state_snapshot"]
    assert "observation_is_independent_from_assignment_readback" in observed["invariants"]
    assert "desired_readback_cannot_create_match" in observed["invariants"]
    receipt = domain["resources"]["operation_execution_receipt"]
    assert set(receipt["does_not_prove"]) >= {
        "desired_assignment_changed",
        "runtime_observed_match",
        "external_effect_verified",
    }
    assert "caller_supplied_summary_is_not_authority" in domain["identity_assurance"]["summary_rule"]


def test_acceptance_wire_propose_get_confirm() -> None:
    registry = _load("intent-registry.yaml")
    domain = _load("domain-model.yaml")
    ownership = _load("aggregate-ownership.yaml")
    events = _load("events.yaml")
    fixture = _fixture("acceptance-wire")

    propose = fixture["propose"]
    assert propose["intent"] == "acceptance-criteria.propose"
    assert propose["proposer"]["principal_type"] == "external_agent"
    assert propose["expected"]["confirmation_status"] == "PROPOSED"
    assert propose["expected"]["is_untrusted"] is True
    assert propose["expected"]["is_authoritative"] is False

    confirm = fixture["confirm"]
    assert confirm["confirmer"]["principal_type"] == "human"
    assert confirm["confirmer"]["required_trust_roles"] == ["maintainer", "domain_reviewer"]
    assert confirm["confirmer"]["reauthentication"] == "required"
    assert confirm["expected"]["confirmation_status"] == "CONFIRMED"
    assert confirm["expected"]["is_authoritative"] is True
    assert confirm["expected"]["new_immutable_record_not_in_place_rewrite"] is True
    assert confirm["expected"]["references_prior_proposed_revision"] is True
    assert confirm["expected"]["readiness_after_confirm"] == "READY"

    provenance = domain["acceptance_provenance"]["acceptance_criteria_revision"]
    assert provenance["agent_can_propose"] is True
    assert provenance["agent_can_confirm"] is False
    assert provenance["confirmer_must_be_human_maintainer_or_domain_reviewer"] is True
    assert provenance["confirmed_revision_references_prior_proposed_revision"] is True
    assert provenance["confirmed_revision_is_immutable_and_additive"] is True

    resource = domain["resources"]["acceptance_criteria_revision"]
    assert resource["owner"] == "case-controller"
    assert resource["subordinate_to"] == "resolution_contract"
    status = resource["confirmation_status_union"]["variants"]
    assert status["PROPOSED"]["is_untrusted"] is True
    assert status["PROPOSED"]["is_authoritative"] is False
    assert status["CONFIRMED"]["is_authoritative"] is True

    confirm_intent = next(i for i in registry["intents"] if i["name"] == "acceptance-criteria.confirm")
    assert confirm_intent["allowed_principal_types"] == ["human"]
    assert confirm_intent["required_trust_roles_any_of"] == ["maintainer", "domain_reviewer"]
    assert confirm_intent["reauthentication"] == "required"
    assert confirm_intent["confirmed_revision_is_immutable_and_additive"] is True
    assert confirm_intent["confirmed_revision_does_not_rewrite_quality_case_payload_or_digest"] is True
    assert confirm_intent["idempotency"] == "required"

    get_intent = next(i for i in registry["intents"] if i["name"] == "acceptance-criteria.get")
    assert get_intent["required_query_fields"] == ["case_revision"]
    assert get_intent["kind"] == "query"

    owned = ownership["resources"]["acceptance_criteria_revision"]
    assert set(owned["commands"]) == {"acceptance-criteria.propose", "acceptance-criteria.confirm"}

    confirmed_ev = events["acceptance_criteria_revision"]["events"]["acceptance_criteria.confirmed"]
    assert "confirmer_is_human_maintainer_or_domain_reviewer" in confirmed_ev["guards"]
    assert "confirmer_is_not_the_proposer" in confirmed_ev["guards"]
    assert "confirmed_revision_is_new_immutable_record_not_in_place_rewrite" in confirmed_ev["guards"]
    assert "confirmed_revision_does_not_rewrite_quality_case_payload_or_digest" in confirmed_ev["guards"]

    # 机检 grounding
    grounding = fixture["contract_grounding"]
    compat = _load("compatibility.yaml")["acceptance_criteria_wire"]
    assert grounding["compatibility"]["acceptance_criteria_wire"] == compat


def test_acceptance_readiness_read_path_is_additive_projection() -> None:
    domain = _load("domain-model.yaml")
    ownership = _load("aggregate-ownership.yaml")
    fixture = _fixture("acceptance-wire")

    readiness = domain["resources"]["case_readiness"]
    assert readiness["kind"] == "projection"
    assert readiness["commands"] == []
    assert readiness["uses_record_envelope"] is False
    assert set(readiness["readiness_values"]) == {"NEEDS_ACCEPTANCE_CRITERIA", "READY"}
    assert "needs_acceptance_criteria_is_projection_not_case_lifecycle" in readiness["invariants"]
    assert "needs_acceptance_criteria_does_not_rewrite_quality_case_payload_or_digest" in readiness["invariants"]
    assert "needs_acceptance_criteria_blocks_gate_pass_but_allows_case_investigation" in readiness["invariants"]

    assert set(fixture["get"]["readiness_invariants"]) == set(readiness["invariants"])
    assert fixture["get"]["expected"]["readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"
    assert ownership["resources"]["case_readiness"]["kind"] == "projection"
    assert ownership["resources"]["case_readiness"]["commands"] == []


def test_acceptance_adversarial_cannot_self_confirm_or_rewrite() -> None:
    fixture = _fixture("acceptance-wire")
    domain = _load("domain-model.yaml")
    events = _load("events.yaml")

    adversarial = {a["name"]: a for a in fixture["adversarial"]}
    assert adversarial["proposer_cannot_self_confirm"]["expected"] == "DENY_AND_AUDIT"
    assert adversarial["non_human_cannot_confirm"]["expected"] == "DENY_AND_AUDIT"
    assert adversarial["confirm_requires_required_trust_role"]["expected"] == "DENY_AND_AUDIT"
    assert adversarial["confirmed_revision_cannot_be_rewritten_in_place"]["expected"] == "REJECT"
    assert adversarial["confirm_without_reauthentication"]["expected"] == "REQUEST_INVALID_AND_AUDIT"

    provenance = domain["acceptance_provenance"]["acceptance_criteria_revision"]
    assert "proposal_does_not_confirm_itself" in provenance["invariants"]
    assert "only_human_maintainer_or_domain_reviewer_can_confirm" in provenance["invariants"]
    assert "agent_or_service_or_connector_principal_cannot_confirm" in provenance["invariants"]
    assert "confirmed_revision_is_new_immutable_record_not_in_place_rewrite" in provenance["invariants"]
    assert "confirmed_revision_does_not_rewrite_quality_case_payload_or_digest" in provenance["invariants"]

    resource = domain["resources"]["acceptance_criteria_revision"]
    assert "proposer_principal_equals_confirmer" in resource["confirmation_status_union"]["variants"]["CONFIRMED"]["forbids"]
    assert "confirmer_principal" in resource["confirmation_status_union"]["variants"]["PROPOSED"]["forbids"]

    proposed = events["acceptance_criteria_revision"]["events"]["acceptance_criteria.proposed"]
    confirmed = events["acceptance_criteria_revision"]["events"]["acceptance_criteria.confirmed"]
    assert "proposer_is_agent_service_or_connector_not_human_maintainer_or_domain_reviewer" in proposed["guards"]
    assert "confirmer_is_not_the_proposer" in confirmed["guards"]
    assert "confirmed_revision_is_new_immutable_record_not_in_place_rewrite" in confirmed["guards"]


def test_onboarding_orchestration_uses_canonical_intents_and_retry_safety() -> None:
    registry = _load("intent-registry.yaml")
    v4_registry = yaml.safe_load((ROOT / "v4" / "intent-registry.yaml").read_text(encoding="utf-8"))
    v4_ownership = yaml.safe_load((ROOT / "v4" / "aggregate-ownership.yaml").read_text(encoding="utf-8"))
    fixture = _fixture("onboarding-orchestration")
    compatibility = _load("compatibility.yaml")

    init = fixture["workflows"]["caseloop_init"]
    assert init["steps"] == ["local_read_only_discovery", "manifest_draft", "human_confirmation",
                             "canonical_manifest_import"]
    assert init["canonical_intents"] == ["capabilities.get", "system-manifests.import"]
    assert compatibility["onboarding_workflow_orchestration"]["caseloop_init"]["canonical_import_intent"] == (
        "system-manifests.import"
    )
    assert "bypass_human_confirmation" in init["forbidden"]

    from_issue = fixture["workflows"]["caseloop_case_from_issue"]
    assert from_issue["steps"] == ["issue_snapshot", "signal_submit", "case_open",
                                   "application_binding", "acceptance_criteria_draft"]
    assert compatibility["onboarding_workflow_orchestration"]["caseloop_case_from_issue"]["canonical_intents_only"] is True

    # 编排的每个操作必须落在 canonical 面上：v4/v5 公开 intent 或 v4/v5 所有权命令，
    # 且写面都有幂等契约；不允许任何 ad-hoc 私有写入。
    v4_intent_names = {item["name"] for item in v4_registry["intents"]}
    v5_intent_names = {item["name"] for item in registry["intents"]}
    v4_command_names = {
        command
        for resource in v4_ownership["resources"].values()
        for command in resource.get("commands", [])
    }
    v5_command_names = {
        command
        for resource in _load("aggregate-ownership.yaml")["resources"].values()
        for command in resource.get("commands", [])
    }
    canonical_names = v4_intent_names | v5_intent_names | v4_command_names | v5_command_names
    for intent_name in init["canonical_intents"] + from_issue["canonical_intents"]:
        assert intent_name in canonical_names, intent_name

    retry = fixture["retry_safety"]
    assert retry["idempotency_keys_reused_on_retry"] is True
    assert retry["no_second_owner_on_retry"] is True
    assert retry["no_duplicate_case_on_retry"] is True
    assert retry["no_auto_confirmed_acceptance"] is True
    assert retry["lost_response_recovers_via_authoritative_read_intent"] is True
    assert compatibility["onboarding_workflow_orchestration"]["retry_safety"] == {
        key: retry[key] for key in ("no_second_owner_on_retry", "no_duplicate_case_on_retry",
                                    "no_auto_confirmed_acceptance")
    }

    # acceptance draft 只 produce 未确认草案，永不自动 confirm
    propose_intent = next(i for i in registry["intents"] if i["name"] == "acceptance-criteria.propose")
    assert propose_intent["records_untrusted_proposal_not_confirmed_acceptance_criteria"] is True
    idem = registry["first_slice_wire_contract"]["idempotency_contract"]
    assert idem["replay_creates_no_second_record"] is True
    assert idem["same_key_same_request"] == "RETURN_SAME_RECORD_AND_RESPONSE"
