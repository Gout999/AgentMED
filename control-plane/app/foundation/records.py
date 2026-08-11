"""Closed V5 record-envelope primitives (schema-major-2).

The record envelope is the immutable self-describing wrapper every V5 record
carries.  This module owns the canonical envelope field set and the single
validation primitive for it.  It defines data and verification mechanics only
(import rule: stdlib + ``app.models`` + ``app.utils``); it never owns domain
commands, capability activation or coordinator decisions, and it never imports
a domain service, API, CLI, Console or adapter.
"""
from __future__ import annotations

from typing import Any

from app.utils.v4_integrity import V4IntegrityError
from app.utils.v5_integrity import V5_HASH_RULE, assert_v5_record_digest

# Envelope fields that every closed V5 record envelope must carry, exactly.
RECORD_ENVELOPE_FIELDS: tuple[str, ...] = (
    "schema_version",
    "workspace_id",
    "revision",
    "recorded_by_principal",
    "recorded_at",
    "immutable",
    "hash_rule",
    "record_digest",
    "authority_receipt_id",
)


class RecordEnvelopeValidationError(Exception):
    """Raised when a record envelope violates the closed field/schema rules."""


def validate_record_envelope_payload(envelope: Any) -> str:
    """Verify one record envelope payload; return its stored record digest.

    The stored digest is first verified over the full envelope (excluding
    ``/record_envelope/record_digest``, raising ``V4IntegrityError`` exactly as
    the underlying ``assert_v5_record_digest`` does), then the nested
    ``record_envelope`` must carry exactly :data:`RECORD_ENVELOPE_FIELDS` with
    the frozen schema-major-2 markers.
    """
    verified_digest = assert_v5_record_digest(envelope)
    record_envelope = envelope.get("record_envelope")
    if (
        not isinstance(record_envelope, dict)
        or set(record_envelope) != set(RECORD_ENVELOPE_FIELDS)
        or record_envelope.get("schema_version") != "2.0"
        or record_envelope.get("immutable") is not True
        or record_envelope.get("hash_rule") != V5_HASH_RULE
    ):
        raise RecordEnvelopeValidationError("v5.record_envelope_fields_invalid")
    return verified_digest


__all__ = [
    "RECORD_ENVELOPE_FIELDS",
    "RecordEnvelopeValidationError",
    "validate_record_envelope_payload",
]
