"""Deterministic emission of compiler output into ``contracts/v5/generated/``.

No timestamps, no absolute paths, no environment identity: re-running emit
must reproduce byte-identical files (convergence plan C1 determinism).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .activated_operations import load_intent_registry
from .emitters import emit_ts_application_list, emit_v5_openapi
from .manifest import build_capability_manifest, build_operation_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
INTENT_REGISTRY_PATH = REPO_ROOT / "contracts/v5/intent-registry.yaml"
SCHEMAS_DIR = REPO_ROOT / "contracts/v5/schemas"
GENERATED_DIR = REPO_ROOT / "contracts/v5/generated"


def dump_json_bytes(data: dict[str, Any]) -> bytes:
    """Canonical deterministic encoding for generated manifests."""
    return (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def write_deterministic(data: dict[str, Any], path: Path) -> None:
    payload = dump_json_bytes(data)
    if path.exists() and path.read_bytes() == payload:
        return
    path.write_bytes(payload)


def write_deterministic_text(payload: str, path: Path) -> None:
    """Deterministic text emission (same skip-if-identical semantics)."""
    data = payload.encode("utf-8")
    if path.exists() and path.read_bytes() == data:
        return
    path.write_bytes(data)


def emit(output_dir: Path | None = None) -> dict[str, Path]:
    """Write generated C1/C4 artifacts; return the written paths.

    JSON manifests keep the canonical ``dump_json_bytes`` encoding; the
    OpenAPI document uses ``yaml.safe_dump(sort_keys=False)`` (insertion
    order preserved, deterministic) and the TypeScript module is plain
    deterministic text.
    """
    output = output_dir or GENERATED_DIR
    output.mkdir(parents=True, exist_ok=True)
    registry = load_intent_registry(INTENT_REGISTRY_PATH)
    operation_manifest = build_operation_manifest(registry, SCHEMAS_DIR)
    capability_manifest = build_capability_manifest(operation_manifest)
    operation_path = output / "operation-manifest.json"
    capability_path = output / "capability-manifest.json"
    write_deterministic(operation_manifest, operation_path)
    write_deterministic(capability_manifest, capability_path)
    openapi_path = output / "openapi.yaml"
    write_deterministic_text(
        yaml.safe_dump(emit_v5_openapi(operation_manifest, SCHEMAS_DIR), sort_keys=False, allow_unicode=True),
        openapi_path,
    )
    ts_path = output / "ts" / "applications.list.ts"
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    write_deterministic_text(emit_ts_application_list(SCHEMAS_DIR), ts_path)
    return {
        "operation": operation_path,
        "capability": capability_path,
        "openapi": openapi_path,
        "ts": ts_path,
    }
