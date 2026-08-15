# AgentMED 最短收敛链路施工排期（V5 收敛视角）

> 状态：**DRAFT（2026-08-14）**——从属施工视图，不拥有 stage 裁决权。
> 权威层：本文只对 [v5-master-execution-plan.md](./v5-master-execution-plan.md) 做
> 最短闭环的排序与 Langfuse 接入编排；任何冲突以 Master Plan、冻结契约与
> [AGENTS.md](../../AGENTS.md) 为准。

---

## 0. 收敛目标与口径

### 0.1 收敛到哪条出口

**LIBRARY_OR_OFFLINE 出口**（叙事 §8.2 / Master Plan §1.1）：

```text
Langfuse 负分 score / maintainer report
  → Signal → Case（V4-S1A 语义已可用）
  → SystemVersionSet 固定（R3-full）
  → SystemEpisode 证据快照（V5-3A-core，含 Langfuse trace 引用）
  → Candidate + 验证门禁（V5-4）
  → VerifiedCandidate / NOT DEPLOYED
```

**明确不做（本排期）**：V5-5 发布授权链（ReleasePlan/WorkOrder/Approval/Executor/回滚）、
V5-3B 归因实验（可诚实 abstain）、V5-2C MCP/A2A、V5-6。

### 0.2 凭证口径（产品 owner 2026-08-14 决定）

- 当前全部使用**测试 key**，不执行轮换；链路跑通后再统一轮换。
- 保留两条不变：live/external write 仍逐次授权；Langfuse 的 source（被治理应用证据源）
  与 sink（AgentMED 自身可观测）必须分 project 隔离。

### 0.3 废弃链路处置（产品 owner 确认）

- 收敛不删除任何废弃链路：文档走 `docs/archive/` 归档（`HISTORICAL/SUPERSEDED` + 原路径
  + 替代入口）；v3/v4 兼容基线、frozen contracts、migrations、evidence 全部保留；
  V4 S1B–S7 保持冻结。
- 物理删除某条链路属于独立决策，需 ADR + owner 明确授权，不在本排期范围内。

---

## 1. 排序总览

```text
D1（生命周期裁决，推荐方案 A）
  → R1（authority/event foundation）
    → R2（V5-1A Application Catalog 关闭）
      → D2（standalone system-versions.record 契约激活）
        → R3-full（V5-1B SystemVersionSet 关闭）
          → R4（V5-1C First System Case 关闭）
            → K20/2A（Work Kernel）
              → K22/2B（Async public intents）
                → E30（V5-3A-core 证据图/SystemEpisode）
                  ├─ E31（V5-3A-adapter：Langfuse，条件 live）
                  └─ C40（V5-4：4A ResolutionContract → 4B Candidate → 4C 双用途 Gate → 4D 自测）
                    → VOnly（VerifiedCandidate / NOT DEPLOYED）
```

旁路与并行：
- `R3-bootstrap`（D2 选 defer 时的 one-shot 出口）**不推荐**——V5-4 的 Candidate base/target
  需要两个真实 VersionSet，defer 会把 R3-full 变成 V5-4 的硬 Entry，收敛延迟。
- `R5` 运维加固包可随时按独立语义提交，不混入 stage completion commit。
- Langfuse 应用侧最小集成（§3-A）是独立 evidence-source 任务，可与 R1–R4 并行。

---

## 2. 逐 stage 排期

| # | Stage | 用户结果 | Entry | 关键验收（摘要） | Evidence | Commit | Stop gate | 解锁 |
|---|---|---|---|---|---|---|---|---|
| 1 | D1 生命周期裁决 | ✅ **已关闭（2026-08-11）**：owner 选方案 A（保留 `REGISTERED → activate → ACTIVE`），见 [D-014](../../decisions/D-014-v5-application-component-activation-lifecycle.md) | R0 DONE | append-only revision/history、激活仅限受信 manifest 事务、公开 activate 保持 defer | D-014 ADR | 已 ACCEPTED | 历史 ACTIVE row 无法 backfill（由 R1 preflight 处理） | R1 |
| 2 | R1 authority/event foundation | 全部权威记录可重建 revision/digest | D1 | major-2 event envelope + named self/exact bindings；AuthorityReceipt closed shape；migration 012 fresh/populated；receipt replay 递归重验 | `evidence/v5/stage-1/` 对应 run | `feat(v5): establish authority event foundation` | 旧事件被重标 major-2；activation 可被非 owner 调用 | R2 |
| 3 | R2 V5-1A Catalog 关闭 | 注册/激活/读取 AIApplication、Environment、SystemComponent、DependencyEdge | R1 | duplicate identity、cycle/fan-out、cross-tenant、role/scope、audit rollback、same-key conflict、并发 register/activate、Console 读模型 | `evidence/v5/stage-1/application-catalog/<run-id>/` | `feat(v5): close ai application catalog lifecycle` | 历史 ACTIVE 无法 backfill；read 不能重建 revision/digest | D2 |
| 4 | D2 standalone record 契约激活 | `system-versions.record` wire 冻结并独立 verifier PASS | R2 | 冻结 request/response/scope/idempotency/error/HTTP-CLI-capability 映射；不复用 first-import bootstrap authority；conformance 更新 | 契约 evidence | 契约 commit | 若 defer 只允许 R3-bootstrap 且不得宣称 R3-full | R3 |
| 5 | R3-full V5-1B VersionSet 关闭 | 同应用至少两个不可变 VersionSet + 可信 semantic diff | D2=activate | standalone record 不暗建前置对象；HTTP/CLI/capabilities/idempotency/audit/GET/diff 同步；second-version PG E2E 真实差异 | `evidence/v5/stage-1/system-version/<run-id>/` | `feat(v5): record and compare immutable system versions` | self-diff 冒充第二版本；mutable alias 被标 immutable | R4 |
| 6 | R4 V5-1C First System Case 关闭 | 真实 Issue 形成可审计 Case + exact/UNKNOWN 版本绑定 + confirmed acceptance | R3 | prompt injection、stale source/version、fresh owner reauth、two-confirmer race、denial audit、installed-CLI + isolated PG 全链路；ResolutionContract 保持 PENDING_MATERIALIZATION | `evidence/v5/stage-1/first-system-case/<run-id>/` | `feat(v5): close first system case intake` | Case 被标 executable 而 ResolutionContract 未 materialize | 2A |
| 7 | V5-2A Work Kernel | 持久 task/attempt/lease/fence/reconcile，无 double lease/ghost success | R4 | claim 同事务（task snapshot+Attempt+capability+receipts）；UNKNOWN→reconcile 后才 retry；outbox per-aggregate causal order；fixture executor 只属 contract/replay | `evidence/v5/stage-2/work-kernel/<run-id>/` | `feat(v5): add durable governance work kernel` | dual lease authority；ghost success | 2B |
| 8 | V5-2B Async public intents | `investigations.start` + `operations.get/list/cancel-request` + CLI wait/follow/json | 2A | detach 只停等待、cancel 只提交 stop request；Operation COMPLETED ≠ Gate/Release PASS；shell-capable Agent 断线重连 | `evidence/v5/stage-2/public-operations/<run-id>/` | `feat(v5): expose durable governance operations` | transport→domain 状态膨胀 | 3A-core |
| 9 | V5-3A-core 证据图 | First Case 有 declared/observed/effect 分栏 + missing evidence | 2B | source-neutral receipt envelope → coverage → mutable EpisodeView → immutable EpisodeSnapshot；Gate 只能绑定 immutable snapshot；缺源/retention gap 必须 PARTIAL/UNKNOWN | `evidence/v5/stage-3/system-evidence/<run-id>/` | `feat(v5): add system episode evidence` | mutable view 被 Gate 绑定；sandbox 冒充 live | 3A-adapter、4 |
| 10 | V5-3A-adapter（Langfuse） | Langfuse 负分/observations 形成可审计 source receipt | 3A-core + 隔离只读凭证 | `LangfuseTraceSource`：observations/scores 增量读取、水位、去重、traceId 聚合、capability discovery、deep-link；失败语义 EVIDENCE_INCOMPLETE/PARTIAL/retryable；broad key 告警并隔离 | `evidence/v5/stage-3/langfuse-adapter/<run-id>/`（`domain-provider-live` 分栏） | `feat(v5): add langfuse trace source adapter` | 无合法隔离凭证或现场不可回读（保持 NOT_RUN 不阻塞 4） | 4（证据面） |
| 11 | V5-4 4A Resolution/BadcaseSpec/Readiness | Case 从 NEEDS 进入 READY，BadcaseSpec exact 绑定 | 3A-core（+3B 声明 required 时） | materialization 同事务写 contract+event+receipt+audit；concurrent materialize 最多一个 canonical；BadcaseSpec 不能反向创建 contract；Readiness 只由投影判定 | `evidence/v5/stage-4/...` | `feat(v5): materialize resolution contracts` | stale/mismatch 被投影为 READY | 4B |
| 12 | V5-4 4B/4C/4D Candidate+Gate | 真实 Candidate 经验证门禁得到可解释裁决 | 4A | base fail / candidate pass；CANDIDATE_VERIFICATION 只产 VerifiedCandidate；uncalibrated Judge 只能 advisory；Gate 自测回放「假修复」与「真修复」报告 false-pass/block | `evidence/v5/stage-4/...`（含 `repo-sandbox`；外部 Agent 路径需 `agent-causal`） | `feat(v5): add candidate verification gate` | 总分掩盖 hard failure | **VOnly 出口** |

> 每个 work package 的完整字段（Migration/Rollback/Verification 明细）以 Master Plan §4 与
> 对应 stage brief 为准；上表只是收敛排序与验收摘要，不能替代 stage brief。

---

## 3. Langfuse 接入的两条线

### 3-A 应用侧最小集成（并行任务，不阻塞 V5 stage）

对应历史口径「Langfuse 最小集成（8 天版）」：

1. 小智客服（demo-app）trace 经 OTel/OTLP 上报自托管 Langfuse（docker compose）——
   当前由另一位 agent 配置；
2. 差评 score（user-feedback=0）→ `signals.submit` 幂等立案（V4-S1B 契约已冻结
   `sources.capabilities / sources.doctor / source-sync-runs.get`，接线即可用）；
3. 采集员引用 Langfuse trace 作为第三方取证素材（safe deep-link 进 Case/Episode）。

凭证：source 用只读 key（隔离 project）；AgentMED 自身 sink 写独立 project 并保留
export receipt。测试 key 先行，跑通后统一轮换。

### 3-B 证据侧适配器（stage 10，3A-adapter）

`LangfuseTraceSource` 属于证据面：只产 source receipt，不拥有领域成功；不可达/脱敏/采样
缺口按失败语义记录，绝不允许把空结果解释为没问题。

---

## 4. 全局纪律（每 stage 必查）

1. 逐 stage：focused tests → 独立 verifier → evidence manifest（digest + subject commit + 分栏）
   → semantic commit；未运行 facet 一律 `NOT_RUN`；
2. 不允许 contract/fixture/mock 冒充 runtime 或 live；
3. `PLANS.md` / `PROJECT_STATE.md` / `LAST_HANDOFF.md` 每个 stage 关闭后同步；
4. 下一 stage 的 Entry 必须能从 clean checkout 独立重建；
5. 任何 stop gate 触发即 NO-GO，先 ADR/合同解决再继续，不允许实现者自行绕过。

---

*本排期为收敛视角的执行排序；正式分派仍按 Master Plan 逐 work package 生成独立 brief。*