"""spike-mcp：Phase 0A 验证用最小 MCP Server（streamable-http）。

提供一个工具 agentmed_ping：原样回显 text 并附上服务器时间。
仅用于验证 Higress 网关侧 MCP 注册 → Consumer 授权 → Worker 调用链路。

注意：Higress mcp-proxy 会把原始请求路径（/mcp-servers/<name>/mcp）
原样转发到上游，因此外层包一层 PathRewrite，把所有路径重写为 /mcp。
"""
import datetime

import uvicorn
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("spike-mcp")


@mcp.tool()
def agentmed_ping(text: str) -> str:
    """回显 text 并附服务器时间戳（spike 验证用）。"""
    return f"PONG | {text} | {datetime.datetime.now(datetime.UTC).isoformat()}"


class PathRewrite:
    """把任意请求路径重写为 /mcp，适配网关透传原始路径的行为。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, path="/mcp", raw_path=b"/mcp")
        await self.app(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(PathRewrite(mcp.streamable_http_app()), host="0.0.0.0", port=8000)
