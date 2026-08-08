# CaseLoop 技术规格说明书（SPEC）

> 版本：v1.0 ｜ 日期：2026-08-07 ｜ 状态：草案待评审
> 上位文档：`docs/plan-v3.md`（终态蓝图，唯一事实源）与 `docs/prd.md`。本文档不引入 plan-v3 之外的架构决策；plan-v3 已定向但未定量之处，给出实现建议值并以【待定】标注，全部汇总于文末「开放问题」。
> 读者：实现者（建造 agent）。目标是精确到可据此写代码。
> 核心架构原则：**确定性控制面 + 概率性执行面**——AI 负责动脑子，系统负责管规矩；**LLM 永远不是状态与权限的权威源**。

---

## 1. 系统架构

### 1.1 架构总图（控制面 / 执行面分离）

引自 plan-v3 §2.1，并标注数据面细节：

```
飞书/Webhook/Poll ──► [Case Controller]      ← 非 LLM：状态/租约/幂等/inbox 去重/outbox（唯一事实源）
                          │ 派单（lease + fencing token）
                          ▼
              [AgentTeams 协作执行层] 4 常设 + 弹性 Worker（契约化集成）
                          │ 产出建议与产物（WorkOrder/归因报告/门禁报告）
                          ▼
              [隔离 Experiment Runner] ──► [Eval/Gate]（双轨，分开报告）
                          │
                          ▼
              [WorkOrder + ApprovalGrant]（不可变，hash 绑定）
                          │
                          ▼
              [Release Controller]          ← 非 LLM：唯一可调 Quality API 写面
                          │ If-Match/expected_revision + idempotency-key + 异步结果查询
                          ▼
              [Quality API: VersionSet / draft→stage→canary→promote|rollback→status]

  [Caseload Controller] ──► AgentTeams（受限 RBAC；Phase 2）
  [Trust Ledger / Audit / Evidence] ◄── 全程事件回流（审计权威源 = 数据库）
```

读面/写面铁律：

- **写面唯一入口**：Quality API 写面（draft/stage/canary/promote/rollback）仅 Release Controller 可调用；任何 Agent（含守门员、质量官）不可直连。
- **读面开放**：`GET /logs`、`GET /feedback` 经 mcp-case-admin 代理对采集员/归因师开放，只读。
- **状态权威**：七个子状态机的权威状态全部存于 Case Controller 的 PG 库；Agent 的"结论"只是进入控制面裁决的输入。

### 1.2 组件职责表

| 组件 | 类型 | 职责 | 关键不做 |
|------|------|------|---------|
| Case Controller | 非 LLM 控制面 | 投诉接入（webhook/poll→inbox 去重→立案）；Case/Experiment/ChangeSet/Eval/Notification/Trust 六个状态机的权威状态与迁移裁决；lease 分配 + fencing token；outbox 可靠外发；事件回流与审计 | 不做任何 LLM 推理；不接受 Agent 直接写状态（只接受"建议"，由控制面裁决迁移） |
| Release Controller | 非 LLM 控制面 | Release 状态机；Quality API 写面唯一调用方；CAS revision 推进灰度/全量/回滚；UNKNOWN→reconcile 对账 | 不决定"是否发布"（那是审批+门禁的结论），只负责"如何安全地执行发布" |
| Caseload Controller | 非 LLM 控制面（Phase 2） | Agent 弹性面：接收扩缩容申请 → 按 desired 公式决策 → 经受限 RBAC 对 AgentTeams 执行 create/drain/remove + 资源凭证对账 | 不由 Agent 现场创建/销毁 Agent；不以 CR 消失为删除成功依据（见 §8.4，S0-001） |
| Experiment Runner | 隔离执行器 | 按 §4 归因实验协议执行 5-cell / 2³ 全因子实验：冻结探针、随机臂序、cell 级幂等续跑、原始计数落库 | 不做裁决推理（裁决规则是 §4.6 的确定性代码，不是 LLM 判断） |
| Eval/Gate | 非 LLM 执行面 | 双轨门禁：规则轨 + LLM 裁判轨（裁判模型 ≠ 运动员模型）；contract/replay 确定性轨与 live-provider E2E 轨分开报告；产出门禁报告（hash 进 WorkOrder） | 裁判 LLM 只打分，不直接改门禁结论（结论按阈值规则计算） |
| AgentTeams 执行层 | 概率性执行面 | 4 常设 Agent（质量官/采集员/守门员/案例官）+ 弹性 Worker（归因师/修复师）：取证、分析、起草、提请 | 不持有权威状态；不直接调 Quality API 写面；审批人以外不可批准 |
| Trust Ledger | 控制面模块 | 信任记账：risk_class × autonomy_state 二维账本，Wilson 判据评估，晋升提请 | 不自动晋升（系统攒够证据只能"提请"，人确认才生效） |
| Audit / Evidence | 控制面模块 | 审计事件入 PG 权威存储，写失败即拒业务；audit.jsonl 仅导出物；Evidence Bundle 归集 | 不异步兜底落盘（失败即拒，见 §11.4） |
| mcp-servers | 工具连接层 | 5 个 MCP server + trust-ledger 模块（§9）；Higress 托管凭证 | 不在 Server 内持有业务状态；无私有协议扩展 |

---

## 2. Quality API v2 契约要点

契约以 OpenAPI 冻结于 `contracts/`，附 conformance suite（先对空实现跑红，再实现到全绿）。本节为要点，字段级 schema 以 `contracts/` 为准。

### 2.1 资源模型

```
VersionSet (不可变)
├── version_set_id      # vs-<ULID>
├── revision            # 单调递增整数，全局 CAS 依据
├── prompt              # {template_ref, git_commit, digest}
├── kb_manifest         # {entries: [{doc_id, digest}], manifest_digest}
├── model               # {name, params: {temperature, top_p, ...}, params_digest}
└── status              # draft | staged | canary | promoted | rolled_back
```

- **不可变**：任何修改产生新 VersionSet（新 revision），原 VersionSet 不可改、不可删。
- digest 全部为 SHA-256（内容寻址），保证"实验报告里的版本"与"线上跑的版本"可对账。

### 2.2 写面（仅 Release Controller 可调）

| 端点 | 语义 | 幂等与并发控制 |
|------|------|--------------|
| `POST /quality/v2/versionsets` | 创建 draft | `Idempotency-Key` 头；同键同参返回首次结果 |
| `POST /quality/v2/versionsets/{id}:stage` | draft → staged | `If-Match: <revision>` 或 body `expected_revision`；不匹配 → `409 CAS_CONFLICT` |
| `POST /quality/v2/versionsets/{id}:canary` | staged → canary（带流量权重与观察窗参数） | 同上；异步：返回 `operation_id` |
| `POST /quality/v2/versionsets/{id}:promote` | canary → promoted（全量） | 同上；异步 |
| `POST /quality/v2/versionsets/{id}:rollback` | 任意活跃态 → rolled_back（回到指定前一 promoted revision） | 同上；异步 |
| `GET /quality/v2/operations/{operation_id}` | 异步结果查询：`pending / succeeded / failed / unknown` | 只读 |

写面通用约定：

- 每个变更请求必须携带：`If-Match`/`expected_revision`（CAS）+ `Idempotency-Key`（24h 服务端去重；同键异参 → `409 IDEMPOTENCY_CONFLICT`）。
- 异步操作：接受即返 `202 + operation_id`；调用方轮询结果；**查询超时或服务端重启导致结果不确定时，调用方必须将 Release 置 UNKNOWN 并进入 reconcile**（§3.5），严禁臆断成功或失败后盲目重试（重试必须带原 Idempotency-Key）。
- 服务端对每个 revision 的迁移做单调校验：禁止 promote 一个已 rolled_back 的 VersionSet。

### 2.3 读面

| 端点 | 语义 | 说明 |
|------|------|------|
| `GET /quality/v2/versionsets/{id}` / `:status` | 查询 VersionSet 状态与当前线上 revision | reconcile 的对账依据 |
| `GET /logs` | 应用请求日志（trace 级，含 prompt 版本、KB 命中、模型参数、延迟、输出摘要） | 已 PII 脱敏；供归因取证 |
| `GET /feedback` | 用户反馈流（投诉、评分、点踩） | 投诉轮询兜底通道的数据源 |

---

## 3. 七个状态机

七个状态机的**权威状态全部存于 Case Controller 的 PG `aggregates` 表**（§7.2），迁移由控制面以确定性代码执行；Agent 经 MCP 提交"建议事件"（如 `AttributionSuggested`），控制面校验前置条件后裁决是否迁移。所有迁移追加 `events` 流水（事件溯源），并在同一事务内写 `outbox`（§7.4）。

通用失败语义（每个状态机只列特化部分）：

- **Worker 丢失**：lease 过期且 fencing token 失效（§7.5）→ 任务重新入队，状态机停留在原状态；已完成的幂等单元（如实验 cell、评测探针）结果保留，丢失单元重跑。
- **人工接管**：任意状态可被人工置 `ESCALATED`（Case 层）或对应状态机的人工终态；接管期间一切自动流转暂停，恢复需人工显式动作。
- **非法迁移**：任何不在迁移表内的请求 → `STATE_CONFLICT`，拒绝并审计。

### 3.1 Case 状态机

**状态枚举**：

```
NEW → TRIAGED → ATTRIBUTING → ATTRIBUTED → FIX_DRAFTING → GATING
  → AWAITING_APPROVAL → RELEASING → VERIFYING → REPLYING → ARCHIVED（终态）
旁路：MERGED（终态）/ CLOSED（终态）/ ESCALATED（任意态可入，人工接管）
```

**迁移表**：

| 当前态 | 触发事件 | 动作（控制面） | 下一态 |
|--------|---------|--------------|--------|
| — | `ComplaintReceived`（inbox 去重通过） | 立案，分配 case_id，创建取证任务 | NEW |
| — | `ComplaintReceived`（dedup_key 命中 open case） | 事件追加到主 case evidence，本投诉记 MERGED | MERGED |
| NEW | 采集员提交 `TriageSuggested` + 取证引用 | 校验取证完整性 → 接受 | TRIAGED |
| TRIAGED | 归因实验创建（Experiment=PLANNED） | 关联 experiment_id | ATTRIBUTING |
| ATTRIBUTING | Experiment 终态=ATTRIBUTED | 记录归因结论（故障层+效应量+CI） | ATTRIBUTED |
| ATTRIBUTING | Experiment 终态=INCONCLUSIVE 且补实验次数未超限 | 触发补实验（新 attempt） | ATTRIBUTING |
| ATTRIBUTING | Experiment 终态=INCONCLUSIVE 且超限 / 人工决定不修 | 记录原因 | CLOSED 或 ESCALATED |
| ATTRIBUTED | 修复师提交 WorkOrder（ChangeSet=DRAFT） | 关联 workorder_id | FIX_DRAFTING |
| FIX_DRAFTING | ChangeSet=GATING | — | GATING |
| GATING | ChangeSet=GATE_PASSED | 门禁报告 hash 绑定进 WorkOrder | AWAITING_APPROVAL |
| GATING | ChangeSet=GATE_FAILED | WorkOrder 作废（可回 FIX_DRAFTING 起草新单） | FIX_DRAFTING |
| AWAITING_APPROVAL | Approval=APPROVED | 创建 Release | RELEASING |
| AWAITING_APPROVAL | Approval=REJECTED / EXPIRED | 记录审批结论 | FIX_DRAFTING（重新起草）或 CLOSED |
| RELEASING | Release=PROMOTED | — | VERIFYING |
| RELEASING | Release=ROLLED_BACK | 记录回滚；可重新归因（若修复无效） | ATTRIBUTING 或 CLOSED |
| VERIFYING | 发布后验证通过（探针恢复 + 线上观测窗达标） | — | REPLYING |
| VERIFYING | 验证失败 | 触发回滚流程 | RELEASING |
| REPLYING | Notification=SENT（飞书回复原群成功） | — | ARCHIVED |
| REPLYING | Notification=DEAD（通知重试耗尽） | 升级人工，**不得 ARCHIVED** | ESCALATED |
| 任意态 | 人工接管 | 暂停自动流转 | ESCALATED |

**失败语义（重复与合并）**：去重键 `dedup_key = hash(source_channel, 用户标识, 归一化投诉内容, 时间窗)`【待定：时间窗取值，建议 24h】。命中已关闭 case 不合并，新立案并关联历史 case_id。

### 3.2 Experiment 状态机

**状态枚举**：

```
PLANNED → PROBES_FROZEN → RUNNING → ANALYZING
  → ATTRIBUTED / INCONCLUSIVE / CONFOUNDED（终态）
CONFOUNDED → FULL_FACTORIAL_PLANNED → FULL_FACTORIAL_RUNNING → ANALYZING（2³ 轮）
```

**迁移表**：

| 当前态 | 触发事件 | 动作 | 下一态 |
|--------|---------|------|--------|
| — | Case=TRIAGED，归因师申请实验 | 生成实验计划（5-cell，版本 digest 锁定） | PLANNED |
| PLANNED | 探针集写入并冻结（discovery + hidden confirmation + unaffected controls，含 digest） | 探针集不可变 | PROBES_FROZEN |
| PROBES_FROZEN | Runner 领单 | 随机臂序，逐 cell 执行（cell 级幂等） | RUNNING |
| RUNNING | 全部 cell 原始计数落库 | — | ANALYZING |
| ANALYZING | §4.6 裁决计算完成 | 三态裁决 + Δ + CI 落库 | ATTRIBUTED / INCONCLUSIVE / CONFOUNDED |
| CONFOUNDED | 自动（强制） | 展开 2³ 全因子计划 | FULL_FACTORIAL_PLANNED |
| FULL_FACTORIAL_PLANNED | 同 PLANNED→RUNNING 链路 | 8 cell 执行 | FULL_FACTORIAL_RUNNING |
| FULL_FACTORIAL_RUNNING | 完成 | 因子效应分解 | ANALYZING |

**失败语义**：

- **Worker 丢失**：RUNNING 中 lease 丢失 → 已完成 cell 保留，丢失 cell 以同臂序参数重跑（cell 幂等键 = `experiment_id:cell:probe_id:rep`）。
- **环境不可信**：unaffected controls 任一失败 → 本轮结果作废，直接 INCONCLUSIVE（reason=`ENV_UNTRUSTED`）。
- **基线臂未恢复**：G 臂 Δ 不显著 → INCONCLUSIVE（reason=`BASELINE_NOT_RESTORED`），禁止归因。
- **补实验**：INCONCLUSIVE 可回 PLANNED 起新 attempt（加重复次数或加探针）；补实验次数上限【待定：建议 2 次】，超限 → Case 升级人工。
- 全因子后仍无法定位 → INCONCLUSIVE（reason=`INTERACTION_UNRESOLVED`）→ Case 升级人工。

### 3.3 ChangeSet（WorkOrder）状态机

**状态枚举**：

```
DRAFT → FROZEN → GATING → GATE_PASSED → AWAITING_APPROVAL
  → APPROVED → RELEASED（终态，绑定 release_id）
失败终态：GATE_FAILED / REJECTED / EXPIRED / SUPERSEDED
```

**迁移表**：

| 当前态 | 触发事件 | 动作 | 下一态 |
|--------|---------|------|--------|
| — | 修复师提交 WorkOrder payload | schema 校验 + 单变量纪律校验（仅一层变更） | DRAFT |
| DRAFT | 修复师定稿 | 计算并绑定 hash（§5.1），此后 payload 任何字节不可改 | FROZEN |
| FROZEN | 守门员触发门禁 | 创建 Eval（QUEUED） | GATING |
| GATING | Eval=REPORTED 且 verdict=PASS | 门禁报告 hash 写入 WorkOrder，重算 hash | GATE_PASSED |
| GATING | Eval=REPORTED 且 verdict=FAIL | 门禁报告归档，WorkOrder 终态不可复活 | GATE_FAILED |
| GATE_PASSED | 系统向飞书提请审批 | 携带 WorkOrder hash + 证据摘要 | AWAITING_APPROVAL |
| AWAITING_APPROVAL | 人批准（ApprovalGrant 绑定 hash+nonce+expiry） | 校验 hash 一致 + nonce 未用 + 未过期 | APPROVED |
| AWAITING_APPROVAL | 人拒绝 | 记录拒绝理由 | REJECTED |
| AWAITING_APPROVAL | 超 expiry 未决 | 审批失效 | EXPIRED |
| APPROVED | Release 创建并绑定 | — | RELEASED |
| 非终态 | 同 case 新 WorkOrder 进 FROZEN | 旧单作废 | SUPERSEDED |

**失败语义（审批拒绝/过期）**：REJECTED/EXPIRED 均为终态，**不可复活**——修复师必须起草新 WorkOrder（新 id、新 hash），旧单留档供审计。审批通过的 WorkOrder 若在执行前被人工撤销，走 SUPERSEDED 语义并审计。

### 3.4 Eval 状态机

**状态枚举**：

```
QUEUED → RUNNING → REPORTED（终态，verdict=PASS|FAIL）
     → EVAL_FAILED（终态，基础设施失败）
```

**迁移表与双轨约定**：

| 当前态 | 触发事件 | 动作 | 下一态 |
|--------|---------|------|--------|
| — | ChangeSet=GATING / 巡检触发 | 生成评测计划（回归集 + 探针），两轨各自排队 | QUEUED |
| QUEUED | Runner 领单 | 确定性轨（contract/replay）与 live 轨（live-provider E2E）**独立执行、独立计时** | RUNNING |
| RUNNING | 两轨均出报告 | 按 §9.3 阈值规则合成 verdict；**两轨分开报告**，禁止合并成单一分数 | REPORTED |
| RUNNING | runner 崩溃/超时 | 重排队（attempt+1，上限 3） | QUEUED |
| RUNNING | 重试耗尽 | 人工排查 | EVAL_FAILED |

**失败语义**：

- live-provider 不可用（额度/网络）：live 轨标 `UNAVAILABLE`，确定性轨照常报告；此时门禁是否可仅凭确定性轨放行【待定：建议 MVP 阶段不可放行，转人工】。
- 裁判模型与运动员模型必须不同（配置级强制校验，相同则拒绝运行）。
- 探针/回归集版本（digest）必须写入报告，保证报告可复算。

### 3.5 Release 状态机

**状态枚举**：

```
PREPARED → DRAFT_CREATED → STAGED → CANARYING → CANARY_VERIFYING
  → PROMOTING → PROMOTED（终态）
任意执行态 → ROLLING_BACK → ROLLED_BACK（终态）
异常：UNKNOWN → reconcile；ROLLBACK_FAILED → BLOCKED（人工终态）
```

**迁移表**：

| 当前态 | 触发事件 | 动作 | 下一态 |
|--------|---------|------|--------|
| — | ChangeSet=APPROVED | 创建 release，锁定目标 WorkOrder hash 与输入 revision | PREPARED |
| PREPARED | 写面 `POST /versionsets` 成功 | 持有新 revision | DRAFT_CREATED |
| DRAFT_CREATED | `:stage` 成功（CAS） | — | STAGED |
| STAGED | `:canary` 接受（202+operation_id） | 启动灰度阶梯与观察窗 | CANARYING |
| CANARYING | 本阶梯 operation=succeeded 且指标达标 | 进下一阶梯 / 末阶梯完成 | CANARYING / CANARY_VERIFYING |
| CANARYING | 指标不达标（错误率/延迟/探针回归超阈） | 自动 `:rollback` | ROLLING_BACK |
| CANARY_VERIFYING | 观察窗达标 | `:promote`（CAS） | PROMOTING |
| PROMOTING | operation=succeeded | 线上 revision 对账一致 | PROMOTED |
| 任意执行态 | 人工触发回滚 / 验证失败 | `:rollback` 到前一 promoted revision | ROLLING_BACK |
| ROLLING_BACK | operation=succeeded + 对账一致 | — | ROLLED_BACK |
| ROLLING_BACK | operation=failed / 对账不一致 | 重试（同 Idempotency-Key）；耗尽后告警 | ROLLBACK_FAILED→BLOCKED |
| 任意执行态 | operation 查询超时 / 控制器重启 / 结果 unknown | 冻结推进，进入 reconcile 循环 | UNKNOWN |

**失败语义（发布 UNKNOWN→reconcile）**：

- reconcile 是确定性对账循环：以 `GET :status` 读取线上真实 revision 与状态，与本地期望比对：
  - 线上已达目标态 → 迁移到对应正常态继续；
  - 线上停在中间态 → 以**原 Idempotency-Key** 重放当前步（服务端去重保证不产生第二次副作用）；
  - 线上状态与期望冲突且无法自动判定（如 revision 被外部改动）→ BLOCKED，升级人工。
- reconcile 周期【待定：建议 5s 起步指数退避至 5min 上限】；UNKNOWN 态下**禁止**发起任何新写面调用。
- **回滚失败**：ROLLBACK_FAILED 是最高优先告警（BLOCKED），人工介入；回滚目标 revision 始终保持可操作（前一 promoted VersionSet 不可删）。

**灰度阶梯**：阶段参数（流量比例序列与各阶梯观察窗）为配置项【待定：建议 5% → 25% → 100%，每阶梯观察窗 ≥10min，MVP 可压缩为演示值】。

### 3.6 Notification 状态机

**状态枚举**：

```
PENDING → SENDING → SENT（终态）
      → FAILED → RETRYING → SENDING（attempt+1）
      → 重试耗尽 → DEAD（终态，升级人工）
```

**迁移表**：

| 当前态 | 触发事件 | 动作 | 下一态 |
|--------|---------|------|--------|
| — | 控制面写 outbox（同事务） | 生成 notification（含幂等键） | PENDING |
| PENDING | 发送器领单 | 调 mcp-notification | SENDING |
| SENDING | 对端确认（飞书 message_id / mock ack） | outbox 标记已投 | SENT |
| SENDING | 失败/超时 | 记录错误 | FAILED |
| FAILED | 到达重试时点（指数退避） | — | RETRYING |
| RETRYING | 发送器再次领单 | attempts+1 | SENDING |
| FAILED/RETRYING | attempts 达上限【待定：建议 5 次】 | 升级人工；关联 case 若处 REPLYING 则置 ESCALATED | DEAD |

**失败语义（通知失败）**：at-least-once 投递 + 幂等键去重（接收侧以幂等键判重，重复投递不产生重复消息）；DEAD 不允许静默丢弃——回复投诉原处是闭环收口要件，case 不得带 DEAD 通知进入 ARCHIVED。

### 3.7 Trust 状态机（per `(risk_class, action_type)` 账本条目）

**状态枚举**（autonomy_state，plan-v3 §2.3.4）：

```
MANUAL → ELIGIBLE → AWAITING_CONFIRMATION → AUTO_ENABLED
任意记账态 → SUSPENDED（验证失败 + 冷却）→ ELIGIBLE（新 epoch）
对账异常 → BLOCKED_UNKNOWN（仅人工可解）
```

**迁移表**：

| 当前态 | 触发事件 | 动作 | 下一态 |
|--------|---------|------|--------|
| — | 新 (risk_class, action_type) 首次出现 | 建账（epoch=1, trials=0, successes=0） | MANUAL |
| MANUAL | risk_class=R1 且 action_type 在白名单 | 开始记账 | ELIGIBLE |
| ELIGIBLE | 当前 epoch Wilson 双侧 95% 下界 > 0.9（§6.3） | 生成证据表，飞书提请人确认 | AWAITING_CONFIRMATION |
| AWAITING_CONFIRMATION | 人确认 | 生效；此后该组合自动执行 | AUTO_ENABLED |
| AWAITING_CONFIRMATION | 人拒绝 / 提请过期 | 回记账态，继续攒证据 | ELIGIBLE |
| ELIGIBLE / AUTO_ENABLED | 该组合一次动作验证失败 | 冷却计时；epoch+1（计数清零重攒） | SUSPENDED |
| SUSPENDED | 冷却期满【待定：冷却时长，建议 24h】 | 重新记账 | ELIGIBLE |
| 任意态 | 账本与控制面状态对账不一致 | 冻结，仅人工解锁 | BLOCKED_UNKNOWN |

**硬约束**：

- `risk_class = R2_HIGH_IMPACT` **永远逐次审批**：迁移表中 R2 不得离开 MANUAL，任何对 R2 的晋升请求直接拒绝并审计。
- 白名单外的动作类型不得进入 ELIGIBLE。
- **MVP 演示口径**：记账正常发生，但晋升评估输出"拒绝晋升"判定事件（`PromotionRejected`，reason 含具体数字，如 `3/3 LB=0.4385<0.9`）——拒绝是统计纪律的证据，不是功能缺失。

---

## 4. 归因实验协议（plan-v3 §2.3.2 实现规格）

目标：把"归因"从经验判断变成可复算的对照实验。协议全程确定性：Runner 执行与裁决计算均为非 LLM 代码；归因师（LLM）只负责提出实验计划建议与解读报告，**裁决不由 LLM 给出**。

### 4.1 5-cell 最小矩阵

记 `P`=prompt 版本、`K`=KB manifest 版本、`M`=模型+参数版本；下标 `1`=当前生产版本（带病），`0`=上一已知良好版本（来源：版本历史，取最近一次 promoted 且无未结质量 case 的 VersionSet）。

| cell | 配置 | 语义 |
|------|------|------|
| C | (P1, K1, M1) | 当前生产配置——应复现 badcase（不恢复） |
| RP | (P0, K1, M1) | 仅回滚 prompt——若恢复则指向 prompt 层 |
| RK | (P1, K0, M1) | 仅回滚 KB——若恢复则指向 KB 层 |
| RM | (P1, K1, M0) | 仅回滚模型+参数——若恢复则指向模型层 |
| G | (P0, K0, M0) | 全部回滚至已知良好基线——应恢复（ sanity 基线） |

每个 cell 的完整版本 digest（prompt git commit、KB manifest_digest、model+params digest）必须写入实验记录，保证"实验用的版本"与"版本库里的版本"可对账。

### 4.2 探针集与冻结

- **探针**=可机器判定的测试用例：输入 + 判定规则（规则轨：断言/正则/结构化比对；裁判轨：LLM 裁判按 rubric 打分，pass/fail 二值化）。
- **探针三分集**：
  - `discovery`：从本次 badcase 衍生的探针，用于发现恢复信号；
  - `hidden confirmation`：冻结的确认探针，对归因师不可见，仅用于验证归因结论不过拟合到 discovery；
  - `unaffected controls`：与本次故障无关的历史稳定探针，每轮实验必跑——任一失败即判定实验环境不可信，本轮作废（§3.2）。
- **冻结**：探针集在 PROBES_FROZEN 态写入并锁定（含 digest），实验全程不可改；新增探针只能进下一 attempt。

### 4.3 执行规程

| 规程 | 约定 |
|------|------|
| 随机臂序 | cell 执行顺序随机化（以 experiment_id 为种子，可复现），消除顺序/预热偏差 |
| 重复调用 | 每 cell 每探针重复 `n` 次调用【待定：建议 n=5，MVP 可按探针规模调整】；演示期 LLM 调用 `temperature=0` |
| 判定 | 每次调用产出 pass/fail；原始计数（passes, trials）按 `cell × probe × rep` 粒度落库，不先聚合成分数 |
| 幂等 | cell 幂等键 `experiment_id:cell:probe_id:rep`，Worker 丢失后重跑不产生重复计数 |
| 隔离 | Experiment Runner 与被治理应用的生产流量隔离；实验调用打标（experiment_id）便于日志侧过滤 |

### 4.4 统计量定义

- cell 成功率：`p_cell = Σ passes / Σ trials`（对 cell 内全部探针与重复求和；探针等权）。
- 效应量（恢复幅度）：`Δ_arm = p_arm − p_C`，arm ∈ {RP, RK, RM, G}。C 为带病基线，故"恢复"表现为 Δ>0。

### 4.5 置信区间计算

- 单比例的区间用 **Wilson score 区间**（公式见 §6.2，z=1.959964）。
- 两比例差（Δ）的 95% CI 用 **Newcombe hybrid score interval**（Method 10）：
  - 对 p_arm 算 Wilson 区间 (l₁, u₁)，对 p_C 算 Wilson 区间 (l₂, u₂)；
  - `LB_Δ = Δ − √((p_arm − l₁)² + (u₂ − p_C)²)`
  - `UB_Δ = Δ + √((u₁ − p_arm)² + (p_C − l₂)²)`
- 恢复显著判据：`LB_Δ > δ_min`，δ_min 为最小有实际意义效应量【待定：建议 δ_min=0.2，结合探针规模校准】。

### 4.6 三态裁决规则（确定性代码）

按序判定，先命中先生效：

| # | 条件 | 裁决 |
|---|------|------|
| R1 | unaffected controls 任一失败 | INCONCLUSIVE（reason=`ENV_UNTRUSTED`），本轮作废 |
| R2 | G 臂恢复不显著（LB_Δ(G) ≤ δ_min） | INCONCLUSIVE（reason=`BASELINE_NOT_RESTORED`）——已知良好基线都复现不了，实验不可信 |
| R3 | RP/RK/RM 中 ≥2 个臂恢复显著，**或** RP/RK/RM 均不显著（G 已恢复、单因素臂无一恢复 → 纯交互嫌疑） | CONFOUNDED——**强制 2³ 全因子**（plan-v3 §2.3.2 原文："多臂同恢复或均不恢复→扩 2³ 全因子识别交互"） |
| R4 | RP/RK/RM 中恰好 1 个臂恢复显著，且该结论在 hidden confirmation 探针上同向复现 | ATTRIBUTED，故障层=该臂对应层（RP→prompt / RK→kb / RM→model） |
| R5 | 单臂显著但 hidden confirmation 未同向复现 | INCONCLUSIVE（reason=`CONFIRMATION_MISMATCH`）→ 补实验（加大 n / 扩充探针）或升级人工 |

**2³ 全因子（CONFOUNDED 后续）**：P/K/M 三因子各取 0/1 共 8 cell，同规程执行；对每探针结果拟合饱和模型 `y ~ P*K*M`（主效应 + 全部二阶/三阶交互，效应按 ±1 编码的对比系数估计），输出显著项清单；归因结论 = 显著主效应/交互项对应层组合（报告 `attributed_layers` 与 `interaction_terms`）。全因子后仍无法定位 → INCONCLUSIVE（`INTERACTION_UNRESOLVED`）→ 升级人工。

### 4.7 归因实验报告（Evidence Bundle 组成）

报告必须包含：experiment_id、case_id、全部 cell 的版本 digest、探针集 digest（三分集）、每 cell 原始计数、Δ 与各 CI、裁决状态与 reason、hidden confirmation 复现结果、执行环境（模型端点、temperature、时间窗）。报告 hash 进 case 证据链，供评审复算。

---

## 5. WorkOrder 与 Approval

### 5.1 WorkOrder（不可变修复单）

```json
{
  "workorder_id": "wo-01J…",
  "case_id": "case-01J…",
  "target": { "app": "xiaozhi-cs", "layer": "prompt | kb | model" },
  "input_versions": { "prompt_digest": "…", "kb_manifest_digest": "…", "model_params_digest": "…" },
  "diff": { "通道特定内容：prompt patch / KB 修订条目 / 模型参数变更" },
  "gate_report": { "eval_id": "eval-01J…", "report_hash": "sha256:…" },
  "single_factor_declaration": "本单仅变更 target.layer 一层",
  "expiry": "RFC3339，超过后本单不可再被执行",
  "nonce": "一次性随机串",
  "hash": "sha256(以上全部字段的规范化序列化)"
}
```

- **不可变**：FROZEN 后任何字段变更 → hash 变化 → 原审批自动失效（hash 不匹配）。
- **单变量纪律**：`target.layer` 单选；控制面校验 diff 不越层（如 prompt 单不得夹带 KB 变更），越层拒绝进 GATING。
- **门禁报告是必要附件**：GATE_PASSED 迁移时把 `gate_report.report_hash` 写入并重算 hash——先门禁后定稿，顺序不可逆。

### 5.2 ApprovalGrant（审批授权）

```json
{
  "approval_id": "apv-01J…",
  "workorder_hash": "sha256:…（批准的对象，精确到 hash）",
  "nonce": "wo 的 nonce + 审批侧随机串",
  "expiry": "RFC3339",
  "decided_by": "人工审批人标识（飞书 user_id）",
  "decided_at": "RFC3339",
  "signature": "控制面对 approval_id+workorder_hash+nonce+expiry 的签名"
}
```

- **审批即批 hash**：审批动作绑定的是 WorkOrder hash 而非 workorder_id——内容被掉包（哪怕一个字节）则 hash 不匹配，执行拒绝（`APPROVAL_MISMATCH`）。
- **防重放**：nonce 一次性，消费即在 PG `approvals` 表标记已用（唯一约束），复用 → `APPROVAL_REPLAYED`；超 expiry → `APPROVAL_EXPIRED`。TTL【待定：建议 30min，审批卡片明示】。
- 审批请求、决定、消费三个事件全部入审计（§11.4）。
- zeroops 的 `common/approval.py`（静态 token 可重放）**不直接沿用**，本规格为重写依据。

---

## 6. 信任账本数学

### 6.1 二维模型（plan-v3 §2.3.4 枚举原样采用）

```
risk_class      = R0_READ | R1_REVERSIBLE_WRITE | R2_HIGH_IMPACT
autonomy_state  = MANUAL | ELIGIBLE | AWAITING_CONFIRMATION |
                  AUTO_ENABLED | SUSPENDED | BLOCKED_UNKNOWN
```

- 账本条目粒度 = `(risk_class, action_type)` 组合；action_type 为例：`case.triage`、`workorder.draft.prompt`、`release.canary_step`、`notification.reply_origin`。
- 仅白名单内 R1 动作可沿 ELIGIBLE→AWAITING_CONFIRMATION→AUTO_ENABLED 晋升；R0 只读无需晋升；R2 永远逐次审批（§3.7 硬约束）。

### 6.2 Wilson score 区间（双侧 95%）

设当前 evidence epoch 内：`trials = n`，`successes = s`，`p̂ = s/n`，`z = z(0.975) = 1.959964`：

```
denominator = 1 + z²/n
centre      = (p̂ + z²/(2n)) / denominator
half_width  = z · √( p̂(1−p̂)/n + z²/(4n²) ) / denominator
LB          = centre − half_width
UB          = centre + half_width
```

- **晋升判据**：`LB > 0.9`（严格大于）。
- **MVP 演示口径验证数**：`s=3, n=3` → centre≈0.7192，half≈0.2807，**LB≈0.4385 < 0.9 → 拒绝晋升**（与 plan-v3 "3/3 下界 0.44<0.9" 一致；contracts/ 附 Wilson 测试向量，实现须全过）。

### 6.3 记账规则

| 规则 | 约定 |
|------|------|
| 一次动作 = 一个样本 | 一次动作中的多条探针/多步检查只算 1 个 trial（成功=动作整体验证通过），杜绝用探针数灌样本 |
| evidence epoch | 每次进入 SUSPENDED 后 epoch+1，计数清零重攒；账本存**原始整数计数** `(trials, successes)`，不存比例（比例可复算，原始计数不可还原） |
| 验证失败 | 该组合 autonomy_state → SUSPENDED + 冷却（§3.7）；冷却期满回 ELIGIBLE |
| 晋升流程 | 系统攒够证据（LB>0.9）→ 飞书带证据表提请（含 n/s/LB/动作历史引用）→ **人确认才生效**；系统不可自行晋升 |
| 账本 API | 见 §9.6 trust-ledger 模块接口 |

---

## 7. 数据存储

存储：PostgreSQL（本地 docker-compose）+ pgvector（案例库）；云上 PolarDB for PostgreSQL 同接口，仅连接串差异。全部权威状态在 PG，**审计权威源 = 数据库**。

### 7.1 表结构总览

| 表 | 用途 | 关键约束 |
|----|------|---------|
| `aggregates` | 七状态机的权威状态（聚合根） | PK `(aggregate_type, aggregate_id)`；`revision BIGINT` 每次迁移 +1，CAS 依据 |
| `events` | 事件溯源流水（全部状态迁移事件） | UNIQUE `(aggregate_id, seq)`；只增不改 |
| `inbox` | 投诉接入去重 | PK `dedup_key`；插入冲突即走合并路径 |
| `outbox` | 可靠外发（通知/审批卡片/周报） | 与状态迁移同事务写入；发送器轮询消费 |
| `leases` | Worker 领单租约 | `fencing_token` 全局单调递增 |
| `workorders` | WorkOrder 留档 | `hash` 唯一；payload 落库即不可改 |
| `approvals` | 审批授权 | `nonce` 唯一（防重放）；状态机 pending→consumed/rejected/expired |
| `trust_ledger` | 信任账本 | PK `(risk_class, action_type, epoch)`；原始整数计数 |
| `audit` | 权威审计 | 只增不改；写失败即拒业务（§11.4） |
| `casebase` | 案例库（pgvector） | `embedding VECTOR`；版本化 |

### 7.2 aggregates / events（聚合 + 事件溯源）

```sql
CREATE TABLE aggregates (
  aggregate_type TEXT NOT NULL,           -- CASE|EXPERIMENT|CHANGESET|EVAL|RELEASE|NOTIFICATION|TRUST
  aggregate_id   TEXT NOT NULL,
  state          TEXT NOT NULL,           -- §3 状态枚举
  payload        JSONB NOT NULL,          -- 状态关联数据（结论引用、关联 id 等）
  revision       BIGINT NOT NULL,         -- CAS 计数，初始 1
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (aggregate_type, aggregate_id)
);

CREATE TABLE events (
  event_id       TEXT PRIMARY KEY,        -- evt-<ULID>
  aggregate_type TEXT NOT NULL,
  aggregate_id   TEXT NOT NULL,
  seq            BIGINT NOT NULL,         -- 聚合内单调序号
  event_type     TEXT NOT NULL,           -- §10 事件目录
  payload        JSONB NOT NULL,
  trace_id       TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (aggregate_id, seq)
);
```

迁移事务纪律（每一次状态迁移都是一个事务）：

```sql
BEGIN;
-- 1) CAS 校验并更新聚合
UPDATE aggregates SET state=$new, payload=$p, revision=revision+1, updated_at=now()
 WHERE aggregate_type=$t AND aggregate_id=$id AND revision=$expected;
-- 影响行数 0 → CAS_CONFLICT，调用方刷新后重决策（不盲目重试）
-- 2) 追加事件
INSERT INTO events (...) VALUES (...);
-- 3) 需要外发时同事务写 outbox
INSERT INTO outbox (...) VALUES (...);
-- 4) 写审计（失败 → 整个事务回滚，业务拒绝）
INSERT INTO audit (...) VALUES (...);
COMMIT;
```

### 7.3 inbox（投诉去重立案）

```sql
CREATE TABLE inbox (
  dedup_key   TEXT PRIMARY KEY,           -- §3.1 去重键
  source      TEXT NOT NULL,              -- feishu_webhook | feishu_poll | api
  raw_payload JSONB NOT NULL,             -- 原始报文（PII 已脱敏）
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  case_id     TEXT,                       -- 立案/合并归属
  disposition TEXT NOT NULL               -- FILED | MERGED
);
```

`INSERT … ON CONFLICT (dedup_key) DO NOTHING` 返回未插入 → 查归属 case → 若 open 则合并（`MERGED` + 事件追加主 case）；若已关闭则换键新立案并关联历史 case_id。

### 7.4 outbox（可靠外发）

```sql
CREATE TABLE outbox (
  outbox_id     TEXT PRIMARY KEY,         -- obx-<ULID>，兼作通知幂等键
  aggregate_id  TEXT NOT NULL,
  channel       TEXT NOT NULL,            -- feishu_origin_reply | feishu_approval_card | feishu_weekly | matrix_log
  payload       JSONB NOT NULL,
  status        TEXT NOT NULL,            -- PENDING|SENDING|SENT|FAILED|RETRYING|DEAD（§3.6）
  attempts      INT NOT NULL DEFAULT 0,
  next_retry_at TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at       TIMESTAMPTZ
);
```

发送器 at-least-once；接收侧以 `outbox_id` 判重。outbox 与状态迁移同事务，杜绝"状态变了但通知丢了"。

### 7.5 leases / fencing token（Worker 领单与脑裂防护）

```sql
CREATE TABLE leases (
  resource_id   TEXT PRIMARY KEY,         -- 任务/聚合维度租约键
  owner_id      TEXT NOT NULL,            -- Worker 标识
  fencing_token BIGINT NOT NULL,          -- 全局单调递增（序列发号）
  acquired_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL
);
```

- 领单 = `INSERT/UPDATE leases` 成功且持有最新 `fencing_token`；租约时长【待定：建议 60s】，Worker 心跳续租。
- Worker 携带 fencing_token 提交产物；控制面校验 token 仍为最新才接受——过期 Worker（脑裂）的写入被拒绝（`LEASE_LOST`）。
- lease 过期未续 → 任务自动重新可领（对应 §3 各状态机的"Worker 丢失"语义）。

### 7.6 审计表（权威存储，失败即拒）

```sql
CREATE TABLE audit (
  audit_id      TEXT PRIMARY KEY,         -- aud-<ULID>
  ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor         TEXT NOT NULL,            -- agent_id | controller | human:<feishu_uid>
  action        TEXT NOT NULL,            -- 如 workorder.approve / release.promote
  target        TEXT NOT NULL,            -- 目标资源 id
  params_digest TEXT NOT NULL,            -- 规范化参数 SHA-256（敏感原文不落库）
  result        TEXT NOT NULL,            -- success | denied | error
  error_code    TEXT,
  trace_id      TEXT NOT NULL,
  evidence_refs JSONB                     -- 关联证据引用
);
```

- 审计写在业务事务内：插入失败 → 事务回滚 → 业务拒绝执行（**不放行**）。
- `audit.jsonl` 仅为定时导出物，供评审翻阅；导出物与库不一致时以库为准。
- zeroops `audit.py`（失败放行）不沿用，本节为重写依据。

### 7.7 案例库（pgvector）

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE casebase (
  doc_id     TEXT PRIMARY KEY,            -- kb-01J…
  doc_type   TEXT NOT NULL,               -- case | probe_pack | postmortem | skill_candidate
  content    TEXT NOT NULL,               -- markdown 正文（已脱敏）
  embedding  VECTOR(1536),                -- 【待定：维度随嵌入模型定，contracts 冻结】
  metadata   JSONB NOT NULL,              -- fault_layer, app, ground_truth_ref, version…
  version    INT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON casebase USING ivfflat (embedding vector_cosine_ops);
```

写入入口唯一：仅案例官（经 mcp-casebase-knowledge `kb.upsert`）；向量检索退化策略见 §9.5。

---

## 8. Agent 组织与仲裁

### 8.1 编制

| Agent | 类型 | 职责 | 主要工具面 |
|-------|------|------|-----------|
| 质量官（quality-officer） | 常设 | Case Controller 的领单 Worker：分诊派单建议、进度跟踪、升级决策、扩缩容申请。**不维护状态机**——权威状态在控制面 | case-admin / notification |
| 采集员（collector） | 常设 | 投诉取证：调 `GET /logs`、`GET /feedback`，结构化 badcase，候选探针 | case-admin |
| 守门员（gatekeeper） | 常设 | 门禁主持：触发 Eval、审查双轨报告、PASS/FAIL 建议；**对放行有一票否决权** | eval-runner / release-admin / trust-ledger |
| 案例官（case-officer） | 常设 | 沉淀：归档证据、写案例库、回归集维护、Skill 候选起草（Phase 2）、周报数据汇总 | casebase-knowledge / notification |
| 归因师（attributionist） | 弹性 | 归因实验计划建议、报告解读；裁决由 §4.6 确定性代码给出 | eval-runner / case-admin |
| 修复师（repairer） | 弹性 | 按归因结论选通道自由起草修复（prompt git 化 / KB 修订 / 模型参数切换），产不可变 WorkOrder | release-admin / case-admin |

组织定义落 `agents/team.yaml`（4 常设 CR + 弹性模板 + Team/Human CR）+ 6 份 SOUL.md，钉 AgentTeams **v1.2.1**。Phase 1 为固定 warm pool，不宣称动态扩缩。

### 8.2 冲突仲裁三规则（plan-v3 §2.2 原样执行）

1. **守门员一票否决**：对"是否放行"，守门员结论优先于任何其他 Agent；
2. **归因置信不足不得进修复**：INCONCLUSIVE / CONFOUNDED 未解时，不得进入修复阶段——补实验或升级人工；
3. **控制面权威状态为最终裁决**：一切分歧以 Case Controller 的权威状态与实验数据为准；LLM 结论永远只是"建议"。不靠投票，不靠资历。

### 8.3 弹性扩缩容：申请-决策-执行

统一口径："**Agent 申请、控制面决策执行**"（对外材料禁用"质量官现场创建"的简化说法）：

1. 质量官根据 caseload 向 Caseload Controller 提交扩缩容申请（建议值与理由）；
2. Caseload Controller 按 desired 公式决策：

```
desired = clamp(minWarm, budgetCap,
                ⌈active_leases / concurrency⌉ + ⌈ready_cases · p95 / drain_horizon⌉)
```

| 参数 | 含义 | 来源 |
|------|------|------|
| `minWarm` | 最小热备 Worker 数 | 配置（Phase 1 固定 warm pool 即 desired 恒等于该值） |
| `budgetCap` | 预算/配额上限 | 配置 |
| `active_leases` | 当前活跃租约数 | leases 表实测 |
| `concurrency` | 单 Worker 可并行 case 数 | 配置【待定：建议 1】 |
| `ready_cases` | 队列中待领 case 数 | 控制面实测 |
| `p95` | 单 case 处理时长 p95（滑动历史窗） | 控制面统计 |
| `drain_horizon` | 期望消化时长 | 配置【待定：建议 30min】 |

3. Controller 经受限 RBAC 对 AgentTeams 执行 create/drain/remove，并做资源对账（§8.4）；
4. 全程审计；Agent 侧不持有 AgentTeams 的管理凭证。

### 8.4 缩容 DRAINING 语义与资源对账（引用 S0-001）

缩容序列（plan-v3 §2.2 原样）：

```
DRAINING → 停止新 claim → 等待 lease=0 → outbox 清空
  → 摘出 Team → Sleep/Delete → 资源凭证对账
```

设计依据——上游缺陷 S0-001（`evidence/spike/S0-001-team-delete-broken.md`，已在 AgentTeams v1.2.1 dev controller 证实）：

- `agt delete team` 可**假成功**：CLI 报 deleted 但 CR 仍 Active（controller 的 detach 流程中 invite 非幂等，M_FORBIDDEN 直接判死删除）；REST DELETE 同样无效。结论：**删除失败部分 non-fatal，不能以 CR 消失为成功依据**。
- 摘除顺序：先 `PUT` 移除全部普通 worker（非 Leader 成员摘除正常），**再处理 leader**——leader 摘除是当前上游死结（必经"restore to personal room"步骤），需手工 Matrix 干预或等上游修复；`PUT` 不允许清空 workerMembers（必须保留且仅保留一个 team_leader）。

因此对账规格（drain/remove 的每一步都要回查，四样齐全才算成功）：

| 对账项 | 检查方式 |
|--------|---------|
| AgentTeams CR | `agt get teams/workers` 中目标消失（或符合预期的 leader-only 残留并显式登记） |
| 容器 | docker 侧对应容器已停止/删除，资源已释放 |
| Matrix 房间 | 团队房间归档/解散状态符合预期 |
| 对象存储用户 | 对应凭证/用户已回收 |

- 任一项不符 → remove 不置成功，进 reconcile/人工队列；残留资源登记在案（S0-001 现场处置模式：容器先 stop 释放资源，残留 CR 登记不阻塞后续异名团队创建）。
- 该缺陷整理为上游 issue 素材：detach 应对 invite 幂等（joined/banned 视为成功），单步失败降级为告警而非阻断删除（复赛开源计划的一部分）。

---

## 9. MCP 接口清单

5 个 MCP server + 1 个 trust-ledger 模块。格式沿用契约文档惯例：传输/鉴权/错误码公共约定 → 每 server 工具表 → 错误与降级 → 权限边界。传输与鉴权细节同 plan-v3 §2.3.9（Higress 托管凭证，Worker 只持消费者令牌）；协议为标准 MCP（Streamable HTTP + stdio 调试），无私有扩展字段。

### 9.1 总览

| MCP Server | 封装能力 | 权限边界 | 主要调用方 |
|-----------|---------|---------|-----------|
| mcp-case-admin | case 查询/领单/提交建议/取证（代理 `GET /logs`、`GET /feedback`） | 读开放；建议写入仅产生"建议事件"，不直接改状态 | 全员（按工具 ACL） |
| mcp-release-admin | WorkOrder 起草/定稿/查询、审批提请与状态、发布进度查询 | **无任何直接发布能力**；写面不在此暴露 | 修复师/守门员/质量官 |
| mcp-eval-runner | 门禁评测触发与报告查询、归因实验执行与结果查询 | 触发类仅守门员/归因师；查询开放 | 守门员/归因师 |
| mcp-notification | 飞书（mock 兼容）：原群回复、审批卡片、周报 | 通道级 ACL | 案例官/质量官/控制面 |
| mcp-casebase-knowledge | 案例库检索/写入 | 读全员；写仅案例官 | 全员 / 案例官 |
| trust-ledger（模块，非独立 server） | 记账/查询/晋升评估 | 由 Case Controller 与守门员内嵌调用 | 控制面 |

### 9.2 公共约定

- **鉴权三层**：消费者令牌（Higress 按身份做工具级 ACL）→ 审批语义绑定 ApprovalGrant（§5.2，hash+nonce+expiry，一次性）→ 真实凭证（LLM Key/飞书 app secret/DB 账号）全部网关侧托管，Worker 零接触。
- **统一错误码**：MCP 标准 error + `data.error_code` + `retryable` + `audit_ref`：

| error_code | 可重试 | 触发场景 |
|-----------|-------|---------|
| `VALIDATION_FAILED` | 否 | 参数 schema 校验失败 |
| `FORBIDDEN` | 否 | 令牌无该工具权限（含"Agent 试图调写面"） |
| `STATE_CONFLICT` | 否 | 状态机非法迁移 / CAS revision 不匹配 |
| `LEASE_LOST` | 否 | fencing token 过期，产物拒收，需重新领单 |
| `APPROVAL_REQUIRED` / `APPROVAL_EXPIRED` / `APPROVAL_MISMATCH` / `APPROVAL_REPLAYED` | 否 | 审批缺失/过期/hash 不符/nonce 复用 |
| `IDEMPOTENCY_CONFLICT` | 否 | 同幂等键异参 |
| `GATE_FAILED` | 否 | 门禁未过（WorkOrder 不得进入审批） |
| `NOT_FOUND` | 否 | 资源不存在 |
| `RATE_LIMITED` | 是 | 网关限流，指数退避 |
| `DEPENDENCY_UNAVAILABLE` / `UPSTREAM_TIMEOUT` | 是 | 下游不可达/超时，退避重试（1s/2s/4s，≤3 次） |
| `INTERNAL_ERROR` | 谨慎 | 重试 1 次后升级 |

- **重试与幂等**：只读工具指数退避 ≤3 次；写工具不自动重试，调用方必须携带幂等键（格式 `<case_id>:<action>:<seq>`），服务端 24h 去重；同键同参返回首次结果。
- **审计**：所有写工具调用、审批流转、拒绝事件按 §7.6 入库；`params_digest` 落哈希不落敏感原文。

### 9.3 mcp-case-admin

| 工具 | 参数（*必填） | 返回 | ACL |
|------|-------------|------|-----|
| `case.list` | `status`, `limit` | `{cases:[{case_id,status,opened_at,summary}]}` | 全员 |
| `case.get` | `case_id*` | case 全量（状态、证据引用、关联 id） | 全员 |
| `case.claim` | `worker_id*`, `case_id*` | `{lease_id, fencing_token, expires_at}`；冲突 → `STATE_CONFLICT` | 常设/弹性 Worker |
| `case.submit_suggestion` | `case_id*`, `fencing_token*`, `kind*`(`triage|attribution|fix|gate|verify`), `payload*`, `evidence_refs` | `{accepted, event_id}`；控制面裁决后迁移 | Worker |
| `app.logs` | `app*`, `time_range*`, `filter`, `limit` | `{entries:[…]}`（代理 Quality API `GET /logs`，已脱敏） | 采集员/归因师 |
| `app.feedback` | `app*`, `time_range*` | `{feedback:[…]}`（代理 `GET /feedback`） | 采集员 |
| `case.timeline` | `case_id*` | 案件时间线（事件序列 + 建议记录） | 全员 |
| `case.escalate` | `case_id*`, `reason*`, `evidence_refs` | `{accepted, event_id}`；升级人工，经 notification 留痕 | 全员 |

降级：`app.logs/feedback` 不可达 → 退避重试，失败返回 `evidence_gap=true`，不阻塞流水线，缺口显式标注。

### 9.4 mcp-release-admin

| 工具 | 参数 | 返回 | ACL |
|------|------|------|-----|
| `workorder.draft` | `case_id*`, `target*`, `input_versions*`, `diff*`, `single_factor_declaration*` | `{workorder_id}`（DRAFT） | 修复师 |
| `workorder.freeze` | `workorder_id*`, `fencing_token*` | `{workorder_id, hash}`（FROZEN） | 修复师 |
| `workorder.get` | `workorder_id*` | 全量 + hash + 状态 | 全员 |
| `approval.request` | `workorder_id*`, `evidence_summary*`, `channel*` | `{approval_id, status:pending}`；前置校验 GATE_PASSED，否则 `GATE_FAILED` | 守门员 |
| `approval.status` | `approval_id*` | `{status:pending|approved|rejected|expired, decided_by, decided_at}` | 全员 |
| `release.get` | `release_id*` 或 `case_id*` | Release 状态机现状 + operation 对账信息 | 全员 |

**边界**：本 server 不暴露 Quality API 写面；`approve` 决定来自人（经 notification 通道），`approval.status` 仅查询。执行发布是 Release Controller 内部行为，Agent 只能 `release.get` 旁观。

### 9.5 mcp-eval-runner

| 工具 | 参数 | 返回 | ACL |
|------|------|------|-----|
| `gate.run` | `workorder_id*`, `suite_digest*` | `{eval_id, status:queued}` | 守门员 |
| `gate.report` | `eval_id*` | 双轨报告（deterministic/live 分列）+ verdict + report_hash | 全员 |
| `experiment.plan` | `case_id*`, `matrix*`(5cell|full2x2x2), `version_refs*` | `{experiment_id}`（PLANNED 建议，控制面确认后生效） | 归因师 |
| `experiment.run` | `experiment_id*` | `{status:running}` | 归因师 |
| `experiment.report` | `experiment_id*` | §4.7 报告全量（原始计数 + Δ + CI + 裁决） | 全员 |
| `probe.freeze` | `experiment_id*`, `probe_set*`（三分集） | `{probe_set_digest}` | 归因师（冻结后全员只读） |

降级：live provider 不可达 → live 轨 `UNAVAILABLE` 标记，确定性轨照常（门禁放行策略见 §3.4【待定】）；向量/裁判模型不可达 → 对应轨标退化并重试，不伪造结果。

### 9.6 mcp-notification（飞书 mock 兼容）

| 工具 | 参数 | 返回 | ACL |
|------|------|------|-----|
| `feishu.reply_origin` | `case_id*`, `text*`, `refs` | `{message_id}`（幂等键=outbox_id） | 控制面/案例官 |
| `feishu.approval_card` | `approval_id*`, `workorder_hash*`, `evidence_summary*`, `expiry*` | `{message_id}` | 控制面 |
| `feishu.weekly_report` | `report*`（§10.4 结构） | `{message_id}` | 案例官 |
| `matrix.log` | `room*`, `text*` | `{event_id}`（对内留痕） | 全员 |

约定：飞书真实凭证为 Phase 1 前置（用户操作）；未到位时用 feishu mock（同工具契约、同消息结构，ack 模拟），降级路径在演示材料中**明示**。发送语义 at-least-once + 幂等键判重；失败后按 §3.6 退避重试，耗尽 DEAD 升级。

### 9.7 mcp-casebase-knowledge

| 工具 | 参数 | 返回 | ACL |
|------|------|------|-----|
| `kb.search` | `query*`, `top_k`, `filters`(`{doc_type, fault_layer, app}`) | `{hits:[{doc_id, score, snippet, metadata}]}` | 全员 |
| `kb.get` | `doc_id*` | `{doc_id, content, metadata, version}` | 全员 |
| `kb.upsert` | `doc_type*`, `content*`, `metadata*`, `idempotency_key*` | `{doc_id, version, indexed}` | **仅案例官** |

降级：向量库不可达 → `kb.search` 退化为全文检索（`degraded:"fulltext_only"`）；嵌入不可达 → 先落全文、标 `pending_embed`，恢复后补偿向量化。

### 9.8 trust-ledger 模块（内嵌接口）

不作为独立 MCP server，以库形式内嵌 Case Controller 与守门员运行时：

| 接口 | 签名 | 语义 |
|------|------|------|
| `record_outcome` | `(risk_class, action_type, success: bool, action_ref) → entry` | 一次动作=一个样本；原始整数计数落 `trust_ledger`（当前 epoch） |
| `get_state` | `(risk_class, action_type) → {autonomy_state, epoch, trials, successes, LB, UB}` | 查询账本与 Wilson 区间 |
| `evaluate_promotion` | `(risk_class, action_type) → {eligible: bool, reason}` | 纯函数：白名单 + R1 + LB>0.9 校验；MVP 口径下输出拒绝判定事件（含数字，如 `3/3 LB=0.4385<0.9`） |
| `request_promotion` | `(risk_class, action_type) → approval_id` | 达标后生成证据表经飞书提请；人确认才生效（§3.7） |
| `suspend` | `(risk_class, action_type, reason) → entry` | 验证失败 → SUSPENDED + 冷却 + epoch+1 |

---

## 10. 事件与消息定义

### 10.1 事件信封（events 表 payload 的统一外壳）

```json
{
  "event_id": "evt-01J…",
  "event_type": "见 §10.2 目录",
  "aggregate": { "type": "CASE", "id": "case-01J…", "seq": 7 },
  "trace_id": "trc-01J…",
  "actor": "collector | case-controller | human:feishu_uid",
  "occurred_at": "RFC3339",
  "payload": { "事件特定字段" }
}
```

### 10.2 事件目录

| event_type | 生产者 | 语义 |
|-----------|--------|------|
| `ComplaintReceived` | Case Controller（接入层） | webhook/poll 原始投诉落入 inbox（已脱敏） |
| `CaseFiled` / `CaseMerged` | Case Controller | 立案 / 合并到既有 case |
| `TriageSuggested` / `TriageAccepted` | 采集员 / 控制面 | 分诊建议与裁决 |
| `ExperimentPlanned` / `ProbesFrozen` / `ExperimentCompleted` | 控制面 | 归因实验生命周期 |
| `AttributionSuggested` / `AttributionAccepted` | 归因师 / 控制面 | 归因建议与裁决（含三态与 Δ/CI 引用） |
| `WorkOrderDrafted` / `WorkOrderFrozen` / `WorkOrderRejected` | 修复师 / 控制面 | WorkOrder 生命周期 |
| `GateReported` | 控制面（Eval 回流） | 门禁报告完成（PASS/FAIL + report_hash） |
| `ApprovalRequested` / `ApprovalDecided` | 控制面 / 审批人 | 审批流转 |
| `ReleaseProgressed` / `ReleaseUnknown` / `ReleaseReconciled` / `ReleaseBlocked` | Release Controller | 发布推进 / UNKNOWN / 对账恢复 / 阻断 |
| `OriginReplied` | 控制面（Notification 回流） | 飞书原群回复成功 |
| `CaseArchived` | 案例官 → 控制面 | 沉淀归档完成 |
| `TrustOutcomeRecorded` / `PromotionRejected` / `PromotionRequested` / `PromotionConfirmed` / `TrustSuspended` | trust-ledger | 信任账本记账与晋升流转 |
| `ManualOverride` | 人工 | 人工接管/恢复，一切人工动作 |
| `WeeklyReportSent` | 案例官 | 质量周报已发 |

### 10.3 投诉接入消息（webhook / poll → inbox）

```json
{
  "source": "feishu_webhook | feishu_poll | api",
  "received_at": "RFC3339",
  "channel_ref": { "chat_id": "oc_…", "message_id": "om_…", "user_open_id": "ou_…" },
  "raw_text": "已脱敏的投诉原文",
  "dedup_key": "sha256(source_channel + 用户标识 + 归一化内容 + 时间窗)",
  "attachments_ref": ["minio://…（可选）"]
}
```

- webhook 为主、poll 为兜底：同一投诉可能双通道到达，**inbox 去重是统一收口**（T3）——poll 扫 `GET /feedback`，与 webhook 命中同键即合并。
- `channel_ref` 是"回复投诉原处"的依据：回复必须带原 `chat_id`（原群），不允许另开新会话冒充闭环。
- 入 inbox 前必须完成 PII 脱敏（§11.2），脱敏失败则拒收并告警，不脱敏不入库。

### 10.4 飞书回复原群消息

```json
{
  "chat_id": "oc_…（原群）",
  "msg_type": "interactive",
  "card": {
    "header": "CaseLoop 处理结果 · case-01J…",
    "fields": [
      { "label": "投诉摘要", "value": "…" },
      { "label": "归因结论", "value": "ATTRIBUTED · prompt 层（Δ=0.83, 95%CI [0.61,0.94]）" },
      { "label": "修复与门禁", "value": "wo-01J… · 双轨门禁 PASS（report_hash 引用）" },
      { "label": "发布", "value": "vs-01J… canary→promoted（灰度观察窗达标）" },
      { "label": "证据", "value": "Evidence Bundle 链接" }
    ]
  },
  "idempotency_key": "outbox_id"
}
```

### 10.5 质量周报消息

```json
{
  "period": "2026-W32",
  "mutation": { "cases_generated": 40, "detected": 33, "detection_rate": 0.825 },
  "attribution": { "experiments": 12, "attributed": 9, "attribution_accuracy": "对照 ground-truth 的命中率" },
  "gate": { "runs": 15, "blocked": 4, "block_rate": 0.267, "first_pass_rate": 0.73 },
  "trust": { "outcomes_recorded": 21, "promotion_requests": 0, "promotion_rejected": 1 },
  "trend": { "检出率/归因准确率/门禁拦截率/一次通过率 的多期序列" }
}
```

巡检流水线：变异算子库 → 探测用例生成 → 周期攻击（Phase 1 为单次）→ 检出/归因/门禁数据回流 → 周报经 mcp-notification 发飞书。

---

## 11. 安全设计

### 11.1 审批防掉包防重放

- 审批对象 = WorkOrder **hash**（§5.1），非 id——内容任何字节变动即 hash 不匹配，执行拒绝（`APPROVAL_MISMATCH`）。
- ApprovalGrant 绑定 `nonce + expiry`：nonce 一次性（PG 唯一约束，消费即作废，复用 → `APPROVAL_REPLAYED`）；超 expiry → `APPROVAL_EXPIRED`。TTL【待定：建议 30min】。
- 审批请求/决定/消费三事件全入 `audit`；审批通道（飞书卡片）不可用 = 发布不可执行，绝不绕过。

### 11.2 PII 入口脱敏

- 脱敏点在**入口侧**（投诉入 inbox 前、日志进文档链前）：手机号/邮箱/证件号/地址/订单号/用户标识等模式字段替换为掩码或稳定假名（同一 case 内同一实体假名一致，便于关联分析）。
- 脱敏规则配置化，命中失败（规则引擎异常）时**拒收并告警**，不允许未脱敏数据落库。
- 审计与 Evidence Bundle 只存 `params_digest`，敏感原文不出现在审计、评测报告与案例库。

### 11.3 凭证托管（Higress）

- LLM Key（StepFun/裁判模型）、飞书 app 凭证、DB 账号、对象存储密钥全部收在 Higress 网关侧；Worker 只持消费者令牌，工具级 ACL 按身份下发。
- AgentTeams 管理面凭证仅 Caseload Controller 持有（受限 RBAC），Agent 不持有。
- 演示环境 docker-compose 注入本地凭证，结构与网关托管一致，保证"演示即真实拓扑"。

### 11.4 审计权威源与失败即拒

- `audit` 表为唯一权威审计存储，写在业务事务内：插入失败 → 事务回滚 → 业务拒绝执行（不放行）。
- `audit.jsonl` 为导出物，仅供评审翻阅；zeroops `audit.py`（失败放行）与 `common/approval.py`（静态 token 可重放）**不直接沿用**，按 §7.6 与 §5.2 重写。

### 11.5 其他边界

- Quality API 写面仅 Release Controller（§1.1 铁律），网络层亦做隔离（仅控制面命名空间可达写面端点）。
- B1–B4 故障注入端点仅演示环境启用，配置级开关，生产构建不含。
- 演示确定性：`temperature=0` + 冻结探针集 + ground-truth fixtures；裁判模型 ≠ 运动员模型（配置级强制）。

---

## 12. 开放问题（【待定】汇总）

> 以下为 plan-v3 已定向但未定量、或本规格给出建议值待评审确认之处。实现时按建议值先行，评审确认后回填。

| # | 位置 | 问题 | 建议值 / 当前处理 |
|---|------|------|------------------|
| 1 | §3.1 / §10.3 | 投诉去重时间窗（dedup_key 组成之一） | 建议 24h |
| 2 | §3.2 | INCONCLUSIVE 补实验次数上限 | 建议 2 次，超限升级人工 |
| 3 | §3.4 | live 轨 UNAVAILABLE 时门禁能否仅凭确定性轨放行 | 建议 MVP 不可放行，转人工 |
| 4 | §3.5 | 灰度阶梯与观察窗参数 | 建议 5% → 25% → 100%，每阶梯 ≥10min（MVP 演示可压缩） |
| 5 | §3.5 | reconcile 退避节奏 | 建议 5s 起步指数退避至 5min 上限 |
| 6 | §3.6 | 通知重试次数上限 | 建议 5 次 |
| 7 | §3.7 | SUSPENDED 冷却时长 | 建议 24h |
| 8 | §4.3 | 归因实验每 cell 每探针重复调用次数 n | 建议 n=5，随探针规模校准 |
| 9 | §4.5 | 最小有实际意义效应量 δ_min | 建议 0.2，随探针规模校准 |
| 10 | §5.2 / §11.1 | ApprovalGrant TTL | 建议 30min |
| 11 | §7.5 | Worker lease 时长 | 建议 60s，心跳续租 |
| 12 | §7.7 | casebase 向量维度 | 随嵌入模型定，contracts/ 冻结时确认 |
| 13 | §8.3 | desired 公式参数（concurrency、drain_horizon） | 建议 1 与 30min；Phase 2 按实测校准 |
| 14 | §9.6 | 飞书凭证到位时间（用户操作） | 未到位用 feishu mock，明示降级路径 |
| 15 | §3.7 / PRD §10 | 信任晋升（AUTO_ENABLED）完整演示所在 Phase | plan-v3 未定演示时点，待评审对齐 |

---

*本文档与 `docs/prd.md` 互为配套；与 `docs/plan-v3.md` 冲突时以 plan-v3 为准。contracts/ 冻结后，字段级 schema 以 contracts/ 为最终裁决。*
