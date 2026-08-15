# AgentMED Wiki —— 产品与施工知识库

> 读者：贡献者、维护者，以及参与施工的 Agent。
> 用途：提供「完成当前任务所需的最小上下文」；本 Wiki 是导航与蒸馏，不是独立权威源。
> 维护：发现已验证的新事实、平台限制或契约变化后，更新对应页面并注明适用版本或日期。

开始任何工作前，先读 [文档权威索引](../docs/README.md)、[产品原则](../docs/product-principles.md)、[仓库工程规则](../AGENTS.md) 和任务涉及的正式 plan。当前 Wiki 同时保留 v3 客服参考纵切、V4 S1A 已实现边界，并导航已接受的 V5 施工基线与当前 repair；不能把 contract、目标架构、参考场景、研究结论或历史比赛叙事写成已实现能力。

## 页面地图

| 页面 | 类型 | 内容 | 什么时候必读 |
|---|---|---|---|
| [项目简介](project-brief.md) | Overview | 通用产品方向、当前客服参考纵切、已实现与待决范围 | 任何任务 |
| [决策卡](decisions.md) | Reference | v3 的 T0–T10、产品 P 系列与 v4 已批准施工决策 | 写架构、PRD 或对外材料前 |
| [本机环境](environment.md) | Reference | 可变环境快照、端口、凭证与 live/replay 纪律 | 本地运行或排障前 |
| [AgentTeams 实测](platform-agentteams.md) | Explanation | v1.2.1 事实、当前六 Worker 快照、双 Team 目标、MCP/Matrix 边界和证据限制 | 碰 `agents/`、AgentTeams、Matrix 或 MCP 时 |
| [契约地图](contracts-map.md) | Reference | 当前 v3 实施契约、v4 target contracts 与已完成的 S1A no-trace runtime 子集 | 改 API、事件、Schema、Gate 或 evidence 前 |
| [施工指南](build-guide.md) | How-to | 分工、Git、安全、验证、live 与历史运维地雷 | 任何代码或运行任务 |
| [术语表](glossary.md) | Reference | 被治理 Agent、内部 Worker、Controller、TraceSource 等术语 | 名词或角色边界不清时 |
| [v4 施工路线速查](v4-execution-map.md) | Approved roadmap | 通用 Signal、双 Team、Claude Code 子 Attempt、CLI/MCP、Skill 自进化、阿里云与阶段出口 | 任何 v4 施工任务 |
| [V5 PRD](../docs/prd-v5.md) / [V5 plan](../docs/plan-v5.md) | Accepted target | AIApplication、SystemVersionSet、系统证据/测评、Agent-native 入口与 V4 兼容 | 改 V5 产品或架构前 |
| [V5 Master Execution Plan](../docs/plans/v5-master-execution-plan.md) | Current execution | work package、依赖、测试、evidence、stop gate、提交边界 | 任何 V5 施工任务 |

## 权威层级

1. **产品定位、范围与口径**：[产品原则](../docs/product-principles.md)。
2. **施工目标与依赖顺序**：[plan-v5](../docs/plan-v5.md) 与 [V5 progressive blueprint](../docs/plans/v5-progressive-delivery.md) 是 V5 已接受目标；[Master Execution Plan](../docs/plans/v5-master-execution-plan.md) 编排当前执行。plan-v4 保留为 V4 兼容性基线。
3. **当前可执行实现要求**：[仓库工程规则](../AGENTS.md)、[plan-v3](../docs/plan-v3.md)、现有 [`contracts/`](../contracts/)、migrations 与可执行测试继续约束已实现的 v3 客服 Scenario Pack，直到被明确的 v4 contract、migration 和测试替换。当前 Phase 0B 契约已经落地；待定项见 [OPEN-QUESTIONS](../contracts/OPEN-QUESTIONS.md)。
4. **当前运行事实**：代码、PostgreSQL 权威记录和可复验证据。工作状态看 [PLANS](../PLANS.md)、[PROJECT_STATE](../docs/context/PROJECT_STATE.md) 与 [LAST_HANDOFF](../docs/context/LAST_HANDOFF.md)。
5. **研究、比赛与交接材料**：有日期的参考快照，只能提供证据、约束或历史背景，不能覆盖前四层。

发生冲突时，不在 Wiki 中自行“猜一个答案”：先标明冲突，再回到对应权威层裁决。

## 当前活动工作

- D-013 已确认 V5 产品边界：治理完整 AIApplication，并让其他 Agent 通过同一治理内核调用。V5-0B/0C contract freeze 已接受；V5-1A/B/C 当前仍是未提交的 repair/closure worktree，不能标 stage `DONE`。
- V4 Stage 0、Stage 1 Entry 与 S1A 已封板。S1A 只证明本地 authenticated no-trace intake，不证明 provider/Agent/live；V4 S1B–S7 冻结，V5-2+ 尚未实现。
- 最近核验的 `agentmed-team` 是六个 CoPaw / StepFun `step-3.7-flash` 质量治理 Worker，全部为 `Sleeping`，`leaderReady=false`、`readyWorkers=0`；运行前须重新查询。AgentTeams 中没有 Claude Code Agent 或 GLM Agent。
- 新 `agentmed-coding-team` 已进入施工目标，但当前为 `NOT CREATED / NOT RUN`；不能把现有客服质量 Worker 改名成专业 Coding Team。
- 两条 live 验收轴必须分开：`domain-provider-live` 与 `agent-causal`。`platform evidence export` 是只读取证类别，不能冒充真实 Agent 因果执行。
- Langfuse 双向接入、Claude Code Runtime Adapter 和真实 GitHub coding workload 均尚未由实现与证据证明；GitHub 留言、认领、fork、push、PR 仍未授权，执行前逐次询问用户。
- 小智客服、StepFun、飞书和 AgentTeams 是当前参考实现或适配器，不是所有部署必须采用的产品身份。

[返回仓库 README](../README.md)
