from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import create_app
from app.models.tables import Audit, Event, Outbox
from app.models.v4_tables import (
    PublicCommandIdempotency,
    PublicCredential,
    PublicPrincipal,
    QualityCase,
    Signal,
    SignalCaseLink,
    SignalContent,
    TraceEvidenceReceipt,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import hash_opaque_bearer
from app.public_api.models import (
    CaseResponse,
    CaseTimelineResponse,
    EvidenceResponse,
    ServerCapabilitiesResponse,
    SignalSubmissionResponse,
)
from app.services.public_read import PublicReadDenial
from app.utils.v4_integrity import canonical_digest


FIXTURES = Path(__file__).resolve().parents[3] / "contracts" / "v4" / "fixtures" / "valid"
WORKSPACE_ID = "ws_01J0000000000001"
REQUEST_ID = "req_01J0000000000004"
CASE_ID = "case_01J0000000000001"
RECEIPT_ID = "ter_01J0000000000001"
PUBLIC_TOKEN = "opaque-public-route-token"
MAX_SIGNAL_BODY_BYTES = 256_000
PUBLIC_SUBJECT = "maintainer-01J0000000000001"
PUBLIC_PROJECT_ID = "proj_01J0000000000001"
PUBLIC_ENVIRONMENT_ID = "env_01J0000000000001"
PUBLIC_SCOPES = [
    "signals:write",
    "cases:read",
    "artifacts:read",
    "capabilities:read",
]


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _principal(*, required_scope: str, project_id: str | None = None, environment_id: str | None = None) -> AcceptedPrincipalContext:
    payload = _fixture("public-principal-context.json")
    payload["requested_context"] = {
        "workspace_id": WORKSPACE_ID,
        "project_id": project_id,
        "environment_id": environment_id,
        "required_scope": required_scope,
    }
    return AcceptedPrincipalContext.model_validate(payload)


def _headers(*, mutation: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {PUBLIC_TOKEN}",
        "X-AgentMED-Workspace-ID": WORKSPACE_ID,
        "X-AgentMED-Contract-Version": "1.0",
        "X-Request-ID": REQUEST_ID,
    }
    if mutation:
        headers["Idempotency-Key"] = "signal-submit-0001"
    return headers


class _TrackingAsgiReceive:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.calls = 0

    async def __call__(self) -> dict[str, object]:
        if self.calls >= len(self._chunks):
            raise AssertionError("handler read beyond the supplied request body")
        body = self._chunks[self.calls]
        self.calls += 1
        return {
            "type": "http.request",
            "body": body,
            "more_body": self.calls < len(self._chunks),
        }


async def _asgi_signal_post(
    app,
    *,
    chunks: list[bytes],
    content_length: str | None,
    chunked: bool = False,
) -> tuple[int, dict[str, Any], _TrackingAsgiReceive]:
    headers = [
        (name.lower().encode("ascii"), value.encode("ascii"))
        for name, value in _headers(mutation=True).items()
    ]
    headers.append((b"content-type", b"application/json"))
    if content_length is not None:
        headers.append((b"content-length", content_length.encode("ascii")))
    if chunked:
        headers.append((b"transfer-encoding", b"chunked"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/signals",
        "raw_path": b"/api/v1/signals",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8090),
    }
    receive = _TrackingAsgiReceive(chunks)
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"")
        for message in sent
        if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(body), receive


def _assert_no_signal_transaction_writes(sqlite_engine) -> None:
    with Session(sqlite_engine) as session:
        for model in (
            SignalContent,
            Signal,
            QualityCase,
            SignalCaseLink,
            TraceEvidenceReceipt,
            PublicCommandIdempotency,
            Event,
            Outbox,
            Audit,
        ):
            assert session.scalars(select(model)).all() == []


def _case_response() -> CaseResponse:
    return CaseResponse.model_validate(
        {
            "schema_version": "1.0",
            "workspace_id": WORKSPACE_ID,
            "request_id": REQUEST_ID,
            "audit_ref": "audit://aud_01J0000000000004",
            "data": {
                "case_id": CASE_ID,
                "status": "OPEN",
                "revision": 1,
                "title": "Maintainer report needs trace correlation",
                "project_id": "proj_01J0000000000001",
                "environment_id": "env_01J0000000000001",
                "governed_agent_id": "ga_01J0000000000001",
                "correlation_status": "NEEDS_CORRELATION",
                "triage_status": "UNTRIAGED",
                "signal_refs": ["sig_01J0000000000001"],
                "run_refs": [],
                "evidence_summary": {
                    "status": "UNKNOWN",
                    "receipt_id": RECEIPT_ID,
                    "receipt_digest": "sha256:" + "a" * 64,
                    "agent_run_ref_id": None,
                    "missing_fields": ["trace.input"],
                },
                "input_summary": None,
                "output_summary": None,
                "opened_at": "2026-08-10T09:00:00Z",
                "updated_at": "2026-08-10T09:00:02Z",
                "resolved_at": None,
                "resolution_ref": None,
                "next_action": {
                    "code": "CORRELATE_TRACE",
                    "command": "case correlate",
                    "href": f"https://agentmed.local/api/v1/cases/{CASE_ID}",
                },
            },
        }
    )


def _timeline_response() -> CaseTimelineResponse:
    return CaseTimelineResponse.model_validate(
        {
            "schema_version": "1.0",
            "workspace_id": WORKSPACE_ID,
            "request_id": REQUEST_ID,
            "audit_ref": "audit://aud_01J0000000000005",
            "data": {
                "case_id": CASE_ID,
                "events": [
                    {
                        "event_id": "evt_01J0000000000001",
                        "event_type": "case.opened",
                        "event_version": "1.0",
                        "occurred_at": "2026-08-10T09:00:00Z",
                        "causation_id": None,
                        "correlation_id": CASE_ID,
                        "actor_principal_id": "prn_01J0000000000001",
                        "transaction_id": "txn_01J0000000000001",
                        "payload_ref": {
                            "uri": "artifact://events/case-opened",
                            "digest": "sha256:" + "b" * 64,
                            "media_type": "application/json",
                        },
                        "payload_digest": "sha256:" + "b" * 64,
                        "redaction_status": "NOT_REQUIRED",
                    }
                ],
                "page": {
                    "limit": 50,
                    "next_cursor": None,
                    "has_more": False,
                    "snapshot": {
                        "watermark_event_id": "evt_01J0000000000001",
                        "order": "occurred_at,event_id",
                        "filter_digest": "sha256:" + "c" * 64,
                        "cursor_scope_digest": "sha256:" + "d" * 64,
                    },
                },
            },
        }
    )


def _evidence_response() -> EvidenceResponse:
    receipt = _fixture("trace-evidence-receipt-no-locator.json")
    return EvidenceResponse.model_validate(
        {
            "schema_version": "1.0",
            "workspace_id": WORKSPACE_ID,
            "request_id": REQUEST_ID,
            "audit_ref": "audit://aud_01J0000000000006",
            "data": {
                "receipt_kind": "TRACE_EVIDENCE_RECEIPT",
                "receipt": receipt,
                "receipt_digest": receipt["receipt_digest"],
                "verification_status": "NOT_VERIFIED",
                "verified_at": None,
                "superseded_by": None,
            },
        }
    )


class FakeCredentialResolver:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def resolve(self, bearer_token: SecretStr, **kwargs: Any) -> AcceptedPrincipalContext:
        assert isinstance(bearer_token, SecretStr)
        self.calls.append({"bearer": bearer_token, **kwargs})
        return _principal(
            required_scope=kwargs["required_scope"],
            project_id=kwargs.get("project_id"),
            environment_id=kwargs.get("environment_id"),
        )

    def bind_requested_context(
        self,
        principal: AcceptedPrincipalContext,
        *,
        project_id: str | None,
        environment_id: str | None,
        required_scope: str,
    ) -> AcceptedPrincipalContext:
        assert principal.principal_id == "prn_01J0000000000001"
        return _principal(
            required_scope=required_scope,
            project_id=project_id,
            environment_id=environment_id,
        )


class FakeSignalService:
    def __init__(self, response: Any | None = None) -> None:
        self.response = response or SignalSubmissionResponse.model_validate(
            _fixture("public-signal-submission-response.json")
        )
        self.calls: list[dict[str, Any]] = []

    def submit(self, submission, **kwargs: Any):
        self.calls.append({"submission": submission, **kwargs})
        return self.response


class FakeReadService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_case(self, **kwargs: Any) -> CaseResponse:
        self.calls.append(("get_case", kwargs))
        return _case_response()

    def get_case_timeline(self, **kwargs: Any) -> CaseTimelineResponse:
        self.calls.append(("get_case_timeline", kwargs))
        return _timeline_response()

    def get_evidence(self, **kwargs: Any) -> EvidenceResponse:
        self.calls.append(("get_evidence", kwargs))
        return _evidence_response()

    def get_capabilities(self, **kwargs: Any) -> ServerCapabilitiesResponse:
        self.calls.append(("get_capabilities", kwargs))
        intents = kwargs["implemented_intents"]
        principal = kwargs["principal"]
        return ServerCapabilitiesResponse.model_validate(
            {
                "schema_version": "1.0",
                "workspace_id": principal.workspace_id,
                "request_id": kwargs["request_id"],
                "audit_ref": "audit://aud_01J0000000000007",
                "data": {
                    "server_version": kwargs["server_version"],
                    "public_api_major": 1,
                    "supported_contract_versions": ["1.0"],
                    "principal": {
                        "principal_id": principal.principal_id,
                        "principal_type": principal.principal_type,
                        "scopes": principal.scopes,
                        "credential_expires_at": principal.expires_at,
                    },
                    "enabled_intents": intents,
                    "generated_at": datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
                },
            }
        )


def _client(sqlite_engine, test_settings, *, signal_response: Any | None = None):
    resolver = FakeCredentialResolver()
    signal = FakeSignalService(signal_response)
    reads = FakeReadService()
    settings = test_settings.model_copy(
        update={
            "public_credential_hash_pepper": SecretStr("route-test-pepper"),
            "public_cursor_signing_key": SecretStr("route-test-cursor-key"),
        }
    )
    app = create_app(settings=settings, engine=sqlite_engine, create_tables=True)
    context = TestClient(app)
    client = context.__enter__()
    app.state.public_credential_resolver_factory = lambda _session: resolver
    app.state.signal_intake_service_factory = lambda _session: signal
    app.state.public_read_service_factory = lambda _session, _key: reads
    return context, client, app, resolver, signal, reads


def _streaming_test_app(sqlite_engine, test_settings):
    resolver = FakeCredentialResolver()
    signal = FakeSignalService()
    settings = test_settings.model_copy(
        update={
            "public_credential_hash_pepper": SecretStr("stream-test-pepper"),
            "public_cursor_signing_key": SecretStr("stream-test-cursor-key"),
        }
    )
    app = create_app(settings=settings, engine=sqlite_engine, create_tables=True)
    app.state.public_credential_resolver_factory = lambda _session: resolver
    app.state.signal_intake_service_factory = lambda _session: signal
    return app, resolver, signal


def _public_claims_digest(
    *,
    workspace_id: str = WORKSPACE_ID,
    project_ids: list[str] | None = None,
    environment_ids: list[str] | None = None,
    scopes: list[str] | None = None,
    audiences: list[str] | None = None,
    principal_type: str = "human",
) -> str:
    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": "https://auth.agentmed.dev",
            "subject": PUBLIC_SUBJECT,
            "principal_type": principal_type,
            "audiences": ["caseloop-public-api"] if audiences is None else audiences,
            "workspace_id": workspace_id,
            "project_ids": (
                [PUBLIC_PROJECT_ID] if project_ids is None else project_ids
            ),
            "environment_ids": (
                [PUBLIC_ENVIRONMENT_ID]
                if environment_ids is None
                else environment_ids
            ),
            "scopes": PUBLIC_SCOPES if scopes is None else scopes,
        }
    )


def _seed_real_public_identity(
    sqlite_engine,
    pepper: SecretStr,
    *,
    workspace_id: str = WORKSPACE_ID,
    project_ids: list[str] | None = None,
    environment_ids: list[str] | None = None,
    scopes: list[str] | None = None,
    audiences: list[str] | None = None,
    principal_type: str = "human",
    claims_digest: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    granted_projects = [PUBLIC_PROJECT_ID] if project_ids is None else project_ids
    granted_environments = (
        [PUBLIC_ENVIRONMENT_ID]
        if environment_ids is None
        else environment_ids
    )
    granted_scopes = PUBLIC_SCOPES if scopes is None else scopes
    granted_audiences = (
        ["caseloop-public-api"] if audiences is None else audiences
    )
    stored_claims_digest = claims_digest or _public_claims_digest(
        workspace_id=workspace_id,
        project_ids=granted_projects,
        environment_ids=granted_environments,
        scopes=granted_scopes,
        audiences=granted_audiences,
        principal_type=principal_type,
    )
    with Session(sqlite_engine) as session:
        session.add(
            PublicPrincipal(
                principal_id="prn_01J0000000000001",
                workspace_id=workspace_id,
                principal_type=principal_type,
                state="ACTIVE",
                subject_digest="sha256:"
                + hashlib.sha256(PUBLIC_SUBJECT.encode("utf-8")).hexdigest(),
                audiences=granted_audiences,
                project_ids=granted_projects,
                environment_ids=granted_environments,
                scopes=granted_scopes,
                claims_digest=stored_claims_digest,
                revoked_at=None,
            )
        )
        session.add(
            PublicCredential(
                credential_id="cred_01J0000000000001",
                workspace_id=workspace_id,
                principal_id="prn_01J0000000000001",
                issuer="https://auth.agentmed.dev",
                subject=PUBLIC_SUBJECT,
                credential_hash=hash_opaque_bearer(PUBLIC_TOKEN, pepper),
                hash_algorithm="hmac-sha256-v1",
                jti_digest="sha256:" + "4" * 64,
                claims_digest=stored_claims_digest,
                audiences=granted_audiences,
                project_ids=granted_projects,
                environment_ids=granted_environments,
                scopes=granted_scopes,
                state="ACTIVE",
                issued_at=now - timedelta(minutes=1),
                not_before=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
                revoked_at=None,
            )
        )
        session.commit()


def test_only_frozen_s1a_http_routes_are_registered(sqlite_engine, test_settings) -> None:
    context, _client_obj, app, *_ = _client(sqlite_engine, test_settings)
    try:
        paths = {route.path for route in app.routes}
        assert {
            "/api/v1/capabilities",
            "/api/v1/signals",
            "/api/v1/cases/{case_id}",
            "/api/v1/cases/{case_id}/timeline",
            "/api/v1/evidence/{receipt_id}",
        } <= paths
        assert not any(path.startswith("/api/v1/sources/") for path in paths)
        assert not any("investigations" in path for path in paths)
    finally:
        context.__exit__(None, None, None)


def test_signal_submit_authenticates_then_binds_body_grants_and_commits_exact_response(
    sqlite_engine, test_settings
) -> None:
    context, client, _app, resolver, signal, _reads = _client(sqlite_engine, test_settings)
    try:
        response = client.post(
            "/api/v1/signals",
            headers=_headers(mutation=True),
            json=_fixture("public-signal-submission.json"),
        )

        assert response.status_code == 201
        assert response.headers["X-AgentMED-Contract-Version"] == "1.0"
        assert response.json() == _fixture("public-signal-submission-response.json")
        assert len(resolver.calls) == 1
        assert resolver.calls[0]["required_scope"] == "signals:write"
        call = signal.calls[0]
        assert call["principal"].requested_context.project_id == "proj_01J0000000000001"
        assert call["idempotency_key"] == "signal-submit-0001"
        assert call["request_id"] == REQUEST_ID
        assert "bearer" not in call and "headers" not in call
    finally:
        context.__exit__(None, None, None)


def test_capabilities_advertises_only_implemented_authorized_s1a_not_s1b_or_skeleton(
    sqlite_engine, test_settings
) -> None:
    context, client, _app, _resolver, _signal, reads = _client(sqlite_engine, test_settings)
    try:
        response = client.get("/api/v1/capabilities", headers=_headers())

        assert response.status_code == 200
        enabled = {item["name"] for item in response.json()["data"]["enabled_intents"]}
        assert enabled == {
            "capabilities.get",
            "signals.submit",
            "cases.get",
            "cases.timeline",
            "evidence.get",
        }
        passed = reads.calls[0][1]["implemented_intents"]
        assert {item["name"] for item in passed} == enabled
        assert "sources.doctor" not in enabled
        assert "investigations.start" not in enabled
    finally:
        context.__exit__(None, None, None)


def test_http_route_uses_real_hmac_credential_resolver_without_internal_token_reuse(
    sqlite_engine, test_settings
) -> None:
    pepper = SecretStr("real-route-public-pepper")
    cursor_key = SecretStr("real-route-cursor-key")
    _seed_real_public_identity(sqlite_engine, pepper)

    settings = test_settings.model_copy(
        update={
            "public_credential_hash_pepper": pepper,
            "public_cursor_signing_key": cursor_key,
        }
    )
    app = create_app(settings=settings, engine=sqlite_engine, create_tables=True)
    with TestClient(app) as client:
        response = client.get("/api/v1/capabilities", headers=_headers())

    with Session(sqlite_engine) as session:
        audit = session.scalar(
            select(Audit).where(
                Audit.contract_version == "v4",
                Audit.action == "public.capabilities.get",
            )
        )

    assert response.status_code == 200
    assert response.json()["data"]["principal"]["principal_id"] == "prn_01J0000000000001"
    assert audit is not None
    assert audit.actor_principal == "prn_01J0000000000001"
    assert PUBLIC_TOKEN not in response.text


@pytest.mark.parametrize(
    ("seed_kwargs", "requested_workspace_id", "payload_changes"),
    [
        (
            {
                "scopes": PUBLIC_SCOPES,
                "claims_digest": _public_claims_digest(
                    scopes=[scope for scope in PUBLIC_SCOPES if scope != "signals:write"]
                ),
            },
            WORKSPACE_ID,
            {},
        ),
        (
            {
                "project_ids": [PUBLIC_PROJECT_ID, "proj_01J0000000000099"],
                "claims_digest": _public_claims_digest(),
            },
            WORKSPACE_ID,
            {"project_id": "proj_01J0000000000099"},
        ),
        (
            {
                "environment_ids": [
                    PUBLIC_ENVIRONMENT_ID,
                    "env_01J0000000000099",
                ],
                "claims_digest": _public_claims_digest(),
            },
            WORKSPACE_ID,
            {"environment_id": "env_01J0000000000099"},
        ),
        (
            {
                "workspace_id": "ws_01J0000000000099",
                "claims_digest": _public_claims_digest(),
            },
            "ws_01J0000000000099",
            {},
        ),
    ],
)
def test_real_asgi_rejects_coordinated_stale_claims_escalation_before_any_write(
    sqlite_engine,
    test_settings,
    seed_kwargs: dict[str, object],
    requested_workspace_id: str,
    payload_changes: dict[str, str],
) -> None:
    pepper = SecretStr("stale-claims-route-pepper")
    _seed_real_public_identity(sqlite_engine, pepper, **seed_kwargs)
    signal = FakeSignalService()
    settings = test_settings.model_copy(
        update={
            "public_credential_hash_pepper": pepper,
            "public_cursor_signing_key": SecretStr("stale-claims-cursor-key"),
        }
    )
    app = create_app(settings=settings, engine=sqlite_engine, create_tables=True)
    app.state.signal_intake_service_factory = lambda _session: signal
    headers = _headers(mutation=True)
    headers["X-AgentMED-Workspace-ID"] = requested_workspace_id
    payload = copy.deepcopy(_fixture("public-signal-submission.json"))
    payload.update(payload_changes)

    with TestClient(app) as client:
        response = client.post("/api/v1/signals", headers=headers, json=payload)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "TOKEN_INVALID"
    assert response.json()["workspace_id"] is None
    assert signal.calls == []
    with Session(sqlite_engine) as session:
        assert session.scalars(select(Signal)).all() == []
        assert session.scalars(select(PublicCommandIdempotency)).all() == []
        assert session.scalars(select(Audit)).all() == []
    assert PUBLIC_TOKEN not in response.text


def test_http_route_calls_real_signal_service_and_rolls_back_missing_source_work(
    sqlite_engine, test_settings
) -> None:
    pepper = SecretStr("real-signal-public-pepper")
    _seed_real_public_identity(sqlite_engine, pepper)
    settings = test_settings.model_copy(
        update={
            "public_credential_hash_pepper": pepper,
            "public_cursor_signing_key": SecretStr("real-signal-cursor-key"),
        }
    )
    app = create_app(settings=settings, engine=sqlite_engine, create_tables=True)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/signals",
            headers=_headers(mutation=True),
            json=_fixture("public-signal-submission.json"),
        )

    with Session(sqlite_engine) as session:
        idempotency_rows = session.scalars(select(PublicCommandIdempotency)).all()
        signal_rows = session.scalars(select(Signal)).all()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
    assert response.json()["workspace_id"] == WORKSPACE_ID
    assert response.json()["error"]["audit_ref"] is None
    assert idempotency_rows == []
    assert signal_rows == []
    assert PUBLIC_TOKEN not in response.text


@pytest.mark.parametrize(
    ("path", "method_name", "scope"),
    [
        (f"/api/v1/cases/{CASE_ID}", "get_case", "cases:read"),
        (f"/api/v1/cases/{CASE_ID}/timeline?limit=50", "get_case_timeline", "cases:read"),
        (f"/api/v1/evidence/{RECEIPT_ID}", "get_evidence", "artifacts:read"),
    ],
)
def test_workspace_bound_read_routes_delegate_to_read_service(
    sqlite_engine, test_settings, path: str, method_name: str, scope: str
) -> None:
    context, client, _app, resolver, _signal, reads = _client(sqlite_engine, test_settings)
    try:
        response = client.get(path, headers=_headers())

        assert response.status_code == 200
        assert resolver.calls[0]["required_scope"] == scope
        assert reads.calls[0][0] == method_name
        assert reads.calls[0][1]["principal"].workspace_id == WORKSPACE_ID
        assert reads.calls[0][1]["request_id"] == REQUEST_ID
    finally:
        context.__exit__(None, None, None)


@pytest.mark.parametrize(
    ("headers", "expected_status", "expected_code"),
    [
        ({}, 401, "AUTHENTICATION_REQUIRED"),
        (
            {
                "Authorization": "Basic abc",
                "X-AgentMED-Workspace-ID": WORKSPACE_ID,
                "X-AgentMED-Contract-Version": "1.0",
            },
            401,
            "TOKEN_INVALID",
        ),
        (
            {
                "Authorization": f"Bearer {PUBLIC_TOKEN}",
                "X-AgentMED-Workspace-ID": WORKSPACE_ID,
                "X-AgentMED-Contract-Version": "2.0",
            },
            412,
            "CONTRACT_VERSION_UNSUPPORTED",
        ),
    ],
)
def test_public_header_failures_use_safe_exact_public_error(
    sqlite_engine,
    test_settings,
    headers: dict[str, str],
    expected_status: int,
    expected_code: str,
) -> None:
    context, client, _app, _resolver, _signal, _reads = _client(sqlite_engine, test_settings)
    try:
        response = client.get("/api/v1/capabilities", headers=headers)

        assert response.status_code == expected_status
        body = response.json()
        assert body["error"]["code"] == expected_code
        assert body["workspace_id"] is None
        assert body["workspace_resolved"] is False
        assert body["error"]["audit_ref"] is None
        assert PUBLIC_TOKEN not in response.text
    finally:
        context.__exit__(None, None, None)


@pytest.mark.parametrize(
    "duplicate_name",
    [
        "Authorization",
        "X-AgentMED-Workspace-ID",
        "X-AgentMED-Contract-Version",
        "X-Request-ID",
        "Idempotency-Key",
    ],
)
def test_raw_duplicate_authority_headers_fail_before_mutation_write(
    sqlite_engine, test_settings, duplicate_name: str
) -> None:
    context, client, _app, resolver, signal, reads = _client(
        sqlite_engine, test_settings
    )
    raw_headers = list(_headers(mutation=True).items())
    original_value = dict(raw_headers)[duplicate_name]
    raw_headers.append((duplicate_name, original_value))
    try:
        response = client.post(
            "/api/v1/signals",
            headers=raw_headers,
            json=_fixture("public-signal-submission.json"),
        )

        assert response.status_code == 400
        body = response.json()
        assert body["error"]["code"] == "REQUEST_INVALID"
        assert body["workspace_id"] is None
        assert body["workspace_resolved"] is False
        assert body["error"]["audit_ref"] is None
        assert resolver.calls == []
        assert signal.calls == []
        assert reads.calls == []
        with Session(sqlite_engine) as session:
            assert session.scalars(select(Signal)).all() == []
            assert session.scalars(select(PublicCommandIdempotency)).all() == []
            assert session.scalars(select(Audit)).all() == []
    finally:
        context.__exit__(None, None, None)


def test_bearer_header_is_validated_before_database_session_resolution(
    sqlite_engine, test_settings
) -> None:
    context, client, app, _resolver, _signal, _reads = _client(sqlite_engine, test_settings)

    def unavailable_session():
        raise RuntimeError("private database connection detail")

    app.state.session_factory = unavailable_session
    try:
        unauthenticated = client.get("/api/v1/capabilities")
        dependency_failure = client.get("/api/v1/capabilities", headers=_headers())

        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
        assert dependency_failure.status_code == 503
        assert dependency_failure.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert "private database" not in dependency_failure.text
    finally:
        context.__exit__(None, None, None)


def test_scope_denial_reports_workspace_only_after_bearer_workspace_was_resolved(
    sqlite_engine, test_settings
) -> None:
    context, client, app, _resolver, _signal, _reads = _client(sqlite_engine, test_settings)

    class ScopeDenied(Exception):
        code = "SCOPE_FORBIDDEN"
        workspace_id = WORKSPACE_ID
        details: dict[str, object] = {}
        audit_ref = None
        rollback_required = True

    class DenyingResolver:
        def resolve(self, _bearer: SecretStr, **_kwargs: Any) -> AcceptedPrincipalContext:
            raise ScopeDenied()

    app.state.public_credential_resolver_factory = lambda _session: DenyingResolver()
    try:
        response = client.get("/api/v1/capabilities", headers=_headers())

        assert response.status_code == 403
        body = response.json()
        assert body["error"]["code"] == "SCOPE_FORBIDDEN"
        assert body["workspace_id"] == WORKSPACE_ID
        assert body["workspace_resolved"] is True
        assert body["error"]["audit_ref"] is None
    finally:
        context.__exit__(None, None, None)


@pytest.mark.parametrize("misconfiguration", ["empty", "reused-internal"])
def test_public_credential_hash_pepper_fails_closed_when_missing_or_reused(
    sqlite_engine, test_settings, misconfiguration: str
) -> None:
    context, client, app, _resolver, _signal, _reads = _client(sqlite_engine, test_settings)
    del app.state.public_credential_resolver_factory
    app.state.settings.public_credential_hash_pepper = SecretStr(
        "" if misconfiguration == "empty" else app.state.settings.control_plane_internal_token
    )
    try:
        response = client.get("/api/v1/capabilities", headers=_headers())

        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert body["workspace_id"] is None
        assert app.state.settings.control_plane_internal_token not in response.text
    finally:
        context.__exit__(None, None, None)


def test_body_cannot_self_assert_workspace_and_validation_uses_public_error(
    sqlite_engine, test_settings
) -> None:
    context, client, _app, _resolver, signal, _reads = _client(sqlite_engine, test_settings)
    payload = copy.deepcopy(_fixture("public-signal-submission.json"))
    payload["workspace_id"] = "ws_01J0000000000099"
    try:
        response = client.post(
            "/api/v1/signals",
            headers=_headers(mutation=True),
            json=payload,
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_FAILED"
        assert signal.calls == []
    finally:
        context.__exit__(None, None, None)


def test_signal_mutation_requires_idempotency_before_service_execution(
    sqlite_engine, test_settings
) -> None:
    context, client, _app, _resolver, signal, _reads = _client(sqlite_engine, test_settings)
    try:
        response = client.post(
            "/api/v1/signals",
            headers=_headers(mutation=False),
            json=_fixture("public-signal-submission.json"),
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
        assert signal.calls == []
    finally:
        context.__exit__(None, None, None)


def test_signal_rejects_non_json_media_type_with_exact_public_error(
    sqlite_engine, test_settings
) -> None:
    context, client, _app, _resolver, signal, _reads = _client(sqlite_engine, test_settings)
    try:
        response = client.post(
            "/api/v1/signals",
            headers={**_headers(mutation=True), "Content-Type": "text/plain"},
            content=json.dumps(_fixture("public-signal-submission.json")),
        )

        assert response.status_code == 415
        assert response.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
        assert signal.calls == []
    finally:
        context.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_declared_oversize_signal_body_is_rejected_before_read_or_authentication(
    sqlite_engine, test_settings
) -> None:
    app, resolver, signal = _streaming_test_app(sqlite_engine, test_settings)

    status, body, receive = await _asgi_signal_post(
        app,
        chunks=[b"body must never be read"],
        content_length=str(MAX_SIGNAL_BODY_BYTES + 1),
    )

    assert status == 413
    assert body["error"]["code"] == "CONTENT_TOO_LARGE"
    assert body["workspace_id"] is None
    assert receive.calls == 0
    assert resolver.calls == []
    assert signal.calls == []
    _assert_no_signal_transaction_writes(sqlite_engine)


@pytest.mark.parametrize(
    ("content_length", "chunked"),
    [(None, True), ("1", False)],
    ids=["chunked-without-content-length", "misleading-small-content-length"],
)
@pytest.mark.asyncio
async def test_streamed_oversize_signal_body_stops_at_first_chunk_over_limit(
    sqlite_engine,
    test_settings,
    content_length: str | None,
    chunked: bool,
) -> None:
    app, resolver, signal = _streaming_test_app(sqlite_engine, test_settings)
    chunks = [
        b"{" + b" " * (MAX_SIGNAL_BODY_BYTES - 1),
        b"x",
        b"handler-must-not-read-this-chunk",
    ]

    status, body, receive = await _asgi_signal_post(
        app,
        chunks=chunks,
        content_length=content_length,
        chunked=chunked,
    )

    assert status == 413
    assert body["error"]["code"] == "CONTENT_TOO_LARGE"
    assert body["workspace_id"] is None
    assert receive.calls == 2
    assert resolver.calls == []
    assert signal.calls == []
    _assert_no_signal_transaction_writes(sqlite_engine)


@pytest.mark.asyncio
async def test_exact_signal_body_limit_accepts_valid_streamed_json(
    sqlite_engine, test_settings
) -> None:
    app, resolver, signal = _streaming_test_app(sqlite_engine, test_settings)
    raw = json.dumps(
        _fixture("public-signal-submission.json"), separators=(",", ":")
    ).encode("utf-8")
    raw += b" " * (MAX_SIGNAL_BODY_BYTES - len(raw))

    status, body, receive = await _asgi_signal_post(
        app,
        chunks=[raw[:100_000], raw[100_000:]],
        content_length=str(MAX_SIGNAL_BODY_BYTES),
    )

    assert status == 201
    assert body == _fixture("public-signal-submission-response.json")
    assert receive.calls == 2
    assert len(resolver.calls) == 1
    assert len(signal.calls) == 1


def test_signal_rejects_duplicate_json_keys_before_fingerprinting_or_service_call(
    sqlite_engine, test_settings
) -> None:
    context, client, _app, _resolver, signal, _reads = _client(sqlite_engine, test_settings)
    raw = json.dumps(_fixture("public-signal-submission.json"))
    raw = raw.replace(
        '"source_id": "src_01J0000000000001",',
        '"source_id": "src_01J0000000000001", '
        '"source_id": "src_01J0000000000099",',
        1,
    )
    try:
        response = client.post(
            "/api/v1/signals",
            headers={**_headers(mutation=True), "Content-Type": "application/json"},
            content=raw,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "REQUEST_INVALID"
        assert signal.calls == []
    finally:
        context.__exit__(None, None, None)


@pytest.mark.parametrize("nonfinite", ["NaN", "Infinity", "-Infinity"])
def test_signal_rejects_nonfinite_json_constants(
    sqlite_engine, test_settings, nonfinite: str
) -> None:
    context, client, _app, _resolver, signal, _reads = _client(
        sqlite_engine, test_settings
    )
    raw = json.dumps(_fixture("public-signal-submission.json"))
    raw = raw.replace('"run_locator": null', f'"run_locator": {nonfinite}')
    try:
        response = client.post(
            "/api/v1/signals",
            headers={**_headers(mutation=True), "Content-Type": "application/json"},
            content=raw,
        )

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "REQUEST_INVALID"
        assert signal.calls == []
    finally:
        context.__exit__(None, None, None)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/cases/not-a-case-id",
        f"/api/v1/cases/{CASE_ID}/timeline?cursor=forged-cursor",
        f"/api/v1/cases/{CASE_ID}/timeline?limit=201",
    ],
)
def test_path_and_cursor_validation_fail_after_auth_without_calling_read_service(
    sqlite_engine, test_settings, path: str
) -> None:
    context, client, _app, _resolver, _signal, reads = _client(sqlite_engine, test_settings)
    try:
        response = client.get(path, headers=_headers())

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_FAILED"
        assert body["workspace_id"] == WORKSPACE_ID
        assert reads.calls == []
    finally:
        context.__exit__(None, None, None)


def test_cross_workspace_or_missing_read_is_non_enumerating_404(
    sqlite_engine, test_settings
) -> None:
    context, client, app, _resolver, _signal, reads = _client(sqlite_engine, test_settings)
    missing_resource = PublicReadDenial(
        "RESOURCE_NOT_FOUND",
        audit_ref="audit://aud_01J0000000000099",
        workspace_id=WORKSPACE_ID,
        details={},
    )

    class TrackingDenialSession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False
            self.closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    tracking = TrackingDenialSession()
    app.state.session_factory = lambda: tracking

    reads.get_case = lambda **_kwargs: (_ for _ in ()).throw(missing_resource)
    try:
        response = client.get(f"/api/v1/cases/{CASE_ID}", headers=_headers())

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"
        assert "workspace" not in response.json()["error"]["details"]
        assert response.json()["error"]["audit_ref"] == missing_resource.audit_ref
        assert response.json()["error"]["audit_status"] == "RECORDED"
        assert tracking.committed is True
        assert tracking.rolled_back is False
        assert tracking.closed is True
    finally:
        context.__exit__(None, None, None)


def test_authenticated_signed_cursor_denial_commits_only_its_audit(
    sqlite_engine, test_settings
) -> None:
    context, client, app, _resolver, _signal, reads = _client(sqlite_engine, test_settings)
    cursor_denied = PublicReadDenial(
        "VALIDATION_FAILED",
        audit_ref="audit://aud_01J0000000000097",
        workspace_id=WORKSPACE_ID,
        details={"fields": ["cursor"]},
    )

    class TrackingDenialSession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            pass

    tracking = TrackingDenialSession()
    app.state.session_factory = lambda: tracking
    reads.get_case_timeline = lambda **_kwargs: (_ for _ in ()).throw(
        cursor_denied
    )
    try:
        response = client.get(
            f"/api/v1/cases/{CASE_ID}/timeline?cursor=cur_01J0000000000001",
            headers=_headers(),
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "VALIDATION_FAILED"
        assert body["error"]["audit_ref"] == cursor_denied.audit_ref
        assert body["error"]["details"] == {"fields": ["cursor"]}
        assert tracking.committed is True
        assert tracking.rolled_back is False
    finally:
        context.__exit__(None, None, None)


def test_duck_typed_read_denial_cannot_commit_or_escape_as_public_error(
    sqlite_engine, test_settings
) -> None:
    context, client, app, _resolver, _signal, reads = _client(sqlite_engine, test_settings)

    class ForgedReadDenial(Exception):
        code = "RESOURCE_NOT_FOUND"
        details: dict[str, object] = {}
        audit_ref = "audit://aud_01J0000000000096"
        rollback_required = False
        workspace_id = WORKSPACE_ID

    class TrackingSession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False
            self.closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    tracking = TrackingSession()
    app.state.session_factory = lambda: tracking
    reads.get_case = lambda **_kwargs: (_ for _ in ()).throw(ForgedReadDenial())
    try:
        response = client.get(f"/api/v1/cases/{CASE_ID}", headers=_headers())

        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert body["error"]["audit_ref"] is None
        assert tracking.committed is False
        assert tracking.rolled_back is True
        assert tracking.closed is True
    finally:
        context.__exit__(None, None, None)


class FailingCommitSession:
    def __init__(self) -> None:
        self.rollback_called = False
        self.close_called = False

    def commit(self) -> None:
        raise RuntimeError("database serialization detail must stay private")

    def rollback(self) -> None:
        self.rollback_called = True

    def close(self) -> None:
        self.close_called = True


def test_commit_failure_rolls_back_and_returns_truthful_audit_unavailable(
    sqlite_engine, test_settings
) -> None:
    context, client, app, _resolver, _signal, _reads = _client(sqlite_engine, test_settings)
    failing = FailingCommitSession()
    app.state.session_factory = lambda: failing
    try:
        response = client.get("/api/v1/capabilities", headers=_headers())

        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "AUDIT_UNAVAILABLE"
        assert body["error"]["audit_status"] == "UNAVAILABLE"
        assert body["error"]["audit_ref"] is None
        assert body["workspace_id"] == WORKSPACE_ID
        assert "serialization" not in response.text
        assert failing.rollback_called is True
        assert failing.close_called is True
    finally:
        context.__exit__(None, None, None)


def test_response_serialization_failure_rolls_back_before_any_commit(
    sqlite_engine, test_settings
) -> None:
    context, client, app, _resolver, _signal, _reads = _client(
        sqlite_engine, test_settings, signal_response={"success": True}
    )

    class TrackingSession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False
            self.closed = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    tracking = TrackingSession()
    app.state.session_factory = lambda: tracking
    try:
        response = client.post(
            "/api/v1/signals",
            headers=_headers(mutation=True),
            json=_fixture("public-signal-submission.json"),
        )

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert tracking.committed is False
        assert tracking.rolled_back is True
        assert tracking.closed is True
    finally:
        context.__exit__(None, None, None)


def test_signal_error_can_never_opt_into_read_denial_audit_only_commit(
    sqlite_engine, test_settings
) -> None:
    context, client, app, _resolver, signal, _reads = _client(sqlite_engine, test_settings)

    class UnsafeMutationDenial(Exception):
        code = "RESOURCE_NOT_FOUND"
        details: dict[str, object] = {}
        audit_ref = "audit://aud_01J0000000000098"
        rollback_required = False

    class TrackingSession:
        def __init__(self) -> None:
            self.committed = False
            self.rolled_back = False

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            pass

    tracking = TrackingSession()
    app.state.session_factory = lambda: tracking
    signal.submit = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        UnsafeMutationDenial()
    )
    try:
        response = client.post(
            "/api/v1/signals",
            headers=_headers(mutation=True),
            json=_fixture("public-signal-submission.json"),
        )

        assert response.status_code == 404
        assert response.json()["error"]["audit_ref"] is None
        assert tracking.committed is False
        assert tracking.rolled_back is True
    finally:
        context.__exit__(None, None, None)
