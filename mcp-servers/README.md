# CaseLoop mcp-servers（T4）

5 个 MCP Server + trust-ledger 模块 + common 安全件重写。

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
    serverkit.py         PathRewrite + 组合 ASGI app + 错误 envelope
    http.py              HTTP 客户端：退避重试 + REST 错误映射统一错误码
    tables.py            mcp_* 自有表（与 control-plane 公共 schema 无冲突）
  trust_ledger/          legacy contract/replay 库（非生产权威写路径）
    wilson.py            Wilson 双侧 95% 区间（z=1.96，13 组向量全过）
    ledger.py            record_outcome/get_state/evaluate_promotion/
                         request_promotion/suspend/reinstate
  servers/               5 个 MCP server（FastMCP + PathRewrite uvicorn :8xxx）
    case_admin.py        mcp-case-admin  :8001
    release_admin.py     mcp-release-admin :8002
    eval_runner.py       mcp-eval-runner  :8003
    notification.py      mcp-notification（feishu-mock） :8004
    casebase_knowledge.py mcp-casebase-knowledge :8005
  scripts/
    run_migrations.py    幂等建表（PG migrations/001_init.sql / SQLite create_all）
    smoke.sh             起 5 个 server 逐一 call 关键工具
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
| `DATABASE_URL` | `postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane` | mcp_* 表所在库 |
| `CONTROL_PLANE_BASE_URL` | `http://127.0.0.1:8090` | case/release 包装的上游 |
| `QUALITY_API_BASE_URL` | `http://127.0.0.1:8080` | demo-app Quality API |
| `APPROVAL_TTL_MINUTES` | `30` | ApprovalGrant TTL（D-001 #10） |
| `TRUST_COOLOFF_HOURS` | `24` | SUSPENDED 冷却（D-001 Q8） |
| `CASE_ADMIN_PORT` … `CASEBASE_PORT` | `8001`…`8005` | 各 server 端口 |

### 3) 建表（幂等）

```bash
.venv/bin/python scripts/run_migrations.py
```

### 4) 起 server（示例）

```bash
.venv/bin/python -m servers.case_admin          # :8001
.venv/bin/python -m servers.notification        # :8004（含 REST GET /api/messages）
```

每个 server 的 HTTP 端点：MCP 在 `/mcp`；网关路径 `/mcp-servers/<name>/mcp` 由 PathRewrite 重写为 `/mcp`。

### 5) 测试

```bash
# 单测（Wilson 13 向量 / nonce 重放 / expiry 过期 / hash 不匹配 / 审计失败 503 专项）
.venv/bin/python -m pytest tests/ -v

# smoke（起 5 个 server，call 关键工具；需要 control-plane 可选）
CONTROL_PLANE_BASE_URL=http://127.0.0.1:8090 bash scripts/smoke.sh
```

## 工具清单（spec §9）

| Server | 工具 |
|--------|------|
| mcp-case-admin | `case.list` `case.get` `case.timeline` `case.claim` `case.submit_suggestion` `case.escalate` `app.logs` `app.feedback` |
| mcp-release-admin | `workorder.draft` `gate.submit` `workorder.freeze` `workorder.get` `approval.request` `approval.status` `release.get` `release.request_canary` `release.request_rollback` |
| mcp-eval-runner | `gate.run` `gate.report` `experiment.plan` `experiment.run` `experiment.report` `probe.freeze` |
| mcp-notification | `feishu.reply_origin` `feishu.approval_card` `feishu.weekly_report` `matrix.log` + REST `GET /api/messages` |
| mcp-casebase-knowledge | `kb.search` `kb.get` `kb.upsert` `kb.badcase_search` `kb.holdout_get` |

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

## 已知边界

见 `OPEN-ISSUES.md`。核心：向量检索 Phase 2（D-001 #12）、live provider E2E MVP 标 skipped、
ACL 由 Higress 网关执行（server 侧文档声明 + kb.upsert 本地校验）。
