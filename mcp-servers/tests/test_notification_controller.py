"""Original-reply MCP freezes the durable post-promote continuation."""
from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import func, select

from common.config import Settings
from common.db import create_all, session_scope
from common.errors import McpError
from common.tables import AuditRow, NotificationMessage
from servers import notification


class _CP:
    def __init__(self):
        self.posts = []

    def post(self, path, json_body=None, **_kwargs):
        self.posts.append((path, json_body or {}))
        return {
            "release_id": "rel_notify0001",
            "status": "configured",
            "duplicate": False,
        }


def test_reply_origin_configures_control_plane_closure_without_fake_delivery(monkeypatch):
    cp = _CP()
    monkeypatch.setattr(notification, "_cp", lambda: cp)

    result = notification.feishu_reply_origin(
        release_id="rel_notify0001",
        channel="feishu:chat-1",
        thread_ref="feishu:chat-1:root-1",
        body_ref="repo:///evidence/reply.txt",
        body_digest="sha256:" + "a" * 64,
    )

    assert cp.posts == [
        (
            "/v1/releases/rel_notify0001/closure-context",
            {
                "channel": "feishu:chat-1",
                "thread_ref": "feishu:chat-1:root-1",
                "body_ref": "repo:///evidence/reply.txt",
                "body_digest": "sha256:" + "a" * 64,
            },
        )
    ]
    assert result["status"] == "configured"
    assert "message_id" not in result


def test_reply_origin_legacy_inline_body_derives_exact_digest(monkeypatch):
    cp = _CP()
    monkeypatch.setattr(notification, "_cp", lambda: cp)
    body_ref = "data:text/plain;base64,aGVsbG8="

    notification.feishu_reply_origin(
        release_id="rel_notify0001",
        channel="feishu:chat-1",
        thread_ref="feishu:chat-1:root-1",
        body_ref=body_ref,
    )

    assert cp.posts[0][1]["body_digest"] == (
        "sha256:" + hashlib.sha256(b"hello").hexdigest()
    )


def test_reply_origin_legacy_external_ref_without_digest_fails_closed(monkeypatch):
    cp = _CP()
    monkeypatch.setattr(notification, "_cp", lambda: cp)

    with pytest.raises(McpError, match="body_digest is required"):
        notification.feishu_reply_origin(
            release_id="rel_notify0001",
            channel="feishu:chat-1",
            thread_ref="feishu:chat-1:root-1",
            body_ref="repo:///evidence/reply.txt",
        )

    assert cp.posts == []


class _MessagesRequest:
    query_params: dict[str, str] = {}


def _count(url: str, model: type) -> int:
    with session_scope(url) as session:
        return int(session.scalar(select(func.count()).select_from(model)) or 0)


def test_notification_send_and_list_use_resolved_notification_database(
    monkeypatch, tmp_path
):
    primary_url = f"sqlite:///{tmp_path / 'primary.db'}"
    notification_url = f"sqlite:///{tmp_path / 'notification.db'}"
    create_all(primary_url)
    create_all(notification_url)
    settings = Settings(
        database_url=primary_url,
        notification_log_url=notification_url,
        audit_jsonl_path=str(tmp_path / "notification-audit.jsonl"),
        _env_file=None,
    )
    monkeypatch.setattr(notification, "_settings", lambda: settings)

    sent = notification._send(
        channel="matrix",
        room="ops",
        text="notification database isolation",
        outbox_id="obx_notification_database_isolation",
        actor="quality-officer",
    )
    listed = notification._api_messages(_MessagesRequest())
    payload = json.loads(listed.body)

    assert sent["status"] == "delivered"
    assert [item["message_id"] for item in payload["items"]] == [
        sent["message_id"]
    ]
    assert _count(primary_url, NotificationMessage) == 0
    assert _count(primary_url, AuditRow) == 0
    assert _count(notification_url, NotificationMessage) == 1
    assert _count(notification_url, AuditRow) == 1
