# V5 迁移恢复手册（011/012 legacy-V5-history preflight）

> 状态：**C5 文档化 / RECOVERY DRILL NOT RUN（drill 见 C5 rollback drill）**
>
> 依据：`docs/plans/v5-architecture-convergence.md` §7（migration and commit
> protocol）与 §9（recovery matrix，[v5-architecture-convergence.md#9](v5-architecture-convergence.md#9)）
>
> 适用迁移：
> - `control-plane/alembic/versions/011_v5_lifecycle_authority_foundation.py`
> - `control-plane/alembic/versions/012_v5_event_envelope.py`
>
> 本文只记录恢复路径，不修改迁移文件本体（迁移是 frozen 语义，任何修正走
> 新迁移并单独授权）。

## 1. 触发条件与错误码

两条迁移的 `upgrade()` 第一条语句都是 `_assert_no_legacy_v5_history()`
（`tests/unit/test_migrations.py::test_v5_r1_graph_and_legacy_preflight_precedes_every_ddl`
用 AST 钉住该结构）：在第一个 DDL 之前做只读盘点，发现任何 legacy V5 事实
即抛出 stable NO-GO，禁止猜测性回填（convergence plan §7）。

| 错误码 | 迁移 | 触发事实（任一即 NO-GO） |
|---|---|---|
| `011.legacy_v5_lifecycle_requires_explicit_recovery` | 011 | `_LEGACY_V5_TABLES`（12 张：`ai_applications`、`environments`、`system_components`、`dependency_edges`、`component_revisions`、`topology_revisions`、`system_version_sets`、`bootstrap_attestations`、`system_assignments`、`application_case_bindings`、`acceptance_criteria_revisions`、`issue_source_snapshots`）任意一行；或 `events`/`outbox` 中 `aggregate_type IN (11 个 v5 aggregate types)`；或 `authority_receipts` 中 `subject_kind IN (11 个 v5 subject kinds)` |
| `012.legacy_v5_event_envelope_requires_explicit_recovery` | 012 | `_V5_TABLES`（011 的 12 张 + `ai_application_lifecycle_revisions`、`system_component_lifecycle_revisions`）任意一行；或 `events`/`outbox` v5 aggregate type；或 `authority_receipts` v5 subject kind 计数非零 |

NO-GO 的失败形态是零部分 schema 变更：

- `alembic_version` 停留在前一版本（011 失败时仍为 `010`，012 失败时仍为 `011`）；
- 011 的 `public_principals.trust_roles`、`ai_application_lifecycle_revisions` 不出现；
- 012 的 `events.event_contract_major`、`outbox.event_contract_major`、索引
  `uq_events_v5_workspace_agg_seq` 不出现。

由 `test_v5_r1_legacy_preflight_has_zero_partial_schema_mutation`、
`test_v5_event_preflight_rejects_post_011_facts_before_first_ddl`（sqlite）与
`test_v5_r1_legacy_preflight_preserves_postgresql_schema_fingerprint`、
`test_v5_event_preflight_rejects_post_011_postgresql_facts_before_first_ddl`
（PG）用 `_schema_fingerprint` 逐一钉住。

## 2. 恢复路径总览

```
触发 NO-GO
  ├─ 1. 冻结与导出（export，只读）
  ├─ 2. digest/shape verify（对照 frozen schemas）
  │     ├─ 一致 → 3. 受审计 replay/recovery（roll-forward）
  │     └─ 不一致 → STOP：保留导出与证据，升级到 migration-owner 审批
  ├─ 4. 恢复后重跑 preflight（应通过）
  └─ 5. upgrade head → post-check（head=012，v5 facts 合法）
```

原则（convergence plan §7、§9 一致）：

- **roll-forward 优先**：把 legacy history 转成合法 V5 权威事实后继续
  upgrade head；
- **downgrade 不是默认回滚**：只在零事实（fresh/empty）数据库上允许；
  事实存在时 downgrade 被 guard 阻塞，**禁止删除 append-only 权威事实**；
- 歧义历史产生 stable NO-GO + 本恢复指南，绝不猜测回填。

## 3. 步骤

### 3.1 冻结与导出（export）

只读连接（`SET TRANSACTION READ ONLY` 或只读副本），导出全部可能触发
preflight 的事实，逐表/逐查询一份 canonical JSON + 行数 + SHA-256 清单：

- 12（011）／14（012）张 V5 表全行；
- `events`、`outbox`：`WHERE aggregate_type IN (<11 个 v5 aggregate types>)`
  （011 的 `_V5_AGGREGATE_TYPES` 与 012 相同）；
- `authority_receipts`：`WHERE subject_kind IN (<11 个 v5 subject kinds>)`；
- `public_principals`：`trust_roles` 非空的行（downgrade guard 也检查此项）。

导出物本身是只读证据类别（platform evidence export 语义），不构成任何
成功 facet。

### 3.2 digest/shape verify

对照 frozen 权威（`contracts/v5/schemas/*.schema.json`）逐条校验：

- 行 shape：`recordEnvelope`（`common.schema.json#/$defs/recordEnvelope`）与
  `records.schema.json` 各 record 定义；`hash_rule`、`revision`、
  exact-binding 链与 schema 的 `allOf` lifecycle 规则一致；
- digest：`record_digest`/`envelope_payload`/`authority_receipts` 引用满足
  `^sha256:[0-9a-f]{64}$`（`common.schema.json#/$defs/digest`），与生成的
  `contracts/v5/generated/ts/applications.list.ts` guard 模块
  （`SHA256_DIGEST`、`envelope`、`exactBinding`、revision guard）判据一致；
- 失败即 STOP：导出、指纹与失败证据原样保留，升级到 migration-owner
  审批（recovery matrix「Migration ambiguity」行：Abort before partial
  rewrite；re-entry 需要 migration-owner approval + failed wave）。

### 3.3 受审计 replay / recovery（roll-forward）

恢复不是直改 append-only 表：每一条恢复写入必须走与正常运行时相同的
权威路径——同一 PostgreSQL UoW 内产生 AuthorityReceipt + audit 记录 +
（如适用）事件/outbox 行，禁止绕过 `application-catalog-controller`
权威语义（见 `contracts/v5/intent-registry.yaml` 的
`activation_authorization` / `authority_chain`）。

恢复完成判据：

- 原 legacy 事实成为合法 V5 权威事实（不再命中 preflight 的 legacy
  检查）；或已明确为已审计的历史并记录处置；
- 重跑 `_assert_no_legacy_v5_history`（或直接 `alembic upgrade head`）
  通过，schema 与版本号进入 012/head；
- 恢复过程本身的 audit 可回放，行数、digest、cardinality 与导出清单
  对账一致。

### 3.4 downgrade 被 fact 阻塞时的处置

`downgrade()` 第一条语句是 `_assert_downgrade_safe()`；有事实时抛出：

- `011.downgrade_blocked.lifecycle_authority_exists`：lifecycle revision
  表有行、`_v5_identity_history_exists()` 为真，或任一 principal 的
  `trust_roles` 非空；
- `012.v5_r1_history_prevents_downgrade`：任一 V5 表行、
  `contract_version='v5'` 或 v5 aggregate 的 events/outbox、v5 subject
  kind 的 authority_receipts，或 `trust_roles` 非空。

处置顺序（convergence plan C5 rollback 语义，destructive downgrade 被
§7 禁止）：

1. **不删任何事实**：append-only 权威事实（events/outbox/receipts/audit）
   保持原样；guard 失败后 schema 不变（`test_v5_r1_011_downgrade_guard_preserves_sqlite_schema`、
   `test_v5_r1_011_postgresql_downgrade_guard_preserves_schema`、
   `test_v5_r1_012_downgrade_blocks_persisted_r1_fact`、
   `test_v5_r1_012_postgresql_downgrade_preserves_schema` 钉住）；
2. **禁用受影响 V5 入口**：当前无运行时 kill-switch；禁用路径由
   `control-plane/tests/test_v5_c5_rollback_drill.py` 通过构造验证——
   构造不含 v5 router 的 app 后，V1/V4 字节不变、v5 写路径 404 且不产生
   v5 outbox 行、alembic head 仍为 `012`、错误信封为 404 而非 500；
3. **保持 V3/V4 compatibility 服务**（已验证的 legacy adapter 继续服务）；
4. **roll forward**：按 §3.1–3.3 完成受审计恢复后重新 upgrade head；
   若 C5 验证失败，按 recovery matrix「C5 cleanup/enforcement/recovery
   failure」行：保持 D2/R3/R4/V5-2+ LOCKED，回到最早失败的 wave 重开。

## 4. 相关引用

- 迁移本体（只读引用，禁止修改）：
  - `control-plane/alembic/versions/011_v5_lifecycle_authority_foundation.py`
    （`_assert_no_legacy_v5_history`、`_assert_downgrade_safe`、
    `_DOWNGRADE_BLOCKED = "011.downgrade_blocked.lifecycle_authority_exists"`）
  - `control-plane/alembic/versions/012_v5_event_envelope.py`
    （`_assert_no_legacy_v5_history`、`_assert_downgrade_safe`、
    `_DOWNGRADE_BLOCKED = "012.v5_r1_history_prevents_downgrade"`）
- 迁移测试：`control-plane/tests/unit/test_migrations.py`
  （`test_v5_r1_graph_and_legacy_preflight_precedes_every_ddl`、
  `test_v5_r1_legacy_preflight_has_zero_partial_schema_mutation`、
  `test_v5_event_preflight_rejects_post_011_facts_before_first_ddl`、
  `test_v5_r1_legacy_preflight_preserves_postgresql_schema_fingerprint`、
  `test_v5_event_preflight_rejects_post_011_postgresql_facts_before_first_ddl`、
  `test_v5_r1_fresh_postgresql_upgrade_reaches_exact_head`、
  `test_v5_r1_011_downgrade_guard_preserves_sqlite_schema`、
  `test_v5_r1_011_postgresql_downgrade_guard_preserves_schema`、
  `test_v5_r1_012_downgrade_blocks_persisted_r1_fact`、
  `test_v5_r1_012_postgresql_downgrade_preserves_schema`）
- 恢复矩阵：`docs/plans/v5-architecture-convergence.md#9`（「Migration
  ambiguity」与「C5 cleanup/enforcement/recovery failure」两行）
- 禁用路径 drill：`control-plane/tests/test_v5_c5_rollback_drill.py`
- 权威 shape/digest 源：`contracts/v5/schemas/*.schema.json`、
  `contracts/v5/generated/ts/applications.list.ts`、
  `contracts/v5/intent-registry.yaml`
