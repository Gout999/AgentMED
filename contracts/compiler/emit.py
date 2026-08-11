"""Deterministic emission of compiler output into ``contracts/v5/generated/``.

No timestamps, no absolute paths, no environment identity: re-running emit
must reproduce byte-identical files (convergence plan C1 determinism).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .activated_operations import load_intent_registry
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


def emit(output_dir: Path | None = None) -> dict[str, Path]:
    """Write operation and capability manifests; return the written paths."""
    output = output_dir or GENERATED_DIR
    output.mkdir(parents=True, exist_ok=True)
    registry = load_intent_registry(INTENT_REGISTRY_PATH)
    operation_manifest = build_operation_manifest(registry, SCHEMAS_DIR)
    capability_manifest = build_capability_manifest(operation_manifest)
    operation_path = output / "operation-manifest.json"
    capability_path = output / "capability-manifest.json"
    write_deterministic(operation_manifest, operation_path)
    write_deterministic(capability_manifest, capability_path)
    return {"operation": operation_path, "capability": capability_path}
