# CaseLoop 决策卡

[返回 Wiki 索引](INDEX.md)

> `P` 系列是用户确认的产品范围与口径决策；`T0–T10` 是当前 v3 已锁定的参考实现决策，其中部分能力尚未实现。v4 已批准施工，但只有明确的 v4 contract、migration 和测试才能替换对应 v3 实现要求；不得忽略或静默改写历史。运行结论仍须由代码、PostgreSQL 记录、测试和证据确认。

## 产品战略决策（2026-08-10）

| # | 决策 | 影响 |
|---|---|---|
| P1 | CaseLoop 是长期维护的开源 AI Agent 质量与变更治理项目 | 黑客松与比赛是历史来源，不拥有产品范围和成功标准 |
| P2 | 目标用户需求决定范围 | 竞品、融资、市场空白和功能重叠只提供参考，不拥有产品裁决权 |
| P3 | 可以集成、依法复用或兼容性重实现已有能力 | 依据用户价值、可靠性、私有部署、许可证与维护成本做选择 |
| P4 | 中国用户优先，核心契约保持 provider-neutral | 国内模型、云和协作渠道是一等适配器，但不锁死核心 |
| P5 | 客服是第一参考 workload，不是最终边界 | 通用 Agent 需求进入新版 PRD / plan / contracts 后，才成为实施基线 |
| P6 | Langfuse 同时是 CaseLoop 自身可观测后端的首个目标和外部 `TraceSource` 的首个适配器 | Langfuse 不是权威状态库，也不是唯一后端；Stage 0 已落 `AgentRunRef` / `TraceEvidenceReceipt` target schema，runtime、migration 与 live receipt 尚未实现 |
| P7 | 被治理 Agent、内部 LLM Worker、确定性 Controller / Executor 与只读 Exporter 必须分开 | 分别建模身份、权限和验收；平台证据导出不能冒充真实 Agent 因果执行 |
| P8 | [v4 全盘计划](../docs/plan-v4.md) 的 Signal、Work Kernel、typed Proposal、独立 Evaluator 与分阶段路线获批施工 | Stage 0 立即冻结 contracts/ADR；计划获批不等于功能已实现 |
| P9 | 保留现有六角色 `caseloop-team` 作为质量治理 Team，另建三角色 `caseloop-coding-team` | 不把客服 Worker 改名成 coding Agent；Coding Team 当前为 `NOT CREATED / NOT RUN` |
| P10 | AgentTeams Worker 是父委托者，Claude Code 是隔离 worktree 中的 child Attempt | 分别记录 requested-by/executor/父子 Attempt；Claude Code 不继承 Worker 长期凭据、审批或 push/merge 权限 |
| P11 | GLM-5.2 是 Coding Planner/Generator 的目标主模型，StepFun 仍可服务现有质量 Team 或低成本任务 | 模型目标必须经过真实 provider smoke 与每次 receipt 证明；当前 AgentTeams 中没有 GLM Agent |
| P12 | live 使用两条独立验收轴：`domain-provider-live` 与 `agent-causal` | `platform evidence export` 只是只读取证类别，不能单独满足 `agent-causal`；Worker 休眠/移除时后者必须失败 |
| P13 | 首个第二 workload 是 coding Agent：维护报告/issue → 本地 patch candidate → sandbox/eval → draft PR 候选 | GitHub 留言、认领、fork、push、PR 均为独立外部写动作，执行前逐次授权 |
| P14 | Skill/MCP 采用候选、隔离、评测、Gate、首次人批、固定版本、canary、rollback/revoke 路线，并接入阿里云要求 | Stage 5 前不生产安装/外部发布；阿里云 Adapter 不取代内部 PostgreSQL Registry 权威 |

完整上位原则见 [docs/product-principles.md](../docs/product-principles.md)。

## T0–T10：当前 v3 参考实现

| # | v3 决策 | 当前施工影响与边界 |
|---|---|---|
| T0 | 治理层与被治理应用通过 Quality API 分离 | 当前只证明小智客服参考纵切；通用 Signal / TraceSource target contracts 已进入 Stage 0，但 migration/runtime 尚未实现，不能据此声称任意 Agent 已可接入 |
| T1 | 小智客服 live 路径真实调用 StepFun | live 不得回落到 mock；明确命名的 unit / contract / replay fake 可以存在，但不得充当 live-provider 证据 |
| T2 | 4 常设 + 2 候选弹性角色；AgentTeams v1.2.1 无原生 autoscaling | 六个 Worker CR 是固定 warm pool 的 desired topology；最近快照六个 CoPaw/StepFun Worker 均为 `Sleeping`，不等于动态伸缩或当前活跃 |
| T3 | 事件驱动 + 轮询兜底，统一经 inbox 去重立案 | v3 投诉入口必须先过 inbox；通用版需要抽象 Signal Adapter，不能让任何来源绕过幂等立案 |
| T4 | 5-cell、效应量 + 95% CI、三态裁决 | 这是客服三组件 VersionSet 的 v3 协议；通用归因仍须保留冻结干预、区间与 UNKNOWN，但变量集合待重新定义 |
| T5 | 三通道自由起草；不可变 WorkOrder hash 绑定 | 审批对象是确切 WorkOrder hash；候选可由 Agent 起草，Controller 负责校验和 seal，落库后不可改 |
| T6 | 规则轨 + 独立裁判轨；contract/replay 与 live-provider 分开 | 当前 GateReport 已编码 rule/judge/deterministic/live-provider；v4 canonical facets 为 `domain-provider-live` 与 `agent-causal`，但尚未由当前 Schema 和实现证明 |
| T7 | 飞书对外、Matrix 对内，对外动作双向留痕 | 这是客服参考场景。Matrix 是协作投影，不是权威审计源，也不单独证明 Agent 因果执行；权威记录仍在 PostgreSQL / outbox / audit |
| T8 | Trust Ledger 为 `risk_class × autonomy_state`；一次动作一个样本 | 多探针不能膨胀样本数；R2 始终逐次审批；MVP 只证明 3/3 仍拒绝晋升 |
| T9 | 案例库 pgvector；数据库为审计权威；OTel 是可观测目标 | audit JSONL、Langfuse、Matrix、MinIO 均不能取代权威状态；当前 B1 证据曾明确 `otel_exported=false`，Langfuse 接入仍未实现 |
| T10 | Skill 只能候选 → 回放验证 → 人工批准 | 不得自动上架；通用 Agent 的 Skill / tool / prompt / model / harness 版本边界仍待新版契约裁决 |

源头和历史语义见 [docs/plan-v3.md](../docs/plan-v3.md)。不要静默改写 T0–T10 的历史含义；新决策另编号。

## 已批准路线中的待冻结施工细节

这些不是重新讨论“要不要做 v4”，而是必须由 Stage 0 contract、现场 smoke 或逐次授权给出可执行答案：

1. Signal、AgentRun、ResolutionContract、CandidateContract、WorkerTask/Attempt、Proposal/Decision、Finding、Gate、Release 与 Closure 的精确字段、owner/command/event 和 v3 cutover 语义。
2. `TraceEvidenceReceipt` 的 completeness、查询水位、去重、留存、脱敏、租户凭证与受支持 Langfuse 版本。
3. GLM-5.2 的官方 origin、结构化输出和最小真实 smoke，以及 coding reviewer 的独立 provider/model；缺失独立轨时必须 `ERROR` 或转人工。
4. 首个真实 GitHub issue 的实时 open/assignee/关联 PR/贡献规则、最新 SHA 复现与 maintainer 许可。候选选择不构成留言、认领、fork、push 或 PR 授权。
5. 阿里云 SLS Skill、MSE AI Registry 和 AISC 的现场版本、权限、付费、配额、凭据隔离与完整终态证据。

完整状态与施工出口见 [v4 施工路线速查](v4-execution-map.md)。

## 历史比赛材料

- 当时比赛材料要求突出 AgentTeams 映射；这不是长期产品文档隐藏适配器可替换性的理由。
- “Agent 申请、控制面决策执行”只描述安全的扩缩责任边界，不证明动态扩缩已经实现。
- 飞书 mock 是明示的 contract/replay 路径，不是真实飞书或真人动作证据。
