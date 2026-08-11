"""Derive the activated V5 operation set from the intent registry.

C1 single-source rule (v5-architecture-convergence.md#C1): an intent is
activated iff its ``wire_status`` is ``FROZEN_R2`` or
``FROZEN_R2_R3_BOOTSTRAP``. Draft, disabled, deferred and unregistered
operations can never enter compiler output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ACTIVATED_WIRE_STATUSES = frozenset({"FROZEN_R2", "FROZEN_R2_R3_BOOTSTRAP"})

# Fields copied verbatim from the registry as activated-operation metadata.
VERBATIM_FIELDS = (
    "contract_major",
    "delivery_slice",
    "wire_status",
    "implementation_status",
    "kind",
    "execution_mode",
    "scope",
    "allowed_principal_types",
    "required_trust_roles_any_of",
    "trust_roles_source",
    "authorization_condition",
    "idempotency",
    "pagination",
    "command_target",
    "workflow_owner",
    "cli",
    "cli_requires_explicit_api_major",
)


def load_intent_registry(path: Path) -> dict[str, Any]:
    """Load and sanity-check the V5 intent registry."""
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict) or not isinstance(data.get("intents"), list):
        raise ValueError(f"invalid intent registry: {path}")
    names = [intent.get("name") for intent in data["intents"]]
    if len(names) != len(set(names)):
        raise ValueError("intent registry contains duplicate intent names")
    return data


def activated_intents(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the activated intents in registry order."""
    return [
        intent
        for intent in registry["intents"]
        if intent.get("wire_status") in ACTIVATED_WIRE_STATUSES
    ]


def operation_metadata(intent: dict[str, Any]) -> dict[str, Any]:
    """Normalize one intent into compiler operation metadata (fail closed)."""
    name = intent.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("intent is missing a name")
    operation: dict[str, Any] = {"intent": name}
    for field in VERBATIM_FIELDS:
        if field in intent:
            operation[field] = intent[field]
    http = intent.get("http")
    if (
        not isinstance(http, dict)
        or not isinstance(http.get("method"), str)
        or not isinstance(http.get("path"), str)
    ):
        raise ValueError(f"activated intent {name} requires http method/path")
    operation["http"] = {
        "method": http["method"],
        "path": http["path"],
        "operation_id": http.get("operation_id"),
    }
    if isinstance(http.get("query_parameters"), dict):
        operation["http"]["query_parameters"] = http["query_parameters"]
    return operation
