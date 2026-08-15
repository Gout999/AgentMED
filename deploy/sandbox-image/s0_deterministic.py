"""Deterministic S0 tools for the AgentTeams CoPaw Worker.

The S0 revision-loop scenario proves delegation/revision mechanics, not raw
model arithmetic. These tools make the two deterministic steps of the scenario
exact: the Patch worker writes the fixed candidate via `write_answer`, and the
Verifier judges it via `verify_answer`. The LLM only decides to invoke the
tool; the value itself cannot drift.

Registered by the Agent Station overlay in copaw_worker/hooks/__init__.py
alongside projectflow/taskflow/filesync/message.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from copaw_worker.hooks.tools.projectflow_upstream import _error, _ok, _store
except ImportError:  # Source-mounted behavioral test on the base image.
    from copaw_worker.hooks.tools.projectflow import _error, _ok, _store  # type: ignore

from copaw_worker.task import TaskflowError


def _task_root(store, task_id: str) -> Path:
    task_root = (store.shared_dir / "tasks" / task_id).resolve()
    if not str(task_root).startswith(str(store.shared_dir.resolve())):
        raise TaskflowError(f"task id escapes the shared store: {task_id}")
    return task_root


def _answer_for_task(task_id: str) -> str:
    """The frozen scenario values: iteration 1 writes 41, iteration 2 writes 42."""
    if "-i001-" in task_id:
        return "41\n"
    if "-i002-" in task_id:
        return "42\n"
    raise TaskflowError(
        f"task id has no supported iteration marker: {task_id} (expected -i001- or -i002-)",
    )


async def _write_answer(payload: dict[str, Any], dry_run: bool):
    store = _store()
    task_id = str(payload.get("taskId") or "")
    if not task_id:
        raise TaskflowError("payload.taskId is required")
    expected = _answer_for_task(task_id)
    task_root = _task_root(store, task_id)
    workspace = task_root / "workspace"
    if not dry_run:
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "answer.txt").write_text(expected, encoding="utf-8")
    return _ok(
        dryRun=dry_run,
        action="write_answer",
        taskId=task_id,
        value=expected.strip(),
        path=f"shared/tasks/{task_id}/workspace/answer.txt",
    )


async def _verify_answer(payload: dict[str, Any], dry_run: bool):
    store = _store()
    task_id = str(payload.get("taskId") or "")
    if not task_id:
        raise TaskflowError("payload.taskId is required")
    task_root = _task_root(store, task_id)
    artifact = task_root / "workspace" / "answer.txt"
    try:
        observed = artifact.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise TaskflowError(f"answer artifact missing: {artifact}") from exc
    if observed == "42\n":
        verdict = "SUCCESS"
    elif observed == "41\n":
        verdict = "REVISION_NEEDED"
    else:
        verdict = "BLOCKED"
    return _ok(
        dryRun=dry_run,
        action="verify_answer",
        taskId=task_id,
        verdict=verdict,
        observed=observed.strip(),
        path=f"shared/tasks/{task_id}/workspace/answer.txt",
    )


async def s0_deterministic(
    action: str,
    payload: dict[str, Any] | str | None = None,
    dryRun: bool = False,
):
    """Deterministic S0 scenario tools: write_answer | verify_answer."""
    payload_data: dict[str, Any] = {}
    try:
        if isinstance(payload, str):
            import json

            payload_data = json.loads(payload) if payload else {}
        elif isinstance(payload, dict):
            payload_data = payload
        if action == "write_answer":
            return await _write_answer(payload_data, dryRun)
        if action == "verify_answer":
            return await _verify_answer(payload_data, dryRun)
        raise TaskflowError(f"unsupported action: {action}")
    except (TaskflowError, ValueError, OSError) as error:
        return _error(
            str(error),
            action=action,
            taskId=payload_data.get("taskId"),
        )
