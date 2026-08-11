"""Canonical graph verifier for closed record chains and causal event chains.

The foundation defines data and verification mechanics only: these functions
never choose business success, never mutate state and never import a domain
service, API, CLI, Console or adapter.  They are the import-safe home of the
closed-record chain, exact previous-binding and causal-chain checks that
``app.services.v5_authority._validate_lifecycle_history_row`` (728-835),
``app.services.v4_event_store._stage1_subject_graph`` (855+) and
``app.services.v4_event_store.validate_stage1_event_semantics`` (1006+)
currently perform against ORM rows.

Import rule: stdlib only at runtime.  ``app.models`` and
``app.utils.{v4_integrity,v5_integrity,jcs}`` are permitted but not required;
this module has no other imports so the import boundary is trivially stable
(asserted by ``tests/test_v5_c2_graph.py`` AST audit).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "FAILURE_KINDS",
    "GraphVerificationError",
    "require_exactly_one",
    "verify_causal_chain",
    "verify_child_bindings",
    "verify_lifecycle_chain",
]

FAILURE_KINDS = frozenset(
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

# Previous-binding envelope field per lifecycle subject kind, mirroring
# ``app.services.v5_authority._V5_LIFECYCLE_BINDINGS`` previous-attribute names.
# A revision-1 row carries the ``f"{field}_or_null"`` variant set to None;
# revision > 1 rows carry the plain ``field``.
_LIFECYCLE_PREVIOUS_FIELDS: dict[str, str] = {
    "AI_APPLICATION": "exact_previous_application_binding",
    "SYSTEM_COMPONENT": "exact_previous_system_component_binding",
}

# Exact closed binding shape (``v5_authority`` calls it ``_EXACT_BINDING_FIELDS``).
_EXACT_BINDING_FIELDS = frozenset({"kind", "id", "revision", "digest"})


class GraphVerificationError(Exception):
    """Typed verification failure for a record or event graph.

    ``failure_kind`` is one of :data:`FAILURE_KINDS`; ``path`` locates the
    failing node in the traversed chain (subject id chain, event chain or
    binding field name) for diagnostics.
    """

    def __init__(
        self,
        failure_kind: str,
        detail: str,
        *,
        path: tuple[str, ...] = (),
    ) -> None:
        self.failure_kind = failure_kind
        self.detail = detail
        self.path = tuple(path)
        message = f"{failure_kind}: {detail}"
        if self.path:
            message += f" @ {' -> '.join(self.path)}"
        super().__init__(message)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise GraphVerificationError(
            "unexpected", f"{what} must be a non-empty string"
        )
    return value


def _require_revision(value: Any, what: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise GraphVerificationError(
            "unexpected", f"{what} must be a positive integer revision"
        )
    return value


def _previous_binding(kind: str, revision: int, envelope: Mapping) -> Any:
    """Return the previous-binding value from an envelope payload.

    Returns ``None`` when the row is a root (no previous pointer) or when the
    pointer is explicitly absent; callers distinguish those cases with
    ``revision``.
    """
    field = _LIFECYCLE_PREVIOUS_FIELDS.get(kind)
    if field is None:
        raise GraphVerificationError(
            "unexpected", f"no previous-binding field registered for kind {kind!r}"
        )
    if revision == 1:
        return envelope.get(f"{field}_or_null")
    return envelope.get(field)


def verify_lifecycle_chain(
    *,
    loader: Any,
    kind: str,
    subject_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    """Walk a closed previous-binding chain and return its head row.

    ``loader(kind, subject_id)`` returns a row mapping for the chain (the head
    on the first call, then one row per previous binding as the walk descends)
    or ``None`` when the row does not exist.  Each row is a record mapping in
    the shape of the V5 lifecycle history rows:

    .. code-block:: python

        {
            "workspace_id": str,
            "revision": int,                      # >= 1
            "record_digest": str,
            "lifecycle_state": str,
            "authority_receipt_id": str,
            "recorded_by_principal": str,
            "recorded_at": str,                   # wire time
            "application_id": str | None,         # SYSTEM_COMPONENT rows only
            "envelope_payload": {
                "record_envelope": {
                    "schema_version": "2.0",
                    "workspace_id": str,
                    "revision": int,
                    "recorded_by_principal": str,
                    "recorded_at": str,
                    "immutable": True,
                    "hash_rule": str,
                    "record_digest": str,         # == row["record_digest"]
                    "authority_receipt_id": str,
                },
                # previous pointer, per kind:
                # AI_APPLICATION   -> "exact_previous_application_binding"
                # SYSTEM_COMPONENT -> "exact_previous_system_component_binding"
                # revision 1 rows carry the f"<field>_or_null" variant (None);
                # revision > 1 rows carry the plain field:
                "exact_previous_application_binding": {
                    "kind": str, "id": str, "revision": int, "digest": str,
                } | None,
                ...other projection fields...,
            },
        }

    Failures are typed: missing head/previous row -> ``missing``, previous
    pointer at a non-consecutive revision -> ``stale_revision``, digest
    mismatch (row vs binding, or envelope vs row) -> ``tampered_digest``,
    revisit of an already-visited revision -> ``cycle`` (covers self loops),
    workspace mismatch -> ``cross_workspace``, malformed shapes -> ``unexpected``.
    """
    _require_str(kind, "kind")
    _require_str(subject_id, "subject_id")
    _require_str(workspace_id, "workspace_id")

    head = loader(kind, subject_id)
    if head is None:
        raise GraphVerificationError(
            "missing",
            f"lifecycle head missing for {kind}:{subject_id}",
            path=(subject_id,),
        )

    visited: set[int] = set()
    path: list[str] = [subject_id]
    row: Any = head
    verified_head: dict[str, Any] | None = None
    expected_revision: int | None = None
    expected_digest: str | None = None
    while True:
        if not isinstance(row, Mapping):
            raise GraphVerificationError(
                "unexpected",
                "lifecycle row must be a record mapping",
                path=tuple(path),
            )
        revision = _require_revision(row.get("revision"), "row revision")
        digest = _require_str(row.get("record_digest"), "row record_digest")
        row_workspace = _require_str(row.get("workspace_id"), "row workspace_id")
        if revision in visited:
            raise GraphVerificationError(
                "cycle",
                f"revision {revision} revisited",
                path=tuple(path),
            )
        visited.add(revision)
        if row_workspace != workspace_id:
            raise GraphVerificationError(
                "cross_workspace",
                f"row workspace {row_workspace!r} != expected {workspace_id!r}",
                path=tuple(path),
            )
        if expected_revision is not None and revision != expected_revision:
            raise GraphVerificationError(
                "stale_revision",
                f"row revision {revision} != previous binding revision "
                f"{expected_revision}",
                path=tuple(path),
            )
        if expected_digest is not None and digest != expected_digest:
            raise GraphVerificationError(
                "tampered_digest",
                f"row record_digest {digest!r} != previous binding digest "
                f"{expected_digest!r}",
                path=tuple(path),
            )

        envelope = row.get("envelope_payload")
        if not isinstance(envelope, Mapping):
            raise GraphVerificationError(
                "unexpected",
                "row envelope_payload must be a mapping",
                path=tuple(path),
            )
        record_envelope = envelope.get("record_envelope")
        if not isinstance(record_envelope, Mapping):
            raise GraphVerificationError(
                "unexpected",
                "envelope_payload must carry a record_envelope mapping",
                path=tuple(path),
            )
        if record_envelope.get("revision") != revision:
            raise GraphVerificationError(
                "stale_revision",
                f"record_envelope revision {record_envelope.get('revision')!r} "
                f"!= row revision {revision}",
                path=tuple(path),
            )
        if record_envelope.get("workspace_id") != row_workspace:
            raise GraphVerificationError(
                "cross_workspace",
                "record_envelope workspace_id does not match the row",
                path=tuple(path),
            )
        if record_envelope.get("record_digest") != digest:
            raise GraphVerificationError(
                "tampered_digest",
                "record_envelope record_digest does not match the row",
                path=tuple(path),
            )

        previous = _previous_binding(kind, revision, envelope)
        if revision == 1:
            if previous is not None:
                raise GraphVerificationError(
                    "unexpected",
                    "root revision 1 must not carry a previous binding",
                    path=tuple(path),
                )
            verified_head = dict(head)
            break

        if previous is None:
            raise GraphVerificationError(
                "unexpected",
                f"revision {revision} must carry a previous binding",
                path=tuple(path),
            )
        if not isinstance(previous, Mapping) or set(previous) != _EXACT_BINDING_FIELDS:
            raise GraphVerificationError(
                "unexpected",
                "previous binding must be exactly {kind, id, revision, digest}",
                path=tuple(path),
            )
        prev_kind = previous.get("kind")
        prev_id = previous.get("id")
        prev_revision = _require_revision(
            previous.get("revision"), "previous binding revision"
        )
        prev_digest = _require_str(
            previous.get("digest"), "previous binding digest"
        )
        if prev_kind != kind or prev_id != subject_id:
            raise GraphVerificationError(
                "unexpected",
                "previous binding must reference the same kind and subject id",
                path=tuple(path),
            )
        if prev_revision in visited:
            raise GraphVerificationError(
                "cycle",
                f"previous binding points back at visited revision {prev_revision}",
                path=tuple(path),
            )
        if prev_revision != revision - 1:
            raise GraphVerificationError(
                "stale_revision",
                f"previous binding revision {prev_revision} != {revision - 1}",
                path=tuple(path),
            )

        path.append(prev_id)
        child = loader(kind, prev_id)
        if child is None:
            raise GraphVerificationError(
                "missing",
                f"previous row missing for {kind}:{prev_id} revision {prev_revision}",
                path=tuple(path),
            )
        if not isinstance(child, Mapping):
            raise GraphVerificationError(
                "unexpected",
                "previous row must be a record mapping",
                path=tuple(path),
            )
        row = child
        expected_revision = prev_revision
        expected_digest = prev_digest
    assert verified_head is not None  # the walk always terminates at revision 1
    return verified_head


def verify_causal_chain(events: list[Mapping[str, Any]]) -> None:
    """Verify one subject's causal event chain in ``events``.

    ``events`` is a non-empty ordered list of event mappings carrying
    ``event_id``, ``causation_id`` (non-empty strings), ``seq`` (positive
    int), ``occurred_at`` (``datetime``) and ``workspace_id`` (non-empty
    string).  Semantics follow ``v4_event_store`` stage-1 causal checks
    (``_stage1_subject_graph`` 855+ / ``validate_stage1_event_semantics``
    1006+):

    - exactly one root: the lowest-``seq`` event whose ``causation_id`` is
      external to the chain; when every event points inside the chain the
      graph is a closed causation loop -> ``cycle``;
    - every non-root event's ``causation_id`` must resolve to a chain event
      (``missing``), and each event id / seq appears exactly once
      (``cardinality``);
    - along each causation edge the child ``seq`` must be strictly greater
      than the parent's (``stale_revision``) — a closed chain of strictly
      increasing revisions is acyclic by construction;
    - the child ``occurred_at`` must be strictly after the parent's
      (``unexpected``: timestamp causality invariant violated);
    - all events share one ``workspace_id`` (``cross_workspace``);
    - malformed entries fail with ``unexpected``.
    """
    if not isinstance(events, (list, tuple)) or not events:
        raise GraphVerificationError(
            "cardinality", "causal chain must be a non-empty list of events"
        )
    by_id: dict[str, Mapping[str, Any]] = {}
    seq_owners: dict[int, str] = {}
    shared_workspace: str | None = None
    for event in events:
        if not isinstance(event, Mapping):
            raise GraphVerificationError(
                "unexpected", "causal chain entries must be event mappings"
            )
        event_id = _require_str(event.get("event_id"), "event_id")
        causation_id = _require_str(event.get("causation_id"), "causation_id")
        seq = _require_revision(event.get("seq"), "event seq")
        occurred_at = event.get("occurred_at")
        if not isinstance(occurred_at, datetime):
            raise GraphVerificationError(
                "unexpected", "occurred_at must be a datetime"
            )
        workspace = _require_str(event.get("workspace_id"), "workspace_id")
        if event_id in by_id:
            raise GraphVerificationError(
                "cardinality", f"duplicate event_id {event_id!r}"
            )
        if seq in seq_owners:
            raise GraphVerificationError(
                "cardinality",
                f"duplicate seq {seq} (event_ids {seq_owners[seq]!r}, {event_id!r})",
            )
        by_id[event_id] = event
        seq_owners[seq] = event_id
        if shared_workspace is None:
            shared_workspace = workspace
        elif workspace != shared_workspace:
            raise GraphVerificationError(
                "cross_workspace",
                f"event {event_id!r} workspace {workspace!r} != "
                f"{shared_workspace!r}",
            )

    external_events = [
        event for event in events if event["causation_id"] not in by_id
    ]
    if not external_events:
        raise GraphVerificationError(
            "cycle",
            "causal chain has no event with an external causation_id "
            "(closed causation loop without an external root)",
        )
    # The single external-causation event is the root; with several external
    # causations the lowest seq decides the root and the others are broken.
    root = (
        external_events[0]
        if len(external_events) == 1
        else min(events, key=lambda event: event["seq"])
    )

    for event in events:
        if event["causation_id"] not in by_id:
            if event is root:
                continue  # the external root
            raise GraphVerificationError(
                "missing",
                f"event {event['event_id']!r} causation_id "
                f"{event['causation_id']!r} resolves to no chain event",
            )
        parent = by_id[event["causation_id"]]
        if event["seq"] <= parent["seq"]:
            raise GraphVerificationError(
                "stale_revision",
                f"event {event['event_id']!r} seq {event['seq']} must be "
                f"strictly greater than causation parent {parent['event_id']!r} "
                f"seq {parent['seq']}",
            )
        if _as_utc(event["occurred_at"]) <= _as_utc(parent["occurred_at"]):
            raise GraphVerificationError(
                "unexpected",
                f"event {event['event_id']!r} occurred_at must be strictly after "
                f"its causation parent {parent['event_id']!r}",
            )


def verify_child_bindings(
    *,
    loader: Any,
    parent: Mapping[str, Any],
    binding_field_names: list[str],
) -> None:
    """Resolve every named child binding of ``parent`` through ``loader``.

    ``parent`` is a record mapping whose ``binding_field_names`` entries hold a
    single exact binding (``{kind, id, revision, digest}``) or ``None`` (no
    child), or a list of such bindings.  Each child is loaded with
    ``loader(binding_kind, binding_id)`` and must satisfy:

    - row exists (``missing``);
    - row revision == binding revision (``stale_revision``);
    - row ``record_digest`` == binding digest (``tampered_digest``);
    - row workspace == parent workspace, and when both rows carry
      ``application_id`` it must match the parent's (``cross_workspace`` with
      a ``cross-owner`` detail — the typed kind for a binding that points
      across an ownership boundary);
    - malformed bindings/rows fail with ``unexpected``.
    """
    if not isinstance(binding_field_names, (list, tuple)):
        raise GraphVerificationError(
            "unexpected", "binding_field_names must be a list of field names"
        )
    if not isinstance(parent, Mapping):
        raise GraphVerificationError(
            "unexpected", "parent must be a record mapping"
        )
    parent_workspace = _require_str(
        parent.get("workspace_id"), "parent workspace_id"
    )
    parent_owner = parent.get("application_id")

    for field in binding_field_names:
        if not isinstance(field, str) or not field:
            raise GraphVerificationError(
                "unexpected", "binding field names must be non-empty strings"
            )
        value = parent.get(field)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            bindings = value
        else:
            bindings = [value]
        for index, binding in enumerate(bindings):
            path = (field, f"[{index}]") if len(bindings) > 1 else (field,)
            _verify_child_binding(
                loader=loader,
                binding=binding,
                parent_workspace=parent_workspace,
                parent_owner=parent_owner,
                path=path,
            )


def _verify_child_binding(
    *,
    loader: Any,
    binding: Any,
    parent_workspace: str,
    parent_owner: Any,
    path: tuple[str, ...],
) -> None:
    if not isinstance(binding, Mapping) or set(binding) != _EXACT_BINDING_FIELDS:
        raise GraphVerificationError(
            "unexpected",
            "child binding must be exactly {kind, id, revision, digest}",
            path=path,
        )
    binding_kind = _require_str(binding.get("kind"), "child binding kind")
    binding_id = _require_str(binding.get("id"), "child binding id")
    binding_revision = _require_revision(
        binding.get("revision"), "child binding revision"
    )
    binding_digest = _require_str(binding.get("digest"), "child binding digest")

    child = loader(binding_kind, binding_id)
    if child is None:
        raise GraphVerificationError(
            "missing",
            f"child {binding_kind}:{binding_id} missing",
            path=path,
        )
    if not isinstance(child, Mapping):
        raise GraphVerificationError(
            "unexpected", "child row must be a record mapping", path=path
        )
    child_revision = _require_revision(child.get("revision"), "child revision")
    child_digest = _require_str(child.get("record_digest"), "child record_digest")
    child_workspace = _require_str(child.get("workspace_id"), "child workspace_id")
    if child_revision != binding_revision:
        raise GraphVerificationError(
            "stale_revision",
            f"child {binding_kind}:{binding_id} revision {child_revision} != "
            f"binding revision {binding_revision}",
            path=path,
        )
    if child_digest != binding_digest:
        raise GraphVerificationError(
            "tampered_digest",
            f"child {binding_kind}:{binding_id} record_digest does not match "
            f"the binding digest",
            path=path,
        )
    if child_workspace != parent_workspace:
        raise GraphVerificationError(
            "cross_workspace",
            "cross-owner: child row workspace_id does not match the parent "
            "record's workspace_id",
            path=path,
        )
    child_owner = child.get("application_id")
    if parent_owner is not None and child_owner is not None and (
        child_owner != parent_owner
    ):
        raise GraphVerificationError(
            "cross_workspace",
            "cross-owner: child application_id does not match the parent "
            "record's application_id",
            path=path,
        )


def require_exactly_one(items: Any, what: str) -> Any:
    """Return the single item of ``items`` or raise a cardinality failure."""
    if not isinstance(what, str) or not what:
        raise GraphVerificationError(
            "unexpected", "what must be a non-empty description"
        )
    if not isinstance(items, (list, tuple)):
        raise GraphVerificationError(
            "unexpected", "items must be a sized list or tuple"
        )
    if len(items) != 1:
        raise GraphVerificationError(
            "cardinality",
            f"expected exactly one {what}, found {len(items)}",
        )
    return items[0]
