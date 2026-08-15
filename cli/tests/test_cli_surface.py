from __future__ import annotations

import io
import json
import copy
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import pytest

from agentmed_cli.errors import ExitFamily
from agentmed_cli.main import build_parser, run
from .wire_samples import digest, success_for


BASE = "http://127.0.0.1:8090"
WORKSPACE = "ws_01J0000000000001"
SOURCE = "src_01J0000000000001"
TOKEN = "public-test-token-never-print"


def _globals() -> list[str]:
    return ["--api-url", BASE, "--workspace-id", WORKSPACE]


def _env() -> dict[str, str]:
    return {"AGENTMED_PUBLIC_TOKEN": TOKEN}


def _response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        201 if request.url.path == "/api/v1/signals" else 200,
        headers={
            "content-type": "application/json",
            "x-agentmed-contract-version": "1.0",
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
    assert request.headers["x-agentmed-workspace-id"] == WORKSPACE
    assert request.headers["x-agentmed-contract-version"] == "1.0"
    assert request.headers["x-request-id"].startswith("req_")
    assert json.loads(stdout.getvalue())["request_id"] == request.headers["x-request-id"]
    assert stderr.getvalue() == ""
    assert TOKEN not in stdout.getvalue()


def test_capabilities_supports_explicit_v2_without_changing_v1_default() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-agentmed-contract-version": "2.0",
            },
            json=success_for(request),
        )

    stdout = io.StringIO()
    exit_code = run(
        ["--api-version", "2", *_globals(), "capabilities", "get"],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert [request.url.path for request in requests] == ["/api/v2/capabilities"]
    request = requests[0]
    assert request.headers["x-agentmed-contract-version"] == "2.0"
    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == "2.0"
    assert payload["data"]["api_major"] == 2
    assert payload["data"]["contract_version"] == "2.0"
    assert payload["data"]["disabled_intents"] == []


def test_capabilities_v2_rejects_a_major_one_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = success_for(request)
        payload["data"]["api_major"] = 1
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-agentmed-contract-version": "2.0",
            },
            json=payload,
        )

    stderr = io.StringIO()
    exit_code = run(
        ["--api-version", "2", *_globals(), "capabilities", "get"],
        env=_env(),
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert json.loads(stderr.getvalue())["error"]["code"] == "REMOTE_PROTOCOL_ERROR"


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


def _run_case_v2(
    argv: list[str],
    *,
    payload_mutator: Callable[[httpx.Request, dict[str, Any]], None] | None = None,
) -> tuple[int, list[httpx.Request], str, str]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = success_for(request)
        if payload_mutator is not None:
            payload_mutator(request, payload)
        return httpx.Response(
            201 if request.method == "POST" else 200,
            headers={
                "content-type": "application/json",
                "x-agentmed-contract-version": "2.0",
            },
            json=payload,
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


def _refresh_v5_receipt(payload: dict[str, Any]) -> None:
    receipt = payload["idempotency"]["receipt"]
    response_without_idempotency = copy.deepcopy(payload)
    response_without_idempotency.pop("idempotency")
    receipt["response_digest"] = digest(response_without_idempotency)
    receipt_without_digest = copy.deepcopy(receipt)
    receipt_without_digest.pop("receipt_digest")
    receipt["receipt_digest"] = digest(receipt_without_digest)


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
    assert request.headers["x-agentmed-contract-version"] == "2.0"
    body = json.loads(request.content)
    assert body["case_id"] == "case_01J0000000000001"
    assert body["case_digest"] == CASE_DIGEST
    assert body["application_id"] == "app_01J0000000000001"
    assert body["declared_system_version_set_binding_or_unknown"] == {
        "kind": "UNKNOWN",
        "reason": "NOT_DECLARED",
    }
    assert json.loads(stdout)["application_case_binding"]["application_case_binding_id"].startswith("acb_")


def test_case_bind_application_loads_manual_snapshot_and_exact_version(tmp_path) -> None:
    snapshot_file = tmp_path / "manual-source.json"
    snapshot_file.write_text(
        json.dumps(
            {
                "source_kind": "manual",
                "source_url": None,
                "external_repo": None,
                "external_issue_number": None,
                "snapshot_payload": {
                    "title": "Maintainer observed a wrong tool call",
                    "body": "No GitHub issue exists.",
                },
                "edited_flag": True,
                "deleted_flag": False,
                "fetched_at": "2026-08-11T12:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    version_digest = "sha256:" + "d" * 64
    exit_code, requests, _stdout, _stderr = _run_case_v2(
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
            "--system-version-set-id",
            "vset_01J0000000000001",
            "--issue-snapshot-file",
            str(snapshot_file),
        ]
    )
    assert exit_code == ExitFamily.OK
    assert [request.url.path for request in requests] == [
        "/api/v2/system-versions/vset_01J0000000000001",
        "/api/v2/cases/case_01J0000000000001:bind-application",
    ]
    body = json.loads(requests[1].content)
    assert body["declared_system_version_set_binding_or_unknown"] == {
        "kind": "SYSTEM_VERSION_SET",
        "id": "vset_01J0000000000001",
        "revision": 1,
        "digest": version_digest,
    }
    assert body["issue_snapshot"]["source_kind"] == "manual"
    assert body["issue_snapshot"]["source_url"] is None
    assert body["issue_snapshot"]["external_repo"] is None
    assert body["issue_snapshot"]["external_issue_number"] is None
    assert body["issue_snapshot"]["snapshot_payload"]["edited_flag"] is True


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


@pytest.mark.parametrize(
    ("field", "malicious_value"),
    [
        ("case_revision", 2),
        ("case_digest", "sha256:" + "d" * 64),
    ],
)
def test_application_binding_get_rejects_query_binding_substitution(
    field: str, malicious_value: object
) -> None:
    def mutate(_request: httpx.Request, payload: dict[str, Any]) -> None:
        payload["application_case_binding"]["exact_case_binding"][field] = (
            malicious_value
        )

    exit_code, _requests, _stdout, stderr = _run_case_v2(
        [
            "case",
            "application-binding",
            "get",
            "case_01J0000000000001",
            "--case-revision",
            "1",
            "--case-digest",
            CASE_DIGEST,
        ],
        payload_mutator=mutate,
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert json.loads(stderr)["error"]["code"] == "REMOTE_BINDING_INVALID"


def test_application_binding_rejects_unmodeled_exact_case_fields() -> None:
    def mutate(_request: httpx.Request, payload: dict[str, Any]) -> None:
        payload["application_case_binding"]["exact_case_binding"][
            "mutable_label"
        ] = "forged"

    exit_code, _requests, _stdout, stderr = _run_case_v2(
        [
            "case",
            "application-binding",
            "get",
            "case_01J0000000000001",
            "--case-digest",
            CASE_DIGEST,
        ],
        payload_mutator=mutate,
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert json.loads(stderr)["error"]["code"] == "REMOTE_PROTOCOL_ERROR"


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


@pytest.mark.parametrize("substitution", ["top-case", "nested-digest"])
def test_acceptance_get_rejects_exact_case_binding_substitution(
    substitution: str,
) -> None:
    def mutate(_request: httpx.Request, payload: dict[str, Any]) -> None:
        if substitution == "top-case":
            payload["exact_case_binding"]["case_id"] = "case_01J0000000000002"
            return
        revision = payload["revisions"][0]
        replacement = "sha256:" + "d" * 64
        revision["exact_case_binding"]["case_digest"] = replacement
        revision["resolution_contract_binding_status"]["exact_case_binding"][
            "case_digest"
        ] = replacement

    exit_code, _requests, _stdout, stderr = _run_case_v2(
        ["case", "acceptance-criteria", "get", "case_01J0000000000001"],
        payload_mutator=mutate,
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert json.loads(stderr)["error"]["code"] == "REMOTE_BINDING_INVALID"


def test_case_acceptance_criteria_confirm_uses_exact_v2_route() -> None:
    exit_code, requests, stdout, _stderr = _run_case_v2(
        [
            "case",
            "acceptance-criteria",
            "confirm",
            "acr_01J0000000000001",
            "--case-id",
            "case_01J0000000000001",
            "--proposed-revision-digest",
            "sha256:" + "e" * 64,
        ]
    )
    assert exit_code == ExitFamily.OK
    assert [request.url.path for request in requests] == [
        "/api/v2/cases/case_01J0000000000001/acceptance-criteria",
        "/api/v2/acceptance-criteria/acr_01J0000000000001:confirm",
    ]
    request = requests[1]
    assert request.method == "POST"
    assert request.url.path == "/api/v2/acceptance-criteria/acr_01J0000000000001:confirm"
    body = json.loads(request.content)
    assert body["exact_proposed_revision_binding"]["id"] == "acr_01J0000000000001"
    assert body["exact_proposed_revision_binding"]["revision"] == 1
    assert body["exact_proposed_revision_binding"]["digest"] == "sha256:" + "e" * 64
    confirmed = json.loads(stdout)["acceptance_criteria_revision"]
    assert confirmed["confirmation_status"] == "CONFIRMED"
    assert confirmed["resolution_contract_binding_status"]["status"] == (
        "PENDING_MATERIALIZATION"
    )
    assert confirmed["reauthentication_credential_binding"]["kind"] == (
        "PUBLIC_CREDENTIAL"
    )


@pytest.mark.parametrize("substitution", ["proposed-state", "wrong-previous"])
def test_acceptance_confirm_rejects_semantically_substituted_response(
    substitution: str,
) -> None:
    def mutate(request: httpx.Request, payload: dict[str, Any]) -> None:
        if not request.url.path.endswith(":confirm"):
            return
        revision = payload["acceptance_criteria_revision"]
        if substitution == "proposed-state":
            revision["confirmation_status"] = "PROPOSED"
            revision["confirmer_principal"] = None
            revision["confirmed_at"] = None
            revision["exact_previous_proposed_revision_binding"] = None
            revision["reauthentication_credential_binding"] = None
        else:
            revision["exact_previous_proposed_revision_binding"]["digest"] = (
                "sha256:" + "f" * 64
            )
        _refresh_v5_receipt(payload)

    exit_code, _requests, _stdout, stderr = _run_case_v2(
        [
            "case",
            "acceptance-criteria",
            "confirm",
            "acr_01J0000000000001",
            "--case-id",
            "case_01J0000000000001",
            "--proposed-revision-digest",
            "sha256:" + "e" * 64,
        ],
        payload_mutator=mutate,
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert json.loads(stderr)["error"]["code"] == "REMOTE_BINDING_INVALID"


@pytest.mark.parametrize(
    "substitution",
    ["mixed-state", "missing-confirmed-at", "non-public-credential"],
)
def test_acceptance_confirm_rejects_non_closed_wire_state(substitution: str) -> None:
    def mutate(request: httpx.Request, payload: dict[str, Any]) -> None:
        if not request.url.path.endswith(":confirm"):
            return
        revision = payload["acceptance_criteria_revision"]
        if substitution == "mixed-state":
            revision["confirmation_status"] = "PROPOSED"
        elif substitution == "missing-confirmed-at":
            revision.pop("confirmed_at")
        else:
            revision["reauthentication_credential_binding"]["kind"] = "SESSION"

    exit_code, _requests, _stdout, stderr = _run_case_v2(
        [
            "case",
            "acceptance-criteria",
            "confirm",
            "acr_01J0000000000001",
            "--case-id",
            "case_01J0000000000001",
            "--proposed-revision-digest",
            "sha256:" + "e" * 64,
        ],
        payload_mutator=mutate,
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert json.loads(stderr)["error"]["code"] == "REMOTE_PROTOCOL_ERROR"


def test_case_acceptance_confirm_rejects_stale_digest_before_write() -> None:
    exit_code, requests, _stdout, stderr = _run_case_v2(
        [
            "case",
            "acceptance-criteria",
            "confirm",
            "acr_01J0000000000001",
            "--case-id",
            "case_01J0000000000001",
            "--proposed-revision-digest",
            "sha256:" + "f" * 64,
        ]
    )
    assert exit_code == ExitFamily.INPUT
    assert [request.url.path for request in requests] == [
        "/api/v2/cases/case_01J0000000000001/acceptance-criteria"
    ]
    assert json.loads(stderr)["error"]["code"] == (
        "PROPOSED_REVISION_BINDING_MISMATCH"
    )


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
    """``agentmed case from-issue`` drives issue_snapshot → signal_submit →
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
                "edited_flag": None,
                "deleted_flag": None,
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
                "x-agentmed-contract-version": contract,
            },
            json=success_for(request),
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    env = {**_env(), "AGENTMED_CACHE_DIR": str(tmp_path / "cache")}
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
            "--system-version-set-id",
            "vset_01J0000000000001",
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
        "/api/v2/applications/app_01J0000000000001",
        "/api/v2/environments/env_01J0000000000001",
        "/api/v2/system-versions/vset_01J0000000000001",
        "/api/v1/signals",
        "/api/v2/cases/case_stage0001/acceptance-criteria",
        "/api/v2/cases/case_stage0001:bind-application",
        "/api/v2/cases/case_stage0001:propose-acceptance-criteria",
    ]
    payload = _json.loads(stdout.getvalue())
    assert payload["case_readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"
    assert payload["next_action"]["code"] == "CONFIRM_ACCEPTANCE_CRITERIA"
    assert "--case-id case_stage0001" in payload["next_action"]["command"]
    assert "auto-confirmed" in stderr.getvalue().lower()
    assert "non-executable" in stderr.getvalue().lower()
    signal_body = _json.loads(requests[3].content)
    assert signal_body["content"]["attachments"][0]["media_type"] == "application/json"
    assert signal_body["project_id"] == "proj_01J0000000000001"
    assert signal_body["environment_id"] == "env_01J0000000000001"
    assert signal_body["source_event_id"].startswith("github-issue:simonw:llm:1466:")
    assert len(signal_body["source_event_version"]) == 64
    assert signal_body["source_event_id"].endswith(signal_body["source_event_version"])
    bind_body = _json.loads(requests[5].content)
    assert bind_body["issue_snapshot"]["external_repo"] == "simonw/llm"
    assert bind_body["issue_snapshot"]["snapshot_payload"]["title"].startswith("BUG:")
    assert bind_body["issue_snapshot"]["snapshot_payload"]["edited_flag"] is False
    assert bind_body["issue_snapshot"]["snapshot_payload"]["deleted_flag"] is False
    assert bind_body["declared_system_version_set_binding_or_unknown"] == {
        "kind": "SYSTEM_VERSION_SET",
        "id": "vset_01J0000000000001",
        "revision": 1,
        "digest": "sha256:" + "d" * 64,
    }
    propose_body = _json.loads(requests[6].content)
    assert propose_body["expected_behavior"]["untrusted"] is True


def test_from_issue_snapshot_version_controls_retry_and_edit_idempotency(tmp_path) -> None:
    snapshot_file = tmp_path / "issue-versioned.json"
    snapshot_payload = {
        "number": 1466,
        "title": "BUG: schema_dsl raises IndexError",
        "body": "first immutable body",
        "state": "open",
        "html_url": "https://github.com/simonw/llm/issues/1466",
        "created_at": "2026-07-30T22:00:00Z",
        "updated_at": "2026-07-30T22:00:00Z",
    }
    snapshot_file.write_text(json.dumps(snapshot_payload), encoding="utf-8")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201 if request.method == "POST" else 200,
            headers={
                "content-type": "application/json",
                "x-agentmed-contract-version": (
                    "2.0" if request.url.path.startswith("/api/v2") else "1.0"
                ),
            },
            json=success_for(request),
        )

    def invoke(*, refresh: bool = False) -> dict[str, object]:
        argv = [
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
            "--declared-version-unknown",
            "--snapshot-file",
            str(snapshot_file),
            "--source-id",
            SOURCE,
            "--reporter-ref",
            "prn_01J0000000000001",
        ]
        if refresh:
            argv.append("--refresh")
        stdout = io.StringIO()
        exit_code = run(
            argv,
            env={**_env(), "AGENTMED_CACHE_DIR": str(tmp_path / "cache")},
            stdout=stdout,
            stderr=io.StringIO(),
            transport=httpx.MockTransport(handler),
        )
        assert exit_code == ExitFamily.OK
        return json.loads(stdout.getvalue())

    first = invoke()
    replay = invoke()
    snapshot_payload["body"] = "edited immutable body"
    snapshot_payload["updated_at"] = "2026-07-30T23:00:00Z"
    snapshot_file.write_text(json.dumps(snapshot_payload), encoding="utf-8")
    edited = invoke(refresh=True)

    signal_requests = [
        request for request in requests if request.url.path == "/api/v1/signals"
    ]
    bind_requests = [
        request for request in requests if request.url.path.endswith(":bind-application")
    ]
    propose_requests = [
        request
        for request in requests
        if request.url.path.endswith(":propose-acceptance-criteria")
    ]
    first_signal, replay_signal, edited_signal = [
        json.loads(request.content) for request in signal_requests
    ]
    assert first["source_event_version"] == replay["source_event_version"]
    assert first_signal["source_event_id"] == replay_signal["source_event_id"]
    assert (
        signal_requests[0].headers["idempotency-key"]
        == signal_requests[1].headers["idempotency-key"]
    )
    assert edited["source_event_version"] != first["source_event_version"]
    assert edited_signal["source_event_id"] != first_signal["source_event_id"]
    assert (
        signal_requests[2].headers["idempotency-key"]
        != signal_requests[0].headers["idempotency-key"]
    )
    assert (
        bind_requests[2].headers["x-agentmed-idempotency-key"]
        != bind_requests[0].headers["x-agentmed-idempotency-key"]
    )
    assert (
        propose_requests[2].headers["x-agentmed-idempotency-key"]
        != propose_requests[0].headers["x-agentmed-idempotency-key"]
    )
    edited_binding = json.loads(bind_requests[2].content)
    assert edited_binding["issue_snapshot"]["edited_flag"] is True
    assert edited_binding["declared_system_version_set_binding_or_unknown"] == {
        "kind": "UNKNOWN",
        "reason": "NOT_DECLARED",
    }


def test_from_issue_application_lookup_fails_before_signal_write(tmp_path) -> None:
    snapshot_file = tmp_path / "issue.json"
    snapshot_file.write_text(
        json.dumps(
            {
                "number": 1466,
                "title": "Scoped issue",
                "body": "data only",
                "state": "open",
                "html_url": "https://github.com/simonw/llm/issues/1466",
                "created_at": "2026-07-30T22:00:00Z",
                "updated_at": "2026-07-30T22:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-agentmed-contract-version": "2.0",
            },
            json={"schema_version": "2.0"},
        )

    stderr = io.StringIO()
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
            str(snapshot_file),
            "--source-id",
            SOURCE,
            "--reporter-ref",
            "prn_01J0000000000001",
        ],
        env=_env(),
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )
    assert exit_code == ExitFamily.PROTOCOL
    assert [request.url.path for request in requests] == [
        "/api/v2/applications/app_01J0000000000001"
    ]
    assert json.loads(stderr.getvalue())["error"]["code"] == "REMOTE_PROTOCOL_ERROR"


def test_from_issue_environment_mismatch_fails_before_signal_write(tmp_path) -> None:
    snapshot_file = tmp_path / "issue.json"
    snapshot_file.write_text(
        json.dumps(
            {
                "number": 1466,
                "title": "Scoped issue",
                "body": "data only",
                "state": "open",
                "html_url": "https://github.com/simonw/llm/issues/1466",
                "created_at": "2026-07-30T22:00:00Z",
                "updated_at": "2026-07-30T22:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = success_for(request)
        if request.url.path.startswith("/api/v2/environments/"):
            payload["environment"]["application_id"] = "app_01J0000000000002"
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-agentmed-contract-version": "2.0",
            },
            json=payload,
        )

    stderr = io.StringIO()
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
            str(snapshot_file),
            "--source-id",
            SOURCE,
            "--reporter-ref",
            "prn_01J0000000000001",
        ],
        env=_env(),
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )
    assert exit_code == ExitFamily.PROTOCOL
    assert [request.url.path for request in requests] == [
        "/api/v2/applications/app_01J0000000000001",
        "/api/v2/environments/env_01J0000000000001",
    ]
    assert json.loads(stderr.getvalue())["error"]["code"] == "REMOTE_BINDING_INVALID"


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


def test_from_issue_rejects_snapshot_bound_to_another_issue(tmp_path) -> None:
    snapshot_file = tmp_path / "wrong-issue.json"
    snapshot_file.write_text(
        json.dumps(
            {
                "number": 1467,
                "title": "A different issue",
                "body": "must not be relabelled as issue 1466",
                "state": "open",
                "html_url": "https://github.com/simonw/llm/issues/1467",
                "created_at": "2026-07-30T22:00:00Z",
                "updated_at": "2026-07-30T22:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise AssertionError("identity mismatch must fail before a control-plane call")

    stderr = io.StringIO()
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
            str(snapshot_file),
        ],
        env=_env(),
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )
    assert exit_code == ExitFamily.INPUT
    assert requests == []
    assert (
        json.loads(stderr.getvalue())["error"]["code"]
        == "ISSUE_SNAPSHOT_IDENTITY_MISMATCH"
    )


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
