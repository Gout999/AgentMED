"""Deterministic MCP role projections; SOUL text is never an ACL boundary."""
from __future__ import annotations

import pytest

from servers import case_admin, casebase_knowledge, eval_runner, notification, release_admin


def _names(projected) -> set[str]:
    return {tool.name for tool in projected._tool_manager.list_tools()}


@pytest.mark.parametrize(
    ("builder", "profile", "expected"),
    [
        (
            release_admin._profiled_mcp,
            "repairer",
            {
                "versionset.list",
                "versionset.get",
                "candidate.create",
                "workorder.draft",
                "workorder.freeze",
                "workorder.get",
                "release.get",
            },
        ),
        (
            release_admin._profiled_mcp,
            "gatekeeper",
            {
                "workorder.get",
                "gate.submit",
                "approval.request",
                "approval.status",
                "release.get",
            },
        ),
        (
            eval_runner._profiled_mcp,
            "gatekeeper",
            {"gate.run", "gate.run_verification", "gate.report"},
        ),
        (
            eval_runner._profiled_mcp,
            "attributionist",
            {
                "versionset.list",
                "versionset.get",
                "experiment.plan",
                "experiment.run",
                "experiment.execute",
                "experiment.report",
                "probe.freeze",
            },
        ),
        (
            case_admin._profiled_mcp,
            "quality-officer",
            {
                "case.list",
                "case.get",
                "case.timeline",
                "case.claim",
                "case.submit_suggestion",
                "case.escalate",
            },
        ),
        (
            case_admin._profiled_mcp,
            "collector",
            {"case.get", "app.logs", "app.feedback"},
        ),
        (
            case_admin._profiled_mcp,
            "case-officer",
            {"case.get"},
        ),
        (
            case_admin._profiled_mcp,
            "attributionist",
            {"case.get", "case.claim", "app.logs"},
        ),
        (
            case_admin._profiled_mcp,
            "repairer",
            {"case.get", "case.timeline", "case.claim"},
        ),
        (
            notification._profiled_mcp,
            "quality-officer",
            {"matrix.log"},
        ),
        (
            notification._profiled_mcp,
            "case-officer",
            {"feishu.reply_origin", "feishu.weekly_report", "matrix.log"},
        ),
        (
            casebase_knowledge._profiled_mcp,
            "case-officer",
            {"kb.search", "kb.get", "kb.upsert", "kb.badcase_search", "kb.holdout_get"},
        ),
    ],
)
def test_role_projection_exposes_exact_allowlist(builder, profile, expected):
    assert _names(builder(profile)) == expected


def test_cross_role_high_impact_tools_are_physically_absent():
    assert "approval.request" not in _names(release_admin._profiled_mcp("repairer"))
    assert "candidate.create" not in _names(release_admin._profiled_mcp("gatekeeper"))
    assert "gate.run" not in _names(eval_runner._profiled_mcp("attributionist"))
    assert "experiment.run" not in _names(eval_runner._profiled_mcp("gatekeeper"))
    assert "case.claim" not in _names(case_admin._profiled_mcp("collector"))
    assert "feishu.reply_origin" not in _names(notification._profiled_mcp("quality-officer"))


@pytest.mark.parametrize(
    "module",
    [case_admin, release_admin, eval_runner, notification, casebase_knowledge],
)
def test_module_union_registry_cannot_be_served_as_fastmcp(module):
    assert not hasattr(module.mcp, "streamable_http_app")
    assert not hasattr(module.mcp, "run")


@pytest.mark.parametrize(
    "builder",
    [
        release_admin._profiled_mcp,
        eval_runner._profiled_mcp,
        case_admin._profiled_mcp,
        notification._profiled_mcp,
        casebase_knowledge._profiled_mcp,
    ],
)
def test_missing_or_unknown_profile_fails_startup(builder):
    with pytest.raises(RuntimeError, match="MCP_TOOL_PROFILE"):
        builder("")
    with pytest.raises(RuntimeError, match="MCP_TOOL_PROFILE"):
        builder("quality-officer" if builder is release_admin._profiled_mcp else "unknown")
