"""Focused read-view evidence projection tests."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.read_views import _missing_trace_evidence_fields


def test_missing_evidence_uses_field_result_status_not_requested_fields() -> None:
    receipt = SimpleNamespace(
        requested_fields=["trace.input", "trace.output", "observations.tools"],
        field_results=[
            {"name": "trace.input", "status": "OBSERVED"},
            {
                "name": "trace.output",
                "status": "MISSING",
                "reason_digest": "sha256:" + "a" * 64,
            },
            {"name": "observations.tools", "status": "OBSERVED"},
        ],
    )

    assert _missing_trace_evidence_fields([receipt]) == ["trace.output"]


def test_missing_evidence_fails_closed_for_absent_or_malformed_results() -> None:
    receipt = SimpleNamespace(
        requested_fields=["trace.input", "trace.output"],
        field_results=[{"name": "trace.input", "status": "UNKNOWN"}],
    )

    assert _missing_trace_evidence_fields([receipt]) == [
        "trace.input",
        "trace.output",
    ]
