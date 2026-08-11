# CaseLoop 项目上下文

> 归档状态：**HISTORICAL / SUPERSEDED**
>
> 原路径：`docs/context/V5_CONSTRUCTION_CONTEXT.md`
>
> 本文保留 2026-08-11 交接文本；其代码基线是 2026-08-10 的 HEAD `850310c`。
> 该源文件此前未进入 Git 历史，不能从 commit 单独重建；归档只保存收到的原文与来源说明。
> 当前 V5-0B/0C 已冻结，
> V5-1A/B/C 已进入 repair/closure worktree；请改读
> [`v5-master-execution-plan.md`](../../plans/v5-master-execution-plan.md)、
> [`PROJECT_STATE.md`](../../context/PROJECT_STATE.md) 和
> [`LAST_HANDOFF.md`](../../context/LAST_HANDOFF.md)。
>
> 本文不能证明当前 runtime、测试、live/provider、Agent 或 production 状态。

> 上下文快照：2026-08-11
>
> 仓库：`[REDACTED_LOCAL_PATH]/caseloop`
>
> 当前分支：`codex/v4-foundation`

## 1. 项目定位

CaseLoop 是一个面向 AI 应用的 Agent-native 治理运营控制面。

它治理的顶层对象是完整的 `AIApplication`。Agent 是其中一种
`SystemComponent`；应用代码、模型、Prompt、RAG、Skill/MCP、Tool、Policy、Memory
和 Runtime 都可以进入版本、证据、评测和变更闭环。

项目面向已经在开发或运行一个或少量 AI Agent / LLM 应用、但尚无完整平台治理团队的
产品和工程团队。核心使用者通常是同时承担应用维护、质量判断和发布责任的工程负责人。

CaseLoop 的首个用户价值，是把模糊的用户负面反馈或 GitHub Issue 转成一条有来源、
可复现、可判定的 bad case，并说明：

- 当时运行的是哪个系统版本；
- 期望行为由谁确认；
- 候选修改是否真正解决问题；
- 当前还缺少什么证据。

当前“小智客服”是第一条参考工作负载，不是最终产品边界。

## 2. V3、V4、V5 的关系

### V3

V3 是已经存在的客服 Scenario Pack 实现与兼容基线。仓库仍保留并验证过：

- P0-1：Gate、GateReport、WorkOrder 和发布绑定；
- P0-2：outbox、审计、通知和 Trust Ledger；
- P0-3：权威 Console read model；
- P0-4：客服 B1 replay 闭环及可移植证据。

这些能力是客服场景中的历史兼容资产，不等同于完整 AI 应用治理，也不是 V5 已实现的
证明。P0-4 replay 已存在，但最后一份独立证据快照中的 live acceptance 没有闭合。

### V4

V4 仍是当前仓库已批准的新开发基线：

| 阶段 | 当前状态 |
|---|---|
| Stage 0 | `DONE (contract-only)` |
| Stage 1 Entry | `DONE (contract-only)` |
| Stage 1A | `DONE (local runtime)` |
| Stage 1B 至 Stage 7 | 尚未实现或暂停 |

Stage 1A 的实现提交是 `22c23f8`。它实现了首个通用 public V4 纵切：经过认证的
HTTP/CLI 入口，把 maintainer report 事务性写成：

```text
Signal
→ OPEN QualityCase
→ SignalCaseLink
→ UNKNOWN Evidence
→ idempotency / audit / outbox / authority records
```

Stage 1A 没有证明真实 provider、Agent runtime、代码仓库操作、人工授权外部动作或生产
能力。

### V5

V5 的产品方向已经由 D-013 接受，但运行时尚未实现。

`docs/prd-v5.md`、`docs/plan-v5.md`、progressive blueprint 和 `contracts/v5/` 当前属于
`DRAFT TARGET / NOT IMPLEMENTED`。V5 不新建第三套 Case、Gate 或 Release 栈，而是在
保持 V4 logical owner 和 lifecycle 的基础上，用 schema-major-2 system profile 泛化
Candidate、Evaluation、Gate、WorkOrder、Approval 和 ExternalOperation。

当前 `contracts/v5/` 是不可路由、不可发现的 draft。此前 36 个 V5 focused tests 和
485 个 v3/v4/V5 combined tests 已通过，但这些测试早于最新产品评审修改，尚未覆盖新的
acceptance provenance、workload profile、两类 Gate 和 CLI workflow 语义。

## 3. 目标用户面对的问题

AI 应用的负面反馈通常散落在 GitHub Issue、用户对话、客服记录、trace、日志、评测和
代码仓库中。Issue 可以是一条真实 bad case，不必达到生产事故的严重程度，但 Issue
正文自身不是可执行指令，也不天然等于验收真相。

维护者需要把这些信息连接起来：

```text
用户实际遇到了什么
→ 当时运行的是哪组 AI 系统组件
→ 期望行为是什么、由谁确认
→ 哪个候选修改改变了什么
→ 原问题是否修好、邻近能力是否回归
→ 如果需要部署，实际运行版本和外部结果是否匹配
```

CaseLoop 的作用是保存这条关系以及每一步的状态和证据，而不是替代 GitHub、CI/CD、
Agent runtime、可观测平台、CMDB、ITSM 或部署平台。

## 4. V5 产品形态

V5 包含三个相互连接的产品表面：

| 产品表面 | 主要使用者 | 作用 |
|---|---|---|
| Human Console | 应用负责人、Maintainer、Reviewer、Approver | 查看 Case、比较候选、审批、干预、复盘和审计 |
| Agent Capability Gateway | 外部 Agent、CI、客户应用 | 报告问题、提交候选、请求评测、请求动作和读取证据 |
| Governance Kernel | 确定性 Controller/Executor | 持有生命周期、权限、幂等、Gate、审批、执行、对账和恢复事实 |

HTTP 是 canonical capability baseline。Console、CLI、MCP、A2A 和 SDK 建立在同一组
canonical intents 和 PostgreSQL 权威状态之上。

V4 S1A 的现有接口位于 `/api/v1`。V5 系统资源目标位于显式 `/api/v2`，同时保留
`/api/v1` 的兼容 façade。

首个低摩擦 CLI workflow 是：

```text
caseloop init <repo>
caseloop case from-issue <github-url>
```

`init` 读取本地仓库，发现代码 revision、Prompt、模型和 RAG 配置，生成 manifest 草稿，
经人工确认后形成应用、环境、组件和 VersionSet。

`case from-issue` 读取 GitHub Issue snapshot，并组合 Signal、Case、application binding 和
acceptance-criteria draft。

## 5. V5 核心对象

应用与版本层级：

```text
Workspace
→ Project
→ AIApplication
→ Environment
→ SystemComponent
→ ComponentRevision / TopologyRevision
→ SystemVersionSet
→ SystemAssignment
```

`SystemVersionSet` 是不可变的运行组合快照，绑定组件 revision/digest、依赖拓扑、构建
来源、环境和 manifest digest。EvaluationBundle 是独立治理工件，不属于运行时
SystemVersionSet。无法固定的远程依赖以较低 identity assurance 或 `UNKNOWN` 表达。

一次用户体验或业务过程由 `SystemEpisodeView` 汇总多个 trace、span、AgentRun、tool call
和外部作用。进入 Gate 或归因的证据会被封存为不可变 `SystemEpisodeSnapshot`。

系统事实分成三类：

- Desired：应该运行什么；
- Observed：实际加载和调用了什么；
- Effect：已经对外部人或系统产生了什么作用。

质量与变更治理对象包括：

```text
Signal
QualityCase
CaseReadiness
AcceptanceCriteriaRevision
ResolutionContract
ProblemRecord
RegressionAsset
SystemCandidateRevision
EvaluationBundle
EvaluationPlan
GateExecution / GateReport
ReleasePlan
SystemWorkOrder
ApprovalGrant
CapabilityLease
SystemExternalOperation
ObservedStateSnapshot
ExternalEffectReceipt
```

`NEEDS_ACCEPTANCE_CRITERIA` 是 Case 的 readiness 投影，不是新的 Case 生命周期。Agent
可以提出验收标准草稿；确认后的 `AcceptanceCriteriaRevision` 属于
`ResolutionContract` 下的不可变从属工件。

## 6. V5 核心闭环

候选验证路径：

```text
Signal
→ QualityCase
→ confirmed AcceptanceCriteria / executable badcase
→ SystemVersionSet / EpisodeSnapshot / Evidence
→ optional Attribution
→ SystemCandidateRevision
→ Candidate Verification Gate
→ VerifiedCandidate / RegressionAsset / NOT DEPLOYED
```

当目标是可部署服务并且需要发布时，流程继续为：

```text
SystemCandidateRevision
→ sealed ReleasePlan
→ Release Authorization Gate
→ SystemWorkOrder
→ human ApprovalGrant
→ CapabilityLease
→ scoped SystemExternalOperation
→ desired assignment
→ independent observed verification
→ post-release Gate
→ stable / partial / UNKNOWN / rollback / compensate
→ Closure / RegressionAsset
```

普通 library 或 offline 项目可以在 `VerifiedCandidate / NOT DEPLOYED` 结束。具有真实部署
面的 AI 应用服务才进入 release、observed verification 和 rollback。

## 7. Evaluation 与 Gate

V5 区分两种评测目的：

- `CANDIDATE_VERIFICATION`：判断候选是否解决问题；PASS 产生
  `VerifiedCandidate`，不产生 WorkOrder；
- `RELEASE_AUTHORIZATION`：判断候选是否具备进入发布动作的证据；它额外绑定评测前
  封存的 `ReleasePlan`，其 PASS 才能产生 WorkOrder。

每份 EvaluationPlan 绑定：

```text
ResolutionContract
+ confirmed AcceptanceCriteria
+ base SystemVersionSet
+ target SystemVersionSet
+ SystemCandidateRevision
+ EvaluationBundle
+ workload profile
+ deployment profile
```

首批公共评测维度包括：

- badcase resolution；
- regression；
- deterministic contract/test；
- repo-sandbox integrity；
- evidence completeness。

现实有效性包含：

- 同一 reproducer 在 base 上失败、在 Candidate 上通过；
- 仓库既有测试、blast-radius 邻近能力和历史 RegressionAsset；
- sealed holdout、mutation/adversarial probe 和 anti-overfit；
- repo-sandbox 与真实 provider、RAG、tool、permission、observed runtime 证据的边界分离；
- 用已知无效 Candidate 检查 false-pass；
- 用真实已接受修复检查 false-block；
- Gate 结论与维护者接受、拒绝、reopen 和 recurrence 结果对照。

首批 workload profile 为：

- `CODE_CHANGE`：以确定性 fail-to-pass、仓库测试、回归和 holdout 为主；确定性判断充分时
  Judge 为 `N/A`；
- `AI_BEHAVIOR_CHANGE`：使用 base/candidate 配对重复、失败分布、effect/interval 和
  unaffected controls；LLM Judge 只有经过维护者标注样本校准后才成为 required evidence，
  否则作为 advisory。

非确定性评测的 repetitions、阈值和最小有意义差异来自对应 workload 的基线波动、风险
和预算，不使用统一全局参数。GateReport 分开表达运行状态、Gate verdict、证据完整度、
各维度结果和归因结论，不把所有内容压成一个总分。

## 8. 比赛黄金纵切

比赛主纵切选择一个可以在本地运行和切换版本的真实开源 AI 应用服务 Issue：

```text
真实 GitHub Issue，只读
→ 外部 Coding Agent 调用 CaseLoop CLI
→ 仓库 manifest 自动发现和人工确认
→ QualityCase 与 AIApplication 绑定
→ confirmed AcceptanceCriteria
→ base fail 的本地复现
→ 单一主要变更面 Candidate
→ sealed ReleasePlan
→ fail-to-pass + regression + holdout + anti-overfit + repo-sandbox
→ RELEASE_AUTHORIZATION Gate
→ SystemWorkOrder
→ Case detail Console 人工审批
→ local target adapter + shadow assignment
→ 从目标 runtime 独立回读 observed digest
→ post-release Gate
→ rollback drill
→ evidence manifest
```

普通代码库或 library Issue 可以作为验证对照，同样完成 badcase、base fail、target pass、
regression、holdout 和 Candidate verification，但终点是：

```text
VerifiedCandidate / NOT DEPLOYED
```

比赛主纵切使用一个 workspace、一个 application、一个 environment、一个主要变更面和
一个 Case detail Console。完整 V5 的多应用、多组件图、持续 Evidence Source、完整
Console、MCP/A2A/SDK、供应链、Incident/SLO、成本和 vendor 治理属于后续产品范围。

## 9. 当前实现结构

当前参考实现主要由以下目录组成：

| 目录 | 内容 |
|---|---|
| `control-plane/` | Python 3.11、FastAPI、SQLAlchemy、Alembic、PostgreSQL 权威控制面 |
| `cli/` | 当前 HTTP/CLI 客户端入口 |
| `contracts/` | v3/v4 已有合同、V5 draft contracts 和 conformance tests |
| `eval-harness/` | Python 评测、B1 实验、Gate 和 replay 工具 |
| `console/` | React 控制台 |
| `mcp-servers/` | 当前 MCP 工具面与兼容测试 |
| `demo-app/` | 小智客服 FastAPI/RAG 参考工作负载 |
| `deploy/` | PostgreSQL、demo-app 等本地 Compose 配置 |
| `evidence/` | 已完成阶段与历史 replay 的证据包 |
| `docs/`、`wiki/` | 产品、架构、计划、决策、状态和使用说明 |

PostgreSQL 是生命周期、权限、幂等、审批、发布决定和审计的权威状态库。Agent 和 LLM
在系统中产出建议、假设和候选工件；确定性 Controller/Executor 记录 Gate、授权、外部
动作与最终状态。

## 10. 当前阶段位置

| V5 阶段 | 当前状态 |
|---|---|
| V5-0A：产品边界与 supersession ADR | 已完成 |
| V5-0B：领域对象、owner、生命周期和 draft contracts | 对齐中 |
| V5-0C：兼容性和 first wire slice | 对齐中 |
| V5-1 及之后 runtime | 尚未开始 |

当前正在对齐的内容是：

```text
contracts/v5
+ intent registry
+ first-case fixtures
+ acceptance provenance / confirmation
+ CaseReadiness
+ workload / deployment applicability
+ verification-only Gate
+ release-authorization Gate
+ CLI onboarding workflow
```

这轮合同和 fixture 对齐之后的位置，是新的独立一致性复核；再往后才是 migration、route
和 runtime 阶段。

## 11. 当前工作区快照

- 当前分支：`codex/v4-foundation`；
- 当前 HEAD：`850310c`，V4 Stage 1A 实现主体为 `22c23f8`；
- 当前没有 staged files；
- V5 PRD、plan、ADR、progressive blueprint、draft contracts 和 conformance test 位于
  working tree，尚未形成 V5 implementation commit；
- working tree 同时保留此前独立的 Judge/provider 与 live-B1 WIP，涉及
  `eval-harness/`、`scripts/run_b1_live.py` 和 `scripts/b1_live/`；
- 先前 Compose 配置检查曾把 provider/Feishu/internal credential 展开到私有工具日志，
  因而下一次 live/provider run 的当前状态包含 credential rotation pending；本地 offline
  施工不受该状态影响。

## 12. 主要上下文文档

| 文档 | 内容 |
|---|---|
| `docs/product-principles.md` | 产品定位、范围与已确认 V5 方向 |
| `docs/decisions/D-013-v5-ai-system-governance-and-agent-native-control-plane.md` | V5 产品边界与版本接管决策 |
| `docs/prd-v5.md` | V5 产品需求、用户路径、评测和比赛纵切 |
| `docs/plan-v5.md` | V5 架构、资源关系、阶段与开工位置 |
| `docs/plans/v5-progressive-delivery.md` | V5 文件级渐进交付蓝图 |
| `contracts/v5/` | 当前不可路由的 V5 draft contracts |
| `PLANS.md` | 当前阶段台账 |
| `docs/context/PROJECT_STATE.md` | 已实现事实、证据和未完成项 |
| `docs/context/LAST_HANDOFF.md` | 最近 V5/V4 交接快照 |
| `AGENTS.md` | 当前仓库施工基线与运行规则 |
