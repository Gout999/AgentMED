"""The intent registry is the single product-level transport mapping."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

import yaml


REGISTRY = Path(__file__).resolve().parents[1] / "v4" / "intent-registry.yaml"
OWNERSHIP = Path(__file__).resolve().parents[1] / "v4" / "aggregate-ownership.yaml"
PUBLIC_WIRE = Path(__file__).resolve().parents[1] / "v4" / "schemas" / "public-api-wire.schema.json"


def _document() -> dict:
    return yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))


def _ownership_document() -> dict:
    return yaml.safe_load(OWNERSHIP.read_text(encoding="utf-8"))


def test_intent_and_operation_identities_are_unique() -> None:
    intents = _document()["intents"]
    names = [intent["name"] for intent in intents]
    operation_ids = [intent["http"]["operation_id"] for intent in intents]
    assert len(names) == len(set(names))
    assert len(operation_ids) == len(set(operation_ids))
    assert all(intent["http"]["path"].startswith("/api/v1/") for intent in intents)


def test_mutations_require_public_command_idempotency_but_queries_do_not() -> None:
    for intent in _document()["intents"]:
        if intent["kind"] == "mutation":
            assert intent["idempotency"] == "required", intent["name"]
        else:
            assert intent["kind"] == "query"
            assert intent["idempotency"] == "none", intent["name"]


def test_execution_modes_are_machine_readable_and_frozen() -> None:
    asynchronous = {
        "sources.doctor",
        "investigations.start",
        "work.stop-request",
        "releases.rollback-request",
    }
    for intent in _document()["intents"]:
        assert intent["execution_mode"] in {"synchronous", "asynchronous"}
        assert (intent["execution_mode"] == "asynchronous") == (
            intent["name"] in asynchronous
        ), intent["name"]


def test_transport_activation_stages_do_not_imply_early_mcp_or_a2a_support() -> None:
    for intent in _document()["intents"]:
        stages = intent["transport_stages"]
        assert set(stages) == {"http", "cli", "mcp", "a2a"}, intent["name"]
        assert stages["http"] == intent["first_stage"], intent["name"]
        assert stages["cli"] == intent["first_stage"], intent["name"]
        for transport in ("mcp", "a2a"):
            mapping = intent[transport]
            if mapping is None:
                assert stages[transport] is None, intent["name"]
            else:
                assert stages[transport] == 6, intent["name"]


def test_signal_submit_is_canonical_and_report_is_only_an_alias() -> None:
    intents = _document()["intents"]
    canonical = [intent["cli"] for intent in intents]
    aliases = [alias for intent in intents for alias in intent["cli_aliases"]]
    assert len(canonical) == len(set(canonical))
    assert len(aliases) == len(set(aliases))
    assert set(canonical).isdisjoint(aliases)
    signal = next(intent for intent in intents if intent["name"] == "signals.submit")
    assert signal["cli"] == "signal submit"
    assert signal["cli_aliases"] == ["report"]
    assert all(intent["cli_aliases"] == [] for intent in intents if intent is not signal)


def test_public_mutations_have_exactly_one_owned_command_target() -> None:
    ownership = _ownership_document()
    assert ownership["rules"]["public_mutation_has_explicit_command_target"] is True
    resources = ownership["resources"]
    command_owners: dict[str, list[str]] = defaultdict(list)
    for resource_name, resource in resources.items():
        for command in resource.get("commands", []):
            command_owners[command].append(resource_name)

    for intent in _document()["intents"]:
        if intent["kind"] == "query":
            assert "command_target" not in intent, intent["name"]
            continue

        target = intent.get("command_target")
        assert isinstance(target, dict), intent["name"]
        assert set(target) == {"resource", "command"}, intent["name"]
        resource_name = target["resource"]
        command = target["command"]
        assert resource_name in resources, intent["name"]
        assert resources[resource_name]["kind"] != "projection", intent["name"]
        assert command in resources[resource_name]["commands"], intent["name"]
        assert command_owners[command] == [resource_name], intent["name"]


def test_cross_domain_public_requests_target_narrow_aggregates() -> None:
    intents = {intent["name"]: intent for intent in _document()["intents"]}
    assert intents["sources.doctor"]["command_target"] == {
        "resource": "source_sync_run",
        "command": "source-sync-runs.request-doctor",
    }
    assert intents["investigations.start"]["command_target"] == {
        "resource": "automation_request",
        "command": "automation-requests.start-investigation",
    }
    assert intents["work.stop-request"]["command_target"] == {
        "resource": "automation_request",
        "command": "automation-requests.request-stop",
    }
    assert intents["releases.rollback-request"]["command_target"] == {
        "resource": "external_operation",
        "command": "external-operations.request-rollback",
    }


def test_stage_one_registry_is_the_frozen_http_cli_vertical_slice() -> None:
    stage_one = {
        intent["name"] for intent in _document()["intents"] if intent["first_stage"] == 1
    }
    assert stage_one == {
        "capabilities.get",
        "signals.submit",
        "cases.get",
        "cases.timeline",
        "evidence.get",
        "sources.capabilities",
        "sources.doctor",
        "source-sync-runs.get",
    }


def test_wire_freeze_is_stage_sliced_and_skeletons_have_no_field_contract() -> None:
    intents = {intent["name"]: intent for intent in _document()["intents"]}
    expected_s1a = {
        "capabilities.get",
        "signals.submit",
        "cases.get",
        "cases.timeline",
        "evidence.get",
    }
    expected_s1b = {
        "sources.capabilities",
        "sources.doctor",
        "source-sync-runs.get",
    }
    assert {
        name for name, intent in intents.items() if intent["delivery_slice"] == "S1A"
    } == expected_s1a
    assert {
        name for name, intent in intents.items() if intent["delivery_slice"] == "S1B"
    } == expected_s1b
    for intent in intents.values():
        assert intent["activation_stage"] == intent["first_stage"]
        if intent["wire_status"] == "FROZEN":
            assert set(intent["field_contract_ref"]) == {"request", "response"}
        else:
            assert intent["wire_status"] == "SKELETON"
            assert intent["field_contract_ref"] is None
            assert intent["delivery_slice"] is None


def test_capability_discovery_schema_allows_only_frozen_intents() -> None:
    intents = _document()["intents"]
    frozen = {intent["name"] for intent in intents if intent["wire_status"] == "FROZEN"}
    skeleton = {intent["name"] for intent in intents if intent["wire_status"] == "SKELETON"}
    schema = json.loads(PUBLIC_WIRE.read_text(encoding="utf-8"))
    allowlist = set(
        schema["$defs"]["server_capabilities_response"]["properties"]["data"]
        ["properties"]["enabled_intents"]["items"]["properties"]["name"]["enum"]
    )
    assert allowlist == frozen
    assert allowlist.isdisjoint(skeleton)


def test_async_intents_have_a_durable_query_companion_before_activation() -> None:
    intents = {intent["name"]: intent for intent in _document()["intents"]}
    assert intents["sources.doctor"]["first_stage"] == intents["source-sync-runs.get"]["first_stage"] == 1
    assert intents["releases.rollback-request"]["first_stage"] == intents["external-operations.get"]["first_stage"] == 4


def test_human_approval_is_not_exposed_to_agent_protocols() -> None:
    intents = {intent["name"]: intent for intent in _document()["intents"]}
    approval = intents["approvals.decide"]
    assert approval["allowed_principal_types"] == ["human"]
    assert approval["scope"] == "approvals:decide:human"
    assert approval["mcp"] is None
    assert approval["a2a"] is None


def test_governed_agent_is_a_resource_not_a_caller_principal() -> None:
    document = _document()
    assert "governed_agent" in document["non_principal_resource_types"]
    assert "governed_agent" not in document["principal_types"]
    for principal in ("external_agent", "service", "connector"):
        forbidden = set(document["forbidden_public_scopes"][principal])
        assert {
            "approvals:decide:human",
            "external_operations:execute:internal",
        } <= forbidden


def test_internal_micro_operations_are_absent_from_public_intents() -> None:
    document = _document()
    public = {intent["name"] for intent in document["intents"]}
    assert public.isdisjoint(document["internal_only_intents"])
    remote_cli = {
        command
        for intent in document["intents"]
        for command in [intent["cli"], *intent["cli_aliases"]]
    }
    assert remote_cli.isdisjoint(document["local_only_commands"])
