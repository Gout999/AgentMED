"""Case-admin worker writes are authorized by the control-plane lease."""
from __future__ import annotations

import pytest

from servers import case_admin


class _LeaseCP:
    def __init__(self, *, active: bool = True):
        self.active = active
        self.posts = []

    def post(self, path, json_body=None, **_kwargs):
        body = json_body or {}
        self.posts.append((path, body))
        if not self.active:
            raise case_admin.McpError("LEASE_LOST", "stale fencing token")
        return {
            "accepted": True,
            "suggestion_id": "evt_authoritative001",
            "event_type": "case.suggestion_recorded",
            "case_id": path.split("/")[3],
        }


def test_suggestion_is_written_by_single_authoritative_control_plane_call(monkeypatch):
    cp = _LeaseCP()
    monkeypatch.setattr(case_admin, "_cp", lambda: cp)
    monkeypatch.setattr(
        case_admin,
        "_settings",
        lambda: case_admin.Settings(mcp_worker_id="quality-officer"),
    )

    result = case_admin.case_submit_suggestion(
        "case_fencing001",
        9,
        "suggestion-fencing-001",
        "fix",
        {"candidate": "prompt-v2"},
        worker_id="quality-officer",
    )

    assert result["accepted"] is True
    assert result["suggestion_id"] == "evt_authoritative001"
    assert cp.posts == [
        (
            "/v1/cases/case_fencing001/suggestions",
            {
                "worker_id": "quality-officer",
                "fencing_token": 9,
                "idempotency_key": "suggestion-fencing-001",
                "kind": "fix",
                "payload": {"candidate": "prompt-v2"},
                "evidence_refs": [],
            },
        )
    ]


def test_stale_suggestion_token_fails_in_authoritative_call(monkeypatch):
    cp = _LeaseCP(active=False)
    monkeypatch.setattr(case_admin, "_cp", lambda: cp)
    monkeypatch.setattr(
        case_admin,
        "_settings",
        lambda: case_admin.Settings(mcp_worker_id="quality-officer"),
    )

    with pytest.raises(case_admin.McpError) as exc:
        case_admin.case_submit_suggestion(
            "case_fencing002",
            4,
            "suggestion-fencing-002",
            "fix",
            {"candidate": "prompt-v2"},
            worker_id="quality-officer",
        )

    assert exc.value.error_code == "LEASE_LOST"
    assert [path for path, _ in cp.posts] == [
        "/v1/cases/case_fencing002/suggestions"
    ]


def test_suggestion_rejects_caller_selected_worker_without_controller_write(monkeypatch):
    cp = _LeaseCP()
    monkeypatch.setattr(case_admin, "_cp", lambda: cp)
    monkeypatch.setattr(
        case_admin,
        "_settings",
        lambda: case_admin.Settings(mcp_worker_id="quality-officer"),
    )

    with pytest.raises(case_admin.McpError) as exc:
        case_admin.case_submit_suggestion(
            "case_fencing003",
            4,
            "suggestion-fencing-003",
            "fix",
            {"candidate": "prompt-v2"},
            worker_id="repairer",
        )

    assert exc.value.error_code == "FORBIDDEN"
    assert cp.posts == []
