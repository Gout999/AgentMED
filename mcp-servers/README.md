# AgentMED mcp-servers（T4）

5 个实现模块以 12 个物理角色 projection 运行，另含 trust-ledger contract/replay
模块与 common 安全件。一个进程只暴露一个角色的固定工具集合。

Agent 团队通过 MCP 工具操作控制面与外部世界；本目录的 trust-ledger 保留为
Wilson/状态机 contract-replay 库，生产权威账本由 control-plane outbox dispatcher
消费真实 Release 结果写入；
common 的审批与审计是安全底线（zeroops 旧实现只作反面参考，已重写）。

- 语言/框架：Python 3.11+ · FastMCP（`mcp==1.12.4`，2.x 移除了 fastmcp，故钉 1.x）
- 传输：Streamable HTTP + PathRewrite（适配 Higress 网关 `/mcp-servers/<name>/mcp` 透传）
- 对齐契约：`docs/spec.md §5/6/9/11`、`contracts/schemas/`、`contracts/wilson/`、`docs/decisions/D-001`

## 目录

```
mcp-servers/
  common/                共享安全件（重写）
    approval.py          ApprovalGrant 校验：hash 绑定 + nonce 原子消费 + expiry 30min
                         + server_recorded proof + audit URI（spec §5.2 / §11.1）
    audit.py             权威审计：写库失败即抛业务 503，不放行（spec §7.6 / §11.4）
    pii.py               PII 入口脱敏（与 control-plane 口径一致）
    jcs.py               JCS(RFC8785) 子集 + SHA-256（WorkOrder hash 契约）
    errors.py            统一错误码（spec §9.2）
    serverkit.py         PathRewrite + 角色投影 + Higress 后端/consumer 双重校验
    http.py              HTTP 客户端：退避重试 + REST 错误映射统一错误码
    tables.py            mcp_* 自有表（与 control-plane 公共 schema 无冲突）
  trust_ledger/          legacy contract/replay 库（非生产权威写路径）
    wilson.py            Wilson 双侧 95% 区间（z=1.96，13 组向量全过）
    ledger.py            record_outcome/get_state/evaluate_promotion/
                         request_promotion/suspend/reinstate
  servers/               5 个实现模块；生产按角色拆成 12 个 FastMCP 进程
    case_admin.py        mcp-agentmed-admin  :8001
    release_admin.py     mcp-agentmed-release :8002
    eval_runner.py       mcp-agentmed-eval  :8003
    notification.py      mcp-agentmed-notify（feishu-mock） :8004
    casebase_knowledge.py mcp-agentmed-casebase-knowledge :8005
  scripts/
    run_migrations.py    幂等建表（PG migrations/001_init.sql / SQLite create_all）
    smoke.sh             起 12 个 projection，验证启动、后端拒绝及精确工具面
    mcp_client.py        MCP Streamable HTTP 客户端（smoke 用）
    trust_demo.py        legacy Wilson contract/replay 演示（不作闭环证据）
  tests/                 单测（SQLite 内存，无外部依赖）
  migrations/001_init.sql
```

## 快速开始

### 1) 建 venv 装依赖

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2) 配置环境变量

复制 `.env.example` 为 `.env`（不入库）。关键项：

| 变量 | 默认 | 说明 |
|------|------|------|
| `DATABASE_URL` | `postgresql+psycopg://agentmed:agentmed@127.0.0.1:5432/control_plane` | mcp_* 表所在库 |
| `CONTROL_PLANE_BASE_URL` | `http://127.0.0.1:8090` | case/release 包装的上游 |
| `CONTROL_PLANE_ROLE_TOKEN` | 空 | 当前 projection 的最小权限控制面 token；只读 projection 必须为空 |
| `QUALITY_API_BASE_URL` | `http://127.0.0.1:8080` | demo-app Quality API |
| `MCP_TOOL_PROFILE` | 空 | 固定角色；空值/未知值拒绝启动 |
| `MCP_WORKER_ID` | 空 | 当前 projection 的规范 worker identity |
| `MCP_EXPECTED_CONSUMER` | 空 | 必须为 `worker-<profile>` |
| `MCP_GATEWAY_BACKEND_TOKEN` | 空 | 当前 projection 独立的 Higress→backend 凭证 |
| `APPROVAL_TTL_MINUTES` | `30` | ApprovalGrant TTL（D-001 #10） |
| `TRUST_COOLOFF_HOURS` | `24` | SUSPENDED 冷却（D-001 Q8） |
| `CASE_ADMIN_PORT` … `CASEBASE_PORT` | `8001`…`8005` | 各 server 端口 |

### 3) 建表（幂等）

```bash
.venv/bin/python scripts/run_migrations.py
```

### 4) 起单一 projection（示例）

```bash
env \
  MCP_TOOL_PROFILE=collector \
  MCP_WORKER_ID=collector \
  MCP_EXPECTED_CONSUMER=worker-collector \
  MCP_GATEWAY_BACKEND_TOKEN="$COLLECTOR_BACKEND_TOKEN" \
  CONTROL_PLANE_ROLE_TOKEN= \
  GATE_AUTHORITY_TOKEN= \
  CASE_ADMIN_PORT=8201 \
  .venv/bin/python -m servers.case_admin
```

每个进程的 MCP 端点是 `/mcp`；网关路径由 PathRewrite 重写。直接访问 backend、
缺失/重复后端 header、或 `X-Mse-Consumer` 与固定角色不符均返回 403。完整 12 进程
启动与 Higress upstream security 配置见 `agents/RUNBOOK.md`。

### 5) 测试

```bash
# 单测（Wilson 13 向量 / nonce 重放 / expiry 过期 / hash 不匹配 / 审计失败 503 专项）
.venv/bin/python -m pytest tests/ -v

# projection smoke（不冒充 B1；仅验证 12 个进程、后端 ACL、精确 tools/list）
bash scripts/smoke.sh
```

## 工具清单（spec §9）

| Projection | 工具 |
|--------|------|
| case-admin / quality-officer | `case.list` `case.get` `case.timeline` `case.claim` `case.submit_suggestion` `case.escalate` |
| case-admin / collector | `case.get` `app.logs` `app.feedback` |
| case-admin / case-officer | `case.get` |
| case-admin / attributionist | `case.get` `case.claim` `app.logs` |
| case-admin / repairer | `case.get` `case.timeline` `case.claim` |
| release-admin / gatekeeper | `workorder.get` `gate.submit` `approval.request` `approval.status` `release.get` |
| release-admin / repairer | `versionset.list` `versionset.get` `candidate.create` `workorder.draft` `workorder.freeze` `workorder.get` `release.get` |
| eval-runner / gatekeeper | `gate.run` `gate.run_verification` `gate.report` |
| eval-runner / attributionist | `versionset.list` `versionset.get` `experiment.plan` `experiment.run` `experiment.execute` `experiment.report` `probe.freeze` |
| notification / quality-officer | `matrix.log` |
| notification / case-officer | `feishu.reply_origin` `feishu.weekly_report` `matrix.log` |
| casebase / case-officer | `kb.search` `kb.get` `kb.upsert` `kb.badcase_search` `kb.holdout_get` |

错误统一 `data.error_code` + `retryable` + `audit_ref`（spec §9.2）；写工具不自动重试，幂等键 `<case_id>:<action>:<seq>`。

## 设计要点

- **审批防掉包防重放**（spec §5.2 / §11.1）：`common/approval.py` 校验 workorder_hash 绑定
  + nonce PG 原子消费（`UPDATE … WHERE nonce_consumed=false`，rowcount=1 才成功）+ expiry
  30min TTL + server_recorded proof + audit URI。复用 → `APPROVAL_REPLAYED`，掉包 →
  `APPROVAL_MISMATCH`，过期 → `APPROVAL_EXPIRED`。
- **审计失败即拒**（spec §7.6 / §11.4）：`common/audit.py` 与业务同事务；写失败抛
  `AuditWriteError` → 业务 503 不放行。`audit.jsonl` 仅为导出物。
- **trust-ledger contract/replay**（spec §6 / §9.8）：一次动作=一个样本；epoch 原始整数计数；Wilson 双侧
  95% 下界 >0.9 且白名单 R1 才可提请晋升；R2 永远逐次审批。MVP 3/3 → 下界≈0.4385 →
  记账但拒绝晋升。生产记录不得调用此 demo 直接改 `mcp_trust_ledger`；权威记录位于
  control-plane `trust_ledger` / `trust_ledger_entries`，来源是 outbox 的真实 Release 事件。
- **WorkOrder hash**（contracts/schemas/workorder.schema.json）：JCS 子集（ASCII 可打印）
  + SHA-256，绑定除 hash 外全部字段（含 hash_rule）。含换行/非 ASCII 的 diff 用 `content_ref`。
- **门禁双轨**（spec §9.5）：`gate.report` 返回 rule_track / judge_track / deterministic_tests
  / live_provider_e2e 分列 + verdict + report_hash；裁判模型 digest ≠ 运动员模型 digest（T6）。
- **MCP 不越权**：物理 projection + gateway backend secret + `X-Mse-Consumer` 三层绑定；
  控制面再按 role token 对 method/path 白名单授权，并把 caller identity 绑定到固定 worker。
  `candidate.create` 与 `feishu.reply_origin` 都只调用控制面；前者不持 Quality
  写 token，后者只返回 QUEUED，送达回执、Case 归档和 Trust 记账均由 transactional outbox
  dispatcher 驱动。

## 已知边界

见 `OPEN-ISSUES.md`。核心：向量检索 Phase 2（D-001 #12）；缺真实 provider 凭证时
live-provider E2E 必须明确阻塞，不能由 replay/skipped 冒充通过。
