"""RFC 8785 integrity helpers for immutable AgentMED V5 records.

V5 records carry a nested ``record_envelope`` (schema-major-2).  The canonical
record digest covers the full record — envelope and payload — excluding exactly
``/record_envelope/record_digest``, per
``contracts/v5/schema-profiles.yaml#common/canonical_record_digest``.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import re
from collections.abc import Mapping
from typing import Any

from app.utils.v4_integrity import V4IntegrityError, canonicalize

DIGEST_PREFIX = "sha256:"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

V5_HASH_RULE = "jcs-rfc8785-v1+sha256(excluding:/record_envelope/record_digest)"


def _record_without_self_digest(record: Mapping[str, Any]) -> dict[str, Any]:
    if "record_envelope" not in record:
        raise V4IntegrityError("v5.record_envelope_missing")
    envelope = record["record_envelope"]
    if not isinstance(envelope, Mapping) or "record_digest" not in envelope:
        raise V4IntegrityError("v5.record_envelope_digest_invalid")
    body = copy.deepcopy(dict(record))
    body["record_envelope"] = dict(body["record_envelope"])
    del body["record_envelope"]["record_digest"]
    return body


def v5_record_digest(record: Mapping[str, Any]) -> str:
    """Return the exact V5 record digest excluding /record_envelope/record_digest."""

    canonical = canonicalize(_record_without_self_digest(record))
    return DIGEST_PREFIX + hashlib.sha256(canonical).hexdigest()


def assert_v5_record_digest(record: Mapping[str, Any]) -> str:
    """Verify an envelope payload's stored digest; return it on success."""

    envelope = record.get("record_envelope")
    if not isinstance(envelope, Mapping):
        raise V4IntegrityError("v5.record_envelope_missing")
    stored = envelope.get("record_digest")
    if not isinstance(stored, str) or not _DIGEST_RE.fullmatch(stored):
        raise V4IntegrityError("v5.record_digest_invalid")
    expected = v5_record_digest(record)
    if not hmac.compare_digest(stored, expected):
        raise V4IntegrityError("v5.record_digest_mismatch")
    if envelope.get("hash_rule") != V5_HASH_RULE:
        raise V4IntegrityError("v5.record_hash_rule_mismatch")
    if envelope.get("immutable") is not True:
        raise V4IntegrityError("v5.record_not_immutable")
    return stored


def v5_subject_identity_key(
    *, subject_kind: str, subject_id: str, subject_revision: int | None
) -> str:
    """Normalize nullable subject revision for portable uniqueness."""

    if not subject_kind or ":" in subject_kind or not subject_id or ":" in subject_id:
        raise V4IntegrityError("v5.authority_subject_identity_invalid")
    if subject_revision is not None and subject_revision < 1:
        raise V4IntegrityError("v5.authority_subject_revision_invalid")
    revision = "singleton" if subject_revision is None else str(subject_revision)
    return f"{subject_kind}:{subject_id}:{revision}"


__all__ = [
    "DIGEST_PREFIX",
    "V5_HASH_RULE",
    "assert_v5_record_digest",
    "v5_record_digest",
    "v5_subject_identity_key",
]
