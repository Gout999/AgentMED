# T6a e2e 平台接线日志

> 阶段：B（团队定义）/ C（MCP 网关注册）/ D（验证与领单）
> 工作树：`caseloop-wt-t6` @ `t6/e2e`（基于 main@8e9dd0a）
> 权威步骤：`agents/RUNBOOK.md`（16 步；跳过 Step 1/2，平台已装）
> 日期：2026-08-08

## Step 0 · 凭证核对 ✅

- `~/Documents/kimi/workspace/ACL-team/.env`：`STEPFUN_API_KEY` 存在（len=65），`STEPFUN_BASE_URL=https://api.stepfun.com/v1`
- `~/agentteams-manager.env`：`AGENTTEAMS_ADMIN_USER=admin`、`AGENTTEAMS_ADMIN_PASSWORD` 存在（平台安装时落盘）
- 运行时凭证导出到仓库外 `/tmp/t6a-env.sh`（mode 600），密钥不入库
- 平台：agentteams-embedded / agentteams-manager-copaw 镜像 v1.2.1，`agt version` → `Controller: dev / Mode: embedded`
- console `http://127.0.0.1:18001/` 探活 OK

## Step 3 · 启动 5 个 MCP server ✅

环境：
- `mcp-servers/.env` 自 `.env.example` 创建（gitignored），关键改动：
  `CONTROL_PLANE_BASE_URL=http://127.0.0.1:18090`（control-plane 宿主映射 18090→8090；8090 宿主被占返回 404）
- `python3.11 -m venv .venv` + `.venv/bin/pip install -r requirements.txt`（mcp==1.12.4，fastmcp 在 mcp 包内）
- `.venv/bin/python scripts/run_migrations.py` → `[migrate] PG schema applied from .../migrations/001_init.sql`（幂等）

启动命令（cwd=`mcp-servers/`，config 从 `.env` 读）：
```bash
nohup .venv/bin/python -m servers.case_admin          > var/mcp-case-admin.log    2>&1 &
nohup .venv/bin/python -m servers.release_admin       > var/mcp-release-admin.log 2>&1 &
nohup .venv/bin/python -m servers.eval_runner         > var/mcp-eval-runner.log   2>&1 &
nohup .venv/bin/python -m servers.notification        > var/mcp-notification.log  2>&1 &
nohup .venv/bin/python -m servers.casebase_knowledge  > var/mcp-casebase-knowledge.log 2>&1 &
```

探活：
```
:8001 up (HTTP 400)   # mcp-case-admin
:8002 up (HTTP 400)   # mcp-release-admin
:8003 up (HTTP 400)   # mcp-eval-runner
:8004 up (HTTP 400)   # mcp-notification
:8005 up (HTTP 400)   # mcp-casebase-knowledge
```
- HTTP 400 = FastMCP 对空 body 初始化请求按协议响应（RUNBOOK 判定标准）
- 5 个日志均 `Application startup complete` / `Uvicorn running on http://0.0.0.0:8xxx`

结论：5 个 MCP server 全部就绪，等待 Step 4-10 接线。

## Step 4-5 · 应用 team.yaml 并验证团队 ✅

应用（RUNBOOK Step 4）：
```bash
docker cp agents/team.yaml agentteams-controller:/tmp/caseloop-team.yaml
docker exec agentteams-controller agt apply -f /tmp/caseloop-team.yaml
```
输出：`worker/quality-officer … worker/repairer` 6 个 created + `team/caseloop-team created` + `human/caseloop-approver created`（8/8）

中间态观察：刚 apply 后 team 短暂 `Failed`（0/0，6 worker Pending），容器逐个拉起（agentteams-worker-{quality-officer,collector,gatekeeper,case-officer,attributionist,repairer}），约 45s 后收敛。

最终状态：
```
===TEAMS===
caseloop-team  Active  quality-officer  collector,gatekeeper,case-officer,attributionist,repairer  5/5
spike-team     Active  spike-leader     spike-worker-a,spike-worker-b  2/2   # 不动
===WORKERS===
collector/gatekeeper/case-officer/attributionist/repairer  Running  caseloop-team
quality-officer  Running  caseloop-team  (Team Leader)
```

SOUL 同步校验：
```bash
python3 agents/scripts/verify-soul-sync.py   # → soul-sync OK
```

结论：caseloop-team Active 5/5（非 leader worker），6 worker 全部 Running，SOUL 与 `agents/souls/*.md` 无漂移。

## Step 6-10 · MCP 网关注册（S0-004 路径） ✅（两处 RUNBOOK 修正）

### Step 6 · worker gateway key
```bash
docker exec agentteams-controller sh -c 'grep WORKER_GATEWAY_KEY /data/worker-creds/quality-officer.env | cut -d= -f2 | tr -d "\"'\'' \r"'
```
- quality-officer key 提取成功（64 hex），`/data/worker-creds/` 下 6 个 caseloop worker cred 齐全

### Step 7 · Higress console 登录
```bash
curl -s -c /tmp/higress-cookie -X POST http://127.0.0.1:18001/session/login \
  -H 'Content-Type: application/json' -d '{"username":"admin","password":"<masked>"}'
# 响应 JSON（非 HTML）= 登录成功；cookie 含 _hi_sess；GET /v1/mcpServer 鉴权通过
```

### Step 8 · 5 个 service-sources（HTTP 201 全过）
```bash
POST /v1/service-sources {"type":"dns","name":"mcp-src-<port>","domain":"host.docker.internal","port":<port>,"protocol":"http"}
# mcp-src-8001 … mcp-src-8005 全部 201；GET /v1/service-sources 确认持久化
```

### Step 9 · 5 个 mcpServer（**RUNBOOK 修正 1：rawConfigurations 双重编码**）
- 按 RUNBOOK 原样 PUT → 全部 **400** `Error occurs when parsing raw configurations`
- 根因：RUNBOOK `register_mcp` 先 `json.dumps('server:\n...')` 得 JSON 字符串，再塞进 body 的
  `json.dumps({...})` → rawConfigurations 被**双重 JSON 编码**；官方 `setup-mcp-proxy.sh` 是
  `printf | jq -Rs .`（单编码）+ `--argjson` 嵌入。
- 修正：rawConfigurations 传纯 YAML 字符串，body 单次编码 → 全部 HTTP 200

### Step 10 · consumers（**RUNBOOK 修正 2：service-source 命名隐藏坑**）
- 初配后网关直连 **503**。排查 controller 容器内 /data/ingresses/：
  `higress.io/destination: mcp-src-.dns:8001` —— source 名被 console 剥离尾部 `-8001`（把 `-<数字>`
  后缀当端口），而 McpBridge 里实际 registry 名是 `mcp-src-8001` → destination 落空 → 503。
- 对照 mcp-spike（正常）：destination `spike-proxy.dns:8000`，source 名 `spike-proxy` 无数字后缀。
- 修正：按官方命名惯例建无数字后缀 source（`case-admin-proxy` / `release-admin-proxy` /
  `eval-runner-proxy` / `notification-proxy` / `casebase-knowledge-proxy`），重注册 5 个 mcpServer
  指向它们 → 5 个 destination 注解全部正确。
- **附带坑（全量替换语义）**：重注册 mcpServer（body 带 `allowedConsumers:["manager"]`）会把
  consumers 重置回仅 manager —— 需在 mcpServer 注册**之后**再 PUT 全量 consumers。
- 最终 consumers 矩阵（RUNBOOK Step 10，console 自动附带 manager）：
  - mcp-case-admin: 6 worker 全量
  - mcp-release-admin: gatekeeper, repairer, quality-officer
  - mcp-eval-runner: gatekeeper, attributionist
  - mcp-notification: quality-officer, case-officer
  - mcp-casebase-knowledge: 6 worker 全量

## Step 13 · 端到端 MCP 工具调用 ✅

用 quality-officer gateway key 从 controller 容器内直连网关
`http://aigw-local.agentteams.io:8080/mcp-servers/mcp-case-admin/mcp`：

| 步骤 | 请求 | 结果 |
|---|---|---|
| initialize | POST | **200**，响应头 `mcp-session-id: c64a995e…`，serverInfo mcp-case-admin 1.12.4 |
| notifications/initialized | POST + SID | **202** |
| tools/list | POST + SID | **200**，8 工具（case.list/get/timeline/claim/submit_suggestion/escalate + app.logs/feedback，对拍 spec §9.3 全齐） |
| tools/call `case.list {}` | POST + SID | **200**，返回真实数据 2 个 DISPATCHED case（经 control-plane→PG） |

5 server 网关可见性（各用授权 worker key，完整握手 initialize→initialized→tools/list）：
- mcp-case-admin（quality-officer）: 8 tools
- mcp-release-admin（gatekeeper）: 9 tools（workorder.draft/gate.submit/…/release.request_rollback）
- mcp-eval-runner（gatekeeper）: 6 tools（gate.run/report、experiment.*、probe.freeze）
- mcp-casebase-knowledge（case-officer）: 5 tools（kb.*）
- mcp-notification（case-officer）: 4 tools（feishu.*、matrix.log）

> 注意：FastMCP 1.12.4 必须发 `notifications/initialized` 后才能 tools/list（缺则 400 / -32602）。
> 网关对 `aigw-local.agentteams.io:8080` 只在容器网络内可达；宿主侧用 `docker exec agentteams-controller` 测试。

## Step 11-12 · MinIO 同步 + worker 侧 mcporter ✅

- controller 把 mcporter.json 落 MinIO `agents/<name>/config/`（6 worker 全有，xl.meta 单文件存储）
- worker 已同步到真干活目录 `/root/.copaw-worker/<name>/config/mcporter.json`，每个含对应
  `mcpServers`（url=`aigw-local.agentteams.io:8080/mcp-servers/<name>/mcp` + `Authorization: Bearer <worker 自有 key>`），与 team.yaml 完全一致

`mcporter list`（cwd=`/root/.copaw-worker/<name>/`，RUNBOOK Step 12 坑 D 规避）：
```
quality-officer : mcp-notification(4) + mcp-case-admin(8)   = 2 healthy
collector       : mcp-case-admin(8)                          = 1 healthy
gatekeeper      : mcp-eval-runner(6) + mcp-release-admin(9)  = 2 healthy
case-officer    : mcp-case-admin(8) + mcp-notification(4) + mcp-casebase-knowledge(5) = 3 healthy
attributionist  : mcp-eval-runner(6) + mcp-case-admin(8)     = 2 healthy
repairer        : mcp-case-admin(8) + mcp-release-admin(9)   = 2 healthy
```
- 全部 healthy，无 `No servers` 误报；工具数与 Step 10 授权矩阵一致

worker 侧真实调用（闭环验证）：
```bash
mcporter call mcp-case-admin case.list   # 注意 selector 用空格分隔 server 与含点工具名
# 返回真实 DISPATCHED case 数据（与 Step 13 一致）
```

## Step 14 · 派单演练（团队可领单 + taskflow 交接）✅

派单通道：caseloop-team 房间 `!sxPUX2qmXTlXmG5WL3:matrix-local.agentteams.io:18080`
（`@caseloop-approver` 为房间成员、代表人类侧派单；`@manager` 不在团队房间，见遗留问题 L1）

### 演练 1 · 直接响应（最小任务）
- 派单：`@quality-officer 请列出当前 case 列表（用 mcp-case-admin 的 case.list 工具）…`
- quality-officer 直接用 `mcporter call mcp-case-admin.case.list`（leader 持有该工具），回报 2 条
  DISPATCHED case 摘要表格。
- 观察：leader 对最小读任务直接完成（合理），未走 taskflow。

### 演练 2 · 委派链路（RUNBOOK Step 14 完整流）
- 派单：`@quality-officer 请把以下取证任务通过 taskflow 委派给 collector（@collector…）：让 collector 用 mcp-case-admin 的 app.logs 采集 demo-app 最近 10 条日志…`
- 时序（房间消息）：
  1. quality-officer `delegate_task` → 建 Project `t6a-drill-20260808` + Task `t6a-drill-20260808-01` 委派给 collector
  2. collector `ack_task` 接单
  3. collector 调 `mcp-case-admin.app.logs`（limit=10）→ 产物写
     `shared/tasks/t6a-drill-20260808-01/evidence-summary.md`
  4. collector `submit_task` → `status: submitted, synced: true, verified: true`
  5. quality-officer `check_task` → 状态 submitted，结果 SUCCESS，回报「路径 + 摘要」
- 产物落位（三处同步）：
  - quality-officer 工作区 `…/workspaces/default/shared/tasks/t6a-drill-20260808-01/evidence-summary.md`
  - collector 工作区同名
  - **团队共享** MinIO `teams/caseloop-team/shared/tasks/t6a-drill-20260808-01/{spec,result,evidence-summary}.md`
- 产物内容：10 条 demo-app 日志，request_id 列表完整，`证据缺口: false`（真实数据）

### 交叉验证（另一 worker 经 filesync 读团队共享产物）
- 派单：`@gatekeeper 请用 filesync（action=pull）读取 shared/tasks/t6a-drill-20260808-01/evidence-summary.md…`
- gatekeeper（未参与任务）`filesync pull` 成功，本地同步出该文件，回报存在 + 摘要
  （工具路径 mcp-case-admin.app.logs / 应用名 demo-app / 10 条日志）

### 观察记录
- **RPM 限流（S0-002 复现）**：quality-officer 处理委派时 00:27:46 遇 429
  `request limited RPM reached, current: 11, limit: 10` → copaw RetryChatModel 自动退避重试成功
  （serial 纪律有效，无需人工干预）。
- **mcporter selector 坑**：含点工具名 `case.list` 用 `mcp-case-admin.case.list` 会误解析
  （Unknown tool: case）；空格分隔 `mcporter call mcp-case-admin case.list` 正常。
- **copaw-sync 指向缺失脚本**（S0-003 已知 v1.2.1 瑕疵），filesync MCP 工具正常可用。

结论：caseloop-team 真实可领单——派单→leader 委派→worker 接单→MCP 工具→产物落团队共享→
taskflow 提交/校验→跨 worker filesync 可读，全链路证据闭环。

## 最终状态对照（主控复核项）

| 验收项 | 结果 |
|---|---|
| `agt get teams` → caseloop-team **Active**，5/5 ready | ✅ |
| `agt get workers` → 6 worker 全部 **Running**（quality-officer 为 leader） | ✅ |
| 5 个 MCP server 网关可见 + worker 侧 mcporter 列工具 | ✅（Step 12/13 全表） |
| 真实端到端工具调用（Step 13） | ✅ `case.list` 经网关返回真实数据；worker 侧 `mcporter call` 亦通过 |
| 派单演练（Step 14） | ✅ 委派链路 + taskflow 交接 + 产物落团队共享 + 跨 worker 可读 |
| evidence/phase1/t6a-wiring-log.md | ✅ 本文件 |
| spike-team / spike-* worker 未动 | ✅ 保持 Running，未重装未删除 |

运行态体检：caseloop-team Active、6 worker Running、control-plane `:18090/healthz` ok、
MCP server :8001-8005 全 up、demo-app API 正常（/v2/logs 需 token 属预期，MCP 工具带
conformance token 代理调用成功）。演练仅用读工具（case.list / app.logs），未注入故障，无需 reset。

## 遗留问题清单

- **L1 · 派单通道**：`@manager` 不在 caseloop-team 房间（v1.2.1 的 manager 邀请/restore 非幂等，
  见 S0-001）。本次派单由房间成员 `@caseloop-approver`（Human CR，代表人类侧）发出。若要严格从
  "manager 房间"派单，需手工把 manager 加入团队房间或等上游修复。
- **L2 · RUNBOOK Step 9 双重编码 bug**：`register_mcp` 的 `rawConfigurations` 会被双重 JSON 编码
  导致 400，须按官方 `setup-mcp-proxy.sh`（`jq -Rs` + `--argjson`）单编码。RUNBOOK 文本待修正。
- **L3 · service-source 命名坑**：source 名带 `-<数字>` 后缀（如 `mcp-src-8001`）会被 console
  当作端口后缀剥离成 `mcp-src-`，导致网关 503。须用无数字后缀命名（官方惯例 `<name>-proxy`）。
  RUNBOOK Step 8 的 `mcp-src-$p` 命名待修正。
- **L4 · consumers 全量替换副作用**：重注册 mcpServer（body 带 `allowedConsumers`）会把已授权
  consumers 重置回仅 manager；mcpServer 注册必须最后统一灌 consumers。
- **L5 · mcporter selector**：含点工具名需空格分隔（`mcporter call mcp-case-admin case.list`），
  `server.tool` 形式对含点工具名误解析。
- **L6 · RPM 限流**：StepFun 实测 limit=10/min（S0-002），多 worker 并发即 429；copaw 自动退避
  重试可恢复，但大规模并发仍需用户决策（升档/降并发）。
- **L7 · 遗留脏记录**：Step 9 初次 400 前的 `mcp-src-*` service-sources（8001-8005）与
  旧 `mcp-server-mcp-*.internal` 记录已被正确版本覆盖；`mcp-src-8001..8005` source 仍残留于
  McpBridge（无害，未影响路由，可后续清理）。
