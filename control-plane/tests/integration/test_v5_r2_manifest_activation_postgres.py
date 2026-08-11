"""R2 manifest-only activation proof on migrated PostgreSQL.

This suite is intentionally not part of offline runs.  It exercises the real
public HTTP import route, PostgreSQL advisory-lock idempotency, the same-UoW
lifecycle/event/audit/receipt graph, and rollback after a post-permit failure.
"""
from __future__ import annotations

import secrets
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import Audit, Event, Outbox
from app.models.v4_tables import (
    AuthorityReceipt,
    ControllerRegistration,
    PublicCommandIdempotency,
    PublicCredential,
    PublicPrincipal,
)
from app.models.v5_tables import (
    AIApplication,
    AIApplicationLifecycleRevision,
    BootstrapAttestation,
    ComponentRevision,
    DependencyEdge,
    Environment,
    SystemComponent,
    SystemComponentLifecycleRevision,
    SystemAssignment,
    SystemVersionSet,
    TopologyRevision,
)
from app.public_api.credential_resolver import (
    digest_public_subject,
    hash_opaque_bearer,
)
from app.services.system_versions import SystemVersionsService
from app.services.v4_audit import V4AuditService
from app.services.v5_authority import build_v5_controller_registration_record
from app.utils.v4_integrity import canonical_digest

from conftest import (
    TEST_DATABASE_URL,
    UnsafeIntegrationDatabaseError,
    _new_pg_engine,
    _reset_pg_database_for_migrations,
)

pytestmark = pytest.mark.integration

ISSUER = "https://auth.caseloop.dev"
AUDIENCES = ["caseloop-public-api"]
SCOPES = [
    "system_manifests:import",
    "system_versions:read",
    "applications:manage",
    "applications:read",
]
NOW = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)


def _alembic_config(root: Path) -> Config:
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    return config


def _claims(*, subject: str, workspace_id: str, project_id: str) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": ISSUER,
            "subject": subject,
            "principal_type": "human",
            "audiences": AUDIENCES,
            "workspace_id": workspace_id,
            "project_ids": [project_id],
            "environment_ids": [],
            "scopes": SCOPES,
        }
    )


def _controller_commands(owner: str) -> list[str]:
    if owner == "application-catalog-controller":
        return [
            "applications.register",
            "applications.activate",
            "environments.register",
            "system-components.register",
            "system-components.activate",
            "dependency-edges.record",
        ]
    return [
        "component-revisions.record",
        "topology-revisions.record",
        "system-versions.record",
        "bootstrap-attestations.record",
        "system-assignments.record",
    ]


def _seed_controller(
    session: Session,
    *,
    workspace_id: str,
    owner: str,
    owner_principal: str,
    controller_principal: str,
    registration_id: str,
) -> None:
    service_digest = canonical_digest(
        {
            "schema_version": "1.0",
            "workspace_id": workspace_id,
            "owner": owner,
            "controller_principal": controller_principal,
            "principal_type": "CONTROLLER_SERVICE",
            "service": "caseloop-control-plane",
        }
    )
    audit = V4AuditService(session, clock=lambda: NOW).record(
        workspace_id=workspace_id,
        actor_principal=owner_principal,
        action="controllers.register",
        target=registration_id,
        params={"owner": owner, "service_identity_digest": service_digest},
        transaction_id=f"txn_seed_{registration_id}",
        evidence_refs={
            "owner": owner,
            "controller_registration_id": registration_id,
            "controller_principal": controller_principal,
        },
        occurred_at=NOW,
    )
    built = build_v5_controller_registration_record(
        controller_registration_id=registration_id,
        workspace_id=workspace_id,
        owner=owner,
        controller_principal=controller_principal,
        allowed_commands=_controller_commands(owner),
        service_identity_digest=service_digest,
        registered_by_human_principal=owner_principal,
        registration_audit_ref=audit.audit_ref,
        valid_from=NOW,
        registered_at=NOW,
    )
    session.add(ControllerRegistration(**built.row_values))


def _seed_workspace(
    session: Session,
    *,
    suffix: str,
    raw_token: str,
    pepper: str,
) -> dict[str, str]:
    workspace_id = f"ws_01J0000000000{suffix}01"
    project_id = f"proj_01J000000000{suffix}01"
    owner = f"prn_01J0000000000{suffix}01"
    principal_id = f"prn_01J0000000000{suffix}02"
    subject = f"r2-importer-{suffix}"
    claims = _claims(subject=subject, workspace_id=workspace_id, project_id=project_id)
    session.add_all(
        [
            PublicPrincipal(
                principal_id=owner,
                workspace_id=workspace_id,
                principal_type="human",
                state="ACTIVE",
                subject_digest=digest_public_subject(f"r2-owner-{suffix}"),
                audiences=AUDIENCES,
                project_ids=[project_id],
                environment_ids=[],
                scopes=["cases:read"],
                trust_roles=[],
                claims_digest=canonical_digest({"owner": owner}),
                revoked_at=None,
            ),
            PublicPrincipal(
                principal_id=principal_id,
                workspace_id=workspace_id,
                principal_type="human",
                state="ACTIVE",
                subject_digest=digest_public_subject(subject),
                audiences=AUDIENCES,
                project_ids=[project_id],
                environment_ids=[],
                scopes=SCOPES,
                trust_roles=["integrator"],
                claims_digest=claims,
                revoked_at=None,
            ),
        ]
    )
    session.flush()
    session.add(
        PublicCredential(
            credential_id=f"cred_01J000000000{suffix}01",
            workspace_id=workspace_id,
            principal_id=principal_id,
            issuer=ISSUER,
            subject=subject,
            credential_hash=hash_opaque_bearer(raw_token, pepper),
            hash_algorithm="hmac-sha256-v1",
            jti_digest="sha256:" + suffix.lower()[0] * 64,
            claims_digest=claims,
            audiences=AUDIENCES,
            project_ids=[project_id],
            environment_ids=[],
            scopes=SCOPES,
            state="ACTIVE",
            issued_at=NOW - timedelta(minutes=10),
            not_before=NOW - timedelta(minutes=5),
            expires_at=NOW + timedelta(days=30),
        )
    )
    session.flush()
    _seed_controller(
        session,
        workspace_id=workspace_id,
        owner="application-catalog-controller",
        owner_principal=owner,
        controller_principal=f"prn_01J0000000000{suffix}03",
        registration_id=f"creg_01J000000000{suffix}01",
    )
    _seed_controller(
        session,
        workspace_id=workspace_id,
        owner="version-controller",
        owner_principal=owner,
        controller_principal=f"prn_01J0000000000{suffix}04",
        registration_id=f"creg_01J000000000{suffix}02",
    )
    session.flush()
    return {
        "workspace_id": workspace_id,
        "project_id": project_id,
        "owner": owner,
        "principal_id": principal_id,
        "token": raw_token,
    }


def _manifest(seed: dict[str, str]) -> dict:
    return {
        "schema_version": "2.0",
        "application": {
            "project_id": seed["project_id"],
            "slug": "r2-postgres-app",
            "display_name": "R2 PostgreSQL App",
            "owner_principal_ids": [seed["owner"]],
            "criticality": "P0",
            "data_classification": "INTERNAL",
            "governance_mode": "MANAGED",
        },
        "environment": {"logical_name": "prod", "risk_classification": "HIGH"},
        "components": [
            {
                "logical_name": "app-code",
                "component_kind": "APPLICATION_CODE",
                "owner_principal_ids": [seed["owner"]],
                "criticality": "P0",
                "data_classification": "INTERNAL",
                "permission_classification": "READ_WRITE",
                "effect_classification": "LOCAL",
                "revision": {
                    "identity_locator": {"type": "git", "path": "."},
                    "identity_assurance": "IMMUTABLE_DIGEST",
                    "content_digest": "sha256:" + "a" * 64,
                },
            },
            {
                "logical_name": "model",
                "component_kind": "MODEL_BINDING",
                "owner_principal_ids": [seed["owner"]],
                "criticality": "P1",
                "data_classification": "INTERNAL",
                "permission_classification": "READ_ONLY",
                "effect_classification": "NONE",
                "revision": {
                    "identity_locator": {"type": "provider", "name": "model"},
                    "identity_assurance": "MUTABLE_ALIAS",
                    "provider_origin": "https://provider.invalid",
                    "resolved_at": "2026-08-11T08:00:00Z",
                },
            },
        ],
        "dependency_edges": [
            {
                "from_component": "app-code",
                "to_component": "model",
                "relation": "INVOKES",
                "required": True,
            }
        ],
        "approver_policy": None,
    }


def _headers(seed: dict[str, str], *, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {seed['token']}",
        "X-CaseLoop-Workspace-ID": seed["workspace_id"],
        "X-CaseLoop-Contract-Version": "2.0",
        "X-CaseLoop-Idempotency-Key": key,
    }


def test_r2_manifest_activation_atomic_graph_and_concurrency_postgres() -> None:
    control_plane_root = Path(__file__).resolve().parents[2]
    if sa.engine.make_url(TEST_DATABASE_URL).database != "control_plane_test":
        raise UnsafeIntegrationDatabaseError(
            "caseloop.integration_reset.refused.v5_exact_database_required"
        )
    engine = _new_pg_engine()
    try:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        command.upgrade(_alembic_config(control_plane_root), "head")
        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "012"

        pepper = secrets.token_urlsafe(48)
        with Session(engine) as session:
            primary = _seed_workspace(
                session, suffix="A1", raw_token=secrets.token_urlsafe(48), pepper=pepper
            )
            rollback_seed = _seed_workspace(
                session, suffix="B1", raw_token=secrets.token_urlsafe(48), pepper=pepper
            )
            race_seed = _seed_workspace(
                session, suffix="C1", raw_token=secrets.token_urlsafe(48), pepper=pepper
            )
            session.commit()

        settings = Settings(
            database_url=TEST_DATABASE_URL,
            public_credential_hash_pepper=SecretStr(pepper),
            public_cursor_signing_key=SecretStr(secrets.token_urlsafe(48)),
            public_auth_issuer=ISSUER,
            require_mcp_role_tokens=False,
        )
        app = create_app(settings=settings, engine=engine, create_tables=False)
        app.state.system_versions_service_factory = lambda session: SystemVersionsService(
            session, clock=lambda: NOW
        )
        with TestClient(app) as client:
            barrier = threading.Barrier(2)
            results: list[tuple[int, dict]] = []
            lock = threading.Lock()

            def import_once() -> None:
                barrier.wait(timeout=10)
                response = client.post(
                    "/api/v2/system-manifests:import",
                    headers=_headers(primary, key="r2-concurrent-import"),
                    json=_manifest(primary),
                )
                with lock:
                    results.append((response.status_code, response.json()))

            threads = [threading.Thread(target=import_once) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            assert [status for status, _ in results] == [201, 201]
            assert {body["idempotency"]["replayed"] for _, body in results} == {
                False,
                True,
            }
            created = next(body for _, body in results if not body["idempotency"]["replayed"])

            # Standalone catalog registration remains REGISTERED; no public
            # standalone activation route is introduced by R2.
            standalone = client.post(
                "/api/v2/applications",
                headers=_headers(primary, key="r2-standalone-register"),
                json={
                    "schema_version": "2.0",
                    "project_id": primary["project_id"],
                    "slug": "standalone-registered",
                    "display_name": "Standalone Registered",
                    "owner_principal_ids": [primary["owner"]],
                    "criticality": "P2",
                    "data_classification": "INTERNAL",
                    "governance_mode": "MANAGED",
                },
            )
            assert standalone.status_code == 201, standalone.text
            assert standalone.json()["application"]["lifecycle_state"] == "REGISTERED"

            # Direct component registration is valid only under the ACTIVE
            # application produced by manifest composition.  This exercises
            # the standalone major-2 registration/receipt path on real PG.
            direct_component_response = client.post(
                "/api/v2/system-components",
                headers=_headers(primary, key="r2-direct-component"),
                json={
                    "schema_version": "2.0",
                    "application_id": created["application"]["application_id"],
                    "component_kind": "SKILL",
                    "logical_name": "cycle-c",
                    "owner_principal_ids": [primary["owner"]],
                    "criticality": "P1",
                    "data_classification": "INTERNAL",
                    "permission_classification": "READ_ONLY",
                    "effect_classification": "NONE",
                    "dataset_role": None,
                },
            )
            assert direct_component_response.status_code == 201, direct_component_response.text
            direct_component = direct_component_response.json()["component"]
            assert direct_component["lifecycle_state"] == "REGISTERED"
            assert direct_component["record_envelope"]["revision"] == 1
            assert direct_component["dataset_role"] is None

            edge_barrier = threading.Barrier(2)
            edge_results: list[tuple[int, dict]] = []
            edge_lock = threading.Lock()
            active_component_id = created["components"][0]["component_id"]
            direct_component_id = direct_component["component_id"]

            def record_opposite_edge(
                *, from_component_id: str, to_component_id: str, key: str
            ) -> None:
                edge_barrier.wait(timeout=10)
                response = client.post(
                    "/api/v2/dependency-edges",
                    headers=_headers(primary, key=key),
                    json={
                        "schema_version": "2.0",
                        "application_id": created["application"]["application_id"],
                        "from_component_id": from_component_id,
                        "to_component_id": to_component_id,
                        "relation": "DEPENDS_ON",
                        "required": True,
                    },
                )
                with edge_lock:
                    edge_results.append((response.status_code, response.json()))

            edge_threads = [
                threading.Thread(
                    target=record_opposite_edge,
                    kwargs={
                        "from_component_id": active_component_id,
                        "to_component_id": direct_component_id,
                        "key": "r2-cycle-edge-a-c",
                    },
                ),
                threading.Thread(
                    target=record_opposite_edge,
                    kwargs={
                        "from_component_id": direct_component_id,
                        "to_component_id": active_component_id,
                        "key": "r2-cycle-edge-c-a",
                    },
                ),
            ]
            for thread in edge_threads:
                thread.start()
            for thread in edge_threads:
                thread.join(timeout=30)
            assert sorted(status for status, _ in edge_results) == [201, 422]
            rejected_edge = next(body for status, body in edge_results if status == 422)
            assert rejected_edge["error"]["code"] == "VALIDATION_FAILED"
            assert rejected_edge["error"]["details"] == {"reason": "GRAPH_CYCLE"}

            # A workspace-scoped transaction lock serializes competing bootstrap
            # manifests even when their idempotency keys and bodies differ.
            race_barrier = threading.Barrier(2)
            race_results: list[tuple[int, dict]] = []
            race_lock = threading.Lock()

            def import_competing_manifest(*, key: str, slug: str) -> None:
                body = _manifest(race_seed)
                body["application"]["slug"] = slug
                race_barrier.wait(timeout=10)
                response = client.post(
                    "/api/v2/system-manifests:import",
                    headers=_headers(race_seed, key=key),
                    json=body,
                )
                with race_lock:
                    race_results.append((response.status_code, response.json()))

            race_threads = [
                threading.Thread(
                    target=import_competing_manifest,
                    kwargs={"key": "r2-race-manifest-a", "slug": "r2-race-app-a"},
                ),
                threading.Thread(
                    target=import_competing_manifest,
                    kwargs={"key": "r2-race-manifest-b", "slug": "r2-race-app-b"},
                ),
            ]
            for thread in race_threads:
                thread.start()
            for thread in race_threads:
                thread.join(timeout=30)
            assert sorted(status for status, _ in race_results) == [201, 409]
            conflict = next(body for status, body in race_results if status == 409)
            assert conflict["error"]["code"] == "CATALOG_CONFLICT"
            assert conflict["error"]["details"] == {
                "reason": "MANIFEST_BOOTSTRAP_ALREADY_EXISTS"
            }

            app.state.system_versions_service_factory = lambda session: SystemVersionsService(
                session,
                clock=lambda: NOW,
                audit_service=V4AuditService(
                    session, clock=lambda: NOW, force_fail=False, fail_on_call=3
                ),
            )
            failed = client.post(
                "/api/v2/system-manifests:import",
                headers=_headers(rollback_seed, key="r2-late-rollback"),
                json=_manifest(rollback_seed),
            )
            assert failed.status_code != 201

        with Session(engine) as session:
            workspace_id = primary["workspace_id"]
            imported_app_id = created["application"]["application_id"]
            assert created["application"]["record_envelope"]["revision"] == 2
            assert all(item["record_envelope"]["revision"] == 2 for item in created["components"])
            assert session.scalar(
                sa.select(sa.func.count()).select_from(AIApplicationLifecycleRevision).where(
                    AIApplicationLifecycleRevision.workspace_id == workspace_id,
                    AIApplicationLifecycleRevision.application_id == imported_app_id,
                )
            ) == 2
            component_ids = {item["component_id"] for item in created["components"]}
            assert session.scalar(
                sa.select(sa.func.count()).select_from(SystemComponentLifecycleRevision).where(
                    SystemComponentLifecycleRevision.workspace_id == workspace_id,
                    SystemComponentLifecycleRevision.component_id.in_(component_ids),
                )
            ) == 4
            direct_component_row = session.get(
                SystemComponent, direct_component["component_id"]
            )
            assert direct_component_row is not None
            assert direct_component_row.lifecycle_state == "REGISTERED"
            assert direct_component_row.revision == 1
            assert direct_component_row.dataset_role is None
            assert direct_component_row.envelope_payload["dataset_role"] is None
            direct_history = session.get(
                SystemComponentLifecycleRevision,
                (workspace_id, direct_component_row.component_id, 1),
            )
            assert direct_history is not None
            direct_event = session.scalars(
                sa.select(Event).where(
                    Event.workspace_id == workspace_id,
                    Event.aggregate_id == direct_component_row.component_id,
                    Event.event_type == "system_component.registered",
                )
            ).one()
            assert direct_event.contract_version == "v5"
            assert direct_event.event_version == "2.0"
            assert direct_event.event_contract_major == 2
            assert direct_event.exact_subject_binding == {
                "kind": "SYSTEM_COMPONENT",
                "id": direct_component_row.component_id,
                "revision": 1,
                "digest": direct_component_row.record_digest,
            }
            assert set(direct_event.payload) == {
                "exact_previous_system_component_binding_or_null",
                "exact_system_component_binding",
                "application_id",
                "component_kind",
                "logical_name",
                "lifecycle_state",
            }
            direct_receipt = session.get(
                AuthorityReceipt, direct_event.authority_receipt_id
            )
            assert direct_receipt is not None
            assert direct_receipt.subject_revision == 1
            assert direct_receipt.receipt_payload["source_event_id"] == direct_event.event_id
            assert direct_receipt.receipt_payload["controller_registration"]["contract_major"] == 1
            direct_outbox = session.scalars(
                sa.select(Outbox).where(Outbox.source_event_id == direct_event.event_id)
            ).one()
            assert direct_outbox.contract_version == "v5"
            assert direct_outbox.event_version == "2.0"
            assert direct_outbox.event_contract_major == 2
            assert direct_outbox.payload["exact_subject_binding"] == direct_event.exact_subject_binding
            direct_controller_audit = session.scalars(
                sa.select(Audit).where(
                    Audit.workspace_id == workspace_id,
                    Audit.transaction_id == direct_event.transaction_id,
                    Audit.action == "controller.system_component.registered",
                )
            ).one()
            assert {
                direct_event.transaction_id,
                direct_receipt.transaction_id,
                direct_outbox.transaction_id,
                direct_controller_audit.transaction_id,
            } == {direct_event.transaction_id}
            raced_edges = list(
                session.scalars(
                    sa.select(DependencyEdge).where(
                        DependencyEdge.workspace_id == workspace_id,
                        DependencyEdge.application_id == imported_app_id,
                        sa.or_(
                            DependencyEdge.from_component_id == direct_component_row.component_id,
                            DependencyEdge.to_component_id == direct_component_row.component_id,
                        ),
                    )
                ).all()
            )
            assert len(raced_edges) == 1
            assert {
                raced_edges[0].from_component_id,
                raced_edges[0].to_component_id,
            } == {active_component_id, direct_component_row.component_id}
            for revision in created["component_revisions"]:
                component = next(
                    item for item in created["components"]
                    if item["component_id"] == revision["component_id"]
                )
                assert revision["exact_system_component_binding"] == {
                    "kind": "SYSTEM_COMPONENT",
                    "id": component["component_id"],
                    "revision": 2,
                    "digest": component["record_envelope"]["record_digest"],
                }
            root_audit = session.scalars(
                sa.select(Audit).where(
                    Audit.workspace_id == workspace_id,
                    Audit.action == "system-manifests.import",
                )
            ).one()
            expected_event_counts = Counter(
                {
                    "application.registered": 1,
                    "application.activated": 1,
                    "environment.registered": 1,
                    "system_component.registered": 2,
                    "system_component.activated": 2,
                    "dependency_edge.recorded": 1,
                    "component_revision.recorded": 2,
                    "topology_revision.recorded": 1,
                    "system_version_set.recorded": 1,
                    "bootstrap_attestation.recorded": 1,
                    "system_assignment.recorded": 1,
                }
            )
            manifest_events = list(
                session.scalars(
                    sa.select(Event).where(
                        Event.workspace_id == workspace_id,
                        Event.transaction_id == root_audit.transaction_id,
                    )
                ).all()
            )
            assert Counter(row.event_type for row in manifest_events) == expected_event_counts
            assert len(manifest_events) == 14
            for event in manifest_events:
                expected_revision = (
                    2
                    if event.event_type
                    in {"application.activated", "system_component.activated"}
                    else 1
                )
                assert event.contract_version == "v5"
                assert event.event_version == "2.0"
                assert event.event_contract_major == 2
                assert event.exact_subject_binding["revision"] == expected_revision
                assert event.authority_receipt_id is not None

            manifest_event_ids = {event.event_id for event in manifest_events}
            manifest_outboxes = list(
                session.scalars(
                    sa.select(Outbox).where(
                        Outbox.workspace_id == workspace_id,
                        Outbox.source_event_id.in_(manifest_event_ids),
                    )
                ).all()
            )
            assert len(manifest_outboxes) == len(manifest_events)
            events_by_id = {event.event_id: event for event in manifest_events}
            for outbox in manifest_outboxes:
                event = events_by_id[outbox.source_event_id]
                assert outbox.contract_version == "v5"
                assert outbox.event_version == "2.0"
                assert outbox.event_contract_major == 2
                assert outbox.payload["exact_subject_binding"] == event.exact_subject_binding
                assert outbox.payload["authority_receipt_id"] == event.authority_receipt_id

            manifest_receipts = list(
                session.scalars(
                    sa.select(AuthorityReceipt).where(
                        AuthorityReceipt.workspace_id == workspace_id,
                        AuthorityReceipt.event_id.in_(manifest_event_ids),
                    )
                ).all()
            )
            assert len(manifest_receipts) == len(manifest_events)
            for receipt in manifest_receipts:
                event = events_by_id[receipt.event_id]
                assert receipt.authority_receipt_id == event.authority_receipt_id
                assert receipt.subject_kind == event.exact_subject_binding["kind"]
                assert receipt.subject_id == event.exact_subject_binding["id"]
                assert receipt.subject_revision == event.exact_subject_binding["revision"]
                assert receipt.subject_digest == event.exact_subject_binding["digest"]
                assert receipt.receipt_payload["source_event_id"] == event.event_id
                assert receipt.receipt_payload["controller_registration"]["contract_major"] == 1

            controller_audits = list(
                session.scalars(
                    sa.select(Audit).where(
                        Audit.workspace_id == workspace_id,
                        Audit.transaction_id == root_audit.transaction_id,
                        Audit.action.like("controller.%"),
                    )
                ).all()
            )
            assert Counter(row.action.removeprefix("controller.") for row in controller_audits) == (
                expected_event_counts
            )
            assert {
                root_audit.transaction_id,
                *(event.transaction_id for event in manifest_events),
                *(outbox.transaction_id for outbox in manifest_outboxes),
                *(receipt.transaction_id for receipt in manifest_receipts),
                *(audit.transaction_id for audit in controller_audits),
            } == {root_audit.transaction_id}
            actions = set(
                session.scalars(
                    sa.select(Audit.action).where(Audit.workspace_id == workspace_id)
                ).all()
            )
            assert {
                "system-manifests.import",
                "controller.application.registered",
                "controller.application.activated",
                "controller.system_component.registered",
                "controller.system_component.activated",
            } <= actions
            assert session.scalar(
                sa.select(sa.func.count()).select_from(PublicCommandIdempotency).where(
                    PublicCommandIdempotency.workspace_id == workspace_id,
                    PublicCommandIdempotency.intent == "system-manifests.import",
                )
            ) == 1
            rollback_workspace = rollback_seed["workspace_id"]
            for model in (
                AIApplication,
                AIApplicationLifecycleRevision,
                SystemComponent,
                SystemComponentLifecycleRevision,
                ComponentRevision,
                SystemVersionSet,
                Event,
                Outbox,
                AuthorityReceipt,
                PublicCommandIdempotency,
            ):
                assert session.scalar(
                    sa.select(sa.func.count()).select_from(model).where(
                        model.workspace_id == rollback_workspace
                    )
                ) == 0

            race_workspace = race_seed["workspace_id"]
            expected_race_counts = {
                AIApplication: 1,
                AIApplicationLifecycleRevision: 2,
                Environment: 1,
                SystemComponent: 2,
                SystemComponentLifecycleRevision: 4,
                DependencyEdge: 1,
                ComponentRevision: 2,
                TopologyRevision: 1,
                SystemVersionSet: 1,
                BootstrapAttestation: 1,
                SystemAssignment: 1,
                Event: 14,
                Outbox: 14,
                AuthorityReceipt: 14,
                PublicCommandIdempotency: 1,
            }
            for model, expected_count in expected_race_counts.items():
                assert session.scalar(
                    sa.select(sa.func.count()).select_from(model).where(
                        model.workspace_id == race_workspace
                    )
                ) == expected_count
    finally:
        _reset_pg_database_for_migrations(engine, TEST_DATABASE_URL)
        engine.dispose()
