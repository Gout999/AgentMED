"""ID 生成（与 contracts 前缀惯例一致）。"""
from __future__ import annotations

import secrets
import string

import ulid


def _ulid() -> str:
    return str(ulid.new())


def new_audit_id() -> str:
    return f"aud_{_ulid()}"


def new_trace_id() -> str:
    return f"tr_{_ulid()}"


def new_message_id() -> str:
    return f"msg_{_ulid()}"


def new_msg_ref() -> str:
    return f"fm_{_ulid()}"


def new_doc_id() -> str:
    return f"kb_{_ulid()}"


def new_eval_id() -> str:
    return f"eval_{_ulid()}"


def new_entry_id() -> str:
    return f"tle_{_ulid()}"


def new_approval_id() -> str:
    return f"appr_{_ulid()}"


def new_suggestion_id() -> str:
    return f"sug_{_ulid()}"


def new_workorder_id() -> str:
    return f"wo_{_ulid()}"


def new_nonce() -> str:
    return str(ulid.new())


def short_token(n: int = 16) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))
