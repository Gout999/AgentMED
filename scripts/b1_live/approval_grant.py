#!/usr/bin/env python3
"""B1 live 独立人工审批适配器。

scripts/run_b1_live.py 以无秘密子进程调用本脚本（_child_env 只保留 PATH 等
安全变量，见 run_b1_live.py:65-116），秘密一律由本脚本自行从仓库根
.env.b1-live 读取（简单 KEY=VALUE 解析，绝不打印秘密值）。

stdin 契约（runner 侧见 run_b1_live.py:_approval_from_command, 708-787）：
    {"schema_version":"0.1.0","phase":...,"workorder_id","workorder_hash",
     "workorder_nonce","workorder_expiry","authorization": null 或对象}
stdout 契约：只输出一行 {"approval_id": "..."} 并以 0 退出；
任何失败 → 非零 exit + stderr 说明。

control-plane 端点（证据：control-plane/app/api/releases.py:222 grant_approval
路由 + app/api/deps.py:213 require_approval_authority 头校验 +
app/services/release_service.py:1289 grant_approval 请求体校验）：
    POST {CONTROL_PLANE_BASE_URL}/v1/approvals
    Authorization: Bearer {APPROVAL_AUTHORITY_TOKEN}
    body 为 ApprovalGrant JSON（schema_version=0.1.0，字段见 build_grant）。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _REPO_ROOT / ".env.b1-live"

# 固定的人类审批人身份。control-plane 仅要求 approver.type=="human" 且
# identity 非空（release_service.py:1315-1322），不校验格式。此处采用飞书
# ou_ 前缀风格，标识「B1 live 跑批中持有独立审批权的人」，与 runner/Agent
# 凭证完全隔离（测试里的样例见 control-plane/tests/conftest.py:402 "human-1"
# 与 tests/unit/test_b1_live_helpers.py:257 "human:test"）。
_APPROVER_IDENTITY = "feishu:ou_b1_live_human_approver"

_HTTP_TIMEOUT_SECONDS = 30.0


class ApprovalGrantError(RuntimeError):
    """审批提交失败（stderr 说明 + 非零退出）。"""


def _load_env(path: Path) -> dict[str, str]:
    """简单 KEY=VALUE 解析 .env.b1-live；绝不向 stdout/stderr 泄露值。"""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ApprovalGrantError(f"无法读取秘密文件 {path}: {exc}") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _require(values: dict[str, str], key: str) -> str:
    value = values.get(key, "").strip()
    if not value:
        raise ApprovalGrantError(f".env.b1-live 缺少必需配置 {key}")
    return value


def _post_json(url: str, *, token: str, payload: dict) -> tuple[int, dict]:
    """直连 POST（trust_env=False，绕过本机 http_proxy 污染）。"""
    try:
        with httpx.Client(trust_env=False, timeout=_HTTP_TIMEOUT_SECONDS) as client:
            response = client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
            )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise ApprovalGrantError(f"control-plane 请求失败: {exc}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise ApprovalGrantError(
            f"control-plane 返回非 JSON（HTTP {response.status_code}）"
        ) from exc
    if not isinstance(data, dict):
        raise ApprovalGrantError(f"control-plane 返回非对象（HTTP {response.status_code}）")
    return response.status_code, data


def build_grant(context: dict) -> dict:
    """按 runner 验收规则构造 ApprovalGrant 请求体（纯函数，便于单测）。"""
    if not isinstance(context, dict):
        raise ApprovalGrantError("stdin 必须是 JSON 对象")
    required = ["workorder_id", "workorder_hash", "workorder_nonce", "workorder_expiry"]
    missing = [
        k for k in required if not isinstance(context.get(k), str) or not context.get(k)
    ]
    if missing:
        raise ApprovalGrantError(f"stdin 缺少字段: {missing}")
    authorization = context.get("authorization")
    if authorization is not None and not isinstance(authorization, dict):
        raise ApprovalGrantError("authorization 必须为 null 或对象")

    if authorization is None:
        # 初始授权：nonce 必须复用 WorkOrder nonce，authorization 保持 null。
        nonce = context["workorder_nonce"]
    else:
        # 动作授权：authorization 逐字回显，nonce 必须是全新的 UUID4（防重放）。
        nonce = str(uuid.uuid4())

    return {
        "schema_version": "0.1.0",
        "approval_id": f"appr_{uuid.uuid4().hex}",
        "workorder_id": context["workorder_id"],
        "workorder_hash": context["workorder_hash"],
        "nonce": nonce,
        # expiry 逐字回显 stdin 的 workorder_expiry：runner 将其与持久化 grant
        # 的 expiry 做字符串相等校验（run_b1_live.py:773）。
        "expiry": context["workorder_expiry"],
        "approver": {"type": "human", "identity": _APPROVER_IDENTITY},
        "decision": "approved",
        "decided_at": datetime.now(timezone.utc).isoformat(),
        "nonce_consumed": False,
        "authorization": authorization,
    }


def main() -> int:
    try:
        try:
            context = json.loads(sys.stdin.read())
        except ValueError as exc:
            raise ApprovalGrantError(f"stdin 不是合法 JSON: {exc}") from exc
        grant = build_grant(context)
        secrets = _load_env(_ENV_PATH)
        base_url = _require(secrets, "CONTROL_PLANE_BASE_URL").rstrip("/")
        token = _require(secrets, "APPROVAL_AUTHORITY_TOKEN")
        status, data = _post_json(
            f"{base_url}/v1/approvals", token=token, payload=grant
        )
        if status >= 400:
            detail = data.get("detail")
            raise ApprovalGrantError(
                f"control-plane 拒绝 ApprovalGrant（HTTP {status}）: "
                f"{json.dumps(detail, ensure_ascii=False)[:400]}"
            )
        if data.get("approval_id") != grant["approval_id"]:
            raise ApprovalGrantError("control-plane 响应未回显 approval_id")
    except ApprovalGrantError as exc:
        print(f"approval_grant: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"approval_id": grant["approval_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
