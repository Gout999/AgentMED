"""agentmed approval CLI — 审批通道的 CLI 封装（D-015：通道=team 原生 Matrix，CLI/MCP 只是皮）。

list   列出待批审批请求（读控制面 mcp_approval_requests）。
decide <approval_id> --approve|--reject [--reason ...]
       发送结构化 Matrix 决策消息（APPROVAL_DECISION ...）到团队房间；
       系统侧只认 Matrix 事件 + nonce 验证（第 5 步 reader 消费此格式）。
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parent.parent.parent

DB_URL = os.environ.get("AGENTMED_DB_URL", "")
MATRIX_BASE = os.environ.get("AGENTMED_MATRIX_BASE", "http://127.0.0.1:18080")
# 团队房间 id 随团队重建而变化：不设默认，强制显式传入，避免向历史房间发送决策。
# 当前房间：!NzWy15gwm3QU6cTfuP:matrix-local.agentteams.io:18080（以 agt get teams 为准）
TEAM_ROOM = os.environ.get("AGENTMED_TEAM_ROOM", "")


def _db_url() -> str:
    if DB_URL:
        return DB_URL
    env = {}
    for line in (ROOT / "deploy" / ".env").read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v.strip()
    return (
        "postgresql+psycopg://" + env["POSTGRES_USER"] + ":"
        + env["POSTGRES_PASSWORD"] + "@127.0.0.1:5433/control_plane"
    )


def _matrix_token() -> str:
    path = Path(os.environ.get("AGENTMED_MATRIX_TOKEN_FILE", "/tmp/admin-token"))
    if path.exists():
        return path.read_text().strip()
    raise SystemExit("matrix token file missing: " + str(path))


def _as_token() -> str:
    path = Path(os.environ.get("AGENTMED_MATRIX_AS_TOKEN_FILE", "/tmp/agentmed-as-token"))
    if path.exists():
        return path.read_text().strip()
    return ""


def _approver_user() -> str:
    return os.environ.get(
        "AGENTMED_APPROVER_MATRIX_ID",
        "@agentmed-approver:matrix-local.agentteams.io:18080",
    )


def list_requests() -> int:
    eng = sa.create_engine(_db_url())
    with eng.connect() as c:
        rows = c.execute(sa.text(
            "SELECT approval_id, workorder_id, nonce, status, created_at "
            "FROM mcp_approval_requests WHERE status='pending' ORDER BY created_at"
        )).fetchall()
    if not rows:
        print("no pending approval requests")
        return 0
    for r in rows:
        print(r[0], "|", r[1], "| nonce=" + r[2], "|", r[3])
    return 0


def decide(approval_id: str, approve: bool, reason: str) -> int:
    eng = sa.create_engine(_db_url())
    with eng.connect() as c:
        row = c.execute(sa.text(
            "SELECT workorder_id, workorder_hash, nonce, status "
            "FROM mcp_approval_requests WHERE approval_id = :aid"
        ), {"aid": approval_id}).fetchone()
    if row is None:
        print("approval not found: " + approval_id)
        return 2
    workorder_id, workorder_hash, nonce, status = row
    if status != "pending":
        print(f"approval {approval_id} is {status}, not pending")
        return 2
    if not TEAM_ROOM:
        raise SystemExit("缺少 AGENTMED_TEAM_ROOM：团队房间随重建变化，必须显式传入（当前房间以 agt get teams 为准）")
    decision = "approved" if approve else "rejected"
    body = (
        "APPROVAL_DECISION approval=" + approval_id
        + " workorder=" + workorder_id
        + " nonce=" + nonce
        + " decision=" + decision
    )
    if reason:
        body += " reason=" + reason
    room_enc = urllib.parse.quote(TEAM_ROOM, safe="")
    as_token = _as_token()
    if as_token:
        # 以 Human CR agentmed-approver 身份发（appservice 模拟）——D-015 语义：审批人回复。
        url = (
            MATRIX_BASE + "/_matrix/client/r0/rooms/" + room_enc
            + "/send/m.room.message/" + approval_id + "-decision"
            + "?user_id=" + urllib.parse.quote(_approver_user(), safe="")
        )
        auth = "Bearer " + as_token
    else:
        url = (
            MATRIX_BASE + "/_matrix/client/r0/rooms/" + room_enc
            + "/send/m.room.message/" + approval_id + "-decision"
        )
        auth = "Bearer " + _matrix_token()
    req = urllib.request.Request(
        url,
        data=json.dumps({"msgtype": "m.text", "body": body}).encode(),
        headers={"Authorization": auth, "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        print("matrix send failed:", exc.code, exc.read().decode()[:200])
        return 3
    print(f"decision sent: {decision} approval={approval_id}")
    print("matrix event:", result.get("event_id"))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="agentmed-approval")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list pending approval requests")
    dec = sub.add_parser("decide", help="send a structured Matrix decision")
    dec.add_argument("approval_id")
    group = dec.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")
    dec.add_argument("--reason", default="")
    args = parser.parse_args()
    if args.cmd == "list":
        return list_requests()
    return decide(args.approval_id, args.approve, args.reason)


if __name__ == "__main__":
    raise SystemExit(main())
