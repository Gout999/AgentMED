"""V5-1B system versions service tests (fail-closed orchestration).

Covers: immutable VersionSet records, exact graph/component binding, semantic
diff (same label/different digest, dependency substitution, policy permission
expansion), identity assurance downgrade honesty, trusted manifest import
allowlist, ALL_OR_NOTHING atomicity, idempotent replay (key + manifest digest),
bootstrap assignment CAS constraints and adversarial cases.
"""
from __future__ import annotations

import copy
from datetime import timedelta
from types import SimpleNamespace

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
    BootstrapAttestation,
    ComponentRevision,
    DependencyEdge,
    Environment,
    SystemAssignment,
    SystemComponent,
    SystemVersionSet,
    TopologyRevision,
)
from app.public_api.v5_models import SystemManifestImportRequest
from app.services.system_versions import SystemVersionsError, SystemVersionsService
from app.services.v4_audit import V4AuditService
from app.utils.v5_integrity import v5_record_digest

from test_v5_application_catalog import (
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
                    "service": "agentmed-control-plane",
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
                "service": "agentmed-control-plane",
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
    )
    session.flush()
    session.get(PublicPrincipal, IMPORT_PRINCIPAL).trust_roles = ["integrator"]
    _seed_v5_controller(session)
    _seed_version_controller(session)
    session.commit()


def _service(session, **kwargs) -> SystemVersionsService:
    return SystemVersionsService(session, clock=lambda: NOW, **kwargs)


class _NoopReadAuthority:
    def validate_receipt_binding(self, **_kwargs) -> None:
        return None


def _diff_service(session) -> SystemVersionsService:
    return _service(session, authority_service=_NoopReadAuthority())


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


def _manifest_with_dataset(dataset_role: str) -> SystemManifestImportRequest:
    payload = _manifest().model_dump(mode="json")
    payload["components"].append(
        {
            "logical_name": "runtime-corpus",
            "component_kind": "DATASET",
            "owner_principal_ids": [OWNER],
            "criticality": "P1",
            "data_classification": "CONFIDENTIAL",
            "permission_classification": "READ_ONLY",
            "effect_classification": "NONE",
            "dataset_role": dataset_role,
            "revision": {
                "identity_locator": {"type": "dataset", "name": "runtime-corpus"},
                "identity_assurance": "UNKNOWN",
                "unknown_reason": "no immutable dataset snapshot supplied",
            },
        }
    )
    return SystemManifestImportRequest.model_validate(payload)


def _import(session, manifest, *, key="import-key-0001", principal=None, request_id="req_01J000000000000A"):
    return _service(session).import_manifest(
        manifest,
        principal=principal or _import_principal(),
        idempotency_key=key,
        request_id=request_id,
    )


def _count(session, model) -> int:
    return int(session.scalar(select(sa.func.count()).select_from(model)) or 0)


def _mk_envelope(
    workspace_id: str, subject_id: str, payload: dict | None = None
) -> dict:
    del subject_id  # the caller's business payload carries the typed id field
    payload = dict(payload or {})
    payload["record_envelope"] = {
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
        response.system_assignment.exact_assignment_authority_binding["binding_kind"]
        == "BOOTSTRAP_ATTESTATION"
    )
    assert (
        response.bootstrap_attestation.attestation_scope
        == "INITIAL_DESIRED_ASSIGNMENT"
    )
    assert response.bootstrap_attestation.attester_trust_role == "integrator"
    # approver policy revision is recorded but NOT part of the runtime VersionSet
    assert response.approver_policy_revision is not None
    bound_ids = {
        binding["id"] for binding in response.system_version_set.exact_component_revision_bindings
    }
    assert response.approver_policy_revision.component_revision_id not in bound_ids

    # All immutable-record bindings use the durable envelope revision and the
    # target record digest (never the edge business digest or null revision).
    edge = response.dependency_edges[0]
    edge_binding = response.topology_revision.exact_edge_revision_bindings[0]
    assert edge_binding == {
        "kind": "DEPENDENCY_EDGE",
        "id": edge.edge_id,
        "revision": 1,
        "digest": edge.record_envelope.record_digest,
    }
    assert edge_binding["digest"] != edge.edge_digest
    revision_records = {
        row.component_revision_id: row for row in response.component_revisions
    }
    for binding in response.system_version_set.exact_component_revision_bindings:
        assert binding["revision"] == 1
        assert binding["digest"] == revision_records[binding["id"]].record_envelope.record_digest
    assert response.system_version_set.exact_topology_revision_binding == {
        "kind": "TOPOLOGY_REVISION",
        "id": response.topology_revision.topology_revision_id,
        "revision": 1,
        "digest": response.topology_revision.record_envelope.record_digest,
    }
    expected_topology_provenance = sorted(
        {
            response.application.record_envelope.authority_receipt_id,
            response.environment.record_envelope.authority_receipt_id,
        }
    )
    assert (
        response.topology_revision.provenance_receipt_ids
        == expected_topology_provenance
    )
    for receipt_id, subject_kind, subject_id, subject_digest in (
        (
            response.application.record_envelope.authority_receipt_id,
            "AI_APPLICATION",
            response.application.application_id,
            response.application.record_envelope.record_digest,
        ),
        (
            response.environment.record_envelope.authority_receipt_id,
            "ENVIRONMENT",
            response.environment.environment_id,
            response.environment.record_envelope.record_digest,
        ),
    ):
        receipt = sqlite_session.get(AuthorityReceipt, receipt_id)
        assert receipt is not None
        assert (
            receipt.subject_kind,
            receipt.subject_id,
            receipt.subject_revision,
            receipt.subject_digest,
        ) == (subject_kind, subject_id, 1, subject_digest)
    assert response.bootstrap_attestation.exact_initial_system_version_set_binding == {
        "kind": "SYSTEM_VERSION_SET",
        "id": response.system_version_set.system_version_set_id,
        "revision": 1,
        "digest": response.system_version_set.record_envelope.record_digest,
    }

    assert _count(sqlite_session, AIApplication) == 1
    assert _count(sqlite_session, Environment) == 1
    assert _count(sqlite_session, SystemComponent) == 3  # 2 runtime + 1 policy
    assert _count(sqlite_session, DependencyEdge) == 1
    assert _count(sqlite_session, ComponentRevision) == 3  # 2 runtime + 1 policy
    assert _count(sqlite_session, TopologyRevision) == 1
    assert _count(sqlite_session, SystemVersionSet) == 1
    assert _count(sqlite_session, BootstrapAttestation) == 1
    assert _count(sqlite_session, SystemAssignment) == 1
    assert _count(sqlite_session, Event) == 13
    assert _count(sqlite_session, Outbox) == 13
    assert _count(sqlite_session, AuthorityReceipt) == 13
    assert _count(sqlite_session, PublicCommandIdempotency) == 1

    receipts = sqlite_session.scalars(select(AuthorityReceipt)).all()
    assert receipts and all(receipt.subject_revision == 1 for receipt in receipts)

    events = sqlite_session.scalars(select(Event)).all()
    assert events and all(event.contract_version == "v5" for event in events)
    assert all(event.event_version == "2.0" for event in events)
    assert all(event.payload["subject_revision"] == 1 for event in events)
    assert all(event.exact_subject_binding["revision"] == 1 for event in events)
    for event in events:
        if event.event_type == "component_revision.recorded":
            component = sqlite_session.get(
                SystemComponent, event.payload["component_id"]
            )
            assert component is not None
            assert event.payload["exact_system_component_binding"] == {
                "kind": "SYSTEM_COMPONENT",
                "id": component.component_id,
                "revision": 1,
                "digest": component.record_digest,
            }
        elif event.event_type == "topology_revision.recorded":
            assert (
                event.payload["exact_edge_revision_bindings"]
                == response.topology_revision.exact_edge_revision_bindings
            )
        elif event.event_type == "system_version_set.recorded":
            assert (
                event.payload["exact_component_revision_bindings"]
                == response.system_version_set.exact_component_revision_bindings
            )
            assert (
                event.payload["exact_topology_revision_binding"]
                == response.system_version_set.exact_topology_revision_binding
            )
        elif event.event_type == "bootstrap_attestation.recorded":
            assert (
                event.payload["exact_initial_system_version_set_binding"]
                == response.bootstrap_attestation.exact_initial_system_version_set_binding
            )
        elif event.event_type == "system_assignment.recorded":
            authority = response.system_assignment.exact_assignment_authority_binding
            assert event.payload["exact_bootstrap_attestation_binding"] == {
                "kind": "BOOTSTRAP_ATTESTATION",
                "id": authority["id"],
                "revision": authority["revision"],
                "digest": authority["digest"],
            }
            assert (
                event.payload["exact_initial_system_version_set_binding"]
                == response.bootstrap_attestation.exact_initial_system_version_set_binding
            )

    audits = sqlite_session.scalars(
        select(Audit).where(Audit.contract_version == "v4")
    ).all()
    actions = {row.action for row in audits}
    assert "system-manifests.import" in actions
    assert {
        "controller.application.registered",
        "controller.component_revision.recorded",
        "controller.topology_revision.recorded",
        "controller.system_version_set.recorded",
        "controller.bootstrap_attestation.recorded",
        "controller.system_assignment.recorded",
    } <= actions


def test_import_identity_assurance_is_recorded_honestly(sqlite_session) -> None:
    _seed_env(sqlite_session)
    response = _import(sqlite_session, _manifest())
    sqlite_session.commit()
    assurances = {
        entry["identity_assurance"]
        for entry in response.system_version_set.identity_assurance_summary[
            "component_assurances"
        ]
    }
    assert {"IMMUTABLE_DIGEST", "MUTABLE_ALIAS"} <= assurances
    model_rev = sqlite_session.get(ComponentRevision, response.component_revisions[1].component_revision_id)
    assert model_rev is not None and model_rev.identity_assurance == "MUTABLE_ALIAS"


def test_runtime_dataset_role_is_persisted_and_digest_recomputes(sqlite_session) -> None:
    _seed_env(sqlite_session)
    response = _import(sqlite_session, _manifest_with_dataset("RUNTIME_DATA"))
    sqlite_session.commit()

    component = next(
        row for row in response.components if row.logical_name == "runtime-corpus"
    )
    revision = next(
        row
        for row in response.component_revisions
        if row.component_id == component.component_id
    )
    assert component.dataset_role == "RUNTIME_DATA"
    assert revision.dataset_role == "RUNTIME_DATA"
    assert revision.exact_provenance_receipt_bindings == []
    assert component.component_id in response.topology_revision.component_ids
    assert revision.component_revision_id in {
        binding["id"]
        for binding in response.system_version_set.exact_component_revision_bindings
    }

    durable_revision = sqlite_session.get(
        ComponentRevision, revision.component_revision_id
    )
    assert durable_revision.exact_provenance_receipt_bindings == []
    assert (
        _service(sqlite_session)._component_configuration_digest(durable_revision)
        == durable_revision.configuration_digest
    )
    got = _service(sqlite_session).get_system_version(
        response.system_version_set.system_version_set_id,
        principal=_reader_principal(),
        request_id="req_01J000000000000D",
    )
    assert (
        got.system_version_set.system_version_set_id
        == response.system_version_set.system_version_set_id
    )
    sqlite_session.commit()


@pytest.mark.parametrize("dataset_role", ["EVALUATION_DATA", "SEALED_HOLDOUT"])
def test_governing_datasets_cannot_enter_runtime_version_set(
    sqlite_session, dataset_role: str
) -> None:
    _seed_env(sqlite_session)

    with pytest.raises(SystemVersionsError) as raised:
        _import(sqlite_session, _manifest_with_dataset(dataset_role))
    assert raised.value.code == "VALIDATION_FAILED"
    assert raised.value.details == {
        "reason": "GOVERNING_DATASET_NOT_RUNTIME_COMPONENT"
    }
    assert _count(sqlite_session, PublicCommandIdempotency) == 0
    assert _count(sqlite_session, AIApplication) == 0
    sqlite_session.rollback()


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


def test_import_requires_server_derived_trust_role(sqlite_session) -> None:
    _seed_env(sqlite_session)
    importer = sqlite_session.get(PublicPrincipal, IMPORT_PRINCIPAL)
    importer.trust_roles = []
    sqlite_session.commit()

    with pytest.raises(SystemVersionsError) as raised:
        _import(sqlite_session, _manifest())
    assert raised.value.code == "SCOPE_FORBIDDEN"
    assert _count(sqlite_session, PublicCommandIdempotency) == 0
    assert _count(sqlite_session, AIApplication) == 0
    sqlite_session.rollback()


def test_import_requires_manifest_project_grant(sqlite_session) -> None:
    _seed_env(sqlite_session)
    from test_v5_application_catalog import OTHER_PROJECT

    project_limited_principal = "prn_01J00000000000D2"
    _seed_principal(
        sqlite_session,
        principal_id=project_limited_principal,
        scopes=["system_manifests:import", "system_versions:read"],
        project_ids=[OTHER_PROJECT],
        trust_roles=["integrator"],
    )
    sqlite_session.commit()
    principal = _principal_context(
        principal_id=project_limited_principal,
        project_ids=[OTHER_PROJECT],
        scopes=["system_manifests:import", "system_versions:read"],
        required_scope="system_manifests:import",
    )

    with pytest.raises(SystemVersionsError) as raised:
        _import(sqlite_session, _manifest(), principal=principal)
    assert raised.value.code == "SCOPE_FORBIDDEN"
    assert _count(sqlite_session, PublicCommandIdempotency) == 0
    assert _count(sqlite_session, AIApplication) == 0
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


def test_same_manifest_digest_different_key_replays_same_records(sqlite_session) -> None:
    _seed_env(sqlite_session)
    first = _import(sqlite_session, _manifest(), key="import-key-0001")
    sqlite_session.commit()
    replay = _import(
        sqlite_session,
        _manifest(),
        key="import-key-9999",
        request_id="req_01J000000000000C",
    )
    assert replay.idempotency.replayed is True
    assert (
        replay.system_version_set.system_version_set_id
        == first.system_version_set.system_version_set_id
    )
    assert replay.system_version_set.manifest_digest == first.system_version_set.manifest_digest
    assert replay.audit_ref == first.audit_ref
    assert _count(sqlite_session, SystemVersionSet) == 1
    assert _count(sqlite_session, AIApplication) == 1
    sqlite_session.commit()


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
                "authority": '{"binding_kind":"BOOTSTRAP_ATTESTATION","id":"batt_x","revision":1,"digest":"sha256:' + "e" * 64 + '"}',
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


def test_topology_digest_binds_component_membership_even_without_edges() -> None:
    assert SystemVersionsService._topology_digest(
        [], component_ids=["cmp_01J0000000000A01"]
    ) != SystemVersionsService._topology_digest(
        [], component_ids=["cmp_01J0000000000A02"]
    )


def test_postgres_import_uses_workspace_transaction_lock() -> None:
    executed: list[tuple[object, dict[str, str]]] = []

    class _PostgresSession:
        @staticmethod
        def get_bind():
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        @staticmethod
        def execute(statement, params):
            executed.append((statement, params))

    service = SimpleNamespace(session=_PostgresSession())
    SystemVersionsService._acquire_workspace_import_lock(service, WORKSPACE)

    assert len(executed) == 1
    statement, params = executed[0]
    assert "pg_advisory_xact_lock" in str(statement)
    assert params == {"lock_key": f"agentmed:v5-manifest-import:{WORKSPACE}"}


# ------------------------------------------------------------------------- diff


def _seed_version_set_row(
    session,
    *,
    version_set_id: str,
    bindings: list[dict],
    topology_binding: dict,
    version_set_digest: str | None = None,
) -> SystemVersionSet:
    revisions = [session.get(ComponentRevision, binding["id"]) for binding in bindings]
    assurance_summary = SystemVersionsService._assurance_summary(
        [
            {
                "component_revision_id": revision.component_revision_id,
                "component_id": revision.component_id,
                "identity_assurance": revision.identity_assurance,
            }
            for revision in revisions
        ]
    )
    version_set_digest = version_set_digest or SystemVersionsService._version_set_digest(
        application_id="app_01J0000000000001",
        declared_environment_id="env_01J0000000000001",
        component_bindings=bindings,
        topology_binding=topology_binding,
        provenance_receipt_ids=[],
        assurance_summary=assurance_summary,
    )
    payload = {
        "system_version_set_id": version_set_id,
        "workspace_id": WORKSPACE,
        "application_id": "app_01J0000000000001",
        "declared_environment_id": "env_01J0000000000001",
        "exact_component_revision_bindings": bindings,
        "exact_topology_revision_binding": topology_binding,
        "identity_assurance_summary": assurance_summary,
        "provenance_receipt_ids": [],
        "version_set_digest": version_set_digest,
        "manifest_digest": None,
    }
    envelope = _mk_envelope(WORKSPACE, version_set_id, payload)
    row = SystemVersionSet(
        system_version_set_id=version_set_id,
        workspace_id=WORKSPACE,
        application_id="app_01J0000000000001",
        declared_environment_id="env_01J0000000000001",
        exact_component_revision_bindings=bindings,
        exact_topology_revision_binding=topology_binding,
        identity_assurance_summary=assurance_summary,
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
) -> ComponentRevision:
    revision_fields = {
        "identity_locator": {
            "type": "git",
            "path": ".",
            "seed": configuration_digest,
        },
        "identity_assurance": identity_assurance,
        "content_digest": configuration_digest,
        "declared_version": None,
        "provider_origin": None,
        "resolved_at": None,
        "immutable_provider_version_attestation": None,
        "exact_observation_receipt_binding": None,
        "unknown_reason": None,
        "interface_schema_digest": None,
        "permission_manifest_digest": permission_manifest_digest,
        "dependency_lock_digest": None,
        "artifact_refs": None,
        "exact_provenance_receipt_bindings": [],
        "dataset_role": None,
    }
    actual_configuration_digest = _service(session)._component_configuration_digest(
        SimpleNamespace(**revision_fields)
    )
    payload = {
        "component_revision_id": revision_id,
        "workspace_id": WORKSPACE,
        "application_id": "app_01J0000000000001",
        "component_id": component_id,
        "component_kind": component_kind,
        "logical_name": component_id,
        "configuration_digest": actual_configuration_digest,
        **revision_fields,
    }
    envelope = _mk_envelope(WORKSPACE, revision_id, payload)
    row = ComponentRevision(
            component_revision_id=revision_id,
            workspace_id=WORKSPACE,
            application_id="app_01J0000000000001",
            component_id=component_id,
            component_kind=component_kind,
            identity_locator=revision_fields["identity_locator"],
            identity_assurance=identity_assurance,
            configuration_digest=actual_configuration_digest,
            exact_provenance_receipt_bindings=[],
            content_digest=configuration_digest,
            permission_manifest_digest=permission_manifest_digest,
            envelope_payload=envelope,
            record_digest=envelope["record_envelope"]["record_digest"],
            authority_receipt_id=envelope["record_envelope"]["authority_receipt_id"],
            recorded_by_principal=OWNER,
            created_at=NOW,
        )
    session.add(row)
    session.flush()
    return row


def _seed_component(
    session, *, component_id: str, kind: str, logical_name: str
) -> SystemComponent:
    payload = {
        "component_id": component_id,
        "workspace_id": WORKSPACE,
        "application_id": "app_01J0000000000001",
        "component_kind": kind,
        "logical_name": logical_name,
        "owner_principal_ids": [OWNER],
        "criticality": "P1",
        "data_classification": "INTERNAL",
        "permission_classification": "READ_ONLY",
        "effect_classification": "NONE",
        "dataset_role": None,
        "lifecycle_state": "ACTIVE",
    }
    envelope = _mk_envelope(WORKSPACE, component_id, payload)
    row = SystemComponent(
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
    session.add(row)
    session.flush()
    return row


def _seed_diff_application_environment(session) -> None:
    application_id = "app_01J0000000000001"
    app_payload = {
        "application_id": application_id,
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "slug": "diff-app",
        "display_name": "Diff app",
        "owner_principal_ids": [OWNER],
        "criticality": "P0",
        "data_classification": "INTERNAL",
        "governance_mode": "MANAGED",
        "lifecycle_state": "ACTIVE",
    }
    app_envelope = _mk_envelope(WORKSPACE, application_id, app_payload)
    session.add(
        AIApplication(
            **app_payload,
            revision=1,
            envelope_payload=app_envelope,
            record_digest=app_envelope["record_envelope"]["record_digest"],
            authority_receipt_id=app_envelope["record_envelope"][
                "authority_receipt_id"
            ],
            recorded_by_principal=OWNER,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    environment_id = "env_01J0000000000001"
    environment_payload = {
        "environment_id": environment_id,
        "workspace_id": WORKSPACE,
        "application_id": application_id,
        "logical_name": "prod",
        "risk_classification": "MEDIUM",
        "lifecycle_state": "ACTIVE",
    }
    environment_envelope = _mk_envelope(
        WORKSPACE, environment_id, environment_payload
    )
    session.add(
        Environment(
            **environment_payload,
            revision=1,
            envelope_payload=environment_envelope,
            record_digest=environment_envelope["record_envelope"]["record_digest"],
            authority_receipt_id=environment_envelope["record_envelope"][
                "authority_receipt_id"
            ],
            recorded_by_principal=OWNER,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    session.flush()


def _seed_empty_topology(
    session, *, topology_id: str, component_ids: list[str]
) -> TopologyRevision:
    application = session.get(AIApplication, "app_01J0000000000001")
    environment = session.get(Environment, "env_01J0000000000001")
    provenance_receipt_ids = sorted(
        {application.authority_receipt_id, environment.authority_receipt_id}
    )
    topology_digest = SystemVersionsService._topology_digest(
        [], component_ids=component_ids
    )
    payload = {
        "topology_revision_id": topology_id,
        "workspace_id": WORKSPACE,
        "application_id": "app_01J0000000000001",
        "component_ids": sorted(component_ids),
        "exact_edge_revision_bindings": [],
        "topology_digest": topology_digest,
        "provenance_receipt_ids": provenance_receipt_ids,
    }
    envelope = _mk_envelope(WORKSPACE, topology_id, payload)
    row = TopologyRevision(
        topology_revision_id=topology_id,
        workspace_id=WORKSPACE,
        application_id="app_01J0000000000001",
        component_ids=payload["component_ids"],
        exact_edge_revision_bindings=[],
        topology_digest=topology_digest,
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


def _seed_diff_pair(session) -> tuple[str, str]:
    """Base: code+model+pipeline; Target: code'(digest changed)+model+retriever
    (dependency substitution) + policy permission manifest change."""
    _seed_diff_application_environment(session)
    for component_id, kind, name in (
        ("cmp_01J0000000000A01", "APPLICATION_CODE", "code"),
        ("cmp_01J0000000000A02", "MODEL_BINDING", "model"),
        ("cmp_01J0000000000A03", "RETRIEVER", "retriever"),
        ("cmp_01J0000000000A04", "POLICY", "policy"),
    ):
        _seed_component(session, component_id=component_id, kind=kind, logical_name=name)
    # base revisions
    base_code = _seed_component_revision(
        session, revision_id="crv_01J0000000000B01", component_id="cmp_01J0000000000A01",
        component_kind="APPLICATION_CODE", configuration_digest=_DIGEST_A,
    )
    base_model = _seed_component_revision(
        session, revision_id="crv_01J0000000000B02", component_id="cmp_01J0000000000A02",
        component_kind="MODEL_BINDING", configuration_digest=_DIGEST_B,
    )
    base_policy = _seed_component_revision(
        session, revision_id="crv_01J0000000000B03", component_id="cmp_01J0000000000A04",
        component_kind="POLICY", configuration_digest=_DIGEST_C,
        permission_manifest_digest="sha256:" + "1" * 64,
    )
    # target revisions
    target_code = _seed_component_revision(
        session, revision_id="crv_01J0000000000B11", component_id="cmp_01J0000000000A01",
        component_kind="APPLICATION_CODE", configuration_digest=_DIGEST_D,
    )
    target_model = _seed_component_revision(
        session, revision_id="crv_01J0000000000B12", component_id="cmp_01J0000000000A02",
        component_kind="MODEL_BINDING", configuration_digest=_DIGEST_B,
    )
    target_retriever = _seed_component_revision(
        session, revision_id="crv_01J0000000000B13", component_id="cmp_01J0000000000A03",
        component_kind="RETRIEVER", configuration_digest=_DIGEST_C,
    )
    target_policy = _seed_component_revision(
        session, revision_id="crv_01J0000000000B14", component_id="cmp_01J0000000000A04",
        component_kind="POLICY", configuration_digest=_DIGEST_C,
        permission_manifest_digest="sha256:" + "2" * 64,
    )
    base_topology = _seed_empty_topology(
        session,
        topology_id="tpr_01J0000000000C01",
        component_ids=[
            base_code.component_id,
            base_model.component_id,
            base_policy.component_id,
        ],
    )
    target_topology = _seed_empty_topology(
        session,
        topology_id="tpr_01J0000000000C02",
        component_ids=[
            target_code.component_id,
            target_model.component_id,
            target_retriever.component_id,
            target_policy.component_id,
        ],
    )
    base_bindings = [
        {
            "kind": "COMPONENT_REVISION",
            "id": revision.component_revision_id,
            "revision": 1,
            "digest": revision.record_digest,
        }
        for revision in (base_code, base_model, base_policy)
    ]
    target_bindings = [
        {
            "kind": "COMPONENT_REVISION",
            "id": revision.component_revision_id,
            "revision": 1,
            "digest": revision.record_digest,
        }
        for revision in (target_code, target_model, target_retriever, target_policy)
    ]
    base = _seed_version_set_row(
        session,
        version_set_id="vset_01J0000000000C01",
        bindings=base_bindings,
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
        bindings=target_bindings,
        topology_binding={
            "kind": "TOPOLOGY_REVISION",
            "id": target_topology.topology_revision_id,
            "revision": 1,
            "digest": target_topology.record_digest,
        },
    )
    session.commit()
    return base.system_version_set_id, target.system_version_set_id


def test_diff_semantics_digest_substitution_permission_expansion(sqlite_session) -> None:
    _seed_env(sqlite_session)
    base_id, target_id = _seed_diff_pair(sqlite_session)
    diff = _diff_service(sqlite_session).diff_system_versions(
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
    assert changed["cmp_01J0000000000A01"].base_digest == sqlite_session.get(
        ComponentRevision, "crv_01J0000000000B01"
    ).configuration_digest
    assert changed["cmp_01J0000000000A01"].target_digest == sqlite_session.get(
        ComponentRevision, "crv_01J0000000000B11"
    ).configuration_digest
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
    diff = _diff_service(sqlite_session).diff_system_versions(
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
        _diff_service(sqlite_session).diff_system_versions(
            base_id,
            target_id,
            principal=foreign,
            request_id="req_01J000000000000C",
        )
    assert raised.value.code == "RESOURCE_NOT_FOUND"
    sqlite_session.commit()


def test_get_fails_closed_on_envelope_scalar_and_receipt_tampering(
    sqlite_session,
) -> None:
    _seed_env(sqlite_session)
    response = _import(sqlite_session, _manifest())
    sqlite_session.commit()
    version_set_id = response.system_version_set.system_version_set_id

    row = sqlite_session.get(SystemVersionSet, version_set_id)
    tampered_envelope = copy.deepcopy(row.envelope_payload)
    tampered_envelope["version_set_digest"] = _DIGEST_D
    sqlite_session.execute(
        sa.update(SystemVersionSet)
        .where(SystemVersionSet.system_version_set_id == version_set_id)
        .values(envelope_payload=tampered_envelope)
    )
    sqlite_session.expire_all()
    with pytest.raises(SystemVersionsError) as raised:
        _service(sqlite_session).get_system_version(
            version_set_id,
            principal=_reader_principal(),
            request_id="req_01J000000000000E",
        )
    assert raised.value.details == {"reason": "VERSION_GRAPH_INTEGRITY_INVALID"}
    sqlite_session.rollback()

    sqlite_session.execute(
        sa.update(SystemVersionSet)
        .where(SystemVersionSet.system_version_set_id == version_set_id)
        .values(version_set_digest=_DIGEST_D)
    )
    sqlite_session.expire_all()
    with pytest.raises(SystemVersionsError) as raised:
        _service(sqlite_session).get_system_version(
            version_set_id,
            principal=_reader_principal(),
            request_id="req_01J000000000000F",
        )
    assert raised.value.details == {"reason": "VERSION_GRAPH_INTEGRITY_INVALID"}
    sqlite_session.rollback()

    row = sqlite_session.get(SystemVersionSet, version_set_id)
    sqlite_session.execute(
        sa.update(AuthorityReceipt)
        .where(
            AuthorityReceipt.authority_receipt_id == row.authority_receipt_id
        )
        .values(subject_digest=_DIGEST_D)
    )
    sqlite_session.expire_all()
    with pytest.raises(SystemVersionsError) as raised:
        _service(sqlite_session).get_system_version(
            version_set_id,
            principal=_reader_principal(),
            request_id="req_01J000000000000G",
        )
    assert raised.value.details == {"reason": "VERSION_GRAPH_INTEGRITY_INVALID"}
    sqlite_session.rollback()


def test_get_and_diff_reject_rehashed_but_inexact_child_binding(
    sqlite_session,
) -> None:
    _seed_env(sqlite_session)
    response = _import(sqlite_session, _manifest())
    sqlite_session.commit()
    version_set_id = response.system_version_set.system_version_set_id
    row = sqlite_session.get(SystemVersionSet, version_set_id)

    bindings = copy.deepcopy(row.exact_component_revision_bindings)
    bindings[0]["digest"] = _DIGEST_D
    version_set_digest = SystemVersionsService._version_set_digest(
        application_id=row.application_id,
        declared_environment_id=row.declared_environment_id,
        component_bindings=bindings,
        topology_binding=row.exact_topology_revision_binding,
        provenance_receipt_ids=row.provenance_receipt_ids,
        assurance_summary=row.identity_assurance_summary,
    )
    envelope = copy.deepcopy(row.envelope_payload)
    envelope["exact_component_revision_bindings"] = bindings
    envelope["version_set_digest"] = version_set_digest
    envelope["record_envelope"]["record_digest"] = ""
    record_digest = v5_record_digest(envelope)
    envelope["record_envelope"]["record_digest"] = record_digest
    sqlite_session.execute(
        sa.update(SystemVersionSet)
        .where(SystemVersionSet.system_version_set_id == version_set_id)
        .values(
            exact_component_revision_bindings=bindings,
            version_set_digest=version_set_digest,
            envelope_payload=envelope,
            record_digest=record_digest,
        )
    )
    sqlite_session.expire_all()

    service = _diff_service(sqlite_session)
    with pytest.raises(SystemVersionsError) as raised:
        service.get_system_version(
            version_set_id,
            principal=_reader_principal(),
            request_id="req_01J000000000000H",
        )
    assert raised.value.details == {"reason": "VERSION_GRAPH_INTEGRITY_INVALID"}
    with pytest.raises(SystemVersionsError) as raised:
        service.diff_system_versions(
            version_set_id,
            version_set_id,
            principal=_reader_principal(),
            request_id="req_01J000000000000I",
        )
    assert raised.value.details == {"reason": "VERSION_GRAPH_INTEGRITY_INVALID"}
    sqlite_session.rollback()


def test_get_rejects_rehashed_topology_with_wrong_provenance_receipt(
    sqlite_session,
) -> None:
    _seed_env(sqlite_session)
    response = _import(sqlite_session, _manifest())
    sqlite_session.commit()
    version_set = sqlite_session.get(
        SystemVersionSet, response.system_version_set.system_version_set_id
    )
    topology = sqlite_session.get(
        TopologyRevision, response.topology_revision.topology_revision_id
    )
    environment = sqlite_session.get(
        Environment, response.environment.environment_id
    )
    component = sqlite_session.get(
        SystemComponent, response.components[0].component_id
    )
    wrong_provenance = sorted(
        {component.authority_receipt_id, environment.authority_receipt_id}
    )

    topology_envelope = copy.deepcopy(topology.envelope_payload)
    topology_envelope["provenance_receipt_ids"] = wrong_provenance
    topology_envelope["record_envelope"]["record_digest"] = ""
    topology_record_digest = v5_record_digest(topology_envelope)
    topology_envelope["record_envelope"]["record_digest"] = topology_record_digest
    topology.provenance_receipt_ids = wrong_provenance
    topology.envelope_payload = topology_envelope
    topology.record_digest = topology_record_digest

    topology_binding = {
        "kind": "TOPOLOGY_REVISION",
        "id": topology.topology_revision_id,
        "revision": 1,
        "digest": topology_record_digest,
    }
    version_set_digest = SystemVersionsService._version_set_digest(
        application_id=version_set.application_id,
        declared_environment_id=version_set.declared_environment_id,
        component_bindings=version_set.exact_component_revision_bindings,
        topology_binding=topology_binding,
        provenance_receipt_ids=version_set.provenance_receipt_ids,
        assurance_summary=version_set.identity_assurance_summary,
    )
    version_envelope = copy.deepcopy(version_set.envelope_payload)
    version_envelope["exact_topology_revision_binding"] = topology_binding
    version_envelope["version_set_digest"] = version_set_digest
    version_envelope["record_envelope"]["record_digest"] = ""
    version_record_digest = v5_record_digest(version_envelope)
    version_envelope["record_envelope"]["record_digest"] = version_record_digest
    version_set.exact_topology_revision_binding = topology_binding
    version_set.version_set_digest = version_set_digest
    version_set.envelope_payload = version_envelope
    version_set.record_digest = version_record_digest

    with pytest.raises(SystemVersionsError) as raised:
        _diff_service(sqlite_session).get_system_version(
            version_set.system_version_set_id,
            principal=_reader_principal(),
            request_id="req_01J000000000000J",
        )
    assert raised.value.details == {"reason": "VERSION_GRAPH_INTEGRITY_INVALID"}
    sqlite_session.rollback()


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
    assert raised.value.commit_audit_on_denial is True
    assert raised.value.rollback_required is False
    sqlite_session.commit()
