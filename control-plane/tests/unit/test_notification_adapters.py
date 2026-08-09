"""Receipt and fail-closed tests for replay and live notification adapters."""

from __future__ import annotations

import hashlib
import json
import base64

import httpx
import pytest

from app.config import Settings
from app.notifications.adapters import (
    DisabledNotificationAdapter,
    FeishuLiveAdapter,
    NotificationDeliveryError,
)
from app.services.outbox_relay import notification_adapter_from_settings


def _payload(tmp_path, text: str = "CaseLoop 已修复并完成验证。", *, data_uri: bool = False):
    path = tmp_path / "reply.txt"
    path.write_text(text, encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "channel": "feishu:oc_live",
        "thread_ref": "feishu:oc_live:om_original_message_001",
        "body_ref": (
            "data:text/plain;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
            if data_uri
            else path.resolve().as_uri()
        ),
        "body_digest": digest,
    }


def test_feishu_live_reply_uses_original_message_and_stable_idempotency(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/tenant_access_token/internal"):
            assert json.loads(request.content) == {
                "app_id": "cli_test",
                "app_secret": "secret",
            }
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        assert request.url.path.endswith(
            "/im/v1/messages/om_original_message_001/reply"
        )
        assert request.headers["Authorization"] == "Bearer tenant-token"
        body = json.loads(request.content)
        assert body["msg_type"] == "text"
        assert json.loads(body["content"])["text"] == "CaseLoop 已修复并完成验证。"
        assert body["uuid"].startswith("caseloop-")
        assert len(body["uuid"]) <= 50
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_reply_001"}})

    adapter = FeishuLiveAdapter(
        app_id="cli_test",
        app_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    payload = _payload(tmp_path, data_uri=True)

    first = adapter.deliver(
        outbox_id="obx_live_001", payload=payload, payload_digest="sha256:" + "1" * 64
    )
    duplicate = adapter.deliver(
        outbox_id="obx_live_001", payload=payload, payload_digest="sha256:" + "1" * 64
    )

    assert first == duplicate
    assert first["provider"] == "feishu"
    assert first["provider_origin"] == "https://open.feishu.cn"
    assert first["provider_message_id"] == "om_reply_001"
    assert first["thread_ref"] == payload["thread_ref"]
    assert first["body_digest"] == payload["body_digest"]
    assert len(calls) == 2


def test_feishu_live_restart_reuses_same_provider_uuid(tmp_path):
    uuids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        body = json.loads(request.content)
        uuids.append(body["uuid"])
        return httpx.Response(
            200, json={"code": 0, "data": {"message_id": "om_same_reply"}}
        )

    transport = httpx.MockTransport(handler)
    payload = _payload(tmp_path)
    for _restart in range(2):
        adapter = FeishuLiveAdapter(
            app_id="cli_test",
            app_secret="secret",
            client=httpx.Client(transport=transport),
        )
        receipt = adapter.deliver(
            outbox_id="obx_restart_001",
            payload=payload,
            payload_digest="sha256:" + "9" * 64,
        )
        assert receipt["provider_message_id"] == "om_same_reply"

    assert len(uuids) == 2
    assert uuids[0] == uuids[1]


def test_feishu_live_missing_success_code_fails_closed(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        return httpx.Response(200, json={"data": {"message_id": "om_untrusted"}})

    adapter = FeishuLiveAdapter(
        app_id="cli_test",
        app_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(NotificationDeliveryError) as exc:
        adapter.deliver(
            outbox_id="obx_missing_code_001",
            payload=_payload(tmp_path),
            payload_digest="sha256:" + "a" * 64,
        )

    assert exc.value.code == "feishu_reply_failed"
    assert exc.value.retryable is True
    assert adapter.receipts == {}


def test_feishu_live_fetch_binds_original_text_identity_and_digest():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        assert request.method == "GET"
        assert request.url.path.endswith("/im/v1/messages/om_original_001")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "message_id": "om_original_001",
                            "msg_type": "text",
                            "deleted": False,
                            "chat_id": "oc_chat_001",
                            "create_time": "1786212345000",
                            "sender": {
                                "id": "ou_private",
                                "id_type": "open_id",
                                "sender_type": "user",
                            },
                            "body": {
                                "content": json.dumps(
                                    {"text": "昨天仲可以退貨，今日點解唔得？"},
                                    ensure_ascii=False,
                                )
                            },
                        }
                    ]
                },
            },
        )

    adapter = FeishuLiveAdapter(
        app_id="cli_test",
        app_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = adapter.fetch_text_message("om_original_001")

    assert result["provider_origin"] == "https://open.feishu.cn"
    assert result["channel"] == "feishu:oc_chat_001"
    assert result["thread_ref"] == "feishu:oc_chat_001:om_original_001"
    assert result["text"] == "昨天仲可以退貨，今日點解唔得？"
    assert result["text_digest"] == "sha256:" + hashlib.sha256(
        result["text"].encode("utf-8")
    ).hexdigest()
    assert result["sender_ref"].startswith("feishu-sender:")
    assert "ou_private" not in json.dumps(result)


@pytest.mark.parametrize(
    "response_json",
    [
        {"code": 0, "data": {"items": []}},
        {
            "code": 0,
            "data": {
                "items": [
                    {
                        "message_id": "om_other",
                        "msg_type": "text",
                        "deleted": False,
                        "chat_id": "oc_chat_001",
                        "sender": {},
                        "body": {"content": '{"text":"x"}'},
                    }
                ]
            },
        },
        {
            "data": {
                "items": [
                    {
                        "message_id": "om_original_001",
                        "msg_type": "text",
                        "deleted": False,
                        "chat_id": "oc_chat_001",
                        "sender": {},
                        "body": {"content": '{"text":"x"}'},
                    }
                ]
            }
        },
        {
            "code": 0,
            "data": {
                "items": [
                    {
                        "message_id": "om_original_001",
                        "msg_type": "text",
                        "deleted": False,
                        "chat_id": "oc_chat_001",
                        "create_time": "invalid",
                        "sender": {},
                        "body": {"content": '{"text":"x"}'},
                    }
                ]
            },
        },
    ],
)
def test_feishu_live_fetch_rejects_ambiguous_or_untrusted_response(response_json):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        return httpx.Response(200, json=response_json)

    adapter = FeishuLiveAdapter(
        app_id="cli_test",
        app_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(NotificationDeliveryError):
        adapter.fetch_text_message("om_original_001")


def test_feishu_live_body_digest_mismatch_never_calls_provider(tmp_path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    adapter = FeishuLiveAdapter(
        app_id="cli_test",
        app_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    payload = _payload(tmp_path)
    payload["body_digest"] = "sha256:" + "0" * 64

    with pytest.raises(NotificationDeliveryError) as exc:
        adapter.deliver(
            outbox_id="obx_live_002",
            payload=payload,
            payload_digest="sha256:" + "2" * 64,
        )

    assert exc.value.code == "body_digest_mismatch"
    assert exc.value.retryable is False
    assert calls == []


def test_feishu_business_error_is_retryable_with_stable_provider_uuid(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
            )
        return httpx.Response(200, json={"code": 230001, "msg": "provider busy"})

    adapter = FeishuLiveAdapter(
        app_id="cli_test",
        app_secret="secret",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(NotificationDeliveryError) as exc:
        adapter.deliver(
            outbox_id="obx_live_003",
            payload=_payload(tmp_path),
            payload_digest="sha256:" + "3" * 64,
        )

    assert exc.value.code == "feishu_reply_failed"
    assert exc.value.retryable is True
    assert adapter.receipts == {}


def test_feishu_live_factory_requires_explicit_adapter_and_credentials():
    missing = notification_adapter_from_settings(
        Settings(notification_adapter="feishu-live", feishu_app_id="", feishu_app_secret="")
    )
    configured = notification_adapter_from_settings(
        Settings(
            notification_adapter="feishu-live",
            feishu_app_id="cli_test",
            feishu_app_secret="secret",
        )
    )

    assert isinstance(missing, DisabledNotificationAdapter)
    assert isinstance(configured, FeishuLiveAdapter)
    configured.client.close()


def test_feishu_live_factory_rejects_nonofficial_origin():
    with pytest.raises(ValueError, match="official"):
        notification_adapter_from_settings(
            Settings(
                notification_adapter="feishu-live",
                feishu_app_id="cli_test",
                feishu_app_secret="secret",
                feishu_base_url="http://127.0.0.1:9999",
            )
        )
