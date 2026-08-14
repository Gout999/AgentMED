"""scripts/b1_live 两个适配器的 mock 单测。

只覆盖 stdin/stdout 契约、nonce 规则、digest 校验失败路径；
不触网、不读真实 .env.b1-live（真机验证另做）。
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import approval_grant  # noqa: E402
import feishu_message  # noqa: E402


# ---------- 公共工具 ----------

def _write_env(tmp_path: Path, values: dict[str, str]) -> Path:
    env_path = tmp_path / ".env.b1-live"
    env_path.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()) + "\n",
        encoding="utf-8",
    )
    return env_path


def _feed_stdin(monkeypatch, payload: dict) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))


# ---------- approval_grant ----------

_APPROVAL_CONTEXT = {
    "schema_version": "0.1.0",
    "phase": "initial",
    "requested_at": "2026-08-09T00:00:00+00:00",
    "workorder_id": "wo_test000000000001",
    "workorder_hash": "sha256:" + "a" * 64,
    "workorder_nonce": "00000000-0000-4000-8000-000000000001",
    "workorder_expiry": "2026-08-10T00:00:00+00:00",
    "authorization": None,
}

_APPROVAL_ENV = {
    "CONTROL_PLANE_BASE_URL": "http://127.0.0.1:18090",
    "APPROVAL_AUTHORITY_TOKEN": "test-approval-token",
}


def _run_approval_main(monkeypatch, tmp_path, capsys, context, env_values):
    """以 mock HTTP 跑一次 approval_grant.main，返回 (exit, posted, stdout)。"""
    posted = {}

    def fake_post_json(url, *, token, payload):
        posted["url"] = url
        posted["token"] = token
        posted["payload"] = dict(payload)
        return 200, {"approval_id": payload["approval_id"]}

    monkeypatch.setattr(approval_grant, "_post_json", fake_post_json)
    monkeypatch.setattr(
        approval_grant, "_ENV_PATH", _write_env(tmp_path, env_values)
    )
    _feed_stdin(monkeypatch, context)
    code = approval_grant.main()
    out = capsys.readouterr()
    return code, posted, out


def test_approval_initial_grant_reuses_workorder_nonce(monkeypatch, tmp_path, capsys):
    code, posted, out = _run_approval_main(
        monkeypatch, tmp_path, capsys, dict(_APPROVAL_CONTEXT), _APPROVAL_ENV
    )

    assert code == 0
    assert out.err == ""
    # stdout 契约：只有 approval_id 一个键
    receipt = json.loads(out.out)
    assert set(receipt) == {"approval_id"}
    assert receipt["approval_id"] == posted["payload"]["approval_id"]
    assert receipt["approval_id"].startswith("appr_")

    # 端点与头部
    assert posted["url"] == "http://127.0.0.1:18090/v1/approvals"
    assert posted["token"] == "test-approval-token"

    grant = posted["payload"]
    assert grant["schema_version"] == "0.1.0"
    assert grant["decision"] == "approved"
    assert grant["nonce_consumed"] is False
    assert grant["approver"]["type"] == "human"
    assert grant["approver"]["identity"].strip()
    # 初始授权：nonce == workorder_nonce 且 authorization 为 null
    assert grant["nonce"] == _APPROVAL_CONTEXT["workorder_nonce"]
    assert grant["authorization"] is None
    # workorder 绑定字段逐字回显
    assert grant["workorder_id"] == _APPROVAL_CONTEXT["workorder_id"]
    assert grant["workorder_hash"] == _APPROVAL_CONTEXT["workorder_hash"]
    assert grant["expiry"] == _APPROVAL_CONTEXT["workorder_expiry"]


def test_approval_action_grant_fresh_uuid4_and_echoed_authorization(
    monkeypatch, tmp_path, capsys
):
    authorization = {
        "action": "canary",
        "release_id": "rel_test0000001",
        "target_revision": "rev-1",
        "params": {"percent": 5},
        "params_digest": "sha256:" + "b" * 64,
    }
    context = dict(_APPROVAL_CONTEXT, authorization=authorization)
    code, posted, out = _run_approval_main(
        monkeypatch, tmp_path, capsys, context, _APPROVAL_ENV
    )

    assert code == 0
    grant = posted["payload"]
    # authorization 逐字回显
    assert grant["authorization"] == authorization
    # nonce 是全新 UUID4，不得等于 workorder_nonce
    assert grant["nonce"] != _APPROVAL_CONTEXT["workorder_nonce"]
    parsed = uuid.UUID(grant["nonce"])
    assert parsed.version == 4
    assert json.loads(out.out)["approval_id"] == grant["approval_id"]


def test_approval_rejects_non_object_authorization(monkeypatch, tmp_path, capsys):
    context = dict(_APPROVAL_CONTEXT, authorization="canary")
    code, posted, out = _run_approval_main(
        monkeypatch, tmp_path, capsys, context, _APPROVAL_ENV
    )
    assert code == 1
    assert "authorization" in out.err
    assert posted == {}  # 未发出任何 HTTP 请求


def test_approval_control_plane_error_fails_closed(monkeypatch, tmp_path, capsys):
    def fake_post_json(url, *, token, payload):
        return 401, {"detail": {"code": "unauthorized", "message": "bad token"}}

    monkeypatch.setattr(approval_grant, "_post_json", fake_post_json)
    monkeypatch.setattr(
        approval_grant, "_ENV_PATH", _write_env(tmp_path, _APPROVAL_ENV)
    )
    _feed_stdin(monkeypatch, dict(_APPROVAL_CONTEXT))
    assert approval_grant.main() == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "unauthorized" in out.err
    # stderr 不得泄露 token 值
    assert _APPROVAL_ENV["APPROVAL_AUTHORITY_TOKEN"] not in out.err


def test_approval_missing_env_key_fails_before_http(monkeypatch, tmp_path, capsys):
    def fake_post_json(url, *, token, payload):  # pragma: no cover - 不应被调用
        raise AssertionError("不应发出 HTTP 请求")

    monkeypatch.setattr(approval_grant, "_post_json", fake_post_json)
    monkeypatch.setattr(
        approval_grant,
        "_ENV_PATH",
        _write_env(tmp_path, {"CONTROL_PLANE_BASE_URL": "http://127.0.0.1:18090"}),
    )
    _feed_stdin(monkeypatch, dict(_APPROVAL_CONTEXT))
    assert approval_grant.main() == 1
    assert "APPROVAL_AUTHORITY_TOKEN" in capsys.readouterr().err


# ---------- feishu_message ----------

_NOT_BEFORE = "2026-08-09T00:00:00+00:00"
_AFTER_NOT_BEFORE_MS = str(
    int(datetime(2026, 8, 9, 0, 0, 1, tzinfo=timezone.utc).timestamp() * 1000)
)

_FEISHU_ENV = {
    "FEISHU_APP_ID": "cli_test",
    "FEISHU_APP_SECRET": "test-secret",
    "FEISHU_CHAT_ID": "oc_test_chat",
    "FEISHU_MESSAGE_MODE": "auto",
}


def _feishu_context() -> dict:
    fixture = feishu_message.load_b1_complaint_fixture()
    return {
        "schema_version": "0.1.0",
        "phase": "await-post-injection-complaint",
        "provider": "feishu",
        "fixture_ref": fixture.repository_ref,
        "fixture_text_digest": fixture.text_digest,
        "injection_operation_id": "op_test_injection_1",
        "not_before": _NOT_BEFORE,
        "instruction": "test",
    }


def _text_item(message_id: str, text: str, create_time_ms: str) -> dict:
    return {
        "message_id": message_id,
        "msg_type": "text",
        "deleted": False,
        "chat_id": "oc_test_chat",
        "create_time": create_time_ms,
        "body": {"content": json.dumps({"text": text}, ensure_ascii=False)},
        "sender": {"id": "u-1"},
    }


def test_feishu_auto_posts_fixture_text_and_returns_exact_keys(
    monkeypatch, tmp_path, capsys
):
    fixture = feishu_message.load_b1_complaint_fixture()
    calls = []

    def fake_request(client, method, path, *, params=None, payload=None, token=None):
        calls.append((method, path, params, payload))
        if path.endswith("tenant_access_token/internal"):
            return 200, {"code": 0, "tenant_access_token": "t-1", "expire": 7200}
        if method == "POST" and path == "/open-apis/im/v1/messages":
            return 200, {"code": 0, "data": {"message_id": "om_testauto0001"}}
        if method == "GET" and path == "/open-apis/im/v1/messages/om_testauto0001":
            return 200, {
                "code": 0,
                "data": {"items": [_text_item("om_testauto0001", fixture.text, _AFTER_NOT_BEFORE_MS)]},
            }
        raise AssertionError(f"未预期的请求 {method} {path}")

    monkeypatch.setattr(feishu_message, "_request", fake_request)
    monkeypatch.setattr(feishu_message, "_ENV_PATH", _write_env(tmp_path, _FEISHU_ENV))
    _feed_stdin(monkeypatch, _feishu_context())

    assert feishu_message.main() == 0
    out = capsys.readouterr()
    # stdout 键集合必须精确相等，message_id 匹配 runner 正则
    receipt = json.loads(out.out)
    assert set(receipt) == {"schema_version", "provider", "message_id"}
    assert receipt["schema_version"] == "0.1.0"
    assert receipt["provider"] == "feishu"
    assert re.fullmatch(r"[A-Za-z0-9_-]{8,256}", receipt["message_id"])

    # 发消息请求形状：receive_id/msg_type/content(text=冻结正文)/稳定 uuid
    post = next(c for c in calls if c[0] == "POST" and c[1] == "/open-apis/im/v1/messages")
    assert post[2] == {"receive_id_type": "chat_id"}
    body = post[3]
    assert body["receive_id"] == "oc_test_chat"
    assert body["msg_type"] == "text"
    assert json.loads(body["content"]) == {"text": fixture.text}
    assert body["uuid"].startswith("caseloop-b1-")


def test_feishu_digest_mismatch_fails_before_any_http(monkeypatch, tmp_path, capsys):
    def fake_request(*_args, **_kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("不应发出 HTTP 请求")

    monkeypatch.setattr(feishu_message, "_request", fake_request)
    monkeypatch.setattr(feishu_message, "_ENV_PATH", _write_env(tmp_path, _FEISHU_ENV))
    context = _feishu_context()
    context["fixture_text_digest"] = "sha256:" + hashlib.sha256(b"tampered").hexdigest()
    _feed_stdin(monkeypatch, context)

    assert feishu_message.main() == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "摘要" in out.err


def test_feishu_missing_chat_id_fails_with_setup_hint(monkeypatch, tmp_path, capsys):
    env = {k: v for k, v in _FEISHU_ENV.items() if k != "FEISHU_CHAT_ID"}
    monkeypatch.setattr(feishu_message, "_ENV_PATH", _write_env(tmp_path, env))
    _feed_stdin(monkeypatch, _feishu_context())

    assert feishu_message.main() == 1
    assert "FEISHU_CHAT_ID 未配置（等待用户建群拉机器人）" in capsys.readouterr().err


def test_feishu_auto_rejects_stale_create_time(monkeypatch, tmp_path, capsys):
    fixture = feishu_message.load_b1_complaint_fixture()
    stale_ms = str(
        int(datetime(2026, 8, 8, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
    )

    def fake_request(client, method, path, *, params=None, payload=None, token=None):
        if path.endswith("tenant_access_token/internal"):
            return 200, {"code": 0, "tenant_access_token": "t-1", "expire": 7200}
        if method == "POST":
            return 200, {"code": 0, "data": {"message_id": "om_testauto0002"}}
        return 200, {
            "code": 0,
            "data": {"items": [_text_item("om_testauto0002", fixture.text, stale_ms)]},
        }

    monkeypatch.setattr(feishu_message, "_request", fake_request)
    monkeypatch.setattr(feishu_message, "_ENV_PATH", _write_env(tmp_path, _FEISHU_ENV))
    _feed_stdin(monkeypatch, _feishu_context())

    assert feishu_message.main() == 1
    assert "not_before" in capsys.readouterr().err


def test_feishu_human_mode_polls_until_match(monkeypatch, tmp_path, capsys):
    fixture = feishu_message.load_b1_complaint_fixture()
    old_ms = str(
        int(datetime(2026, 8, 8, 12, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    )
    list_calls = []

    def fake_request(client, method, path, *, params=None, payload=None, token=None):
        if path.endswith("tenant_access_token/internal"):
            return 200, {"code": 0, "tenant_access_token": "t-1", "expire": 7200}
        assert method == "GET" and path == "/open-apis/im/v1/messages"
        assert params["container_id_type"] == "chat"
        assert params["container_id"] == "oc_test_chat"
        list_calls.append(params)
        if len(list_calls) == 1:
            # 第一轮：只有一条早于 not_before 的旧消息和一条其他文本
            return 200, {
                "code": 0,
                "data": {
                    "items": [
                        _text_item("om_other_00001", "别的话题", _AFTER_NOT_BEFORE_MS),
                        _text_item("om_old_0000001", fixture.text, old_ms),
                    ]
                },
            }
        return 200, {
            "code": 0,
            "data": {
                "items": [_text_item("om_human_000001", fixture.text, _AFTER_NOT_BEFORE_MS)]
            },
        }

    monkeypatch.setattr(feishu_message, "_request", fake_request)
    monkeypatch.setattr(feishu_message, "_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(
        feishu_message,
        "_ENV_PATH",
        _write_env(tmp_path, dict(_FEISHU_ENV, FEISHU_MESSAGE_MODE="human")),
    )
    _feed_stdin(monkeypatch, _feishu_context())

    assert feishu_message.main() == 0
    receipt = json.loads(capsys.readouterr().out)
    assert set(receipt) == {"schema_version", "provider", "message_id"}
    assert receipt["message_id"] == "om_human_000001"
    assert len(list_calls) == 2


def test_feishu_human_mode_times_out(monkeypatch, tmp_path, capsys):
    def fake_request(client, method, path, *, params=None, payload=None, token=None):
        if path.endswith("tenant_access_token/internal"):
            return 200, {"code": 0, "tenant_access_token": "t-1", "expire": 7200}
        return 200, {"code": 0, "data": {"items": []}}

    monkeypatch.setattr(feishu_message, "_request", fake_request)
    monkeypatch.setattr(feishu_message, "_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(feishu_message, "_POLL_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(
        feishu_message,
        "_ENV_PATH",
        _write_env(tmp_path, dict(_FEISHU_ENV, FEISHU_MESSAGE_MODE="human")),
    )
    _feed_stdin(monkeypatch, _feishu_context())

    assert feishu_message.main() == 1
    assert "超时" in capsys.readouterr().err
