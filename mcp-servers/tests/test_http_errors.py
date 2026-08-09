"""FastAPI error envelopes remain machine-visible at the MCP boundary."""
from __future__ import annotations

import json

import pytest

from common.http import HttpClient


@pytest.mark.parametrize(
    ("status", "detail", "expected_code", "retryable"),
    [
        (403, {"code": "forbidden", "message": "wrong role"}, "FORBIDDEN", False),
        (409, {"code": "lease_lost", "message": "stale lease"}, "LEASE_LOST", False),
        (
            503,
            {"code": "auth_misconfigured", "message": "duplicate token"},
            "DEPENDENCY_UNAVAILABLE",
            True,
        ),
    ],
)
def test_fastapi_detail_envelope_maps_to_mcp_error(status, detail, expected_code, retryable):
    error = HttpClient._map_error(status, json.dumps({"detail": detail}))
    assert error.error_code == expected_code
    assert error.message == detail["message"]
    assert error.retryable is retryable
