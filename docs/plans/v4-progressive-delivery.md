# AgentMED v4 渐进式施工台账

> 状态：**APPROVED FOR PROGRESSIVE DELIVERY / Stage 0 CONTRACT FREEZE COMPLETE**
>
> 生效日期：2026-08-10
>
> 当前施工分支：`codex/v4-foundation`
>
> 兼容性提示：本文是 V4 已批准计划与历史施工台账。V4 S1A 保留为已验证的本地
> compatibility runtime；V5 stage 的当前施工编排以
> [`v5-master-execution-plan.md`](v5-master-execution-plan.md) 为准。
>
> 目的：把已确认的 AgentMED v4 产品方向拆成可以逐阶段运行、验证、回滚和提交的工程闭环。本文描述目标与施工顺序，不把计划中的能力写成当前实现。

## 0. 权威边界与台账规则

1. `docs/product-principles.md` 决定产品定位和范围；在某个 v4 Stage 完成契约、迁移、测试、证据和 cutover 前，`docs/plan-v3.md`、现有 `contracts/` 与可执行测试仍是该部分当前实现基线。
2. PostgreSQL 控制面始终拥有生命周期、权限、幂等、审批、Gate、外部动作和审计的权威状态。Agent、模型、Claude Code、Matrix、MinIO 和导出器都不是权威状态源。
3. 每个 Stage 必须先通过 focused 与 negative tests，再跑 replay；只有外部前置和凭据真实可用时才跑对应 live。连接失败、空结果、跳过或无法核验均不是通过。
4. 全库只使用以下 canonical evidence facets：`contract`、`replay`、`domain-provider-live`、`agentteams-native`、`claude-runtime-live`、`agent-causal`、`repo-sandbox`、`human-authorized-external`、`production-canary`；不得组合成新名称或相互替代。mock 与 platform evidence export 都不是成功 facet。
5. 每个 v4 Stage closure 使用独立 evidence 目录、verifier 报告和 semantic commit。任何未完成项保持 `NOT RUN`、`FAILED`、`INCONCLUSIVE`、`ERROR` 或 `UNKNOWN` 的原始状态。
6. migration 使用 expand → backfill → contract；不依赖 `create_all`。回滚优先关闭入口、停止派单和恢复上一兼容版本，不删除权威记录。
7. 分支、commit、push、PR 和任何外部写动作遵守仓库授权边界。GitHub 外部写见 §5，必须逐动作取得授权。

### 0.1 当前状态

| Stage | 状态 | 完成判据 |
|---|---|---|
| Stage 0 · 语言、契约与双 Team 冻结 | `DONE (contract-only)` | 正式文档、ADR、已列明的 Stage 0 contract slices、Intent/transport/OpenAPI skeleton 和 contract tests 通过；不宣称完整 HTTP 字段合同、migration、runtime 或 live 已完成 |
| Stage 1 · Shadow Signal + Langfuse | `S1A DONE (LOCAL RUNTIME) / S1B PROVIDER-LIVE BLOCKED` | S1A 已完成认证 no-trace intake；S1B 仍需两条 Langfuse 链、First Useful Case 与 clean-machine 自助激活 |
| Stage 2 · Work Kernel + Claude Code + Coding Team | `NOT STARTED` | 2A/2B/2C 全部通过，形成真实 agent-causal coding receipt chain |
| Stage 3 · Verified Candidate | `NOT STARTED` | 3A/3B/3C 全部通过，代码与 Prompt 候选复用同一治理内核 |
| Stage 4 · Guarded External Operations | `NOT STARTED` | 经逐次人批完成一个可对账、可停止的 draft PR 动作 |
| Stage 5 · Skill/MCP 自进化与阿里云 | `NOT STARTED` | 固定版本 Skill 与 MCP 分别完成 Gate、真实 Worker canary、rollback/revoke；阿里云路径诚实分轨 |
| Stage 6 · Agent-native 入口 | `NOT STARTED` | HTTP/CLI/MCP/SDK 对相同 intent 返回相同权威结果 |
| Stage 7 · 开源生产成熟 | `NOT STARTED` | 两个非同构真实 workload 与 Day-2/灾备/升级证据通过 |

只有 evidence manifest 与 verifier 均通过后才能更新此表。代码合入、文档勾选或一次 runner 成功本身不构成 Stage 完成。

### 0.2 每个 evidence 包的固定结构

```text
evidence/v4/stage-<n>/<slice>/<run-id>/
  run-manifest.json
  environment.json
  commands.jsonl
  results/
  receipts/
  artifacts/
  digests.json
  verifier-report.md
```

`run-manifest.json` 至少记录 commit、dirty 状态、Stage/facet、开始和结束时间、实际运行的测试/live、provider/runtime/model 的非秘密标识、终态和缺失项。原始 secret、访问令牌、个人路径中的敏感配置和未脱敏输入不得进入 evidence。

## 1. 双 Team 与身份模型

### 1.1 保留现有质量治理 Team

`agentmed-team` 是当前 v3 客服 Scenario 的六个质量治理 Worker，不是专业 coding team，也不因 v4 被重命名或重组：

| Worker | 责任 | 当前声明的 runtime / model |
|---|---|---|
| `quality-officer` | 领单、分诊、协调、升级 | CoPaw / StepFun 3.7 Flash |
| `collector` | 投诉、日志和 badcase 取证 | CoPaw / StepFun 3.7 Flash |
| `attributionist` | 实验建议与归因分析 | CoPaw / StepFun 3.7 Flash |
| `repairer` | Prompt/配置类候选起草 | CoPaw / StepFun 3.7 Flash |
| `gatekeeper` | 评测建议与否决 | CoPaw / StepFun 3.7 Flash |
| `case-officer` | 归档、回归资产与闭环通知 | CoPaw / StepFun 3.7 Flash |

它继续采用 4 常设 + 2 固定 warm-pool 的 Phase 1 口径，不宣称动态扩缩。Worker 此刻是 Running 还是 Sleeping 属于可变部署快照，只进入 `PROJECT_STATE`/handoff 和本次 live evidence，不写成长期架构事实。

### 1.2 新增专业 Coding Team

新建独立的 `agentmed-coding-team`：

| Worker principal | 责任 | 初始执行策略 | 明确禁止 |
|---|---|---|---|
| `coding-planner` | 读取 issue/报告、复现基线、提出可测的 `ResolutionContract` | GLM-5.2 通过 provider smoke 后作为目标主模型 | 修改代码、查看隐藏 holdout、批准或发布 |
| `coding-generator` | 领取生成任务，委托 Claude Code 子 Attempt 产出最小 patch | Claude Code CLI harness + GLM-5.2 | 持有 GitHub 写凭据、修改冻结测试、批准自己的 Proposal |
| `coding-reviewer` | 对抗审查、操作 sandbox、提交 `Finding` | 与 Generator 不同的 provider/model origin + 确定性测试 | 改写候选、决定 Gate、执行外部动作 |

两个 Team 复用同一个 `Signal → QualityCase → WorkerTask → Attempt → typed Proposal → Gate → scoped Execution` 内核，但拥有不同 Team manifest、SOUL、Skill、MCP allowlist、预算和权限。`agentmed-b1-loop` 不作为 coding Skill 复用。

### 1.3 四类身份不能混用

1. **AgentTeams Worker principal**：领取 WorkerTask、持有 lease/fencing token、提交 Proposal/Finding。
2. **Runtime session**：CoPaw session 或 Claude Code CLI session，证明某次执行发生；它不自动等于新的 Agent principal。
3. **Model invocation**：provider、requested/resolved model、usage 与 response receipt；Claude Code 是 harness，不是模型名。
4. **GovernedAgent**：被 AgentMED 观察和治理的外部 Agent 资源，不等于 AgentMED 内部 Worker。

如果 `coding-generator` 只是委托 Claude Code，父 Worker Attempt 与 Claude Code 子 Attempt 必须用 `parent_attempt_id` 连接。不能把 wrapper 与 Claude session 重复计算成两个参与 Agent；只有独立 principal、claim、lease 和 receipt 完整的身份才进入 `ParticipationManifest`。

### 1.4 模型和 fallback 纪律

- GLM-5.2 是 coding 主模型的目标配置，不在 `domain-provider-live` smoke 成功前写成“已接入”。
- StepFun 3.7 Flash 继续服务现有质量队和适合的低成本分诊，不是 GLM 的静默 fallback，也不能单独成为代码放行依据。
- `requested_model`、`resolved_provider`、`resolved_model`、provider origin、`model_resolution_receipt_digest`、`model_call_receipt_digest` 和 usage 必须进入 Attempt；不记录 secret/base URL credential。
- fallback 必须结束当前 Attempt 并创建新 Attempt，保留原失败原因。不能在同一 Attempt 中静默更换 provider/model。
- Reviewer 与 Generator 无法满足独立模型轨时，结果进入 `HUMAN_REQUIRED`；确定性 Gate 仍必须运行。

## 2. Attempt、恢复与取消协议

### 2.1 状态与不变量

```text
Dispatch:   PENDING → SENT → ACKED | FAILED | UNKNOWN
WorkerTask: QUEUED → LEASED → COMPLETED
                 ↘ WAITING_RETRY → LEASED
                 ↘ CANCEL_REQUESTED → CANCELLED
                 ↘ EXHAUSTED | BLOCKED_UNKNOWN
Attempt:    CREATED → STARTING → RUNNING → OUTPUT_RECORDED → SUCCEEDED
                    ↘ FAILED | TIMED_OUT
                    ↘ CANCEL_REQUESTED → CANCELLED
                    ↘ UNKNOWN
```

- WorkerTask 只拥有排队、lease、fencing、retry、cancel-request 与 exhausted，不拥有 Case 是否解决。
- 每次领取或重试创建新的 immutable Attempt；terminal Attempt 不得改写为另一结果。
- `SUCCEEDED` 只表示 runtime 成功产出符合 output contract 的 artifact，不表示 Proposal 被接受、Gate 通过或 Case 已解决。
- lease 过期、fencing token 错误、receipt digest 不匹配和无法确定外部结果都 fail closed。

### 2.2 Claude Code 父子 Attempt 顺序

1. Domain Controller 在同一 PG 事务创建父 `WorkerTask` 与 `work.requested` outbox。
2. AgentTeams Worker claim 父 WorkerTask；Work Controller 创建父 Attempt、lease 与 fence。
3. 父 Attempt 提交受限 `DelegationProposal`；Proposal Controller 接受后，在 Decision 与首个下游 causation 的同一事务中，由 Work Controller 创建 child WorkerTask，并签发一次性 `CapabilityLease(grant_kind=DISPATCH_CLAIM)`；child claim 创建 Attempt 后另签发绑定 task/attempt/repo/base/ResolutionContract/budget/expiry 的 `CapabilityLease(grant_kind=ATTEMPT_RUNTIME)`。
4. host-side `ClaudeCodeRuntimeAdapter` 以精确 argv 在一次性 worktree 启动 CLI；Adapter 不创建 Attempt，禁止 shell 拼接。
5. CLI 用一次性 dispatch token claim child WorkerTask；Work Controller 在 claim 时创建 child Attempt、lease 与 fence。Adapter 只采集 session、stream-json、tool、model、diff、exit code、timeout 和 artifact digests，并对实际观察到的事实签发 runtime receipt。
6. child Attempt 作为作者提交引用自身 receipt chain 与 artifact digest 的 typed `ChangeProposal`；父 Worker只消费接受后的结果并 terminalize 父任务，不重新署名 patch。
7. Controller独立校验 principal、lease/fence、时间顺序、nonce、grant、repo/base、receipt 与 digest，再在同一事务保存 `ProposalDecision` 与首个下游 causation event。

### 2.3 crash、timeout 与 unknown outcome

| 故障点 | 恢复动作 | 禁止行为 |
|---|---|---|
| claim 后、CLI 启动前崩溃 | lease 到期后创建新 Attempt；旧 Attempt 标 `FAILED` 或可证实的 `CANCELLED` | 复用旧 fencing token |
| CLI 已启动、启动 receipt 未提交 | 用预分配 `runtime_session_id`、PID birth marker 和 worktree marker reconcile | 不查询就盲目再启动一份 |
| Adapter 重启 | 扫描非 terminal Attempt，核对 session/process/worktree/stream log 后恢复观察或标 `UNKNOWN` | 从本地 state file 推断业务成功 |
| wall timeout | 对进程组发 SIGTERM，等待短 grace 后 SIGKILL；保存退出与 kill receipt | timeout 直接标成功或丢弃日志 |
| CLI 退出但 artifact/receipt 写库失败 | 从内容寻址 artifact 与 session receipt reconcile；不能确认则 `UNKNOWN` | 仅凭 exit code 0 补成功 |
| model/provider 失败 | 当前 Attempt 终止；策略允许时新建 fallback Attempt | 原 Attempt 内改 model 字段 |
| 用户请求取消 | 记录 `CANCEL_REQUESTED`，停止未来工作；只有观察到进程终止才标 `CANCELLED` | 宣称撤销已发生的文件或外部副作用 |
| lease 丢失后迟到输出 | 保存为拒收证据，ProposalDecision 必须拒绝 | 回填新 lease 或接受迟到 Proposal |

恢复扫描、retry budget、backoff、最大 Attempt 数和 exhausted 原因均由 PG 权威记录。工作目录可删除，Attempt、receipts、Proposal 和 audit 不能删除。

## 3. Stage 0–7 施工矩阵

## Stage 0 · 冻结 v4 语言、双 Team 与契约

### Entry

- 用户已批准按完整闭环渐进施工。
- 当前工作在非 `main` 分支进行；现有未提交改动已盘点，禁止混淆历史 live 与 v4 新能力。
- `docs/product-principles.md` 已作为产品策略上位原则。

### Deliverables

- 正式 `docs/plan-v4.md` 与 `docs/prd-v2.md`；原 `docs/prd.md` 保留为 v3 客服 Scenario PRD，小智客服/B1 不再定义产品边界。
- Aggregate Ownership、Worker/Runtime/Model Identity、Delegation/Recovery、Cutover/Compatibility ADR。
- v4 公共与证据切片：common identifiers、canonical evidence facets、`PublicError`、`IdempotencyReceipt`、`SignalEnvelope`、`AgentRunRef`、`TraceEvidenceReceipt`。
- v4 工作与裁决切片：`ResolutionContract`、pre-build `CandidateContract`、pre-build `EvaluationPlan`、post-build `CandidateRevision`、`GateReport-v4`、`WorkerTask`、`Attempt`、`AgentIntent`、typed `Proposal`、`ProposalDecision`。
- v4 身份、能力与执行工件切片：`AgentManifest`、`ParticipationManifest`、`CapabilityLease`、仅在 exact Gate `PASS` 后创建的 `WorkOrder-v4`、`SkillManifest`、`MCPManifest`。
- event catalog、可达且 fail-closed 的 state machines、正向 fixtures、定向 negative mutation fixtures，以及 ownership/causality/permission-intersection/cutover conformance tests。
- v1 Intent Registry 与 transport exposure rules；公共 auth/scope/idempotency/error/compatibility 契约。
- OpenAPI 3.1 从 Stage 1 Entry 起按 slice 冻结：registry 用 `wire_status / activation_stage / field_contract_ref` 区分。S1A 冻结 common + `capabilities.get/signals.submit/cases.get/cases.timeline/evidence.get`，S1B 冻结 `sources.capabilities/sources.doctor/source-sync-runs.get`；OpenAPI 只含这些 FROZEN operations。Stage 2/4 目标 intent 保持 `SKELETON + null field_contract_ref`，不能生成 route、CLI/SDK 或 discovery 广告。
- 双 Team 的明确现状/目标表；删除“把现有六角色重组为 coding PGE”的表述。

### 主要文件

```text
docs/plan-v4.md
docs/prd-v2.md
docs/product-principles.md
docs/plans/v4-progressive-delivery.md
docs/decisions/D-008-v4-aggregate-ownership.md
docs/decisions/D-009-v4-runtime-causality.md
docs/decisions/D-010-v4-capability-secret-boundary.md
docs/decisions/D-011-v4-proposal-gate-workorder-binding.md
docs/decisions/D-012-v3-v4-cutover-compatibility.md
contracts/v4/
agents/coding/team.yaml
agents/coding/souls/
PLANS.md
docs/context/PROJECT_STATE.md
docs/context/LAST_HANDOFF.md
wiki/
```

### Migration

无数据库 migration。Stage 0 只冻结契约、owner/command/event matrix、cutover 和未来 migration 计划。

### 验证

- **focused**：全部已列 Stage 0 JSON Schema 与 fixtures、Intent Registry ↔ OpenAPI 骨架一致性、OpenAPI lint、canonical evidence facets、文档链接、现有 SOUL 同步、owner matrix 校验。
- **negative**：projection 接受写命令、Worker 可 self-approve、Exporter 有 claim/submit 权限、同一事实出现两个 owner 时 contract test 必须失败。
- **replay**：Stage 0 不改 v3 runtime/contract，不重跑完整 B1；运行 v3 schema/Wilson compatibility tests，并引用已提交的历史 B1 replay provenance。只有实际触碰 v3 调用链、验收契约或可复现性要求时才新跑。
- **live**：无；Stage 0 不用历史 live 冒充新能力。

### Evidence

- facet：`contract`
- 路径：`evidence/v4/stage-0/contracts/<run-id>/`
- 必需：ADR review、schema/conformance tests、Intent/OpenAPI 骨架 review、v3 compatibility test 与历史 replay provenance、完整 request/response field freeze 的 Stage 1 Entry 债项清单；不把旧 replay 重跑当成文档/contract 修改的固定仪式。

### Exit

- 上述 Stage 0 contract slices、event/state-machine catalog、fixtures 与 ownership/causality/permission/cutover tests 全部通过；缺失、跳过或无法核验不得记为完成。
- 每条 command 只有一个 aggregate owner；scoped `ExternalOperation` 的唯一 PostgreSQL owner 是 `scoped-executor-controller`。Repo、Release、Closure、Distribution 只是按类型执行副作用并返回 receipt 的 Executor/Adapter，不共同拥有或直接改写 `ExternalOperation`。
- Intent Registry 与 OpenAPI 骨架在 intent、transport、scope、error 与 idempotency 上一致；Stage 0 只据此关闭“骨架”交付，**不得**声称完整 request/response 字段已经冻结或 Public API 已实现。
- 当前施工 slice 的完整 request/response field freeze 是该 slice runtime Entry 硬门；未来 Stage 的 skeleton 不阻塞 Stage 1，但也不得 advertise。

### Rollback

在尚未 cutover 前关闭 v4 feature flags/入口，继续使用 plan-v3 contracts；不得改写或删除 v3 证据。

### Semantic commits

```text
docs(v4): ratify dual-team progressive delivery plan
test(contracts): freeze v4 work and identity contracts
```

## Stage 1 · Shadow Signal + Langfuse 只读纵切

### Entry

- Stage 0 contract/verifier 通过。
- S1A/S1B 的 exact request/response、required/optional/nullability、显式错误、async durable query/correlation、cursor snapshot、contract-version handshake 已冻结并通过 schema/conformance；Stage 2/4 skeleton 不在 OpenAPI，也不进入 route/CLI/capability discovery。
- S1A 的 `signals.submit` 必须在同一 PG 事务写四个领域事实与各 owner receipt/audit/outbox/idempotency；无 locator 返回 `UNKNOWN + null AgentRunRef`，禁止伪造 run。`source_id` 必须绑定 authenticated workspace 的 `ACTIVE` manual SourceConnection；007 提供最小 bootstrap，008 再扩 connector cursor/DLQ。
- runtime pin `rfc8785==0.1.4` 并使用 no-float JCS/self-field exclusion；PG audit 权威，JSONL/export 只能 after-commit/outbox，避免 rollback 后留下幽灵成功。
- Langfuse credential 由 Connector/Secret Broker 隔离，Adapter 只允许 allowlisted reads；除非 provider-side read-only 机制已单独验证，否则按广权限 project secret 管理且不宣称 key 本身最小权限。字段范围、retention 和脱敏策略已发现。
- AgentMED OTel sink 使用独立 Langfuse project 与独立 write credential；不得复用 TraceSource read credential。两者分别有 credential reference、audience/project binding、rotation/revocation、retention 与脱敏策略。
- 未配置 AgentTeams 时也必须能产生 Shadow 价值。

### Deliverables

- Manual HTTP/CLI Signal intake；Public MCP 与 A2A 统一留到 Stage 6，Stage 1 不以 registry 中存在 mapping 冒充 transport 已启用。
- `external_feedback`、`internal_feedback`、`maintainer_report`、Langfuse negative score 的标准化、幂等、关联和补证。
- `LangfuseTraceSource` 的增量 cursor、水位、去重、DLQ、trace snapshot 与 completeness。
- AgentMED 自身 W3C/OTel trace 输出到独立 Langfuse project，形成 export receipt、脱敏结果与明确失败语义；业务状态仍只在 PG。该自观测写路径与读取被治理 Agent 的 `LangfuseTraceSource` 是两条独立验收链，任何一条不能替代另一条。
- First Useful Case：来源、输入/输出摘要、确切版本、完整性、缺失项、evidence digest 与下一步。
- 可安装的 single-node quickstart、readiness/doctor、macOS arm64 与 Linux x64 clean-machine 验收；至少两名非项目开发者在不接触 AgentTeams 内部对象的前提下完成 Shadow activation。

### 主要文件

```text
control-plane/app/api/signals.py
control-plane/app/api/sources.py
control-plane/app/services/signal_service.py
control-plane/app/services/evidence_service.py
control-plane/app/connectors/langfuse/
control-plane/app/workers/connectors.py
control-plane/app/observability/tracing.py
demo-app/app/tracing.py
contracts/v4/schemas/signal-envelope.schema.json
contracts/v4/schemas/agent-run-ref.schema.json
contracts/v4/schemas/trace-evidence-receipt.schema.json
contracts/v4/openapi/public-api.yaml
cli/
console/
deploy/compose.yaml
```

### Migration

- `007_signal_trace_evidence.py`：Signal、Signal↔Case、AgentRunRef、TraceEvidenceReceipt 与 source event uniqueness。
- `008_connector_cursor_dlq.py`：Connector、cursor/watermark、delivery attempt、dead letter 与 replay audit。

两份 migration 均从 `006` 线性前进并提供 upgrade/downgrade 测试；downgrade 不得静默丢弃已固化证据。

当前工作树中的 `007` 是 prototype 阶段的 additive expand migration，但会直接执行 `ALTER TABLE`、约束和索引创建；它没有证明大表上的 zero-downtime。当前规模可在明确 maintenance window 内执行；进入大数据量或生产部署前，必须先评估锁与扫描时间，并按需要拆成 online DDL、分批 backfill、`NOT VALID`/后置校验等可回滚步骤。现有本地 PostgreSQL upgrade/downgrade 验证不能被写成零停机证据。

### 验证

- **focused**：规范化、同源幂等、跨源 link proposal、trace completeness、PII policy、cursor/watermark、DLQ replay、audit failure rollback；AgentMED 自身 OTel export receipt、client-side masking、export 失败不影响 PG 权威写入但必须留下可观测失败；quickstart/readiness/doctor。
- **negative**：重复 webhook、错误 endpoint/version、权限过宽、retention 缺口、恶意 Signal 指令、附件路径穿越/压缩炸弹、跨租户引用。
- **replay**：固定 Langfuse fixture 和无 trace maintainer report，各自重复投递并得到同一 Case/receipt。
- **live**：一条真实 Langfuse 历史低分 trace；一条真实维护人员直报；一条 AgentMED 自身请求真实导出到隔离的 Langfuse project 并按 trace ID 回读核对 receipt。provider 不可达时明确失败，不退回 fixture 宣称 live。

### Evidence

- facets：`contract`、`replay`、`domain-provider-live`
- 路径：`evidence/v4/stage-1/shadow-langfuse/<run-id>/`
- `domain-provider-live` 分开保存读取侧原始 query/window、字段 capability、脱敏摘要和 receipt digest，以及写入侧 OTel export/read-back receipt；两条 manifest 独立判定。
- clean-machine evidence 分别记录 macOS arm64、Linux x64 的安装来源、版本、冷/热启动时间、readiness、First Useful Case 和两名非项目开发者的自助完成结果。

### Exit

- Langfuse 读取侧：真实低分 trace 可被不可变绑定为 First Useful Case，缺字段得到 `PARTIAL/UNKNOWN` 而不是伪造 `COMPLETE`。
- Langfuse 写入侧：AgentMED 自身一条真实请求的 W3C/OTel trace 可按 ID 在隔离 project 回读，脱敏策略和 export failure receipt 均通过；PG 仍是唯一业务权威。
- 无 AgentTeams 时 manual/internal maintainer Signal 仍可工作；macOS arm64 与 Linux x64 clean-machine quickstart 通过，至少两名非项目开发者完成 Shadow activation。
- Stage 1 focused/replay/live、独立 verifier 和 `evidence/v4/stage-1/` 全部通过；任一双接入链或安装路径缺失时不得进入 Stage 2。

### Rollback

关闭 connector poller 和 source，同时禁用 OTel exporter 并撤销/轮换独立 sink credential；保留 007/008 表与 immutable receipts，回落到 manual Shadow intake。任何已创建 Case 不因 connector rollback 被删除；已导出的 Langfuse trace 按冻结 retention/delete 合同处理，不能假装本地禁用已删除远端数据。

### Semantic commits

```text
feat(signal): add durable signal and trace evidence
feat(langfuse): add read-only shadow connector
```

## Stage 2 · Durable Work、Claude Code 与 Coding Team

Stage 2 必须按 2A → 2B → 2C 顺序完成；任何一个子阶段未通过都不能宣称 Coding Team 已跑通。

### Stage 2A · Durable Work Kernel

#### Entry

- Stage 1 至少一个 First Useful Case 可被调查。
- WorkerTask、Attempt、Proposal 与 ProposalDecision schemas 已冻结。

#### Deliverables

- PG 权威 `WorkerTask/lease/fence/Attempt/Proposal/ProposalDecision/reaction ledger`。
- Domain event 与 `work.requested` 在同一事务写入 outbox。
- `work.claim/heartbeat/submit/fail/cancel-request/reconcile` 的内部 API/MCP。
- 实现 §2 的 retry、exhausted、cancel 和 UNKNOWN 恢复语义。

#### 主要文件

```text
control-plane/app/api/work.py
control-plane/app/services/work_service.py
control-plane/app/services/work_coordinator.py
control-plane/app/workers/work_dispatcher.py
control-plane/app/models/tables.py
mcp-servers/servers/work.py
contracts/v4/schemas/worker-task.schema.json
contracts/v4/schemas/attempt.schema.json
contracts/v4/schemas/proposal.schema.json
contracts/v4/events/work-events.yaml
```

#### Migration

- `009_work_kernel.py`：worker_tasks、task leases、attempts、proposals、proposal_decisions、coordinator reactions 与所需 uniqueness/fencing indexes。

#### 验证

- **focused**：claim/heartbeat、lease expiry、fence、idempotent submit、retry budget、terminal immutability、outbox transaction、audit fail-closed。
- **negative**：错误 principal/role、过期 token、跨 task replay、动作后补 Proposal、重复 decision、Coordinator 写领域成功。
- **replay**：crash 前后的事件流重放得到相同任务/Attempt/Decision 投影。
- **live**：无模型 live；使用真实 PG 和真实 outbox worker 的本地 integration。

#### Exit

- WorkerTask/Attempt/Proposal/Decision 的 lease、fence、幂等、同事务 causation 与 `UNKNOWN→reconcile` 全部通过；Coordinator、Adapter 与 Exporter 无法伪造领域成功。
- crash/restart 后没有双 lease、动作后补 Proposal 或不明外部副作用；2A verifier/evidence 通过后才允许 Runtime Adapter 消费任务。

#### Evidence / rollback / commit

- facets：`contract`、`replay`（manifest 数组中分列）；路径：`evidence/v4/stage-2/2a-work-kernel/<run-id>/`
- rollback：停止 dispatcher，禁止创建新 v4 WorkerTask；v3 Case lease 不迁回也不双 lease。
- commit：`feat(work): add durable typed agent work kernel`

### Stage 2B · Claude Code Runtime Adapter

#### Entry

- 2A 通过。
- 本机 Claude Code 版本与兼容 flags 已探测；模型 provider 只通过 secret broker 注入。
- 准备无敏感数据、无外网要求的确定性 fixture repo。

#### Deliverables

- host-side `ClaudeCodeRuntimeAdapter`，不把个人 auth/keychain/config 挂入 Worker 容器。
- 一次性 worktree、精确 argv、`--bare`、严格 MCP、工具 allow/deny、budget、wall timeout、进程组取消和 stream-json parser。
- 父子 Attempt、runtime dispatch/model/tool/artifact receipts。
- GLM-5.2 小型 structured provider smoke；失败时保留原状态，不宣称已接入。

#### 主要文件

```text
runtime-adapters/claude-code/
runtime-adapters/claude-code/tests/
control-plane/app/services/runtime_dispatch_service.py
control-plane/app/services/attempt_recovery_service.py
contracts/v4/schemas/runtime-dispatch-receipt.schema.json
contracts/v4/schemas/model-receipt.schema.json
contracts/v4/schemas/tool-receipt.schema.json
```

#### Migration

- `010_runtime_delegation_receipts.py`：attempt parent/child relation、runtime dispatch、model/tool receipts、capability grants、runtime session reconciliation keys。

#### 验证

- **focused**：argv 构造、schema parser、budget、output digest、worktree isolation、tool allowlist、session correlation、Attempt recovery。
- **negative**：shell injection、global config 泄漏、越界文件、外网访问、修改冻结测试、model resolution/call receipt 与产物错绑、CLI exit 0 但无 artifact。
- **replay**：保存的 stream-json 与 tool receipts 可离线重建相同 terminal observation，但不能伪造一次 live execution。
- **live**：先做最小 GLM structured smoke，再用真实 Claude CLI 在 fixture repo 产生一个受限 patch；两者分开计费/结果记录。

#### Exit

- 固定版本 Claude Code 在隔离 worktree 以 child principal/Attempt claim，产出 schema-valid artifact，并具有独立 runtime/model/tool/Skill/exit receipt；CLI exit 0 本身不能通过。
- 关闭必需 Claude process、删除 receipt、越界读写或造成未知终态时验收失败；GLM provider 未 smoke 时保持 provider `NOT RUN/BLOCKED`，不得静默回落成已接入。

#### Evidence / rollback / commit

- facets：`domain-provider-live`、`claude-runtime-live`；路径：`evidence/v4/stage-2/2b-claude-runtime/<run-id>/`
- rollback：runtime kill switch；停止新 spawn；按 §2 reconcile 或终止进程组；保留 Attempt/receipts，删除一次性 worktree。
- commit：`feat(runtime): add isolated claude-code execution adapter`

### Stage 2C · AgentTeams Coding Team 因果闭环

#### Entry

- 2A/2B 通过。
- 先完成钉版本 AgentTeams v1.2.1 compatibility spike：现有 `delegate_task → ack_task → submit_task` 是 Worker 内部 taskflow/file protocol，不假设存在可供外部 Adapter 直接调用的 Controller Task API。若不能取得真实 Worker identity、wake/ack 与 digest receipt，2C 保持 `BLOCKED`，不得由 Manager/exporter 代跑。
- design-only `agents/coding/team.yaml` 为 `design_status=APPROVED`、`lifecycle_status=NOT_CREATED`、`runtime_status=NOT_RUN`；三个 principal、SOUL、Skill/MCP allowlist 与预算完成审查。
- 现有 `agentmed-team` manifest/SOUL 不被修改为 coding 角色。

#### Deliverables

- 2C 内从已批准 design manifest 生成钉住 AgentTeams 版本的 deployment manifest，记录 design/source digest、生成器版本和语义 diff；审查通过后才 apply。deployment manifest 生成是 2C 的首项交付，不以“2C 已通过”为前置。
- apply deployment manifest，并回查 Team/Worker CR、容器、Matrix、MinIO、SOUL、Skill/MCP allowlist 与预算，形成实际部署 receipt；随后才运行 `agentmed-coding-team` 的 Planner、Generator、Reviewer 因果验收。
- AgentTeams Adapter 负责 wake/dispatch；Worker 必须用原生身份 claim，Adapter 不能代 ack/submit。
- 过渡 bridge 只把 taskflow/Matrix/MinIO 当 transport；若必须改上游，先形成最小结构化 Task API 贡献方案，不把上游 Controller 私有状态复制成 AgentMED 权威表。
- Planner ResolutionContract → Generator Claude 子 Attempt → ChangeProposal → Reviewer `Finding` → Controller Decision → Team 外确定性 Gate 的真实链。Reviewer 只提交 Finding，不拥有或决定 Gate。
- `ParticipationManifest` 只列实际参与角色；未参与角色不进入成功声明。

#### 主要文件

```text
agents/coding/team.yaml
agents/coding/souls/coding-planner.md
agents/coding/souls/coding-generator.md
agents/coding/souls/coding-reviewer.md
agents/coding/skills/
runtime-adapters/agentteams/
mcp-servers/servers/work.py
deploy/
```

#### Migration

不新增 migration；复用 009/010。若 AgentTeams-specific 字段不能放入通用 receipt，先修改 contract/ADR，不得直接加平台专用权威表。

#### 验证

- **focused**：design → deployment manifest 字段/角色/权限等价、生成 provenance、Team/role binding、wake/claim/submit、parent-child Attempt、ParticipationManifest、role-specific MCP ACL。
- **negative**：未批准/摘要不匹配的 design manifest、生成后角色或权限漂移、Worker 关闭、仅 exporter 运行、Adapter 代 claim、错误 Team/role、Skill 只安装未调用、Claude 完成但父 Worker 未提交、Reviewer 与 Generator 同 origin、Reviewer 试图决定 Gate、伪造 Matrix/MinIO 记录。
- **replay**：AgentTeams transport receipts 可回放验证签名/digest；`replay` 不能被标为 `agent-causal`。
- **live**：用 2C 内生成并审查过的 deployment manifest 部署 Team；真实三个 Worker 中实际需要的角色领取任务；Generator 委托真实 Claude CLI；Reviewer 操作真实 sandbox 并只提交 Finding；Team 外确定性 Gate 独立计算终态。关闭任一必需 Worker 后同一测试必须失败。

#### Exit

- 已批准 design manifest → 版本钉定 deployment manifest → apply/resource back-check → 必需 Worker 原生 claim → Claude child Attempt → Reviewer Finding → Team 外确定性 Gate 的 digest/receipt chain 完整。
- 至少三种独立 principal 真实参与；停止任一必需 Worker或只保留 Matrix/MinIO/exporter 记录时 `agent-causal` 必须失败，Team 状态保持 `BLOCKED/UNKNOWN`。

#### Evidence / rollback / commit

- facets：`agentteams-native`、`claude-runtime-live`、`agent-causal`；路径：`evidence/v4/stage-2/2c-coding-team/<run-id>/`
- rollback：suspend coding Team、停止 dispatch、回落 Stage 1 Shadow；现有质量 Team 保持原样。
- commit：`feat(agentteams): add isolated agentmed coding team`

## Stage 3 · Verified Candidate 循环

Stage 3 Entry 还必须把 Stage 0 中仅作为 content-addressed pointer 的
`judge_model_policy_digest` 与 `threshold_digest` 升级为可执行 typed
binding：前者绑定 exact Judge AgentManifest/model-call policy，后者绑定
exact threshold/decision receipt。未完成这两个绑定时，contract fixture
不能升级成真实 Judge 或 live Gate 证据。

Stage 3 按 code-first 实施。Skill/MCP 生产安装仍留在 Stage 5。

### Stage 3A · Candidate/Evaluation Kernel + Fixture

#### Entry

- Stage 2 的 `agent-causal` facet 通过。
- fixture repo 有冻结失败测试、允许修改面和公开验收，不依赖外部写权限。

#### Deliverables

- ResolutionContract、CandidateContract、Candidate revision、Finding、EvaluationPlan/HoldoutBinding、sandbox execution 与 GateReport。
- coding Agent 修复确定性 fixture；Reviewer 能拒绝 Stub、删测试和表面完成。

#### 主要文件

```text
control-plane/app/api/candidates.py
control-plane/app/services/candidate_service.py
control-plane/app/services/evaluation_service.py
eval-harness/eval_harness/sandbox/
runtime-adapters/repo/
contracts/v4/schemas/resolution-contract.schema.json
contracts/v4/schemas/candidate-contract.schema.json
contracts/v4/schemas/finding.schema.json
contracts/v4/schemas/evaluation-plan.schema.json
```

#### Migration

- `011_candidate_evaluation.py`：resolution/candidate contracts、candidates/revisions、findings、evaluation plans/executions/reports。
- `012_repo_change_candidate.py`：repo snapshot、base revision、worktree/candidate artifact binding 与 immutable diff digest；不包含 GitHub 写操作。

#### 验证

- **focused**：revision/digest、allowed change surface、sandbox result、hidden binding、Finding/Decision、Gate fail-closed。
- **negative**：删/改测试、Stub、超范围 diff、读取 holdout、依赖/网络逃逸、伪造 test output、平均分覆盖硬失败。
- **replay**：同一 repo/base/candidate/eval plan 重放得到可比较结果；环境差异明确记录。
- **live**：真实 Coding Team 修复本地 fixture，并由独立 Reviewer + deterministic Gate 验证。

#### Exit

- pre-build Resolution/Candidate/EvaluationPlan 与 post-build CandidateRevision/GateReport/WorkOrder 精确绑定；修改测试、越界 diff、假终态、自评自批和换绑均 fail closed。
- Coding Team 在本地 fixture 上产出可复验 patch，独立 Reviewer 与确定性 Gate 一致给出可解释终态；无远端写。

#### Evidence / rollback / commit

- facets：`agent-causal`、`repo-sandbox`；路径：`evidence/v4/stage-3/3a-coding-fixture/<run-id>/`
- rollback：拒绝 Candidate、销毁 sandbox/worktree，保留所有 Findings 与结果。
- commit：`feat(candidate): add verified repo change loop`

### Stage 3B · 真实 GitHub Issue，本地 verified draft

2026-08-10 的只读候选快照如下；真正开工时必须重新核对 issue、关联 PR、assignee、默认分支和贡献规则，任何远端写仍走 §5 逐动作授权：

| 顺位 | 候选 | 本地纵切价值 | 开工前硬门 |
|---|---|---|---|
| 1 | [MCP Go SDK #1154](https://github.com/modelcontextprotocol/go-sdk/issues/1154) | 小范围 session leak、无凭据本地 repro、回归测试清楚 | 先在 issue 获 maintainer 确认；未确认只允许本地复现/patch |
| 2 | [Goose #11059](https://github.com/aaif-goose/goose/issues/11059) | 并行测试环境变量竞态，适合 crash/flake 证据 | board 必须确认是 `Ready`；状态未知时不得实现/PR |
| 3 | [Aider #934](https://github.com/Aider-AI/aider/issues/934) | GUI 未跟踪文件 crash，改动面较小 | 必须在最新 main 重现并接受 CLA/贡献要求 |

首选失效时按同一选择合同换候选，不为保住演示而忽略上游规则或已有 PR。

#### Entry

- 3A 通过。
- 候选 issue 仍 open、最新主干可复现、没有冲突的 active PR，并已读取 CONTRIBUTING/CLA/license/security policy。
- 只允许读公开元数据和本地 clone/fetch；没有任何外部写授权。

#### Deliverables

- 固定 issue JSON、关联 PR 搜索结果、贡献规则、license、base SHA 和 baseline failure。
- Coding Team 产出最小本地 patch、focused/full required tests、Reviewer Finding、GateReport 与 draft PR 文本 artifact。
- 明确 `NO REMOTE WRITE PERFORMED`。

#### 主要文件

```text
connectors/github/read_only.py
runtime-adapters/repo/github_snapshot.py
eval-harness/scenarios/coding/
evidence/v4/stage-3/3b-real-issue/
```

#### Migration

无新增 migration；复用 011/012。GitHub issue 正文只作为不可信 Signal/Evidence，不增加 provider-specific Case 状态机。

#### 验证

- **focused**：最新 base reproduction、target tests、项目规定的 lint/type/build/test。
- **negative**：issue prompt injection、关联 PR 已存在、issue 已关闭/被认领、需 secret/付费服务、宽泛重构、生成文件/许可证越界。
- **replay**：保存的 issue/base/candidate 可在固定环境重跑；若上游主干变化，旧结果保留、创建新 snapshot/Attempt。
- **live**：真实第三方 repo 的本地修复与验证；不得 comment、claim、fork、push 或开 PR。

#### Exit

- issue 状态、贡献规则和 base SHA 均在运行时重新核验，真实失败可重现；Coding Team 给出最小本地 patch、项目规定测试、Finding、GateReport 与 draft PR 文本 artifact。
- evidence 明确 `NO REMOTE WRITE PERFORMED`；issue 已关闭、被认领、有冲突 PR 或需要 secret 时必须退出或换候选。

#### Evidence / rollback / commit

- facets：`domain-provider-live`、`agent-causal`、`repo-sandbox`；路径：`evidence/v4/stage-3/3b-real-issue/<run-id>/`
- rollback：删除本地 worktree/clone cache；保留公开 source snapshot、patch 和测试证据；远端无状态可回滚。
- commit：`test(coding): verify a real issue resolution locally`

### Stage 3C · Prompt Candidate 复用

#### Entry

- 3A/3B 通过。
- v3 B1 Scenario 有冻结 VersionSet、probe/eval 与历史兼容契约。
- D-012 cutover gate 已通过：相关 v3 in-flight run 均已 drain/terminalize，或由 crash-injection 验证过的 reconciler 恢复；同一 routing key 无 v3 Case lease 与 v4 WorkerTask lease 并存。未通过时本子阶段保持 `BLOCKED`，不能用新 runner 绕过恢复债。

#### Deliverables

- 将同一 Candidate/Revision/Finding/Gate 内核用于 Prompt candidate，不复制一条 Prompt 专用超级 runner。
- 现有 `agentmed-team` 继续处理质量场景；新 coding Team 不冒充 B1 质量角色。
- 证明 code 与 prompt 两类 Candidate 共享权威内核、但使用不同 Adapter/sandbox。

#### Migration

无新增 migration；必要的 candidate subtype 采用 additive schema/version，不另建平行状态机。

#### 验证

- **focused**：Prompt VersionSet/diff、公开 eval、隐藏 confirmation、Gate/WorkOrder binding。
- **negative**：复用 pre-change GateReport、候选偷看 holdout、Exporter 生成 repairer 产品、Candidate subtype 绕过权限。
- **replay**：B1 contract/replay 与历史 evidence 分开；不得改写历史 live。
- **live**：只有新 agent-causal 语义需要新跑；旧 provider/domain live 作为历史证据保留，不全量重跑。

#### Exit

- Prompt candidate 复用与 code candidate 相同的 Proposal/CandidateRevision/Gate/WorkOrder 内核，且 v3→v4 cutover 无双 lease、无 exporter 冒充 repairer。
- 兼容测试与受影响的新因果边界通过；未受影响的历史 provider/domain live 保持原 provenance，不因阶段推进被重跑或升级口径。

#### Evidence / rollback / commit

- facets：`contract`、`replay`；具备前置时另列 `agent-causal`。路径：`evidence/v4/stage-3/3c-prompt-candidate/<run-id>/`
- rollback：关闭 v4 prompt adapter，继续使用 v3 Scenario contract；不迁回或改写已生成 v4 Candidate。
- commit：`feat(candidate): reuse governed candidate loop for prompts`

## Stage 4 · Guarded External Operations

### Entry

- Stage 3 verified local Candidate 已通过 Gate。
- 若 Candidate 来源于 v3 Scenario，D-012 cutover receipt 必须证明旧 run 已终态、旧 lease 已失效且没有双 lease；否则不得创建外部操作。
- 外部动作 target、风险、blast radius、rollback、预算和 reconcile API 已冻结。
- 对本次具体动作取得人类 ApprovalGrant；Stage 3 的“开始修复”授权不自动包含 Stage 4 写权限。

### Deliverables

- PolicyGrant、`ApprovalGrant`（human authority）、RepoOperation、operation attempt、unknown-outcome reconcile 和 kill switch。
- GitHub credential 只在 Repo Controller/Secret Broker 中；Agent、Claude Code、Reviewer、Matrix 和 artifact 均不可见。
- 第一轮最多到 draft PR，不自动 merge。

### 主要文件

```text
control-plane/app/api/repo_operations.py
control-plane/app/services/repo_operation_service.py
control-plane/app/services/policy_grant_service.py
control-plane/app/workers/repo_controller.py
runtime-adapters/repo/github.py
contracts/v4/schemas/repo-operation.schema.json
contracts/v4/schemas/policy-grant.schema.json
```

### Migration

- `013_external_operation_policy.py`：PolicyGrant、ApprovalGrant binding、ExternalOperation、operation attempts、provider resource identity 与 reconcile state。

### 验证

- **focused**：approval hash/nonce/expiry、repo/base/diff/action binding、idempotency、CAS、secret isolation、reconcile。
- **negative**：Agent self-approve、过期/重放 grant、base 漂移、同 approval 换 diff/action、provider 响应丢失、权限撤销、branch protection、kill switch。
- **replay**：provider fake 仅作 contract/replay；不能当真实 GitHub 写证据。
- **live**：仅在对应逐次授权后执行；保存 exact request intent 与 provider receipt，响应未知时查询精确 remote resource 后再定终态。

### GitHub 逐次授权矩阵

以下每项都是独立外部动作；前一项获批不授权后一项：

| 动作 | 是否必须单独授权 | ApprovalGrant 必须绑定 |
|---|---|---|
| 在 issue 留言或声明认领 | 是 | repo、issue、消息 digest、调用身份、expiry |
| 在用户账号/组织创建 fork | 是 | source repo、目标 owner、默认可见性 |
| 创建远端 branch | 是 | repo/fork、branch name、base SHA |
| push commit | 是 | branch、base SHA、commit/tree/diff digest；禁止 force-push |
| 创建 draft PR | 是 | head/base、title/body digest、commit SHA、issue link |
| 更新已有 PR/追加 push | 是 | PR、expected head、new commit/diff digest |
| 标记 Ready for Review | 是 | PR、expected head、当前 GateReport |
| merge | 是，且不属于第一轮 | PR、exact head、merge strategy、最新 Gate/approval |
| 关闭 PR、删除远端 branch 或其他清理 | 是 | exact remote resource 与可恢复性说明 |

任何授权都不能用自然语言 issue/PR 内容替代，不能由 Agent token 产生；unknown outcome 必须通过 GitHub API 对 exact owner/repo/ref/PR reconcile，不能盲重试。

### Evidence

- facets：`human-authorized-external` 证明 exact 人类授权；`domain-provider-live` 独立证明真实 GitHub API 写入与 provider receipt。两者缺一不可，不能互相替代。
- 路径：`evidence/v4/stage-4/guarded-repo/<run-id>/`
- 公开 evidence 只保留 credential-free receipt、remote resource ID/URL、commit/diff digest 和批准引用。

### Exit

- 每个实际 GitHub 写动作都由独立 human ApprovalGrant 绑定 exact repo/base/diff/action/nonce/expiry，Agent/service principal 无法自产批准或取得 credential。
- 在 fork、branch、push、创建 draft PR 各自逐次获批后，真实 draft PR 完成 provider receipt 与 unknown-outcome reconcile；comment/claim、Ready、merge、关闭 PR 或删除远端资源未获各自独立批准时一律未发生。

### Rollback

停止未来动作、撤销 grant/credential、关闭 draft PR 或删除远端 branch 都是新的外部动作，必须在原 PolicyGrant 明确包含补偿动作或重新获批后执行。不能用 `git reset --hard`、force-push 或删除审计记录“回滚”。

### Semantic commit

```text
feat(repo): add approval-bound external operation controller
```

## Stage 5 · Skill/MCP 自进化与阿里云

### Entry

- Candidate/Gate/ExternalOperation 内核稳定。
- 内部 Registry owner、权限 diff、builder/Gate/Release 分离和供应链 contract 已冻结。
- 阿里云 live 使用最小 RAM、明确计费/区域/retention 前置；未配置时保持 `NOT RUN`。

### Deliverables

- Skill/MCP Package、Candidate、Version、Assignment、Release、Revocation 与 EffectivePermissionGrant。
- quarantine builder、SBOM、provenance、signature、hidden temporal holdout、old/candidate/no-skill 三臂。
- 固定版本 skill-up adapter、AISC 独立扫描、MSE distribution、官方只读 SLS Skill。

### 主要文件

```text
control-plane/app/api/capabilities.py
control-plane/app/services/skill_registry_service.py
control-plane/app/services/capability_release_service.py
runtime-adapters/skills/
connectors/aliyun/
contracts/v4/skills/
agents/coding/skills/
```

### Migration

- `014_skill_mcp_registry.py`：package/candidate/version/assignment/eval/revocation。
- `015_capability_distribution.py`：permission grants、build/scan attestations、distribution/release attempts 与 reconcile。

### 验证

- **focused**：tree/artifact/manifest digests、signature/provenance、permission diff、三臂 eval、assignment/canary/revoke。
- **focused**：除 Skill 纵切外，至少一个自有 MCP package 完成 Candidate→Version→Assignment→schema/tool-call Gate→canary→revoke，证明 MCP 复用同一治理内核而非只存在负测试。
- **negative**：路径穿越、symlink/压缩炸弹、恶意依赖构建、凭据文件、holdout 泄漏、自改 evaluator、远端 MCP schema rug-pull、Registry 重打包。
- **replay**：固定 package/scan/eval fixtures；供应商 receipt replay 不等于 live scan/publish。
- **live**：官方 SLS Skill 由真实 Worker 调用；自有 Skill 经首次人批发布固定版本、下载重验、真实 Worker canary、rollback/revoke/compensation；自有 MCP 固定版本由真实 Worker 按冻结 schema 调用后完成 revoke，并证明旧 assignment 不再可用。

### Evidence

- facets：`contract`、`replay`、`domain-provider-live`、`agent-causal`、`human-authorized-external`
- 路径：`evidence/v4/stage-5/skill-mcp-evolution/<run-id>/`
- 各 facet 使用独立 manifest，不用 MSE/AISC receipt 代替功能 Gate、签名或 provenance。

### Exit

- 一条 Skill 与一条 MCP 正向治理链分别完成 immutable version、permission diff、独立 Gate、真实 Worker canary、rollback/revoke；关闭旧 assignment 后旧版本不能继续调用。
- 比赛/中国首发必选的阿里云 slice——官方 Skill 真实 Worker 调用、AISC 扫描、自有 Skill 固定版本分发与下载重验——必须全部 `PASS`；任一 `BLOCKED/NOT RUN` 时 Stage 5 保持 `BLOCKED`。供应商 receipt 不得代替功能、安全、签名或 provenance Gate。

### Rollback

停止 assignment、revoke 当前版本、恢复上一固定版本、补偿外部分发 UNKNOWN；内部 immutable version 和审计保留。Stage 5 完成前 Guarded Autopilot 不得扩大 Skill/MCP 权限或自动 publish。

### Semantic commits

```text
feat(skills): add governed skill and mcp evolution
feat(aliyun): add evidence-bound skill distribution adapters
```

## Stage 6 · Agent-native 公共入口

### Entry

- Canonical application intents、resource IDs、error taxonomy 和 N/N-1 compatibility 已稳定。
- Human、external agent、connector、internal worker/controller principal 明确分离。

### Deliverables

- remote Public MCP + OAuth，本地 stdio MCP。
- portable Skills 与 Claude Code/Codex/Qwen Code 薄 Adapter。
- Python/TypeScript SDK、webhook 管理与条件化 A2A adapter。
- 所有入口只调用共同 application service；不暴露 `work.claim`、role token 或 execute authority。

### 主要文件

```text
public-api/
cli/
mcp-servers/servers/public_gateway.py
skills/
plugins/claude-code/
sdks/python/
sdks/typescript/
connectors/webhooks/
```

### Migration

- `016_public_principals_webhooks.py`：public principals/scopes、OAuth/client binding、webhook registrations、delivery/replay state。

### 验证

- **focused**：HTTP/CLI/MCP/SDK intent mapping、idempotency/resource/audit parity、OAuth audience/scope、SSE cursor/webhook replay。
- **negative**：Agent token human approve、MCP 获得 release execute、caller 自报 role、跨 workspace key、client timeout 取消服务端任务、schema downgrade。
- **replay**：相同 intent 经多入口得到相同权威 resource/operation 与 error code。
- **live**：Claude Code、Codex、Qwen 中至少两个客户端真实执行“报 Signal→启动/观察 run→读 evidence”；不进行人批或 release。

### Evidence

- facets：`contract`、`replay`（分别记录，不合并别名）
- evidence category：`client-live`；这是客户端互操作运行类别，不是 success facet
- 路径：`evidence/v4/stage-6/agent-native/<run-id>/`

### Exit

- HTTP、CLI、stdio/remote MCP 与首批 SDK 对同一 intent 返回相同 resource/operation/error/audit 语义；至少两个 Agent 客户端完成报 Signal、启动/观察 run、读取 evidence。
- Agent token 无法审批或执行 release；客户端断开不取消 durable operation；A2A 没有真实 partner 时保持条件性交付而不阻断已验证入口。

### Rollback

逐 Adapter 禁用、撤销 token/webhook secret；核心 HTTP 和已创建 durable operation 保持可查。客户端 detach 不取消服务端权威任务。

### Semantic commit

```text
feat(public-api): add agent-native governed entrypoints
```

## Stage 7 · 开源生产成熟

### Entry

- 至少两个非同构 workload 已在非生产环境形成 verified proposal。
- single-node production 的数据、secret、backup、upgrade 与 SLO owner 明确。
- 没有因“最终要做 K8s”跳过单机 Day-2 和真实恢复。

### Deliverables

- 先完成 pin 版本的 single-node production profile，再按真实需求完成 K8s/VPC/HA。
- 租户/principal/evidence/credential/release target 隔离；retention、delete、legal hold 与 audit retention 分离。
- backup/restore、upgrade/rollback、key rotation、support bundle、DLQ replay、unknown outcome reconcile。
- connector SDK、贡献者文档、许可证/SBOM、安全审查和真实 pilot。

### 主要文件

```text
deploy/compose.yaml
deploy/production/
deploy/kubernetes/
docs/operations/
docs/security/
docs/contributing/
connectors/sdk/
```

### Migration

- `017_tenant_retention_operations.py`：tenant isolation metadata、retention/delete/legal-hold operations 与审计绑定。
- 后续 production migration 只按已冻结 schema 追加；禁止为“阶段数字完整”预建空表。

### 验证

- **focused**：tenant ACL、backup/restore、key rotation、migration N/N-1、retention/delete/legal hold、support bundle 脱敏。
- **negative**：跨租户缓存/dedup、旧 token、部分 restore、升级中断、PG/object store 不一致、公开 evidence secret/PII 泄漏。
- **replay**：灾难恢复与 unknown outcome reconcile 演练可重复执行。
- **live**：两个非同构真实 workload；single-node 故障恢复；具备条件时再跑 production canary/HA，而不是用本地 Compose 冒充生产。

### Evidence

- facets：`contract`、`replay`；`production-rehearsal` 只写入 evidence category/metadata，不是 success facet
- 真实生产条件满足并实际执行 canary 后，才单独增加 canonical `production-canary` facet
- 路径：`evidence/v4/stage-7/production/<run-id>/`
- 必须量化安装到 First Useful Case、verified proposal、guarded release 的时间、人工触碰、成功率、UNKNOWN 和成本。

### Exit

- 两个非同构 workload、single-node 安装/恢复、N/N-1 upgrade/rollback、backup restore、key rotation、tenant/PII/retention 边界与公开 support bundle 全部通过。
- K8s/HA 与 `production-canary` 只有在真实条件下运行后才算对应 facet；未运行不阻断 single-node 开源成熟，但必须保持 `NOT RUN`，不得由 Compose rehearsal 代替。

### Rollback

使用发布清单和 N/N-1 兼容版本回滚应用；数据库按 expand/backfill/contract 计划停止在兼容点，禁止破坏性倒库。restore 演练使用独立目标并验证 digest/row counts。凭据轮换失败时撤销新凭据并恢复已验证的旧路径，不暴露 secret。

### Semantic commits

```text
feat(platform): harden open-source production profiles
docs(release): publish verified adoption and recovery evidence
```

## 4. 每个 Stage 的统一完成清单

```text
[ ] Entry 条件逐项有证据，不以“应该具备”代替
[ ] Migration upgrade/downgrade/compatibility 测试通过
[ ] Focused tests 通过
[ ] Negative/fail-closed tests 通过
[ ] Replay 与 live facet 分开
[ ] live 外部前置真实可用；未运行保持 NOT RUN
[ ] Attempt/receipt/artifact/decision digest 可追溯
[ ] Verifier 无未解决 finding
[ ] Rollback/kill switch/reconcile 实际演练
[ ] evidence/v4/... 包完整且无 secret/PII
[ ] PLANS.md、PROJECT_STATE、LAST_HANDOFF 更新
[ ] Semantic commit 只包含本 Stage closure
[ ] push/PR 前再次取得用户授权
```

## 5. 首条完整纵切的固定顺序

```text
maintainer_report 或只读 GitHub issue snapshot
→ QualityCase
→ coding-planner 复现与 ResolutionContract Proposal
→ Controller 冻结合同
→ coding-generator 父 Attempt
→ Claude Code + GLM-5.2 子 Attempt
→ immutable local patch Proposal
→ coding-reviewer Finding
→ deterministic sandbox/Gate
→ verified local draft
→ 停在外部写授权门前
→ 用户逐项批准 comment/fork/branch/push/draft PR
→ Repo Controller 执行并 reconcile
→ regression/closure evidence
```

这条纵切跑通前，不并行建设自动 merge、复杂 HA 或多种 Candidate 的生产发布。跑通后再复用相同内核扩展 Langfuse 负反馈的 Prompt 修复、Skill/MCP 自进化与更多 Agent runtime。
