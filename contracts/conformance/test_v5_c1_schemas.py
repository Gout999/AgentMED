"""C1 single-source-wire conformance tests.

Validates the generated JSON Schema 2020-12 wire contracts and the C1 corpus
against them, plus manifest/schema consistency. Run from the repository root
(conformance convention):

    cd contracts
    ../eval-harness/.venv/bin/python -m pytest conformance/test_v5_c1_schemas.py -q

or with any interpreter that has ``jsonschema`` + ``PyYAML`` and
``PYTHONPATH=contracts``.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "contracts/v5/schemas"
CORPUS_DIR = REPO_ROOT / "contracts/v5/corpus"
GENERATED_DIR = REPO_ROOT / "contracts/v5/generated"
PREFIX = "https://caseloop.dev/schemas/v5/"

# date-time/uri formats are enforced (requires rfc3339-validator; see
# conformance/requirements.txt).
FORMAT_CHECKER = FormatChecker()

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
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema_registry() -> Registry:
    registry = Registry()
    for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        document = _load_json(path)
        registry = registry.with_resource(
            PREFIX + path.name, Resource.from_contents(document)
        )
    return registry


def _generated_validator(registry: Registry, name: str, definition: str):
    return Draft202012Validator(
        {"$ref": PREFIX + name + ".schema.json#/$defs/" + definition},
        registry=registry,
        format_checker=FORMAT_CHECKER,
    )


def test_all_v5_schemas_are_meta_valid(schema_registry: Registry) -> None:
    files = sorted(SCHEMAS_DIR.glob("*.schema.json"))
    assert len(files) == 21
    for path in files:
        document = _load_json(path)
        assert document.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
        assert document.get("$id") == PREFIX + path.name
        Draft202012Validator.check_schema(document)


def test_operation_manifest_matches_schemas() -> None:
    manifest = _load_json(GENERATED_DIR / "operation-manifest.json")
    names = [op["intent"] for op in manifest["operations"]]
    assert names == EXPECTED_ACTIVATED_NAMES
    assert manifest["activated_intent_count"] == 19
    for op in manifest["operations"]:
        for definition in ("request", "response", "error"):
            assert (
                f"{PREFIX}{op['intent']}.schema.json#/$defs/{definition}"
                == op["schema"][definition]
            )


def test_capability_manifest_is_exact_19() -> None:
    manifest = _load_json(GENERATED_DIR / "capability-manifest.json")
    assert manifest["enabled_intent_count"] == 19
    assert manifest["disabled_intents"] == []
    names = [entry["name"] for entry in manifest["enabled_intents"]]
    assert names == EXPECTED_ACTIVATED_NAMES
    assert all(entry["http"] is True and entry["cli"] is True for entry in manifest["enabled_intents"])


def _iter_corpus_cases():
    for path in sorted(CORPUS_DIR.glob("*.json")):
        document = _load_json(path)
        intent = document["intent"]
        for case in document["cases"]:
            yield intent, path.name, case


def test_corpus_verdicts_match_generated_schemas(schema_registry: Registry) -> None:
    failures = []
    for intent, file_name, case in _iter_corpus_cases():
        if intent == "error-envelope":
            continue
        definition = case["direction"]
        validator = _generated_validator(schema_registry, intent, definition)
        got = validator.is_valid(case["instance"])
        want = case["expected"] == "valid"
        if got != want:
            failures.append((file_name, case["id"], case["expected"], got, case.get("reason")))
    assert not failures, failures


def test_error_corpus_applies_to_every_activated_intent(
    schema_registry: Registry,
) -> None:
    failures = []
    for intent, file_name, case in _iter_corpus_cases():
        if intent != "error-envelope":
            continue
        for activated in EXPECTED_ACTIVATED_NAMES:
            validator = _generated_validator(schema_registry, activated, "error")
            got = validator.is_valid(case["instance"])
            want = case["expected"] == "valid"
            if got != want:
                failures.append(
                    (activated, case["id"], case["expected"], got, case.get("reason"))
                )
    assert not failures, failures
