# CaseLoop Agent 团队安装 Runbook

> 目标：从零安装到 `caseloop-team` 可领单（派单→worker 接单→MCP 工具可用→taskflow 交接）。
> 钉版：AgentTeams **v1.2.1**。团队定义：`agents/team.yaml`（6 Worker + Team + Human）。
> 依据：`wiki/platform-agentteams.md`（六坑）、`evidence/spike/S0-001/003/004`、`deploy/README.md`、`mcp-servers/README.md`。
> 总步骤：16 步（Step 0–15）。

---

## 前置

| 项 | 要求 |
|----|------|
| Docker Desktop | 完全启动，M 系列 ≥ 4.39.0；Docker VM 内存 ≥ 8 GB |
| 端口空闲 | 18080 / 18001 / 18088 / 18888 / 13000 |
| 网络 | 系统/浏览器代理放行 `127.0.0.1` 与 `*-local.agentteams.io` |
| 凭证 | StepFun key（`STEPFUN_API_KEY`）、飞书 mock 不需真凭证 |

---

## 阶段 A：平台安装与验证

### Step 0 · 凭证准备

密钥不入库、不进 git 跟踪文件，从本地安全来源导出：

```bash
export STEPFUN_API_KEY=<stepfun key>            # 真实 key，勿写入仓库
export STEPFUN_BASE_URL=https://api.stepfun.com/v1
export ADMIN_PASSWORD='<本地演示管理员密码，自行设定，≥8位>'
```

### Step 1 · 安装 AgentTeams v1.2.1

```bash
AGENTTEAMS_NON_INTERACTIVE=1 \
AGENTTEAMS_LANGUAGE=zh \
AGENTTEAMS_VERSION=v1.2.1 \
AGENTTEAMS_LLM_PROVIDER=openai-compat \
AGENTTEAMS_OPENAI_BASE_URL="$STEPFUN_BASE_URL" \
AGENTTEAMS_LLM_API_KEY="$STEPFUN_API_KEY" \
AGENTTEAMS_DEFAULT_MODEL=step-3.7-flash \
AGENTTEAMS_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
bash <(curl -sSL https://raw.githubusercontent.com/agentscope-ai/AgentTeams/main/install/agentteams-install.sh)
```

**坑 1（避开）**：**不要设** `AGENTTEAMS_DATA_DIR`——安装脚本把它当 docker volume 名用，传绝对路径直接报错。
**坑 2（避开）**：若存在旧 `~/agentteams-manager.env`，先 `bash agentteams-install.sh uninstall` 并删掉该 env 文件，否则非交互模式也会弹"升级方式"菜单卡住。

### Step 2 · 验证平台

```bash
docker ps | grep -E "agentteams-controller|agentteams-manager"
docker exec agentteams-controller agt version          # 期望 v1.2.1
curl -sf http://127.0.0.1:18001/ >/dev/null && echo "Higress console OK"
```

---

## 阶段 B：团队定义

### Step 3 · 启动 5 个 MCP server

来自 `mcp-servers/`（FastMCP，每个含 PathRewrite，把网关透传路径重写为 `/mcp`）。按 `mcp-servers/README.md` 起：

```bash
cd mcp-servers
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/run_migrations.py            # 幂等建表（依赖 PG 已起）
```

| Server | 端口 | 启动命令 |
|--------|------|---------|
| mcp-case-admin | 8001 | `.venv/bin/python -m servers.case_admin` |
| mcp-release-admin | 8002 | `.venv/bin/python -m servers.release_admin` |
| mcp-eval-runner | 8003 | `.venv/bin/python -m servers.eval_runner` |
| mcp-notification | 8004 | `.venv/bin/python -m servers.notification` |
| mcp-casebase-knowledge | 8005 | `.venv/bin/python -m servers.casebase_knowledge` |

每个 server 本地端点：`http://127.0.0.1:<port>/mcp`（5 个 server 中仅 mcp-notification 另有 `/healthz`；统一用 MCP 端点探活）。验证：

```bash
for p in 8001 8002 8003 8004 8005; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$p/mcp" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{}')
  [ "$code" != "000" ] && echo ":$p up (HTTP $code)" || echo ":$p DOWN"
done
```

> HTTP 000 = 连接拒绝（server 没起来）；4xx = server 已响应（FastMCP 对空 body 的初始化请求按协议回 400/200 均正常）。

> 若 `mcp-servers/` 已按 T4 打包成镜像，也可 `docker run` 起同一批端口。

### Step 4 · 应用 team.yaml

`agt apply` **不做拓扑排序**，文件已按「6 Worker → Team → Human」排序。

```bash
docker cp agents/team.yaml agentteams-controller:/tmp/caseloop-team.yaml
docker exec agentteams-controller agt apply -f /tmp/caseloop-team.yaml
```

### Step 5 · 验证团队与 Worker

```bash
docker exec agentteams-controller agt get teams
docker exec agentteams-controller agt get workers
```

期望：`caseloop-team` phase `Active`；6 个 worker 全部 `Running`（gatekeeper 等被 @mention 时从 Sleeping 唤醒，spike 已验证）。

**SOUL 同步校验**（防 `team.yaml` 内联 soul 与 `agents/souls/*.md` 漂移）：

```bash
python3 agents/scripts/verify-soul-sync.py   # 期望输出 soul-sync OK
```

---

## 阶段 C：MCP 网关注册（S0-004 路径）

### Step 6 · 提取 worker gateway key（**坑 B：引号 / CRLF**）

controller 在 `/data/worker-creds/<name>.env` 落 `WORKER_GATEWAY_KEY="<64 hex>"`（带双引号 + CRLF）。拼 `Authorization` 前必须 `tr -d` 掉双引号/单引号/空格/`\r`：

```bash
# 逐个 worker 取 key（示例取 quality-officer）
docker exec agentteams-controller sh -c \
  'grep WORKER_GATEWAY_KEY /data/worker-creds/quality-officer.env | cut -d= -f2 | tr -d "\"'\'' \r"'
```

> 团队定义里 `mcpServers` 的 Authorization 由 controller 自动注入（`GenerateMcporterConfig`），**不需要**手工配 key；此步提取的 key 仅用于 Step 13 的网关直连验证。

### Step 7 · 登录 Higress 控制台拿会话 cookie

控制台在 **controller :18001**（宿主映射同端口）；官方 `setup-mcp-proxy.sh` 硬编码 `127.0.0.1:8001` 连不上，**只能手工作业 console API**（**坑 A**）。

```bash
export COOKIE=/tmp/higress-cookie
curl -s -c "$COOKIE" -X POST http://127.0.0.1:18001/session/login \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"admin\",\"password\":\"$ADMIN_PASSWORD\"}"
```

> 用户名默认 `admin`（`AGENTTEAMS_ADMIN_USER`）；响应非 HTML 即登录成功。

### Step 8 · 注册 5 个 service-sources

```bash
console_api() { # method path desc body
  local code=$(curl -s -o /tmp/higress.out -w '%{http_code}' \
    -X "$1" "http://127.0.0.1:18001$2" -H 'Content-Type: application/json' -d "$4")
  echo "  [$1 $2] $3 -> HTTP $code : $(head -c 160 /tmp/higress.out)"
}
for p in 8001 8002 8003 8004 8005; do
  console_api POST /v1/service-sources "service-source :$p" \
    "{\"type\":\"dns\",\"name\":\"mcp-src-$p\",\"domain\":\"host.docker.internal\",\"port\":$p,\"protocol\":\"http\"}"
done
```

> service source 是 DNS 型，后端 `host.docker.internal:<port>`（FastMCP 的 `/mcp` 由各 server 的 PathRewrite 承接）。

### Step 9 · 注册 5 个 mcpServer（mcp-proxy）

用 mcp-proxy `rawConfigurations` 指向对应 service source（**坑 C 前置**：FastMCP 只认 `/mcp`，网关把 `/mcp-servers/<name>/mcp` 原样透传，靠 server 内 PathRewrite 重写，网关侧不改路径）：

```bash
register_mcp() { # name port
  local name=$1 port=$2 src="mcp-src-$port"
  local raw=$(python3 -c "import json,sys;print(json.dumps('server:\n  name: ${name}-mcp-server\n  type: mcp-proxy\n  transport: http\n  mcpServerURL: \"http://host.docker.internal:${port}/mcp\"\n  timeout: 5000\n'))")
  local body=$(python3 - "$name" "$src" "$port" "$raw" <<'PY'
import json,sys
name,src,port,raw = sys.argv[1:5]
print(json.dumps({
  "name": name, "description": f"{name} MCP Proxy Server (http)",
  "type": "OPEN_API", "rawConfigurations": raw, "mcpServerName": name,
  "domains": ["aigw-local.agentteams.io"],
  "services": [{"name": f"{src}.dns", "port": int(port), "weight": 100}],
  "consumerAuthInfo": {"type": "key-auth", "enable": True, "allowedConsumers": ["manager"]},
}))
PY
)
  console_api PUT /v1/mcpServer "mcpServer $name" "$body"
}
register_mcp mcp-case-admin        8001
register_mcp mcp-release-admin     8002
register_mcp mcp-eval-runner       8003
register_mcp mcp-notification      8004
register_mcp mcp-casebase-knowledge 8005
```

### Step 10 · 配置 consumers（**坑 C：全量替换**）

`PUT /v1/mcpServer/consumers` 是**全量替换语义**——每次带完整列表，漏发已授权 consumer 会被踢出（403 = 未授权 Consumer）。Consumer 名为 `worker-<name>`（对应 `/data/worker-creds/<name>.env` 的 key）。

```bash
set_consumers() { # name consumer...
  local name=$1; shift
  local cl=$(python3 -c "import json,sys;print(json.dumps(sys.argv[1:]))" "$@")
  console_api PUT /v1/mcpServer/consumers "consumers $name" \
    "{\"mcpServerName\":\"$name\",\"consumers\":$cl}"
}

set_consumers mcp-case-admin \
  worker-quality-officer worker-collector worker-gatekeeper \
  worker-case-officer worker-attributionist worker-repairer
set_consumers mcp-release-admin \
  worker-gatekeeper worker-repairer worker-quality-officer
set_consumers mcp-eval-runner \
  worker-gatekeeper worker-attributionist
set_consumers mcp-notification \
  worker-quality-officer worker-case-officer
set_consumers mcp-casebase-knowledge \
  worker-quality-officer worker-collector worker-gatekeeper \
  worker-case-officer worker-attributionist worker-repairer
```

> 授权矩阵与 SOUL 工具面一致（`agents/souls/*.md` §2）。`manager` 不在 caseloop MCP 的消费者内（最小权限；运维验证用 Step 13 的 worker key 直连）。

---

## 阶段 D：验证与领单

### Step 11 · 等待 MinIO 同步 mcporter

controller 把 mcporter.json 写入 `/root/agentteams-fs/agents/<name>/config/` → 上传 MinIO `agents/<name>/config/` → worker **周期同步**到 `/root/.copaw-worker/<name>/config/`。无需重启，等待同步（数分钟）。

### Step 12 · 验证 worker 侧 mcporter（**坑 D：cwd**）

mcporter 按 cwd 解析 `./config/mcporter.json`。worker pid1 的 cwd（`/root/agentteams-fs/agents/<name>/`）**没有** config/，会显示 `No servers`——**误报**。必须在真干活目录跑：

```bash
docker exec agentteams-worker-quality-officer sh -c \
  'cd /root/.copaw-worker/quality-officer && mcporter list'
# 期望列出 mcp-case-admin / mcp-notification，状态 healthy
```

6 个 worker 逐一确认各自的 mcpServers（见 Step 10 授权矩阵）。

### Step 13 · 端到端 MCP 工具调用

用 Step 6 提取的 gateway key 直连网关，验证链路 `worker 凭证 → Higress → host.docker.internal:<port>/mcp`：

```bash
KEY=$(docker exec agentteams-controller sh -c \
  'grep WORKER_GATEWAY_KEY /data/worker-creds/quality-officer.env | cut -d= -f2 | tr -d "\"'\'' \r"')
# initialize
curl -s -X POST http://aigw-local.agentteams.io:8080/mcp-servers/mcp-case-admin/mcp \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"runbook-verify","version":"0.1"}}}'
```

> 响应头应有 `mcp-session-id`。随后按 `notifications/initialized` → `tools/list` → `tools/call` 依次验证（参考 `evidence/spike/mcp-gateway-*.txt`）。工具名与 spec §9 对拍（如 `case.list` / `case.get`）。

### Step 14 · 派单演练（团队可领单）

在 Manager 房间 @`quality-officer` 派一个最小任务（如"列出 case 列表并回报"）：

1. manager 房间 @`quality-officer`（完整 Matrix ID）下达任务；
2. quality-officer 领单后 `taskflow(ack_task)`，拆解并 @ 对应 worker（如 @`collector`）；
3. worker `taskflow(ack_task)` → 调 MCP 工具 → 产物写 `shared/tasks/{task-id}/` → `taskflow(submit_task)`；
4. 房间只出现「路径 + 摘要」；交叉验证另一 worker 能经 taskflow 读取 `shared/tasks/{task-id}/` 产物（S0-003 语义）。

**串行纪律**：同一时刻活跃 worker ≤2（D-001）；若 worker 报 `429 RATE_LIMITED`，指数退避重试，不并发抢任务。

---

## 阶段 E：运维注意事项

### Step 15 · 缩容 / 删除对账（S0-001）

- `agt delete team` 会**假成功**：CLI 报 deleted 但 CR 仍 Active。**不能以 CR 消失为删除成功依据**。
- 删除后回查四样齐全才算成功：`agt get teams/workers` 目标消失（或登记 leader-only 残留）+ 容器停止/删除 + Matrix 房间归档/解散 + MinIO 用户/凭证回收。
- 摘除顺序：**先 PUT 移除全部普通 worker（workerMembers 只留 leader）→ 再处理 leader**。leader 摘除是上游死结（必经"restore Manager to personal room"，invite 非幂等 403），需手工 Matrix 干预或等上游修复。
- 残留 CR 不阻塞异名团队创建（现场处置模式：容器先 stop 释放资源，残留 CR 登记在案）。

---

## 附录 A：六坑速查

| # | 坑 | 规避 |
|---|----|------|
| 1 | `AGENTTEAMS_DATA_DIR` 传绝对路径被当 volume 名报错 | 不设该变量 |
| 2 | 旧 `~/agentteams-manager.env` 触发升级菜单 | 先 uninstall + 删 env 再装 |
| 3 | 官方 `setup-mcp-proxy.sh` 硬编码 console `:8001` 连不上 | 手工作业 console API（本 runbook Step 7-10） |
| 4 | worker gateway key 带引号/`\r`，key-auth 401 | `tr -d "\"'\'' \r"`（Step 6） |
| 5 | consumers 全量替换，漏发即踢 | 每次带完整列表（Step 10） |
| 6 | mcporter 按 cwd 解析 config，pid1 cwd 无 config 显示 No servers | 在 `/root/.copaw-worker/<name>/` 跑（Step 12） |

## 附录 B：团队可领单验收清单

- [ ] `agt get teams` → caseloop-team Active；`agt get workers` → 6 worker Running
- [ ] 5 个 MCP server 健康；5 个 mcpServer 代理已注册
- [ ] 每个 worker `mcporter list` 在其 cwd 下无 No servers 误报
- [ ] 用任一 worker key 直连网关 `tools/list` 返回其授权工具
- [ ] 派单演练走通：manager → leader → worker → taskflow 交接 → 产物落 `shared/tasks/`
