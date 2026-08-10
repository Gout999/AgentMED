# AgentTeams v1.2.1 适配器实测

[返回 Wiki 索引](INDEX.md)

> 适用范围：2026-08-07 至 2026-08-10 的本地 AgentTeams v1.2.1 profile，以及当前 v3 客服参考实现和已批准的 v4 Adapter 目标。执行前复核实际安装版本与运行状态。AgentTeams 是可替换的内部协作适配器，不是 CaseLoop 产品身份，也不是被治理 Agent 必须使用的 runtime。

本页中的 `Worker` 专指 CaseLoop 内部 LLM Worker。它不同于被治理 Agent，也不同于 outbox dispatcher、evaluator 等确定性进程。

## 当前运行快照（2026-08-10）

最近一次本地查询中，`caseloop-team` 的 Team CR 为 `Active`，但 `leaderReady=false`、`readyWorkers=0`；六个 Worker 都是 CoPaw runtime、StepFun `step-3.7-flash`，状态均为 `Sleeping`。容器停睡是运行快照，不是删除，也不是动态缩容证明。

| Worker | 职责 | Runtime / model | 实测状态 |
|---|---|---|---|
| `quality-officer` | Team Leader、分诊、协调、升级 | CoPaw / `step-3.7-flash` | `Sleeping` |
| `collector` | 日志/反馈取证与 badcase | CoPaw / `step-3.7-flash` | `Sleeping` |
| `attributionist` | 实验计划与归因建议 | CoPaw / `step-3.7-flash` | `Sleeping` |
| `repairer` | 候选修复与 WorkOrder 草拟 | CoPaw / `step-3.7-flash` | `Sleeping` |
| `gatekeeper` | 评测与放行建议 | CoPaw / `step-3.7-flash` | `Sleeping` |
| `case-officer` | 归档、回归资产与通知 | CoPaw / `step-3.7-flash` | `Sleeping` |

仓库 [agents/team.yaml](../agents/team.yaml) 的 `spec.state: Running` 是 desired spec，Team CR 的 `Active` 是资源状态；二者都不能证明某个 Agent 当前活跃、读取了任务或调用了模型。运行/验收前必须重新查询 Worker、容器和 session。

`caseloop-approver` 是 Human，不是第七个 Agent。当前 AgentTeams 中没有 Claude Code Agent，也没有 GLM Agent；本机 CLI、CCR 配置或 provider 条目存在都不能改变这个事实。

## 已确认的产品边界

- Phase 1 的六个 Worker CR 静态存在于 [agents/team.yaml](../agents/team.yaml)；没有动态扩缩证据。
- AgentTeams v1.2.1 没有原生 autoscaling 或 CaseLoop 所需的 durable business queue。
- 仓库中当前没有 v3 规划的 Caseload Controller 实现；已批准的 v4 目标改为 PostgreSQL Work Kernel、WorkerTask/Attempt 与 Runtime Adapter，仍待 Stage 0 contracts 和后续实现证明。
- Kine、Matrix 和 MinIO 都不是 CaseLoop 生命周期、审批、发布或审计的权威源。PostgreSQL 控制面仍是唯一业务权威。
- 平台资源、消息和工件存在，不自动证明内部 Agent 真实执行或对后续动作有因果贡献。

## 证据语义

| 记录 | 能证明 | 不能证明 |
|---|---|---|
| Team / Worker CR | 平台接受了资源声明 | Worker 正在运行、读取任务或调用模型 |
| Matrix event | 某身份成功发送一条消息 | 接收 Worker 阅读、推理或创作了内容 |
| MinIO object + digest | 指定字节被写入并可回读 | 工件由某 Agent 创作或影响后续决策 |
| exporter 签名 | 回执字节完整且签发者明确 | 回执中的 Agent 因果声明真实 |
| Kine 记录 | AgentTeams 资源状态 | CaseLoop Case / Release / Approval / audit 状态 |
| Worker session + model/tool receipts | 对应调用在该 session 中发生 | 仍需 pre-action proposal 和权威 causation binding 才能证明业务贡献 |

因此，Matrix/MinIO 导出只能叫 `platform evidence export`。`agent-causal` 还必须证明 Worker 原生接单、模型/Skill/MCP 调用、动作前 Proposal、作者身份与后续权威事件之间的因果绑定。

### 两条 live 验收轴

| 验收轴 | 必须证明 |
|---|---|
| `domain-provider-live` | 本次声明的 StepFun、Langfuse、飞书、GitHub 或其他外部边界确实被真实调用，并保存可核验 provider receipt |
| `agent-causal` | Controller task/outbox → Runtime 真唤醒 → Worker 原生 claim → 模型/Skill/MCP receipt → 动作前 Proposal → Controller causation |

`platform evidence export` 不是第三条成功轨。它可以支持取证，但不能单独满足 `agent-causal`。六个 Worker 全部休眠时，`agent-causal` 验收必须失败或 `BLOCKED`；否则测试没有证明真实 Worker 因果参与。

## v4 双 Team 目标（批准，尚未实现）

v4 保留现有 `caseloop-team` 作为质量治理 Team，另建专业 `caseloop-coding-team`：

| 目标角色 | AgentTeams / Runtime 边界 | 当前状态 |
|---|---|---|
| `coding-planner` | 父 Worker；目标模型 GLM-5.2，必须先以官方 origin smoke 和 assignment receipt 证明 | `NOT CREATED` |
| `coding-generator` | 父 Worker 先 claim 并提交 `DelegationProposal`；获 Controller 接受后，由宿主 Claude Code Runtime Adapter 启动隔离 child Attempt | `NOT CREATED` |
| `coding-reviewer` | 独立模型轨操作 sandbox、提交 Finding；确定性 Eval Runner/Gate 不属于 Agent Team | `NOT CREATED` |

Claude Code 不是 AgentTeams v1.2.1 当前已部署的原生 Worker，也不能在 Manager 身份下代跑后冒充 Worker。父 Worker、Claude child Attempt、模型 provider、Controller 与 Repo Executor 必须分别记录身份、receipt、权限和 causation。详细链路见 [v4 施工路线](v4-execution-map.md#agentteams-父-worker-与-claude-code-子-attempt)。

## 安装快照（v1.2.1）

- 安装脚本支持 `openai-compat` provider 和自定义 OpenAI-compatible base URL。
- `AGENTTEAMS_DATA_DIR` 在该版本被当成 Docker volume 名；传绝对路径会失败。
- 已存在的 manager env 可能让非交互安装进入升级分支；重装或卸载属于破坏性操作，必须先核对目标和用户授权。
- 内嵌组件包括 controller、Higress、Tuwunel/Matrix、MinIO、Element Web 和 Go controller。

参考端口见 [environment.md](environment.md)。安装命令和凭证来源应以当前官方版本及本地 runbook 为准，不能复制本页快照中的旧假设。

## 认证与管理边界

- controller REST 需要受保护的 bearer token；Matrix 管理面还存在高权限 AppService token。两者都不得写入日志、Wiki、evidence 或 Agent 工件。
- AppService 可以代表用户执行管理操作，这只适合受控诊断与恢复。管理员代发、代 ack 或代 submit 不能记为 Worker 自己的行为。
- Exporter 的目标权限模型应是只读：无 Matrix/MinIO/control-plane 写权限。需要平台写入的调试工具不能兼任独立证据采集器。
- controller 业务日志与 Docker supervisord 输出不是同一来源；诊断时要记录实际读取的日志位置和版本。

## CR 行为（v1beta1，实测快照）

- `agt apply -f` 按文档顺序执行，不做拓扑排序；定义文件必须按依赖顺序组织。
- Team 的 `workerMembers` 必须且只能有一个 `team_leader`；PUT 不允许清空。
- 删除普通成员前要先从 Team 摘除；Team / Worker 删除是 reconcile 流程，CLI 返回成功不代表底层资源已经消失。
- 删除完成应回查 CR、容器、Matrix 房间和 MinIO 用户，不能只看一条 API 响应。

### S0-001：Team 删除假成功与 Leader detach 死锁

历史证据位于 `evidence/spike/S0-001-*`。当 manager 已在 Worker personal room 时，重复 invite 返回 403，v1.2.1 controller 未将“已经是目标状态”视为幂等成功，导致 reconcile 无限重试。

这项缺陷只是未来 Worker 生命周期控制的设计输入，不代表 Caseload Controller 已经实现。若当前版本仍需删除资源，先摘普通成员、最后处理 leader，并完成四类资源回查。

## 存储

- controller 的 CR 持久化使用 Kine / SQLite，历史路径为 `/data/agentteams-controller/agentteams.db`，删除以 tombstone 表示。
- Matrix 保存协作消息，MinIO 保存 Worker workspace / artifact；两者只服务适配器运行和取证。
- CaseLoop 的 Case、Gate、Approval、Release、Trust 和 audit 不得从 Kine/Matrix/MinIO 反推或覆盖。

## 消息、Skill 与 session

以下行为含 2026-08-08 历史观察，使用前复验：

- 群聊任务需要正确的 `m.mentions` 和完整 Worker MXID；仅在正文里写 `@名字` 不能保证投递。
- 内部 Worker 的单线程 ReAct loop 可能在忙时排队新消息；重启窗口存在消息已推进 sync token 但未处理的风险。
- Skill 文件可经 MinIO workspace 同步到 Worker；文件出现只证明同步，不证明 Skill 被发现、选择或调用。
- v1.2.1 Worker 镜像中未确认可用的 `agentteams-sync`，且历史 `copaw-sync` 指向缺失脚本；不要把即时同步命令写成保证，须回查实际 Worker 文件与运行日志。
- session 损坏时的 `/new` 属于适配器操作，不是 CaseLoop 的业务重试、幂等或恢复语义。

## MCP 注册与调用（Higress，实测快照）

历史配置路径是：创建 service source → 创建 MCP proxy → 全量设置 `allowedConsumers` → 把受权 endpoint 和 consumer token 写入对应 Worker 配置 → 等待并验证 MinIO 同步。

已知风险：

- `allowedConsumers` 是全量替换语义；遗漏已有 consumer 会撤销其权限。
- bearer 值从 env 文件读取时可能含引号或换行；不得把清洗前后的 secret 打进日志。
- Higress 可能原样透传 `/mcp-servers/<name>/mcp`；上游 FastMCP 需要明确的 PathRewrite。
- `mcporter` 按当前工作目录解析相对配置；必须在 Worker 真正执行目录验证，而不是只看 pid 1 的 cwd。
- MCP 工具调用成功只证明工具路径可用；要宣称 Agent 使用了它，还需 Worker session 与调用 receipt。

完整受信 smoke 用法见 [mcp-servers/README.md](../mcp-servers/README.md)。

## 当前未提交 exporter 路径的已知冲突

截至 2026-08-10，工作树中的 `scripts/b1_live/agent_trace.py` 明确不通过 `m.mentions` 唤醒六个 LLM Worker，而是由 evidence exporter 根据 runner context 生成角色 payload、ack/submit 与 Matrix/MinIO 记录。与此同时，runner 仍可能把结果标成 `agent_runtime_executed=true`。

这条未提交路径只能证明 exporter 实际执行的平台写入与回读，不能证明六个 Worker 真实执行。它不否定更早的 live 历史，但在语义冲突解决前不得合入或作为 `agent-causal` 验收依据。

## 修改适配器时的验收清单

1. 钉住并记录 AgentTeams 版本；把推断与实测分栏。
2. 资源变更后回查 CR、容器、Matrix、MinIO，而不是相信“成功样式”。
3. 业务事实只写 PostgreSQL 控制面；平台状态只能作为引用或 evidence。
4. 管理员、Exporter、Worker 使用不同身份和最小权限 token。
5. `domain-provider-live` 与 `agent-causal` 独立报告；`platform evidence export` 使用不同来源类别，不能写入成功 facets。
6. 关闭、休眠或移除真实 Worker 时，`agent-causal` 验收必须失败或 `BLOCKED`；否则测试没有证明 Worker 因果参与。
7. `caseloop-coding-team` 创建前保持 `NOT CREATED`；Direct Claude Runtime 通过也不能冒充 AgentTeams 父 Worker 委托已经集成。
8. GitHub 留言、认领、fork、push 或 PR 不属于 AgentTeams smoke；这些外部写动作必须逐次取得用户授权。
