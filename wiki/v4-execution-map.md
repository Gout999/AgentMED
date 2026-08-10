# CaseLoop v4 施工路线速查

[返回 Wiki 索引](INDEX.md)

> 状态：**S1A IMPLEMENTED IN WORKING TREE / VERIFIER PENDING**
>
> 更新：2026-08-10
>
> 完整架构见 [CaseLoop v4 全盘计划](../docs/plan-v4.md)，文件级 entry/test/live/evidence/rollback 见 [v4 渐进式施工台账](../docs/plans/v4-progressive-delivery.md)。本页只做导航和状态蒸馏；“路线已批准”不等于功能已经实现。`docs/plan-v3.md`、现有 `contracts/`、migrations 与可执行测试继续约束已实现的 v3 客服 Scenario Pack，直到被明确的 v4 contract、migration 和测试替换。

## 一句话

CaseLoop v4 要成为**可由人和其他 Agent 调用的开源 Agent 质量维护团队**：接收外部/内部反馈、维护报告、运行异常与 eval 回归，绑定确切 Agent Run，驱动真实 Agent Team 调查和生成候选，再由独立 Evaluator 与确定性控制面验证、授权、发布和回滚。

## 当前事实，不要混写

| 项目 | 当前状态 |
|---|---|
| v4 产品方向与施工路线 | `APPROVED` |
| Stage 0 文档、ADR 与契约施工 | `DONE (contract-only)` |
| Stage 1 Entry wire contract | `DONE (contract-only)`；14 个 target intent 中 8 个 S1A/S1B intent 已冻结，后续 6 个仍为 `SKELETON` |
| S1A · 无 trace 维护报告 | `IMPLEMENTED IN WORKING TREE / VERIFIER PENDING`；007、bootstrap、5 个 HTTP/CLI intent 与 PG 事务链已施工，尚无阶段 evidence/completion commit，不能称 `DONE` 或 live |
| S1B · Langfuse 读 + CaseLoop OTel 写回 | `NOT IMPLEMENTED / PROVIDER-LIVE BLOCKED`；当前只有 frozen wire contract，live 前还需轮换隔离凭证并取得单独授权 |
| 现有 `caseloop-team` | 六个 CoPaw / StepFun `step-3.7-flash` 质量治理 Worker；最近实测均为 `Sleeping`，`leaderReady=false`、`readyWorkers=0` |
| AgentTeams 中的 Claude Code / GLM Agent | 不存在 |
| 新 `caseloop-coding-team` | `design_status=APPROVED`、`lifecycle_status=NOT_CREATED`、`runtime_status=NOT_RUN`；计划在 Stage 2C 内生成 deployment manifest、部署并验收 |
| Langfuse 双向接入 | 已批准；S1B 尚未实现，provider-live 未运行且当前被阻塞 |
| 真实 GitHub issue 修复 | `NOT RUN` |
| GitHub 留言、认领、fork、push、PR | 未授权；执行前逐次询问用户 |

`caseloop-team` 的 Team CR 显示 `Active` 或仓库 desired spec 写 `state: Running`，都不能证明 Worker 当前活跃。运行状态是有时间戳的快照，实际操作前必须重新查询。

## Signal 取代 Complaint

核心输入为 `SignalEnvelope`：

```text
external_feedback    外部用户差评/投诉
internal_feedback    运营、业务或领域专家反馈
maintainer_report    维护人员主动立案
monitor_alert        监控异常
eval_regression      CI/canary/回归失败
runtime_failure      模型、工具、权限或运行错误
policy_violation     安全/策略命中
agent_self_report    被治理 Agent 自报；只作线索
scheduled_inspection 周期巡检
```

所有来源统一经过鉴权、验签、脱敏、幂等、Trace 绑定、去重/合并和 outbox。任何 Signal 都不能直接批准或发布修复。

## 双 Team，而不是把客服 Worker 改名

| Team / 单元 | 角色 | 当前/目标执行后端 | 状态与边界 |
|---|---|---|---|
| `caseloop-team` | `quality-officer`、`collector`、`attributionist`、`repairer`、`gatekeeper`、`case-officer` | CoPaw + StepFun `step-3.7-flash` | 已部署的质量治理 Team；当前六个 Worker 均休眠，不是专业 Coding Team |
| `caseloop-coding-team` | `coding-planner`、`coding-generator`、`coding-reviewer` | Planner/Generator 目标模型为 GLM-5.2；Generator 委托受控 Claude Code 子 Attempt；Reviewer 使用独立模型轨操作 sandbox，并且只提交 `Finding` | `design_status=APPROVED`、`lifecycle_status=NOT_CREATED`、`runtime_status=NOT_RUN`；provider 必须以每次 receipt 和 smoke 证明 |
| Eval Runner / Code Gate | 非 Agent 的确定性服务 | 编译、测试、lint、sandbox、diff/权限规则 | 计算硬门禁，不让模型自行放行 |
| Independent Judge | 独立模型轨 | 与最终 Generator 不同的 independence key | 提交评分/Finding，无放行权 |
| Controllers / scoped Executors | 确定性权威 | PostgreSQL 控制面和范围受限的外部动作执行器 | 冻结合约、裁决 Proposal、审批、发布/回滚；不冒充 Agent 作者 |

`caseloop-approver` 是 Human，不是第七个 Agent。当前 AgentTeams 内没有 Claude Code Agent 或 GLM Agent；目标模型配置、CCR 路由或本机 CLI 存在都不能替代真实运行验收。

`coding-reviewer` 不持有 Gate。它只提交绑定确切 Candidate revision 的 `Finding` 和 sandbox evidence；确定性 Eval Runner / Code Gate 位于 Team 外，根据冻结 EvaluationPlan 和所有必选 evidence facets 计算终态。

### AgentTeams 父 Worker 与 Claude Code 子 Attempt

正确链路是：

```text
Controller 创建父 WorkerTask/outbox
  → AgentTeams Adapter 唤醒指定父 Worker
  → 父 Worker 原生身份 claim
  → 父 Worker 提交 DelegationProposal(backend=claude-code)
  → Controller 接受并创建 child WorkerTask + CapabilityLease(DISPATCH_CLAIM)
  → host ClaudeCodeRuntimeAdapter 在隔离 worktree 启动 Claude Code
  → Claude native session claim child WorkerTask，Work Controller 创建 child Attempt + lease/fence + CapabilityLease(ATTEMPT_RUNTIME)
  → child Attempt 作为作者提交 typed ChangeProposal
  → Controller 同事务记录 ProposalDecision + 首个下游 causation
  → 父 Worker消费 child 结果并提交自己的平台任务结果，不重新署名 patch
```

Claude Code、AgentTeams Worker、模型 provider、Controller 和 Repo Executor 是不同身份。Claude Code 不持有 Worker 长期 role token、human approval token、Release Controller token、push/merge 权限或生产凭据。

## 避免“大号状态机”

通用工作内核拆成：

```text
WorkerTask: QUEUED → LEASED → COMPLETED | WAITING_RETRY | EXHAUSTED | CANCELLED | BLOCKED_UNKNOWN
Attempt:  CREATED → STARTING → RUNNING → OUTPUT_RECORDED → SUCCEEDED | FAILED | TIMED_OUT | CANCELLED | UNKNOWN
typed Proposal: Agent 的不可变非权威产物
ProposalDecision: Controller 独立接受或拒绝
Finding:  Evaluator 对具体候选版本的证据化问题
```

Case 只持有问题生命周期；WorkerTask 只持有 queue/lease/fence/retry；Experiment、Candidate、Gate、scoped Execution、Closure、SkillRelease 各自持有领域状态。`AutomationRunView` 只是只读投影，不接受 start/cancel 写命令。runner 只负责测试、故障注入和等待终态，不再模拟整家公司。

## 两条 live 验收轴

| 验收轴 | 必须证明 | 不能用什么替代 |
|---|---|---|
| `domain-provider-live` | 真实调用本次声明的 StepFun、Langfuse、飞书、GitHub 或其他外部边界，并保存 provider receipt | mock、fixture、连接失败、配置存在 |
| `agent-causal` | Controller task/outbox → Runtime 真唤醒 → Worker 原生 claim → 模型/Skill/MCP receipt → 动作前 Proposal → Controller causation | Team/Worker CR、Matrix 消息、MinIO 对象或 exporter 自述 |

`platform evidence export` 是只读取证类别，不是第三条成功轨。它可以证明平台记录存在，但不能单独满足 `agent-causal`。关闭或休眠真实 Worker 时，`agent-causal` 验收必须失败或 `BLOCKED`；否则测试没有证明 Worker 因果参与。

## 人和其他 Agent 的入口

```text
HTTP API   公共能力基线
CLI        人、CI、Claude Code/Codex 等 shell Agent
Public MCP Agent-native 工具发现和结构化调用
Skill      教客户端何时、怎样安全调用
Plugin     Claude Code 等客户端分发包
A2A        稳定 Run API 之上的长任务 Adapter
```

当前内部 MCP projection 不能直接给客户 Agent。Registry 是 target catalog：S1A/S1B 仅 `capabilities.get`、`signals.submit`、`cases.get/timeline`、`evidence.get`、`sources.capabilities/doctor`、`source-sync-runs.get` 具有 `FROZEN` field contract；Stage 2/4 intent 保持 `SKELETON`，不能生成 route/CLI/discovery。所有 Public MCP mapping 到 Stage 6 才启用。Agent/service token 永远不能获得 human approval 或内部 release execute 权限。

## Langfuse

已批准两个方向：

1. CaseLoop 自身的 Agent/LLM/tool trace 通过 OTel/OTLP 发往 Langfuse；
2. `LangfuseTraceSource` 读取被治理 Agent 的 observations/scores。

进入 Case 前形成 `TraceEvidenceReceipt`，记录 exact Signal binding、requested/result field set、来源版本、digest、`COMPLETE/PARTIAL/UNKNOWN` 和缺失项。无 trace locator 时固定为 `collection_mode=NO_LOCATOR / query=null / AgentRunRef=null / UNKNOWN`，禁止伪造 run。Case 主状态仍为 `OPEN`，另记 `correlation_status=NEEDS_CORRELATION`。Langfuse 不是 CaseLoop 权威数据库，也不默认拥有全部输入输出。

## Skill/MCP 自进化

自进化是“自动生成和筛选下一候选”，不是让在线 Skill 原地修改自己：

```text
真实失败/能力缺口
  → UNTRUSTED_IMPORT / SkillCandidate
  → quarantine + digest/signature/license + safe unpack
  → static scan + hermetic build + SBOM/provenance
  → old/candidate/no-skill 三臂
  → visible regression + hidden holdout + adversarial + cross-runtime
  → Gate + 首次 HumanApproval
  → immutable SkillVersion
  → registry mirror
  → 已批准 digest/cohort 的 Policy canary
  → promote / rollback / revoke / compensate
```

Stage 5 前不得生产安装、外部发布或 Policy promote Skill/MCP。阿里云是发布、分发和扫描 Adapter；内部 PostgreSQL Registry 仍持有权威版本与裁决。

## 建设顺序与当前状态

| Stage | 目标 | 当前状态 | 关键出口 |
|---|---|---|---|
| 0 | 正式 PRD v2、ADR、v4 contracts、Intent Registry、迁移语义 | `DONE (contract-only)` | owner/command/event、auth/idempotency/error、双轨 evidence 与 v3 cutover contract review 已通过；不代表 runtime/live |
| 1A | authenticated maintainer report without trace：Signal + HTTP/CLI | `IMPLEMENTED IN WORKING TREE / VERIFIER PENDING` | 本地无 trace 维护报告必须完成独立 verifier、evidence 与 completion commit 后才能关闭；当前不声明 `DONE`/live |
| 1B | Langfuse 读取 + CaseLoop OTel 输出/回读 | `NOT IMPLEMENTED / PROVIDER-LIVE BLOCKED` | connector/008/runtime 尚未施工；真实读取和独立 sink 回读还需要轮换凭证、单独 live 授权与 provider receipt |
| 2A | Durable Work Kernel | `NOT STARTED` | lease/fence/retry/cancel/reconcile 与动作前 Proposal 的 PG 因果语义成立 |
| 2B | Direct Claude Runtime | `NOT STARTED` | fixture 中 child Attempt 的 claim、runtime/model/tool receipt 和终态语义成立 |
| 2C | AgentTeams Parent Delegation 与 Coding Team | `NOT STARTED` | 在 2C 内从已批准 design manifest 生成、审查、部署版本钉定 manifest，再完成真唤醒/父子 Attempt/Reviewer Finding/Team 外 Gate；Manager/exporter 代跑必须失败 |
| 3 | Coding Generator–Evaluator 修复循环 | `NOT STARTED` | 首个 code patch/draft PR candidate 通过 sandbox、独立 Reviewer 和确定性 Gate；外部写仍不发生 |
| 4 | PolicyGrant 与 Guarded Autopilot | `NOT STARTED` | 低风险预授权动作可回滚；高风险动作不能伪装成人批 |
| 5 | Skill/MCP 自进化与阿里云 | `NOT STARTED` | 官方 SLS Skill 真调用；自有 Skill 与自有 MCP 分别完成固定版本、真实 Worker canary、rollback/revoke |
| 6 | remote OAuth MCP、完整 Skills/plugins、SDK、A2A | `NOT STARTED` | 多入口语义一致；Agent token 不能审批或发布 |
| 7 | 生产与开源成熟 | `NOT STARTED` | 两个非同构真实 workload、灾备、升级回滚、key rotation 和真实 pilot |

每个 Stage 都有自己的 entry/exit、focused tests、verifier、`evidence/v4/stage-<n>/`、rollback 和 semantic commit。只重跑被新代码或新 evidence contract 影响的边界，不因时间经过重跑所有历史 live。

## 首个真实 Coding 案例的外部动作边界

Stage 3 先在本地完成：重新确认 issue 状态和贡献规则、固定最新 SHA、复现失败、生成 patch、跑 focused/full tests、独立审查 diff 与证据。候选优先级与实时状态必须重新核验。

以下动作均未因本计划获批而自动授权：GitHub 留言、认领、fork、push、创建 PR。目标仓库要求 maintainer 许可时必须先取得许可；任何外部写动作都在执行前逐次询问用户。
