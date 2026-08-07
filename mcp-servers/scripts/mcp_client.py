"""MCP Streamable HTTP 客户端（smoke.sh 用）。

用法：
  .venv/bin/python scripts/mcp_client.py <port> <tool_name> '<json_args>'

输出：成功 → JSON 结果；失败 → 打印 isError + envelope 并退出码 1。
"""
from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def call_tool(port: int, tool_name: str, args: dict):
    url = f"http://127.0.0.1:{port}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)
            if result.isError:
                text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
                print(json.dumps({"ok": False, "isError": True, "text": text}, ensure_ascii=False))
                return 1
            if result.structuredContent is not None:
                print(json.dumps({"ok": True, **result.structuredContent}, ensure_ascii=False, default=str))
            else:
                text = "".join(c.text for c in result.content if getattr(c, "type", "") == "text")
                print(json.dumps({"ok": True, "content": text}, ensure_ascii=False, default=str))
            return 0


async def list_tools(port: int):
    url = f"http://127.0.0.1:{port}/mcp"
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(json.dumps({"ok": True, "tools": [t.name for t in tools.tools]}, ensure_ascii=False))
            return 0


async def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    port = int(sys.argv[1])
    action = sys.argv[2]
    if action == "list":
        return await list_tools(port)
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    return await call_tool(port, action, args)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
