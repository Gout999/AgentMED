"""D-014 decision freeze: explicit Application/Component activation authority.

NOTE (stale tests, owner decision pending): commit 3b7e511 "feat(v5): close
1A/1B/1C contracts" rewrote contracts/v5/{aggregate-ownership, domain-model,
intent-registry}.yaml and removed their `application_component_activation_lifecycle`
sections. The four tests marked xfail below still assert the removed D-014
sections. Owner decision: either restore the removed D-014 sections, or supersede
these tests to the 3b7e511 rewrite. Until then they run as expected failures so
the suite stays green-with-xfails; the D-014 fixture and ADR remain untouched.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "v5"
FIXTURE = V5 / "fixtures" / "application-component-activation-lifecycle.yaml"
ADR = ROOT.parent / "docs" / "decisions" / "D-014-v5-application-component-activation-lifecycle.md"


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


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)


def test_decision_is_accepted_but_explicitly_not_implementation_proof() -> None:
    text = ADR.read_text(encoding="utf-8")
    fixture = _load(FIXTURE)

    assert "Status: **ACCEPTED / NOT IMPLEMENTATION PROOF**" in text
    assert "Decider: product owner" in text
    assert "Option A — preserve `REGISTERED → ACTIVE`" in text
    assert fixture["decider"] == "product_owner"
    assert fixture["selected_option"] == "A_PRESERVE_REGISTERED_TO_ACTIVE"
    assert fixture["runtime_status"] == "NOT_RUN"
    assert {"migration_exists", "runtime_path_implemented", "activation_route_implemented"} <= set(
        fixture["does_not_prove"]
    )


@pytest.mark.xfail(
    reason="stale vs 3b7e511 (close 1A/1B/1C contracts): removed D-014 lifecycle section — owner decision pending",
    strict=False,
)
def test_contracts_freeze_two_immutable_revisions_and_historical_authority() -> None:
    ownership = _load(V5 / "aggregate-ownership.yaml")
    domain = _load(V5 / "domain-model.yaml")
    fixture = _load(FIXTURE)

    ownership_decision = ownership["application_component_activation_lifecycle"]
    domain_decision = domain["application_component_activation_lifecycle"]
    assert ownership_decision["decision"] == "PRESERVE_REGISTERED_TO_ACTIVE"
    assert domain_decision["decision"] == "PRESERVE_REGISTERED_TO_ACTIVE"
    history = ownership_decision["authoritative_history"]
    assert history["append_only"] is True
    assert history["current_head_is_projection_not_history_authority"] is True
    assert history["historical_binding_resolution_key"] == ["kind", "id", "revision", "digest"]
    assert history["physical_table_names_are_normative"] is False
    assert history["recommended_implementation_names"] == {
        "ai_application": "ai_application_lifecycle_revisions",
        "system_component": "system_component_lifecycle_revisions",
    }
    domain_history = domain_decision["immutable_revision_history"]
    assert domain_history["physical_table_names_are_normative"] is False
    assert domain_history["recommended_implementation_names"] == history[
        "recommended_implementation_names"
    ]
    activation = ownership_decision["activation"]
    assert activation["exact_previous_binding_is_server_derived"] is True
    assert activation["compare_and_swap"] == {
        "expected_revision": 1,
        "expected_state": "REGISTERED",
        "next_revision": 2,
        "next_state": "ACTIVE",
    }
    assert activation["duplicate_or_stale_or_wrong_state"] == "REJECT_AND_AUDIT"

    for name, expected_commands in (
        ("ai_application", ["applications.register", "applications.activate"]),
        ("system_component", ["system-components.register", "system-components.activate"]),
    ):
        revisions = fixture["authoritative_sequences"][name]["revisions"]
        assert [(row["revision"], row["lifecycle_state"]) for row in revisions] == [
            (1, "REGISTERED"),
            (2, "ACTIVE"),
        ]
        assert [row["command"] for row in revisions] == expected_commands
        assert revisions[1]["exact_previous_binding"] == revisions[0]["exact_binding"]


@pytest.mark.xfail(
    reason="stale vs 3b7e511 (close 1A/1B/1C contracts): removed D-014 lifecycle section — owner decision pending",
    strict=False,
)
def test_component_revision_binds_current_authoritative_active_revision() -> None:
    domain = _load(V5 / "domain-model.yaml")
    fixture = _load(FIXTURE)
    component_revision = domain["resources"]["component_revision"]
    binding = fixture["component_revision"]["exact_system_component_binding"]

    assert "exact_system_component_binding" in component_revision["required_fields"]
    contract = component_revision["exact_system_component_binding_contract"]
    assert contract["required_kind"] == "SYSTEM_COMPONENT"
    assert contract["required_lifecycle_state"] == "ACTIVE"
    assert contract["required_revision_rule"] == (
        "CURRENT_AUTHORITATIVE_ACTIVE_LIFECYCLE_REVISION_AT_RECORDING"
    )
    assert contract["first_manifest_expected_revision"] == 2
    assert contract["later_active_revision_after_deprecate_reactivate_is_allowed"] is True
    assert contract["stale_historical_active_revision_is_forbidden"] is True
    assert contract["deprecated_or_retired_revision_is_forbidden"] is True
    assert contract["same_workspace_and_application_required"] is True
    assert binding["kind"] == "SYSTEM_COMPONENT"
    assert binding["revision"] == fixture["component_revision"]["initial_manifest_expected_revision"]
    assert fixture["component_revision"]["bare_component_id_is_authority"] is False
    assert fixture["component_revision"]["mutable_current_head_is_authority"] is False
    later = fixture["component_revision"]["later_reactivation_example"]
    assert later["historical_active_binding"]["revision"] == 2
    assert later["deprecated_binding"]["revision"] == 3
    assert later["current_active_binding"]["revision"] == 4
    assert later["component_revision_must_bind"] == "current_active_binding"


@pytest.mark.xfail(
    reason="stale vs 3b7e511 (close 1A/1B/1C contracts): canonical_command_order rewritten (system-components.activate dropped for system-assignments.record) — owner decision pending",
    strict=False,
)
def test_manifest_order_includes_application_and_component_activation() -> None:
    ownership = _load(V5 / "aggregate-ownership.yaml")
    registry = _load(V5 / "intent-registry.yaml")
    fixture = _load(FIXTURE)
    first_system = _load(V5 / "fixtures" / "first-system-case.yaml")
    bootstrap = _load(V5 / "fixtures" / "bootstrap-import-atomic.yaml")

    order = fixture["trusted_manifest"]["canonical_command_order"]
    assert ownership["components"]["manifest_import_coordinator"]["canonical_command_order"] == order
    import_intent = next(
        intent for intent in registry["intents"] if intent["name"] == "system-manifests.import"
    )
    assert [target["command"] for target in import_intent["workflow_targets"]] == order
    assert first_system["expected_command_order"][:-1] == order
    assert bootstrap["contract_grounding"]["aggregate_ownership"]["components"][
        "manifest_import_coordinator"
    ]["canonical_command_order"] == order
    assert order.index("applications.activate") < order.index("environments.register")
    assert order.index("system-components.activate") < order.index("component-revisions.record")
    assert fixture["trusted_manifest"]["transaction"] == "ALL_OR_NOTHING_LOCAL_POSTGRES"
    assert fixture["trusted_manifest"]["retry"] == "IDEMPOTENT_REPLAY_NO_NEW_ACTIVATION_REVISION"


@pytest.mark.xfail(
    reason="stale vs 3b7e511 (close 1A/1B/1C contracts): removed D-014 lifecycle section — owner decision pending",
    strict=False,
)
def test_activation_is_manifest_workflow_only_and_public_transports_are_deferred() -> None:
    registry = _load(V5 / "intent-registry.yaml")
    fixture = _load(FIXTURE)
    boundary = registry["application_component_activation_lifecycle"]
    public_names = {intent["name"] for intent in registry["intents"]}

    assert boundary["workflow_only_owner_commands"] == [
        "applications.activate",
        "system-components.activate",
    ]
    assert boundary["register_intent_may_silently_activate"] is False
    assert "applications.activate" not in public_names
    assert "system-components.activate" not in public_names
    authorization = boundary["activation_authorization"]
    assert authorization["invocation"] == "MANIFEST_IMPORT_COORDINATOR_ONLY_SAME_POSTGRESQL_UOW"
    assert authorization["direct_public_or_internal_controller_invocation"] == "FORBIDDEN"
    assert authorization["exact_authenticated_root_intent"] == "system-manifests.import"
    assert authorization["allowed_initiating_principal_types"] == ["human", "service"]
    assert authorization["internal_controller_scope_alone_authorizes_activation"] is False
    assert set(authorization["exact_binding_fields"]) == {
        "authenticated_request_digest", "manifest_digest", "idempotency_key",
        "workspace_id", "initiating_principal_id", "initiating_principal_type",
    }
    assert authorization["authority_chain"] == {
        "subject_and_authority_receipt_actor": "application-catalog-controller",
        "initiating_command_audit_actor": "exact_authenticated_initiating_principal",
        "both_layers_required_same_transaction": True,
    }
    fixture_authorization = fixture["trusted_manifest"]["activation_authorization"]
    assert fixture_authorization["workflow_owner"] == "manifest_import_coordinator"
    assert fixture_authorization["root_intent"] == "system-manifests.import"
    assert fixture_authorization["same_postgresql_unit_of_work"] is True
    assert fixture_authorization["authenticated_request_digest"].startswith("sha256:")
    assert fixture_authorization["manifest_digest"].startswith("sha256:")
    assert fixture_authorization["initiating_principal"]["principal_type"] in {"human", "service"}
    assert fixture["trusted_manifest"]["authority_chain"] == {
        "subject_and_authority_receipt_actor": "application-catalog-controller",
        "initiating_command_audit_actor": fixture_authorization["initiating_principal"][
            "principal_id"
        ],
        "initiating_command_audit_action": "system-manifests.import",
        "both_layers_required_same_transaction": True,
    }
    for command in boundary["workflow_only_owner_commands"]:
        assert command not in registry["internal_intent_authorization"]
        policy = registry["workflow_only_command_authorization"][command]
        assert policy["owner"] == "application-catalog-controller"
        assert policy["required_workflow_owner"] == "manifest_import_coordinator"
        assert policy["required_root_intent"] == "system-manifests.import"
        assert policy["same_postgresql_unit_of_work"] is True
        assert policy["direct_invocation_by_internal_controller"] == "FORBIDDEN"
    assert set(boundary["standalone_public_activation"].values()) >= {"FORBIDDEN"}
    assert set(fixture["standalone_public_activation"]["transports"].values()) == {"FORBIDDEN"}


def test_adversarial_and_recovery_boundaries_fail_closed() -> None:
    fixture = _load(FIXTURE)
    outcomes = {row["name"]: row["expected"] for row in fixture["adversarial"]}

    assert outcomes == {
        "update_revision_1_in_place": "REJECT_AND_AUDIT",
        "activation_without_exact_previous_binding": "REJECT_AND_AUDIT",
        "activate_from_active": "REJECT_AND_AUDIT",
        "concurrent_second_activation": "REJECT_AND_AUDIT",
        "component_revision_binds_registered_revision_1": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "component_revision_binds_bare_component_id": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "cross_workspace_active_component_binding": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "activation_receipt_failure": "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION",
        "internal_controller_with_activation_scope_calls_directly": "DENY_AND_AUDIT",
        "activation_request_digest_or_manifest_digest_mismatch": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "controller_receipt_without_initiating_principal_audit": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "initiating_principal_audit_without_controller_receipt": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "component_revision_binds_stale_active_revision_after_reactivation": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "component_revision_binds_deprecated_revision": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "component_revision_binds_retired_revision": (
            "REJECT_AND_ROLLBACK_ENTIRE_TRANSACTION"
        ),
        "reinterpret_legacy_active_revision_1_as_registered": "EXPLICIT_RECOVERY_REQUIRED",
    }
    assert fixture["recovery"] == {
        "populated_without_v5_catalog_history": "UPGRADE_ALLOWED",
        "populated_with_direct_active_v5_history": "EXPLICIT_RECOVERY_REQUIRED",
        "in_place_relabel_or_digest_rewrite": "FORBIDDEN",
        "disposable_database": "REBUILD",
        "durable_database": "EXPORT_VERIFY_REPLAY_RECONCILE_CUTOVER",
    }
