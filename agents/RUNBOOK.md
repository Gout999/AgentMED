# AgentMED Agent 团队安装 Runbook

> 目标：从零安装到 `agentmed-team` 可领单（派单→worker 接单→MCP 工具可用→taskflow 交接）。
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
| 凭证 | StepFun key（`STEPFUN_API_KEY`）；live B1 另需真实飞书与独立 evidence adapters |

---

## 阶段 A：平台安装与验证

### Step 0 · 凭证准备

密钥不入库、不进 git 跟踪文件，从本地安全来源导出：

```bash
export STEPFUN_API_KEY=<stepfun key>            # 真实 key，勿写入仓库
export STEPFUN_BASE_URL=https://api.stepfun.com/step_plan/v1
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

### Step 3 · 启动角色隔离 MCP projections

来自 `mcp-servers/`（FastMCP，每个含 PathRewrite，把网关透传路径重写为 `/mcp`）。按 `mcp-servers/README.md` 起：

```bash
cd mcp-servers
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/run_migrations.py            # 幂等建表（依赖 PG 已起）
```

每个进程必须显式绑定 profile、规范 worker identity、唯一 backend token 与唯一
Higress consumer。写 projection 只收对应的 `CONTROL_PLANE_ROLE_TOKEN`；只有
`mcp-agentmed-eval-gatekeeper` 收独立 `GATE_AUTHORITY_TOKEN`。不要使用共享 `.env`
向所有进程散发 token，也绝不能把通用 `CONTROL_PLANE_TOKEN` 交给 MCP 进程。

先从本地 secret manager 导出以下值（全部互异；不写入仓库）：

```bash
: "${CP_ROLE_QUALITY_OFFICER:?required}"
: "${CP_ROLE_COLLECTOR:?required only in the control-plane map}"
: "${CP_ROLE_CASE_OFFICER:?required}"
: "${CP_ROLE_ATTRIBUTIONIST:?required}"
: "${CP_ROLE_REPAIRER:?required}"
: "${CP_ROLE_GATEKEEPER:?required}"
: "${GATE_AUTHORITY_TOKEN:?required}"

export CONTROL_PLANE_ROLE_TOKENS_JSON="$(python3 - <<'PY'
import json, os
print(json.dumps({
    "quality-officer": os.environ["CP_ROLE_QUALITY_OFFICER"],
    "collector": os.environ["CP_ROLE_COLLECTOR"],
    "case-officer": os.environ["CP_ROLE_CASE_OFFICER"],
    "attributionist": os.environ["CP_ROLE_ATTRIBUTIONIST"],
    "repairer": os.environ["CP_ROLE_REPAIRER"],
    "gatekeeper": os.environ["CP_ROLE_GATEKEEPER"],
}, separators=(",", ":")))
PY
)"
export CONTROL_PLANE_BASE_URL="${CONTROL_PLANE_BASE_URL:-http://127.0.0.1:18090}"

# Compose 会 REQUIRE_MCP_ROLE_TOKENS=true；缺角色、JSON 错误或任一 authority
# token 重复时 control-plane 拒绝启动。其余 compose secrets 亦须已从安全来源导出。
docker compose -f ../deploy/compose.yaml up -d control-plane outbox-dispatcher

MCP_SECRET_DIR="${MCP_SECRET_DIR:-/tmp/agentmed-mcp-projections}"
mkdir -p "$MCP_SECRET_DIR"
chmod 700 "$MCP_SECRET_DIR"

start_projection() { # name module profile port-var port worker role-token gate-token
  local name="$1" module="$2" profile="$3" port_var="$4" port="$5"
  local worker="$6" role_token="$7" gate_token="$8" backend_token
  backend_token="$(openssl rand -hex 32)"
  umask 077
  printf 'MCP_GATEWAY_BACKEND_TOKEN=%s\n' "$backend_token" >"$MCP_SECRET_DIR/$name.env"
  env \
    MCP_TOOL_PROFILE="$profile" \
    MCP_WORKER_ID="$worker" \
    MCP_EXPECTED_CONSUMER="worker-$profile" \
    MCP_GATEWAY_BACKEND_TOKEN="$backend_token" \
    CONTROL_PLANE_BASE_URL="$CONTROL_PLANE_BASE_URL" \
    CONTROL_PLANE_ROLE_TOKEN="$role_token" \
    GATE_AUTHORITY_TOKEN="$gate_token" \
    "$port_var=$port" \
    .venv/bin/python -m "servers.$module" >"/tmp/$name.log" 2>&1 &
}

start_projection mcp-agentmed-admin-quality-officer case_admin quality-officer CASE_ADMIN_PORT 8101 quality-officer "$CP_ROLE_QUALITY_OFFICER" ""
start_projection mcp-agentmed-admin-collector case_admin collector CASE_ADMIN_PORT 8201 collector "" ""
start_projection mcp-agentmed-admin-case-officer case_admin case-officer CASE_ADMIN_PORT 8301 case-officer "" ""
start_projection mcp-agentmed-admin-attributionist case_admin attributionist CASE_ADMIN_PORT 8401 eval-runner "$CP_ROLE_ATTRIBUTIONIST" ""
start_projection mcp-agentmed-admin-repairer case_admin repairer CASE_ADMIN_PORT 8501 repairer "$CP_ROLE_REPAIRER" ""
start_projection mcp-agentmed-release-gatekeeper release_admin gatekeeper RELEASE_ADMIN_PORT 8102 gatekeeper "$CP_ROLE_GATEKEEPER" ""
start_projection mcp-agentmed-release-repairer release_admin repairer RELEASE_ADMIN_PORT 8202 repairer "$CP_ROLE_REPAIRER" ""
start_projection mcp-agentmed-eval-gatekeeper eval_runner gatekeeper EVAL_RUNNER_PORT 8103 gatekeeper "$CP_ROLE_GATEKEEPER" "$GATE_AUTHORITY_TOKEN"
start_projection mcp-agentmed-eval-attributionist eval_runner attributionist EVAL_RUNNER_PORT 8203 eval-runner "$CP_ROLE_ATTRIBUTIONIST" ""
start_projection mcp-agentmed-notify-quality-officer notification quality-officer NOTIFICATION_PORT 8104 quality-officer "" ""
start_projection mcp-agentmed-notify-case-officer notification case-officer NOTIFICATION_PORT 8204 case-officer "$CP_ROLE_CASE_OFFICER" ""
start_projection mcp-agentmed-casebase-knowledge casebase_knowledge case-officer CASEBASE_PORT 8005 case-officer "" ""
```

每个 backend 本地 `/mcp` 只接受 Higress 加入的两项凭证。未带 header 的直连必须
稳定返回 403；HTTP 400/200 不能当作这项安全验收：

```bash
for p in 8101 8201 8301 8401 8501 8102 8202 8103 8203 8104 8204 8005; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$p/mcp" -d '{}')
  [ "$code" = "403" ] && echo ":$p up and direct access denied" || echo ":$p FAIL HTTP $code"
done
```

> 若 `mcp-servers/` 已按 T4 打包成镜像，也可 `docker run` 起同一批端口。

### Step 4 · 应用 team.yaml

`agt apply` **不做拓扑排序**，文件已按「6 Worker → Team → Human」排序。

先把仓库冻结的 B1 Skill 放入 Manager 的权威 Worker Skill 目录；
`team.yaml` 六个 Worker 的 `spec.skills` 都引用同一个名字：

```bash
docker exec agentteams-manager mkdir -p /root/worker-skills/agentmed-b1-loop
docker cp agents/skills/agentmed-b1-loop/SKILL.md \
  agentteams-manager:/root/worker-skills/agentmed-b1-loop/SKILL.md
```

```bash
docker cp agents/team.yaml agentteams-controller:/tmp/agentmed-team.yaml
docker exec agentteams-controller agt apply -f /tmp/agentmed-team.yaml
```

### Step 5 · 验证团队与 Worker

```bash
docker exec agentteams-controller agt get teams
docker exec agentteams-controller agt get workers
```

期望：`agentmed-team` phase `Active`；6 个 worker 全部 `Running`（gatekeeper 等被 @mention 时从 Sleeping 唤醒，spike 已验证）。

把已在 CR 中声明的 Skill 推送到六个固定 Worker；失败或缺少任何一个文件都
不能进入 live B1：

```bash
for worker in quality-officer collector gatekeeper case-officer attributionist repairer; do
  docker exec agentteams-manager bash \
    /opt/agentteams/agent/skills/worker-management/scripts/push-worker-skills.sh \
    --worker "$worker" --no-notify
  docker exec "agentteams-worker-$worker" agentteams-sync
done
```

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

### Step 8 · 注册角色隔离 service-sources

```bash
console_api() { # method path desc body
  local code=$(curl -s -o /tmp/higress.out -w '%{http_code}' \
    -X "$1" "http://127.0.0.1:18001$2" -H 'Content-Type: application/json' -d "$4")
  echo "  [$1 $2] $3 -> HTTP $code : $(head -c 160 /tmp/higress.out)"
}
for p in 8101 8201 8301 8401 8501 8102 8202 8103 8203 8104 8204 8005; do
  console_api POST /v1/service-sources "service-source :$p" \
    "{\"type\":\"dns\",\"name\":\"mcp-src-$p\",\"domain\":\"host.docker.internal\",\"port\":$p,\"protocol\":\"http\"}"
done
```

> service source 是 DNS 型，后端 `host.docker.internal:<port>`（FastMCP 的 `/mcp` 由各 server 的 PathRewrite 承接）。

### Step 9 · 注册角色隔离 mcpServer（mcp-proxy）

用 mcp-proxy `rawConfigurations` 指向对应 service source。每个 projection 从 Step 3
读取自己的 backend token，并用 Higress `defaultUpstreamSecurity` 为所有 backend MCP
请求加入 `X-AgentMED-Gateway-Token`。key-auth 鉴权后加入的 `X-Mse-Consumer`
必须原样到达 backend；不要开启 Authorization passthrough：

```bash
register_mcp() { # name port
  local name=$1 port=$2 src="mcp-src-$port"
  local backend_token
  backend_token=$(cut -d= -f2 "$MCP_SECRET_DIR/$name.env")
  [ "${#backend_token}" -eq 64 ] || { echo "invalid backend token for $name"; return 1; }
  local body=$(python3 - "$name" "$src" "$port" "$backend_token" <<'PY'
import json,sys
name,src,port,backend_token = sys.argv[1:5]
raw = f'''server:
  name: {name}-mcp-server
  type: mcp-proxy
  transport: http
  mcpServerURL: "http://host.docker.internal:{port}/mcp"
  timeout: 5000
  passthroughAuthHeader: false
  defaultUpstreamSecurity:
    id: AgentMEDBackend
  securitySchemes:
    - id: AgentMEDBackend
      type: apiKey
      in: header
      name: X-AgentMED-Gateway-Token
      defaultCredential: "{backend_token}"
'''
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
register_mcp mcp-agentmed-admin-quality-officer       8101
register_mcp mcp-agentmed-admin-collector             8201
register_mcp mcp-agentmed-admin-case-officer          8301
register_mcp mcp-agentmed-admin-attributionist        8401
register_mcp mcp-agentmed-admin-repairer               8501
register_mcp mcp-agentmed-release-gatekeeper          8102
register_mcp mcp-agentmed-release-repairer            8202
register_mcp mcp-agentmed-eval-gatekeeper            8103
register_mcp mcp-agentmed-eval-attributionist        8203
register_mcp mcp-agentmed-notify-quality-officer      8104
register_mcp mcp-agentmed-notify-case-officer         8204
register_mcp mcp-agentmed-casebase-knowledge                8005
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

set_consumers mcp-agentmed-admin-quality-officer       worker-quality-officer
set_consumers mcp-agentmed-admin-collector             worker-collector
set_consumers mcp-agentmed-admin-case-officer          worker-case-officer
set_consumers mcp-agentmed-admin-attributionist        worker-attributionist
set_consumers mcp-agentmed-admin-repairer               worker-repairer
set_consumers mcp-agentmed-release-gatekeeper          worker-gatekeeper
set_consumers mcp-agentmed-release-repairer            worker-repairer
set_consumers mcp-agentmed-eval-gatekeeper            worker-gatekeeper
set_consumers mcp-agentmed-eval-attributionist        worker-attributionist
set_consumers mcp-agentmed-notify-quality-officer      worker-quality-officer
set_consumers mcp-agentmed-notify-case-officer         worker-case-officer
set_consumers mcp-agentmed-casebase-knowledge                worker-case-officer
```

> 授权矩阵与 SOUL 工具面一致（`agents/souls/*.md` §2）。`manager` 不在 agentmed MCP 的消费者内（最小权限；运维验证用 Step 13 的 worker key 直连）。
> `securitySchemes/defaultUpstreamSecurity` 与 `X-Mse-Consumer` 行为来自 Higress 官方
> MCP Server/key-auth 文档；AgentTeams v1.2.1 内置 plugin 仍必须由 Step 13 真机验证，
> 未验证前不得写成 live evidence。

---

## 阶段 D：验证与领单

### Step 11 · 等待 MinIO 同步 mcporter

controller 把 mcporter.json 写入 `/root/agentteams-fs/agents/<name>/config/` → 上传 MinIO `agents/<name>/config/` → worker **周期同步**到 `/root/.copaw-worker/<name>/config/`。无需重启，等待同步（数分钟）。

### Step 12 · 验证 worker 侧 mcporter（**坑 D：cwd**）

mcporter 按 cwd 解析 `./config/mcporter.json`。worker pid1 的 cwd（`/root/agentteams-fs/agents/<name>/`）**没有** config/，会显示 `No servers`——**误报**。必须在真干活目录跑：

```bash
docker exec agentteams-worker-quality-officer sh -c \
  'cd /root/.copaw-worker/quality-officer && mcporter list'
# 期望列出 mcp-agentmed-admin / mcp-agentmed-notify，状态 healthy
```

6 个 worker 逐一确认各自的 mcpServers（见 Step 10 授权矩阵）。

### Step 13 · 端到端 MCP 工具调用

用 Step 6 提取的 gateway key 直连网关，验证链路 `worker 凭证 → Higress → host.docker.internal:<port>/mcp`：

```bash
KEY=$(docker exec agentteams-controller sh -c \
  'grep WORKER_GATEWAY_KEY /data/worker-creds/quality-officer.env | cut -d= -f2 | tr -d "\"'\'' \r"')
# initialize
curl -s -X POST http://aigw-local.agentteams.io:8080/mcp-servers/mcp-agentmed-admin-quality-officer/mcp \
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

### Live B1 AgentTeams evidence boundary

`make demo-b1-live` 额外要求 `AGENTMED_B1_AGENT_TRACE_COMMAND` 与
`AGENTMED_B1_AGENT_TRACE_PUBLIC_KEY`（32-byte raw Ed25519 public key 的 base64）。命令由
AgentTeams/Matrix 凭证持有方提供，凭证不能传入 B1 runner。Runner 会用无秘密
环境调用三类阶段：

1. `phase=start`：必须真实派发 `agentmed-team` B1 task，并返回同一 Team Room、
   dispatch Matrix event、六个固定 Worker 和仓库 Skill digest；
2. pre-action role phases：六个 Worker 在对应控制面动作前分别导出 dispatch intent、
   complaint evidence、experiment plan、repair proposal、initial/post-canary gate request、
   closure intent；`phase=workorder` 由已 ack 的 repairer task 生成完整不可变 WorkOrder，提交到
   `shared/tasks/{task-id}/`，并把同一 session/task/skill 绑定的 artifact URI + digest
   导出到 runner 指定的 evidence 目录；runner 只验 binding/hash 后交控制面冻结，
   不得自行生成替代 WorkOrder；
3. `phase=complete`：从 AgentTeams taskflow/Matrix/session 导出物回读同一 session，
   为每个角色返回 task ack/submit receipt、Matrix event、Skill digest 和 Control
   Plane source IDs，并附逐角色 `task-handoff` artifact。每个 handoff 的
   `payload.product_refs` 必须精确列出第 2 步该角色的全部产物；repairer handoff
   还必须引用完全相同的 WorkOrder artifact。六个角色的 task、ack、submit receipt
   ID 必须分别唯一，不得把一个执行记录复制成六个角色。

每张 receipt 都由独立 exporter 的 Ed25519 私钥签名；runner 只持部署钉定公钥，
签名覆盖除 `attestation` 外的完整 canonical JSON。stdin/ stdout 的精确机器契约由
`scripts/run_b1_live.py::_agent_trace_from_command` 校验。任一字段缺失、角色重复、
task/ack/submit ID 跨角色重用、签名错误、Skill digest 漂移、source ID 不相等、artifact
越出 evidence 目录、digest 漂移或 adapter 失败时，live run fail closed；直接运行
Python 脚本的 trace 不能冒充 AgentTeams。Agent 产物是建议；域状态仍由
deterministic AgentMED executor 执行。completion source IDs 仅作事后权威对账，
不宣称 LLM 直接写入状态。

### Live B1 Feishu post-injection boundary

fresh B1 不能预先配置旧 `message_id` 再声称其发生于注入之后。`make demo-b1-live` 因此要求
`AGENTMED_B1_FEISHU_MESSAGE_COMMAND`：Release Controller 确认 B1 已 active 后，runner 才以
无控制面/Quality/模型/飞书秘密的环境启动该命令，并在 stdin 传入 fixture ref/digest、
injection operation ID 与 provider `injected_at`。命令负责在其独立凭证边界等待真人发出新消息，
stdout 只能返回：

```json
{"schema_version":"0.1.0","provider":"feishu","message_id":"om_..."}
```

随后 Control Plane 用自身 Feishu live adapter 抓取原消息；只有 message ID/channel/thread、
仓库冻结 complaint digest，以及 `create_time > injected_at` 全部成立，才可事务性建立 Inbox/Case。
旧消息、`hello`、超时、adapter substitution 或时间不可判定一律 fail closed，并在尚未 promote 时
触发 Quality 补偿。当前对已创建 Case/Experiment/Release 的跨进程 durable resume 尚未完成，
必须在 P0-4 状态中标为 blocker。

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

- [ ] `agt get teams` → agentmed-team Active；`agt get workers` → 6 worker Running
- [ ] 12 个 MCP projection 健康；12 个 mcpServer 代理已注册
- [ ] 每个 worker `mcporter list` 在其 cwd 下无 No servers 误报
- [ ] 用任一 worker key 直连网关 `tools/list` 返回其授权工具
- [ ] 派单演练走通：manager → leader → worker → taskflow 交接 → 产物落 `shared/tasks/`
- [ ] `agentmed-b1-loop` 已同步至六个 Worker，live trace adapter 可回读 taskflow + Matrix + Skill digest
