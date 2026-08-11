# CaseLoop V5 progressive delivery blueprint

> 状态：**ACCEPTED TARGET BLUEPRINT（2026-08-11；freeze: `8dd25ca` + `b3727d7`）**
>
> 更新：2026-08-11
>
> 目标：从已完成的 V4 Stage 1A 出发，以可独立验证和回滚的步骤建设 AI 应用系统治理与 Agent-native 能力面。
>
> 当前施工、work package、stop gate 与提交边界以
> [`v5-master-execution-plan.md`](v5-master-execution-plan.md) 为准；本文保留目标语义、
> 历史 freeze 和 stage exit，不作为 current worktree 状态页。
>
> D-015 已接受 V5 为新产品/领域开发默认设计与施工基线，V3/V4 为 compatibility lanes；
> 这不是 runtime/public cutover proof。当前执行必须先完成
> [`v5-architecture-convergence.md`](v5-architecture-convergence.md) 的 C0–C5，D2、R3、
> R4 与 V5-2+ 在此之前保持锁定。

本蓝图面向能够冷启动接手任务的后续 Agent/贡献者。每个步骤都必须先读本文件、
`AGENTS.md`、`docs/product-principles.md`、`docs/plan-v5.md`、
`docs/context/PROJECT_STATE.md` 和 `docs/context/LAST_HANDOFF.md`。V5-0 契约
基线已于 2026-08-11 冻结并接受；D-015 进一步确定默认开发 lane。当前先按 C0–C5
收敛模块化单体与 compatibility façades，收敛完成后 V5 stages 再按下面更新后的依赖图施工，
每个 stage 完成 focused tests、verifier、evidence 与 semantic commit 后才能进入
下一 stage。V4 S1B–S7 仍冻结。接受契约不等于实现 runtime：每个 stage 的 runtime
facet 只有在真实 evidence 出现后才可标记。

## 0. 全局规则

- 当前收敛分支是 `codex/v5-convergence`；原 `codex/v4-foundation` 的 mixed judge/live/R3+
  WIP 已登记并隔离，不混入 C0-C5。
- V4 S1A public routes、payload、digest、authority receipt 和 evidence 保持兼容。
- V3/V4 是显式 compatibility lanes；compatibility façade 可验证、翻译与调度，但没有
  domain success authority。
- public API/CLI 默认 major 与 active routes 不因 D-015 或 C0–C5 改变。
- authoritative core 是模块化单体；每个 authoritative business transaction 使用一个
  PostgreSQL unit of work，required row/event/outbox/audit/receipt/idempotency 一起提交或回滚。
- 新持久化状态必须走 Alembic migration；禁止 `create_all`。
- 每个 Stage 使用自己的 focused tests、verifier、evidence 和 semantic commit。
- external write、付费 provider、真人审批和 production action 仍逐次授权。
- `contract/replay/domain-provider-live/agentteams-native/claude-runtime-live/agent-causal/repo-sandbox/human-authorized-external/production-canary`
  分开报告；未运行保持 `NOT_RUN`。
- 如果审阅发现重复 owner、不可达状态、隐式成功或迁移双 lease，Stage 立即 NO-GO。

## 1. 依赖图

```text
V5-0A Product/ADR alignment
  └── V5-0B Domain/ownership contracts
       ├── V5-0C Compatibility + wire slice
       │    └── R2 Application catalog contract/replay boundary
       │          └── C0 Authority/clean branch/WIP characterization
       │                └── C1 Single-source wire + activated-operation compiler
       │                      └── C2 Records/event specs/graph verifier foundation
       │                            └── C3 Capability/import-cycle/service decomposition
       │                                  └── C4 Generated transport cutover
       │                                        └── C5 Compatibility cleanup/enforcement/recovery
       │                                              └── D2 Complete version-graph contract
       │                                                    └── R3-full System version/diff
       │                                                          └── R4 First System Case
       │                                                                └── V5-2A Durable Work Kernel
       │                                                                      └── V5-2B Async public intents
       │                                                                            ├── V5-2C Agent-native transport
       │                                                                            └── V5-3 Evidence/Episode
       │                                                                                  ├── V5-3 Attribution（可选/并行）
       │                                                                                  └── V5-4 Candidate/Evaluation/Gate
       │                                                                                        ├── verification-only → VerifiedCandidate / NOT DEPLOYED
       │                                                                                        └── release-applicable → V5-5 External operations/recovery

V5-6 operations modules begin only after the relevant V5-5 invariants exist.
```

历史目标依赖曾允许 `V5-1A` 与 `V5-2A` 在 V5-0C 后并行；D-015 的当前执行裁决不采用该
并行入口。必须先按 `R2 → C0 → C1 → C2 → C3 → C4 → C5 → D2 → R3-full → R4` 关闭
authority、module、transaction 与 compatibility 收敛，之后才允许 V5-2A。R3-bootstrap
只是 one-shot holding lane，不能从它推导 R4 或 V5-2A 解锁。
`V5-2C` 与 V5-3、Console read model 可
并行，V5-3 不以 A2A/MCP runtime 通过为前置。Attribution 只有在 workload 把它声明为
required 时才阻塞 Gate；比赛可诚实 abstain 后直接进入独立 Candidate/Gate。所有入口
均不得复制 application service。

### 1.1 C0–C5 收敛规则

- C0 接受并复核 D-015/convergence plan，建立 clean branch、WIP inventory 与 characterization；
  文档或计数存在不等于 C0 DONE；
- C1 以 JSON Schema 2020-12 + intent registry 建立 single-source wire 和
  activated-operation compiler，generated/legacy validators 先做 shadow dual validation；
- C2 在 C1 之后抽取 records、event specifications 与 exact-binding graph verifier foundation，
  不承载业务 command 或 transport activation；
- C3 消除 capability/import cycles，并按 canonical owner 拆 coordinator/service/repository，
  保持一个 PostgreSQL UoW，不增加 D2/R3/R4 行为；
- C4 将 OpenAPI/Python/TypeScript/CLI/router/capability façades切到 generated artifacts，保留
  shadow parity、per-surface fallback，默认 major 不变；
- C5 只在 C4 parity 后清理已证明 obsolete 的 compatibility duplication，启用 effective
  boundary enforcement，完成 recovery 与 independent P0/P1 review，只解锁 D2。

不能把 C1 改成纯 module inventory，也不能在 C1 wire 单一来源之前启动 shared foundation
或业务 decomposition。C0 characterization 的 LOC/model/handler 数只定义结构风险和范围，
不是 bug、dead-code、安全删除或 implementation proof。

任一 wave 出现 dual owner、split transaction、public wire/error/audit/receipt/idempotency/replay
漂移、隐式 V2 default、locked intent discovery、V3/V4 history rewrite 或后续 stage 混入，立即
`NO-GO`。回滚先停受影响的 V5 route/capability/dispatcher，保留 append-only facts，继续安全的
V3/V4 compatibility service，并从最早失败 wave 重新准入。

R2 当前只可报告 `contract=PASS` 与 `replay=PASS`。最终 subject SHA 和 evidence bundle digest
按产品 owner 指示标记 `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`，在最终项目收口前不得
生成 placeholder 或改称 evidence/runtime PASS。

## 2. V5-0 — 设计、owner 与兼容冻结

> 状态：V5-0B/0C 已于 2026-08-11 关闭（`8dd25ca`、`b3727d7` 独立验收 PASS）。
> V5-0A 的产品决策已接受，但 D-013/product-principles 的 clean-checkout 文档包装在当前
> repair 中重新打开；这不改写 0B/0C 历史 freeze。本节保留历史 context 与 exit，当前
> repair 见 Master Plan R0。

### V5-0A · Product boundary and supersession

#### Context

V4 S1A 已完成，但 V4 后续顺序不再匹配产品方向。D-013 只接受产品边界；V5 PRD、
plan 和 contracts 在 V5-0A 开工时仍是 draft。以下 Tasks/Verification/Exit 保留当时的
施工记录；当前 accepted baseline 与 partial runtime 状态见本文件顶部和 Master Plan。

#### Tasks

- 复核 `docs/prd-v5.md` 和 `docs/plan-v5.md`；
- 明确 V4 各阶段 keep/generalize/adapter/defer/deprecate；
- 更新 README、Wiki、PLANS 和 context 的权威层级；
- 吸收两份独立产品/评测 review 已被用户接受的共同结论：验收标准来源、workload split、
  CLI-first onboarding、现实 Gate 自验证和条件式 release；不删除完整 V5 target；
- 保留 v3/v4 当前实现事实与 evidence provenance；
- 不修改 runtime code 或 migrations。

#### Verification

- Markdown links 可解析；
- 当时尚未实现的 V5 能力均标注 target/not implemented；
- `rg` 不再把 S1B–S7 写成下一施工步骤；
- 独立产品/评测审阅的成立需求、未验证假设和过重设计已有明确处置；

#### Exit / rollback

- Exit：产品定位、anti-goals、V4→V5 关系和施工暂停无冲突。
- Rollback：撤销当时的 draft 文档引用；不触碰 S1A runtime/evidence。
- Commit：`docs(v5): define ai-system governance boundary`

### V5-0B · Domain, ownership and state contracts

#### Context

不得创建第三套 Case/Change/Release authority。V3 `ChangeSet` 保持 Scenario aggregate。
V4 Candidate/Evaluation/Gate/WorkOrder schema 均为 closed，V5 必须通过 schema-major-2
system profiles 保留相同逻辑 owner 与已有 lifecycle，不能声称 JSON subtype/additive
extension；V4 仅命名但未实现的人类 Approval lifecycle 由 V5 显式补齐。

#### Tasks

- 冻结 `AIApplication/SystemComponent/ComponentRevision/DependencyEdge`；
- 冻结 `SystemVersionSet/SystemAssignment`；
- 冻结 common record envelope、V5 exact-record-binding 与 AuthorityReceipt major-2；
- 冻结 `SystemEpisodeView/Snapshot/ObservedStateSnapshot/ExternalEffectReceipt`；
- 冻结 additive CaseReadiness、BadcaseSpec/AcceptanceCriteria provenance 与现有
  ResolutionContract owner 的 exact binding；不创建第四套 Case lifecycle；
- 冻结 schema-major-2 `SystemCandidateRevision/EvaluationPlan/GateReport/WorkOrder/
  ApprovalGrant/CapabilityLease/ExternalOperation` 与 pre-Gate `ReleasePlan`；
- 建立 owner/command/event 与 projection no-command matrix；
- 建立状态机 reachability、UNKNOWN reconcile 和 terminal immutability fixtures；
- 明确 fact class 与 canonical evidence facet 正交。
- 冻结 `CODE_CHANGE/AI_BEHAVIOR_CHANGE` 与 `LIBRARY_OR_OFFLINE/DEPLOYED_SERVICE`
  profile、dimension applicability、`CANDIDATE_VERIFICATION/RELEASE_AUTHORIZATION` purpose；
- 冻结 Judge `N/A/advisory/required`、required calibration binding 与 workload-baseline
  repetitions/threshold 语义；
- V5 contracts 只新增/组合系统资源，并显式引用 v4 Work/Candidate/Gate/WorkOrder
  安全合同；禁止复制同名状态机或创建第二 owner。

#### Target files

```text
contracts/v5/domain-model.yaml
contracts/v5/aggregate-ownership.yaml
contracts/v5/state-machines.yaml
contracts/v5/schema-profiles.yaml
contracts/v5/events.yaml
contracts/v5/fixtures/
contracts/conformance/test_v5_*.py
```

#### Verification

- 每个 command 只有一个 owner；
- no-duplicate-lifecycle 检查证明 v3/v4/V5 没有同名异义 owner；
- `SystemEpisodeView`、`SystemReleaseView`、A2A Task 无领域命令；Gate 只绑定 immutable snapshot；
- desired/observed/effect 互不推断；
- unknown before retry；
- verification-only PASS 不能产生 WorkOrder；ReleasePlan→release-authorizing Gate→WorkOrder
  →human Approval→ExternalOperation 链不可绕过；
- Issue/Agent proposal 不能自我确认 acceptance；非 PASS 不能由人原位改写为 PASS；
- Executor 不能在审批后补 release 参数，adapter receipt 不能单独发布成功；
- direct restore/resume 不构成第二 rollback authority；
- 每个 aggregate creation event 和 transition event 均被 machine 消费；
- v3/v4 contracts 独立通过。

#### Exit / rollback

- Exit：contract verifier 和对抗审阅 PASS；runtime facets 全部 `NOT_RUN`。
- Rollback：删除未进入 migration 的 draft namespace；不改 v4 schema。
- Commit：`test(contracts): freeze v5 system governance ownership`

### V5-0C · Compatibility and first wire slice

#### Context

S1A immutable Signal/QualityCase/Evidence payload 含 Agent-era binding，不能原位改写。
V5 需要 additive link 与 route receipt。

#### Tasks

- 冻结 v3/v4/V5 expand/backfill/route/contract 规则；
- 冻结 `ApplicationCaseBinding`，引用 S1A subject id/digest；
- 冻结 acceptance proposal/get/confirm wire 与 additive Case readiness read path；
- 冻结 first-slice public auth/error/idempotency/cursor/compatibility；
- V5 skeleton intent 不生成 route/CLI/SDK/discovery；
- 冻结 API major/minor 与 N/N-1 handshake；V2 CLI 显式 `--api-version 2`，默认继续 V1，
  URL/header/response mismatch 或 downgrade 拒绝并审计；
- 冻结 public/internal principal allowlist、server-derived trust role 与 resource visibility；
- V5 system resource 使用 `/api/v2`；V4 S1A `/api/v1` 保持兼容 façade，二者调用同一
  kernel；
- 为 signal/case/evidence old receipt 建立不变性 fixture。

#### First slice

```text
capabilities.get
system-manifests.import
applications.get
system-versions.get
system-versions.diff
cases.bind-application
case-application-bindings.get
acceptance-criteria.propose
acceptance-criteria.get
acceptance-criteria.confirm
```

#### Verification

- 旧 S1A response/receipt byte-for-byte regression；
- same key/same request replay；same key/different request conflict；
- cross-workspace reference、caller self-report owner、route downgrade 全部拒绝；
- 旧/新 authority 不双写同一 fact。
- 从空 V5 领域表（已有 V4 Controller trust roots 与既有 V4 Case）经 trusted manifest
  import 调用每个 canonical owner，在同一 local PG transaction
  构造 Application/Environment/Component/Revision/Topology/Version/BootstrapAttestation/
  Assignment；任一步失败整笔回滚，禁止 `system-versions.record` 暗中创建前置对象；
- manifest import 仅允许 integrator/catalog-admin/trusted-builder；`external_agent` 不能自报
  owner、declared deployed version、observed state 或 effect；
- binding write 丢失响应或断线后，CLI/Agent/Console 可经 read intent 权威回读 exact
  `(case_id, case_revision, case_digest)` binding；同 exact Case 只能有一个 target，换绑
  必须产生新 Case revision，冲突不能由“latest”静默覆盖。
- `caseloop init` 与 `caseloop case from-issue` 只编排上述 canonical intents；网络断线、
  局部失败和重试不产生第二 owner、重复 Case 或自动 confirmed acceptance；

#### Exit / rollback

- Exit：wire/compat verifier PASS，first slice 获明确批准后才进入 migration。
- Rollback：保持 V5 intents undiscoverable；S1A 继续服务。
- Commit：`test(contracts): freeze v5 first system slice`

## 3. V5-1 — First System Case

### V5-1A · Application catalog

> 状态：**R2 CONTRACT/REPLAY PASS ONLY / ARCHITECTURE CONVERGENCE ACTIVE**。该边界不证明
> 完整 runtime；最终 subject SHA/evidence digest 依 owner 指示延后最终项目收口。C0–C5
> 只允许行为不变的结构收敛，不得借 V5-1A 名义继续领域扩张。

#### Entry

- V5-0C PASS；exact first-slice wire contract 已批准。

#### Deliverables

- migration：AIApplication、Environment、SystemComponent、DependencyEdge；
- application/version controller 与 AuthorityReceipt；
- 完整平台保留 Application/Environment/Component/Edge HTTP/CLI register/get；本 slice
  尚不能导入需要 Revision/VersionSet/Assignment 的完整 manifest；
- 单 Workspace runtime，但所有 key 带 workspace/project/environment；
- Console Applications read model，包含 loading/empty/error/partial/UNKNOWN。

#### Verification

- owner/criticality/environment/digest validation；
- cross-workspace graph、cycle policy、duplicate component identity；
- audit fail rolls back；outbox same transaction；
- PostgreSQL concurrency/idempotency；
- 旧 S1A suites 全绿。

#### Exit / rollback

- Exit：一个本地 AIApplication 可注册、读取和审计；无 runtime/version claim。
- Rollback：禁用 V5 route，保留 append-only rows；不删除历史。
- Evidence：`evidence/v5/stage-1/application-catalog/<run-id>/`
- Commit：`feat(v5): add ai application catalog`

### V5-1B · System manifest and VersionSet

> 状态：**LOCKED**。C5 PASS 后仍须先通过 D2 完整 version-graph contract gate；现有
> workspace-initial bootstrap 不证明第二 VersionSet 或真实 diff。

#### Deliverables

- ComponentRevision、SystemVersionSet、SystemAssignment desired pointer；
- trusted one-shot manifest import，在 1A+1B owners 都可用后作为联合出口；
- `caseloop init <repo>` 本地只读 discovery：生成 git/project/test command 与可识别
  `APPLICATION_CODE/PROMPT/MODEL_BINDING/RETRIEVER/INDEX` 的 manifest 草稿；必须人工
  确认后才调用 canonical import，扫描结果不构成 observed runtime；
- identity assurance 与 semantic diff；
- manifest CLI：validate/import/get/diff；standalone `system-versions.record` 必须先经过
  Master Plan D2 contract activation，且只引用既有 catalog/revision/topology，不暗建
  前置对象；
- first manifest 只包含当前 workload 能可靠确认的组件：至少 application code，以及实际
  存在的 Prompt/model/retriever/index/Agent/tool binding；不为填满枚举制造空资产；
- 导入一个独立可信的人类 approver policy revision，供后续 Approval exact binding 使用；
  它不进入 runtime SystemVersionSet，也不能成为同一 Candidate 的变更面；
- immutable JCS digest 与 provenance refs；
- bootstrap assignment 固定 generation=1、previous=null、exact BootstrapAttestation；后续
  只接受 CAS previous+1 与 exact WorkOrder/ExternalOperation authority；

#### Verification

- mutable alias/unknown 不冒充 immutable；
- same label/different digest、dependency substitution、policy permission expansion；
- assignment CAS/idempotency；
- bootstrap authority 只证明 desired，不证明 observed；
- VersionSet immutable；
- graph digest 与 component revisions 精确绑定；
- discovery root escape、symlink、secret redaction、mutable alias 与不稳定重复扫描；
- 无法可靠识别的组件保留 `UNKNOWN`，不要求 First Case 填满完整 V5 component enum。

#### Exit / rollback

- Target Exit：一个 application/environment 至少有两个可独立记录的 exact declared
  VersionSet，semantic diff 覆盖真实变更，且 desired assignment 不宣称 observed runtime。
  若 D2 选择 defer，只能形成 `BOOTSTRAP_ONLY` 限定出口，V5-4 前仍必须补齐 target exit。
- Evidence：`evidence/v5/stage-1/system-version/<run-id>/`
- Commit：`feat(v5): add immutable system versions`

### V5-1C · First System Case

> 状态：**LOCKED**。只在 C5、D2 与 R3-full 依次 PASS 后恢复；已有 repair/fixture/Console
> 不构成本 stage completion。

#### Deliverables

- additive ApplicationCaseBinding；
- Manual/GitHub Issue read-only SourceConnection；
- `caseloop case from-issue <url>` CLI workflow，组合 canonical intake/binding/acceptance
  intents，不新增 owner，不把 Issue 文本当作 instruction 或 acceptance truth；
- Signal→QualityCase→Application/Environment/SystemVersionSet；
- additive CaseReadiness + immutable BadcaseSpec/AcceptanceCriteria artifact，由现有
  ResolutionContract owner 在后续 Gate 前 exact seal；记录来源、复现输入/环境、期望行为、
  oracle/evaluator、workload/deployment profile、确认人和 revision；
- 无 runtime locator 时保留 `UNKNOWN`；
- Console Case 页面显示 system binding、acceptance readiness 和 missing evidence。

#### Verification

- issue prompt injection、duplicate webhook、edited/deleted source、malicious attachment；
- Signal/Case immutable digest 不被 application link 改写；
- application/version 不可确定时 Case 仍 OPEN；
- 明确 Issue 可形成 maintainer-confirmed acceptance input；模糊 Issue 显示
  `NEEDS_ACCEPTANCE_CRITERIA` 并返回下一步。两者在 V5-4 ResolutionContract exact
  materialization 前都不能启动 Gate；
- Agent proposal 不能自我确认验收标准，confirmed revision 不能原位改写；
- no remote comment/claim/fork/push/PR。

#### Exit / rollback

- Exit：一个真实 Issue 或 maintainer report 形成 First System Case，并产生 confirmed
  acceptance input 或诚实进入 `NEEDS_ACCEPTANCE_CRITERIA`；confirmed 状态保持
  `PENDING_MATERIALIZATION`，直到 V5-4 exact ResolutionContract 后才可能 executable。
  记录从 source 到该结果的人类时间和阻塞原因。
- Evidence：`evidence/v5/stage-1/first-system-case/<run-id>/`
- Commit：`feat(v5): bind quality cases to ai applications`

## 4. V5-2 — Durable capability plane

### V5-2A · Work Kernel

> 状态：**LOCKED / NOT IMPLEMENTED**。C0–C5、D2、R3-full 与 R4 未关闭前不得施工、
> advertise 或借 structural convergence 预埋 runtime。

#### Deliverables

- V4 WorkerTask/Attempt/lease/fence/retry/cancel/reconcile runtime；
- typed Proposal/Decision 和 reaction ledger；
- PG outbox dispatcher；
- 确定性 fixture executor，暂不依赖模型或 AgentTeams。

#### Verification

- claim/heartbeat/lease expiry/fence；
- duplicate submit、cross-task replay、late receipt、post-action Proposal；
- crash before/after claim/output/decision；
- `UNKNOWN→reconcile` before retry；
- Coordinator/Adapter/Exporter 无法写领域成功。

#### Exit / rollback

- Exit：真实 PG/outbox 下无双 lease、ghost success 或 ambiguous retry。
- Evidence：`evidence/v5/stage-2/work-kernel/<run-id>/`
- Commit：`feat(v5): add durable governance work kernel`

### V5-2B · Async public intents

#### Deliverables

```text
investigations.start
operations.get
operations.list
operations.cancel-request
```

- durable operation id；
- CLI wait/follow/json；
- detach 不取消；cancel 只产生 stop request；
- AutomationRunView 只读投影。
- OperationState 只从 V4 WorkerTask/Attempt 投影；`COMPLETED` 表示可信领域 artifact 已
  产生，Gate `FAILED/INCONCLUSIVE/ERROR/UNKNOWN` 仍可作为 completed artifact；只有无法
  产生可信 artifact 才是 transport `FAILED`。

#### Verification

- transport timeout、Ctrl-C、retry/idempotency；
- stale cursor、revoked principal、cross-workspace operation；
- Task/operation completed 不膨胀为 Gate/Release success。

#### Exit / rollback

- Exit：一个 shell-capable Agent 可通过 CLI 发起和观察调查。
- Competition：若黄金纵切没有通用长任务，generic list/cancel 不作为比赛关门线；不得
  因此删掉完整 V5 target。
- Evidence：`evidence/v5/stage-2/public-operations/<run-id>/`
- Commit：`feat(v5): expose durable governance operations`

### V5-2C · First Agent-native transport

#### Deliverables

- 基于 canonical intents 的最小 Public MCP 或 A2A adapter；
- 外部 Agent principal、OAuth/audience/scope；
- 结构化 Artifact 与 capability discovery；
- A2A 使用 `protocolVersion: "1.0"`、官方 `SendMessage/GetTask/ListTasks/CancelTask` 与可选 subscribe/stream，
  不继承旧 `tasks.create` 映射，也不复制 CaseLoop state machine。
- MCP Tasks 只作为 negotiated optional extension；baseline async tool 返回 operation id，
  再用 `operations.get/cancel-request`；tool allowlist、input/output schema、structuredContent
  与 transport/domain error 分开。

#### Verification

- Agent token human approve/execute、caller self-reported role、scope expansion；
- protocol downgrade、disconnect、duplicate task、artifact/status equivocation；
- 同 intent 经 HTTP/CLI/Agent transport 返回同一 resource/error/audit。

#### Exit / rollback

- Exit：此增量 slice 启用时，至少一个真实外部 Agent 通过该 transport 启动并观察一个
  allowlisted operation，得到结构化 artifact；不审批或发布。Signal/evidence 只有在对应
  canonical intent 已进入该 transport allowlist 时才验收。比赛 P0 可由真实 Agent CLI
  调用先行关闭，不因本 slice 未实现而伪称 A2A 已支持。
- Evidence：仍使用现有 canonical facets；`client-live` 只能作为非 facet metadata/category。
- Commit：`feat(v5): add first agent-native gateway`

## 5. V5-3 — System evidence and attribution

### V5-3A-core · Receipt graph and SystemEpisode

#### Deliverables

- immutable evidence receipt graph；
- repo-sandbox observations 与诚实 `UNKNOWN`；
- SystemEpisodeView projection + immutable SystemEpisodeSnapshot；
- ObservedStateSnapshot 与 ExternalEffectReceipt；
- coverage/completeness/identity assurance。

#### Verification

- missing receipt、sampling、masking、retention 与 source absence；
- desired != observed；approval != effect；
- exporter 不能补造 run/effect；
- sandbox observation 不能冒充 provider/live。
- view 带 projection revision/as-of/watermark/assignment generation；Gate/归因不能绑定
  mutable view；
- ExternalEffectReceipt origin 必须区分 CaseLoop operation 与 governed system episode；
  workload 无 external effect 时该 receipt 为 N/A，不阻塞比赛。

#### Exit / rollback

- Exit：一个 First System Case 有可审计的 declared/observed/effect 分栏；缺失诚实可见。
- Evidence：`evidence/v5/stage-3/system-evidence/<run-id>/`
- Commit：`feat(v5): add system episode evidence`

### V5-3A-adapter · Live Evidence Source（比赛非阻塞）

#### Entry

- V5-3A-core PASS；对应 source 的合法凭据与现场环境可验证。

#### Deliverables

- Source capability/doctor/sync/cursor/DLQ；
- Langfuse、OTel 或其他 source 的一个真实 Adapter；
- source-specific masking、retention、identity 和 reconciliation receipt。

#### Verification / exit / rollback

- 验证 broad credential、source timeout、cursor replay、采样缺口与 ambiguous outcome；
- 只有真实接入并回读才报告 `domain-provider-live`；真实 Agent 因果链才报告
  `agent-causal`；
- Exit：该 Adapter 独立通过，不改变 V5-3A-core 或比赛闭环状态；
- Rollback：关闭 source、撤销 credential，保留已固化 receipt/snapshot，回落 manual/repo evidence；
- Evidence：`evidence/v5/stage-3/source-adapter/<run-id>/`；
- Commit：`feat(v5): add <source> evidence adapter`。

### V5-3B · Attribution

#### Deliverables

- frozen AttributionPlan；
- known-good control 与 single-component intervention；
- paired randomization、effect/interval、hidden confirmation；
- typed AttributionReport。

#### Verification

- 多组件同时变化、低 power、unavailable rollback、contaminated control；
- Agent hypothesis 不等于 verdict；
- 允许 INCONCLUSIVE/CONFOUNDED/INSUFFICIENT_EVIDENCE。
- 归因 `INCONCLUSIVE` 可允许人工或外部 Agent 提交独立 Candidate 进入验证，但不得
  声称 root cause；只有 workload 将 attribution 声明为 required Gate facet 时才阻止 PASS。

#### Exit / rollback

- Exit：比赛 workload 至少对一个主要变更面完成可重复归因或诚实 abstain。
- Evidence：`evidence/v5/stage-3/attribution/<run-id>/`
- Commit：`feat(v5): add evidence-bound system attribution`

## 6. V5-4 — System candidate and Gate

### Deliverables

- ResolutionContract/CandidateContract/EvaluationPlan runtime；
- ResolutionContract exact seal confirmed BadcaseSpec/AcceptanceCriteria；
- 外部 Agent Candidate submission；
- schema-major-2 SystemCandidateRevision component diff bundle；
- EvaluationPurpose 分为 `CANDIDATE_VERIFICATION` 与 `RELEASE_AUTHORIZATION`；前者
  PASS 只能产生 VerifiedCandidate/RegressionAsset candidate，后者才要求 Proposal
  Controller 在 Gate 前 seal ReleasePlan（target/rollout/observed verification/recovery/
  known-good），不含 Approval/nonce/expiry；
- VerifiedCandidate 只是 exact Candidate + verification GateReport 的只读 projection；后续
  请求 release 必须 seal ReleasePlan 并新跑 release-authorizing Gate，不能复用旧 PASS；
- repo sandbox、Finding、EvaluationBundle、Gate tracks；
- 真实 AI 开源 Issue 的 base-fail reproduction/patch/target-pass；
- 数据模型允许多个 component diff，但 competition profile 强制 exactly one changed
  component/主要变更面；
- P0 required evaluation dimensions 固定为 badcase、regression、deterministic/repo-sandbox integrity
  和 evidence completeness；latency、cost、permission、external-effect safety 与
  compatibility 仅在 workload applicable 时 required；
- `CODE_CHANGE` 运行 repo tests、历史 Case regression、sealed holdout 与 anti-overfit；
  确定性 oracle 足够时 Judge 为 `N/A`；
- `AI_BEHAVIOR_CHANGE` 运行 paired base/candidate repetitions、失败分布、effect/interval
  与 unaffected controls；repetitions/threshold 来自 workload baseline；Judge 未经维护者
  标注校准只能 advisory；
- 只有 `RELEASE_AUTHORIZATION` Gate PASS 创建 WorkOrder；无远端写。
- GateReport 精确绑定 ResolutionContract、base/target/Candidate/EvaluationBundle；release
  purpose 另绑定 ReleasePlan；PASS 后创建
  schema-major-2 SystemWorkOrder，绑定 expected assignment generation、nonce 和 expiry；
  无远端写。

### Verification

- 修改/删除测试、Stub、超范围 diff、network/fs escape、holdout leak；
- base 意外 PASS、candidate 仍 FAIL、弱断言、硬编码公开样例、Issue/solution leak、边界变体；
- Evaluator 与 Generator independence；
- Judge `N/A/advisory/required` applicability、校准缺失和 rubric disagreement；
- 非确定性小样本/高波动诚实 `INCONCLUSIVE`，不继承 fixture 演示阈值；
- 硬失败不被总分覆盖；
- Candidate/SystemVersion/Evaluation 换绑；
- ReleasePlan 在 Gate/Approval 后被替换或 Executor 补参数；
- A2A Task success 不冒充 Gate PASS。
- Gate self-test：回放已知“测试通过但未修好”的 Candidate 测 false-pass，回放真实已合并
  修复测 false-block，并与维护者原始裁决对照；报告样本类型和分母。

### Exit / rollback

- Exit：外部 Coding Agent 经 CLI/Agent-native transport 提交 exact Candidate；独立 Gate
  对本地 Issue 给出与 confirmed acceptance 对齐的可解释终态；verification-only PASS
  产生 VerifiedCandidate/`NOT DEPLOYED`，只有 release-authorizing PASS 产生 WorkOrder；
  Gate self-test 同时留下 false-pass/false-block 基线。
- Rollback：拒绝 Candidate，销毁 sandbox，保留 immutable Finding/evidence。
- Evidence：`evidence/v5/stage-4/system-candidate/<run-id>/`
- Commit：`feat(v5): add system candidate evaluation gate`

## 7. V5-5 — Guarded release and recovery

### Entry

- target 的 deployment profile 为 `DEPLOYED_SERVICE`，且有可独立回读的本地/真实 runtime；
- exact SystemWorkOrder 来自 `RELEASE_AUTHORIZATION` Gate PASS，且已绑定 pre-Gate ReleasePlan；
- 真正完成 V4 Stage-4 的 schema-major-2 human ApprovalGrant、CapabilityLease、
  ExternalOperation contracts 与 runtime；现有 V4 skeleton/reference 不算前置已完成；
- v3 crash recovery/cutover debt 对目标 routing key 已关闭；
- 若本 slice 触达 live/provider/external adapter，对应 credentials 已按记录轮换；纯
  local/shadow 比赛路径不以外部凭据为前置；
- 本次 external action 有单独授权。

### Deliverables

- P0 human ApprovalGrant；PolicyGrant 后置 P1；
- 已 seal ReleasePlan 的只读消费与 operation-specific local process manager；
- scoped ExternalOperation + Secret Broker；
- local/shadow SystemAssignment；
- 最小 local runtime/deploy target adapter，从目标进程、容器或 version endpoint 独立
  回读实际 loaded revision/digest；只读回 desired assignment 不构成 observed evidence；
- observed version verification，无法读取或不匹配时为 `UNKNOWN`；
- post-release 重跑原 badcase、适用 regression，并记录 recurrence observation；
- stop exposure、restore、reconcile、revoke、compensate；
- kill switch 与 SystemReleaseView。
- assignment/observation/effect receipt 通过 outbox 反向绑定 root operation；只有 exact
  generation、required receipts、independent COMPLETE/MATCH 且无 UNKNOWN 才可 succeed；
- 每次 rollback 都 fresh 冻结绑定可信 known-good 证据的 break-glass ReleasePlan，创建
  RecoveryWorkOrder + reauthenticated human emergency approval，再创建新的
  SYSTEM_ROLLBACK operation；原 WorkOrder/Approval/nonce/capability 不能复用，禁止
  direct restore/resume；

### Verification

- hash/nonce/expiry/target mismatch；
- Agent self-approve、broad credential、base drift；
- Executor 在审批后替换 rollout/recovery/target；adapter receipt 直接 succeed；
- crash before/after desired switch、provider response loss、partial operation；
- rollback observed mismatch、in-flight duplicate、compensation UNKNOWN；
- post-release 原 badcase 复发、regression 失败、recurrence source 缺失或不确定；
- audit failure rolls back local authority transaction。

### Exit / rollback

- Exit：deployed-service 比赛路径完成 local/shadow assignment、observed digest、post-release
  badcase/regression 与 recurrence observation；真实外部写另行验收。`LIBRARY_OR_OFFLINE`
  verification-only path 不进入本 Stage。
- Rollback：停止未来 dispatch，经 authorized SYSTEM_ROLLBACK 恢复 known-good desired，
  验证 observed 后才 resume exposure，并 reconcile/compensate；
  不删除 audit 或 force rewrite remote history。
- Evidence：`evidence/v5/stage-5/guarded-release/<run-id>/`
- Commit：`feat(v5): add approval-bound system release recovery`

## 8. V5-6 — Operations and ecosystem

V5-6 不是一个巨型提交，按真实用户需求拆成独立模块：

| Slice | 资源 | 依赖 | 首个出口 |
|---|---|---|---|
| Reliability | Incident/Problem/KnownError/SLO/ErrorBudget | V5-3/5 | Case→Problem→PIR，SLO 可冻结 rollout |
| Supply chain | AssetRiskFinding、BOM/provenance、permission diff | V5-1/4 | 一个 Skill/MCP/model dependency 影响分析与 revoke |
| Data/memory | lineage、retention、memory ledger/quarantine | V5-1/3 | 污染来源可定位、隔离、rebuild/revert |
| Cost/vendor | CostEvent、RateCard、Budget、DeprecationNotice | V5-2/3 | 每 Case 成本与一个 EOL migration Case |
| Public ecosystem | MCP+A2A+SDK+webhook+connector SDK | V5-2C | 两种异构 Agent client parity |
| Production | backup/restore/key rotation/N/N-1/multi-tenant/DR | all | single-node recovery 先于 K8s/HA |

每个 slice 单独 migration、tests、evidence、rollback 和 semantic commit；不能用阶段名把
未实现模块一起标记 DONE。

每个 V5-6 slice 的共同 Gate：

- Entry：所依赖的 V5-1/2/3/4/5 aggregate 与 recovery invariant 已有真实 evidence，
  并冻结该 slice 的 owner、migration、required facets 和外部授权边界；
- Verification：focused positive/negative、replay、幂等、权限、UNKNOWN/reconcile 与
  conditional-live 测试分别通过，未执行的 live facet 保持 `NOT_RUN`；
- Evidence：独立写入 `evidence/v5/stage-6/<slice>/<run-id>/`，不得借用另一 slice 的
  PASS；
- Rollback：关闭该 slice 的 route/adapter/dispatcher，撤销对应 capability/credential，
  保留 append-only authority、receipt 和 audit；
- Exit：只关闭该 slice，不把整个 V5-6 或其他模块一起标为 DONE。

## 9. 比赛提交 Gate

必须：

- 一个可本地运行/切换版本的真实开源 AI 应用服务 Issue/source snapshot；
- 从空 V5 领域表（已有 V4 Controller trust roots 与既有 V4 Case）经 CLI 只读 discovery、
  人工确认和一次性 manifest import 得到一个 AIApplication/Environment/SystemVersionSet；
- 一个真实外部 Agent 经显式 V2 CLI 调用；
- `caseloop init`/`case from-issue` 形成经人工确认的 manifest 与 AcceptanceCriteria；若原
  Issue 模糊，必须诚实展示 `NEEDS_ACCEPTANCE_CRITERIA`，不能继续伪造成功路径；
- V5-4 从 exact Case binding、confirmed AcceptanceCriteria、source snapshot 和 base
  VersionSet 物化 exact ResolutionContract，再从它产生 executable BadcaseSpec；
- 一个本地 Candidate 与 repo-sandbox evidence；
- 单 repo component/单主要变更面；
- base FAIL/target PASS、repo/历史 regression、sealed holdout/anti-overfit、repo-sandbox
  integrity、evidence completeness Gate；AI 行为 workload 另按 baseline 做 paired repeats；
- pre-Gate ReleasePlan、`RELEASE_AUTHORIZATION` PASS 后 SystemWorkOrder、重新认证的人类 Approval；
- 一个 Case detail Console 读真实权威状态；
- local target adapter、shadow assignment、独立 observed digest、post-release Gate 与
  rollback drill；
- 明确 `NO REMOTE WRITE PERFORMED`，除非逐动作获批；
- evidence manifest 可独立验证且不含 secret/PII。
- Gate 自验证至少包含已知无效 Candidate 与真实已合并修复两类回放，并报告
  false-pass/false-block、维护者一致性和样本分母。

不得：

- 用静态 UI、预制成功 JSON 或 exporter 制造闭环；
- 用 A2A `completed` 冒充问题修好或发布成功；
- 用 GitHub Issue 文本授予权限；
- 把 local assignment 称为 production canary；
- 把单变更面演示称为完整跨组件自动归因；
- 把 target-only/deferred V5 contracts 称为已实现平台治理。
- 把 library/offline 的本地 assignment drill 称为真实 deployed-service release proof。

建议同时演示一个普通 `CODE_CHANGE`/library Issue 对照路径：confirmed AcceptanceCriteria
→ exact ResolutionContract → executable BadcaseSpec → Candidate verification →
`VerifiedCandidate / NOT DEPLOYED`。它不进入 V5-5，也不替代
主路径的 observed/rollback 证明；其作用是诚实展示 CaseLoop 相对 GitHub/CI 的增量与成本。

不作为比赛关门线：generic catalog CRUD/graph、SystemEpisode public endpoint、完整
Attribution engine、generic operation list/cancel、无外部 effect 时的
ExternalEffectReceipt、三套 Console workspace、Public A2A/MCP runtime。
这些是比赛交付顺序而非产品删减；完整 V5 的多组件治理、持续来源、完整 Console、
Agent 协议生态、供应链、memory/data、Incident/SLO、成本与 vendor 模块继续按后续 slice
交付。

## 10. 计划变更协议

任何 Stage 的 split、reorder、skip 或 abandon 必须：

1. 写清触发证据；
2. 检查依赖和 authority 是否变化；
3. 更新本文件、PLANS 和 context；
4. 若改变 product/owner/security/cutover，新增或 supersede ADR；
5. 保留原 evidence 与 completion claim，不回写历史；
6. 由独立 verifier 复核新的 entry/exit。
