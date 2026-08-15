# AgentMED 终态对齐交付计划（收敛链路 + 完整版图）

> 状态：**DRAFT（2026-08-14）**——从属施工视图，不拥有 stage 裁决权。
> 靶文档：[AgentMED-完整产品叙事文档](../presentation/AgentMED-完整产品叙事文档.md) 与
> [AgentMED-项目信息说明书](../presentation/AgentMED-项目信息说明书.md) 描述的终态产品形态。
> 权威层：任何冲突以 [v5-master-execution-plan.md](./v5-master-execution-plan.md)、
> 冻结契约与 [AGENTS.md](../../AGENTS.md) 为准；本文是「终态 → 施工」的对齐视图。

---

## 0. 设计原则：以终态为靶

叙事终态 = 三产品表面（Human Console / Agent Capability Gateway / Deterministic
Governance Kernel）+ 十项核心能力 + 三条端到端路径 + 六层能力版图 + 三类交付形态。
本计划把每一项终态要素锚定到具体施工载体（V5 stage / work package），并标注当前状态
与缺口；交付顺序先收敛最短闭环，再逐段补全版图。

凭证口径（owner 2026-08-14）：测试 key 先行，链路跑通后统一轮换；废弃链路归档不删除。

---

## 1. 终态对齐矩阵（叙事要素 → 施工载体 → 状态）

### 1.1 三产品表面（说明书 §5）

| 终态要素 | 施工载体 | 当前状态 | 缺口 |
|---|---|---|---|
| Human Console | R2 Applications 读模型、R4 Case Workspace、V5-3 Evidence 视图、V5-4 Change Center、V5-5 Approval/Recovery 面 | Console 存在（v3/v4 + V5 修复；Applications/CaseDetail 已改） | Evidence & Audit、Change Center、审批/恢复面未实现 |
| Agent Capability Gateway | V5-2B 异步 intents（HTTP/CLI）+ V5-2C MCP/A2A | `/api/v2` 仅 1A/1B/1C allowlist | 2B/2C 未施工 |
| Deterministic Governance Kernel | R1 authority/event、V5-2A Work Kernel、V5-4 Gate、V5-5 Approval/Executor | PG 控制面部分存在（V4 S1A 有验证证据） | authority foundation 未关闭、Work/Approval/Executor 未施工 |

### 1.2 十项核心能力（说明书 §7）

| # | 能力 | 施工载体 | 当前状态 | 收敛链路是否覆盖 |
|---|---|---|---|---|
| 1 | 发现并登记完整 AI 应用 | R2 catalog + R3 repo discovery | IN_PROGRESS（修复 worktree） | ✅ |
| 2 | 固定不可变系统版本 | R3-full SystemVersionSet | IN_PROGRESS（第二版本缺失） | ✅ |
| 3 | 模糊反馈 → 可信 Case | R4 First System Case | IN_PROGRESS | ✅ |
| 4 | 构建可绑定证据 | V5-3A-core Episode Snapshot | TODO | ✅ |
| 5 | 归因 + 诚实 abstain | V5-3B Attribution | TODO | ⏸ 可选，可 abstain |
| 6 | 验证真实修复 | V5-4 4A/4B + Gate | TODO | ✅ |
| 7 | 分离候选验证与发布授权 | V5-4 4C two-purpose Gate + V5-5 | TODO | 部分（4C 在收敛内，发布链在后） |
| 8 | 控制高风险授权 | V5-5 5A ApprovalGrant/CapabilityLease | TODO | ❌ 后置 |
| 9 | 独立观察/对账/恢复 | V5-5 5B/5C/5D | TODO | ❌ 后置 |
| 10 | 事故沉淀回归资产 | V5-6 Reliability slice（Case→Problem→PIR） | TODO | ❌ 后置 |

### 1.3 三条端到端路径（说明书 §8）

| 路径 | 施工出口 | 当前状态 |
|---|---|---|
| First Useful Case | R4（NEEDS_ACCEPTANCE_CRITERIA 诚实出口） | IN_PROGRESS |
| Verification-only | V5-4 → VerifiedCandidate / NOT DEPLOYED | TODO（收敛链路的终点） |
| Deployed-service | V5-5（WorkOrder→Approval→Observed→Recover） | TODO（收敛后补齐） |

### 1.4 六层能力版图（说明书 §15.1）

| 能力层 | 终态对象 | 施工载体 | 状态 |
|---|---|---|---|
| AI Application Foundation | Application/Environment/Component/Topology/VersionSet/Assignment | R1+R2+R3 | IN_PROGRESS |
| Quality Case & Evidence | Signal/Case/Acceptance/Episode/Evidence | R4 + V5-3 | R4 IN_PROGRESS / V5-3 TODO |
| Candidate & Evaluation | ResolutionContract/Candidate/EvaluationBundle/GateReport | V5-4 | TODO |
| Change Authority | ReleasePlan/WorkOrder/Approval/CapabilityLease | V5-5 | TODO |
| Operations & Recovery | ExternalOperation/ObservedSnapshot/EffectReceipt/Recovery | V5-5 | TODO |
| Continuous Operations | RegressionAsset/Incident/Problem/KnownError/SLO | V5-6 Reliability 等 slice | TODO |
| Enterprise & Ecosystem | Tenant/Adapter/PolicyGrant/Evidence Package | V5-6 slices | TODO |

### 1.5 叙事闭环八段（叙事 §五–§九）

| 链路段 | 施工载体 | 状态 |
|---|---|---|
| ① 信号/立案 | V4-S1A（已验证）；Langfuse 负分→signals.submit 接线 | ✅ 已可用 |
| ② 观测来源（Langfuse） | V5-3A-adapter；应用侧 OTLP（Langfuse 已部署，skill 已下发 Agent Station 团队） | 应用侧已就绪 / 适配器 TODO |
| ③ 版本固定 | R3-full | IN_PROGRESS |
| ④ 证据封存 | V5-3A-core | TODO |
| ⑤ 候选+验证门禁 | V5-4 | TODO |
| ⑥ 发布授权链 | V5-5 | TODO |
| ⑦ 观察/对账/回滚 | V5-5 | TODO |
| ⑧ 回归资产沉淀 | V5-6 | TODO |

---

## 2. 交付顺序（先收敛，后补全）

### 2.1 第一阶段：收敛链路（本排期执行）

```text
D1(已裁决 D-014 方案A) → R1 → R2 → D2 → R3-full → R4
  → V5-2A → V5-2B → V5-3A-core → [3A-adapter: Langfuse]
  → V5-4(4A→4B→4C→4D) → VerifiedCandidate / NOT DEPLOYED
```

逐 stage 排期表（Entry/验收/Evidence/Commit/Stop gate/解锁）见
[v5-convergence-chain-schedule.md](./v5-convergence-chain-schedule.md) 的 12 步定义；
D1 已由 D-014 关闭（方案 A），故排期起点推进到 R1。

### 2.2 第二阶段：补全终态（收敛出口之后）

```text
VOnly → V5-5(5A→5B→5C→5D 发布授权链+观察恢复)  → Core V5 complete
  → V5-2C(Agent-native transport)  [可与 V5-3 并行]
  → V5-3B(Attribution，可选)  [workload 声明 required 时才阻塞]
  → V5-6 模块注册表（Reliability → Supply chain → Data/memory →
    Cost/vendor → Public ecosystem → Production，每 slice read/advisory 先行）
```

---

## 3. 与叙事终态逐项核对的三条硬口径

1. **诚实出口优先**：Verification-only 是终态合法出口之一（叙事 §8.2），收敛链路以它为
   终点，不伪造部署；Deployed-service 必须等 V5-5 的独立 observed 证据。
2. **Langfuse 定位不变**：适配器只产 source receipt；负分立案走 signals.submit；
   PG 保持唯一权威。
3. **废弃链路处置**：归档/冻结/标记 HISTORICAL，不删除；v3/v4 基线、冻结契约、evidence
   全部保留（owner 2026-08-14 确认）。

---

*本计划是终态对齐的施工视图；正式分派仍按 Master Plan 逐 work package 生成独立 brief。*