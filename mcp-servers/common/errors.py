"""统一错误码（spec §9.2 公共约定）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class McpError(Exception):
    """MCP 工具错误：统一 error_code + retryable + audit_ref（spec §9.2）。

    __str__ 输出 JSON envelope，FastMCP 以 isError=true 的 text 回传，客户端可解析。
    """

    error_code: str
    message: str
    retryable: bool = False
    audit_ref: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.to_envelope_str()

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.audit_ref:
            body["audit_ref"] = self.audit_ref
        body.update(self.extra)
        return body

    def to_envelope_str(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ---- 公共错误码（spec §9.2） ----
VALIDATION_FAILED = "VALIDATION_FAILED"
FORBIDDEN = "FORBIDDEN"
STATE_CONFLICT = "STATE_CONFLICT"
LEASE_LOST = "LEASE_LOST"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
APPROVAL_MISMATCH = "APPROVAL_MISMATCH"
APPROVAL_REPLAYED = "APPROVAL_REPLAYED"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
GATE_FAILED = "GATE_FAILED"
NOT_FOUND = "NOT_FOUND"
RATE_LIMITED = "RATE_LIMITED"
DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
INTERNAL_ERROR = "INTERNAL_ERROR"


def validation(message: str, **extra: Any) -> McpError:
    return McpError(VALIDATION_FAILED, message, extra=extra)


def forbidden(message: str, **extra: Any) -> McpError:
    return McpError(FORBIDDEN, message, extra=extra)


def state_conflict(message: str, **extra: Any) -> McpError:
    return McpError(STATE_CONFLICT, message, extra=extra)


def lease_lost(message: str, **extra: Any) -> McpError:
    return McpError(LEASE_LOST, message, extra=extra)


def not_found(message: str, **extra: Any) -> McpError:
    return McpError(NOT_FOUND, message, extra=extra)


def dependency_unavailable(message: str, **extra: Any) -> McpError:
    return McpError(DEPENDENCY_UNAVAILABLE, message, retryable=True, extra=extra)
