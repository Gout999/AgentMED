"""Build the deterministic C1 operation and capability manifests.

Fail-closed integrity: every activated intent must resolve to one JSON Schema
2020-12 contract (request/response/error ``$defs``) before it may enter output.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .activated_operations import (
    ACTIVATED_WIRE_STATUSES,
    activated_intents,
    operation_metadata,
)

SCHEMA_PREFIX = "https://caseloop.dev/schemas/v5/"
MANIFEST_SCHEMA_VERSION = "1.0"


def schema_uri(intent_name: str) -> str:
    return f"{SCHEMA_PREFIX}{intent_name}.schema.json"


def _validate_activated_schema(intent_name: str, schemas_dir: Path) -> None:
    path = schemas_dir / f"{intent_name}.schema.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"activated intent {intent_name} has no JSON Schema 2020-12 contract: {path}"
        )
    document = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(document)
    definitions = document.get("$defs", {})
    for required_def in ("request", "response", "error"):
        if required_def not in definitions:
            raise ValueError(
                f"activated intent {intent_name} schema missing $defs/{required_def}"
            )


def build_operation_manifest(
    registry: dict[str, Any], schemas_dir: Path
) -> dict[str, Any]:
    """Build the deterministic activated-operation manifest."""
    operations = []
    for intent in activated_intents(registry):
        name = intent["name"]
        _validate_activated_schema(name, schemas_dir)
        operation = operation_metadata(intent)
        operation["schema"] = {
            "request": f"{schema_uri(name)}#/$defs/request",
            "response": f"{schema_uri(name)}#/$defs/response",
            "error": f"{schema_uri(name)}#/$defs/error",
        }
        operations.append(operation)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_by": "caseloop-v5-compiler",
        "source": {
            "intent_registry": "contracts/v5/intent-registry.yaml",
            "wire_schemas": "contracts/v5/schemas/*.schema.json",
        },
        "activation_rule": {
            "activated_wire_statuses": sorted(ACTIVATED_WIRE_STATUSES),
            "draft_or_deferred_intents_excluded": True,
            "not_a_route_or_capability_activation": True,
        },
        "activated_intent_count": len(operations),
        "operations": operations,
    }


def build_capability_manifest(
    operation_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Build the candidate capability manifest from the activated-operation set.

    Mirrors the enabled-intent metadata of
    ``control-plane/app/services/v5_capabilities.py``
    (name/scope/execution_mode with http=true and cli=true); it is the C1
    candidate single source that C4 will cut capability discovery over.
    """
    enabled = []
    for operation in operation_manifest["operations"]:
        enabled.append(
            {
                "name": operation["intent"],
                "scope": operation["scope"],
                "execution_mode": operation["execution_mode"],
                "http": True,
                "cli": True,
            }
        )
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_by": "caseloop-v5-compiler",
        "source": {"operation_manifest": "contracts/v5/generated/operation-manifest.json"},
        "enabled_intent_count": len(enabled),
        "enabled_intents": enabled,
        "disabled_intents": [],
    }
