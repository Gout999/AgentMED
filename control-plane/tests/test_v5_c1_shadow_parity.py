"""C1 shadow dual validation: legacy Pydantic models vs generated 2020-12 schemas.

Runs the shared C1 corpus through BOTH validators (legacy
``control-plane/app/public_api`` models and the generated
``contracts/v5/schemas/*.schema.json``) and asserts they agree with each other
and with the corpus's expected verdict. A mismatch is recorded as a test
failure — it is never silently coerced (v5-architecture-convergence.md#C1).

Coverage matrix (Task E / C4): every ``(intent, direction)`` pair that appears
in the corpus is accounted for explicitly — either a legacy Pydantic model
(``LEGACY_MODELS``) or a declared legacy-N/A direction
(``LEGACY_NA_DIRECTIONS``) such as ``applications.list/query`` and GET
"request" (no body).  Legacy-N/A keeps the skip semantics (no legacy
determination is attempted) but every corpus case still runs the generated
validator, and the matrix itself is asserted exactly so a future corpus case
can never fall through both validators silently.

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

# The 11 C1 activated intents (contracts/v5/generated/operation-manifest.json).
ACTIVATED_INTENTS = frozenset(
    {
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
    }
)

# GET intents: HTTP GET with no request body; a "request" corpus direction can
# never have a legacy Pydantic body model (legacy N/A by construction).
_GET_NO_BODY_INTENTS = frozenset(
    {
        "capabilities.get",
        "applications.get",
        "environments.get",
        "system-components.get",
        "dependency-edges.get",
    }
)

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

# Explicit legacy-N/A declarations: directions without a legacy Pydantic body
# model.  Skip semantics preserved (no legacy determination attempted), but
# the declaration makes the coverage matrix complete and the generated-side
# validation still runs on every corpus case.
LEGACY_NA_DIRECTIONS = {
    ("applications.list", "query"): (
        "query-only intent: no legacy Pydantic body model; generated "
        "$defs/query validates the query-parameter shape"
    ),
    ("capabilities.get", "request"): "GET has no request body (legacy N/A)",
    ("applications.get", "request"): "GET has no request body (legacy N/A)",
    ("environments.get", "request"): "GET has no request body (legacy N/A)",
    ("system-components.get", "request"): "GET has no request body (legacy N/A)",
    ("dependency-edges.get", "request"): "GET has no request body (legacy N/A)",
}

LEGACY_ERROR_MODEL = public_errors.PublicErrorEnvelope


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _corpus_directions() -> dict[tuple[str, str], int]:
    """(intent, direction) -> number of corpus cases, across all files."""
    matrix: dict[tuple[str, str], int] = {}
    for path in sorted(CORPUS_DIR.glob("*.json")):
        document = _load_json(path)
        intent = document["intent"]
        for case in document["cases"]:
            key = (intent, case["direction"])
            matrix[key] = matrix.get(key, 0) + 1
    return matrix


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


# ---------------------------------------------------------------------------
# Coverage matrix
# ---------------------------------------------------------------------------


# The corpus also carries the cross-cutting error envelope as its own file;
# it is mapped by LEGACY_ERROR_MODEL and is not an activated intent.
ERROR_ENVELOPE_KEY = ("error-envelope", "error")


def test_coverage_matrix_accounts_for_every_corpus_direction() -> None:
    """Every corpus (intent, direction) maps to a legacy model or a declared N/A."""
    matrix = _corpus_directions()
    assert len(matrix) == 18, f"unexpected corpus direction count: {sorted(matrix)}"
    for key in sorted(matrix):
        assert key in LEGACY_MODELS or key in LEGACY_NA_DIRECTIONS or key == ERROR_ENVELOPE_KEY, (
            f"corpus direction {key} has no coverage-matrix entry "
            f"({matrix[key]} case(s))"
        )


def test_coverage_matrix_covers_all_activated_intents() -> None:
    """All 11 activated intents have at least one covered direction."""
    matrix = _corpus_directions()
    covered = {intent for intent, _ in matrix} - {"error-envelope"}
    assert covered == ACTIVATED_INTENTS, (
        f"corpus intents {sorted(covered - ACTIVATED_INTENTS)} not activated; "
        f"activated intents {sorted(ACTIVATED_INTENTS - covered)} missing from corpus"
    )
    mapped = set(LEGACY_MODELS) | set(LEGACY_NA_DIRECTIONS)
    assert {intent for intent, _ in mapped} == ACTIVATED_INTENTS


def test_legacy_na_declarations_are_exact() -> None:
    """Declared N/A directions are exactly: list/query plus GET no-body request."""
    matrix = _corpus_directions()
    na_with_cases = sorted(set(LEGACY_NA_DIRECTIONS) & set(matrix))
    assert na_with_cases == [("applications.list", "query")], (
        f"unexpected N/A direction with corpus cases: {na_with_cases}"
    )
    na_without_cases = sorted(set(LEGACY_NA_DIRECTIONS) - set(matrix))
    assert na_without_cases == sorted(
        (intent, "request") for intent in sorted(_GET_NO_BODY_INTENTS)
    ), f"unexpected declared-absent N/A directions: {na_without_cases}"
    # The only corpus direction without a legacy model is list/query
    # (error-envelope is mapped by LEGACY_ERROR_MODEL).
    unmapped = sorted(set(matrix) - set(LEGACY_MODELS) - {ERROR_ENVELOPE_KEY})
    assert unmapped == [("applications.list", "query")], unmapped
    # No stale legacy mapping for a direction the corpus never exercises.
    for key in LEGACY_MODELS:
        assert key in matrix, f"stale legacy mapping {key}"


def test_legacy_na_schemas_expose_the_direction_definition() -> None:
    """Declared N/A directions resolve to a generated $defs/<direction>."""
    registry = Registry()
    documents: dict[str, dict] = {}
    for path in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        document = _load_json(path)
        documents[path.name] = document
        registry = registry.with_resource(
            PREFIX + path.name, Resource.from_contents(document)
        )
    for (intent, direction), _reason in LEGACY_NA_DIRECTIONS.items():
        schema_path = f"{intent}.schema.json"
        assert direction in documents[schema_path].get("$defs", {}), (
            f"generated schema {schema_path} lacks $defs/{direction} "
            f"needed for declared N/A direction"
        )


# ---------------------------------------------------------------------------
# Shadow parity
# ---------------------------------------------------------------------------


def test_legacy_and_generated_agree_on_corpus(schema_registry: Registry) -> None:
    mismatches = []
    for intent, case in _corpus_cases():
        direction = case["direction"]
        if intent == "error-envelope":
            legacy_model = LEGACY_ERROR_MODEL
            legacy_na = False
            validator = _generated_validator(schema_registry, "capabilities.get", "error")
        else:
            key = (intent, direction)
            legacy_model = LEGACY_MODELS.get(key)
            legacy_na = key in LEGACY_NA_DIRECTIONS
            if legacy_model is None and not legacy_na:
                # Unreachable while the matrix tests pass, but never silent:
                # an unmapped corpus case is a hard failure with its own row.
                mismatches.append(
                    {
                        "id": case["id"],
                        "intent": intent,
                        "direction": direction,
                        "expected": case["expected"],
                        "legacy_valid": None,
                        "generated_valid": None,
                        "reason": "no coverage-matrix entry (legacy N/A not declared)",
                    }
                )
                continue
            validator = _generated_validator(schema_registry, intent, direction)
        if legacy_model is None:
            # Legacy N/A: skip semantics preserved — no legacy determination
            # is attempted; the generated side must still match the corpus.
            generated_valid = validator.is_valid(case["instance"])
            expected = case["expected"] == "valid"
            if generated_valid != expected:
                mismatches.append(
                    {
                        "id": case["id"],
                        "intent": intent,
                        "direction": direction,
                        "expected": case["expected"],
                        "legacy_valid": "N/A",
                        "generated_valid": generated_valid,
                        "reason": case.get("reason"),
                    }
                )
            continue
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
