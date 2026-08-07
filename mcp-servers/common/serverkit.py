"""MCP server 骨架：FastMCP + PathRewrite(uvicorn) + 组合 ASGI + 错误 envelope。

- FastMCP `streamable_http_app()` 返回 Starlette，路由默认挂在 `/mcp`（streamable_http_path）。
- 网关透传原始路径 `/mcp-servers/<name>/mcp` 需 PathRewrite 成 `/mcp`（只重写 mcp 路径，
  其余 REST/healthz 原样透传）。
- 工具异常统一抛 McpError，其 __str__ 为 JSON envelope（error_code/retryable/audit_ref），
  FastMCP 会以 isError=true 的 text 内容回传；客户端按 envelope 解析（spec §9.2）。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class PathRewrite:
    """把网关透传路径（/mcp-servers/<name>/mcp）重写为 /mcp；其余路径原样透传。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/mcp" or path.endswith("/mcp"):
                scope = dict(scope, path="/mcp", raw_path=b"/mcp")
        await self.app(scope, receive, send)


def build_server_app(
    mcp: FastMCP,
    *,
    extra_routes: Optional[list] = None,
    extra_middleware: Optional[list] = None,
):
    """组合 ASGI app：FastMCP /mcp 路由 + 附加 REST 路由。

    FastMCP Starlette 实例的 routes 为可变 list；追加 REST 路由后整体包 PathRewrite。
    """
    app = mcp.streamable_http_app()
    if extra_routes:
        app.routes.extend(extra_routes)
    if extra_middleware:
        for mw in extra_middleware:
            app.add_middleware(mw)
    return PathRewrite(app)


def json_response(status: int, payload: Any) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


def error_envelope(
    error_code: str,
    message: str,
    *,
    retryable: bool = False,
    audit_ref: Optional[str] = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error_code": error_code,
        "message": message,
        "retryable": retryable,
    }
    if audit_ref:
        body["audit_ref"] = audit_ref
    body.update(extra)
    return body


def parse_error_text(text: str) -> Optional[dict[str, Any]]:
    """从 FastMCP isError 文本中提取 JSON envelope（ToolError 前缀包裹后）。"""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        return json.loads(text[start:])
    except json.JSONDecodeError:
        return None


def make_envelope_str(error_code: str, message: str, *, retryable: bool = False, audit_ref: Optional[str] = None, **extra: Any) -> str:
    """供 McpError.__str__ 输出 JSON envelope（客户端可解析）。"""
    return json.dumps(error_envelope(error_code, message, retryable=retryable, audit_ref=audit_ref, **extra), ensure_ascii=False)
