"""Focused R2 public capability allowlist tests."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Audit
from app.models.v4_tables import PublicPrincipal
from app.public_api.v5_capability_models import V5ServerCapabilitiesResponse
from app.services.v4_audit import V4AuditService
from app.services.v5_capabilities import (
    IMPLEMENTED_V5_PUBLIC_INTENTS,
    V5CapabilitiesError,
    V5CapabilitiesService,
)

from test_v5_application_catalog import (
    CATALOG_PRINCIPAL,
    NOW,
    _principal_context,
    _seed_principal,
)


EXPECTED_R2_INTENTS = {
    "capabilities.get",
    "applications.register",
    "applications.get",
    "applications.list",
    "environments.register",
    "environments.get",
    "system-components.register",
    "system-components.get",
    "dependency-edges.record",
    "dependency-edges.get",
    "system-manifests.import",
}

EXPECTED_ACTIVATED_INTENTS = {
    "capabilities.get",
    "applications.register",
    "applications.get",
    "applications.list",
    "environments.register",
    "environments.get",
    "system-components.register",
    "system-components.get",
    "dependency-edges.record",
    "dependency-edges.get",
    "system-manifests.import",
    "system-versions.record",
    "system-versions.get",
    "system-versions.diff",
    "cases.bind-application",
    "case-application-bindings.get",
    "acceptance-criteria.propose",
    "acceptance-criteria.get",
    "acceptance-criteria.confirm",
    "investigations.start",
    "operations.get",
    "operations.list",
    "operations.cancel-request",
}


def _seed_capability_principal(
    sqlite_session,
    *,
    scopes: list[str],
    trust_roles: list[str],
) -> None:
    _seed_principal(
        sqlite_session,
        principal_id=CATALOG_PRINCIPAL,
        scopes=scopes,
        trust_roles=trust_roles,
    )
    sqlite_session.flush()


def test_v5_capabilities_advertise_only_r2_intents_in_principal_scope(
    sqlite_session,
) -> None:
    scopes = [
        "capabilities:read",
        "applications:manage",
        "applications:read",
        "system_manifests:import",
    ]
    principal = _principal_context(
        scopes=scopes,
        required_scope="capabilities:read",
    )
    _seed_capability_principal(
        sqlite_session,
        scopes=scopes,
        trust_roles=["integrator", "domain_reviewer"],
    )

    response = V5CapabilitiesService(
        sqlite_session, clock=lambda: NOW
    ).get_capabilities(
        principal=principal,
        request_id="req_01J00000000000CP",
        server_version="0.1.0+v5-r2",
    )

    parsed = V5ServerCapabilitiesResponse.model_validate(
        response.model_dump(mode="json")
    )
    assert parsed.data.api_major == 2
    assert parsed.data.contract_version == "2.0"
    assert parsed.data.disabled_intents == []
    assert {item.name for item in parsed.data.enabled_intents} == EXPECTED_R2_INTENTS
    assert all(item.http is True and item.cli is True for item in parsed.data.enabled_intents)
    modes = {item.name: item.execution_mode for item in parsed.data.enabled_intents}
    assert modes["system-manifests.import"] == "synchronous_local_transaction"
    assert {
        mode for name, mode in modes.items() if name != "system-manifests.import"
    } == {"synchronous"}
    forbidden = {
        "applications.activate",
        "system-components.activate",
        "system-versions.record",
        "cases.bind-application",
        "acceptance-criteria.confirm",
    }
    assert forbidden.isdisjoint(
        {item.name for item in parsed.data.enabled_intents}
    )

    audit = sqlite_session.scalar(
        select(Audit).where(Audit.action == "public.v5.capabilities.get")
    )
    assert audit is not None
    assert response.audit_ref == f"audit://{audit.audit_id}"


def test_v5_capability_allowlist_is_exact_and_unique() -> None:
    names = [str(item["name"]) for item in IMPLEMENTED_V5_PUBLIC_INTENTS]
    assert len(names) == 23
    assert set(names) == EXPECTED_ACTIVATED_INTENTS
    assert len(names) == len(set(names))
    assert all(
        set(item["principal_types"])
        <= {"human", "external_agent", "service", "connector"}
        for item in IMPLEMENTED_V5_PUBLIC_INTENTS
    )


def test_v5_capabilities_fail_closed_when_audit_is_unavailable(
    sqlite_session,
) -> None:
    scopes = sorted({str(item["scope"]) for item in IMPLEMENTED_V5_PUBLIC_INTENTS})
    principal = _principal_context(
        scopes=scopes,
        required_scope="capabilities:read",
    )
    _seed_capability_principal(
        sqlite_session,
        scopes=scopes,
        trust_roles=["integrator", "domain_reviewer"],
    )
    service = V5CapabilitiesService(
        sqlite_session,
        clock=lambda: NOW,
        audit_service=V4AuditService(
            sqlite_session,
            clock=lambda: NOW,
            force_fail=True,
        ),
    )

    with pytest.raises(V5CapabilitiesError) as exc_info:
        service.get_capabilities(
            principal=principal,
            request_id="req_01J0000000000AUD",
            server_version="0.1.0+v5-r2",
        )

    assert exc_info.value.code == "AUDIT_UNAVAILABLE"
    assert exc_info.value.rollback_required is True
    assert sqlite_session.scalars(select(Audit)).all() == []


@pytest.mark.parametrize(
    ("principal_type", "expected"),
    [
        ("human", EXPECTED_ACTIVATED_INTENTS),
        ("service", EXPECTED_ACTIVATED_INTENTS - {"acceptance-criteria.confirm"}),
        (
            "external_agent",
            {
                "capabilities.get",
                "applications.get",
                "applications.list",
                "environments.get",
                "system-components.get",
                "dependency-edges.get",
                "system-versions.get",
                "system-versions.diff",
                "case-application-bindings.get",
                "acceptance-criteria.propose",
                "acceptance-criteria.get",
                "investigations.start",
                "operations.get",
                "operations.list",
                "operations.cancel-request",
            },
        ),
        (
            "connector",
            {
                "capabilities.get",
                "applications.get",
                "applications.list",
                "environments.get",
                "system-components.get",
                "dependency-edges.get",
                "system-versions.get",
                "system-versions.diff",
                "case-application-bindings.get",
                "acceptance-criteria.propose",
                "acceptance-criteria.get",
                "operations.get",
                "operations.list",
            },
        ),
    ],
)
def test_v5_capabilities_filter_scope_and_principal_type(
    sqlite_session,
    principal_type: str,
    expected: set[str],
) -> None:
    scopes = sorted({str(item["scope"]) for item in IMPLEMENTED_V5_PUBLIC_INTENTS})
    base = _principal_context(scopes=scopes, required_scope="capabilities:read")
    payload = base.model_dump(mode="python")
    payload["principal_type"] = principal_type
    principal = type(base).model_validate(payload)
    _seed_principal(
        sqlite_session,
        principal_id=CATALOG_PRINCIPAL,
        scopes=scopes,
        trust_roles=["integrator", "domain_reviewer"],
    )
    sqlite_session.flush()
    persisted = sqlite_session.get(PublicPrincipal, CATALOG_PRINCIPAL)
    assert persisted is not None
    persisted.principal_type = principal_type
    sqlite_session.flush()

    response = V5CapabilitiesService(
        sqlite_session, clock=lambda: NOW
    ).get_capabilities(
        principal=principal,
        request_id="req_01J0000000000CPT",
        server_version="0.1.0+v5-r2",
    )

    assert {item.name for item in response.data.enabled_intents} == expected


def test_v5_capabilities_hide_catalog_mutations_and_import_from_roleless_human(
    sqlite_session,
) -> None:
    scopes = sorted({str(item["scope"]) for item in IMPLEMENTED_V5_PUBLIC_INTENTS})
    principal = _principal_context(scopes=scopes, required_scope="capabilities:read")
    _seed_capability_principal(sqlite_session, scopes=scopes, trust_roles=[])

    response = V5CapabilitiesService(
        sqlite_session, clock=lambda: NOW
    ).get_capabilities(
        principal=principal,
        request_id="req_01J000000000ROLE",
        server_version="0.1.0+v5-r2",
    )

    assert {item.name for item in response.data.enabled_intents} == {
        "capabilities.get",
        "applications.get",
        "applications.list",
        "environments.get",
        "system-components.get",
        "dependency-edges.get",
        "system-versions.get",
        "system-versions.diff",
        "case-application-bindings.get",
        "acceptance-criteria.get",
        "operations.get",
        "operations.list",
    }


def test_v5_capabilities_trusted_builder_sees_import_but_not_catalog_mutations(
    sqlite_session,
) -> None:
    scopes = sorted({str(item["scope"]) for item in IMPLEMENTED_V5_PUBLIC_INTENTS})
    principal = _principal_context(scopes=scopes, required_scope="capabilities:read")
    _seed_capability_principal(
        sqlite_session,
        scopes=scopes,
        trust_roles=["trusted_builder"],
    )

    response = V5CapabilitiesService(
        sqlite_session, clock=lambda: NOW
    ).get_capabilities(
        principal=principal,
        request_id="req_01J000000000BLD",
        server_version="0.1.0+v5-r2",
    )

    names = {item.name for item in response.data.enabled_intents}
    assert "system-manifests.import" in names
    assert {
        "applications.register",
        "environments.register",
        "system-components.register",
        "dependency-edges.record",
    }.isdisjoint(names)
