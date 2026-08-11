# V5-1B System manifest / VersionSet / 原子导入 — Verification Evidence

- Stage: V5-1B (ComponentRevision / SystemVersionSet / SystemAssignment /
  trusted manifest import / `caseloop init` discovery)
- Run ID: `run-20260811-v5-1b`
- Commit: `feat(v5): add immutable system versions`
- Date: 2026-08-11 (local)

## Acceptance commands

| Command | Result | Count |
|---|---|---|
| `control-plane pytest tests/unit` | PASS | 739 passed |
| `control-plane pytest tests/integration` (PG 127.0.0.1:5432 control_plane_test, CASELOOP_ALLOW_INTEGRATION_RESET=true) | PASS | 22 passed |
| `cli pytest tests` (via control-plane .venv) | PASS | 89 passed |
| `contracts conformance` (../eval-harness/.venv/bin/python -m pytest conformance/) | 517 passed / 15 failed | 15 failures all in `test_quality_api.py` — Quality API service at 127.0.0.1:8080 not running (pre-existing live-gate gap, unchanged from V5-1A). Offline baseline 517 preserved. |
| `console npm run build` | PASS | tsc + vite build OK |
| `console npm test` | PASS | 7 passed |
| One-shot `alembic upgrade head` on disposable PG | PASS | head = 009 |
| Old S1A / V5-1A suites | PASS | unit 739 (incl. test_public_v4_api, test_v5_application_catalog) + integration 22 (incl. stage1a + v5 catalog) |

## blueprint V5-1B Verification (docs/plans/v5-progressive-delivery.md §3) coverage

| Verification item | Coverage |
|---|---|
| ComponentRevision / SystemVersionSet / SystemAssignment desired pointer | migration 009 (`009_system_version_manifest.py`) + `app/services/system_versions.py`; one-shot import constructs all 9 owners in one local PG transaction |
| trusted one-shot manifest import（1A+1B owners 联合出口） | `system-manifests.import` intent: application/env/components/edges via application-catalog-controller, revisions/topology/version-set/attestation/assignment via version-controller; per-construct event+controller-audit+authority receipt in one transaction; manifest-level command audit + idempotency receipt |
| `caseloop init <repo>` 本地只读 discovery | `cli/src/caseloop_cli/discovery.py`: git revision/tree/ref, project type, test commands, APPLICATION_CODE/PROMPT/MODEL_BINDING/RETRIEVER/INDEX 可识别组件; 草稿标 `application`/`environment` 为人工必填; 不调用 import、不写服务端状态 |
| identity assurance 与 semantic diff | identity_assurance discriminator 校验 (IMMUTABLE_DIGEST/PROVIDER_VERSION/MUTABLE_ALIAS/OBSERVED_ONLY/UNKNOWN); diff 覆盖 ADDED/REMOVED/DIGEST_CHANGED/DEPENDENCY_SUBSTITUTION/PERMISSION_EXPANSION |
| manifest CLI validate/record/get/diff | `system-manifest import\|record\|validate\|get\|diff`; record 为 import 的 CLI 别名（同一 canonical intent） |
| first manifest 只含可靠确认组件，不造空资产 | manifest 模型要求至少一个 APPLICATION_CODE；discovery 只对文件名/目录名启发可确认的路径发组件，否则 UNKNOWN/省略 |
| 独立可信 human approver policy revision 导入 | manifest `approver_policy`（POLICY 组件）在事务内记录为 COMPONENT_REVISION，但**不进入** runtime VersionSet bindings、不进 topology；diff/版本集不包含它 |
| immutable JCS digest 与 provenance refs | v5 record digest（envelope 全量 JCS）、component configuration_digest、topology_digest、version_set_digest、manifest_digest；artifact_refs/git provenance 由 discovery 提供 |
| bootstrap assignment 固定 generation=1 / previous=null / exact BootstrapAttestation | import 构造 BOOTSTRAP 转换，generation=1、expected_previous_generation=null、exact_assignment_authority_binding=bootstrap attestation；单元+PG 断言 |
| mutable alias/unknown 不冒充 immutable | 模型拒绝非 IMMUTABLE_DIGEST 携带 content_digest；MUTABLE_ALIAS 需要 provider_origin+resolved_at；UNKNOWN 需要 unknown_reason |
| same label/different digest / dependency substitution / policy permission expansion | `_semantic_diff` 单元测试三组向量全覆盖 |
| assignment CAS / idempotency | DB 部分唯一索引（one non-retired assignment per identity key）+ 服务层 one-shot 冲突；same-key 与 same-manifest-digest 双路径 replay |
| bootstrap authority 只证明 desired 不证明 observed | attestation `proves: authority_to_create_initial_desired_assignment_only`；无 observed 字段/断言 |
| VersionSet immutable | 4 张 immutable 表注册 before_update/before_delete guard（单元断言更新被拒） |
| graph digest 与 component revisions 精确绑定 | version_set_digest 覆盖 exact_component_revision_bindings + exact_topology_revision_binding + 派生 assurance summary；digest 确定性/绑定敏感测试 |
| discovery root escape / symlink / secret redaction / unstable repeat scan | CLI 测试 8 项：非 git 目录拒绝、root symlink 拒绝、内部 symlink 不跟随、.env/credentials 红名单不泄漏、两次扫描草稿逐字节一致、组件识别 |
| 无法可靠识别组件保留 UNKNOWN | discovery 在无 git 时 APPLICATION_CODE assurance=UNKNOWN + unknown_reason；子路径无 digest 时 UNKNOWN |

## Exit criteria

- 一个 application/environment 有 exact declared VersionSet 与 desired assignment：PASS（PG + CLI E2E：import 产出 version set + bootstrap assignment）。
- 不宣称 observed runtime：PASS（无 observed 字段、无运行态断言）。
- Evidence 目录：`evidence/v5/stage-1/system-version/run-20260811-v5-1b/`。
- Commit：`feat(v5): add immutable system versions`（禁止 push）。

## trusted_attestor 对齐处置（0C 验收遗留）

- 处置：**最小修补（移除悬空角色）**，不改变 `system-manifests.import` 的
  `[integrator, catalog_admin, trusted_builder]`。
  1. `contracts/v5/intent-registry.yaml` `system-versions.record.required_trust_roles_any_of`
     `[integrator, trusted_builder, trusted_attestor]` → `[integrator, trusted_builder]`；
  2. `contracts/v5/domain-model.yaml` `bootstrap_attestation.allowed_attester_trust_roles`
     `[integrator, trusted_builder, trusted_attestor]` → `[integrator, catalog_admin, trusted_builder]`
     （与 importer_trust_roles 精确对齐——bootstrap attestation 由可信 manifest import 在事务内创建，导入者即 attester）；
  3. `contracts/v5/fixtures/bootstrap-import-atomic.yaml` contract_grounding 同步更新。
- 理由：角色词表中不存在 `trusted_attestor` 的定义（0C 验收注记为悬空）；移除后角色词汇表与
  manifest import 允许清单一致。conformance 离线基线复跑 517 passed / 15 live-gate failed 不变。
- 无 conformance 断言依赖被移除的角色清单（test_v5_first_slice.py:236/241 只断言 import 角色）。

## Evidence facets

| Facet | Status |
|---|---|
| contract | PASS — conformance 517（15 个 quality-api live 前置失败属环境缺口，非本 slice；与 V5-1A 一致） |
| replay | NOT_RUN |
| domain-provider-live | NOT_RUN |
| agentteams-native | NOT_RUN |
| claude-runtime-live | NOT_RUN |
| agent-causal | NOT_RUN |
| repo-sandbox | PASS — local-runtime：real PG + Alembic 009 + real installed CLI subprocess + real uvicorn（本 slice 的 local-runtime 证据） |
| human-authorized-external | NOT_RUN |
| production-canary | NOT_RUN |

## Honest uncertainties / decisions

1. **Manifest wire shape 是 DRAFT 运行时解释**：`field_contract_ref` 为 null，无冻结 JSON schema。
   字段布局（`application`/`environment`/`components[].revision`/`dependency_edges`/`approver_policy`）
   与 identity assurance 判别式约束是本 slice 的实现解释；后续合同冻结需确认。
2. **Trust roles 尚未 server-derived**：1A 已注记。本 slice 以 scope `system_manifests:import`
   + principal_type（human/service）作为 enforcement；manifest 的 attester_trust_role 固定写
   `integrator`（attester 即导入者）。fixture 的 negative case `importer_role_not_allowlisted`
   由 scope/principal_type 拒绝路径覆盖。
3. **事件 payload 扁平化**：5 个新 subject 的 event payload 携带 flatten 业务字段
   （与 1A 一致），`exact_*_binding` 由 controller 共享字段承载，v5 event envelope
   （event_contract_major 2）是后续 slice 项。
4. **Bootstrap 空域前置按 workspace 解释**：fixture preconditions `authoritative_v5_domain_tables: EMPTY`
   实现为「该 workspace 尚无 ai_application 行」；同 workspace 第二次 manifest → CATALOG_CONFLICT
   （MANIFEST_BOOTSTRAP_ALREADY_EXISTS）整笔回滚。增量演进走 1A catalog CLI 路径。
5. **attestation_scope 闭合域**：`('INITIAL_DESIRED_ASSIGNMENT',)` 为 1B 保守值，
   `assignment.lifecycle_state/exposure` 闭合域为 `('ACTIVE','RETIRED')` / `('EXPOSED','STOPPED')`，
   均未在冻结合同中枚举（同 1A 的 domain-value 不确定性）。
6. **approver policy revision 的 `record` CLI 别名**：intent-registry 只有
   `system-manifests.import` 的 HTTP 路径；CLI `system-manifest record` 复用同一 canonical import
   （manifest 级 record 语义），`system-versions.record` 独立 HTTP 路由不在本 stage 交付
   （简报 route 清单为 import/get/diff）。
7. **语义 diff 的 permission expansion 信号**：以 POLICY 组件 revision 的
   `permission_manifest_digest` 变化为信号；component 级 `permission_classification`
   （READ_ONLY→READ_WRITE→ELEVATED）在 diff 中作为 details 附带，不作为独立触发
   （组件本身是 singleton，不随版本集 revision 变化）。
8. **Baseline counts**：conformance 517 passed / 15 failed（live-gate）与 V5-1A 相同；
   unit 715→739、integration 20→22、cli 81→89 为本 slice 新增测试。
9. **既有 dirty worktree**：README/contracts-conformance-README/eval-harness/scripts/wiki/docs
   修改与 untracked V5_CONSTRUCTION_CONTEXT.md / D-013 / b1_live/ 在 V5-1A 前已存在，
   本次提交不含这些文件（仅 contracts/conformance/README.md 的修改在 V5-1A 前已存在，
   本 slice 未触碰；`git add` 按文件白名单逐文件加入）。
10. **`test_stage1a_local_bootstrap.py` head 断言更新**：该测试把 alembic head 硬编码为 '008'；
    head 前进到 009 后改为从 alembic script 动态取当前 head（断言语义不变：单 head + 祖先含 007）。
