#!/usr/bin/env python3
"""Behavioral tests for the deterministic S0 tools."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile

if os.getenv("AGENT_STATION_TEST_INSTALLED") == "1":
    from copaw_worker.hooks.tools.s0_deterministic import s0_deterministic
else:
    from s0_deterministic import s0_deterministic
from copaw_worker.task import FileSystemTaskStore


def response_json(response) -> dict:
    block = response.content[0]
    text = block.get("text") if isinstance(block, dict) else block.text
    return json.loads(text)


async def run() -> None:
    with tempfile.TemporaryDirectory(prefix="agent-station-s0tools-") as temp_dir:
        working_dir = Path(temp_dir) / ".copaw"
        workspace = working_dir / "workspaces" / "default"
        workspace.mkdir(parents=True)
        os.environ["COPAW_WORKING_DIR"] = str(working_dir)
        store = FileSystemTaskStore(workspace)

        # write_answer i001 -> 41
        written = response_json(
            await s0_deterministic(
                action="write_answer",
                payload={"taskId": "s0-revision-loop-005-i001-patch"},
            ),
        )
        assert written["ok"] is True
        assert written["value"] == "41"
        answer = (store.shared_dir / "tasks" / "s0-revision-loop-005-i001-patch" / "workspace" / "answer.txt").read_text("utf-8")
        assert answer == "41\n"

        # verify_answer i001 -> REVISION_NEEDED
        judged = response_json(
            await s0_deterministic(
                action="verify_answer",
                payload={"taskId": "s0-revision-loop-005-i001-patch"},
            ),
        )
        assert judged["ok"] is True
        assert judged["verdict"] == "REVISION_NEEDED"
        assert judged["observed"] == "41"

        # write_answer i002 -> 42
        written2 = response_json(
            await s0_deterministic(
                action="write_answer",
                payload={"taskId": "s0-revision-loop-005-i002-patch"},
            ),
        )
        assert written2["value"] == "42"

        # verify_answer i002 -> SUCCESS
        judged2 = response_json(
            await s0_deterministic(
                action="verify_answer",
                payload={"taskId": "s0-revision-loop-005-i002-patch"},
            ),
        )
        assert judged2["verdict"] == "SUCCESS"

        # unsupported iteration -> fail-closed
        bad = response_json(
            await s0_deterministic(
                action="write_answer",
                payload={"taskId": "s0-revision-loop-005-i999-patch"},
            ),
        )
        assert bad["ok"] is False

        # unknown action -> fail-closed
        unknown = response_json(
            await s0_deterministic(action="frobnicate", payload={}),
        )
        assert unknown["ok"] is False


if __name__ == "__main__":
    asyncio.run(run())
    print("s0 deterministic tools tests passed")
