"""V5-0C compatibility freeze: S1A byte-for-byte regression, first wire slice,
api-major handshake, authority no-dual-write, and fail-closed error /
idempotency / cursor contracts.

所有断言针对 contracts/v5/*.yaml、contracts/v4/* 的实际字段与
contracts/v5/fixtures/ 的 fixture 内容，不引入 runtime 语义。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "v5"
V4 = ROOT / "v4"
V4_VALID = V4 / "fixtures" / "valid"
V4_OWNERSHIP = V4 / "aggregate-ownership.yaml"

NEW_0C_FIXTURES = [
    V5 / "fixtures" / "first-wire-slice.yaml",
    V5 / "fixtures" / "s1a-receipt-regression.yaml",
    V5 / "fixtures" / "binding-readback.yaml",
    V5 / "fixtures" / "bootstrap-import-atomic.yaml",
    V5 / "fixtures" / "acceptance-wire.yaml",
    V5 / "fixtures" / "principal-allowlist-adversarial.yaml",
    V5 / "fixtures" / "onboarding-orchestration.yaml",
]

FIRST_SLICE = [
    "capabilities.get",
    "system-manifests.import",
    "applications.get",
    "system-versions.get",
    "system-versions.diff",
    "cases.bind-application",
    "case-application-bindings.get",
    "acceptance-criteria.propose",
    "acceptance-criteria.get",
    "acceptance-criteria.confirm",
]


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_new_0c_fixtures_are_valid_and_draft_not_run() -> None:
    for path in NEW_0C_FIXTURES:
        document = _fixture(path)
        assert document["status"] == "draft_contract_fixture", path
        assert document["runtime_status"] == "NOT_RUN", path
        assert "fixture_id" in document, path
        assert document["does_not_prove"], path


def test_s1a_receipt_fixtures_are_byte_for_byte_unchanged() -> None:
    """旧 S1A response/receipt byte-for-byte regression：文件字节 sha256 必须与
    v5 fixture 固定的摘要完全一致，任何原位改写都会令本测试变红。"""
    regression = _fixture(V5 / "fixtures" / "s1a-receipt-regression.yaml")
    subject = (ROOT.parent / regression["subject"]).resolve()
    assert subject == V4_VALID.resolve()
    pinned = {entry["path"]: entry for entry in regression["pinned_files"]}
    assert pinned, "regression fixture must pin at least one file"
    for entry in regression["pinned_files"]:
        path = V4_VALID / entry["path"]
        assert path.exists(), path
        assert _sha256(path) == entry["sha256"], entry["path"]


def test_s1a_receipt_structural_invariants_still_hold() -> None:
    """在字节级摘要之上，再机检 S1A 关键结构不变性（幂等回执不可变、
    authority receipt 自含摘要、response 带 replayed 标志等）。"""
    regression = _fixture(V5 / "fixtures" / "s1a-receipt-regression.yaml")
    by_path = {entry["path"]: entry for entry in regression["pinned_files"]}

    response = json.loads((V4_VALID / "public-signal-submission-response.json").read_text())
    assert response["idempotency"]["replayed"] is False
    receipt = response["idempotency"]["receipt"]
    assert receipt["immutable"] is True
    assert receipt["hash_rule"] == "jcs-rfc8785-v1+sha256(excluding:/receipt_digest)"
    assert receipt["receipt_digest"].startswith("sha256:")
    assert response["case"]["revision"] >= 1
    assert response["case"]["status"] in {"OPEN", "CLOSED"}
    assert any("immutable" in item for item in by_path["public-signal-submission-response.json"]["immutability"])

    authority = json.loads((V4_VALID / "authority-receipt.json").read_text())
    assert authority["immutable"] is True
    assert authority["hash_rule"] == "jcs-rfc8785-v1+sha256(excluding:/authority_receipt_digest)"
    assert authority["authority_receipt_digest"].startswith("sha256:")
    assert authority["subject"]["kind"] == "SKILL_MANIFEST"
    assert set(authority["subject"]) >= {"kind", "id", "revision", "digest"}

    # 文件级禁止改写规则必须与 compatibility.yaml history_rules 一致
    compatibility = _load("compatibility.yaml")
    assert compatibility["history_rules"] == {
        "rewrite_existing_payload": False,
        "recompute_existing_digest": False,
        "reinterpret_governed_agent_id_as_application_id": False,
        "rewrite_existing_authority_receipt": False,
        "upgrade_historical_evidence_facet": False,
    }
    for forbidden in regression["forbidden_changes"]:
        assert forbidden.startswith(("rewrite_", "recompute_", "reinterpret_", "upgrade_",
                                     "rename_", "delete_")), forbidden


def test_first_wire_slice_matches_registry_and_compatibility() -> None:
    registry = _load("intent-registry.yaml")
    compatibility = _load("compatibility.yaml")
    fixture = _fixture(V5 / "fixtures" / "first-wire-slice.yaml")

    assert compatibility["first_slice"]["intents"] == FIRST_SLICE
    assert compatibility["v5_target_surface"]["first_wire_slice"] == FIRST_SLICE
    assert registry["first_slice_wire_contract"]["intents"] == FIRST_SLICE
    assert [intent["name"] for intent in fixture["slice"]["intents"]] == FIRST_SLICE
    assert registry["first_slice_wire_contract"]["count"] == len(FIRST_SLICE)
    assert compatibility["first_slice"]["count"] == len(FIRST_SLICE)
    assert registry["first_slice_wire_contract"]["historical_freeze"] is True
    assert compatibility["first_slice"]["historical_freeze"] is True
    assert fixture["historical_freeze"] is True

    by_name = {intent["name"]: intent for intent in registry["intents"]}
    enabled = set(registry["runtime_overlay"]["enabled_intents"])
    assert set(FIRST_SLICE) <= set(by_name)
    for name in FIRST_SLICE:
        intent = by_name[name]
        assert intent["contract_major"] == 2
        assert name in enabled
        assert intent["wire_status"] == "FROZEN"
        assert intent["implementation_status"] == "IMPLEMENTED"
        assert intent["cli_requires_explicit_api_major"] is True
        assert intent["http"]["path"].startswith("/api/v2/")
        fixture_intent = next(i for i in fixture["slice"]["intents"] if i["name"] == name)
        assert fixture_intent["http"] == intent["http"]
        assert fixture_intent["kind"] == intent["kind"]
        assert fixture_intent["scope"] == intent["scope"]
        assert fixture_intent["idempotency"] == intent["idempotency"]
        if intent["kind"] == "mutation":
            assert intent["idempotency"] == "required"
        else:
            assert intent["idempotency"] == "none"

    # Historical freeze remains all-disabled; current activation is the
    # additive explicit allowlist with audited discovery, but no MCP/A2A.
    assert registry["activation_flags"] == {
        "http_routes": True,
        "cli_commands": True,
        "sdk_methods": False,
        "capability_discovery": True,
        "mcp_tools": False,
        "a2a_agent_card": False,
        "a2a_transport": False,
        "a2a_methods": False,
    }
    for example in fixture["skeleton_outside_slice"]["examples"]:
        assert example in by_name, example
        assert example not in FIRST_SLICE, example
        assert by_name[example]["wire_status"] == "DRAFT"
        assert by_name[example]["implementation_status"] == "NOT_IMPLEMENTED"
    assert fixture["skeleton_outside_slice"]["skeleton_generates"] == compatibility["first_slice"][
        "skeleton_intents_generate"
    ]
    assert fixture["transport_status"]["http_routes"] == "DISABLED"
    assert fixture["transport_status"]["cli_commands"] == "DISABLED"
    assert registry["runtime_overlay"]["capability_discovery_enabled"] is True


def test_api_major_handshake_fail_closed() -> None:
    registry = _load("intent-registry.yaml")
    fixture = _fixture(V5 / "fixtures" / "first-wire-slice.yaml")
    selection = registry["api_major_selection"]
    grounding = fixture["contract_grounding"]["intent_registry"]["api_major_selection"]

    assert selection["default_api_major"] == 1
    assert selection["v2_requires_explicit_selection"] is True
    assert selection["cli"]["flag"] == "--api-version 2"
    assert selection["cli"]["silent_default_change"] == "FORBIDDEN"
    assert selection["http"]["request_header"] == "X-AgentMED-Contract-Version"
    assert selection["http"]["response_header"] == "X-AgentMED-Contract-Version"
    assert selection["http"]["v2_required_request_value"] == "2.0"
    assert selection["http"]["v2_missing_or_other_value_error"] == "REQUEST_INVALID"
    assert selection["http"]["v2_never_falls_back_to_v1"] is True
    assert selection["http"]["url_major_must_match_request_header_major"] is True
    assert selection["http"]["response_must_echo_contract_major"] is True
    assert selection["http"]["mismatched_or_downgraded_major"] == "REJECT_AND_AUDIT"

    # fixture 机检：handshake 每一条都与 registry 一致
    assert fixture["api_major_handshake"]["default_api_major"] == grounding["default_api_major"]
    assert fixture["api_major_handshake"]["v2_requires_explicit_selection"] == grounding["v2_requires_explicit_selection"]
    assert fixture["api_major_handshake"]["cli_flag"] == grounding["cli"]["flag"]
    assert fixture["api_major_handshake"]["http_request_header"] == grounding["http"]["request_header"]
    assert fixture["api_major_handshake"]["http_response_header"] == grounding["http"]["response_header"]
    assert fixture["api_major_handshake"]["v2_required_request_value"] == grounding["http"]["v2_required_request_value"]
    outcomes = {case["name"]: case["expected"] for case in fixture["api_major_handshake"]["cases"]}
    assert outcomes["v1_default_unchanged"] == "UNCHANGED_COMPATIBILITY_FACADE"
    assert outcomes["v2_missing_or_other_header_value"] == "REQUEST_INVALID"
    assert outcomes["v2_never_falls_back_to_v1"] == "NO_FALLBACK"
    assert outcomes["url_major_mismatches_request_header"] == "REJECT_AND_AUDIT"
    assert outcomes["response_does_not_echo_contract_major"] == "REJECT_AND_AUDIT"
    assert outcomes["attempted_downgrade"] == "REJECT_AND_AUDIT"


def test_old_and_new_authority_do_not_dual_write_same_fact() -> None:
    v4_ownership = yaml.safe_load(V4_OWNERSHIP.read_text(encoding="utf-8"))
    v5_ownership = yaml.safe_load((V5 / "aggregate-ownership.yaml").read_text(encoding="utf-8"))
    compatibility = _load("compatibility.yaml")

    v4_kinds = set(v4_ownership["record_authority"])
    v5_kinds = set(v5_ownership["record_authority"])
    assert v4_kinds.isdisjoint(v5_kinds), v4_kinds & v5_kinds

    # v5 只能通过 explicit bridge 引用 v4 记录，不能新增对同一 fact 的 authority；
    # CONTROLLER_REGISTRATION 是 v4 trust root（无 authority receipt 的注册记录），
    # 不属于 v4 record_authority 事实集合。
    profiles = yaml.safe_load((V5 / "schema-profiles.yaml").read_text(encoding="utf-8"))
    bridge_kinds = set(profiles["common"]["exact_record_binding_v4_bridge"]["kinds"])
    assert bridge_kinds - {"CONTROLLER_REGISTRATION"} <= v4_kinds
    assert bridge_kinds.isdisjoint(v5_kinds)

    assert compatibility["authority_routing_no_dual_write"]["same_fact_dual_write_forbidden"] is True
    assert compatibility["route_authority"]["same_fact_dual_write_forbidden"] is True
    assert compatibility["route_authority"]["one_owner_per_routing_key"] is True
    assert compatibility["api_compatibility"]["v1_and_v2_call_same_governance_kernel"] is True
    assert compatibility["api_compatibility"]["transport_adapters_may_not_create_parallel_authority"] is True


def test_first_slice_error_idempotency_cursor_contract_fail_closed() -> None:
    registry = _load("intent-registry.yaml")
    contract = registry["first_slice_wire_contract"]
    assert contract["slice_name"] == "V5-0C"
    assert contract["status"] == "DRAFT"
    assert contract["runtime_status"] == "NOT_IMPLEMENTED"
    assert contract["transport_generation_all_disabled"] is True
    assert contract["http"]["api_prefix"] == "/api/v2"

    errors = contract["error_contract"]
    assert errors["fail_closed"] is True
    assert errors["error_envelope"] == "contracts/v4/schemas/public-error.schema.json"
    assert (ROOT / "v4" / "schemas" / "public-error.schema.json").exists()
    assert errors["unknown_or_not_allowed_intent"] == "OPAQUE_NOT_FOUND"
    assert errors["unauthorized_resource"] == "OPAQUE_NOT_FOUND"
    assert errors["conflict"] == "CONFLICT_AND_AUDIT"
    assert errors["route_or_version_mismatch"] == "REJECT_AND_AUDIT"
    assert errors["audit_failure_fails_business_transaction"] is True

    idem = contract["idempotency_contract"]
    assert idem["mutation_idempotency_key_required"] is True
    assert idem["same_key_same_request"] == "RETURN_SAME_RECORD_AND_RESPONSE"
    assert idem["same_key_different_request"] == "CONFLICT_AND_AUDIT"
    assert idem["replay_creates_no_second_record"] is True
    assert idem["replay_does_not_duplicate_cross_owner_reaction"] is True
    assert idem["response_includes_replayed_flag"] is True

    cursor = contract["cursor_contract"]
    assert cursor["paginated_queries_require_server_issued_opaque_cursor"] is True
    assert cursor["missing_or_invalid_cursor"] == "REQUEST_INVALID"
    assert cursor["visibility_filter_before_cursor_and_count"] is True
    assert cursor["cursor_does_not_authorize_visibility_bypass"] is True
    # operations.list 是首个带分页的查询 intent，必须已冻结 cursor 语义
    operations_list = next(i for i in registry["intents"] if i["name"] == "operations.list")
    assert operations_list["visibility_filter_before_count_and_pagination"] is True
    assert operations_list["cursor_required_for_pagination"] is True

    allowlist = contract["principal_allowlist_contract"]
    assert allowlist["public"] == ["human", "external_agent", "service", "connector"]
    assert allowlist["internal"] == ["internal_controller", "internal_worker"]
    assert allowlist["trust_roles_are_server_derived"] is True
    assert allowlist["caller_payload_may_assert_trust_role"] is False
    assert allowlist["external_agent_cannot_self_report"] == [
        "owner",
        "declared_deployed_version",
        "observed_state",
        "external_effect",
    ]
    assert allowlist["manifest_import_allowed_roles"] == ["integrator", "catalog_admin", "trusted_builder"]
    assert allowlist["deny_unlisted_principal_type"] is True
    assert allowlist["unauthorized_resource_response"] == "OPAQUE_NOT_FOUND"


def test_first_wire_slice_fixture_grounding_matches_contract() -> None:
    """把 first-wire-slice fixture 的 contract_grounding 喂给真实合同逐字段比对。"""
    fixture = _fixture(V5 / "fixtures" / "first-wire-slice.yaml")
    registry = _load("intent-registry.yaml")
    compatibility = _load("compatibility.yaml")
    grounding = fixture["contract_grounding"]

    compat = grounding["compatibility"]
    assert compat["first_slice"]["status"] == compatibility["first_slice"]["status"]
    assert compat["first_slice"]["count"] == compatibility["first_slice"]["count"]
    assert compat["v5_target_surface"]["first_wire_slice_count"] == len(
        compatibility["v5_target_surface"]["first_wire_slice"]
    )
    assert compat["schema_major_rules"] == {
        key: compatibility["schema_major_rules"][key]
        for key in compat["schema_major_rules"]
    }

    ireg = grounding["intent_registry"]
    assert ireg["first_slice_wire_contract"]["count"] == registry["first_slice_wire_contract"]["count"]
    assert ireg["first_slice_wire_contract"]["transport_generation_all_disabled"] == registry[
        "first_slice_wire_contract"
    ]["transport_generation_all_disabled"]
    selection = registry["api_major_selection"]
    assert ireg["api_major_selection"]["default_api_major"] == selection["default_api_major"]
    assert ireg["api_major_selection"]["v2_requires_explicit_selection"] == selection["v2_requires_explicit_selection"]
    for key, expected in ireg["api_major_selection"]["cli"].items():
        assert selection["cli"][key] == expected, key
    for key, expected in ireg["api_major_selection"]["http"].items():
        assert selection["http"][key] == expected, key
    assert ireg["wire_contract_rules"] == {
        key: registry["wire_contract_rules"][key]
        for key in ireg["wire_contract_rules"]
    }
