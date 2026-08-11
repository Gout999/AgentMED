"""C2 graph verifier tests: closed record chains, causal chains, child bindings.

Covers ``app/foundation/graph.py`` — the canonical graph verifier extracted
during the V5 C2 wave (v5-architecture-convergence.md#C2).  The module must
stay import-safe: the AST audit at the bottom asserts the foundation import
boundary (stdlib only; no domain service/API/CLI/Console/adapter, no
``app.services`` / ``app.api`` / ``app.public_api`` / ``app.main``, and no
ORM/validation third-party packages).

Run from the control-plane directory:

    /tmp/c1-venv312/bin/python -m pytest tests/test_v5_c2_graph.py -q
"""

from __future__ import annotations

import ast
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.foundation.graph import (
    FAILURE_KINDS,
    GraphVerificationError,
    require_exactly_one,
    verify_causal_chain,
    verify_child_bindings,
    verify_lifecycle_chain,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GRAPH_SOURCE = REPO_ROOT / "app/foundation/graph.py"

DIGEST_RULE = "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"


def _binding(*, kind: str, id: str, revision: int, digest: str) -> dict:
    return {"kind": kind, "id": id, "revision": revision, "digest": digest}


def make_row(
    *,
    revision: int,
    digest: str,
    workspace_id: str = "ws-1",
    lifecycle_state: str = "ACTIVE",
    authority_receipt_id: str = "receipt-1",
    recorded_by_principal: str = "principal-1",
    recorded_at: str = "2026-01-01T00:00:00Z",
    application_id: str | None = None,
    previous: dict | None = None,
    previous_field: str = "exact_previous_system_component_binding",
    envelope_revision: int | None = None,
    envelope_digest: str | None = None,
    envelope_workspace: str | None = None,
) -> dict:
    row = {
        "workspace_id": workspace_id,
        "revision": revision,
        "record_digest": digest,
        "lifecycle_state": lifecycle_state,
        "authority_receipt_id": authority_receipt_id,
        "recorded_by_principal": recorded_by_principal,
        "recorded_at": recorded_at,
    }
    if application_id is not None:
        row["application_id"] = application_id
    record_envelope = {
        "schema_version": "2.0",
        "workspace_id": (
            envelope_workspace if envelope_workspace is not None else workspace_id
        ),
        "revision": (
            envelope_revision if envelope_revision is not None else revision
        ),
        "recorded_by_principal": recorded_by_principal,
        "recorded_at": recorded_at,
        "immutable": True,
        "hash_rule": DIGEST_RULE,
        "record_digest": (
            envelope_digest if envelope_digest is not None else digest
        ),
        "authority_receipt_id": authority_receipt_id,
    }
    envelope = {"record_envelope": record_envelope}
    if previous is not None:
        envelope[
            f"{previous_field}_or_null" if revision == 1 else previous_field
        ] = previous
    elif revision == 1:
        envelope[f"{previous_field}_or_null"] = None
    row["envelope_payload"] = envelope
    return row


def chain_loader(*rows):
    """Loader that yields one row per call, head first, then previous rows."""
    remaining = list(rows)

    def loader(kind, subject_id):
        return remaining.pop(0) if remaining else None

    return loader


def map_loader(rows_by_key):
    def loader(kind, subject_id):
        return rows_by_key.get((kind, subject_id))

    return loader


def make_event(
    *,
    event_id: str,
    causation_id: str,
    seq: int,
    occurred_at: datetime,
    workspace_id: str = "ws-1",
) -> dict:
    return {
        "event_id": event_id,
        "causation_id": causation_id,
        "seq": seq,
        "occurred_at": occurred_at,
        "workspace_id": workspace_id,
    }


T1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
T2 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
T3 = datetime(2026, 1, 1, 0, 0, 2, tzinfo=timezone.utc)


# ---------------------------------------------------------------- error shape


def test_graph_error_carries_typed_attributes() -> None:
    exc = GraphVerificationError("cycle", "revision 2 revisited", path=("s1",))
    assert exc.failure_kind == "cycle"
    assert exc.detail == "revision 2 revisited"
    assert exc.path == ("s1",)
    assert str(exc) == "cycle: revision 2 revisited @ s1"


def test_graph_error_defaults_path_to_empty() -> None:
    exc = GraphVerificationError("missing", "head missing")
    assert exc.path == ()
    assert str(exc) == "missing: head missing"


def test_failure_kinds_are_the_closed_enum() -> None:
    assert FAILURE_KINDS == frozenset(
        {
            "missing",
            "stale_revision",
            "tampered_digest",
            "cycle",
            "cross_workspace",
            "cardinality",
            "unexpected",
        }
    )


# ------------------------------------------------------- verify_lifecycle_chain


def test_lifecycle_chain_valid_walk_returns_head() -> None:
    head = make_row(
        revision=3,
        digest="digest-3",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=2, digest="digest-2"
        ),
    )
    mid = make_row(
        revision=2,
        digest="digest-2",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
        ),
    )
    root = make_row(revision=1, digest="digest-1")
    loader = chain_loader(head, mid, root)
    result = verify_lifecycle_chain(
        loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
        workspace_id="ws-1",
    )
    assert result == head


def test_lifecycle_chain_head_missing() -> None:
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=chain_loader(None),
            kind="SYSTEM_COMPONENT",
            subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "missing"
    assert exc_info.value.path == ("comp-1",)


def test_lifecycle_chain_tampered_digest() -> None:
    head = make_row(
        revision=2,
        digest="digest-2",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
        ),
    )
    tampered = make_row(revision=1, digest="other-digest")
    loader = chain_loader(head, tampered)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "tampered_digest"
    assert "digest-1" in exc_info.value.detail


def test_lifecycle_chain_envelope_digest_tampered() -> None:
    head = make_row(
        revision=2,
        digest="digest-2",
        envelope_digest="digest-2",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
        ),
    )
    # record_envelope digest rewritten to something else inside the head row.
    head["envelope_payload"]["record_envelope"]["record_digest"] = "forged"
    loader = chain_loader(head, make_row(revision=1, digest="digest-1"))
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "tampered_digest"


def test_lifecycle_chain_self_cycle() -> None:
    head = make_row(
        revision=2,
        digest="digest-2",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=2, digest="digest-2"
        ),
    )
    loader = chain_loader(head, head)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "cycle"


def test_lifecycle_chain_long_cycle() -> None:
    head = make_row(
        revision=3,
        digest="digest-3",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=2, digest="digest-2"
        ),
    )
    mid = make_row(
        revision=2,
        digest="digest-2",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=3, digest="digest-3"
        ),
    )
    loader = chain_loader(head, mid)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "cycle"
    assert exc_info.value.path == ("comp-1", "comp-1")


def test_lifecycle_chain_cross_workspace() -> None:
    head = make_row(
        revision=2,
        digest="digest-2",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
        ),
    )
    other_ws = make_row(revision=1, digest="digest-1", workspace_id="ws-9")
    loader = chain_loader(head, other_ws)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "cross_workspace"


def test_lifecycle_chain_envelope_workspace_crossed() -> None:
    head = make_row(
        revision=2,
        digest="digest-2",
        envelope_workspace="ws-9",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
        ),
    )
    loader = chain_loader(head, make_row(revision=1, digest="digest-1"))
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "cross_workspace"


def test_lifecycle_chain_stale_revision_skips_a_step() -> None:
    head = make_row(
        revision=3,
        digest="digest-3",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
        ),
    )
    loader = chain_loader(head)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "stale_revision"


def test_lifecycle_chain_envelope_revision_stale() -> None:
    head = make_row(
        revision=2,
        digest="digest-2",
        envelope_revision=1,
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
        ),
    )
    loader = chain_loader(head, make_row(revision=1, digest="digest-1"))
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "stale_revision"


def test_lifecycle_chain_previous_row_missing() -> None:
    head = make_row(
        revision=2,
        digest="digest-2",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
        ),
    )
    loader = chain_loader(head, None)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "missing"


def test_lifecycle_chain_root_with_previous_is_unexpected() -> None:
    root = make_row(
        revision=1,
        digest="digest-1",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
        ),
    )
    loader = chain_loader(root)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "unexpected"


def test_lifecycle_chain_missing_previous_binding_is_unexpected() -> None:
    head = make_row(revision=2, digest="digest-2")
    loader = chain_loader(head)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "unexpected"


def test_lifecycle_chain_previous_id_mismatch_is_unexpected() -> None:
    head = make_row(
        revision=2,
        digest="digest-2",
        previous=_binding(
            kind="SYSTEM_COMPONENT", id="other-1", revision=1, digest="digest-1"
        ),
    )
    loader = chain_loader(head)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "unexpected"


def test_lifecycle_chain_previous_binding_shape_invalid() -> None:
    head = make_row(revision=2, digest="digest-2")
    head["envelope_payload"]["exact_previous_system_component_binding"] = {
        "kind": "SYSTEM_COMPONENT",
        "id": "comp-1",
    }
    loader = chain_loader(head)
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=loader, kind="SYSTEM_COMPONENT", subject_id="comp-1",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "unexpected"


def test_lifecycle_chain_unknown_kind_is_unexpected() -> None:
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=chain_loader(make_row(revision=1, digest="digest-1")),
            kind="UNKNOWN_KIND",
            subject_id="x",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "unexpected"
    assert "no previous-binding field registered" in exc_info.value.detail


def test_lifecycle_chain_application_previous_field_name() -> None:
    head = make_row(
        revision=2,
        digest="digest-2",
        previous=_binding(
            kind="AI_APPLICATION", id="app-1", revision=1, digest="digest-1"
        ),
        previous_field="exact_previous_application_binding",
    )
    root = make_row(
        revision=1,
        digest="digest-1",
        previous_field="exact_previous_application_binding",
    )
    result = verify_lifecycle_chain(
        loader=chain_loader(head, root),
        kind="AI_APPLICATION",
        subject_id="app-1",
        workspace_id="ws-1",
    )
    assert result["revision"] == 2


def test_lifecycle_chain_bad_arguments() -> None:
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_lifecycle_chain(
            loader=chain_loader(make_row(revision=1, digest="d")),
            kind="",
            subject_id="x",
            workspace_id="ws-1",
        )
    assert exc_info.value.failure_kind == "unexpected"


# ---------------------------------------------------------- verify_causal_chain


def test_causal_chain_valid() -> None:
    events = [
        make_event(event_id="e1", causation_id="external-trace", seq=1, occurred_at=T1),
        make_event(event_id="e2", causation_id="e1", seq=2, occurred_at=T2),
        make_event(event_id="e3", causation_id="e2", seq=3, occurred_at=T3),
    ]
    assert verify_causal_chain(events) is None


def test_causal_chain_broken_causation() -> None:
    events = [
        make_event(event_id="e1", causation_id="external-trace", seq=1, occurred_at=T1),
        make_event(event_id="e2", causation_id="ghost-parent", seq=2, occurred_at=T2),
    ]
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain(events)
    assert exc_info.value.failure_kind == "missing"
    assert "ghost-parent" in exc_info.value.detail


def test_causal_chain_seq_not_strictly_increasing() -> None:
    events = [
        make_event(event_id="e1", causation_id="external-trace", seq=2, occurred_at=T1),
        make_event(event_id="e2", causation_id="e1", seq=1, occurred_at=T2),
    ]
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain(events)
    assert exc_info.value.failure_kind == "stale_revision"


def test_causal_chain_time_reversal() -> None:
    events = [
        make_event(event_id="e1", causation_id="external-trace", seq=1, occurred_at=T2),
        make_event(event_id="e2", causation_id="e1", seq=2, occurred_at=T1),
    ]
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain(events)
    assert exc_info.value.failure_kind == "unexpected"
    assert "occurred_at" in exc_info.value.detail


def test_causal_chain_cross_workspace() -> None:
    events = [
        make_event(event_id="e1", causation_id="external-trace", seq=1, occurred_at=T1),
        make_event(
            event_id="e2",
            causation_id="e1",
            seq=2,
            occurred_at=T2,
            workspace_id="ws-9",
        ),
    ]
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain(events)
    assert exc_info.value.failure_kind == "cross_workspace"


def test_causal_chain_empty_is_cardinality() -> None:
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain([])
    assert exc_info.value.failure_kind == "cardinality"


def test_causal_chain_duplicate_event_id() -> None:
    events = [
        make_event(event_id="e1", causation_id="external-trace", seq=1, occurred_at=T1),
        make_event(event_id="e1", causation_id="e1", seq=2, occurred_at=T2),
    ]
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain(events)
    assert exc_info.value.failure_kind == "cardinality"


def test_causal_chain_duplicate_seq() -> None:
    events = [
        make_event(event_id="e1", causation_id="external-trace", seq=1, occurred_at=T1),
        make_event(event_id="e2", causation_id="e1", seq=1, occurred_at=T2),
    ]
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain(events)
    assert exc_info.value.failure_kind == "cardinality"


def test_causal_chain_no_external_root_is_cycle() -> None:
    events = [
        make_event(event_id="e1", causation_id="e2", seq=1, occurred_at=T1),
        make_event(event_id="e2", causation_id="e1", seq=2, occurred_at=T2),
    ]
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain(events)
    assert exc_info.value.failure_kind == "cycle"


def test_causal_chain_second_external_causation_is_missing() -> None:
    events = [
        make_event(event_id="e1", causation_id="trace-a", seq=1, occurred_at=T1),
        make_event(event_id="e2", causation_id="trace-b", seq=2, occurred_at=T2),
    ]
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain(events)
    assert exc_info.value.failure_kind == "missing"
    assert "e2" in exc_info.value.detail


def test_causal_chain_malformed_event() -> None:
    events = [make_event(event_id="e1", causation_id="t", seq=1, occurred_at=T1)]
    events[0]["seq"] = 0
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_causal_chain(events)
    assert exc_info.value.failure_kind == "unexpected"


# ------------------------------------------------------- verify_child_bindings


def _child_fixture():
    parent = make_row(
        revision=3,
        digest="digest-3",
        application_id="app-1",
        workspace_id="ws-1",
    )
    child = make_row(
        revision=1,
        digest="digest-1",
        application_id="app-1",
        workspace_id="ws-1",
    )
    binding = _binding(
        kind="SYSTEM_COMPONENT", id="comp-1", revision=1, digest="digest-1"
    )
    return parent, child, binding


def test_child_bindings_valid_single_and_list() -> None:
    parent, child, binding = _child_fixture()
    loader = map_loader({("SYSTEM_COMPONENT", "comp-1"): child})
    parent["child_binding"] = binding
    assert verify_child_bindings(
        loader=loader, parent=parent, binding_field_names=["child_binding"]
    ) is None
    parent["child_binding"] = [binding, binding]
    assert verify_child_bindings(
        loader=loader, parent=parent, binding_field_names=["child_binding"]
    ) is None


def test_child_bindings_none_is_skipped() -> None:
    parent, _, _ = _child_fixture()
    parent["child_binding"] = None
    assert verify_child_bindings(
        loader=map_loader({}), parent=parent,
        binding_field_names=["child_binding"],
    ) is None


def test_child_bindings_missing() -> None:
    parent, _, binding = _child_fixture()
    parent["child_binding"] = binding
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_child_bindings(
            loader=map_loader({}), parent=parent,
            binding_field_names=["child_binding"],
        )
    assert exc_info.value.failure_kind == "missing"


def test_child_bindings_tampered_digest() -> None:
    parent, child, binding = _child_fixture()
    child["record_digest"] = "forged"
    child["envelope_payload"]["record_envelope"]["record_digest"] = "forged"
    parent["child_binding"] = binding
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_child_bindings(
            loader=map_loader({("SYSTEM_COMPONENT", "comp-1"): child}),
            parent=parent,
            binding_field_names=["child_binding"],
        )
    assert exc_info.value.failure_kind == "tampered_digest"


def test_child_bindings_stale_revision() -> None:
    parent, child, binding = _child_fixture()
    child["revision"] = 2
    child["envelope_payload"]["record_envelope"]["revision"] = 2
    parent["child_binding"] = binding
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_child_bindings(
            loader=map_loader({("SYSTEM_COMPONENT", "comp-1"): child}),
            parent=parent,
            binding_field_names=["child_binding"],
        )
    assert exc_info.value.failure_kind == "stale_revision"


def test_child_bindings_cross_workspace_owner() -> None:
    parent, child, binding = _child_fixture()
    child["workspace_id"] = "ws-9"
    child["envelope_payload"]["record_envelope"]["workspace_id"] = "ws-9"
    parent["child_binding"] = binding
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_child_bindings(
            loader=map_loader({("SYSTEM_COMPONENT", "comp-1"): child}),
            parent=parent,
            binding_field_names=["child_binding"],
        )
    assert exc_info.value.failure_kind == "cross_workspace"
    assert "cross-owner" in exc_info.value.detail


def test_child_bindings_cross_owner_application_id() -> None:
    parent, child, binding = _child_fixture()
    child["application_id"] = "app-9"
    parent["child_binding"] = binding
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_child_bindings(
            loader=map_loader({("SYSTEM_COMPONENT", "comp-1"): child}),
            parent=parent,
            binding_field_names=["child_binding"],
        )
    assert exc_info.value.failure_kind == "cross_workspace"
    assert "cross-owner" in exc_info.value.detail


def test_child_bindings_shape_invalid() -> None:
    parent, _, _ = _child_fixture()
    parent["child_binding"] = {"kind": "SYSTEM_COMPONENT", "id": "comp-1"}
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_child_bindings(
            loader=map_loader({}), parent=parent,
            binding_field_names=["child_binding"],
        )
    assert exc_info.value.failure_kind == "unexpected"


def test_child_bindings_path_names_field() -> None:
    parent, _, binding = _child_fixture()
    parent["child_binding"] = binding
    with pytest.raises(GraphVerificationError) as exc_info:
        verify_child_bindings(
            loader=map_loader({}), parent=parent,
            binding_field_names=["child_binding"],
        )
    assert exc_info.value.path == ("child_binding",)


# ----------------------------------------------------------- require_exactly_one


def test_require_exactly_one_returns_the_item() -> None:
    assert require_exactly_one(["only"], "event") == "only"


def test_require_exactly_one_zero_is_cardinality() -> None:
    with pytest.raises(GraphVerificationError) as exc_info:
        require_exactly_one([], "event")
    assert exc_info.value.failure_kind == "cardinality"


def test_require_exactly_one_many_is_cardinality() -> None:
    with pytest.raises(GraphVerificationError) as exc_info:
        require_exactly_one(["a", "b"], "event")
    assert exc_info.value.failure_kind == "cardinality"
    assert "2" in exc_info.value.detail


def test_require_exactly_one_uncollected_is_unexpected() -> None:
    with pytest.raises(GraphVerificationError) as exc_info:
        require_exactly_one(42, "event")
    assert exc_info.value.failure_kind == "unexpected"


# ------------------------------------------------------------- import boundary


FORBIDDEN_DOMAIN_MARKERS = (
    "app.services",
    "app.api",
    "app.public_api",
    "app.main",
    "app.quality",
    "app.bootstrap",
    "app.workers",
    "app.notifications",
    "app.console",
    "sqlalchemy",
    "pydantic",
    "rfc8785",
)

# app.* imports permitted by the foundation contract.
_ALLOWED_APP_MODULES = {
    "app.models",
    "app.utils.v4_integrity",
    "app.utils.v5_integrity",
    "app.utils.jcs",
}


def _module_forbidden(module: str) -> bool:
    if module.startswith("app"):
        return module not in _ALLOWED_APP_MODULES and not any(
            module.startswith(allowed + ".") for allowed in _ALLOWED_APP_MODULES
        )
    top = module.partition(".")[0]
    if top in sys.stdlib_module_names:
        return False
    return any(marker in module for marker in FORBIDDEN_DOMAIN_MARKERS)


def test_graph_module_import_boundary() -> None:
    """AST audit: graph.py must not import any domain package or third-party lib."""
    tree = ast.parse(GRAPH_SOURCE.read_text(encoding="utf-8"))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith("."):  # intra-foundation relative import
                continue
            if _module_forbidden(module):
                violations.append(f"from {module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _module_forbidden(alias.name):
                    violations.append(f"import {alias.name}")
    assert not violations, f"forbidden imports in graph.py: {violations}"


def test_graph_module_imports_are_only_stdlib() -> None:
    tree = ast.parse(GRAPH_SOURCE.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and not (node.module or "").startswith("."):
            imports.append(node.module or "")
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    for module in imports:
        top = module.partition(".")[0]
        assert top in sys.stdlib_module_names, (
            f"graph.py imports non-stdlib module {module!r}"
        )
