# CaseLoop

面向 AI Agent 的开源质量与变更治理控制面。

当一个 Agent 在线上答错、调错工具或完成了错误动作，团队通常要在 trace、日志、评测、配置和发布系统之间手工拼证据：哪里坏了、改什么、是否真的修好、能不能发布，很难形成一条可信闭环。

CaseLoop 希望把这件事变成一个可审计的治理流程：接收质量信号，绑定 Agent run/trace，形成案件，提出并验证归因假设，起草候选修复，经过评测门禁和人工审批后发布或回滚，再把结果沉淀为回归资产和信任证据。AI 负责分析与起草，确定性控制面掌握状态、权限、审批和发布。

当前仓库用「小智客服」跑第一条纵向参考链路。投诉、飞书回复、prompt/知识库/模型三层归因和固定六角色都是这个场景的实现，**不是 CaseLoop 的最终产品边界，也不是通用 Agent 接入已经完成的证明**。v4 产品与渐进施工路线已经批准，Stage 0 已完成 PRD、架构决策和 `contracts/v4` 的 contract-only 冻结；Langfuse、专业 Coding Team、Claude Code Runtime Adapter、数据库 migration 与 runtime 仍未实现。

CaseLoop 不以击败某个闭源产品为目标。Langfuse、OpenTelemetry、Phoenix、eval 框架、Agent runtime 和 sandbox 都可以是参考或适配对象；是否集成、复用或自行实现，以目标用户需求、可靠性、私有部署、许可证和维护成本决定。完整原则见 [`docs/product-principles.md`](docs/product-principles.md)。

正式目标见 [PRD v2](docs/prd-v2.md)，架构见 [plan v4](docs/plan-v4.md)，文件、migration、测试、live、回滚与提交边界见 [v4 施工台账](docs/plans/v4-progressive-delivery.md)。

### 现在 AgentTeams 里是什么

现有 `caseloop-team` 是六个 CoPaw + StepFun `step-3.7-flash` 的质量治理 Worker：质量官、采集员、归因师、修复师、守门员和案例官。最近运行快照中六个均为 `Sleeping`，`leaderReady=false`、`readyWorkers=0`；Team CR 显示 `Active` 不等于 Agent 正在工作。Human approver 不是第七个 Agent。

新 `caseloop-coding-team`（planner/generator/reviewer）、Claude Code child Attempt 和 GLM-5.2 主模型都只是已经批准的施工目标，当前分别是 `NOT CREATED / NOT IMPLEMENTED / NOT VERIFIED`。

## 历史 Phase 1 演示记录

`evidence/phase1/` 保留了 2026-08-08 三个早期演示场景的日志、digest 与截图。它们是历史材料，**不是**当前 P0-4 所要求的、可由 manifest 独立重验的 live-provider B1 纵向闭环证明；当前结论只以 `evidence/p0/p0-4-b1/`、`evidence/p0/p0-4-b1-live/` 和 `docs/context/PROJECT_STATE.md` 为准。

| 场景 | 历史注入内容 | 早期材料记录 |
|------|------------|-----------|
| 售后政策矛盾 | 改掉 prompt，让客服坚称「激活不可退」 | 全链路首次走通，修复后实测答复恢复正确 |
| 同上，但换措辞 | 用剧本之外的话术投诉同一件事 | 零人工纠偏走完全程 |
| 知识库过时 | 把 KB 里某耳机续航从 30 小时改成 8 小时 | 保留于历史 `evidence/phase1/` 材料 |

这些场景仍适合说明产品目标，但旧截图或房间日志不能直接升级成当前契约下的验收证据。只有当要证明新的代码/provider 路径、满足不同 evidence contract，或旧材料无法独立复验时才需要重跑；不能因为时间过去了就把已发生的 live 历史作废。

## 为什么敢让 AI 干这个

答案是：不完全敢。而且这个「不敢」被写进了代码里。

- **信任账本**。每个真实 release outcome 通过 outbox 记为一次行动样本。即使 3/3 成功，Wilson 双侧 95% 下界也约为 0.4385，低于 0.9，确定性策略仍会**拒绝晋升**。
- **高风险操作永远逐次人审**。审批批的是 WorkOrder 的 hash——换一个字节都过不了闸。
- **live 门禁要求裁判和运动员不是同一个模型**。缺失独立裁判身份、digest 不可验证或二者相同时，必须 fail closed。

## 架构一句话

**确定性控制面 + 概率性执行面。** LLM 出主意，系统管规矩；LLM 永远不是状态和权限的权威源，一切分歧以控制面数据库和实验数据裁决。

```
质量信号 → 案件(非 LLM，去重幂等) → Agent Run / Trace 取证 → 假设与对照实验
        → 候选修复 → 双轨评测门禁 → 人工审批(批 hash)
        → 灰度发布 / 回滚 → 场景化 Closure → 回归资产与信任记账
```

当前参考实现技术栈：FastAPI + pgvector（演示应用与案例库）、自研控制面（PG 事件溯源）、MCP（内部 Agent 工具面）、AgentTeams（当前质量协作适配器）、StepFun `step-3.7-flash`（现有质量 Team provider）、React 控制台。

## 跑起来看看

```bash
# 业务栈：演示应用 + 控制面 + 固定 outbox-dispatcher + 控制台 + Postgres
cd deploy && docker compose up -d

# 控制台：http://127.0.0.1:8088 （案例、实验、审批、账本可视化）
# 控制面 API：:18090 ｜ 演示应用：:8080
```

这条 Compose 命令不会启动或证明 AgentTeams 六个 Worker 活跃，也不会证明 Langfuse、Claude Code 或 coding Team 已接通。

多 Agent 编排层（AgentTeams）是当前可选参考适配器，历史部署说明见 [deploy/README.md](deploy/README.md)，版本化平台边界见 [AgentTeams Wiki](wiki/platform-agentteams.md)。
想接着开发，先读 [PROJECT_STATE](docs/context/PROJECT_STATE.md)（当前事实）、[PLANS](PLANS.md)（工作与阻塞）和 [Wiki 索引](wiki/INDEX.md)（产品与施工知识）。

## 仓库里有什么

| 目录 | 内容 |
|------|------|
| `demo-app/` | 演示应用「小智客服」：FastAPI RAG，prompt git 版本化，B1–B4 故障注入端点 |
| `control-plane/` | 确定性控制面：立案 / 实验 / 发布控制器，PG 事件溯源，唯一事实源 |
| `eval-harness/` | 回归评测集、双轨跑分、对照实验执行器 |
| `mcp-servers/` | agent 工具面：5 个 MCP Server + 信任账本 |
| `agents/` | Phase 1 固定六角色 warm pool 与角色 SOUL；未声称动态扩缩容 |
| `contracts/` | Quality API 契约、事件/状态定义、conformance 套件 |
| `console/` | 治理控制台前端 |
| `evidence/` | 历史 Phase 1 材料与当前 P0 replay/live 分离证据；是否完成以各 manifest 验证结果为准 |
| `docs/` | 产品原则、当前 v3 参考实现蓝图、PRD/SPEC、研究与比赛历史材料 |

## 诚实的边界

当前仓库源自黑客松期间的构建，但项目目标已经明确为长期维护的开源项目；比赛来源不决定产品范围。当前实现仍是 **pre-production reference implementation**，不是生产系统。可重复的 `make demo-b1-replay` 使用明确标注的 replay/feishu-mock adapter；它不会被算作 live-provider 成功。

验收至少分两条轴：`domain-provider-live` 证明真实外部 provider 或领域调用，`agent-causal` 证明真实 Worker claim、模型/Skill/MCP receipt、动作前 Proposal 和 Controller causation。`platform evidence export` 只是对 Matrix/MinIO/CR 的只读取证类别，不是第三种成功，也不能满足 `agent-causal`。缺少凭证、服务或可验证回执时必须 fail closed 并报告 BLOCKED。当前可核实的测试数量与 live 状态以 [PROJECT_STATE](docs/context/PROJECT_STATE.md) 及对应 Evidence Bundle 为准，不再用历史总数宣称「全绿」。

当前工作与阻塞以 [PLANS](PLANS.md) 为准；[施工指南](wiki/build-guide.md) 保留了 G1–G17 历史审计快照和复验纪律，欢迎来看，也欢迎来挑。

## License

Apache License 2.0
