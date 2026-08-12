from __future__ import annotations

import io
import copy
import json
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from caseloop_cli.errors import ExitFamily
from caseloop_cli._generated.public_v2 import (
    ApplicationGetResponse,
    ComponentGetResponse,
    EnvironmentGetResponse,
)
from caseloop_cli.client import PublicApiClient, RuntimeConfig
from caseloop_cli.errors import CliError
from caseloop_cli._generated.manifest_v2 import (
    ExactSlotVersionSetBinding as CliExactSlotVersionSetBinding,
    ExactV4EvidenceBinding as CliExactV4EvidenceBinding,
    ExactV5EvidenceBinding as CliExactV5EvidenceBinding,
    IdentityAssuranceSummary as CliIdentityAssuranceSummary,
)
from caseloop_cli.main import build_parser, run
from .wire_samples import digest, success_for


BASE = "http://127.0.0.1:8090"
WORKSPACE = "ws_01J0000000000001"
SOURCE = "src_01J0000000000001"
TOKEN = "public-test-token-never-print"
OPERATION_ID = "op_01J0000000000001"
AUTOMATION_REQUEST_ID = "arq_01J0000000000001"
TASK_ID = "task_01J0000000000001"
CASE_ID = "case_01J0000000000001"


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


def _operation_record(*, state: str = "SUBMITTED", cancel_requested: bool = False) -> dict[str, object]:
    return {
        "operation_id": OPERATION_ID,
        "automation_request_id": AUTOMATION_REQUEST_ID,
        "canonical_intent": "investigations.start",
        "state": state,
        "requester_principal": "prn_01J0000000000001",
        "exact_case_binding": {
            "case_id": CASE_ID,
            "case_revision": 1,
            "case_digest": "sha256:" + "c" * 64,
        },
        "application_id": "app_01J0000000000001",
        "environment_id": "env_01J0000000000001",
        "exact_work_task_binding": {
            "kind": "WORK_TASK",
            "id": TASK_ID,
            "revision": 1,
            "digest": "sha256:" + "d" * 64,
        },
        "exact_current_attempt_binding_or_null": None,
        "cancel_requested": cancel_requested,
        "artifact_or_null": None,
        "created_at": "2026-08-13T03:00:00Z",
        "updated_at": "2026-08-13T03:00:00Z",
    }


def _operation_get_response(request: httpx.Request, *, state: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={
            "content-type": "application/json",
            "x-caseloop-contract-version": "2.0",
        },
        json={
            "schema_version": "2.0",
            "workspace_id": WORKSPACE,
            "request_id": request.headers["x-request-id"],
            "audit_ref": "audit://aud_01J0000000000001",
            "operation": _operation_record(
                state=state, cancel_requested=state in {"CANCEL_REQUESTED", "CANCELED"}
            ),
        },
    )


def _async_operation_response(
    request: httpx.Request, *, intent: str, state: str
) -> httpx.Response:
    operation = _operation_record(
        state=state, cancel_requested=state == "CANCEL_REQUESTED"
    )
    core = {
        "schema_version": "2.0",
        "workspace_id": WORKSPACE,
        "request_id": request.headers["x-request-id"],
        "audit_ref": "audit://aud_01J0000000000001",
        "operation": operation,
    }
    request_payload = json.loads(request.content)
    request_payload = {
        ("case_id" if intent == "investigations.start" else "operation_id"): (
            CASE_ID if intent == "investigations.start" else OPERATION_ID
        ),
        **request_payload,
    }
    receipt = {
        "schema_version": "1.0",
        "workspace_id": WORKSPACE,
        "principal_id": "prn_01J0000000000001",
        "intent": intent,
        "idempotency_key": request.headers["x-caseloop-idempotency-key"],
        "request_fingerprint": digest(request_payload),
        "resource": {"kind": "automation_request", "id": AUTOMATION_REQUEST_ID},
        "operation_id": OPERATION_ID,
        "request_id": request.headers["x-request-id"],
        "audit_ref": core["audit_ref"],
        "status": "ACCEPTED",
        "response_digest": digest(core),
        "created_at": "2026-08-13T03:00:00Z",
        "idempotency_receipt_id": "idemr_01J0000000000001",
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/receipt_digest)",
    }
    receipt["receipt_digest"] = digest(receipt)
    return httpx.Response(
        202,
        headers={
            "content-type": "application/json",
            "x-caseloop-contract-version": "2.0",
        },
        json={**core, "idempotency": {"receipt": receipt, "replayed": False}},
    )


def test_help_exposes_only_frozen_stage1a_cli_commands() -> None:
    help_text = build_parser().format_help()
    assert all(name in help_text for name in ("capabilities", "signal", "report", "case", "evidence"))
    assert all(
        name not in help_text
        for name in ("project", "source", "investigation", "release", "skill")
    )


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


def test_capabilities_supports_explicit_v2_without_changing_v1_default() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
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
    assert request.headers["x-caseloop-contract-version"] == "2.0"
    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == "2.0"
    assert payload["data"]["api_major"] == 2
    assert payload["data"]["contract_version"] == "2.0"
    assert payload["data"]["disabled_intents"] == []
    enabled = {item["name"] for item in payload["data"]["enabled_intents"]}
    assert enabled == {
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
    modes = {
        item["name"]: item["execution_mode"]
        for item in payload["data"]["enabled_intents"]
    }
    assert modes["system-manifests.import"] == "synchronous_local_transaction"
    assert modes["investigations.start"] == "asynchronous"
    assert modes["operations.cancel-request"] == "asynchronous"


def test_application_list_uses_authenticated_v2_collection_and_query() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
            },
            json=success_for(request),
        )

    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "application",
            "list",
            "--project-id",
            "proj_01J0000000000001",
            "--limit",
            "25",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/api/v2/applications"
    assert dict(request.url.params) == {
        "limit": "25",
        "project_id": "proj_01J0000000000001",
    }
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers["x-caseloop-contract-version"] == "2.0"
    assert json.loads(stdout.getvalue())["items"] == []


def test_application_list_rejects_same_workspace_cross_project_item() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = success_for(request)
        payload["items"] = [
            {
                "application": {
                    "record_envelope": {
                        "schema_version": "2.0",
                        "workspace_id": WORKSPACE,
                        "revision": 1,
                        "recorded_by_principal": "prn_01J0000000000001",
                        "recorded_at": "2026-08-11T10:00:00Z",
                        "immutable": True,
                        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
                        "record_digest": "sha256:" + "a" * 64,
                        "authority_receipt_id": "arec_01J0000000000001",
                    },
                    "application_id": "app_01J0000000000001",
                    "workspace_id": WORKSPACE,
                    "project_id": "proj_01J0000000000099",
                    "slug": "cross-project",
                    "display_name": "Cross project",
                    "owner_principal_ids": ["prn_01J0000000000001"],
                    "criticality": "P1",
                    "data_classification": "INTERNAL",
                    "governance_mode": "MANAGED",
                    "lifecycle_state": "REGISTERED",
                    "exact_previous_application_binding_or_null": None,
                },
                "environments": [],
                "system_components": [],
                "dependency_edges": [],
            }
        ]
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
            },
            json=payload,
        )

    stderr = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "application",
            "list",
            "--project-id",
            "proj_01J0000000000001",
        ],
        env=_env(),
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert json.loads(stderr.getvalue())["error"]["code"] == "REMOTE_BINDING_INVALID"


@pytest.mark.parametrize("mutation", ["wrong-major", "future-intent"])
def test_capabilities_v2_rejects_non_r2_discovery(mutation: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = success_for(request)
        if mutation == "wrong-major":
            payload["data"]["api_major"] = 1
        else:
            payload["data"]["enabled_intents"].append(
                {
                    "name": "system-versions.get",
                    "scope": "system_versions:read",
                    "execution_mode": "synchronous",
                    "http": True,
                    "cli": True,
                }
            )
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
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


def test_v2_catalog_receipt_actor_is_distinct_from_authority_recorder() -> None:
    """Real server semantics: caller owns the receipt, controller seals the row."""

    def handler(request: httpx.Request) -> httpx.Response:
        payload = success_for(request)
        assert (
            payload["idempotency"]["receipt"]["principal_id"]
            != payload["application"]["record_envelope"]["recorded_by_principal"]
        )
        return httpx.Response(
            201,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
            },
            json=payload,
        )

    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "application",
            "register",
            "--project-id",
            "proj_01J0000000000001",
            "--slug",
            "authority-recorded",
            "--display-name",
            "Authority recorded",
            "--owner-principal-id",
            "prn_01J0000000000001",
            "--criticality",
            "P1",
            "--data-classification",
            "INTERNAL",
            "--governance-mode",
            "MANAGED",
            "--idempotency-key",
            "application-register-key",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert json.loads(stdout.getvalue())["application"]["slug"] == "authority-recorded"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("intent", "environments.register"),
        ("idempotency_key", "different-idempotency-key"),
        ("request_id", "req_01J0000000000099"),
    ],
)
def test_v2_mutation_rejects_rehashed_receipt_binding_drift(
    field: str,
    value: str,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = success_for(request)
        receipt = payload["idempotency"]["receipt"]
        receipt[field] = value
        response_without_idempotency = copy.deepcopy(payload)
        response_without_idempotency.pop("idempotency")
        receipt["response_digest"] = digest(response_without_idempotency)
        receipt_without_digest = copy.deepcopy(receipt)
        receipt_without_digest.pop("receipt_digest")
        receipt["receipt_digest"] = digest(receipt_without_digest)
        return httpx.Response(
            201,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
            },
            json=payload,
        )

    stderr = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "application",
            "register",
            "--project-id",
            "proj_01J0000000000001",
            "--slug",
            "receipt-check",
            "--display-name",
            "Receipt check",
            "--owner-principal-id",
            "prn_01J0000000000001",
            "--criticality",
            "P1",
            "--data-classification",
            "INTERNAL",
            "--governance-mode",
            "MANAGED",
            "--idempotency-key",
            "application-register-key",
        ],
        env=_env(),
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.PROTOCOL
    assert json.loads(stderr.getvalue())["error"]["code"] in {
        "REMOTE_BINDING_INVALID",
        "REMOTE_PROTOCOL_ERROR",
    }


@pytest.mark.parametrize(
    ("response_model", "resource_field", "resource"),
    [
        (
            ApplicationGetResponse,
            "application",
            {
                "application_id": "app_01J0000000000001",
                "project_id": "proj_01J0000000000001",
                "slug": "r2-app",
                "display_name": "R2 app",
                "owner_principal_ids": ["prn_01J0000000000001"],
                "criticality": "P1",
                "data_classification": "INTERNAL",
                "governance_mode": "MANAGED",
                "lifecycle_state": "REGISTERED",
            },
        ),
        (
            ComponentGetResponse,
            "component",
            {
                "component_id": "cmp_01J0000000000001",
                "application_id": "app_01J0000000000001",
                "component_kind": "AGENT",
                "logical_name": "r2-agent",
                "owner_principal_ids": ["prn_01J0000000000001"],
                "criticality": "P1",
                "data_classification": "INTERNAL",
                "permission_classification": "READ_WRITE",
                "effect_classification": "LOCAL",
                "dataset_role": None,
                "lifecycle_state": "REGISTERED",
            },
        ),
    ],
    ids=["application", "component"],
)
def test_r2_cli_catalog_wire_accepts_registered_lifecycle(
    response_model,
    resource_field: str,
    resource: dict[str, object],
) -> None:
    envelope = {
        "schema_version": "2.0",
        "workspace_id": WORKSPACE,
        "revision": 1,
        "recorded_by_principal": "prn_01J0000000000001",
        "recorded_at": "2026-08-11T10:00:00Z",
        "immutable": True,
        "hash_rule": (
            "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"
        ),
        "record_digest": "sha256:" + "a" * 64,
        "authority_receipt_id": "arec_01J0000000000001",
    }
    parsed = response_model.model_validate(
        {
            "schema_version": "2.0",
            "workspace_id": WORKSPACE,
            "request_id": "req_01J0000000000001",
            "audit_ref": "audit://aud_01J0000000000001",
            resource_field: {
                "record_envelope": envelope,
                "workspace_id": WORKSPACE,
                (
                    "exact_previous_application_binding_or_null"
                    if resource_field == "application"
                    else "exact_previous_system_component_binding_or_null"
                ): None,
                **resource,
            },
        }
    )
    assert getattr(parsed, resource_field).lifecycle_state == "REGISTERED"

    malicious = parsed.model_dump(mode="json")
    if resource_field == "application":
        malicious[resource_field]["slug"] = "Uppercase-Is-Forbidden"
        with pytest.raises(ValidationError):
            response_model.model_validate(malicious)
    else:
        malicious[resource_field]["logical_name"] = "spaces are forbidden"
        with pytest.raises(ValidationError):
            response_model.model_validate(malicious)
        malicious = parsed.model_dump(mode="json")
        malicious[resource_field]["component_kind"] = "UNFROZEN_KIND"
        with pytest.raises(ValidationError):
            response_model.model_validate(malicious)


def test_r2_cli_environment_response_rejects_noncanonical_logical_name() -> None:
    with pytest.raises(ValidationError):
        EnvironmentGetResponse.model_validate(
            {
                "schema_version": "2.0",
                "workspace_id": WORKSPACE,
                "request_id": "req_01J0000000000001",
                "audit_ref": "audit://aud_01J0000000000001",
                "environment": {
                    "record_envelope": {
                        "schema_version": "2.0",
                        "workspace_id": WORKSPACE,
                        "revision": 1,
                        "recorded_by_principal": "prn_01J0000000000001",
                        "recorded_at": "2026-08-11T10:00:00Z",
                        "immutable": True,
                        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
                        "record_digest": "sha256:" + "a" * 64,
                        "authority_receipt_id": "arec_01J0000000000001",
                    },
                    "environment_id": "env_01J0000000000001",
                    "workspace_id": WORKSPACE,
                    "application_id": "app_01J0000000000001",
                    "logical_name": "Upper Case",
                    "risk_classification": "LOW",
                    "lifecycle_state": "ACTIVE",
                },
            }
        )


def test_cli_bootstrap_nested_bindings_match_closed_server_shape() -> None:
    binding = {
        "slot": "PRIMARY",
        "kind": "SYSTEM_VERSION_SET",
        "id": "vset_01J0000000000001",
        "revision": 1,
        "digest": "sha256:" + "a" * 64,
    }
    CliExactSlotVersionSetBinding.model_validate(binding)
    summary = {
        "component_assurances": [
            {
                "component_revision_id": "crv_01J0000000000001",
                "component_id": "cmp_01J0000000000001",
                "identity_assurance": "IMMUTABLE_DIGEST",
            }
        ]
    }
    CliIdentityAssuranceSummary.model_validate(summary)

    for invalid in (
        {**binding, "slot": "CANARY"},
        {**binding, "revision": None},
        {**binding, "unexpected": True},
    ):
        with pytest.raises(ValidationError):
            CliExactSlotVersionSetBinding.model_validate(invalid)
    with pytest.raises(ValidationError):
        CliIdentityAssuranceSummary.model_validate({"component_count": 1})

    v4_evidence = {
        "contract_major": 1,
        "kind": "TRACE_EVIDENCE_RECEIPT",
        "id": "ter_01J0000000000001",
        "revision": None,
        "digest": "sha256:" + "a" * 64,
    }
    v5_evidence = {
        "kind": "OBSERVED_STATE_SNAPSHOT",
        "id": "oss_01J0000000000001",
        "revision": 1,
        "digest": "sha256:" + "a" * 64,
    }
    CliExactV4EvidenceBinding.model_validate(v4_evidence)
    CliExactV5EvidenceBinding.model_validate(v5_evidence)
    for model, invalid in (
        (CliExactV4EvidenceBinding, {**v4_evidence, "contract_major": 2}),
        (CliExactV5EvidenceBinding, {**v5_evidence, "contract_major": 1}),
        (CliExactV5EvidenceBinding, {**v5_evidence, "revision": None}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(invalid)


def test_r3_system_manifest_help_has_only_import_and_validate(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["system-manifest", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "import" in help_text
    assert "validate" in help_text
    assert all(action not in help_text for action in ("record", "get", "diff"))


def test_system_version_commands_in_help(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["system-version", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert all(action in help_text for action in ("record", "get", "diff"))


def test_system_version_record_sends_canonical_body_and_idempotency() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
            },
            json=success_for(request),
        )

    expected_body = {
        "schema_version": "2.0",
        "application_id": "app_01J0000000000001",
        "environment_id": "env_01J0000000000001",
        "exact_component_revision_bindings": [
            {
                "kind": "COMPONENT_REVISION",
                "id": "crv_01J0000000000001",
                "revision": 1,
                "digest": "sha256:" + "b" * 64,
            }
        ],
        "exact_topology_revision_binding": {
            "kind": "TOPOLOGY_REVISION",
            "id": "tpr_01J0000000000001",
            "revision": 1,
            "digest": "sha256:" + "c" * 64,
        },
        "exact_previous_system_version_set_binding_or_null": None,
    }
    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "system-version",
            "record",
            "--application-id",
            "app_01J0000000000001",
            "--environment-id",
            "env_01J0000000000001",
            "--component-revisions",
            json.dumps(expected_body["exact_component_revision_bindings"]),
            "--topology-revision",
            json.dumps(expected_body["exact_topology_revision_binding"]),
            "--idempotency-key",
            "system-version-record-key",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v2/system-versions"
    assert request.headers["x-caseloop-contract-version"] == "2.0"
    assert request.headers["x-caseloop-idempotency-key"] == "system-version-record-key"
    assert request.content == json.dumps(
        expected_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert json.loads(stdout.getvalue())["system_version_set"][
        "system_version_set_id"
    ] == "vset_01J0000000000001"


def test_system_version_get_path_parameter() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
            },
            json=success_for(request),
        )

    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "system-version",
            "get",
            "--system-version-set-id",
            "vset_01J0000000000001",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v2/system-versions/vset_01J0000000000001"
    assert request.url.query.decode() == ""
    assert json.loads(stdout.getvalue())["system_version_set"][
        "system_version_set_id"
    ] == "vset_01J0000000000001"


def test_system_version_diff_query_parameters() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
            },
            json=success_for(request),
        )

    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "system-version",
            "diff",
            "--source-version-set-id",
            "vset_01J0000000000001",
            "--target-version-set-id",
            "vset_01J0000000000002",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v2/system-versions:diff"
    assert dict(request.url.params) == {
        "source_version_set_id": "vset_01J0000000000001",
        "target_version_set_id": "vset_01J0000000000002",
    }
    payload = json.loads(stdout.getvalue())
    assert payload["source_binding"]["id"] == "vset_01J0000000000001"
    assert payload["target_binding"]["id"] == "vset_01J0000000000002"
    assert payload["diff"]["deterministic"] is True


def test_r2_manifest_validate_is_local_only_and_needs_no_http_or_credential(
    tmp_path,
) -> None:
    manifest = {
        "schema_version": "2.0",
        "application": {
            "project_id": "proj_01J0000000000001",
            "slug": "local-validate",
            "display_name": "Local validate",
            "owner_principal_ids": ["prn_01J0000000000001"],
            "criticality": "P1",
            "data_classification": "INTERNAL",
            "governance_mode": "MANAGED",
        },
        "environment": {
            "logical_name": "prod",
            "risk_classification": "MEDIUM",
        },
        "components": [
            {
                "logical_name": "app-code",
                "component_kind": "APPLICATION_CODE",
                "owner_principal_ids": ["prn_01J0000000000001"],
                "criticality": "P1",
                "data_classification": "INTERNAL",
                "permission_classification": "READ_ONLY",
                "effect_classification": "LOCAL",
                "revision": {
                    "identity_locator": {"type": "git", "path": "."},
                    "identity_assurance": "IMMUTABLE_DIGEST",
                    "content_digest": "sha256:" + "a" * 64,
                },
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    requests: list[httpx.Request] = []
    stdout = io.StringIO()

    exit_code = run(
        [
            "--api-version",
            "2",
            "system-manifest",
            "validate",
            "--manifest-file",
            str(manifest_path),
        ],
        env={},
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    )

    assert exit_code == ExitFamily.OK
    assert requests == []
    assert json.loads(stdout.getvalue()) == {
        "schema_version": "1.0",
        "manifest_valid": True,
    }


def test_r3_init_is_local_and_validate_remains_available() -> None:
    """``init`` is a local-only command: no API URL/credential required and no
    HTTP request is made; ``system-manifest validate`` stays local too."""
    requests: list[httpx.Request] = []
    stdout = io.StringIO()
    exit_code = run(
        ["--api-version", "2", "init", "."],
        env={},
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    )

    assert exit_code == ExitFamily.OK
    assert requests == []
    payload = json.loads(stdout.getvalue())
    assert "_discovery" in payload


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v2/cases/case_01J0000000000001/application-binding"),
        ("GET", "/api/v2/cases/case_01J0000000000001/acceptance-criteria"),
    ],
)
def test_r4_client_accepts_activated_1c_reads(method: str, path: str) -> None:
    """The R4 1C intents are activated: the client compiles a wire spec for
    each (method, path) and completes a full response round-trip instead of
    failing closed before HTTP."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "application/json",
                "x-caseloop-contract-version": "2.0",
            },
            json=success_for(request),
        )

    client = PublicApiClient(
        RuntimeConfig(base_url=BASE, workspace_id=WORKSPACE, token=TOKEN),
        transport=httpx.MockTransport(handler),
    )
    payload = client.request(method, path, api_major=2)
    assert isinstance(payload, dict)


def test_r4_client_rejects_unactivated_future_operation_before_http() -> None:
    requests: list[httpx.Request] = []
    client = PublicApiClient(
        RuntimeConfig(base_url=BASE, workspace_id=WORKSPACE, token=TOKEN),
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    )

    with pytest.raises(CliError) as exc_info:
        client.request(
            "GET", "/api/v2/system-episodes/ep_01J0000000000001", api_major=2
        )

    assert exc_info.value.code == "CLIENT_OPERATION_UNSUPPORTED"
    assert requests == []


@pytest.mark.parametrize("action", ["record", "get", "diff"])
def test_r2_system_manifest_future_actions_have_no_dispatch(action: str) -> None:
    requests: list[httpx.Request] = []
    stderr = io.StringIO()
    exit_code = run(
        ["--api-version", "2", *_globals(), "system-manifest", action],
        env=_env(),
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    )

    assert exit_code == ExitFamily.INPUT
    assert requests == []
    assert json.loads(stderr.getvalue())["error"]["code"] == "CLI_USAGE_INVALID"


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


@pytest.mark.parametrize(
    "argv",
    [
        ["case", "bind-application"],
        ["case", "application-binding"],
        ["case", "acceptance-criteria"],
    ],
)
def test_r4_case_actions_require_their_option_surface(argv: list[str]) -> None:
    """The R4 case actions are exposed; incomplete invocations fail closed
    with CLI_USAGE_INVALID before any HTTP."""
    requests: list[httpx.Request] = []
    stderr = io.StringIO()
    exit_code = run(
        ["--api-version", "2", *_globals(), *argv],
        env=_env(),
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    )

    assert exit_code == ExitFamily.INPUT
    assert requests == []
    assert json.loads(stderr.getvalue())["error"]["code"] == "CLI_USAGE_INVALID"


def test_r4_case_help_exposes_v1_reads_and_v2_actions(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["case", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "get" in help_text
    assert "timeline" in help_text
    assert all(
        action in help_text
        for action in (
            "bind-application",
            "application-binding",
            "acceptance-criteria",
            "from-issue",
        )
    )


def test_r4_acceptance_criteria_help_has_propose_get_confirm(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["case", "acceptance-criteria", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert all(action in help_text for action in ("propose", "get", "confirm"))


def test_r4_application_binding_help_has_get(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["case", "application-binding", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "get" in help_text


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


# ---------------------------------------------------------------------------
# R4 V5-1C CLI surface (case binding / acceptance criteria / from-issue).


def _v2_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        201 if request.method == "POST" else 200,
        headers={
            "content-type": "application/json",
            "x-caseloop-contract-version": "2.0",
        },
        json=success_for(request),
    )


def test_case_bind_application_sends_canonical_body_and_idempotency() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _v2_response(request)

    expected_body = {
        "schema_version": "2.0",
        "case_id": "case_01J0000000000001",
        "case_revision": 1,
        "case_digest": "sha256:" + "c" * 64,
        "application_id": "app_01J0000000000001",
        "environment_id": "env_01J0000000000001",
        "declared_system_version_set_binding_or_unknown": "UNKNOWN",
        "issue_snapshot": None,
    }
    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "case",
            "bind-application",
            "case_01J0000000000001",
            "--application-id",
            "app_01J0000000000001",
            "--environment-id",
            "env_01J0000000000001",
            "--case-revision",
            "1",
            "--case-digest",
            "sha256:" + "c" * 64,
            "--idempotency-key",
            "case-bind-application-key",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v2/cases/case_01J0000000000001:bind-application"
    assert request.headers["x-caseloop-contract-version"] == "2.0"
    assert request.headers["x-caseloop-idempotency-key"] == "case-bind-application-key"
    assert request.content == json.dumps(
        expected_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload = json.loads(stdout.getvalue())
    assert payload["application_case_binding"]["application_case_binding_id"].startswith(
        "acb_"
    )


def test_case_application_binding_get_query_parameters() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _v2_response(request)

    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "case",
            "application-binding",
            "get",
            "--case-id",
            "case_01J0000000000001",
            "--case-revision",
            "3",
            "--case-digest",
            "sha256:" + "d" * 64,
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v2/cases/case_01J0000000000001/application-binding"
    assert dict(request.url.params) == {
        "case_revision": "3",
        "case_digest": "sha256:" + "d" * 64,
    }
    payload = json.loads(stdout.getvalue())
    assert payload["application_case_binding"]["binding_digest"].startswith("sha256:")


def test_case_acceptance_criteria_propose_sends_canonical_body() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _v2_response(request)

    draft = {
        "acceptance_source": {"kind": "manual", "url": "https://example.com/x"},
        "expected_behavior": {"summary": "schema_dsl must not crash"},
        "applicable_workload_profile": {"name": "cli-once"},
        "applicable_deployment_profile": {"name": "local-shadow"},
    }
    expected_body = {
        "schema_version": "2.0",
        "case_id": "case_01J0000000000001",
        "case_revision": 1,
        "case_digest": "sha256:" + "c" * 64,
        "acceptance_source": draft["acceptance_source"],
        "reproducer_input": None,
        "reproducer_environment": None,
        "expected_behavior": draft["expected_behavior"],
        "oracle_or_evaluator": None,
        "applicable_workload_profile": draft["applicable_workload_profile"],
        "applicable_deployment_profile": draft["applicable_deployment_profile"],
    }
    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "case",
            "acceptance-criteria",
            "propose",
            "case_01J0000000000001",
            "--case-revision",
            "1",
            "--case-digest",
            "sha256:" + "c" * 64,
            "--acceptance-json",
            json.dumps(draft),
            "--idempotency-key",
            "acceptance-propose-key",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert (
        request.url.path
        == "/api/v2/cases/case_01J0000000000001:propose-acceptance-criteria"
    )
    assert request.headers["x-caseloop-idempotency-key"] == "acceptance-propose-key"
    assert request.content == json.dumps(
        expected_body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    payload = json.loads(stdout.getvalue())
    assert payload["acceptance_criteria_revision"][
        "acceptance_criteria_revision_id"
    ].startswith("acr_")


def test_case_acceptance_criteria_get_query_parameter() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _v2_response(request)

    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "case",
            "acceptance-criteria",
            "get",
            "case_01J0000000000001",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert request.url.path == "/api/v2/cases/case_01J0000000000001/acceptance-criteria"
    assert dict(request.url.params) == {"case_revision": "1"}
    payload = json.loads(stdout.getvalue())
    assert payload["case_readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"
    assert payload["next_action"]["code"] == "CONFIRM_ACCEPTANCE_CRITERIA"


def test_case_acceptance_criteria_confirm_e2e_form() -> None:
    """The R4 e2e journey form: positional revision id + proposed digest only
    (no case id), so the CLI binds the digest directly without a pre-read."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _v2_response(request)

    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "case",
            "acceptance-criteria",
            "confirm",
            "acr_01J0000000000001",
            "--proposed-revision-digest",
            "sha256:" + "f" * 64,
            "--idempotency-key",
            "acceptance-confirm-key",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert (
        request.url.path
        == "/api/v2/acceptance-criteria/acr_01J0000000000001:confirm"
    )
    body = json.loads(request.content)
    assert body == {
        "schema_version": "2.0",
        "exact_proposed_revision_binding": {
            "kind": "ACCEPTANCE_CRITERIA_REVISION",
            "id": "acr_01J0000000000001",
            "revision": None,
            "digest": "sha256:" + "f" * 64,
        },
        "confirmation_note": None,
    }
    payload = json.loads(stdout.getvalue())
    assert payload["acceptance_criteria_revision"]["confirmation_status"] == "PROPOSED"


def test_case_acceptance_criteria_confirm_authoritative_pre_read() -> None:
    """With an explicit case id the CLI re-reads the proposal from the Case
    read model and verifies the operator-provided digest before confirming."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _v2_response(request)

    stdout = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "case",
            "acceptance-criteria",
            "confirm",
            "acr_01J0000000000001",
            "--case-id",
            "case_01J0000000000001",
            "--case-revision",
            "1",
            "--proposed-revision-digest",
            "sha256:" + "e" * 64,
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert [request.url.path for request in requests] == [
        "/api/v2/cases/case_01J0000000000001/acceptance-criteria",
        "/api/v2/acceptance-criteria/acr_01J0000000000001:confirm",
    ]
    get_request = requests[0]
    assert dict(get_request.url.params) == {"case_revision": "1"}
    confirm_body = json.loads(requests[1].content)
    assert confirm_body["exact_proposed_revision_binding"]["revision"] == 1
    assert confirm_body["exact_proposed_revision_binding"]["digest"] == (
        "sha256:" + "e" * 64
    )


def test_case_v2_actions_reject_api_version_1() -> None:
    stderr = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "1",
            *_globals(),
            "case",
            "bind-application",
            "case_01J0000000000001",
            "--application-id",
            "app_01J0000000000001",
            "--environment-id",
            "env_01J0000000000001",
            "--case-digest",
            "sha256:" + "c" * 64,
        ],
        env=_env(),
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(_response),
    )

    assert exit_code == ExitFamily.INPUT
    assert json.loads(stderr.getvalue())["error"]["code"] == "API_VERSION_REQUIRED"


def test_case_from_issue_composes_canonical_intents(tmp_path) -> None:
    """``case from-issue`` orchestrates signals.submit → acceptance read →
    cases.bind-application → acceptance-criteria.propose from a local read-only
    snapshot, never auto-confirms, and is deterministic on retry."""
    snapshot = {
        "number": 1466,
        "title": "BUG: schema_dsl() raises IndexError",
        "body": "Calling schema_dsl() with a field missing its name crashes.",
        "state": "closed",
        "html_url": "https://github.com/simonw/llm/issues/1466",
        "updated_at": "2026-07-30T22:33:06Z",
        "created_at": "2026-06-01T20:50:26Z",
        "user": {"login": "devteamaegis"},
    }
    snapshot_file = tmp_path / "issue-1466.json"
    snapshot_file.write_text(json.dumps(snapshot), encoding="utf-8")

    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path == "/api/v2/applications/app_01J0000000000001":
            core = {
                "schema_version": "2.0",
                "workspace_id": WORKSPACE,
                "request_id": request.headers["x-request-id"],
                "audit_ref": "audit://aud_01J0000000000001",
            }
            payload = {
                **core,
                "application": {
                    "record_envelope": {
                        "schema_version": "2.0",
                        "workspace_id": WORKSPACE,
                        "revision": 2,
                        "recorded_by_principal": "prn_01J0000000000001",
                        "recorded_at": "2026-08-11T10:00:00Z",
                        "immutable": True,
                        "hash_rule": (
                            "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"
                        ),
                        "record_digest": "sha256:" + "a" * 64,
                        "authority_receipt_id": "arec_01J0000000000001",
                    },
                    "application_id": "app_01J0000000000001",
                    "workspace_id": WORKSPACE,
                    "project_id": "proj_01J0000000000001",
                    "slug": "llm-cli",
                    "display_name": "LLM CLI",
                    "owner_principal_ids": ["prn_01J0000000000001"],
                    "criticality": "P0",
                    "data_classification": "INTERNAL",
                    "governance_mode": "MANAGED",
                    "lifecycle_state": "ACTIVE",
                    "exact_previous_application_binding": {
                        "kind": "AI_APPLICATION",
                        "id": "app_01J0000000000001",
                        "revision": 1,
                        "digest": "sha256:" + "b" * 64,
                    },
                },
            }
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "x-caseloop-contract-version": "2.0",
                },
                json=payload,
            )
        if path == "/api/v2/environments/env_01J0000000000001":
            payload = {
                "schema_version": "2.0",
                "workspace_id": WORKSPACE,
                "request_id": request.headers["x-request-id"],
                "audit_ref": "audit://aud_01J0000000000001",
                "environment": {
                    "record_envelope": {
                        "schema_version": "2.0",
                        "workspace_id": WORKSPACE,
                        "revision": 1,
                        "recorded_by_principal": "prn_01J0000000000001",
                        "recorded_at": "2026-08-11T10:00:00Z",
                        "immutable": True,
                        "hash_rule": (
                            "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"
                        ),
                        "record_digest": "sha256:" + "b" * 64,
                        "authority_receipt_id": "arec_01J0000000000001",
                    },
                    "environment_id": "env_01J0000000000001",
                    "workspace_id": WORKSPACE,
                    "application_id": "app_01J0000000000001",
                    "logical_name": "local-shadow",
                    "risk_classification": "LOW",
                    "lifecycle_state": "ACTIVE",
                },
            }
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "x-caseloop-contract-version": "2.0",
                },
                json=payload,
            )
        if path == "/api/v1/signals":
            return httpx.Response(
                201,
                headers={
                    "content-type": "application/json",
                    "x-caseloop-contract-version": "1.0",
                },
                json=success_for(request),
            )
        if path.endswith("/acceptance-criteria"):
            case_id = path.split("/")[-2]
            payload = {
                "schema_version": "2.0",
                "workspace_id": WORKSPACE,
                "request_id": request.headers["x-request-id"],
                "audit_ref": "audit://aud_01J0000000000001",
                "exact_case_binding": {
                    "case_id": case_id,
                    "case_revision": 1,
                    "case_digest": "sha256:" + "c" * 64,
                },
                "case_readiness": "NEEDS_ACCEPTANCE_CRITERIA",
                "revisions": [],
                "next_action": None,
            }
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "x-caseloop-contract-version": "2.0",
                },
                json=payload,
            )
        return _v2_response(request)

    stdout = io.StringIO()
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
            "maintainer-01J0000000000001",
        ],
        env=_env(),
        stdout=stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )

    assert exit_code == ExitFamily.OK
    assert [request.url.path for request in requests] == [
        "/api/v2/applications/app_01J0000000000001",
        "/api/v2/environments/env_01J0000000000001",
        "/api/v1/signals",
        "/api/v2/cases/case_stage0001/acceptance-criteria",
        "/api/v2/cases/case_stage0001:bind-application",
        "/api/v2/cases/case_stage0001:propose-acceptance-criteria",
    ]
    signal_body = json.loads(requests[2].content)
    assert signal_body["source_event_id"].startswith(
        "github-issue:simonw:llm:1466:"
    )
    assert signal_body["content"]["summary"] == snapshot["title"]
    assert signal_body["content"]["attachments"][0]["uri"] == snapshot["html_url"]
    assert requests[2].headers["idempotency-key"].startswith(
        "case-from-issue-simonw-llm-1466-"
    )
    bind_body = json.loads(requests[4].content)
    assert bind_body["case_id"] == "case_stage0001"
    assert bind_body["declared_system_version_set_binding_or_unknown"] == "UNKNOWN"
    assert bind_body["issue_snapshot"]["external_repo"] == "simonw/llm"
    propose_body = json.loads(requests[5].content)
    assert propose_body["acceptance_source"]["repo"] == "simonw/llm"
    assert propose_body["case_id"] == "case_stage0001"

    payload = json.loads(stdout.getvalue())
    assert payload["case_id"] == "case_stage0001"
    assert payload["case_readiness"] == "NEEDS_ACCEPTANCE_CRITERIA"
    assert payload["next_action"]["code"] == "CONFIRM_ACCEPTANCE_CRITERIA"
    assert payload["acceptance_criteria_revision_id"].startswith("acr_")
    assert payload["acceptance_criteria_revision_digest"].startswith("sha256:")

    # A retry with the same snapshot derives the same source event and
    # idempotency keys: no duplicate case, no second owner.
    first_signal_idem = requests[2].headers["idempotency-key"]
    first_source_event = signal_body["source_event_id"]
    requests.clear()
    stdout2 = io.StringIO()
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
            "maintainer-01J0000000000001",
        ],
        env=_env(),
        stdout=stdout2,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(handler),
    )
    assert exit_code == ExitFamily.OK
    retry_signal_body = json.loads(requests[2].content)
    assert requests[2].headers["idempotency-key"] == first_signal_idem
    assert retry_signal_body["source_event_id"] == first_source_event
    retry = json.loads(stdout2.getvalue())
    assert retry["source_event_version"] == payload["source_event_version"]
    assert retry["acceptance_criteria_revision_id"] == payload["acceptance_criteria_revision_id"]


def test_v5_investigation_start_then_new_cli_process_can_reconnect_and_wait() -> None:
    start_requests: list[httpx.Request] = []

    def start_handler(request: httpx.Request) -> httpx.Response:
        start_requests.append(request)
        return _async_operation_response(
            request, intent="investigations.start", state="SUBMITTED"
        )

    started_stdout = io.StringIO()
    started = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "case",
            "investigate",
            CASE_ID,
            "--case-digest",
            "sha256:" + "c" * 64,
            "--instructions",
            "Inspect the durable case binding.",
            "--idempotency-key",
            "investigation-cli-0001",
        ],
        env=_env(),
        stdout=started_stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(start_handler),
    )
    assert started == ExitFamily.OK
    assert json.loads(started_stdout.getvalue())["operation"]["operation_id"] == OPERATION_ID
    assert len(start_requests) == 1
    assert start_requests[0].url.path == f"/api/v2/cases/{CASE_ID}:investigate"
    assert start_requests[0].headers["x-caseloop-idempotency-key"] == "investigation-cli-0001"
    assert json.loads(start_requests[0].content)["max_attempts"] == 3

    states = iter(["WORKING", "CANCELED"])
    reconnect_requests: list[httpx.Request] = []

    def reconnect_handler(request: httpx.Request) -> httpx.Response:
        reconnect_requests.append(request)
        return _operation_get_response(request, state=next(states))

    reconnected_stdout = io.StringIO()
    reconnected = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "operation",
            "follow",
            OPERATION_ID,
            "--timeout-seconds",
            "5",
        ],
        env=_env(),
        stdout=reconnected_stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(reconnect_handler),
        sleep=lambda _: None,
    )
    assert reconnected == ExitFamily.OK
    lines = [json.loads(line) for line in reconnected_stdout.getvalue().splitlines()]
    assert [line["operation"]["state"] for line in lines] == ["WORKING", "CANCELED"]
    assert [request.url.path for request in reconnect_requests] == [
        f"/api/v2/operations/{OPERATION_ID}",
        f"/api/v2/operations/{OPERATION_ID}",
    ]


def test_operation_cancel_is_explicit_and_wait_ctrl_c_only_detaches() -> None:
    requests: list[httpx.Request] = []

    def cancel_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _async_operation_response(
            request, intent="operations.cancel-request", state="CANCEL_REQUESTED"
        )

    cancel_stdout = io.StringIO()
    canceled = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "operation",
            "cancel",
            OPERATION_ID,
            "--reason",
            "operator stop",
            "--idempotency-key",
            "operation-cancel-0001",
        ],
        env=_env(),
        stdout=cancel_stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(cancel_handler),
    )
    assert canceled == ExitFamily.OK
    assert json.loads(cancel_stdout.getvalue())["operation"]["state"] == "CANCEL_REQUESTED"
    assert requests[0].url.path == f"/api/v2/operations/{OPERATION_ID}:cancel"

    wait_requests: list[httpx.Request] = []

    def wait_handler(request: httpx.Request) -> httpx.Response:
        wait_requests.append(request)
        return _operation_get_response(request, state="WORKING")

    def interrupt_wait(_: float) -> None:
        raise KeyboardInterrupt

    detached_stdout = io.StringIO()
    detached = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "operation",
            "wait",
            OPERATION_ID,
            "--timeout-seconds",
            "5",
        ],
        env=_env(),
        stdout=detached_stdout,
        stderr=io.StringIO(),
        transport=httpx.MockTransport(wait_handler),
        sleep=interrupt_wait,
    )
    assert detached == ExitFamily.OK
    detached_payload = json.loads(detached_stdout.getvalue())
    assert detached_payload["detached"] is True
    assert detached_payload["last_observation"]["operation"]["state"] == "WORKING"
    assert [request.method for request in wait_requests] == ["GET"]
    assert all(":cancel" not in request.url.path for request in wait_requests)


def test_operation_wait_timeout_is_temporary_and_preserves_last_state() -> None:
    times = iter(
        [
            datetime(2026, 8, 13, 3, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 13, 3, 0, 2, tzinfo=timezone.utc),
        ]
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = run(
        [
            "--api-version",
            "2",
            *_globals(),
            "operation",
            "wait",
            OPERATION_ID,
            "--timeout-seconds",
            "1",
        ],
        env=_env(),
        stdout=stdout,
        stderr=stderr,
        transport=httpx.MockTransport(
            lambda request: _operation_get_response(request, state="WORKING")
        ),
        sleep=lambda _: None,
        now=lambda: next(times),
    )
    assert exit_code == ExitFamily.TEMPORARY
    assert stdout.getvalue() == ""
    error = json.loads(stderr.getvalue())
    assert error["error"]["code"] == "OPERATION_WAIT_TIMEOUT"
    assert error["error"]["details"] == {
        "operation_id": OPERATION_ID,
        "last_state": "WORKING",
    }
