from __future__ import annotations

import io
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from caseloop_cli.client import PublicApiClient, RuntimeConfig, validate_base_url
from caseloop_cli.errors import CliError, ExitFamily
from caseloop_cli.main import run
from .wire_samples import capabilities as capabilities_success
from .wire_samples import digest
from .wire_samples import evidence as evidence_success
from .wire_samples import signal as signal_success
from .wire_samples import source_query_evidence
from .wire_samples import success_for


BASE = "http://127.0.0.1:8090"
WORKSPACE = "ws_01J0000000000001"
SOURCE = "src_01J0000000000001"
TOKEN = "retry-token-never-echo"
FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "v4" / "fixtures" / "valid"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "url",
    [
        "http://caseloop.example",
        "ftp://127.0.0.1:8090",
        "https://user:password@caseloop.example",
        "https://caseloop.example/base-path",
        "https://caseloop.example?query=yes",
        "https://caseloop.example#fragment",
    ],
)
def test_unsafe_or_ambiguous_base_url_is_rejected(url: str) -> None:
    with pytest.raises(CliError) as caught:
        validate_base_url(url)
    assert caught.value.code == "API_URL_INVALID"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8090",
        "http://[::1]:8090",
        "http://localhost:8090",
        "https://caseloop.example",
    ],
)
def test_loopback_http_and_remote_https_are_allowed(url: str) -> None:
    assert validate_base_url(url).startswith(("http://", "https://"))


def test_http_client_disables_environment_proxy_and_redirects(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def request(self, *_args, **_kwargs):
            request = httpx.Request(
                "GET",
                BASE + "/api/v1/capabilities",
                headers=_kwargs["headers"],
            )
            return httpx.Response(
                200,
                request=request,
                headers={
                    "content-type": "application/json",
                    "x-caseloop-contract-version": "1.0",
                },
                json=success_for(request),
            )

    monkeypatch.setattr(httpx, "Client", FakeClient)
    client = PublicApiClient(RuntimeConfig(BASE, WORKSPACE, TOKEN))
    client.request("GET", "/api/v1/capabilities")

    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False


def test_signal_network_retry_reuses_exact_body_and_authority_headers() -> None:
    attempts: list[tuple[bytes, str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(
            (
                request.content,
                request.headers["idempotency-key"],
                request.headers["x-request-id"],
                request.headers["authorization"],
            )
        )
        if len(attempts) < 3:
            raise httpx.ConnectError("private upstream detail", request=request)
        return httpx.Response(
            201,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json=success_for(request),
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run(
        [
            "--api-url",
            BASE,
            "--workspace-id",
            WORKSPACE,
            "signal",
            "submit",
            "--source-id",
            SOURCE,
            "--summary",
            "Wrong tool",
            "--reporter-ref",
            "maintainer-01J0000000000001",
        ],
        env={"CASELOOP_PUBLIC_TOKEN": TOKEN},
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(handler),
        now=lambda: datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
        sleep=lambda _seconds: None,
    )

    assert exit_code == ExitFamily.OK
    assert len(attempts) == 3
    assert len({item[0] for item in attempts}) == 1
    assert len({item[1] for item in attempts}) == 1
    assert len({item[2] for item in attempts}) == 1
    assert {item[3] for item in attempts} == {f"Bearer {TOKEN}"}
    body = json.loads(attempts[0][0])
    assert body["source_event_id"].startswith("maintainer-report-")
    assert body["occurred_at"] == "2026-08-10T09:00:00Z"
    assert stderr.getvalue() == ""


@pytest.mark.parametrize(
    "missing",
    ["source-event-id", "occurred-at"],
)
def test_explicit_idempotency_key_requires_explicit_stable_event_fields(missing: str) -> None:
    argv = [
        "--api-url",
        BASE,
        "--workspace-id",
        WORKSPACE,
        "signal",
        "submit",
        "--source-id",
        SOURCE,
        "--summary",
        "Wrong tool",
        "--reporter-ref",
        "maintainer-01J0000000000001",
        "--idempotency-key",
        "stable-key-0001",
    ]
    if missing != "source-event-id":
        argv += ["--source-event-id", "maintainer-event-0001"]
    if missing != "occurred-at":
        argv += ["--occurred-at", "2026-08-10T09:00:00Z"]

    stderr = io.StringIO()
    exit_code = run(
        argv,
        env={"CASELOOP_PUBLIC_TOKEN": TOKEN},
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert exit_code == ExitFamily.INPUT
    assert json.loads(stderr.getvalue())["error"]["code"] == "STABLE_EVENT_FIELDS_REQUIRED"


def test_redirect_is_not_followed_and_server_body_or_token_is_not_echoed() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            307,
            headers={"location": f"https://attacker.example/?token={TOKEN}"},
            content=b"redirect details must stay private",
        )

    stderr = io.StringIO()
    exit_code = run(
        ["--api-url", BASE, "--workspace-id", WORKSPACE, "capabilities", "get"],
        env={"CASELOOP_PUBLIC_TOKEN": TOKEN},
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert len(requests) == 1
    error = stderr.getvalue()
    assert json.loads(error)["error"]["code"] == "REMOTE_REDIRECT_REFUSED"
    assert TOKEN not in error
    assert "redirect details" not in error


def test_success_response_that_reflects_credential_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json={"schema_version": "1.0", "unexpected": TOKEN},
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run(
        ["--api-url", BASE, "--workspace-id", WORKSPACE, "capabilities", "get"],
        env={"CASELOOP_PUBLIC_TOKEN": TOKEN},
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue())["error"]["code"] == "REMOTE_PROTOCOL_ERROR"
    assert TOKEN not in stderr.getvalue()


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": "1.0", "ok": True},
        {
            "schema_version": "1.0",
            "workspace_id": WORKSPACE,
            "workspace_resolved": True,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "Public error disguised as a success",
                "retryable": False,
                "retry_after_ms": None,
                "request_id": "req_01J0000000000001",
                "operation_id": None,
                "audit_ref": None,
                "audit_status": "NOT_APPLICABLE",
                "details": {},
                "help_url": None,
            },
        },
    ],
)
def test_arbitrary_200_or_public_error_200_cannot_become_capabilities_success(
    payload: dict[str, object]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json=payload,
        )

    client = PublicApiClient(
        RuntimeConfig(BASE, WORKSPACE, TOKEN), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(CliError) as caught:
        client.request(
            "GET", "/api/v1/capabilities", request_id="req_01J0000000000001"
        )
    assert caught.value.code == "REMOTE_PROTOCOL_ERROR"
    assert caught.value.exit_family == ExitFamily.PROTOCOL


@pytest.mark.parametrize("binding", ["workspace_id", "request_id"])
def test_success_envelope_must_bind_workspace_and_stable_request_id(binding: str) -> None:
    sample_request = httpx.Request(
        "GET",
        BASE + "/api/v1/capabilities",
        headers={
            "X-CaseLoop-Workspace-ID": WORKSPACE,
            "X-Request-ID": "req_01J0000000000001",
        },
    )
    payload = capabilities_success(sample_request)
    payload[binding] = (
        "ws_01J0000000000099"
        if binding == "workspace_id"
        else "req_01J0000000000099"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json=payload,
        )

    client = PublicApiClient(
        RuntimeConfig(BASE, WORKSPACE, TOKEN), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(CliError) as caught:
        client.request(
            "GET", "/api/v1/capabilities", request_id="req_01J0000000000001"
        )
    assert caught.value.code == "REMOTE_BINDING_INVALID"
    assert caught.value.exit_family == ExitFamily.PROTOCOL


@pytest.mark.parametrize(
    "drift",
    [
        "idempotency_key",
        "resource",
        "source_event_id",
        "no_trace",
        "request_fingerprint",
        "response_digest",
        "receipt_digest",
    ],
)
def test_signal_success_binds_exact_idempotency_resource_and_no_trace_chain(drift: str) -> None:
    body = _fixture("public-signal-submission.json")
    sample_request = httpx.Request(
        "POST",
        BASE + "/api/v1/signals",
        headers={
            "X-CaseLoop-Workspace-ID": WORKSPACE,
            "X-Request-ID": "req_01J0000000000002",
            "Idempotency-Key": "signal-submit-0001",
        },
        content=json.dumps(body).encode("utf-8"),
    )
    payload = signal_success(sample_request)
    if drift == "idempotency_key":
        payload["idempotency"]["receipt"]["idempotency_key"] = "different-key-0001"
    elif drift == "resource":
        payload["idempotency"]["receipt"]["resource"]["id"] = "sig_01J0000000000099"
    elif drift == "source_event_id":
        payload["signal"]["source_event_id"] = "different-event"
    elif drift == "no_trace":
        payload["evidence"]["agent_run_ref_id"] = "arr_01J0000000000001"
    else:
        payload["idempotency"]["receipt"][drift] = "sha256:" + "0" * 64

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json=payload,
        )

    client = PublicApiClient(
        RuntimeConfig(BASE, WORKSPACE, TOKEN), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(CliError) as caught:
        client.request(
            "POST",
            "/api/v1/signals",
            body=json.dumps(body).encode("utf-8"),
            idempotency_key="signal-submit-0001",
            request_id="req_01J0000000000002",
        )
    assert caught.value.code in {"REMOTE_PROTOCOL_ERROR", "REMOTE_BINDING_INVALID"}
    assert caught.value.exit_family == ExitFamily.PROTOCOL


def test_same_key_replay_accepts_original_response_request_binding_only() -> None:
    body = _fixture("public-signal-submission.json")
    raw_body = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    original: dict[str, object] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal original
        if original is None:
            original = signal_success(request)
            payload = original
        else:
            payload = copy.deepcopy(original)
            payload["idempotency"]["replayed"] = True
        return httpx.Response(
            201,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json=payload,
        )

    client = PublicApiClient(
        RuntimeConfig(BASE, WORKSPACE, TOKEN), transport=httpx.MockTransport(handler)
    )
    first = client.request(
        "POST",
        "/api/v1/signals",
        body=raw_body,
        idempotency_key="signal-submit-0001",
        request_id="req_01J0000000000001",
    )
    replay = client.request(
        "POST",
        "/api/v1/signals",
        body=raw_body,
        idempotency_key="signal-submit-0001",
        request_id="req_01J0000000000002",
    )

    assert first["request_id"] == replay["request_id"] == "req_01J0000000000001"
    assert first["idempotency"]["replayed"] is False
    assert replay["idempotency"]["replayed"] is True
    assert replay["idempotency"]["receipt"]["request_id"] == replay["request_id"]


def test_non_2xx_requires_complete_public_error_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json={"error": {"code": "TOKEN_INVALID"}},
        )

    client = PublicApiClient(
        RuntimeConfig(BASE, WORKSPACE, TOKEN), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(CliError) as caught:
        client.request(
            "GET", "/api/v1/capabilities", request_id="req_01J0000000000001"
        )
    assert caught.value.code == "REMOTE_PROTOCOL_ERROR"
    assert caught.value.exit_family == ExitFamily.PROTOCOL


@pytest.mark.parametrize(
    ("tamper", "recompute_digest"),
    [
        ("workspace_id", False),
        ("receipt_id", False),
        ("signal_id", False),
        ("signal_digest", False),
        ("failure_message_digest", False),
        ("collected_at", False),
        ("workspace_id", True),
        ("receipt_id", True),
        ("case_id", True),
        ("query", True),
        ("agent_run_ref", True),
        ("completeness", True),
        ("collection_mode", True),
    ],
)
def test_evidence_receipt_tampering_fails_self_hash_or_exact_binding(
    tamper: str, recompute_digest: bool
) -> None:
    receipt_id = "ter_01J0000000000001"
    request_id = "req_01J0000000000001"
    sample_request = httpx.Request(
        "GET",
        BASE + f"/api/v1/evidence/{receipt_id}",
        headers={
            "X-CaseLoop-Workspace-ID": WORKSPACE,
            "X-Request-ID": request_id,
        },
    )
    payload = evidence_success(sample_request, receipt_id)
    receipt = payload["data"]["receipt"]
    if tamper == "workspace_id":
        receipt["workspace_id"] = "ws_01J0000000000099"
    elif tamper == "receipt_id":
        receipt["receipt_id"] = "ter_01J0000000000099"
    elif tamper == "case_id":
        receipt["case_id"] = "case_01J0000000000001"
    elif tamper == "signal_id":
        receipt["signal_id"] = "sig_01J0000000000099"
    elif tamper == "signal_digest":
        receipt["signal_digest"] = "sha256:" + "9" * 64
    elif tamper == "failure_message_digest":
        receipt["failure"]["message_digest"] = "sha256:" + "8" * 64
    elif tamper == "collected_at":
        receipt["collected_at"] = "2026-08-10T09:00:59Z"
    elif tamper == "query":
        receipt["query"] = {
            "adapter_kind": "custom",
            "endpoint_origin": "https://trace.example",
            "source_version": "1",
            "requested_at": "2026-08-10T09:00:00Z",
            "window_start": "2026-08-10T08:59:00Z",
            "window_end": "2026-08-10T09:01:00Z",
            "filters_digest": "sha256:" + "7" * 64,
        }
    elif tamper == "agent_run_ref":
        receipt["agent_run_ref_id"] = "arr_01J0000000000001"
        receipt["agent_run_ref_digest"] = "sha256:" + "6" * 64
    elif tamper == "completeness":
        receipt["completeness"] = "PARTIAL"
    else:
        receipt["collection_mode"] = "SOURCE_QUERY"

    if recompute_digest:
        without_self_digest = copy.deepcopy(receipt)
        without_self_digest.pop("receipt_digest")
        receipt["receipt_digest"] = digest(without_self_digest)
        payload["data"]["receipt_digest"] = receipt["receipt_digest"]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json=payload,
        )

    client = PublicApiClient(
        RuntimeConfig(BASE, WORKSPACE, TOKEN), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(CliError) as caught:
        client.request(
            "GET", f"/api/v1/evidence/{receipt_id}", request_id=request_id
        )
    assert caught.value.code in {"REMOTE_BINDING_INVALID", "REMOTE_PROTOCOL_ERROR"}
    assert caught.value.exit_family == ExitFamily.PROTOCOL


def test_source_query_partial_evidence_with_exact_self_hash_is_accepted() -> None:
    receipt_id = "ter_01J0000000000001"
    request_id = "req_01J0000000000001"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json=source_query_evidence(request, receipt_id),
        )

    client = PublicApiClient(
        RuntimeConfig(BASE, WORKSPACE, TOKEN), transport=httpx.MockTransport(handler)
    )
    result = client.request(
        "GET", f"/api/v1/evidence/{receipt_id}", request_id=request_id
    )

    assert result["data"]["receipt"]["collection_mode"] == "SOURCE_QUERY"
    assert result["data"]["receipt"]["completeness"] == "PARTIAL"


@pytest.mark.parametrize(
    ("status", "server_code", "expected_exit"),
    [
        (401, "TOKEN_INVALID", ExitFamily.AUTH),
        (403, "SCOPE_FORBIDDEN", ExitFamily.AUTH),
        (404, "RESOURCE_NOT_FOUND", ExitFamily.NOT_FOUND),
        (409, "IDEMPOTENCY_CONFLICT", ExitFamily.CONFLICT),
        (422, "VALIDATION_FAILED", ExitFamily.INPUT),
        (429, "RATE_LIMITED", ExitFamily.TEMPORARY),
        (503, "DEPENDENCY_UNAVAILABLE", ExitFamily.TEMPORARY),
        (500, "INTERNAL_ERROR", ExitFamily.REMOTE),
    ],
)
def test_public_error_status_maps_to_stable_exit_family(
    status: int, server_code: str, expected_exit: ExitFamily
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        retryable = status in {429, 503}
        envelope = {
            "schema_version": "1.0",
            "workspace_id": None,
            "workspace_resolved": False,
            "error": {
                "code": server_code,
                "message": "This text is never parsed",
                "retryable": retryable,
                "request_id": request.headers["x-request-id"],
                "operation_id": None,
                "audit_ref": None,
                "audit_status": "NOT_APPLICABLE",
                "details": {},
                "help_url": None,
                "retry_after_ms": 1000 if retryable else None,
            },
        }
        return httpx.Response(
            status,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "1.0",
            },
            json=envelope,
        )

    stderr = io.StringIO()
    exit_code = run(
        ["--api-url", BASE, "--workspace-id", WORKSPACE, "capabilities", "get"],
        env={"CASELOOP_PUBLIC_TOKEN": TOKEN},
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )

    assert exit_code == expected_exit
    assert json.loads(stderr.getvalue())["error"]["code"] == server_code


def test_transport_retry_is_bounded_and_private_exception_is_sanitized() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError(f"dial failed with {TOKEN}", request=request)

    stderr = io.StringIO()
    exit_code = run(
        ["--api-url", BASE, "--workspace-id", WORKSPACE, "capabilities", "get"],
        env={"CASELOOP_PUBLIC_TOKEN": TOKEN},
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
    )

    assert calls == 3
    assert exit_code == ExitFamily.TEMPORARY
    assert json.loads(stderr.getvalue())["error"]["code"] == "NETWORK_UNAVAILABLE"
    assert TOKEN not in stderr.getvalue()
