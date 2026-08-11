"""C1 shadow dual validation: legacy Pydantic models vs generated 2020-12 schemas.

Runs the shared C1 corpus through BOTH validators (legacy
``control-plane/app/public_api`` models and the generated
``contracts/v5/schemas/*.schema.json``) and asserts they agree with each other
and with the corpus's expected verdict. A mismatch is recorded as a test
failure — it is never silently coerced (v5-architecture-convergence.md#C1).

Run from the control-plane directory with a Python that can import the app
models (pydantic + sqlalchemy):

    cd control-plane
    /tmp/c1-venv/bin/python -m pytest tests/test_v5_c1_shadow_parity.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
from referencing import Registry, Resource

from app.public_api import errors as public_errors
from app.public_api import v5_models as v5m
from app.public_api.v5_capability_models import V5ServerCapabilitiesResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "contracts/v5/schemas"
CORPUS_DIR = REPO_ROOT / "contracts/v5/corpus"
PREFIX = "https://caseloop.dev/schemas/v5/"

# date-time/uri formats are enforced (requires rfc3339-validator).
FORMAT_CHECKER = FormatChecker()

# intent -> legacy request/response model for the activated R2 surface.
LEGACY_MODELS = {
    ("capabilities.get", "response"): V5ServerCapabilitiesResponse,
    ("applications.register", "request"): v5m.ApplicationRegisterRequest,
    ("applications.register", "response"): v5m.ApplicationRegisterResponse,
    ("applications.get", "response"): v5m.ApplicationGetResponse,
    ("applications.list", "response"): v5m.ApplicationListResponse,
    ("environments.register", "request"): v5m.EnvironmentRegisterRequest,
    ("environments.register", "response"): v5m.EnvironmentRegisterResponse,
    ("environments.get", "response"): v5m.EnvironmentGetResponse,
    ("system-components.register", "request"): v5m.ComponentRegisterRequest,
    ("system-components.register", "response"): v5m.ComponentRegisterResponse,
    ("system-components.get", "response"): v5m.ComponentGetResponse,
    ("dependency-edges.record", "request"): v5m.DependencyEdgeRecordRequest,
    ("dependency-edges.record", "response"): v5m.DependencyEdgeRecordResponse,
    ("dependency-edges.get", "response"): v5m.DependencyEdgeGetResponse,
    ("system-manifests.import", "request"): v5m.SystemManifestImportRequest,
    ("system-manifests.import", "response"): v5m.SystemManifestImportResponse,
}
LEGACY_ERROR_MODEL = public_errors.PublicErrorEnvelope


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


def _corpus_cases():
    for path in sorted(CORPUS_DIR.glob("*.json")):
        document = _load_json(path)
        intent = document["intent"]
        for case in document["cases"]:
            yield intent, case


def test_legacy_and_generated_agree_on_corpus(schema_registry: Registry) -> None:
    mismatches = []
    for intent, case in _corpus_cases():
        direction = case["direction"]
        if intent == "error-envelope":
            legacy_model = LEGACY_ERROR_MODEL
            validator = _generated_validator(schema_registry, "capabilities.get", "error")
        else:
            key = (intent, direction)
            legacy_model = LEGACY_MODELS.get(key)
            if legacy_model is None:
                # e.g. applications.list "query" direction or GET "request": no
                # legacy Pydantic body model; generated-side coverage is enough.
                continue
            validator = _generated_validator(schema_registry, intent, direction)
        try:
            legacy_model.model_validate(case["instance"])
            legacy_valid = True
        except ValidationError:
            legacy_valid = False
        generated_valid = validator.is_valid(case["instance"])
        expected = case["expected"] == "valid"
        if legacy_valid != generated_valid or legacy_valid != expected:
            mismatches.append(
                {
                    "id": case["id"],
                    "intent": intent,
                    "direction": direction,
                    "expected": case["expected"],
                    "legacy_valid": legacy_valid,
                    "generated_valid": generated_valid,
                    "reason": case.get("reason"),
                }
            )
    assert not mismatches, mismatches
