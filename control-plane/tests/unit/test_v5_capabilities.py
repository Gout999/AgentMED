"""Focused V5 capability-discovery service tests."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Audit
from app.public_api.v5_capability_models import V5ServerCapabilitiesResponse
from app.services.v5_capabilities import (
    IMPLEMENTED_V5_PUBLIC_INTENTS,
    V5CapabilitiesService,
)

from test_v5_application_catalog import NOW, _principal_context


def test_v5_capabilities_advertise_only_implemented_intents_in_principal_scope(
    sqlite_session,
) -> None:
    scopes = ["capabilities:read", "applications:read", "system_versions:read"]
    principal = _principal_context(
        scopes=scopes,
        required_scope="capabilities:read",
    )

    response = V5CapabilitiesService(
        sqlite_session, clock=lambda: NOW
    ).get_capabilities(
        principal=principal,
        request_id="req_01J00000000000CP",
        server_version="0.1.0+v5-1c",
    )

    parsed = V5ServerCapabilitiesResponse.model_validate(
        response.model_dump(mode="json")
    )
    assert parsed.data.api_major == 2
    assert parsed.data.contract_version == "2.0"
    assert parsed.data.disabled_intents == []
    assert [item.name for item in parsed.data.enabled_intents] == [
        "capabilities.get",
        "applications.get",
        "environments.get",
        "system-components.get",
        "dependency-edges.get",
        "system-versions.get",
        "system-versions.diff",
    ]
    assert all(item.http is True and item.cli is True for item in parsed.data.enabled_intents)
    assert "system-versions.record" not in {
        item.name for item in parsed.data.enabled_intents
    }

    audit = sqlite_session.scalar(
        select(Audit).where(Audit.action == "public.v5.capabilities.get")
    )
    assert audit is not None
    assert response.audit_ref == f"audit://{audit.audit_id}"


def test_v5_capability_allowlist_is_exact_and_unique() -> None:
    names = [str(item["name"]) for item in IMPLEMENTED_V5_PUBLIC_INTENTS]
    assert len(names) == 17
    assert len(names) == len(set(names))
    assert "capabilities.get" in names
    assert "system-versions.record" not in names
    assert not any(name.startswith("releases.") for name in names)
    assert all(
        set(item["principal_types"])
        <= {"human", "external_agent", "service", "connector"}
        for item in IMPLEMENTED_V5_PUBLIC_INTENTS
    )


@pytest.mark.parametrize(
    ("principal_type", "expected"),
    [
        ("human", {str(item["name"]) for item in IMPLEMENTED_V5_PUBLIC_INTENTS}),
        (
            "service",
            {
                str(item["name"])
                for item in IMPLEMENTED_V5_PUBLIC_INTENTS
                if item["name"] != "acceptance-criteria.confirm"
            },
        ),
        (
            "external_agent",
            {
                "capabilities.get",
                "applications.get",
                "environments.get",
                "system-components.get",
                "dependency-edges.get",
                "system-versions.get",
                "system-versions.diff",
                "case-application-bindings.get",
                "acceptance-criteria.propose",
                "acceptance-criteria.get",
            },
        ),
        (
            "connector",
            {
                "capabilities.get",
                "applications.get",
                "environments.get",
                "system-components.get",
                "dependency-edges.get",
                "system-versions.get",
                "system-versions.diff",
                "case-application-bindings.get",
                "acceptance-criteria.propose",
                "acceptance-criteria.get",
            },
        ),
    ],
)
def test_v5_capabilities_filter_scope_and_principal_type(
    sqlite_session, principal_type: str, expected: set[str]
) -> None:
    scopes = sorted({str(item["scope"]) for item in IMPLEMENTED_V5_PUBLIC_INTENTS})
    base = _principal_context(scopes=scopes, required_scope="capabilities:read")
    payload = base.model_dump(mode="python")
    payload["principal_type"] = principal_type
    principal = type(base).model_validate(payload)

    response = V5CapabilitiesService(
        sqlite_session, clock=lambda: NOW
    ).get_capabilities(
        principal=principal,
        request_id="req_01J0000000000CPT",
        server_version="0.1.0+v5-1c",
    )

    assert {item.name for item in response.data.enabled_intents} == expected
