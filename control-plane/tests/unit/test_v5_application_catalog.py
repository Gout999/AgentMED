"""V5-1A application catalog service tests (fail-closed orchestration)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
import yaml
from pydantic import SecretStr
from sqlalchemy import select

from app.models import Audit, Event, Outbox
from app.models.v4_tables import (
    AuthorityReceipt,
    ControllerRegistration,
    PublicCommandIdempotency,
    PublicPrincipal,
)
from app.models.v5_tables import (
    AIApplication,
    AIApplicationLifecycleRevision,
    DependencyEdge,
    Environment,
    SystemComponent,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.v5_models import (
    ApplicationRegisterRequest,
    ComponentRegisterRequest,
    DependencyEdgeRecordRequest,
    EnvironmentRegisterRequest,
)
from app.services.application_catalog import (
    ApplicationCatalogError,
    ApplicationCatalogService,
    V5ReadDenial,
)
from app.services.v4_audit import V4AuditService
from app.services.v4_event_store import V4EventStore, V4EventStoreError
from app.services.v5_authority import (
    V5AuthorityService,
    V5_CATALOG_OWNER,
    build_v5_controller_registration_record,
)
from app.services.v5_lifecycle_authority import (
    V5LifecycleAuthorityError,
    V5LifecycleAuthorityService,
)
from app.services.v5_manifest_import_coordinator import V5ManifestImportCoordinator
from app.utils.ids import (
    new_application_id,
    new_authority_receipt_id,
    new_catalog_environment_id,
    new_system_component_id,
)
from app.utils.v4_integrity import canonical_digest
from app.utils.v5_integrity import v5_record_digest

REPO = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
WORKSPACE = "ws_01J0000000000001"
PROJECT = "proj_01J0000000000001"
OTHER_WORKSPACE = "ws_01J0000000000002"
OTHER_PROJECT = "proj_01J0000000000002"
OWNER = "prn_01J0000000000001"
CATALOG_PRINCIPAL = "prn_01J000000000000A"
CONTROLLER_PRINCIPAL = "prn_01J000000000000B"
SUBJECT = "catalog-admin-01J0000000000001"
ISSUER = "https://auth.caseloop.dev"
AUDIENCES = ["caseloop-public-api"]
SCOPES = ["applications:manage", "applications:read"]
_APPLICATION_ID = "app_01J0000000000001"
_ENVIRONMENT_ID = "env_01J0000000000001"
_COMPONENT_A = "cmp_01J0000000000001"
_COMPONENT_B = "cmp_01J0000000000002"

_FIXTURE = yaml.safe_load(
    (REPO / "contracts/v5/fixtures/application-catalog.yaml").read_text(encoding="utf-8")
)


def _claims(workspace_id: str, project_ids: list[str], scopes: list[str]) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": SUBJECT,
            "principal_type": "human",
            "audiences": AUDIENCES,
            "workspace_id": workspace_id,
            "project_ids": project_ids,
            "environment_ids": [],
            "scopes": scopes,
        }
    )


def _principal_context(
    *,
    workspace_id: str = WORKSPACE,
    project_ids: list[str] | None = None,
    scopes: list[str] | None = None,
    principal_id: str = CATALOG_PRINCIPAL,
    required_scope: str = "applications:manage",
) -> AcceptedPrincipalContext:
    scopes = scopes or SCOPES
    return AcceptedPrincipalContext.model_validate(
        {
            "schema_version": "1.0",
            "principal_id": principal_id,
            "principal_type": "human",
            "issuer": ISSUER,
            "subject": SUBJECT,
            "audiences": AUDIENCES,
            "workspace_id": workspace_id,
            "project_ids": project_ids or [PROJECT],
            "environment_ids": [],
            "scopes": scopes,
            "credential_id": "cred_01J000000000000A",
            "jti_digest": "sha256:" + "a" * 64,
            "issued_at": NOW - timedelta(minutes=10),
            "not_before": NOW - timedelta(minutes=5),
            "expires_at": NOW + timedelta(days=30),
            "revoked_at": None,
            "revocation_checked_at": NOW,
            "requested_context": {
                "workspace_id": workspace_id,
                "project_id": PROJECT if PROJECT in (project_ids or [PROJECT]) else None,
                "environment_id": None,
                "required_scope": required_scope,
            },
            "evaluated_at": NOW,
            "claims_digest": _claims(workspace_id, project_ids or [PROJECT], scopes),
        }
    )


def _seed_principal(
    session,
    *,
    principal_id: str,
    workspace_id: str = WORKSPACE,
    scopes: list[str] | None = None,
    project_ids: list[str] | None = None,
    trust_roles: list[str] | None = None,
) -> None:
    from app.public_api.credential_resolver import digest_public_subject

    scopes = scopes or SCOPES
    project_ids = project_ids or [PROJECT]
    session.add(
        PublicPrincipal(
            principal_id=principal_id,
            workspace_id=workspace_id,
            principal_type="human",
            state="ACTIVE",
            subject_digest=digest_public_subject(SUBJECT),
            audiences=list(AUDIENCES),
            project_ids=project_ids,
            environment_ids=[],
            scopes=list(scopes),
            trust_roles=list(trust_roles or []),
            claims_digest=_claims(workspace_id, project_ids, scopes),
            revoked_at=None,
        )
    )


def _seed_v5_controller(session) -> None:
    audit = V4AuditService(session, clock=lambda: NOW)
    recorded = audit.record(
        workspace_id=WORKSPACE,
        actor_principal=OWNER,
        action="controllers.register",
        target=f"creg_01J000000000000A",
        params={
            "owner": V5_CATALOG_OWNER,
            "service_identity_digest": canonical_digest(
                {
                    "schema_version": "1.0",
                    "workspace_id": WORKSPACE,
                    "owner": V5_CATALOG_OWNER,
                    "controller_principal": CONTROLLER_PRINCIPAL,
                    "principal_type": "CONTROLLER_SERVICE",
                    "service": "caseloop-control-plane",
                }
            ),
        },
        transaction_id="txn_v5_bootstrap_controller",
        evidence_refs={
            "owner": V5_CATALOG_OWNER,
            "controller_registration_id": "creg_01J000000000000A",
            "controller_principal": CONTROLLER_PRINCIPAL,
        },
        occurred_at=NOW,
    )
    commands = [
        "applications.register",
        "applications.activate",
        "applications.archive",
        "applications.restore",
        "environments.register",
        "environments.retire",
        "environments.restore",
        "system-components.register",
        "system-components.activate",
        "system-components.deprecate",
        "system-components.reactivate",
        "system-components.retire",
        "dependency-edges.record",
    ]
    built = build_v5_controller_registration_record(
        controller_registration_id="creg_01J000000000000A",
        workspace_id=WORKSPACE,
        owner=V5_CATALOG_OWNER,
        controller_principal=CONTROLLER_PRINCIPAL,
        allowed_commands=commands,
        service_identity_digest=canonical_digest(
            {
                "schema_version": "1.0",
                "workspace_id": WORKSPACE,
                "owner": V5_CATALOG_OWNER,
                "controller_principal": CONTROLLER_PRINCIPAL,
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
        principal_id=CATALOG_PRINCIPAL,
        trust_roles=["catalog_admin"],
    )
    _seed_v5_controller(session)
    session.commit()


def _service(session, **kwargs) -> ApplicationCatalogService:
    return ApplicationCatalogService(session, clock=lambda: NOW, **kwargs)


def _count(session, model) -> int:
    from sqlalchemy import func, select

    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _activate_registered_application_for_foundation_test(session, application) -> None:
    """Prepare an ACTIVE prerequisite; this is not a public activation proof."""

    registered = application.model_dump(mode="json")
    registered_digest = registered["record_envelope"]["record_digest"]
    registered.pop("exact_previous_application_binding_or_null")
    registered["lifecycle_state"] = "ACTIVE"
    registered["exact_previous_application_binding"] = {
        "kind": "AI_APPLICATION",
        "id": registered["application_id"],
        "revision": 1,
        "digest": registered_digest,
    }
    registered["record_envelope"] = {
        **registered["record_envelope"],
        "revision": 2,
        "recorded_at": (NOW + timedelta(microseconds=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "authority_receipt_id": new_authority_receipt_id(),
        "record_digest": "",
    }
    registered["record_envelope"]["record_digest"] = v5_record_digest(registered)
    V5LifecycleAuthorityService(
        session
    )._append_activation_revision_for_foundation_test(
        kind="AI_APPLICATION", envelope_payload=registered
    )


def _raw_registered_application_payload(*, application_id: str = _APPLICATION_ID):
    payload = {
        "application_id": application_id,
        "workspace_id": WORKSPACE,
        "project_id": PROJECT,
        "slug": "permit-boundary",
        "display_name": "Permit Boundary",
        "owner_principal_ids": [OWNER],
        "criticality": "P1",
        "data_classification": "INTERNAL",
        "governance_mode": "MANAGED",
        "lifecycle_state": "REGISTERED",
        "exact_previous_application_binding_or_null": None,
        "record_envelope": {
            "schema_version": "2.0",
            "workspace_id": WORKSPACE,
            "revision": 1,
            "recorded_by_principal": CONTROLLER_PRINCIPAL,
            "recorded_at": NOW.isoformat().replace("+00:00", "Z"),
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
            "record_digest": "",
            "authority_receipt_id": new_authority_receipt_id(),
        },
    }
    payload["record_envelope"]["record_digest"] = v5_record_digest(payload)
    return payload


def _raw_activation_payload(registered: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in registered.items()
        if key not in {"exact_previous_application_binding_or_null", "record_envelope"}
    }
    payload["lifecycle_state"] = "ACTIVE"
    payload["exact_previous_application_binding"] = {
        "kind": "AI_APPLICATION",
        "id": registered["application_id"],
        "revision": 1,
        "digest": registered["record_envelope"]["record_digest"],
    }
    payload["record_envelope"] = {
        **registered["record_envelope"],
        "revision": 2,
        "authority_receipt_id": new_authority_receipt_id(),
        "record_digest": "",
    }
    payload["record_envelope"]["record_digest"] = v5_record_digest(payload)
    return payload


def _app_request(**overrides: Any) -> ApplicationRegisterRequest:
    base = _FIXTURE["applications"][0]["register_request"]
    payload = {**base, **overrides}
    return ApplicationRegisterRequest.model_validate(payload)


def _env_request(**overrides: Any) -> EnvironmentRegisterRequest:
    base = _FIXTURE["environments"][0]["register_request"]
    payload = {**base, "application_id": _APPLICATION_ID, **overrides}
    return EnvironmentRegisterRequest.model_validate(payload)


def _component_request(**overrides: Any) -> ComponentRegisterRequest:
    base = _FIXTURE["components"][0]["register_request"]
    payload = {**base, "application_id": _APPLICATION_ID, **overrides}
    return ComponentRegisterRequest.model_validate(payload)


def _edge_request(**overrides: Any) -> DependencyEdgeRecordRequest:
    base = _FIXTURE["dependency_edges"][0]["record_request"]
    payload = {**base, "application_id": _APPLICATION_ID, **overrides}
    return DependencyEdgeRecordRequest.model_validate(payload)


# ------------------------------------------------------------------------- application


def test_register_application_creates_exact_slice(sqlite_session) -> None:
    _seed_env(sqlite_session)
    response = _service(sqlite_session).register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    assert response.schema_version == "2.0"
    assert response.workspace_id == WORKSPACE
    assert response.application.record_envelope.immutable is True
    assert response.application.record_envelope.revision == 1
    assert response.application.lifecycle_state == "REGISTERED"
    assert response.idempotency.replayed is False
    assert response.idempotency.receipt.intent == "applications.register"
    assert response.idempotency.receipt.resource.kind == "ai_application"
    assert response.application.record_envelope.record_digest.startswith("sha256:")

    app = sqlite_session.get(AIApplication, response.application.application_id)
    assert app is not None and app.record_digest == app.envelope_payload["record_envelope"]["record_digest"]
    assert _count(sqlite_session, AIApplication) == 1
    assert _count(sqlite_session, AIApplicationLifecycleRevision) == 1
    assert _count(sqlite_session, Event) == 1
    assert _count(sqlite_session, Outbox) == 1
    assert _count(sqlite_session, AuthorityReceipt) == 1
    assert _count(sqlite_session, PublicCommandIdempotency) == 1
    audits = sqlite_session.scalars(
        select(Audit).where(Audit.contract_version == "v4")
    ).all()
    actions = {row.action for row in audits}
    assert {"controller.application.registered", "applications.register"} <= actions


def test_register_application_replay_same_key(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    first = service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    replay = service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000B",
    )
    assert replay.idempotency.replayed is True
    normalized = replay.model_dump(mode="json")
    normalized["idempotency"]["replayed"] = False
    assert normalized == first.model_dump(mode="json")
    assert _count(sqlite_session, AIApplication) == 1
    assert _count(sqlite_session, Event) == 1
    assert _count(sqlite_session, AuthorityReceipt) == 1


def test_register_application_replay_rechecks_revocation(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    principal_row = sqlite_session.get(PublicPrincipal, CATALOG_PRINCIPAL)
    assert principal_row is not None
    principal_row.state = "REVOKED"
    principal_row.revoked_at = NOW
    sqlite_session.commit()

    with pytest.raises(ApplicationCatalogError) as raised:
        service.register_application(
            _app_request(),
            principal=_principal_context(),
            idempotency_key="app-register-0001",
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "TOKEN_INVALID"
    assert _count(sqlite_session, AIApplication) == 1
    assert _count(sqlite_session, Event) == 1
    assert _count(sqlite_session, AuthorityReceipt) == 1
    assert _count(sqlite_session, PublicCommandIdempotency) == 1


def test_register_application_rejects_roleless_principal_before_idempotency(
    sqlite_session,
) -> None:
    _seed_env(sqlite_session)
    principal_row = sqlite_session.get(PublicPrincipal, CATALOG_PRINCIPAL)
    assert principal_row is not None
    principal_row.trust_roles = []
    sqlite_session.commit()

    with pytest.raises(ApplicationCatalogError) as raised:
        _service(sqlite_session).register_application(
            _app_request(),
            principal=_principal_context(),
            idempotency_key="app-register-roleless",
            request_id="req_01J000000000000A",
        )
    assert raised.value.code == "SCOPE_FORBIDDEN"
    assert _count(sqlite_session, AIApplication) == 0
    assert _count(sqlite_session, PublicCommandIdempotency) == 0


def test_register_application_replay_rechecks_project_claims(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    changed_context = _principal_context(project_ids=[OTHER_PROJECT])
    principal_row = sqlite_session.get(PublicPrincipal, CATALOG_PRINCIPAL)
    assert principal_row is not None
    principal_row.project_ids = [OTHER_PROJECT]
    principal_row.claims_digest = changed_context.claims_digest
    sqlite_session.commit()

    with pytest.raises(ApplicationCatalogError) as raised:
        service.register_application(
            _app_request(),
            principal=changed_context,
            idempotency_key="app-register-0001",
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "WORKSPACE_ACCESS_DENIED"
    assert _count(sqlite_session, AIApplication) == 1
    assert _count(sqlite_session, Event) == 1
    assert _count(sqlite_session, AuthorityReceipt) == 1
    assert _count(sqlite_session, PublicCommandIdempotency) == 1


def test_register_application_duplicate_slug_conflicts(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    with pytest.raises(ApplicationCatalogError) as raised:
        service.register_application(
            _app_request(slug="case-review-assistant"),
            principal=_principal_context(),
            idempotency_key="app-register-0002",
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "CATALOG_CONFLICT"
    sqlite_session.rollback()


def test_register_application_unknown_owner_principal(sqlite_session) -> None:
    _seed_env(sqlite_session)
    with pytest.raises(ApplicationCatalogError) as raised:
        _service(sqlite_session).register_application(
            _app_request(owner_principal_ids=["prn_01J0000000000ZZZ"]),
            principal=_principal_context(),
            idempotency_key="app-register-0001",
            request_id="req_01J000000000000A",
        )
    assert raised.value.code == "VALIDATION_FAILED"
    sqlite_session.rollback()


def test_register_application_cross_workspace_denied(sqlite_session) -> None:
    _seed_env(sqlite_session)
    # A foreign principal exists in another workspace and requests a project
    # it has no grant for: WORKSPACE_ACCESS_DENIED (opaque, 403 semantics).
    foreign_principal = "prn_01J00000000000F1"
    _seed_principal(
        sqlite_session,
        principal_id=foreign_principal,
        workspace_id=OTHER_WORKSPACE,
        scopes=SCOPES,
        project_ids=[OTHER_PROJECT],
        trust_roles=["catalog_admin"],
    )
    sqlite_session.commit()
    foreign = _principal_context(
        workspace_id=OTHER_WORKSPACE,
        project_ids=[OTHER_PROJECT],
        principal_id=foreign_principal,
    )
    with pytest.raises(ApplicationCatalogError) as raised:
        _service(sqlite_session).register_application(
            _app_request(),
            principal=foreign,
            idempotency_key="app-register-0001",
            request_id="req_01J000000000000A",
        )
    assert raised.value.code == "WORKSPACE_ACCESS_DENIED"
    sqlite_session.rollback()

    # A principal that does not exist at all in the requested workspace.
    ghost = _principal_context(workspace_id=OTHER_WORKSPACE, project_ids=[OTHER_PROJECT])
    with pytest.raises(ApplicationCatalogError) as raised:
        _service(sqlite_session).register_application(
            _app_request(),
            principal=ghost,
            idempotency_key="app-register-0002",
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "TOKEN_INVALID"
    sqlite_session.rollback()


def test_register_application_principal_type_forbidden(sqlite_session) -> None:
    _seed_env(sqlite_session)
    connector = AcceptedPrincipalContext.model_validate(
        {
            "schema_version": "1.0",
            "principal_id": CATALOG_PRINCIPAL,
            "principal_type": "connector",
            "issuer": ISSUER,
            "subject": SUBJECT,
            "audiences": AUDIENCES,
            "workspace_id": WORKSPACE,
            "project_ids": [PROJECT],
            "environment_ids": [],
            "scopes": SCOPES,
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
                "required_scope": "applications:manage",
            },
            "evaluated_at": NOW,
            "claims_digest": canonical_digest(
                {
                    "schema_version": "1.0",
                    "issuer": ISSUER,
                    "subject": SUBJECT,
                    "principal_type": "connector",
                    "audiences": AUDIENCES,
                    "workspace_id": WORKSPACE,
                    "project_ids": [PROJECT],
                    "environment_ids": [],
                    "scopes": SCOPES,
                }
            ),
        }
    )
    # The connector row does not match the accepted context -> TOKEN_INVALID.
    with pytest.raises(ApplicationCatalogError) as raised:
        _service(sqlite_session).register_application(
            _app_request(),
            principal=connector,
            idempotency_key="app-register-0001",
            request_id="req_01J000000000000A",
        )
    assert raised.value.code == "TOKEN_INVALID"
    sqlite_session.rollback()


# ------------------------------------------------------- environment / component / edge


def test_register_environment_requires_application_and_unique_name(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    app = service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    application_id = app.application.application_id
    _activate_registered_application_for_foundation_test(sqlite_session, app.application)
    response = service.register_environment(
        _env_request(application_id=application_id),
        principal=_principal_context(),
        idempotency_key="env-register-0001",
        request_id="req_01J000000000000B",
    )
    assert response.environment.lifecycle_state == "ACTIVE"
    event = sqlite_session.scalar(
        select(Event).where(
            Event.event_type == "environment.registered",
            Event.aggregate_id == response.environment.environment_id,
        )
    )
    assert event is not None
    assert event.contract_version == "v5"
    assert event.event_contract_major == 2
    assert event.exact_subject_binding["revision"] == 1
    receipt = sqlite_session.get(AuthorityReceipt, event.authority_receipt_id)
    assert receipt is not None and receipt.subject_revision == 1
    assert receipt.receipt_payload["source_event_id"] == event.event_id
    sqlite_session.commit()

    with pytest.raises(ApplicationCatalogError) as raised:
        service.register_environment(
            _env_request(application_id=application_id),
            principal=_principal_context(),
            idempotency_key="env-register-0002",
            request_id="req_01J000000000000C",
        )
    assert raised.value.code == "CATALOG_CONFLICT"
    sqlite_session.rollback()

    with pytest.raises(ApplicationCatalogError) as raised:
        service.register_environment(
            _env_request(application_id="app_01J0000000000ZZZ"),
            principal=_principal_context(),
            idempotency_key="env-register-0003",
            request_id="req_01J000000000000D",
        )
    assert raised.value.code == "RESOURCE_NOT_FOUND"
    sqlite_session.rollback()


def test_register_component_requires_active_parent_before_idempotency(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    app = service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()

    with pytest.raises(ApplicationCatalogError) as raised:
        service.register_component(
            _component_request(application_id=app.application.application_id),
            principal=_principal_context(),
            idempotency_key="component-register-inactive",
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "CATALOG_CONFLICT"
    assert raised.value.details == {"reason": "APPLICATION_NOT_ACTIVE"}
    assert _count(sqlite_session, SystemComponent) == 0
    assert sqlite_session.scalar(
        select(sa.func.count()).select_from(PublicCommandIdempotency).where(
            PublicCommandIdempotency.intent == "system-components.register"
        )
    ) == 0
    sqlite_session.rollback()


def test_register_component_duplicate_identity(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    app = service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    application_id = app.application.application_id
    _activate_registered_application_for_foundation_test(sqlite_session, app.application)
    service.register_component(
        _component_request(application_id=application_id),
        principal=_principal_context(),
        idempotency_key="component-register-0001",
        request_id="req_01J000000000000B",
    )
    sqlite_session.commit()
    with pytest.raises(ApplicationCatalogError) as raised:
        service.register_component(
            _component_request(application_id=application_id),
            principal=_principal_context(),
            idempotency_key="component-register-0002",
            request_id="req_01J000000000000C",
        )
    assert raised.value.code == "CATALOG_CONFLICT"
    sqlite_session.rollback()


def _register_full_graph(service, *, session) -> tuple[str, str, str]:
    app = service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    session.commit()
    application_id = app.application.application_id
    _activate_registered_application_for_foundation_test(session, app.application)
    first = service.register_component(
        _component_request(
            application_id=application_id,
            logical_name="triage-agent",
            owner_principal_ids=[OWNER],
        ),
        principal=_principal_context(),
        idempotency_key="component-register-0001",
        request_id="req_01J000000000000B",
    )
    session.commit()
    second = service.register_component(
        _component_request(
            application_id=application_id,
            component_kind="SKILL",
            logical_name="triage-skill",
            owner_principal_ids=[OWNER],
        ),
        principal=_principal_context(),
        idempotency_key="component-register-0002",
        request_id="req_01J000000000000C",
    )
    session.commit()
    return application_id, first.component.component_id, second.component.component_id


def test_record_edge_and_reject_self_missing_cycle(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    application_id, component_a, component_b = _register_full_graph(
        service, session=sqlite_session
    )

    response = service.record_dependency_edge(
        _edge_request(
            application_id=application_id,
            from_component_id=component_a,
            to_component_id=component_b,
        ),
        principal=_principal_context(),
        idempotency_key="edge-record-0001",
        request_id="req_01J000000000000D",
    )
    assert response.edge.edge_digest.startswith("sha256:")
    event = sqlite_session.scalar(
        select(Event).where(
            Event.event_type == "dependency_edge.recorded",
            Event.aggregate_id == response.edge.edge_id,
        )
    )
    assert event is not None
    assert event.contract_version == "v5"
    assert event.event_contract_major == 2
    assert event.exact_subject_binding["revision"] == 1
    receipt = sqlite_session.get(AuthorityReceipt, event.authority_receipt_id)
    assert receipt is not None and receipt.subject_revision == 1
    assert receipt.receipt_payload["source_event_id"] == event.event_id
    sqlite_session.commit()

    # A -> B already exists; B -> A must cycle.
    with pytest.raises(ApplicationCatalogError) as raised:
        service.record_dependency_edge(
            _edge_request(
                application_id=application_id,
                from_component_id=component_b,
                to_component_id=component_a,
            ),
            principal=_principal_context(),
            idempotency_key="edge-record-0002",
            request_id="req_01J000000000000E",
        )
    assert raised.value.code == "VALIDATION_FAILED"
    assert raised.value.details == {"reason": "GRAPH_CYCLE"}
    sqlite_session.rollback()

    # Missing component reference.
    with pytest.raises(ApplicationCatalogError) as raised:
        service.record_dependency_edge(
            _edge_request(
                application_id=application_id,
                from_component_id=component_a,
                to_component_id="cmp_01J0000000000ZZZ",
            ),
            principal=_principal_context(),
            idempotency_key="edge-record-0003",
            request_id="req_01J000000000000F",
        )
    assert raised.value.code == "RESOURCE_NOT_FOUND"
    sqlite_session.rollback()


# -------------------------------------------------------------------------- reads


def test_get_application_and_missing_denial(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    registered = service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    got = service.get_application(
        registered.application.application_id,
        principal=_principal_context(required_scope="applications:read"),
        request_id="req_01J000000000000B",
    )
    assert (
        got.application.record_envelope.record_digest
        == registered.application.record_envelope.record_digest
    )
    sqlite_session.commit()

    with pytest.raises(V5ReadDenial) as raised:
        service.get_application(
            "app_01J0000000000ZZZ",
            principal=_principal_context(required_scope="applications:read"),
            request_id="req_01J000000000000C",
        )
    assert raised.value.code == "RESOURCE_NOT_FOUND"
    assert raised.value.rollback_required is False
    sqlite_session.commit()


def test_get_application_cross_project_denied(sqlite_session) -> None:
    """Same workspace, but a reader without the application's project grant
    must receive an audited RESOURCE_NOT_FOUND (OPAQUE_NOT_FOUND)."""
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    registered = service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    application_id = registered.application.application_id

    # Same workspace + applications:read, but granted only OTHER_PROJECT.
    foreign_reader = "prn_01J00000000000F2"
    _seed_principal(
        sqlite_session,
        principal_id=foreign_reader,
        scopes=["applications:read"],
        project_ids=[OTHER_PROJECT],
    )
    sqlite_session.commit()
    reader = _principal_context(
        principal_id=foreign_reader,
        project_ids=[OTHER_PROJECT],
        scopes=["applications:read"],
        required_scope="applications:read",
    )
    with pytest.raises(V5ReadDenial) as raised:
        service.get_application(
            application_id,
            principal=reader,
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "RESOURCE_NOT_FOUND"
    assert raised.value.rollback_required is False
    sqlite_session.commit()

    # The same-project reader is unaffected.
    got = service.get_application(
        application_id,
        principal=_principal_context(required_scope="applications:read"),
        request_id="req_01J000000000000C",
    )
    assert got.application.application_id == application_id
    sqlite_session.commit()


def test_get_application_tampered_digest_fails_closed(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    registered = service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    # Raw SQL bypasses the ORM immutable-write guard so we can simulate a
    # persisted-digest tamper exactly as an attacker or a bug would leave it.
    sqlite_session.execute(
        sa.text("UPDATE ai_applications SET record_digest = :digest WHERE application_id = :id"),
        {"digest": "sha256:" + "f" * 64, "id": registered.application.application_id},
    )
    sqlite_session.commit()
    with pytest.raises(ApplicationCatalogError) as raised:
        service.get_application(
            registered.application.application_id,
            principal=_principal_context(required_scope="applications:read"),
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "INTERNAL_ERROR"
    sqlite_session.rollback()


# ------------------------------------------------------------- failure semantics


def test_audit_failure_rolls_back_everything(sqlite_session) -> None:
    _seed_env(sqlite_session)
    failing_audit = V4AuditService(sqlite_session, clock=lambda: NOW, force_fail=False, fail_on_call=2)
    with pytest.raises(ApplicationCatalogError) as raised:
        _service(sqlite_session, audit_service=failing_audit).register_application(
            _app_request(),
            principal=_principal_context(),
            idempotency_key="app-register-0001",
            request_id="req_01J000000000000A",
        )
    assert raised.value.code == "AUDIT_UNAVAILABLE"
    sqlite_session.rollback()
    for model in (
        AIApplication,
        Event,
        Outbox,
        AuthorityReceipt,
        PublicCommandIdempotency,
    ):
        assert _count(sqlite_session, model) == 0


def test_activation_storage_rejects_direct_and_forged_callers(sqlite_session) -> None:
    _seed_env(sqlite_session)
    registered = _raw_registered_application_payload()
    lifecycle = V5LifecycleAuthorityService(sqlite_session)
    lifecycle.append_registration_revision(
        kind="AI_APPLICATION", envelope_payload=registered
    )
    activated = _raw_activation_payload(registered)

    with pytest.raises(V5LifecycleAuthorityError) as direct:
        lifecycle.append_activation_revision(
            kind="AI_APPLICATION", envelope_payload=activated
        )
    assert direct.value.code == "v5.lifecycle.composition_required"

    with pytest.raises(V5LifecycleAuthorityError) as forged:
        lifecycle.append_composed_activation_revision(
            kind="AI_APPLICATION",
            envelope_payload=activated,
            transaction_id="txn_manifest_forged",
            composition_capability=object(),
        )
    assert forged.value.code == "v5.manifest.capability_forged"
    with pytest.raises(V4EventStoreError) as forged_event:
        V4EventStore(sqlite_session).append_composed_activation_event(
            workspace_id=WORKSPACE,
            aggregate_type="ai_application",
            aggregate_id=_APPLICATION_ID,
            event_type="application.activated",
            payload={
                "exact_previous_application_binding": activated[
                    "exact_previous_application_binding"
                ],
                "exact_application_binding": {
                    "kind": "AI_APPLICATION",
                    "id": _APPLICATION_ID,
                    "revision": 2,
                    "digest": activated["record_envelope"]["record_digest"],
                },
                "lifecycle_state": "ACTIVE",
                "manifest_activation_context": {},
                "initiating_command_audit_ref": "audit://aud_forged0001",
            },
            causation_id="req_01J00000000000F1",
            correlation_id=_APPLICATION_ID,
            actor_principal=CONTROLLER_PRINCIPAL,
            transaction_id="txn_manifest_forged",
            occurred_at=NOW,
            authority_receipt_id=activated["record_envelope"][
                "authority_receipt_id"
            ],
            composition_capability=object(),
        )
    assert forged_event.value.code == "v5.manifest.capability_forged"
    row = sqlite_session.get(AIApplication, _APPLICATION_ID)
    assert row is not None and row.revision == 1
    assert row.lifecycle_state == "REGISTERED"
    assert _count(sqlite_session, AIApplicationLifecycleRevision) == 1


def test_activation_permit_cannot_cross_transaction(sqlite_session) -> None:
    _seed_env(sqlite_session)
    import_principal_id = "prn_01J000000000000C"
    import_scopes = ["system_manifests:import"]
    _seed_principal(
        sqlite_session,
        principal_id=import_principal_id,
        scopes=import_scopes,
        trust_roles=["integrator"],
    )
    principal = _principal_context(
        principal_id=import_principal_id,
        scopes=import_scopes,
        required_scope="system_manifests:import",
    )
    registered = _raw_registered_application_payload()
    lifecycle = V5LifecycleAuthorityService(sqlite_session)
    lifecycle.append_registration_revision(
        kind="AI_APPLICATION", envelope_payload=registered
    )
    activated = _raw_activation_payload(registered)
    previous = activated["exact_previous_application_binding"]
    current = {
        "kind": "AI_APPLICATION",
        "id": activated["application_id"],
        "revision": 2,
        "digest": activated["record_envelope"]["record_digest"],
    }
    transaction_id = "txn_manifest_cross_transaction"
    request_digest = canonical_digest({"request": "cross-transaction"})
    manifest_digest = canonical_digest({"manifest": "cross-transaction"})
    idempotency_key = "manifest-cross-transaction"
    root_audit = V4AuditService(sqlite_session, clock=lambda: NOW).record(
        workspace_id=WORKSPACE,
        actor_principal=import_principal_id,
        action="system-manifests.import",
        target="",
        params={
            "authenticated_request_digest": request_digest,
            "manifest_digest": manifest_digest,
            "idempotency_key": idempotency_key,
        },
        transaction_id=transaction_id,
        evidence_refs={"manifest_digest": manifest_digest},
        occurred_at=NOW,
    )
    coordinator = V5ManifestImportCoordinator(sqlite_session)
    coordinator.validate_root(
        principal=principal,
        project_id=PROJECT,
        transaction_id=transaction_id,
        authenticated_request_digest=request_digest,
        manifest_digest=manifest_digest,
        idempotency_key=idempotency_key,
        initiating_audit_ref=root_audit.audit_ref,
    )
    controller = V5AuthorityService(sqlite_session).resolve_controller(
        workspace_id=WORKSPACE,
        subject_kind="AI_APPLICATION",
        command="applications.activate",
        event_type="application.activated",
        recorded_at=NOW,
    )
    permit = coordinator._issue(
        purpose="STORAGE_ACTIVATE",
        controller=controller,
        principal=principal,
        transaction_id=transaction_id,
        authenticated_request_digest=request_digest,
        manifest_digest=manifest_digest,
        idempotency_key=idempotency_key,
        initiating_audit_ref=root_audit.audit_ref,
        subject_kind="AI_APPLICATION",
        subject_id=activated["application_id"],
        previous_binding=previous,
        new_binding=current,
        event_type="application.activated",
        recorded_at=NOW,
    )
    sqlite_session.commit()

    with pytest.raises(V5LifecycleAuthorityError) as raised:
        lifecycle.append_composed_activation_revision(
            kind="AI_APPLICATION",
            envelope_payload=activated,
            transaction_id=transaction_id,
            composition_capability=permit,
        )
    assert raised.value.code == "v5.manifest.capability_binding_mismatch"
    row = sqlite_session.get(AIApplication, _APPLICATION_ID)
    assert row is not None and row.revision == 1


def test_idempotency_conflict_on_different_payload(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _service(sqlite_session)
    service.register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
    sqlite_session.commit()
    with pytest.raises(ApplicationCatalogError) as raised:
        service.register_application(
            _app_request(slug="different-slug"),
            principal=_principal_context(),
            idempotency_key="app-register-0001",
            request_id="req_01J000000000000B",
        )
    assert raised.value.code == "IDEMPOTENCY_CONFLICT"
    sqlite_session.rollback()


def test_service_never_commits(sqlite_session, monkeypatch) -> None:
    _seed_env(sqlite_session)
    commit = lambda: (_ for _ in ()).throw(AssertionError("service must not commit"))
    monkeypatch.setattr(sqlite_session, "commit", commit)
    _service(sqlite_session).register_application(
        _app_request(),
        principal=_principal_context(),
        idempotency_key="app-register-0001",
        request_id="req_01J000000000000A",
    )
