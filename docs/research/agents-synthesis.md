# AgentMED for Agents —— 调研综合与当前战略

> 更新：2026-08-10 ｜ 外部研究快照截至：2026-08-09
>
> 本文是六路调研的当前综合，用于需求讨论，不是实现证明。产品定位与取舍以 `docs/product-principles.md` 为准；接受后的需求只有迁入新版 PRD、plan、contracts 和可执行测试，才成为实施基线。
>
> 原始报告：`agents-track1`（评测回归）/ `agents-track2`（商业与产品能力）/ `agents-track3`（归因学术）/ `agents-track4`（自动修复）/ `agents-track5`（版本化与回放）/ `agents-track6`（安全合规）。价格、版本、法律、融资和厂商能力使用前须重新核验。

## 一、上位裁决

AgentMED 最终要做成一个面向国内用户、长期维护的**开源 AI Agent 质量与变更治理项目**。

这带来五条直接结论：

1. 产品不是为了击败某个闭源竞品，也不靠“全球没人做”证明价值；
2. 功能范围由目标用户的实际治理任务、安全边界和闭环完整性决定；
3. 别人做过的能力可以直接研究、集成、依法复用，或按公开契约独立实现；
4. 是否自建由可靠性、控制权、数据边界、许可证、国内部署和维护成本决定，不由“红海/空白”决定；
5. 客服是第一条参考 workload，不是最终边界；“所有 Agent 已可接入”目前也不是事实。

因此，竞品、并购、论文和法规只提供技术证据与环境信号，不拥有产品裁决权。

## 二、六路调研真正支持了什么

### 1. 可直接参考和复用的成熟能力很多

- OpenTelemetry、Langfuse、Phoenix 等已经提供 trace 采集、查询与可视化的成熟模式；
- Langfuse、LangSmith、Braintrust、promptfoo、MLflow 等覆盖不同程度的评测、数据集、版本与门禁；
- SWE-bench、Terminal-Bench/Harbor、cassette 和 sandbox snapshot/fork 提供环境固化、判分器验证与回放的工程基线；
- GEPA/DSPy 等可作为修复候选生成器；
- MCP OAuth、现有身份/策略/凭证系统可承担授权基础能力。

这些能力不是 AgentMED “不能碰”的边界。默认应先评估兼容和复用；若不能满足用户的私有部署、证据绑定、确定性失败语义或统一体验，再选择兼容性重实现或自建。

### 2. 单次读日志不等于可靠归因

本轮学术材料显示，在特定 Agent 失败基准上，单靠模型读取轨迹来定位责任 Agent 或错误步骤的准确率仍然很低；反事实脚手架有提升，但也不足以成为生产裁决。完整输入、输出、上下文和工具 I/O 会改善分析，但不会自动产生因果证明。

更稳妥的产品假设是：LLM/tracer 负责生成候选原因和缩小排查面，确定性控制面通过冻结版本、冻结探针、单因素干预、重复运行、效应量和区间来验证或拒绝假设。配置级归因与轨迹步级定位必须分开表述，证据不足时返回 `UNKNOWN`。

这是一条需要在真实工作负载中继续验证的机制，不是已经被学术或当前代码证明的普遍结论。

### 3. 修复可以开放生成，发布必须受控

现有优化器和研究说明，模型可以起草 prompt、skill、tool 配置或其他候选变更；同时也有 reward hacking、自评不可靠和破坏验证器的风险。

因此应保持：候选生成器可插拔，Agent 可自由提出候选；Controller 校验并封装不可变 WorkOrder；独立评测门禁、人工审批、灰度与回滚决定候选是否进入生产。不能让提出修复的 Agent 同时改考题、当裁判和批准发布。

### 4. 通用 Agent 治理需要比客服三层更完整的版本与证据

调研提出的 MVP 版本集候选至少包括：system prompt、skill manifest、tool schema、model snapshot、harness commit 和环境 image digest。它还可能需要纳入 memory/RAG 数据、policy、secret grant、network policy 和外部依赖；六元组目前不是最终标准。

回放也不能只有一个真假开关。至少要区分：

- 全回放：验证 harness、schema 和编排，不证明新模型质量；
- 工具结果回放 + live LLM：隔离外部工具噪声；
- 冻结环境 + live 执行：重新运行工具但固定环境；
- 全 live 多 seed：最终 provider 证据。

每份 GateReport/EvidenceBundle 必须标明证据层级，不能把 replay 升级成 live，也不能把合成故障升级成真实线上效果。

### 5. 安全与合规材料支持控制要求，不等于“已经合规”

不可变工单、最小授权、人工否决、独立复验、自动日志和事故复盘可以支持部分审计与治理义务。但法规适用性、数据保留、隐私、跨境、删除请求和行业要求仍需单独评估；不能说 AgentMED 的架构天然或全面合规。

AgentMED 也不是实时安全盾牌。提示注入、tool poisoning、供应链与第三方 OAuth 风险可以成为质量 Signal 和版本 Gate 输入，但实时检测/拦截通常需要专门的安全组件。

## 三、目标用户与核心工作

首要用户假设是：已经有真实 Agent/LLM 流量，并且能够提供版本、运行证据、评测以及发布/回滚接点的国内应用与平台团队。

他们需要的不是另一张 trace 图，而是把分散系统连成一条可信工作流：

```text
质量 Signal
  → 绑定 Agent Run / Trace
  → 形成 Case 与不可变输入快照
  → 生成并验证归因假设
  → 形成候选变更与 WorkOrder
  → 独立 Gate + 人工/策略 Approval
  → Release / Rollback
  → 场景化 Closure
  → 回归资产、审计证据与 Trust 记账
```

其中“投诉”和“回复原群”只是客服场景的 Signal Adapter 与 Closure Adapter。代码 Agent、研究 Agent、内部自动化或其他业务 Agent 会有不同的信号、结果与发布接点。

这条用户与工作流假设仍需通过访谈、第二工作负载和真实 pilot 验证，不能只由竞品图推导。

## 四、AgentMED 当前应持有的核心

下列能力是当前最值得进入通用需求讨论的核心，不是因为市场空白，而是因为它们共同决定治理闭环是否可信：

- 跨来源的 Case 生命周期、幂等、租约、失败与人工接管；
- Agent Run/Trace、版本、探针、候选与发布结果之间的不可变 evidence binding；
- 假设生成与确定性实验裁决的分离；
- 不可变 WorkOrder、ApprovalGrant 与 Release arbitration；
- 规则/模型双轨门禁及判分器自身验证；
- replay/live/真人/真实 Agent 因果参与的证据分级；
- 发布、回滚、审计、回归资产与 Trust Ledger 的事务性闭环。

这里的“核心”表示 AgentMED 必须对行为和失败语义负责，不表示每个底层组件都要从零实现。

## 五、Langfuse 是第一个通用接入需求

已确认的需求有两条：

1. **AgentMED 自身可观测**：AgentMED 自己的关键 Agent/LLM 运行通过标准 trace 链路进入 Langfuse，便于开发者观察和调试；
2. **治理对象取证**：当 AgentMED 治理 A Agent 时，通过 Langfuse 适配器读取该 Agent 的输入、输出、模型调用、工具调用和相关上下文，作为案件取证来源。

产品上应抽象为可插拔 `TraceSource`，Langfuse 是首个实现，OpenTelemetry/Phoenix/其他后端可按同一契约接入。AgentMED 不需要复制 Langfuse 的整套存储与 UI，但可以实现满足用户需求的采集、查询、证据固化和安全跳转能力。

必须保留四条边界：

- Langfuse 不是 AgentMED 生命周期的权威数据库；
- “能查询到”不等于“完整采集”，需要显式 completeness 状态；
- 进入案件或门禁前，要固化查询窗口、来源、digest、去重和缺失状态；
- 凭据、脱敏、留存和租户隔离必须按国内私有部署场景设计，证据不足时 fail closed 或返回 `UNKNOWN`。

## 六、“真 Agent”与“证据导出”必须拆开

这项研究提出的关键张力现已在 PRD v2 / plan v4 中裁决，但仍需由后续实现和证据证明：

- 真实 Agent 执行，要求 Worker 实际取得输入、调用模型/Skill/工具、产出候选，并在后续领域事件中留下动作前的因果引用；
- 证据导出，只能在动作完成后读取并整理平台记录，证明“这些记录存在”，不能反向证明某个 Agent 做出了贡献；
- 确定性服务完成的步骤应诚实称为 Controller/Executor，不必为了多 Agent 叙事伪装成 Agent；
- exporter 应只读，不能创建 task、ack、submit、人工批准或角色工件来补齐证据。

因此后续验收使用 canonical facets `domain-provider-live` 与 `agent-causal` 分轨。前者可以由确定性 harness 驱动；后者必须在真实 Worker 停止时失败，并能证明提案在领域动作之前产生且被 Controller 接受。

## 七、相关项目的使用方式

`agents-competitors.md` 已改为“相关项目与实现参考”。它的用途是：

- 找可复用的协议、数据模型、验证模式和 UI；
- 找兼容接口与迁移路径；
- 识别我们尚未满足的用户需求与风险；
- 防止把别人已经验证过的工程问题重新踩一遍。

它不再用于证明“对手不会进来”“业务域更大”“必须抢时间”或“某能力不能自己做”。

## 八、当前未知项与需求讨论顺序

以下问题仍未拍板：

1. 首批目标用户的具体角色、触发频率与最痛工作；
2. “适用于所有 Agent”的最小接入契约，以及不满足契约时的降级边界；
3. 第二个真实 workload 选什么，如何证明不是客服专用；
4. 被治理 Agent、AgentMED 内部 Worker、确定性 Executor 与 Exporter 的身份和权限模型；
5. 通用 Signal、Trace、VersionSet、Proposal、Closure 与 Release Adapter 的 schema；
6. Langfuse Cloud/self-host、轮询/上游双写、留存、脱敏和凭据边界；
7. 哪些角色必须有真实 Agent 因果贡献，哪些应明确由确定性服务执行；
8. 长任务、多组件同时变化、外部副作用和不可重放 Agent 的处理方式；
9. 开源治理、贡献边界、兼容性策略与长期维护方式；
10. 真实用户需求、成本、SLO 与 pilot 接受门槛。

建议顺序是：先访谈并冻结用户工作与接入契约，再做 Langfuse 最小纵切和第二 workload，最后才把接受的需求写进 PRD v2 / plan v4 / contracts。当前 v3 继续作为客服参考实现基线，不能一边讨论一边静默改写。

## 九、一句话

**AgentMED 要做的不是“市场上没人做的功能”，而是目标用户真正需要的一条可信 Agent 质量治理闭环；成熟能力尽量参考和复用，关键控制与证据边界由开源代码明确承担。**
