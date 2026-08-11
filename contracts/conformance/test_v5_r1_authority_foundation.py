"""R1 contract candidate: lifecycle history, major-2 events, and exact authority.

This suite freezes machine-readable R1 constraints only.  It deliberately does
not treat the contract candidate as migration, runtime, route, or R2 evidence.
"""
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "v5"


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


def _closed_revision_shape_accepts(
    profile: dict, variant_name: str, values: dict
) -> bool:
    variant = profile["revision_shape_union"]["one_of"][variant_name]
    required = set(profile["required_fields"]) | set(variant["required_fields"])
    optional = set(profile.get("optional_fields", []))
    forbidden = set(profile.get("forbidden_fields", [])) | set(
        variant.get("forbidden_fields", [])
    )
    keys = set(values)
    if not required <= keys or keys & forbidden:
        return False
    if keys - required - optional:
        return False
    return all(values.get(field) == expected for field, expected in variant.get(
        "constants", {}
    ).items())


def test_r1_slice_separates_implemented_foundation_from_unimplemented_composition() -> None:
    for name in ("events.yaml", "state-machines.yaml", "schema-profiles.yaml"):
        document = _load(name)
        r1 = document["r1_authority_foundation"]
        # The file-wide target still contains later disabled V5 surfaces.  R1's
        # semantic subject therefore reports its implemented foundation separately.
        assert document["runtime_status"] == "NOT_IMPLEMENTED"
        assert r1["stage"] == "R1"
        assert r1["contract_status"] == "CANDIDATE"
        assert r1["r1_foundation_runtime_status"] == (
            "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
        )
        assert r1["revision_2_storage_cas_harness"] == (
            "NON_PRODUCTION_IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
        )
        assert r1["composed_manifest_activation_runtime"] == "NOT_IMPLEMENTED"
        assert r1["composed_manifest_activation_owner_stage"] == "R2"
        assert r1["production_activated_event_routes"] == "DISABLED"
        assert r1["production_direct_revision_2_append"] == "DENY_ALL"
        assert r1["component_revision_current_active_resolver_runtime"] == (
            "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
        )
        assert r1["component_revision_major_2_envelope_runtime"] == (
            "NOT_IMPLEMENTED"
        )
        assert r1["component_revision_writer_validation_runtime"] == (
            "NOT_IMPLEMENTED"
        )
        assert r1["component_revision_producer_atomic_enforcement_runtime"] == (
            "NOT_IMPLEMENTED"
        )
        assert r1["component_revision_producer_owner_stage"] == "R3"
        assert r1["implemented_lifecycle_primitives"] == {
            "ai_application": [
                "registration_revision_1",
                "revision_2_storage_cas_non_production_harness",
            ],
            "system_component": [
                "registration_revision_1",
                "revision_2_storage_cas_non_production_harness",
            ],
        }
        assert r1["later_lifecycle_transitions_runtime"] == "NOT_IMPLEMENTED"
        assert r1["later_lifecycle_transitions_owner_stage"] == "R2"
        assert set(r1["later_lifecycle_transitions"]) == {
            "application.archived",
            "application.restored",
            "system_component.deprecated",
            "system_component.reactivated",
            "system_component.retired",
        }
        assert r1["public_activation_routes"] == "DISABLED"
        assert "post_commit_verifier_or_evidence_closure" in r1["does_not_prove"]
        assert "composed_manifest_activation_runtime" in r1["does_not_prove"]
        assert "component_revision_producer_atomic_enforcement" in r1[
            "does_not_prove"
        ]
        assert "component_revision_major_2_envelope_runtime" in r1[
            "does_not_prove"
        ]
        assert "component_revision_writer_validation_runtime" in r1[
            "does_not_prove"
        ]
        assert "public_activation_transport_or_route" in r1["does_not_prove"]
        assert "r2_application_catalog_closure" in r1["does_not_prove"]
        assert "DONE" not in str(r1)
        assert "PASS" not in str(r1)

    assert _load("schema-profiles.yaml")["route_status"] == "DISABLED"


def test_major_2_lifecycle_envelope_binds_new_revision_not_mutable_head() -> None:
    events = _load("events.yaml")
    envelope = events["event_envelope_v5"]
    lifecycle = envelope["lifecycle_revision_rules"]

    assert events["event_contract_major"] == 2
    assert envelope["constants"] == {"event_contract_major": 2}
    assert envelope["subject_binding_is_the_post_event_immutable_revision"] is True
    assert lifecycle == {
        "exact_subject_binding": "exact_new_post_event_lifecycle_revision",
        "exact_previous_binding": (
            "payload_only_and_server_derived_from_locked_authoritative_history"
        ),
        "previous_and_new_share_kind_id_and_workspace": True,
        "new_revision_equals_previous_revision_plus_one": True,
        "new_record_digest_is_independently_canonical": True,
        "current_head_projection_is_not_event_or_binding_authority": True,
    }
    assert events["rules"]["v4_event_payloads_are_not_reinterpreted"] is True
    assert events["rules"][
        "lifecycle_events_are_append_only_and_never_rebind_historical_envelopes"
    ] is True


def test_application_and_component_activation_events_are_manifest_only() -> None:
    events = _load("events.yaml")

    cases = (
        (
            events["ai_application"]["events"]["application.activated"],
            "exact_previous_application_binding",
            "exact_application_binding",
            "exact_previous_application_binding_is_locked_current_registered_revision_1",
            "exact_application_binding_is_new_revision_2",
        ),
        (
            events["system_component"]["events"]["system_component.activated"],
            "exact_previous_system_component_binding",
            "exact_system_component_binding",
            "exact_previous_system_component_binding_is_locked_current_registered_revision_1",
            "exact_system_component_binding_is_new_revision_2",
        ),
    )
    for event, previous, new, previous_guard, new_guard in cases:
        assert event["event_version"] == "2.0"
        assert event["composed_runtime_status"] == "NOT_IMPLEMENTED"
        assert event["composed_runtime_owner_stage"] == "R2"
        assert event["production_route_status"] == "DISABLED"
        assert event["direct_production_append"] == "FORBIDDEN"
        assert event["r1_validation_scope"] == (
            "STRUCTURAL_CONTRACT_ONLY_NON_PRODUCTION"
        )
        assert event["constants"] == {"lifecycle_state": "ACTIVE"}
        assert {
            previous,
            new,
            "manifest_activation_context",
            "initiating_command_audit_ref",
        } <= set(event["payload_required"])
        assert {
            previous_guard,
            new_guard,
            "manifest_activation_context_is_exact_authenticated_system_manifest_import",
            "initiating_command_audit_ref_equals_manifest_activation_context_audit_ref",
            "application_catalog_controller_receipt_and_initiating_principal_audit_share_transaction",
            "direct_public_or_internal_controller_activation_is_forbidden",
        } <= set(event["guards"])

    later = {
        "application.archived": events["ai_application"]["events"][
            "application.archived"
        ],
        "application.restored": events["ai_application"]["events"][
            "application.restored"
        ],
        "system_component.deprecated": events["system_component"]["events"][
            "system_component.deprecated"
        ],
        "system_component.reactivated": events["system_component"]["events"][
            "system_component.reactivated"
        ],
        "system_component.retired": events["system_component"]["events"][
            "system_component.retired"
        ],
    }
    assert all(event["runtime_status"] == "NOT_IMPLEMENTED" for event in later.values())
    assert all(event["runtime_owner_stage"] == "R2" for event in later.values())


def test_lifecycle_machines_require_append_only_adjacent_revision_cas() -> None:
    machines = _load("state-machines.yaml")
    foundation = machines["r1_authority_foundation"]
    assert foundation["lifecycle_authority"] == (
        "APPEND_ONLY_POSTGRESQL_REVISION_HISTORY"
    )
    assert foundation["current_head_role"] == (
        "PROJECTION_ONLY_NOT_HISTORICAL_AUTHORITY"
    )
    assert foundation["transition_concurrency"] == (
        "LOCK_CURRENT_HEAD_AND_COMPARE_AND_SWAP"
    )
    assert foundation["activation_invocation"] == (
        "MANIFEST_IMPORT_COORDINATOR_ONLY_SAME_POSTGRESQL_UOW"
    )

    for name in ("ai_application", "system_component"):
        machine = machines["machines"][name]
        history = machine["revision_history"]
        assert history["initial_revision"] == {
            "revision": 1,
            "state": "REGISTERED",
            "exact_previous_binding": None,
        }
        assert history["activation_revision"] == {
            "revision": 2,
            "state": "ACTIVE",
            "exact_previous_revision": 1,
        }
        assert history["exact_resolution_key"] == ["kind", "id", "revision", "digest"]
        assert history["current_head_is_projection_not_history_authority"] is True
        assert history["every_transition_creates_new_adjacent_revision"] is True
        assert history[
            "every_non_initial_revision_exactly_binds_locked_previous_revision"
        ] is True
        assert machine["failure_semantics"]["missing_or_stale_previous_binding"] == (
            "REJECT_AND_AUDIT"
        )
        assert machine["failure_semantics"]["receipt_event_audit_or_revision_failure"] == (
            "ROLLBACK_ENTIRE_TRANSACTION"
        )


def test_schema_profiles_require_dual_activation_authority_in_one_transaction() -> None:
    common = _load("schema-profiles.yaml")["common"]
    context = common["manifest_activation_context_v5"]
    audit = common["initiating_command_audit_v5"]
    receipt = common["authority_receipt_v5"]
    transaction = common["lifecycle_activation_transaction_v5"]

    assert context["constants"] == {
        "root_intent": "system-manifests.import",
        "workflow_owner": "manifest_import_coordinator",
    }
    assert context["initiating_principal_type_values"] == ["human", "service"]
    assert {
        "authenticated_request_digest",
        "manifest_digest",
        "idempotency_key",
        "workspace_id",
        "initiating_principal_id",
        "initiating_principal_type",
        "initiating_command_audit_ref",
    } <= set(context["required_fields"])
    assert "internal_controller_scope_alone_is_not_authority" in context["invariants"]
    assert audit["constants"] == {"action": "system-manifests.import"}
    assert audit["same_transaction_as_subject_event_and_authority_receipt"] is True
    assert receipt["additional_properties"] is False
    assert "manifest_activation_context" not in receipt["required_fields"]
    assert "initiating_command_audit_ref" not in receipt["required_fields"]
    assert transaction["field_refs"]["controller_authority_receipt"] == (
        "authority_receipt_v5"
    )
    assert set(transaction["receipt_constraints"]["allowed_commands"]) == {
        "applications.activate",
        "system-components.activate",
    }
    assert transaction["receipt_constraints"]["controller_principal"] == (
        "application-catalog-controller"
    )
    assert transaction["receipt_constraints"][
        "receipt_schema_remains_closed_without_activation_context_or_initiating_audit_fields"
    ] is True
    assert transaction["audit_constraints"] == {
        "actor": "exact_authenticated_initiating_principal",
        "action": "system-manifests.import",
        "distinct_from_controller_authority_receipt": True,
    }
    assert transaction["transaction_constraints"][
        "all_required_artifacts_share_transaction_id"
    ] is True
    assert "neither_authority_layer_may_substitute_for_the_other" in transaction[
        "invariants"
    ]


def test_lifecycle_record_profiles_use_mutually_exclusive_closed_previous_shapes() -> None:
    profiles = _load("schema-profiles.yaml")["profiles"]
    for name, initial_field, transition_field in (
        (
            "ai_application",
            "exact_previous_application_binding_or_null",
            "exact_previous_application_binding",
        ),
        (
            "system_component",
            "exact_previous_system_component_binding_or_null",
            "exact_previous_system_component_binding",
        ),
    ):
        profile = profiles[name]
        union = profile["revision_shape_union"]
        assert union["closed_shape_rule"] == (
            "base_required_plus_optional_plus_selected_variant_only"
        )
        initial = union["one_of"]["INITIAL_REGISTERED"]
        transition = union["one_of"]["NON_INITIAL_TRANSITION"]
        assert initial["required_fields"] == [initial_field]
        assert initial["constants"] == {initial_field: None}
        assert initial["forbidden_fields"] == [transition_field]
        assert transition["required_fields"] == [transition_field]
        assert transition["forbidden_fields"] == [initial_field]
        assert initial["additional_properties"] is False
        assert transition["additional_properties"] is False
        assert profile["activation_revision_contract"]["exact_previous_field"] == (
            transition_field
        )
        assert profile["activation_revision_contract"]["r1_runtime_scope"] == (
            "NON_PRODUCTION_STORAGE_CAS_HARNESS_ONLY"
        )
        assert profile["activation_revision_contract"]["production_runtime_status"] == (
            "NOT_IMPLEMENTED"
        )
        assert profile["activation_revision_contract"][
            "production_runtime_owner_stage"
        ] == "R2"

        base = {field: "value" for field in profile["required_fields"]}
        initial_values = {**base, initial_field: None}
        transition_values = {**base, transition_field: {"revision": 1}}
        assert _closed_revision_shape_accepts(
            profile, "INITIAL_REGISTERED", initial_values
        )
        assert _closed_revision_shape_accepts(
            profile, "NON_INITIAL_TRANSITION", transition_values
        )

        # Counterexamples: wrong field for the selected revision, missing previous,
        # both spellings, and non-null initial previous all fail the closed union.
        assert not _closed_revision_shape_accepts(
            profile, "INITIAL_REGISTERED", {**base, transition_field: {"revision": 1}}
        )
        assert not _closed_revision_shape_accepts(
            profile, "NON_INITIAL_TRANSITION", base
        )
        assert not _closed_revision_shape_accepts(
            profile,
            "NON_INITIAL_TRANSITION",
            {**base, initial_field: None, transition_field: {"revision": 1}},
        )
        assert not _closed_revision_shape_accepts(
            profile, "INITIAL_REGISTERED", {**base, initial_field: {"revision": 0}}
        )


def test_component_revision_binds_exact_current_authoritative_active_history() -> None:
    profiles = _load("schema-profiles.yaml")
    binding = profiles["common"][
        "current_authoritative_active_system_component_binding_v5"
    ]
    component_revision = profiles["profiles"]["component_revision"]
    recorded = _load("events.yaml")["component_revision"]["events"][
        "component_revision.recorded"
    ]

    assert binding["base_profile"] == "exact_record_binding_v5:SYSTEM_COMPONENT"
    assert binding["r1_resolution_runtime_status"] == (
        "IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER"
    )
    assert binding["producer_atomic_enforcement_runtime"] == "NOT_IMPLEMENTED"
    assert binding["producer_owner_stage"] == "R3"
    assert binding["resolution_source"] == "APPEND_ONLY_LIFECYCLE_HISTORY"
    assert binding["required_lifecycle_state"] == "ACTIVE"
    assert binding["required_revision_rule"] == (
        "CURRENT_AUTHORITATIVE_ACTIVE_LIFECYCLE_REVISION_AT_RECORDING"
    )
    assert binding["first_manifest_expected_revision"] == 2
    assert binding["later_active_revision_after_deprecate_reactivate_is_allowed"] is True
    assert {
        "stale_historical_active_revision",
        "deprecated_revision",
        "retired_revision",
        "registered_revision",
        "mutable_current_head_projection_without_exact_history",
    } <= set(binding["forbidden"])
    assert component_revision["field_refs"]["exact_system_component_binding"] == (
        "current_authoritative_active_system_component_binding_v5"
    )
    assert component_revision["producer_runtime_status"] == "NOT_IMPLEMENTED"
    assert component_revision["producer_owner_stage"] == "R3"
    assert set(component_revision["r1_available_primitives"]) == {
        "current_authoritative_active_history_resolution",
        "exact_binding_digest_validation",
        "generic_exact_record_binding_validation",
    }
    assert component_revision["event_envelope_runtime_status"] == "NOT_IMPLEMENTED"
    assert component_revision["event_envelope_owner_stage"] == "R3"
    assert component_revision["writer_validation_runtime_status"] == "NOT_IMPLEMENTED"
    assert component_revision["writer_validation_owner_stage"] == "R3"
    assert {
        "exact_system_component_binding_is_revalidated_at_recording",
        "stale_historical_active_or_non_active_binding_fails_closed",
        "dependent_record_event_receipt_and_binding_validation_share_transaction",
    } <= set(component_revision["r3_required_producer_invariants"])
    assert recorded["producer_runtime_status"] == "NOT_IMPLEMENTED"
    assert recorded["producer_owner_stage"] == "R3"
    assert recorded["event_envelope_runtime_status"] == "NOT_IMPLEMENTED"
    assert recorded["event_envelope_owner_stage"] == "R3"
    assert recorded["writer_validation_runtime_status"] == "NOT_IMPLEMENTED"
    assert recorded["writer_validation_owner_stage"] == "R3"
    assert set(recorded["r1_available_primitives"]) == {
        "current_authoritative_active_exact_binding_resolver",
        "generic_exact_record_binding_validation",
    }
    assert all("envelope" not in primitive for primitive in recorded[
        "r1_available_primitives"
    ])
    assert {
        "exact_system_component_binding_resolves_from_append_only_history",
        "exact_system_component_binding_is_current_authoritative_active_at_recording",
        "stale_historical_active_deprecated_retired_registered_or_mutable_head_binding_is_forbidden",
    } <= set(recorded["r3_required_producer_guards"])
