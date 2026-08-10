"""RFC 8785 integrity helpers for immutable CaseLoop v4 records.

v3 hashes remain untouched.  A v4 self-hash always excludes exactly one
top-level self-digest field and forbids floats at any depth so all supported
runtimes produce the same bytes.
"""
from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from typing import Any

import rfc8785

DIGEST_PREFIX = "sha256:"
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class V4IntegrityError(ValueError):
    """Stable fail-closed error for malformed or tampered v4 records."""


def _reject_floats(value: Any, *, path: str = "$") -> None:
    if isinstance(value, float):
        raise V4IntegrityError(f"v4.float_forbidden:{path}")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise V4IntegrityError(f"v4.non_string_key:{path}")
            _reject_floats(child, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, child in enumerate(value):
            _reject_floats(child, path=f"{path}[{index}]")


def _body_without_self_digest(
    record: Mapping[str, Any], *, self_digest_field: str
) -> dict[str, Any]:
    if (
        not self_digest_field
        or "." in self_digest_field
        or "/" in self_digest_field
        or self_digest_field not in record
    ):
        raise V4IntegrityError("v4.self_digest_field_invalid")
    return {key: value for key, value in record.items() if key != self_digest_field}


def canonicalize_record(
    record: Mapping[str, Any], *, self_digest_field: str
) -> bytes:
    """Return RFC 8785 bytes after excluding one declared self-digest field."""

    body = _body_without_self_digest(record, self_digest_field=self_digest_field)
    return canonicalize(body)


def canonicalize(value: Any) -> bytes:
    """Return fail-closed RFC 8785 bytes for an arbitrary JSON value."""

    _reject_floats(value)
    try:
        return rfc8785.dumps(value)
    except Exception as exc:  # rfc8785 exposes several domain-specific errors.
        raise V4IntegrityError(f"v4.canonicalization_failed:{type(exc).__name__}") from exc


def canonical_digest(value: Any) -> str:
    """Digest a complete v4 JSON value without inventing a self-digest field."""

    return DIGEST_PREFIX + hashlib.sha256(canonicalize(value)).hexdigest()


def authority_subject_identity_key(
    *, subject_kind: str, subject_id: str, subject_revision: int | None
) -> str:
    """Normalize nullable subject revision for portable uniqueness.

    PostgreSQL and SQLite both allow repeated NULL values in ordinary unique
    constraints.  AuthorityReceipt therefore stores this non-null identity key
    so a singleton subject cannot be rebound to two digests.
    """

    if not subject_kind or ":" in subject_kind or not subject_id or ":" in subject_id:
        raise V4IntegrityError("v4.authority_subject_identity_invalid")
    if subject_revision is not None and subject_revision < 1:
        raise V4IntegrityError("v4.authority_subject_revision_invalid")
    revision = "singleton" if subject_revision is None else str(subject_revision)
    return f"{subject_kind}:{subject_id}:{revision}"


def record_digest(record: Mapping[str, Any], *, self_digest_field: str) -> str:
    canonical = canonicalize_record(record, self_digest_field=self_digest_field)
    return DIGEST_PREFIX + hashlib.sha256(canonical).hexdigest()


def assert_record_digest(
    record: Mapping[str, Any], *, self_digest_field: str
) -> None:
    actual = record.get(self_digest_field)
    if not isinstance(actual, str) or not _DIGEST_RE.fullmatch(actual):
        raise V4IntegrityError("v4.record_digest_invalid")
    expected = record_digest(record, self_digest_field=self_digest_field)
    if not hmac.compare_digest(actual, expected):
        raise V4IntegrityError("v4.record_digest_mismatch")
