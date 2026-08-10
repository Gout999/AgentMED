"""ID 生成：evt_ / case_ / obx_ / aud_ / lease_ 等。"""
from __future__ import annotations

import secrets
import string

import ulid


def _ulid() -> str:
    return str(ulid.new())


def new_event_id() -> str:
    return f"evt_{_ulid()}"


def new_case_id() -> str:
    return f"case_{_ulid()}"


def new_outbox_id() -> str:
    return f"obx_{_ulid()}"


def new_outbox_receipt_id() -> str:
    return f"odr_{_ulid()}"


def new_audit_id() -> str:
    return f"aud_{_ulid()}"


def new_lease_id() -> str:
    return f"lease_{_ulid()}"


def new_release_id() -> str:
    return f"rel_{_ulid()}"


def new_experiment_id() -> str:
    return f"exp_{_ulid()}"


def new_notification_id() -> str:
    return f"notif_{_ulid()}"


def new_changeset_id() -> str:
    return f"cs_{_ulid()}"


def new_operation_id() -> str:
    return f"cop_{_ulid()}"


def new_trace_id() -> str:
    return f"tr_{_ulid()}"


def new_trust_entry_id() -> str:
    return f"tle_{_ulid()}"


def new_workspace_id() -> str:
    return f"ws_{_ulid()}"


def new_source_connection_id() -> str:
    return f"src_{_ulid()}"


def new_signal_id() -> str:
    return f"sig_{_ulid()}"


def new_signal_content_id() -> str:
    return f"sigc_{_ulid()}"


def new_signal_case_link_id() -> str:
    return f"scl_{_ulid()}"


def new_agent_run_ref_id() -> str:
    return f"arr_{_ulid()}"


def new_trace_evidence_receipt_id() -> str:
    return f"ter_{_ulid()}"


def new_principal_id() -> str:
    return f"prn_{_ulid()}"


def new_credential_id() -> str:
    return f"cred_{_ulid()}"


def new_idempotency_record_id() -> str:
    return f"idem_{_ulid()}"


def new_idempotency_receipt_id() -> str:
    return f"idemr_{_ulid()}"


def new_controller_registration_id() -> str:
    return f"creg_{_ulid()}"


def new_authority_receipt_id() -> str:
    return f"arec_{_ulid()}"


def new_transaction_id() -> str:
    return f"txn_{_ulid()}"


def new_request_id() -> str:
    return f"req_{_ulid()}"


def new_v4_operation_id() -> str:
    """v4 public operation ID; keep legacy ``new_operation_id``'s ``cop_`` stable."""

    return f"op_{_ulid()}"


def short_token(n: int = 16) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))
