from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import httpx
import pytest

from caseloop_cli.errors import ExitFamily
from caseloop_cli.main import build_parser, run
from .wire_samples import success_for


BASE = "http://127.0.0.1:8090"
WORKSPACE = "ws_01J0000000000001"
SOURCE = "src_01J0000000000001"
TOKEN = "public-test-token-never-print"


def _globals() -> list[str]:
    return ["--api-url", BASE, "--workspace-id", WORKSPACE]


def _env() -> dict[str, str]:
    return {"CASELOOP_PUBLIC_TOKEN": TOKEN}


def _response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        201 if request.url.path == "/api/v1/signals" else 200,
        headers={
            "content-type": "application/json",
            "x-caseloop-contract-version": "1.0",
        },
        json=success_for(request),
    )


def test_help_exposes_only_frozen_stage1a_cli_commands() -> None:
    help_text = build_parser().format_help()
    assert all(name in help_text for name in ("capabilities", "signal", "report", "case", "evidence"))
    assert all(name not in help_text for name in ("project", "source", "investigation", "release", "skill"))


@pytest.mark.parametrize(
    ("argv", "expected_path", "expected_query"),
    [
        (["capabilities", "get"], "/api/v1/capabilities", ""),
        (["case", "get", "case_01J0000000000001"], "/api/v1/cases/case_01J0000000000001", ""),
        (
            ["case", "timeline", "case_01J0000000000001", "--limit", "17", "--cursor", "cur_01J0000000000001"],
            "/api/v1/cases/case_01J0000000000001/timeline",
            "limit=17&cursor=cur_01J0000000000001",
        ),
        (["evidence", "get", "ter_01J0000000000001"], "/api/v1/evidence/ter_01J0000000000001", ""),
    ],
)
def test_read_commands_use_exact_frozen_routes_and_machine_json(
    argv: list[str], expected_path: str, expected_query: str
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _response(request)

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run(
        [*_globals(), *argv],
        env=_env(),
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == expected_path
    assert request.url.query.decode() == expected_query
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["x-caseloop-workspace-id"] == WORKSPACE
    assert request.headers["x-caseloop-contract-version"] == "1.0"
    assert request.headers["x-request-id"].startswith("req_")
    assert json.loads(stdout.getvalue())["request_id"] == request.headers["x-request-id"]
    assert stderr.getvalue() == ""
    assert TOKEN not in stdout.getvalue()


def test_unknown_or_unfrozen_command_is_stable_input_error() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run([*_globals(), "project", "init"], env=_env(), stdout=stdout, stderr=stderr)

    assert exit_code == ExitFamily.INPUT
    assert stdout.getvalue() == ""
    payload = json.loads(stderr.getvalue())
    assert payload == {
        "error": {"code": "CLI_USAGE_INVALID", "details": {}, "retryable": False},
        "schema_version": "1.0",
    }


def _run_case_v2(argv: list[str]) -> tuple[int, list[httpx.Request], str, str]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201 if request.method == "POST" else 200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
            },
            json=success_for(request),
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run(
        ["--api-version", "2", *_globals(), *argv],
        env=_env(),
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )
    return exit_code, requests, stdout.getvalue(), stderr.getvalue()


CASE_DIGEST = "sha256:" + "c" * 64
PROPOSE_JSON = (
    '{"acceptance_source":{"kind":"github_issue"},"expected_behavior":{"summary":"x"},'
    '"applicable_workload_profile":{"name":"w"},"applicable_deployment_profile":{"name":"d"}}'
)


def test_case_bind_application_uses_exact_v2_route() -> None:
    exit_code, requests, stdout, _stderr = _run_case_v2(
        [
            "case",
            "bind-application",
            "case_01J0000000000001",
            "--application-id",
            "app_01J0000000000001",
            "--environment-id",
            "env_01J0000000000001",
            "--case-digest",
            CASE_DIGEST,
            "--idempotency-key",
            "bind-cli-00000001",
        ]
    )
    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v2/cases/case_01J0000000000001:bind-application"
    assert request.headers["x-caseloop-contract-version"] == "2.0"
    body = json.loads(request.content)
    assert body["case_id"] == "case_01J0000000000001"
    assert body["case_digest"] == CASE_DIGEST
    assert body["application_id"] == "app_01J0000000000001"
    assert json.loads(stdout)["application_case_binding"]["application_case_binding_id"].startswith("acb_")


def test_case_application_binding_get_uses_exact_v2_route() -> None:
    exit_code, requests, stdout, _stderr = _run_case_v2(
        [
            "case",
            "application-binding",
            "get",
            "case_01J0000000000001",
            "--case-revision",
            "1",
            "--case-digest",
            CASE_DIGEST,
        ]
    )
    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v2/cases/case_01J0000000000001/application-binding"
    assert request.url.params["case_digest"] == CASE_DIGEST
    assert request.url.params["case_revision"] == "1"
    assert (
        json.loads(stdout)["application_case_binding"]["exact_case_binding"]["case_id"]
        == "case_01J0000000000001"
    )


def test_case_acceptance_criteria_propose_uses_exact_v2_route() -> None:
    exit_code, requests, stdout, _stderr = _run_case_v2(
        [
            "case",
            "acceptance-criteria",
            "propose",
            "case_01J0000000000001",
            "--case-digest",
            CASE_DIGEST,
            "--acceptance-json",
            PROPOSE_JSON,
        ]
    )
    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v2/cases/case_01J0000000000001:propose-acceptance-criteria"
    body = json.loads(request.content)
    assert body["expected_behavior"] == {"summary": "x"}
    assert json.loads(stdout)["acceptance_criteria_revision"]["confirmation_status"] == "PROPOSED"


def test_case_acceptance_criteria_get_uses_exact_v2_route() -> None:
    exit_code, requests, stdout, _stderr = _run_case_v2(
        ["case", "acceptance-criteria", "get", "case_01J0000000000001"]
    )
    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v2/cases/case_01J0000000000001/acceptance-criteria"
    assert request.url.params["case_revision"] == "1"
    payload = json.loads(stdout)
    assert payload["case_readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"
    assert payload["exact_case_binding"]["case_digest"] == CASE_DIGEST


def test_case_acceptance_criteria_confirm_uses_exact_v2_route() -> None:
    exit_code, requests, stdout, _stderr = _run_case_v2(
        [
            "case",
            "acceptance-criteria",
            "confirm",
            "acr_01J0000000000001",
            "--proposed-revision-digest",
            "sha256:" + "e" * 64,
        ]
    )
    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v2/acceptance-criteria/acr_01J0000000000001:confirm"
    body = json.loads(request.content)
    assert body["exact_proposed_revision_binding"]["id"] == "acr_01J0000000000001"


def test_case_v2_actions_require_explicit_api_version_2() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run(
        [
            *_globals(),
            "case",
            "bind-application",
            "case_01J0000000000001",
            "--application-id",
            "app_01J0000000000001",
            "--environment-id",
            "env_01J0000000000001",
            "--case-digest",
            CASE_DIGEST,
        ],
        env=_env(),
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(_response),
    )
    assert exit_code == ExitFamily.INPUT
    assert json.loads(stderr.getvalue())["error"]["code"] == "API_VERSION_REQUIRED"


def test_case_v1_actions_reject_api_version_2() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run(
        ["--api-version", "2", *_globals(), "case", "get", "case_01J0000000000001"],
        env=_env(),
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(_response),
    )
    assert exit_code == ExitFamily.INPUT
    assert json.loads(stderr.getvalue())["error"]["code"] == "API_MAJOR_MISMATCH"


def test_case_from_issue_composes_canonical_intents_only(tmp_path) -> None:
    """``caseloop case from-issue`` drives issue_snapshot → signal_submit →
    case binding → acceptance draft and never auto-confirms."""
    import json as _json

    snapshot = tmp_path / "issue-1466.json"
    snapshot.write_text(
        _json.dumps(
            {
                "number": 1466,
                "title": "BUG: schema_dsl raises IndexError",
                "body": "minimal repro: schema_dsl(':description')",
                "state": "open",
                "html_url": "https://github.com/simonw/llm/issues/1466",
                "updated_at": "2026-07-30T22:33:06Z",
            }
        ),
        encoding="utf-8",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        contract = "2.0" if request.url.path.startswith("/api/v2") else "1.0"
        return httpx.Response(
            201 if request.method == "POST" else 200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": contract,
            },
            json=success_for(request),
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    env = {**_env(), "CASELOOP_CACHE_DIR": str(tmp_path / "cache")}
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "case",
            "from-issue",
            "https://github.com/simonw/llm/issues/1466",
            "--application-id",
            "app_01J0000000000001",
            "--environment-id",
            "env_01J0000000000001",
            "--snapshot-file",
            str(snapshot),
            "--source-id",
            SOURCE,
            "--reporter-ref",
            "prn_01J0000000000001",
        ],
        env=env,
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )
    assert exit_code == ExitFamily.OK
    paths = [request.url.path for request in requests]
    assert paths == [
        "/api/v1/signals",
        "/api/v2/cases/case_stage0001/acceptance-criteria",
        "/api/v2/cases/case_stage0001:bind-application",
        "/api/v2/cases/case_stage0001:propose-acceptance-criteria",
    ]
    payload = _json.loads(stdout.getvalue())
    assert payload["case_readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"
    assert payload["next_action"]["code"] == "CONFIRM_ACCEPTANCE_CRITERIA"
    assert "auto-confirmed" in stderr.getvalue().lower()
    signal_body = _json.loads(requests[0].content)
    assert signal_body["content"]["attachments"][0]["media_type"] == "application/json"
    assert signal_body["source_event_id"] == "github-issue:simonw:llm:1466"
    bind_body = _json.loads(requests[2].content)
    assert bind_body["issue_snapshot"]["external_repo"] == "simonw/llm"
    assert bind_body["issue_snapshot"]["snapshot_payload"]["title"].startswith("BUG:")
    propose_body = _json.loads(requests[3].content)
    assert propose_body["expected_behavior"]["untrusted"] is True


def test_from_issue_rejects_malformed_url() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "case",
            "from-issue",
            "https://example.com/not-github",
            "--application-id",
            "app_01J0000000000001",
            "--environment-id",
            "env_01J0000000000001",
        ],
        env=_env(),
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(_response),
    )
    assert exit_code == ExitFamily.INPUT
    assert json.loads(stderr.getvalue())["error"]["code"] == "ISSUE_URL_INVALID"


def test_token_can_never_be_supplied_in_argv() -> None:
    stderr = io.StringIO()
    exit_code = run(
        [*_globals(), "--token", TOKEN, "capabilities", "get"],
        env={},
        stdout=io.StringIO(),
        stderr=stderr,
    )
    assert exit_code == ExitFamily.INPUT
    assert TOKEN not in stderr.getvalue()


def test_signal_submit_and_report_alias_are_the_same_no_trace_maintainer_wire() -> None:
    bodies: list[bytes] = []
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        paths.append(request.url.path)
        return _response(request)

    fixed_now = lambda: datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    common = [
        "--source-id",
        SOURCE,
        "--summary",
        "Agent chose the wrong tool",
        "--body",
        "No trace is available",
        "--reporter-ref",
        "maintainer-01J0000000000001",
        "--privacy",
        "INTERNAL",
    ]
    transport = httpx.MockTransport(handler)

    first = run(
        [*_globals(), "signal", "submit", *common],
        env=_env(),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        transport=transport,
        now=fixed_now,
    )
    second = run(
        [*_globals(), "report", *common],
        env=_env(),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        transport=transport,
        now=fixed_now,
    )

    assert first == second == ExitFamily.OK
    assert paths == ["/api/v1/signals", "/api/v1/signals"]
    for raw in bodies:
        body = json.loads(raw)
        assert body["schema_version"] == "1.0"
        assert body["signal_kind"] == "maintainer_report"
        assert body["reporter"] == {
            "kind": "maintainer",
            "source_subject_ref": "maintainer-01J0000000000001",
        }
        assert body["run_locator"] is None
        assert body["privacy_classification"] == "INTERNAL"
        assert body["occurred_at"] == "2026-08-10T09:00:00Z"
        assert body["content"] == {
            "attachments": [],
            "body": "No trace is available",
            "summary": "Agent chose the wrong tool",
        }
        assert "workspace_id" not in body
        assert "trace_id" not in raw.decode()


def test_signal_cli_does_not_accept_confidential_or_trace_options() -> None:
    for argv in (
        [*_globals(), "signal", "submit", "--privacy", "CONFIDENTIAL"],
        [*_globals(), "signal", "submit", "--trace-id", "trace-1"],
    ):
        stderr = io.StringIO()
        exit_code = run(argv, env=_env(), stdout=io.StringIO(), stderr=stderr)
        assert exit_code == ExitFamily.INPUT
        assert json.loads(stderr.getvalue())["error"]["code"] == "CLI_USAGE_INVALID"
