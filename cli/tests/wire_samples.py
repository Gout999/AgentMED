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
        request.headers["x-agentmed-workspace-id"],
        request.headers["x-request-id"],
    )


def capabilities(request: httpx.Request) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    if request.url.path == "/api/v2/capabilities":
        return {
            "schema_version": "2.0",
            "workspace_id": workspace_id,
            "request_id": request_id,
            "audit_ref": "audit://aud_01J0000000000011",
            "data": {
                "server_version": "5.1c-test",
                "api_major": 2,
                "contract_version": "2.0",
                "principal": {
                    "principal_id": "prn_01J0000000000001",
                    "principal_type": "human",
                    "scopes": ["capabilities:read"],
                    "credential_expires_at": "2026-08-12T10:00:00Z",
                },
                "enabled_intents": [
                    {
                        "name": "capabilities.get",
                        "scope": "capabilities:read",
                        "execution_mode": "synchronous",
                        "http": True,
                        "cli": True,
                    }
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
                "href": f"https://agentmed.local/api/v1/cases/{case_id}",
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
    if path.endswith("/timeline"):
        return timeline(request, path.split("/")[-2])
    if path.startswith("/api/v1/cases/"):
        return case(request, path.rsplit("/", 1)[-1])
    if path.startswith("/api/v1/evidence/"):
        return evidence(request, path.rsplit("/", 1)[-1])
    if path.startswith("/api/v2/applications/"):
        return application_get(request, path.rsplit("/", 1)[-1])
    if path.startswith("/api/v2/environments/"):
        return environment_get(request, path.rsplit("/", 1)[-1])
    if path.startswith("/api/v2/system-versions/"):
        return system_version_get(request, path.rsplit("/", 1)[-1])
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
        "workspace_id": request.headers["x-agentmed-workspace-id"],
        "request_id": request.headers["x-request-id"],
        "audit_ref": "audit://aud_01J0000000000007",
    }


def _record_envelope(request: httpx.Request, *, digest_char: str) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "workspace_id": request.headers["x-agentmed-workspace-id"],
        "revision": 1,
        "recorded_by_principal": "prn_01J0000000000001",
        "recorded_at": "2026-08-11T10:00:00Z",
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
        "record_digest": "sha256:" + digest_char * 64,
        "authority_receipt_id": "arec_01J0000000000001",
    }


def application_get(request: httpx.Request, application_id: str) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    return {
        "schema_version": "2.0",
        "workspace_id": workspace_id,
        "request_id": request_id,
        "audit_ref": "audit://aud_01J0000000000008",
        "application": {
            "record_envelope": _record_envelope(request, digest_char="a"),
            "application_id": application_id,
            "workspace_id": workspace_id,
            "project_id": "proj_01J0000000000001",
            "slug": "sample-application",
            "display_name": "Sample application",
            "owner_principal_ids": ["prn_01J0000000000001"],
            "criticality": "P1",
            "data_classification": "INTERNAL",
            "governance_mode": "MANAGED",
            "lifecycle_state": "ACTIVE",
        },
    }


def system_version_get(
    request: httpx.Request, system_version_set_id: str
) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    return {
        "schema_version": "2.0",
        "workspace_id": workspace_id,
        "request_id": request_id,
        "audit_ref": "audit://aud_01J0000000000009",
        "system_version_set": {
            "record_envelope": _record_envelope(request, digest_char="d"),
            "system_version_set_id": system_version_set_id,
            "workspace_id": workspace_id,
            "application_id": "app_01J0000000000001",
            "declared_environment_id": "env_01J0000000000001",
            "exact_component_revision_bindings": [],
            "exact_topology_revision_binding": {
                "kind": "TOPOLOGY_REVISION",
                "id": "tpr_01J0000000000001",
                "revision": 1,
                "digest": "sha256:" + "b" * 64,
            },
            "identity_assurance_summary": {},
            "provenance_receipt_ids": [],
            "version_set_digest": "sha256:" + "c" * 64,
            "manifest_digest": None,
            "manifest": None,
        },
    }


def environment_get(request: httpx.Request, environment_id: str) -> dict[str, Any]:
    workspace_id, request_id = _context(request)
    return {
        "schema_version": "2.0",
        "workspace_id": workspace_id,
        "request_id": request_id,
        "audit_ref": "audit://aud_01J0000000000010",
        "environment": {
            "record_envelope": _record_envelope(request, digest_char="f"),
            "environment_id": environment_id,
            "workspace_id": workspace_id,
            "application_id": "app_01J0000000000001",
            "logical_name": "local-shadow",
            "risk_classification": "LOW",
            "lifecycle_state": "ACTIVE",
        },
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
        "workspace_id": request.headers["x-agentmed-workspace-id"],
        "principal_id": "prn_01J0000000000001",
        "intent": intent,
        "idempotency_key": request.headers["x-agentmed-idempotency-key"],
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
    declared_binding: dict[str, Any] = {"kind": "UNKNOWN", "reason": "NOT_DECLARED"}
    if request.content:
        submission = json.loads(request.content)
        candidate = submission.get("declared_system_version_set_binding_or_unknown")
        if isinstance(candidate, dict):
            declared_binding = candidate
    return {
        "application_case_binding_id": binding_id,
        "workspace_id": request.headers["x-agentmed-workspace-id"],
        "exact_case_binding": {
            "case_id": "case_01J0000000000001",
            "case_revision": int(request.url.params.get("case_revision", "1")),
            "case_digest": case_digest,
        },
        "application_id": "app_01J0000000000001",
        "environment_id": "env_01J0000000000001",
        "declared_system_version_set_binding_or_unknown": declared_binding,
        "binding_digest": "sha256:" + "d" * 64,
        "record_envelope": {
            "schema_version": "2.0",
            "workspace_id": request.headers["x-agentmed-workspace-id"],
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
    envelope = _binding_envelope(request, binding_id)
    envelope["exact_case_binding"]["case_id"] = request.url.path.split("/")[-2]
    return {
        **_v5_core(request, resource_kind="application_case_binding", resource_field="application_case_binding"),
        "application_case_binding": envelope,
    }


def _revision_envelope(
    request: httpx.Request,
    revision_id: str,
    *,
    exact_case_binding: dict[str, Any] | None = None,
    confirmed_from: dict[str, Any] | None = None,
) -> dict[str, Any]:
    exact_case_binding = exact_case_binding or {
        "case_id": "case_01J0000000000001",
        "case_revision": 1,
        "case_digest": "sha256:" + "c" * 64,
    }
    confirmed = confirmed_from is not None
    return {
        "acceptance_criteria_revision_id": revision_id,
        "workspace_id": request.headers["x-agentmed-workspace-id"],
        "exact_case_binding": copy.deepcopy(exact_case_binding),
        "resolution_contract_binding_status": {
            "status": "PENDING_MATERIALIZATION",
            "owner": "resolution-contract-controller",
            "materialization_stage": "V5-4",
            "exact_case_binding": copy.deepcopy(exact_case_binding),
        },
        "confirmation_status": "CONFIRMED" if confirmed else "PROPOSED",
        "proposer_principal": "prn_01J0000000000001",
        "proposed_at": "2026-08-11T10:00:00Z",
        "confirmer_principal": "prn_01J0000000000002" if confirmed else None,
        "confirmed_at": "2026-08-11T10:05:00Z" if confirmed else None,
        "exact_previous_proposed_revision_binding": (
            {
                **confirmed_from,
            }
            if confirmed
            else None
        ),
        "reauthentication_credential_binding": (
            {
                "kind": "PUBLIC_CREDENTIAL",
                "credential_id": "cred_01J0000000000002",
                "principal_id": "prn_01J0000000000002",
                "jti_digest": "sha256:" + "8" * 64,
                "claims_digest": "sha256:" + "9" * 64,
                "issued_at": "2026-08-11T10:04:00Z",
                "binding_digest": "sha256:" + "7" * 64,
            }
            if confirmed
            else None
        ),
        "acceptance_source": {"kind": "github_issue", "repo": "simonw/llm", "number": 1466},
        "reproducer_input": {"kind": "code"},
        "reproducer_environment": None,
        "expected_behavior": {"summary": "schema_dsl must not crash"},
        "oracle_or_evaluator": None,
        "applicable_workload_profile": {"name": "cli-once"},
        "applicable_deployment_profile": {"name": "local-shadow"},
        "acceptance_digest": "sha256:" + ("6" if confirmed else "f") * 64,
        "record_envelope": {
            "schema_version": "2.0",
            "workspace_id": request.headers["x-agentmed-workspace-id"],
            "revision": 1,
            "recorded_by_principal": "prn_01J0000000000001",
            "recorded_at": "2026-08-11T10:00:00Z",
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)",
            "record_digest": "sha256:" + ("6" if confirmed else "e") * 64,
            "authority_receipt_id": "arec_01J0000000000001",
        },
    }


def acceptance_propose(request: httpx.Request) -> dict[str, Any]:
    revision_id = "acr_01J0000000000001"
    submission = json.loads(request.content)
    exact_case_binding = {
        "case_id": submission["case_id"],
        "case_revision": submission["case_revision"],
        "case_digest": submission["case_digest"],
    }
    core = {
        **_v5_core(request, resource_kind="acceptance_criteria_revision", resource_field="acceptance_criteria_revision"),
        "acceptance_criteria_revision": _revision_envelope(
            request, revision_id, exact_case_binding=exact_case_binding
        ),
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
    exact_case_binding = {
        "case_id": request.url.path.split("/")[-2],
        "case_revision": int(request.url.params.get("case_revision", "1")),
        "case_digest": "sha256:" + "c" * 64,
    }
    return {
        **_v5_core(request, resource_kind="acceptance_criteria_revision", resource_field="x"),
        "exact_case_binding": exact_case_binding,
        "case_readiness": "NEEDS_ACCEPTANCE_CRITERIA",
        "revisions": [
            _revision_envelope(
                request,
                "acr_01J0000000000001",
                exact_case_binding=exact_case_binding,
            )
        ],
        "next_action": {
            "code": "CONFIRM_ACCEPTANCE_CRITERIA",
            "command": "case acceptance-criteria confirm",
        },
    }


def acceptance_confirm(request: httpx.Request) -> dict[str, Any]:
    proposed_revision_id = request.url.path.split("/")[-1][: -len(":confirm")]
    submission = json.loads(request.content)
    proposed_binding = submission["exact_proposed_revision_binding"]
    assert proposed_binding["id"] == proposed_revision_id
    revision_id = "acr_01J0000000000002"
    core = {
        **_v5_core(request, resource_kind="acceptance_criteria_revision", resource_field="acceptance_criteria_revision"),
        "acceptance_criteria_revision": _revision_envelope(
            request, revision_id, confirmed_from=proposed_binding
        ),
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
