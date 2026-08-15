# AgentMED 项目信息说明书（终态产品版）

> 产品定位、系统形态、能力、架构、场景与运营信息
>
> 文档口径：项目完整建成后的产品形态
> 适用：产品介绍、演示材料、合作沟通与内部对齐
>
> **当前状态边界**：本文是 pre-production 终态说明，不是当前实现或 live 证明。
> V5-1A/B/C 仍为 `IN_PROGRESS`，V5-2+ 为 `NOT_IMPLEMENTED`；provider/live/production facets
> 全部保持 `NOT_RUN`。当前事实见 [`PROJECT_STATE.md`](../context/PROJECT_STATE.md)，
> 文档权威顺序见 [`docs/README.md`](../README.md)。

> 配套资料：[《AgentMED 完整产品叙事》](./AgentMED-完整产品叙事文档.md)

---

## 0. 文档说明

本文是 AgentMED 完整产品的项目级信息说明书，用于说明项目最终解决什么问题、服务哪些用户、以什么形态交付、具备哪些能力、如何工作，以及如何与现有 AI 工程生态协作。

本文仅描述项目完整建成后的产品形态、产品语义和用户场景。

## 1. 项目基本信息

| 项目项 | 内容 |
|---|---|
| 项目名称 | AgentMED |
| 产品类别 | 面向 AI 应用的 Agent-native 治理运营控制面 |
| 顶层治理对象 | 完整 `AIApplication`；Agent 是 `SystemComponent` 之一 |
| 核心目标用户 | 运行一个或少量真实 AI 应用、没有完整平台治理团队的产品/工程团队 |
| 核心使用者 | 兼任 AI 应用维护负责人的工程负责人 |
| 核心任务 | 从坏结果出发，确认问题、固定版本、验证候选、控制发布并完成观察与恢复 |
| 产品表面 | Human Console、Agent Capability Gateway、Deterministic Governance Kernel |
| Canonical 接口 | HTTP；CLI、MCP、A2A、SDK 和 Console 复用相同 intents |
| 权威状态 | PostgreSQL 控制面 |
| 交付方式 | 开源、自托管与私有部署优先；核心协议 provider-neutral |
| 主要接入 | Repo、Agent、CI、Eval、Observability、Runtime、Deploy、IAM 与协作平台 |
| 核心闭环 | Signal → Case → Version/Evidence → Candidate → 双 Gate → Approval → Observe/Recover |
| 最终运营范围 | 单次问题治理、持续质量运营、供应链/成本治理与企业生态集成 |

### 1.1 一句话定位

AgentMED 把 AI 应用的一次坏结果，变成可复现的 Case、可验证的修复，以及在需要部署时经人工授权、可观察、可对账、可恢复的系统变更。

### 1.2 产品使命

让 Agent 的生产力能够进入真实软件系统，同时不失去对事实、权限、风险和最终责任的控制。

### 1.3 产品口号

> 让每一次 AI 变更，都有案可查、有证可验、有权可控、有错可退。

## 2. 问题定义

### 2.1 AI 应用的行为由完整系统共同决定

真实 AI 应用通常包含应用代码、Agent、模型绑定、Prompt、RAG、数据与索引、Retriever、Skill、MCP Server、Tool Schema、Policy、Memory Policy、Runtime Profile、连接器和部署环境。任何一层变化都可能改变最终行为、权限边界或证据解释。

因此，只记录一个模型名、一个 Prompt 版本或一个 AgentRun，无法回答“问题发生时整套系统到底运行了什么”。

### 2.2 事故真相分散在多个平台

用户反馈、Issue、trace、日志、评测、仓库、配置、审批、部署和外部副作用分别保存在不同系统里。每个平台保留局部事实，却没有一个系统共同回答：

1. 正确行为由谁确认；
2. 出错时运行的确切系统版本是什么；
3. 某个 exact Candidate 是否真正解决原问题；
4. Candidate 是否有资格进入发布；
5. 获批版本是否真的被目标 Runtime 加载；
6. 外部作用是否完成、部分完成或未知；
7. 失败后如何停止、对账、恢复和补偿。

### 2.3 核心用户 Job-to-be-Done

> 当一个 AI 应用出现坏结果时，帮助维护者快速确认“发生了什么、当时运行了什么、正确行为是什么、哪个候选真正修复了问题”，并安全决定是停在 Verified Candidate，还是进入人工授权、独立观察和可恢复的发布。

## 3. 目标用户与角色

### 3.1 核心目标用户画像

核心目标用户通常具有以下特征：

- 运行一个或少量真实 AI 应用；
- 团队规模较小，没有专职平台治理团队；
- 工程负责人同时维护 Prompt、代码、模型、RAG、工具和 Runtime；
- 必须对最终用户体验、变更风险和恢复结果负责；
- 希望使用现有 Coding Agent、CI 和工程工具，而不是被迫更换整套技术栈；
- 需要中文、自托管、私有化、数据驻留和受限网络支持，同时希望核心协议保持 provider-neutral。

### 3.2 人类角色

| 角色 | 主要责任 | 权力边界 |
|---|---|---|
| Application Owner | 定义应用边界、owner、criticality、SLO 与最终责任 | 不能用 owner 身份替代具体审批 |
| Integrator | 安装、自托管、连接 source/repo/runtime/deploy，设置数据和 secret 边界 | 不能自行宣布 Gate 或 Release 成功 |
| Maintainer | 处理 Case、补证、审查归因与 Candidate | 可以确认技术验收，但不能绕过发布 Gate |
| Domain Reviewer | 确认业务语义、金标准和不可机器验证结果 | 确认验收标准，不直接获得执行权 |
| Release Approver | 批准确切 WorkOrder、target、nonce、expiry 和风险 | 只能批准绑定的具体工单 |
| Reliability/Security Reviewer | 处理事故、权限、供应链、恢复和审计异常 | 紧急恢复仍需新的受控授权链 |

一个人可以兼任多个角色，但 principal、scope、ApprovalGrant 和 audit 必须分别记录。

### 3.3 程序化角色

| Principal | 允许能力 | 明确禁止 |
|---|---|---|
| External Agent | 提交 Signal、请求调查、提交 Candidate、请求测评或动作、读取证据 | human approval、内部 execute、自报 owner/observed/effect |
| CI / Service | 提交构建和评测事实、启动 allowlisted Gate、读取结果 | 扩权、修改 Gate Policy、生产发布 |
| Connector | 同步来源事实、回写 receipt | 决定 Case、Gate 或 Release 成功 |
| Internal Worker | 领取受限任务，提交 Finding、Proposal 和 artifact | 权威生命周期、审批、Gate 仲裁 |
| Internal Controller | 按单一 aggregate owner 记录确定性决定 | 冒充 Agent 作者或人类 authority |
| Exporter | 只读导出平台事实 | 补造 Agent 因果、审批或执行证据 |

## 4. 产品定位与边界

### 4.1 AgentMED 持有什么

AgentMED 自身必须权威持有：

- AIApplication、Environment、SystemComponent 和系统版本；
- Signal、QualityCase、Case binding、验收标准、BadcaseSpec、RegressionAsset、Problem 与 KnownError；
- Evidence receipt、Episode snapshot、coverage、missing 与 digest；
- Candidate、ResolutionContract、EvaluationPlan、GateReport；
- ReleasePlan、WorkOrder、Approval、Capability 与 ExternalOperation；
- Desired、Observed、Effect、reconcile、rollback 与 compensation。

CaseReadiness、Audit timeline、Console view、SystemReleaseView 与 A2A Task 由上述权威事实派生，是只读投影，不自行拥有领域成功。

### 4.2 AgentMED 不替代什么

AgentMED 不建设完整的：

- 通用 CMDB；
- trace/observability 存储；
- Agent runtime；
- CI/CD 与部署平台；
- ITSM、IAM、FinOps 或 GRC；
- 云账单、供应商市场或法律合规判断引擎。

它通过 Adapter 对接这些系统，并要求这些系统返回可验证 receipt。外部平台可以提供事实或执行动作，但不能拥有 AgentMED 的领域成功。

## 5. 最终产品形态

### 5.1 Human Console

Human Console 用于人类查看、比较、确认、审批、干预、复盘和审计。终态信息架构包括：

1. **Applications**：应用、owner、criticality、环境、开放 Case、drift 与风险；
2. **System Graph**：组件、依赖、版本 diff、desired/observed；
3. **Case Workspace**：Signal、验收标准、Episode、证据、归因、Candidate 与 Gate；
4. **Change Center**：ReleasePlan、WorkOrder、Approval、Operation、rollback 与 compensation；
5. **Evidence & Audit**：receipt、完整性、timeline 和可导出证据包。

审批视图必须展示 exact hash、target、risk、nonce、expiry 和 change/evidence diff。

### 5.2 Agent Capability Gateway

Gateway 让外部 Agent、CI 与客户应用通过标准能力接口：

- 报告问题；
- 请求调查；
- 提交验收标准草稿；
- 提交 Candidate；
- 启动 Evaluation 或 Release 请求；
- 读取 Operation、Gate、Case 与 Evidence。

Agent Gateway 提供一等调用权，不提供审批主权或内部执行能力。

### 5.3 Deterministic Governance Kernel

Governance Kernel 由确定性的 Controller 和 Executor 构成，负责：

- 生命周期与 aggregate ownership；
- workspace、身份、scope 与资源可见性；
- 幂等、并发、lease、fencing、retry 与 cancel；
- immutable binding、digest 与 AuthorityReceipt；
- Gate 仲裁、WorkOrder 创建与 Approval 绑定；
- scoped execution、audit、outbox、reconcile 与 recovery。

PostgreSQL 是这套事实的唯一权威来源。

## 6. 核心治理对象

### 6.1 应用层级

```text
Workspace → Project → AIApplication → Environment
                                   ├→ SystemComponent[]
                                   ├→ DependencyEdge[]
                                   ├→ TopologyRevision[]
                                   ├→ SystemVersionSet[]
                                   └→ SystemAssignment
```

### 6.2 组件类型

完整产品支持：

`APPLICATION_CODE`、`AGENT`、`MODEL_BINDING`、`PROMPT`、`DATASET`、`INDEX`、`EMBEDDING`、`RETRIEVER`、`SKILL`、`MCP_SERVER`、`TOOL_SCHEMA`、`POLICY`、`MEMORY_POLICY`、`RUNTIME_PROFILE`、`CONNECTOR`。

首次接入不要求小团队填满所有组件，只记录实际 workload 中能够可靠确认的对象。无法确认的字段必须保留 assurance 或 `UNKNOWN`。

### 6.3 版本身份保证

每个 component binding 使用以下 assurance：

| Assurance | 含义 |
|---|---|
| `IMMUTABLE_DIGEST` | 能由不可变 digest 精确绑定 |
| `PROVIDER_VERSION` | 由 provider 的稳定版本身份绑定 |
| `MUTABLE_ALIAS` | 远程别名可变化，不能声称 exact reproducibility |
| `OBSERVED_ONLY` | 只能从真实运行时观察 |
| `UNKNOWN` | 无法可靠确定 |

`SystemVersionSet` 可以包含低 assurance 组件，但 Gate、Release Policy 和 Console 必须显示它们。

### 6.4 三类系统事实

| 事实 | 回答的问题 | 典型资源 |
|---|---|---|
| Desired | 系统应该运行什么？ | SystemAssignment、release target |
| Observed | 实际加载、解析或调用了什么？ | ObservedStateSnapshot、runtime receipt |
| Effect | 已经对用户或外部系统做了什么？ | ExternalEffectReceipt、reconcile receipt |

三类事实不能互相推断成功。

## 7. 核心能力

### 能力一：发现并登记完整 AI 应用

本地只读发现仓库 revision、项目类型、测试命令，以及可识别的代码、Prompt、模型、Retriever 和 Index，生成 manifest 草稿；由人确认后再进入权威导入。

### 能力二：固定不可变系统版本

记录 ComponentRevision、TopologyRevision 与 SystemVersionSet，使用 digest 固定组件组合和依赖关系；环境 Assignment 只表示 desired，不改写 VersionSet。

### 能力三：把模糊反馈变成可信 Case

将 Issue、用户反馈或异常转成稳定 Signal/Case，保留来源，绑定应用和环境。信息不足时显示 `NEEDS_ACCEPTANCE_CRITERIA`，而不是让 Agent 制造验收真相。

### 能力四：构建可绑定证据

把 trace、AgentRun、tool call、日志和外部作用组织为 provenance-preserving Episode View，并在 Gate 前 seal 为不可变 Episode Snapshot，报告 coverage、missing 和 digest。

### 能力五：支持归因，也允许 abstain

通过 exact VersionSet replay、known-good control、受控组件替换、配对实验、effect 与 interval 提供归因。证据不足时输出 `INCONCLUSIVE`、`CONFOUNDED` 或 `INSUFFICIENT_EVIDENCE`。

### 能力六：验证真实修复

同一 badcase 必须 base fail、Candidate pass；同时验证仓库回归、历史 Case、邻近影响、sealed holdout、anti-overfit 和 repo-sandbox integrity。

### 能力七：分离候选验证与发布授权

`CANDIDATE_VERIFICATION` PASS 只产生 VerifiedCandidate/NOT DEPLOYED；只有绑定 pre-sealed ReleasePlan 的 `RELEASE_AUTHORIZATION` PASS 才能创建 WorkOrder。

### 能力八：控制高风险授权

WorkOrder 精确绑定 Candidate、GateReport、ReleasePlan、base/target、assignment generation、nonce 和 expiry。高风险动作必须由重新认证的人类批准。

### 能力九：独立观察、对账和恢复

执行后从真实 Runtime 独立回读 observed digest。出现 mismatch、partial 或 UNKNOWN 时进入 reconcile、rollback 或 compensation，不自动宣告成功。

### 能力十：把事故沉淀为回归资产

经显式关闭与审核后的真实 badcase 可形成 RegressionAsset，在后续相关 workload 的 Gate 中实际执行，构成持续改进闭环。

## 8. 三条端到端路径

### 8.1 First Useful Case

```text
Signal / read-only Issue snapshot
→ QualityCase
→ Application + Environment binding
→ AcceptanceCriteria draft
→ human confirmation 或 NEEDS_ACCEPTANCE_CRITERIA
→ SystemVersionSet / evidence gap
→ NextRequiredAction
```

该路径的成功标准是形成可信可执行问题，或明确指出缺少什么，不要求立即产生 Candidate。

### 8.2 Verification-only 路径

```text
confirmed badcase + base VersionSet
→ immutable Candidate
→ Candidate Verification
→ base fail / Candidate pass
→ regression + holdout + anti-overfit + sandbox
→ VerifiedCandidate
→ NOT DEPLOYED
→ explicit Case closure
→ reviewed RegressionAsset
```

适用于 library、offline 或不存在真实部署面的 workload。

### 8.3 Deployed-service 路径

```text
SystemCandidateRevision + explicit release request
→ frozen ReleasePlan
→ RELEASE_AUTHORIZATION Gate
→ SystemWorkOrder
→ re-authenticated human Approval
→ CapabilityLease
→ scoped SystemExternalOperation
→ Desired Assignment
→ independent Observed verification
→ post-release Gate
→ stable / partial / UNKNOWN
→ reconcile / rollback / compensate
→ explicit Closure
```

发布、通知、A2A Task 或 Adapter response 的完成都不会自动关闭 Case。

## 9. 逻辑架构

```text
Human Console                External Agent / CI / Client
      │                                  │
      └──────────── HTTP / CLI / MCP / A2A / SDK ────────────┐
                                                             ▼
                         Public Gateway
          Auth · Workspace · Scope · Idempotency · Audit
                                                             ▼
                  Canonical Application Intents
                                                             ▼
             Deterministic Governance Kernel (PG)
 Application │ Version │ Case │ Evidence │ Work │ Candidate
 Evaluation/Gate │ Approval │ Executor │ Recovery │ Read Models
                                                             ▼
 Evidence / Repo / Runtime / Eval / Deploy / IAM Adapters
```

### 9.1 Controller ownership

| Controller | 权威职责 |
|---|---|
| Application Catalog | AIApplication、Environment、Component、DependencyEdge |
| Version Controller | ComponentRevision、TopologyRevision、VersionSet、desired Assignment |
| Signal/Case Controller | Signal、QualityCase、binding、Acceptance Criteria、BadcaseSpec、RegressionAsset |
| Evidence Controller | Evidence receipt、coverage、Episode Snapshot、Observed/Effect |
| Work Controller | durable task、attempt、lease、fence、retry、reconcile |
| Proposal/Candidate Controller | ResolutionContract、Candidate、ReleasePlan |
| Evaluation Registry | EvaluationBundle、holdout binding |
| Gate Controller | EvaluationPlan、GateExecution、GateReport、WorkOrder、RecoveryWorkOrder |
| Approval Controller | ApprovalGrant |
| Capability Controller | CapabilityLease |
| Scoped Executor | ExternalOperation、OperationExecutionReceipt |
| Read Models | CaseReadiness、Console、timeline、SystemReleaseView、A2A Task；只读，不拥有领域成功 |

## 10. 接口与 Agent-native 能力

### 10.1 接口原则

- HTTP 是 canonical baseline；
- 对外 API 使用显式 major-version 协商，完整系统治理资源位于 `/api/v2`；
- 对既有客户端保持兼容 façade，不允许 silent downgrade；
- CLI、MCP、A2A、SDK 和 Console 只映射 canonical intents；
- 同一 intent 必须返回相同资源、状态、错误、幂等和审计语义；
- SDK 只封装客户端调用，不复制领域状态机或权限判定；
- MCP tools 按 authenticated scope 过滤并逐次授权；tool annotation 不是 authority；
- A2A Task 用于异步传输和进度跟踪，Task `COMPLETED` 不表示 Gate PASS 或 Release Success；
- 客户端 timeout、Ctrl-C 或 detach 只停止等待，不取消权威 Operation；
- Transport terminal 不映射领域 terminal。

### 10.2 代表性 intents

按用户旅程可分为：

- Capability：`capabilities.get`
- Signal/Case：`signals.submit`、`cases.get`、`cases.timeline`
- Acceptance：`acceptance-criteria.propose/get/confirm`
- Application/Version：`applications.register/get`、`system-manifests.import`、`system-versions.get/diff`
- Case binding/Episode：`cases.bind-application`、`system-episodes.get`
- Investigation/Operation：`investigations.start`、`operations.get/list/cancel-request`
- Candidate/Evaluation：`candidates.submit`、`evaluations.start/get`、`gate-reports.get`
- Approval/Release：`approvals.decide`、`releases.request/rollback-request`
- Evidence：`evidence.get`

Public MCP/A2A 不暴露 internal work claim、Gate recording、human approval 或 execute authority。

## 11. 数据、证据与评测语义

每个非投影权威记录都使用统一 envelope，至少包含 `schema_version`、`workspace_id`、`revision`、`recorded_by_principal`、`recorded_at`、`immutable`、`hash_rule`、`record_digest` 和 `authority_receipt_id`。记录以 RFC 8785 JCS 与 SHA-256 生成 canonical digest 并精确绑定，业务子工件的 checksum 不能代替 authority binding。

### 11.1 Evidence facet 与 fact class

项目只使用以下 canonical evidence facets：

`contract`、`replay`、`domain-provider-live`、`agentteams-native`、`claude-runtime-live`、`agent-causal`、`repo-sandbox`、`human-authorized-external`、`production-canary`。

mock 与平台 evidence export 不是 success facet，不能替代真实 Agent、人工审批、外部动作或生产证据。
这些 facet 名称用于分类证据面，不表示 AgentTeams、Claude 或任何单一 provider 是必装依赖。

Evidence facet 回答“证据来自哪个验收面”。每份可绑定证据还必须声明它证明的 fact class：

`DECLARED_CONFIGURATION`、`OBSERVED_RUNTIME`、`EXECUTION_ATTEMPT`、`EXTERNAL_EFFECT`、`EVALUATION_RESULT`、`AUTHORITY_DECISION`。

两者正交，不得从一类事实推断另一类。例如，Adapter 的 execution receipt 只能证明执行尝试，不能证明外部 effect 或 observed runtime。

### 11.2 数据隔离与 Evidence Source

| Dataset role | 用途 | 硬边界 |
|---|---|---|
| `RUNTIME_DATA` | 真实应用运行 | 不自动成为评测或 holdout |
| `EVALUATION_DATA` | 可见的评测与调试 | 与运行数据、sealed holdout 分离 |
| `SEALED_HOLDOUT` | 独立确认与 anti-overfit | 对 Candidate producer 不可读写 |

相同 digest 跨数据角色出现时，必须进行显式 non-leakage review。Application 和 Component 记录 owner、criticality 与 data classification；Component 还记录 permission/effect classification，使 Gate 和 Approval 能按影响面决定证据和授权要求。

每个 Evidence Source 都必须定义 capability discovery、credential reference、auth/signature、cursor、去重与幂等、retry/DLQ、masking/retention 以及 `UNKNOWN` 语义。来源不可用、采样不完整或签名无法验证时，系统记录缺口，不将空结果解释为没有问题。

### 11.3 Evaluation 输出与 workload 语义

```text
RunState = SUBMITTED | WORKING | INPUT_REQUIRED | AUTH_REQUIRED | CANCEL_REQUESTED | CANCELED | REJECTED | FAILED | COMPLETED
SystemGateExecution uncertainty = UNKNOWN
GateVerdict = PASS | FAILED | INCONCLUSIVE | ERROR
NormalizedGateOutcome = PASS | FAILED | INCONCLUSIVE | ERROR | UNKNOWN
EvidenceCompleteness = COMPLETE | PARTIAL | UNKNOWN
EvaluationDimensionResults[]
AttributionVerdict
```

`UNKNOWN` 不是终态 GateReport verdict。它属于 SystemGateExecution 的不确定状态，必须先 reconcile，才能产生新的终态 GateReport。

Evaluation dimension 与 evidence facet 是两种不同维度：前者描述 badcase、regression、latency 等质量检查，后者描述证据来源与验收面。

| Workload profile | 核心判定 | Judge 语义 |
|---|---|---|
| `CODE_CHANGE` | 确定性 fail-to-pass、repo tests、历史回归、holdout/anti-overfit | 确定性标准足够时为 `N/A` |
| `AI_BEHAVIOR_CHANGE` | base/Candidate 配对重复、原始失败分布、effect/interval、历史与 unaffected controls | 只有经该领域人类样本校准且与 Candidate producer 独立时才可 required；否则只能 advisory |

非确定性评测不使用全局固定重复次数或阈值。每个 workload 根据 base 波动、最小有意义差异、风险和预算冻结 repetitions、通过阈值与区间规则，并在报告中保留原始 trials 与失败分布。

### 11.4 Fail-closed 规则

任何 required/applicable evidence 出现以下状态，都不能创建可执行 WorkOrder：

- FAILED；
- INCONCLUSIVE；
- ERROR；
- UNKNOWN；
- 缺失、重复、过期、换绑或 digest 不匹配。

Optional、advisory 和有理由的 N/A 必须单独显示，不能提升为 required PASS。

## 12. 权限、安全与恢复机制

### 12.1 权威与权限

- PostgreSQL 持有生命周期、权限、审批、发布和审计权威；
- LLM/Agent 只产生建议、假设与候选工件；
- 信任角色由服务端认证注册推导，默认拒绝未列出的 principal type 和 scope；
- 访问先校验 workspace，再校验资源可见性；无权对象返回 opaque not-found，不泄露对象是否存在；
- mutation 的幂等 key 按 authenticated principal + workspace + intent 隔离；同 key 异请求必须冲突并审计；
- Release/Executor 只消费 exact WorkOrder 和 Approval；
- Approval 绑定 target、nonce、expiry、risk 和 evidence diff；
- CapabilityLease 限定身份、目标、动作、参数与有效期；
- Audit failure 使所在业务事务失败。

### 12.2 不可信输入

Issue、trace、附件、tool output、Agent proposal 和外部消息始终是不可信数据，不能成为 system/developer instruction、验收真相或授权。

### 12.3 外部动作语义

跨系统步骤使用 at-least-once intent、provider resource identity、attempt、receipt 和 UNKNOWN reconcile；不宣称跨平台 exactly-once 或原子回退。

### 12.4 Rollback

Rollback 至少包含：

1. stop exposure；
2. restore known-good desired assignment；
3. verify observed runtime；
4. reconcile in-flight/UNKNOWN；
5. revoke capability/credential；
6. compensate irreversible effects where possible。

每次 rollback 使用新的 break-glass ReleasePlan、RecoveryWorkOrder、重新认证的 emergency Approval、单次 CapabilityLease 与 `SYSTEM_ROLLBACK` ExternalOperation。原发布 WorkOrder、Approval、nonce 或 capability 不得复用，也不存在 direct restore/resume 旁路。

## 13. 与相邻产品的关系

| 相邻能力 | 它主要回答什么 | AgentMED 的职责 |
|---|---|---|
| Langfuse / OTel / Phoenix | 发生了什么，有哪些 trace 与观测 | 固化来源证据，绑定进 Case/Gate，显示完整性与缺失 |
| Coding Agent / 优化器 | 可以怎么改 | 接收 Candidate，并独立验证 exact Candidate |
| Eval 框架 | 在某套数据和 evaluator 上表现怎样 | 冻结真实问题、版本、purpose 和硬失败语义 |
| Agent Runtime | 怎样执行 Agent 与工具 | 作为可替换 Adapter，不拥有治理权威 |
| CI/CD / Deploy | 怎样构建和部署 | 消费获批操作并返回 receipt；AgentMED 负责授权、观察和恢复 |
| CMDB / ITSM / IAM | 资产、工单、身份与通用流程 | 通过 Adapter 互操作；AgentMED 持有 AI 特有治理事实 |

AgentMED 的差异来自职责边界和可验证治理交易，不来自“市场没人做”或“替代所有工具”。

## 14. 完整产品场景

### 14.1 应用接入与系统建模

团队以自托管或私有化实例建立 Workspace，接入代码仓库、模型与 RAG 配置、可观测来源、Runtime、部署平台和身份系统。`agentmed init` 从仓库只读生成 manifest 草稿，由负责人确认应用、环境、组件、依赖和责任边界。对无法精确固定的依赖，系统保留 assurance 与 `UNKNOWN`，不伪造完整性。

接入后，每个环境都有可比较的 SystemVersionSet、desired Assignment 和完整审计时间线。有真实部署面的环境还必须保留独立 observed state；对 library 或 offline workload，Observed 明确为 `N/A` 或 `UNKNOWN`，不伪造运行证明。小团队可以先登记真实 workload 所需的少量组件，再随使用深度增量完善，无需先完成一次大型资产盘点。

### 14.2 从用户反馈到 First Useful Case

用户反馈、Issue、trace 异常、SLO 事件或外部平台消息进入 Signal Inbox。AgentMED 保留来源快照，建立 QualityCase，并关联受影响应用、环境、问题发生时的 SystemVersionSet 与 Episode evidence。

Agent 根据来源起草验收标准，Maintainer 或 Domain Reviewer 确认“什么才算修好”。信息足够时，Case 形成可执行 badcase；信息不足时，系统给出 `NEEDS_ACCEPTANCE_CRITERIA` 和具体缺口。两种结果都会告诉用户下一项可执行动作。

### 14.3 从 Agent Candidate 到 VerifiedCandidate

外部 Coding Agent、内部 Worker 或 Maintainer 在受限证据上开展调查，提交原因假设和不可变 Candidate。AgentMED 冻结 ResolutionContract、base/target VersionSet、EvaluationBundle、blast radius 和恢复要求。

Candidate Verification 在同一 badcase 上要求 base fail、Candidate pass，并执行仓库回归、历史 Case、邻近能力、sealed holdout、anti-overfit 和 sandbox integrity。必需证据缺失、未知或不一致时，Gate 拒绝放行并返回 Next Required Action。通过后系统生成 VerifiedCandidate，但不自动获得部署权。

### 14.4 在线服务的受控发布与恢复

对有真实部署面的应用，Maintainer 显式提出 release request，并在 Gate 前冻结 ReleasePlan、rollout、observed verification、known-good rollback 和 recovery 参数。Release Authorization Gate 通过后创建 exact WorkOrder，由重新认证的人类审批，再交给限时、限目标、限动作的 CapabilityLease 和 Executor。

执行结束后，AgentMED 从目标 Runtime 独立回读 observed state，对照 desired、observed 与 effect。不一致、局部成功或超时进入 reconcile；需要恢复时，系统通过新的 RecoveryWorkOrder 和紧急人工授权执行 stop exposure、restore known-good、observed verification、capability revoke 与可能的 compensation。

### 14.5 持续质量与运营治理

经显式关闭与审核后的真实 badcase 可沉淀为 RegressionAsset，并在后续相关 Gate 中真正执行。AgentMED 把 Incident、Problem、KnownError、SLO、Error Budget、版本漂移、供应链变化、Memory 与数据治理、成本预算与供应商退场纳入同一管理视图。

Application Owner 查看多应用风险与质量趋势，Reliability/Security Reviewer 跟踪权限扩大、未知外部作用、恢复演练和凭据撤销。低风险自动化可以通过受限 PolicyGrant 运行，高风险发布与恢复始终保留明确的人类控制点。

在运营交易中，多个 Case 可聚合为 Problem 和 KnownError，恢复与事后复盘形成 PIR（Post-Incident Review）；SLO 或 Error Budget 越界可以冻结 rollout。供应链变更会触发影响分析与 revoke，Memory/数据污染进入 lineage 定位、quarantine 和 rebuild/revert，vendor EOL 会形成可跟踪的 migration Case。

AgentMED 自身也纳入同样的运行纪律：支持 backup/restore、key rotation、N/N-1 协议与版本兼容、自身 SLO 与可观测、单节点恢复以及企业 HA/DR。

### 14.6 企业生态与合规协作

AgentMED 通过 MCP、A2A、SDK 和 Adapter 与 CMDB、ITSM、IAM、GRC、FinOps、仓库、评测、观测和部署平台互操作。多租户、跨组织信任、数据驻留、retention、legal hold、HA/DR 和受限网络支持企业部署。

系统可以生成按 Case、Gate、Release 和 Recovery 组织的证据包，为内审和合规评估提供有来源、可追溯的材料。AgentMED 提供证据和控制机制，具体法规适用性仍由组织与专业方独立判断。

## 15. 最终能力版图

### 15.1 能力分层

| 能力层 | 主要对象 | 最终产品能力 |
|---|---|---|
| AI Application Foundation | Application、Environment、Component、Topology、VersionSet、Assignment | 应用登记、仓库发现、不可变版本、系统图、desired/observed drift |
| Quality Case & Evidence | Signal、Case、Acceptance、Episode、Evidence | 来源保全、人类确认验收、可执行 badcase、证据快照、完整性与缺口 |
| Candidate & Evaluation | ResolutionContract、Candidate、EvaluationBundle、GateReport | Agent 候选接入、原因归因、fail-to-pass、回归、holdout、anti-overfit、硬失败语义 |
| Change Authority | ReleasePlan、WorkOrder、Approval、CapabilityLease | 候选验证与发布授权分离、exact binding、人类审批、限时限权执行 |
| Operations & Recovery | ExternalOperation、ObservedSnapshot、EffectReceipt、RecoveryWorkOrder | 独立观察、UNKNOWN 对账、rollback、compensation、凭据撤销与审计 |
| Continuous Operations | RegressionAsset、Incident、Problem、KnownError、SLO | 回归资产复用、事故管理、误差预算、供应链、Memory/Data 和成本治理 |
| Enterprise & Ecosystem | Tenant、Adapter、PolicyGrant、Evidence Package | 多租户、MCP/A2A/SDK、CMDB/ITSM/IAM/GRC/FinOps 集成、HA/DR、retention、legal hold |

### 15.2 交付与部署形态

| 形态 | 适用场景 | 产品特性 |
|---|---|---|
| 本地单应用 | 个人开发者、小团队、首次接入 | CLI-first、本地仓库发现、First Useful Case、verification-only |
| 团队自托管 | 多环境应用与日常运营 | Human Console、统一 Gateway、私有 PostgreSQL、内部 Adapter |
| 企业私有化 | 数据驻留、受限网络与审计要求 | IAM、多租户、retention/legal hold、HA/DR、企业系统集成 |

### 15.3 产品使用模式

- **Verification-only**：对 library、离线 workload 或无真实部署面的对象，以 VerifiedCandidate / NOT DEPLOYED 为完整终点。
- **Guarded deployment**：对在线应用，执行独立 Release Authorization、人类审批、受限执行、Observed 验证和恢复。
- **Continuous governance**：将真实 Case、回归资产、SLO、Incident、供应链和成本信号持续纳入同一治理循环。

## 16. 成功指标

产品使用以下指标衡量用户价值、治理质量与运营可靠性：

- Signal→confirmed executable badcase 的时间与转化率；
- time-to-first-system-case；
- 首个 Case 与后续复用 Case 的人类分钟差异；
- manifest 自动发现覆盖率、UNKNOWN 数量与人工修正量；
- Signal→Application/Version/Episode 关联率；
- Desired/Observed drift 发现率与误报率；
- Evidence completeness 与 UNKNOWN 原因分布；
- attribution confirmation rate；
- verified resolution rate，以及 Gate 与维护者真实接受/拒绝结论的一致率；
- Gate false-pass 与 false-block，同时报告样本数、workload 和不确定性；
- production escape、reopen 与 recurrence；
- RegressionAsset 沉淀率与真实复用率；
- 相对原生 Issue/CI/人工拼证据流程增加或节省的净时间；
- 未经授权动作数量，目标为 0；
- rollback/reconcile/compensation 完成率；
- Agent-native 调用成功率、幂等重试率和 Operation 恢复率；
- 每个 verified Case 的时间、token、费用与人类投入。

每个指标都需定义 owner、计算公式、统计窗口、数据源、分母和不确定性，并按 workload、环境风险和证据完整性分层解读。数量增长不是成功本身；真正的成功是人类工作量下降、误放行和越权动作减少、问题更快被证明并恢复。

## 17. Anti-goals 与对外表述边界

### 17.1 Anti-goals

- 不建设适用于所有传统软件的通用治理平台；
- 不复制完整 observability、Agent runtime、CMDB、ITSM、IAM、CI/CD、FinOps 或 GRC；
- 不要求用户采用 AgentTeams、Claude Code、某个模型或 trace vendor；
- 不允许 Agent 同时提出、评价、批准和执行自己的高风险修改；
- 不用一个总分、移动标签或聊天文本证明系统已安全发布；
- 不把 contract、fixture、mock、replay 或 exporter 当成 live 证据。

### 17.2 对外表述

| 避免表述 | 准确表述 |
|---|---|
| “Agent 可以自动批准并发布修复” | “Agent 可以调查和提交 Candidate；确定性内核裁决，人类批准高风险动作。” |
| “Issue 就是验收标准” | “Issue 是不可信来源；Agent 可起草，授权人类确认验收标准。” |
| “测试通过就证明已经修好并上线” | “Candidate Verification 与 Release Authorization 是两个 exact-bound Gate。” |
| “设置目标版本就代表部署成功” | “Desired、Observed、Effect 分开；成功需要独立 observed evidence。” |
| “Rollback 可以撤销一切” | “Rollback 包含 stop、restore、observe、reconcile 和可能的 compensate。” |
| “AgentMED 替代所有观测、Agent 和部署平台” | “AgentMED 是跨平台的治理与授权层，通过 Adapter 复用现有系统。” |
| “用了这些控制就全面合规” | “AgentMED 提供证据、权限、审计和恢复机制；具体法规仍需独立评估。” |

## 18. 术语表

| 术语 | 定义 |
|---|---|
| Workspace / Project / Tenant | 租户、项目与资源隔离边界；Tenant 用于多租户部署 |
| AIApplication | 被治理的完整 AI 应用边界 |
| SystemComponent | 构成应用行为、权限或证据解释的组件 |
| SystemVersionSet | 不可变的完整系统组合快照 |
| SystemAssignment | 某环境的 desired VersionSet pointer |
| Signal | 用户反馈、Issue 或异常等质量信号 |
| QualityCase | 对问题、证据、责任和后续动作负责的案件 |
| CaseReadiness | 对 Case 是否具备可执行验收条件的只读判定 |
| NextRequiredAction | 当 Case 或 Gate 无法继续时，系统返回的下一项可执行动作 |
| AcceptanceCriteriaRevision | 经授权人类确认的不可变验收标准 |
| ResolutionContract | Gate 前冻结的问题、验收、版本和验证责任合同 |
| SystemEpisodeView | 可变化的 evidence graph/read model |
| SystemEpisodeSnapshot | Gate/归因可精确绑定的不可变证据快照 |
| Exact Binding | 以 kind、id、revision 和 digest 引用确切不可变记录 |
| AuthorityReceipt | Controller 在同一事务内合法记录权威对象的不可变证明，不是执行权限 |
| SystemCandidateRevision | exact immutable 系统级候选修改 |
| EvaluationBundle | Dataset、Label、Evaluator、Judge、Runtime、Threshold 与 Holdout 的冻结组合 |
| EvaluationPurpose | 区分 `CANDIDATE_VERIFICATION` 与 `RELEASE_AUTHORIZATION`；只有后者的 exact PASS 可创建 WorkOrder |
| RunState / GateVerdict | 评测运行进度与终态裁决；二者不由 transport terminal 推断 |
| AttributionVerdict | 对原因归因的结论，允许 attributed、inconclusive、confounded 或证据不足 |
| SystemGateExecution | Gate 的权威业务生命周期根，可进入 `UNKNOWN` 并对账 |
| SystemGateReport | 多维评测、证据完整性和 GateVerdict 的不可变结果 |
| VerifiedCandidate | Candidate verification PASS 的只读标签，不表示部署 |
| ReleasePlan | Gate 前冻结的 rollout、observation 和 recovery 计划 |
| SystemWorkOrder | Release Authorization PASS 后产生的确切工单 |
| SystemRecoveryWorkOrder | 恢复或 rollback 使用的新工单，不复用原发布授权 |
| ApprovalGrant | 对 exact WorkOrder 的人类授权 |
| CapabilityLease | 限时、限目标、限动作的执行能力 |
| SystemExternalOperation | 受控外部动作及其 attempt/receipt/reconcile |
| Operation / Attempt | 持久业务操作与单次执行尝试；重试不创造第二个业务意图 |
| PolicyGrant | 仅对低风险、受限条件授予的自动化政策，不覆盖高风险人类审批 |
| Problem / KnownError | 聚合多个相关 Case 的根问题，以及已知但尚需管理的错误模式 |
| Desired / Observed / Effect | 期望状态、实际运行状态和已发生外部作用 |
| RegressionAsset | 经显式关闭与审核的真实 Case 可沉淀为回归资产，并在后续 Gate 中实际执行 |
| UNKNOWN | 无足够证据判断成功或失败，需要补证或 reconcile |

## 19. 产品资料来源

本说明书以以下项目内部资料为产品语义来源：

- [`docs/product-principles.md`](../product-principles.md)：产品身份、用户价值、边界与核心原则；
- [`docs/prd-v5.md`](../prd-v5.md)：最终产品形态、用户旅程、能力和成功指标；
- [`docs/plan-v5.md`](../plan-v5.md)：逻辑架构、Controller ownership、可靠性与兼容策略；
- [`contracts/v5/`](../../contracts/v5/)：核心对象、intent、状态、exact binding 与失败语义。

这些文件共同解释产品“是什么、如何工作、为什么如此设计”。
