# R1 Work Package Brief — V5 Authority & Event Foundation

> 生成：2026-08-14 ｜ 依据：v5-master-execution-plan.md §R1、D-014、全局 stage protocol
> 状态：DRAFT brief，正式开工前需按 Master Plan 收窄文件/hunk allowlist 并确认 Entry。

## 0. 一句话

实现 D-014 方案 A 的权威基础：Application/SystemComponent 的 append-only 生命周期修订、
major-2 事件信封与 AuthorityReceipt、exact subject/dependent bindings，以及读/重放完整性。

## 1. User outcome

- 任何权威生命周期记录都能从不可变历史重建 `(kind, id, revision, digest)`；
- `register` 产出 revision 1 `REGISTERED` + `*.registered` 事件；`activate` 产出 revision 2
  `ACTIVE` + `*.activated` 事件（含 exact previous/new bindings）；
- 每条修订有自己的 envelope、digest、AuthorityReceipt、事件、outbox 与 audit，revision 1 不被覆盖；
- `ComponentRevision` 持久化 `exact_system_component_binding`（当前 ACTIVE 修订 + 不可变 digest）。

## 2. Authority owner

- `application-catalog-controller` 是 register 与 activate 的唯一 owner；
- 激活仅限受信 manifest 事务（`manifest_import_coordinator`，同 PG 事务，11 步序列任一失败整体回滚）；
- `internal_controller` 不得独立激活；Agent/LLM 输出不得制造生命周期修订。

## 3. Entry

- R0 DONE；D1 ACCEPTED（D-014，方案 A）；
- 当前 migration head 012（012 拒绝任何已有 V5 authority history 的语义不变）；
- 工作区含未提交的 V5-1A/B/C 修复——R1 只在下方 allowlist 内施工，不混入 1B/1C 业务扩展。

## 4. Scope（owned paths 默认 allowlist）

- `control-plane/app/models/v4_tables.py`、`control-plane/app/models/v5_tables.py`；
- `control-plane/app/services/v4_event_store.py`、`control-plane/app/services/v5_authority.py`；
- 新 Alembic revision（append-only `*_lifecycle_revisions` 历史表，实际命名以 D-014 建议为准）；
- `contracts/v5/events.yaml`、`contracts/v5/state-machines.yaml`、`contracts/v5/schema-profiles.yaml`；
- focused tests（unit/contract/adversarial/PG）。

## 5. Non-goals

- 公开 standalone `applications.activate` / `system-components.activate`（保持 defer、不可发现）；
- SystemVersion / Case / Acceptance 业务扩展；V5-2 Work；live/provider/Agent facet。

## 6. Migration

- 新 head（013）前置 preflight：
  - populated 前 head 且无 V5 目录/权威历史的库 → 正常升级；
  - 存在 direct-`ACTIVE` V5 行/事件/outbox/receipts 的库 → 稳定 recovery-required 错误，
    不得部分迁移或 backfill；
  - disposable 开发库 → rebuild-only；durable 库 → export→verify→replay→identity map→reconcile→cutover；
- 012 与历史事件信封不重写、不重标；后续 migration 需覆盖 populated 012 的 upgrade/recovery。

## 7. Verification

- 状态机 reachability（REGISTERED→ACTIVE 及非法边）；creation/activation 事件与 receipt replay；
- 篡改 row/envelope/scalar 拒绝；fresh 与 populated migration；PG 并发激活（双 activate 恰一胜出）；
- 激活非 owner 调用 fail-closed + audit。

## 8. Evidence

- `evidence/v5/stage-1/authority-foundation/<run-id>/`，digest-bearing manifest + subject commit；
- facet 分栏：contract / replay 按实跑记录；domain-provider-live / agent-causal / repo-sandbox /
  human-authorized-external / production-canary 一律 `NOT_RUN`。

## 9. Stop gate

- 历史 ACTIVE row 无法确定性 backfill；activation 可被非 owner 调用；
- read path 不能重建 revision/digest；任一命中即 NO-GO，先 ADR/修复再继续。

## 10. Rollback

- 禁用新 routes/事件写入、停 dispatcher，保留全部 append-only facts 与已发事件；
- 只允许 roll-forward repair；降级仅限尚无任何新生命周期修订/激活事件存在时。

## 11. Commit

`feat(v5): establish authority event foundation`（单一语义；禁止混入 1B/1C 业务扩展与 ops/live WIP）。

## 12. Unlock

R2（V5-1A Application Catalog closure）。

## 13. 迁移影响复核要点（D1 收尾）

- 盘点本机是否存在 durable 库（有/无都要留证据）；无 durable 环境时 inventory 并标 `N/A`，
  不得假设为无；
- direct-ACTIVE 历史数据清单与处置路径写入 evidence。