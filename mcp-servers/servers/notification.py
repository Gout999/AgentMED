"""mcp-notification：控制面通知命令 + 明确隔离的辅助 mock 能力。

- feishu.reply_origin 只向控制面排队；dispatcher 才能接收 provider 回执并归档 Case。
- approval_card / weekly_report 是当前明确标注的 feishu-mock 辅助适配器。
- matrix.log：对内留痕。
- thread_ref 格式（D-001 Q5）：feishu-mock:<room>:<msg_ref>；真飞书 feishu:<chat_id>:<message_id>，其中 message_id 是原投诉消息/reply 目标。
- REST 查询端点：GET /api/messages（mock 群消息日志，接口签名与真飞书一致）。
- 幂等：outbox_id 唯一；同键同参返回首次结果（§9.2）。
"""
import logging
import sys
from pathlib import Path
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.routing import Route

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.audit import AuditService  # noqa: E402
from common.config import Settings, get_settings  # noqa: E402
from common.db import session_scope  # noqa: E402
from common.errors import IDEMPOTENCY_CONFLICT, McpError, dependency_unavailable, forbidden, validation  # noqa: E402
from common.http import HttpClient  # noqa: E402
from common.ids import new_message_id, new_msg_ref  # noqa: E402
from common.serverkit import (  # noqa: E402
    ToolDefinitionRegistry,
    build_server_app,
    build_tool_projection,
    json_response,
    validate_projection_runtime,
)
from common.tables import NotificationMessage  # noqa: E402

logger = logging.getLogger(__name__)

mcp = ToolDefinitionRegistry("mcp-notification")

_DEFAULT_ROOM = "demo"


def _settings() -> Settings:
    return get_settings()


def _cp() -> HttpClient:
    settings = _settings()
    return HttpClient(settings.control_plane_base_url, token=settings.control_plane_role_token)


def _send(
    *,
    channel: str,
    room: str,
    text: str,
    outbox_id: Optional[str],
    thread_ref: Optional[str] = None,
    actor: str = "agent",
) -> dict[str, Any]:
    """落 NotificationMessage + 审计（幂等：同 outbox_id 返回既有消息）。"""
    message_id = new_message_id()
    msg_ref = new_msg_ref()
    with session_scope() as session:
        if outbox_id:
            existing = session.scalar(
                select(NotificationMessage).where(NotificationMessage.outbox_id == outbox_id)
            )
            if existing is not None:
                return {
                    "message_id": existing.message_id,
                    "msg_ref": existing.msg_ref,
                    "thread_ref": existing.thread_ref,
                    "status": existing.status,
                    "duplicate": True,
                }
        row = NotificationMessage(
            message_id=message_id,
            channel=channel,
            room=room,
            thread_ref=thread_ref,
            text=text,
            msg_ref=msg_ref,
            outbox_id=outbox_id,
            status="delivered",
        )
        session.add(row)
        audit = AuditService(session)
        audit.record(
            actor=actor,
            action=f"notification.{channel}.sent",
            target=message_id,
            params={"room": room, "msg_ref": msg_ref, "outbox_id": outbox_id},
            result="success",
        )
        try:
            session.flush()
        except IntegrityError as exc:
            # 并发同键：幂等返回首次结果
            if outbox_id:
                existing = session.scalar(
                    select(NotificationMessage).where(NotificationMessage.outbox_id == outbox_id)
                )
                if existing is not None:
                    session.rollback()
                    return {
                        "message_id": existing.message_id,
                        "msg_ref": existing.msg_ref,
                        "thread_ref": existing.thread_ref,
                        "status": existing.status,
                        "duplicate": True,
                    }
            raise McpError(IDEMPOTENCY_CONFLICT, f"message insert conflict: {exc}") from exc
    return {
        "message_id": message_id,
        "msg_ref": msg_ref,
        "thread_ref": thread_ref,
        "status": "delivered",
        "duplicate": False,
    }


def _parse_feishu_mock_ref(ref: str) -> Optional[tuple[str, str]]:
    """从 `feishu-mock:<room>:<msg_ref>` 解析 (room, parent_msg_ref)。"""
    if ref.startswith("feishu-mock:"):
        parts = ref.split(":")
        if len(parts) >= 3:
            return parts[1], parts[2]
        if len(parts) == 2:
            return parts[1], ""
    return None


@mcp.tool(name="feishu.reply_origin")
def feishu_reply_origin(
    release_id: str,
    channel: str,
    thread_ref: str,
    body_ref: str,
    body_digest: str,
) -> dict[str, Any]:
    """Freeze the original-thread reply as the durable post-promote continuation.

    The controller validates that channel/thread_ref equal the immutable
    complaint origin. This must run before promote; success means CONFIGURED,
    never delivered. RELEASE_PROMOTED outbox consumption queues the reply, and
    only a provider receipt may close the Case.
    """

    if not all(
        isinstance(value, str) and value
        for value in (release_id, channel, thread_ref, body_ref, body_digest)
    ):
        raise validation("release_id, channel, thread_ref, body_ref, and body_digest are required")
    if not body_digest.startswith("sha256:") or len(body_digest) != 71:
        raise validation("body_digest must be sha256:<64 hex>")
    try:
        return _cp().post(
            f"/v1/releases/{release_id}/closure-context",
            json_body={
                "channel": channel,
                "thread_ref": thread_ref,
                "body_ref": body_ref,
                "body_digest": body_digest,
            },
        )
    except McpError as exc:
        raise exc
    except Exception as exc:  # noqa: BLE001
        raise dependency_unavailable(f"notification controller unreachable: {exc}") from exc


@mcp.tool(name="feishu.approval_card")
def feishu_approval_card(
    approval_id: str,
    workorder_hash: str,
    evidence_summary: str,
    expiry: str,
    room: str = "approval",
    idempotency_key: Optional[str] = None,
) -> dict[str, Any]:
    """发审批卡片（ACL：控制面）。包含 workorder_hash + expiry，明示 TTL。返回 {message_id}。"""
    text = (
        f"【审批卡片】approval_id={approval_id} workorder_hash={workorder_hash} "
        f"expiry={expiry}\n{evidence_summary}"
    )
    outbox_id = idempotency_key or f"approval:{approval_id}:card"
    return _send(
        channel="feishu-mock",
        room=room,
        text=text,
        outbox_id=outbox_id,
        thread_ref=f"feishu-mock:{room}:",
        actor="controller:release",
    )


@mcp.tool(name="feishu.weekly_report")
def feishu_weekly_report(report: dict[str, Any], room: str = "weekly") -> dict[str, Any]:
    """发质量周报（ACL：案例官）。report 为 §10.4 结构。返回 {message_id}。"""
    import json

    text = json.dumps(report, ensure_ascii=False)
    return _send(
        channel="feishu-mock",
        room=room,
        text=text,
        outbox_id=f"weekly:{report.get('week', 'unknown')}",
        thread_ref=f"feishu-mock:{room}:",
        actor="case-officer",
    )


@mcp.tool(name="matrix.log")
def matrix_log(room: str, text: str) -> dict[str, Any]:
    """对内留痕消息（ACL：全员）。返回 {event_id}（=message_id）。"""
    actor = _settings().mcp_worker_id
    if not actor:
        raise forbidden("MCP process has no canonical worker identity")
    result = _send(channel="matrix", room=room, text=text, outbox_id=None, actor=actor)
    return {"event_id": result["message_id"], "room": room}


def _stable_seq(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---------- REST 查询端点（mock 群消息日志） ----------


def _api_messages(request) -> Any:
    channel = request.query_params.get("channel")
    room = request.query_params.get("room")
    limit = min(int(request.query_params.get("limit", "50")), 200)
    with session_scope() as session:
        q = select(NotificationMessage).order_by(NotificationMessage.created_at.desc())
        if channel:
            q = q.where(NotificationMessage.channel == channel)
        if room:
            q = q.where(NotificationMessage.room == room)
        rows = session.scalars(q.limit(limit)).all()
    return json_response(
        200,
        {
            "items": [
                {
                    "message_id": r.message_id,
                    "channel": r.channel,
                    "room": r.room,
                    "thread_ref": r.thread_ref,
                    "text": r.text,
                    "msg_ref": r.msg_ref,
                    "outbox_id": r.outbox_id,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]
        },
    )


def _api_health(request) -> Any:
    return json_response(200, {"status": "ok", "service": "mcp-notification"})


def _profiled_mcp(profile: str) -> FastMCP:
    profiles = {
        "quality-officer": {"matrix.log": matrix_log},
        "case-officer": {
            "feishu.reply_origin": feishu_reply_origin,
            "feishu.weekly_report": feishu_weekly_report,
            "matrix.log": matrix_log,
        },
    }
    return build_tool_projection("mcp-notification", profile, profiles)


def main() -> None:
    import uvicorn

    s = _settings()
    validate_projection_runtime(
        s,
        profile_workers={
            "quality-officer": "quality-officer",
            "case-officer": "case-officer",
        },
        role_token_profiles=frozenset({"case-officer"}),
    )
    app = build_server_app(
        _profiled_mcp(s.mcp_tool_profile),
        expected_consumer=s.mcp_expected_consumer,
        gateway_backend_token=s.mcp_gateway_backend_token,
        extra_routes=[
            Route("/api/messages", _api_messages, methods=["GET"]),
            Route("/healthz", _api_health, methods=["GET"]),
        ],
    )
    uvicorn.run(app, host=s.host, port=s.notification_port, log_level=s.log_level.lower())


if __name__ == "__main__":
    main()
