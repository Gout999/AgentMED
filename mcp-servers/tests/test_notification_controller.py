"""Original-reply MCP freezes the durable post-promote continuation."""
from __future__ import annotations

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
