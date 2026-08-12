# CaseLoop 产品原则

> 生命周期标记：**AUTHORITATIVE** ｜ 本文属于当前权威层（产品定位、范围取舍与对外口径的上位原则），不能归档；研究报告、比赛材料与历史快照不得覆盖本文。

> 状态：**V5 是全部新产品/领域开发的默认设计与施工基线；runtime 仅按证据分阶段成立** ｜ 生效日期：2026-08-10 ｜ D-015 当前裁决：2026-08-11
>
> 本文是产品定位、范围取舍与对外口径的上位原则。V5 产品转向由 `docs/decisions/D-013-v5-ai-system-governance-and-agent-native-control-plane.md` 记录；D-015 进一步裁决 `docs/prd-v5.md`、`docs/plan-v5.md`、`docs/plans/v5-progressive-delivery.md` 与冻结的 `contracts/v5/` 为全部新产品和领域开发的默认设计与施工基线。V4 只保留为兼容基线，V3 是已实现兼容 lane。该裁决不改变 public API/CLI 的默认 major，不自动启用 route/capability，也不证明完整 V5 runtime、provider、Agent、external 或 production 已实现。当前执行与阻塞以 `docs/plans/v5-master-execution-plan.md`、`PLANS.md` 和 `docs/context/` 为准；运行事实只由代码、PostgreSQL 权威记录和可复验证据证明。研究报告和比赛材料不能覆盖这些层级。

## 当前开发权威与收敛前置门

- 所有新的产品边界、领域模型、权威写路径和公共 intent 都必须先按 V5 基线设计，再按显式 migration、route、capability、测试和证据施工；不得从 V3/V4 历史正文继续派生新的默认领域语义。
- V4 继续约束仍在服务的 V4 compatibility surface；V3 contracts、migrations、runtime 和可执行测试继续约束已实现兼容 lane。兼容事实只有在对应边界被显式替换后才退出，不能由计划文字追溯改写。
- 在架构收敛关闭前暂停 D2、R3、R4 与 V5-2+。收敛目标是模块化单体，每个权威业务事务只使用一个 PostgreSQL unit of work；compat/shared/facade/projection/transport/adapter 只能复用、翻译或投影，不能拥有领域成功。
- 每一轮收敛必须行为保持：public wire、默认 major、route/capability 开关、授权顺序、错误、事务、event/outbox/receipt/audit、幂等与 replay 语义不得漂移。结构迁移与新领域能力、contract 变更必须分轮施工和验收。

## 1. 产品身份

CaseLoop 最终要做成一个长期维护的、**面向 AI 应用的 Agent-native 治理运营控制面**。

它服务的是已经在开发或运行 AI Agent / LLM 应用、需要处理坏结果、验证修复和控制发布风险的团队。被治理的顶层对象是完整 `AIApplication`，Agent 是其中一种一等 `SystemComponent`；应用代码、模型、Prompt、RAG、Skill/MCP/tool、Policy、Memory 与 Runtime 都可以成为系统版本、归因、评测和变更的一部分。

它的第一用户价值不是要求维护者先完成一套治理登记，而是把一条模糊反馈或 Issue
变成**有来源、可复现、可判定的 bad case**：说明当时运行了什么、期望行为由谁确认、
候选是否真正解决问题，以及下一步还缺什么证据。版本、评测、审批、发布和恢复内核
都服务于这条用户旅程，不能反过来成为首用门槛。

CaseLoop 同时提供两类产品表面：人类 Console 用于查看、审批、干预、复盘和审计；HTTP、CLI、MCP、A2A 与 SDK 让 CI、客户应用和其他 Agent 把 CaseLoop 作为自身治理能力调用。Console 和 Agent-native 入口必须复用同一 canonical intent 与 PostgreSQL 权威内核，不能形成两套业务语义。

小智客服是当前第一个参考工作负载，用来跑通纵向闭环；它不是 CaseLoop 的最终产品边界，也不代表通用 Agent 或 AI 应用系统接入已经完成。

我们的目标不是击败某个闭源产品，也不是为了寻找“市场空白”而决定功能。需求是否进入 CaseLoop，首先看它是否解决目标用户的真实工作，而不是看同类产品有没有做、会不会功能重叠。

## 2. 用户需求决定范围

产品范围按下面的顺序判断：

1. 目标用户是否确实需要这项能力；
2. 这项能力是否属于从 AI 应用资产与版本、质量信号、取证、归因、候选修复、评测门禁到发布、恢复与复盘的治理闭环；
3. 它是否能在开源、自托管和可维护的前提下可靠交付；
4. 它是否减少用户定义问题、拼接证据和确认修复的净工作，而不是只增加登记与审批；
5. 它与现有组件应当集成、复用还是由 CaseLoop 自己实现。

“已有项目做过”不是删除需求的理由。成熟实现可以成为设计参考、兼容对象或依赖；如果目标用户仍需要统一体验、私有部署、国内适配、确定性治理或长期可维护性，CaseLoop 可以实现自己的兼容组件。

## 3. 与其他项目的关系

Langfuse、OpenTelemetry、Phoenix、各类 eval 框架、Agent runtime、sandbox、身份与策略系统，以及 CMDB、ITSM、SRE、CI/CD、供应链和 FinOps 系统，首先是可学习、可集成的相关项目，不是用来反向定义 CaseLoop 边界的“敌人”。

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
- 期望配置、实际运行状态和已经发生的外部副作用必须分开记录，任一层都不能由另一层推断为成功；
- 发布必须绑定确切且不可变的 WorkOrder、审批、GateReport、版本、nonce 与有效期；
- 对拟创建或执行 WorkOrder 的每项 required/applicable evidence，`FAILED`、
  `INCONCLUSIVE`、`ERROR`、`UNKNOWN`、缺失或不匹配都必须 fail closed；预先有理由声明的
  `N/A` 和 optional/advisory Finding 必须单独显示，不能被提升成 required PASS；
- fail closed 约束的是可执行 WorkOrder 与外部动作授权，不把调查中的 `INCONCLUSIVE/UNKNOWN`
  伪装成 Case 失败或产品死路；系统必须返回补充验收标准、补证、增加样本或重新评测的
  明确下一步，人类不能直接覆盖一份失败 Gate 后执行发布；
- evidence 只使用 `contract`、`replay`、`domain-provider-live`、`agentteams-native`、`claude-runtime-live`、`agent-causal`、`repo-sandbox`、`human-authorized-external`、`production-canary` 这组 canonical facets；mock 和 platform export 不是成功 facet；
- 证据导出器只能证明它实际观察和导出的事实，不能补造 Agent 执行、人工审批或因果关系。

这些约束不是为了制造产品差异，而是为了让开源项目值得用户信任。

## 7. 当前已确认的 V5 方向

以下是需求讨论已经确认的方向，**不表示仓库已经实现**：

1. 核心治理层级为 `Workspace → Project → AIApplication → Environment → SystemComponent`；Agent 是组件类型，不再承载完整应用系统边界；
2. `SystemVersionSet` 必须不可变地绑定会改变 AI 行为、权限或证据解释的组件；无法固定的远程依赖明确标记 assurance 与 `UNKNOWN`，不能伪装成 exact binding；
3. `SystemEpisodeView` 用于关联一次用户体验或业务过程中的应用、Agent、模型、RAG、工具和外部作用证据；单个 trace/span 只是其来源之一。Gate/归因必须绑定带 exact receipt set、assignment generation 和 watermark 的不可变 `SystemEpisodeSnapshot`，不能绑定会变化的 view；
4. CaseLoop 自身应接入标准化可观测链路；被治理系统通过可插拔 Evidence Source 读取 Langfuse、OpenTelemetry、Phoenix、CI、repo、runtime 或其他来源。Langfuse 是适配器，不是权威状态库；
5. 进入 Case、归因和 Gate 的证据需要被固化、去重、校验完整性并绑定 digest。采集范围、脱敏、采样、丢失、留存和权限不足必须显式表达为 `PARTIAL/UNKNOWN`；
6. 外部 Agent、CaseLoop 内部 Worker、被治理 Agent、runtime session、provider/model、确定性 Controller/Executor 和只读 Exporter 是不同身份，权限、作者和 receipt 必须分别记录；
7. Canonical HTTP intent 是能力基线；CLI、MCP、A2A、SDK 和 Console 是薄 Adapter。外部 Agent 可以报告、调查、提交候选、请求测评和请求动作，但不能 human approve 或取得内部 release execute authority；
8. Durable Work、不可变 Candidate/Evaluation/Gate，以及在请求部署时使用的 pre-Gate
   ReleasePlan、精确 WorkOrder/人类审批、幂等外部动作、`UNKNOWN` reconcile、rollback 和
   compensation 是 V5 必须继承的治理内核；verification-only PASS 不产生 WorkOrder，
   V4 closed schema 通过同 owner 的 schema-major-2 system profile 泛化，不能原位继承；
9. 当前 `caseloop-team`、计划中的 `caseloop-coding-team`、AgentTeams、Claude Code 和具体模型都降为可替换的参考 Worker/Runtime/Client Adapter，不是 V5 内核或用户必装依赖；
10. 通用 CMDB、观测存储、Agent runtime、CI/CD、IAM、ITSM、账单和 GRC 平台通过 Adapter 对接。CaseLoop 自身持有 AI 系统版本、质量案件、证据绑定、变更授权、执行对账和恢复事实。
11. First Useful Case 必须记录验收标准来源；来源不足时诚实显示
    `NEEDS_ACCEPTANCE_CRITERIA`，而不是让 Agent 从 Issue 文本自动制造金标准；
12. 首批同时支持轻量的代码 Issue 验证路径与完整的 AI 行为治理路径。代码库或离线库可在
    Verified Candidate 结束；只有存在真实部署面的应用才要求 release、observed 与 rollback
    证明。两条路径共享 Case、VersionSet、Evidence 和 Gate 内核，不形成两套产品。

## 8. 文档与对外表述纪律

- 不再用“竞品威胁”“抢窗口”“红海/真空决定范围”指导产品；
- 不使用“没人做”“唯一”“全面合规”“所有 Agent 已可接入”等未经验证的绝对表述；
- 已有项目做得好的能力，应明确写成参考、复用或兼容对象；
- 当前实现、历史演示、研究推断、已确认需求和待决问题必须分栏表达；
- V5 产品边界已由 D-013 记录；D-015 将 V5 PRD/plan/blueprint/frozen contracts 确立为全部新产品与领域开发的默认设计和施工基线。该裁决不追溯改写 V3/V4 兼容事实，也不自动改变 public default、启用 route/capability 或证明 runtime 已完成；尚未被 migration、runtime、测试和证据替换的运行边界仍由对应兼容 lane 约束。
