# Spike 发现 S0-004：Higress MCP 网关端到端验证（注册路径 + 三个坑）

> 日期：2026-08-07 ｜ 环境：AgentTeams v1.2.1 + Higress 网关 + spike-mcp（FastMCP 1.12.4，streamable-http，监听宿主机 :8000）
> 结论：**MCP 链路打通**——controller 与 worker 均可经 Higress `mcp-spike` 代理调用 `caseloop_ping` 并拿到 PONG。
> 注册走 console API 手工作业（官方脚本在 manager 容器内不可用）；过程中踩到 401 引号坑、路径透传坑，均已解决。

## 注册路径（console API 手工作业）

Higress 侧真实操作链（本 spike 由 console API 手工作业完成，脚本不可用见下）：

1. `POST /v1/service-sources` → 建 service source `spike-proxy.dns`（后端 `host.docker.internal:8000/mcp`，DNS 类型）。
2. `PUT /v1/mcpServer` → 建 `mcp-spike`，`type=OPEN_API` + `mcp-proxy` `rawConfigurations`（指向上面的 service source）。
3. `PUT /v1/mcpServer/consumers` → **全量替换语义**：一次请求带完整 `allowedConsumers` 列表
   = `manager` + `worker-spike-leader` + `worker-spike-worker-a` + `worker-spike-worker-b`；
   每次改都要重发全量，不是增量追加。
4. 网关侧对每个 consumer 做 key-auth：Consumer 名 → `worker-<name>`，对应 controller
   `/data/worker-creds/<name>.env` 里的 `WORKER_GATEWAY_KEY`。

**坑 A：官方脚本 `setup-mcp-proxy.sh` 在 manager 容器内跑不通。**
脚本硬编码 `CONSOLE_URL=127.0.0.1:8001`，而 Higress console 实际在 **controller 容器 :18001**；
manager 容器内没有 console、也没有对应端口监听，脚本必然连不上。→ 只能手工作业 console API
（本 spike 用 `/tmp/higress-cookie` 会话 + 上述三步完成）。

## 坑 B：401 引号坑（key-auth 鉴权失败）

从 controller 提取 worker 网关钥匙时，`/data/worker-creds/<name>.env` 形如
`WORKER_GATEWAY_KEY="<64 hex>"`（带**双引号**），`grep | cut -d= -f2` 取出来还带着 `\r`（CRLF）。
直接把带引号/`\r` 的值拼进 `Authorization: Bearer <KEY>` 会被 key-auth 打回 401。

解法（踩坑后修正）：
```bash
docker exec agentteams-controller sh -c 'grep WORKER_GATEWAY_KEY /data/worker-creds/spike-worker-a.env | cut -d= -f2 | tr -d "\"'\'' \r"'
```
必须 `tr -d` 掉双引号、单引号、空格、`\r` 四类字符。

## 坑 C：路径透传坑 + PathRewrite 解法

Higress `mcp-proxy` 会把**原始请求路径 `/mcp-servers/mcp-spike/mcp` 原样透传给上游**，
而 FastMCP 的 `streamable_http_app()` 只接受路径 `/mcp` —— 不重写则网关返回 404
（`FastMCP server not found on path /mcp-servers/mcp-spike/mcp`）。

解法（server.py，已修好勿动）：外层包一个 `PathRewrite` ASGI middleware，把任意请求路径统一重写为 `/mcp`：
```python
class PathRewrite:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            scope = dict(scope, path="/mcp", raw_path=b"/mcp")
        await self.app(scope, receive, send)

uvicorn.run(PathRewrite(mcp.streamable_http_app()), host="0.0.0.0", port=8000)
```
宿主机直连 `:8000/mcp` 本身能 200；只有走网关透传路径时才需要重写。

## 最终成功调用链（spike-worker-a 钥匙）

controller 容器内（`aigw-local.agentteams.io:8080/mcp-servers/mcp-spike/mcp`）：

| 步骤 | 方法 | 结果 |
|---|---|---|
| initialize | POST | 200，响应头 `mcp-session-id` |
| notifications/initialized | POST + Session-Id | 202 |
| tools/list | POST + Session-Id | 200，`tools: [caseloop_ping]` |
| tools/call `caseloop_ping {"text":"caseloop-e2e-001"}` | POST + Session-Id | 200，`PONG | caseloop-e2e-001 | …` |

worker 容器内 mcporter 直连验证：
```
mcporter list                              → mcp-spike (1 tool, healthy)
mcporter call mcp-spike.caseloop_ping text=caseloop-worker-direct
→ {"result": "PONG | caseloop-worker-direct | …"}
```

## worker 侧 mcporter 配置与同步

- controller 源配置：`/root/agentteams-fs/agents/<name>/config/mcporter.json`
- MinIO 存储：`agents/<name>/config/mcporter.json`（controller 落盘后自动上传）
- worker 实际生效：`/root/.copaw-worker/<name>/config/mcporter.json`（周期同步拉取，本次 17:08 写入 → 17:13 出现在 worker）
- 无 `agentteams-sync` 命令；`copaw-sync` 指向缺失的 file-sync 脚本（v1.2.1 镜像瑕疵）。
  实际靠 MinIO 周期同步即可，无需重启。
- **注意**：mcporter 按 cwd 解析 `./config/mcporter.json`。worker 进程 pid1 的 cwd 是
  `/root/agentteams-fs/agents/spike-worker-a`（那里没有 config/，`mcporter list` 显示 No servers），
  而真正干活的工作目录是 `/root/.copaw-worker/spike-worker-a`（有 config/mcporter.json，
  `mcporter list` 正常）。排查"worker 看不到工具"时先看从哪个目录跑的 mcporter。

## 证据文件

- `mcp-gateway-initialize.txt`：initialize 请求/响应原文（HTTP 200 + session）
- `mcp-gateway-initialized-and-tools-list.txt`：notifications/initialized（202）+ tools/list（200，含 caseloop_ping schema）
- `mcp-gateway-tools-call.txt`：tools/call（200，`PONG | caseloop-e2e-001`）
- 房间验证：spike-worker-a 实际调用回 `PONG | caseloop-e2e-001`（详见部署 README 的 Step 3 记录）
