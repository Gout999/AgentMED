# AgentMED 完整产品叙事（终态产品口径）

> 从一次 AI 坏结果，到可信修复、受控变更与可恢复运营
>
> 文档性质：产品叙事主稿
> 叙事口径：项目完整建成后的产品形态、用户场景与长期价值
>
> **当前状态边界**：本文是 pre-production 终态叙事，不是当前实现或 live 证明。
> V5-1A/B/C 仍为 `IN_PROGRESS`，V5-2+ 为 `NOT_IMPLEMENTED`；provider/live/production facets
> 全部保持 `NOT_RUN`。当前事实见 [`PROJECT_STATE.md`](../context/PROJECT_STATE.md)，
> 文档权威顺序见 [`docs/README.md`](../README.md)。

> 配套资料：[《AgentMED 项目信息说明书》](./AgentMED-项目信息说明书.md)

---

## 序章：生成越来越快，证明却越来越难

AI 正在迅速改变软件的生产方式。过去需要工程师数天完成的分析、修改和测试，现在一个 Coding Agent 可能在几分钟内给出答案；过去需要手工编排的工作流，现在也可以由多个 Agent 协作完成。模型越来越强，工具越来越多，自动化链路越来越长。

但当 AI 应用真正进入用户场景之后，团队遇到的核心问题并没有消失，反而变得更复杂。

用户只会说一句：“它刚才答错了”“这个 Agent 做了不该做的事”“升级以后效果变差了”。这句话背后可能涉及应用代码、模型、Prompt、RAG、索引、工具、权限策略、Memory、Runtime、部署环境以及外部系统。任何一层变化，都可能让同样的输入产生不同的结果。与此同时，Issue、trace、日志、评测、代码、配置、审批、部署和外部副作用散落在不同平台里，没有一个系统对完整闭环负责。

于是，团队会陷入一连串无法轻易回答的问题：

- 出错当时，真正运行的是哪一套完整系统？
- 用户描述的“错误”，究竟对应什么可以被确认的正确行为？
- Agent 给出的修改，是真的解决了原问题，还是只让自己新增的测试通过？
- 候选修复通过验证以后，是否就有资格进入发布？
- 被批准的版本，是否真的被目标 Runtime 加载？
- 系统已经对用户或外部平台产生了什么影响？
- 如果结果不一致、证据不足或执行结果未知，系统能否安全停止、对账和恢复？

今天的 AI 工具大多擅长回答“可以怎么改”，可真正进入生产以后，团队更需要有人回答：“这次改变是否可信、是否获准、是否真的生效，以及失败后如何恢复。”

> AgentMED 要解决的，不是 Agent 会不会写代码，而是 Agent 写出改变之后，这个改变如何被可信地采用。

## 一、真正被治理的不是一个 Agent，而是一整个 AI 应用

很多 AI 工具仍然把 Agent 当成系统的中心对象。但一个真实的 AI 应用从来不只是一个 Agent，也不只是一个 Prompt。

它通常同时包含应用代码与编排逻辑、模型与参数、Prompt、RAG 和检索链路、数据集与索引、Skill、MCP Server、Tool Schema、Policy、Memory Policy、Runtime Profile、连接器和部署环境。用户最终看到的行为，是这些组件共同作用的结果。

因此，只记录“用了哪个模型”或“是哪一个 AgentRun”远远不够。团队需要知道的是：在某个环境里，这一刻完整组合了哪些组件；这些组件之间如何依赖；哪些绑定是不可变的；哪些远程依赖仍是可变别名；哪些部分因为来源缺失只能诚实标记为未知。

AgentMED 把完整的 `AIApplication` 作为顶层治理对象。Agent 是一等 `SystemComponent`，但不是整个产品边界。代码、模型、Prompt、RAG、Skill、工具、Policy、Memory 和 Runtime 都可以进入同一套版本、证据、评测、变更和恢复闭环。

这使系统能够从“某个 Agent 做了什么”升级为“这次用户体验背后，完整 AI 系统运行了什么”。

## 二、最先需要帮助的人，是正在为结果负责的工程负责人

AgentMED 的核心目标用户，不是已经拥有完整治理平台的大型组织，而是运行一个或少量真实 AI 应用、没有专职平台团队的产品与工程团队。

在这样的团队中，工程负责人往往同时承担 AI 应用维护者的职责。他既要修改 Prompt、代码和模型配置，也要理解 RAG、工具与 Runtime；既要响应用户反馈，也要判断变更风险；既要让 Agent 提升开发效率，又必须对最终用户体验、发布结果和恢复负责。

他真正想完成的工作不是“维护一张治理清单”，而是：

> 当一个 AI 应用出现坏结果时，快速确认发生了什么、当时运行了什么、正确行为应该是什么、哪个候选真正修复了问题，并安全决定是停在已验证候选，还是进入人工授权、独立观察和可恢复的发布。

围绕这位核心负责人，还会有 Integrator、Maintainer、Domain Reviewer、Release Approver，以及 Reliability/Security Reviewer。一个人可以兼任多个角色，但身份、权限、审批和审计不能因此混在一起。谁提出了候选、谁确认了验收标准、谁批准了高风险动作、谁执行了外部操作，必须能够被分别证明。

## 三、现有工具拥有局部真相，但没有人拥有完整闭环

可观测平台可以告诉团队“发生了什么”：有哪些 trace、span、日志、指标和调用链。Coding Agent 可以告诉团队“可以怎么改”。评测框架可以说明某个版本在一组数据和 evaluator 上表现如何。CI/CD 可以构建和部署。ITSM、CMDB 与 IAM 可以管理通用工单、资产和身份。

这些能力都很重要，AgentMED 也不打算替代它们。

问题在于，每个平台只保留了一部分真相：

- Issue 保存了用户描述，却不一定包含权威验收标准；
- trace 保存了运行片段，却不一定能固定完整系统版本；
- 仓库保存了代码差异，却无法证明目标 Runtime 已经加载；
- 评测平台给出分数，却不一定绑定真实 bad case、候选、发布计划和证据完整性；
- CI/CD 完成部署动作，却无法单独证明实际加载、外部效果和恢复结果；
- Agent 可以生成提案，却不应成为自己提案的评分者、审批者和高风险执行授权者。

AgentMED 的价值不是再造这些平台，而是把它们提供的证据和动作，连接成一条具有权威状态、失败语义和责任边界的治理交易。

## 四、AgentMED 是什么

AgentMED 是面向 AI 应用的 Agent-native 治理运营控制面。

它把一次 AI 坏结果，转化为可复现的 Case、可验证的候选修复，以及在需要部署时经人工授权、可观察、可对账、可恢复的系统变更。

它自身持有六类不能外包的治理事实：

1. AI 应用及其确切系统版本；
2. 质量 Case 与经确认的验收合同；
3. 证据快照、来源和完整性状态；
4. Candidate、Evaluation 与 Gate 的确切绑定；
5. 变更授权、执行、观察、对账和恢复事实；
6. 审计时间线与从真实事故沉淀出的回归资产。

AgentMED 不以“替代所有工具”为目标。它通过 Adapter 对接 observability、repo、Agent runtime、eval、CI/CD、IAM、ITSM 和其他平台；这些平台提供来源证据或执行动作，但不能自行宣布 Case 已解决、Gate 已通过或发布已成功。

一句话概括：

> Agent 负责提出候选，确定性系统负责验真和约束，人类掌握高风险授权，运行结果必须被独立核对。

## 五、一条坏结果如何进入 AgentMED

### 1. 从一句反馈开始，而不是从一张治理表开始

AgentMED 的第一价值是 First Useful Case。

一条真实反馈、Issue 或异常进入系统后，首先形成稳定的 Signal 与 QualityCase，并绑定明确的 AIApplication 和 Environment。系统保留原始来源，但不会把来源文本自动提升为系统指令、金标准或授权。

如果用户只说“它答错了”，系统不会要求 Agent 猜测“什么才算正确”。Agent 可以根据 Issue、trace 和历史 Case 提出 Acceptance Criteria 草稿，但只有 Maintainer 或 Domain Reviewer 能确认期望行为。

确认后的验收标准会冻结复现输入、适用环境、期望结果、判断方式和责任人。若信息仍不足，AgentMED 会返回 `NEEDS_ACCEPTANCE_CRITERIA` 和下一项所需动作。

“不知道正确答案是什么”在 AgentMED 中不是一个失败页面，而是一个诚实、可操作的产品结果。

### 2. 固定问题发生时的系统版本

AgentMED 使用不可变 `SystemVersionSet` 表示一套完整运行组合。它精确绑定组件 revision、依赖拓扑、来源、环境和自身摘要。

环境中的 `SystemAssignment` 只表示 desired——系统应该运行哪一版。它不会改写既有 VersionSet，也不证明目标 Runtime 已经切换。

对于能够固定的代码、Prompt 或配置，系统记录不可变 digest；对于 provider version、可变 alias、只能从运行时观察的对象或完全未知的依赖，系统分别记录 assurance。AgentMED 不为了生成一张看起来完整的版本图而制造精确性。

### 3. 把散落事实封成可绑定证据

一次用户体验可能跨越多个 trace、AgentRun、tool call、日志和外部作用。AgentMED 将这些来源组织为带 provenance、watermark、coverage 和 missing 信息的 `SystemEpisodeView`。

但不断变化的 View 不能成为 Gate 的证据。进入归因或评测前，Evidence Controller 会把确切 receipt 集合、assignment generation 和完整性状态封成不可变 `SystemEpisodeSnapshot`。后续新证据只能产生新快照，不能修改旧判断的证据基础。

### 4. Agent 提出候选，但不获得裁决权

外部 Coding Agent、内部 Worker 或 Maintainer 都可以根据 Case 调查证据、提出原因假设，并提交不可变 `SystemCandidateRevision`。

Candidate 可以修改一个或多个系统组件，但必须说明主要变更面、依赖顺序、风险、blast radius、验证要求和恢复要求。Candidate 是提案，不是已接受结论，更不是执行权限。

### 5. 验证的是原问题，而不是 Agent 自己写的测试

Candidate Verification Gate 回答一个具体问题：

> 这个 exact Candidate 是否解决了被人确认的真实问题，并有足够证据进入下一项动作？

同一个 bad case 必须在 base 上失败、在 Candidate 上通过。系统同时执行仓库既有测试、历史 Case 形成的回归资产、与 blast radius 相邻的检查、sealed holdout 和 anti-overfit 检查。

如果是确定性代码问题，Judge 可以明确为 `N/A`；如果是非确定性 AI 行为，系统需要配对重复、原始失败分布、effect 与 interval。未经领域样本校准的 LLM Judge 只能提供 advisory Finding，不能决定发布。

任何 required dimension 或 evidence 出现 `FAILED`、`INCONCLUSIVE`、`ERROR`、`UNKNOWN`、缺失或换绑，都不能被一个总分平均掉。

但 fail closed 不等于让用户走进死胡同。系统会给出补充验收标准、补证、增加样本、修复 evaluator 或重新评测的 Next Required Action。

## 六、修复被验证，不等于获准发布

这是 AgentMED 最重要的产品边界之一。

`CANDIDATE_VERIFICATION` 通过，只说明一个候选已被验证。对于 library 或 offline workload，流程可以明确结束为：

`VerifiedCandidate / NOT DEPLOYED`

系统不会为了讲一个“完整发布故事”而伪造部署。

只有 Maintainer 对真实可部署目标显式请求 release，流程才进入新的授权链：

`ReleasePlan → RELEASE_AUTHORIZATION Gate → SystemWorkOrder → human Approval → scoped operation`

发布计划必须在 Gate 前冻结 rollout、observed verification、known-good rollback 和 recovery 参数。Release Authorization Gate 必须重新精确绑定 Candidate、base/target VersionSet、EvaluationBundle 和这份 ReleasePlan。

只有该目的的 exact PASS 才能创建 WorkOrder。WorkOrder 进一步绑定确切 target、assignment generation、nonce、expiry 和风险。重新认证的人类批准的是这份具体工单，而不是一句模糊的“同意上线”。

Executor 只能执行已获批参数，不能在 Gate 或审批之后悄悄改变 rollout、目标或恢复计划。

## 七、系统必须分开回答 Desired、Observed 与 Effect

AI 系统最危险的误判，往往来自三个事实被混在一起：

- **Desired**：系统应该运行什么；
- **Observed**：目标实际上加载、解析或调用了什么；
- **Effect**：系统已经对用户或外部平台做了什么。

移动 desired pointer，不等于 Runtime 已经切换；部署 Adapter 返回成功，不等于目标进程加载了正确 digest；动作获得批准，也不等于外部效果已经成功。

AgentMED 要求从真实目标进程、容器或 version endpoint 独立读取 observed state。只有确切 assignment generation、required execution receipts、完整且匹配的 ObservedStateSnapshot、适用的 post-release Gate，以及不存在 required child/effect UNKNOWN 时，发布才有资格被判定为成功。

如果 Desired 已变化、Observed 尚未匹配，AgentMED 返回的是 `UNKNOWN`，而不是一排绿色勾。

这体现了 AgentMED 的产品性格：它不以“自动化完成”为目标，而以“系统事实可以被证明”为目标。

## 八、失败后不是改回一个标签，而是完成恢复

真实系统的 rollback 从来不是简单地把版本号改回去。

AgentMED 将恢复拆成一组可审计的动作：

1. stop exposure，先停止继续扩大影响；
2. restore known-good desired assignment；
3. 从真实目标独立验证 observed runtime；
4. reconcile 仍在途或结果未知的动作；
5. revoke capability、credential 或临时权限；
6. 在可能时 compensate 已经发生的不可逆外部作用。

每次 rollback 都创建新的 Operation、RecoveryWorkOrder 和重新认证的人类 emergency approval。原发布 WorkOrder、Approval、nonce 与 capability 不能被重复使用。

AgentMED 不声称跨系统 exactly-once，也不承诺所有副作用都可以撤销。它承诺的是：每一步都有 intent、attempt、provider identity、receipt、UNKNOWN reconcile 和明确责任。

## 九、一次事故最终要变成下一次发布的防线

一个 Case 的价值不应该在“修完以后”消失。

当 Case 被显式关闭，真实 bad case、验收标准、修复候选、证据和回归结果可以沉淀为 `RegressionAsset`。后续相关变更不只是看到一个历史链接，而是要在 regression bundle 中实际执行这项资产。

这样，AgentMED 才真正形成“Loop”：

一次坏结果被立成可信案件；一次可信案件产生可验证修复；一次修复形成未来变更的防线；下一次 Gate 再使用这些历史经验。

治理不再是发布前额外增加的一组审批，而是一个持续吸收真实失败、不断提高系统可信度的学习闭环。

## 十、三个产品表面，共享一个治理真相

AgentMED 最终呈现为三个同等重要、职责分离的产品表面。

**Human Console** 面向 Application Owner、Maintainer、Reviewer、Approver、SRE 和安全人员。它用于查看、比较、确认验收标准、审批、干预、复盘和审计。

**Agent Capability Gateway** 面向外部 Agent、CI 与客户应用。它允许程序化调用者提交 Signal、请求调查、提交 Candidate、启动评测或发布请求、读取 Operation 与 Evidence。

**Deterministic Governance Kernel** 由确定性的 Controller 与 Executor 构成。它拥有生命周期、权限、幂等、租约、Gate、审批、执行、审计、对账和恢复。

HTTP 是 canonical capability baseline。CLI、MCP、A2A、SDK 和 Console 都是这套能力的 Adapter。相同 intent 必须复用相同资源、状态、错误、幂等和审计语义，不能形成多套业务真相。

## 十一、Agent-native，不是 Agent authority

AgentMED 相信 Agent 会成为软件系统的一等用户。Agent 不应只能躲在聊天框里，它应该能够通过清晰的 capability 接口报告问题、读取证据、发起调查、提交候选、请求独立评测和跟踪结果。

但第一等调用权不等于主权。

Agent 不能自报 Application Owner、已部署版本、Observed State 或外部效果；不能确认自己提出的验收标准；不能同时提案、评分、批准并执行自己的高风险修改；不能直接改写 Gate、Approval、Release 或其他权威生命周期；也不能从 MCP/A2A 的 Task 完成状态中获得人类审批或内部 execute authority。

Agent 可以负责动脑，确定性系统负责管规矩，人类负责承担高风险授权。

这一边界不是为了限制 Agent 的价值，而是为了让 Agent 的价值能够进入真实生产。

## 十二、AgentMED 的边界与完整能力版图

AgentMED 不建设适用于所有传统软件的通用治理平台，也不复制完整 observability、Agent runtime、CMDB、ITSM、IAM、CI/CD、FinOps 或 GRC。

它与这些系统的差异，不来自一张更长的功能清单，而来自职责边界：

- Observability 回答“发生了什么”，AgentMED 把来源证据绑定进 Case、Gate 与恢复生命周期；
- Coding Agent 回答“可以怎么改”，AgentMED 独立验证这个 exact Candidate 是否有资格进入下一步；
- Eval 框架回答“在某套数据上表现怎样”，AgentMED 冻结真实问题、版本、目的、证据和硬失败语义；
- CI/CD 执行构建和部署，AgentMED 持有授权、观察、对账与恢复事实；
- ITSM 管理通用流程，AgentMED 管理 AI 应用特有的完整组件版本、非确定性评测和 Agent-native intent。

完整产品同时覆盖单个 AI 应用的可信闭环、持续运营与企业生态：多 Agent 与多组件 topology、Public MCP/A2A/SDK、Incident/Problem/KnownError、SLO/Error Budget、模型与数据供应链、Memory 治理、成本与供应商退场、多租户、企业系统 Adapter、合规证据映射与高可用运行。

这些扩展能力不改变内核原则：Agent 负责提出，系统负责约束，人类负责授权，结果必须被观察，失败必须可以对账。

## 十三、AgentMED 如何改变团队的一天

团队第一次接入 AgentMED 时，通过自托管或私有部署建立 Workspace，连接代码仓库、模型与 RAG 配置、可观测来源、Runtime、部署平台和身份系统。`agentmed init` 从真实仓库生成系统 manifest 草稿，负责人确认应用、环境、组件与责任边界。此后，每个环境都拥有可比较的系统版本、desired assignment、observed state 和完整审计时间线。

当新的用户反馈、Issue、trace 异常或 SLO 事件出现时，AgentMED 自动把来源组织为待处理 Signal。维护者不再从多个平台手工拼接上下文，而是在 Case Workspace 中看到问题来源、受影响应用、当时系统版本、Episode evidence、缺失信息和下一项动作。

Domain Reviewer 可以确认“什么才算修好”；外部 Coding Agent 可以在相同 Case 上读取受限证据并提交 Candidate；Gate 独立验证真实 badcase、回归、holdout 和安全边界。对于离线资产，团队得到明确的 Verified Candidate；对于在线服务，Release Approver 在 Change Center 中审批 exact WorkOrder，随后由受限 Executor 执行。

发布后，AgentMED 从目标 Runtime 独立回读 observed state，并持续对照 desired、observed 与 effect。任何不一致都会进入 reconcile 或 recovery，而不是隐藏在“流水线成功”之后。事故经显式关闭与审核后，真实 badcase 可沉淀为 RegressionAsset，进入下一次相关 Gate。

在持续运营层，Application Owner 可以从 Applications 与 System Graph 查看多应用风险、版本漂移、开放 Case、SLO、供应链变化、成本和供应商退场影响。Reliability/Security Reviewer 可以审计权限扩大、未知外部作用、恢复演练和凭据撤销。Agent、CI 和客户应用则通过同一 Capability Gateway 将治理能力嵌入各自工作流。

最终，团队不再依赖某位工程师记住每次事故的来龙去脉，也不再把一串工具的绿色状态当成系统成功。AgentMED 把“我们认为已经修好”变成“我们能够证明修复、授权、运行与恢复都发生在同一个确切对象上”。

## 结语：敢说 UNKNOWN，才有资格按下发布按钮

AI 时代并不缺少更大胆的 Agent。真正稀缺的是一个能够在证据不足时停下来、在事实不一致时拒绝变绿、在高风险动作前要求确切授权、在失败以后继续对账和恢复的控制面。

AgentMED 希望让团队获得一种新的确定性：不是保证 AI 永远不犯错，而是保证每一次错误都能被有来源地记录，每一次修复都能被独立验证，每一次高风险变更都经过明确授权，每一次运行结果都被真实观察，每一次失败都留下可以恢复和复用的资产。

> 让每一次 AI 变更，都有案可查、有证可验、有权可控、有错可退。
