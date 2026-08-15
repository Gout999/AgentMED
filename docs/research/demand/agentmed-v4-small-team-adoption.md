# AgentMED v4 需求研究：让小型 Agent 团队真正用起来

> 状态：需求研究快照，不是实现基线；已批准裁决以 `docs/prd-v2.md` 与 `docs/plan-v4.md` 为准
>
> 更新：2026-08-10
>
> 范围：小团队采用、通用 Signal、GAN-inspired Agent Team、Agent-native 接口、Skill/MCP 自进化与阿里云接入
>
> 事实纪律：当前实现以代码、`docs/plan-v3.md`、现有 `contracts/` 和测试为兼容基线；本文中的早期建议若与已批准 PRD/plan 冲突，以后者为准。特别是“重组现有六角色”已被否决，正式方案保留质量 Team 并新增独立 Coding Team。

## 1. 结论

AgentMED 面向小型 Agent 团队时，不应表现成“另一套要先学会运维的多 Agent 平台”，而应表现成一个可接入现有工具的 AI 维护团队：

> 把差评、错误、内部反馈或维护人员发现的问题交给 AgentMED；它自动绑定运行证据、调查原因、提出并验证修复，在用户预先授权的低风险范围内自动发布和回滚。

要做到这一点，下一阶段不是继续给“投诉流程”堆分支，而是完成五个结构性变化：

1. `Complaint` 提升为通用、不可变、可去重的 `SignalEnvelope`；
2. 用持久化 `WorkerTask / Attempt / typed Proposal / ProposalDecision` 承载真实 Agent 工作，不把每个角色步骤塞进 Case 状态机；
3. 保留现有六角色质量治理 Team，新增 Planner–Generator–Reviewer 专业 Coding Team；两者通过 WorkerTask/Attempt/typed Proposal 协作，确定性 Controller 最终仲裁；
4. 提供统一 HTTP 能力层、CLI、公共 MCP 和 portable Skill，使人、Claude Code、Codex、CI 和客户自研 Agent 都能调用；
5. 把 Skill/MCP 当一等版本化资产，用同一套候选、独立评测、审批、灰度、回滚和审计闭环治理。

## 2. 当前仓库真正具备与缺失的能力

### 2.1 已具备

- PostgreSQL 控制面持有 Case、实验、Gate、WorkOrder、审批、发布、通知和审计权威状态；
- `case.submit_suggestion` 已提供带租约、fencing、幂等和审计的非权威 Worker 建议入口；
- Quality API 已有 `/v2/logs` 与 `/v2/feedback` 读面；
- 六个内部角色已经覆盖协调、采集、归因、修复、门禁和沉淀；
- 现有 MCP 按角色做了最小工具投影，权限边界比“一个万能 MCP”更安全；
- Gate、不可变 WorkOrder、ApprovalGrant、Release Controller、outbox 与 Trust Ledger 是可继承的核心。

### 2.2 关键缺口

- 唯一通用入口仍叫 `POST /v1/complaints`，`source` 只接受 `webhook | poll`；
- 负反馈能被采集员读取，但不会自动、可靠、租户化地创建案件；
- 没有内部维护反馈、监控告警、eval 回归、运行异常、Agent 自报的统一契约；
- Complaint 没有通用 `run_ref / trace_ref`，无法在立案时冻结原始 Agent Run；
- 当前 `trace_id` 多为业务关联字符串，不等于可读取、完整的 OTel/Langfuse trace；
- 当前 12 个 MCP projection 是内部 Worker 接口，不是公共产品 API；
- 没有统一 `agentmed` CLI，也没有公共 MCP facade；
- `agentmed-b1-loop` 是 B1 客服纵切 Skill，不是通用岗位能力；
- 没有 Skill Registry、Skill Candidate、独立 Skill Eval、发布/撤销/回滚或阿里云同步权威记录；
- 当前本地 runner 同时扮演编排器、Actor 模拟器和证据组装器，继续扩展会成为“大号状态机”；
- 当前证据导出路径不能证明六个 Worker 真实领取任务、调用模型/Skill/MCP 并产生因果贡献。
- 当前用户假设仍来自仓库审计和公开资料，尚没有符合 ICP 的访谈、脱敏真实工作流和 Shadow pilot 证据。

## 3. 小团队的采用假设

以下是待访谈验证的用户假设，不是市场统计。建议首发 ICP 先收窄为 **2–8 人、运行 1–3 个真实 Agent、已有 Langfuse/Git/飞书中至少两项的国内 AI 产品团队**；5–20 人平台团队与多客户服务商是后续画像。

| 团队 | 常见现状 | 第一个可感知价值 |
|---|---|---|
| 2–5 人 AI 产品团队 | Langfuse Cloud、GitHub、飞书，一个人兼产品与维护 | 接一条低分 trace，自动形成有证据的问题报告 |
| 5–20 人内部 Agent 团队 | 自托管 Langfuse/Phoenix、GitLab、Sentry、企微/飞书 | 自动立案、调查、候选修复、草稿 PR 与回归验证 |
| 小型 Agent 交付服务商 | 多客户、多 Agent、多代码仓、多套凭据 | 租户隔离、项目级权限和不同自治等级 |

小团队不应被要求理解 Matrix、MinIO、AgentTeams CR 或内部六角色进程。它们是可替换的内部执行实现。

合理的首次体验目标是：

> CLI 已安装、Docker 健康且 Langfuse 凭据有效时，用 `agentmed quickstart --profile local --source langfuse` 把一条历史坏 trace 变成 First Useful Case；另用 `agentmed report` 支持暂时没有 trace 的维护人员直报。

First Useful Case 不只是 trace 镜像：它必须同时给出 Signal/reporter、确切 run/version、跨来源去重、完整性/缺失项、不可变 receipt 和下一步。没有 trace 的报告进入 `NEEDS_CORRELATION`，不能补造关联。“五分钟”只在明确前置下作为待测 pilot 目标，fixture 冷启动和真实来源热启动分开报告。

不能承诺“安装五分钟后即可无条件修改生产”。

需求已批准进入 Stage 0，pilot 不再阻塞契约施工。正式宣称通用产品效果前仍应完成 Evidence Gate：访谈至少 5 个符合 ICP 的团队，3 个愿意提供脱敏真实 Signal/trace/人工流程，2 个愿意跑 Shadow pilot。这个门槛用于校验共同工作流和采用摩擦，不是市场统计。

## 4. Signal：外部投诉只是一个来源

### 4.1 首批 Signal 类型

| 类型 | 例子 | 首批 Adapter |
|---|---|---|
| `external_feedback` | 点踩、低评分、用户文字反馈、重复追问 | Langfuse score、Quality API feedback、飞书 webhook |
| `internal_feedback` | 运营或领域专家认为结果不对 | Console、飞书/企微、CLI |
| `maintainer_report` | “最近经常选错这个工具” | CLI、公共 MCP、GitHub/GitLab issue |
| `monitor_alert` | 延迟、成本、成功率或业务指标异常 | 通用 webhook、Sentry、OTel |
| `eval_regression` | CI eval、canary 或定期回归失败 | JUnit/JSON、Promptfoo/Phoenix/Langfuse |
| `runtime_failure` | exception、timeout、tool permission/auth failure | OTel、Claude Code hook、SDK |
| `policy_violation` | guardrail、安全扫描或越权命中 | 安全产品 webhook |
| `agent_self_report` | 被治理 Agent 主动声明“不确定/失败” | 公共 MCP/HTTP；默认不可信，只作线索 |
| `scheduled_inspection` | 周期巡检发现质量漂移 | AgentMED scheduler |

Langfuse 已把人工、模型和程序产生的质量判断统一表示为 score；Phoenix annotation 也区分 `HUMAN`、`LLM` 和 `CODE` 来源。AgentMED 应读取这些现有记录并固化案件证据，而不是重新造一套反馈数据库：

- [Langfuse Scores](https://langfuse.com/docs/evaluation/scores/overview)
- [Phoenix Annotations](https://arize.com/docs/phoenix/tracing/concepts-tracing/annotations-concepts)

### 4.2 `SignalEnvelope` 最小字段

```text
signal_id
schema_version
workspace_id / project_id / environment
kind / source_connector / source_event_id
occurred_at / received_at
reporter {kind, principal_ref, authenticity}
governed_agent_ref
run_ref {trace_source, trace_id, root_span_id, session_id}
summary_ref / body_digest / attachment_refs
severity / labels / confidence
privacy {classification, redaction_state, retention_policy}
completeness {status, missing_fields}
idempotency_key / dedup_fingerprint
```

入口流程必须是确定性的：验签与鉴权 → PII 处理 → 幂等落库 → Trace 绑定 → 去重/合并 → 风险分类 → 创建或关联 Case → outbox 派发。任何 Adapter 失败都必须留下可见的 retry/dead-letter 状态。

Signal、trace、issue、IM 正文和附件都是不可信输入：自然语言中的权限请求没有授权效力，不能拼入系统指令；附件先做类型/大小/安全解包与恶意内容限制；跨源相似匹配只能生成可审计、可拆分的 link proposal。来源更新和删除用 superseding event 表达，并保留 acknowledgement、correlation 与 Closure/reopen 路径。

飞书和 GitHub webhook 都明确要求处理重试与去重，这与 AgentMED 现有 inbox/outbox 基础一致：

- [飞书事件订阅](https://open.feishu.cn/document/server-docs/event-subscription-guide/overview?lang=zh-CN)
- [GitHub Webhook 最佳实践](https://docs.github.com/en/webhooks/using-webhooks/best-practices-for-using-webhooks)

## 5. Anthropic 的 GAN-inspired 架构如何正确用于 AgentMED

用户提到的文章是 Anthropic 2026-03-24 发布的 [Harness design for long-running application development](https://www.anthropic.com/engineering/harness-design-long-running-apps)。原文明确采用受 GAN 启发的 Generator–Evaluator 循环，并扩展为 Planner、Generator、Evaluator 三 Agent。

它不是真正的 GAN 训练：没有 minimax loss、反向传播或模型权重更新。正确口径是：

- `GAN-inspired generator–evaluator loop`；
- Planner–Builder–Verifier architecture；
- 对抗式生成—验收闭环。

文章中最值得复用的机制：

1. 执行者不能给自己的工作最终评分；
2. 开工前 Generator 与 Evaluator 先约定可测试的完成合同；
3. Evaluator 必须操作真实系统，而不是只读 Generator 的自述；
4. 结构化文件/工件承担跨 Agent 交接；
5. 任一硬阈值失败就把具体发现退回返工；
6. 随模型能力变化持续做消融，删除不再带来效果的脚手架。

文章也给出重要反面约束：完整 harness 在一个展示中约 6 小时、200 美元，单 Agent 约 20 分钟、9 美元；Evaluator 起初会替 Generator 开脱，也会漏测。因此不能用“多 Agent 数量”证明质量，必须按任务复杂度启用并持续校准。

另一篇 [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) 适合参考独立子任务的并行取证，但其官方数据也显示多 Agent 约消耗普通聊天 15 倍 token，并指出依赖关系密集的编码任务不天然适合大规模并行。

### 5.1 现有六角色的重组

| v4 单元 | 现有角色 | 真正职责 |
|---|---|---|
| Planner Cell | 质量官、采集员、归因师 | 冻结问题、证据、目标、允许范围、预算和验证方法；提出假设，不裁决事实 |
| Generator | 修复师 | 自由生成 prompt/skill/tool/code 等候选工件 |
| Evaluator Agent | 守门员 | 主动找反例、操作 sandbox、提交 Finding；不能计算权威 PASS 或发布 |
| Eval Runner + Independent Judge | 确定性服务 + 独立模型轨 | 执行规则/测试与版本化评分，只提供 Gate evidence |
| Gate Controller | 确定性 Controller | 按冻结策略逐项计算 PASS/FAIL/INCONCLUSIVE/ERROR/UNKNOWN |
| Memory/Curator | 案例官 | 把已验证 Case 沉淀为回归、知识与 Skill 候选 |
| Authority | 确定性 Controllers | 冻结 hash、接受/拒绝 Proposal、Gate、审批、发布与审计 |

### 5.2 两层公开合同 + 密封评测计划

Planner 先根据 Signal、证据和用户目标提出内容寻址的公开 `ResolutionContract`：

```text
problem_statement
target_agent_and_version
goal_state
allowed_change_surfaces
public_acceptance_criteria
required_evidence
risk_class / permissions / budget
rollback_plan
max_iterations / plateau_rule / stop_conditions
```

Evaluator Agent 审查其可测性与风险，Controller 校验并冻结。每个 Candidate revision 开工前，Generator 再提出公开的 `CandidateContract`，说明本轮构建范围与验证方式，由 Evaluator 审查后冻结。这准确对应 Anthropic 的 Generator–Evaluator sprint contract，同时保留 AgentMED 的上层问题合同。

隐藏考题、标签、独立 Judge 配置和阈值单独放在 Eval Controller 专属的 sealed `EvaluationPlan / HoldoutBinding`。参与生成的 Worker 只能看见 digest、策略类别和最终聚合 verdict，不能访问或修改其内容。

### 5.3 只并行独立工作

可并行：不同日志源取证、相互独立的归因假设、多个候选方案、独立安全/质量检查。

必须串行并由控制面仲裁：合同冻结、Candidate 冻结、Gate、WorkOrder 冻结、审批、发布、回滚和权威状态迁移。

## 6. 避免“大号状态机”：持久化工作内核

不要再让一个本地 runner 依次模拟所有 Actor。v4 应增加通用工作内核：

```text
WorkerTask: READY → LEASED → SUBMITTED → ACCEPTED | REJECTED | DEAD
Attempt:  每次真实 Worker/模型/Skill/MCP 执行的不可变输入、输出与 receipt
typed Proposal: Agent 提出的不可变非权威产物
ProposalDecision: Controller 独立记录接受或拒绝
Finding:  Evaluator 对某个 candidate revision 的证据化问题
```

WorkerTask 只拥有 queue/lease/fence/retry/cancel/exhausted，不拥有领域成功。Case 只表达问题是否待解决；Experiment、Candidate、Gate、各 scoped ExternalOperation、Closure 和 SkillRelease 各自持有领域状态。`AutomationRunView` 只是跨聚合进度投影和事件游标，不接受 start/cancel 写命令。

真实因果链必须是：Controller 事务性创建 task/outbox → Runtime Adapter 真正唤醒 Worker → Worker 原生身份 claim lease → 模型/Skill/MCP 网关产生 receipt → Worker 在动作前提交 typed Proposal/Intent → Controller 同事务保存 ProposalDecision 和下游 causation event。Exporter 只能读，不能 dispatch、ack/submit、写工件或业务命令。

这使 runner 退回它应有的位置：测试 harness、故障注入器和终态等待器。

## 7. “全自动”应定义为赢得的自治

| 等级 | 自动能力 | 生产写权限 |
|---|---|---|
| A0 诊断接入 | doctor、读取一条 trace、报告缺失 | 无 |
| A1 自动观察 | 拉取负反馈、去重立案、固化证据 | 无 |
| A2 自动调查 | Agent Team 提出假设，反方尝试推翻 | 无 |
| A3 自动验证 | 生成候选，在 replay/sandbox/eval 验证 | 无 |
| A4 自动提案 | 创建草稿 PR、draft prompt/skill、WorkOrder | 仅草稿/分支 |
| A5 受控执行 | 人批准确切 WorkOrder 后 canary/rollback | 单次授权 |
| A6 赢得的自治 | 在预先批准的低风险策略内自动发布/回滚 | PolicyGrant 限制且可撤销 |

自动化必须按 `Agent × 项目 × 动作类型 × 风险等级` 分别晋升。Human Approval 与 PolicyGrant 必须是两种不同权威记录，不能用自动脚本伪装成“human approved”。

建议新项目默认 A1，首批 pilot 只到 A4；A5 逐次人批，A6 等真实 pilot、自动降级、kill switch、rollback/compensation 验证后再开。

建议始终人工或专门授权的动作：权限与凭据、不可逆数据迁移、钱/合同/身份、安全策略以及高影响对外发送。

## 8. Langfuse 与 TraceSource

Langfuse 在 v4 中有两种角色：

1. AgentMED 自身的模型、Agent、工具调用通过 OTel/OTLP 输出到 Langfuse；
2. AgentMED 通过 `TraceSource` 读取被治理 Agent 的 observation、score 和相关上下文。

TraceSource 必须报告 `COMPLETE | PARTIAL | UNKNOWN`，并把查询窗口、字段、来源版本、响应 digest 和缺失项固化为 `TraceEvidenceReceipt`。进入案件的必要证据要在来源 retention 删除前复制到客户控制的不可变存储或生成可验证引用。

不能默认随 AgentMED 再部署整套 Langfuse。其自托管包含 Web、Worker、PostgreSQL、ClickHouse、Redis/Valkey 与对象存储，对小团队可能过重：[Langfuse Self-hosting](https://langfuse.com/self-hosting)。

Langfuse project key 的公开文档没有提供细粒度只读 scope；凭据必须只进入确定性 TraceSource 进程，并由 endpoint allowlist/egress proxy 限制为读取路径。Agent Worker 永远不接触该 secret。

## 9. 人和其他 Agent 如何调用 AgentMED

正确分层是：

```text
HTTP API = 唯一公共能力基线
CLI      = 人、CI 与所有 shell-capable Agent 的通用操控面
MCP      = Claude Code/Codex/Cursor 等 Agent-native 工具面
Skill    = 教 Agent 何时、如何安全使用 AgentMED
Plugin   = Claude Code 等特定客户端的分发包
A2A      = 稳定 Run API 之上的跨厂商长任务适配器
```

“Cloud Code”按上下文应统一写为 **Claude Code**。Anthropic 官方的 `claude -p` 是程序调用 Claude；要让 Claude 调 AgentMED，AgentMED 自己仍需要 CLI、MCP 或 HTTP。参考：

- [Claude Code headless/非交互模式](https://code.claude.com/docs/en/headless)
- [Claude Code CLI](https://code.claude.com/docs/en/cli-usage)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)

当前 12 个内部 MCP projection 不能直接对外。公共 MCP 必须经过 tenant auth、scope、幂等、审计和限流，只暴露产品级 intent，例如 `signals.submit`、`runs.start/get/cancel`、`cases.get/timeline`、`proposals.get` 和 `evidence.get`。

这里的 `runs.start/cancel` 只是公共 convenience intent：底层创建范围极窄的 AutomationRequest/stop-request，`AutomationRunView` 仍是只读投影。所有 mutation 要有持久化幂等 fingerprint、统一机器错误 envelope 和 durable operation ID；timeout/Ctrl-C 只停止客户端等待。Human approval 不进入 model-visible Skill/MCP/A2A，只能由 human-scoped UI/CLI 完成并重新鉴权。

## 10. Skills/MCP 自进化的工程边界

开放 [Agent Skills 规范](https://agentskills.io/specification) 只定义 `SKILL.md` 目录与渐进式加载；`allowed-tools` 仍属实验字段，规范没有定义 registry、依赖解析、签名、撤销或安全发布。因此 AgentMED 必须补自己的治理 sidecar，而不能把 `SKILL.md` 当发布授权。

建议对象：

```text
SkillPackage        portable SKILL.md + resources
SkillManifest       digest、来源、license、依赖、权限、兼容矩阵、SBOM
SkillCandidate      Agent/人提出的不可变变更候选
SkillEvalPlan       trigger、with/without、任务质量、旧能力回归、安全与权限测试
SkillEvalReport     多引擎结果、方差、隐藏 holdout、扫描 receipt
SkillVersion        SemVer + content digest，不可变
SkillDeployment     runtime/tenant/environment/固定版本
SkillRelease        Gate、HumanApproval/限定 PolicyGrant、canary、promote、rollback/revoke/compensate
```

“自进化”应解释为系统受控地生成下一候选，而不是 Skill 在生产中原地改自己：

```text
真实失败/重复模式
  → Curator 提出能力缺口
  → Skill Engineer 生成候选
  → 独立 Evaluator 运行 with/without + hidden holdout + conformance
  → 规则/Agent Judge/安全/权限/供应链多轨 Gate
  → Controller 冻结与授权
  → 少量 Worker canary
  → promote 或 rollback/revoke
```

外部 Skill/MCP 不能先 build 再 scan。安全顺序是：quarantine 拉取字节 → canonical digest/来源/license/签名 → 防路径穿越、symlink 与 archive bomb 的安全解包 → 静态 instruction/code/secret scan → hermetic dependency/build → 独立 SBOM/provenance → 动态 sandbox eval。发现结果一律先是 `UNTRUSTED_IMPORT`，不能自动安装或执行。

有效权限由 `Skill 请求 ∩ Agent 身份 ∩ 租户策略 ∩ Task/WorkOrder Grant` 决定，并由 mount、process allowlist、egress proxy、MCP gateway 和短期 RAM/IAM 身份强制。`allowed-tools` 或 MCP annotations 不是授权。第一次发布、生产 assignment 和权限扩大必须人批；PolicyGrant 只允许已批准 digest/cohort 的 canary。Rollback 只影响后续 assignment，已发生外部副作用需要独立 CompensationWorkOrder。

MCP 也要有 Candidate/Version/Assignment/Eval/Gate/Release/Revocation；每次 session/Attempt 重新测量 tool catalog/schema digest，远程服务运行中换 schema 必须阻断。无法固定制品的远程 MCP 标为 `UNPINNABLE_REMOTE`。

MCP 描述和 schema 可走相同闭环；可执行 MCP server 代码还必须经过构建、依赖锁、SBOM、漏洞/恶意代码扫描、sandbox 和更高风险审批。

阿里巴巴开源的 [skill-up](https://github.com/alibaba/skill-up) 已提供多引擎 with/without-skill 评测、规则/脚本/Agent judge、JUnit/HTML 报告和 CI，可作为 Eval Runner Adapter；它不能替代隐藏 holdout、发布权威或防篡改控制面。

## 11. 阿里云 Skills 的正确接法

公开 GOAI Agent Infra 规则中，Skill 是必选项，阿里云官方 Skills 属合理使用与评分考虑，并非“使用越多越好”。用户已把阿里云 Skills 接入提升为本项目交付要求，因此 v4 按更高标准验收：

1. 从 [阿里云 Agent Skills 门户](https://help.aliyun.com/zh/skillsportal/learn-about-the-alibaba-cloud-agent-skills-portal) 安装至少一个官方 Skill，在只读或可逆场景真实调用并留 receipt；
2. 将 AgentMED portable Skill 以固定版本发布/镜像到 [MSE AI Registry](https://help.aliyun.com/zh/mse/user-guide/ai-registry-skill-management-guide)，验证 Draft → 安全审核 → Publish → 固定版本下载 → 下线/回滚；
3. 把 [Agent Security Center Skills 检测](https://help.aliyun.com/zh/document_detail/3045542.html) 作为 Skill Gate 的一条独立安全轨，绑定精确文件清单、本地与供应商 SHA-256、root/child task、扫描规则版本、完整终态和报告 digest；timeout/error/漏文件均失败关闭；
4. 同一 Skill 仍可安装到 AgentTeams、本地 `.claude/skills`、`.codex/skills` 或其他兼容 runtime；阿里云是发布/安全 Adapter，不是 AgentMED 的唯一权威源。

AgentMED PostgreSQL Registry 应保存内部不可变版本和发布裁决；MSE AI Registry 是首选阿里 distribution target。只有组委会后续书面指定百炼环境，或项目主动采用百炼 Managed Agents runtime 时，才接支持精确版本绑定的 Managed Agents Skills API；ModelScope 先视作待验证的开源发现/分发目标。不能用外部 `latest` 标签替代已批准 digest。

MSE 审核扫描与 AISC 不是同一证明。当前 AISC 还依赖付费版、按文件计费、10MB 单文件限制、默认 10QPS 和公网可访问压缩包，因此它应进入 `competition-aliyun` profile，而不是成为所有开源部署的硬依赖；上传内容还要受租户同意、数据分类、区域、留存和删除策略约束。扫描通过不替代功能、SBOM、漏洞和许可证验证。

首个官方 Skill 推荐 `alibabacloud-sls-query`：只绑定 Collector，用限制到指定 Project/Logstore 的 `log:GetLogStoreLogs` 与 `log:GetIndex` 读取测试 SLS 中的一条 Agent 负反馈/异常，再由确定性 Connector 规范化为 Signal。保存 Portal Skill ID、上游 commit/本地包 digest、CLI 版本、查询窗口、API receipt、原始响应 digest、真实 Worker identity 和 Signal source_event_id。Cloud Skills Portal 的公开接口当前侧重发现与安装，未确认第三方项目可通过公开写 API 上架；因此“消费官方 Skill”和“发布 AgentMED 自有 Skill 到 MSE AI Registry”应作为两条不同证据。

当前仓库也必须修正文档与实现的数量口径：实际只有一个正式 `agentmed-b1-loop` 包，所谓“8 个 Skill”目前是八个能力域，不是八个已实现、可复用、可发布的 Skill。v4 建议形成少量职责清晰的真实包，例如 `agentmed-intake`、`agentmed-investigate`、`agentmed-change-proposal`、`agentmed-evaluate-release`、`agentmed-skill-evolution` 和 `agentmed-operator`，无需为了数字机械复制。

## 12. 采用效果的验收方式

下一阶段不能以“有六个 Agent”“消息很多”或“平台记录存在”证明效果。至少做：

1. 同一真实 Case 的单 Agent、现有六角色、重组后 Planner–Generator–Evaluator 消融；
2. Worker 关闭时 `agent-causal` 必须失败；
3. Evaluator 必须操作真实 sandbox，并与人工金标准比较一致率；
4. 每个 Proposal 必须在领域动作之前产生并由后续 event 以 causation/evidence 引用；
5. Skill 候选做 with/without、隐藏 holdout、旧能力 conformance 和多引擎测试；
6. 公共 HTTP、CLI、MCP 对同一 intent 产生相同权威结果和错误码；
7. 衡量 verified resolution、回归逃逸、UNKNOWN、回滚、未经授权动作、成本与耗时，而不是只看自然语言质量。

## 13. 仍需用户访谈验证

1. 问题最先出现在哪里：Langfuse、飞书、GitHub、Sentry 还是口头反馈？
2. 负反馈能否稳定关联到 run/trace 和确切版本？
3. 最常需要改的是 prompt、skill、知识、tool、代码、模型还是 harness？
4. 谁能批准 PR、prompt 发布、Skill 发布和生产回滚？
5. 哪些动作愿意预先授权，哪些永远要逐次确认？
6. 哪些 trace 数据不能复制，是否只能保存 digest/客户对象存储引用？
7. 单机 Compose、已有 K8s/VPC 或纯外接 SaaS，哪种是首批部署现实？
8. “一次处理有用”的终态是什么：不再复现、领域专家接受、差评下降、issue 关闭，还是业务指标恢复？

## 14. 建议裁决

下一条真实纵切选择 **coding Agent / Claude Code**，而不是再做一个客服变体：它天然有内部维护反馈、工具失败、代码/Skill/MCP 变更、PR、sandbox 和机器终态验证，也能同时验证 CLI、公共 MCP、portable Skill 和阿里云发布 Adapter。

这不会把 AgentMED 绑定到 Claude。Claude Code 是首个参考客户端；HTTP、CLI、MCP、Agent Skills 和 Adapter contracts 继续保持 provider-neutral。
