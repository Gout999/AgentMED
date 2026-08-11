from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import rfc8785


FIXTURES = Path(__file__).resolve().parents[2] / "contracts" / "v4" / "fixtures" / "valid"
MISSING = ["trace.input", "trace.output", "observations.model", "observations.tools"]


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def _context(request: httpx.Request) -> tuple[str, str]:
    return (
        request.headers["x-caseloop-workspace-id"],
        request.headers["x-request-id"],
    )


def capabilities(request: httpx.Request) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    if request.url.path == "/api/v2/capabilities":
        intents = [
            ("capabilities.get", "capabilities:read"),
            ("applications.register", "applications:manage"),
            ("applications.get", "applications:read"),
            ("applications.list", "applications:read"),
            ("environments.register", "applications:manage"),
            ("environments.get", "applications:read"),
            ("system-components.register", "applications:manage"),
            ("system-components.get", "applications:read"),
            ("dependency-edges.record", "applications:manage"),
            ("dependency-edges.get", "applications:read"),
            ("system-manifests.import", "system_manifests:import"),
        ]
        return {
            "schema_version": "2.0",
            "workspace_id": workspace_id,
            "request_id": request_id,
            "audit_ref": "audit://aud_01J0000000000001",
            "data": {
                "server_version": "0.1.0+v5-r2",
                "api_major": 2,
                "contract_version": "2.0",
                "principal": {
                    "principal_id": "prn_01J0000000000001",
                    "principal_type": "human",
                    "scopes": [
                        "capabilities:read",
                        "applications:manage",
                        "applications:read",
                        "system_manifests:import",
                    ],
                    "credential_expires_at": "2026-08-12T10:00:00Z",
                },
                "enabled_intents": [
                    {
                        "name": name,
                        "scope": scope,
                        "execution_mode": (
                            "synchronous_local_transaction"
                            if name == "system-manifests.import"
                            else "synchronous"
                        ),
                        "http": True,
                        "cli": True,
                    }
                    for name, scope in intents
                ],
                "disabled_intents": [],
                "generated_at": "2026-08-11T10:00:00Z",
            },
        }
    payload = fixture("public-server-capabilities-response.json")
    payload["workspace_id"] = workspace_id
    payload["request_id"] = request_id
    payload["data"]["enabled_intents"] = [
        item
        for item in payload["data"]["enabled_intents"]
        if item["name"]
        in {
            "signals.submit",
            "cases.get",
            "cases.timeline",
            "evidence.get",
            "capabilities.get",
        }
    ]
    return payload


def application_list(request: httpx.Request) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    return {
        "schema_version": "2.0",
        "workspace_id": workspace_id,
        "request_id": request_id,
        "audit_ref": "audit://aud_01J0000000000001",
        "items": [],
        "next_cursor": None,
    }


def application_register(request: httpx.Request) -> dict[str, Any]:
    submission = json.loads(request.content)
    application_id = "app_01J0000000000001"
    core = {
        "schema_version": "2.0",
        "workspace_id": request.headers["x-caseloop-workspace-id"],
        "request_id": request.headers["x-request-id"],
        "audit_ref": "audit://aud_01J0000000000007",
        "application": {
            "record_envelope": {
                "schema_version": "2.0",
                "workspace_id": request.headers["x-caseloop-workspace-id"],
                "revision": 1,
                "recorded_by_principal": "prn_01J000000000000A",
                "recorded_at": "2026-08-11T10:00:00Z",
                "immutable": True,
                "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
                "record_digest": "sha256:" + "a" * 64,
                "authority_receipt_id": "arec_01J0000000000001",
            },
            "application_id": application_id,
            "workspace_id": request.headers["x-caseloop-workspace-id"],
            "project_id": submission["project_id"],
            "slug": submission["slug"],
            "display_name": submission["display_name"],
            "owner_principal_ids": submission["owner_principal_ids"],
            "criticality": submission["criticality"],
            "data_classification": submission["data_classification"],
            "governance_mode": submission["governance_mode"],
            "lifecycle_state": "REGISTERED",
            "exact_previous_application_binding_or_null": None,
        },
    }
    receipt = _case_v2_receipt(
        request,
        intent="applications.register",
        resource_kind="ai_application",
        resource_id=application_id,
        audit_ref=core["audit_ref"],
        core=core,
    )
    return {**core, "idempotency": {"receipt": receipt, "replayed": False}}


def signal(request: httpx.Request) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    submission = json.loads(request.content)
    payload = fixture("public-signal-submission-response.json")
    payload["workspace_id"] = workspace_id
    payload["request_id"] = request_id
    payload["signal"]["source_event_id"] = submission["source_event_id"]
    payload["next_action"]["command"] = None
    payload["next_action"]["href"] = None
    receipt = payload["idempotency"]["receipt"]
    receipt["workspace_id"] = workspace_id
    receipt["request_id"] = request_id
    receipt["idempotency_key"] = request.headers["idempotency-key"]
    receipt["resource"]["id"] = payload["signal"]["signal_id"]
    receipt["audit_ref"] = payload["audit_ref"]
    receipt["request_fingerprint"] = digest(submission)
    response_without_idempotency = copy.deepcopy(payload)
    response_without_idempotency.pop("idempotency")
    receipt["response_digest"] = digest(response_without_idempotency)
    receipt_without_digest = copy.deepcopy(receipt)
    receipt_without_digest.pop("receipt_digest")
    receipt["receipt_digest"] = digest(receipt_without_digest)
    return payload


def case(request: httpx.Request, case_id: str) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    return {
        "schema_version": "1.0",
        "workspace_id": workspace_id,
        "request_id": request_id,
        "audit_ref": "audit://aud_01J0000000000004",
        "data": {
            "case_id": case_id,
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
                "receipt_id": "ter_01J0000000000001",
                "receipt_digest": "sha256:" + "a" * 64,
                "agent_run_ref_id": None,
                "missing_fields": copy.copy(MISSING),
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
                "href": f"https://caseloop.local/api/v1/cases/{case_id}",
            },
        },
    }


def timeline(request: httpx.Request, case_id: str) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    limit = int(request.url.params.get("limit", "50"))
    return {
        "schema_version": "1.0",
        "workspace_id": workspace_id,
        "request_id": request_id,
        "audit_ref": "audit://aud_01J0000000000005",
        "data": {
            "case_id": case_id,
            "events": [
                {
                    "event_id": "evt_01J0000000000001",
                    "event_type": "case.opened",
                    "event_version": "1.0",
                    "occurred_at": "2026-08-10T09:00:00Z",
                    "causation_id": None,
                    "correlation_id": case_id,
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
                "limit": limit,
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


def evidence(request: httpx.Request, receipt_id: str) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    receipt = fixture("trace-evidence-receipt-no-locator.json")
    receipt["workspace_id"] = workspace_id
    receipt["receipt_id"] = receipt_id
    receipt_without_digest = copy.deepcopy(receipt)
    receipt_without_digest.pop("receipt_digest")
    receipt["receipt_digest"] = digest(receipt_without_digest)
    return {
        "schema_version": "1.0",
        "workspace_id": workspace_id,
        "request_id": request_id,
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


def source_query_evidence(request: httpx.Request, receipt_id: str) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    receipt = fixture("trace-evidence-receipt.json")
    receipt["workspace_id"] = workspace_id
    receipt["receipt_id"] = receipt_id
    receipt_without_digest = copy.deepcopy(receipt)
    receipt_without_digest.pop("receipt_digest")
    receipt["receipt_digest"] = digest(receipt_without_digest)
    return {
        "schema_version": "1.0",
        "workspace_id": workspace_id,
        "request_id": request_id,
        "audit_ref": "audit://aud_01J0000000000006",
        "data": {
            "receipt_kind": "TRACE_EVIDENCE_RECEIPT",
            "receipt": receipt,
            "receipt_digest": receipt["receipt_digest"],
            "verification_status": "VERIFIED",
            "verified_at": "2026-08-10T09:00:03Z",
            "superseded_by": None,
        },
    }


def success_for(request: httpx.Request) -> dict[str, Any]:
    path = request.url.path
    if path in {"/api/v1/capabilities", "/api/v2/capabilities"}:
        return capabilities(request)
    if path == "/api/v1/signals":
        return signal(request)
    if path == "/api/v2/applications" and request.method == "GET":
        return application_list(request)
    if path == "/api/v2/applications" and request.method == "POST":
        return application_register(request)
    if path.endswith("/timeline"):
        return timeline(request, path.split("/")[-2])
    if path.startswith("/api/v1/cases/"):
        return case(request, path.rsplit("/", 1)[-1])
    if path.startswith("/api/v1/evidence/"):
        return evidence(request, path.rsplit("/", 1)[-1])
    if path.endswith(":bind-application"):
        return case_bind_application(request)
    if path.endswith("/application-binding"):
        return case_application_binding_get(request)
    if path.endswith(":propose-acceptance-criteria"):
        return acceptance_propose(request)
    if path.endswith("/acceptance-criteria"):
        return acceptance_get(request)
    if path.endswith(":confirm"):
        return acceptance_confirm(request)
    raise AssertionError(path)


# ---------------------------------------------------------------------------
# V5-1C wire samples (case binding / acceptance criteria).


def _v5_core(
    request: httpx.Request, *, resource_kind: str, resource_field: str
) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "workspace_id": request.headers["x-caseloop-workspace-id"],
        "request_id": request.headers["x-request-id"],
        "audit_ref": "audit://aud_01J0000000000007",
    }


def _case_v2_receipt(
    request: httpx.Request,
    *,
    intent: str,
    resource_kind: str,
    resource_id: str,
    audit_ref: str,
    core: dict[str, Any],
) -> dict[str, Any]:
    submission = json.loads(request.content)
    response_without_idempotency = copy.deepcopy(core)
    response_without_idempotency.pop("idempotency", None)
    receipt: dict[str, Any] = {
        "schema_version": "1.0",
        "workspace_id": request.headers["x-caseloop-workspace-id"],
        "principal_id": "prn_01J0000000000001",
        "intent": intent,
        "idempotency_key": request.headers["x-caseloop-idempotency-key"],
        "request_fingerprint": digest(submission),
        "resource": {"kind": resource_kind, "id": resource_id},
        "operation_id": None,
        "request_id": request.headers["x-request-id"],
        "audit_ref": audit_ref,
        "status": "COMPLETED",
        "response_digest": digest(response_without_idempotency),
        "created_at": "2026-08-11T10:00:00Z",
        "idempotency_receipt_id": "idemr_01J0000000000001",
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/receipt_digest)",
        "receipt_digest": "",
    }
    receipt_without_digest = copy.deepcopy(receipt)
    receipt_without_digest.pop("receipt_digest")
    receipt["receipt_digest"] = digest(receipt_without_digest)
    return receipt


def _binding_envelope(request: httpx.Request, binding_id: str) -> dict[str, Any]:
    case_digest = request.url.params.get("case_digest", "sha256:" + "c" * 64)
    return {
        "application_case_binding_id": binding_id,
        "workspace_id": request.headers["x-caseloop-workspace-id"],
        "exact_case_binding": {
            "case_id": "case_01J0000000000001",
            "case_revision": int(request.url.params.get("case_revision", "1")),
            "case_digest": case_digest,
        },
        "application_id": "app_01J0000000000001",
        "environment_id": "env_01J0000000000001",
        "declared_system_version_set_binding_or_unknown": None,
        "binding_digest": "sha256:" + "d" * 64,
        "record_envelope": {
            "schema_version": "2.0",
            "workspace_id": request.headers["x-caseloop-workspace-id"],
            "revision": 1,
            "recorded_by_principal": "prn_01J0000000000001",
            "recorded_at": "2026-08-11T10:00:00Z",
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
            "record_digest": "sha256:" + "e" * 64,
            "authority_receipt_id": "arec_01J0000000000001",
        },
    }


def case_bind_application(request: httpx.Request) -> dict[str, Any]:
    body = json.loads(request.content)
    binding_id = "acb_01J0000000000001"
    envelope = _binding_envelope(request, binding_id)
    envelope["exact_case_binding"]["case_id"] = body["case_id"]
    core = {
        **_v5_core(request, resource_kind="application_case_binding", resource_field="application_case_binding"),
        "application_case_binding": envelope,
    }
    core["audit_ref"] = "audit://aud_01J0000000000007"
    receipt = _case_v2_receipt(
        request,
        intent="cases.bind-application",
        resource_kind="application_case_binding",
        resource_id=binding_id,
        audit_ref=core["audit_ref"],
        core=core,
    )
    return {**core, "idempotency": {"receipt": receipt, "replayed": False}}


def case_application_binding_get(request: httpx.Request) -> dict[str, Any]:
    binding_id = "acb_01J0000000000001"
    return {
        **_v5_core(request, resource_kind="application_case_binding", resource_field="application_case_binding"),
        "application_case_binding": _binding_envelope(request, binding_id),
    }


def _revision_envelope(request: httpx.Request, revision_id: str) -> dict[str, Any]:
    case_digest = "sha256:" + "c" * 64
    return {
        "acceptance_criteria_revision_id": revision_id,
        "workspace_id": request.headers["x-caseloop-workspace-id"],
        "exact_case_binding": {
            "case_id": "case_01J0000000000001",
            "case_revision": 1,
            "case_digest": case_digest,
        },
        "exact_resolution_contract_binding": {
            "kind": "RESOLUTION_CONTRACT",
            "revision": None,
            "digest": None,
            "materialization": "DECLARED_BY_CASE",
        },
        "confirmation_status": "PROPOSED",
        "proposer_principal": "prn_01J0000000000001",
        "proposed_at": "2026-08-11T10:00:00Z",
        "confirmer_principal": None,
        "confirmed_at": None,
        "exact_previous_proposed_revision_binding": None,
        "acceptance_source": {"kind": "github_issue", "repo": "simonw/llm", "number": 1466},
        "reproducer_input": {"kind": "code"},
        "reproducer_environment": None,
        "expected_behavior": {"summary": "schema_dsl must not crash"},
        "oracle_or_evaluator": None,
        "applicable_workload_profile": {"name": "cli-once"},
        "applicable_deployment_profile": {"name": "local-shadow"},
        "acceptance_digest": "sha256:" + "f" * 64,
        "record_envelope": {
            "schema_version": "2.0",
            "workspace_id": request.headers["x-caseloop-workspace-id"],
            "revision": 1,
            "recorded_by_principal": "prn_01J0000000000001",
            "recorded_at": "2026-08-11T10:00:00Z",
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
            "record_digest": "sha256:" + "e" * 64,
            "authority_receipt_id": "arec_01J0000000000001",
        },
    }


def acceptance_propose(request: httpx.Request) -> dict[str, Any]:
    revision_id = "acr_01J0000000000001"
    core = {
        **_v5_core(request, resource_kind="acceptance_criteria_revision", resource_field="acceptance_criteria_revision"),
        "acceptance_criteria_revision": _revision_envelope(request, revision_id),
    }
    core["audit_ref"] = "audit://aud_01J0000000000007"
    receipt = _case_v2_receipt(
        request,
        intent="acceptance-criteria.propose",
        resource_kind="acceptance_criteria_revision",
        resource_id=revision_id,
        audit_ref=core["audit_ref"],
        core=core,
    )
    return {**core, "idempotency": {"receipt": receipt, "replayed": False}}


def acceptance_get(request: httpx.Request) -> dict[str, Any]:
    return {
        **_v5_core(request, resource_kind="acceptance_criteria_revision", resource_field="x"),
        "exact_case_binding": {
            "case_id": "case_01J0000000000001",
            "case_revision": 1,
            "case_digest": "sha256:" + "c" * 64,
        },
        "case_readiness": "NEEDS_ACCEPTANCE_CRITERIA",
        "revisions": [_revision_envelope(request, "acr_01J0000000000001")],
        "next_action": {
            "code": "CONFIRM_ACCEPTANCE_CRITERIA",
            "command": "case acceptance-criteria confirm",
        },
    }


def acceptance_confirm(request: httpx.Request) -> dict[str, Any]:
    revision_id = request.url.path.split("/")[-1][: -len(":confirm")]
    core = {
        **_v5_core(request, resource_kind="acceptance_criteria_revision", resource_field="acceptance_criteria_revision"),
        "acceptance_criteria_revision": _revision_envelope(request, revision_id),
    }
    core["audit_ref"] = "audit://aud_01J0000000000007"
    receipt = _case_v2_receipt(
        request,
        intent="acceptance-criteria.confirm",
        resource_kind="acceptance_criteria_revision",
        resource_id=revision_id,
        audit_ref=core["audit_ref"],
        core=core,
    )
    return {**core, "idempotency": {"receipt": receipt, "replayed": False}}
