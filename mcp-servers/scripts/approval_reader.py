"""审批 reader（段6 确定性件）：读 Matrix 审批事件 → 验 nonce → 控制面核发。

D-015：系统只认 Matrix 事件 + nonce 验证。本 reader：
  1. 以 @agentmed-approver 身份（appservice 模拟）轮询团队房间消息；
  2. 解析 APPROVAL_DECISION approval=.. workorder=.. nonce=.. decision=.. reason=..；
  3. 验 DB 审批请求行（nonce 匹配 + pending）；
  4. 调控制面 /v1/changesets/{cs}/approve|reject（APPROVAL_AUTHORITY_TOKEN）；
  5. 更新请求状态 + 审计，事件 id 记状态文件（幂等）。

用法：
  .venv/bin/python scripts/approval_reader.py --once      # 处理一轮后退出
  .venv/bin/python scripts/approval_reader.py             # 循环轮询（30s）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "var" / "approval-reader-state.json"

DB_URL = os.environ.get("AGENTMED_DB_URL", "")
MATRIX_BASE = os.environ.get("AGENTMED_MATRIX_BASE", "http://127.0.0.1:18080")
TEAM_ROOM = os.environ.get(
    "AGENTMED_TEAM_ROOM", "!NzWy15gwm3QU6cTfuP:matrix-local.agentteams.io:18080"
)
CONTROL_PLANE = os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:18090")

DECISION_RE = re.compile(
    r"APPROVAL_DECISION\s+approval=(\S+)\s+workorder=(\S+)\s+nonce=(\S+)\s+decision=(approved|rejected)(?:\s+reason=(.*))?"
)


def _db_url() -> str:
    if DB_URL:
        return DB_URL
    env = {}
    for line in (ROOT.parent / "deploy" / ".env").read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v.strip()
    return (
        "postgresql+psycopg://" + env["POSTGRES_USER"] + ":"
        + env["POSTGRES_PASSWORD"] + "@127.0.0.1:5433/control_plane"
    )


def _approval_token() -> str:
    env = {}
    for line in (ROOT.parent / "deploy" / ".env").read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v.strip()
    return env["APPROVAL_AUTHORITY_TOKEN"]


def _as_token() -> str:
    path = Path(os.environ.get("AGENTMED_MATRIX_AS_TOKEN_FILE", "/tmp/agentmed-as-token"))
    if path.exists():
        return path.read_text().strip()
    raise SystemExit("AS token file missing")


def fetch_decisions() -> list[tuple[str, dict]]:
    """返回 [(event_id, parsed_fields)]。"""
    room_enc = urllib.parse.quote(TEAM_ROOM, safe="")
    user_enc = urllib.parse.quote("@agentmed-approver:matrix-local.agentteams.io:18080", safe="")
    url = (
        f"{MATRIX_BASE}/_matrix/client/r0/rooms/{room_enc}/messages?dir=b&limit=50"
        f"&user_id={user_enc}"
    )
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + _as_token()})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())
    out = []
    for event in data.get("chunk", []):
        body = (event.get("content") or {}).get("body") or ""
        m = DECISION_RE.match(body)
        if m and event.get("sender", "").startswith("@agentmed-approver"):
            out.append((
                event["event_id"],
                {
                    "approval": m.group(1),
                    "workorder": m.group(2),
                    "nonce": m.group(3),
                    "decision": m.group(4),
                    "reason": (m.group(5) or "").strip(),
                },
            ))
    return out


def process(approval_id: str, workorder_id: str, nonce: str, decision: str, reason: str) -> str:
    eng = sa.create_engine(_db_url())
    with eng.connect() as c:
        row = c.execute(sa.text(
            "SELECT workorder_hash, status FROM mcp_approval_requests WHERE approval_id = :aid"
        ), {"aid": approval_id}).fetchone()
    if row is None:
        return f"approval {approval_id} not found"
    workorder_hash, status = row
    if status != "pending":
        return f"approval {approval_id} already {status}"
    if row is not None and nonce != "":
        nonce_row = None
        with eng.connect() as c:
            nonce_row = c.execute(sa.text(
                "SELECT nonce FROM mcp_approval_requests WHERE approval_id = :aid AND nonce = :n"
            ), {"aid": approval_id, "n": nonce}).fetchone()
        if nonce_row is None:
            return f"nonce mismatch for {approval_id}"
    changeset_id = "cs_" + workorder_id
    action = "approve" if decision == "approved" else "reject"
    decision_value = "approved" if decision == "approved" else "rejected"

    # 1) 登记 ApprovalGrant（决策适配层：Matrix 决策 → 控制面不可变授权记录）。
    #    approve 校验要求 WorkOrder + ApprovalGrant 同 nonce 绑定。
    with eng.connect() as c:
        wo_row = c.execute(sa.text(
            "SELECT payload FROM workorders WHERE workorder_id = :wid"
        ), {"wid": workorder_id}).fetchone()
    if wo_row is None:
        return f"workorder {workorder_id} not registered in control plane"
    wo_payload = wo_row[0] if isinstance(wo_row[0], dict) else json.loads(wo_row[0])
    grant = {
        "schema_version": "0.1.0",
        "approval_id": approval_id,
        "workorder_hash": workorder_hash,
        "workorder_id": workorder_id,
        "nonce": wo_payload.get("nonce"),
        "expiry": wo_payload.get("expiry"),
        "approver": {"type": "human", "identity": "agentmed-approver"},
        "decision": decision_value,
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "nonce_consumed": False,
    }
    grant_req = urllib.request.Request(
        f"{CONTROL_PLANE}/v1/approvals",
        data=json.dumps(grant).encode(),
        headers={"Authorization": "Bearer " + _approval_token(), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(grant_req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:200]
        if "nonce_replay" not in detail:
            return f"grant registration failed: {exc.code} {detail}"

    # approved 路径：grant_approval（release_service）已在同事务内推进 changeset
    # APPROVED + case RELEASING——无需再调 changeset approve（会撞 APPROVED 终态）。
    if action == "approve":
        with eng.begin() as c:
            c.execute(sa.text(
                "UPDATE mcp_approval_requests SET status = 'approved' WHERE approval_id = :aid"
            ), {"aid": approval_id})
        return f"approved ok (grant advanced changeset {changeset_id})"
    body = {"approval_id": approval_id, "approver": "agentmed-approver", "reason": reason or "rejected by approver"}
    req = urllib.request.Request(
        f"{CONTROL_PLANE}/v1/changesets/{changeset_id}/reject",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + _approval_token(), "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = resp.read().decode()[:300]
    except urllib.error.HTTPError as exc:
        return f"control-plane reject failed: {exc.code} {exc.read().decode()[:200]}"
    with eng.begin() as c:
        c.execute(sa.text(
            "UPDATE mcp_approval_requests SET status = :st, evidence_summary = evidence_summary || :reason "
            "WHERE approval_id = :aid"
        ), {"st": decision, "reason": " | reader: " + reason[:120], "aid": approval_id})
    return f"{action} ok: {result}"


def _load_state() -> set[str]:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if STATE.exists():
        return set(json.loads(STATE.read_text()))
    return set()


def run_once() -> int:
    processed = _load_state()
    handled = 0
    for event_id, fields in fetch_decisions():
        if event_id in processed:
            continue
        result = process(
            fields["approval"], fields["workorder"], fields["nonce"],
            fields["decision"], fields["reason"],
        )
        print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {event_id[:12]} {fields['decision']} {fields['approval']}: {result}")
        processed.add(event_id)
        handled += 1
    if handled:
        STATE.write_text(json.dumps(sorted(processed), indent=1))
    else:
        print("no new decisions")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    if args.once:
        return run_once()
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001
            print("poll error:", type(exc).__name__, str(exc)[:150])
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
