"""V5-1B acceptance hardening: DEPENDENCY_SUBSTITUTION and REMOVED diff vectors.

The stage's existing semantic-diff tests assert ADDED, DIGEST_CHANGED and
PERMISSION_EXPANSION positively and REMOVED only negatively (``removed ==
set()``); DEPENDENCY_SUBSTITUTION had no positive assertion.  This file closes
that gap with exact-edge seeded diff pairs through the real service path.
"""
from __future__ import annotations

from app.models.v5_tables import (
    AIApplication,
    DependencyEdge,
    TopologyRevision,
)
from tests.unit.test_v5_system_versions import (
    _DIGEST_A,
    _DIGEST_B,
    _DIGEST_C,
    _DIGEST_D,
    _mk_envelope,
    _seed_component,
    _seed_component_revision,
    _seed_env,
    _seed_version_set_row,
    _reader_principal,
    _service,
    NOW,
    OWNER,
    PROJECT,
    WORKSPACE,
)


def _seed_edge(
    session,
    *,
    edge_id: str,
    from_component_id: str,
    to_component_id: str,
    digest: str,
    relation: str = "INVOKES",
) -> None:
    envelope = _mk_envelope(WORKSPACE, edge_id)
    session.add(
        DependencyEdge(
            edge_id=edge_id,
            workspace_id=WORKSPACE,
            application_id="app_01J0000000000001",
            from_component_id=from_component_id,
            to_component_id=to_component_id,
            relation=relation,
            required=True,
            edge_digest=digest,
            envelope_payload=envelope,
            record_digest=envelope["record_envelope"]["record_digest"],
            authority_receipt_id=envelope["record_envelope"]["authority_receipt_id"],
            recorded_by_principal=OWNER,
            created_at=NOW,
        )
    )


def _seed_topology(
    session,
    *,
    topology_id: str,
    edge_bindings: list[dict],
    digest: str,
) -> None:
    envelope = _mk_envelope(WORKSPACE, topology_id)
    session.add(
        TopologyRevision(
            topology_revision_id=topology_id,
            workspace_id=WORKSPACE,
            application_id="app_01J0000000000001",
            component_ids=["cmp_01J0000000000A01"],
            exact_edge_revision_bindings=edge_bindings,
            topology_digest=digest,
            provenance_receipt_ids=[],
            envelope_payload=envelope,
            record_digest=envelope["record_envelope"]["record_digest"],
            authority_receipt_id=envelope["record_envelope"]["authority_receipt_id"],
            recorded_by_principal=OWNER,
            created_at=NOW,
        )
    )


def _seed_substitution_pair(session) -> tuple[str, str]:
    """Base: code->model edge; Target: code->retriever edge (substitution),
    plus the model component removed and the retriever added."""
    app_envelope = _mk_envelope(WORKSPACE, "app_01J0000000000001")
    session.add(
        AIApplication(
            application_id="app_01J0000000000001",
            workspace_id=WORKSPACE,
            project_id=PROJECT,
            slug="subst-app",
            display_name="Substitution app",
            owner_principal_ids=[OWNER],
            criticality="P0",
            data_classification="INTERNAL",
            governance_mode="MANAGED",
            lifecycle_state="ACTIVE",
            revision=1,
            envelope_payload=app_envelope,
            record_digest=app_envelope["record_envelope"]["record_digest"],
            authority_receipt_id=app_envelope["record_envelope"]["authority_receipt_id"],
            recorded_by_principal=OWNER,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    for component_id, kind, name in (
        ("cmp_01J0000000000A01", "APPLICATION_CODE", "code"),
        ("cmp_01J0000000000A02", "MODEL_BINDING", "model"),
        ("cmp_01J0000000000A03", "RETRIEVER", "retriever"),
    ):
        _seed_component(session, component_id=component_id, kind=kind, logical_name=name)

    _seed_component_revision(
        session,
        revision_id="crv_01J0000000000B01",
        component_id="cmp_01J0000000000A01",
        component_kind="APPLICATION_CODE",
        configuration_digest=_DIGEST_A,
    )
    _seed_component_revision(
        session,
        revision_id="crv_01J0000000000B02",
        component_id="cmp_01J0000000000A02",
        component_kind="MODEL_BINDING",
        configuration_digest=_DIGEST_B,
    )
    _seed_component_revision(
        session,
        revision_id="crv_01J0000000000B11",
        component_id="cmp_01J0000000000A01",
        component_kind="APPLICATION_CODE",
        configuration_digest=_DIGEST_D,
    )
    _seed_component_revision(
        session,
        revision_id="crv_01J0000000000B13",
        component_id="cmp_01J0000000000A03",
        component_kind="RETRIEVER",
        configuration_digest=_DIGEST_C,
    )

    _seed_edge(
        session,
        edge_id="de_01J0000000000E01",
        from_component_id="cmp_01J0000000000A01",
        to_component_id="cmp_01J0000000000A02",
        digest=_DIGEST_A,
    )
    _seed_edge(
        session,
        edge_id="de_01J0000000000E02",
        from_component_id="cmp_01J0000000000A01",
        to_component_id="cmp_01J0000000000A03",
        digest=_DIGEST_D,
    )
    _seed_topology(
        session,
        topology_id="tpr_01J0000000000C01",
        edge_bindings=[
            {
                "kind": "DEPENDENCY_EDGE",
                "id": "de_01J0000000000E01",
                "revision": None,
                "digest": _DIGEST_A,
            }
        ],
        digest=_DIGEST_A,
    )
    _seed_topology(
        session,
        topology_id="tpr_01J0000000000C02",
        edge_bindings=[
            {
                "kind": "DEPENDENCY_EDGE",
                "id": "de_01J0000000000E02",
                "revision": None,
                "digest": _DIGEST_D,
            }
        ],
        digest=_DIGEST_D,
    )

    base = _seed_version_set_row(
        session,
        version_set_id="vset_01J0000000000C01",
        bindings=[
            {
                "kind": "COMPONENT_REVISION",
                "id": "crv_01J0000000000B01",
                "revision": None,
                "digest": _DIGEST_A,
            },
            {
                "kind": "COMPONENT_REVISION",
                "id": "crv_01J0000000000B02",
                "revision": None,
                "digest": _DIGEST_B,
            },
        ],
        topology_binding={
            "kind": "TOPOLOGY_REVISION",
            "id": "tpr_01J0000000000C01",
            "revision": None,
            "digest": _DIGEST_A,
        },
    )
    target = _seed_version_set_row(
        session,
        version_set_id="vset_01J0000000000C02",
        bindings=[
            {
                "kind": "COMPONENT_REVISION",
                "id": "crv_01J0000000000B11",
                "revision": None,
                "digest": _DIGEST_D,
            },
            {
                "kind": "COMPONENT_REVISION",
                "id": "crv_01J0000000000B13",
                "revision": None,
                "digest": _DIGEST_C,
            },
        ],
        topology_binding={
            "kind": "TOPOLOGY_REVISION",
            "id": "tpr_01J0000000000C02",
            "revision": None,
            "digest": _DIGEST_D,
        },
        version_set_digest=_DIGEST_D,
    )
    session.commit()
    return base.system_version_set_id, target.system_version_set_id


def test_diff_dependency_substitution_and_removal_vectors(sqlite_session) -> None:
    _seed_env(sqlite_session)
    base_id, target_id = _seed_substitution_pair(sqlite_session)
    diff = _service(sqlite_session).diff_system_versions(
        base_id,
        target_id,
        principal=_reader_principal(),
        request_id="req_01J000000000000S",
    )
    sqlite_session.commit()

    # REMOVED: model is in base but not target.
    removed = {item.component_id: item for item in diff.removed}
    assert set(removed) == {"cmp_01J0000000000A02"}
    assert removed["cmp_01J0000000000A02"].diff_kind == "REMOVED"

    # ADDED: retriever is in target but not base.
    added = {item.component_id: item for item in diff.added}
    assert set(added) == {"cmp_01J0000000000A03"}
    assert added["cmp_01J0000000000A03"].diff_kind == "ADDED"

    # DIGEST_CHANGED: code revision digest changed.
    changed = {item.component_id: item for item in diff.changed}
    assert set(changed) == {"cmp_01J0000000000A01"}
    assert changed["cmp_01J0000000000A01"].diff_kind == "DIGEST_CHANGED"
    assert changed["cmp_01J0000000000A01"].base_digest == _DIGEST_A
    assert changed["cmp_01J0000000000A01"].target_digest == _DIGEST_D

    # DEPENDENCY_SUBSTITUTION: code's INVOKES edge now points at the retriever.
    substitutions = list(diff.dependency_substitutions)
    assert len(substitutions) == 1
    substitution = substitutions[0]
    assert substitution.component_id == "cmp_01J0000000000A01"
    assert substitution.diff_kind == "DEPENDENCY_SUBSTITUTION"
    assert substitution.details["relation"] == "INVOKES"
    assert substitution.details["base_to_component_id"] == "cmp_01J0000000000A02"
    assert substitution.details["target_to_component_id"] == "cmp_01J0000000000A03"

    # No permission expansion: neither side carries a POLICY revision change.
    assert diff.policy_permission_expansions == []


def test_diff_unchanged_edges_do_not_emit_substitution(sqlite_session) -> None:
    """A target that keeps the same edge emits no substitution entry."""
    _seed_env(sqlite_session)
    base_id, target_id = _seed_substitution_pair(sqlite_session)
    # Re-target a third version set that repeats the base bindings exactly.
    same = _seed_version_set_row(
        sqlite_session,
        version_set_id="vset_01J0000000000C03",
        bindings=[
            {
                "kind": "COMPONENT_REVISION",
                "id": "crv_01J0000000000B01",
                "revision": None,
                "digest": _DIGEST_A,
            },
            {
                "kind": "COMPONENT_REVISION",
                "id": "crv_01J0000000000B02",
                "revision": None,
                "digest": _DIGEST_B,
            },
        ],
        topology_binding={
            "kind": "TOPOLOGY_REVISION",
            "id": "tpr_01J0000000000C01",
            "revision": None,
            "digest": _DIGEST_A,
        },
        version_set_digest=_DIGEST_B,
    )
    sqlite_session.commit()
    diff = _service(sqlite_session).diff_system_versions(
        base_id,
        same.system_version_set_id,
        principal=_reader_principal(),
        request_id="req_01J000000000000T",
    )
    sqlite_session.commit()
    assert diff.dependency_substitutions == []
    assert diff.added == []
    assert diff.removed == []
    assert diff.changed == []
