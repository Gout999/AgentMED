# CaseLoop 产品原则

> 状态：用户已确认 ｜ 生效日期：2026-08-10
>
> 本文是产品定位、范围取舍与对外口径的上位原则。v4 新开发以 `docs/plan-v4.md` 和 `contracts/v4/` target contracts 为施工基线；尚未迁移的当前实现仍以 `docs/plan-v3.md`、v3 contracts、migrations 和可执行测试为兼容基线。运行事实以代码、PostgreSQL 权威记录和可复验证据为准。研究报告和比赛材料不能覆盖这些层级。

## 1. 产品身份

CaseLoop 最终要做成一个长期维护的**开源 AI Agent 质量与变更治理项目**。

它服务的是已经在开发或运行 AI Agent / LLM 应用、需要处理坏结果、验证修复和控制发布风险的团队。小智客服是当前第一个参考工作负载，用来跑通纵向闭环；它不是 CaseLoop 的最终产品边界，也不代表通用 Agent 接入已经完成。

我们的目标不是击败某个闭源产品，也不是为了寻找“市场空白”而决定功能。需求是否进入 CaseLoop，首先看它是否解决目标用户的真实工作，而不是看同类产品有没有做、会不会功能重叠。

## 2. 用户需求决定范围

产品范围按下面的顺序判断：

1. 目标用户是否确实需要这项能力；
2. 这项能力是否属于从质量信号、取证、归因、候选修复、评测门禁到发布与复盘的治理闭环；
3. 它是否能在开源、自托管和可维护的前提下可靠交付；
4. 它与现有组件应当集成、复用还是由 CaseLoop 自己实现。

“已有项目做过”不是删除需求的理由。成熟实现可以成为设计参考、兼容对象或依赖；如果目标用户仍需要统一体验、私有部署、国内适配、确定性治理或长期可维护性，CaseLoop 可以实现自己的兼容组件。

## 3. 与其他项目的关系

Langfuse、OpenTelemetry、Phoenix、各类 eval 框架、Agent runtime、sandbox、身份与策略系统，首先是可学习、可集成的相关项目，不是用来反向定义 CaseLoop 边界的“敌人”。

CaseLoop 应当：

- 优先研究公开文档、协议、接口、数据模型和已验证的工程模式；
- 对许可证兼容的开源实现，可依法复用并保留许可证与归属说明；
- 对不适合直接复用的实现，只参考公开行为、协议和设计思想，再独立编写；
- 主动保持可互操作，避免为了“差异化”制造不兼容接口；
- 不把并购、融资、市场份额或所谓窗口期当成功能优先级依据。

## 4. 集成、复用或自行实现

每项能力按同一组工程标准裁决：

| 方案 | 适用条件 |
|---|---|
| 直接集成 | 已有项目稳定、许可证与部署方式兼容，且能满足用户的数据、权限、可靠性和维护要求 |
| 复用开源实现 | 代码质量与许可证可接受，长期维护成本低于重写，并能清楚保留来源与修改记录 |
| 兼容性重实现 | 公开契约有价值，但现有实现不适合国内环境、私有部署、数据边界或统一治理需求 |
| 自行设计 | 没有可用实现，或确定性控制、证据绑定、审计与失败语义必须由 CaseLoop 持有 |

最终选择看用户价值、可靠性、安全边界、许可证、部署兼容性和长期维护成本，不看“会不会和别人做同样的事”。

## 5. 中国用户优先，协议保持开放

CaseLoop 首先把国内团队的使用条件做好：中文文档、自托管与私有化、国内模型和云服务、飞书/企业微信等协作渠道、数据驻留与受限网络环境，都应成为一等需求。

“国内优先”不等于把核心锁死在某一家厂商。核心契约应保持 provider-neutral；StepFun、飞书、AgentTeams、Langfuse 等是当前或计划中的实现与适配器，不能被写成所有用户必须采用的产品身份。

关于“国内没有同类完整项目”的判断只能作为待持续验证的研究结论，不能写成绝对事实，也不能成为 CaseLoop 存在的唯一理由。

## 6. 不变的安全与证据边界

- PostgreSQL 控制面持有生命周期、权限、审批、发布与审计的权威状态；
- LLM 和 Agent 只能产出建议、假设与候选工件，不能自行改写权威状态；
- 发布必须绑定确切且不可变的 WorkOrder、审批、GateReport、版本、nonce 与有效期；
- `FAILED`、`INCONCLUSIVE`、`ERROR`、`UNKNOWN`、缺失或不匹配的证据都必须 fail closed；
- evidence 只使用 `contract`、`replay`、`domain-provider-live`、`agentteams-native`、`claude-runtime-live`、`agent-causal`、`repo-sandbox`、`human-authorized-external`、`production-canary` 这组 canonical facets；mock 和 platform export 不是成功 facet；
- 证据导出器只能证明它实际观察和导出的事实，不能补造 Agent 执行、人工审批或因果关系。

这些约束不是为了制造产品差异，而是为了让开源项目值得用户信任。

## 7. 当前已确认的通用化方向

以下是需求讨论已经确认的方向，**不表示仓库已经实现**：

1. CaseLoop 自身应接入标准化可观测链路，并支持把自身运行 trace 发送到 Langfuse；
2. 治理外部 Agent 时，应能通过可插拔 `TraceSource` 读取该 Agent 在 Langfuse 中的输入、输出、模型调用、工具调用和相关上下文；
3. Langfuse 是首个适配器，不是唯一后端；OpenTelemetry、Phoenix 或其他符合契约的来源应可接入；
4. trace 是取证来源，不是 CaseLoop 的权威生命周期数据库；进入案件和门禁的证据需要被固化、去重、校验完整性并绑定 digest；
5. 不能默认 Langfuse 一定拥有“全部”输入输出。接入契约必须报告采集范围、脱敏、丢失、留存和权限造成的完整性状态；证据不足时返回 `UNKNOWN`；
6. “被治理的 Agent”和“CaseLoop 内部负责分析/起草的 Agent”是两类身份，后续 PRD 与契约必须分开描述。
7. 当前 `caseloop-team` 是六个 CoPaw/StepFun 质量治理 Worker，不是专业 coding Team；v4 新增独立 `caseloop-coding-team`，不能通过重命名或事后证据把两者混为一队；
8. AgentTeams Worker principal、Claude Code runtime session、模型 provider/model、被治理 Agent 与确定性 Controller/Executor 是不同身份，权限、作者与 evidence receipt 必须分别记录；
9. Claude Code 是首个受控 coding execution harness，GLM-5.2 是目标主模型；二者都必须通过实际 runtime/provider 验证后才能写成已接通能力。

## 8. 文档与对外表述纪律

- 不再用“竞品威胁”“抢窗口”“红海/真空决定范围”指导产品；
- 不使用“没人做”“唯一”“全面合规”“所有 Agent 已可接入”等未经验证的绝对表述；
- 已有项目做得好的能力，应明确写成参考、复用或兼容对象；
- 当前实现、历史演示、研究推断、已确认需求和待决问题必须分栏表达；
- 已批准需求进入 `docs/prd-v2.md` 与 `docs/plan-v4.md` 后成为 v4 施工基线；尚未被 migration、contract 和测试替换的运行边界仍以 v3 实现基线为准。
