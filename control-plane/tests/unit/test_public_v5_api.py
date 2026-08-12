"""V5-1A /api/v2 public route tests (TestClient + real credential resolver),
plus the C4 route↔manifest registry gate (real-manifest match and tamper
fail-closed cases).
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import select

from app.api import public_v5
from app.api import v5_route_registry
from app.api.v5_route_registry import (
    RouteManifestMismatchError,
    check_registered_v5_routes,
    install_route_manifest_check,
)
from app.main import create_app
from app.models import Audit
from app.models.v4_tables import PublicCredential
from app.public_api.credential_resolver import hash_opaque_bearer
from app.public_api.v5_models import (
    ApplicationRegisterResponse,
    ComponentRegisterResponse,
    ExactBootstrapAttestationAuthorityBinding,
    ExactComponentRevisionBinding,
    ExactDependencyEdgeBinding,
    ExactV4EvidenceBinding,
    ExactV5EvidenceBinding,
    ExactSlotVersionSetBinding,
    ExactTopologyRevisionBinding,
    IdentityAssuranceSummary,
    SystemAssignmentRecord,
)
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from app.services.v5_capabilities import (
    V5CapabilitiesManifestError,
    V5ManifestHttpRoute,
    load_v5_operation_manifest,
)

from test_v5_application_catalog import (
    _FIXTURE,
    _activate_registered_application_for_foundation_test,
    _claims,
    _seed_principal,
    _seed_v5_controller,
    AUDIENCES,
    CATALOG_PRINCIPAL,
    ISSUER,
    OTHER_PROJECT,
    OWNER,
    PROJECT,
    SUBJECT,
    WORKSPACE,
)

RAW_TOKEN = "route-test-catalog-token-0123456789-abcdef"
FOREIGN_READER_TOKEN = "route-test-foreign-reader-token-0123456789"
PEPPER = "route-test-catalog-pepper"
CURSOR_KEY = "route-test-catalog-cursor"
ROUTE_SCOPES = [
    "applications:manage",
    "applications:read",
    "capabilities:read",
    "system_manifests:import",
]


@pytest.fixture()
def client(sqlite_engine):
    from datetime import datetime

    from app.config import Settings
    from app.services.application_catalog import ApplicationCatalogService

    settings = Settings(
        database_url="sqlite://",
        public_credential_hash_pepper=SecretStr(PEPPER),
        public_cursor_signing_key=SecretStr(CURSOR_KEY),
        public_auth_issuer=ISSUER,
        require_mcp_role_tokens=False,
    )
    _seed_route_identity(sqlite_engine, settings)
    app = create_app(settings=settings, engine=sqlite_engine, create_tables=True)
    fixed_now = datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc)
    app.state.application_catalog_service_factory = (
        lambda session: ApplicationCatalogService(session, clock=lambda: fixed_now)
    )
    context = TestClient(app)
    client = context.__enter__()
    try:
        yield client
    finally:
        context.__exit__(None, None, None)


def _seed_route_identity(engine, settings) -> None:
    from sqlalchemy.orm import Session

    session = Session(engine)
    try:
        _seed_principal(session, principal_id=OWNER, scopes=["signals:write", "cases:read"])
        _seed_principal(
            session,
            principal_id=CATALOG_PRINCIPAL,
            scopes=ROUTE_SCOPES,
            trust_roles=["integrator"],
        )
        _seed_v5_controller(session)
        session.add(
            PublicCredential(
                credential_id="cred_01J000000000000A",
                workspace_id=WORKSPACE,
                principal_id=CATALOG_PRINCIPAL,
                issuer=ISSUER,
                subject=SUBJECT,
                credential_hash=hash_opaque_bearer(RAW_TOKEN, PEPPER),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "b" * 64,
                claims_digest=_claims(WORKSPACE, [PROJECT], ROUTE_SCOPES),
                audiences=list(AUDIENCES),
                project_ids=[PROJECT],
                environment_ids=[],
                scopes=ROUTE_SCOPES,
                state="ACTIVE",
                issued_at=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                not_before=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                expires_at=datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc),
                revoked_at=None,
            )
        )
        # A same-workspace reader granted only OTHER_PROJECT (cross-project
        # visibility probe for applications.get).
        foreign_reader_id = "prn_01J00000000000F2"
        _seed_principal(
            session,
            principal_id=foreign_reader_id,
            scopes=["applications:read"],
            project_ids=[OTHER_PROJECT],
        )
        session.add(
            PublicCredential(
                credential_id="cred_01J000000000000B",
                workspace_id=WORKSPACE,
                principal_id=foreign_reader_id,
                issuer=ISSUER,
                subject=SUBJECT,
                credential_hash=hash_opaque_bearer(FOREIGN_READER_TOKEN, PEPPER),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "c" * 64,
                claims_digest=_claims(WORKSPACE, [OTHER_PROJECT], ["applications:read"]),
                audiences=list(AUDIENCES),
                project_ids=[OTHER_PROJECT],
                environment_ids=[],
                scopes=["applications:read"],
                state="ACTIVE",
                issued_at=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                not_before=datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc),
                expires_at=datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc),
                revoked_at=None,
            )
        )
        session.commit()
    finally:
        session.close()


def _headers(*, mutation: bool = False, contract_version: str = "2.0") -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {RAW_TOKEN}",
        "X-CaseLoop-Workspace-ID": WORKSPACE,
        "X-CaseLoop-Contract-Version": contract_version,
        "X-Request-ID": "req_01J000000000000A",
        "Content-Type": "application/json",
    }
    if mutation:
        headers["X-CaseLoop-Idempotency-Key"] = "app-register-0001"
    return headers


def _app_body() -> bytes:
    return json.dumps(_FIXTURE["applications"][0]["register_request"]).encode("utf-8")


def _record_envelope() -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "workspace_id": WORKSPACE,
        "revision": 1,
        "recorded_by_principal": CATALOG_PRINCIPAL,
        "recorded_at": "2026-08-11T10:00:00Z",
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
        "record_digest": "sha256:" + "a" * 64,
        "authority_receipt_id": "arec_01J0000000000001",
    }


def test_bootstrap_nested_authority_models_are_closed_and_exact() -> None:
    digest = "sha256:" + "b" * 64
    valid_bindings = [
        (
            ExactDependencyEdgeBinding,
            {"kind": "DEPENDENCY_EDGE", "id": "de_01J0000000000001", "revision": 1, "digest": digest},
        ),
        (
            ExactComponentRevisionBinding,
            {"kind": "COMPONENT_REVISION", "id": "crv_01J0000000000001", "revision": 1, "digest": digest},
        ),
        (
            ExactTopologyRevisionBinding,
            {"kind": "TOPOLOGY_REVISION", "id": "tpr_01J0000000000001", "revision": 1, "digest": digest},
        ),
        (
            ExactSlotVersionSetBinding,
            {"slot": "PRIMARY", "kind": "SYSTEM_VERSION_SET", "id": "vset_01J0000000000001", "revision": 1, "digest": digest},
        ),
        (
            ExactBootstrapAttestationAuthorityBinding,
            {"binding_kind": "BOOTSTRAP_ATTESTATION", "id": "batt_01J0000000000001", "revision": 1, "digest": digest},
        ),
    ]
    for model, payload in valid_bindings:
        model.model_validate(payload)
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "unexpected": True})
        with pytest.raises(ValidationError):
            model.model_validate({**payload, "revision": None})

    v4_evidence = {
        "contract_major": 1,
        "kind": "TRACE_EVIDENCE_RECEIPT",
        "id": "ter_01J0000000000001",
        "revision": None,
        "digest": digest,
    }
    v5_evidence = {
        "kind": "OBSERVED_STATE_SNAPSHOT",
        "id": "oss_01J0000000000001",
        "revision": 1,
        "digest": digest,
    }
    ExactV4EvidenceBinding.model_validate(v4_evidence)
    ExactV5EvidenceBinding.model_validate(v5_evidence)
    for model, payload in (
        (ExactV4EvidenceBinding, {**v4_evidence, "contract_major": 2}),
        (ExactV4EvidenceBinding, {k: v for k, v in v4_evidence.items() if k != "contract_major"}),
        (ExactV5EvidenceBinding, {**v5_evidence, "contract_major": 1}),
        (ExactV5EvidenceBinding, {**v5_evidence, "revision": None}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    with pytest.raises(ValidationError):
        ExactSlotVersionSetBinding.model_validate(
            {**valid_bindings[3][1], "slot": "CANARY"}
        )

    summary = {
        "component_assurances": [
            {
                "component_revision_id": "crv_01J0000000000001",
                "component_id": "cmp_01J0000000000001",
                "identity_assurance": "IMMUTABLE_DIGEST",
            }
        ]
    }
    IdentityAssuranceSummary.model_validate(summary)
    with pytest.raises(ValidationError):
        IdentityAssuranceSummary.model_validate({"component_count": 1})

    assignment = {
        "record_envelope": _record_envelope(),
        "assignment_id": "asg_01J0000000000001",
        "workspace_id": WORKSPACE,
        "application_id": "app_01J0000000000001",
        "environment_id": "env_01J0000000000001",
        "generation": 1,
        "lifecycle_state": "ACTIVE",
        "transition_kind": "BOOTSTRAP",
        "exact_previous_assignment_binding_or_null": None,
        "exact_slot_version_set_bindings": [valid_bindings[3][1]],
        "exposure": "EXPOSED",
        "expected_previous_generation": None,
        "exact_assignment_authority_binding": valid_bindings[4][1],
        "requested_by_external_operation_id": None,
    }
    SystemAssignmentRecord.model_validate(assignment)
    for mutation in (
        {"exact_previous_assignment_binding_or_null": {}},
        {"generation": 2},
        {"transition_kind": "ROLL_FORWARD"},
    ):
        with pytest.raises(ValidationError):
            SystemAssignmentRecord.model_validate({**assignment, **mutation})


def test_v5_capability_discovery_is_exactly_r2_scoped(client) -> None:
    response = client.get("/api/v2/capabilities", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "2.0"
    assert body["data"]["api_major"] == 2
    assert body["data"]["contract_version"] == "2.0"
    assert body["data"]["disabled_intents"] == []
    assert {item["name"] for item in body["data"]["enabled_intents"]} == {
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
    modes = {
        item["name"]: item["execution_mode"]
        for item in body["data"]["enabled_intents"]
    }
    assert modes["system-manifests.import"] == "synchronous_local_transaction"
    assert {
        mode for name, mode in modes.items() if name != "system-manifests.import"
    } == {"synchronous"}


def test_register_application_v2_success(client) -> None:
    response = client.post(
        "/api/v2/applications",
        headers=_headers(mutation=True),
        content=_app_body(),
    )
    assert response.status_code == 201
    assert response.headers["x-caseloop-contract-version"] == "2.0"
    body = response.json()
    assert body["schema_version"] == "2.0"
    assert body["workspace_id"] == WORKSPACE
    assert body["application"]["record_envelope"]["immutable"] is True
    assert body["application"]["lifecycle_state"] in {"REGISTERED", "ACTIVE"}
    registered_shape = json.loads(response.content)
    registered_shape["application"]["lifecycle_state"] = "REGISTERED"
    ApplicationRegisterResponse.model_validate(registered_shape)
    assert body["idempotency"]["replayed"] is False
    assert body["idempotency"]["receipt"]["intent"] == "applications.register"


def test_list_applications_v2_is_authenticated_project_scoped_graph(client) -> None:
    registered = client.post(
        "/api/v2/applications",
        headers=_headers(mutation=True),
        content=_app_body(),
    )
    assert registered.status_code == 201

    response = client.get(
        f"/api/v2/applications?project_id={PROJECT}&limit=25",
        headers=_headers(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == "2.0"
    assert body["next_cursor"] is None
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["application"]["application_id"] == registered.json()["application"][
        "application_id"
    ]
    assert item["application"]["exact_previous_application_binding_or_null"] is None
    assert item["environments"] == []
    assert item["system_components"] == []
    assert item["dependency_edges"] == []

    foreign_headers = _headers()
    foreign_headers["Authorization"] = f"Bearer {FOREIGN_READER_TOKEN}"
    foreign = client.get(
        f"/api/v2/applications?project_id={OTHER_PROJECT}",
        headers=foreign_headers,
    )
    assert foreign.status_code == 200
    assert foreign.json()["items"] == []


def test_r3_registers_system_version_read_and_diff_routes(client) -> None:
    openapi_paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v2/system-versions" in openapi_paths
    assert "/api/v2/system-versions/{system_version_set_id}" in openapi_paths
    assert "/api/v2/system-versions:diff" in openapi_paths
    # registered routes require authentication before any lookup
    version = client.get("/api/v2/system-versions/vset_01J0000000000001")
    assert version.status_code == 401
    diff = client.get(
        "/api/v2/system-versions:diff",
        params={
            "source_version_set_id": "vset_01J0000000000001",
            "target_version_set_id": "vset_01J0000000000002",
        },
    )
    assert diff.status_code == 401


def test_r4_registers_case_and_acceptance_routes(client) -> None:
    openapi_paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v2/cases/{case_id}:bind-application" in openapi_paths
    assert "/api/v2/cases/{case_id}/application-binding" in openapi_paths
    assert "/api/v2/cases/{case_id}:propose-acceptance-criteria" in openapi_paths
    assert "/api/v2/cases/{case_id}/acceptance-criteria" in openapi_paths
    assert "/api/v2/acceptance-criteria/{acceptance_criteria_revision_id}:confirm" in openapi_paths
    # unauthenticated probes are rejected before any lookup
    assert (
        client.get(
            "/api/v2/cases/case_01J0000000000001/application-binding",
            params={"case_revision": 1, "case_digest": "sha256:" + "0" * 64},
        ).status_code
        == 401
    )


def test_r2_openapi_exactly_matches_activated_intent_operations(client) -> None:
    expected = {
        ("GET", "/api/v2/capabilities"): "getV5Capabilities",
        ("POST", "/api/v2/applications"): "registerApplication",
        ("GET", "/api/v2/applications"): "listApplications",
        ("GET", "/api/v2/applications/{application_id}"): "getApplication",
        ("POST", "/api/v2/environments"): "registerEnvironment",
        ("GET", "/api/v2/environments/{environment_id}"): "getEnvironment",
        ("POST", "/api/v2/system-components"): "registerSystemComponent",
        ("GET", "/api/v2/system-components/{component_id}"): "getSystemComponent",
        ("POST", "/api/v2/dependency-edges"): "recordDependencyEdge",
        (
            "GET",
            "/api/v2/dependency-edges/{dependency_edge_id}",
        ): "getDependencyEdge",
        ("POST", "/api/v2/system-manifests:import"): "importSystemManifest",
        ("POST", "/api/v2/system-versions"): "recordSystemVersion",
        (
            "GET",
            "/api/v2/system-versions/{system_version_set_id}",
        ): "getSystemVersion",
        ("GET", "/api/v2/system-versions:diff"): "diffSystemVersions",
        (
            "POST",
            "/api/v2/cases/{case_id}:bind-application",
        ): "bindCaseApplication",
        (
            "GET",
            "/api/v2/cases/{case_id}/application-binding",
        ): "getCaseApplicationBinding",
        (
            "POST",
            "/api/v2/cases/{case_id}:propose-acceptance-criteria",
        ): "proposeAcceptanceCriteria",
        (
            "GET",
            "/api/v2/cases/{case_id}/acceptance-criteria",
        ): "getAcceptanceCriteria",
        (
            "POST",
            "/api/v2/acceptance-criteria/{acceptance_criteria_revision_id}:confirm",
        ): "confirmAcceptanceCriteria",
    }
    openapi_paths = client.get("/openapi.json").json()["paths"]
    actual = {
        (method.upper(), path): operation["operationId"]
        for path, path_item in openapi_paths.items()
        if path.startswith("/api/v2/")
        for method, operation in path_item.items()
        if method.lower() in {"get", "post", "put", "patch", "delete"}
    }

    assert actual == expected


def test_r2_version_header_denials_are_committed_for_all_r2_routes(client) -> None:
    read_paths = [
        "/api/v2/capabilities",
        f"/api/v2/applications?project_id={PROJECT}",
        "/api/v2/applications/app_01J0000000000001",
        "/api/v2/environments/env_01J0000000000001",
        "/api/v2/system-components/cmp_01J0000000000001",
        "/api/v2/dependency-edges/de_01J0000000000001",
    ]
    mutation_paths = [
        "/api/v2/applications",
        "/api/v2/environments",
        "/api/v2/system-components",
        "/api/v2/dependency-edges",
        "/api/v2/system-manifests:import",
    ]
    persisted_audit_ids: list[str] = []

    for index, path in enumerate(read_paths):
        headers = _headers()
        headers.pop("X-CaseLoop-Contract-Version")
        headers["X-Request-ID"] = f"req_01J0000000000H{index:02d}"
        response = client.get(path, headers=headers)
        body = response.json()
        assert response.status_code == 400
        assert body["error"]["code"] == "REQUEST_INVALID"
        audit_ref = body["error"]["audit_ref"]
        assert audit_ref.startswith("audit://aud_")
        persisted_audit_ids.append(audit_ref.removeprefix("audit://"))

    for index, path in enumerate(mutation_paths):
        headers = _headers(mutation=True)
        headers.pop("X-CaseLoop-Contract-Version")
        headers["X-Request-ID"] = f"req_01J0000000000M{index:02d}"
        response = client.post(path, headers=headers, content=b"{}")
        body = response.json()
        assert response.status_code == 400
        assert body["error"]["code"] == "REQUEST_INVALID"
        audit_ref = body["error"]["audit_ref"]
        assert audit_ref.startswith("audit://aud_")
        persisted_audit_ids.append(audit_ref.removeprefix("audit://"))

    with client.app.state.session_factory() as session:
        audits = session.scalars(
            select(Audit).where(Audit.action == "public-v2.header_rejected")
        ).all()
        assert {audit.audit_id for audit in audits} == set(persisted_audit_ids)
        assert all(
            audit.result == "denied" and audit.error_code == "REQUEST_INVALID"
            for audit in audits
        )


def test_r2_non_version_mutation_header_denial_does_not_create_audit(client) -> None:
    headers = _headers(mutation=True)
    headers.pop("X-CaseLoop-Idempotency-Key")
    response = client.post("/api/v2/applications", headers=headers, content=_app_body())

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert response.json()["error"]["audit_ref"] is None
    with client.app.state.session_factory() as session:
        assert session.scalars(
            select(Audit).where(Audit.action == "public-v2.header_rejected")
        ).all() == []


def test_r2_header_denial_audit_failure_returns_no_nonexistent_ref(
    client, monkeypatch
) -> None:
    def fail_audit(*_args, **_kwargs):
        raise V4AuditUnavailable("forced header audit failure")

    monkeypatch.setattr(V4AuditService, "record", fail_audit)
    headers = _headers()
    headers.pop("X-CaseLoop-Contract-Version")
    response = client.get("/api/v2/capabilities", headers=headers)

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "AUDIT_UNAVAILABLE"
    assert body["error"]["audit_ref"] is None
    with client.app.state.session_factory() as session:
        assert session.scalars(
            select(Audit).where(Audit.action == "public-v2.header_rejected")
        ).all() == []


def test_list_applications_requires_explicit_project_scope(client) -> None:
    response = client.get("/api/v2/applications", headers=_headers())

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert response.json()["error"]["audit_ref"].startswith("audit://aud_")


@pytest.mark.parametrize(
    ("query", "expected_code", "expected_status"),
    [
        (f"project_id={OTHER_PROJECT}", "RESOURCE_NOT_FOUND", 404),
        (
            f"project_id={PROJECT}&cursor=cur_forged-cross-scope",
            "REQUEST_INVALID",
            400,
        ),
        (f"project_id={PROJECT}&limit=101", "REQUEST_INVALID", 400),
    ],
)
def test_list_applications_denials_are_audited_without_item_or_count_leak(
    client,
    query: str,
    expected_code: str,
    expected_status: int,
) -> None:
    response = client.get(f"/api/v2/applications?{query}", headers=_headers())

    assert response.status_code == expected_status
    body = response.json()
    assert body["error"]["code"] == expected_code
    assert body["error"]["details"] == {}
    assert body["error"]["audit_ref"].startswith("audit://aud_")
    assert "items" not in body
    assert "count" not in response.text.lower()
    audit_id = body["error"]["audit_ref"].removeprefix("audit://")
    with client.app.state.session_factory() as session:
        audit = session.get(Audit, audit_id)
        assert audit is not None
        assert audit.action == "public.v5.applications.list"
        assert audit.result == "denied"
        assert audit.error_code == expected_code


def test_register_application_replay_returns_same_record(client) -> None:
    first = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    assert first.status_code == 201
    replay = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    assert replay.status_code == 201
    assert replay.json()["idempotency"]["replayed"] is True
    normalized = dict(replay.json())
    normalized["idempotency"]["replayed"] = False
    assert normalized == first.json()


def test_get_application_v2(client) -> None:
    registered = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    application_id = registered.json()["application"]["application_id"]
    response = client.get(
        f"/api/v2/applications/{application_id}", headers=_headers()
    )
    assert response.status_code == 200
    assert response.json()["application"]["application_id"] == application_id


def test_get_application_cross_project_is_opaque_not_found(client) -> None:
    """applications.get visibility: same workspace, other-project grant -> 404."""
    registered = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    application_id = registered.json()["application"]["application_id"]
    foreign_headers = {
        "Authorization": f"Bearer {FOREIGN_READER_TOKEN}",
        "X-CaseLoop-Workspace-ID": WORKSPACE,
        "X-CaseLoop-Contract-Version": "2.0",
        "X-Request-ID": "req_01J000000000000D",
        "Content-Type": "application/json",
    }
    response = client.get(
        f"/api/v2/applications/{application_id}", headers=foreign_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_v2_missing_application_returns_404(client) -> None:
    response = client.get(
        "/api/v2/applications/app_01J0000000000ZZZ", headers=_headers()
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


def test_wrong_contract_version_is_request_invalid(client) -> None:
    for version in ("1.0", "3.0", None):
        headers = _headers(mutation=True, contract_version=version)
        if version is None:
            headers.pop("X-CaseLoop-Contract-Version")
        response = client.post(
            "/api/v2/applications",
            headers=headers,
            content=_app_body(),
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "REQUEST_INVALID"


def test_missing_idempotency_key_is_rejected(client) -> None:
    headers = _headers(mutation=True)
    headers.pop("X-CaseLoop-Idempotency-Key")
    response = client.post("/api/v2/applications", headers=headers, content=_app_body())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_missing_bearer_is_authentication_required(client) -> None:
    headers = _headers(mutation=True)
    headers.pop("Authorization")
    response = client.post("/api/v2/applications", headers=headers, content=_app_body())
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_duplicate_slug_is_catalog_conflict(client) -> None:
    first = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    assert first.status_code == 201
    headers = _headers(mutation=True)
    headers["X-CaseLoop-Idempotency-Key"] = "app-register-0002"
    response = client.post("/api/v2/applications", headers=headers, content=_app_body())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CATALOG_CONFLICT"


def test_cross_workspace_request_is_denied(client) -> None:
    headers = _headers(mutation=True)
    headers["X-CaseLoop-Workspace-ID"] = "ws_01J0000000000999"
    response = client.post("/api/v2/applications", headers=headers, content=_app_body())
    assert response.status_code in (401, 403)
    assert response.json()["error"]["code"] in {
        "WORKSPACE_ACCESS_DENIED",
        "TOKEN_INVALID",
        "SCOPE_FORBIDDEN",
    }


def test_environment_and_component_registration_v2(client) -> None:
    app_body = json.loads(_app_body())
    registered = client.post(
        "/api/v2/applications", headers=_headers(mutation=True), content=_app_body()
    )
    application_id = registered.json()["application"]["application_id"]
    with client.app.state.session_factory() as session:
        application = ApplicationRegisterResponse.model_validate(
            registered.json()
        ).application
        _activate_registered_application_for_foundation_test(session, application)
        session.commit()

    env_headers = _headers(mutation=True)
    env_headers["X-CaseLoop-Idempotency-Key"] = "env-register-0001"
    env_body = json.dumps(
        {
            "schema_version": "2.0",
            "application_id": application_id,
            "logical_name": "production",
            "risk_classification": "HIGH",
        }
    ).encode("utf-8")
    env = client.post("/api/v2/environments", headers=env_headers, content=env_body)
    assert env.status_code == 201
    assert env.json()["environment"]["logical_name"] == "production"

    component_headers = _headers(mutation=True)
    component_headers["X-CaseLoop-Idempotency-Key"] = "component-register-0001"
    component_body = json.dumps(
        {
            "schema_version": "2.0",
            "application_id": application_id,
            "component_kind": "AGENT",
            "logical_name": "triage-agent",
            "owner_principal_ids": [OWNER],
            "criticality": "P1",
            "data_classification": "INTERNAL",
            "permission_classification": "READ_WRITE",
            "effect_classification": "LOCAL",
        }
    ).encode("utf-8")
    component = client.post(
        "/api/v2/system-components", headers=component_headers, content=component_body
    )
    assert component.status_code == 201
    assert component.json()["component"]["component_kind"] == "AGENT"
    assert component.json()["component"]["lifecycle_state"] in {
        "REGISTERED",
        "ACTIVE",
    }
    registered_component = component.json()
    registered_component["component"]["lifecycle_state"] = "REGISTERED"
    ComponentRegisterResponse.model_validate(registered_component)


# ---------------------------------------------------------------------------
# C4 route↔manifest registry gate (app.api.v5_route_registry).
# ---------------------------------------------------------------------------


def _registered_route_keys(router) -> set[tuple[str, str, str]]:
    return {
        (method, route.path, route.operation_id)
        for route in router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
        if method not in {"HEAD", "OPTIONS"}
    }


def test_v5_route_registry_matches_operation_manifest_exactly() -> None:
    manifest = load_v5_operation_manifest()
    http_entries = manifest.http_entries
    assert len(http_entries) == 19
    check_registered_v5_routes(public_v5.router)  # must not raise
    expected = {
        (entry.method.upper(), entry.path, entry.operation_id)
        for entry in http_entries
    }
    registered = _registered_route_keys(public_v5.router)
    assert len(registered) == 19
    assert registered == expected


@pytest.mark.parametrize(
    ("tamper", "expected_missing", "expected_extra"),
    [
        (
            "drop",
            [],
            [("GET", "/api/v2/cases/{case_id}/acceptance-criteria", "getAcceptanceCriteria")],
        ),
        (
            "add",
            [("POST", "/api/v2/system-versions", "createSystemVersion")],
            [],
        ),
        (
            "path",
            [("GET", "/api/v2/capabilities-renamed", "getV5Capabilities")],
            [("GET", "/api/v2/capabilities", "getV5Capabilities")],
        ),
        (
            "method",
            [("POST", "/api/v2/capabilities", "getV5Capabilities")],
            [("GET", "/api/v2/capabilities", "getV5Capabilities")],
        ),
        (
            "operation_id",
            [("GET", "/api/v2/capabilities", "tamperedOperationId")],
            [("GET", "/api/v2/capabilities", "getV5Capabilities")],
        ),
    ],
)
def test_v5_route_registry_tampered_manifest_fails_closed(
    tamper: str,
    expected_missing: list[tuple[str, str, str]],
    expected_extra: list[tuple[str, str, str]],
) -> None:
    real = load_v5_operation_manifest()
    entries = list(real.http_entries)
    if tamper == "drop":
        mutated = dataclasses.replace(real, http_entries=tuple(entries[:-1]))
    elif tamper == "add":
        mutated = dataclasses.replace(
            real,
            http_entries=tuple(entries)
            + (
                V5ManifestHttpRoute(
                    method="POST",
                    path="/api/v2/system-versions",
                    operation_id="createSystemVersion",
                ),
            ),
        )
    else:
        first = entries[0]
        replacement = {
            "path": V5ManifestHttpRoute(
                method=first.method,
                path="/api/v2/capabilities-renamed",
                operation_id=first.operation_id,
            ),
            "method": V5ManifestHttpRoute(
                method="POST",
                path=first.path,
                operation_id=first.operation_id,
            ),
            "operation_id": V5ManifestHttpRoute(
                method=first.method,
                path=first.path,
                operation_id="tamperedOperationId",
            ),
        }[tamper]
        mutated = dataclasses.replace(real, http_entries=(replacement, *entries[1:]))

    with pytest.raises(RouteManifestMismatchError) as excinfo:
        check_registered_v5_routes(public_v5.router, manifest=mutated)
    error = excinfo.value
    assert "v5.route_registry.mismatch" in str(error)
    assert set(error.missing) == set(expected_missing)
    assert set(error.extra) == set(expected_extra)
    assert error.details["missing"] == [list(key) for key in sorted(expected_missing)]
    assert error.details["extra"] == [list(key) for key in sorted(expected_extra)]


def test_v5_route_registry_tampered_manifest_file_fails_closed(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    manifest_source = (
        repo_root / "contracts/v5/generated/operation-manifest.json"
    )
    document = json.loads(manifest_source.read_text(encoding="utf-8"))
    for operation in document["operations"]:
        if operation.get("intent") == "capabilities.get":
            operation["http"]["operation_id"] = "tamperedOperationId"
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "operation-manifest.json").write_text(
        json.dumps(document, sort_keys=True), encoding="utf-8"
    )

    with pytest.raises(RouteManifestMismatchError) as excinfo:
        check_registered_v5_routes(public_v5.router, manifest_path=tmp_path)
    assert ("GET", "/api/v2/capabilities", "tamperedOperationId") in excinfo.value.missing
    assert ("GET", "/api/v2/capabilities", "getV5Capabilities") in excinfo.value.extra


def test_v5_route_registry_install_hook_passes_silently(capsys) -> None:
    install_route_manifest_check(public_v5.router)
    assert capsys.readouterr().err == ""


def test_v5_route_registry_install_hook_fails_closed_on_mismatch(
    monkeypatch, capsys
) -> None:
    def failing_check(*_args, **_kwargs):
        raise RouteManifestMismatchError(
            missing=[("GET", "/api/v2/capabilities", "getV5Capabilities")],
            extra=[],
        )

    monkeypatch.setattr(
        v5_route_registry, "check_registered_v5_routes", failing_check
    )
    with pytest.raises(RouteManifestMismatchError):
        install_route_manifest_check(public_v5.router)
    assert capsys.readouterr().err == ""  # fail-closed: nothing is swallowed


def test_v5_route_registry_install_hook_fails_closed_when_manifest_unavailable(
    monkeypatch, capsys
) -> None:
    def failing_check(*_args, **_kwargs):
        raise V5CapabilitiesManifestError(
            "v5.capabilities.operation_manifest_unavailable"
        )

    monkeypatch.setattr(
        v5_route_registry, "check_registered_v5_routes", failing_check
    )
    with pytest.raises(V5CapabilitiesManifestError):
        install_route_manifest_check(public_v5.router)
    assert capsys.readouterr().err == ""
