# CaseLoop 文档归档

本目录保存已经被替代、但仍有溯源价值的状态、handoff 和施工快照。归档是可恢复移动，
不是删除，也不改变历史事实。

## 归档不具备当前权威

归档内容只能回答“当时记录了什么”。它不能覆盖：

- [`AGENTS.md`](../../AGENTS.md)；
- [`docs/product-principles.md`](../product-principles.md)；
- 当前 V5 plan、frozen contracts 和
  [`v5-master-execution-plan.md`](../plans/v5-master-execution-plan.md)；
- [`PLANS.md`](../../PLANS.md)、[`PROJECT_STATE.md`](../context/PROJECT_STATE.md) 与
  [`LAST_HANDOFF.md`](../context/LAST_HANDOFF.md)；
- 绑定精确 commit/run/facet 的 `evidence/`。

## 目录

| 目录 | 内容 |
|---|---|
| `status/` | 被当前状态台账替代的根状态页 |
| `context/` | 旧 handoff、construction context 和项目快照 |
| `plans/` | 已完成或被新路线替代的施工排期 |
| `decisions/` | 不再属于 active ADR namespace 的历史过程决定 |

## 2026-08-11 首批归档清单

| 原路径 | 归档文件 | 当前替代入口 |
|---|---|---|
| `STATUS.md` | [`status/STATUS-2026-08-09-pr1.md`](status/STATUS-2026-08-09-pr1.md) | 根 `STATUS.md` 当前导航 |
| `docs/context/LAST_HANDOFF.md` 累计历史 | [`context/LAST_HANDOFF-history-through-2026-08-11.md`](context/LAST_HANDOFF-history-through-2026-08-11.md) | 单一 current handoff |
| `docs/context/V5_CONSTRUCTION_CONTEXT.md` | [`context/V5_CONSTRUCTION_CONTEXT-2026-08-10.md`](context/V5_CONSTRUCTION_CONTEXT-2026-08-10.md) | Master Execution Plan |
| `docs/plans/phase1-execution.md` | [`plans/phase1-execution-2026-08-07-b1.md`](plans/phase1-execution-2026-08-07-b1.md) | Master Execution Plan |
| `docs/decisions/D-002-executor-routing.md` | [`decisions/D-002-executor-routing-2026-08-07.md`](decisions/D-002-executor-routing-2026-08-07.md) | active D-002 Gate/WorkOrder ADR |

## 每个归档文件应记录

- 原路径；
- 创建/适用日期；
- 当时的 branch/commit（能确定时）；
- `HISTORICAL` 或 `SUPERSEDED` 状态；
- 当前替代入口；
- 明确“不能从该快照推导”的能力。

## 不归档的资产

- ADR 和产品原则；
- v3/v4 兼容基线与 V5 已接受 plan；
- frozen contracts、migrations 和 executable tests；
- evidence bundle；
- 仍被 runtime、contract 或 active Agent definition 引用的设计文档；
- 研究和 competition 文档。它们保留在原 namespace，并以自身 status/date 限定语义。
