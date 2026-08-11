"""V5-1B system versions service tests (fail-closed orchestration).

Covers: immutable VersionSet records, exact graph/component binding, semantic
diff (same label/different digest, dependency substitution, policy permission
expansion), identity assurance downgrade honesty, trusted manifest import
allowlist, ALL_OR_NOTHING atomicity, idempotent replay (key + manifest digest),
bootstrap assignment CAS constraints and adversarial cases.
"""
from __future__ import annotations

from datetime import timedelta

import sqlalchemy as sa
import pytest
from sqlalchemy import select

from app.models import Audit, Event, Outbox
from app.models.v4_tables import (
    AuthorityReceipt,
    PublicCommandIdempotency,
    PublicPrincipal,
)
from app.models.v5_tables import (
    AIApplication,
    AIApplicationLifecycleRevision,
    BootstrapAttestation,
    ComponentRevision,
    DependencyEdge,
    Environment,
    SystemAssignment,
    SystemComponent,
    SystemComponentLifecycleRevision,
    SystemVersionSet,
    TopologyRevision,
)
from app.public_api.v5_models import SystemManifestImportRequest
from app.services.application_catalog import ApplicationCatalogService
from app.services.system_versions import SystemVersionsError, SystemVersionsService
from app.services.v4_audit import V4AuditService
from app.utils.v5_integrity import v5_record_digest

from test_v5_application_catalog import (
    _app_request,
    _claims,
    _principal_context,
    _seed_principal,
    _seed_v5_controller,
    NOW,
    OWNER,
    PROJECT,
    WORKSPACE,
)
from app.services.v5_authority import (
    build_v5_controller_registration_record,
)
from app.models.v4_tables import ControllerRegistration
from app.utils.ids import (
    new_authority_receipt_id,
    new_component_revision_id,
    new_topology_revision_id,
    new_system_version_set_id,
)
from app.utils.v4_integrity import canonical_digest

VERSION_CONTROLLER_PRINCIPAL = "prn_01J000000000000C"
IMPORT_PRINCIPAL = "prn_01J000000000000A"
VERSION_CONTROLLER_REGISTRATION = "creg_01J000000000000B"

_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64
_DIGEST_D = "sha256:" + "d" * 64


def _version_controller_commands() -> list[str]:
    return [
        "component-revisions.record",
        "topology-revisions.record",
        "system-versions.record",
        "bootstrap-attestations.record",
        "system-assignments.record",
    ]


def _seed_version_controller(session) -> None:
    audit = V4AuditService(session, clock=lambda: NOW)
    recorded = audit.record(
        workspace_id=WORKSPACE,
        actor_principal=OWNER,
        action="controllers.register",
        target=VERSION_CONTROLLER_REGISTRATION,
        params={
            "owner": "version-controller",
            "service_identity_digest": canonical_digest(
                {
                    "schema_version": "1.0",
                    "workspace_id": WORKSPACE,
                    "owner": "version-controller",
                    "controller_principal": VERSION_CONTROLLER_PRINCIPAL,
                    "principal_type": "CONTROLLER_SERVICE",
                    "service": "caseloop-control-plane",
                }
            ),
        },
        transaction_id="txn_v5_version_controller",
        evidence_refs={
            "owner": "version-controller",
            "controller_registration_id": VERSION_CONTROLLER_REGISTRATION,
            "controller_principal": VERSION_CONTROLLER_PRINCIPAL,
        },
        occurred_at=NOW,
    )
    built = build_v5_controller_registration_record(
        controller_registration_id=VERSION_CONTROLLER_REGISTRATION,
        workspace_id=WORKSPACE,
        owner="version-controller",
        controller_principal=VERSION_CONTROLLER_PRINCIPAL,
        allowed_commands=_version_controller_commands(),
        service_identity_digest=canonical_digest(
            {
                "schema_version": "1.0",
                "workspace_id": WORKSPACE,
                "owner": "version-controller",
                "controller_principal": VERSION_CONTROLLER_PRINCIPAL,
                "principal_type": "CONTROLLER_SERVICE",
                "service": "caseloop-control-plane",
            }
        ),
        registered_by_human_principal=OWNER,
        registration_audit_ref=recorded.audit_ref,
        valid_from=NOW,
        registered_at=NOW,
    )
    session.add(ControllerRegistration(**built.row_values))
    session.flush()


def _seed_env(session) -> None:
    _seed_principal(session, principal_id=OWNER, scopes=["signals:write", "cases:read"])
    _seed_principal(
        session,
        principal_id=IMPORT_PRINCIPAL,
        scopes=["system_manifests:import", "system_versions:read"],
        trust_roles=["integrator"],
    )
    _seed_v5_controller(session)
    _seed_version_controller(session)
    session.commit()


def _service(session, **kwargs) -> SystemVersionsService:
    return SystemVersionsService(session, clock=lambda: NOW, **kwargs)


def _import_principal(**overrides):
    return _principal_context(
        principal_id=IMPORT_PRINCIPAL,
        scopes=["system_manifests:import", "system_versions:read"],
        required_scope="system_manifests:import",
        **overrides,
    )


def _reader_principal(**overrides):
    return _principal_context(
        principal_id=IMPORT_PRINCIPAL,
        scopes=["system_manifests:import", "system_versions:read"],
        required_scope="system_versions:read",
        **overrides,
    )


def _manifest(**overrides):
    base = {
        "schema_version": "2.0",
        "application": {
            "project_id": PROJECT,
            "slug": "llm-cli",
            "display_name": "LLM CLI",
            "owner_principal_ids": [OWNER],
            "criticality": "P0",
            "data_classification": "INTERNAL",
            "governance_mode": "MANAGED",
        },
        "environment": {"logical_name": "prod", "risk_classification": "MEDIUM"},
        "components": [
            {
                "logical_name": "llm-code",
                "component_kind": "APPLICATION_CODE",
                "owner_principal_ids": [OWNER],
                "criticality": "P0",
                "data_classification": "INTERNAL",
                "permission_classification": "READ_WRITE",
                "effect_classification": "LOCAL",
                "revision": {
                    "identity_locator": {"type": "git", "path": "."},
                    "identity_assurance": "IMMUTABLE_DIGEST",
                    "content_digest": _DIGEST_A,
                    "artifact_refs": [{"kind": "git_commit", "ref": "abc123", "digest": _DIGEST_B}],
                },
            },
            {
                "logical_name": "llm-model",
                "component_kind": "MODEL_BINDING",
                "owner_principal_ids": [OWNER],
                "criticality": "P1",
                "data_classification": "INTERNAL",
                "permission_classification": "READ_ONLY",
                "effect_classification": "NONE",
                "revision": {
                    "identity_locator": {"type": "provider", "name": "openai-gpt4o"},
                    "identity_assurance": "MUTABLE_ALIAS",
                    "provider_origin": "https://api.openai.com",
                    "resolved_at": "2026-08-11T08:00:00Z",
                },
            },
        ],
        "dependency_edges": [
            {
                "from_component": "llm-code",
                "to_component": "llm-model",
                "relation": "INVOKES",
                "required": True,
            }
        ],
        "approver_policy": {
            "logical_name": "human-approver-policy",
            "component_kind": "POLICY",
            "owner_principal_ids": [OWNER],
            "criticality": "P1",
            "data_classification": "CONFIDENTIAL",
            "permission_classification": "READ_ONLY",
            "effect_classification": "NONE",
            "revision": {
                "identity_locator": {"type": "file", "path": "policies/approvers.yaml"},
                "identity_assurance": "UNKNOWN",
                "unknown_reason": "policy file has no digest source",
            },
        },
    }
    return SystemManifestImportRequest.model_validate({**base, **overrides})


def _import(session, manifest, *, key="import-key-0001", principal=None, request_id="req_01J000000000000A"):
    return _service(session).import_manifest(
        manifest,
        principal=principal or _import_principal(),
        idempotency_key=key,
        request_id=request_id,
    )


def _count(session, model) -> int:
    return int(session.scalar(select(sa.func.count()).select_from(model)) or 0)


def _mk_envelope(workspace_id: str, subject_id: str) -> dict:
    payload = {
        "record_envelope": {
            "schema_version": "2.0",
            "workspace_id": workspace_id,
            "revision": 1,
            "recorded_by_principal": OWNER,
            "recorded_at": "2026-08-11T09:00:00Z",
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
            "record_digest": "",
            "authority_receipt_id": new_authority_receipt_id(),
        }
    }
    payload["record_envelope"]["record_digest"] = v5_record_digest(payload)
    return payload


# ------------------------------------------------------------- happy path import


def test_trusted_manifest_import_creates_exact_construct_set(sqlite_session) -> None:
    _seed_env(sqlite_session)
    response = _import(sqlite_session, _manifest())
    sqlite_session.commit()

    assert response.schema_version == "2.0"
    assert response.system_version_set.manifest_digest is not None
    assert response.idempotency.replayed is False
    assert response.idempotency.receipt.intent == "system-manifests.import"
    assert response.system_assignment.generation == 1
    assert response.system_assignment.transition_kind == "BOOTSTRAP"
    assert response.system_assignment.exact_previous_assignment_binding_or_null is None
    assert response.system_assignment.exposure == "EXPOSED"
    assert (
        response.system_assignment.exact_assignment_authority_binding.binding_kind
        == "BOOTSTRAP_ATTESTATION"
    )
    assert (
        response.bootstrap_attestation.attestation_scope
        == "INITIAL_DESIRED_ASSIGNMENT"
    )
    # approver policy revision is recorded but NOT part of the runtime VersionSet
    assert response.approver_policy_revision is not None
    bound_ids = {
        binding.id for binding in response.system_version_set.exact_component_revision_bindings
    }
    assert response.approver_policy_revision.component_revision_id not in bound_ids

    assert _count(sqlite_session, AIApplication) == 1
    assert _count(sqlite_session, Environment) == 1
    assert _count(sqlite_session, SystemComponent) == 3  # 2 runtime + 1 policy
    assert _count(sqlite_session, DependencyEdge) == 1
    assert _count(sqlite_session, ComponentRevision) == 3  # 2 runtime + 1 policy
    assert _count(sqlite_session, TopologyRevision) == 1
    assert _count(sqlite_session, SystemVersionSet) == 1
    assert _count(sqlite_session, BootstrapAttestation) == 1
    assert _count(sqlite_session, SystemAssignment) == 1
    assert _count(sqlite_session, AIApplicationLifecycleRevision) == 2
    assert _count(sqlite_session, SystemComponentLifecycleRevision) == 6
    assert response.application.lifecycle_state == "ACTIVE"
    assert response.application.record_envelope.revision == 2
    assert all(item.lifecycle_state == "ACTIVE" for item in response.components)
    assert all(item.record_envelope.revision == 2 for item in response.components)
    active_components = {
        item.component_id: item for item in response.components
    }
    for revision in [*response.component_revisions, response.approver_policy_revision]:
        assert revision is not None
        binding = revision.exact_system_component_binding
        component = active_components.get(revision.component_id)
        if component is None:
            component = next(
                row
                for row in sqlite_session.scalars(select(SystemComponent)).all()
                if row.component_id == revision.component_id
            ).envelope_payload
            assert binding.revision == component["record_envelope"]["revision"]
            assert binding.digest == component["record_envelope"]["record_digest"]
        else:
            assert binding.revision == 2
            assert binding.digest == component.record_envelope.record_digest
        assert binding.kind == "SYSTEM_COMPONENT"
        assert binding.id == revision.component_id
    assert _count(sqlite_session, Event) == 17
    assert _count(sqlite_session, Outbox) == 17
    assert _count(sqlite_session, AuthorityReceipt) == 17
    assert _count(sqlite_session, PublicCommandIdempotency) == 1

    audits = sqlite_session.scalars(
        select(Audit).where(Audit.contract_version == "v4")
    ).all()
    actions = {row.action for row in audits}
    assert "system-manifests.import" in actions
    root_audit = next(row for row in audits if row.action == "system-manifests.import")

    major2_types = {
        "environment.registered",
        "dependency_edge.recorded",
        "component_revision.recorded",
        "topology_revision.recorded",
        "system_version_set.recorded",
        "bootstrap_attestation.recorded",
        "system_assignment.recorded",
    }
    major2_events = list(
        sqlite_session.scalars(select(Event).where(Event.event_type.in_(major2_types))).all()
    )
    assert {event.event_type for event in major2_events} == major2_types
    assert all(
        event.contract_version == "v5"
        and event.event_version == "2.0"
        and event.event_contract_major == 2
        and event.exact_subject_binding["revision"] == 1
        and event.authority_receipt_id is not None
        for event in major2_events
    )
    major2_receipts = [
        sqlite_session.get(AuthorityReceipt, event.authority_receipt_id)
        for event in major2_events
    ]
    assert all(
        receipt is not None
        and receipt.subject_revision == 1
        and receipt.receipt_payload["source_event_id"] == receipt.event_id
        and receipt.receipt_payload["controller_registration"]["contract_major"] == 1
        and "resource" not in receipt.receipt_payload
        and "event_id" not in receipt.receipt_payload
        for receipt in major2_receipts
    )
    outboxes = list(
        sqlite_session.scalars(
            select(Outbox).where(
                Outbox.source_event_id.in_([event.event_id for event in major2_events])
            )
        ).all()
    )
    assert len(outboxes) == len(major2_events)
    assert all(
        row.contract_version == "v5" and row.event_contract_major == 2
        for row in outboxes
    )
    transaction_ids = {root_audit.transaction_id}
    transaction_ids.update(event.transaction_id for event in sqlite_session.scalars(select(Event)))
    transaction_ids.update(
        receipt.transaction_id
        for receipt in sqlite_session.scalars(select(AuthorityReceipt))
    )
    transaction_ids.update(row.transaction_id for row in sqlite_session.scalars(select(Outbox)))
    assert transaction_ids == {root_audit.transaction_id}
    assert response.idempotency.receipt.audit_ref == f"audit://{root_audit.audit_id}"
    assert response.topology_revision.provenance_receipt_ids
    assert all(
        receipt_id.startswith("arec_")
        for receipt_id in response.topology_revision.provenance_receipt_ids
    )
    edge_row = sqlite_session.get(
        DependencyEdge, response.dependency_edges[0].edge_id
    )
    edge_binding = response.topology_revision.exact_edge_revision_bindings[0]
    assert edge_row is not None
    assert edge_binding.revision == 1
    assert edge_binding.digest == edge_row.record_digest

    assert root_audit.params_digest == canonical_digest(
        {
            "authenticated_request_digest": response.idempotency.receipt.request_fingerprint,
            "manifest_digest": response.manifest_digest,
            "idempotency_key": response.idempotency.receipt.idempotency_key,
        }
    )
    assert {
        "controller.application.registered",
        "controller.application.activated",
        "controller.system_component.registered",
        "controller.system_component.activated",
        "controller.component_revision.recorded",
        "controller.topology_revision.recorded",
        "controller.system_version_set.recorded",
        "controller.bootstrap_attestation.recorded",
        "controller.system_assignment.recorded",
    } <= actions


def test_same_key_replay_preserves_canonical_logical_graph_order(sqlite_session) -> None:
    _seed_env(sqlite_session)
    base = _manifest().model_dump(mode="json")
    code, model = base["components"]
    skill = {
        **code,
        "logical_name": "aaa-skill",
        "component_kind": "SKILL",
        "criticality": "P1",
        "permission_classification": "READ_ONLY",
        "effect_classification": "NONE",
        "revision": {
            "identity_locator": {"type": "git", "path": "skills/aaa"},
            "identity_assurance": "IMMUTABLE_DIGEST",
            "content_digest": _DIGEST_C,
        },
    }
    base["components"] = [model, code, skill]
    base["dependency_edges"] = [
        {
            "from_component": "llm-model",
            "to_component": "aaa-skill",
            "relation": "INVOKES",
            "required": True,
        },
        {
            "from_component": "llm-code",
            "to_component": "llm-model",
            "relation": "INVOKES",
            "required": True,
        },
    ]
    manifest = SystemManifestImportRequest.model_validate(base)

    created = _import(sqlite_session, manifest, key="import-key-order")
    sqlite_session.commit()
    assert [item.logical_name for item in created.components] == [
        "aaa-skill",
        "llm-code",
        "llm-model",
    ]
    assert [item.logical_name for item in created.component_revisions] == [
        "aaa-skill",
        "llm-code",
        "llm-model",
    ]
    names_by_id = {item.component_id: item.logical_name for item in created.components}
    assert [
        (names_by_id[item.from_component_id], names_by_id[item.to_component_id])
        for item in created.dependency_edges
    ] == [("llm-code", "llm-model"), ("llm-model", "aaa-skill")]

    replayed = _import(sqlite_session, manifest, key="import-key-order")
    created_wire = created.model_dump(mode="json")
    replayed_wire = replayed.model_dump(mode="json")
    created_wire["idempotency"]["replayed"] = True
    assert replayed_wire == created_wire


def test_import_identity_assurance_is_recorded_honestly(sqlite_session) -> None:
    _seed_env(sqlite_session)
    response = _import(sqlite_session, _manifest())
    sqlite_session.commit()
    assurances = {
        entry.identity_assurance
        for entry in response.system_version_set.identity_assurance_summary.component_assurances
    }
    assert {"IMMUTABLE_DIGEST", "MUTABLE_ALIAS"} <= assurances
    model_rev = sqlite_session.get(ComponentRevision, response.component_revisions[1].component_revision_id)
    assert model_rev is not None and model_rev.identity_assurance == "MUTABLE_ALIAS"


def test_mutable_alias_cannot_masquerade_as_immutable(sqlite_session) -> None:
    _seed_env(sqlite_session)
    with pytest.raises(Exception):
        SystemManifestImportRequest.model_validate(
            {
                **_manifest().model_dump(mode="json"),
                "components": [
                    {
                        **_manifest().components[0].model_dump(mode="json"),
                        "revision": {
                            "identity_locator": {"type": "provider"},
                            "identity_assurance": "MUTABLE_ALIAS",
                            "content_digest": _DIGEST_A,
                        },
                    }
                ],
            }
        )


def test_manifest_requires_application_code_and_rejects_silent_prereq(sqlite_session) -> None:
    _seed_env(sqlite_session)
    # A manifest without any APPLICATION_CODE component is rejected before any
    # record is written (system-versions.record never silently creates
    # prerequisites).
    payload = _manifest().model_dump(mode="json")
    payload["components"] = [
        {
            "logical_name": "only-retriever",
            "component_kind": "RETRIEVER",
            "owner_principal_ids": [OWNER],
            "criticality": "P1",
            "data_classification": "INTERNAL",
            "permission_classification": "READ_ONLY",
            "effect_classification": "NONE",
            "revision": {
                "identity_locator": {"type": "dir", "path": "retrievers/"},
                "identity_assurance": "UNKNOWN",
                "unknown_reason": "no digest",
            },
        }
    ]
    payload["dependency_edges"] = []
    with pytest.raises(Exception):
        SystemManifestImportRequest.model_validate(payload)


def test_manifest_edge_cycle_and_unknown_reference_rejected(sqlite_session) -> None:
    _seed_env(sqlite_session)
    payload = _manifest().model_dump(mode="json")
    payload["dependency_edges"] = [
        {"from_component": "llm-code", "to_component": "llm-model", "relation": "DEPENDS_ON", "required": True},
        {"from_component": "llm-model", "to_component": "llm-code", "relation": "DEPENDS_ON", "required": True},
    ]
    with pytest.raises(Exception):
        SystemManifestImportRequest.model_validate(payload)
    payload = _manifest().model_dump(mode="json")
    payload["dependency_edges"] = [
        {"from_component": "llm-code", "to_component": "ghost", "relation": "DEPENDS_ON", "required": True},
    ]
    with pytest.raises(Exception):
        SystemManifestImportRequest.model_validate(payload)


def test_import_principal_allowlist_rejects_connector_and_missing_scope(sqlite_session) -> None:
    _seed_env(sqlite_session)
    # A connector principal is not allowed to import, even when it holds the
    # scope: the server row is human-typed, so the mismatch is TOKEN_INVALID.
    from app.public_api.auth_contract import AcceptedPrincipalContext
    from app.utils.v4_integrity import canonical_digest
    from test_v5_application_catalog import (
        AUDIENCES,
        ISSUER,
        SUBJECT,
    )

    connector_claims = canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": SUBJECT,
            "principal_type": "connector",
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE,
            "project_ids": [PROJECT],
            "environment_ids": [],
            "scopes": ["system_manifests:import", "system_versions:read"],
        }
    )
    connector = AcceptedPrincipalContext.model_validate(
        {
            "schema_version": "1.0",
            "principal_id": IMPORT_PRINCIPAL,
            "principal_type": "connector",
            "issuer": ISSUER,
            "subject": SUBJECT,
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE,
            "project_ids": [PROJECT],
            "environment_ids": [],
            "scopes": ["system_manifests:import", "system_versions:read"],
            "credential_id": "cred_01J000000000000A",
            "jti_digest": "sha256:" + "a" * 64,
            "issued_at": NOW - timedelta(minutes=10),
            "not_before": NOW - timedelta(minutes=5),
            "expires_at": NOW + timedelta(days=30),
            "revoked_at": None,
            "revocation_checked_at": NOW,
            "requested_context": {
                "workspace_id": WORKSPACE,
                "project_id": PROJECT,
                "environment_id": None,
                "required_scope": "system_manifests:import",
            },
            "evaluated_at": NOW,
            "claims_digest": connector_claims,
        }
    )
    with pytest.raises(SystemVersionsError) as raised:
        _import(sqlite_session, _manifest(), principal=connector)
    assert raised.value.code == "TOKEN_INVALID"
    sqlite_session.rollback()

    # A valid human without the import scope: the row exists but its granted
    # scopes lack system_manifests:import -> audited SCOPE_FORBIDDEN.
    import_outsider = "prn_01J00000000000D1"
    _seed_principal(
        sqlite_session,
        principal_id=import_outsider,
        scopes=["applications:read"],
    )
    sqlite_session.commit()
    no_scope = _principal_context(
        principal_id=import_outsider,
        scopes=["applications:read"],
        required_scope="applications:read",
    )
    with pytest.raises(SystemVersionsError) as raised:
        _import(sqlite_session, _manifest(), principal=no_scope)
    assert raised.value.code == "SCOPE_FORBIDDEN"
    sqlite_session.rollback()


def test_audit_failure_rolls_back_every_construct(sqlite_session) -> None:
    _seed_env(sqlite_session)
    failing_audit = V4AuditService(sqlite_session, clock=lambda: NOW, force_fail=False, fail_on_call=1)
    with pytest.raises(SystemVersionsError) as raised:
        _service(sqlite_session, audit_service=failing_audit).import_manifest(
            _manifest(),
            principal=_import_principal(),
            idempotency_key="import-key-0001",
            request_id="req_01J000000000000A",
        )
    assert raised.value.code == "AUDIT_UNAVAILABLE"
    sqlite_session.rollback()
    for model in (
        AIApplication,
        Environment,
        SystemComponent,
        DependencyEdge,
        ComponentRevision,
        TopologyRevision,
        SystemVersionSet,
        BootstrapAttestation,
        SystemAssignment,
        Event,
        Outbox,
        AuthorityReceipt,
        PublicCommandIdempotency,
    ):
        assert _count(sqlite_session, model) == 0, model.__name__


def test_failure_after_activation_permit_rolls_back_lifecycle_and_terminal_idempotency(
    sqlite_session,
) -> None:
    _seed_env(sqlite_session)
    failing_audit = V4AuditService(
        sqlite_session, clock=lambda: NOW, force_fail=False, fail_on_call=3
    )
    with pytest.raises(SystemVersionsError) as raised:
        _service(sqlite_session, audit_service=failing_audit).import_manifest(
            _manifest(),
            principal=_import_principal(),
            idempotency_key="import-late-failure",
            request_id="req_01J000000000000F",
        )
    assert raised.value.code == "AUDIT_UNAVAILABLE"
    sqlite_session.rollback()
    for model in (
        AIApplication,
        AIApplicationLifecycleRevision,
        SystemComponent,
        SystemComponentLifecycleRevision,
        Event,
        Outbox,
        AuthorityReceipt,
        PublicCommandIdempotency,
    ):
        assert _count(sqlite_session, model) == 0, model.__name__


def test_service_never_commits(sqlite_session, monkeypatch) -> None:
    _seed_env(sqlite_session)
    commit = lambda: (_ for _ in ()).throw(AssertionError("service must not commit"))
    monkeypatch.setattr(sqlite_session, "commit", commit)
    _import(sqlite_session, _manifest())


# ------------------------------------------------------------------ idempotency


def test_same_key_replay_returns_same_records(sqlite_session) -> None:
    _seed_env(sqlite_session)
    first = _import(sqlite_session, _manifest(), key="import-key-0001")
    sqlite_session.commit()
    replay = _import(
        sqlite_session,
        _manifest(),
        key="import-key-0001",
        request_id="req_01J000000000000B",
    )
    assert replay.idempotency.replayed is True
    assert (
        replay.system_version_set.system_version_set_id
        == first.system_version_set.system_version_set_id
    )
    assert _count(sqlite_session, SystemVersionSet) == 1
    assert _count(sqlite_session, SystemAssignment) == 1
    sqlite_session.commit()


def test_same_key_replay_rechecks_current_import_trust_role(sqlite_session) -> None:
    _seed_env(sqlite_session)
    _import(sqlite_session, _manifest(), key="import-key-0001")
    sqlite_session.commit()
    principal_row = sqlite_session.get(PublicPrincipal, IMPORT_PRINCIPAL)
    assert principal_row is not None
    principal_row.trust_roles = []
    sqlite_session.commit()

    with pytest.raises(SystemVersionsError) as raised:
        _import(
            sqlite_session,
            _manifest(),
            key="import-key-0001",
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "v5.manifest.principal_not_authorized"
    assert _count(sqlite_session, SystemVersionSet) == 1
    assert _count(sqlite_session, SystemAssignment) == 1
    assert _count(sqlite_session, PublicCommandIdempotency) == 1


@pytest.mark.parametrize(
    "target",
    [
        "application_history",
        "environment",
        "component",
        "edge",
        "component_revision",
        "topology",
        "version_set",
        "attestation",
        "assignment",
        "receipt",
        "event",
        "outbox",
        "root_audit",
    ],
)
def test_same_key_replay_rejects_any_tampered_authoritative_graph_node(
    sqlite_session, target: str
) -> None:
    _seed_env(sqlite_session)
    response = _import(sqlite_session, _manifest(), key="import-key-0001")
    sqlite_session.commit()
    event = sqlite_session.scalar(
        select(Event).where(Event.event_type == "environment.registered")
    )
    assert event is not None
    outbox = sqlite_session.scalar(
        select(Outbox).where(Outbox.source_event_id == event.event_id)
    )
    assert outbox is not None
    root_audit = sqlite_session.scalar(
        select(Audit).where(Audit.action == "system-manifests.import")
    )
    assert root_audit is not None
    tamper = "sha256:" + "f" * 64
    updates = {
        "application_history": (
            "ai_application_lifecycle_revisions", "record_digest",
            "application_id", response.application.application_id,
        ),
        "environment": (
            "environments", "record_digest", "environment_id",
            response.environment.environment_id,
        ),
        "component": (
            "system_components", "record_digest", "component_id",
            response.components[0].component_id,
        ),
        "edge": (
            "dependency_edges", "record_digest", "edge_id",
            response.dependency_edges[0].edge_id,
        ),
        "component_revision": (
            "component_revisions", "record_digest", "component_revision_id",
            response.component_revisions[0].component_revision_id,
        ),
        "topology": (
            "topology_revisions", "record_digest", "topology_revision_id",
            response.topology_revision.topology_revision_id,
        ),
        "version_set": (
            "system_version_sets", "record_digest", "system_version_set_id",
            response.system_version_set.system_version_set_id,
        ),
        "attestation": (
            "bootstrap_attestations", "record_digest", "bootstrap_attestation_id",
            response.bootstrap_attestation.bootstrap_attestation_id,
        ),
        "assignment": (
            "system_assignments", "record_digest", "assignment_id",
            response.system_assignment.assignment_id,
        ),
        "receipt": (
            "authority_receipts", "authority_receipt_digest", "authority_receipt_id",
            event.authority_receipt_id,
        ),
        "event": ("events", "payload_digest", "event_id", event.event_id),
        "outbox": ("outbox", "payload_digest", "outbox_id", outbox.outbox_id),
        "root_audit": ("audit", "audit_digest", "audit_id", root_audit.audit_id),
    }
    table, column, id_column, row_id = updates[target]
    where = f"{id_column} = :row_id"
    if target == "application_history":
        where += " AND revision = 1"
    sqlite_session.execute(
        sa.text(f"UPDATE {table} SET {column} = :tamper WHERE {where}"),
        {"tamper": tamper, "row_id": row_id},
    )
    sqlite_session.commit()

    with pytest.raises(SystemVersionsError) as raised:
        _import(
            sqlite_session,
            _manifest(),
            key="import-key-0001",
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "INTERNAL_ERROR"
    sqlite_session.rollback()


def test_same_key_replay_rejects_cross_transaction_receipt_rebind(sqlite_session) -> None:
    _seed_env(sqlite_session)
    _import(sqlite_session, _manifest(), key="import-key-0001")
    sqlite_session.commit()
    event = sqlite_session.scalar(
        select(Event).where(Event.event_type == "system_version_set.recorded")
    )
    assert event is not None
    sqlite_session.execute(
        sa.text(
            "UPDATE authority_receipts SET transaction_id = :transaction_id "
            "WHERE authority_receipt_id = :receipt_id"
        ),
        {
            "transaction_id": "txn_cross_transaction_rebind",
            "receipt_id": event.authority_receipt_id,
        },
    )
    sqlite_session.commit()
    with pytest.raises(SystemVersionsError) as raised:
        _import(sqlite_session, _manifest(), key="import-key-0001")
    assert raised.value.code == "INTERNAL_ERROR"
    sqlite_session.rollback()


def test_same_manifest_digest_different_key_conflicts_without_new_records(sqlite_session) -> None:
    _seed_env(sqlite_session)
    _import(sqlite_session, _manifest(), key="import-key-0001")
    sqlite_session.commit()
    counts = {
        model: _count(sqlite_session, model)
        for model in (SystemVersionSet, AIApplication, AuthorityReceipt, Audit, PublicCommandIdempotency)
    }
    with pytest.raises(SystemVersionsError) as raised:
        _import(
            sqlite_session,
            _manifest(),
            key="import-key-9999",
            request_id="req_01J000000000000C",
        )
    assert raised.value.code == "CATALOG_CONFLICT"
    assert _count(sqlite_session, SystemVersionSet) == 1
    assert _count(sqlite_session, AIApplication) == 1
    assert all(_count(sqlite_session, model) == count for model, count in counts.items())
    sqlite_session.rollback()


def test_same_key_replay_ignores_unrelated_legacy_event_and_audit(sqlite_session) -> None:
    _seed_env(sqlite_session)
    unrelated_payload = {"legacy": True, "case_id": "case_unrelated"}
    V4AuditService(sqlite_session, clock=lambda: NOW).record(
        workspace_id=WORKSPACE,
        actor_principal=OWNER,
        action="controller.legacy.observed",
        target="case_unrelated",
        params={"source": "preexisting-v4"},
        transaction_id="txn_unrelated_v4",
        evidence_refs={"case_id": "case_unrelated"},
        occurred_at=NOW,
    )
    sqlite_session.add(
        Event(
            event_id="evt_01J0000000000LEGACY1",
            aggregate_type="legacy_case",
            aggregate_id="case_unrelated",
            seq=1,
            event_type="legacy.case.observed",
            payload=unrelated_payload,
            causation_id="legacy-cause",
            correlation_id="legacy-correlation",
            actor="legacy-controller",
            trace_id=None,
            occurred_at=NOW,
            created_at=NOW,
            contract_version="v4",
            workspace_id=WORKSPACE,
            event_version="1.0",
            event_contract_major=None,
            routing_key=None,
            exact_subject_binding=None,
            authority_receipt_id=None,
            transaction_id="txn_unrelated_v4",
            actor_principal=OWNER,
            payload_digest=canonical_digest(unrelated_payload),
        )
    )
    sqlite_session.commit()

    created = _import(sqlite_session, _manifest(), key="import-key-unrelated")
    sqlite_session.commit()
    replayed = _import(sqlite_session, _manifest(), key="import-key-unrelated")
    assert replayed.idempotency.replayed is True
    assert (
        replayed.system_version_set.system_version_set_id
        == created.system_version_set.system_version_set_id
    )


def test_same_key_replay_ignores_later_valid_catalog_transaction(sqlite_session) -> None:
    _seed_env(sqlite_session)
    created = _import(sqlite_session, _manifest(), key="import-key-later-catalog")
    sqlite_session.commit()

    later_principal_id = "prn_01J000000000000D"
    _seed_principal(
        sqlite_session,
        principal_id=later_principal_id,
        scopes=["applications:manage"],
        trust_roles=["integrator"],
    )
    sqlite_session.commit()
    later = ApplicationCatalogService(sqlite_session, clock=lambda: NOW).register_application(
        _app_request(
            slug="later-sidecar",
            display_name="Later Sidecar",
        ),
        principal=_principal_context(
            principal_id=later_principal_id,
            scopes=["applications:manage"],
            required_scope="applications:manage",
        ),
        idempotency_key="later-catalog-key",
        request_id="req_01J000000000LATER1",
    )
    sqlite_session.commit()
    assert later.application.application_id != created.application.application_id

    replayed = _import(sqlite_session, _manifest(), key="import-key-later-catalog")
    created_wire = created.model_dump(mode="json")
    replayed_wire = replayed.model_dump(mode="json")
    created_wire["idempotency"]["replayed"] = True
    assert replayed_wire == created_wire


def test_idempotency_conflict_on_different_manifest_same_key(sqlite_session) -> None:
    _seed_env(sqlite_session)
    _import(sqlite_session, _manifest(), key="import-key-0001")
    sqlite_session.commit()
    other_payload = _manifest().model_dump(mode="json")
    other_payload["application"]["slug"] = "different-app"
    other = SystemManifestImportRequest.model_validate(other_payload)
    with pytest.raises(SystemVersionsError) as raised:
        _import(sqlite_session, other, key="import-key-0001")
    assert raised.value.code == "IDEMPOTENCY_CONFLICT"
    sqlite_session.rollback()


def test_bootstrap_one_shot_per_workspace_conflicts_on_second_manifest(sqlite_session) -> None:
    _seed_env(sqlite_session)
    _import(sqlite_session, _manifest(), key="import-key-0001")
    sqlite_session.commit()
    second_payload = _manifest().model_dump(mode="json")
    second_payload["application"]["slug"] = "second-app"
    second = SystemManifestImportRequest.model_validate(second_payload)
    with pytest.raises(SystemVersionsError) as raised:
        _import(sqlite_session, second, key="import-key-0002")
    assert raised.value.code == "CATALOG_CONFLICT"
    assert raised.value.details == {"reason": "MANIFEST_BOOTSTRAP_ALREADY_EXISTS"}
    sqlite_session.rollback()
    # The second manifest left nothing behind.
    assert _count(sqlite_session, AIApplication) == 1
    assert _count(sqlite_session, SystemAssignment) == 1


def test_bootstrap_rejects_partial_orphan_authoritative_state_before_writes(
    sqlite_session,
) -> None:
    _seed_env(sqlite_session)
    payload = {
        "environment_id": "env_01J0000000000ORPHAN",
        "workspace_id": WORKSPACE,
        "application_id": "app_01J0000000000ORPHAN",
        "logical_name": "orphan",
        "risk_classification": "LOW",
        "lifecycle_state": "ACTIVE",
        **_mk_envelope(WORKSPACE, "env_01J0000000000ORPHAN"),
    }
    digest = v5_record_digest(payload)
    payload["record_envelope"]["record_digest"] = digest
    sqlite_session.add(
        Environment(
            environment_id=payload["environment_id"],
            workspace_id=WORKSPACE,
            application_id=payload["application_id"],
            logical_name=payload["logical_name"],
            risk_classification=payload["risk_classification"],
            lifecycle_state="ACTIVE",
            revision=1,
            envelope_payload=payload,
            record_digest=digest,
            authority_receipt_id=payload["record_envelope"]["authority_receipt_id"],
            recorded_by_principal=OWNER,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    sqlite_session.commit()
    before = _count(sqlite_session, PublicCommandIdempotency)
    with pytest.raises(SystemVersionsError) as raised:
        _import(sqlite_session, _manifest(), key="import-key-orphan")
    assert raised.value.code == "CATALOG_CONFLICT"
    assert _count(sqlite_session, PublicCommandIdempotency) == before
    assert _count(sqlite_session, AIApplication) == 0
    sqlite_session.rollback()


def test_assignment_cas_guard_one_active_aggregate(sqlite_session) -> None:
    _seed_env(sqlite_session)
    _import(sqlite_session, _manifest(), key="import-key-0001")
    sqlite_session.commit()
    # A second ACTIVE assignment for the same identity key must be rejected by
    # the partial unique index, even if inserted directly.
    existing = sqlite_session.scalar(select(SystemAssignment))
    from app.utils.ids import new_system_assignment_id

    with pytest.raises(sa.exc.IntegrityError):
        sqlite_session.execute(
            sa.text(
                """INSERT INTO system_assignments (
                  assignment_id, workspace_id, application_id, environment_id,
                  generation, lifecycle_state, transition_kind, revision,
                  exact_previous_assignment_binding_or_null,
                  exact_slot_version_set_bindings, exposure,
                  expected_previous_generation, exact_assignment_authority_binding,
                  requested_by_external_operation_id, envelope_payload,
                  record_digest, authority_receipt_id, recorded_by_principal
                ) VALUES (
                  :assignment_id, :workspace_id, :application_id, :environment_id,
                  1, 'ACTIVE', 'BOOTSTRAP', 1, NULL, :slots, 'EXPOSED', NULL,
                  :authority, NULL, :envelope, :digest, :receipt, :principal
                )"""
            ),
            {
                "assignment_id": new_system_assignment_id(),
                "workspace_id": existing.workspace_id,
                "application_id": existing.application_id,
                "environment_id": existing.environment_id,
                "slots": "[]",
                "authority": '{"binding_kind":"BOOTSTRAP_ATTESTATION","id":"batt_x","revision":null,"digest":"sha256:' + "e" * 64 + '"}',
                "envelope": "{}",
                "digest": "sha256:" + "f" * 64,
                "receipt": new_authority_receipt_id(),
                "principal": OWNER,
            },
        )
    sqlite_session.rollback()


# ------------------------------------------------------------------- immutability


def test_version_set_records_are_immutable(sqlite_session) -> None:
    _seed_env(sqlite_session)
    response = _import(sqlite_session, _manifest())
    sqlite_session.commit()
    for model, row_id in (
        (ComponentRevision, response.component_revisions[0].component_revision_id),
        (TopologyRevision, response.topology_revision.topology_revision_id),
        (SystemVersionSet, response.system_version_set.system_version_set_id),
        (BootstrapAttestation, response.bootstrap_attestation.bootstrap_attestation_id),
    ):
        row = sqlite_session.get(model, row_id)
        with pytest.raises(RuntimeError, match="immutable_record_update_forbidden"):
            row.envelope_payload = {"tampered": True}
            sqlite_session.flush()
        sqlite_session.rollback()


def test_version_set_digest_is_deterministic_and_binding_sensitive() -> None:
    bindings_a = [
        {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000001", "revision": 1, "digest": _DIGEST_A},
        {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000002", "revision": 1, "digest": _DIGEST_B},
    ]
    bindings_b = [
        {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000001", "revision": 1, "digest": _DIGEST_A},
        {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000003", "revision": 1, "digest": _DIGEST_C},
    ]
    topology = {"kind": "TOPOLOGY_REVISION", "id": "tpr_01J0000000000001", "revision": 1, "digest": _DIGEST_D}
    summary = {"component_assurances": []}
    service = _service
    kwargs = dict(
        application_id="app_01J0000000000001",
        declared_environment_id="env_01J0000000000001",
        component_bindings=bindings_a,
        topology_binding=topology,
        provenance_receipt_ids=[],
        assurance_summary=summary,
    )
    d1 = SystemVersionsService._version_set_digest(**kwargs)
    d2 = SystemVersionsService._version_set_digest(**kwargs)
    assert d1 == d2
    d3 = SystemVersionsService._version_set_digest(**{**kwargs, "component_bindings": bindings_b})
    assert d1 != d3


# ------------------------------------------------------------------------- diff


def _seed_version_set_row(
    session,
    *,
    version_set_id: str,
    bindings: list[dict],
    topology_binding: dict,
    version_set_digest: str = _DIGEST_A,
) -> SystemVersionSet:
    envelope = _mk_envelope(WORKSPACE, version_set_id)
    row = SystemVersionSet(
        system_version_set_id=version_set_id,
        workspace_id=WORKSPACE,
        application_id="app_01J0000000000001",
        declared_environment_id="env_01J0000000000001",
        exact_component_revision_bindings=bindings,
        exact_topology_revision_binding=topology_binding,
        identity_assurance_summary={"component_assurances": []},
        provenance_receipt_ids=[],
        version_set_digest=version_set_digest,
        manifest_digest=None,
        envelope_payload=envelope,
        record_digest=envelope["record_envelope"]["record_digest"],
        authority_receipt_id=envelope["record_envelope"]["authority_receipt_id"],
        recorded_by_principal=OWNER,
        created_at=NOW,
    )
    session.add(row)
    return row


def _seed_component_revision(
    session,
    *,
    revision_id: str,
    component_id: str,
    component_kind: str,
    configuration_digest: str,
    permission_manifest_digest: str | None = None,
    identity_assurance: str = "IMMUTABLE_DIGEST",
) -> None:
    envelope = _mk_envelope(WORKSPACE, revision_id)
    session.add(
        ComponentRevision(
            component_revision_id=revision_id,
            workspace_id=WORKSPACE,
            application_id="app_01J0000000000001",
            component_id=component_id,
            component_kind=component_kind,
            identity_locator={"type": "git", "path": "."},
            identity_assurance=identity_assurance,
            configuration_digest=configuration_digest,
            exact_provenance_receipt_bindings=[],
            permission_manifest_digest=permission_manifest_digest,
            envelope_payload=envelope,
            record_digest=envelope["record_envelope"]["record_digest"],
            authority_receipt_id=envelope["record_envelope"]["authority_receipt_id"],
            recorded_by_principal=OWNER,
            created_at=NOW,
        )
    )


def _seed_component(session, *, component_id: str, kind: str, logical_name: str) -> None:
    envelope = _mk_envelope(WORKSPACE, component_id)
    session.add(
        SystemComponent(
            component_id=component_id,
            workspace_id=WORKSPACE,
            application_id="app_01J0000000000001",
            component_kind=kind,
            logical_name=logical_name,
            owner_principal_ids=[OWNER],
            criticality="P1",
            data_classification="INTERNAL",
            permission_classification="READ_ONLY",
            effect_classification="NONE",
            lifecycle_state="ACTIVE",
            revision=1,
            envelope_payload=envelope,
            record_digest=envelope["record_envelope"]["record_digest"],
            authority_receipt_id=envelope["record_envelope"]["authority_receipt_id"],
            recorded_by_principal=OWNER,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _seed_diff_pair(session) -> tuple[str, str]:
    """Base: code+model+pipeline; Target: code'(digest changed)+model+retriever
    (dependency substitution) + policy permission manifest change."""
    envelope = _mk_envelope(WORKSPACE, "app_01J0000000000001")
    session.add(
        AIApplication(
            application_id="app_01J0000000000001",
            workspace_id=WORKSPACE,
            project_id=PROJECT,
            slug="diff-app",
            display_name="Diff app",
            owner_principal_ids=[OWNER],
            criticality="P0",
            data_classification="INTERNAL",
            governance_mode="MANAGED",
            lifecycle_state="ACTIVE",
            revision=1,
            envelope_payload=envelope,
            record_digest=envelope["record_envelope"]["record_digest"],
            authority_receipt_id=envelope["record_envelope"]["authority_receipt_id"],
            recorded_by_principal=OWNER,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    for component_id, kind, name in (
        ("cmp_01J0000000000A01", "APPLICATION_CODE", "code"),
        ("cmp_01J0000000000A02", "MODEL_BINDING", "model"),
        ("cmp_01J0000000000A03", "RETRIEVER", "retriever"),
        ("cmp_01J0000000000A04", "POLICY", "policy"),
    ):
        _seed_component(session, component_id=component_id, kind=kind, logical_name=name)
    # base revisions
    _seed_component_revision(
        session, revision_id="crv_01J0000000000B01", component_id="cmp_01J0000000000A01",
        component_kind="APPLICATION_CODE", configuration_digest=_DIGEST_A,
    )
    _seed_component_revision(
        session, revision_id="crv_01J0000000000B02", component_id="cmp_01J0000000000A02",
        component_kind="MODEL_BINDING", configuration_digest=_DIGEST_B,
    )
    _seed_component_revision(
        session, revision_id="crv_01J0000000000B03", component_id="cmp_01J0000000000A04",
        component_kind="POLICY", configuration_digest=_DIGEST_C,
        permission_manifest_digest="sha256:" + "1" * 64,
    )
    # target revisions
    _seed_component_revision(
        session, revision_id="crv_01J0000000000B11", component_id="cmp_01J0000000000A01",
        component_kind="APPLICATION_CODE", configuration_digest=_DIGEST_D,
    )
    _seed_component_revision(
        session, revision_id="crv_01J0000000000B12", component_id="cmp_01J0000000000A02",
        component_kind="MODEL_BINDING", configuration_digest=_DIGEST_B,
    )
    _seed_component_revision(
        session, revision_id="crv_01J0000000000B13", component_id="cmp_01J0000000000A03",
        component_kind="RETRIEVER", configuration_digest=_DIGEST_C,
    )
    _seed_component_revision(
        session, revision_id="crv_01J0000000000B14", component_id="cmp_01J0000000000A04",
        component_kind="POLICY", configuration_digest=_DIGEST_C,
        permission_manifest_digest="sha256:" + "2" * 64,
    )
    base = _seed_version_set_row(
        session,
        version_set_id="vset_01J0000000000C01",
        bindings=[
            {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000B01", "revision": None, "digest": _DIGEST_A},
            {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000B02", "revision": None, "digest": _DIGEST_B},
            {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000B03", "revision": None, "digest": _DIGEST_C},
        ],
        topology_binding={"kind": "TOPOLOGY_REVISION", "id": "tpr_01J0000000000C01", "revision": None, "digest": _DIGEST_A},
    )
    target = _seed_version_set_row(
        session,
        version_set_id="vset_01J0000000000C02",
        bindings=[
            {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000B11", "revision": None, "digest": _DIGEST_D},
            {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000B12", "revision": None, "digest": _DIGEST_B},
            {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000B13", "revision": None, "digest": _DIGEST_C},
            {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000B14", "revision": None, "digest": _DIGEST_C},
        ],
        topology_binding={"kind": "TOPOLOGY_REVISION", "id": "tpr_01J0000000000C02", "revision": None, "digest": _DIGEST_B},
        version_set_digest=_DIGEST_B,
    )
    session.commit()
    return base.system_version_set_id, target.system_version_set_id


def test_diff_semantics_digest_substitution_permission_expansion(sqlite_session) -> None:
    _seed_env(sqlite_session)
    base_id, target_id = _seed_diff_pair(sqlite_session)
    diff = _service(sqlite_session).diff_system_versions(
        base_id,
        target_id,
        principal=_reader_principal(),
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    assert {item.component_id for item in diff.removed} == set()
    assert {item.component_id for item in diff.added} == {"cmp_01J0000000000A03"}
    changed = {item.component_id: item for item in diff.changed}
    assert "cmp_01J0000000000A01" in changed
    assert changed["cmp_01J0000000000A01"].diff_kind == "DIGEST_CHANGED"
    assert changed["cmp_01J0000000000A01"].base_digest == _DIGEST_A
    assert changed["cmp_01J0000000000A01"].target_digest == _DIGEST_D
    expansions = {
        item.component_id: item for item in diff.policy_permission_expansions
    }
    assert "cmp_01J0000000000A04" in expansions
    assert expansions["cmp_01J0000000000A04"].diff_kind == "PERMISSION_EXPANSION"
    assert (
        expansions["cmp_01J0000000000A04"].base_digest == "sha256:" + "1" * 64
    )
    assert (
        expansions["cmp_01J0000000000A04"].target_digest == "sha256:" + "2" * 64
    )


def test_diff_same_label_different_digest_uses_component_identity(sqlite_session) -> None:
    """same label (component_id), different digest -> DIGEST_CHANGED, not ADDED."""
    _seed_env(sqlite_session)
    base_id, target_id = _seed_diff_pair(sqlite_session)
    diff = _service(sqlite_session).diff_system_versions(
        base_id,
        target_id,
        principal=_reader_principal(),
        request_id="req_01J000000000000B",
    )
    sqlite_session.commit()
    changed_components = {item.component_id for item in diff.changed}
    assert "cmp_01J0000000000A01" in changed_components
    assert not any(item.component_id == "cmp_01J0000000000A01" for item in diff.added)
    assert not any(item.component_id == "cmp_01J0000000000A01" for item in diff.removed)


def test_diff_requires_visibility_of_both_versions(sqlite_session) -> None:
    _seed_env(sqlite_session)
    base_id, target_id = _seed_diff_pair(sqlite_session)
    from test_v5_application_catalog import OTHER_PROJECT

    foreign_reader = "prn_01J00000000000F2"
    _seed_principal(
        sqlite_session,
        principal_id=foreign_reader,
        scopes=["system_versions:read"],
        project_ids=[OTHER_PROJECT],
    )
    sqlite_session.commit()
    foreign = _principal_context(
        principal_id=foreign_reader,
        project_ids=[OTHER_PROJECT],
        scopes=["system_versions:read"],
        required_scope="system_versions:read",
    )
    from app.services.system_versions import V5ReadDenial

    with pytest.raises(V5ReadDenial) as raised:
        _service(sqlite_session).diff_system_versions(
            base_id,
            target_id,
            principal=foreign,
            request_id="req_01J000000000000C",
        )
    assert raised.value.code == "RESOURCE_NOT_FOUND"
    sqlite_session.commit()


def test_get_missing_and_cross_workspace_denied(sqlite_session) -> None:
    _seed_env(sqlite_session)
    from app.services.system_versions import V5ReadDenial

    with pytest.raises(V5ReadDenial) as raised:
        _service(sqlite_session).get_system_version(
            "vset_01J0000000000ZZZ",
            principal=_reader_principal(),
            request_id="req_01J000000000000A",
        )
    assert raised.value.code == "RESOURCE_NOT_FOUND"
    sqlite_session.commit()
