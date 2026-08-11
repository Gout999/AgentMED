# CaseLoop 文档索引与权威层级

本文是文档导航，不是新的产品、合同或运行权威。发生冲突时按下表从上到下处理；
不得用 Wiki、研究、presentation、handoff 或历史 evidence 覆盖正式权威层。

## 开工前最短阅读路径

1. [`AGENTS.md`](../AGENTS.md)：仓库工程、安全、证据和 Definition of Done；
2. [`product-principles.md`](product-principles.md)：产品定位与范围；
3. [`D-015`](decisions/D-015-v5-default-development-baseline-and-v3-v4-compatibility-lanes.md)：
   默认开发基线与 V3/V4 兼容 lane；
4. [`plan-v5.md`](plan-v5.md)：V5 目标架构与迁移边界；
5. [`plans/v5-master-execution-plan.md`](plans/v5-master-execution-plan.md)：当前执行编排、
   stop gate、work package 与提交边界；
6. [`V5 Architecture Convergence`](plans/v5-architecture-convergence.md)：跨层协议收敛施工；
7. 任务涉及的 [`contracts/v5/`](../contracts/v5/) 和对应 conformance test；
8. [`PLANS.md`](../PLANS.md)、[`PROJECT_STATE.md`](context/PROJECT_STATE.md) 和
   [`LAST_HANDOFF.md`](context/LAST_HANDOFF.md)：当前状态、阻塞与下一步。

## 权威层级

| 层级 | 文档 | 能决定什么 | 不能证明什么 |
|---|---|---|---|
| 工程规则 | `AGENTS.md` | 安全、施工、证据、完成定义 | 某能力已经实现 |
| 产品 | `product-principles.md`、D-013、D-015 | 产品身份、范围、默认开发基线与兼容 lane | runtime/live/cutover |
| 需求/架构 | `prd-v5.md`、`plan-v5.md` | 目标语义、owner、迁移边界 | 当前状态 |
| 执行编排 | `v5-master-execution-plan.md`、`v5-architecture-convergence.md` | work package、依赖、测试、stop gate | 自动授权 commit/live/external write |
| 冻结合同 | `contracts/v5/` | wire、状态机、事件、exact binding | route/provider 已上线 |
| 当前事实 | `PLANS.md`、`PROJECT_STATE.md`、`LAST_HANDOFF.md` | 当前 HEAD/WIP/证据/阻塞 | 覆盖产品或合同 |
| 证据 | `evidence/` | 绑定特定 commit/run/facet 的结果 | 推导未运行 facet |
| 导航/背景 | `wiki/`、`research/`、`competition/`、`presentation/` | 蒸馏、历史、研究、对外叙事 | 覆盖前述层级 |

## 版本关系

- 新开发默认基线与兼容 lane 由 [`D-015`](decisions/D-015-v5-default-development-baseline-and-v3-v4-compatibility-lanes.md)
  裁决，机检边界在 [`contracts/v5/compatibility.yaml`](../contracts/v5/compatibility.yaml)；
- v3 contracts、migrations 和测试保留为尚未迁移部分的实现兼容基线；
- V4 Stage 0/Stage 1 Entry 是 contract-only，V4 S1A 是已验证的本地 runtime；
- V5-0B/0C 是已接受的 contract freeze；R1/R2 semantic subjects 已通过 clean verifier，
  但 checksum-bearing final evidence 依 owner 指示延后，因此保持 `VERIFYING`；
- C0-C5 architecture convergence 已完成并由 `b6fa629` remediation 重验；当前唯一执行入口
  是 Master §17 的 D2，R3/R4/V5-2+ 继续按 predecessor 锁定；
- V5-2+ 在对应 stage 产生 runtime、证据和 completion commit 前保持未实现。

## 文档生命周期

| 标记 | 含义 |
|---|---|
| `AUTHORITATIVE` | 当前权威层的一部分，不能直接归档 |
| `CURRENT` | 当前状态或执行入口，必须随阶段收口更新 |
| `HISTORICAL` | 真实历史快照，只证明当时的内容 |
| `SUPERSEDED` | 已由新文档替代，不得作为当前入口 |
| `DRAFT` | 未接受的设计或计划，不授权实现 |

过时的 handoff、状态页和施工快照移入 [`archive/`](archive/README.md)。Evidence、ADR、
冻结合同、已批准兼容 plan 和仍被 runtime/contract 引用的设计文件不因“旧”而归档。

## 维护规则

- `LAST_HANDOFF.md` 只保留一个 current handoff；旧段落移入 archive；
- `PROJECT_STATE.md` 记录当前事实和必要的 verified baseline，不复制整段历史 handoff；
- Master Plan 管执行，progressive blueprint 保留已接受的目标与历史 stage 设计；
- 每次 stage closure 同步 `PLANS.md`、`PROJECT_STATE.md`、`LAST_HANDOFF.md` 和 evidence；
- 每次移动文档先用 `rg` 检查入站引用，并同步所有 active link；
- archive 文件保留原日期、适用 commit、原路径、superseded-by 和能力边界。
