"""CaseLoop V5 C1 activated-operation compiler.

Deterministic, side-effect-free: consumes ``contracts/v5/intent-registry.yaml``
plus the C1 wire schemas (``contracts/v5/schemas/*.schema.json``) and emits the
activated-operation and capability manifests into ``contracts/v5/generated/``.

The compiler never imports domain, API, CLI, Console or persistence code and
never touches a database; it only transforms committed contract data.
"""

from .activated_operations import (
    ACTIVATED_WIRE_STATUSES,
    activated_intents,
    load_intent_registry,
    operation_metadata,
)
from .manifest import build_capability_manifest, build_operation_manifest, schema_uri
from .emit import emit, write_deterministic

__version__ = "0.1.0"

__all__ = [
    "ACTIVATED_WIRE_STATUSES",
    "activated_intents",
    "build_capability_manifest",
    "build_operation_manifest",
    "emit",
    "load_intent_registry",
    "operation_metadata",
    "schema_uri",
    "write_deterministic",
]
