"""ID 生成：vs_ / op_ / req_ / fb_ / tr_（格式对齐 openapi pattern）。"""
from __future__ import annotations

import secrets

_CHARS = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _tok(n: int = 16) -> str:
    return "".join(secrets.choice(_CHARS) for _ in range(n))


def new_versionset_id() -> str:
    return f"vs_{_tok(16)}"


def new_operation_id() -> str:
    return f"op_{_tok(16)}"


def new_request_id() -> str:
    return f"req_{_tok(16)}"


def new_feedback_id() -> str:
    return f"fb_{_tok(16)}"


def new_trace_id() -> str:
    return f"tr_{_tok(16)}"
