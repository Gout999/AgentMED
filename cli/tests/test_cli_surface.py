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
