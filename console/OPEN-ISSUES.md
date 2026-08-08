# Console 数据缺口清单（T7）

> 原则：**不为凑数据去改已验收的后端代码，也严禁编造假数据**。以下缺口全部以「数据待接入」
> 标注在页面上，待 control-plane / mcp-servers 暴露对应 REST 端点后接入。均在 scope 外。

| # | 视图 | 缺口 | 建议接入方式（control-plane 侧） |
|---|------|------|------|
| 1 | 顶栏 · demo-app 基线 digest | console 只通 control-plane，control-plane 无 demo-app 透传端点；现用静态快照 `vs_baseline0000000001` digest `sha256:8fc6a7ac…`（demo-app 实测 active） | control-plane 增加 `GET /v1/env`（透传 demo-app active versionset digest），或 nginx 增 `/api/demo/` 反代 demo-app:8080 |
| 2 | 总览信任卡 / 信任页 · 账本网格 / 拒绝晋升 | `trust_ledger` 表在 control-plane 库但无 REST 读端点；现环境表为空（真实空） | control-plane 增加 `GET /v1/trust/ledger`（risk_class × action_type × epoch 投影 + Wilson LB/UB + autonomy_state）与 `GET /v1/trust/denials`（audit trust.promotion_rejected） |
| 3 | 案例详情 · 时间线 | events 表在库但 control-plane 无 `GET /v1/cases/{id}/events`；`case.timeline` 属 mcp-case-admin（:8001，非 REST） | control-plane 增加 `GET /v1/cases/{id}/events`（按 seq 升序返回 event_type/payload/actor/occurred_at），或网关透传 case-admin MCP |
| 4 | 案例详情 · 证据引用面板 | case aggregate payload 未投影 evidence ref（随案件推进到 ATTRIBUTING 后才可能出现） | 由 #3 events 端点渲染，无需新端点 |
| 5 | 实验详情 · 5-cell 条形 / Δ 与 CI / 归因层 | cell 级 recovery_rate 与 verdict 的 deltas/attributed_layer 在 events，aggregate payload 只投影 `cell_progress`(末位)/`verdict`/`report_ref` | control-plane 实验详情 `_view` 补投影 cells/deltas/CI；或由 #3 泛化 events 端点透出 |
| 6 | 审批 · workorder hash / 提请人 | `workorders` 表有 hash、drafted 事件有 author_agent，但 control-plane **无 `GET /v1/workorders`**；changeset aggregate payload 未投影这两字段 | control-plane 增加 `GET /v1/workorders`（workorder_id/hash/case_id/channel/payload/created_at） |
| 7 | 信任页 · 门禁报告列表 | eval/gate 数据在 mcp_* 表（mcp-eval-runner `gate.report`），control-plane 无 REST | control-plane 增加 `GET /v1/gates`（rule_track/judge_track/deterministic/live_e2e 分列 + verdict + report_hash），或网关透传 eval-runner MCP |

## 审批写路径说明

待审批队列本身可读（`/api/v1/changesets?state=AWAITING_APPROVAL`），但**批准/拒绝按钮按规格禁用**并标注
「经 release-admin MCP 审批」：审批写面是 release-admin MCP `approval.request`（common/approval.py 做
hash 绑定 + nonce 原子消费 + expiry 校验，spec §5.2 / §11.1），control-plane 的 `POST /v1/changesets/{id}/approve|reject`
需要 `approval_id` + `workorder_hash` 才能构造有效调用，而这两者均无 REST 读端点可供 console 获取。
为避免「绕过审批机制直接改状态机」的反模式，控制台定位为**只读监控面**，写动作保留在 MCP 侧。
