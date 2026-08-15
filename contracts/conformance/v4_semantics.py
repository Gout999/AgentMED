"""Reusable cross-field and cross-document semantics for AgentMED v4 contracts.

JSON Schema remains responsible for payload shape.  These checks enforce
relationships that Draft 2020-12 cannot express without non-portable
extensions, and therefore must run when a v4 evidence bundle is accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from v4_integrity import (
    candidate_scope_violations,
    compute_content_digest,
    exact_record_binding,
    evaluation_plan_violations,
    gate_chain_violations,
    model_call_receipt_violations,
    model_independence_key,
    resolution_reviewed_content_digest,
    workorder_chain_violations,
    IMMUTABLE_DIGEST_FIELDS,
    record_integrity_violations,
)


Document = Mapping[str, Any]
Bundle = Mapping[str, Any]

_V4_ROOT = Path(__file__).resolve().parents[1] / "v4"
_AUTHORITY_CATALOG = yaml.safe_load(
    (_V4_ROOT / "aggregate-ownership.yaml").read_text(encoding="utf-8")
)["record_authority"]

_AUTHORITY_DOCUMENTS = {
    "resolution_contract": "resolution_contract",
    "candidate_contract": "candidate_contract",
    "candidate_revision": "candidate_revision",
    "evaluation_plan": "evaluation_plan",
    "proposal": "proposal",
    "delegation_proposal": "proposal",
    "proposal_decision": "proposal_decision",
    "delegation_decision": "proposal_decision",
    "gate_report": "gate_report",
    "workorder": "workorder",
    "agent_manifest": "agent_manifest",
    "participation_manifest": "participation_manifest",
    "worker_task": "worker_task",
    "worker_task_issuance_snapshot": "worker_task",
    "worker_task_claim_snapshot": "worker_task",
    "attempt": "attempt",
    "attempt_claim_snapshot": "attempt",
    "proposal_attempt_snapshot": "attempt",
    "capability_lease": "capability_lease",
    "capability_current_snapshot": "capability_lease",
    "agent_intent": "agent_intent",
    "skill_manifest": "skill_manifest",
    "mcp_manifest": "mcp_manifest",
    "trace_evidence_receipt": "trace_evidence_receipt",
}

_AUTHORITY_COLLECTIONS = {
    "identity_attempts": "attempt",
    "identity_agent_manifests": "agent_manifest",
    "model_call_receipts": "model_call_receipt",
    "resolution_review_receipts": "resolution_review_receipt",
    "gate_track_receipts": "gate_track_receipt",
}

_RECORD_KIND = {
    "resolution_contract": "RESOLUTION_CONTRACT",
    "candidate_contract": "CANDIDATE_CONTRACT",
    "candidate_revision": "CANDIDATE_REVISION",
    "evaluation_plan": "EVALUATION_PLAN",
    "proposal": "PROPOSAL",
    "proposal_decision": "PROPOSAL_DECISION",
    "gate_report": "GATE_REPORT",
    "workorder": "WORKORDER",
    "agent_manifest": "AGENT_MANIFEST",
    "participation_manifest": "PARTICIPATION_MANIFEST",
    "worker_task": "WORKER_TASK",
    "attempt": "ATTEMPT",
    "capability_lease": "CAPABILITY_LEASE",
    "agent_intent": "AGENT_INTENT",
    "skill_manifest": "SKILL_MANIFEST",
    "mcp_manifest": "MCP_MANIFEST",
    "trace_evidence_receipt": "TRACE_EVIDENCE_RECEIPT",
    "model_call_receipt": "MODEL_CALL_RECEIPT",
    "resolution_review_receipt": "RESOLUTION_REVIEW_RECEIPT",
    "gate_track_receipt": "GATE_TRACK_RECEIPT",
}

_CONTROLLER_ACTOR_FIELDS = {
    "resolution_contract": "frozen_by_principal",
    "candidate_contract": "frozen_by_principal",
    "candidate_revision": "recorded_by_principal",
    "evaluation_plan": "frozen_by_principal",
    "proposal_decision": "decided_by_principal",
    "gate_report": "created_by_principal",
    "workorder": "created_by_principal",
    "participation_manifest": "frozen_by_principal",
}


@dataclass(frozen=True)
class SemanticViolation:
    """One stable, machine-readable semantic contract violation."""

    code: str
    path: tuple[str | int, ...]
    message: str


class SemanticValidationError(ValueError):
    """Raised when one or more v4 semantic invariants fail."""

    def __init__(self, violations: Sequence[SemanticViolation]) -> None:
        self.violations = tuple(violations)
        detail = "; ".join(
            f"{violation.code}@{'.'.join(map(str, violation.path))}"
            for violation in self.violations
        )
        super().__init__(detail)


def _violation(
    code: str, path: tuple[str | int, ...], message: str
) -> SemanticViolation:
    return SemanticViolation(code=code, path=path, message=message)


def _integrity_as_semantic(
    violations: Sequence[Any], root: str | None = None
) -> list[SemanticViolation]:
    return [
        _violation(
            item.code,
            ((root,) if root is not None else ()) + tuple(item.path),
            item.message,
        )
        for item in violations
    ]


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def public_principal_context_violations(
    context: Document,
) -> tuple[SemanticViolation, ...]:
    """Validate the accepted, server-resolved public authorization context."""

    violations: list[SemanticViolation] = []
    times: dict[str, datetime] = {}
    for field in ("not_before", "evaluated_at", "expires_at"):
        value = context.get(field)
        try:
            if not isinstance(value, str):
                raise TypeError(field)
            times[field] = _parse_time(value)
        except (TypeError, ValueError):
            violations.append(
                _violation(
                    "public_principal_context.invalid_time",
                    ("public_principal_context", field),
                    "accepted context time bounds must be parseable RFC 3339 timestamps",
                )
            )
    if len(times) == 3 and not (
        times["not_before"] <= times["evaluated_at"] < times["expires_at"]
    ):
        violations.append(
            _violation(
                "public_principal_context.outside_validity",
                ("public_principal_context", "evaluated_at"),
                "accepted context requires not_before <= evaluated_at < expires_at",
            )
        )

    requested = context.get("requested_context")
    if not isinstance(requested, Mapping):
        violations.append(
            _violation(
                "public_principal_context.requested_context_missing",
                ("public_principal_context", "requested_context"),
                "accepted context must bind the exact requested authorization context",
            )
        )
    else:
        if requested.get("workspace_id") != context.get("workspace_id"):
            violations.append(
                _violation(
                    "public_principal_context.workspace_mismatch",
                    ("public_principal_context", "requested_context", "workspace_id"),
                    "requested workspace must equal the server-resolved workspace",
                )
            )
        for singular, plural in (
            ("project_id", "project_ids"),
            ("environment_id", "environment_ids"),
        ):
            requested_id = requested.get(singular)
            grants = context.get(plural)
            if requested_id is not None and (
                not isinstance(grants, Sequence)
                or isinstance(grants, (str, bytes))
                or requested_id not in grants
            ):
                violations.append(
                    _violation(
                        f"public_principal_context.{singular}_not_granted",
                        ("public_principal_context", "requested_context", singular),
                        f"requested {singular} must be null or present in the resolved grants",
                    )
                )
        scopes = context.get("scopes")
        required_scope = requested.get("required_scope")
        if (
            not isinstance(scopes, Sequence)
            or isinstance(scopes, (str, bytes))
            or required_scope not in scopes
        ):
            violations.append(
                _violation(
                    "public_principal_context.required_scope_not_granted",
                    ("public_principal_context", "requested_context", "required_scope"),
                    "the required scope must be present in the accepted resolved scopes",
                )
            )

    if "agentmed-public-api" not in context.get("audiences", ()):
        violations.append(
            _violation(
                "public_principal_context.audience_not_accepted",
                ("public_principal_context", "audiences"),
                "accepted context must include the AgentMED public API audience",
            )
        )
    if context.get("revoked_at") is not None:
        violations.append(
            _violation(
                "public_principal_context.revoked_credential_accepted",
                ("public_principal_context", "revoked_at"),
                "a revoked credential cannot produce an accepted context",
            )
        )
    for field in (
        "jti",
        "raw_jti",
        "token",
        "raw_token",
        "access_token",
        "authorization",
    ):
        if field in context:
            violations.append(
                _violation(
                    "public_principal_context.raw_credential_exposed",
                    ("public_principal_context", field),
                    "accepted context may expose only jti_digest, never raw credential material",
                )
            )
    return tuple(violations)


def _authority_context_violations(bundle: Bundle) -> list[SemanticViolation]:
    subjects = [
        ((bundle_key,), record_name, bundle[bundle_key])
        for bundle_key, record_name in _AUTHORITY_DOCUMENTS.items()
        if isinstance(bundle.get(bundle_key), Mapping)
        and "authority_receipt_id" in bundle[bundle_key]
    ]
    for collection_key, record_name in _AUTHORITY_COLLECTIONS.items():
        collection = bundle.get(collection_key)
        if isinstance(collection, Sequence) and not isinstance(
            collection, (str, bytes)
        ):
            subjects.extend(
                ((collection_key, index), record_name, item)
                for index, item in enumerate(collection)
                if isinstance(item, Mapping) and "authority_receipt_id" in item
            )
    if not subjects:
        return []

    violations: list[SemanticViolation] = []
    registrations = bundle.get("controller_registrations")
    receipts = bundle.get("authority_receipts")
    events = bundle.get("authority_events")
    audits = bundle.get("authority_audits")
    for name, value in (
        ("controller_registrations", registrations),
        ("authority_receipts", receipts),
        ("authority_events", events),
        ("authority_audits", audits),
    ):
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            violations.append(
                _violation(
                    "authority.context_missing",
                    (name,),
                    f"controller-recorded snapshots require {name}",
                )
            )
    if violations:
        return violations

    registration_by_id = {
        item.get("controller_registration_id"): item
        for item in registrations
        if isinstance(item, Mapping)
    }
    receipt_by_id = {
        item.get("authority_receipt_id"): item
        for item in receipts
        if isinstance(item, Mapping)
    }
    event_by_id = {
        item.get("event_id"): item
        for item in events
        if isinstance(item, Mapping)
    }
    audit_by_ref = {
        item.get("audit_ref"): item
        for item in audits
        if isinstance(item, Mapping)
    }

    if len(registration_by_id) != len(registrations):
        violations.append(
            _violation(
                "authority.registration_id_duplicate",
                ("controller_registrations",),
                "ControllerRegistration ids must be unique",
            )
        )
    if len(receipt_by_id) != len(receipts):
        violations.append(
            _violation(
                "authority.receipt_id_duplicate",
                ("authority_receipts",),
                "AuthorityReceipt ids must be unique",
            )
        )
    if len(event_by_id) != len(events):
        violations.append(
            _violation(
                "authority.event_id_duplicate",
                ("authority_events",),
                "authority event ids must be unique",
            )
        )
    if len(audit_by_ref) != len(audits):
        violations.append(
            _violation(
                "authority.audit_ref_duplicate",
                ("authority_audits",),
                "authority audit references must be unique",
            )
        )

    subject_identities: dict[tuple[Any, Any, Any], tuple[str | int, ...]] = {}
    for subject_path, record_name, subject in subjects:
        binding = exact_record_binding(record_name, subject)
        identity = (binding["kind"], binding["id"], binding["revision"])
        if identity in subject_identities:
            violations.append(
                _violation(
                    "authority.subject_identity_duplicate",
                    subject_path,
                    "one immutable kind/id/revision may appear only once in an authority bundle",
                )
            )
        else:
            subject_identities[identity] = subject_path

    receipt_subject_identities: dict[
        tuple[Any, Any, Any], tuple[str | int, ...]
    ] = {}
    for index, receipt in enumerate(receipts):
        if not isinstance(receipt, Mapping):
            continue
        subject = receipt.get("subject")
        if not isinstance(subject, Mapping):
            continue
        identity = (subject.get("kind"), subject.get("id"), subject.get("revision"))
        if identity in receipt_subject_identities:
            violations.append(
                _violation(
                    "authority.receipt_subject_identity_duplicate",
                    ("authority_receipts", index, "subject"),
                    "AuthorityReceipts may not equivocate for one immutable kind/id/revision",
                )
            )
        else:
            receipt_subject_identities[identity] = (
                "authority_receipts",
                index,
                "subject",
            )

    expected_ownership_digest = compute_content_digest(
        yaml.safe_load(
            (_V4_ROOT / "aggregate-ownership.yaml").read_text(encoding="utf-8")
        )
    )
    expected_event_digest = compute_content_digest(
        yaml.safe_load(
            (_V4_ROOT / "events" / "events.yaml").read_text(encoding="utf-8")
        )
    )

    for subject_path, record_name, subject in subjects:
        kind = _RECORD_KIND[record_name]
        expected_subject = exact_record_binding(record_name, subject)
        for item in record_integrity_violations(
            subject, IMMUTABLE_DIGEST_FIELDS[record_name]
        ):
            violations.append(
                _violation(
                    item.code,
                    (*subject_path, *item.path),
                    item.message,
                )
            )

        receipt_id = subject["authority_receipt_id"]
        receipt = receipt_by_id.get(receipt_id)
        if receipt is None:
            violations.append(
                _violation(
                    "authority.receipt_missing",
                    (*subject_path, "authority_receipt_id"),
                    "subject must resolve its preallocated AuthorityReceipt id",
                )
            )
            continue
        for item in record_integrity_violations(
            receipt, IMMUTABLE_DIGEST_FIELDS["authority_receipt"]
        ):
            violations.append(
                _violation(
                    item.code,
                    ("authority_receipts", receipt_id, *item.path),
                    item.message,
                )
            )
        if receipt.get("subject") != expected_subject:
            violations.append(
                _violation(
                    "authority.subject_binding_mismatch",
                    ("authority_receipts", receipt_id, "subject"),
                    "AuthorityReceipt must bind the exact subject kind, id, revision and digest",
                )
            )
        if receipt.get("workspace_id") != subject.get("workspace_id"):
            violations.append(
                _violation(
                    "authority.workspace_mismatch",
                    ("authority_receipts", receipt_id, "workspace_id"),
                    "subject and AuthorityReceipt must share one workspace",
                )
            )

        registration_binding = receipt.get("controller_registration", {})
        registration = registration_by_id.get(registration_binding.get("id"))
        if registration is None:
            violations.append(
                _violation(
                    "authority.registration_missing",
                    ("authority_receipts", receipt_id, "controller_registration"),
                    "AuthorityReceipt must resolve an exact ControllerRegistration",
                )
            )
            continue
        for item in record_integrity_violations(
            registration, IMMUTABLE_DIGEST_FIELDS["controller_registration"]
        ):
            violations.append(
                _violation(
                    item.code,
                    ("controller_registrations", registration_binding.get("id"), *item.path),
                    item.message,
                )
            )
        if registration_binding != exact_record_binding(
            "controller_registration", registration
        ):
            violations.append(
                _violation(
                    "authority.registration_binding_mismatch",
                    ("authority_receipts", receipt_id, "controller_registration"),
                    "receipt must bind the exact registration revision and digest",
                )
            )
        catalog = _AUTHORITY_CATALOG[kind]
        command = receipt.get("command")
        event_type = receipt.get("event_type")
        expected_events = catalog["command_events"].get(command, ())
        if (
            receipt.get("resource") != catalog["resource"]
            or receipt.get("owner") != catalog["owner"]
            or event_type not in expected_events
        ):
            violations.append(
                _violation(
                    "authority.command_event_mapping_mismatch",
                    ("authority_receipts", receipt_id),
                    "resource, owner, command and event must match one exact record-authority mapping",
                )
            )
        if (
            registration.get("state") != "ACTIVE"
            or registration.get("workspace_id") != subject.get("workspace_id")
            or registration.get("owner") != receipt.get("owner")
            or registration.get("controller_principal")
            != receipt.get("controller_principal")
            or command not in registration.get("allowed_commands", ())
        ):
            violations.append(
                _violation(
                    "authority.registration_not_authorized",
                    ("controller_registrations", registration.get("controller_registration_id")),
                    "registration must be active, in-workspace, owner/principal exact, and allow the command",
                )
            )
        if (
            registration.get("ownership_contract", {}).get("digest")
            != expected_ownership_digest
            or registration.get("event_catalog", {}).get("digest")
            != expected_event_digest
        ):
            violations.append(
                _violation(
                    "authority.catalog_digest_mismatch",
                    ("controller_registrations", registration.get("controller_registration_id")),
                    "registration must freeze the exact ownership and event catalogs",
                )
            )
        recorded_at = _parse_time(receipt["recorded_at"])
        expires_at = registration.get("expires_at")
        if recorded_at < _parse_time(registration["valid_from"]) or (
            expires_at is not None and recorded_at >= _parse_time(expires_at)
        ):
            violations.append(
                _violation(
                    "authority.registration_outside_validity",
                    ("authority_receipts", receipt_id, "recorded_at"),
                    "receipt time must fall within registration validity",
                )
            )
        if kind == "CAPABILITY_LEASE" and subject.get("issued_by_principal") != receipt.get(
            "controller_principal"
        ):
            violations.append(
                _violation(
                    "authority.capability_issuer_mismatch",
                    (*subject_path, "issued_by_principal"),
                    "CapabilityLease issuer must be the registered capability Controller",
                )
            )
        actor_field = _CONTROLLER_ACTOR_FIELDS.get(record_name)
        if actor_field is not None and subject.get(actor_field) != receipt.get(
            "controller_principal"
        ):
            violations.append(
                _violation(
                    f"authority.{record_name}_actor_mismatch",
                    (*subject_path, actor_field),
                    "the business-record authority actor must be the registered owning Controller principal",
                )
            )
        if record_name == "proposal_decision" and (
            receipt.get("event_id") != subject.get("decision_event_id")
            or receipt.get("transaction_id") != subject.get("transaction_id")
            or receipt.get("audit_ref") != subject.get("audit_ref")
        ):
            violations.append(
                _violation(
                    "authority.proposal_decision_receipt_mismatch",
                    (*subject_path, "authority_receipt_id"),
                    "ProposalDecision authority must bind its own decision event, transaction and audit row",
                )
            )
        if record_name == "candidate_revision" and isinstance(
            bundle.get("proposal_decision"), Mapping
        ):
            downstream = bundle["proposal_decision"].get("downstream_event", {})
            if (
                receipt.get("event_id") != downstream.get("event_id")
                or receipt.get("event_type") != downstream.get("event_type")
                or receipt.get("transaction_id") != downstream.get("transaction_id")
            ):
                violations.append(
                    _violation(
                        "authority.candidate_revision_causation_mismatch",
                        (*subject_path, "authority_receipt_id"),
                        "CandidateRevision authority event must be the exact ProposalDecision downstream event in the same transaction",
                    )
                )

        event = event_by_id.get(receipt.get("event_id"))
        if event is None:
            violations.append(
                _violation(
                    "authority.event_missing",
                    ("authority_receipts", receipt_id, "event_id"),
                    "receipt must resolve the authoritative same-transaction event",
                )
            )
        elif (
            event.get("contract_version") != "v4"
            or event.get("aggregate_type") != receipt.get("resource")
            or event.get("event_type") != event_type
            or event.get("transaction_id") != receipt.get("transaction_id")
            or event.get("actor_principal") != receipt.get("controller_principal")
            or event.get("authority_receipt_id") != receipt_id
            or event.get("subject") != expected_subject
        ):
            violations.append(
                _violation(
                    "authority.event_binding_mismatch",
                    ("authority_events", receipt.get("event_id")),
                    "event must bind v4 routing, receipt, subject, type, actor and transaction exactly",
                )
            )
        audit = audit_by_ref.get(receipt.get("audit_ref"))
        if audit is None:
            violations.append(
                _violation(
                    "authority.audit_missing",
                    ("authority_receipts", receipt_id, "audit_ref"),
                    "receipt must resolve the authoritative same-transaction audit row",
                )
            )
        elif (
            audit.get("transaction_id") != receipt.get("transaction_id")
            or audit.get("controller_principal") != receipt.get("controller_principal")
            or audit.get("authority_receipt_id") != receipt_id
            or audit.get("subject") != expected_subject
        ):
            violations.append(
                _violation(
                    "authority.audit_binding_mismatch",
                    ("authority_audits", receipt.get("audit_ref")),
                    "audit must bind receipt, subject, principal and transaction exactly",
                )
            )
    return violations


def _record_documents(bundle: Bundle, record_name: str) -> list[Document]:
    documents = [
        bundle[key]
        for key, name in _AUTHORITY_DOCUMENTS.items()
        if name == record_name and isinstance(bundle.get(key), Mapping)
    ]
    for key, name in _AUTHORITY_COLLECTIONS.items():
        if name != record_name:
            continue
        collection = bundle.get(key)
        if isinstance(collection, Sequence) and not isinstance(
            collection, (str, bytes)
        ):
            documents.extend(item for item in collection if isinstance(item, Mapping))
    return documents


def _resolve_exact_record(
    bundle: Bundle, record_name: str, binding: Document
) -> Document | None:
    matches = [
        document
        for document in _record_documents(bundle, record_name)
        if exact_record_binding(record_name, document) == binding
    ]
    return matches[0] if len(matches) == 1 else None


def _resolve_agent_execution(
    identity: Document,
    bundle: Bundle,
    prefix: str,
) -> tuple[dict[str, Document] | None, list[SemanticViolation]]:
    violations: list[SemanticViolation] = []
    attempt = _resolve_exact_record(bundle, "attempt", identity.get("attempt", {}))
    manifest = _resolve_exact_record(
        bundle, "agent_manifest", identity.get("agent_manifest", {})
    )
    model_receipt = _resolve_exact_record(
        bundle, "model_call_receipt", identity.get("model_call_receipt", {})
    )
    for name, document in (
        ("attempt", attempt),
        ("agent_manifest", manifest),
        ("model_call_receipt", model_receipt),
    ):
        if document is None:
            violations.append(
                _violation(
                    f"{prefix}.{name}_missing",
                    (prefix, name),
                    f"agent execution must resolve one exact {name} record",
                )
            )
    if violations:
        return None, violations
    assert attempt is not None and manifest is not None and model_receipt is not None
    if attempt.get("state") != "SUCCEEDED":
        violations.append(
            _violation(
                f"{prefix}.attempt_not_terminal",
                (prefix, "attempt"),
                "review/generation/judge identity must bind a succeeded terminal Attempt",
            )
        )
    resolution = bundle.get("resolution_contract")
    if isinstance(resolution, Mapping) and model_receipt.get("case_id") != resolution.get(
        "case_id"
    ):
        violations.append(
            _violation(
                f"{prefix}.model_call_case_mismatch",
                (prefix, "model_call_receipt", "case_id"),
                "ModelCallReceipt must remain in the governed Resolution Case",
            )
        )
    if (
        identity.get("principal_id") != attempt.get("executor_principal")
        or identity.get("principal_id") != manifest.get("principal_id")
        or attempt.get("agent_manifest")
        != exact_record_binding("agent_manifest", manifest)
    ):
        violations.append(
            _violation(
                f"{prefix}.principal_manifest_mismatch",
                (prefix, "principal_id"),
                "execution principal, Attempt and immutable AgentManifest must agree",
            )
        )
    attempt_model = attempt.get("model", {})
    if (
        attempt_model.get("model_call_receipt")
        != exact_record_binding("model_call_receipt", model_receipt)
        or attempt_model.get("model_call_receipt_digest")
        != model_receipt.get("model_call_receipt_digest")
        or model_receipt.get("outcome") != "SUCCEEDED"
    ):
        violations.append(
            _violation(
                f"{prefix}.model_call_binding_mismatch",
                (prefix, "model_call_receipt"),
                "terminal Attempt must bind the exact successful ModelCallReceipt",
            )
        )
    call_snapshot = _resolve_exact_record(
        bundle, "attempt", model_receipt.get("attempt_snapshot", {})
    )
    if call_snapshot is None:
        violations.append(
            _violation(
                f"{prefix}.call_time_attempt_missing",
                (prefix, "model_call_receipt", "attempt_snapshot"),
                "ModelCallReceipt must resolve its call-time Attempt snapshot",
            )
        )
    else:
        violations.extend(
            _integrity_as_semantic(
                model_call_receipt_violations(
                    model_receipt,
                    call_snapshot,
                    manifest,
                ),
                prefix,
            )
        )
        if (
            call_snapshot.get("attempt_id") != attempt.get("attempt_id")
            or call_snapshot.get("revision", 0) >= attempt.get("revision", 0)
        ):
            violations.append(
                _violation(
                    f"{prefix}.call_time_attempt_lineage_mismatch",
                    (prefix, "model_call_receipt", "attempt_snapshot"),
                    "model call snapshot must be an earlier revision of the same Attempt",
                )
            )
    if attempt.get("completed_at") is not None and _parse_time(
        model_receipt["recorded_at"]
    ) > _parse_time(attempt["completed_at"]):
        violations.append(
            _violation(
                f"{prefix}.model_call_recorded_after_terminal",
                (prefix, "model_call_receipt", "recorded_at"),
                "ModelCallReceipt must be recorded before the terminal Attempt snapshot",
            )
        )
    return {
        "attempt": attempt,
        "agent_manifest": manifest,
        "model_call_receipt": model_receipt,
    }, violations


def _distinct_model_dimensions(
    left: Document,
    right: Document,
    dimensions: Sequence[str],
    code_prefix: str,
    path: tuple[str | int, ...],
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    left_basis = left.get("independence_basis", {})
    right_basis = right.get("independence_basis", {})
    for dimension in dimensions:
        if left_basis.get(dimension) == right_basis.get(dimension):
            violations.append(
                _violation(
                    f"{code_prefix}.dimension_not_distinct.{dimension}",
                    (*path, dimension),
                    f"independence policy requires distinct {dimension}",
                )
            )
    return violations


def _validate_resolution_review(
    resolution: Document,
    bundle: Bundle,
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    receipt = _resolve_exact_record(
        bundle, "resolution_review_receipt", resolution.get("review_receipt", {})
    )
    if receipt is None:
        return [
            _violation(
                "resolution_contract.review_receipt_missing",
                ("resolution_contract", "review_receipt"),
                "frozen Resolution must resolve one exact ResolutionReviewReceipt",
            )
        ]
    if (
        receipt.get("workspace_id") != resolution.get("workspace_id")
        or receipt.get("case_id") != resolution.get("case_id")
        or receipt.get("resolution_contract_id")
        != resolution.get("resolution_contract_id")
        or receipt.get("reviewed_content_digest")
        != resolution_reviewed_content_digest(resolution)
        or receipt.get("review_policy") != resolution.get("review_policy")
        or receipt.get("proposer") != resolution.get("proposer")
        or receipt.get("verdict") != "APPROVED"
        or _parse_time(receipt["reviewed_at"]) > _parse_time(resolution["frozen_at"])
    ):
        violations.append(
            _violation(
                "resolution_contract.review_receipt_mismatch",
                ("resolution_contract", "review_receipt"),
                "review receipt must approve the exact proposed content and frozen policy before freeze",
            )
        )
    proposer, proposer_violations = _resolve_agent_execution(
        resolution.get("proposer", {}), bundle, "resolution_proposer"
    )
    violations.extend(proposer_violations)
    mode = resolution.get("review_policy", {}).get("mode")
    reviewer = receipt.get("reviewer", {})
    if mode in {"AGENT_SEPARATE", "AGENT_INDEPENDENT"}:
        reviewer_execution = reviewer.get("execution", {})
        resolved_reviewer, reviewer_violations = _resolve_agent_execution(
            reviewer_execution, bundle, "resolution_reviewer"
        )
        violations.extend(reviewer_violations)
        if proposer is not None and resolved_reviewer is not None:
            proposer_attempt = proposer["attempt"]
            reviewer_attempt = resolved_reviewer["attempt"]
            proposer_manifest = proposer["agent_manifest"]
            reviewer_manifest = resolved_reviewer["agent_manifest"]
            if proposer_attempt.get("attempt_id") == reviewer_attempt.get("attempt_id"):
                violations.append(
                    _violation(
                        "resolution_contract.review_attempt_not_independent",
                        ("resolution_contract", "review_receipt"),
                        "proposer and Agent reviewer must use distinct Attempts",
                    )
                )
            if resolution["proposer"].get("principal_id") == reviewer_execution.get(
                "principal_id"
            ):
                violations.append(
                    _violation(
                        "resolution_contract.review_principal_not_independent",
                        ("resolution_contract", "review_receipt"),
                        "proposer and Agent reviewer must use distinct principals",
                    )
                )
            if (
                proposer_manifest.get("agent_manifest_id")
                == reviewer_manifest.get("agent_manifest_id")
                or proposer_manifest.get("manifest_digest")
                == reviewer_manifest.get("manifest_digest")
            ):
                violations.append(
                    _violation(
                        "resolution_contract.review_manifest_not_independent",
                        ("resolution_contract", "review_receipt"),
                        "proposer and Agent reviewer must use distinct AgentManifest id and digest",
                    )
                )
            if mode == "AGENT_INDEPENDENT":
                proposer_call = proposer["model_call_receipt"]
                reviewer_call = resolved_reviewer["model_call_receipt"]
                if proposer_call.get("independence_key") == reviewer_call.get(
                    "independence_key"
                ):
                    violations.append(
                        _violation(
                            "resolution_contract.review_independence_key_reused",
                            ("resolution_contract", "review_policy"),
                            "AGENT_INDEPENDENT review requires a different complete independence key",
                        )
                    )
                violations.extend(
                    _distinct_model_dimensions(
                        proposer_call,
                        reviewer_call,
                        resolution["review_policy"].get(
                            "required_distinct_dimensions", ()
                        ),
                        "resolution_contract.review",
                        ("resolution_contract", "review_policy", "required_distinct_dimensions"),
                    )
                )
    else:
        if proposer is not None and reviewer.get("principal_id") == resolution[
            "proposer"
        ].get("principal_id"):
            violations.append(
                _violation(
                    "resolution_contract.review_principal_not_independent",
                    ("resolution_contract", "review_receipt"),
                    "human/deterministic reviewer principal must differ from proposer",
                )
            )
    return violations


def _validate_proposal_decision(document: Document) -> list[SemanticViolation]:
    if document["decision"] != "ACCEPTED":
        return []
    violations: list[SemanticViolation] = []
    downstream = document["downstream_event"]
    if downstream["causation_id"] != document["decision_event_id"]:
        violations.append(
            _violation(
                "proposal_decision.causation_mismatch",
                ("proposal_decision", "downstream_event", "causation_id"),
                "the first downstream event must be caused by proposal.accepted",
            )
        )
    if downstream["transaction_id"] != document["transaction_id"]:
        violations.append(
            _violation(
                "proposal_decision.transaction_mismatch",
                ("proposal_decision", "downstream_event", "transaction_id"),
                "ProposalDecision and its first downstream event must share a transaction",
            )
        )
    accepted_ids = {
        document["proposal_id"],
        document["accepted_proposal_id"],
        downstream["accepted_proposal_id"],
    }
    if len(accepted_ids) != 1:
        violations.append(
            _violation(
                "proposal_decision.proposal_binding_mismatch",
                ("proposal_decision", "accepted_proposal_id"),
                "decision and downstream event must bind the exact submitted Proposal",
            )
        )
    return violations


def _validate_attempt_action_order(document: Document) -> list[SemanticViolation]:
    proposal = document.get("pre_controlled_action")
    if not proposal or proposal.get("controlled_action_started_at") is None:
        return []
    if _parse_time(proposal["submitted_at"]) < _parse_time(
        proposal["controlled_action_started_at"]
    ):
        return []
    return [
        _violation(
            "attempt.action_before_proposal",
            ("attempt", "pre_controlled_action", "controlled_action_started_at"),
            "a controlled downstream action must start strictly after its Proposal was submitted",
        )
    ]


def _validate_proposal_author(
    proposal: Document,
    attempt: Document,
    authored_attempt: Document,
    worker_task: Document,
    candidate_contract: Document | None,
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    if proposal["authored_by_attempt_id"] != attempt["attempt_id"]:
        violations.append(
            _violation(
                "proposal.attempt_binding_mismatch",
                ("proposal", "authored_by_attempt_id"),
                "Proposal author must be the exact Attempt in the receipt chain",
            )
        )
    if proposal.get("authored_by_attempt") != exact_record_binding(
        "attempt", authored_attempt
    ) or authored_attempt.get("state") != "OUTPUT_RECORDED":
        violations.append(
            _violation(
                "proposal.author_snapshot_mismatch",
                ("proposal", "authored_by_attempt"),
                "Proposal must bind the exact OUTPUT_RECORDED sandbox snapshot that sealed its artifact",
            )
        )
    if authored_attempt.get("attempt_id") != attempt.get("attempt_id"):
        violations.append(
            _violation(
                "proposal.author_revision_identity_mismatch",
                ("proposal_attempt_snapshot", "attempt_id"),
                "Proposal snapshot and current Attempt must retain one identity",
            )
        )
    if (
        proposal["worker_task_id"] != attempt["worker_task_id"]
        or proposal["worker_task_id"] != worker_task["worker_task_id"]
    ):
        violations.append(
            _violation(
                "proposal.worker_task_mismatch",
                ("proposal", "worker_task_id"),
                "Proposal must bind the exact WorkerTask claimed by its author Attempt",
            )
        )
    if (
        proposal["input_snapshot_digest"] != attempt["input_snapshot_digest"]
        or proposal["input_snapshot_digest"]
        != worker_task["input_snapshot_digest"]
    ):
        violations.append(
            _violation(
                "proposal.input_snapshot_mismatch",
                ("proposal", "input_snapshot_digest"),
                "Proposal must bind the immutable input snapshot used by its author Attempt",
            )
        )
    if proposal["agent_manifest"] != attempt["agent_manifest"]:
        violations.append(
            _violation(
                "proposal.agent_manifest_mismatch",
                ("proposal", "agent_manifest"),
                "Proposal must bind the exact AgentManifest used by its author Attempt",
            )
        )
    if (
        proposal["workspace_id"] != attempt["workspace_id"]
        or proposal["workspace_id"] != worker_task["workspace_id"]
    ):
        violations.append(
            _violation(
                "proposal.workspace_mismatch",
                ("proposal", "workspace_id"),
                "Proposal and its author Attempt must belong to the same workspace",
            )
        )
    if proposal["case_id"] != worker_task["case_id"]:
        violations.append(
            _violation(
                "proposal.case_mismatch",
                ("proposal", "case_id"),
                "Proposal must bind the exact Case carried by its WorkerTask",
            )
        )
    if attempt["assigned_team_id"] != worker_task["assigned_team_id"]:
        violations.append(
            _violation(
                "proposal.attempt_team_mismatch",
                ("attempt", "assigned_team_id"),
                "author Attempt must use the Team assigned by its WorkerTask",
            )
        )
    output_artifact = authored_attempt.get("output_artifact")
    if output_artifact is None or proposal["content_ref"]["digest"] != output_artifact["digest"]:
        violations.append(
            _violation(
                "proposal.output_artifact_mismatch",
                ("proposal", "content_ref", "digest"),
                "Proposal content must be the exact artifact recorded by its author Attempt",
            )
        )
    candidate_binding = proposal["candidate_contract"]
    if candidate_binding is not None and candidate_contract is not None:
        if candidate_binding != exact_record_binding(
            "candidate_contract", candidate_contract
        ):
            violations.append(
                _violation(
                    "proposal.candidate_contract_mismatch",
                    ("proposal", "candidate_contract"),
                    "Proposal must bind the exact frozen CandidateContract",
                )
            )
        if (
            proposal["workspace_id"] != candidate_contract["workspace_id"]
            or proposal["case_id"] != candidate_contract["case_id"]
        ):
            violations.append(
                _violation(
                    "proposal.candidate_context_mismatch",
                    ("proposal", "candidate_contract"),
                    "Proposal and CandidateContract must share workspace and Case",
                )
            )
    expected_principal = attempt["executor_principal"]
    if proposal["authored_by_principal"] != expected_principal:
        violations.append(
            _violation(
                "proposal.author_principal_mismatch",
                ("proposal", "authored_by_principal"),
                "Proposal author principal must equal the author Attempt executor",
            )
        )
    if proposal["submitted_by_principal"] != expected_principal:
        violations.append(
            _violation(
                "proposal.submitter_not_author",
                ("proposal", "submitted_by_principal"),
                "a parent or adapter principal cannot submit a child Attempt Proposal",
            )
        )
    receipt = attempt.get("pre_controlled_action")
    if receipt and (
        receipt["proposal_id"] != proposal["proposal_id"]
        or receipt["proposal_digest"] != proposal["proposal_digest"]
        or receipt["submitted_at"] != proposal["submitted_at"]
    ):
        violations.append(
            _violation(
                "proposal.attempt_receipt_mismatch",
                ("proposal", "proposal_digest"),
                "Attempt pre-controlled-action receipt must bind the exact Proposal and submission time",
            )
        )
    return violations


def _validate_candidate_revision(
    revision: Document,
    resolution: Document,
    candidate_contract: Document,
    evaluation_plan: Document,
    attempt: Document,
    proposal: Document,
    decision: Document,
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    expected_resolution = {
        "id": resolution["resolution_contract_id"],
        "digest": resolution["contract_digest"],
    }
    expected_contract = {
        "id": candidate_contract["candidate_contract_id"],
        "digest": candidate_contract["contract_digest"],
    }
    if revision["resolution_contract"] != expected_resolution:
        violations.append(
            _violation(
                "candidate_revision.resolution_contract_mismatch",
                ("candidate_revision", "resolution_contract"),
                "CandidateRevision must bind the exact ResolutionContract",
            )
        )
    if revision["candidate_contract"] != expected_contract:
        violations.append(
            _violation(
                "candidate_revision.candidate_contract_mismatch",
                ("candidate_revision", "candidate_contract"),
                "CandidateRevision must bind the exact pre-build CandidateContract",
            )
        )
    expected_proposal = exact_record_binding("proposal", proposal)
    if revision["accepted_proposal"] != expected_proposal:
        violations.append(
            _violation(
                "candidate_revision.accepted_proposal_mismatch",
                ("candidate_revision", "accepted_proposal"),
                "CandidateRevision must bind the exact accepted pre-controlled-action Proposal",
            )
        )
    if (
        revision["candidate_id"] != candidate_contract["candidate_id"]
        or revision["revision"] != candidate_contract["planned_revision"]
    ):
        violations.append(
            _violation(
                "candidate_revision.planned_revision_mismatch",
                ("candidate_revision", "revision"),
                "CandidateRevision identity must equal the planned candidate revision",
            )
        )
    if (
        revision["workspace_id"] != resolution["workspace_id"]
        or revision["workspace_id"] != candidate_contract["workspace_id"]
        or revision["case_id"] != resolution["case_id"]
        or revision["case_id"] != candidate_contract["case_id"]
    ):
        violations.append(
            _violation(
                "candidate_revision.subject_context_mismatch",
                ("candidate_revision", "workspace_id"),
                "CandidateRevision, CandidateContract, and Resolution must share workspace and Case",
            )
        )
    if (
        revision["candidate_kind"] != candidate_contract["candidate_kind"]
        or revision["base"] != candidate_contract["base"]
        or revision["target"]["target_ref"]
        != candidate_contract["base"]["target_ref"]
    ):
        violations.append(
            _violation(
                "candidate_revision.build_contract_mismatch",
                ("candidate_revision", "base"),
                "CandidateRevision kind and build base must satisfy CandidateContract",
            )
        )
    repo = attempt.get("repo_context")
    attempt_base = None
    if repo is not None:
        attempt_base = {
            "target_ref": repo["repository_ref"],
            "revision": repo["base_revision"],
            "content_digest": repo["base_digest"],
        }
    if (
        revision["producer_attempt"] != exact_record_binding("attempt", attempt)
        or revision["workspace_id"] != attempt["workspace_id"]
        or revision["input_snapshot_digest"] != attempt["input_snapshot_digest"]
        or revision["base"] != attempt_base
    ):
        violations.append(
            _violation(
                "candidate_revision.producer_attempt_mismatch",
                ("candidate_revision", "producer_attempt"),
                "CandidateRevision must bind the exact producer Attempt input and base",
            )
        )
    if attempt["state"] != "SUCCEEDED":
        violations.append(
            _violation(
                "candidate_revision.producer_attempt_not_succeeded",
                ("attempt", "state"),
                "only a succeeded producer Attempt may record CandidateRevision",
            )
        )
    if (
        revision["candidate_artifact"] != attempt.get("output_artifact")
        or revision["candidate_artifact"] != proposal["content_ref"]
    ):
        violations.append(
            _violation(
                "candidate_revision.output_artifact_mismatch",
                ("candidate_revision", "candidate_artifact"),
                "CandidateRevision must bind the exact producer Attempt output artifact",
            )
        )
    action_started_at = (attempt.get("pre_controlled_action") or {}).get(
        "controlled_action_started_at"
    )
    if (
        decision["decision"] != "ACCEPTED"
        or decision["proposal_id"] != proposal["proposal_id"]
        or decision["proposal_digest"] != proposal["proposal_digest"]
        or decision["accepted_proposal_id"] != proposal["proposal_id"]
    ):
        violations.append(
            _violation(
                "candidate_revision.proposal_not_accepted",
                ("proposal_decision",),
                "CandidateRevision may record only the exact accepted Proposal",
            )
        )
    if revision["recorded_by_principal"] != decision["decided_by_principal"]:
        violations.append(
            _violation(
                "candidate_revision.recorder_not_proposal_controller",
                ("candidate_revision", "recorded_by_principal"),
                "CandidateRevision must be recorded by the principal that accepted the Proposal",
            )
        )
    if (
        action_started_at is None
        or attempt.get("started_at") is None
        or attempt.get("completed_at") is None
        or not (
            _parse_time(resolution["frozen_at"])
            <= _parse_time(candidate_contract["frozen_at"])
            <= _parse_time(evaluation_plan["frozen_at"])
            <= _parse_time(attempt["started_at"])
            <= _parse_time(proposal["submitted_at"])
            < _parse_time(decision["decided_at"])
            < _parse_time(action_started_at)
            <= _parse_time(attempt["completed_at"])
            <= _parse_time(revision["recorded_at"])
        )
    ):
        violations.append(
            _violation(
                "candidate_revision.invalid_build_timeline",
                ("candidate_revision", "recorded_at"),
                "freeze, Proposal, acceptance, action, Attempt terminal, and CandidateRevision times must be monotonic",
            )
        )
    return violations


def _validate_evaluation_plan(
    plan: Document,
    resolution: Document,
    candidate_contract: Document,
) -> list[SemanticViolation]:
    violations = _integrity_as_semantic(
        evaluation_plan_violations(plan, resolution)
    )
    expected_resolution = {
        "id": resolution["resolution_contract_id"],
        "digest": resolution["contract_digest"],
    }
    expected_contract = {
        "id": candidate_contract["candidate_contract_id"],
        "digest": candidate_contract["contract_digest"],
    }
    if (
        plan["resolution_contract"] == expected_resolution
        and plan["candidate_contract"] == expected_contract
        and plan["candidate_planned_revision"]
        == candidate_contract["planned_revision"]
        and plan["workspace_id"] == resolution["workspace_id"]
        and plan["workspace_id"] == candidate_contract["workspace_id"]
        and plan["case_id"] == resolution["case_id"]
        and plan["case_id"] == candidate_contract["case_id"]
    ):
        pass
    else:
        violations.append(
            _violation(
                "evaluation_plan.contract_chain_mismatch",
                ("evaluation_plan",),
                "pre-build EvaluationPlan must bind the exact Resolution, CandidateContract, and planned revision",
            )
        )
    policy = plan.get("judge_policy", {})
    dimensions = set(policy.get("required_distinct_dimensions", ()))
    required_default = {"provider_org", "model_family"}
    if (
        policy.get("policy_mode") in {"CODING_DEFAULT", "HIGH_RISK"}
        or resolution.get("risk_class") in {"R2_HIGH_IMPACT", "R3_IRREVERSIBLE"}
    ) and not required_default <= dimensions:
        violations.append(
            _violation(
                "evaluation_plan.independence_dimensions_too_weak",
                ("evaluation_plan", "judge_policy", "required_distinct_dimensions"),
                "coding-default and high-risk policy require provider_org and model_family separation",
            )
        )
    if policy.get("policy_mode") == "LOW_RISK_RELAXED" and (
        resolution.get("risk_class") not in {"R0_READ_ONLY", "R1_REVERSIBLE"}
        or policy.get("relaxation_reason_digest") is None
    ):
        violations.append(
            _violation(
                "evaluation_plan.invalid_low_risk_relaxation",
                ("evaluation_plan", "judge_policy"),
                "relaxed independence is limited to R0/R1 and requires a frozen reason digest",
            )
        )
    return violations


def _validate_participation(document: Document) -> list[SemanticViolation]:
    if document["mode"] != "SINGLE_AGENT" or len(document["participants"]) == 1:
        return []
    return [
        _violation(
            "participation_manifest.single_agent_cardinality",
            ("participation_manifest", "participants"),
            "SINGLE_AGENT mode must contain exactly one participating Agent principal",
        )
    ]


def _validate_trace_receipt(document: Document) -> list[SemanticViolation]:
    names = [result["name"] for result in document["field_results"]]
    if len(names) == len(set(names)):
        return []
    return [
        _violation(
            "trace_evidence_receipt.duplicate_field_name",
            ("trace_evidence_receipt", "field_results"),
            "each requested trace field must have exactly one terminal result",
        )
    ]


def _validate_agent_intent(
    intent: Document,
    proposal: Document | None,
    attempt_snapshot: Document | None,
    capability: Document | None,
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    for name, value in (
        ("proposal", proposal),
        ("proposal_attempt_snapshot", attempt_snapshot),
        ("capability_lease", capability),
    ):
        if value is None:
            violations.append(
                _violation(
                    "agent_intent.context_missing",
                    (name,),
                    f"AgentIntent recording requires exact {name} context",
                )
            )
    if proposal is None or attempt_snapshot is None or capability is None:
        return violations
    if intent["proposal"] != exact_record_binding("proposal", proposal):
        violations.append(
            _violation(
                "agent_intent.proposal_mismatch",
                ("agent_intent", "proposal"),
                "AgentIntent must bind the exact recorded Proposal",
            )
        )
    if intent["attempt_snapshot"] != exact_record_binding(
        "attempt", attempt_snapshot
    ):
        violations.append(
            _violation(
                "agent_intent.attempt_snapshot_mismatch",
                ("agent_intent", "attempt_snapshot"),
                "AgentIntent must bind the exact author Attempt snapshot",
            )
        )
    if intent["runtime_capability"] != exact_record_binding(
        "capability_lease", capability
    ):
        violations.append(
            _violation(
                "agent_intent.capability_mismatch",
                ("agent_intent", "runtime_capability"),
                "AgentIntent must retain the exact runtime capability used to submit it",
            )
        )
    if (
        intent["attempt_id"] != attempt_snapshot["attempt_id"]
        or intent["worker_task_id"] != attempt_snapshot["worker_task_id"]
        or intent["capability_grant_id"] != capability["capability_grant_id"]
    ):
        violations.append(
            _violation(
                "agent_intent.identity_mismatch",
                ("agent_intent",),
                "AgentIntent ids must match its exact Attempt and Capability snapshots",
            )
        )
    return violations


IDENTITY_CHAIN_DOCUMENTS = (
    "worker_task",
    "worker_task_claim_snapshot",
    "attempt",
    "attempt_claim_snapshot",
    "proposal_attempt_snapshot",
    "proposal",
    "proposal_decision",
    "agent_manifest",
    "participation_manifest",
    "capability_lease",
)


def _context_violations(
    bundle: Bundle, required: Sequence[str], chain: str
) -> list[SemanticViolation]:
    return [
        _violation(
            f"{chain}.context_missing",
            (name,),
            f"{chain} semantics require related {name}",
        )
        for name in required
        if bundle.get(name) is None
    ]


def _canonical_permission_items(items: Sequence[Document]) -> set[str]:
    return {
        json.dumps(item, sort_keys=True, separators=(",", ":")) for item in items
    }


def _validate_permission_intersection(
    lease: Document, manifest: Document | None
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    sources = lease["source_permissions"]
    effective = lease["effective_permissions"]
    for surface_name, effective_items in effective.items():
        source_sets = [
            _canonical_permission_items(source[surface_name])
            for source in sources.values()
        ]
        exact_intersection = set.intersection(*source_sets)
        if _canonical_permission_items(effective_items) != exact_intersection:
            violations.append(
                _violation(
                    "capability_lease.effective_permissions_not_intersection",
                    ("capability_lease", "effective_permissions", surface_name),
                    "effective permissions must equal the exact four-way intersection",
                )
            )
    if manifest is not None and sources["principal_ceiling"] != manifest["permission_ceiling"]:
        violations.append(
            _violation(
                "identity_chain.permission_ceiling_mismatch",
                ("capability_lease", "source_permissions", "principal_ceiling"),
                "CapabilityLease principal ceiling must be the exact frozen AgentManifest ceiling",
            )
        )
    return violations


def _validate_manifest_runtime(
    attempt: Document, manifest: Document
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    if attempt["runtime"]["runtime_kind"] not in manifest["runtime_policy"][
        "allowed_runtime_kinds"
    ]:
        violations.append(
            _violation(
                "identity_chain.runtime_policy_mismatch",
                ("attempt", "runtime", "runtime_kind"),
                "Attempt runtime must be allowed by the exact AgentManifest",
            )
        )
    resolved_model = attempt["model"].get("resolved_model")
    if resolved_model is not None:
        primary = manifest["model_policy"]["primary_model"]
        fallbacks = {
            entry["model"]
            for entry in manifest["model_policy"]["allowed_fallback_models"]
        }
        if resolved_model != primary and resolved_model not in fallbacks:
            violations.append(
                _violation(
                    "identity_chain.model_policy_mismatch",
                    ("attempt", "model", "resolved_model"),
                    "resolved model must be frozen in the exact AgentManifest policy",
                )
            )
        if resolved_model in fallbacks and attempt["attempt_kind"] != "FALLBACK":
            violations.append(
                _violation(
                    "identity_chain.silent_model_fallback",
                    ("attempt", "attempt_kind"),
                    "a fallback model must run in a distinct FALLBACK Attempt",
                )
            )
    if attempt["state"] == "SUCCEEDED":
        required_skills = {
            skill["manifest_digest"]
            for skill in manifest["skills"]
            if skill["required_for_success"]
        }
        called_skills = {
            usage["skill_manifest_digest"]
            for usage in attempt["skill_usage"]
            if usage["called"]
        }
        if not required_skills <= called_skills:
            violations.append(
                _violation(
                    "identity_chain.required_skill_not_called",
                    ("attempt", "skill_usage"),
                    "every required frozen Skill must have a call receipt before success",
                )
            )
    return violations


def _validate_attempt_runtime_capability(
    lease: Document,
    attempt: Document,
    worker_task: Document,
    manifest: Document,
    attempt_claim_snapshot: Document,
    worker_task_claim_snapshot: Document,
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    if lease["grant_kind"] != "ATTEMPT_RUNTIME":
        violations.append(
            _violation(
                "identity_chain.attempt_capability_kind_mismatch",
                ("capability_lease", "grant_kind"),
                "Attempt.capability_grant_id must resolve to an ATTEMPT_RUNTIME grant",
            )
        )
        return violations
    if lease["issuance_basis"] != "CLAIMED_ATTEMPT":
        violations.append(
            _violation(
                "identity_chain.attempt_capability_basis_mismatch",
                ("capability_lease", "issuance_basis"),
                "runtime authority may be issued only from an already claimed Attempt",
            )
        )
    if attempt["capability_grant_id"] != lease["capability_grant_id"]:
        violations.append(
            _violation(
                "identity_chain.attempt_capability_id_mismatch",
                ("attempt", "capability_grant_id"),
                "Attempt must point to the exact runtime CapabilityLease",
            )
        )
    if attempt.get("runtime_capability") != exact_record_binding(
        "capability_lease", lease
    ):
        violations.append(
            _violation(
                "identity_chain.attempt_capability_snapshot_mismatch",
                ("attempt", "runtime_capability"),
                "current Attempt must retain the exact issuance CapabilityLease snapshot",
            )
        )
    if lease["bound_worker_task_id"] != worker_task_claim_snapshot["worker_task_id"]:
        violations.append(
            _violation(
                "identity_chain.capability_worker_task_mismatch",
                ("capability_lease", "bound_worker_task_id"),
                "runtime capability must bind the exact claimed WorkerTask",
            )
        )
    if lease["bound_attempt_id"] != attempt_claim_snapshot["attempt_id"]:
        violations.append(
            _violation(
                "identity_chain.capability_attempt_mismatch",
                ("capability_lease", "bound_attempt_id"),
                "runtime capability must bind the exact claimed Attempt",
            )
        )
    if lease["principal_id"] != attempt_claim_snapshot["executor_principal"]:
        violations.append(
            _violation(
                "identity_chain.capability_principal_mismatch",
                ("capability_lease", "principal_id"),
                "runtime capability audience principal must equal the Attempt executor",
            )
        )
    if lease["bound_resource"] != exact_record_binding(
        "attempt", attempt_claim_snapshot
    ):
        violations.append(
            _violation(
                "identity_chain.capability_resource_digest_mismatch",
                ("capability_lease", "bound_resource"),
                "runtime capability must bind the exact claim-time Attempt snapshot, never a future terminal digest",
            )
        )
    lease_claim = lease["claim_binding"]
    attempt_claim = attempt_claim_snapshot["claim_binding"]
    claim_fields = (
        "claim_event_id",
        "lease_id",
        "worker_principal",
        "runtime_session_id",
        "fencing_token",
        "claimed_at",
    )
    for field in claim_fields:
        if lease_claim[field] != attempt_claim[field]:
            violations.append(
                _violation(
                    f"identity_chain.capability_claim_{field}_mismatch",
                    ("capability_lease", "claim_binding", field),
                    f"runtime capability must bind the exact claim {field}",
                )
            )
    if lease_claim["attempt_id"] != attempt_claim_snapshot["attempt_id"]:
        violations.append(
            _violation(
                "identity_chain.capability_claim_attempt_mismatch",
                ("capability_lease", "claim_binding", "attempt_id"),
                "runtime capability claim receipt must bind the exact Attempt",
            )
        )
    if attempt_claim["worker_principal"] != attempt_claim_snapshot["executor_principal"]:
        violations.append(
            _violation(
                "identity_chain.claim_principal_mismatch",
                ("attempt", "claim_binding", "worker_principal"),
                "the claimed Worker principal must equal the Attempt executor",
            )
        )
    runtime_session_id = attempt["runtime"]["runtime_session_id"]
    if runtime_session_id is not None and attempt_claim["runtime_session_id"] != runtime_session_id:
        violations.append(
            _violation(
                "identity_chain.capability_native_session_mismatch",
                ("capability_lease", "claim_binding", "runtime_session_id"),
                "runtime capability must bind the exact native runtime session",
            )
        )
    if _parse_time(lease["issued_at"]) < _parse_time(attempt_claim["claimed_at"]):
        violations.append(
            _violation(
                "identity_chain.capability_issued_before_claim",
                ("capability_lease", "issued_at"),
                "ATTEMPT_RUNTIME authority cannot predate the authoritative claim",
            )
        )
    if _parse_time(lease["expires_at"]) <= _parse_time(lease["issued_at"]):
        violations.append(
            _violation(
                "capability_lease.invalid_expiry",
                ("capability_lease", "expires_at"),
                "capability expiry must be strictly after issuance",
            )
        )
    if lease["resolution_contract"] != worker_task_claim_snapshot["resolution_contract"]:
        violations.append(
            _violation(
                "identity_chain.capability_resolution_contract_mismatch",
                ("capability_lease", "resolution_contract"),
                "runtime capability must retain the WorkerTask resolution boundary",
            )
        )
    current_lease = worker_task_claim_snapshot.get("current_lease")
    if current_lease is not None and (
        current_lease["lease_id"] != lease_claim["lease_id"]
        or current_lease["attempt_id"] != attempt_claim_snapshot["attempt_id"]
        or current_lease["worker_principal"] != attempt_claim_snapshot["executor_principal"]
        or current_lease["fencing_token"] != lease_claim["fencing_token"]
    ):
        violations.append(
            _violation(
                "identity_chain.current_lease_claim_mismatch",
                ("worker_task", "current_lease"),
                "active WorkerTask lease must equal the immutable Attempt claim and fence",
            )
        )
    if attempt_claim_snapshot.get("worker_task_snapshot") != exact_record_binding(
        "worker_task", worker_task_claim_snapshot
    ):
        violations.append(
            _violation(
                "identity_chain.attempt_worker_snapshot_mismatch",
                ("attempt_claim_snapshot", "worker_task_snapshot"),
                "claim-time Attempt must bind the exact claim-time WorkerTask snapshot",
            )
        )
    violations.extend(_validate_permission_intersection(lease, manifest))
    return violations


def _validate_complete_identity_chain(bundle: Bundle) -> list[SemanticViolation]:
    missing = _context_violations(bundle, IDENTITY_CHAIN_DOCUMENTS, "identity_chain")
    if missing:
        return missing
    worker_task = bundle["worker_task"]
    worker_task_claim_snapshot = bundle["worker_task_claim_snapshot"]
    attempt = bundle["attempt"]
    attempt_claim_snapshot = bundle["attempt_claim_snapshot"]
    proposal_attempt_snapshot = bundle["proposal_attempt_snapshot"]
    proposal = bundle["proposal"]
    decision = bundle["proposal_decision"]
    manifest = bundle["agent_manifest"]
    participation = bundle["participation_manifest"]
    lease = bundle["capability_lease"]
    violations: list[SemanticViolation] = []

    if len(
        {
            worker_task["workspace_id"],
            worker_task_claim_snapshot["workspace_id"],
            attempt["workspace_id"],
            attempt_claim_snapshot["workspace_id"],
            proposal_attempt_snapshot["workspace_id"],
            proposal["workspace_id"],
            decision["workspace_id"],
            manifest["workspace_id"],
            participation["workspace_id"],
            lease["workspace_id"],
        }
    ) != 1:
        violations.append(
            _violation(
                "identity_chain.workspace_mismatch",
                ("identity_chain", "workspace_id"),
                "every identity and authority record must share one workspace",
            )
        )
    if len({worker_task["case_id"], proposal["case_id"], participation["case_id"]}) != 1:
        violations.append(
            _violation(
                "identity_chain.case_mismatch",
                ("identity_chain", "case_id"),
                "WorkerTask, Proposal and ParticipationManifest must share one Case",
            )
        )
    if len(
        {
            worker_task["worker_task_id"],
            worker_task_claim_snapshot["worker_task_id"],
            attempt["worker_task_id"],
            attempt_claim_snapshot["worker_task_id"],
            proposal_attempt_snapshot["worker_task_id"],
            proposal["worker_task_id"],
        }
    ) != 1:
        violations.append(
            _violation(
                "identity_chain.worker_task_mismatch",
                ("identity_chain", "worker_task_id"),
                "Attempt and Proposal must bind the exact WorkerTask",
            )
        )
    if len(
        {
            worker_task["input_snapshot_digest"],
            worker_task_claim_snapshot["input_snapshot_digest"],
            attempt["input_snapshot_digest"],
            attempt_claim_snapshot["input_snapshot_digest"],
            proposal_attempt_snapshot["input_snapshot_digest"],
            proposal["input_snapshot_digest"],
        }
    ) != 1:
        violations.append(
            _violation(
                "identity_chain.input_snapshot_mismatch",
                ("identity_chain", "input_snapshot_digest"),
                "the full author chain must retain one immutable input snapshot",
            )
        )
    if len(
        {
            worker_task["assigned_team_id"],
            worker_task_claim_snapshot["assigned_team_id"],
            attempt["assigned_team_id"],
            attempt_claim_snapshot["assigned_team_id"],
            proposal_attempt_snapshot["assigned_team_id"],
            manifest["team_id"],
        }
    ) != 1:
        violations.append(
            _violation(
                "identity_chain.team_mismatch",
                ("identity_chain", "team_id"),
                "WorkerTask, Attempt and AgentManifest must bind the assigned Team",
            )
        )
    if worker_task["team_manifest_digest"] != participation["team_manifest_digest"]:
        violations.append(
            _violation(
                "identity_chain.team_manifest_mismatch",
                ("participation_manifest", "team_manifest_digest"),
                "ParticipationManifest must freeze the WorkerTask Team manifest",
            )
        )
    if worker_task["role_manifest_digest"] != manifest["role_manifest_digest"]:
        violations.append(
            _violation(
                "identity_chain.role_manifest_mismatch",
                ("agent_manifest", "role_manifest_digest"),
                "AgentManifest role digest must equal the WorkerTask assignment",
            )
        )
    expected_manifest = exact_record_binding("agent_manifest", manifest)
    if attempt["agent_manifest"] != expected_manifest or proposal["agent_manifest"] != expected_manifest:
        violations.append(
            _violation(
                "identity_chain.agent_manifest_mismatch",
                ("identity_chain", "agent_manifest"),
                "Attempt and Proposal must bind the exact frozen AgentManifest",
            )
        )
    expected_principal = manifest["principal_id"]
    if {
        attempt["executor_principal"],
        proposal["authored_by_principal"],
        proposal["submitted_by_principal"],
        lease["principal_id"],
    } != {expected_principal}:
        violations.append(
            _violation(
                "identity_chain.principal_mismatch",
                ("identity_chain", "principal_id"),
                "manifest, executor, Proposal author/submitter and capability principal must match",
            )
        )
    if attempt["requested_by_principal"] != worker_task["requested_by_principal"]:
        violations.append(
            _violation(
                "identity_chain.requester_mismatch",
                ("attempt", "requested_by_principal"),
                "Attempt must retain the WorkerTask requesting Controller principal",
            )
        )
    expected_participation = exact_record_binding(
        "participation_manifest", participation
    )
    if worker_task["participation_manifest"] != expected_participation:
        violations.append(
            _violation(
                "identity_chain.participation_manifest_mismatch",
                ("worker_task", "participation_manifest"),
                "WorkerTask must bind the exact frozen ParticipationManifest",
            )
        )
    matching_participants = [
        participant
        for participant in participation["participants"]
        if participant["worker_task_id"] == worker_task["worker_task_id"]
    ]
    expected_participant = {
        "principal_id": expected_principal,
        "role": manifest["role"],
        "agent_manifest": expected_manifest,
        "worker_task_id": worker_task["worker_task_id"],
        "enabled": True,
    }
    if matching_participants != [expected_participant]:
        violations.append(
            _violation(
                "identity_chain.participant_mismatch",
                ("participation_manifest", "participants"),
                "exactly one enabled participant must bind task, role, principal and AgentManifest",
            )
        )
    if worker_task["assigned_role"] != manifest["role"]:
        violations.append(
            _violation(
                "identity_chain.role_mismatch",
                ("worker_task", "assigned_role"),
                "WorkerTask assigned role must equal the AgentManifest role",
            )
        )
    if worker_task.get("previous_snapshot") != exact_record_binding(
        "worker_task", worker_task_claim_snapshot
    ):
        violations.append(
            _violation(
                "identity_chain.worker_revision_history_mismatch",
                ("worker_task", "previous_snapshot"),
                "current WorkerTask must retain the exact claim-time predecessor snapshot",
            )
        )
    if attempt.get("previous_snapshot") != exact_record_binding(
        "attempt", proposal_attempt_snapshot
    ):
        violations.append(
            _violation(
                "identity_chain.attempt_revision_history_mismatch",
                ("attempt", "previous_snapshot"),
                "current Attempt must retain the exact pre-Proposal output snapshot",
            )
        )
    if participation["resolution_contract"] != worker_task["resolution_contract"]:
        violations.append(
            _violation(
                "identity_chain.resolution_contract_mismatch",
                ("participation_manifest", "resolution_contract"),
                "ParticipationManifest and WorkerTask must share one resolution boundary",
            )
        )
    if attempt["state"] == "SUCCEEDED":
        if worker_task["state"] != "COMPLETED":
            violations.append(
                _violation(
                    "identity_chain.succeeded_attempt_task_not_completed",
                    ("worker_task", "state"),
                    "a terminal successful Attempt cannot be presented with a queued WorkerTask",
                )
            )
        if worker_task.get("terminal_attempt") != exact_record_binding(
            "attempt", attempt
        ):
            violations.append(
                _violation(
                    "identity_chain.terminal_attempt_snapshot_mismatch",
                    ("worker_task", "terminal_attempt"),
                    "completed WorkerTask must bind the exact terminal Attempt snapshot",
                )
            )
        if worker_task["terminal_attempt_id"] != attempt["attempt_id"]:
            violations.append(
                _violation(
                    "identity_chain.terminal_attempt_mismatch",
                    ("worker_task", "terminal_attempt_id"),
                    "completed WorkerTask must bind the exact terminal Attempt",
                )
            )
        if worker_task["accepted_proposal_id"] != proposal["proposal_id"]:
            violations.append(
                _violation(
                    "identity_chain.task_accepted_proposal_mismatch",
                    ("worker_task", "accepted_proposal_id"),
                    "completed WorkerTask must bind the accepted Proposal",
                )
            )
    if decision["proposal_id"] != proposal["proposal_id"]:
        violations.append(
            _violation(
                "identity_chain.proposal_decision_id_mismatch",
                ("proposal_decision", "proposal_id"),
                "ProposalDecision must bind the submitted Proposal, not an internally self-consistent substitute",
            )
        )
    if decision["proposal_digest"] != proposal["proposal_digest"]:
        violations.append(
            _violation(
                "identity_chain.proposal_decision_digest_mismatch",
                ("proposal_decision", "proposal_digest"),
                "ProposalDecision must bind the exact immutable Proposal digest",
            )
        )
    if _parse_time(decision["decided_at"]) <= _parse_time(proposal["submitted_at"]):
        violations.append(
            _violation(
                "identity_chain.decision_before_submission",
                ("proposal_decision", "decided_at"),
                "ProposalDecision must occur strictly after Proposal submission",
            )
        )
    if attempt["state"] == "SUCCEEDED":
        if decision["decision"] != "ACCEPTED":
            violations.append(
                _violation(
                    "identity_chain.proposal_not_accepted",
                    ("proposal_decision", "decision"),
                    "a successful Attempt and completed task require an ACCEPTED ProposalDecision",
                )
            )
        action_started_at = attempt["pre_controlled_action"][
            "controlled_action_started_at"
        ]
        if action_started_at is not None and _parse_time(action_started_at) <= _parse_time(
            decision["decided_at"]
        ):
            violations.append(
                _violation(
                    "identity_chain.action_before_decision",
                    ("attempt", "pre_controlled_action", "controlled_action_started_at"),
                    "controlled downstream action must start strictly after the exact Proposal was accepted",
                )
            )
    violations.extend(
        _validate_attempt_runtime_capability(
            lease,
            attempt,
            worker_task,
            manifest,
            attempt_claim_snapshot,
            worker_task_claim_snapshot,
        )
    )
    violations.extend(_validate_manifest_runtime(attempt, manifest))
    return violations


def _validate_dispatch_capability(bundle: Bundle, lease: Document) -> list[SemanticViolation]:
    if lease["issuance_basis"] == "ROOT_CONTROLLER":
        required = ("worker_task", "agent_manifest", "participation_manifest")
        violations = _context_violations(bundle, required, "root_dispatch")
        if violations:
            return violations
        worker_task = bundle["worker_task"]
        manifest = bundle["agent_manifest"]
        participation = bundle["participation_manifest"]
        if worker_task["task_kind"] == "DELEGATED_RUNTIME":
            violations.append(
                _violation(
                    "root_dispatch.delegated_task_without_proposal",
                    ("worker_task", "task_kind"),
                    "a delegated child task cannot use ROOT_CONTROLLER authority",
                )
            )
        if lease["accepted_proposal"] is not None:
            violations.append(
                _violation(
                    "root_dispatch.fabricated_accepted_proposal",
                    ("capability_lease", "accepted_proposal"),
                    "root Controller dispatch must not fabricate an accepted Proposal",
                )
            )
        violations.extend(
            _validate_dispatch_identity(lease, worker_task, manifest, participation)
        )
        return violations
    if lease["issuance_basis"] == "ACCEPTED_DELEGATION":
        required = (
            "worker_task",
            "agent_manifest",
            "participation_manifest",
            "delegation_proposal",
            "delegation_decision",
        )
        violations = _context_violations(bundle, required, "delegated_dispatch")
        if violations:
            return violations
        worker_task = bundle["worker_task"]
        proposal = bundle["delegation_proposal"]
        decision = bundle["delegation_decision"]
        manifest = bundle["agent_manifest"]
        participation = bundle["participation_manifest"]
        if worker_task["task_kind"] != "DELEGATED_RUNTIME":
            violations.append(
                _violation(
                    "delegated_dispatch.child_task_kind_mismatch",
                    ("worker_task", "task_kind"),
                    "accepted delegation authority may dispatch only a DELEGATED_RUNTIME child task",
                )
            )
        if proposal["proposal_type"] != "DELEGATION" or proposal["delegation"] is None:
            violations.append(
                _violation(
                    "delegated_dispatch.proposal_type_mismatch",
                    ("delegation_proposal", "proposal_type"),
                    "child dispatch requires an accepted DelegationProposal",
                )
            )
        else:
            if worker_task["input_snapshot_digest"] != proposal["delegation"]["child_input_digest"]:
                violations.append(
                    _violation(
                        "delegated_dispatch.child_input_mismatch",
                        ("worker_task", "input_snapshot_digest"),
                        "child WorkerTask input must equal the accepted delegation child input",
                    )
                )
            if worker_task["capability_requirements_digest"] != proposal["delegation"]["capability_requirements_digest"]:
                violations.append(
                    _violation(
                        "delegated_dispatch.capability_requirements_mismatch",
                        ("worker_task", "capability_requirements_digest"),
                        "child WorkerTask capability requirements must equal the accepted delegation",
                    )
                )
        if worker_task["delegation_proposal_id"] != proposal["proposal_id"]:
            violations.append(
                _violation(
                    "delegated_dispatch.child_proposal_id_mismatch",
                    ("worker_task", "delegation_proposal_id"),
                    "child WorkerTask must retain the parent DelegationProposal id",
                )
            )
        if worker_task["parent_attempt_id"] != proposal["authored_by_attempt_id"]:
            violations.append(
                _violation(
                    "delegated_dispatch.parent_attempt_mismatch",
                    ("worker_task", "parent_attempt_id"),
                    "child WorkerTask parent Attempt must be the DelegationProposal author",
                )
            )
        expected_proposal = exact_record_binding("proposal", proposal)
        if lease["accepted_proposal"] != expected_proposal:
            violations.append(
                _violation(
                    "delegated_dispatch.capability_proposal_mismatch",
                    ("capability_lease", "accepted_proposal"),
                    "delegated dispatch capability must bind the exact accepted parent Proposal",
                )
            )
        violations.extend(_validate_proposal_decision(decision))
        if decision["decision"] != "ACCEPTED":
            violations.append(
                _violation(
                    "delegated_dispatch.proposal_not_accepted",
                    ("delegation_decision", "decision"),
                    "delegated dispatch requires an ACCEPTED ProposalDecision",
                )
            )
        if decision["proposal_id"] != proposal["proposal_id"] or decision["proposal_digest"] != proposal["proposal_digest"]:
            violations.append(
                _violation(
                    "delegated_dispatch.decision_proposal_mismatch",
                    ("delegation_decision", "proposal_id"),
                    "delegation Decision must bind the exact parent Proposal id and digest",
                )
            )
        causation = lease["dispatch_causation"]
        downstream = decision["downstream_event"]
        if downstream is None or downstream["event_type"] != "work.requested":
            violations.append(
                _violation(
                    "delegated_dispatch.downstream_not_child_work",
                    ("delegation_decision", "downstream_event", "event_type"),
                    "accepted delegation must cause child work.requested in the Decision transaction",
                )
            )
        elif (
            causation["decision_event_id"] != decision["decision_event_id"]
            or causation["downstream_event_id"] != downstream["event_id"]
            or causation["transaction_id"] != decision["transaction_id"]
            or causation["worker_task_snapshot"]
            != exact_record_binding("worker_task", worker_task)
        ):
            violations.append(
                _violation(
                    "delegated_dispatch.causation_binding_mismatch",
                    ("capability_lease", "dispatch_causation"),
                    "Decision, child WorkerTask and dispatch grant must share exact causation and transaction bindings",
                )
            )
        violations.extend(
            _validate_dispatch_identity(lease, worker_task, manifest, participation)
        )
        return violations
    return [
        _violation(
            "capability_lease.dispatch_basis_invalid",
            ("capability_lease", "issuance_basis"),
            "DISPATCH_CLAIM must be root Controller or accepted delegation authority",
        )
    ]


def _validate_dispatch_identity(
    lease: Document,
    worker_task: Document,
    manifest: Document,
    participation: Document,
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    if worker_task["state"] not in {"QUEUED", "WAITING_RETRY"}:
        violations.append(
            _violation(
                "dispatch_identity.task_not_dispatchable",
                ("worker_task", "state"),
                "dispatch authority may target only queued or retry-ready work",
            )
        )
    if len(
        {
            lease["workspace_id"],
            worker_task["workspace_id"],
            manifest["workspace_id"],
            participation["workspace_id"],
        }
    ) != 1:
        violations.append(
            _violation(
                "dispatch_identity.workspace_mismatch",
                ("capability_lease", "workspace_id"),
                "dispatch capability and child identity must share one workspace",
            )
        )
    if lease["bound_worker_task_id"] != worker_task["worker_task_id"]:
        violations.append(
            _violation(
                "dispatch_identity.worker_task_mismatch",
                ("capability_lease", "bound_worker_task_id"),
                "dispatch capability must bind the exact WorkerTask",
            )
        )
    if lease["bound_resource"] != exact_record_binding("worker_task", worker_task):
        violations.append(
            _violation(
                "dispatch_identity.task_digest_mismatch",
                ("capability_lease", "bound_resource"),
                "dispatch capability must bind the exact issuance-time WorkerTask snapshot",
            )
        )
    if lease["principal_id"] != manifest["principal_id"]:
        violations.append(
            _violation(
                "dispatch_identity.principal_mismatch",
                ("capability_lease", "principal_id"),
                "dispatch capability principal must equal the selected Agent principal",
            )
        )
    if worker_task["assigned_team_id"] != manifest["team_id"]:
        violations.append(
            _violation(
                "dispatch_identity.team_mismatch",
                ("worker_task", "assigned_team_id"),
                "dispatch WorkerTask and selected AgentManifest must share one Team",
            )
        )
    if worker_task["assigned_role"] != manifest["role"]:
        violations.append(
            _violation(
                "dispatch_identity.role_mismatch",
                ("worker_task", "assigned_role"),
                "dispatch WorkerTask role must equal the selected AgentManifest role",
            )
        )
    if worker_task["role_manifest_digest"] != manifest["role_manifest_digest"]:
        violations.append(
            _violation(
                "dispatch_identity.role_manifest_mismatch",
                ("worker_task", "role_manifest_digest"),
                "dispatch WorkerTask must retain the exact frozen role manifest",
            )
        )
    if worker_task["team_manifest_digest"] != participation["team_manifest_digest"]:
        violations.append(
            _violation(
                "dispatch_identity.team_manifest_mismatch",
                ("worker_task", "team_manifest_digest"),
                "dispatch WorkerTask must retain the selected Team manifest",
            )
        )
    expected_participation = exact_record_binding(
        "participation_manifest", participation
    )
    if worker_task["participation_manifest"] != expected_participation:
        violations.append(
            _violation(
                "dispatch_identity.participation_manifest_mismatch",
                ("worker_task", "participation_manifest"),
                "dispatch WorkerTask must bind the exact ParticipationManifest",
            )
        )
    if worker_task["case_id"] != participation["case_id"]:
        violations.append(
            _violation(
                "dispatch_identity.case_mismatch",
                ("worker_task", "case_id"),
                "dispatch WorkerTask and ParticipationManifest must share one Case",
            )
        )
    expected_manifest = exact_record_binding("agent_manifest", manifest)
    matches = [
        participant
        for participant in participation["participants"]
        if participant["worker_task_id"] == worker_task["worker_task_id"]
        and participant["principal_id"] == manifest["principal_id"]
        and participant["role"] == worker_task["assigned_role"]
        and participant["agent_manifest"] == expected_manifest
    ]
    if len(matches) != 1:
        violations.append(
            _violation(
                "dispatch_identity.participant_mismatch",
                ("participation_manifest", "participants"),
                "dispatch requires one exact selected task/role/principal/manifest participant",
            )
        )
    if lease["resolution_contract"] != worker_task["resolution_contract"]:
        violations.append(
            _violation(
                "dispatch_identity.resolution_contract_mismatch",
                ("capability_lease", "resolution_contract"),
                "dispatch capability must retain the WorkerTask resolution boundary",
            )
        )
    if _parse_time(lease["expires_at"]) <= _parse_time(lease["issued_at"]):
        violations.append(
            _violation(
                "capability_lease.invalid_expiry",
                ("capability_lease", "expires_at"),
                "capability expiry must be strictly after issuance",
            )
        )
    violations.extend(_validate_permission_intersection(lease, manifest))
    return violations


def _validate_capability_chain(bundle: Bundle) -> list[SemanticViolation]:
    lease = bundle.get("capability_lease")
    identity_present = any(bundle.get(name) is not None for name in IDENTITY_CHAIN_DOCUMENTS)
    if lease is None:
        if identity_present:
            return _context_violations(bundle, IDENTITY_CHAIN_DOCUMENTS, "identity_chain")
        return []
    if lease["grant_kind"] == "DISPATCH_CLAIM":
        violations = _validate_dispatch_capability(bundle, lease)
        if bundle.get("attempt") is not None:
            violations.append(
                _violation(
                    "identity_chain.attempt_capability_kind_mismatch",
                    ("capability_lease", "grant_kind"),
                    "a bundle containing an Attempt must resolve its pointer to ATTEMPT_RUNTIME, not dispatch authority",
                )
            )
        return violations
    if lease["grant_kind"] == "ACTION_EXECUTION":
        return [
            _violation(
                "capability_lease.action_execution_stage4_deferred",
                ("capability_lease", "grant_kind"),
                "ACTION_EXECUTION semantic acceptance is intentionally deferred to Stage 4",
            )
        ]
    return _validate_complete_identity_chain(bundle)


def _validate_planner_generator_separation(
    resolution: Document,
    candidate_revision: Document,
    bundle: Bundle,
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    generator_attempt = _resolve_exact_record(
        bundle, "attempt", candidate_revision.get("producer_attempt", {})
    )
    if generator_attempt is None:
        return [
            _violation(
                "planner_generator.generator_attempt_missing",
                ("candidate_revision", "producer_attempt"),
                "CandidateRevision must resolve its exact Generator Attempt",
            )
        ]
    generator_identity = {
        "principal_id": generator_attempt.get("executor_principal"),
        "attempt": exact_record_binding("attempt", generator_attempt),
        "agent_manifest": generator_attempt.get("agent_manifest"),
        "model_call_receipt": generator_attempt.get("model", {}).get(
            "model_call_receipt"
        ),
    }
    proposer, proposer_violations = _resolve_agent_execution(
        resolution.get("proposer", {}), bundle, "resolution_proposer"
    )
    generator, generator_violations = _resolve_agent_execution(
        generator_identity, bundle, "candidate_generator"
    )
    violations.extend(proposer_violations)
    violations.extend(generator_violations)
    if proposer is None or generator is None:
        return violations
    if proposer["attempt"].get("attempt_id") == generator["attempt"].get(
        "attempt_id"
    ):
        violations.append(
            _violation(
                "planner_generator.attempt_not_separated",
                ("candidate_revision", "producer_attempt"),
                "Planner and Generator must use distinct Attempts",
            )
        )
    if proposer["agent_manifest"].get("role") == generator[
        "agent_manifest"
    ].get("role"):
        violations.append(
            _violation(
                "planner_generator.role_not_separated",
                ("candidate_revision", "producer_attempt"),
                "Planner and Generator AgentManifest roles must be separated",
            )
        )
    return violations


def _validate_generator_judge_independence(
    resolution: Document,
    candidate_revision: Document,
    evaluation_plan: Document,
    gate_report: Document,
    bundle: Bundle,
) -> list[SemanticViolation]:
    violations: list[SemanticViolation] = []
    judge_bindings = [
        item
        for item in gate_report.get("track_receipts", ())
        if item.get("track") == "INDEPENDENT_JUDGE"
    ]
    if len(judge_bindings) != 1:
        return [
            _violation(
                "gate_report.independent_judge_receipt_missing",
                ("gate_report", "track_receipts"),
                "GateReport must bind exactly one INDEPENDENT_JUDGE receipt",
            )
        ]
    track_receipt = _resolve_exact_record(
        bundle, "gate_track_receipt", judge_bindings[0].get("receipt", {})
    )
    if track_receipt is None:
        return [
            _violation(
                "gate_report.independent_judge_receipt_missing",
                ("gate_report", "track_receipts"),
                "INDEPENDENT_JUDGE binding must resolve one exact typed receipt",
            )
        ]
    executor = track_receipt.get("executor", {})
    judge_identity = executor.get("execution", {})
    judge, judge_violations = _resolve_agent_execution(
        judge_identity, bundle, "independent_judge"
    )
    violations.extend(judge_violations)

    generator_attempt = _resolve_exact_record(
        bundle, "attempt", candidate_revision.get("producer_attempt", {})
    )
    if generator_attempt is None:
        violations.append(
            _violation(
                "gate_report.generator_attempt_missing",
                ("candidate_revision", "producer_attempt"),
                "Gate independence requires the exact Generator Attempt",
            )
        )
        return violations
    generator_identity = {
        "principal_id": generator_attempt.get("executor_principal"),
        "attempt": exact_record_binding("attempt", generator_attempt),
        "agent_manifest": generator_attempt.get("agent_manifest"),
        "model_call_receipt": generator_attempt.get("model", {}).get(
            "model_call_receipt"
        ),
    }
    generator, generator_violations = _resolve_agent_execution(
        generator_identity, bundle, "candidate_generator"
    )
    violations.extend(generator_violations)
    if (
        executor.get("evaluator_version_digest")
        != evaluation_plan.get("judge_policy", {}).get("evaluator_version_digest")
    ):
        violations.append(
            _violation(
                "gate_track_receipt.evaluator_version_mismatch",
                ("gate_track_receipts", "executor", "evaluator_version_digest"),
                "judge receipt must use the evaluator version frozen in EvaluationPlan",
            )
        )
    must_be_independent = (
        gate_report.get("status") == "PASS"
        or track_receipt.get("status") == "PASS"
    )
    if not must_be_independent or generator is None or judge is None:
        return violations
    generator_attempt = generator["attempt"]
    judge_attempt = judge["attempt"]
    generator_manifest = generator["agent_manifest"]
    judge_manifest = judge["agent_manifest"]
    if generator_attempt.get("attempt_id") == judge_attempt.get("attempt_id"):
        violations.append(
            _violation(
                "gate_report.generator_judge_attempt_not_independent",
                ("gate_report", "track_receipts"),
                "Generator and independent Judge must use distinct Attempts",
            )
        )
    if generator_identity.get("principal_id") == judge_identity.get("principal_id"):
        violations.append(
            _violation(
                "gate_report.generator_judge_principal_not_independent",
                ("gate_report", "track_receipts"),
                "Generator and independent Judge must use distinct principals",
            )
        )
    if (
        generator_manifest.get("agent_manifest_id")
        == judge_manifest.get("agent_manifest_id")
        or generator_manifest.get("manifest_digest")
        == judge_manifest.get("manifest_digest")
    ):
        violations.append(
            _violation(
                "gate_report.generator_judge_manifest_not_independent",
                ("gate_report", "track_receipts"),
                "Generator and independent Judge must use distinct AgentManifest id and digest",
            )
        )
    generator_call = generator["model_call_receipt"]
    judge_call = judge["model_call_receipt"]
    if generator_call.get("independence_key") == judge_call.get("independence_key"):
        violations.append(
            _violation(
                "gate_report.generator_judge_independence_key_reused",
                ("evaluation_plan", "judge_policy"),
                "PASS requires distinct recomputed Generator and Judge independence keys",
            )
        )
    violations.extend(
        _distinct_model_dimensions(
            generator_call,
            judge_call,
            evaluation_plan.get("judge_policy", {}).get(
                "required_distinct_dimensions", ()
            ),
            "gate_report.generator_judge",
            ("evaluation_plan", "judge_policy", "required_distinct_dimensions"),
        )
    )
    if (
        track_receipt.get("workspace_id") != resolution.get("workspace_id")
        or track_receipt.get("case_id") != resolution.get("case_id")
    ):
        violations.append(
            _violation(
                "gate_track_receipt.subject_context_mismatch",
                ("gate_track_receipt", "workspace_id"),
                "judge track must share the governed Resolution workspace and Case",
            )
        )
    return violations


def _validate_workorder(
    workorder: Document,
    resolution: Document,
    candidate_contract: Document,
    candidate_revision: Document,
    evaluation_plan: Document,
    gate_report: Document,
    gate_track_receipts: Sequence[Document] | None,
) -> list[SemanticViolation]:
    return _integrity_as_semantic(
        workorder_chain_violations(
            workorder,
            resolution,
            candidate_contract,
            candidate_revision,
            evaluation_plan,
            gate_report,
            gate_track_receipts,
        )
    )


def validate_semantics(bundle: Bundle) -> tuple[SemanticViolation, ...]:
    """Return all cross-field/cross-document violations in a v4 bundle.

    The caller should run JSON Schema validation first.  WorkOrder and Proposal
    checks fail closed when their required related documents are absent.
    """

    violations: list[SemanticViolation] = []
    public_principal_context = bundle.get("public_principal_context")
    if isinstance(public_principal_context, Mapping):
        violations.extend(
            public_principal_context_violations(public_principal_context)
        )
    violations.extend(_authority_context_violations(bundle))
    violations.extend(_validate_capability_chain(bundle))
    resolution = bundle.get("resolution_contract")
    candidate = bundle.get("candidate_contract")
    candidate_revision = bundle.get("candidate_revision")
    evaluation_plan = bundle.get("evaluation_plan")
    gate_report = bundle.get("gate_report")
    attempt = bundle.get("attempt")
    proposal = bundle.get("proposal")
    decision = bundle.get("proposal_decision")
    if resolution is not None:
        violations.extend(_validate_resolution_review(resolution, bundle))
    if candidate is not None:
        violations.extend(
            _integrity_as_semantic(candidate_scope_violations(candidate, resolution))
        )

    if candidate_revision is not None:
        revision_context = {
            "resolution_contract": resolution,
            "candidate_contract": candidate,
            "evaluation_plan": evaluation_plan,
            "attempt": attempt,
            "proposal": proposal,
            "proposal_decision": decision,
        }
        missing = [name for name, value in revision_context.items() if value is None]
        if missing:
            for name in missing:
                violations.append(
                    _violation(
                        "candidate_revision.semantic_context_missing",
                        (name,),
                        f"CandidateRevision semantics require related {name}",
                    )
                )
        else:
            violations.extend(
                _validate_candidate_revision(
                    candidate_revision,
                    resolution,  # type: ignore[arg-type]
                    candidate,  # type: ignore[arg-type]
                    revision_context["evaluation_plan"],  # type: ignore[arg-type]
                    revision_context["attempt"],  # type: ignore[arg-type]
                    revision_context["proposal"],  # type: ignore[arg-type]
                    revision_context["proposal_decision"],  # type: ignore[arg-type]
                )
            )
            violations.extend(
                _validate_planner_generator_separation(
                    resolution,  # type: ignore[arg-type]
                    candidate_revision,
                    bundle,
                )
            )

    if evaluation_plan is not None:
        if resolution is None or candidate is None:
            for name, value in (
                ("resolution_contract", resolution),
                ("candidate_contract", candidate),
            ):
                if value is None:
                    violations.append(
                        _violation(
                            "evaluation_plan.semantic_context_missing",
                            (name,),
                            f"EvaluationPlan semantics require related {name}",
                        )
                    )
        else:
            violations.extend(
                _validate_evaluation_plan(evaluation_plan, resolution, candidate)
            )

    if decision is not None:
        violations.extend(_validate_proposal_decision(decision))

    if attempt is not None:
        violations.extend(_validate_attempt_action_order(attempt))

    if proposal is not None:
        worker_task = bundle.get("worker_task")
        authored_attempt = bundle.get("proposal_attempt_snapshot")
        if attempt is None:
            violations.append(
                _violation(
                    "proposal.author_attempt_missing",
                    ("attempt",),
                    "Proposal semantics require its author Attempt",
                )
            )
        if worker_task is None:
            violations.append(
                _violation(
                    "proposal.worker_task_context_missing",
                    ("worker_task",),
                    "Proposal semantics require the immutable WorkerTask identity",
                )
            )
        if authored_attempt is None:
            violations.append(
                _violation(
                    "proposal.author_snapshot_missing",
                    ("proposal_attempt_snapshot",),
                    "Proposal semantics require the exact OUTPUT_RECORDED author snapshot",
                )
            )
        candidate_required = (
            proposal["proposal_type"] == "CHANGE"
            or proposal["candidate_contract"] is not None
        )
        if candidate_required and candidate is None:
            violations.append(
                _violation(
                    "proposal.candidate_contract_context_missing",
                    ("candidate_contract",),
                    "CHANGE or candidate-bound Proposal semantics require CandidateContract",
                )
            )
        if attempt is not None and authored_attempt is not None and worker_task is not None:
            violations.extend(
                _validate_proposal_author(
                    proposal,
                    attempt,
                    authored_attempt,
                    worker_task,
                    candidate,
                )
            )

    participation = bundle.get("participation_manifest")
    if participation is not None:
        violations.extend(_validate_participation(participation))

    trace_receipt = bundle.get("trace_evidence_receipt")
    if trace_receipt is not None:
        violations.extend(_validate_trace_receipt(trace_receipt))

    agent_intent = bundle.get("agent_intent")
    if agent_intent is not None:
        violations.extend(
            _validate_agent_intent(
                agent_intent,
                proposal,
                bundle.get("proposal_attempt_snapshot"),
                bundle.get("capability_lease"),
            )
        )

    if gate_report is not None:
        gate_context = {
            "resolution_contract": resolution,
            "candidate_contract": candidate,
            "candidate_revision": candidate_revision,
            "evaluation_plan": evaluation_plan,
        }
        missing = [name for name, value in gate_context.items() if value is None]
        if missing:
            for name in missing:
                violations.append(
                    _violation(
                        "gate_report.semantic_context_missing",
                        (name,),
                        f"GateReport semantics require related {name}",
                    )
                )
        else:
            violations.extend(
                _integrity_as_semantic(
                    gate_chain_violations(
                        gate_report,
                        resolution,  # type: ignore[arg-type]
                        candidate,  # type: ignore[arg-type]
                        candidate_revision,  # type: ignore[arg-type]
                        evaluation_plan,  # type: ignore[arg-type]
                        bundle.get("gate_track_receipts"),
                    )
                )
            )
            violations.extend(
                _validate_generator_judge_independence(
                    resolution,  # type: ignore[arg-type]
                    candidate_revision,  # type: ignore[arg-type]
                    evaluation_plan,  # type: ignore[arg-type]
                    gate_report,
                    bundle,
                )
            )

    workorder = bundle.get("workorder")
    if workorder is not None:
        required = {
            "resolution_contract": resolution,
            "candidate_contract": candidate,
            "candidate_revision": candidate_revision,
            "evaluation_plan": evaluation_plan,
            "gate_report": gate_report,
        }
        missing = [name for name, document in required.items() if document is None]
        if missing:
            for name in missing:
                violations.append(
                    _violation(
                        "workorder.semantic_context_missing",
                        (name,),
                        f"WorkOrder semantics require related {name}",
                    )
                )
        else:
            violations.extend(
                _validate_workorder(
                    workorder,
                    resolution,  # type: ignore[arg-type]
                    candidate,  # type: ignore[arg-type]
                    required["candidate_revision"],  # type: ignore[arg-type]
                    required["evaluation_plan"],  # type: ignore[arg-type]
                    required["gate_report"],  # type: ignore[arg-type]
                    bundle.get("gate_track_receipts"),
                )
            )

    return tuple(violations)


def assert_semantically_valid(bundle: Bundle) -> None:
    """Raise a stable aggregate exception when v4 semantics fail."""

    violations = validate_semantics(bundle)
    if violations:
        raise SemanticValidationError(violations)
