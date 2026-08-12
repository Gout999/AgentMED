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
    assert all(
        name not in help_text
        for name in ("project", "source", "investigation", "release", "skill", "init")
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
    }
    modes = {
        item["name"]: item["execution_mode"]
        for item in payload["data"]["enabled_intents"]
    }
    assert modes["system-manifests.import"] == "synchronous_local_transaction"


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


def test_r2_init_is_hidden_but_local_validate_remains_available() -> None:
    requests: list[httpx.Request] = []
    stderr = io.StringIO()
    exit_code = run(
        ["--api-version", "2", "init", "."],
        env={},
        stdout=io.StringIO(),
        stderr=stderr,
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    )

    assert exit_code == ExitFamily.INPUT
    assert requests == []
    assert json.loads(stderr.getvalue())["error"]["code"] == "CLI_USAGE_INVALID"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v2/cases/case_01J0000000000001:bind-application"),
        ("GET", "/api/v2/cases/case_01J0000000000001/application-binding"),
    ],
)
def test_r2_client_rejects_unactivated_operations_before_http(
    method: str,
    path: str,
) -> None:
    requests: list[httpx.Request] = []
    client = PublicApiClient(
        RuntimeConfig(base_url=BASE, workspace_id=WORKSPACE, token=TOKEN),
        transport=httpx.MockTransport(lambda request: requests.append(request)),
    )

    with pytest.raises(CliError) as exc_info:
        client.request(method, path, api_major=2)

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
        ["case", "from-issue"],
    ],
)
def test_r2_cli_hides_r4_case_and_acceptance_actions(argv: list[str]) -> None:
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


def test_r2_case_help_exposes_only_v1_read_actions(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["case", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "get" in help_text
    assert "timeline" in help_text
    assert all(
        action not in help_text
        for action in (
            "bind-application",
            "application-binding",
            "acceptance-criteria",
            "from-issue",
        )
    )


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
