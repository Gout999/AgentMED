"""D2 complete version-graph contract freeze.

Freezes system-versions.record/get/diff as FROZEN_FOR_IMPLEMENTATION /
NOT_IMPLEMENTED: complete standalone wire, authority, idempotency, lineage/CAS,
event/outbox/audit/receipt semantics, and a non-trivial two-VersionSet diff
fixture. Contract-only: nothing is activated, no transport is generated, and
R3-full capability discovery stays hidden. Run from the repository root
(conformance convention):

    cd contracts
    ../eval-harness/.venv/bin/python -m pytest conformance/test_v5_d2_version_graph_contract.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "v5"
FIXTURE = V5 / "fixtures" / "system-versions-graph.yaml"
SCHEMAS_DIR = V5 / "schemas"
GENERATED_DIR = V5 / "generated"

D2_INTENTS = ["system-versions.record", "system-versions.get", "system-versions.diff"]
ACTIVATED_NAMES = [
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


def _load_yaml(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)


def _load_json(name: str) -> dict:
    return json.loads((GENERATED_DIR / name).read_text(encoding="utf-8"))


def _registry_intent(registry: dict, name: str) -> dict:
    return next(intent for intent in registry["intents"] if intent["name"] == name)


def test_fixture_freeze_metadata_is_contract_only() -> None:
    fixture = _load_yaml(FIXTURE)

    assert fixture["contract_status"] == "FROZEN_FOR_IMPLEMENTATION"
    assert fixture["implementation_status"] == "NOT_IMPLEMENTED"
    assert fixture["runtime_status"] == "NOT_RUN"
    assert fixture["plan_ref"] == "docs/plans/v5-master-execution-plan.md#17.3"
    assert {
        "runtime_path_implemented",
        "transport_route_or_capability_activation",
        "second_version_set_persisted",
        "semantic_diff_runtime",
    } <= set(fixture["does_not_prove"])


def test_three_intents_frozen_but_not_activated() -> None:
    registry = _load_yaml(V5 / "intent-registry.yaml")
    compatibility = _load_yaml(V5 / "compatibility.yaml")

    for name in D2_INTENTS:
        intent = _registry_intent(registry, name)
        assert intent["wire_status"] == "FROZEN_FOR_IMPLEMENTATION"
        assert intent["implementation_status"] == "NOT_IMPLEMENTED"
        assert intent["field_contract_ref"].startswith(
            "contracts/v5/schema-profiles.yaml#d2_wire_profiles/"
        )
        assert intent["http"]["path"].startswith("/api/v2/")
        assert intent["cli_requires_explicit_api_major"] is True

    # 未激活：不在 R2 激活 allowlist，且不进入任何 generated manifest。
    r2_activated = registry["r2_application_catalog_contract"]["activated_contract_intents"]
    assert set(D2_INTENTS).isdisjoint(r2_activated)
    operation_names = [op["intent"] for op in _load_json("operation-manifest.json")["operations"]]
    assert operation_names == ACTIVATED_NAMES
    capability_names = [
        entry["name"] for entry in _load_json("capability-manifest.json")["enabled_intents"]
    ]
    assert capability_names == ACTIVATED_NAMES
    assert set(D2_INTENTS).isdisjoint(capability_names)

    # capability 发现保持隐藏。
    d2 = registry["d2_complete_version_graph_contract"]
    assert d2["activation_status"] == "NOT_ACTIVATED"
    assert set(d2["transports"].values()) == {"FORBIDDEN"}
    assert d2["r3_full_capability_discovery_remains_hidden"] is True
    for name in D2_INTENTS:
        surface = compatibility["d015_development_and_compatibility_lanes"][
            "d2_frozen_contract_surfaces"
        ][name]
        assert surface["contract_status"] == "FROZEN_FOR_IMPLEMENTATION"
        assert surface["implementation_status"] == "NOT_IMPLEMENTED"
        assert set(
            {
                surface["public_http"],
                surface["public_cli"],
                surface["capability_discovery"],
            }
        ) == {"HIDDEN_NOT_ACTIVATED"}


def test_record_references_existing_authority_valid_objects_only() -> None:
    registry = _load_yaml(V5 / "intent-registry.yaml")
    domain = _load_yaml(V5 / "domain-model.yaml")
    fixture = _load_yaml(FIXTURE)

    record_contract = registry["d2_complete_version_graph_contract"]["record_contract"]
    assert record_contract["references_existing_authority_valid_objects_only"] is True
    assert record_contract["never_creates_prerequisite_objects"] is True
    assert record_contract["application_required_state"] == "ACTIVE"
    assert record_contract["environment_required_state"] == "ACTIVE"
    binding_rule = record_contract["component_revision_binding_rule"]
    assert binding_rule["exact_immutable_binding_required"] is True
    assert binding_rule["bare_component_id_is_authority"] is False
    assert binding_rule["mutable_current_head_is_authority"] is False
    assert record_contract["server_derived_fields"] == [
        "identity_assurance_summary",
        "version_set_digest",
    ]
    assert record_contract["creates_immutable_next_system_version_set"] is True

    # 跨文件一致：domain-model 的 standalone record 契约声明一致。
    domain_record = domain["d2_complete_version_graph_contract"]["standalone_record_contract"]
    assert domain_record["references_existing_authority_valid_objects_only"] is True
    assert domain_record["never_creates_prerequisite_objects"] is True
    assert domain_record["component_revision_binding_rule"] == binding_rule

    # fixture：record 只引用 catalog_graph 中已存在的对象。
    catalog_ids = {
        row["component_revision_id"] for row in fixture["catalog_graph"]["component_revisions"]
    }
    for version_set in fixture["version_sets"]:
        for binding in version_set["exact_component_revision_bindings"]:
            assert binding["id"] in catalog_ids


def test_lineage_and_cas_require_exact_previous_binding() -> None:
    registry = _load_yaml(V5 / "intent-registry.yaml")
    fixture = _load_yaml(FIXTURE)

    lineage = registry["d2_complete_version_graph_contract"]["record_contract"]["lineage_and_cas"]
    assert lineage["exact_previous_system_version_set_binding_required_from_second_version"] is True
    assert lineage["expected_previous"] == "CURRENT_AUTHORITATIVE_HEAD"
    assert lineage["stale_or_wrong_previous"] == "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"

    version_sets = fixture["version_sets"]
    assert [vs["revision"] for vs in version_sets] == [1, 2]
    assert version_sets[0]["origin"] == "bootstrap_import"
    assert version_sets[0]["exact_previous_system_version_set_binding_or_null"] is None
    assert version_sets[1]["origin"] == "standalone_record"
    assert (
        version_sets[1]["exact_previous_system_version_set_binding_or_null"]
        == version_sets[0]["exact_binding"]
    )


def test_fixture_is_non_trivial_two_version_set_graph() -> None:
    fixture = _load_yaml(FIXTURE)

    vset_1 = fixture["version_sets"][0]
    vset_2 = fixture["version_sets"][1]

    # 组件输入顺序非字典序（model, web, ... 而非字典序）。
    ids_1 = [b["id"] for b in vset_1["exact_component_revision_bindings"]]
    assert ids_1 != sorted(ids_1)

    ids_2 = [b["id"] for b in vset_2["exact_component_revision_bindings"]]
    assert ids_2 != sorted(ids_2)

    # vset_1: web/model/data；vset_2: web/model@new/tool —— model changed、
    # data removed、tool added、web unchanged（按组件维度推导）。
    revision_to_component = {
        row["component_revision_id"]: row["component_id"]
        for row in fixture["catalog_graph"]["component_revisions"]
    }
    components_1 = {
        revision_to_component[b["id"]] for b in vset_1["exact_component_revision_bindings"]
    }
    components_2 = {
        revision_to_component[b["id"]] for b in vset_2["exact_component_revision_bindings"]
    }
    assert components_1 != components_2
    assert vset_1["exact_topology_revision_binding"]["id"] != vset_2[
        "exact_topology_revision_binding"
    ]["id"]

    expected = fixture["expected_diff"]
    assert {revision_to_component[b["id"]] for b in expected["added"]} == components_2 - components_1
    assert {revision_to_component[b["id"]] for b in expected["removed"]} == components_1 - components_2
    assert len(expected["changed"]) == 1
    changed = expected["changed"][0]
    assert revision_to_component[changed["from_binding"]["id"]] == revision_to_component[
        changed["to_binding"]["id"]
    ]
    assert changed["from_binding"]["id"] in {
        b["id"] for b in vset_1["exact_component_revision_bindings"]
    }
    assert changed["to_binding"]["id"] in {
        b["id"] for b in vset_2["exact_component_revision_bindings"]
    }
    assert expected["deterministic"] is True
    assert expected["not_self_diff"] is True


def test_diff_contract_boundaries_fail_closed() -> None:
    registry = _load_yaml(V5 / "intent-registry.yaml")
    domain = _load_yaml(V5 / "domain-model.yaml")
    fixture = _load_yaml(FIXTURE)

    diff_contract = registry["d2_complete_version_graph_contract"]["diff_contract"]
    assert diff_contract["same_workspace_and_application_required"] is True
    assert diff_contract["cross_workspace_or_application"] == "REJECT_AND_AUDIT"
    assert diff_contract["self_diff"] == "FORBIDDEN"
    assert diff_contract["output_deterministic"] is True
    assert diff_contract["reverse_or_reordered_diff_equivocation"] == "FORBIDDEN"

    domain_diff = domain["d2_complete_version_graph_contract"]["standalone_diff_contract"]
    assert domain_diff["self_diff"] == "FORBIDDEN"
    assert domain_diff["output_deterministic"] is True

    fixture_diff = fixture["diff_contract"]
    assert fixture_diff["self_diff"] == "FORBIDDEN"
    assert fixture_diff["cross_workspace_or_application"] == "REJECT_AND_AUDIT"
    assert fixture_diff["reverse_or_reordered_diff_equivocation"] == "FORBIDDEN"


def test_adversarial_and_recovery_boundaries_fail_closed() -> None:
    fixture = _load_yaml(FIXTURE)
    outcomes = {row["name"]: row["expected"] for row in fixture["adversarial"]}

    assert outcomes == {
        "stale_component_revision_binding": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "stale_topology_revision_binding": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "cross_workspace_application_reference": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "non_active_component_revision_binding": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "bare_id_without_exact_binding": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "wrong_exact_previous_system_version_set_binding": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "same_idempotency_key_different_body": "CONFLICT_WITH_ZERO_NEW_FACTS",
        "missing_required_trust_role": "DENY_AND_AUDIT",
        "tampered_authority_receipt": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "self_diff": "REJECT_AND_AUDIT",
        "cross_application_diff": "REJECT_AND_AUDIT",
        "reverse_diff_equivocation": "REJECT_AND_AUDIT",
        "record_creates_prerequisite_object": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
    }

    assert fixture["recovery"]["in_place_digest_rewrite_or_relabel"] == "FORBIDDEN"
    assert fixture["recovery"]["disposable_database"] == "REBUILD"


def test_events_and_ownership_are_consistent() -> None:
    registry = _load_yaml(V5 / "intent-registry.yaml")
    events = _load_yaml(V5 / "events.yaml")
    ownership = _load_yaml(V5 / "aggregate-ownership.yaml")
    fixture = _load_yaml(FIXTURE)

    # record 的唯一业务事件是 system_version_set.recorded。
    d2_events = events["d2_complete_version_graph_contract"]
    assert d2_events["production_public_intent_events"]["system-versions.record"] == [
        "system_version_set.recorded"
    ]
    assert d2_events["production_public_intent_events"]["system-versions.get"] == []
    assert d2_events["production_public_intent_events"]["system-versions.diff"] == []
    assert d2_events["standalone_record_event_contract"]["event"] == "system_version_set.recorded"
    assert d2_events["standalone_record_event_contract"]["event_version"] == "2.0"
    assert (
        d2_events["standalone_record_event_contract"][
            "exact_previous_system_version_set_binding_included_from_second_version"
        ]
        is True
    )

    # 事件必须与 fixture 的 standalone record 契约一致。
    assert fixture["standalone_record_contract"]["event"] == "system_version_set.recorded"

    # ownership：system_version_set 由 version-controller 拥有。
    assert ownership["record_authority"]["SYSTEM_VERSION_SET"]["owner"] == "version-controller"
    assert ownership["record_authority"]["SYSTEM_VERSION_SET"]["command_events"] == {
        "system-versions.record": ["system_version_set.recorded"]
    }
    assert ownership["resources"]["system_version_set"]["commands"] == ["system-versions.record"]
    assert ownership["resources"]["system_version_set"]["events"] == ["system_version_set.recorded"]


def test_schema_files_exist_with_frozen_definitions() -> None:
    for name in D2_INTENTS:
        path = SCHEMAS_DIR / f"{name}.schema.json"
        assert path.exists(), path
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert document["$id"] == f"https://caseloop.dev/schemas/v5/{name}.schema.json"
        for definition in ("request", "response", "error"):
            assert definition in document["$defs"]

    record = json.loads((SCHEMAS_DIR / "system-versions.record.schema.json").read_text())
    assert record["$defs"]["request"]["required"] == [
        "schema_version",
        "application_id",
        "environment_id",
        "exact_component_revision_bindings",
        "exact_topology_revision_binding",
        "exact_previous_system_version_set_binding_or_null",
    ]
    assert "exact_previous_system_version_set_binding_or_null" in record["$defs"][
        "recordedSystemVersionSet"
    ]["required"]

    diff = json.loads((SCHEMAS_DIR / "system-versions.diff.schema.json").read_text())
    assert diff["$defs"]["query"]["required"] == [
        "source_version_set_id",
        "target_version_set_id",
    ]
    assert diff["$defs"]["versionDiff"]["required"] == [
        "added",
        "removed",
        "changed",
        "topology_changes",
        "assurance_delta",
        "deterministic",
    ]
    assert diff["$defs"]["versionDiff"]["properties"]["deterministic"] == {"const": True}


def test_wire_profile_refs_resolve() -> None:
    registry = _load_yaml(V5 / "intent-registry.yaml")
    profiles = _load_yaml(V5 / "schema-profiles.yaml")

    d2_profiles = profiles["d2_wire_profiles"]
    for name in D2_INTENTS:
        intent = _registry_intent(registry, name)
        profile_key = intent["field_contract_ref"].split("/")[-1]
        assert profile_key in d2_profiles, profile_key

    assert d2_profiles["system_versions_record"]["request_required_fields"] == [
        "schema_version",
        "application_id",
        "environment_id",
        "exact_component_revision_bindings",
        "exact_topology_revision_binding",
        "exact_previous_system_version_set_binding_or_null",
    ]
    assert d2_profiles["system_versions_record"]["authorization"][
        "required_trust_roles_any_of"
    ] == ["integrator", "trusted_builder"]
    assert d2_profiles["system_versions_record"]["authorization"][
        "authorization_before_idempotency_acquire"
    ] is True
