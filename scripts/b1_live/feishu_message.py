#!/usr/bin/env python3
"""B1 live 飞书投诉消息获取适配器（auto/human 双模式）。

runner 调用契约见 scripts/run_b1_live.py:_feishu_message_id_from_command
（620-705）。runner 以无秘密子进程调用本脚本，飞书凭证一律由本脚本自行从
仓库根 .env.b1-live 读取（简单 KEY=VALUE 解析，绝不打印秘密值）。

stdin：
    {"schema_version":"0.1.0","phase":"await-post-injection-complaint",
     "provider":"feishu","fixture_ref","fixture_text_digest",
     "injection_operation_id","not_before","instruction"}
stdout 必须精确为三个键：
    {"schema_version":"0.1.0","provider":"feishu","message_id":"om_..."}
任何失败 → 非零 exit + stderr 说明。

模式（.env.b1-live 的 FEISHU_MESSAGE_MODE，缺省 auto）：
- auto：校验冻结 fixture 摘要后用 tenant token 把正文 POST 进群，再 GET
  回读该消息，尽力确认 create_time 晚于 stdin 的 not_before（控制面随后做
  权威校验）；
- human：人工把 fixture 正文发进群，本脚本轮询群消息（按创建时间倒序），
  找正文 sha256 匹配且 create_time 不早于 not_before 的首条 text 消息。
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_PATH = _REPO_ROOT / ".env.b1-live"
# 与 run_b1_live.py 相同的数据源：control-plane 的冻结 B1 fixture 加载器
# （contracts/fixtures/b1-prompt-regression.yaml 是唯一事实源）。
sys.path.insert(0, str(_REPO_ROOT / "control-plane"))

from app.services.b1_fixture import B1FixtureError, load_b1_complaint_fixture  # noqa: E402

_FEISHU_BASE_URL = "https://open.feishu.cn"
_HTTP_TIMEOUT_SECONDS = 30.0
_POLL_INTERVAL_SECONDS = 5.0
_POLL_TIMEOUT_SECONDS = 600.0

# 进程内 tenant token 缓存
_token_cache: dict = {"token": None, "expires_at": 0.0}


class FeishuMessageError(RuntimeError):
    """飞书消息获取失败（stderr 说明 + 非零退出）。"""


def _load_env(path: Path) -> dict[str, str]:
    """简单 KEY=VALUE 解析 .env.b1-live；绝不向 stdout/stderr 泄露值。"""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise FeishuMessageError(f"无法读取秘密文件 {path}: {exc}") from exc
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
        raise FeishuMessageError(f".env.b1-live 缺少必需配置 {key}")
    return value


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict]:
    """直连飞书（client 以 trust_env=False 构造，绕过 http_proxy 污染）。"""
    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        response = client.request(
            method, path, params=params, json=payload, headers=headers
        )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        raise FeishuMessageError(f"飞书请求失败 {method} {path}: {exc}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise FeishuMessageError(
            f"飞书返回非 JSON（HTTP {response.status_code}）"
        ) from exc
    if not isinstance(data, dict):
        raise FeishuMessageError(f"飞书返回非对象（HTTP {response.status_code}）")
    return response.status_code, data


def _tenant_token(client: httpx.Client, app_id: str, app_secret: str) -> str:
    """获取 tenant_access_token，进程内缓存（过期前 60s 刷新）。"""
    now = time.monotonic()
    cached = _token_cache.get("token")
    if cached and now < float(_token_cache["expires_at"]):
        return str(cached)
    status, data = _request(
        client,
        "POST",
        "/open-apis/auth/v3/tenant_access_token/internal",
        payload={"app_id": app_id, "app_secret": app_secret},
    )
    token = data.get("tenant_access_token")
    if status >= 400 or data.get("code") != 0 or not isinstance(token, str) or not token:
        raise FeishuMessageError(
            f"飞书 tenant token 获取失败（HTTP {status} code={data.get('code')}）"
        )
    try:
        expires_in = max(60, int(data.get("expire", 7200)))
    except (TypeError, ValueError):
        expires_in = 7200
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + expires_in - 60
    return token


def _verified_fixture(context: dict):
    """加载冻结 fixture 并校验其 sha256 等于 stdin 的 fixture_text_digest。"""
    if not isinstance(context, dict):
        raise FeishuMessageError("stdin 必须是 JSON 对象")
    expected = context.get("fixture_text_digest")
    if not isinstance(expected, str) or not expected.startswith("sha256:"):
        raise FeishuMessageError("stdin 缺少合法的 fixture_text_digest")
    fixture = load_b1_complaint_fixture()
    if fixture.text_digest != expected:
        raise FeishuMessageError(
            "冻结 fixture 摘要与 stdin 不符："
            f"fixture={fixture.text_digest} stdin={expected}"
        )
    return fixture


def _parse_not_before(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise FeishuMessageError(f"stdin 的 not_before 不是合法时间: {value!r}") from exc
    if parsed.tzinfo is None:
        raise FeishuMessageError("stdin 的 not_before 必须带时区")
    return parsed


def _create_time(value) -> datetime:
    """飞书 create_time 为 13 位毫秒 epoch 字符串。"""
    raw = str(value or "")
    if re.fullmatch(r"[1-9][0-9]{12}", raw) is None:
        raise FeishuMessageError("飞书 create_time 不是毫秒 epoch")
    return datetime.fromtimestamp(int(raw) / 1000, timezone.utc)


def _item_text(item: dict) -> str:
    """从飞书消息 item 中取出 text 正文（body.content 为 JSON 字符串）。"""
    body = item.get("body") or {}
    try:
        content = json.loads(body.get("content") or "")
    except (TypeError, ValueError) as exc:
        raise FeishuMessageError("飞书消息 body.content 不是合法 JSON") from exc
    text = content.get("text") if isinstance(content, dict) else None
    if not isinstance(text, str):
        raise FeishuMessageError("飞书消息缺少 text 正文")
    return text


def _text_matches(text: str, digest: str) -> bool:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest() == digest


def _run_auto(client, settings: dict, context: dict, fixture, not_before) -> str:
    """auto 模式：机器人代发 fixture 正文进群并回读校验。"""
    chat_id = settings["chat_id"]
    token = _tenant_token(client, settings["app_id"], settings["app_secret"])
    # 同一注入操作的重试使用稳定 uuid，飞书侧去重，避免重复发消息。
    dedup_uuid = "caseloop-b1-" + hashlib.sha256(
        f"{context.get('injection_operation_id')}:{fixture.text_digest}".encode("utf-8")
    ).hexdigest()[:32]
    status, data = _request(
        client,
        "POST",
        "/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        payload={
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": fixture.text}, ensure_ascii=False),
            "uuid": dedup_uuid,
        },
        token=token,
    )
    message_id = (data.get("data") or {}).get("message_id")
    if (
        status >= 400
        or data.get("code") != 0
        or not isinstance(message_id, str)
        or not message_id
    ):
        raise FeishuMessageError(
            f"飞书发消息失败（HTTP {status} code={data.get('code')} msg={data.get('msg')}）"
        )

    # 回读这条消息做尽力校验（控制面随后做权威校验）。
    status, data = _request(
        client, "GET", f"/open-apis/im/v1/messages/{message_id}", token=token
    )
    items = (data.get("data") or {}).get("items")
    if status >= 400 or data.get("code") != 0 or not isinstance(items, list) or len(items) != 1:
        raise FeishuMessageError(
            f"飞书回读消息失败（HTTP {status} code={data.get('code')}）"
        )
    item = items[0]
    if item.get("msg_type") != "text" or not _text_matches(_item_text(item), fixture.text_digest):
        raise FeishuMessageError("回读消息正文与冻结 fixture 不符")
    if _create_time(item.get("create_time")) <= not_before:
        raise FeishuMessageError("回读消息 create_time 未晚于 not_before")
    return message_id


def _run_human(client, settings: dict, context: dict, fixture, not_before) -> str:
    """human 模式：轮询群消息，找正文匹配且不早于 not_before 的首条。"""
    chat_id = settings["chat_id"]
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        token = _tenant_token(client, settings["app_id"], settings["app_secret"])
        status, data = _request(
            client,
            "GET",
            "/open-apis/im/v1/messages",
            params={
                "container_id_type": "chat",
                "container_id": chat_id,
                "sort_type": "ByCreateTimeDesc",
                "page_size": "20",
            },
            token=token,
        )
        if status < 400 and data.get("code") == 0:
            items = (data.get("data") or {}).get("items") or []
            for item in items:
                if not isinstance(item, dict) or item.get("msg_type") != "text":
                    continue
                if item.get("deleted") is True:
                    continue
                message_id = item.get("message_id")
                if not isinstance(message_id, str) or not message_id:
                    continue
                try:
                    text = _item_text(item)
                    created = _create_time(item.get("create_time"))
                except FeishuMessageError:
                    continue
                if _text_matches(text, fixture.text_digest) and created >= not_before:
                    return message_id
        # 瞬时错误（网络/限流/机器人尚未入群）一律重试，直到总超时。
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise FeishuMessageError(
        f"等待人工投诉消息超时（{_POLL_TIMEOUT_SECONDS:g}s 内未见匹配 fixture 的消息）"
    )


def main() -> int:
    try:
        try:
            context = json.loads(sys.stdin.read())
        except ValueError as exc:
            raise FeishuMessageError(f"stdin 不是合法 JSON: {exc}") from exc
        fixture = _verified_fixture(context)
        not_before = _parse_not_before(context.get("not_before"))
        secrets = _load_env(_ENV_PATH)
        chat_id = secrets.get("FEISHU_CHAT_ID", "").strip()
        if not chat_id:
            raise FeishuMessageError("FEISHU_CHAT_ID 未配置（等待用户建群拉机器人）")
        settings = {
            "app_id": _require(secrets, "FEISHU_APP_ID"),
            "app_secret": _require(secrets, "FEISHU_APP_SECRET"),
            "chat_id": chat_id,
        }
        mode = (secrets.get("FEISHU_MESSAGE_MODE", "auto") or "auto").strip()
        if mode not in ("auto", "human"):
            raise FeishuMessageError(f"FEISHU_MESSAGE_MODE 非法: {mode}")
        with httpx.Client(
            trust_env=False,
            timeout=_HTTP_TIMEOUT_SECONDS,
            base_url=_FEISHU_BASE_URL,
        ) as client:
            if mode == "auto":
                message_id = _run_auto(client, settings, context, fixture, not_before)
            else:
                message_id = _run_human(client, settings, context, fixture, not_before)
    except (FeishuMessageError, B1FixtureError) as exc:
        print(f"feishu_message: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {"schema_version": "0.1.0", "provider": "feishu", "message_id": message_id},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
