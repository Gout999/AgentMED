from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitFamily(IntEnum):
    OK = 0
    INPUT = 2
    CONFIG = 3
    AUTH = 10
    NOT_FOUND = 11
    CONFLICT = 12
    TEMPORARY = 20
    REMOTE = 21
    PROTOCOL = 22


class CliError(Exception):
    """A secret-safe, machine-classifiable CLI failure."""

    def __init__(
        self,
        code: str,
        exit_family: ExitFamily,
        *,
        details: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.exit_family = exit_family
        self.details = details or {}
        self.payload = payload

    def as_payload(self) -> dict[str, Any]:
        if self.payload is not None:
            return self.payload
        return {
            "schema_version": "1.0",
            "error": {
                "code": self.code,
                "retryable": self.exit_family == ExitFamily.TEMPORARY,
                "details": self.details,
            },
        }
