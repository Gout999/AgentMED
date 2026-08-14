# CaseLoop 产品需求文档

> 版本：Rebuild Draft 0.1
>
> 日期：2026-08-13
>
> 状态：新项目产品基线草案
>
> 适用范围：新实现。本文不继承旧仓库的 V3/V4/V5 版本、施工阶段、数据库对象或完成状态。

## 0. 执行摘要

CaseLoop 是一套**由专业 Agent Team 驱动的 AI 应用质量改进与安全变更系统**。

当 AI 应用产生错误回答、工具调用失败、回归、低分 trace、用户投诉或维护 Issue 时，CaseLoop 把零散信息转化为一个可追踪的 Case；由职责隔离的 Agent Team 完成取证、调查、候选修复和独立验证；由确定性治理内核掌握状态、权限、审计与执行；由人类对业务预期和高风险动作保留最终权力。

核心闭环是：

```text
异常/反馈
→ 有来源、可复现、经人确认的 bad case
→ 绑定当时的版本与证据
→ Agent Team 调查并生成候选修复
→ 独立验证候选是否真的解决问题且没有制造回归
→ VerifiedCandidate / Reject / 补证
→ 按需人工批准发布
→ 独立观察、对账、回滚或补偿
→ 沉淀为下一次自动拦截的回归资产
```

CaseLoop 的差异不在于“有更多 Agent”，而在于它把 Agent 的创造性工作置于可复核的分工、证据、门禁和授权之下。

## 1. 产品定位

### 1.1 一句话定义

**CaseLoop 让 AI 应用团队把一次坏结果，转化为一个经过独立验证、可安全采用并能持续防止复发的修复。**

### 1.2 产品类别

CaseLoop 位于 AI 应用、可观测系统、评测工具、Agent runtime、代码仓库和发布系统之上，是面向质量事件的治理与执行闭环。

它自身拥有：

- Case 生命周期与责任边界；
- 验收标准与 bad case；
- 行为相关版本快照；
- 调查、候选和验证记录；
- 变更授权、动作对账和恢复事实；
- 可复用的回归资产。

它通过 Adapter 使用现有 trace、日志、eval、repo、CI、runtime 和部署系统，不复制这些系统的全部能力。

### 1.3 我们不是什么

CaseLoop 不是：

- 客服工单或投诉管理系统；
- 通用 Agent runtime 或多 Agent 聊天框架；
- 全量 observability、trace 存储或数据平台；
- 通用 CMDB、ITSM、IAM、CI/CD 或发布平台；
- “多个模型投票通过即可自动改生产”的自治系统；
- 依赖某个模型、AgentTeams、Langfuse、飞书或云厂商才能成立的产品。

客户服务、代码助手、RAG 问答、运营 Agent 等只是被治理工作负载。

## 2. 用户问题

AI 应用上线后的坏结果通常通过聊天、Issue、低分 trace、告警和人工截图进入团队。当前处理方式存在六类断点：

1. **问题不成案**：反馈缺少运行、版本和证据绑定，无法稳定复现。
2. **预期不明确**：Agent 或工程师直接从 Issue 文本猜测正确答案，业务人员并未确认验收标准。
3. **调查靠经验**：Prompt、模型、RAG、工具、代码和环境相互影响，结论不可证伪。
4. **修复与判卷同源**：提出修复的人或 Agent 同时选择测试和解释结果，容易过拟合或 reward hacking。
5. **变更缺少授权与对账**：候选通过局部测试后直接上线；出现超时或未知结果时盲目重试。
6. **经验不沉淀**：Case 关闭后，bad case、验证方法和失败模式没有进入回归资产，同类问题重复发生。

## 3. 目标用户

### 3.1 首发 ICP

首发面向：

- 2–8 人的 AI 产品或工程团队；
- 正在维护 1–3 个真实 AI 应用；
- 工程负责人通常兼任质量与发布负责人；
- 已有 Git、CI、日志/trace 或反馈渠道中的一部分，但没有专门的 AI 质量平台团队；
- 希望先以 Shadow/Copilot 方式证明价值，再逐步授权低风险动作。

该 ICP 仍需通过访谈和 Shadow pilot 验证，不能作为已经确认的市场事实。

### 3.2 主要人类身份

| 身份 | 主要任务 |
|---|---|
| Application Owner / Maintainer | 对应用结果和 Case 处理负责，审阅调查与候选 |
| Reporter | 提交问题、补充背景并查看进度 |
| Domain Reviewer | 确认业务预期、金标准和不可机器判定的结果 |
| Release Approver | 批准精确的变更目标、范围、风险和有效期 |
| Integrator | 连接 trace、repo、CI、runtime、通知和凭据边界 |

一个人可以兼任多个身份，但系统必须保留不同 principal、scope 和审计记录。

### 3.3 核心 Jobs to Be Done

当我的 AI 应用出现一个坏结果时，我希望：

1. 快速知道它发生在哪个确切版本和上下文中；
2. 把模糊反馈变成经业务确认、可以重放和判定的 bad case；
3. 让专业 Agent 分工调查并提出修复，而不是让我手工拼接日志和 prompt；
4. 在采用候选前证明它修好了问题，而且没有破坏相邻能力；
5. 如果需要部署，能够批准一个不会被掉包的精确动作，并知道实际发生了什么；
6. 把这次经验变成下一次自动复用的回归资产。

## 4. 产品愿景与首个价值

### 4.1 十分产品形态

最终形态下，团队把来自用户、trace、CI、告警或维护者的异常交给 CaseLoop。CaseLoop 自动组织 Agent Team 完成调查、候选生成和验证；对低风险、可逆且已赢得信任的动作按策略执行；对高风险动作请求人类批准；持续观察结果，在异常或不确定时回滚、对账或升级；把每次 Case 变成长期质量资产。

### 4.2 First Useful Case

首个用户价值不是自动发布，而是：

> 一条真实 Signal 进入后，用户在一个 Case Workspace 中看到来源、确切版本、证据完整性、经确认的验收标准、当前缺口和下一步。

### 4.3 First Verified Fix

首个可证明产品价值的完整出口是：

> 在精确 base 与 candidate 上证明 base 能稳定复现失败，candidate 通过 badcase 与必要 regression，并输出 `VERIFIED`、`REJECTED` 或 `INCONCLUSIVE` 及完整证据。

`VerifiedCandidate / NOT DEPLOYED` 是完整产品结果，不是半成品。

## 5. 产品原则

1. **确定性控制面，概率性执行面**：Agent 负责理解、调查、解释和起草；系统负责状态、权限、幂等、门禁、审批、审计和动作对账。
2. **职责分离优于模型共识**：提案者、验证者和批准者必须分离；更多 Agent 的一致意见不能代替证据。
3. **先确认什么叫修好**：Agent 可以起草 AcceptanceSpec，人类 Maintainer/Domain Reviewer 必须确认权威预期。
4. **证据绑定而非聊天记忆**：Signal、版本、证据、候选、测试和动作以不可变引用/digest 关联。
5. **验证与发布分离**：Candidate Verification PASS 只形成 VerifiedCandidate；发布需要独立的 Release Authorization。
6. **诚实表达不确定性**：缺失、部分、冲突或未知状态必须显示为 `NEEDS_CONTEXT`、`PARTIAL`、`INCONCLUSIVE` 或 `UNKNOWN`。
7. **Desired、Observed、Effect 分离**：想要部署什么、实际加载了什么、外部产生了什么副作用，不能互相推断。
8. **人类掌握业务语义和高风险权力**：金标准、生产发布、权限扩大、钱、身份和不可逆动作不交给 Agent 自批。
9. **每次事故让系统变强**：已验证的 bad case、探针、回归和恢复经验进入 RegressionAsset。
10. **平台中立**：模型、Agent runtime、trace、repo、CI 和部署系统都是 Adapter。

## 6. Agent Team 组织

六个角色是稳定的产品职责，不是固定的部署拓扑。一个 runtime 可以承载多个隔离角色，也可以为高风险角色部署独立 Worker；但身份、工具、上下文、输出和审计必须分开。

### 6.1 质量官 / Case Lead

**使命**：分诊、协调、追踪和升级，保证 Case 有负责人和下一步。

**主要产出**：优先级建议、任务分配、进度摘要、升级请求。

**不得做**：直接改 Case 权威状态；替代守门员放行；持有发布执行凭据。

### 6.2 采集员 / Evidence Investigator

**使命**：把原始 Signal 转换为可复核的 bad case 与证据包。

**主要产出**：来源快照、运行/版本定位、证据完整性、badcase 草稿、候选探针。

**不得做**：编造缺失证据；给出最终归因；修改被治理系统。

### 6.3 归因师 / Attribution Analyst

**使命**：在确有必要时提出可证伪调查或对照实验，解释机器报告。

**主要产出**：调查假设、实验计划、结果解读和不确定性说明。

**不得做**：绕过实验把猜测写成事实；把 `INCONCLUSIVE` 包装成确定结论；选择性修改冻结测试。

**适用原则**：归因不是每个 Case 的强制步骤。明显错误、直接复现或低风险修复可以跳过重型因果实验；跨层、重复或高风险问题才启用严格归因。

### 6.4 修复师 / Candidate Builder

**使命**：基于 Case、证据和允许的变更范围起草最小候选修复。

**主要产出**：CandidateRevision、diff/artifact、变更说明、自检和验证建议。

**不得做**：修改验收标准或隐藏测试；自行批准、发布或宣布修复成功；在候选密封后原位改写。

### 6.5 守门员 / Independent Verifier

**使命**：独立设计或执行验证，反向寻找候选失败条件，并给出可复核 Gate 结论。

**主要产出**：EvaluationPlan、GateReport、false-pass 风险和补证要求。

**不得做**：修复候选；修改原始评测结果；让人工审批覆盖 required Gate 失败；执行发布。

**仲裁语义**：required Gate 不是 `PASS` 时，不得形成可执行发布授权。守门员可以阻止放行，但不能凭主观意见制造 PASS。

### 6.6 案例官 / Learning Curator

**使命**：把已关闭 Case 转化为团队长期记忆和回归防线。

**主要产出**：RegressionAsset、案例摘要、探针包、复发关联和质量趋势。

**不得做**：修改进行中的 Case；无证据地把 Case 标成已解决；覆盖历史资产。

### 6.7 人类与确定性 Controller

Agent Team 之外还存在两类不可替代主体：

- **人类**：确认 AcceptanceSpec、裁决业务语义、批准高风险动作、接管异常 Case；
- **确定性 Controller/Executor**：持有状态机、权限、幂等、lease/fencing、Gate 规则、审计和外部执行能力。

### 6.8 冲突仲裁

1. required Gate 未通过时，任何 Agent 或人类都不能把同一份失败报告改成通过；需要新证据、新候选或新的明确决策范围。
2. 证据不足或结论冲突时，Case 进入补证、补实验、人工裁决或 `INCONCLUSIVE`，不得自动向下推进。
3. 权威状态和实际动作以治理内核记录为准，聊天、房间消息和 Agent 自报只作为输入。

## 7. 端到端用户流程

### 7.1 Signal 接入与立案

来源可以是人工报告、低分 trace、用户反馈、Issue、CI 回归、运行错误、工具失败或告警。

系统必须：

- 保留来源引用与内容 digest；
- 进行幂等去重和相似 Case 提示；
- 创建或关联 Case；
- 明确真实性、脱敏、完整性和缺失字段；
- 在无法定位运行时显示 `NEEDS_CONTEXT`，不得伪造 trace。

### 7.2 验收标准确认

采集员和 Agent 可以起草“什么是错误、什么是正确、如何判定”。Maintainer 或 Domain Reviewer 确认后形成不可变 AcceptanceSpec revision。

没有权威验收标准时，Case 可以继续调查，但不能生成“已验证修复”的结论。

### 7.3 版本与证据绑定

Case 绑定发生问题时的 `VersionSnapshot` 与可用 EvidenceReceipt。VersionSnapshot 是行为相关输入的不可变快照，可以包含：

- application/repository revision；
- Prompt；
- model/provider/parameters；
- RAG/knowledge manifest；
- tools/Skill/MCP schema；
- policy、memory、runtime 或 environment 引用。

MVP 允许使用一个可扩展 opaque manifest，不要求用户先维护完整组件拓扑。

### 7.4 调查与归因

质量官创建 typed investigation task；采集员和归因师提交假设、Finding 与证据引用。

调查可以采用：

- 版本 diff 与历史已知良好版本比较；
- trace/log/tool trajectory 分析；
- 本地或 sandbox 重放；
- 单层干预、paired test 或多因素实验；
- repo、测试、配置和依赖检查。

输出为 InvestigationReport，允许 `SUPPORTED`、`REFUTED`、`INCONCLUSIVE` 或 `CONFOUNDED`。它帮助缩小修复范围，但不自动授权修改。

### 7.5 候选修复

修复师在冻结的 base、AcceptanceSpec 和变更边界上生成 CandidateRevision。

候选必须：

- 指明 base VersionSnapshot；
- 记录 artifact/diff digest；
- 不修改验收标准和冻结评测；
- 说明预期作用、风险和已知限制；
- 在进入验证后不可原位修改，修改需创建新 revision。

### 7.6 独立验证

守门员针对 exact CandidateRevision 执行 EvaluationPlan。至少覆盖：

1. base 是否能够稳定复现 bad case；
2. candidate 是否使该 bad case fail-to-pass；
3. 相邻历史回归是否仍通过；
4. known-bad、mutation 或对抗候选能否被拦截；
5. 测试、数据、sandbox 和判定器是否被候选污染；
6. 无法机器验证的部分是否获得独立人类判断。

Gate 结果为：

- `VERIFIED`：required evidence 全部通过，形成 VerifiedCandidate；
- `REJECTED`：候选未解决问题或造成不可接受回归；
- `INCONCLUSIVE`：证据不足、统计效力不足或结果冲突；
- `ERROR`：评测基础设施或完整性失效。

只有 `VERIFIED` 才能进入可选发布流程。

### 7.7 发布请求与授权

如果用户希望部署 VerifiedCandidate，系统创建不可变 ReleasePlan，明确：

- source/base 与 target；
- 目标环境和作用范围；
- canary/观察窗；
- 风险、预算和停止条件；
- rollback/reconcile 方式；
- nonce、expiry 和所需证据。

Release Authorization Gate 针对 Candidate + ReleasePlan 重新判断“是否适合执行该动作”。PASS 后生成 exact WorkOrder，由人类 Release Approver 批准其 hash。

### 7.8 执行、观察与恢复

确定性 Executor 执行 WorkOrder，并记录 idempotency、attempt、provider receipt 和外部效果。

系统必须独立回读目标实际加载的版本或效果：

- desired assignment 不等于 observed state；
- provider 接收请求不等于动作成功；
- 超时或响应丢失进入 `UNKNOWN`，先 reconcile 再决定重试；
- 观察失败时按新鲜授权执行 rollback、停止或补偿；
- rollback 不删除已经发生的外部副作用。

### 7.9 关闭与沉淀

Case 只有在结论、证据和后续动作均明确后才关闭。案例官生成 RegressionAsset，包括：

- badcase 与 AcceptanceSpec；
- base/candidate 引用；
- 有效探针和回归集；
- Investigation/Gate 摘要；
- 已知复发模式；
- 发布、观察和恢复事实（如适用）。

RegressionAsset 默认不可覆盖，后续修订产生新版本。

## 8. 最小核心对象

| 对象 | 产品语义 |
|---|---|
| `AIApplication` | 被治理 AI 应用的轻量身份与 owner，不要求完整 CMDB |
| `Signal` | 来自反馈、trace、Issue、CI、告警或人工的质量线索 |
| `Case` | 一次质量问题从立案到结论的权威工作空间 |
| `AcceptanceSpec` | 经人确认的预期行为、badcase input 和判定方式 |
| `VersionSnapshot` | 行为相关代码、模型、Prompt、数据、工具和环境的不可变组合快照 |
| `EvidenceReceipt` | 来源、查询范围、完整性、缺失项和 artifact digest |
| `Task / Attempt` | Agent 或 executor 的持久工作、lease、身份和产物容器 |
| `InvestigationReport` | 假设、证据、实验和不确定性结论 |
| `CandidateRevision` | 基于 exact base 的不可变候选修复 |
| `EvaluationPlan / GateReport` | 针对 exact candidate 的冻结验证和判定记录 |
| `VerifiedCandidate` | 已通过 Candidate Verification、但不代表已部署的候选 |
| `ReleasePlan / WorkOrder` | 部署意图和经 Gate 冻结的可执行动作 |
| `ApprovalGrant` | 人类对 exact WorkOrder 的一次性授权 |
| `ExternalOperation` | 外部动作、幂等、receipt、UNKNOWN 和 reconcile 记录 |
| `Observation` | 对目标实际版本、行为或副作用的独立回读 |
| `RegressionAsset` | 可复用 badcase、探针、回归和历史教训 |

## 9. 产品表面

### 9.1 Case Workspace

人类主界面围绕一个 Case，而不是围绕数据库对象导航。单页需要回答：

- 发生了什么，来源可靠吗；
- 当时运行了什么；
- 正确行为由谁确认；
- Agent Team 当前在做什么；
- 候选改了什么；
- 哪些测试通过、失败或缺失；
- 当前是否可以采用、发布或必须补证；
- 如果已执行，实际发生了什么，是否需要回滚。

### 9.2 API 与 CLI

HTTP 是 canonical capability；CLI、SDK、MCP/A2A 和 Console 是薄 Adapter。

首批命令应围绕用户动作，而非治理对象 CRUD：

```text
caseloop case create
caseloop case show
caseloop case accept
caseloop investigate
caseloop candidate submit
caseloop verify
caseloop evidence export
```

发布命令在发布阶段单独启用，并且不能暴露给普通 Agent token。

### 9.3 Agent Work Interface

Agent 通过 typed Task 领取工作，通过 Attempt 提交结构化工件。每个角色只能看到完成职责所需的工具、数据和权限。

系统不得要求 Agent 通过房间聊天声明业务成功；成功必须由权威记录、Gate 或 provider readback 证明。

### 9.4 Adapters

首批 Adapter 类型：

- Signal：manual、webhook、Issue、negative score；
- Evidence：trace/log、repo、CI/eval artifact；
- Candidate：repo patch、Prompt/RAG/model configuration artifact；
- Runtime/Release：local target 或 shadow adapter；
- Notification：可选协作渠道。

## 10. 自治模式与风险

| 模式 | 系统能力 | 默认外部写 |
|---|---|---|
| Shadow | 立案、关联、调查、报告和资产沉淀 | 无 |
| Copilot | 生成候选、运行验证、起草 ReleasePlan/PR | 仅隔离或可丢弃草稿 |
| Guarded Action | 对已验证、可逆、窄范围动作执行 canary/rollback | exact 人批或已验证 PolicyGrant |

首发阶段以下动作始终要求人类明确授权：

- 生产发布或流量变更；
- push、merge、正式 PR 或上游留言；
- 权限、凭据和身份变更；
- 钱、合同、合规判断和敏感数据外发；
- 不可逆或难以恢复的外部动作。

低风险自治必须通过真实 pilot 赢得，不能由测试数量或 Agent 自评直接开启。

## 11. 功能需求

### 11.1 核心验证闭环

| 编号 | 需求 | 验收标准 |
|---|---|---|
| FR-01 | 多来源 Signal 立案与去重 | 同 source event 重试不重复立案；原始引用和 digest 可查 |
| FR-02 | AcceptanceSpec 起草与人类确认 | Agent 草稿不能冒充确认；每次确认有 principal 和 revision |
| FR-03 | VersionSnapshot 记录与比较 | exact snapshot 不可变；base/candidate 可以确定性 diff |
| FR-04 | EvidenceReceipt 与完整性 | 缺失字段和权限不足明确显示；不得用 desired 值补造 observed 证据 |
| FR-05 | Durable Task/Attempt | 断线、重启和重试不丢工作；stale lease/fence 的产物被拒收 |
| FR-06 | 六类 Agent 职责隔离 | 每类产物可追溯到角色 principal；提案者不能兼任该候选的最终验证者 |
| FR-07 | CandidateRevision | 候选绑定 exact base、diff/artifact digest 和允许变更面；验证后不可原位修改 |
| FR-08 | Independent Evaluation/Gate | base fail、candidate pass、regression 和 known-bad/anti-tamper 至少各有证据 |
| FR-09 | VerifiedCandidate | 只有 Gate `VERIFIED` 才生成；状态明确为 `NOT DEPLOYED` |
| FR-10 | Evidence export | 一个 Case 可导出机器可验的对象、digest、时间线和 facet 状态 |

### 11.2 安全发布与恢复

| 编号 | 需求 | 验收标准 |
|---|---|---|
| FR-11 | ReleasePlan 与独立授权 Gate | plan 参数在 Gate 前冻结；Candidate Verification 不能替代 Release Authorization |
| FR-12 | Exact human approval | Approval 绑定 WorkOrder hash、target、nonce、expiry；变更任一字段即失效 |
| FR-13 | 幂等外部执行 | 同 key 同请求不重复动作；同 key 异请求稳定冲突 |
| FR-14 | Observation 与 UNKNOWN reconcile | provider receipt、desired 和 observed 分列；未知结果不盲重试 |
| FR-15 | Rollback/stop/compensate | 恢复动作有新鲜权限和 readback；历史副作用不被抹除 |

### 11.3 学习与持续质量

| 编号 | 需求 | 验收标准 |
|---|---|---|
| FR-16 | RegressionAsset | 已关闭 Case 可生成版本化资产，后续 Gate 可实际引用 |
| FR-17 | 复发与相似 Case | 新 Signal 可以提示相似历史 Case，但不能自动继承旧结论 |
| FR-18 | 质量趋势 | 指标能回溯到 Case/Gate/Operation，不使用无来源汇总 |

## 12. 非功能需求

### 12.1 权威与安全

- 单一事务性权威存储拥有 lifecycle、permission、idempotency、approval、audit 和 outbox；
- audit 写失败时业务事务失败；
- secrets 只存引用，Agent 不读取生产凭据；
- workspace/project 边界从 schema 首日存在，即使 MVP 只运行单 workspace；
- 不可信 Signal、Issue、trace、repo 和候选不得扩大工具、网络或权限。

### 12.2 可靠性

- mutation 有 request fingerprint 和幂等语义；
- durable task 使用 lease/fencing、retry/backoff 和 DLQ；
- 外部动作采用 at-least-once + reconcile，不宣称 exactly-once；
- 客户端 detach 不等于取消；
- 失败、跳过和连接异常不能包装成 PASS。

### 12.3 证据与可复现

- 每个结论能追溯到 exact subject、输入、版本、工具、输出和 digest；
- replay、真实 provider、真实 Agent、外部动作和 production 证据分开报告；
- fixture/mock 只能证明 contract/replay，不能证明 live；
- 公开 evidence 不包含 secret 或未脱敏 PII。

### 12.4 可部署与可替换

- 默认本地栈最多 2–3 个核心服务，fresh checkout 十分钟内达到 First Useful Case；
- 本地验证路径不要求付费 provider key；
- 模型、Agent runtime、trace、repo、CI 和通知渠道通过 Adapter 替换；
- K8s、HA 和企业部署由真实客户需求驱动，不阻塞首发。

## 13. MVP：First Verified Fix

### 13.1 MVP 假设

如果一个小型 AI 团队可以用 CaseLoop 在明显少于现有人工流程的时间内，把真实坏结果变成可信的 VerifiedCandidate，并且 Gate 能拦截已知坏候选，那么“Agent Team + 独立治理闭环”具有产品价值。

### 13.2 MVP 纵切

```text
一个真实 AI 应用或 AI repo 的真实问题
→ manual/Issue/trace Signal
→ Case + confirmed AcceptanceSpec
→ exact base VersionSnapshot
→ 六类职责中按需激活取证、调查、修复、验证和沉淀角色
→ base fail 的真实复现
→ CandidateRevision
→ badcase fail-to-pass + regression + known-bad/anti-tamper
→ VERIFIED / REJECTED / INCONCLUSIVE
→ RegressionAsset
```

MVP 不执行远程 push、PR merge 或生产发布，以 `VerifiedCandidate / NOT DEPLOYED` 结束。

### 13.3 MVP 必须包含

- 一个轻量 AIApplication 和 opaque VersionSnapshot；
- 一个真实 Signal 来源与 manual fallback；
- AcceptanceSpec 人类确认；
- 持久 Task/Attempt 与角色身份；
- 至少一个真实 Agent 参与调查或候选生成；
- independent verifier 与 deterministic Gate；
- PostgreSQL/HTTP/CLI 的真实本地链路；
- 一个 Case Workspace；
- 可验 evidence manifest；
- 一个 known-bad candidate，证明 Gate 不是只会通过。

### 13.4 MVP 明确不做

- 生产 release、canary 和自动 rollback；
- Public MCP/A2A/SDK 全家桶；
- Agent 动态扩缩容；
- 强制 5-cell/全因子归因；
- 完整组件拓扑、Episode 图或企业 CMDB；
- Trust Ledger 自动晋升；
- Skill/MCP 自演化；
- 多租户、HA、GRC、FinOps、Incident/SLO 全套模块。

### 13.5 MVP 出口

- 2 名非项目维护者可以在不理解内部聚合和状态机的情况下独立完成流程；
- 至少 3 个真实 Case；
- 每个 Case 的 base 可以稳定复现失败；
- 至少一个 candidate 被 VERIFIED、一个被 REJECTED 或 INCONCLUSIVE；
- known-bad candidate 被 Gate 拦截；
- 记录从 Signal 到 verdict 的时间、人类分钟、token/费用和阻塞点；
- 未经授权外部动作数量为 0；
- evidence 来自真实业务记录和 receipts，而不只是测试数量。

## 14. 后续产品阶段

### 阶段 A：可信候选

完成 First Useful Case、Agent Team 调查、Candidate 和独立验证，出口为 VerifiedCandidate。

### 阶段 B：本地/Shadow 安全变更

接入一个可独立回读版本的本地 target，完成 ReleasePlan、人工批准、执行、观察、UNKNOWN reconcile 和 rollback drill。

### 阶段 C：真实工作流接入

增加 trace、Issue、repo、CI 和协作渠道 Adapter；运行真实 Shadow pilot；根据采用摩擦调整模型和流程。

### 阶段 D：受控生产动作

仅在真实 pilot 证明 Gate、回滚和人工边界可靠后，启用窄范围 canary 与 Guarded Action。

### 阶段 E：扩展能力

按真实用户需求逐项准入高级归因、Skill/MCP 治理、持续巡检、Incident/SLO、多租户和企业 Adapter。任何模块都不得以“完整平台需要”为理由提前进入。

## 15. 成功指标

### 激活与采用

- fresh install → First Useful Case 时间；
- First Useful Case → First Verified Fix 转化率；
- 非项目用户独立完成率；
- 第 2、3 个 Case 相比首个 Case 的人类时间下降。

### 质量价值

- Signal→Version/Evidence 关联率；
- base 失败可复现率；
- verified resolution rate；
- REJECTED/INCONCLUSIVE 原因分布；
- Gate false-pass、false-block 和 known-bad 检出率；
- Case reopen/recurrence；
- RegressionAsset 后续实际复用率。

### 效率与成本

- Signal→verdict 用时；
- 每个 Case 的人类分钟、Agent token、费用和工具调用；
- Agent Proposal 接受率；
- 因证据缺失而阻塞的比例。

### 安全与可靠性

- 未经授权动作数，目标 0；
- 幂等冲突和重复副作用；
- UNKNOWN reconcile 成功率；
- rollback/stop/compensate 成功率；
- evidence 完整性与审计失败数。

## 16. 产品验证计划

在宣称通用产品价值前，至少完成：

1. 访谈 5 个符合 ICP 的团队；
2. 获得 3 份脱敏的真实 badcase 处理流程；
3. 找到 2 个愿意运行 Shadow pilot 的团队；
4. 记录他们当前从反馈到修复的步骤、时间、角色和风险；
5. 用同一真实 Case 对比原流程与 CaseLoop 流程；
6. 验证用户是否愿意持续把 RegressionAsset 用于后续 Gate。

如果用户只需要更好的 trace 浏览或 eval runner，而不需要 Agent Team 协作、候选治理和安全变更闭环，应缩小或调整产品定位。

## 17. Anti-goals

- 不先建设一个拥有所有 AI 治理对象的“大平台”；
- 不把 Agent 数量、聊天活跃度或房间记录当成产品价值；
- 不让同一 Agent 同时提出、判定、批准并执行高风险修改；
- 不用单一总分或 LLM 主观文字证明候选安全；
- 不要求所有 Case 都进行重型因果归因；
- 不把 VerifiedCandidate 描述成已部署；
- 不把 desired 配置、provider receipt 或 adapter 自报当成 observed effect；
- 不把测试、fixture、replay 或合同冻结描述成 live/production；
- 不绑定某个 Agent runtime、模型、trace vendor 或协作工具；
- 不在核心闭环获得真实采用前扩张企业模块。

## 18. 待产品负责人确认的决策

| 决策 | 推荐默认值 | 影响 |
|---|---|---|
| 首个工作负载 | 一个可本地复现的真实 AI 应用/repo badcase | 决定 Candidate 和 Gate Adapter |
| 首发是否包含发布 | 不包含，以 VerifiedCandidate 结束 | 显著降低范围并先验证核心价值 |
| 六角色部署方式 | 六类逻辑 principal，按 Case 激活；不强制六个常驻进程 | 保留分工，降低运行成本 |
| 第一个真实 Signal | manual/Issue + 一个 trace/score Adapter | 平衡可用性与真实性 |
| 首发形态 | 本地自托管，HTTP/CLI + 单 Case Workspace | 最快得到真实用户反馈 |
| 高级归因 | 可选，按 Case 风险启用 | 避免实验系统阻塞普通修复 |
| 旧项目数据迁移 | MVP 不迁移；只把旧合同和 evidence 当测试语料 | 避免兼容历史拖入重建 |

---

本 PRD 回答“为谁解决什么问题、产品如何工作、哪些边界必须成立”。新项目的技术架构、数据模型、API 和施工计划应在本 PRD 获得确认后单独设计。
