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
import secrets
from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class ToolDefinitionRegistry:
    """Decorator-only registry that deliberately cannot serve an MCP app.

    Implementation modules use decorators for readable tool metadata, but only
    ``build_tool_projection`` creates a serviceable FastMCP object.  This
    prevents an import/CLI shortcut from exposing the union of every role.
    """

    def __init__(self, name: str):
        self.name = name
        self.names: set[str] = set()

    def tool(self, *, name: str):
        if name in self.names:
            raise RuntimeError(f"duplicate MCP tool definition: {name}")
        self.names.add(name)

        def decorator(func):
            return func

        return decorator


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


class TrustedGatewayOnly:
    """Reject direct MCP backend access and cross-consumer projection reuse.

    Strict mode (default) requires the exact gateway backend token AND the
    consumer header. Demo mode (MCP_TRUST_GATEWAY_CONSUMER=true) accepts the
    gateway-injected consumer header alone — valid only when the projection
    binds to loopback (MCP_HOST=127.0.0.1), because Higress key-auth injects
    x-mse-consumer and the header cannot be forged through the gateway; a
    loopback bind removes the direct-forgery path.
    """

    def __init__(self, app, *, expected_consumer: str, backend_token: str,
                 trust_consumer: bool = False, host: str = "0.0.0.0"):
        if not expected_consumer or not backend_token:
            raise RuntimeError(
                "MCP_EXPECTED_CONSUMER and MCP_GATEWAY_BACKEND_TOKEN are required"
            )
        if trust_consumer and host not in ("127.0.0.1", "localhost"):
            raise RuntimeError(
                "MCP_TRUST_GATEWAY_CONSUMER requires MCP_HOST=127.0.0.1/localhost"
            )
        self.app = app
        self.expected_consumer = expected_consumer
        self.backend_token = backend_token
        self.trust_consumer = trust_consumer

    @staticmethod
    def _header_values(scope: dict[str, Any], name: bytes) -> list[str]:
        return [
            value.decode("utf-8", errors="replace")
            for key, value in scope.get("headers", [])
            if key.lower() == name
        ]

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope.get("type") == "http" else ""
        # Health contains no domain data and remains usable by a supervisor.
        # Every other HTTP route, including notification mock inspection, is a
        # backend surface and requires the exact trusted gateway hop.
        if scope.get("type") == "http" and path != "/healthz":
            tokens = self._header_values(scope, b"x-caseloop-gateway-token")
            consumers = self._header_values(scope, b"x-mse-consumer")
            token_ok = (
                len(tokens) == 1
                and secrets.compare_digest(tokens[0], self.backend_token)
            )
            consumer_ok = (
                len(consumers) == 1
                and secrets.compare_digest(consumers[0], self.expected_consumer)
            )
            authorized = consumer_ok and (token_ok or self.trust_consumer)
            if not authorized:
                response = JSONResponse(
                    {
                        "error_code": "FORBIDDEN",
                        "message": "MCP backend accepts only its authenticated gateway projection",
                        "retryable": False,
                    },
                    status_code=403,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_server_app(
    mcp: FastMCP,
    *,
    expected_consumer: str,
    gateway_backend_token: str,
    extra_routes: Optional[list] = None,
    extra_middleware: Optional[list] = None,
    trust_consumer: bool = False,
    host: str = "0.0.0.0",
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
    return TrustedGatewayOnly(
        PathRewrite(app),
        expected_consumer=expected_consumer,
        backend_token=gateway_backend_token,
        trust_consumer=trust_consumer,
        host=host,
    )


def build_tool_projection(
    server_name: str,
    profile: str,
    profiles: dict[str, dict[str, Callable[..., Any]]],
) -> FastMCP:
    """Build a physically separate MCP tool surface for one gateway consumer role.

    Higress authenticates consumers at MCP-server granularity.  Each projected
    server therefore exposes only one role's allowlisted callables; prompts and
    caller-supplied role strings are never used as authorization.
    """

    tools = profiles.get(profile)
    if tools is None:
        allowed = ", ".join(sorted(profiles))
        raise RuntimeError(
            f"MCP_TOOL_PROFILE={profile!r} is invalid for {server_name}; expected one of: {allowed}"
        )
    projected = FastMCP(f"{server_name}-{profile}")
    for tool_name, func in tools.items():
        projected.tool(name=tool_name)(func)
    return projected


def validate_projection_runtime(
    settings: Any,
    *,
    profile_workers: dict[str, str],
    role_token_profiles: frozenset[str] = frozenset(),
    gate_authority_profiles: frozenset[str] = frozenset(),
) -> None:
    """Fail startup when a projection is misbound or receives excess authority."""

    profile = settings.mcp_tool_profile
    if profile not in profile_workers:
        raise RuntimeError(
            f"MCP_TOOL_PROFILE={profile!r} has no fixed runtime identity"
        )
    expected_consumer = f"worker-{profile}"
    if settings.mcp_expected_consumer != expected_consumer:
        raise RuntimeError(
            f"MCP_EXPECTED_CONSUMER must be {expected_consumer!r} for profile {profile}"
        )
    expected_worker = profile_workers[profile]
    if settings.mcp_worker_id != expected_worker:
        raise RuntimeError(
            f"MCP_WORKER_ID must be {expected_worker!r} for profile {profile}"
        )
    if not settings.mcp_gateway_backend_token:
        raise RuntimeError("MCP_GATEWAY_BACKEND_TOKEN is required")

    has_role_token = bool(settings.control_plane_role_token)
    needs_role_token = profile in role_token_profiles
    if has_role_token != needs_role_token:
        requirement = "required" if needs_role_token else "forbidden"
        raise RuntimeError(
            f"CONTROL_PLANE_ROLE_TOKEN is {requirement} for profile {profile}"
        )

    has_gate_token = bool(settings.gate_authority_token)
    needs_gate_token = profile in gate_authority_profiles
    if has_gate_token != needs_gate_token:
        requirement = "required" if needs_gate_token else "forbidden"
        raise RuntimeError(
            f"GATE_AUTHORITY_TOKEN is {requirement} for profile {profile}"
        )


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
