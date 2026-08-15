"""RFC 8785 integrity and GateReport invariants for CaseLoop v4 contracts.

Canonical bytes are produced by the pinned ``rfc8785==0.1.4`` implementation.
Unicode is preserved exactly (no normalization) and object names are sorted by
UTF-16 code units as required by JCS.  CaseLoop v4 deliberately rejects Python
``float`` values, including negative zero and non-finite values, so every
accepted numeric value is an RFC 8785 safe integer.  Missing the pinned package
is a hard conformance failure; there is no fallback serializer.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Mapping, Sequence

try:
    import rfc8785
except ModuleNotFoundError as exc:  # pragma: no cover - dependency failure is fatal
    raise RuntimeError(
        "CaseLoop v4 integrity requires pinned dependency rfc8785==0.1.4"
    ) from exc


Document = Mapping[str, Any]

IMMUTABLE_DIGEST_FIELDS = {
    "agent_intent": "intent_digest",
    "agent_manifest": "manifest_digest",
    "agent_run_ref": "agent_run_ref_digest",
    "attempt": "attempt_digest",
    "authority_receipt": "authority_receipt_digest",
    "resolution_contract": "contract_digest",
    "resolution_review_receipt": "review_receipt_digest",
    "candidate_contract": "contract_digest",
    "candidate_revision": "revision_digest",
    "capability_lease": "grant_digest",
    "controller_registration": "registration_digest",
    "evaluation_plan": "plan_digest",
    "idempotency_receipt": "receipt_digest",
    "mcp_manifest": "manifest_digest",
    "model_call_receipt": "model_call_receipt_digest",
    "participation_manifest": "manifest_digest",
    "proposal": "proposal_digest",
    "proposal_decision": "decision_digest",
    "gate_report": "gate_report_digest",
    "gate_track_receipt": "gate_track_receipt_digest",
    "signal_envelope": "signal_digest",
    "skill_manifest": "manifest_digest",
    "trace_evidence_receipt": "receipt_digest",
    "worker_task": "task_digest",
    "workorder": "workorder_hash",
}

RECORD_METADATA = {
    "agent_intent": ("AGENT_INTENT", "agent_intent_id", "intent_digest"),
    "agent_manifest": ("AGENT_MANIFEST", "agent_manifest_id", "manifest_digest"),
    "agent_run_ref": ("AGENT_RUN_REF", "agent_run_ref_id", "agent_run_ref_digest"),
    "attempt": ("ATTEMPT", "attempt_id", "attempt_digest"),
    "authority_receipt": ("AUTHORITY_RECEIPT", "authority_receipt_id", "authority_receipt_digest"),
    "candidate_contract": ("CANDIDATE_CONTRACT", "candidate_contract_id", "contract_digest"),
    "candidate_revision": ("CANDIDATE_REVISION", "candidate_revision_id", "revision_digest"),
    "capability_lease": ("CAPABILITY_LEASE", "capability_grant_id", "grant_digest"),
    "controller_registration": ("CONTROLLER_REGISTRATION", "controller_registration_id", "registration_digest"),
    "evaluation_plan": ("EVALUATION_PLAN", "evaluation_plan_id", "plan_digest"),
    "gate_report": ("GATE_REPORT", "gate_report_id", "gate_report_digest"),
    "gate_track_receipt": ("GATE_TRACK_RECEIPT", "gate_track_receipt_id", "gate_track_receipt_digest"),
    "idempotency_receipt": ("IDEMPOTENCY_RECEIPT", "idempotency_receipt_id", "receipt_digest"),
    "mcp_manifest": ("MCP_MANIFEST", "mcp_manifest_id", "manifest_digest"),
    "model_call_receipt": ("MODEL_CALL_RECEIPT", "model_call_receipt_id", "model_call_receipt_digest"),
    "participation_manifest": ("PARTICIPATION_MANIFEST", "participation_manifest_id", "manifest_digest"),
    "proposal": ("PROPOSAL", "proposal_id", "proposal_digest"),
    "proposal_decision": ("PROPOSAL_DECISION", "proposal_decision_id", "decision_digest"),
    "resolution_contract": ("RESOLUTION_CONTRACT", "resolution_contract_id", "contract_digest"),
    "resolution_review_receipt": ("RESOLUTION_REVIEW_RECEIPT", "resolution_review_receipt_id", "review_receipt_digest"),
    "signal_envelope": ("SIGNAL_ENVELOPE", "signal_id", "signal_digest"),
    "skill_manifest": ("SKILL_MANIFEST", "skill_manifest_id", "manifest_digest"),
    "trace_evidence_receipt": ("TRACE_EVIDENCE_RECEIPT", "receipt_id", "receipt_digest"),
    "worker_task": ("WORKER_TASK", "worker_task_id", "task_digest"),
    "workorder": ("WORKORDER", "workorder_id", "workorder_hash"),
}


@dataclass(frozen=True)
class IntegrityViolation:
    """Stable, machine-readable integrity or GateReport violation."""

    code: str
    path: tuple[str | int, ...]
    message: str


class IntegrityValidationError(ValueError):
    """Raised when a v4 immutable record or GateReport fails validation."""

    def __init__(self, violations: Sequence[IntegrityViolation]) -> None:
        self.violations = tuple(violations)
        detail = "; ".join(
            f"{item.code}@{'.'.join(map(str, item.path))}"
            for item in self.violations
        )
        super().__init__(detail)


class UnsupportedJCSValue(ValueError):
    """A JSON value lies outside CaseLoop's deterministic JCS profile."""


def _reject_floats(value: Any, path: tuple[str | int, ...] = ()) -> None:
    if isinstance(value, float):
        raise UnsupportedJCSValue(
            f"floating-point value at {'.'.join(map(str, path)) or '<root>'} is forbidden"
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise UnsupportedJCSValue(
                    f"non-string object name at {'.'.join(map(str, path)) or '<root>'}"
                )
            _reject_floats(item, (*path, key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_floats(item, (*path, index))


def canonical_json_bytes(value: Any) -> bytes:
    """Return RFC 8785 canonical bytes for the CaseLoop no-float profile."""

    _reject_floats(value)
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, UnicodeError, ValueError, TypeError) as exc:
        raise UnsupportedJCSValue(str(exc)) from exc


def hash_rule(digest_field: str) -> str:
    """Return the frozen self-hash rule for one top-level digest field."""

    return f"jcs-rfc8785-v1+sha256(excluding:/{digest_field})"


def compute_record_digest(document: Document, digest_field: str) -> str:
    """Hash a record after excluding exactly its own top-level digest field."""

    if digest_field not in document:
        raise KeyError(digest_field)
    canonical_body = {
        key: value for key, value in document.items() if key != digest_field
    }
    return "sha256:" + sha256(canonical_json_bytes(canonical_body)).hexdigest()


def compute_content_digest(value: Any) -> str:
    """Hash referenced content without claiming that it is a record self-hash."""

    return "sha256:" + sha256(canonical_json_bytes(value)).hexdigest()


def exact_record_binding(
    record_name: str,
    document: Document,
    *,
    revision: int | None | object = ...,
) -> dict[str, Any]:
    """Return the exact kind/id/revision/digest binding for one frozen snapshot."""

    kind, identifier_field, digest_field = RECORD_METADATA[record_name]
    if revision is ...:
        revision = document.get("revision")
    return {
        "kind": kind,
        "id": document[identifier_field],
        "revision": revision,
        "digest": document[digest_field],
    }


def model_independence_key(basis: Document) -> str:
    """Hash the complete normalized provider-independence basis."""

    return compute_content_digest(basis)


_RESOLUTION_REVIEWED_FIELDS = (
    "schema_version",
    "resolution_contract_id",
    "workspace_id",
    "case_id",
    "revision",
    "problem",
    "objective",
    "agent_version_set",
    "allowed_change_surface",
    "allowed_action_scope",
    "forbidden_actions",
    "acceptance_criteria",
    "budget",
    "risk_class",
    "stop_conditions",
    "input_snapshot_digest",
    "proposer",
    "review_policy",
)


def resolution_reviewed_content_digest(resolution: Document) -> str:
    """Digest only the Resolution content that exists before review/freeze."""

    return compute_content_digest(
        {field: resolution[field] for field in _RESOLUTION_REVIEWED_FIELDS}
    )


def model_call_receipt_violations(
    receipt: Document,
    attempt_snapshot: Document,
    agent_manifest: Document,
) -> tuple[IntegrityViolation, ...]:
    """Validate one real call receipt against its call-time identity snapshot."""

    violations: list[IntegrityViolation] = []
    if receipt.get("attempt_snapshot") != exact_record_binding(
        "attempt", attempt_snapshot
    ) or attempt_snapshot.get("state") != "RUNNING":
        violations.append(
            IntegrityViolation(
                "model_call_receipt.attempt_snapshot_mismatch",
                ("attempt_snapshot",),
                "ModelCallReceipt must bind the exact call-time RUNNING Attempt snapshot",
            )
        )
    if receipt.get("agent_manifest") != exact_record_binding(
        "agent_manifest", agent_manifest
    ):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.agent_manifest_mismatch",
                ("agent_manifest",),
                "ModelCallReceipt must bind the exact AgentManifest used by the Attempt",
            )
        )
    attempt_model = attempt_snapshot.get("model", {})
    attempt_runtime = attempt_snapshot.get("runtime", {})
    if (
        receipt.get("workspace_id") != attempt_snapshot.get("workspace_id")
        or receipt.get("executor_principal")
        != attempt_snapshot.get("executor_principal")
        or receipt.get("agent_manifest") != attempt_snapshot.get("agent_manifest")
        or receipt.get("runtime_session_id")
        != attempt_runtime.get("runtime_session_id")
        or receipt.get("requested_model") != attempt_model.get("requested_model")
        or receipt.get("resolved_provider")
        != attempt_model.get("resolved_provider")
        or receipt.get("resolved_model") != attempt_model.get("resolved_model")
        or receipt.get("model_resolution_receipt_digest")
        != attempt_model.get("model_resolution_receipt_digest")
    ):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.attempt_identity_mismatch",
                ("attempt_snapshot",),
                "call receipt identity, runtime and resolved model must equal the call-time Attempt",
            )
        )
    if agent_manifest.get("principal_id") != receipt.get("executor_principal"):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.manifest_principal_mismatch",
                ("executor_principal",),
                "call executor must equal the exact AgentManifest principal",
            )
        )
    model_policy = agent_manifest.get("model_policy", {})
    allowed_models = {
        model_policy.get("primary_model"),
        *(
            item.get("model")
            for item in model_policy.get("allowed_fallback_models", ())
            if isinstance(item, Mapping)
        ),
    }
    if (
        receipt.get("requested_model") not in allowed_models
        or receipt.get("resolved_model") not in allowed_models
    ):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.manifest_model_policy_mismatch",
                ("requested_model",),
                "the call-time requested model must be frozen in the exact AgentManifest model policy",
            )
        )
    basis = receipt.get("independence_basis", {})
    provenance = receipt.get("provider_provenance", {})
    provenance_basis = {
        field: provenance.get(field)
        for field in (
            "provider_org",
            "endpoint_origin",
            "account_project",
            "credential_ref",
            "model_family",
        )
    }
    if basis != provenance_basis:
        violations.append(
            IntegrityViolation(
                "model_call_receipt.provenance_basis_mismatch",
                ("independence_basis",),
                "independence basis must equal the observed provider provenance fields",
            )
        )
    if receipt.get("independence_key") != model_independence_key(basis):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.independence_key_mismatch",
                ("independence_key",),
                "independence key must be the canonical digest of the complete basis",
            )
        )
    token_usage = provenance.get("token_usage")
    if token_usage is not None and receipt.get("usage_digest") != compute_content_digest(
        token_usage
    ):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.usage_digest_mismatch",
                ("usage_digest",),
                "usage digest must bind the exact token usage structure",
            )
        )
    if token_usage is not None and token_usage.get("total_tokens") != (
        token_usage.get("input_tokens", 0) + token_usage.get("output_tokens", 0)
    ):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.token_usage_total_mismatch",
                ("provider_provenance", "token_usage", "total_tokens"),
                "provider token total must equal input plus output tokens",
            )
        )
    if (
        provenance.get("returned_model") != receipt.get("resolved_model")
        or provenance.get("raw_retained") is not False
    ):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.provider_result_mismatch",
                ("provider_provenance",),
                "provider returned model and raw-retention policy must match the receipt",
            )
        )
    if not (
        _parse_time(receipt["started_at"])
        <= _parse_time(receipt["completed_at"])
        <= _parse_time(receipt["recorded_at"])
    ):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.invalid_timeline",
                ("recorded_at",),
                "model call start, completion and recording times must be monotonic",
            )
        )
    if attempt_snapshot.get("started_at") is not None and _parse_time(
        receipt["started_at"]
    ) < _parse_time(attempt_snapshot["started_at"]):
        violations.append(
            IntegrityViolation(
                "model_call_receipt.before_attempt_started",
                ("started_at",),
                "model call cannot begin before its call-time RUNNING Attempt",
            )
        )
    return tuple(violations)


def gate_track_receipt_violations(
    receipt: Document,
    resolution: Document,
    candidate_contract: Document,
    candidate_revision: Document,
    evaluation_plan: Document,
) -> tuple[IntegrityViolation, ...]:
    """Validate one typed Gate track against the exact frozen subject chain."""

    violations: list[IntegrityViolation] = []
    expected = {
        "resolution_contract": exact_record_binding("resolution_contract", resolution),
        "candidate_contract": exact_record_binding("candidate_contract", candidate_contract),
        "candidate_revision": exact_record_binding("candidate_revision", candidate_revision),
        "evaluation_plan": exact_record_binding("evaluation_plan", evaluation_plan),
    }
    for field, binding in expected.items():
        if receipt.get(field) != binding:
            violations.append(
                IntegrityViolation(
                    f"gate_track_receipt.{field}_mismatch",
                    (field,),
                    f"GateTrackReceipt must bind the exact {field}",
                )
            )
    if (
        receipt.get("workspace_id") != resolution.get("workspace_id")
        or receipt.get("case_id") != resolution.get("case_id")
    ):
        violations.append(
            IntegrityViolation(
                "gate_track_receipt.subject_context_mismatch",
                ("workspace_id",),
                "GateTrackReceipt must share the frozen subject workspace and Case",
            )
        )
    if _parse_time(receipt["started_at"]) > _parse_time(receipt["completed_at"]):
        violations.append(
            IntegrityViolation(
                "gate_track_receipt.invalid_timeline",
                ("completed_at",),
                "track completion cannot precede track start",
            )
        )
    return tuple(violations)


def revision_chain_violations(
    record_name: str,
    current: Document,
    previous: Document | None,
) -> tuple[IntegrityViolation, ...]:
    """Validate one append-only per-revision snapshot link without querying current state."""

    violations: list[IntegrityViolation] = []
    _kind, identifier_field, _digest_field = RECORD_METADATA[record_name]
    revision = current.get("revision")
    if not isinstance(revision, int) or revision < 1:
        return (
            IntegrityViolation(
                "revision.invalid",
                ("revision",),
                "revision must be a positive integer",
            ),
        )
    if revision == 1:
        if previous is not None or current.get("previous_snapshot") is not None:
            violations.append(
                IntegrityViolation(
                    "revision.genesis_has_previous",
                    ("previous_snapshot",),
                    "revision 1 must not bind a previous snapshot",
                )
            )
        return tuple(violations)
    if previous is None:
        violations.append(
            IntegrityViolation(
                "revision.previous_missing",
                ("previous_snapshot",),
                "non-genesis revision requires the exact preceding snapshot",
            )
        )
        return tuple(violations)
    if current.get(identifier_field) != previous.get(identifier_field):
        violations.append(
            IntegrityViolation(
                "revision.identity_changed",
                (identifier_field,),
                "all revisions must retain one record identity",
            )
        )
    if previous.get("revision") != revision - 1:
        violations.append(
            IntegrityViolation(
                "revision.not_contiguous",
                ("revision",),
                "revision must advance exactly one snapshot",
            )
        )
    if current.get("previous_snapshot") != exact_record_binding(record_name, previous):
        violations.append(
            IntegrityViolation(
                "revision.previous_binding_mismatch",
                ("previous_snapshot",),
                "previous_snapshot must bind the exact immediately preceding revision",
            )
        )
    return tuple(violations)


def record_integrity_violations(
    document: Document, digest_field: str
) -> tuple[IntegrityViolation, ...]:
    """Validate the declared rule and recomputed digest of one immutable record."""

    violations: list[IntegrityViolation] = []
    expected_rule = hash_rule(digest_field)
    if document.get("hash_rule") != expected_rule:
        violations.append(
            IntegrityViolation(
                "integrity.hash_rule_mismatch",
                ("hash_rule",),
                f"hash_rule must be {expected_rule}",
            )
        )
    try:
        actual = compute_record_digest(document, digest_field)
    except (KeyError, UnsupportedJCSValue) as exc:
        violations.append(
            IntegrityViolation(
                "integrity.canonicalization_failed",
                (digest_field,),
                str(exc),
            )
        )
    else:
        if document.get(digest_field) != actual:
            violations.append(
                IntegrityViolation(
                    "integrity.digest_mismatch",
                    (digest_field,),
                    "declared digest does not match RFC 8785 canonical content",
                )
            )
    return tuple(violations)


def assert_record_integrity(document: Document, digest_field: str) -> None:
    """Raise a stable error when an immutable record cannot be reproduced."""

    violations = record_integrity_violations(document, digest_field)
    if violations:
        raise IntegrityValidationError(violations)


def _binding_matches(binding: Document, identifier: Any, digest: Any) -> bool:
    return binding.get("id") == identifier and binding.get("digest") == digest


def _path_is_forbidden(path: str, forbidden_paths: Sequence[str]) -> bool:
    return any(
        path == forbidden or (forbidden.endswith("/") and path.startswith(forbidden))
        for forbidden in forbidden_paths
    )


def candidate_scope_violations(
    candidate_contract: Document,
    resolution: Document | None = None,
) -> tuple[IntegrityViolation, ...]:
    """Enforce the Resolution-to-Candidate change, permission, and budget ceiling."""

    violations: list[IntegrityViolation] = []
    forbidden = candidate_contract.get("forbidden_paths", ())
    allowed_files = candidate_contract.get("allowed_action_scope", {}).get("files", ())
    if any(_path_is_forbidden(item["path"], forbidden) for item in allowed_files):
        violations.append(
            IntegrityViolation(
                "candidate_contract.forbidden_scope_overlap",
                ("allowed_action_scope", "files"),
                "CandidateContract allowed files cannot equal or descend from a forbidden path",
            )
        )
    if resolution is None:
        return tuple(violations)

    kind_to_surface = {
        "PROMPT": "PROMPT",
        "KNOWLEDGE": "KNOWLEDGE",
        "MODEL_CONFIG": "MODEL_CONFIG",
        "SKILL": "SKILL",
        "MCP": "MCP",
        "CODE_PATCH": "CODE",
        "HARNESS": "HARNESS",
        "POLICY": "POLICY",
        "EVALUATOR_ASSET": "TEST",
    }
    if kind_to_surface.get(candidate_contract.get("candidate_kind")) not in set(
        resolution.get("allowed_change_surface", ())
    ):
        violations.append(
            IntegrityViolation(
                "candidate_contract.change_surface_exceeds_resolution",
                ("candidate_kind",),
                "CandidateContract kind must be allowed by ResolutionContract",
            )
        )

    resolution_scope = resolution.get("allowed_action_scope", {})
    candidate_scope = candidate_contract.get("allowed_action_scope", {})
    surface_names = (
        "files",
        "processes",
        "network",
        "mcp_tools",
        "cloud_actions",
        "secret_refs",
    )
    for surface_name in surface_names:
        ceiling = {
            canonical_json_bytes(item)
            for item in resolution_scope.get(surface_name, ())
        }
        requested = {
            canonical_json_bytes(item)
            for item in candidate_scope.get(surface_name, ())
        }
        if not requested <= ceiling:
            violations.append(
                IntegrityViolation(
                    "candidate_contract.action_scope_exceeds_resolution",
                    ("allowed_action_scope", surface_name),
                    f"CandidateContract {surface_name} scope must be a subset of ResolutionContract",
                )
            )

    forbidden_categories = {
        "PROCESS_EXECUTION": "processes",
        "NETWORK_ACCESS": "network",
        "MCP_TOOL_CALL": "mcp_tools",
        "CLOUD_ACTION": "cloud_actions",
        "SECRET_ACCESS": "secret_refs",
    }
    forbidden_actions = set(resolution.get("forbidden_actions", ()))
    for action, surface_name in forbidden_categories.items():
        if action in forbidden_actions and candidate_scope.get(surface_name):
            violations.append(
                IntegrityViolation(
                    "candidate_contract.forbidden_action_requested",
                    ("allowed_action_scope", surface_name),
                    f"ResolutionContract forbids {action}",
                )
            )
    repository_actions = {
        item.get("action")
        for item in candidate_scope.get("cloud_actions", ())
    }
    if repository_actions & forbidden_actions:
        violations.append(
            IntegrityViolation(
                "candidate_contract.forbidden_action_requested",
                ("allowed_action_scope", "cloud_actions"),
                "CandidateContract requests a repository action forbidden by ResolutionContract",
            )
        )

    candidate_budget = candidate_contract.get("budget", {})
    resolution_budget = resolution.get("budget", {})
    for dimension in (
        "max_attempts",
        "max_wall_seconds",
        "max_tokens",
        "max_cost_microunits",
    ):
        if candidate_budget.get(dimension, 0) > resolution_budget.get(dimension, -1):
            violations.append(
                IntegrityViolation(
                    "candidate_contract.budget_exceeds_resolution",
                    ("budget", dimension),
                    f"CandidateContract {dimension} cannot exceed ResolutionContract",
                )
            )
    return tuple(violations)


def required_acceptance_criteria_digest(resolution: Document) -> str:
    """Digest the complete ordered set of required Resolution criteria."""

    required = [
        criterion
        for criterion in resolution.get("acceptance_criteria", ())
        if criterion.get("required") is True
    ]
    return "sha256:" + sha256(canonical_json_bytes(required)).hexdigest()


def evaluation_plan_violations(
    evaluation_plan: Document, resolution: Document
) -> tuple[IntegrityViolation, ...]:
    """Prove that the frozen plan covers every required Resolution criterion."""

    violations: list[IntegrityViolation] = []
    if evaluation_plan.get("visible_suite", {}).get(
        "criteria_digest"
    ) != required_acceptance_criteria_digest(resolution):
        violations.append(
            IntegrityViolation(
                "evaluation_plan.criteria_digest_mismatch",
                ("visible_suite", "criteria_digest"),
                "visible suite must bind the canonical complete required criteria set",
            )
        )
    required_tracks = set(evaluation_plan.get("required_tracks", ()))
    required_verifiers = {
        criterion.get("verifier_kind")
        for criterion in resolution.get("acceptance_criteria", ())
        if criterion.get("required") is True
    }
    if not required_verifiers <= required_tracks:
        violations.append(
            IntegrityViolation(
                "evaluation_plan.required_criterion_unmapped",
                ("required_tracks",),
                "every required Resolution criterion verifier must remain a required Gate track",
            )
        )
    return tuple(violations)


def gate_report_violations(
    report: Document,
    evaluation_plan: Document | None = None,
    gate_track_receipts: Sequence[Document] | None = None,
) -> tuple[IntegrityViolation, ...]:
    """Enforce exact typed-receipt coverage and the bidirectional PASS rule."""

    violations: list[IntegrityViolation] = []
    required = list(report.get("required_tracks", ()))
    bindings = list(report.get("track_receipts", ()))
    receipt_tracks = [binding.get("track") for binding in bindings]

    if len(receipt_tracks) != len(set(receipt_tracks)):
        violations.append(
            IntegrityViolation(
                "gate_report.duplicate_track",
                ("track_receipts",),
                "each Gate track must have exactly one terminal receipt",
            )
        )
    missing = set(required) - set(receipt_tracks)
    if missing:
        violations.append(
            IntegrityViolation(
                "gate_report.required_track_missing",
                ("track_receipts",),
                "every required Gate track must have a terminal receipt",
            )
        )
    unexpected = set(receipt_tracks) - set(required)
    if unexpected:
        violations.append(
            IntegrityViolation(
                "gate_report.unexpected_track",
                ("track_receipts",),
                "GateReport cannot substitute an unrequired track",
            )
        )

    if evaluation_plan is not None:
        if set(required) != set(evaluation_plan.get("required_tracks", ())):
            violations.append(
                IntegrityViolation(
                    "gate_report.evaluation_tracks_mismatch",
                    ("required_tracks",),
                    "GateReport required tracks must exactly equal EvaluationPlan",
                )
            )

    resolved: list[Document] = []
    if gate_track_receipts is None:
        violations.append(
            IntegrityViolation(
                "gate_report.track_receipt_context_missing",
                ("track_receipts",),
                "GateReport semantics require the referenced typed GateTrackReceipt records",
            )
        )
    else:
        by_id: dict[Any, list[Document]] = {}
        for receipt in gate_track_receipts:
            by_id.setdefault(receipt.get("gate_track_receipt_id"), []).append(receipt)
        if any(len(items) != 1 for items in by_id.values()):
            violations.append(
                IntegrityViolation(
                    "gate_report.track_receipt_id_duplicate",
                    ("track_receipts",),
                    "GateTrackReceipt ids must resolve uniquely",
                )
            )
        for index, binding in enumerate(bindings):
            exact_binding = binding.get("receipt", {})
            matches = by_id.get(exact_binding.get("id"), ())
            if len(matches) != 1:
                violations.append(
                    IntegrityViolation(
                        "gate_report.track_receipt_missing",
                        ("track_receipts", index, "receipt"),
                        "each GateReport receipt binding must resolve exactly once",
                    )
                )
                continue
            receipt = matches[0]
            if exact_binding != exact_record_binding("gate_track_receipt", receipt):
                violations.append(
                    IntegrityViolation(
                        "gate_report.track_receipt_binding_mismatch",
                        ("track_receipts", index, "receipt"),
                        "GateReport must bind the exact typed GateTrackReceipt snapshot",
                    )
                )
                continue
            resolved.append(receipt)
            violations.extend(
                IntegrityViolation(
                    item.code,
                    ("track_receipts", index, *item.path),
                    item.message,
                )
                for item in record_integrity_violations(
                    receipt, IMMUTABLE_DIGEST_FIELDS["gate_track_receipt"]
                )
            )
            if receipt.get("track") != binding.get("track"):
                violations.append(
                    IntegrityViolation(
                        "gate_report.track_label_mismatch",
                        ("track_receipts", index, "track"),
                        "GateReport track label must equal the resolved receipt track",
                    )
                )
            if receipt.get("gate_execution_id") != report.get("gate_execution_id"):
                violations.append(
                    IntegrityViolation(
                        "gate_report.track_gate_execution_mismatch",
                        ("track_receipts", index),
                        "every typed receipt must belong to the exact Gate execution",
                    )
                )

    all_required_complete_pass = (
        len(receipt_tracks) == len(set(receipt_tracks))
        and set(receipt_tracks) == set(required)
        and len(resolved) == len(bindings)
        and all(
            receipt.get("status") == "PASS"
            and receipt.get("completeness") == "COMPLETE"
            for receipt in resolved
        )
        and report.get("completeness") == "COMPLETE"
    )
    if (report.get("status") == "PASS") != all_required_complete_pass:
        violations.append(
            IntegrityViolation(
                "gate_report.pass_iff_complete",
                ("status",),
                "PASS iff every exact required track is COMPLETE, PASS, and receipt-backed",
            )
        )
    started_at = _parse_time(report["started_at"])
    completed_at = _parse_time(report["completed_at"])
    if started_at >= completed_at:
        violations.append(
            IntegrityViolation(
                "gate_report.invalid_terminal_window",
                ("completed_at",),
                "GateReport completion must be strictly after Gate start",
            )
        )
    for index, receipt in enumerate(resolved):
        receipt_time = _parse_time(receipt["completed_at"])
        if not started_at <= receipt_time <= completed_at:
            violations.append(
                IntegrityViolation(
                    "gate_report.track_outside_execution_window",
                    ("track_receipts", index, "completed_at"),
                    "track receipt time must be inside the Gate execution window",
                )
            )
    if resolved:
        actual_facets = {
            facet for receipt in resolved for facet in receipt.get("evidence_facets", ())
        }
        if set(report.get("evidence_facets", ())) != actual_facets:
            violations.append(
                IntegrityViolation(
                    "gate_report.evidence_facets_mismatch",
                    ("evidence_facets",),
                    "GateReport facets must equal the union of exact typed receipt facets",
                )
            )
        status = report.get("status")
        if status != "PASS" and not any(
            receipt.get("status") == status for receipt in resolved
        ):
            violations.append(
                IntegrityViolation(
                    "gate_report.terminal_status_unbacked",
                    ("status",),
                    "a non-PASS Gate status must be backed by a typed receipt with that status",
                )
            )
    return tuple(violations)


def gate_chain_violations(
    report: Document,
    resolution: Document,
    candidate_contract: Document,
    candidate_revision: Document,
    evaluation_plan: Document,
    gate_track_receipts: Sequence[Document] | None = None,
) -> tuple[IntegrityViolation, ...]:
    """Validate the exact pre-build, post-build, evaluation, and Gate subject chain."""

    violations = list(candidate_scope_violations(candidate_contract, resolution))
    violations.extend(evaluation_plan_violations(evaluation_plan, resolution))
    violations.extend(
        gate_report_violations(report, evaluation_plan, gate_track_receipts)
    )

    referenced_ids = {
        binding.get("receipt", {}).get("id")
        for binding in report.get("track_receipts", ())
    }
    for receipt in gate_track_receipts or ():
        if receipt.get("gate_track_receipt_id") not in referenced_ids:
            continue
        violations.extend(
            gate_track_receipt_violations(
                receipt,
                resolution,
                candidate_contract,
                candidate_revision,
                evaluation_plan,
            )
        )

    comparisons = (
        ("resolution_contract", resolution["resolution_contract_id"], resolution["contract_digest"]),
        ("candidate_contract", candidate_contract["candidate_contract_id"], candidate_contract["contract_digest"]),
        ("candidate_revision", candidate_revision["candidate_revision_id"], candidate_revision["revision_digest"]),
        ("evaluation_plan", evaluation_plan["evaluation_plan_id"], evaluation_plan["plan_digest"]),
    )
    for name, identifier, digest in comparisons:
        if not _binding_matches(report[name], identifier, digest):
            violations.append(
                IntegrityViolation(
                    f"gate_report.{name}_mismatch",
                    (name,),
                    f"GateReport must bind the exact {name}",
                )
            )

    if (
        report.get("workspace_id") != resolution.get("workspace_id")
        or report.get("workspace_id") != candidate_contract.get("workspace_id")
        or report.get("workspace_id") != candidate_revision.get("workspace_id")
        or report.get("workspace_id") != evaluation_plan.get("workspace_id")
        or report.get("case_id") != resolution.get("case_id")
        or report.get("case_id") != candidate_contract.get("case_id")
        or report.get("case_id") != candidate_revision.get("case_id")
        or report.get("case_id") != evaluation_plan.get("case_id")
    ):
        violations.append(
            IntegrityViolation(
                "gate_report.subject_context_mismatch",
                ("workspace_id",),
                "all Gate subject records must share workspace and Case",
            )
        )

    if (
        candidate_contract.get("resolution_contract") != report.get("resolution_contract")
        or candidate_revision.get("resolution_contract") != report.get("resolution_contract")
        or candidate_revision.get("candidate_contract") != report.get("candidate_contract")
        or evaluation_plan.get("resolution_contract") != report.get("resolution_contract")
        or evaluation_plan.get("candidate_contract") != report.get("candidate_contract")
        or evaluation_plan.get("candidate_planned_revision")
        != candidate_contract.get("planned_revision")
        or candidate_revision.get("candidate_id") != candidate_contract.get("candidate_id")
        or candidate_revision.get("revision") != candidate_contract.get("planned_revision")
    ):
        violations.append(
            IntegrityViolation(
                "gate_report.contract_chain_mismatch",
                ("candidate_revision",),
                "Resolution, CandidateContract, CandidateRevision, and EvaluationPlan must form one exact chain",
            )
        )

    if candidate_revision.get("base") != candidate_contract.get("base"):
        violations.append(
            IntegrityViolation(
                "gate_report.candidate_base_mismatch",
                ("base",),
                "CandidateRevision must use the CandidateContract base",
            )
        )
    if report.get("base") != candidate_revision.get("base"):
        violations.append(
            IntegrityViolation(
                "gate_report.base_mismatch",
                ("base",),
                "GateReport must bind the exact built base",
            )
        )
    if report.get("target") != candidate_revision.get("target"):
        violations.append(
            IntegrityViolation(
                "gate_report.target_mismatch",
                ("target",),
                "GateReport must bind the exact CandidateRevision target",
            )
        )
    if report.get("diff_artifact") != candidate_revision.get("diff_artifact"):
        violations.append(
            IntegrityViolation(
                "gate_report.diff_artifact_mismatch",
                ("diff_artifact",),
                "GateReport must bind the exact CandidateRevision diff artifact",
            )
        )
    if _parse_time(report["started_at"]) < _parse_time(
        candidate_revision["recorded_at"]
    ):
        violations.append(
            IntegrityViolation(
                "gate_report.started_before_candidate_revision",
                ("started_at",),
                "Gate execution cannot start before CandidateRevision is recorded",
            )
        )
    return tuple(violations)


def assert_gate_chain(
    report: Document,
    resolution: Document,
    candidate_contract: Document,
    candidate_revision: Document,
    evaluation_plan: Document,
    gate_track_receipts: Sequence[Document] | None = None,
) -> None:
    violations = gate_chain_violations(
        report,
        resolution,
        candidate_contract,
        candidate_revision,
        evaluation_plan,
        gate_track_receipts,
    )
    if violations:
        raise IntegrityValidationError(violations)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def workorder_chain_violations(
    workorder: Document,
    resolution: Document,
    candidate_contract: Document,
    candidate_revision: Document,
    evaluation_plan: Document,
    gate_report: Document,
    gate_track_receipts: Sequence[Document] | None = None,
) -> tuple[IntegrityViolation, ...]:
    """Fail closed on WorkOrder subject, scope, risk, time, and artifact bindings."""

    violations = list(
        gate_chain_violations(
            gate_report,
            resolution,
            candidate_contract,
            candidate_revision,
            evaluation_plan,
            gate_track_receipts,
        )
    )
    comparisons = (
        ("resolution_contract", resolution["resolution_contract_id"], resolution["contract_digest"]),
        ("candidate_contract", candidate_contract["candidate_contract_id"], candidate_contract["contract_digest"]),
        ("candidate_revision", candidate_revision["candidate_revision_id"], candidate_revision["revision_digest"]),
        ("evaluation_plan", evaluation_plan["evaluation_plan_id"], evaluation_plan["plan_digest"]),
        ("gate_report", gate_report["gate_report_id"], gate_report["gate_report_digest"]),
    )
    for name, identifier, digest in comparisons:
        if not _binding_matches(workorder[name], identifier, digest):
            violations.append(
                IntegrityViolation(
                    f"workorder.{name}_mismatch",
                    (name,),
                    f"WorkOrder must bind the exact {name}",
                )
            )

    if (
        workorder.get("workspace_id") != resolution.get("workspace_id")
        or workorder.get("workspace_id") != candidate_contract.get("workspace_id")
        or workorder.get("workspace_id") != candidate_revision.get("workspace_id")
        or workorder.get("workspace_id") != gate_report.get("workspace_id")
        or workorder.get("case_id") != resolution.get("case_id")
        or workorder.get("case_id") != candidate_contract.get("case_id")
        or workorder.get("case_id") != candidate_revision.get("case_id")
        or workorder.get("case_id") != gate_report.get("case_id")
    ):
        violations.append(
            IntegrityViolation(
                "workorder.subject_context_mismatch",
                ("workspace_id",),
                "WorkOrder and all governed records must share workspace and Case",
            )
        )

    allowed_scope = candidate_contract.get("allowed_action_scope", {})
    requested_scope = workorder.get("action_scope", {})
    for surface_name in (
        "files",
        "processes",
        "network",
        "mcp_tools",
        "cloud_actions",
        "secret_refs",
    ):
        allowed_items = {
            canonical_json_bytes(item) for item in allowed_scope.get(surface_name, ())
        }
        requested_items = {
            canonical_json_bytes(item) for item in requested_scope.get(surface_name, ())
        }
        if not requested_items <= allowed_items:
            violations.append(
                IntegrityViolation(
                    "workorder.action_scope_exceeds_candidate",
                    ("action_scope", surface_name),
                    f"WorkOrder {surface_name} scope must be a subset of CandidateContract",
                )
            )
    forbidden = candidate_contract.get("forbidden_paths", ())
    if any(
        _path_is_forbidden(item["path"], forbidden)
        for item in requested_scope.get("files", ())
    ):
        violations.append(
            IntegrityViolation(
                "workorder.forbidden_path_requested",
                ("action_scope", "files"),
                "WorkOrder cannot request a forbidden CandidateContract path",
            )
        )

    risk_order = {
        "R0_READ_ONLY": 0,
        "R1_REVERSIBLE": 1,
        "R2_HIGH_IMPACT": 2,
        "R3_IRREVERSIBLE": 3,
    }
    if risk_order.get(workorder.get("risk_class"), -1) < risk_order.get(
        resolution.get("risk_class"), 4
    ):
        violations.append(
            IntegrityViolation(
                "workorder.risk_downgrade",
                ("risk_class",),
                "WorkOrder risk cannot be lower than ResolutionContract risk",
            )
        )
    if (
        workorder.get("risk_class") in {"R2_HIGH_IMPACT", "R3_IRREVERSIBLE"}
        and workorder.get("required_authorization") != "HUMAN_APPROVAL"
    ):
        violations.append(
            IntegrityViolation(
                "workorder.high_risk_requires_human",
                ("required_authorization",),
                "R2/R3 WorkOrders require human approval",
            )
        )

    if _parse_time(workorder["created_at"]) >= _parse_time(workorder["expires_at"]):
        violations.append(
            IntegrityViolation(
                "workorder.invalid_expiry",
                ("expires_at",),
                "WorkOrder must expire strictly after creation",
            )
        )
    if _parse_time(workorder["created_at"]) < _parse_time(gate_report["completed_at"]):
        violations.append(
            IntegrityViolation(
                "workorder.created_before_gate_terminal",
                ("created_at",),
                "WorkOrder cannot be created before its GateReport completes",
            )
        )

    target = workorder.get("target", {})
    base = candidate_contract.get("base", {})
    revision_target = candidate_revision.get("target", {})
    if (
        target.get("repository_ref") != base.get("target_ref")
        or target.get("base_revision") != base.get("revision")
        or target.get("base_digest") != base.get("content_digest")
        or target.get("repository_ref") != revision_target.get("target_ref")
        or target.get("target_revision") != revision_target.get("revision")
        or target.get("target_digest") != revision_target.get("content_digest")
    ):
        violations.append(
            IntegrityViolation(
                "workorder.target_chain_mismatch",
                ("target",),
                "WorkOrder base and target must come from CandidateContract and CandidateRevision",
            )
        )
    if workorder.get("candidate_artifact") != candidate_revision.get(
        "candidate_artifact"
    ):
        violations.append(
            IntegrityViolation(
                "workorder.candidate_artifact_mismatch",
                ("candidate_artifact",),
                "WorkOrder must bind the exact CandidateRevision artifact",
            )
        )
    if workorder.get("diff_artifact") != candidate_revision.get("diff_artifact"):
        violations.append(
            IntegrityViolation(
                "workorder.diff_artifact_mismatch",
                ("diff_artifact",),
                "WorkOrder must bind the exact CandidateRevision diff",
            )
        )
    if workorder.get("gate_status") != "PASS" or gate_report.get("status") != "PASS":
        violations.append(
            IntegrityViolation(
                "workorder.gate_not_pass",
                ("gate_status",),
                "only an exact valid PASS GateReport can authorize WorkOrder creation",
            )
        )
    return tuple(violations)
