"""Langfuse signal source（flow-first 第 2 步，确定性，无模型依赖）。

真实接入：AgentMED（模型代理，langfuse SDK 4.14.4 / OpenTelemetry）把每次真实
模型调用写成 observation（product=agentmed）。本适配器读【真实项目】的数据：

  1) scores（v3）：负分 → maintainer_report 信号；
  2) observations（v2）：瞬间闭合的 llm 观测（latency<=0.001、无输出）=
     模型调用未完成的真实故障特征 → 信号。

幂等：状态文件 + 每信号独立 Idempotency-Key。trace 定位由 v2 观测自带 traceId。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "var" / "langfuse-signal-state.json"

LANGFUSE_BASE = os.environ.get("LANGFUSE_BASE_URL", "http://127.0.0.1:3001")
LANGFUSE_PK = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-84970690db0784d6520aceac1d74d5c2")
LANGFUSE_SK = os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-4539ece94bd23c9be767998bf5fd75370c7fa45d288ed7bf")
CONTROL_PLANE = os.environ.get("CONTROL_PLANE_BASE_URL", "http://127.0.0.1:18090")
OPERATOR_BEARER = os.environ.get("CASELOOP_OPERATOR_BEARER", "demo-operator-bearer-20260814-flow-first")
WORKSPACE = os.environ.get("CASELOOP_WORKSPACE_ID", "ws_wsLocalDemoAgentstation001")
SOURCE_ID = os.environ.get("CASELOOP_SIGNAL_SOURCE_ID", "src_srcLocalDemoManual0000001")
SCORE_THRESHOLD = float(os.environ.get("LANGFUSE_NEGATIVE_THRESHOLD", "1.0"))
OBS_LATENCY_LIMIT = float(os.environ.get("LANGFUSE_INSTANT_CLOSE_LATENCY", "0.002"))


def _auth() -> str:
    return "Basic " + base64.b64encode(f"{LANGFUSE_PK}:{LANGFUSE_SK}".encode()).decode()


def langfuse_get(path: str) -> dict:
    req = urllib.request.Request(LANGFUSE_BASE + path, headers={"Authorization": _auth()})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def submit_signal(source_event_id: str, summary: str, body_text: str) -> str | None:
    payload = {
        "schema_version": "1.0",
        "source_id": SOURCE_ID,
        "source_event_id": source_event_id,
        "source_event_version": "1",
        "signal_kind": "maintainer_report",
        "reporter": {"kind": "maintainer", "source_subject_ref": "caseloop-demo-operator"},
        "project_id": "proj_projLocalDemoAgentstation01",
        "environment_id": None,
        "governed_agent_id": None,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "content": {"summary": summary[:256], "body": body_text, "attachments": []},
        "run_locator": None,
        "privacy_classification": "INTERNAL",
    }
    headers = {
        "Authorization": "Bearer " + OPERATOR_BEARER,
        "X-CaseLoop-Workspace-ID": WORKSPACE,
        "X-CaseLoop-Contract-Version": "1.0",
        "Idempotency-Key": source_event_id,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        CONTROL_PLANE + "/api/v1/signals",
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            public_case = result.get("case", {}).get("case_id")
            internal_case = _internal_intake(source_event_id, payload["content"]["summary"], body_text)
            return public_case or internal_case
    except urllib.error.HTTPError as exc:
        print(f"  submit failed for {source_event_id}: {exc.code} {exc.read().decode()[:160]}")
        return None


def _internal_intake(external_id: str, summary: str, body: str) -> str | None:
    """同源投诉同时开内部聚合案（case_service 聚合）：worker 写链（claim/suggestions）
    只认内部聚合；公共信号只产生 wire 视图。两案以 external_id 关联。"""
    complaint = {
        "source": "webhook",
        "text": summary,
        "external_id": external_id,
        "channel": "langfuse:default",
        "complainant_ref": "langfuse-monitor",
        "app_ref": "agent-station",
        "title": summary[:200],
        "auto_open": True,
    }
    req = urllib.request.Request(
        CONTROL_PLANE + "/v1/complaints",
        data=json.dumps(complaint).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return result.get("case_id")
    except urllib.error.HTTPError as exc:
        print(f"  internal intake failed for {external_id}: {exc.code} {exc.read().decode()[:160]}")
        return None


def _load_state() -> set[str]:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if STATE.exists():
        return set(json.loads(STATE.read_text()))
    return set()


def _save_state(processed: set[str]) -> None:
    STATE.write_text(json.dumps(sorted(processed), indent=1))


def main() -> int:
    processed = _load_state()
    opened = 0

    scores = langfuse_get("/api/public/v3/scores?limit=100").get("data", [])
    for s in scores:
        if s.get("value") is None or s["value"] >= SCORE_THRESHOLD:
            continue
        sid = s["id"]
        key = "score-" + sid
        if key in processed:
            continue
        case_id = submit_signal(
            key,
            f"langfuse 负分 {s.get('name')}={s['value']} trace={s.get('traceId') or 'n/a'}",
            f"score_id={sid} name={s.get('name')} value={s['value']} comment={s.get('comment') or ''}",
        )
        if case_id:
            print(f"  opened {case_id} from score {sid}")
            processed.add(key)
            opened += 1

    obs = langfuse_get("/api/public/v2/observations?limit=50").get("data", [])
    # 按 traceId 聚合：一个 trace 里多个瞬间闭合观测 = 一个故障信号
    by_trace: dict[str, list[dict]] = {}
    for o in obs:
        latency = o.get("latency")
        if latency is None or latency > OBS_LATENCY_LIMIT:
            continue
        by_trace.setdefault(o.get("traceId") or o["id"], []).append(o)
    for trace_id, items in by_trace.items():
        key = "trace-" + trace_id
        if key in processed:
            continue
        names = sorted({i.get("name") or "" for i in items})
        oids = [i["id"] for i in items]
        case_id = submit_signal(
            key,
            f"langfuse 异常 trace {trace_id[:12]} 含 {len(items)} 个瞬间闭合观测（模型调用未完成）",
            f"trace_id={trace_id} observations={','.join(oids)} names={','.join(names)} window={items[0].get('startTime')}",
        )
        if case_id:
            print(f"  opened {case_id} from trace {trace_id} ({len(items)} obs)")
            processed.add(key)
            opened += 1

    _save_state(processed)
    print(f"opened={opened} total_processed={len(processed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
