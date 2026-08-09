"""Original-reply MCP freezes the durable post-promote continuation."""
from __future__ import annotations

import hashlib

import pytest

from common.errors import McpError
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
