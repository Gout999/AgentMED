"""Major-aware exact record binding validation for the V5 foundation.

The exact binding is the closed ``{kind, id, revision, digest}`` reference
used across V5 records, events, receipts and graph verification.  This module
defines the per-contract-major binding kind enums and the validation
mechanics only; it never owns domain commands, capability activation,
transport generation or coordinator decisions, and it never imports a domain
service, API, CLI, Console or adapter.

Import rule: stdlib + ``app.models`` + ``app.utils`` only.
"""
from __future__ import annotations

import re
from typing import Any

#: Exact record binding kinds per ``contracts/v5/schema-profiles.yaml``
#: ``exact_record_binding_v5`` (contract major 2).
V5_BINDING_KINDS = frozenset(
    {
        "AI_APPLICATION",
        "ENVIRONMENT",
        "SYSTEM_COMPONENT",
        "DEPENDENCY_EDGE",
        "COMPONENT_REVISION",
        "PROVIDER_VERSION_ATTESTATION",
        "TOPOLOGY_REVISION",
        "SYSTEM_VERSION_SET",
        "BOOTSTRAP_ATTESTATION",
        "SYSTEM_ASSIGNMENT",
        "APPLICATION_CASE_BINDING",
        "CASE_EPISODE_BINDING",
        "ACCEPTANCE_CRITERIA_REVISION",
        "BADCASE_SPEC",
        "REGRESSION_ASSET",
        "OBSERVED_STATE_SNAPSHOT",
        "OPERATION_EXECUTION_RECEIPT",
        "EXTERNAL_EFFECT_RECEIPT",
        "SYSTEM_EPISODE_SNAPSHOT",
        "EVALUATION_BUNDLE",
        "RELEASE_PLAN",
        "SYSTEM_CANDIDATE_REVISION",
        "SYSTEM_EVALUATION_PLAN",
        "SYSTEM_GATE_EXECUTION",
        "SYSTEM_GATE_TRACK_RECEIPT",
        "SYSTEM_GATE_REPORT",
        "SYSTEM_WORKORDER",
        "SYSTEM_RECOVERY_WORKORDER",
        "SYSTEM_APPROVAL_GRANT",
        "SYSTEM_CAPABILITY_LEASE",
        "SYSTEM_EXTERNAL_OPERATION",
    }
)

#: Exact record binding kinds per ``contracts/v5/schema-profiles.yaml``
#: ``exact_record_binding_v4_bridge`` (contract major 1): exact references to
#: preserved contract-major-1 records.
V4_BRIDGE_BINDING_KINDS = frozenset(
    {
        "QUALITY_CASE",
        "CANDIDATE_CONTRACT",
        "PROPOSAL",
        "PROPOSAL_DECISION",
        "ATTEMPT",
        "GATE_TRACK_RECEIPT",
        "CONTROLLER_REGISTRATION",
        "TRACE_EVIDENCE_RECEIPT",
        "MODEL_CALL_RECEIPT",
        "RESOLUTION_REVIEW_RECEIPT",
    }
)

#: Closed field set of an exact record binding.
BINDING_FIELD_NAMES = ("kind", "id", "revision", "digest")

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: ``subject_kind`` -> envelope field carrying the record id, used when
#: deriving the exact subject binding that cannot live inside the hashed
#: envelope without creating a digest cycle.
_V5_EXACT_BINDING_ID_FIELD: dict[str, str] = {
    "APPLICATION_CASE_BINDING": "application_case_binding_id",
    "ACCEPTANCE_CRITERIA_REVISION": "acceptance_criteria_revision_id",
}


class BindingValidationError(Exception):
    """An exact record binding violates the closed shape for its major."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def validate_binding(value: Any, *, contract_major: int) -> dict[str, Any]:
    """Validate a closed exact record binding for the given contract major.

    ``contract_major=2`` accepts kinds from :data:`V5_BINDING_KINDS`;
    ``contract_major=1`` accepts the preserved V4 bridge kinds from
    :data:`V4_BRIDGE_BINDING_KINDS`.  The value must be a dict whose field set
    is exactly ``{kind, id, revision, digest}``, with a non-empty string id, a
    revision of ``int >= 1`` (bool is rejected) and a digest matching
    ``^sha256:[0-9a-f]{64}$``.  Unknown fields are rejected.  On success the
    original ``value`` object is returned unchanged.
    """

    if not isinstance(value, dict) or set(value) != set(BINDING_FIELD_NAMES):
        raise BindingValidationError("v5.exact_binding_fields_mismatch")
    kind = value.get("kind")
    binding_id = value.get("id")
    revision = value.get("revision")
    digest = value.get("digest")
    if contract_major == 2:
        kinds = V5_BINDING_KINDS
    elif contract_major == 1:
        kinds = V4_BRIDGE_BINDING_KINDS
    else:
        raise BindingValidationError("v5.binding_major_invalid")
    if kind not in kinds:
        raise BindingValidationError("v5.exact_binding_kind_mismatch")
    if not isinstance(binding_id, str) or not binding_id:
        raise BindingValidationError("v5.exact_binding_id_invalid")
    if (
        not isinstance(revision, int)
        or isinstance(revision, bool)
        or revision < 1
    ):
        raise BindingValidationError("v5.exact_binding_revision_invalid")
    if not isinstance(digest, str) or _DIGEST_RE.fullmatch(digest) is None:
        raise BindingValidationError("v5.exact_binding_digest_invalid")
    return value


def derive_exact_subject_binding(
    subject_kind: str, envelope: dict[str, Any]
) -> dict[str, Any]:
    """Derive the exact subject binding for a subject envelope payload.

    Extracted verbatim from ``app.services.v5_authority``
    ``_derived_exact_subject_binding``; the error class is the foundation's
    :class:`BindingValidationError` (a foundation package cannot import the
    domain ``V5AuthorityError``) while the error code string is preserved.
    """

    envelope_payload = envelope.get("record_envelope")
    id_field = _V5_EXACT_BINDING_ID_FIELD.get(subject_kind)
    if (
        not isinstance(envelope_payload, dict)
        or id_field is None
        or envelope.get(id_field) is None
        or not isinstance(envelope_payload.get("record_digest"), str)
    ):
        raise BindingValidationError("v5.authority.subject_binding_invalid")
    return {
        "kind": subject_kind,
        "id": envelope[id_field],
        "revision": None,
        "digest": envelope_payload["record_digest"],
    }


__all__ = [
    "V5_BINDING_KINDS",
    "V4_BRIDGE_BINDING_KINDS",
    "BINDING_FIELD_NAMES",
    "BindingValidationError",
    "validate_binding",
    "derive_exact_subject_binding",
]
