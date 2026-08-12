"""C1 activated-operation compiler tests.

Run from the repository root with the contracts path on sys.path:

    PYTHONPATH=contracts python3 -m pytest contracts/compiler/tests -q

Coverage: exact 14-intent allowlist, deterministic regeneration, schema
referential integrity, registry metadata parity, capability manifest shape and
the no-side-effect import rule (convergence plan C1 verification list).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from compiler.emit import SCHEMAS_DIR, REPO_ROOT, emit
from compiler.activated_operations import load_intent_registry
from compiler.manifest import build_capability_manifest, build_operation_manifest

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
]

FORBIDDEN_IMPORT_MARKERS = (
    "app.",
    "sqlalchemy",
    "fastapi",
    "pydantic",
    "psycopg",
    "httpx",
    "requests",
    "django",
    "flask",
    "torch",
)


@pytest.fixture(scope="module")
def registry() -> dict:
    return load_intent_registry(REPO_ROOT / "contracts/v5/intent-registry.yaml")


@pytest.fixture(scope="module")
def operation_manifest(registry: dict) -> dict:
    return build_operation_manifest(registry, SCHEMAS_DIR)


def test_activated_allowlist_is_exact_14(operation_manifest: dict) -> None:
    names = [op["intent"] for op in operation_manifest["operations"]]
    assert names == EXPECTED_ACTIVATED_NAMES
    assert operation_manifest["activated_intent_count"] == 14


def test_draft_and_unregistered_intents_are_excluded(operation_manifest: dict) -> None:
    names = {op["intent"] for op in operation_manifest["operations"]}
    for excluded in (
        "cases.bind-application",
        "acceptance-criteria.propose",
        "acceptance-criteria.confirm",
    ):
        assert excluded not in names


def test_every_operation_resolves_schema(operation_manifest: dict) -> None:
    for op in operation_manifest["operations"]:
        schema = op["schema"]
        assert schema["request"].endswith("#/$defs/request")
        assert schema["response"].endswith("#/$defs/response")
        assert schema["error"].endswith("#/$defs/error")
        name = op["intent"]
        for pointer in ("request", "response", "error"):
            document = json.loads(
                (SCHEMAS_DIR / f"{name}.schema.json").read_text(encoding="utf-8")
            )
            assert pointer in document["$defs"]


def test_deterministic_regeneration(tmp_path: Path) -> None:
    first = emit(tmp_path)
    second_dir = tmp_path / "again"
    second = emit(second_dir)
    for key in first:
        assert first[key].read_bytes() == second[key].read_bytes()
    # Re-emit into the same directory is byte-identical as well.
    emit(tmp_path)
    assert first["operation"].read_bytes() == second["operation"].read_bytes()


def test_operation_metadata_parity_with_registry(
    registry: dict, operation_manifest: dict
) -> None:
    registry_by_name = {intent["name"]: intent for intent in registry["intents"]}
    for op in operation_manifest["operations"]:
        intent = registry_by_name[op["intent"]]
        assert op["http"]["method"] == intent["http"]["method"]
        assert op["http"]["path"] == intent["http"]["path"]
        assert op["http"]["operation_id"] == intent["http"]["operation_id"]
        assert op["scope"] == intent["scope"]
        assert op["idempotency"] == intent["idempotency"]
        assert op["execution_mode"] == intent["execution_mode"]
        assert op["allowed_principal_types"] == intent["allowed_principal_types"]


def test_capability_manifest_shape(operation_manifest: dict) -> None:
    capability_manifest = build_capability_manifest(operation_manifest)
    assert capability_manifest["enabled_intent_count"] == 14
    assert capability_manifest["disabled_intents"] == []
    by_name = {op["intent"]: op for op in operation_manifest["operations"]}
    for entry in capability_manifest["enabled_intents"]:
        operation = by_name[entry["name"]]
        assert entry["http"] is True
        assert entry["cli"] is True
        assert entry["scope"] == operation["scope"]
        assert entry["execution_mode"] == operation["execution_mode"]


def test_compiler_imports_are_side_effect_free() -> None:
    compiler_root = Path(__file__).resolve().parents[1]
    for source in sorted(compiler_root.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for marker in FORBIDDEN_IMPORT_MARKERS:
                    assert marker not in module, (
                        f"{source.name} must not import {module}"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    for marker in FORBIDDEN_IMPORT_MARKERS:
                        assert marker not in alias.name, (
                            f"{source.name} must not import {alias.name}"
                        )
