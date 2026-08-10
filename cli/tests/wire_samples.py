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
    if path == "/api/v1/capabilities":
        return capabilities(request)
    if path == "/api/v1/signals":
        return signal(request)
    if path.endswith("/timeline"):
        return timeline(request, path.split("/")[-2])
    if path.startswith("/api/v1/cases/"):
        return case(request, path.rsplit("/", 1)[-1])
    if path.startswith("/api/v1/evidence/"):
        return evidence(request, path.rsplit("/", 1)[-1])
    raise AssertionError(path)
