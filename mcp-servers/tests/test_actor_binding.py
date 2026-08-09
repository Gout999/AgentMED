"""Caller-supplied identities cannot change authoritative MCP audit actors."""
from __future__ import annotations

import pytest

from common.errors import McpError
from servers import casebase_knowledge, eval_runner, notification, release_admin


@pytest.mark.parametrize("worker_id", ["quality-officer", "case-officer"])
def test_matrix_log_uses_projection_worker_as_audit_actor(monkeypatch, worker_id):
    captured = {}
    monkeypatch.setattr(
        notification,
        "_settings",
        lambda: notification.Settings(mcp_worker_id=worker_id),
    )

    def send(**kwargs):
        captured.update(kwargs)
        return {"message_id": "msg_actor_binding"}

    monkeypatch.setattr(notification, "_send", send)
    receipt = notification.matrix_log("internal", "bound actor")
    assert receipt["event_id"] == "msg_actor_binding"
    assert captured["actor"] == worker_id


def test_casebase_rejects_self_reported_actor_before_database_write(monkeypatch):
    monkeypatch.setattr(
        casebase_knowledge,
        "_settings",
        lambda: casebase_knowledge.Settings(mcp_worker_id="case-officer"),
    )
    monkeypatch.setattr(
        casebase_knowledge,
        "session_scope",
        lambda *_args, **_kwargs: pytest.fail("database write must not start"),
    )
    with pytest.raises(McpError) as exc:
        casebase_knowledge.kb_upsert("case", "body", actor="repairer")
    assert exc.value.error_code == "FORBIDDEN"


def test_candidate_rejects_self_reported_worker_before_database_or_cp_write(monkeypatch):
    monkeypatch.setattr(
        release_admin,
        "_settings",
        lambda: release_admin.Settings(mcp_worker_id="repairer"),
    )
    monkeypatch.setattr(
        release_admin,
        "session_scope",
        lambda *_args, **_kwargs: pytest.fail("database write must not start"),
    )
    with pytest.raises(McpError) as exc:
        release_admin.candidate_create(
            case_id="case_actor01",
            worker_id="gatekeeper",
            fencing_token=1,
            channel="prompt",
            attribution_report_digest="sha256:" + "1" * 64,
            base_versionset_id="vs_actor01",
            base_versionset_digest="sha256:" + "2" * 64,
            base_revision=1,
            target_prompt_digest="sha256:" + "3" * 64,
            content={},
            idempotency_key="actor-binding-01",
        )
    assert exc.value.error_code == "FORBIDDEN"


def test_experiment_run_rejects_self_reported_runner_before_cp_write(monkeypatch):
    monkeypatch.setattr(
        eval_runner,
        "_settings",
        lambda: eval_runner.Settings(mcp_worker_id="eval-runner"),
    )
    monkeypatch.setattr(
        eval_runner,
        "_cp",
        lambda: pytest.fail("control-plane write must not start"),
    )
    with pytest.raises(McpError) as exc:
        eval_runner.experiment_run(
            "exp_actor01",
            "lease_actor01",
            1,
            runner_id="repairer",
        )
    assert exc.value.error_code == "FORBIDDEN"
