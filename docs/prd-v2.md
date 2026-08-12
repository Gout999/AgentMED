# CaseLoop 产品需求文档 v2

> **生命周期标记（2026-08-12）：SUPERSEDED** —— v4 产品方向已被 V5 承接：新产品/领域开发以 `docs/prd-v5.md` 与 V5 blueprint/contracts 为默认设计与施工基线（D-015），本文不再作为新开发的产品需求入口。V4 仅保留为兼容 lane，其兼容表面继续由 `docs/plan-v4.md`、`contracts/v4/` 与 V4 evidence 约束；本文仍是 v4 目标产品的历史定义。不能从本文推导 V5 runtime/live 已实现。

> 状态：**APPROVED TARGET / IMPLEMENTATION IN PROGRESS**
>
> 日期：2026-08-10
>
> 上位原则：[`product-principles.md`](product-principles.md)
>
> 施工与架构：[`plan-v4.md`](plan-v4.md)；文件级执行台账：[`plans/v4-progressive-delivery.md`](plans/v4-progressive-delivery.md)
>
> 兼容边界：本文定义 v4 目标产品。当前已实现的客服 Scenario Pack 仍由 `plan-v3.md`、现有 `contracts/`、migrations 和可执行测试约束，直到相应能力被显式迁移。本文获批不等于功能已实现。

## 1. 产品定义

CaseLoop 是一个可由人、CI 或其他 Agent 调用的开源 Agent 质量与变更治理系统。它把散落在反馈渠道、trace、日志、评测、代码仓库和发布系统中的质量处理工作，收口为一条可追踪、可验证、可回滚的闭环：

```text
Signal → Case → Run/Trace evidence → Investigation → Candidate
       → Evaluation/Gate → Approval/Policy → Scoped execution
       → Observe/Reconcile/Rollback → Closure → Regression asset
```

CaseLoop 不是 Agent runtime、trace 存储、通用聊天编排器或“多个 LLM 同意即可自动发布”的系统。PostgreSQL 控制面持有生命周期、权限、幂等、审批、发布、审计和因果记录；Agent 只提交假设、Finding、Proposal 与候选工件。

“支持所有 Agent”的产品含义是：核心契约不绑定客服、coding、某个模型或 runtime。能提供至少一种质量 Signal，并能提供运行/版本证据、验证接点或受控动作接点的 Agent，可以按能力逐步接入。无法观测、无法版本化、无法复验或无法隔离副作用的对象只能进入观察、补证或 `UNKNOWN`，不承诺自动归因和自动修复。

## 2. 目标用户

### 2.1 首发用户

首发面向国内 2–8 人、运行 1–3 个真实 Agent 的产品团队。开发者常兼维护负责人，已有 Langfuse、GitHub/GitLab、飞书或 CI 中的至少两项，但没有专职 Agent 平台团队。

主要身份：

| 身份 | 主要任务 |
|---|---|
| Integrator | 安装、自托管、连接 trace/feedback/repo，设置数据与凭据边界 |
| Reporter | 提交内部或外部质量 Signal，补充上下文，查看处理状态 |
| Maintainer | 关联运行、审阅调查和候选、运行验证、处理 `UNKNOWN` |
| Domain Reviewer | 判断业务语义和不可机器验证的结果 |
| Release Approver | 批准确切 hash、目标、范围、nonce 和有效期 |
| External Agent / CI | 通过 HTTP、CLI、Skill 或 MCP 报告、调查和读取状态；不能冒充人批 |

一个人可以兼任多个岗位，但系统仍保留不同 principal、scope 和审计记录。

### 2.2 暂不承诺

- 没有任何 Signal、run、版本或验证接点的黑盒系统；
- 首次接入即要求不可逆、高损失、钱/身份/敏感数据写入的全自动场景；
- 以 CaseLoop 代替客户既有 observability、eval、sandbox、runtime 或 identity 平台；
- 无人监督的自我修改评测、隐藏考题、审批策略或生产权限。

## 3. 用户价值与成功标准

首个可用价值不是“全自动修复”，而是 **First Useful Case**：一条真实坏 trace 或维护报告进入 CaseLoop，形成确切 `signal_id` 与 `case_id`，显示来源、运行/版本绑定、证据完整性、缺失项、去重结果和下一步。

产品依次证明四层价值：

1. **看清**：Signal 能关联到运行、版本和证据；缺失被诚实标注；
2. **查明**：真实 Worker/工具提出可证伪的调查与 ResolutionContract；
3. **修好**：候选在冻结基线、sandbox 和独立 Gate 下通过；
4. **安全执行**：人批或窄范围 PolicyGrant 约束动作，结果可对账、回滚或进入 `UNKNOWN` reconcile。

核心指标：First Useful Case 用时、Signal→Run/Version 关联率、trace completeness、verified-resolution rate、复发率、人工分钟、Proposal 接受率、Gate false-pass、`UNKNOWN/INCONCLUSIVE` 率、每个 verified Case 成本、未经授权动作数（目标 0）、回滚/撤销成功率。

## 4. 当前实现与 v4 目标

| 能力 | 当前事实 | v4 目标 |
|---|---|---|
| 参考场景 | 小智客服 v3 Scenario Pack | 保留并适配到通用 Signal/Closure |
| 内部 Agent Team | 六个 CoPaw + StepFun `step-3.7-flash` 质量 Worker；运行状态见 context | 保留质量 Team，另建专业 Coding Team |
| Trace | 少量 OTel span 和业务 `trace_id` 字符串；无 Langfuse Adapter | 标准 RunRef/TraceEvidenceReceipt；Langfuse 首个 TraceSource |
| Agent 因果证据 | 历史平台记录与未提交 exporter 路径不能证明新口径下的 Worker 因果 | WorkerTask/Attempt/receipts/pre-action Proposal/PG causation |
| 公共 Agent 入口 | 现有 MCP 是内部角色投影 | canonical HTTP + CLI/Skill；Public MCP 是薄 Adapter |
| Coding workload | 未实现 | 独立 Coding Team + Claude Code child Attempt + sandbox Gate |
| Skill 演化 | 需求与研究；无生产治理闭环 | 候选、三臂 eval、签名、固定版本、canary、revoke |

## 5. 核心对象

### 5.1 租户与治理对象

- `Workspace / Project / Environment`：从第一版 schema 即存在；首版运行可只支持单 Workspace；
- `GovernedAgent`：被治理对象，不等于调用 CaseLoop 的 Agent principal；
- `AgentVersionSet`：可扩展的版本快照，包含模型、prompt、knowledge、Skill、tool/MCP schema、harness/orchestrator、policy 和 environment digest；
- `SourceConnection`：Signal/trace/issue/monitor 等来源，只保存 credential reference 和 capability，不保存明文 secret。

### 5.2 质量输入与证据

- `Signal`：外部投诉、内部负反馈、维护报告、trace anomaly、tool failure、eval regression、monitoring alert、security event；
- `AgentRunRef`：来源、project/environment、trace/root span、run time、VersionSet 与 immutable locator；
- `TraceEvidenceReceipt`：查询窗口、字段范围、source response/artifact digest、completeness、missing fields、retention 和 provenance；
- `Case`：问题处理的领域案件；Signal 和 Case 允许多对多但关联必须可追踪、可 supersede。

### 5.3 Agent 工作与候选

- `WorkerTask`：Controller 创建的 typed work request；
- `Attempt`：一次实际 executor/session 的 lease、fence、runtime/model/Skill/MCP receipt 容器；fallback 必须新建 Attempt；
- `ResolutionContract`：公开的目标、范围、基线、约束和可见 eval；
- `CandidateContract`：开工前冻结的一次候选 planned revision、base、允许改动面与公开验证边界；
- `CandidateRevision`：确切 Attempt 先在隔离 sandbox 生成并密封候选 artifact，ChangeProposal 绑定该 `OUTPUT_RECORDED` snapshot 并在 Controller/repo/release 等受控动作前完成裁决；Attempt 终止后再固化不可变 artifact/diff/target snapshot。Proposal 不是 plan artifact、不引用未来 revision，runner 也不能预填 known-good 产物；
- `Proposal / Finding / AgentIntent`：typed 非权威产物；ChangeProposal 必须在候选 sandbox output 密封后、任何受控下游动作前提交，其他外部动作意图仍须在动作前提交；
- `ProposalDecision`：Controller 的接受/拒绝记录，与首个下游 causation event 同事务；
- `EvaluationPlan / GateReport-v4 / WorkOrder-v4`：EvaluationPlan 在候选生成前冻结；GateReport 在构建后对 exact CandidateRevision 形成确定性结论，并仅在 exact `PASS` 后创建可授权 WorkOrder。

### 5.4 外部动作

- `ApprovalGrant`：由人类 authority 批准确切 WorkOrder hash；
- `PolicyGrant`：按 tenant/project/environment/risk/action/target/budget/expiry 限定的预授权；
- `ExternalOperation`：Repo、Quality、Closure、Skill distribution 等确定性 Executor 的幂等操作和结果对账；
- `AutomationRunView`：跨聚合只读投影，不拥有领域状态机。

## 6. Signal 与 Langfuse

### 6.1 Signal 入口

首批支持：Manual HTTP/CLI、Langfuse negative score polling、无 trace 的 maintainer report。后续增加飞书、Quality API feedback、GitHub/GitLab、Sentry/OTel/eval CI 和客户端 hooks。

每个 Signal 必须：

- 按 `(workspace, source, source_event_id)` 去重；
- 先保留原始内容 digest/ref，再规范化；解析失败进入可见 DLQ；
- 区分 external/internal/synthetic，禁止 bot seed 冒充真人投诉；
- 允许 `NEEDS_CORRELATION`，不得为跑通伪造 trace；
- 有 acknowledgement、correlation 状态、closure 和 reopen 路径。

### 6.2 Langfuse 双向需求

1. CaseLoop 自身通过标准 OTel/GenAI instrumentation 把内部模型、Agent 和工具调用发送到可配置 Langfuse；
2. Langfuse TraceSource 以 project-scoped credential 增量读取被治理 Agent 的 observation/score，按 trace ID 组装 Run evidence；
3. source credential 视为广权限 secret；CaseLoop 只暴露 allowlisted read operations，不宣称官方 key 是细粒度只读；
4. polling 使用 watermark、重叠窗口、ID 去重、cursor CAS、重试和 DLQ；
5. retention 前固化所需 evidence receipt/artifact；Langfuse 不是权威状态库；
6. 脱敏、采样、丢 span、留存、权限或 instrumentation 不全时返回 `PARTIAL/UNKNOWN`。

## 7. 双 Team 与执行身份

### 7.1 现有质量治理 Team

`caseloop-team` 保留六个职责：`quality-officer`、`collector`、`attributionist`、`repairer`、`gatekeeper`、`case-officer`。它是 v3 客服 Scenario 和通用质量调查的参考 Team，不是专业 coding Team；固定 warm pool 不等于所有角色每次都参与。

### 7.2 新增 Coding Team

`caseloop-coding-team`：

- `coding-planner`：复现问题、冻结基线、提出 ResolutionContract；
- `coding-generator`：提出执行委托并消费 Claude Code child Attempt 的 patch；
- `coding-reviewer`：独立对抗审查、运行 sandbox、提交 Finding；
- deterministic Code Gate / Repo Executor 均在 Team 外。

GLM-5.2 是目标主模型，但必须先通过官方 origin、结构化输出与最小真实 smoke。StepFun 3.7 Flash 保留给现有质量 Team 和低成本分诊，不作同一 Attempt 的静默 fallback。Reviewer 与最终 Generator 必须满足 independence key，否则转人工或 Gate `ERROR`。

### 7.3 Claude Code 委托

Claude Code 是受控执行 harness，不是 AgentTeams 当前原生 Worker，也不是额外计数的虚构 Agent。父 AgentTeams Worker claim 后提交 `DelegationProposal`；Controller 接受后创建 child `WorkerTask` 与一次性 `CapabilityLease(DISPATCH_CLAIM)`；host Adapter 只能用它启动/claim 指定 child。native session claim child WorkerTask 时才由 Work Controller 创建 child `Attempt`、lease/fence 与另一份 `CapabilityLease(ATTEMPT_RUNTIME)`。binary/session、`model_resolution_receipt_digest`、`model_call_receipt_digest`、tool/diff/exit receipts 与 `ChangeProposal` 均绑定 child Attempt；patch 作者是 child Attempt，父 Attempt 只是委托者。

模型进程不获得 human approval、Release、push/merge、生产或长期 Worker token。Repo 写动作由确定性 Repo Controller 在独立授权后执行。

## 8. 公共调用面

公共 HTTP 是唯一能力基线；CLI、portable Skill、Public MCP、SDK/Webhook 和未来 A2A 都是同一 intent/application service 的 Adapter。

Stage 0 v1 Intent Registry 是 target catalog；`wire_status` 决定当前是否已有可执行字段合同：

- Stage 1 S1A `FROZEN`：`capabilities.get`、`signals.submit`、`cases.get`、`cases.timeline`、`evidence.get`；
- Stage 1 S1B `FROZEN`：`sources.capabilities`、`sources.doctor`、`source-sync-runs.get`；
- Stage 2 `SKELETON`：`investigations.start`、`runviews.get`、`work.stop-request`；
- Stage 4 `SKELETON`：`approvals.decide`、`releases.rollback-request`、`external-operations.get`。

每个 intent 机器可读地声明 `execution_mode`、`transport_stages`、`activation_stage`、`wire_status` 和 `field_contract_ref`。只有 `FROZEN + implemented + caller authorized` 才生成 HTTP/CLI 并进入 `capabilities.get`；SKELETON 即使已有 method/path/CLI target mapping 也不得暴露。`report` 只是 `signal submit` alias；Public MCP/A2A 到 Stage 6 才启用。

审批决定只进入人类 Console/HTTP/CLI；不进入 model-invocable MCP、A2A 或 portable Skill。Release execute 永不进入公共 Agent token。

所有 mutation 按 `(workspace, principal, intent, idempotency_key)` 保存 canonical request fingerprint；同 key 同请求返回原结果和原 immutable receipt，不重复执行；同 key 异请求返回稳定 `IDEMPOTENCY_CONFLICT`。`replayed` 是 delivery metadata，不进入 receipt self-hash。CLI/MCP/HTTP 必须共享 resource ID、状态、error code、request ID 与可信 audit ref；认证前 workspace 或 audit commit 失败时对应字段为 null，禁止伪造。

## 9. 自治与权限

采用三种产品模式：

| 模式 | 能力 | 默认外部写 |
|---|---|---|
| Shadow | intake、关联、证据、报告 | 无 |
| Copilot | 调查、候选、验证、草稿 WorkOrder/PR | 仅受控草稿 |
| Guarded Autopilot | 预授权低风险 canary/promote/rollback | PolicyGrant 限定 |

风险级别至少覆盖：只读、可丢弃草稿、可逆配置/流量动作、外部通知/数据写、代码合并/权限扩大、钱/身份/不可逆动作。首次真实 GitHub issue 的留言、认领、fork、push、draft PR 和 merge 均逐次人批；低风险自动化必须通过真实 pilot 赢得权限。

## 10. Skill/MCP 自进化

CaseLoop 支持自有 Skill/MCP 与阿里云适配，但 Agent Skills 的 `SKILL.md` 只是可移植描述格式，不是安全、签名或发布权威。

每个 capability 由 immutable package digest、版本、来源、SBOM/provenance、权限/网络/secret sidecar、依赖和兼容范围组成。候选经过 quarantine、old/candidate/no-skill 三臂、隐藏 holdout、供应链/权限 Gate、人批、固定版本 publish、下载 digest 核验、Worker canary、promote/revoke/rollback。

阿里云 profile 的目标证据：官方只读 SLS Skill 被真实 Worker 调用；自有 Skill 通过 MSE AI Registry 固定版本发布/下载/canary；AISC 是独立安全轨，不能代替功能、SBOM、许可证或权限验证。Model Studio 只在书面赛制或采用对应 managed runtime 时接入。

## 11. 功能阶段

| Stage | 用户可见能力 | 硬出口 |
|---|---|---|
| 0 | 统一语言、PRD、contracts 与施工基线 | ownership/identity/error/idempotency/cutover conformance 通过 |
| 1 | Manual + Langfuse Shadow First Useful Case | 真实 trace 与无 trace 报告各跑通；无 AgentTeams 也有价值 |
| 2 | Work Kernel、Claude Adapter、Coding Team 真因果 | Worker/Claude 关闭即失败；pre-action Proposal 到 PG causation 闭合 |
| 3 | 真实 coding issue 的本地 patch + review + Gate | 固定 base、隔离 sandbox、真实失败复现和验证；无 GitHub 写 |
| 4 | 人批的 RepoChange / draft PR，随后受控低风险策略 | 精确 hash 审批、幂等、UNKNOWN reconcile、kill switch |
| 5 | Skill/MCP 自进化与阿里云证据 | 三臂/holdout/supply-chain/canary/revoke 通过 |
| 6 | remote MCP、portable clients、SDK/webhook、条件 A2A | 各入口同 intent/authority；Agent token 不能审批/发布 |
| 7 | 开源生产成熟 | 两个非同构 workload、Day-2、DR、N/N-1、安全与 pilot 证据 |

详细 entry、文件、migration、测试、live、rollback 和提交边界以 `docs/plans/v4-progressive-delivery.md` 为唯一施工台账。

## 12. 非功能要求

### 12.1 安全与隐私

- secrets 只存引用，按 connector/runtime/executor 分权；日志、evidence、CLI argv 不得泄漏；
- client-side masking 优先；raw data 是否曾进入外部系统必须诚实说明；
- workspace/project/environment 从 schema 首日隔离；跨租户引用 fail closed；
- evidence retention、业务删除、legal hold、审计保留分开；
- 恶意 Signal/issue/trace/Skill 都作为不可信输入，不得扩大工具或网络权限。

### 12.2 可靠性

- mutation 幂等、outbox/reaction 去重、lease/fencing、retry/DLQ；
- timeout、取消、断连、stdout 损坏、上游 unknown outcome 不得包装成失败前无副作用；
- 客户端 detach 不取消 durable operation；外部动作必须可 reconcile；
- audit 写失败使业务事务失败；平台日志和 exporter 签名不是业务权威。

### 12.3 兼容与开源体验

- public API major version、payload schema version、event version、CLI/server handshake 和 N/N-1 策略在 Stage 0 冻结；
- migrations 使用 expand/backfill/contract；禁止以 `create_all` 代替部署路径；
- macOS arm64 与 Linux x64 的 clean-machine quickstart 进入 Stage 1 证据；
- 单机 Compose 先成熟，K8s/HA 由真实 pilot 需求驱动；
- 公开 evidence 不含 secret/PII，依赖许可证、来源和修改记录完整。

## 13. 证据与完成定义

证据只使用这一组 canonical facets：`contract`、`replay`、`domain-provider-live`、`agentteams-native`、`claude-runtime-live`、`agent-causal`、`repo-sandbox`、`human-authorized-external`、`production-canary`。一个 facet 通过不能替代另一个。

`platform evidence export` 只是对 Matrix/MinIO/CR 等平台记录的只读采集类别，不是成功 facet；它不能满足 `agent-causal`。关闭必需 Worker 时 agent-causal 验收必须失败。

每个 Stage 完成需要：真实调用链实现；PG 权威记录与 audit 同事务；幂等、fail-closed 和 recovery 测试；相关 unit/integration/contract/replay 通过；受影响的 live boundary 诚实报告；独立 verifier 无未解决 P0；`evidence/v4/stage-<n>/`、`PLANS.md` 与 context 指向 completion commit。

## 14. 外部动作边界

本地分支、文档、contracts、测试和渐进实现已获授权。以下仍需逐次询问：付费 provider live、GitHub/GitLab 留言或认领、fork、push、创建/关闭 PR、merge、真实生产写、真人审批以及外部 Skill 发布。首次真实 coding case 先做到 verified local patch；外部 PR 是后续独立验收。

## 15. v3 Scenario Appendix

小智客服继续提供 B1 prompt 回归、Quality API、Case/Gate/Approval/Release/Outbox/Trust 的参考实现和历史证据。投诉、飞书回复、固定六角色和三层归因属于该 Scenario，不直接定义 v4 核心。

用户确认此前发生过 live 运行；该历史不因时间经过自动失效。是否需要重跑由代码/provider 路径或 evidence contract 是否变化决定。旧 provider/domain live 不自动满足新 `agent-causal` contract，新验收也不能反过来声称历史 live 从未发生。
