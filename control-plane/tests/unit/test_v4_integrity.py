"""Stage 1A immutable-record hashing and identifier contracts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

import pytest

from app.models.v4_tables import PublicCommandIdempotency, PublicPrincipal
from app.utils import ids
from app.utils.v4_integrity import (
    DIGEST_PREFIX,
    V4IntegrityError,
    assert_record_digest,
    authority_subject_identity_key,
    canonical_digest,
    canonicalize_record,
    record_digest,
)


def test_rfc8785_record_digest_preserves_unicode_and_excludes_only_self_field() -> None:
    record = {
        "schema_version": "1.0",
        "signal_id": "sig_01J0000000000001",
        "summary": "维护人员发现输出漂移",
        "nested": {"b": 2, "a": True},
        "signal_digest": "sha256:" + "0" * 64,
    }

    canonical = canonicalize_record(record, self_digest_field="signal_digest")
    digest = record_digest(record, self_digest_field="signal_digest")

    assert "维护人员".encode() in canonical
    assert b"signal_digest" not in canonical
    assert digest.startswith(DIGEST_PREFIX)
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
    assert digest == record_digest(
        {**record, "signal_digest": "sha256:" + "f" * 64},
        self_digest_field="signal_digest",
    )


@pytest.mark.parametrize(
    "record",
    [
        {"schema_version": "1.0", "score": 0.5, "digest": "sha256:" + "0" * 64},
        {"schema_version": "1.0", "nested": [1, {"score": 1.0}], "digest": "sha256:" + "0" * 64},
    ],
)
def test_v4_hashing_rejects_floats_at_any_depth(record: dict[str, object]) -> None:
    with pytest.raises(V4IntegrityError, match=r"v4\.float_forbidden"):
        record_digest(record, self_digest_field="digest")


@pytest.mark.parametrize("field", ["", "nested.digest", "missing_digest"])
def test_v4_hashing_requires_one_existing_top_level_self_field(field: str) -> None:
    record = {"schema_version": "1.0", "digest": "sha256:" + "0" * 64}
    with pytest.raises(V4IntegrityError, match=r"v4\.self_digest_field_invalid"):
        record_digest(record, self_digest_field=field)


def test_assert_record_digest_fails_closed_on_tamper() -> None:
    record = {"schema_version": "1.0", "value": "sealed", "digest": ""}
    record["digest"] = record_digest(record, self_digest_field="digest")
    assert_record_digest(record, self_digest_field="digest")

    record["value"] = "tampered"
    with pytest.raises(V4IntegrityError, match=r"v4\.record_digest_mismatch"):
        assert_record_digest(record, self_digest_field="digest")


def test_canonical_digest_hashes_complete_object_without_fake_self_field() -> None:
    value = {"event_type": "signal.received", "payload": {"summary": "有问题"}}
    assert canonical_digest(value) == canonical_digest(
        {"payload": {"summary": "有问题"}, "event_type": "signal.received"}
    )
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", canonical_digest(value))


@pytest.mark.parametrize(
    "value",
    [
        {"score": 0.1},
        {"unsafe_integer": 2**60},
        {1: "non-string-key"},
    ],
)
def test_canonical_digest_rejects_float_unsafe_integer_and_non_string_key(value: object) -> None:
    with pytest.raises(V4IntegrityError, match="^v4\\."):
        canonical_digest(value)


def test_authority_subject_identity_normalizes_null_revision_without_digest() -> None:
    singleton = authority_subject_identity_key(
        subject_kind="TRACE_EVIDENCE_RECEIPT",
        subject_id="ter_01J0000000000001",
        subject_revision=None,
    )
    revisioned = authority_subject_identity_key(
        subject_kind="QUALITY_CASE",
        subject_id="case_01J0000000000001",
        subject_revision=1,
    )
    assert singleton == "TRACE_EVIDENCE_RECEIPT:ter_01J0000000000001:singleton"
    assert revisioned == "QUALITY_CASE:case_01J0000000000001:1"
    assert "sha256" not in singleton


@pytest.mark.parametrize(
    ("factory", "prefix"),
    [
        (ids.new_workspace_id, "ws_"),
        (ids.new_source_connection_id, "src_"),
        (ids.new_signal_id, "sig_"),
        (ids.new_signal_content_id, "sigc_"),
        (ids.new_signal_case_link_id, "scl_"),
        (ids.new_agent_run_ref_id, "arr_"),
        (ids.new_trace_evidence_receipt_id, "ter_"),
        (ids.new_principal_id, "prn_"),
        (ids.new_credential_id, "cred_"),
        (ids.new_idempotency_record_id, "idem_"),
        (ids.new_idempotency_receipt_id, "idemr_"),
        (ids.new_controller_registration_id, "creg_"),
        (ids.new_authority_receipt_id, "arec_"),
        (ids.new_transaction_id, "txn_"),
        (ids.new_request_id, "req_"),
        (ids.new_v4_operation_id, "op_"),
    ],
)
def test_v4_id_factories_are_prefixed_and_unique(factory, prefix: str) -> None:  # type: ignore[no-untyped-def]
    first = factory()
    second = factory()
    assert first.startswith(prefix)
    assert second.startswith(prefix)
    assert first != second


def test_idempotency_terminal_transition_cannot_rebind_immutable_identity(
    sqlite_session,
) -> None:
    now = datetime.now(timezone.utc)
    principal = PublicPrincipal(
        principal_id="prn_01J0000000000001",
        workspace_id="ws_01J0000000000001",
        principal_type="human",
        state="ACTIVE",
        subject_digest="sha256:" + "a" * 64,
        audiences=["caseloop-public-api"],
        project_ids=[],
        environment_ids=[],
        scopes=["signals:write"],
        claims_digest="sha256:" + "b" * 64,
    )
    record = PublicCommandIdempotency(
        idempotency_record_id="idem_01J0000000000001",
        workspace_id=principal.workspace_id,
        principal_id=principal.principal_id,
        intent="signals.submit",
        idempotency_key="idem-key-00000001",
        request_fingerprint="sha256:" + "c" * 64,
        state="PENDING",
        request_id="req_01J0000000000001",
        expires_at=now + timedelta(days=1),
    )
    sqlite_session.add_all([principal, record])
    sqlite_session.commit()

    record.request_fingerprint = "sha256:" + "d" * 64
    record.state = "COMPLETED"
    with pytest.raises(
        RuntimeError, match="v4.idempotency_identity_update_forbidden"
    ):
        sqlite_session.flush()
