"""C4 emitter tests: OpenAPI 3.1 surface and the applications.list TS module.

Run from the repository root with the contracts path on sys.path:

    PYTHONPATH=contracts python3 -m pytest contracts/compiler/tests -q

Coverage: deterministic regeneration (bytes), the exact 23-operation
activated surface with no inactive intent, path/query parameter emission,
external-schema refs, the omitted securitySchemes (C4 decision), the TS
module's interface/guard/pattern extraction from the frozen schemas, and the
emit() artifact set (convergence plan C4 verification list for task A).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from compiler.emit import REPO_ROOT, SCHEMAS_DIR, emit
from compiler.activated_operations import load_intent_registry
from compiler.manifest import build_operation_manifest
from compiler.emitters import emit_ts_application_list, emit_v5_openapi

EXPECTED_ACTIVATED_NAMES = [
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
    "system-versions.record",
    "system-versions.get",
    "system-versions.diff",
    "cases.bind-application",
    "case-application-bindings.get",
    "acceptance-criteria.propose",
    "acceptance-criteria.confirm",
    "acceptance-criteria.get",
    "investigations.start",
    "operations.get",
    "operations.list",
    "operations.cancel-request",
]

INACTIVE_INTENTS = (
    "system-episodes.get",
)

TS_EXPECTED_INTERFACES = (
    "ApplicationCatalogList",
    "ApplicationCatalogItem",
    "ApplicationRecord",
    "EnvironmentRecord",
    "ComponentRecord",
    "EdgeRecord",
    "Envelope",
    "ExactBinding",
)

TS_EXPECTED_GUARDS = (
    "applicationCatalogList",
    "applicationCatalogItem",
    "applicationRecord",
    "environmentRecord",
    "componentRecord",
    "edgeRecord",
    "envelope",
)


@pytest.fixture(scope="module")
def operation_manifest() -> dict:
    registry = load_intent_registry(REPO_ROOT / "contracts/v5/intent-registry.yaml")
    return build_operation_manifest(registry, SCHEMAS_DIR)


@pytest.fixture(scope="module")
def openapi_document(operation_manifest: dict) -> dict:
    return emit_v5_openapi(operation_manifest, SCHEMAS_DIR)


@pytest.fixture(scope="module")
def ts_module() -> str:
    return emit_ts_application_list(SCHEMAS_DIR)


def _all_operations(document: dict) -> list[tuple[str, str, dict]]:
    found = []
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            found.append((path, method, operation))
    return found


def test_openapi_is_deterministic(tmp_path: Path) -> None:
    first = emit(tmp_path)
    second = emit(tmp_path / "again")
    assert first["openapi"].read_bytes() == second["openapi"].read_bytes()
    assert first["ts"].read_bytes() == second["ts"].read_bytes()
    # Re-emit into the same directory is byte-identical as well.
    emit(tmp_path)
    assert first["openapi"].read_bytes() == second["openapi"].read_bytes()
    assert first["ts"].read_bytes() == second["ts"].read_bytes()


def test_openapi_covers_all_23_activated_operations(
    operation_manifest: dict, openapi_document: dict
) -> None:
    operations = _all_operations(openapi_document)
    assert len(operations) == 23
    intents = [operation["x-caseloop-intent"] for _, _, operation in operations]
    assert len(set(intents)) == 23
    # Path grouping reorders operations with the same path template; the
    # surface must match the activated set, not the manifest ordering.
    assert sorted(intents) == sorted(EXPECTED_ACTIVATED_NAMES)

    manifest_by_intent = {op["intent"]: op for op in operation_manifest["operations"]}
    for path, method, operation in operations:
        intent = operation["x-caseloop-intent"]
        http = manifest_by_intent[intent]["http"]
        assert path == http["path"]
        assert method == http["method"].lower()
        assert operation["operationId"] == http["operation_id"]
        assert operation["x-caseloop-scope"] == manifest_by_intent[intent]["scope"]
        assert operation["x-caseloop-wire-status"] == manifest_by_intent[intent][
            "wire_status"
        ]
        assert operation["x-caseloop-delivery-slice"] == manifest_by_intent[intent][
            "delivery_slice"
        ]


def test_openapi_excludes_inactive_intents(
    openapi_document: dict, operation_manifest: dict
) -> None:
    text = yaml.safe_dump(openapi_document, sort_keys=False)
    activated = {op["intent"] for op in operation_manifest["operations"]}
    for intent in _all_operations(openapi_document):
        assert intent[2]["x-caseloop-intent"] in activated
    for intent in INACTIVE_INTENTS:
        assert intent not in text


def test_openapi_path_and_query_parameters(openapi_document: dict) -> None:
    by_intent = {
        operation["x-caseloop-intent"]: operation
        for _, _, operation in _all_operations(openapi_document)
    }

    get_application = by_intent["applications.get"]["parameters"]
    assert {"name": "application_id", "in": "path", "required": True} == {
        "name": get_application[0]["name"],
        "in": get_application[0]["in"],
        "required": get_application[0]["required"],
    }
    assert get_application[0]["schema"]["pattern"] == "^app_[0-9A-Za-z]{8,64}$"

    list_parameters = by_intent["applications.list"]["parameters"]
    by_name = {parameter["name"]: parameter for parameter in list_parameters}
    assert by_name["project_id"]["in"] == "query"
    assert by_name["project_id"]["required"] is True
    assert (
        by_name["project_id"]["schema"]["$ref"]
        == "../schemas/common.schema.json#/$defs/idProjectId"
    )
    assert by_name["cursor"]["required"] is False
    assert (
        by_name["cursor"]["schema"]["$ref"]
        == "../schemas/records.schema.json#/$defs/applicationListCursor"
    )
    assert by_name["limit"]["required"] is False
    assert by_name["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
    }

    for intent, pattern in (
        ("environments.get", "^env_[0-9A-Za-z]{8,64}$"),
        ("system-components.get", "^cmp_[0-9A-Za-z]{8,64}$"),
        ("dependency-edges.get", "^de_[0-9A-Za-z]{8,64}$"),
        ("system-versions.get", "^vset_[0-9A-Za-z]{8,64}$"),
    ):
        parameters = by_intent[intent]["parameters"]
        assert parameters[0]["required"] is True
        assert parameters[0]["schema"]["pattern"] == pattern

    diff_parameters = by_intent["system-versions.diff"]["parameters"]
    diff_by_name = {parameter["name"]: parameter for parameter in diff_parameters}
    assert set(diff_by_name) == {"source_version_set_id", "target_version_set_id"}
    for name in ("source_version_set_id", "target_version_set_id"):
        assert diff_by_name[name]["in"] == "query"
        assert diff_by_name[name]["required"] is True
        assert (
            diff_by_name[name]["schema"]["$ref"]
            == "../schemas/common.schema.json#/$defs/idSystemVersionSetId"
        )

    # R4 first-system-case surface: case_id path parameter on the three case
    # routes, acceptance_criteria_revision_id on confirm, and the exact-case
    # query parameters on the two read intents.
    for intent in (
        "cases.bind-application",
        "case-application-bindings.get",
        "acceptance-criteria.propose",
        "acceptance-criteria.get",
    ):
        parameters = by_intent[intent]["parameters"]
        case_param = next(p for p in parameters if p["name"] == "case_id")
        assert case_param["in"] == "path"
        assert case_param["required"] is True
        assert case_param["schema"]["pattern"] == "^case_[0-9A-Za-z]{8,64}$"

    confirm_parameters = by_intent["acceptance-criteria.confirm"]["parameters"]
    revision_param = confirm_parameters[0]
    assert revision_param["name"] == "acceptance_criteria_revision_id"
    assert revision_param["in"] == "path"
    assert revision_param["required"] is True
    assert revision_param["schema"]["pattern"] == "^acr_[0-9A-Za-z]{8,64}$"

    binding_parameters = by_intent["case-application-bindings.get"]["parameters"]
    binding_by_name = {parameter["name"]: parameter for parameter in binding_parameters}
    assert set(binding_by_name) == {"case_id", "case_revision", "case_digest"}
    assert binding_by_name["case_revision"]["in"] == "query"
    assert binding_by_name["case_revision"]["required"] is True
    assert binding_by_name["case_revision"]["schema"] == {
        "type": "integer",
        "minimum": 1,
    }
    assert binding_by_name["case_digest"]["in"] == "query"
    assert binding_by_name["case_digest"]["required"] is True
    assert (
        binding_by_name["case_digest"]["schema"]["$ref"]
        == "../schemas/common.schema.json#/$defs/digest"
    )

    acceptance_parameters = by_intent["acceptance-criteria.get"]["parameters"]
    acceptance_by_name = {parameter["name"]: parameter for parameter in acceptance_parameters}
    assert set(acceptance_by_name) == {"case_id", "case_revision"}
    assert acceptance_by_name["case_revision"]["in"] == "query"
    assert acceptance_by_name["case_revision"]["required"] is True
    assert acceptance_by_name["case_revision"]["schema"] == {
        "type": "integer",
        "minimum": 1,
    }


def test_openapi_schema_refs_and_request_body(openapi_document: dict) -> None:
    by_intent = {
        operation["x-caseloop-intent"]: operation
        for _, _, operation in _all_operations(openapi_document)
    }
    for intent, kind, success_status in (
        ("applications.register", "mutation", "201"),
        ("environments.register", "mutation", "201"),
        ("system-components.register", "mutation", "201"),
        ("dependency-edges.record", "mutation", "201"),
        ("system-manifests.import", "mutation", "201"),
        ("applications.get", "query", "200"),
        ("applications.list", "query", "200"),
        ("capabilities.get", "query", "200"),
    ):
        operation = by_intent[intent]
        if kind == "mutation":
            request_ref = operation["requestBody"]["content"]["application/json"][
                "schema"
            ]["$ref"]
            assert request_ref == f"../schemas/{intent}.schema.json#/$defs/request"
        else:
            assert "requestBody" not in operation
        response_ref = operation["responses"][success_status]["content"][
            "application/json"
        ]["schema"]["$ref"]
        assert response_ref == f"../schemas/{intent}.schema.json#/$defs/response"
        error_ref = operation["responses"]["default"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        assert error_ref == f"../schemas/{intent}.schema.json#/$defs/error"


def test_openapi_has_no_security_schemes_yet(openapi_document: dict) -> None:
    # No components block and no top-level security key; the C4 decision to
    # omit securitySchemes is carried in the document description instead.
    assert "security" not in openapi_document
    assert "components" not in openapi_document
    assert "C4 decision" in openapi_document["info"]["description"]
    assert (
        "securitySchemes is intentionally not emitted"
        in openapi_document["info"]["description"]
    )


def test_ts_module_is_deterministic_and_basic(ts_module: str) -> None:
    assert ts_module
    for interface in TS_EXPECTED_INTERFACES:
        assert f"interface {interface} " in ts_module
    for guard in TS_EXPECTED_GUARDS:
        assert f"const {guard}: Guard<" in ts_module
    assert "export const guards" in ts_module
    assert "exactKeys" in ts_module
    assert "revision === 1" in ts_module
    assert "revision === revision - 1" in ts_module
    assert "const WORKSPACE_ID = /^ws_[0-9A-Za-z]{8,64}$/" in ts_module
    assert "const SHA256_DIGEST = /^sha256:[0-9a-f]{64}$/" in ts_module


def test_ts_enum_unions_match_schema(ts_module: str) -> None:
    common = json.loads(
        (SCHEMAS_DIR / "common.schema.json").read_text(encoding="utf-8")
    )["$defs"]
    for def_name in ("criticality", "componentKind", "dependencyRelation"):
        alias = def_name[0].upper() + def_name[1:]
        expected = " | ".join(
            json.dumps(value) for value in common[def_name]["enum"]
        )
        assert f"export type {alias} = {expected};" in ts_module


def test_ts_pattern_consts_match_schema(ts_module: str) -> None:
    common = json.loads(
        (SCHEMAS_DIR / "common.schema.json").read_text(encoding="utf-8")
    )["$defs"]
    records = json.loads(
        (SCHEMAS_DIR / "records.schema.json").read_text(encoding="utf-8")
    )["$defs"]
    expected = {
        "WORKSPACE_ID": common["idWorkspaceId"]["pattern"],
        "PROJECT_ID": common["idProjectId"]["pattern"],
        "PRINCIPAL_ID": common["idPrincipalId"]["pattern"],
        "APPLICATION_ID": common["idApplicationId"]["pattern"],
        "ENVIRONMENT_ID": common["idEnvironmentId"]["pattern"],
        "COMPONENT_ID": common["idComponentId"]["pattern"],
        "EDGE_ID": common["idEdgeId"]["pattern"],
        "REQUEST_ID": common["idRequestId"]["pattern"],
        "AUTHORITY_RECEIPT_ID": common["idAuthorityReceiptId"]["pattern"],
        "AUDIT_REF": common["auditRef"]["pattern"],
        "SHA256_DIGEST": common["digest"]["pattern"],
        "SLUG": common["slug"]["pattern"],
        "LOGICAL_NAME": common["logicalName"]["pattern"],
        "CURSOR": records["applicationListCursor"]["pattern"],
    }
    emitted = {}
    for line in ts_module.splitlines():
        match = re.match(r"^const ([A-Z0-9_]+) = /(.+)/;$", line)
        if match:
            emitted[match.group(1)] = match.group(2).replace(r"\/", "/")
    for const_name, pattern in expected.items():
        assert emitted[const_name] == pattern


def test_ts_guards_map_exports_all_surfaces(ts_module: str) -> None:
    for guard in (*TS_EXPECTED_GUARDS, "exactBinding: isExactBinding"):
        assert f"  {guard}," in ts_module
    assert "  exactBinding: isExactBinding," in ts_module
    assert 'exactBinding(value, "AI_APPLICATION", APPLICATION_ID)' in ts_module
    assert 'exactBinding(value, "SYSTEM_COMPONENT", COMPONENT_ID)' in ts_module


def test_emit_writes_all_artifacts(tmp_path: Path) -> None:
    written = emit(tmp_path)
    assert set(written) == {"operation", "capability", "openapi", "ts"}
    assert written["openapi"].is_file()
    assert written["ts"].is_file()
    assert written["ts"].name == "applications.list.ts"
    assert written["ts"].parent.name == "ts"
    assert yaml.safe_load(written["openapi"].read_text(encoding="utf-8"))[
        "openapi"
    ] == "3.1.0"
