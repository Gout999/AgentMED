# CaseLoop 术语表

[返回 Wiki 索引](INDEX.md)

| 术语 | 含义 |
|---|---|
| 控制面 | 非 LLM 的确定性系统。PostgreSQL 中的生命周期、权限、幂等、审批、发布与审计记录是权威事实 |
| 被治理 Agent | CaseLoop 外部的治理对象；它产生业务运行、输出和 trace，不等同于 CaseLoop 内部 Worker |
| CaseLoop 内部 LLM Worker | 质量官、采集员、归因师、修复师、守门员、案例官等概率性执行角色，只能产出建议、假设和候选工件 |
| 确定性进程 Worker | outbox dispatcher、evaluator runner 等按代码规则执行工作的进程；名字含 Worker 也不表示它是 LLM Agent |
| Controller / Executor | 校验命令、持久化状态、编排获批动作或调用唯一写面的确定性组件；不能伪装成 Agent 判断 |
| Evidence Exporter | 只读采集来源记录、保留 provenance，并明确区分 PostgreSQL 权威事实与 Matrix/MinIO 等平台证据；不能创建任务、ack/submit、审批、工件或因果关系 |
| 概率性执行面 | CaseLoop 内部 LLM Worker 的建议和候选生成侧；不是权威状态机，也不是所有执行进程的总称 |
| Case Controller | v3 控制面核心：inbox 去重立案、派单、lease、fencing、幂等与 outbox |
| Release Controller | 唯一允许调用 Quality API 写面的组件；以 CAS 执行 stage、canary、promote、rollback 和 reconcile |
| Caseload Controller | 当前 v3 Phase 2 规划组件，用于内部 Worker 扩缩；尚未实现，通用产品是否继承仍待裁决 |
| Quality API | 当前 v3 小智客服参考应用的版本、日志、反馈与发布契约；通用 Agent 不一定直接实现同一接口 |
| Signal Adapter | 把投诉、评分下降、运行错误、安全命中或人工事件规范化成 Case 输入的通用接入边界 |
| SignalEnvelope（v4 草案） | 外部/内部反馈、维护报告、监控、eval、运行错误或 Agent 自报的不可变规范输入；带租户、来源、reporter、run/trace、隐私、完整性与幂等信息 |
| Closure Adapter | 把已验证结果送回来源系统的通用出口；回复飞书原群只是客服实现之一 |
| Agent Run | 被治理 Agent 的一次可关联执行，包含输入、输出、模型/工具活动、环境和版本引用；最小字段尚待新版契约冻结 |
| ResolutionContract（v4 草案） | Planner 提出、Evaluator Agent 审查可测性、Controller 冻结的问题、目标、允许变更面、公开验收、预算、风险、回滚和停止条件 |
| CandidateContract（v4 草案） | 每个 Candidate revision 开工前由 Generator 提出、Evaluator 审查、Controller 冻结的本轮构建范围和公开验证方法 |
| EvaluationPlan / HoldoutBinding（v4 草案） | Eval Controller 专属的密封考题、标签、Judge 配置和阈值；生成侧只能获得 digest 与聚合 verdict |
| WorkerTask（v4 草案） | 承载内部 Agent 或确定性 worker 工作的持久任务；只拥有 queue、lease、fencing、retry、cancel-request、exhausted，不拥有领域成功 |
| Attempt（v4 草案） | 一次真实 Worker 执行的不可变输入快照、模型/Skill/MCP/tool receipts、输出和失败记录 |
| typed Proposal（v4 草案） | Agent 提交的不可变非权威 Case brief、归因假设、变更候选或回归建议；不同语义不能挤进一个万能对象 |
| ProposalDecision（v4 草案） | Controller 对确切 Proposal 的独立接受/拒绝记录；接受后与首个下游领域 event 同事务绑定 causation |
| Finding（v4 草案） | 独立 Evaluator 对确切 candidate revision 提出的可复现问题与证据 |
| AutomationRunView（v4 草案） | 汇总多个权威聚合状态的只读进度投影和事件游标；不接受 start/cancel，也不拥有业务终态 |
| TraceSource | 读取外部 Agent run / trace 的可插拔接口；Langfuse 是首个计划适配器，不是唯一后端 |
| Langfuse | 可接收 CaseLoop 自身 trace，也可作为被治理 Agent 的取证来源；不持有 CaseLoop 生命周期权威状态 |
| trace completeness | 对采集范围、缺失、脱敏、采样、留存和权限限制的显式说明；不足时必须为 `UNKNOWN` |
| VersionSet | v3 为 `{prompt, KB manifest, model + params}` 的不可变集合；通用 Agent 的 skill、tool、harness、memory、policy、environment 等候选维度尚未冻结 |
| reference workload | 用于验证治理闭环的首个真实场景；不等于最终产品范围，也不自动证明通用能力 |
| provider-neutral | 核心契约不要求 StepFun、飞书、AgentTeams、Langfuse 或任何单一厂商；具体项目通过适配器接入 |
| 5-cell 实验 | v3 客服三因素归因矩阵：当前态 C、分别恢复 P/K/M 的 RP/RK/RM、全部恢复 G；不应原样套给任意 Agent |
| 三态裁决 | `ATTRIBUTED`、`INCONCLUSIVE`、`CONFOUNDED`；证据不足时补实验或人工接管，不得猜测成功 |
| WorkOrder | 不可变候选变更信封，hash 绑定目标、输入版本、diff、GateReport、expiry 与 nonce |
| ApprovalGrant | 人工审批凭证，绑定确切 `workorder_hash + nonce + expiry`；一次性消费以防重放 |
| PolicyGrant（v4 草案） | 组织预先授权某 principal 在限定项目、动作、风险、预算、证据、有效期和 blast radius 内自动执行；不能冒充 Human Approval |
| 双轨门禁 | v3 的确定性规则轨 + 独立 LLM 裁判轨；裁判与被测模型必须分离 |
| replay | 明确标注的确定性或录制输入验证，可使用 fake / fixture；不能充当 live-provider 证据 |
| domain-provider-live | 真实 provider 与确定性控制面调用链的现场证据；不自动证明内部 Agent 有因果贡献 |
| agent-causal | 内部 Agent 真实接单、调用模型/Skill/MCP、在动作前生成 proposal，并能被后续权威事件以 causation/evidence 引用 |
| platform evidence export | 把 Matrix、MinIO、CR 或其他平台记录导出成证据；只证明相应平台操作，不等于 `agent-causal` |
| Matrix projection | AgentTeams 内部协作消息的投影；消息发送成功不证明 Worker 阅读、推理或创作 |
| MinIO workspace | AgentTeams 工件存储位置；对象和 digest 证明字节存在，不证明工件作者或因果贡献 |
| GateReport | 门禁报告，必须绑定确切候选、版本、数据集和 evidence；任何缺失、UNKNOWN 或不匹配都 fail closed |
| Trust Ledger | `risk_class × autonomy_state` 的治理证据账本；一次动作一个样本，Wilson 双侧下界满足阈值才可晋升 |
| evidence epoch | Trust Ledger 的原始整数计数周期；不得用多条探针虚增一次动作的样本量 |
| warm pool | Phase 1 固定规模的内部 Worker 池；不是动态扩缩能力 |
| feishu mock | 只允许具名 contract/replay；默认通知 adapter 是 `disabled` 且 fail closed，mock 不代表 live 飞书或真人消息 |
| SOUL | AgentTeams 中内部 Worker 的人设和规程文件 `SOUL.md`；文件存在不证明规程被实际执行 |
| portable Skill（v4 草案） | 以开放 Agent Skills 目录为兼容格式的能力包；发布权限、依赖、签名、撤销和 Gate 由 CaseLoop sidecar 与控制面补足 |
| SkillCandidate（v4 草案） | 人或 Agent 生成的不可变 Skill 变更候选；必须经过 old/candidate/no-skill、隐藏 holdout、安全、权限和供应链验证后才能发布 |
| Team CR / Worker CR / Human CR | AgentTeams 声明式资源；资源存在只证明平台声明状态，不证明业务闭环或 Agent 因果执行 |
| kine | AgentTeams controller 的 SQLite KV 存储；不是 CaseLoop 业务状态、审批、发布或审计权威源 |
