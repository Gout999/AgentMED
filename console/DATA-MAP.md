# CaseLoop 控制台数据映射表（T7）

> 数据通路（冻结）：console 只调相对路径 `/api/*`；nginx 反代 `/api/` → `http://control-plane:8090/`
> （compose 内网）。本表以 `control-plane/README.md` 接口清单 + 运行环境实测（2026-08-08，5 个实验 /
> 2 个 case / 6 个待审批 changeset）为准。
>
> 状态图例：✅ 真实端点可渲染 · ⚠️ 部分字段缺失（有端点但数据未投影） · ⏳ 数据待接入（无 REST 端点）

## 全局

| 视图 | 端点 | 字段 | 状态 |
|------|------|------|------|
| 顶栏 · demo-app 基线 digest | —（control-plane 无透传端点） | 现环境 active 版本集 `vs_baseline0000000001` digest `sha256:8fc6a7ac…`（demo-app 实测） | ⏳ 静态快照，见 OPEN-ISSUES #1 |
| 顶栏 · 手动刷新 | —（前端轮询 10s + 手动按钮） | — | ✅ |

## 1. 总览 `/`

| 指标卡 | 端点 | 说明 | 状态 |
|--------|------|------|------|
| 活跃 case 数 | `GET /api/v1/cases` | 非终态（CLOSED/MERGED/DUPLICATE_DISMISSED 之外）计数 | ✅ |
| 进行中实验 | `GET /api/v1/experiments` | 非终态（VERDICT_COMPUTED/CANCELLED 之外）计数 | ✅ |
| 待审批 | `GET /api/v1/changesets?state=AWAITING_APPROVAL` | items 计数 | ✅ |
| 信任账本最新评估结论 | — | `trust_ledger` 表在 control-plane 库但未暴露 REST | ⏳ #2 |
| 最近 case 表格（10 行） | `GET /api/v1/cases` | case_id / state / updated_at；摘要取 detail payload.title | ✅ |

## 2. 案例 `/cases` · `/cases/:id`

| 视图 | 端点 | 说明 | 状态 |
|------|------|------|------|
| 列表（状态筛选） | `GET /api/v1/cases?state=` | state chip 组 = case 状态机非终态 | ✅ |
| 详情 · 基础信息 | `GET /api/v1/cases/{id}` | state / revision / payload / updated_at / event_count | ✅ |
| 详情 · 时间线（事件流 + Agent 建议） | — | events 表在 DB，control-plane 未暴露 `GET events`；`case.timeline` 属 mcp-case-admin（:8001，非 REST） | ⏳ #3 |
| 详情 · 证据引用面板 | 来自 detail.payload | 现环境 case payload 无 evidence ref（随案件推进才会出现） | ⚠️ #4 |

## 3. 实验 `/experiments` · `/experiments/:id`

| 视图 | 端点 | 说明 | 状态 |
|------|------|------|------|
| 列表 | `GET /api/v1/experiments` | experiment_id / state / payload | ✅ |
| 详情 · 状态/协议元数据 | `GET /api/v1/experiments/{id}` | payload: case_id / hypothesis_layer / verdict / report_ref | ✅ |
| 详情 · 5-cell 臂恢复率条形对比 | — | cell 级 recovery_rate 在 events，aggregate payload 只投影了最后一个 `cell_progress` | ⏳ #5 |
| 详情 · Δ 效应量与 95%CI 表 | — | deltas 在 verdict_computed 事件 payload，未并入 aggregate | ⏳ #5 |
| 详情 · 三态裁决大字结论 | `payload.verdict` | ATTRIBUTED/INCONCLUSIVE/CONFOUNDED；现环境 5 实验均 REQUESTED → 渲染「未出裁决」 | ⚠️ #5 |
| 详情 · 归因层标注 | `payload.attributed_layer` | 未并入 aggregate（在事件 payload） | ⏳ #5 |

## 4. 审批 `/approvals`

| 视图 | 端点 | 说明 | 状态 |
|------|------|------|------|
| 待审批 WorkOrder 队列 | `GET /api/v1/changesets?state=AWAITING_APPROVAL` | changeset_id（`cs_` 前缀即 workorder_id）/ nonce / expiry（freeze 时间）/ gate_report_ref | ✅ |
| workorder hash 完整可复核 | — | `workorders` 表有 hash 但 control-plane **无 `GET /v1/workorders`** | ⏳ #6 |
| 提请人 author_agent | — | drafted 事件 payload 字段，未并入 aggregate | ⏳ #6 |
| 证据摘要 | `payload.gate_report_ref` | 门禁报告引用 | ✅ |
| 批准/拒绝按钮 | — | 审批写面 = release-admin MCP `approval.request`（common/approval.py 校验 hash+nonce+expiry）；control-plane `POST /v1/changesets/{id}/approve|reject` 需 approval_id + workorder_hash，console 无法构造有效调用 | ⏳ 按钮禁用标注「经 release-admin MCP 审批」 |
| 历史审批记录 | `GET /api/v1/changesets` | APPROVED / REJECTED / EXPIRED / COMMITTED 态；现环境全 AWAITING_APPROVAL，历史为空为真实状态 | ✅ |

## 5. 门禁与信任 `/trust`

| 视图 | 端点 | 说明 | 状态 |
|------|------|------|------|
| 门禁报告列表（规则轨/裁判轨分列） | — | eval/gate 数据在 mcp_* 表（mcp-eval-runner `gate.report`，spec §9.5），control-plane 无 REST | ⏳ #7 |
| 信任账本网格（risk_class × action_type） | — | trust_ledger 表在库但无 REST；现环境表为空（真实状态） | ⏳ #2 |
| 拒绝晋升事件记录 | — | audit 表 `trust.promotion_rejected` 记录，无 REST 读端点 | ⏳ #2 |

## 现状运行数据（2026-08-08 实测）

| 聚合 | 状态 | 数量 |
|------|------|------|
| case | DISPATCHED | 2 |
| experiment | REQUESTED | 5 |
| changeset | AWAITING_APPROVAL | 6 |
| release | — | 0 |
| approval / trust_ledger | — | 0（真实空） |
