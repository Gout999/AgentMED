"""V5-1B acceptance hardening: DEPENDENCY_SUBSTITUTION and REMOVED diff vectors.

The stage's existing semantic-diff tests assert ADDED, DIGEST_CHANGED and
PERMISSION_EXPANSION positively and REMOVED only negatively (``removed ==
set()``); DEPENDENCY_SUBSTITUTION had no positive assertion.  This file closes
that gap with exact-edge seeded diff pairs through the real service path.
"""
from __future__ import annotations

from app.models.v5_tables import (
    AIApplication,
    ComponentRevision,
    DependencyEdge,
    Environment,
    TopologyRevision,
)
from app.services.system_versions import SystemVersionsService
from app.utils.v4_integrity import canonical_digest
from tests.unit.test_v5_system_versions import (
    _DIGEST_A,
    _DIGEST_B,
    _DIGEST_C,
    _DIGEST_D,
    _mk_envelope,
    _seed_component,
    _seed_component_revision,
    _seed_diff_application_environment,
    _seed_env,
    _seed_version_set_row,
    _diff_service,
    _reader_principal,
    NOW,
    OWNER,
    WORKSPACE,
)


def _seed_edge(
    session,
    *,
    edge_id: str,
    from_component_id: str,
    to_component_id: str,
    relation: str = "INVOKES",
) -> DependencyEdge:
    digest = canonical_digest(
        {
            "from_component_id": from_component_id,
            "to_component_id": to_component_id,
            "relation": relation,
            "required": True,
        }
    )
    payload = {
        "edge_id": edge_id,
        "workspace_id": WORKSPACE,
        "application_id": "app_01J0000000000001",
        "from_component_id": from_component_id,
        "to_component_id": to_component_id,
        "relation": relation,
        "required": True,
        "edge_digest": digest,
    }
    envelope = _mk_envelope(WORKSPACE, edge_id, payload)
    row = DependencyEdge(
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
    session.add(row)
    session.flush()
    return row


def _seed_topology(
    session,
    *,
    topology_id: str,
    edges: list[DependencyEdge],
    component_ids: list[str],
) -> TopologyRevision:
    application = session.get(AIApplication, "app_01J0000000000001")
    environment = session.get(Environment, "env_01J0000000000001")
    provenance_receipt_ids = sorted(
        {application.authority_receipt_id, environment.authority_receipt_id}
    )
    edge_bindings = [
        {
            "kind": "DEPENDENCY_EDGE",
            "id": edge.edge_id,
            "revision": 1,
            "digest": edge.record_digest,
        }
        for edge in edges
    ]
    digest = SystemVersionsService._topology_digest(
        edges, component_ids=component_ids
    )
    payload = {
        "topology_revision_id": topology_id,
        "workspace_id": WORKSPACE,
        "application_id": "app_01J0000000000001",
        "component_ids": sorted(component_ids),
        "exact_edge_revision_bindings": edge_bindings,
        "topology_digest": digest,
        "provenance_receipt_ids": provenance_receipt_ids,
    }
    envelope = _mk_envelope(WORKSPACE, topology_id, payload)
    row = TopologyRevision(
            topology_revision_id=topology_id,
            workspace_id=WORKSPACE,
            application_id="app_01J0000000000001",
            component_ids=payload["component_ids"],
            exact_edge_revision_bindings=edge_bindings,
            topology_digest=digest,
            provenance_receipt_ids=provenance_receipt_ids,
            envelope_payload=envelope,
            record_digest=envelope["record_envelope"]["record_digest"],
            authority_receipt_id=envelope["record_envelope"]["authority_receipt_id"],
            recorded_by_principal=OWNER,
            created_at=NOW,
        )
    session.add(row)
    session.flush()
    return row


def _seed_substitution_pair(session) -> tuple[str, str]:
    """Base: code->model edge; Target: code->retriever edge (substitution),
    plus the model component removed and the retriever added."""
    _seed_diff_application_environment(session)
    for component_id, kind, name in (
        ("cmp_01J0000000000A01", "APPLICATION_CODE", "code"),
        ("cmp_01J0000000000A02", "MODEL_BINDING", "model"),
        ("cmp_01J0000000000A03", "RETRIEVER", "retriever"),
        ("cmp_01J0000000000A04", "PROMPT", "shared-prompt"),
    ):
        _seed_component(session, component_id=component_id, kind=kind, logical_name=name)

    base_code = _seed_component_revision(
        session,
        revision_id="crv_01J0000000000B01",
        component_id="cmp_01J0000000000A01",
        component_kind="APPLICATION_CODE",
        configuration_digest=_DIGEST_A,
    )
    base_model = _seed_component_revision(
        session,
        revision_id="crv_01J0000000000B02",
        component_id="cmp_01J0000000000A02",
        component_kind="MODEL_BINDING",
        configuration_digest=_DIGEST_B,
    )
    target_code = _seed_component_revision(
        session,
        revision_id="crv_01J0000000000B11",
        component_id="cmp_01J0000000000A01",
        component_kind="APPLICATION_CODE",
        configuration_digest=_DIGEST_D,
    )
    target_retriever = _seed_component_revision(
        session,
        revision_id="crv_01J0000000000B13",
        component_id="cmp_01J0000000000A03",
        component_kind="RETRIEVER",
        configuration_digest=_DIGEST_C,
    )
    shared_prompt = _seed_component_revision(
        session,
        revision_id="crv_01J0000000000B14",
        component_id="cmp_01J0000000000A04",
        component_kind="PROMPT",
        configuration_digest=_DIGEST_B,
    )

    base_edge = _seed_edge(
        session,
        edge_id="de_01J0000000000E01",
        from_component_id="cmp_01J0000000000A01",
        to_component_id="cmp_01J0000000000A02",
    )
    target_edge = _seed_edge(
        session,
        edge_id="de_01J0000000000E02",
        from_component_id="cmp_01J0000000000A01",
        to_component_id="cmp_01J0000000000A03",
    )
    # This unchanged fan-out edge is deliberately last in both topology
    # bindings.  The previous single-value `(from, relation)` map overwrote the
    # changed edge with this shared one and incorrectly reported no change.
    shared_edge = _seed_edge(
        session,
        edge_id="de_01J0000000000E03",
        from_component_id="cmp_01J0000000000A01",
        to_component_id="cmp_01J0000000000A04",
    )
    base_topology = _seed_topology(
        session,
        topology_id="tpr_01J0000000000C01",
        edges=[base_edge, shared_edge],
        component_ids=[
            base_code.component_id,
            base_model.component_id,
            shared_prompt.component_id,
        ],
    )
    target_topology = _seed_topology(
        session,
        topology_id="tpr_01J0000000000C02",
        edges=[target_edge, shared_edge],
        component_ids=[
            target_code.component_id,
            target_retriever.component_id,
            shared_prompt.component_id,
        ],
    )

    base = _seed_version_set_row(
        session,
        version_set_id="vset_01J0000000000C01",
        bindings=[
            {
                "kind": "COMPONENT_REVISION",
                "id": revision.component_revision_id,
                "revision": 1,
                "digest": revision.record_digest,
            }
            for revision in (base_code, base_model, shared_prompt)
        ],
        topology_binding={
            "kind": "TOPOLOGY_REVISION",
            "id": base_topology.topology_revision_id,
            "revision": 1,
            "digest": base_topology.record_digest,
        },
    )
    target = _seed_version_set_row(
        session,
        version_set_id="vset_01J0000000000C02",
        bindings=[
            {
                "kind": "COMPONENT_REVISION",
                "id": revision.component_revision_id,
                "revision": 1,
                "digest": revision.record_digest,
            }
            for revision in (target_code, target_retriever, shared_prompt)
        ],
        topology_binding={
            "kind": "TOPOLOGY_REVISION",
            "id": target_topology.topology_revision_id,
            "revision": 1,
            "digest": target_topology.record_digest,
        },
    )
    session.commit()
    return base.system_version_set_id, target.system_version_set_id


def test_diff_dependency_substitution_and_removal_vectors(sqlite_session) -> None:
    _seed_env(sqlite_session)
    base_id, target_id = _seed_substitution_pair(sqlite_session)
    diff = _diff_service(sqlite_session).diff_system_versions(
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
    assert changed["cmp_01J0000000000A01"].base_digest == sqlite_session.get(
        ComponentRevision, "crv_01J0000000000B01"
    ).configuration_digest
    assert changed["cmp_01J0000000000A01"].target_digest == sqlite_session.get(
        ComponentRevision, "crv_01J0000000000B11"
    ).configuration_digest

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
    base_id, _target_id = _seed_substitution_pair(sqlite_session)
    diff = _diff_service(sqlite_session).diff_system_versions(
        base_id,
        base_id,
        principal=_reader_principal(),
        request_id="req_01J000000000000T",
    )
    sqlite_session.commit()
    assert diff.dependency_substitutions == []
    assert diff.added == []
    assert diff.removed == []
    assert diff.changed == []
