"""Frozen Stage 1 OpenAPI and the stage-aware Intent Registry describe one surface."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


V4 = Path(__file__).resolve().parents[1] / "v4"
OPENAPI_PATH = V4 / "openapi" / "public-api.yaml"
REGISTRY_PATH = V4 / "intent-registry.yaml"
HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _operations(document: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    result = {}
    for path, path_item in document["paths"].items():
        for method, operation in path_item.items():
            if method.lower() not in HTTP_METHODS:
                continue
            result[operation["operationId"]] = (method.upper(), path, operation)
    return result


def test_openapi_is_31_and_uses_a_separate_public_major_path() -> None:
    document = _yaml(OPENAPI_PATH)
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"].startswith("1.")
    assert all(path.startswith("/api/v1/") for path in document["paths"])
    assert all(not path.startswith("/v1/") for path in document["paths"])
    assert document["security"] == [{"BearerAuth": []}]


def test_every_frozen_http_intent_has_one_matching_operation_and_skeletons_have_none() -> None:
    openapi = _yaml(OPENAPI_PATH)
    registry = _yaml(REGISTRY_PATH)
    operations = _operations(openapi)
    expected = {
        intent["http"]["operation_id"]: intent
        for intent in registry["intents"]
        if intent["wire_status"] == "FROZEN"
    }
    skeletons = {
        intent["http"]["operation_id"]
        for intent in registry["intents"]
        if intent["wire_status"] == "SKELETON"
    }
    assert set(operations) == set(expected)
    assert set(operations).isdisjoint(skeletons)
    for operation_id, intent in expected.items():
        method, path, operation = operations[operation_id]
        assert method == intent["http"]["method"]
        assert path == intent["http"]["path"]
        assert operation["x-caseloop-intent"] == intent["name"]
        assert operation["x-caseloop-scope"] == intent["scope"]
        assert operation["x-caseloop-wire-status"] == "FROZEN"
        assert operation["x-caseloop-activation-stage"] == intent["activation_stage"]
        assert operation["x-caseloop-delivery-slice"] == intent["delivery_slice"]


def test_every_mutation_requires_idempotency_key_and_a_request_body() -> None:
    openapi = _yaml(OPENAPI_PATH)
    registry = _yaml(REGISTRY_PATH)
    operations = _operations(openapi)
    for intent in registry["intents"]:
        if intent["wire_status"] != "FROZEN":
            continue
        operation = operations[intent["http"]["operation_id"]][2]
        refs = {
            parameter.get("$ref")
            for parameter in operation.get("parameters", [])
            if isinstance(parameter, dict)
        }
        if intent["kind"] == "mutation":
            assert "#/components/parameters/IdempotencyKey" in refs, intent["name"]
            assert operation["requestBody"]["required"] is True
        else:
            assert "#/components/parameters/IdempotencyKey" not in refs, intent["name"]


def test_execution_mode_matches_the_http_success_contract() -> None:
    operations = _operations(_yaml(OPENAPI_PATH))
    for intent in _yaml(REGISTRY_PATH)["intents"]:
        if intent["wire_status"] != "FROZEN":
            continue
        operation = operations[intent["http"]["operation_id"]][2]
        success_codes = {
            str(code)
            for code in operation["responses"]
            if str(code).isdigit() and 200 <= int(code) < 300
        }
        if intent["execution_mode"] == "asynchronous":
            assert success_codes == {"202"}, intent["name"]
        else:
            assert "202" not in success_codes
            assert success_codes, intent["name"]


def test_every_operation_has_machine_readable_public_error_response() -> None:
    for operation_id, (_method, _path, operation) in _operations(_yaml(OPENAPI_PATH)).items():
        assert operation["responses"]["default"] == {
            "$ref": "#/components/responses/PublicError"
        }, operation_id
    public_error = _yaml(OPENAPI_PATH)["components"]["responses"]["PublicError"]
    assert public_error["content"]["application/json"]["schema"]["$ref"] == (
        "../schemas/public-error.schema.json"
    )


def test_every_frozen_operation_requires_workspace_contract_and_auth_context() -> None:
    document = _yaml(OPENAPI_PATH)
    assert document["security"] == [{"BearerAuth": []}]
    assert document["components"]["securitySchemes"]["BearerAuth"][
        "x-caseloop-accepted-context"
    ] == "../schemas/public-principal-context.schema.json"
    required = {
        "#/components/parameters/WorkspaceId",
        "#/components/parameters/ContractVersion",
        "#/components/parameters/RequestId",
        "#/components/parameters/ClientVersion",
    }
    for operation_id, (_method, _path, operation) in _operations(document).items():
        refs = {
            item["$ref"]
            for item in operation.get("parameters", [])
            if "$ref" in item
        }
        assert required <= refs, operation_id


def test_success_and_request_body_refs_equal_the_registry_field_contract() -> None:
    operations = _operations(_yaml(OPENAPI_PATH))
    frozen = [
        intent
        for intent in _yaml(REGISTRY_PATH)["intents"]
        if intent["wire_status"] == "FROZEN"
    ]
    for intent in frozen:
        operation = operations[intent["http"]["operation_id"]][2]
        success = next(
            response
            for code, response in operation["responses"].items()
            if str(code).isdigit() and 200 <= int(code) < 300
        )
        actual_response = success["content"]["application/json"]["schema"]["$ref"]
        expected_response = intent["field_contract_ref"]["response"].replace(
            "contracts/v4/schemas/", "../schemas/"
        )
        assert actual_response == expected_response, intent["name"]
        if intent["kind"] == "mutation":
            actual_request = operation["requestBody"]["content"]["application/json"][
                "schema"
            ]["$ref"]
            expected_request = intent["field_contract_ref"]["request"].replace(
                "contracts/v4/schemas/", "../schemas/"
            )
            assert actual_request == expected_request, intent["name"]


def test_common_errors_are_explicit_and_no_placeholder_envelope_remains() -> None:
    document = _yaml(OPENAPI_PATH)
    registry = {
        intent["http"]["operation_id"]: intent
        for intent in _yaml(REGISTRY_PATH)["intents"]
        if intent["wire_status"] == "FROZEN"
    }
    for operation_id, (_method, _path, operation) in _operations(document).items():
        codes = {str(code) for code in operation["responses"]}
        assert {"400", "401", "403", "412", "429", "503", "default"} <= codes
        if registry[operation_id]["kind"] == "mutation":
            assert {"409", "413", "415", "422"} <= codes
    serialized = OPENAPI_PATH.read_text(encoding="utf-8")
    assert "ResourceEnvelope" not in serialized
    assert "EmptyCommand" not in serialized


def test_external_schema_references_resolve_to_versioned_contract_files() -> None:
    document = _yaml(OPENAPI_PATH)

    def refs(value: Any) -> list[str]:
        if isinstance(value, dict):
            own = [value["$ref"]] if "$ref" in value else []
            return own + [ref for item in value.values() for ref in refs(item)]
        if isinstance(value, list):
            return [ref for item in value for ref in refs(item)]
        return []

    external = [ref for ref in refs(document) if not ref.startswith("#/")]
    assert external
    for ref in external:
        relative = ref.split("#", 1)[0]
        assert (OPENAPI_PATH.parent / relative).resolve().is_file(), ref


def test_public_api_does_not_expose_internal_worker_or_release_execution_tools() -> None:
    paths = set(_yaml(OPENAPI_PATH)["paths"])
    forbidden_fragments = ("/claim", "/heartbeat", "/proposal-decisions", "/gate-results", ":execute")
    assert not any(fragment in path for path in paths for fragment in forbidden_fragments)
    operation_ids = set(_operations(_yaml(OPENAPI_PATH)))
    assert {
        "decideApproval",
        "requestReleaseRollback",
        "getExternalOperation",
        "startInvestigation",
        "getRunView",
        "requestRunStop",
    }.isdisjoint(operation_ids)
