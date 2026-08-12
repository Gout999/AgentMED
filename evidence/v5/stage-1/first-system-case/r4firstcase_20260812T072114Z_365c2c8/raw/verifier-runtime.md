# V5-R4-full Gate — Independent Verifier Report (Angle: runtime)

- Verifier: independent runtime verifier（只读审查；唯一写入为本报告文件）
- Worktree: /private/tmp/caseloop-r4-verify.37W4g6/checkout（detached）
- HEAD: df86662e762e431ffdc3442cb4e79b9ba0001615（`feat(v5): first system case binding and acceptance criteria`）
- Base: f266980
- git status: `Not currently on any branch. nothing to commit, working tree clean`（仅 evidence 目录除外，其内容不在提交中）
- Subject: R4 语义提交 df86662 —— 5 个 1C intent 激活（FROZEN_R4）+ case binding / acceptance criteria runtime + readiness 界限（PENDING_MATERIALIZATION，绝不 READY）+ CLI case 命令族
- Verification date: 2026-08-12

## Verdict

**CONDITIONAL PASS（1 个 P0、3 个 P1）**

5 个 1C 路由与 manifest 精确一致、bind/propose/confirm 的 exact 绑定与同事务
authority 记录、readiness 界限、4 个测试文件全绿、wire 校验注册完整 —— 均独立
复核通过。但 Master §17.5（R4 gate acceptance）明确要求「duplicate confirmation …
与 concurrent confirm 全 fail closed」，运行时对同一 proposal 用不同 idempotency
key 二次 confirm **不拒绝**（行为探针实测产生第二个 CONFIRMED revision），且无任何
测试覆盖。该 P0 需在 R4 关门前处置。

## 独立复核结果（每项实际执行）

| # | 检查项 | 方法 | 结果 |
|---|---|---|---|
| ① | 5 条 1C 路由 operation_id 与 operation-manifest.json 一致 | 直接运行 import-time gate `check_registered_v5_routes(public_v5.router)` + 逐条映射 | PASS：gate 精确通过；5 个 FROZEN_R4 intent（cases.bind-application / case-application-bindings.get / acceptance-criteria.propose / acceptance-criteria.get / acceptance-criteria.confirm）的 (method, path, operation_id) 全部 OK；注册路由共 19 条 = manifest http 条目 |
| ② | case_binding / acceptance 的 bind/propose/confirm 语义 | 通读 `app/services/case_binding.py`（899 行）、`app/services/acceptance.py`（1065 行）+ 行为探针 | PASS（详见下文） |
| ③ | confirm 后 readiness=PENDING_MATERIALIZATION（绝不 READY）、next_action 清空 | 读 acceptance.get() + 测试断言 | PASS：`readiness = "PENDING_MATERIALIZATION" if confirmed else "NEEDS_ACCEPTANCE_CRITERIA"`（acceptance.py:630-632），无 READY 分支；confirmed 时 `next_action=None`（:642）；测试 test_confirm_creates_new_immutable_confirmed_revision 断言 case_readiness=="PENDING_MATERIALIZATION" 且 next_action is None（test:1326-1327）；schema 枚举含 READY（未来 V5-4A 状态）但运行时绝不输出，符合 Master §17.5「readiness 最大到 NEEDS/PENDING」 |
| ④ | 指定 4 个测试文件 | `cd control-plane && env -u CASELOOP_ALLOW_INTEGRATION_RESET -u DATABASE_URL /Users/xiejiachen/caseloop/control-plane/.venv/bin/python -m pytest tests/unit/test_v5_case_binding_acceptance.py tests/unit/test_v5_capabilities.py tests/unit/test_public_v5_api.py tests/unit/test_v5_system_versions_record.py -q` | **77 passed in 2.90s**（全绿） |
| ⑤ | v5_generated_wire.py 的 5 个 intent 响应校验注册 | 读 `app/public_api/v5_generated_wire.py` + 程序化交叉核对所有路由 model | PASS：5 个 R4 intent 在 request 与 response 两个映射中均注册（bind 的 req/resp、binding-get resp、propose req/resp、get resp、confirm req/resp）；全部 28 个路由 model 均在注册表内，无遗漏 |
| ⑥ | duplicate confirm / concurrent confirm 是否 fail-closed（§17.5） | 行为探针（sqlite 内存库复用测试 helper，不落盘） | **FAIL**：同一 proposal 以不同 idempotency key 二次 confirm 成功，revision 数 2→3（PROPOSED+CONFIRMED+CONFIRMED），无任何拒绝；同 key 并发由 uq_public_idempotency_scope 兜底，不同 key 不设防 |

## P0 / P1 列表

### P0-1 — 同一 proposal 的重复确认（duplicate confirmation）不 fail-closed
- 位置: control-plane/app/services/acceptance.py:455-527（`confirm`/`_load_proposed_revision`）；
  模型层无约束: control-plane/app/models/v5_tables.py:895-940（无
  exact_previous_proposed_revision_binding 唯一性约束）
- 说明: `_load_proposed_revision` 仅要求目标 revision `confirmation_status == "PROPOSED"`
  且 digest 匹配；proposal 永不改写（保持 PROPOSED），因此持有 proposal id/digest 的客户端
  可无限次以新 idempotency key 重新 confirm，每次生成一条新的 CONFIRMED revision（含各自的
  event/outbox/audit/authority receipt/idempotency receipt）。
- 实测: 行为探针 —— propose（agent）→ confirm（confirmer，key A）→ confirm（同一 proposal，
  key B）全部成功；revisions 2→3，statuses `['PROPOSED','CONFIRMED','CONFIRMED']`。
- 依据: docs/plans/v5-master-execution-plan.md:1003-1004（Master §17.5 acceptance）明确
  「cross-workspace/project、stale VersionSet/source、wrong proposer/confirmer、expired
  credential、duplicate confirmation、audit/outbox failure 与 concurrent confirm 全 fail closed」。
  同 key 重放/并发由 `uq_public_idempotency_scope`（v4_tables.py:371-375）与
  `PublicIdempotencyService.acquire` 兜底（fail-closed 成立）；不同 key 的重复/并发 confirm 无任何
  守卫，且契约层（schemas、conformance）、unit/integration/CLI 测试均无覆盖。
- 影响: 产生重复的权威 CONFIRMED 记录（审计/治理完整性受损）；readiness 仍为
  PENDING_MATERIALIZATION（不升级），但 §17.5 的写路径 fail-closed 验收未达成。
- 修复方向（供参考）: confirm 时校验该 proposal 尚无 CONFIRMED 子记录（同 exact binding
  已存在 confirmed revision 即拒绝，例如 VALIDATION_FAILED/ALREADY_CONFIRMED）；或对
  (proposal id) 的 confirmed 子记录加唯一性约束；并补 unit/integration 双覆盖。
- 注: 施工方验证记录（run-20260811-v5-1c/verification.md）与提交说明均未声称覆盖
  duplicate-confirm，属验收清单遗漏而非声明不符。

### P1-1 — acceptance.py 模块 docstring 与运行时语义矛盾（stale）
- 位置: control-plane/app/services/acceptance.py:7-10
- 说明: docstring 称「a case with a confirmed revision for its exact binding is READY,
  otherwise NEEDS_ACCEPTANCE_CRITERIA」，但运行时（:630-632）confirmed 后输出
  PENDING_MATERIALIZATION，永不输出 READY（Master §17.5 前 V5-4A 不允许）。文档与代码矛盾，
  会误导后续维护者。
- 建议: docstring 改为 PENDING_MATERIALIZATION 语义。

### P1-2 — R4 提交未携带本阶段 evidence；现存 evidence 与 R4 语义冲突
- 位置: evidence/v5/stage-1/first-system-case/（R4 提交 df86662 未新增任何 evidence 目录）；
  run-20260811-v5-1c/verification.md:40
- 说明: `git show df86662 --stat` 无 evidence 改动；AGENTS.md「Definition of done」要求每阶段
  evidence under evidence/v5/。唯一现存的 first-system-case evidence 来自 R3 时代提交，其
  Exit 行「reauthenticated human 经 CLI confirm 后 readiness=READY」与 R4 的
  PENDING_MATERIALIZATION 语义直接冲突；若作为 R4 gate 证据引用会误述 readiness。
- 建议: R4 关闭时补充本 stage evidence（含本 verifier 报告目录），并在新 evidence 中明确
  R4 readiness 界限，覆盖旧 READY 声明。

### P1-3 — validate_generated_wire 对未注册 model 静默放行（fail-open 设计）
- 位置: control-plane/app/public_api/v5_generated_wire.py:100-103
- 说明: `intent is None → return`（跳过生成式 wire 校验）。当前无实际影响（程序化核对：28 个
  活跃路由 model 全部已注册）；但 route↔manifest import-time gate 只校验 (method, path,
  operation_id)，不校验 model↔schema 注册，未来新增激活路由若漏登记 model，生成式校验将被
  静默跳过。
- 建议: 对已激活/已注册路由的 model 缺失时改为抛错（fail-closed），或让 route-registry gate
  一并校验 model 注册完整性。

## 重跑命令实际结果

- 测试: 4 文件 `-q` → **77 passed in 2.90s**（无失败、无 skip）
- route gate: `check_registered_v5_routes(public_v5.router)` → PASS（exact match，19=19）
- duplicate-confirm 探针: 二次 confirm（新 key）**成功**，revisions 2→3 —— 见 P0-1
- git status: clean（detached, HEAD=df86662）

## ② 语义核对要点（通过项）

- **exact case revision/digest 绑定**: bind/propose 均经 `_load_exact_case`
  （case_binding.py:426-460 / acceptance.py:273-307）逐项校验
  `revision == case_revision`、`record_digest == case_digest`、
  `snapshot.record_digest == case_digest`，任一不符 → audited RESOURCE_NOT_FOUND（opaque）；
  confirm 经 `_load_proposed_revision`（acceptance.py:529-560）校验 workspace + PROPOSED 状态 +
  digest 精确匹配。
- **immutable authority 同事务**: `_write_binding_record`（case_binding.py:492-645）与
  `_write_revision`（acceptance.py:683-925）在同一 session 内依次写入记录行 + event
  （`V4EventStore._append_event` 内 `session.add_all([event, outbox])`，同事务）+ controller
  audit + authority receipt + command audit + idempotency receipt；服务只 flush，路由单一
  `_commit`；audit/outbox 失败抛错由路由 rollback（test_bind_application_audit_failure_rolls_back_everything
  与 test_binding_service_never_commits / test_acceptance_service_never_commits 覆盖）。
- **fail-closed 输入**: cross-workspace（case/application/environment 同 workspace 校验，
  不同 workspace → opaque not-found）、wrong actor（bind 仅 human/service + cases:bind scope；
  confirm 仅 human + acceptance_criteria:confirm scope + proposer≠confirmer +
  reauthentication issued_at ≥ proposed_at；agent 可 propose 不可 confirm）、stale
  revision/digest（not-found）、同 exact case 不同 target → CATALOG_CONFLICT（probe + 测试）、
  同 target 不同 key → 幂等重放同记录、同 key 重放 → 原响应。
- **readiness/next_action**: 无 confirmed → NEEDS_ACCEPTANCE_CRITERIA +
  next_action=CONFIRM_ACCEPTANCE_CRITERIA；有 confirmed → PENDING_MATERIALIZATION +
  next_action=None；运行时无 READY 分支。

## 问题详情

见上表 P0-1 探针命令与输出摘要、P1-1/2/3 文件:行引用。全部判断基于本 verifier 自行执行的
命令与代码阅读，未采信施工方声明。
