# CaseLoop control-plane

确定性控制面（Case/Release Controller）：**LLM 不是状态与权限的权威源**。状态权威源 = PG
（aggregates/events/inbox/outbox/leases/audit），事件溯源 + 状态机 + CAS 乐观并发 + lease/fencing。

- 语言/框架：Python 3.11+ · FastAPI · SQLAlchemy 2 · Alembic
- 依赖：见 `requirements.txt`（钉版本）
- 对齐契约：`contracts/events/`（events.yaml + state-machines.yaml）、`contracts/schemas/`、`docs/spec.md §7`

## 目录

```
control-plane/
  app/
    api/           REST 路由：cases / experiments / changesets / releases / notifications
    services/      Case Controller / Release Controller / Notification / Experiment / ChangeSet
    models/        spec §7 十表 ORM
    quality/       Quality API v2 客户端（写面唯一入口，按 contracts/quality-api/openapi.yaml）
    utils/         JCS(SHA-256) / ULID id / PII 脱敏
  alembic/         001 initial migration
  tests/           unit（SQLite 内存） + integration（compose PG 真跑）
```

## 快速开始

### 1) 起 Postgres

```bash
docker compose -f ../deploy/compose.yaml up -d postgres
```

### 2) 建 venv 并装依赖

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3) 初始化数据库（二选一）

```bash
# 方式 A：alembic 迁移
.venv/bin/alembic upgrade head

# 方式 B：应用启动时建表（仅开发）
# create_app(create_tables=True)
```

### 4) 起服务

方式 A：宿主机 uvicorn（本地开发）

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090
```

方式 B：compose 容器（e2e 编排 / 统一部署）

```bash
# 在仓库根目录；仅起 control-plane（自动拉 postgres 依赖 + 构建镜像）
docker compose -f deploy/compose.yaml up -d --build control-plane

# 宿主 8090 被其他服务占用，compose 映射到 18090
curl http://127.0.0.1:18090/healthz     # {"status":"ok",...}
curl http://127.0.0.1:18090/v1/cases    # {"items":[],...}

# 重启（alembic 幂等：已在 head 时 upgrade 为 no-op）
docker compose -f deploy/compose.yaml restart control-plane
```

容器入口先 `alembic upgrade head` 再起 uvicorn；`DATABASE_URL` 由 compose
注入 compose 内部网络地址（`postgres:5432/control_plane`），`QUALITY_API_BASE_URL`
指向 `demo-app:8080`。镜像构建见 `Dockerfile`，非 root 运行。

配置全部走环境变量，模板见 `.env.example`（复制为 `.env` 后填写；`.env` 不入库）。

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
| POST | `/v1/experiments/{id}/verdict` | 出裁决（verdict_computed） |
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
| POST | `/v1/workorders` | 登记不可变 WorkOrder（JCS hash 校验） |
| POST | `/v1/approvals` | 登记 ApprovalGrant（hash 绑定 + nonce 唯一） |
| POST | `/v1/releases` | 启动发布（nonce 一次性消费 + expiry 校验） |
| POST | `/v1/releases/{id}/stage` | draft→staged |
| POST | `/v1/releases/{id}/canary` | staged→canary（灰度百分比） |
| POST | `/v1/releases/{id}/promote` | canary→active（须经 VERIFYING） |
| POST | `/v1/releases/{id}/rollback` | 回滚（verification failed 路径） |
| POST | `/v1/releases/{id}/reconcile` | UNKNOWN→对账（resume/confirm/compensate） |
| GET | `/v1/releases` · `/v1/releases/{id}` | 列表 / 读取 |
| GET | `/v1/operations/{operation_id}` | 异步写操作状态 |

### Notification（对外通知，outbox 模式）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/notifications` | 入队（notification.queued + outbox 同事务） |
| POST | `/v1/notifications/{id}/sent` · `/failed` · `/retry` · `/dead-letter` | 生命周期 |
| GET | `/v1/notifications` · `/v1/notifications/{id}` | 列表 / 读取 |

### 运维

- `GET /healthz`
- `POST /v1/outbox/relay`：手动触发 outbox 投递（MVP sink=logging）

## 环境变量（.env.example 全量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `DATABASE_URL` | `postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane` | PG 连接串 |
| `QUALITY_API_BASE_URL` | `http://127.0.0.1:8080` | 被治理应用 Quality API |
| `QUALITY_API_TOKEN` | 空 | 写面 Bearer token（不入库） |
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
| `OUTBOX_SINK` | `logging` | 投递目标（MVP=logging） |
| `AUDIT_JSONL_PATH` | `./var/audit.jsonl` | 审计导出物（权威源=DB） |
| `AUDIT_FORCE_FAIL` | `false` | 仅测试：审计写失败开关 |

## 设计要点

- **事件溯源 + CAS**：每个状态迁移 = 一个事务（CAS 校验 revision → 更新 aggregate →
  追加 event → 必要时写 outbox → 写 audit）。`revision` 即事件序号。
- **lease + fencing token**：领单 = `leases` 表 + 全局单调 `fencing_counter`；
  过期回收后新 token，旧 token 写一律拒绝（防脑裂）。
- **inbox 去重**：`sha256(source|external_id)`；无 external_id 时按 D-001 Q4
  （先 PII 脱敏 → 归一化 → 哈希）。去重窗内重复 → 返回已有 case；窗外 → 换键重立案。
- **UNKNOWN→reconcile**：Quality API 写操作结果不可考（超时/410/分区）→ Release 进 UNKNOWN；
  以 `GET /status` 权威对账，按实际生效情况 resume / confirm / compensate，指数退避重试。
- **审计失败即拒业务**：audit 写入与业务同事务；写失败 → 事务回滚 → 503。
- **JCS 限制**：WorkOrder hash 用 JCS 的 ASCII 可打印子集（与 contracts/conformance 一致）；
  含换行/非 ASCII 的 diff 请用 `content_ref` 而非内联 `content`。

## 已知遗留

- `eval` / `trust` 状态机已在 `state_machines.py` 定义，但对应服务（eval-harness / trust-ledger）
  属 T3/T4 范围，本组件未实现。
- outbox 投递 MVP 为 logging sink（无真实飞书通道；T4 的 notification server 会接管）。
