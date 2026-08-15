"""Agent Station overlay for the missing CoPaw task-result acceptance action.

All existing actions delegate to the pinned AgentTeams v1.2.0 implementation.
The overlay adds one guarded state transition that validates the durable Worker
result before changing a DAG or Loop node.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

try:
    from copaw_worker.hooks.tools.projectflow_upstream import (
        _coerce_payload,
        _error,
        _ok,
        _required_str,
        _store,
        projectflow as upstream_projectflow,
    )
except ImportError:  # Allows the source-mounted behavioral test on the base image.
    from copaw_worker.hooks.tools.projectflow import (
        _coerce_payload,
        _error,
        _ok,
        _required_str,
        _store,
        projectflow as upstream_projectflow,
    )

from copaw_worker.task import (
    DagTask,
    LoopPlan,
    TaskflowError,
    parse_dag_tasks,
    parse_loop_plan,
    parse_plan_type,
    replace_dag_tasks,
    replace_loop_plan,
)
from copaw_worker.hooks.tools.taskflow import _runtime_config_field


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise TaskflowError(f"payload.{key} must be a boolean")
    return value


def _node_status(result_status: str, accepted: bool) -> str:
    if result_status in {"SUCCESS", "SUCCESS_WITH_NOTES"}:
        return "completed" if accepted else "revision"
    if result_status == "REVISION_NEEDED":
        if accepted:
            raise TaskflowError("REVISION_NEEDED cannot be accepted as completed")
        return "revision"
    if result_status in {"BLOCKED", "INTERRUPTED"}:
        if accepted:
            raise TaskflowError(f"{result_status} cannot be accepted as completed")
        return "blocked"
    raise TaskflowError(f"unsupported result status: {result_status}")


def _required_leader_identity() -> tuple[str, str]:
    role = _runtime_config_field("member", "role")
    actor = _runtime_config_field("member", "matrixUserId")
    if role != "team_leader":
        raise TaskflowError("accept_task_result requires a team_leader runtime")
    if not actor:
        raise TaskflowError("team leader Matrix identity is required")
    return actor, role


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_regular_file(path: Path, *, root: Path, label: str) -> Path:
    if path.is_symlink():
        raise TaskflowError(f"{label} must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise TaskflowError(f"{label} does not exist: {path}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise TaskflowError(f"{label} escapes the task directory: {path}") from exc
    if not resolved.is_file():
        raise TaskflowError(f"{label} is not a regular file: {path}")
    return resolved


def _hash_task_files(store, task_id: str, result) -> tuple[str, list[dict[str, Any]]]:
    task_root = (store.shared_dir / "tasks" / task_id).resolve()
    result_path = _resolve_regular_file(
        task_root / "result.md",
        root=task_root,
        label="task result",
    )
    if result.status in {"SUCCESS", "SUCCESS_WITH_NOTES"} and not result.deliverables:
        raise TaskflowError("successful task result requires at least one deliverable")

    hashed_deliverables: list[dict[str, Any]] = []
    for raw_path in result.deliverables:
        if not isinstance(raw_path, str) or not raw_path.startswith("shared/"):
            raise TaskflowError(
                f"deliverable path must start with shared/: {raw_path}",
            )
        candidate = store.shared_dir / raw_path.removeprefix("shared/")
        resolved = _resolve_regular_file(
            candidate,
            root=task_root,
            label=f"deliverable {raw_path}",
        )
        hashed_deliverables.append(
            {
                "path": raw_path,
                "sha256": _sha256(resolved),
                "size": resolved.stat().st_size,
            },
        )
    return _sha256(result_path), hashed_deliverables


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _replace_status(tasks: list[DagTask], task_id: str, status: str) -> list[DagTask]:
    found = False
    updated: list[DagTask] = []
    for task in tasks:
        if task.task_id == task_id:
            found = True
            if task.status != "delegated":
                raise TaskflowError(
                    f"task {task_id} is not awaiting acceptance ({task.status})",
                )
            updated.append(
                DagTask(
                    task_id=task.task_id,
                    title=task.title,
                    assigned_to=task.assigned_to,
                    depends_on=task.depends_on,
                    status=status,
                ),
            )
        else:
            updated.append(task)
    if not found:
        raise TaskflowError(f"task not found in project graph: {task_id}")
    return updated


async def _accept_task_result(payload: dict[str, Any], dry_run: bool):
    store = _store()
    project_id = _required_str(payload, "projectId")
    task_id = _required_str(payload, "taskId")
    expected_status = _required_str(payload, "resultStatus")
    accepted = _required_bool(payload, "accepted")
    reason = _required_str(payload, "summary")
    run_id = _required_str(payload, "runId")
    matrix_event_id = payload.get("matrixEventId")
    if matrix_event_id is not None:
        if not isinstance(matrix_event_id, str) or not matrix_event_id.startswith("$"):
            raise TaskflowError("payload.matrixEventId must start with $")
        matrix_event_id = matrix_event_id.strip()
    actor, actor_role = _required_leader_identity()

    project = store.read_project_meta(project_id)
    if project.status != "active":
        raise TaskflowError(f"project is not active: {project_id} ({project.status})")

    task = store.read_task_meta(task_id)
    if task.project_id != project_id:
        raise TaskflowError(
            f"task {task_id} belongs to {task.project_id}, not {project_id}",
        )
    if task.status != "submitted":
        raise TaskflowError(f"task {task_id} is not submitted ({task.status})")

    result = store.read_task_result(task_id)
    if result.status != expected_status:
        raise TaskflowError(
            f"payload.resultStatus {expected_status} does not match durable result {result.status}",
        )

    result_sha256, deliverables = _hash_task_files(store, task_id, result)

    status = _node_status(result.status, accepted)
    plan = store.read_project_plan(project_id)
    plan_type = parse_plan_type(plan)
    if plan_type == "loop":
        loop = parse_loop_plan(plan)
        if loop is None:
            raise TaskflowError(f"project has no loop plan: {project_id}")
        updated_tasks = _replace_status(loop.tasks, task_id, status)
        updated_plan = replace_loop_plan(
            plan,
            LoopPlan(
                goal=loop.goal,
                stop_condition=loop.stop_condition,
                iteration_template=loop.iteration_template,
                max_iterations=loop.max_iterations,
                current_iteration=loop.current_iteration,
                status=loop.status,
                tasks=updated_tasks,
                history=loop.history,
            ),
        )
    else:
        updated_tasks = _replace_status(parse_dag_tasks(plan), task_id, status)
        updated_plan = replace_dag_tasks(plan, updated_tasks)

    receipt_path = (
        store.shared_dir
        / "projects"
        / project_id
        / "acceptances"
        / f"{task_id}.json"
    )
    if receipt_path.exists():
        raise TaskflowError(f"acceptance receipt already exists for task {task_id}")
    receipt = {
        "schemaVersion": "agent-station.acceptance.v1",
        "state": "pending",
        "runId": run_id,
        "matrixEventId": matrix_event_id,
        "projectId": project_id,
        "taskId": task_id,
        "actor": {"matrixUserId": actor, "role": actor_role},
        "decidedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "decision": status,
        "accepted": status == "completed",
        "resultStatus": result.status,
        "reason": reason,
        "resultSha256": result_sha256,
        "deliverables": deliverables,
        "previousNodeStatus": "delegated",
        "nodeStatus": status,
    }
    if not dry_run:
        _atomic_write_json(receipt_path, receipt)
        store.write_project_plan(project_id, updated_plan)
        receipt["state"] = "committed"
        _atomic_write_json(receipt_path, receipt)

    return _ok(
        dryRun=dry_run,
        action="accept_task_result",
        projectId=project_id,
        taskId=task_id,
        result=asdict(result),
        nodeStatus=status,
        accepted=status == "completed",
        summary=reason,
        receiptPath=(
            f"shared/projects/{project_id}/acceptances/{task_id}.json"
        ),
        receipt=receipt,
    )


async def projectflow(
    action: str,
    payload: dict[str, Any] | str | None = None,
    dryRun: bool = False,
):
    """Manage AgentTeams projects, including validated Worker result acceptance."""
    if action != "accept_task_result":
        return await upstream_projectflow(action=action, payload=payload, dryRun=dryRun)

    payload_data: dict[str, Any] = {}
    try:
        payload_data = _coerce_payload(payload)
        return await _accept_task_result(payload_data, dryRun)
    except (TaskflowError, ValueError, OSError) as error:
        return _error(
            str(error),
            action=action,
            projectId=payload_data.get("projectId"),
            taskId=payload_data.get("taskId"),
        )
