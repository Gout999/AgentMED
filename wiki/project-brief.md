# 一页看懂 CaseLoop

[返回 Wiki 索引](INDEX.md)

## 产品定位

CaseLoop 是面向 AI 应用团队的**开源 Agent-native 治理运营控制面**。它帮助团队把坏结果转成可追踪案件，绑定完整系统版本和运行证据，验证归因与候选修复，并在确定性门禁、审批、发布、回滚、补偿与审计约束下完成闭环。

目标用户的真实治理任务决定产品范围。已有 trace、eval、runtime、sandbox 和身份系统可以被参考、复用或适配；功能重叠不是删除需求的理由。完整原则见 [CaseLoop 产品原则](../docs/product-principles.md)。

CaseLoop 优先满足国内团队的中文、自托管、私有化、国内模型、云服务与协作渠道需求；核心契约保持 provider-neutral。StepFun、飞书、AgentTeams 和 Langfuse 都是当前或计划中的实现与适配器，不是产品身份。

V5 产品边界已由 D-013 接受：顶层对象是 `AIApplication`，Agent 是组件；Console 与其他
Agent 调用同一治理内核。D-015 已使 V5 成为新开发默认设计与施工基线（V3/V4 为兼容
lane，非 runtime cutover）。V5 [PRD](../docs/prd-v5.md)、[plan](../docs/plan-v5.md)、
blueprint 和 frozen `contracts/v5/` 已是 V5 stage 施工基线，当前编排见
[Master Execution Plan](../docs/plans/v5-master-execution-plan.md)。C0–C5 收敛已完成并经
`b6fa629` 复核；V5-1B（R3-full）与 V5-1C（R4）已关闭，V5-1A（R2）保持 `VERIFYING`。
当前唯一执行入口是 V5-2A，代码施工在远端机，见
[V5 远端施工交接](../docs/context/V5_REMOTE_CONSTRUCTION_HANDOFF.md)；V5-2+ 尚未实现。
plan v4 保留为 V4 兼容性基线。

## 适用边界

- 当前仓库的第一条参考 workload 是「小智客服」；v3 另选 AgentTeams v1.2.1 作为 CaseLoop 内部协作适配器。两者都用于证明和暴露纵向闭环的工程问题，不等于通用 Agent 接入已经完成。
- 通用化目标面向能够提供版本、运行证据、评测和发布/回滚接点的完整 AI 应用；V4 S1A
  no-trace intake 已完成，V5-1A catalog 已提交并保持 `VERIFYING`，V5-1B version 与 V5-1C Case
  已关闭，V5-2 durable operation、V5-3 evidence、V5-4 Gate 与 V5-5 release/recovery 未实现。
- CaseLoop 不取代 Agent runtime，也不把 Langfuse 等 trace 系统当生命周期权威数据库。
- LLM 与 Agent 只给建议、假设和候选工件；PostgreSQL 控制面持有权限、状态、审批、发布和审计事实。

## V5 核心闭环（已接受目标；实现按 stage 分栏）

```text
质量 Signal → Case → confirmed badcase / NEEDS_ACCEPTANCE_CRITERIA
  → AIApplication / SystemVersionSet / EpisodeSnapshot → 候选变更
  → Candidate Verification Gate → VerifiedCandidate / NOT DEPLOYED
  └→ deployable target：pre-Gate ReleasePlan → Release Authorization Gate
     → SystemWorkOrder → human Approval → Observed
     → scoped Release / Rollback / Reconcile / Compensate
  → Closure → 回归资产与信任证据
```

投诉与回复原群分别只是客服场景的 `Signal Adapter` 和 `Closure Adapter`。通用 Agent 的 Signal 也可能来自评分下降、运行错误、安全策略命中或人工立案；Closure 也可能是回滚、issue、告警处置或变更记录。

## Langfuse 的两个角色（已确认需求，尚未实现）

| 方向 | 用途 | 必须遵守的边界 |
|---|---|---|
| CaseLoop → Langfuse | 把 CaseLoop 自身 LLM / Agent 调用 trace 发到 Langfuse，支持自身可观测与排障 | 使用标准 OTel/trace 传播；不能把 trace 后端写成控制面权威源 |
| Langfuse → CaseLoop | `TraceSource` 从被治理 Agent 的 Langfuse project 读取输入、输出、模型调用、工具调用和上下文 | 查询窗口、水位、去重、来源和 digest 必须固化；Stage 0 target schema 已落盘，Connector/migration/live receipt 尚未实现 |

不能默认 Langfuse 一定拥有“全部输入输出”。脱敏、采样、丢失、留存、权限与 instrumentation 都可能造成缺口；接入结果必须报告 completeness，证据不足时为 `UNKNOWN` 并 fail closed。Langfuse 是首个适配器，不是唯一后端。

## 四类身份必须分开

| 身份 | 职责 | 不能做什么 |
|---|---|---|
| 被治理 Agent | 产生需要治理的业务运行与 trace | 不能被写成 CaseLoop 内部 Worker |
| CaseLoop 内部 Worker | 采集、分析、归因、起草修复或验证，产出非权威建议与工件 | 不能直接改权威状态、批准自己或发布 |
| Runtime child Attempt | 在受控 runtime 中执行一次确切模型/Skill/MCP/tool 工作，例如 Claude Code child Attempt | 不能冒充发起委托的 AgentTeams Worker，也不能继承其长期凭据或身份 |
| Controller / Executor | 确定性地校验、持久化、调度和执行获批动作 | 不能伪装成“Agent 做了判断” |
| Evidence Exporter | 只读收集来源记录、保留 provenance，并区分 PostgreSQL 权威事实与 Matrix/MinIO 等平台证据 | 不能创建任务、ack/submit、审批或补造因果关系 |

下文的质量官、采集员、归因师等均指 CaseLoop 内部 Worker，不是被治理 Agent。AgentTeams 父 Worker 与 Claude Code 子 Attempt 必须分别记录 `requested_by`、`executor`、父子 task/attempt 和 capability grant。

## 当前 v3 客服参考纵切

```text
live：真实飞书输入 ─┐
replay：具名 fixture ─┴→ Case Controller 去重立案、租约、幂等与 outbox
  → 采集 Quality API 的 logs / feedback
  → 冻结探针和版本，运行 5-cell 对照实验，计算 Δ + 95% CI
  → ATTRIBUTED 才允许自由起草候选修复
  → 不可变 WorkOrder 绑定目标、版本、diff、Gate、expiry 与 nonce
  → 规则轨 + 独立 LLM 裁判轨 + contract/replay/live 分类门禁
  → 人工审批确切 WorkOrder hash
  → Release Controller 以 CAS 执行 stage / canary / promote / rollback
  → 通知原处、归档、Trust Ledger 记账
```

这里的确定性控制面、不可变证据绑定、fail-closed 和唯一写面是应保留的安全边界。客服特有的投诉、飞书回复、固定六角色、三元 VersionSet 与 5-cell 变量，则需要在通用需求中抽象，不能直接套给所有 Agent。

## 当前 AgentTeams 里到底是什么

最近一次 2026-08-10 本地快照中，`caseloop-team` 的 Team CR 为 `Active`，但 `leaderReady=false`、`readyWorkers=0`，六个 Worker 均为 `Sleeping`。仓库 desired spec 中的 `state: Running` 和 Team CR 的 `Active` 都不等于 Agent 正在执行；运行前必须重新查询。

| Worker | v3 职责 | Runtime / model | 当前快照 |
|---|---|---|---|
| `quality-officer` | Team Leader、分诊、协调、升级 | CoPaw / StepFun `step-3.7-flash` | `Sleeping` |
| `collector` | 日志/反馈取证、badcase 与候选探针 | CoPaw / StepFun `step-3.7-flash` | `Sleeping` |
| `attributionist` | 实验计划与归因报告建议 | CoPaw / StepFun `step-3.7-flash` | `Sleeping` |
| `repairer` | 起草候选修复与 WorkOrder | CoPaw / StepFun `step-3.7-flash` | `Sleeping` |
| `gatekeeper` | 评测与放行建议 | CoPaw / StepFun `step-3.7-flash` | `Sleeping` |
| `case-officer` | 归档、回归资产与通知 | CoPaw / StepFun `step-3.7-flash` | `Sleeping` |

`caseloop-approver` 是 Human，不是第七个 Agent。AgentTeams 中当前没有 Claude Code Agent 或 GLM Agent。平台房间、Matrix 消息和 MinIO 工件只能证明相应记录存在；若 Worker 没有真实领取任务、调用模型/Skill/MCP 并在动作前提交可追溯 Proposal，就不能宣称 `agent-causal`。

## 新 Coding Team（批准目标，尚未创建）

v4 保留上述质量治理 Team，另外新增 `caseloop-coding-team`，而不是把客服角色硬改名：

| 角色 | 目标职责与执行方式 | 当前状态 |
|---|---|---|
| `coding-planner` | 形成可测的修复计划；GLM-5.2 是目标模型，真实 provider 以 smoke/receipt 为准 | `NOT CREATED` |
| `coding-generator` | 提交委托 Proposal，由宿主 `ClaudeCodeRuntimeAdapter` 在隔离 worktree 启动 Claude Code child Attempt | `NOT CREATED` |
| `coding-reviewer` | 使用独立模型轨操作 sandbox、找反例并提交 Finding；确定性 Gate 决定硬结果 | `NOT CREATED` |

Claude Code、父 Worker、模型 provider、Controller 与 Repo Executor 是不同身份。Claude Code 不能取得 human approval、push/merge、Release Controller 或生产凭据。

## 两条 live 验收轴

| 验收轴 | 证明什么 |
|---|---|
| `domain-provider-live` | 本次声明的 StepFun、Langfuse、飞书、GitHub 等外部边界确实被真实调用 |
| `agent-causal` | task/outbox、真唤醒、Worker 原生 claim、模型/Skill/MCP receipt、动作前 Proposal 与 Controller causation 连成可信链 |

`platform evidence export` 是只读取证类别，不是第三条成功轨；它不能替代 `agent-causal`。六个 Worker 全部休眠时，`agent-causal` 验收必须失败或 `BLOCKED`。

## 当前状态

| 分类 | 状态 |
|---|---|
| 产品战略与 v4 路线 | `APPROVED`：开源、用户需求驱动、中国优先、provider-neutral、客服为首个参考 workload |
| Stage 0 | `DONE (contract-only)`：正式 PRD、ADR、v4 contracts、Intent Registry 与迁移语义；无 migration/runtime/live 声明 |
| Stage 1A | `DONE (local runtime)`：migration 007、strict bootstrap、5 个 authenticated HTTP/CLI intent 和 no-trace Signal/Case/Link/`UNKNOWN` Evidence 事务链；所有 provider/Agent/external/production facets 为 `NOT_RUN` |
| v3 确定性控制面 | P0-1～P0-3 已验证；P0-4 有 replay 证据，但最后独立证据快照中的 live acceptance 仍为 BLOCKED，详见 [PLANS](../PLANS.md) 与 [PROJECT_STATE](../docs/context/PROJECT_STATE.md) |
| 历史 live 演示 | 不因时间经过自动失效；它证明什么以当时调用链与证据为准 |
| 通用 Agent 接入 | 最小 S1A no-trace 子集已由本地实现与测试证明；完整 TraceSource、Work Kernel、Agent runtime、Gate/Release 仍未实现 |
| Langfuse 双向集成 | 已确认需求，仓库尚未实现 |
| Claude Code Runtime Adapter | 已批准，尚未实现/运行 |
| `caseloop-coding-team` | `NOT CREATED / NOT RUN` |
| 真实 GitHub coding case | `NOT RUN`；留言、认领、fork、push、PR 均未授权，执行前逐次询问用户 |
| 真实 Agent 因果执行 | 在新的 agent-causal 验收口径下，当前快照尚无独立验证的合格证据；更早 live 是否包含真实 Agent 因果参与，只按当时调用链和证据判断，不因本次口径更新被抹除 |

当前活动项是 V5-2A（Durable Work Kernel）远端施工与本地 disposable-PostgreSQL
journey/live facet 收口；V4 S1B–S7 冻结，V5-2B+ 未开始。每项能力只有在真实调用路径、
权威记录、测试、evidence、semantic commit 和 post-commit verifier 齐全后才可标为已实现。

## 统计纪律示例

Trust Ledger 的 v3 MVP 只演示「记账但拒绝晋升」：3/3 成功时 Wilson 双侧 95% 下界约为 `0.438`，仍小于 `0.9`，因此拒绝晋升。拒绝是证据不足时的正确行为，不是把 UNKNOWN 包装成成功。
