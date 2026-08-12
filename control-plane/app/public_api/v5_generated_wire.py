"""Fail-closed V5 generated-wire validation.

The JSON Schema 2020-12 artifacts are the structural wire authority.  Pydantic
models remain a second, stricter semantic layer for invariants that JSON Schema
cannot express (for example graph membership and cross-field equality).  A
payload must pass both layers; there is no generated/legacy winner or fallback.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_DIR = _REPO_ROOT / "contracts" / "v5" / "schemas"

_REQUEST_INTENTS = {
    "ApplicationRegisterRequest": "applications.register",
    "EnvironmentRegisterRequest": "environments.register",
    "ComponentRegisterRequest": "system-components.register",
    "DependencyEdgeRecordRequest": "dependency-edges.record",
    "SystemManifestImportRequest": "system-manifests.import",
    "SystemVersionRecordRequest": "system-versions.record",
}

_RESPONSE_INTENTS = {
    "V5ServerCapabilitiesResponse": "capabilities.get",
    "ApplicationRegisterResponse": "applications.register",
    "ApplicationGetResponse": "applications.get",
    "ApplicationListResponse": "applications.list",
    "EnvironmentRegisterResponse": "environments.register",
    "EnvironmentGetResponse": "environments.get",
    "ComponentRegisterResponse": "system-components.register",
    "ComponentGetResponse": "system-components.get",
    "DependencyEdgeRecordResponse": "dependency-edges.record",
    "DependencyEdgeGetResponse": "dependency-edges.get",
    "SystemManifestImportResponse": "system-manifests.import",
    "SystemVersionRecordResponse": "system-versions.record",
    "SystemVersionGetResponse": "system-versions.get",
    "SystemVersionDiffResponse": "system-versions.diff",
}


class GeneratedWireValidationError(ValueError):
    """Stable internal marker for generated structural-wire rejection."""

    def __init__(self, *, intent: str, direction: str, fields: list[str]) -> None:
        self.intent = intent
        self.direction = direction
        self.fields = fields
        super().__init__(f"generated V5 {direction} schema rejected {intent}")


@lru_cache(maxsize=None)
def _registry() -> Registry:
    resources = []
    for path in sorted(_SCHEMA_DIR.glob("*.schema.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        resources.append((document["$id"], Resource.from_contents(document)))
    return Registry().with_resources(resources)


@lru_cache(maxsize=None)
def _validator(intent: str, direction: Literal["request", "response"]):
    path = _SCHEMA_DIR / f"{intent}.schema.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    schema = {
        "$schema": document["$schema"],
        "$ref": f"{document['$id']}#/$defs/{direction}",
    }
    return Draft202012Validator(
        schema,
        registry=_registry(),
        format_checker=FormatChecker(),
    )


def validate_generated_wire(
    *,
    model_name: str,
    direction: Literal["request", "response"],
    payload: dict[str, Any],
) -> None:
    mapping = _REQUEST_INTENTS if direction == "request" else _RESPONSE_INTENTS
    intent = mapping.get(model_name)
    if intent is None:
        # R3/R4 model shells are deliberately unregistered and have no C1
        # activated-operation schema.  Their transport remains disabled.
        return
    errors = sorted(
        _validator(intent, direction).iter_errors(payload),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if not errors:
        return
    fields = sorted(
        {
            ".".join(str(part) for part in error.absolute_path) or direction
            for error in errors
        }
    )
    raise GeneratedWireValidationError(
        intent=intent,
        direction=direction,
        fields=fields,
    )


__all__ = ["GeneratedWireValidationError", "validate_generated_wire"]
