# AgentMED for Agents —— 相关项目与实现参考

> 2026-08-10 更新 ｜ 外部事实主要核验于 2026-08-09
>
> 文件名为兼容既有链接保留。本文不是竞争策略，也不决定 AgentMED 做或不做什么；它用于寻找可复用设计、兼容接口、风险和证据。产品取舍以 `docs/product-principles.md` 为准，时效性事实使用前须重新核验。

## 0. 阅读方式

相关项目覆盖 Agent 可观测、评测、安全、身份、修复验证和自治分级。能力重叠是正常的，也可能正好说明已有工程模式可直接学习。

本文回答四个问题：

1. 对方已经验证了哪些用户任务或工程机制；
2. AgentMED 可以复用、兼容或参考什么；
3. 哪些边界仍需要自己的确定性控制与证据绑定；
4. 哪些结论只是本轮公开样本中的推断，不能升级成全球事实。

## 1. 两项重点核验

| 参考项 | 本轮核验到的事实 | 对 AgentMED 的帮助 |
|---|---|---|
| Cleric `graduated autonomy` | 官网描述按 problem type 的 accuracy 决定独立行动或升级人工，并强调用 live environment 验证修复；其公开定位集中在 infra SRE | 直接参考“按问题类验证结果放权”的产品与证据表达；研究它如何把验证、升级人工和用户界面连起来，而不是声称 AgentMED 发明了该理念 |
| CSA Agentic Trust Framework | CSA 零信任工作组 2026-02 发布 Intern→Principal 四级框架，强调自治需要通过门槛获得；它是框架，不是 AgentMED 的统计实现 | 作为自治/信任术语和控制要求的参考；AgentMED 的 Wilson、evidence epoch 与逐动作样本仍需独立验证，不能借标准背书成既定正确方案 |

来源：[Cleric](https://cleric.ai/)、[Cleric verifying fixes](https://cleric.ai/blog/verifying-fixes)、[CSA ATF](https://cloudsecurityalliance.org/blog/2026/02/02/the-agentic-trust-framework-zero-trust-governance-for-ai-agents)。

## 2. 五类最相关参考

### ① Cleric：Agent 化 SRE 与验证后放权

- 已公开的机制：告警/工单取证、调查与修复、用线上信号验证结果、按问题类做 graduated autonomy；
- 可借鉴：问题分类、验证回执、人工升级、按能力类别积累表现的 UI 和工作流；
- 与当前 AgentMED 参考实现的不同：工作负载和发布接点不同。这个差异不构成永久产品边界，AgentMED 应保持接口兼容并持续研究其实现。

### ② Microsoft Agent 365 与 CSA ATF：身份、策略与自治框架

- 已公开的机制：Agent 注册、可见性、身份/策略治理、自治等级与晋升/降级原则；
- 可借鉴：Agent 一等身份、owner、策略版本、人工否决、降级与生命周期术语；
- AgentMED 仍需负责：与具体质量案件、不可变 WorkOrder、评测证据和发布决定的绑定。身份与策略引擎本身可以集成，不必为了产品叙事另造一套。

### ③ AIR 等 Agent incident-response 研究

- 论文覆盖运行时检测、遏制、恢复和防复发 guardrail；“首个”等表述属于作者或本轮样本口径，不能当全球事实；
- 可借鉴：事故 taxonomy、contain/recover 生命周期、合成故障与已知根因 benchmark；
- 需求影响：质量 Case 和安全 Incident 可能共享信号与证据，但实时拦截、质量归因和发布仲裁应保持不同责任边界。

来源：[AIR arXiv](https://arxiv.org/abs/2602.11749)。

### ④ Credo Agent Governor、Cisco AI Defense 与 guardrail/策略系统

- 已公开的机制：把政策编译进 Agent harness、权限与运行时安全检查；
- 可借鉴：policy-as-code、版本化安全 surface、风险信号和拦截回执；
- 接入关系：这类系统可以向 AgentMED 提供 Signal 和 Gate 输入。若目标用户需要统一自托管体验，也可以实现兼容适配或必要的本地能力。

### ⑤ Maxim、Galileo、Confident AI、Braintrust 等 Agent eval 平台

- 已公开的机制：trajectory/tool-call 评测、simulation、数据集回流、review 与 CI/release gate；
- 可借鉴：评测 schema、数据集版本、人工 review、experiment UI、门禁与 trace 绑定；
- 接入关系：它们可以作为 Eval Provider。AgentMED 需要解决跨系统案件、确切审批对象和发布/回滚的权威闭环，但不必否定或复制全部评测功能。

## 3. 能力对照不是产品裁决

| 能力 | 已有参考 | 当前 AgentMED 需求讨论应问的问题 |
|---|---|---|
| Trace 与运行观测 | Langfuse、Phoenix、OTel、Braintrust 等 | 如何用统一 TraceSource 接入、报告完整性并固化案件证据 |
| Eval 与门禁 | Maxim、Galileo、Braintrust、promptfoo 等 | 哪些 provider 可直接使用，Gate 如何绑定不可变版本与审批 |
| 身份与策略 | Agent 365、Permit.io、OPA/Cedar 等 | 如何绑定 Agent 身份、owner、WorkOrder 和临时授权，不重复造通用 IAM |
| Sandbox 与 replay | E2B、Modal、Daytona、SWE-bench、cassette 系 | 如何选择可自托管/国内可用实现，并统一 evidence level 与退出机制 |
| 修复候选 | GEPA/DSPy、厂商优化器与人工编辑器 | 如何定义可插拔 Repair Proposer，并独立验证候选而非相信自评 |
| 修复验证与自治 | Cleric、CSA ATF | 如何用真实 workload 校准样本、阈值、降级和人工接管 |
| 质量案件与发布仲裁 | 多个系统分别覆盖部分环节 | 用户是否需要跨系统的 Case/WorkOrder/Approval/Release/Trust 闭环，以及最小契约是什么 |

表中最后一行仍是待验证产品假设。本轮样本没有发现单一项目同时覆盖同一套精确链路，不等于“全球无人做”，更不自动证明用户需要 AgentMED 的全部设计。

## 4. 对外准确表述

可以说：

- “AgentMED 是开源 AI Agent 质量与变更治理控制面，当前从客服参考 workload 向通用 Agent 契约扩展。”
- “我们会接入 Langfuse/OTel、评测、runtime、sandbox 与身份系统，并在其上绑定案件、证据、审批和发布。”
- “Cleric、CSA ATF 和各类 eval 平台已经验证了修复验证、earned autonomy 与轨迹评测的重要性；AgentMED 直接参考这些成果。”
- “截至 2026-08-09，本轮公开样本未发现单一项目同时覆盖我们讨论中的精确整链；这只是调研范围内结论。”

不能说：

- “开源世界没人做”或“多 Agent 质量治理为零”；
- “某项目不会进入我们的领域”或“我们的市场一定更大”；
- “因为别人已经做了，所以 AgentMED 永远不自建”；
- “某标准、论文或融资事件证明 AgentMED 的产品需求和统计方案已经成立”。

## 5. 对实现的直接帮助

1. Langfuse 作为第一个 TraceSource 和 AgentMED 自身可观测后端；
2. OTel 作为 provider-neutral 的传播与采集基础；
3. 评测平台作为可插拔 Eval Provider，AgentMED 负责证据与发布绑定；
4. Cleric/CSA 的按问题类自治与降级语义作为 Trust Ledger 校准参考；
5. 现有 sandbox、replay、OAuth、策略引擎优先做兼容性评估；
6. 需要自行实现的部分，以目标用户的闭环、确定性状态、失败语义和私有部署要求为依据。

## 6. 仍需补的国内参考

本轮对国内项目的公开样本不完整，不能据此得出“国内没有人做”的绝对结论。后续应按目标用户工作流继续调查：国内 Agent 平台、LLMOps/可观测、评测、私有化 trace、审批发布与飞书/企业微信集成；结果用于兼容与需求设计，不用于制造竞争叙事。
