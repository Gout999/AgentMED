# AgentMED control-plane

确定性控制面（Case/Release Controller）：**LLM 不是状态与权限的权威源**。状态权威源 = PG
（aggregates/events/inbox/outbox/leases/audit/trust ledger），事件溯源 + 状态机 + CAS 乐观并发 + lease/fencing。

- 语言/框架：Python 3.11+ · FastAPI · SQLAlchemy 2 · Alembic
- 依赖：见 `requirements.txt`（钉版本）
- 对齐契约：`contracts/events/`（events.yaml + state-machines.yaml）、`contracts/schemas/`、`docs/spec.md §7`

## 目录

```
control-plane/
  app/
    api/           REST 路由：cases / experiments / changesets / releases / notifications
    services/      Case Controller / Release Controller / Notification / Experiment / ChangeSet
    models/        权威状态、outbox receipt 与 append-only Trust ORM
    workers/       Phase 1 固定单 outbox dispatcher（非动态扩缩容）
    quality/       Quality API v2 客户端（写面唯一入口，按 contracts/quality-api/openapi.yaml）
    utils/         JCS(SHA-256) / ULID id / PII 脱敏
  alembic/         001 initial through current head 012 (V5 event envelope)
  tests/           unit（SQLite 内存） + integration（compose PG 真跑）
```

## 快速开始

### 1) 起 Postgres

```bash
cp ../deploy/.env.example ../deploy/.env
# Fill every required value in ../deploy/.env before starting the stack.
docker compose --env-file ../deploy/.env -f ../deploy/compose.yaml up -d postgres
```

### 2) 建 venv 并装依赖

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3) 初始化数据库

> 迁移 012 对已经存在的旧 V5 authority/event history 会 fail closed；它不会把旧事件重标
> 为 V5 major-2。未接受的 disposable 开发库应按明确的 rebuild-only 路径重建；需要保留
> 的历史必须先 export、验证并通过受审计的 replay/recovery 迁移。不要在未知 populated
> V5 数据上直接把 `upgrade head` 当作无风险操作。

```bash
.venv/bin/alembic upgrade head
```

### 4) 起服务

方式 A：宿主机 uvicorn（本地开发）

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090
```

另起固定一个 outbox dispatcher（Phase 1 不声称动态扩缩容）：

```bash
.venv/bin/python -m app.workers.outbox
```

`NOTIFICATION_ADAPTER=disabled` 是默认 fail-closed 配置。只有 contract/replay
测试可显式使用 `NOTIFICATION_ADAPTER=feishu-mock`；这不代表 live Feishu 已通过。

方式 B：compose 容器（e2e 编排 / 统一部署）

```bash
# 在仓库根目录；仅起 control-plane（自动拉 postgres 依赖 + 构建镜像）
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build control-plane

# 宿主 8090 被其他服务占用，compose 映射到 18090
curl http://127.0.0.1:18090/healthz     # {"status":"ok",...}
curl http://127.0.0.1:18090/v1/cases    # {"items":[],...}

# 重启（alembic 幂等：已在 head 时 upgrade 为 no-op）
docker compose --env-file deploy/.env -f deploy/compose.yaml restart control-plane
```

容器入口先 `alembic upgrade head` 再起 uvicorn；`DATABASE_URL` 由 compose
注入 compose 内部网络地址（`postgres:5432/control_plane`），`QUALITY_API_BASE_URL`
指向 `demo-app:8080`。镜像构建见 `Dockerfile`，非 root 运行。

配置全部走环境变量，模板见 `.env.example`（复制为 `.env` 后填写；`.env` 不入库）。

V5-1A/B/C 的本地 First System Case 使用独立的三阶段管理入口，不通过 public HTTP
签发或轮换凭证；可执行步骤、凭证切换点与人工保管边界见
[`V5_FIRST_CASE_LOCAL.md`](V5_FIRST_CASE_LOCAL.md)。

### 5) 跑测试

```bash
# unit（无外部依赖，SQLite 内存）
.venv/bin/python -m pytest tests/unit

# 全部（integration 需 compose PG 已起）
.venv/bin/python -m pytest

# 只看 integration 五场景
.venv/bin/python -m pytest tests/integration -v
```

## REST 接口清单

### V5-1 current overlay（`/api/v2`，显式 major）

这些 route 只覆盖当前 V5-1A/B/C worktree overlay，不表示 stage 已 DONE。调用者必须使用
V2 public credential、workspace header 和对应 scope；凭证签发/轮换不属于 public HTTP。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v2/capabilities` | 按当前 principal/scope 过滤的 V5 allowlist |
| POST/GET | `/api/v2/applications` · `/applications/{id}` | Application 注册/读取 |
| POST/GET | `/api/v2/environments` · `/environments/{id}` | Environment 注册/读取 |
| POST/GET | `/api/v2/system-components` · `/system-components/{id}` | Component 注册/读取 |
| POST/GET | `/api/v2/dependency-edges` · `/dependency-edges/{id}` | Edge 记录/读取 |
| POST | `/api/v2/system-manifests:import` | trusted one-shot atomic bootstrap |
| GET | `/api/v2/system-versions/{id}` · `/system-versions:diff` | exact VersionSet 读取/比较 |
| POST/GET | `/api/v2/cases/{id}:bind-application` · `/cases/{id}/application-binding` | Case/Application exact binding |
| POST/GET | `/api/v2/cases/{id}:propose-acceptance-criteria` · `/cases/{id}/acceptance-criteria` | Acceptance proposal/read |
| POST | `/api/v2/acceptance-criteria/{id}:confirm` | fresh human reauth confirmation；仍非 V5-4 executable |

Standalone `system-versions.record`、V5-2+、Public MCP/A2A/SDK、approval/release 仍未实现，
不得从内部 V1 release route 或本地 bootstrap 推导为 V5 public capability。

### Case Controller（`/v1`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/complaints` | 投诉接入（webhook\|poll；inbox 去重；PII 入口脱敏） |
| GET | `/v1/cases` | 列 case（`?state=&limit=&cursor=`） |
| GET | `/v1/cases/{case_id}` | 读 case（state/revision/payload/event_count） |
| POST | `/v1/cases/{case_id}/claim` | Worker 领单（lease 60s + fencing token） |
| POST | `/v1/cases/{case_id}/heartbeat` | 心跳续租（需 worker_id + fencing_token） |
| POST | `/v1/cases/{case_id}/reclaim` | lease 过期回收（case.worker_lost → OPEN） |
| POST | `/v1/cases/{case_id}/transitions` | 通用状态迁移（event_type + payload [+expected_revision][+fencing_token][+guard]） |

### Experiment（归因实验）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/experiments` | 创建（experiment.requested） |
| POST | `/v1/experiments/{id}/protocol` | 冻结协议（protocol_frozen） |
| POST | `/v1/experiments/{id}/start` | 启动（started，runner 领单） |
| POST | `/v1/experiments/{id}/cells` | cell 完成（cell_completed，自迁移累计） |
| POST | `/v1/experiments/{id}/verdict` | 提交完整 EvidenceBundle/AttributionReport；控制面重算后裁决（需当前 fencing token） |
| POST | `/v1/experiments/{id}/escalate-full-factorial` | CONFOUNDED → 2³ 全因子 |
| POST | `/v1/experiments/{id}/cancel` | 取消 |
| GET | `/v1/experiments` · `/v1/experiments/{id}` | 列表 / 读取 |

### ChangeSet（修复变更集）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/changesets` | 起草（changeset.drafted） |
| POST | `/v1/changesets/{id}/gate` | 附门禁（gate_attached，仅 passed） |
| POST | `/v1/changesets/{id}/approval-request` | 提请审批（approval_requested） |
| POST | `/v1/changesets/{id}/approve` · `/reject` · `/expire` | 审批三态 |
| POST | `/v1/changesets/{id}/commit` | 移交 Release（committed） |
| POST | `/v1/changesets/{id}/supersede` | 被新工单取代（superseded） |
| GET | `/v1/changesets` · `/v1/changesets/{id}` | 列表 / 读取 |

### Release Controller（写面唯一入口，调 Quality API）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/release-candidates` | 由 Release Controller 基于精确 active VersionSet 创建单变量 DRAFT 候选 |
| POST | `/v1/workorders` | 登记不可变 WorkOrder（JCS hash 校验） |
| POST | `/v1/approvals` | 登记 ApprovalGrant（hash 绑定 + nonce 唯一） |
| POST | `/v1/releases` | 启动发布（nonce 一次性消费 + expiry 校验） |
| POST | `/v1/releases/{id}/approval-context` | 返回下一动作不可变授权参数及 digest（审批权威据此签发 grant） |
| POST | `/v1/releases/{id}/stage` | draft→staged |
| POST | `/v1/releases/{id}/canary` | staged→canary（灰度百分比） |
| GET | `/v1/releases/{id}/verification-context` | 返回精确 canary target/revision/digest，供 post-canary Gate 使用 |
| POST | `/v1/releases/{id}/promote` | canary→active（须经 VERIFYING） |
| POST | `/v1/releases/{id}/rollback` | 回滚（verification failed 路径） |
| POST | `/v1/releases/{id}/reconcile` | UNKNOWN→对账（resume/confirm/compensate） |
| GET | `/v1/releases` · `/v1/releases/{id}` | 列表 / 读取 |
| GET | `/v1/operations/{operation_id}` | 异步写操作状态 |

### Notification（对外通知，outbox 模式）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/notifications` | 以已完成 Release receipt 推进 Case，并原子入队原投诉渠道通知 |
| GET | `/v1/notifications` · `/v1/notifications/{id}` | 列表 / 读取 |

Notification 的 SENT/RETRY/DEAD 生命周期只由 outbox dispatcher 根据绑定
`outbox_id + payload_digest` 的 provider receipt 驱动；不存在可伪造 ACK 的 REST 路由。

### 运维

- `GET /healthz`
- `POST /v1/outbox/relay`：带内部鉴权的运维触发；生产常驻路径为固定 dispatcher worker

## 环境变量（.env.example 全量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `DATABASE_URL` | `postgresql+psycopg://agentmed:agentmed@127.0.0.1:5432/control_plane` | PG 连接串 |
| `QUALITY_API_BASE_URL` | `http://127.0.0.1:8080` | 被治理应用 Quality API |
| `QUALITY_API_TOKEN` | 空 | 写面 Bearer token（不入库） |
| `CONTROL_PLANE_TOKEN` | 空 | Gate/WorkOrder/ChangeSet/Release 内部写接口；未配置时 fail closed |
| `APPROVAL_AUTHORITY_TOKEN` | 空 | 独立审批权威凭证；必须与控制面 token 不同，未配置时 fail closed |
| `ATTRIBUTION_DELTA_MIN` | `0.2` | 控制面重算归因裁决使用的最小实际效应量 |
| `GATE_POLICY_PROFILE` | `live` | `live` 严格要求 provider 轨；`isolated-replay` 只供显式 replay 命令 |
| `ALLOW_ISOLATED_REPLAY_GATE` | `false` | 二次保险；仅隔离 SQLite replay controller 可设为 `true`，生产 PostgreSQL 即使误设也拒绝 |
| `LEASE_TTL_SECONDS` | `60` | Worker 租约时长（D-001 #11） |
| `COMPLAINT_DEDUP_WINDOW_HOURS` | `24` | 投诉去重窗（D-001 #1） |
| `APPROVAL_TTL_MINUTES` | `30` | ApprovalGrant TTL（D-001 #10） |
| `CANARY_STEPS` | `5,25,100` | 灰度阶梯（D-001 #4） |
| `CANARY_OBSERVATION_SECONDS` | `120` | 灰度最短观察窗（MVP 2min） |
| `OPERATION_TTL_HOURS` | `24` | 写操作 TTL（D-001 Q1） |
| `OPERATION_POLL_TIMEOUT_SECONDS` | `5` | 异步写轮询超时（超时→UNKNOWN→reconcile） |
| `RECONCILE_BACKOFF_INITIAL_SECONDS` | `5` | reconcile 指数退避起点（D-001 #5） |
| `RECONCILE_BACKOFF_MAX_SECONDS` | `300` | reconcile 退避上限 |
| `OUTBOX_RELAY_INTERVAL_SECONDS` | `1` | outbox 轮询间隔 |
| `OUTBOX_CLAIM_TTL_SECONDS` | `30` | PROCESSING claim 过期回收 |
| `OUTBOX_MAX_ATTEMPTS` | `5` | 重试上限，耗尽后 DEAD |
| `OUTBOX_RETRY_INITIAL_SECONDS` | `2` | 指数退避起点 |
| `OUTBOX_RETRY_MAX_SECONDS` | `300` | 指数退避上限 |
| `NOTIFICATION_ADAPTER` | `disabled` | `disabled` fail closed；`feishu-mock` 仅 contract/replay |
| `AUDIT_JSONL_PATH` | `./var/audit.jsonl` | 审计导出物（权威源=DB） |
| `AUDIT_FORCE_FAIL` | `false` | 仅测试：审计写失败开关 |

## 设计要点

- **事件溯源 + CAS**：每个状态迁移 = 一个事务（CAS 校验 revision → 更新 aggregate →
  追加 event → 必要时写 outbox → 写 audit）。`revision` 即事件序号。
- **事务 outbox**：九类关键领域事件统一绑定 source event、payload digest 与稳定事件名；
  dispatcher 用 `FOR UPDATE SKIP LOCKED` + claim lease，ACK、领域消费、不可变 receipt 与审计
  同事务提交。旧 logging-only SENT 在 003 迁移后标为 DEAD，不冒充已投递。
- **Trust 权威闭环**：真实 `RELEASE_PROMOTED/ROLLED_BACK/UNKNOWN` 由 dispatcher 消费；
  `(action_type,risk_class,action_ref)` 去重保证一次行动多 probes 只算一个样本。
  Release 是 R2，永远 MANUAL；3/3 的 Wilson 双侧 95% 下界约 0.438，明确拒绝晋升。
- **通知与归档**：provider receipt 必须精确绑定 outbox id 与 payload digest；ACK 后同事务写
  `notification.sent`、`case.closed`、`NOTIFICATION_SENT`、`CASE_ARCHIVED` 与审计。
  receipt 无效会死信并令 Case ESCALATED。
- **lease + fencing token**：领单 = `leases` 表 + 全局单调 `fencing_counter`；
  过期回收后新 token，旧 token 写一律拒绝（防脑裂）。
- **inbox 去重**：`sha256(source|external_id)`；无 external_id 时按 D-001 Q4
  （先 PII 脱敏 → 归一化 → 哈希）。去重窗内重复 → 返回已有 case；窗外 → 换键重立案。
- **UNKNOWN→reconcile**：Quality API 写操作结果不可考（超时/410/分区）→ Release 进 UNKNOWN；
  以原请求及原 Idempotency-Key 精确重放，并要求 `GET /status` history 以真实 Quality
  operation id 证明生效；pending、无 history 或无法归因时保持 UNKNOWN。
- **审计失败即拒业务**：audit 写入与业务同事务；写失败 → 事务回滚 → 503。
- **R2 逐次授权**：canary/promote/rollback 各需一个绑定 release、action、target revision、
  参数 digest 的独立 ApprovalGrant；grant nonce 只消费一次，reconcile 复用原 operation 绑定，
  不会重新授权或扩大动作。promote 额外绑定获批 active 基线 digest，Quality API 在全局锁内
  核对，防止并发候选串行掉包。

## B1 纵向闭环

从仓库根目录运行：

```bash
make demo-b1-replay
make demo-b1-live
```

`demo-b1-replay` 使用明确标注的录制 provider、确定性 judge、Fake Quality lifecycle
和 Feishu mock adapter，但调用生产 control-plane service、事务 outbox、Release Controller
与 Trust 路径；它不会声称 live-provider 通过。`demo-b1-live` 绝不回退 replay；缺真实
VersionSet、模型/裁判凭证、审批或 Feishu/部署权限时会写 machine-readable BLOCKED 报告并
非零退出。两类报告分别保存于 `evidence/p0/p0-4-b1/` 与
`evidence/p0/p0-4-b1-live/`。若 control-plane 与 eval-harness 使用不同虚拟环境，显式指定：

```bash
make PYTHON=/path/to/control-plane/python \
  SUITE_PYTHON=/path/to/eval-harness/python demo-b1-replay
```

live runner 至少要求以下外部边界；任何一项缺失都只会生成 BLOCKED 报告：

- `STEPFUN_API_KEY`、`JUDGE_MODEL`（必须不同于运动员模型），默认
  `STEPFUN_BASE_URL=https://api.stepfun.com/step_plan/v1`；
- 两个真实、不可变 B1 VersionSet ID、Quality read endpoint/token、Control Plane endpoint，
  以及互不复用的 controller/gate authority token；
- `AGENTMED_B1_APPROVAL_COMMAND`：由 runner 外部持权，每个动作返回一个新鲜、已持久化的
  human ApprovalGrant ID；
- `AGENTMED_B1_AGENT_TRACE_COMMAND` 与部署钉定的
  `AGENTMED_B1_AGENT_TRACE_PUBLIC_KEY`：导出真实 AgentTeams v1.2.1/Matrix/skill receipt；
- `AGENTMED_B1_FEISHU_MESSAGE_COMMAND`：**只在 B1 注入成功后**等待新投诉，并从 stdout
  返回 `{"schema_version":"0.1.0","provider":"feishu","message_id":"..."}`。
  Runner 不向该命令传 Control Plane、Quality、StepFun 或 Feishu secret；message ID 只是
  locator，Control Plane 会自行抓取原消息，并在建立 Case 前验证仓库 B1 fixture digest 与
  provider `create_time > injected_at`。可用
  `AGENTMED_B1_FEISHU_MESSAGE_TIMEOUT_SECONDS` 设置 1–3600 秒等待上限。

当前 live 中途失败会安全补偿 Quality fault，但尚未 durable resume/terminalize 已创建的
Case/Experiment/Release；因此同一投诉跨失败重试仍是明确 P0-4 blocker，不能把 live 命令称为
完整可恢复。
- **JCS 限制**：WorkOrder hash 用 JCS 的 ASCII 可打印子集（与 contracts/conformance 一致）；
  含换行/非 ASCII 的 diff 请用 `content_ref` 而非内联 `content`。

## 已知遗留

- live Feishu adapter、post-injection message acquisition command 与凭证尚未提供；
  `feishu-mock` 只属于明确标注的 contract/replay 路径。
- live AgentTeams exporter 私钥/Matrix taskflow 及部署钉定公钥 registry 尚未提供；本仓库只负责
  receipt contract 与 Ed25519 验证，不能自签 live 成功。
- eval-harness live provider 仍取决于外部模型凭证；不得把 skipped 当 PASS。
