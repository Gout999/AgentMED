#!/usr/bin/env python3
"""Behavioral tests for the Agent Station CoPaw acceptance overlay."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile

if os.getenv("AGENT_STATION_TEST_INSTALLED") == "1":
    from copaw_worker.hooks.tools.projectflow import projectflow
else:
    from agent_station_projectflow import projectflow
from copaw_worker.task import (
    FileSystemTaskStore,
    TaskResult,
    ack_task,
    create_project,
    delegate_task,
    parse_loop_plan,
    plan_loop,
    ready_loop_nodes,
    submit_task,
)


def response_json(response) -> dict:
    block = response.content[0]
    text = block.get("text") if isinstance(block, dict) else block.text
    return json.loads(text)


def seed_submitted_task(
    store: FileSystemTaskStore,
    *,
    project_id: str,
    task_id: str,
    result_status: str,
    include_deliverable: bool = True,
) -> None:
    create_project(store, project_id=project_id, title=project_id)
    plan_loop(
        store,
        project_id=project_id,
        goal="Produce a verified result.",
        stop_condition="Verifier accepts.",
        iteration_template="Patch then verify.",
        max_iterations=2,
        current_iteration=1,
        tasks=[
            {
                "taskId": task_id,
                "title": "Patch candidate",
                "assignedTo": "s0-patch",
                "dependsOn": [],
            },
            {
                "taskId": f"{project_id}-verify",
                "title": "Verify candidate",
                "assignedTo": "s0-verify",
                "dependsOn": [task_id],
            },
        ],
    )
    delegate_task(
        store,
        project_id=project_id,
        task_id=task_id,
        room_id="room:!s0-team:matrix.test",
        spec="Produce a deterministic candidate.",
    )
    ack_task(store, task_id=task_id, actor="s0-patch")
    deliverables: list[str] = []
    if include_deliverable:
        deliverable = store.shared_dir / "tasks" / task_id / "workspace" / "answer.txt"
        deliverable.parent.mkdir(parents=True, exist_ok=True)
        deliverable.write_text("42\n", encoding="utf-8")
        deliverables.append(f"shared/tasks/{task_id}/workspace/answer.txt")
    submit_task(
        store,
        task_id=task_id,
        result=TaskResult(
            status=result_status,
            summary=f"Submitted with {result_status}.",
            deliverables=deliverables,
        ),
        actor="s0-patch",
    )


async def run() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-station-acceptance-") as temp_dir:
        working_dir = Path(temp_dir) / ".copaw"
        workspace = working_dir / "workspaces" / "default"
        workspace.mkdir(parents=True)
        os.environ["COPAW_WORKING_DIR"] = str(working_dir)
        runtime_dir = Path(temp_dir) / "runtime"
        runtime_dir.mkdir()
        (runtime_dir / "runtime.yaml").write_text(
            "member:\n"
            "  matrixUserId: '@s0-triage:matrix.test'\n"
            "  role: team_leader\n",
            encoding="utf-8",
        )
        store = FileSystemTaskStore(workspace)

        seed_submitted_task(
            store,
            project_id="accept-success",
            task_id="accept-success-patch",
            result_status="SUCCESS",
        )
        accepted = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-success",
                    "taskId": "accept-success-patch",
                    "resultStatus": "SUCCESS",
                    "accepted": True,
                    "summary": "Independent gate accepted the candidate.",
                    "runId": "s0-test-run",
                },
            ),
        )
        assert accepted["ok"] is True
        assert accepted["nodeStatus"] == "completed"
        assert accepted["accepted"] is True
        receipt_path = (
            store.shared_dir
            / "projects"
            / "accept-success"
            / "acceptances"
            / "accept-success-patch.json"
        )
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt["state"] == "committed"
        assert receipt["actor"] == {
            "matrixUserId": "@s0-triage:matrix.test",
            "role": "team_leader",
        }
        assert receipt["reason"] == "Independent gate accepted the candidate."
        assert receipt["runId"] == "s0-test-run"
        assert len(receipt["resultSha256"]) == 64
        assert receipt["deliverables"][0]["path"].endswith("/answer.txt")
        assert len(receipt["deliverables"][0]["sha256"]) == 64
        assert [task.task_id for task in ready_loop_nodes(store, project_id="accept-success")] == [
            "accept-success-verify",
        ]

        duplicate = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-success",
                    "taskId": "accept-success-patch",
                    "resultStatus": "SUCCESS",
                    "accepted": True,
                    "summary": "Attempt duplicate acceptance.",
                    "runId": "s0-test-run",
                },
            ),
        )
        assert duplicate["ok"] is False

        seed_submitted_task(
            store,
            project_id="accept-revision",
            task_id="accept-revision-patch",
            result_status="REVISION_NEEDED",
        )
        revised = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-revision",
                    "taskId": "accept-revision-patch",
                    "resultStatus": "REVISION_NEEDED",
                    "accepted": False,
                    "summary": "Verifier rejected the candidate.",
                    "runId": "s0-test-run",
                },
            ),
        )
        assert revised["ok"] is True
        assert revised["nodeStatus"] == "revision"
        assert revised["accepted"] is False
        loop = parse_loop_plan(store.read_project_plan("accept-revision"))
        assert loop is not None
        assert {task.task_id: task.status for task in loop.tasks}[
            "accept-revision-patch"
        ] == "revision"

        mismatch = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-success",
                    "taskId": "accept-success-patch",
                    "resultStatus": "REVISION_NEEDED",
                    "accepted": False,
                    "summary": "Payload status mismatch.",
                    "runId": "s0-test-run",
                },
            ),
        )
        assert mismatch["ok"] is False
        assert "does not match" in mismatch["error"]

        seed_submitted_task(
            store,
            project_id="accept-empty",
            task_id="accept-empty-patch",
            result_status="SUCCESS",
            include_deliverable=False,
        )
        empty_deliverable = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-empty",
                    "taskId": "accept-empty-patch",
                    "resultStatus": "SUCCESS",
                    "accepted": True,
                    "summary": "Should not accept an empty result.",
                    "runId": "s0-test-run",
                },
            ),
        )
        assert empty_deliverable["ok"] is False
        assert "deliverable" in empty_deliverable["error"].lower()

        seed_submitted_task(
            store,
            project_id="accept-missing",
            task_id="accept-missing-patch",
            result_status="SUCCESS",
        )
        missing_path = (
            store.shared_dir
            / "tasks"
            / "accept-missing-patch"
            / "workspace"
            / "answer.txt"
        )
        missing_path.unlink()
        missing_deliverable = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-missing",
                    "taskId": "accept-missing-patch",
                    "resultStatus": "SUCCESS",
                    "accepted": True,
                    "summary": "Should not accept a missing file.",
                    "runId": "s0-test-run",
                },
            ),
        )
        assert missing_deliverable["ok"] is False
        assert "does not exist" in missing_deliverable["error"]

        seed_submitted_task(
            store,
            project_id="accept-symlink",
            task_id="accept-symlink-patch",
            result_status="SUCCESS",
        )
        symlink_path = (
            store.shared_dir
            / "tasks"
            / "accept-symlink-patch"
            / "workspace"
            / "answer.txt"
        )
        symlink_path.unlink()
        symlink_path.symlink_to("/etc/hosts")
        symlink_deliverable = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-symlink",
                    "taskId": "accept-symlink-patch",
                    "resultStatus": "SUCCESS",
                    "accepted": True,
                    "summary": "Should reject a symbolic-link deliverable.",
                    "runId": "s0-test-run",
                },
            ),
        )
        assert symlink_deliverable["ok"] is False
        assert "symbolic link" in symlink_deliverable["error"]

        seed_submitted_task(
            store,
            project_id="accept-dry-run",
            task_id="accept-dry-run-patch",
            result_status="SUCCESS",
        )
        dry_run = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-dry-run",
                    "taskId": "accept-dry-run-patch",
                    "resultStatus": "SUCCESS",
                    "accepted": True,
                    "summary": "Preview the guarded state transition.",
                    "runId": "s0-test-run",
                },
                dryRun=True,
            ),
        )
        assert dry_run["ok"] is True
        assert dry_run["dryRun"] is True
        dry_loop = parse_loop_plan(store.read_project_plan("accept-dry-run"))
        assert dry_loop is not None
        assert {task.task_id: task.status for task in dry_loop.tasks}[
            "accept-dry-run-patch"
        ] == "delegated"
        assert not (
            store.shared_dir
            / "projects"
            / "accept-dry-run"
            / "acceptances"
            / "accept-dry-run-patch.json"
        ).exists()

        seed_submitted_task(
            store,
            project_id="accept-wrong-role",
            task_id="accept-wrong-role-patch",
            result_status="SUCCESS",
        )
        (runtime_dir / "runtime.yaml").write_text(
            "member:\n"
            "  matrixUserId: '@s0-patch:matrix.test'\n"
            "  role: worker\n",
            encoding="utf-8",
        )
        wrong_role = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-wrong-role",
                    "taskId": "accept-wrong-role-patch",
                    "resultStatus": "SUCCESS",
                    "accepted": True,
                    "summary": "A Worker must not accept its own result.",
                    "runId": "s0-test-run",
                },
            ),
        )
        assert wrong_role["ok"] is False
        assert "team_leader" in wrong_role["error"]

        no_reason = response_json(
            await projectflow(
                action="accept_task_result",
                payload={
                    "projectId": "accept-revision",
                    "taskId": "accept-revision-patch",
                    "resultStatus": "REVISION_NEEDED",
                    "accepted": False,
                    "summary": "",
                    "runId": "s0-test-run",
                },
            ),
        )
        assert no_reason["ok"] is False
        assert "summary" in no_reason["error"]


if __name__ == "__main__":
    asyncio.run(run())
    print("acceptance overlay tests passed")
