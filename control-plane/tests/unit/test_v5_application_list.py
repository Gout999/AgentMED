"""Focused authenticated applications.list authority and cursor tests."""
from __future__ import annotations

import pytest
from sqlalchemy import select, update

from app.models import Audit
from app.models.v5_tables import AIApplication, DependencyEdge
from app.services.v4_audit import V4AuditService
from app.services.v5_application_list import (
    V5ApplicationListError,
    V5ApplicationListService,
)

from test_v5_application_catalog import (
    NOW,
    PROJECT,
    SCOPES,
    _app_request,
    _principal_context,
    _seed_env,
    _service,
)
from test_v5_system_versions import (
    _import as _import_manifest,
    _manifest,
    _seed_env as _seed_manifest_env,
)


CURSOR_KEY = "r2-applications-list-cursor-key"


def _read_principal():
    return _principal_context(
        scopes=SCOPES,
        required_scope="applications:read",
    )


def _list_service(sqlite_session, **kwargs) -> V5ApplicationListService:
    return V5ApplicationListService(
        sqlite_session,
        cursor_signing_key=CURSOR_KEY,
        clock=lambda: NOW,
        **kwargs,
    )


def test_applications_list_revalidates_authority_and_pages_with_opaque_cursor(
    sqlite_session,
) -> None:
    _seed_env(sqlite_session)
    catalog = _service(sqlite_session)
    first = catalog.register_application(
        _app_request(slug="r2-list-a"),
        principal=_principal_context(),
        idempotency_key="r2-list-app-0001",
        request_id="req_01J0000000000LA1",
    )
    second = catalog.register_application(
        _app_request(slug="r2-list-b"),
        principal=_principal_context(),
        idempotency_key="r2-list-app-0002",
        request_id="req_01J0000000000LA2",
    )
    sqlite_session.commit()

    first_page = _list_service(sqlite_session).list_applications(
        principal=_read_principal(),
        request_id="req_01J0000000000LP1",
        project_id=PROJECT,
        limit=1,
        cursor=None,
    )
    assert len(first_page.items) == 1
    assert first_page.next_cursor is not None
    assert first_page.next_cursor.startswith("cur_")
    assert all(
        item.application.application_id in {
            first.application.application_id,
            second.application.application_id,
        }
        for item in first_page.items
    )

    with pytest.raises(V5ApplicationListError) as scope_error:
        _list_service(sqlite_session).list_applications(
            principal=_read_principal(),
            request_id="req_01J0000000000LPS",
            project_id=PROJECT,
            limit=2,
            cursor=first_page.next_cursor,
        )
    assert scope_error.value.code == "REQUEST_INVALID"

    second_page = _list_service(sqlite_session).list_applications(
        principal=_read_principal(),
        request_id="req_01J0000000000LP2",
        project_id=PROJECT,
        limit=1,
        cursor=first_page.next_cursor,
    )
    assert len(second_page.items) == 1
    assert second_page.next_cursor is None
    assert (
        second_page.items[0].application.application_id
        != first_page.items[0].application.application_id
    )


def test_applications_list_rejects_tampered_cursor_and_projection(
    sqlite_session,
) -> None:
    _seed_env(sqlite_session)
    registered = _service(sqlite_session).register_application(
        _app_request(slug="r2-list-tamper"),
        principal=_principal_context(),
        idempotency_key="r2-list-app-tamper",
        request_id="req_01J0000000000LAT",
    )
    sqlite_session.commit()
    service = _list_service(sqlite_session)

    with pytest.raises(V5ApplicationListError) as cursor_error:
        service.list_applications(
            principal=_read_principal(),
            request_id="req_01J0000000000LTC",
            project_id=PROJECT,
            limit=1,
            cursor="cur_not-a-valid-signed-cursor",
        )
    assert cursor_error.value.code == "REQUEST_INVALID"
    assert cursor_error.value.rollback_required is False
    assert cursor_error.value.details == {}
    assert cursor_error.value.audit_ref is not None
    denial_audit = sqlite_session.scalar(
        select(Audit).where(Audit.audit_id == cursor_error.value.audit_ref.removeprefix("audit://"))
    )
    assert denial_audit is not None
    assert denial_audit.result == "denied"
    assert denial_audit.error_code == "REQUEST_INVALID"

    row = sqlite_session.get(AIApplication, registered.application.application_id)
    assert row is not None
    tampered = dict(row.envelope_payload)
    tampered["display_name"] = "tampered"
    row.envelope_payload = tampered
    sqlite_session.flush()
    with pytest.raises(V5ApplicationListError) as integrity_error:
        service.list_applications(
            principal=_read_principal(),
            request_id="req_01J0000000000LTI",
            project_id=PROJECT,
            limit=50,
            cursor=None,
        )
    assert integrity_error.value.code == "INTERNAL_ERROR"


def test_applications_list_returns_revalidated_manifest_bootstrap_graph(
    sqlite_session,
) -> None:
    _seed_manifest_env(sqlite_session)
    imported = _import_manifest(sqlite_session, _manifest())
    sqlite_session.commit()

    response = _list_service(sqlite_session).list_applications(
        principal=_read_principal(),
        request_id="req_01J0000000000LBG",
        project_id=PROJECT,
        limit=50,
        cursor=None,
    )

    assert len(response.items) == 1
    item = response.items[0]
    assert item.application.application_id == imported.application.application_id
    assert item.application.record_envelope.revision == 2
    assert item.application.lifecycle_state == "ACTIVE"
    assert len(item.environments) == 1
    assert len(item.system_components) == 3
    assert all(component.record_envelope.revision == 2 for component in item.system_components)
    assert len(item.dependency_edges) == 1
    assert item.dependency_edges[0].record_envelope.revision == 1

    edge = sqlite_session.get(
        DependencyEdge,
        item.dependency_edges[0].edge_id,
    )
    assert edge is not None
    tampered_edge = dict(edge.envelope_payload)
    tampered_edge["from_component_id"] = item.system_components[1].component_id
    sqlite_session.execute(
        update(DependencyEdge)
        .where(DependencyEdge.edge_id == edge.edge_id)
        .values(envelope_payload=tampered_edge)
    )
    sqlite_session.expire_all()
    with pytest.raises(V5ApplicationListError) as relation_error:
        _list_service(sqlite_session).list_applications(
            principal=_read_principal(),
            request_id="req_01J0000000000LBR",
            project_id=PROJECT,
            limit=50,
            cursor=None,
        )
    assert relation_error.value.code == "INTERNAL_ERROR"


def test_applications_list_audit_failure_fails_closed(sqlite_session) -> None:
    _seed_env(sqlite_session)
    service = _list_service(
        sqlite_session,
        audit_service=V4AuditService(
            sqlite_session,
            clock=lambda: NOW,
            force_fail=True,
        ),
    )

    with pytest.raises(V5ApplicationListError) as exc_info:
        service.list_applications(
            principal=_read_principal(),
            request_id="req_01J0000000000LAF",
            project_id=PROJECT,
            limit=50,
            cursor=None,
        )

    assert exc_info.value.code == "AUDIT_UNAVAILABLE"
    assert exc_info.value.rollback_required is True
