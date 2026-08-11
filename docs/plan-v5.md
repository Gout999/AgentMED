# CaseLoop V5 总体架构与迁移计划

> 状态：**DEFAULT BASELINE FOR ALL NEW PRODUCT/DOMAIN DEVELOPMENT（D-015，2026-08-11）/ PARTIALLY IMPLEMENTED BY EVIDENCE**
>
> 更新：2026-08-11
>
> 产品需求：[`prd-v5.md`](prd-v5.md)
>
> 边界决策：[`D-013`](decisions/D-013-v5-ai-system-governance-and-agent-native-control-plane.md)
>
> 目标蓝图：[`plans/v5-progressive-delivery.md`](plans/v5-progressive-delivery.md)
>
> 当前执行编排：[`plans/v5-master-execution-plan.md`](plans/v5-master-execution-plan.md)

本文是 V5 的已接受目标架构、兼容规则和依赖顺序。按 D-015，它是所有新产品与领域
开发的默认设计和施工基线；`docs/plan-v4.md` 只保留为 V4 兼容基线，v3 保留为已实现
兼容 lane。该默认权威不改变 public API/CLI 的默认 major，不自动激活 route 或
capability，也不授权把完整 V5 runtime、provider、Agent、repository、external 或
production 写成已完成。绿色 contract 只能证明设计可解析，不能证明能力已实现；每项
runtime 事实仍需 migration、真实调用路径、focused tests、verifier、evidence 与 semantic
commit 独立闭合。

## 当前架构收敛与 re-entry

C0–C5 已完成并由 remediation subject `b6fa629` 通过 detached clean unified gate。D2 是当前
唯一施工入口，必须按 Master §17 冻结完整 `system-versions.record/get/diff` bundle；R3、R4
与 V5-2+ 仍不得以“V5 已成为默认基线”为由绕过 predecessor。
收敛后的 control plane 仍是模块化单体：一个权威业务事务由一个 PostgreSQL unit of
work 持有原子边界；domain owner 决定领域成功，compat/shared/facade/projection、
transport 与 adapter 都不得创建第二套成功 authority。

收敛按独立 wave 进行，每一 wave 必须保持既有行为：public wire 与默认 major、route/
capability 开关、auth-before-replay 顺序、稳定错误、transaction boundary、event/outbox/
receipt/audit bytes 与 cardinality、idempotency 和 replay 都不得漂移。模块搬迁不得与新
领域能力、contract shape 或持久化语义变更混在同一 wave；任一行为保持 gate 失败即停止
该 wave。收敛关闭后，后续 V5 stage 仍按 Master Execution Plan 的证据门逐项施工。

## 0. 总裁决

V5 不继续采用“先完成 CaseLoop 自有 Coding Team，再在末期开放 Agent-native
入口”的 V4 关键路径。新的产品结构是：

```text
Human Console + Agent Capability Gateway
                  ↓
        Canonical Application Intents
                  ↓
     Deterministic Governance Kernel (PG)
                  ↓
Evidence / Repo / Runtime / Eval / Deploy / IAM Adapters
```

CaseLoop 可以调度内部 Worker，也可以消费外部 Agent 提交的 Candidate，但两者都
不是 lifecycle authority。AgentTeams、Claude Code、具体 provider/model 和 trace
vendor 都是可选 Adapter。

## 1. 不可跳过的 V5 铁律

1. PostgreSQL 是 lifecycle、authority、idempotency、approval、release 和 audit 的
   唯一权威；LLM/Agent 只提交非权威产物。
2. 一个 durable business fact 只有一个 aggregate owner；projection、Coordinator、
   A2A Task、MCP tool 和 Console 不能拥有领域成功。
3. `Desired`、`Observed`、`Effect` 分开记录，移动 desired pointer 不能证明 observed
   生效，Approval 不能证明 effect 成功。
4. immutable Candidate 与 exact EvaluationPlan/GateReport 绑定；只有 required
   evidence 全部 `PASS` 才能创建可执行 WorkOrder。
5. `FAILED/INCONCLUSIVE/ERROR/UNKNOWN`、缺失、重复、换绑和不完整证据 fail closed。
6. external operation 采用 at-least-once intent + exact resource reconciliation，
   不声称跨系统 exactly-once 或原子回退。
7. rollback 至少包含 stop exposure、restore assignment、verify observed、reconcile
   in-flight/UNKNOWN、revoke 和 compensation。
8. Agent token 不能 human approve 或取得 internal release execute authority。
9. 历史记录、receipt、digest 和 evidence 不就地改义；迁移使用 expand-and-route。
10. current implementation、contract target、replay、provider live、Agent causal 和
    production canary 分栏报告。
11. V4 Candidate/Evaluation/Gate/WorkOrder 等 JSON Schema 是封闭合同；V5 系统对象
    使用 schema-major-2 严格 profile，保留同一逻辑 owner 与已有 lifecycle，不做原位
    继承；V4 缺失的人类 Approval lifecycle 由 V5 补齐。
12. 每个 V5 权威记录都使用 workspace/revision/digest/AuthorityReceipt envelope；
    projection 不得冒充可绑定的不可变证据。

## 2. 架构边界

### 2.1 Public Gateway

负责 auth、workspace binding、contract negotiation、scope、idempotency、rate/budget、
request/response envelope 和 audit commit。HTTP 是能力基线；其他 transport 只做映射。

### 2.2 Application Catalog

负责 `AIApplication`、`Environment`、`SystemComponent` 和当前 `DependencyEdge`。
不可变 `TopologyRevision` 保存进入 SystemVersionSet 的历史依赖图。它是 AI-system
service catalog，不管理通用服务器、网络和采购合同。

### 2.3 Version Controller

负责不可变 component revision 和 `SystemVersionSet`，以及 environment 的 desired
`SystemAssignment`。VersionSet 只证明 declared composition，不证明 runtime observed。

### 2.4 Signal / Case Controller

沿用 V4 1A `Signal`、`QualityCase`、SignalCaseLink 和 no-trace `UNKNOWN` 语义；通过
additive binding 把 Case 关联到 AIApplication、Environment、不可变
SystemEpisodeSnapshot 和 Problem。它投影 additive `CaseReadiness`，但不把
`NEEDS_ACCEPTANCE_CRITERIA` 变成第四套 Case lifecycle 或改写 S1A digest。

### 2.5 Evidence Controller

负责 Evidence Source query、immutable receipt、coverage/completeness、
ObservedStateSnapshot 和 ExternalEffectReceipt。可变 `SystemEpisodeView` 是从这些
receipt 构建的 provenance-preserving read model；Gate/归因只绑定带 watermark、exact
assignment generation 和 receipt set 的不可变 `SystemEpisodeSnapshot`。两者都不拥有
执行或领域成功。Source adapter 不能决定 Case/Gate/Release 成功。

### 2.6 Work Controller

继承 V4 Stage 2A 的 WorkerTask/Attempt/lease/fence/retry/cancel/UNKNOWN reconcile，
并为 CLI/MCP/A2A 异步 operation 提供 durable execution。Runtime Adapter 不能代
Worker claim 或写领域终态。

### 2.7 Proposal / Candidate Controller

复用 ResolutionContract、CandidateContract、typed Proposal、ProposalDecision 和
CandidateRevision 的逻辑 owner/状态语义。由于 V4 schema 封闭，`SystemCandidateRevision`
是 schema-major-2 successor，不是 JSON subtype；它不创建新的 `ChangeSet` 状态机。
AcceptanceCriteria/BadcaseSpec 是该 owner 下由 Maintainer/Domain Reviewer 确认的 immutable
subordinate artifact，最终由 ResolutionContract exact seal；外部 Agent 只能 propose。

### 2.8 Evaluation / Gate Controller

Evaluation Registry 冻结 EvaluationBundle/Holdout；Gate Controller 冻结 exact
EvaluationPlan、运行多维 evaluation、记录 track receipts 和 GateReport。Gate
Controller 对 candidate-verification PASS 只记录 VerifiedCandidate；只有 exact
release-authorization PASS 才创建 schema-major-2 SystemWorkOrder。

### 2.9 Scoped Executor Controller

唯一拥有 ExternalOperation；验证 WorkOrder、human Approval、target revision、nonce、
expiry、credential capability 和 kill switch。比赛 P0 不启用 PolicyGrant。Repo、deployment、closure、distribution
只是 typed Executor/Adapter。ExternalOperation 只能记录动作与 provider receipt，不能
直接改写 Version Controller 拥有的 `SystemAssignment`。

### 2.10 Read Models

`AutomationRunView`、`SystemReleaseView`、Console timeline、A2A Task 和 report/export
都是只读投影。它们可以聚合状态但不能接收领域写命令。

## 3. V5 资源关系

```text
AIApplication
├── Environment
│   ├── SystemAssignment (desired)
│   ├── ObservedStateSnapshot[]
│   ├── SystemEpisodeView[]
│   └── SystemEpisodeSnapshot[]
├── SystemComponent[]
│   └── ComponentRevision[]
├── DependencyEdge[]
├── TopologyRevision[]
└── SystemVersionSet[]

Signal[] ↔ QualityCase[]
QualityCase → ApplicationCaseBinding / CaseEpisodeBinding / EvidenceReceipt / Finding
QualityCase → CaseReadiness / AcceptanceCriteria(subordinate) → ResolutionContract
ResolutionContract → CandidateContract
CandidateContract → SystemCandidateRevision(schema-major-2)
SystemCandidateRevision + EvaluationBundle → CandidateVerificationPlan → GateReport
GateReport(verification PASS) → VerifiedCandidate / NOT DEPLOYED
SystemCandidateRevision + ReleasePlan(frozen) + EvaluationBundle
  → ReleaseAuthorizationPlan → GateReport
GateReport(release PASS) → SystemWorkOrder → human ApprovalGrant → CapabilityLease
SystemWorkOrder + ApprovalGrant + CapabilityLease → SystemExternalOperation[]
Release manager → Version Controller command → SystemAssignment (desired)
ExternalOperation → operation/effect/reconcile receipt
Observed verifier → ObservedStateSnapshot
```

## 4. Version 与 identity assurance

每个 component binding 声明：

```text
IMMUTABLE_DIGEST
PROVIDER_VERSION
MUTABLE_ALIAS
OBSERVED_ONLY
UNKNOWN
```

`SystemVersionSet` 允许包含低 assurance 组件，但 Gate、release policy 和 Console 必须
显示它。需要 exact binding 的 required component 为 `MUTABLE_ALIAS/OBSERVED_ONLY/UNKNOWN` 时不能
宣称可复现或自动发布。

`identity_assurance_summary` 必须从每个 exact component revision 确定性计算；Release
Controller 仍逐项检查原始 binding，不能信任调用方提交的 summary。

应用代码、运行时 Policy/guardrail 和 RuntimeProfile 都以 component revision 进入
VersionSet；不可变 TopologyRevision 提供 graph digest。治理用 `EvaluationBundle` 不
属于 `SystemVersionSet`。EvaluationPlan/GateReport 单独精确绑定
`(ResolutionContract, base SystemVersionSet, target SystemVersionSet, CandidateRevision,
EvaluationBundle)`；release-authorization purpose 另绑定 pre-Gate ReleasePlan。如果应用运行时本身包含
evaluator/guardrail，则该运行组件按普通 SystemComponent 版本化。

## 5. Evidence 模型

V5 暂不增加 canonical success facet 名称；沿用当前 facet vocabulary，并在 receipt
中增加 orthogonal `fact_class`：

```text
DECLARED_CONFIGURATION
OBSERVED_RUNTIME
EXECUTION_ATTEMPT
EXTERNAL_EFFECT
EVALUATION_RESULT
AUTHORITY_DECISION
```

原因：facet 表达证据来源/验收面，fact class 表达该证据证明哪类事实；二者不可混用。
未来若真实 workload 证明必须新增 facet，另立 ADR 和 schema migration。

`SystemEpisodeView` 是 evidence projection，不是新的执行权威。它带 projection revision、
`as_of`、source watermark 和 exact assignment generation，引用原始 receipt 并报告
coverage/missing。需要成为 Gate 或归因证据时，由 Evidence Controller seal 成
`SystemEpisodeSnapshot`；后来的 receipt 不改写既有 snapshot。

## 6. Evaluation 模型

Evaluation 输出五层：

```text
RunState
GateVerdict = PASS | FAILED | INCONCLUSIVE | ERROR | UNKNOWN
EvidenceCompleteness
EvaluationDimensionResults[]
AttributionVerdict
```

这里的 RunState 只是由 V4 WorkerTask/Attempt 形成的只读 projection vocabulary；它没有
独立 owner 或状态机。GateVerdict 只来自 exact `SystemGateReport`。

P0 不建设万能总分。每个 workload 分别冻结 required evaluation dimensions、required
evidence facets、hard failure、threshold、independence key、budget 和 holdout。前者描述
badcase/regression/latency 等质量维度，后者只使用 canonical evidence-facet vocabulary；
二者不能混成新 success label。Judge 只能产生 track receipt/Finding，不能决定 Release。

First Useful Case 在 Gate 之前增加 additive `CaseReadiness` 与 immutable
`AcceptanceCriteriaRevision`。它记录验收来源、base reproducer/environment、期望行为、
oracle/evaluator、适用范围和确认者；外部 Agent 只能 proposal，Maintainer/Domain Reviewer
确认后由 exact `ResolutionContract` 冻结。无可信来源时 readiness 为
`NEEDS_ACCEPTANCE_CRITERIA`，允许 Case 继续调查，但禁止制造 Gate PASS。

Evaluation 分两种 purpose：

```text
CANDIDATE_VERIFICATION
RELEASE_AUTHORIZATION
```

两者共享 exact ResolutionContract、base/target VersionSet、Candidate、EvaluationBundle 和
workload profile；只有 `RELEASE_AUTHORIZATION` exact 绑定 pre-Gate ReleasePlan，且只有它的
PASS 能创建 WorkOrder。首批 workload profile 为 `CODE_CHANGE` 与
`AI_BEHAVIOR_CHANGE`，deployment profile 区分 `LIBRARY_OR_OFFLINE` 与
`DEPLOYED_SERVICE`；profile 只决定 applicable evidence 和流程深度，不能降低权限或跳过
适用的安全维度。

`VerifiedCandidate` 只是 Candidate + verification GateReport 的只读 projection，不拥有
状态机或命令；显式请求发布后必须 seal ReleasePlan 并运行新的 release-authorization Gate，
不能复用或重命名先前的 verification PASS。

现实 Gate 至少验证 base FAIL/target PASS、仓库/历史/邻近 regression、sealed holdout 与
anti-overfit。非确定性评测使用 base/candidate 配对重复并报告原始分布/区间，次数与阈值
来自 workload 基线，不继承演示常量。确定性代码任务可把 Judge 标为有理由的 `N/A`；
LLM Judge 未经维护者标注校准只能 advisory。required `FAILED/INCONCLUSIVE/ERROR/UNKNOWN`
仍不能授权 release，但必须返回补验收标准、补证、增加样本或重跑的下一步。

## 7. Release 与 recovery

系统级 release 不是一个新的 mega-aggregate。Candidate 可以先经过不产生执行权限的
verification-only 路径：

```text
SystemCandidateRevision → CANDIDATE_VERIFICATION Gate
→ VerifiedCandidate / NOT DEPLOYED
```

只有显式请求一个可部署 target 时才进入严格发布顺序：

```text
SystemCandidateRevision → ReleasePlan（审批前冻结）
→ exact RELEASE_AUTHORIZATION Gate PASS → SystemWorkOrder
→ human ApprovalGrant → CapabilityLease
→ SystemExternalOperation(SYSTEM_RELEASE)
```

`SystemWorkOrder` 精确绑定 Candidate、EvaluationPlan、GateReport、ReleasePlan、base/
target VersionSet、assignment generation、nonce 和 expiry。根执行对象是复用 V4
ExternalOperation 状态机的 schema-major-2 `SystemExternalOperation`；Executor 只能消费
已 seal 的参数，不能在 Gate/审批后补 rollout 或 recovery。每个 operation 有自己
的 owner state、attempt、provider resource identity 和 reconcile。局部
`release-execution-manager` 只拥有 reaction ledger，不拥有 Case resolution、Gate、
Approval、assignment 或 operation terminal。它通过 outbox/reaction 向 Version
Controller 提交 exact desired-assignment command；Version Controller 独立校验并记录。
Executor 只返回 execution receipt；Version/Evidence Controller 的 assignment 与 observation
receipt 必须反向关联到根 operation。只有 exact assignment generation、所有 required
execution receipt、独立 `ObservedStateSnapshot=COMPLETE/MATCH`、所需 post-release Gate
PASS 且没有 required child/effect `UNKNOWN` 时才可 `SUCCEEDED`；adapter receipt 本身不够。

rollback 仍创建新的 `SYSTEM_ROLLBACK` operation。每次都先冻结绑定既有可信
known-good 证据的 break-glass ReleasePlan，创建新的 RecoveryWorkOrder，并取得重新认证
的人类 emergency approval；原发布 WorkOrder/Approval 不能复用，禁止直接 restore/
resume Assignment。

`SystemReleaseView` 汇总：

- exact WorkOrder/Candidate/SystemVersionSet；
- desired target；
- 每个 operation state；
- observed verification；
- effect receipts；
- rollback/compensation status；
- `PARTIAL/UNKNOWN` 原因。

## 8. Agent-native 入口

### 8.1 调用原则

- canonical intent 先冻结，再生成 CLI/MCP/A2A/SDK；
- V2 CLI 必须显式使用 `caseloop --api-version 2 ...`；默认仍是 V1，URL/header/response
  major 不一致或 downgrade 必须拒绝并审计；
- 所有 mutation 有 durable idempotency fingerprint；
- 客户端 timeout/Ctrl-C/detach 只停止等待；
- A2A Task terminal 不映射 Gate/Release terminal；
- Artifact 承载结构化结果，Message 只承载交互；
- portable Skill 只教调用方法，不授予权限；
- Public MCP/A2A 不暴露 internal work、Gate recording、approval 或 execute。

### 8.2 P0 transport

1. HTTP + CLI：所有已冻结 P0 intent；一个真实外部 Agent 通过 CLI 调用即可满足比赛
   P0 的 Agent-native consumption；
2. A2A target contract 在 V5-0 起草，只有 exact wire contract 被批准后才冻结；runtime
   在 Durable Work 后增量实现。使用 `protocolVersion: "1.0"` 和官方
   `SendMessage/GetTask/ListTasks/CancelTask`，不沿用旧 `tasks.create`；
3. Public MCP 目标基线为当前 2026-07-28 spec；tool list 按每次请求的认证 scope 过滤，
   tool annotation 只作提示而不是 authority；
4. Public MCP/A2A runtime 与 Console read model 可并行，均不能阻塞治理 kernel 或
   复制 application service。

协议目标以官方资料为准：[A2A v1.0 specification](https://a2a-protocol.org/latest/specification/)、
[MCP 2026-07-28 release](https://blog.modelcontextprotocol.io/posts/2026-07-28/)。实现 slice
开始前仍需复核当时的 current version，不能把本 draft 快照当成永久最新。

## 9. V4 → V5 处置

| V4 资产/阶段 | V5 决策 | 兼容要求 |
|---|---|---|
| S1A Signal/Case/Evidence/Auth/CLI | KEEP | 原 payload/digest/route 不改义；additive application binding |
| S1B Langfuse | GENERALIZE | 变成 Evidence Source framework；Langfuse 是 adapter |
| S2A Durable Work | KEEP / P0 | Agent-native 长任务和 recovery 前置 |
| S2B Claude Runtime | ADAPTER | 不阻塞外部 Agent Candidate path |
| S2C AgentTeams Coding Team | OPTIONAL DOGFOOD | 需要时按 agent-causal 独立验收 |
| S3 Candidate/Eval | GENERALIZE / P0 | schema-major-2 Candidate/Eval/Gate/WorkOrder 保留原 owner |
| S3 GitHub Issue | KEEP AS WORKLOAD | 本地 verified；无外部写默认 |
| S3 Prompt 专项 | MERGE | 通过 component adapter，不建平行 runner |
| S4 External Operation | IMPLEMENT / P0 | V4 当前链只到 WorkOrder；需真正实现 exact human approval/capability/operation/reconcile |
| S5 Skill/MCP | SPLIT / P1 | manifest/permission diff P0；registry/distribution P1 |
| S6 Agent-native | MOVE EARLIER | durable work 后尽早提供最小真实入口 |
| S7 Production | SPLIT | single-node recovery/backup/key rotation 前移；HA 后置 |
| v3 ChangeSet | SCENARIO COMPATIBILITY | 不复用名称、不改成 V5 aggregate |
| v3 Gate/Release/Console | ADAPT | 保留历史事实；通过显式 adapter/cutover 复用 |

## 10. 迁移策略

1. `contracts/v5/` 先作为 `draft_target_contract`，不得生成 route 或 migration；
2. V5-0 复核通过后，冻结 first slice 的 exact field contracts；
3. migrations 只 additive：application/component/version/episode snapshot/bindings；
4. S1A immutable Signal/Case/Evidence receipt 通过 link table 关联应用，不修改原 digest；
5. V5 新系统资源使用 `/api/v2`，现有 S1A `/api/v1` 保持兼容 façade；两者调用同一
   kernel，route receipt 绑定 contract version、routing key、old/new authority 和 cutover evidence；
6. v3 workload 在 crash recovery debt 关闭前只能 Shadow，不能进入 V5 external write；
7. rollback 停止新 V5 route，保留 append-only V5 facts，不把结果反写为 v3/v4 成功。

## 11. 施工阶段

| Stage | 目标 | 硬出口 |
|---|---|---|
| V5-0 | 产品、对象、owner、兼容与 first-slice contract freeze | S1A 不回归；无第三 authority；draft 经 verifier |
| V5-1 | First System Case | CLI 自动发现 manifest 草稿 + 人工确认导入 + Issue/Signal + VersionSet/UNKNOWN evidence + confirmed AcceptanceCriteria 或诚实 NEEDS_ACCEPTANCE_CRITERIA |
| V5-2 | Durable capability plane | Work Kernel + HTTP/CLI；真实 Agent CLI 调用，MCP/A2A 可独立增量验收 |
| V5-3 Core | System evidence | desired/observed/effect + receipt graph + repo-sandbox/UNKNOWN |
| V5-3 Adapter | Evidence Source integration | Langfuse/OTel 等真实 source 独立验收，不阻塞比赛 |
| V5-4 | System candidate & Gate | 外部 Agent Candidate + fail-to-pass/regression/holdout + Gate 自验证；verification PASS 不冒充 release |
| V5-5 | Guarded release & recovery | 仅可部署 target：release-authorizing Gate + exact approval + local/shadow operation + observed verify + rollback/reconcile |
| V5-6 | Operations & ecosystem | Incident/SLO/cost/supply-chain/adapters/production maturity |

具体 entry、文件、tests、evidence 和 rollback 见 progressive delivery draft。

## 12. 比赛 P0 边界

完整 V5 仍以上述平台能力为目标；比赛关门线单独收窄为：

- 单 Workspace、单 AIApplication、单 Environment；
- CLI 只读自动发现 manifest 草稿，经确认后一次性导入，生成一个以真实 workload 组件为主的 SystemVersionSet；
- 一个真实、可本地运行/切换版本的 AI 开源应用服务 Issue；
- 一个外部 Coding Agent 通过显式 V2 CLI 调用；MCP/A2A 有 runtime evidence 时另列；
- 单一主要变更面；
- maintainer-confirmed AcceptanceCriteria、base FAIL/target PASS、regression、sealed holdout/
  anti-overfit、repo-sandbox Gate；完整 attribution 可 abstain 且不阻塞；
- 预先 seal 的 ReleasePlan、SystemWorkOrder 和人类 approval；
- 本地 target adapter、shadow assignment、独立 observed digest、post-release Gate 与 rollback drill；
- 一个由真实权威数据驱动的 Case detail Console，串起 Case、Gate、Approval、Release 和 Observed；
- 无远端写，除非另行逐动作授权。

普通代码库/library Issue 可以作为 `CODE_CHANGE` 对照，在 Candidate verification 后以
`VerifiedCandidate / NOT DEPLOYED` 结束；它不需要伪造 shadow runtime，也不能替代上述
deployed-service 路径对 observed/recovery 的证明。比赛案例数量从少量真实样本开始，
但 Gate 必须同时回放已知无效 Candidate 和真实已合并修复，报告 false-pass/false-block。

比赛不要求 generic catalog CRUD/graph、SystemEpisode public endpoint、完整 attribution
engine、无外部 effect 时的 ExternalEffectReceipt、通用 operation list/cancel、三套 Console
工作区、生产流量 canary、完整 A2A/MCP 生态、多租户、HA、合规或自动补偿所有不可逆作用。
这只是比赛纵切的交付顺序，不删除完整 V5 的多应用/多组件、持续来源、完整 Console、
Agent 协议生态、供应链、memory/data、Incident/SLO、成本与 vendor 治理目标。

## 13. 开工 Gate

V5 implementation 在以下条件满足前为 `NO-GO`：

1. 本文、PRD、progressive delivery、D-013 与 `contracts/v5/` 一致性审查通过；
2. 对象/owner/command/event 无重复 authority；
3. S1A immutable payload/digest 的 additive linking 方案冻结；
4. V3 ChangeSet、Gate、Release 与 V5 Candidate/Operation 无命名或 owner 混淆；
5. first slice exact wire contracts 被明确批准；
6. PLANS、PROJECT_STATE、LAST_HANDOFF 和 Wiki 指向同一施工起点；
7. schema-major-2 exact bindings、AuthorityReceipt、ReleasePlan→Gate→WorkOrder→human
   Approval→ExternalOperation 顺序经机器测试；
8. 从空 V5 领域表（预置已启动的 V4 Controller trust roots 与一个既有 V4 Case）经
   trusted manifest import 构造 First System Case 的正反 fixture 通过；
9. V5-5 前真正完成 V4 Stage-4 human grant、capability 与 ExternalOperation runtime，不能只引用 skeleton；
10. 只有触达对应 live/provider/external Adapter 时才要求其凭据轮换；external write 仍逐次授权。
11. acceptance provenance/confirmation、Case readiness、workload/deployment profile 与
    dimension applicability 已有 exact contract；Issue/Agent proposal 不能自动确认金标准；
12. verification-only Gate PASS 不产生 WorkOrder，release-authorizing Gate 仍完整绑定
    ReleasePlan→WorkOrder→human Approval→ExternalOperation，并有正反 fixture；
13. fail-to-pass、holdout/anti-overfit、Judge `N/A/advisory/required` 与 required calibration
    有对抗 fixture；演示 repetitions/threshold 不被合同冻结成通用质量保证；
14. `caseloop init`/`case from-issue` 被证明只编排 canonical intents，在失败/重试时不产生
    第二 owner、重复 Case、自动 confirmed acceptance 或 observed claim。
