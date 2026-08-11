# CaseLoop 产品需求文档 v5

> 状态：**ACCEPTED V5 CONSTRUCTION BASELINE（2026-08-11；freeze: `8dd25ca` + `b3727d7`）/ PARTIALLY IMPLEMENTED BY STAGE**
>
> 更新：2026-08-11
>
> 上位原则：[`product-principles.md`](product-principles.md)
>
> 产品边界决策：[`D-013`](decisions/D-013-v5-ai-system-governance-and-agent-native-control-plane.md)
>
> 架构与依赖：[`plan-v5.md`](plan-v5.md)；文件级施工草案：[`plans/v5-progressive-delivery.md`](plans/v5-progressive-delivery.md)

本文是 V5 的已接受产品目标，并成为 V5 stage 施工基线的一部分。被接受的是目标与
契约；它不证明 V5 runtime、provider、Agent、
repository、human-authorized external 或 production 能力已经实现。当前可执行
事实仍以 `docs/context/PROJECT_STATE.md`、代码、migrations、测试和对应 evidence
为准。V4 Stage 1A 已实现边界保持兼容，S1B–S7 不因本文出现而自动完成；V5 runtime
按 `plans/v5-progressive-delivery.md` 依赖图逐 stage 施工。

## 1. 产品定义

CaseLoop V5 是一个**面向 AI 应用的 Agent-native 治理运营控制面**。它把 AI
应用运行中散落的用户反馈、Issue、trace、日志、评测、代码、配置、数据、工具、
发布和外部作用组织成一条可追踪、可验证、可批准、可恢复的系统治理闭环：

```text
Signal → QualityCase → confirmed AcceptanceCriteria
       → exact V5-4 ResolutionContract / executable badcase
       → SystemVersionSet / EpisodeSnapshot / evidence
       → optional Attribution → SystemCandidateRevision
       → Candidate Verification Gate → VerifiedCandidate / RegressionAsset (NOT DEPLOYED)
       └→ when deployment is requested: frozen ReleasePlan
          → Release Authorization Gate → SystemWorkOrder → human Approval
          → scoped SystemExternalOperation
          → Observe / Reconcile / Rollback / Compensate
       → Closure → RegressionAsset / Problem
```

CaseLoop 不是通用 CMDB、trace 存储、Agent runtime、CI/CD、ITSM、IAM、云账单或
合规判断引擎。它自身持有 AI 系统版本、案件、证据绑定、变更授权、执行对账和恢复
事实，并通过 Adapter 对接已有平台。

## 2. 产品形态

V5 有三个同等重要但职责分离的表面：

| 表面 | 主要使用者 | 职责 |
|---|---|---|
| Human Console | AI 应用负责人、Reviewer、Approver、SRE、安全人员 | 查看、比较、审批、干预、复盘、审计 |
| Agent Capability Gateway | 外部 Agent、CI、客户应用 | 报告、调查、提交候选、请求测评、请求动作、读取证据 |
| Governance Kernel | 确定性 Controller/Executor | 生命周期、权限、幂等、Gate、审批、执行、审计、恢复 |

HTTP 是 canonical capability baseline。V5 新系统资源使用 `/api/v2`；现有 V4 S1A
`/api/v1` 保持兼容 façade，二者调用同一治理内核而不是两套 Case 状态机。CLI、MCP、
A2A、SDK 和 Console 只调用共同 application service；同一 intent 必须返回同一资源、
状态、错误、幂等和审计语义。

## 3. 目标用户与角色

### 3.1 首发用户

首发面向运行一个或少量 AI 应用、没有完整平台治理团队的产品/工程团队。核心用户是
兼任 AI 应用维护负责人的工程负责人；其工作不是维护一个孤立 Prompt，而是对最终
用户体验、多个 AI 组件、变更风险和恢复结果负责。

### 3.2 人类角色

| 角色 | 主要任务 |
|---|---|
| Application Owner | 定义应用边界、owner、criticality、SLO 和最终责任 |
| Integrator | 安装、自托管、连接 source/repo/runtime/deploy、设置数据和 secret 边界 |
| Maintainer | 处理 Case、补证、审查归因和系统级 Candidate |
| Domain Reviewer | 判断业务语义、标注金标准和不可机器验证结果 |
| Release Approver | 批准确切 WorkOrder、目标、nonce、有效期和风险 |
| Reliability/Security Reviewer | 处理事故、权限、供应链、恢复和审计异常 |

一人可兼任多个角色，但 principal、scope、ApprovalGrant 和 audit 必须分开。

### 3.3 程序化角色

| principal | 允许的典型能力 | 明确禁止 |
|---|---|---|
| `external_agent` | submit Signal、请求调查/测评/变更、读状态和证据 | human approve、内部 execute、伪造 owner |
| `ci` / `service` | 提交构建/评测事实、启动 allowlisted Gate、读结果 | 扩权、改变 Gate policy、生产发布 |
| `connector` | 同步来源事实、回写 closure receipt | 决定 Case/Gate/Release 成功 |
| `internal_worker` | 领取受限任务、提交 Finding/Proposal/artifact | 权威状态、审批、Gate 仲裁 |
| `internal_controller` | 按单一 aggregate owner 记录确定性决定 | 冒充 Agent 作者或人类 authority |

## 4. 首个用户价值

首个价值仍是 **First Useful Case**，但 V5 的完整定义升级为：

1. 一条真实反馈或 Issue 形成稳定 `signal_id` 和 `case_id`；
2. 指向一个明确 `AIApplication` 和 `Environment`；
3. 记录验收标准来源，并形成一个维护者确认的可执行 bad case；如果输入不足，则明确
   显示 `case_readiness=NEEDS_ACCEPTANCE_CRITERIA`；
4. 展示关联的 `SystemVersionSet`、`SystemEpisodeView/Snapshot` 或证据缺口；
5. 分开显示 desired、observed、effect；
6. 给出下一项可执行治理动作，而不是伪造验收标准、归因或成功。

`NEEDS_ACCEPTANCE_CRITERIA` 是 additive Case readiness，不是新的 Case lifecycle 终态，
也不改写 S1A `QualityCase` 的不可变 payload/digest。Agent 可以从 Issue、trace 或历史 Case
提出验收标准草稿，但只有 Maintainer/Domain Reviewer 能确认期望行为；确认后的 immutable
`AcceptanceCriteriaRevision` 是 ResolutionContract owner 下的 subordinate artifact，不拥有
独立 lifecycle；它在 Gate 前被 exact `ResolutionContract` 引用。First Useful Case
允许以“已形成可信 bad case”或“已明确指出缺失的验收信息”结束，不能要求每条模糊反馈
立即进入 Candidate 或 Release。

## 5. 核心资源模型

### 5.1 应用与组件

```text
Workspace → Project → AIApplication → Environment
                                   ├→ SystemComponent[]
                                   ├→ DependencyEdge[]
                                   ├→ TopologyRevision[]
                                   ├→ SystemVersionSet[]
                                   └→ SystemAssignment
```

`SystemComponent.kind` 完整目标枚举：

```text
APPLICATION_CODE
AGENT
MODEL_BINDING
PROMPT
DATASET
INDEX
EMBEDDING
RETRIEVER
SKILL
MCP_SERVER
TOOL_SCHEMA
POLICY
MEMORY_POLICY
RUNTIME_PROFILE
CONNECTOR
```

每个组件记录 owner、source、immutable identity/digest、version assurance、data/
permission classification 和 provider metadata。远程依赖无法固定时必须标记
`MUTABLE_ALIAS` 或 `UNKNOWN`，不能声称 exact reproducibility。

首个导入 profile 只要求识别当前 workload 实际存在的组件，并优先自动发现
`APPLICATION_CODE`、`PROMPT`、`MODEL_BINDING`、`RETRIEVER/INDEX`。其余类型保留在完整
V5 模型中，但不要求小团队为了 First Useful Case 填满一张系统清单；未能可靠发现的
组件显示 assurance/`UNKNOWN`，由用户确认或后续补齐。

所有权威 V5 record/aggregate revision 使用同一 envelope：`schema_version`、`workspace_id`、
`revision`、`recorded_by_principal`、`recorded_at`、`immutable`、`hash_rule`、
`record_digest`、`authority_receipt_id`。V5 使用自己的 exact-binding kind 与
AuthorityReceipt schema-major；不能拿 V4 closed enum 假装已经证明 V5 subject。

运行数据、治理评测数据与 sealed holdout 具有不同 dataset role；SystemVersionSet 中的
runtime data 不得与 governing EvaluationBundle/holdout 混为同一权威资产。

### 5.2 `SystemVersionSet`

`DependencyEdge` 是 catalog 中的当前关系；历史运行图通过不可变
`TopologyRevision`/graph digest 进入 VersionSet，不能让可变关系改写历史。

`SystemVersionSet` 是不可变的运行组合快照，至少绑定：

- component revision/digest 列表；
- topology revision / dependency graph digest；
- build/provenance pointer；
- declared environment；
- manifest self digest。

应用代码、运行时 Policy/guardrail 和 RuntimeProfile 都按普通 component binding 进入
VersionSet，不再设置重复的顶层 version authority。治理用 EvaluationBundle 不进入
运行 VersionSet。

环境中的 `SystemAssignment` 是可变的 desired pointer，不得改写 VersionSet。
首个 assignment 使用 exact `BootstrapAttestation`、generation `1` 和
`expected_previous_generation=null`，只证明 desired。此后更新必须 CAS `previous+1` 并
由 exact SystemExternalOperation 触发，Version Controller 再校验其 WorkOrder/Approval/
ReleasePlan chain；不存在 direct WorkOrder 或 emergency restore 旁路。

### 5.3 `SystemEpisodeView` 与 `SystemEpisodeSnapshot`

`SystemEpisodeView` 是来源可追溯的 evidence graph/read model，表示一次用户可感知或
业务相关的执行过程，可关联多个 trace、span、request、AgentRun、tool call 和
external effect。它带 projection revision、`as_of`、source watermark 和 exact assignment
generation，不接受执行命令、不拥有任何领域成功。

Gate 或归因不能绑定会随新 receipt 变化的 view。Evidence Controller 必须把 exact receipt
nodes、assignment generation、watermark、coverage、missing 和 digest seal 成不可变
`SystemEpisodeSnapshot`；后续数据只能生成新 snapshot，不能改写旧证据。

### 5.4 三类系统事实

| 事实 | 回答的问题 | 典型资源 |
|---|---|---|
| Desired | 应该运行什么？ | `SystemAssignment`、release target |
| Observed | 实际加载/解析/调用了什么？ | `ObservedStateSnapshot`、runtime receipts |
| Effect | 已经对人或系统做了什么？ | `ExternalEffectReceipt`、reconcile receipt |

移动 desired pointer 不等于 observed 已切换；动作获批不等于 effect 已成功；rollback
也不能撤销已经发生的不可逆 effect。

`ExternalEffectReceipt` 可来自 CaseLoop 发起的 ExternalOperation，也可来自被治理应用
自身的 exact Episode/run；两种 origin 必须是互斥 union，不能强行把应用既有副作用
伪装成 CaseLoop 执行动作。

### 5.5 Case 与 Problem

- `Signal`、`QualityCase` 和现有 S1A 语义保持兼容；
- Case 新增 application/environment binding、impact、severity、owner 和 escalation；
- Case 通过 additive `CaseReadiness` 投影关联 immutable `AcceptanceCriteriaRevision`，至少
  记录来源（Issue、Maintainer、历史 trace/error analysis）、复现输入/环境、期望行为、
  适用 workload 和确认人；来源文本始终是不可信输入，不能自行成为金标准或授权；
- `workload_profile` 首批区分 `CODE_CHANGE` 与 `AI_BEHAVIOR_CHANGE`；另记录
  `deployment_profile=LIBRARY_OR_OFFLINE | DEPLOYED_SERVICE`，混合变更按 required 维度并集处理；
- 多个 Case 可关联 `ProblemRecord` 与 `KnownError`；
- Case resolution 不由 Gate、Release、通知或 A2A Task 的终态自动推断；
- closure 后的真实 badcase 可成为 `RegressionAsset` 或 `EvalDatasetItem`，并在后续相关
  workload 的 regression bundle 中实际执行，而不只留下一个索引记录。

### 5.6 候选、测评与变更

- `CandidateRevision` 继续表示一个确切不可变候选；
- `ResolutionContract` 在 Gate 前冻结 exact `AcceptanceCriteriaRevision`、badcase reproducer、
  workload/deployment profile、允许的测试变更和验收责任人；Issue 文本或 Agent proposal
  不能替代这份确认后的合同；
- V4 Candidate/Evaluation/Gate/WorkOrder JSON Schema 是封闭合同；V5 使用 schema-major-2
  `SystemCandidateRevision` 等严格 profile，保留相同逻辑 owner 与已有 lifecycle，而不是
  原位 subtype 或第三套生命周期；V4 缺失的人类 Approval lifecycle 由 V5 补齐；
  Candidate 可包含一个或多个组件 diff，并记录依赖
  顺序、允许变更面、风险、blast radius、验证和恢复要求；
- `EvaluationBundle` 绑定 Dataset/Label/Evaluator/Judge/Runtime/Threshold/Holdout；
- `ReleasePlan` 在 Gate 前冻结 rollout、observed verification、known-good rollback 和
  recovery 参数，不含 WorkOrder、Approval、nonce 或 expiry；
- `EvaluationPurpose=CANDIDATE_VERIFICATION | RELEASE_AUTHORIZATION`。两者都精确绑定
  `(ResolutionContract, base SystemVersionSet, target SystemVersionSet,
  SystemCandidateRevision, EvaluationBundle)`；只有 `RELEASE_AUTHORIZATION` 额外 exact
  绑定 pre-Gate `ReleasePlan`，且只有该目的的 PASS 才允许创建 `SystemWorkOrder`；
  `CANDIDATE_VERIFICATION` PASS 只产生 `VerifiedCandidate`/回归资产候选，不表示已部署；
  治理用 EvaluationBundle 不属于运行时 SystemVersionSet，避免测试集更新制造应用版本或
  污染被测对象/裁判独立性；
- `GateReport` 分开报告 RunState、GateVerdict、EvidenceCompleteness、
  EvaluationDimensionResults 与
  AttributionVerdict；
- exact `RELEASE_AUTHORIZATION` Gate PASS 后才创建 `SystemWorkOrder`，它再绑定 ReleasePlan、base/target、expected
  assignment generation、nonce 和 expiry；比赛 P0 只接受重新认证的人类 ApprovalGrant，
  PolicyGrant 后置；真正的执行 authority 是 exact WorkOrder、Approval、CapabilityLease、
  每个 `SystemExternalOperation` 及目标 Controller receipt。`SystemReleaseView`
  只汇总 target、步骤、observed verification、partial/UNKNOWN、rollback 和
  compensation，不接受写命令。

`VerifiedCandidate` 是精确绑定 Candidate 与 verification GateReport 的只读 projection/
展示标签，不拥有命令或新的 lifecycle。后续请求 release 时必须先 seal ReleasePlan，再运行
一份新的 `RELEASE_AUTHORIZATION` EvaluationPlan/Gate；不能把旧 verification PASS 换名为
发布授权。

V3 已有名为 `ChangeSet` 的 Scenario aggregate，V5 不复用该名称承载新的系统级
生命周期，也不就地改变它的 pre-Gate/审批语义。

传统 `ChangeRecord` 的 standard/normal/emergency、窗口、冲突和 PIR 元数据放在 P1；
在独立 owner ADR 前它不进入 P0 执行链，也不得成为第二个审批或发布 lifecycle。

## 6. Signal 与证据来源

比赛 P0 来源只冻结：

- Manual HTTP/CLI；
- GitHub Issue read-only snapshot 或 maintainer report。

CLI 提供两个低摩擦 workflow helper：

```text
caseloop init <repo>
caseloop case from-issue <github-url>
```

`init` 在本地只读发现 git revision、项目/运行描述和可识别的 Prompt、模型、RAG 配置，
生成 manifest 草稿并由人确认后调用 canonical `system-manifests.import`；`case from-issue`
组合现有 Signal/Case、source snapshot、application binding 与 acceptance-criteria draft
intents。它们是 CLI application workflow，不新增 aggregate owner，不让一个便利命令绕过
各 canonical intent 的授权、幂等、审计或失败语义。无法发现的字段保留 `UNKNOWN`。

GitLab、CI/eval webhook、Langfuse/OTel live、飞书/企微、Sentry、Phoenix、runtime
hooks、policy/security event、成本与 vendor deprecation 均是后续 Adapter slice。
V5-3 Core 先用 immutable receipt、repo-sandbox 与诚实 `UNKNOWN` 建立 evidence graph；
真实 trace source 不阻塞比赛闭环，也不得用 mock 冒充 live。

每个 source 必须有 capability discovery、credential reference、auth/signature、cursor、
幂等、重试/DLQ、masking/retention 和 `UNKNOWN` 语义。Issue、trace、附件和 tool output
始终是不可信数据，不能作为 system/developer instruction 或授权。

## 7. 归因

归因至少支持：

- exact SystemVersionSet 重放；
- known-good control；
- 单组件或受控组件组替换；
- paired order/randomization；
- effect size 与 interval；
- hidden confirmation/holdout；
- `ATTRIBUTED`、`INCONCLUSIVE`、`CONFOUNDED`、`NO_MEANINGFUL_EFFECT`、
  `INSUFFICIENT_EVIDENCE`、`ROLLBACK_UNAVAILABLE`。

LLM hypothesis 不是归因结论；来源缺失或多个组件同时变化时允许 abstain。

## 8. Evaluation 与 Gate

Gate 要回答的是：**这个 exact Candidate 是否解决了被确认的真实问题，并有足够证据进入
下一项动作**。形式上合法的测试、数学区间或 Judge 分数都不能单独回答这个问题。
每个 EvaluationPlan 必须 exact 绑定 ResolutionContract/AcceptanceCriteria、base/target
VersionSet、Candidate、EvaluationBundle 和本次 workload/deployment profile；
`RELEASE_AUTHORIZATION` purpose 还必须 exact 绑定在评测前 seal 的 ReleasePlan，
`CANDIDATE_VERIFICATION` purpose 明确禁止产生 WorkOrder 或 deployment claim。

系统评测按 workload 配置 required evaluation dimensions。比赛 P0 的公共 required 集合为：

- badcase resolution；
- regression；
- deterministic contract/test 与 repo-sandbox integrity；
- evidence completeness。

在这组公共维度之上，执行以下现实有效性规则：

1. **Badcase fidelity / fail-to-pass**：同一已确认 reproducer 必须在 base 上失败、在
   Candidate 上通过；若原问题无法在足够相似的环境中复现，结果是
   `INCONCLUSIVE`/`UNKNOWN`，不能把新增测试通过写成“问题已解决”。
2. **Regression composition**：至少包含仓库既有套件、与 blast radius 相邻的能力，
   以及历史 Case 形成的 RegressionAsset；覆盖面由变更影响决定，不要求所有项目盲目全跑。
3. **Anti-overfit**：sealed holdout 对 Candidate producer 不可读写；实现变更与测试变更
   分开说明；Gate 检查删改断言、硬编码公开样例、越界 diff、solution leak，并可使用
   Issue 改写版、边界变体或 mutation probe。
4. **真实环境边界**：repo-sandbox 只证明补丁可应用、指定测试通过且未逃逸。涉及真实
   model/provider、RAG、tool、permission 或 external effect 的 Case，还必须按 applicable
   profile补 replay/live/observed 证据；缺失时诚实降为 `UNKNOWN`。

首批 workload profile：

| profile | 核心判定 | Judge | release 适用性 |
|---|---|---|---|
| `CODE_CHANGE` | deterministic fail-to-pass、repo tests、历史回归、holdout/anti-overfit | 确定性标准足够时为 `N/A`，不能因未配置 Judge 变成 `ERROR` | library/offline 可在 Verified Candidate 结束；deployed service 继续 guarded release |
| `AI_BEHAVIOR_CHANGE` | base/candidate 配对重复、失败分布、effect/interval、历史与 unaffected controls | 只有经过该领域人类样本校准后才可 required；否则 advisory | 有真实部署面时要求 observed/post-release；没有部署面不得声称 release 已验证 |

非确定性评测不使用一个全局固定次数或阈值。repetitions、最小有意义差异、通过阈值和
区间规则应从该 workload 的 base 波动、风险和预算推导，并把原始 trials 与失败分布写入
报告。演示中的 16 probes、3 次重复、`delta=0.2`、Judge `0.8` 只属于对应 fixture，
不得成为 V5 的通用质量保证；小规模运行可以用于早期信号，但必须诚实报告功效不足。

LLM Judge 必须绑定 evaluator/rubric/model 版本并与 Candidate producer 独立。上岗程序是：
维护者标注样本 → per-dimension 一致性报告 → 分歧回流修订 rubric；未校准或一致性不足时
只能产生 advisory Finding。人类不能覆盖一份 required `FAILED/INCONCLUSIVE/UNKNOWN`
Gate 后直接执行发布，但可以补充验收标准、修改下一份 EvaluationPlan 或增加证据后重跑。

security/permission、external-effect safety、latency、cost 与 compatibility 在该
workload 或变更面 applicable 时才进入 required 集合；一旦被声明 required，失败语义
与其他 hard dimension 相同。首版不使用总分补偿任一硬失败。

这里的 evaluation dimension（badcase、regression、latency 等）不是 evidence facet。
证据来源/验收面仍只使用仓库既有 canonical vocabulary；Gate 同时冻结
`required_evaluation_dimensions` 与 `required_evidence_facets`，不得新增诸如
`client-live` 的混合口径。

任何 required facet 的 `FAILED`、`INCONCLUSIVE`、`ERROR`、`UNKNOWN`、缺失、重复或
换绑都不能创建可执行 WorkOrder。硬失败不能被总分平均掉；A2A Task `COMPLETED` 也
不表示 Gate `PASS`。

Gate 对用户返回分层的下一步，而不是只有一个总分：

- required hard dimension `FAILED`：拒绝当前 Candidate；
- required evidence `INCONCLUSIVE/ERROR/UNKNOWN`：不创建 WorkOrder，Case 保持开放并
  返回补证、重采样、人工确认验收标准或修复 evaluator 的 `NextRequiredAction`；
- non-applicable dimension：显式 `N/A`，不伪装为 PASS；
- optional dimension 的低 assurance 或小样本：单独风险提示，不覆盖 required 结果。

Gate 本身也必须被验证：用已知“测试通过但没有真正修好”的对抗 Candidate 测 false-pass，
用真实已合并修复测 false-block，并与维护者原始接受/拒绝结论、reopen/recurrence 对照。
比赛样本可以从少量高质量案例开始扩充，但必须报告分母、Case 类型和不确定性，不能只
展示一个成功 demo。

## 9. Change、Release 与 Recovery

Candidate verification 与发布授权是两种不同目的：

```text
SystemCandidateRevision → CANDIDATE_VERIFICATION Gate
→ VerifiedCandidate / RegressionAsset candidate / NOT DEPLOYED
```

只有 Maintainer 对一个可部署 target 显式请求发布时，才进入严格发布顺序：

```text
SystemCandidateRevision → ReleasePlan（先 seal）
→ exact RELEASE_AUTHORIZATION Gate PASS → SystemWorkOrder
→ human ApprovalGrant → CapabilityLease → scoped SystemExternalOperation[]
→ desired assignment → observed verification
→ stable / partial / UNKNOWN / rollback / compensate
```

`LIBRARY_OR_OFFLINE` workload 可以在 Verified Candidate 结束，并显式记录
`NOT DEPLOYED / release=N/A`；不得为了演示闭环伪造 shadow runtime。`DEPLOYED_SERVICE`
workload 一旦请求 release，仍必须完整经过 ReleasePlan、release-authorizing Gate、WorkOrder、
human Approval、observed verification 和适用的 post-release Gate，不能用较轻的 Candidate
verification PASS 作为执行 authority。

Executor 不创建或修改 ReleasePlan；`SystemExternalOperation(SYSTEM_RELEASE)` 成功至少要求
exact assignment generation、全部 required execution receipt、独立
`ObservedStateSnapshot=COMPLETE/MATCH`、所需 post-release Gate PASS，且没有 required
child/effect `UNKNOWN`。Adapter response 只能证明调用结果，不能单独使发布成功。

回退必须分别处理：

1. stop exposure；
2. restore known-good desired assignment；
3. verify observed runtime；
4. reconcile in-flight/UNKNOWN；
5. revoke capability/credential；
6. compensate irreversible effects where possible。

跨系统步骤不宣称原子 exactly-once。每个 external operation 使用幂等 intent、provider
resource identity、attempt、receipt 和 `UNKNOWN→reconcile`。

rollback 也必须新建 `SYSTEM_ROLLBACK` operation。每次回滚都新建绑定既有可信
known-good 证据的 break-glass `ReleasePlan` 与 `RecoveryWorkOrder`，取得重新认证的
人类 emergency approval 后才恢复；原发布计划里的 known-good 只提供预先封存的恢复
目标和证据，不能复用原 WorkOrder、Approval、nonce 或 capability。Assignment 的 freeze
是审计化 stop-exposure 动作，但 restore/resume 不能成为平行发布权威；resume 前必须
独立观察到当前 generation 已匹配 known-good，并保留事后复核义务。

## 10. Agent-native 能力面

P0 canonical intents：

```text
capabilities.get
signals.submit
cases.get
cases.timeline
acceptance-criteria.propose
acceptance-criteria.get
acceptance-criteria.confirm
applications.register
applications.get
system-manifests.import
system-versions.record
system-versions.get
system-versions.diff
cases.bind-application
case-application-bindings.get
system-episodes.get
investigations.start
operations.get
operations.list
operations.cancel-request
candidates.submit
evaluations.start
evaluations.get
gate-reports.get
approvals.decide
releases.request
releases.rollback-request
release-operations.get
evidence.get
```

V2 canonical identity 是 `(api_major, intent_name)`。CLI 默认继续调用 V1；V5 必须显式
`caseloop --api-version 2 ...`，URL/header/response major 不一致或降级一律拒绝并审计。
`system-manifests.import` 是 trusted one-shot bootstrap：它在一个本地 PostgreSQL 事务中
依次调用 Application、Environment、Component、Revision、Topology、Version、Bootstrap
Attestation 和 Assignment 的各自 Controller；任一步失败整笔回滚，不能让
`system-versions.record` 暗中制造前置资源。

`ApplicationCaseBinding` 的读取必须携带 `case_revision + case_digest`。同一 exact Case
binding 只有一个 target；相同请求幂等，不同 target 冲突，重新绑定必须先产生新的
QualityCase revision，不能用含糊的 “latest” 覆盖历史。

`acceptance-criteria.propose` 可由外部 Agent 或 Maintainer 提交不可信草稿；
`acceptance-criteria.confirm` 只允许授权的 Maintainer/Domain Reviewer，并生成新的
immutable revision，禁止原位改写。CLI `init`/`case from-issue` 只是按顺序调用这些
canonical intents 的便利 workflow；它们不拥有新的生命周期，也不把 Issue 内容提升为
系统指令、验收真相或权限。

`applications.register` 只允许 Integrator/Catalog Admin；`system-versions.record` 只允许
可信 builder/attestor。外部 Agent 可以请求调查、提交不可信 Candidate、启动独立测评
和申请动作，但不能自报 application owner、declared deployed version、observed state
或 external effect。

`releases.request` 与 `releases.rollback-request` 只创建绑定 exact WorkOrder/target 的
审批请求；Public Gateway 不直接创建或执行 `ExternalOperation`。比赛 P0 只有重新认证的
人类批准；PolicyGrant 是 P1，不能作为比赛旁路。

`approvals.decide` 只允许重新认证的人类 HTTP/CLI/Console；`releases.execute`、内部
work claim、Gate recording 和 Controller commands 不进入 Public MCP/A2A/portable
Skill。

A2A target 使用 `protocolVersion: "1.0"`，只映射稳定的异步 operation：官方
`SendMessage` 创建或继续 Task，`GetTask`、
`ListTasks`、`CancelTask` 和可选 subscribe/stream 用于观察与停止请求。V5 不沿用旧
`tasks.create` 伪方法。A2A task/context 是传输资源；Case、Gate、Change 和 Release
各自保持独立 aggregate owner。Artifact 返回结构化 CaseBrief、AttributionReport、
GateReport、ChangeProposal 和 EvidenceRef。

A2A/MCP Task `COMPLETED` 只表示委托处理完成。Gate `FAILED/INCONCLUSIVE/ERROR/UNKNOWN`
可以作为一个已完成 Task 的结构化领域结果返回；不得把 transport state 翻译成发布成功。

`operations.get/list` 必须先按 authenticated request owner 或 application reader 做资源
过滤，再计算 count/cursor；未授权返回 opaque not-found，A2A Get/List Task 继承同一
可见性。MCP Tasks 是可协商的 optional extension，baseline async tool 返回
`operation_id` 后由 `operations.get` 查询。所有 HTTP/CLI/SDK/MCP/A2A activation flag 在
draft 阶段分别为 false。

## 11. Console

目标 IA 包含五类页面：

1. Applications：owner、criticality、环境、开放 Case、drift 和风险；
2. System Graph：组件、依赖、desired/observed 和版本 diff；
3. Case Workspace：Signal、Episode、证据、归因、Candidate、Gate；
4. Change Center：Candidate、WorkOrder、审批、release、rollback、compensation；
5. Evidence & Audit：receipt、完整性、timeline 和可导出证据包。

Console 不绕过 canonical intents；审批视图必须显示 exact hash、target、risk、nonce、
expiry 和 change/evidence diff。

比赛 P0 只交付一个由真实权威数据驱动的 Case detail Console，在同一页面串起
Application/Case、Candidate/Gate/Approval 与 Release/Observed/Recovery。System Graph、
三个独立工作区、完整 Change Center 与 Evidence Audit 页面在后续增量展开。

## 12. 能力优先级

### P0 — 一个 AI 应用的可信闭环

- CLI-first onboarding：`caseloop init <repo>` 生成 manifest 草稿，
  `caseloop case from-issue <url>` 形成只读 source snapshot、Case 和验收标准草稿；
- Application/System catalog target；比赛仅需确认后的一次性 manifest import；
- SystemVersionSet；
- Signal/Case、CaseReadiness、AcceptanceCriteria/BadcaseSpec、SystemEpisodeView/Snapshot；
- `CODE_CHANGE` 与 `AI_BEHAVIOR_CHANGE` workload profile；
- desired/observed/effect；
- durable operation；
- external Candidate intake；
- Candidate verification 与 release authorization 两种 evaluation purpose；
- human approval（P0 不启用 PolicyGrant）；
- 对 `DEPLOYED_SERVICE` 的 local/shadow release；`LIBRARY_OR_OFFLINE` 可以 Verified
  Candidate/`NOT DEPLOYED` 结束；最小 target adapter 必须从真实目标进程、容器或 version
  endpoint 独立回读 loaded revision/digest，失败为 `UNKNOWN`；只读回
  `SystemAssignment` 不能算 observed verification；
- rollback/reconcile；
- HTTP/CLI；至少一个真实外部 Agent 通过 CLI 完成调用即可关闭比赛 P0，Public
  MCP/A2A runtime 在 Durable Work 后增量交付；
- Console 目标 IA；比赛仅一个 Case detail 页面。

### P1 — 持续运营

- 多 Agent/多组件 topology；
- Public MCP + 完整 A2A/SDK/webhook；
- production canary；
- Incident/Problem/KnownError、SLO/ErrorBudget；
- Skill/MCP/model/data/memory supply-chain；
- cost budget、vendor deprecation；
- PolicyGrant 低风险自治。

### P2 — 企业与生态

- 多租户；
- CMDB/ITSM/IAM/GRC/FinOps adapters；
- compliance evidence mapping；
- cross-organization trust；
- HA/DR 与 enterprise retention/legal hold。

## 13. 比赛黄金纵切

黄金纵切优先选择一个**可在本地运行和切换版本的真实开源 AI 应用服务 Issue**，使
VersionSet、AI 行为评测和 observed verification 都有实际对象：

```text
真实 AI 开源项目 GitHub Issue（只读）
→ 外部 Coding Agent 经 CLI 调用 CaseLoop
→ caseloop init/case from-issue + manifest draft/confirm
→ QualityCase/Application binding + confirmed AcceptanceCriteria
→ base fail 的本地复现与单一主要变更面 patch Candidate
→ 预先 seal ReleasePlan
→ badcase fail-to-pass + regression + holdout/anti-overfit + repo-sandbox
→ RELEASE_AUTHORIZATION Gate → SystemWorkOrder
→ Case detail Console 人工审批
→ local target adapter + shadow assignment
→ 从目标 runtime 独立回读 observed digest
→ post-release badcase/regression Gate
→ rollback drill + evidence manifest
```

可以另用普通 `CODE_CHANGE`/library Issue 作为对照：同样完成 source snapshot、confirmed
badcase、base fail/target pass、repo regression、holdout 与 Candidate verification，但以
`VerifiedCandidate / NOT DEPLOYED` 结束。它能证明 Agent 候选与 Gate 的增量价值，不能
替代 deployed-service 纵切的 release/observed/rollback 证明。若比赛最终只找到 library
Issue，必须缩小对外 claim，而不是把本地 assignment drill 称为真实服务治理。

P0 不要求上游留言、认领、fork、push 或 PR。任何外部写仍逐次授权；本地 verified
patch 必须明确 `NO REMOTE WRITE PERFORMED`。

比赛关门线不要求 generic catalog CRUD/graph、SystemEpisode public endpoint、完整
Attribution engine、通用 operations list/cancel、无外部 effect 时的
ExternalEffectReceipt、三套 Console workspace 或 A2A/MCP runtime。它们仍属于完整 V5
目标，但不能挤占首个可验证纵切。这里收窄的是比赛交付顺序，不是产品范围：多应用/
多组件版本图、持续 trace/source、完整 Console、MCP/A2A/SDK、供应链、memory/data、
Incident/SLO、成本与 vendor 治理仍按 P1/P2 继续建设。

## 14. 成功指标

- Issue/Signal→confirmed AcceptanceCriteria→V5-4 exact executable badcase 的时间、转化率和阻塞原因；
- time-to-first-system-case，以及 First Case 与后续复用 Case 的人类分钟差异；
- manifest 自动发现覆盖率、`UNKNOWN` 数量和人工修正量；
- Signal→Application/SystemVersion/Episode 关联率；
- desired/observed drift 发现率和误报率；
- evidence completeness 与 `UNKNOWN` 原因分布；
- attribution confirmation rate；
- verified resolution rate，以及 Gate 与维护者真实接受/拒绝结论的一致率；
- production escape / reopen / recurrence；
- Gate false-pass（安全目标 0）与 false-block，均报告样本数、workload 和不确定性；
- RegressionAsset 沉淀率、后续 Gate 实际复用率；
- 相对原生 GitHub/CI/人工拼证据流程增加或节省的时间；
- 未经授权动作（目标 0）；
- rollback/reconcile/compensation 成功率；
- Agent-native 调用成功率、幂等重试率和 operation 恢复率；
- 每个 verified Case 的人类分钟、token、时间和费用。

## 15. Anti-goals

- 不建设适用于所有传统软件的通用治理平台；
- 不复制完整 observability、runtime、CMDB、ITSM、IAM、CI/CD、FinOps 或 GRC；
- 不要求用户采用 AgentTeams、Claude Code、某个模型或某个 trace vendor；
- 不允许 Agent 同时提出、评价、批准并执行自己的高风险修改；
- 不用一个总分、移动标签或聊天文本证明系统已安全发布；
- 不把 draft contracts、fixture、mock、replay 或 exporter 当成 live 能力。

## 16. 当前事实与迁移边界

- V4 Stage 1A 是当前唯一已实现的通用 public V4 slice；
- v3 客服 Gate/Release/Console/Replay 是兼容资产，不是 V5 系统治理证明；
- V4 Stage 2A/3/4 的安全语义迁入 V5，但尚未实现；
- AgentTeams/Claude/coding Team 是可选 Adapter，当前未创建或验证；
- V5 使用 expand-and-route 与 additive migrations，不重写历史表/receipt/evidence；
- 只有触达对应 provider/live/external Adapter 时，才要求完成已记录的凭据轮换和
  单独授权；纯 local/shadow 比赛路径不被无关凭据阻塞。
