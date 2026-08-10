# CaseLoop Console 数据映射（P0-3 当前态）

> T7 在 2026-08-08 首次交付时，`/v1/env`、Trust、Case events、WorkOrders 和 Gates
> 确实尚未接通，原文记录的是当时的真实运行快照。随后 `9afcefa` 合并了朋友的 T8
> 只读投影，本轮 P0-3 在该实现上完成 Console 接线；不应再把旧缺口当成当前事实。
>
> 数据边界保持不变：浏览器只请求相对路径 `/api/*`；Vite 本地代理或 nginx compose
> 代理再转发到 control-plane。Console 是只读监控面，不绕过 Release Controller 或审批权威。

状态图例：✅ 实际端点 · ⚠️ 部分/UNKNOWN 必须显式显示 · ⛔ 写面不属于 Console。

## 当前权威端点

| 能力 | Console 请求 | 权威来源 | 状态 |
|---|---|---|---|
| demo-app active VersionSet | `GET /api/v1/env` | control-plane 透传 Quality API active VersionSet | ✅；Quality API 不可达或无 active 时显示红色 `UNKNOWN` |
| Case 列表/详情 | `GET /api/v1/cases[/{id}]` | Case aggregate | ✅ |
| Case 时间线 | `GET /api/v1/cases/{id}/events` | control-plane events，含 seq/actor/causation/correlation/trace | ✅ |
| Experiment 列表/完整详情 | `GET /api/v1/experiments`、`/{id}?_view=full` | Experiment aggregate + events | ✅；CI 缺样本量/方差时显示 `UNKNOWN` |
| WorkOrder/审批队列 | `GET /api/v1/workorders` | 不可变 `workorders` 为主，ChangeSet 仅提供 lifecycle state | ✅；重验 payload 与投影列，完整 payload 不暴露；任何 WorkOrder/Gate binding 失败即 `UNKNOWN` |
| GateReport | `GET /api/v1/gates` | control-plane `gate_reports`，每次读取重验 hash/schema/binding | ✅；合法但尚未绑定标为 `UNBOUND`；损坏/不完整绑定标为 `integrity_error/UNKNOWN`，绝不作为整体 PASS |
| Trust Ledger | `GET /api/v1/trust/ledger` | control-plane 权威 Trust ledger | ✅ |
| Trust 拒绝晋升 | `GET /api/v1/trust/denials` | control-plane audit + Trust entry | ✅ |
| Release | `GET /api/v1/releases[/{id}]` | Release aggregate | ✅；`UNKNOWN` 保持 fail-closed 语义 |
| Notification | `GET /api/v1/notifications[/{id}]` | Notification aggregate | ✅；receipt 缺失显示 `UNKNOWN` |
| Evidence bindings | `GET /api/v1/evidence?case_id=` | events、不可变 WorkOrder、完整性通过的 GateReport | ⚠️ 只证明 ref/digest 已记录；artifact 内容仍不可读取/验证 |
| AI 应用目录（V5-1A） | `GET /api/v1/applications` | control-plane V5 catalog（`ai_applications` 等，每次读取重验 `record_envelope` digest） | ✅；任何行完整性重验失败即行级 `integrity_error`/`UNKNOWN` 并置 partial 警示，绝不作为可信数据 |

## 页面映射

| 页面 | 真实数据源 | Partial/UNKNOWN 行为 |
|---|---|---|
| `/` 总览 | Cases（列表直接投影真实 title）、Experiments、WorkOrders、Trust 四个独立请求 | 单一来源失败不遮蔽其余来源；刷新失败保留上次结果并标明 `STALE`，不产生逐行 N+1 请求 |
| `/cases`、`/cases/:id` | Case list/detail、Case events、Evidence | loading/empty/error/retry/stale；事件和证据各自可失败 |
| `/experiments`、`/experiments/:id` | Experiment list、`_view=full` | 无真实 recovery/delta/CI 时显示 `UNKNOWN`，不生成固定 `0.00` |
| `/approvals` | WorkOrders + ChangeSet lifecycle + Gate binding | 显示 exact hash、nonce、expiry、target revision、binding digest；任一完整性错误阻断可信展示 |
| `/trust` | Gates、Trust ledger、Trust denials 三个独立请求 | `failed/error/skipped/integrity_error/UNBOUND/UNKNOWN` 不会被映射成整体 PASS |
| `/operations` | Releases、Notifications、Evidence | 每个来源独立 loading/empty/error/retry；artifact 内容保持 `UNKNOWN` |
| `/applications` | V5 catalog Applications read model | loading/empty/error/retry/stale；行级 integrity 重验失败置 `integrity_error`/`UNKNOWN` 并显示 partial 警示 |

## 验证

```bash
cd console
npm test                    # Vitest API client unit
npm run build               # TypeScript + production bundle
npm run test:integration    # 随机 scratch PG + Alembic + FastAPI + Vite + Chromium
```

`test:integration` 不使用 Playwright route interception。它经 Vite `/api` 创建真实投诉，浏览器回读
`complaint.received` 的 seq、actor 和 `text_ref`，并在同一 SPA 组件切换 Case ID 验证 request-key
隔离。API client 对每个端点执行 runtime schema validation；结构畸形即 `invalid_response`。退出时
停止服务并通过显式 admin host/port/user 删除唯一命名的 scratch 数据库。
