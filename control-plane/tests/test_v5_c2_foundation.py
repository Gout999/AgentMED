"""C2 foundation cross-cutting guarantees.

Covers the wave-level invariants that no single module test covers
(v5-architecture-convergence.md#C2 verification list):

- import graph: the foundation package never imports a domain service, API,
  CLI, Console or adapter;
- contract-major separation of binding/event primitives;
- deterministic canonicalization anchored to a fixed golden digest;
- the binding-kind registry matches the frozen contract
  (contracts/v5/schema-profiles.yaml);
- ``require_exactly_one`` has one canonical implementation (graph) with a
  legacy error-contract shim in events.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import yaml

from app.foundation import bindings, events, graph, records

REPO_ROOT = Path(__file__).resolve().parents[2]
FOUNDATION_DIR = REPO_ROOT / "control-plane/app/foundation"

ALLOWED_IMPORT_PREFIXES = ("app.models", "app.utils", "app.foundation")
FORBIDDEN_IMPORT_MARKERS = (
    "app.services",
    "app.api",
    "app.public_api",
    "app.main",
    "app.workers",
    "app.quality",
    "app.notifications",
    "app.bootstrap",
)

# Fixed anchor for deterministic canonicalization: the digest of the record
# below computed by app.utils.v5_integrity.v5_record_digest (rfc8785 JCS +
# sha256, excluding /record_envelope/record_digest).
GOLDEN_RECORD_DIGEST = "sha256:3823a7d5c8da78362cbc2e215ff36b3be0aaf61ecbb9f5a38a199514cb9ea2e5"

_GOLDEN_RECORD = {
    "record_envelope": {
        "schema_version": "2.0",
        "workspace_id": "ws_AAAAAAAA",
        "revision": 1,
        "recorded_by_principal": "prn_BBBBBBBB",
        "recorded_at": "2026-08-11T00:00:00Z",
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
        "record_digest": GOLDEN_RECORD_DIGEST,
        "authority_receipt_id": "arec_CCCCCCCC",
    },
    "application_id": "app_DDDDDDDD",
    "workspace_id": "ws_AAAAAAAA",
    "project_id": "proj_EEEEEEEE",
    "slug": "customer-service",
    "display_name": "Customer Service",
    "owner_principal_ids": ["prn_BBBBBBBB"],
    "criticality": "P1",
    "data_classification": "INTERNAL",
    "governance_mode": "MANAGED",
    "lifecycle_state": "REGISTERED",
    "exact_previous_application_binding_or_null": None,
}

_V5_BINDING = {
    "kind": "AI_APPLICATION",
    "id": "app_AAAAAAAA",
    "revision": 1,
    "digest": "sha256:" + "a" * 64,
}
_V4_BRIDGE_BINDING = {
    "kind": "QUALITY_CASE",
    "id": "case_AAAAAAAA",
    "revision": 1,
    "digest": "sha256:" + "a" * 64,
}


def test_foundation_import_graph_is_clean() -> None:
    """No foundation module may import a domain service/API/CLI/Console/adapter."""
    violations = []
    for source in sorted(FOUNDATION_DIR.glob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                top = module.split(".")[0]
                if not module.startswith(ALLOWED_IMPORT_PREFIXES) and top not in sys.stdlib_module_names:
                    violations.append((source.name, module))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    top = name.split(".")[0]
                    if not name.startswith(ALLOWED_IMPORT_PREFIXES) and top not in sys.stdlib_module_names:
                        violations.append((source.name, name))
    assert not violations, violations


def test_binding_kinds_match_frozen_contract() -> None:
    profiles = yaml.safe_load(
        (REPO_ROOT / "contracts/v5/schema-profiles.yaml").read_text(encoding="utf-8")
    )
    v5_kinds = set(profiles["common"]["exact_record_binding_v5"]["kinds"])
    v4_kinds = set(profiles["common"]["exact_record_binding_v4_bridge"]["kinds"])
    assert len(v5_kinds) == 35
    assert len(v4_kinds) == 10
    assert bindings.V5_BINDING_KINDS == v5_kinds
    assert bindings.V4_BRIDGE_BINDING_KINDS == v4_kinds
    assert v5_kinds.isdisjoint(v4_kinds)


def test_validate_binding_contract_major_separation() -> None:
    assert bindings.validate_binding(_V5_BINDING, contract_major=2) is _V5_BINDING
    assert bindings.validate_binding(_V4_BRIDGE_BINDING, contract_major=1) is _V4_BRIDGE_BINDING
    with pytest.raises(bindings.BindingValidationError):
        bindings.validate_binding(_V5_BINDING, contract_major=1)
    with pytest.raises(bindings.BindingValidationError):
        bindings.validate_binding(_V4_BRIDGE_BINDING, contract_major=2)
    with pytest.raises(bindings.BindingValidationError):
        bindings.validate_binding(_V5_BINDING, contract_major=3)
    # closed shape: unknown field rejected, bad digest rejected
    with pytest.raises(bindings.BindingValidationError):
        bindings.validate_binding(dict(_V5_BINDING, rogue="x"), contract_major=2)
    with pytest.raises(bindings.BindingValidationError):
        bindings.validate_binding(dict(_V5_BINDING, digest="md5:abc"), contract_major=2)


def test_record_digest_is_deterministic_and_self_excluding() -> None:
    from app.utils.v5_integrity import v5_record_digest

    assert v5_record_digest(_GOLDEN_RECORD) == GOLDEN_RECORD_DIGEST
    mutated = dict(_GOLDEN_RECORD)
    mutated["record_envelope"] = dict(
        _GOLDEN_RECORD["record_envelope"], record_digest="sha256:" + "f" * 64
    )
    assert v5_record_digest(mutated) == GOLDEN_RECORD_DIGEST


def test_record_envelope_validation() -> None:
    from app.utils.v4_integrity import V4IntegrityError

    digest = records.validate_record_envelope_payload(_GOLDEN_RECORD)
    assert digest == _GOLDEN_RECORD["record_envelope"]["record_digest"]
    with pytest.raises((records.RecordEnvelopeValidationError, V4IntegrityError)):
        records.validate_record_envelope_payload(
            {k: v for k, v in _GOLDEN_RECORD.items() if k != "record_envelope"}
        )
    bad = dict(_GOLDEN_RECORD)
    bad["record_envelope"] = dict(_GOLDEN_RECORD["record_envelope"], immutable=False)
    # digest covers the whole envelope, so this fails at digest verification
    # (V4IntegrityError) before the envelope-field check.
    with pytest.raises((records.RecordEnvelopeValidationError, V4IntegrityError)):
        records.validate_record_envelope_payload(bad)


def test_require_exactly_one_single_implementation() -> None:
    """graph is the canonical implementation; events keeps the legacy shim."""
    assert events.require_exactly_one([42], "code") == 42
    assert graph.require_exactly_one([42], "code") == 42
    with pytest.raises(events.V4EventIntegrityError) as excinfo:
        events.require_exactly_one([], "v4.stage1_case_cardinality_mismatch")
    assert str(excinfo.value) == "v4.stage1_case_cardinality_mismatch"
    with pytest.raises(events.V4EventIntegrityError) as excinfo:
        events.require_exactly_one([1, 2], "v4.stage1_cardinality")
    assert str(excinfo.value) == "v4.stage1_cardinality"
    with pytest.raises(graph.GraphVerificationError) as excinfo:
        graph.require_exactly_one([1, 2], "x")
    assert excinfo.value.failure_kind == "cardinality"


def test_events_route_tables_are_major_aware() -> None:
    # EVENT_ROUTES is keyed by (aggregate_type, event_type) -> EventRoute.
    # Event type NAMES may repeat across majors; the major is distinguished by
    # the route record type and version markers, not by the name.
    assert len(events.EVENT_ROUTES) >= 14
    assert len(events.V5_EVENT_ROUTES) >= 11
    for route in events.EVENT_ROUTES.values():
        assert isinstance(route, events.EventRoute)
    # every v5 route is a major-2 route (carries self-binding/revision fields)
    for route in events.V5_EVENT_ROUTES.values():
        assert isinstance(route, events.V5EventRoute)
        assert route.self_binding_field
        # V5-2A Work routes advance one revision per event, so they pin
        # dynamic_revision instead of a fixed self_revision; either way the
        # self binding must carry an integer revision >= 1.
        assert route.self_revision is not None or route.dynamic_revision
