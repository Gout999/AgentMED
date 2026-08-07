"""ID 生成：evt_ / case_ / obx_ / aud_ / lease_ 等。"""
from __future__ import annotations

import secrets
import string

from ulid import ULID


def _ulid() -> str:
    return str(ULID())


def new_event_id() -> str:
    return f"evt_{_ulid()}"


def new_case_id() -> str:
    return f"case_{_ulid()}"


def new_outbox_id() -> str:
    return f"obx_{_ulid()}"


def new_audit_id() -> str:
    return f"aud_{_ulid()}"


def new_lease_id() -> str:
    return f"lease_{_ulid()}"


def new_release_id() -> str:
    return f"rel_{_ulid()}"


def new_operation_id() -> str:
    return f"cop_{_ulid()}"


def new_trace_id() -> str:
    return f"tr_{_ulid()}"


def short_token(n: int = 16) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))
