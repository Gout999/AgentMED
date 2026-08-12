"""C1 activated-operation manifest loader for the CLI v2 surface (C4).

Derives the CLI's v2 command/wire metadata from the C1 generated artifact
``contracts/v5/generated/operation-manifest.json``.  Discovery mirrors
``control-plane/app/services/v5_capabilities.py``: candidate roots are derived
from this module's location (upward walk to the repo root, where
``contracts/v5/generated/operation-manifest.json`` lives), the same deployment
candidates are checked, and an explicit override is accepted for tests.

Fallback: when no candidate carries the manifest (for example a pip-installed
CLI outside the repository), a frozen literal table replicating the current
activated set is used and ``FALLBACK_USED`` is explicitly True.

Fail-closed: a discoverable manifest that fails structural validation raises
``CliOperationManifestError`` instead of serving stale or partial metadata.
Only discovery failure (manifest absent) triggers the frozen fallback.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_CLI_TOKENS = re.compile(r"^([A-Za-z0-9-]+) ([A-Za-z0-9-]+)$")
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
_IDEMPOTENCY_KINDS = frozenset({"none", "required", "optional"})
_KINDS = frozenset({"query", "mutation"})


class CliOperationManifestError(RuntimeError):
    """Fail-closed boundary for loading the C1 activated-operation manifest."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _manifest_invalid(detail: str) -> CliOperationManifestError:
    return CliOperationManifestError(f"v5.cli.operation_manifest_invalid: {detail}")


@dataclass(frozen=True)
class CliOperation:
    """One activated intent's CLI/wire metadata (derived, deterministic)."""

    intent: str
    command: str  # first token of the manifest ``cli`` string
    action: str  # second token of the manifest ``cli`` string
    method: str
    path: str  # manifest path template, e.g. /api/v2/applications/{application_id}
    operation_id: str
    idempotency: str  # "none" | "required" | "optional"
    scope: str
    status_code: int  # derived: 201 for writes, 200 for reads
    contract_major: int
    command_target_resource: str | None = None


@dataclass(frozen=True)
class _ManifestLoadResult:
    fallback_used: bool
    source: str
    operations: tuple[CliOperation, ...]


# Frozen R2 fallback (C4).  The C1 generated manifest is the authoritative
# derivation source; these literals replicate the current activated set so a
# CLI installed outside the repository keeps byte-identical command names,
# paths, operation ids, idempotency kinds, scopes and status codes.  They are
# run through the same structural validation as manifest entries.
_FROZEN_OPERATIONS: tuple[dict[str, object], ...] = (
    {
        "intent": "capabilities.get",
        "cli": "capabilities get",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "query",
        "http": {
            "method": "GET",
            "operation_id": "getV5Capabilities",
            "path": "/api/v2/capabilities",
        },
        "idempotency": "none",
        "scope": "capabilities:read",
    },
    {
        "intent": "applications.register",
        "cli": "application register",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "mutation",
        "command_target": {
            "command": "applications.register",
            "resource": "ai_application",
        },
        "http": {
            "method": "POST",
            "operation_id": "registerApplication",
            "path": "/api/v2/applications",
        },
        "idempotency": "required",
        "scope": "applications:manage",
    },
    {
        "intent": "applications.get",
        "cli": "application get",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "query",
        "http": {
            "method": "GET",
            "operation_id": "getApplication",
            "path": "/api/v2/applications/{application_id}",
        },
        "idempotency": "none",
        "scope": "applications:read",
    },
    {
        "intent": "applications.list",
        "cli": "application list",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "query",
        "http": {
            "method": "GET",
            "operation_id": "listApplications",
            "path": "/api/v2/applications",
        },
        "idempotency": "none",
        "scope": "applications:read",
    },
    {
        "intent": "environments.register",
        "cli": "environment register",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "mutation",
        "command_target": {
            "command": "environments.register",
            "resource": "environment",
        },
        "http": {
            "method": "POST",
            "operation_id": "registerEnvironment",
            "path": "/api/v2/environments",
        },
        "idempotency": "required",
        "scope": "applications:manage",
    },
    {
        "intent": "environments.get",
        "cli": "environment get",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "query",
        "http": {
            "method": "GET",
            "operation_id": "getEnvironment",
            "path": "/api/v2/environments/{environment_id}",
        },
        "idempotency": "none",
        "scope": "applications:read",
    },
    {
        "intent": "system-components.register",
        "cli": "system-component register",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "mutation",
        "command_target": {
            "command": "system-components.register",
            "resource": "system_component",
        },
        "http": {
            "method": "POST",
            "operation_id": "registerSystemComponent",
            "path": "/api/v2/system-components",
        },
        "idempotency": "required",
        "scope": "applications:manage",
    },
    {
        "intent": "system-components.get",
        "cli": "system-component get",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "query",
        "http": {
            "method": "GET",
            "operation_id": "getSystemComponent",
            "path": "/api/v2/system-components/{component_id}",
        },
        "idempotency": "none",
        "scope": "applications:read",
    },
    {
        "intent": "dependency-edges.record",
        "cli": "dependency-edge record",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "mutation",
        "command_target": {
            "command": "dependency-edges.record",
            "resource": "dependency_edge",
        },
        "http": {
            "method": "POST",
            "operation_id": "recordDependencyEdge",
            "path": "/api/v2/dependency-edges",
        },
        "idempotency": "required",
        "scope": "applications:manage",
    },
    {
        "intent": "dependency-edges.get",
        "cli": "dependency-edge get",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "query",
        "http": {
            "method": "GET",
            "operation_id": "getDependencyEdge",
            "path": "/api/v2/dependency-edges/{dependency_edge_id}",
        },
        "idempotency": "none",
        "scope": "applications:read",
    },
    {
        "intent": "system-manifests.import",
        "cli": "system-manifest import",
        "contract_major": 2,
        "execution_mode": "synchronous_local_transaction",
        "kind": "mutation",
        "http": {
            "method": "POST",
            "operation_id": "importSystemManifest",
            "path": "/api/v2/system-manifests:import",
        },
        "idempotency": "required",
        "scope": "system_manifests:import",
    },
    {
        "intent": "system-versions.record",
        "cli": "system-version record",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "mutation",
        "command_target": {
            "command": "system-versions.record",
            "resource": "system_version_set",
        },
        "http": {
            "method": "POST",
            "operation_id": "recordSystemVersion",
            "path": "/api/v2/system-versions",
        },
        "idempotency": "required",
        "scope": "system_versions:record",
    },
    {
        "intent": "system-versions.get",
        "cli": "system-version get",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "query",
        "http": {
            "method": "GET",
            "operation_id": "getSystemVersion",
            "path": "/api/v2/system-versions/{system_version_set_id}",
        },
        "idempotency": "none",
        "scope": "system_versions:read",
    },
    {
        "intent": "system-versions.diff",
        "cli": "system-version diff",
        "contract_major": 2,
        "execution_mode": "synchronous",
        "kind": "query",
        "http": {
            "method": "GET",
            "operation_id": "diffSystemVersions",
            "path": "/api/v2/system-versions:diff",
        },
        "idempotency": "none",
        "scope": "system_versions:read",
    },
)


def _candidate_manifest_paths(
    explicit: str | Path | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    module_dir = Path(__file__).resolve().parent
    for ancestor in (module_dir, *module_dir.parents):
        candidates.append(
            ancestor / "contracts" / "v5" / "generated" / "operation-manifest.json"
        )
    candidates.extend(
        [
            Path("/srv/contracts/v5/generated/operation-manifest.json"),
            Path("/app/contracts/v5/generated/operation-manifest.json"),
        ]
    )
    return candidates


def _discover_manifest(explicit: str | Path | None = None) -> Path | None:
    """First candidate that actually carries the manifest wins; else None.

    The upward walk starts at this module's directory, so a repository
    checkout is found before any higher-level or deployment candidate.
    """
    for candidate in _candidate_manifest_paths(explicit):
        if candidate.is_file():
            return candidate.resolve()
    return None


def _validated_operation(operation: object) -> dict[str, Any]:
    if not isinstance(operation, dict):
        raise _manifest_invalid("operations entries must be objects")
    intent = operation.get("intent")
    cli = operation.get("cli")
    scope = operation.get("scope")
    execution_mode = operation.get("execution_mode")
    kind = operation.get("kind")
    contract_major = operation.get("contract_major")
    http = operation.get("http")
    idempotency = operation.get("idempotency")
    if not isinstance(intent, str) or not intent:
        raise _manifest_invalid("intent is required")
    if not isinstance(cli, str) or _CLI_TOKENS.fullmatch(cli) is None:
        raise _manifest_invalid(
            f"{intent}: cli must be exactly '<command> <action>'"
        )
    if not isinstance(scope, str) or not scope:
        raise _manifest_invalid(f"{intent}: scope is required")
    if not isinstance(execution_mode, str) or not execution_mode:
        raise _manifest_invalid(f"{intent}: execution_mode is required")
    if kind not in _KINDS:
        raise _manifest_invalid(f"{intent}: kind must be query or mutation")
    if not isinstance(contract_major, int) or contract_major < 1:
        raise _manifest_invalid(f"{intent}: contract_major must be a positive int")
    if idempotency not in _IDEMPOTENCY_KINDS:
        raise _manifest_invalid(
            f"{intent}: idempotency must be one of none|required|optional"
        )
    if not isinstance(http, dict):
        raise _manifest_invalid(f"{intent}: http is required")
    method = http.get("method")
    operation_id = http.get("operation_id")
    path = http.get("path")
    if not isinstance(method, str) or method.upper() not in _HTTP_METHODS:
        raise _manifest_invalid(f"{intent}: http.method is required")
    if not isinstance(operation_id, str) or not operation_id:
        raise _manifest_invalid(f"{intent}: http.operation_id is required")
    if not isinstance(path, str) or not path.startswith("/"):
        raise _manifest_invalid(f"{intent}: http.path must start with /")
    command_target_resource: str | None = None
    command_target = operation.get("command_target")
    if command_target is not None:
        if not isinstance(command_target, dict):
            raise _manifest_invalid(f"{intent}: command_target must be an object")
        resource = command_target.get("resource")
        if not isinstance(resource, str) or not resource:
            raise _manifest_invalid(
                f"{intent}: command_target.resource is required"
            )
        command_target_resource = resource
    return {
        "intent": intent,
        "cli": cli,
        "scope": scope,
        "execution_mode": execution_mode,
        "kind": kind,
        "contract_major": contract_major,
        "idempotency": idempotency,
        "http": {
            "method": method.upper(),
            "operation_id": operation_id,
            "path": path,
        },
        "command_target_resource": command_target_resource,
    }


def _derive_operation(operation: dict[str, Any]) -> CliOperation:
    """Derive the deterministic CLI metadata table entry.

    The manifest carries no explicit success status codes; the canonical rule
    mirrors the pre-cutover client spec byte-for-byte: writes return 201,
    reads return 200.
    """
    http = operation["http"]
    method = http["method"]
    status_code = 201 if method in {"POST", "PUT", "PATCH"} else 200
    command, action = _CLI_TOKENS.fullmatch(operation["cli"]).groups()
    return CliOperation(
        intent=operation["intent"],
        command=command,
        action=action,
        method=method,
        path=http["path"],
        operation_id=http["operation_id"],
        idempotency=operation["idempotency"],
        scope=operation["scope"],
        status_code=status_code,
        contract_major=operation["contract_major"],
        command_target_resource=operation["command_target_resource"],
    )


def _load_and_validate(raw: str) -> tuple[CliOperation, ...]:
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise _manifest_invalid("manifest is not valid JSON") from exc
    if not isinstance(document, dict):
        raise _manifest_invalid("manifest root must be an object")
    operations = document.get("operations")
    if not isinstance(operations, list) or not operations:
        raise _manifest_invalid("operations must be a non-empty list")
    if document.get("activated_intent_count") != len(operations):
        raise _manifest_invalid(
            "activated_intent_count does not match operations length"
        )
    validated = [_validated_operation(operation) for operation in operations]
    derived = [_derive_operation(operation) for operation in validated]
    intents = [operation.intent for operation in derived]
    if len(intents) != len(set(intents)):
        raise _manifest_invalid("duplicate activated intent names")
    cli_pairs = [(operation.command, operation.action) for operation in derived]
    if len(cli_pairs) != len(set(cli_pairs)):
        raise _manifest_invalid("duplicate cli command/action pairs")
    return tuple(derived)


def _frozen_fallback_operations() -> tuple[CliOperation, ...]:
    validated = [_validated_operation(operation) for operation in _FROZEN_OPERATIONS]
    return tuple(_derive_operation(operation) for operation in validated)


@lru_cache(maxsize=8)
def _load_manifest_cached(manifest_path: str) -> tuple[CliOperation, ...]:
    path = Path(manifest_path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliOperationManifestError(
            "v5.cli.operation_manifest_unavailable"
        ) from exc
    return _load_and_validate(raw)


def load_operation_manifest(
    explicit: str | Path | None = None,
) -> _ManifestLoadResult:
    """Load and derive the CLI v2 metadata table from the C1 manifest.

    Mirrors ``v5_capabilities.load_v5_operation_manifest``: candidate-root
    discovery, an lru_cache keyed on the resolved path, fail-closed errors for
    inconsistent manifests, and the explicit frozen fallback only when the
    manifest is absent.
    """
    discovered = _discover_manifest(explicit)
    if discovered is None:
        return _ManifestLoadResult(
            fallback_used=True,
            source="frozen-literal-fallback",
            operations=_frozen_fallback_operations(),
        )
    return _ManifestLoadResult(
        fallback_used=False,
        source=str(discovered),
        operations=_load_manifest_cached(str(discovered)),
    )


_LOADED_OPERATION_MANIFEST = load_operation_manifest()

#: True when no C1 manifest was discoverable and the frozen literal table
#: (byte-identical metadata) is serving as the explicit fallback.
FALLBACK_USED: bool = _LOADED_OPERATION_MANIFEST.fallback_used

#: Resolved manifest path, or "frozen-literal-fallback" when the fallback is
#: active.  Deterministic: never a timestamp or non-canonical path.
OPERATION_MANIFEST_SOURCE: str = _LOADED_OPERATION_MANIFEST.source

#: The v2 command metadata table: one entry per activated intent.
V2_CLI_OPERATIONS: tuple[CliOperation, ...] = (
    _LOADED_OPERATION_MANIFEST.operations
)

__all__ = [
    "CliOperation",
    "CliOperationManifestError",
    "FALLBACK_USED",
    "OPERATION_MANIFEST_SOURCE",
    "V2_CLI_OPERATIONS",
    "load_operation_manifest",
]
