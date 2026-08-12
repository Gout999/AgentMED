# CaseLoop V5-R4-full Gate — Independent Verifier Report (ANGLE=journey)

- **Verifier**: journey 独立 verifier（本报告作者），只读审查；唯一写权限为本报告文件。
- **Subject**: semantic commit `df86662`（feat(v5): first system case binding and acceptance criteria），base=`f266980`，worktree `/private/tmp/caseloop-r4-verify.37W4g6/checkout`（detached）。
- **Gate**: Master §17.5 R4 — First System Case closure。
- **日期**: 2026-08-12（会话内）。

## 0. Subject 状态确认

- `git status`：detached，`nothing to commit, working tree clean`（首次检查）；HEAD=`df86662e762e431ffdc3442cb4e79b9ba0001615`；merge-base with f266980 = `f266980`（base 确认）。
- 验证期间出现两个 untracked 条目（详见 §6 Observation）：
  - `evidence/v5/stage-1/first-system-case/r4firstcase_df86662/`（并发 verifier 报告目录，含 `verifier-runtime.md`、`verifier-contracts.md`）。
  - `cli/uv.lock`（110KB，15:02 出现；base 与 HEAD 均无此文件，非我产生，疑似并发验证进程 `uv` 产物）。

## 1. 重跑命令与实际结果（全部独立执行）

| # | 命令 | 结果 |
|---|---|---|
| 1 | journey 3 文件：`env CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test /Users/xiejiachen/caseloop/control-plane/.venv/bin/python -m pytest tests/integration/test_v5_case_binding_r4_postgres.py tests/integration/test_v5_case_binding_acceptance_postgres.py tests/integration/test_v5_system_versions_r3_postgres.py -q` | **5 passed, 1 skipped** (2.82s) |
| 2 | CLI：`cd cli && .../python -m pytest tests -q` | **130 passed** (1.12s) |
| 3 | stage1a 真实服务器 CLI e2e：`tests/integration/test_stage1a_public_cli_postgres.py` | **1 passed** (5.51s) |
| 4 | alembic head | `013 (head)` |
| 5 | `git diff --name-only f266980 df86662 -- control-plane/alembic` | 空（**R4 未改动任何 migration/env.py**） |
| 6 | conformance：`cd contracts && .../python -m pytest conformance/ -q` | **557 passed, 15 failed**（15 个失败均为 quality-api 服务未启动的 live 前置失败，与 R4 无关） |
| 7 | control-plane unit（无 env）：`pytest tests/unit -q` | 895 passed, 12 skipped（PG opt-in skip） |
| 8 | control-plane unit + C4（无 env） | 910 passed, 12 skipped |
| 9 | **canonical 全量**（AGENTS.md 文档命令，含 env）：`CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=... pytest -q` | **30 failed, 1007 passed, 1 skipped** (94.82s) —— 30 个失败为 SQLite 迁移单测被 `alembic/env.py` 的 DATABASE_URL 覆盖劫持到 PG（详见 P1-3） |

journey 重跑核心结论：**journey 必须全过的部分全部通过**；唯一的 skip 是 R2 遗留 from-issue e2e（确认合理，见 §3）。

## 2. journey 覆盖 §17.5 Exit 核对（按任务要求逐项）

journey 主测试 `test_v5_case_binding_r4_postgres.py::test_r4_source_case_bind_propose_confirm_journey_postgres`（L395-586，真实 HTTP TestClient + disposable PG + alembic upgrade head）覆盖：

1. **source→case**：`POST /api/v1/signals` 创建 Case（L422-429，201，返回 case_id/revision），并从权威行读 record_digest（L431-436）。
2. **bind**：`POST /api/v2/cases/{case_id}:bind-application`（L439-463，201），exact_case_binding={case_id, case_revision, case_digest} 校验（L466-470）。
3. **bind get**：`GET /api/v2/cases/{case_id}/application-binding`（L473-485，200）。
4. **propose**：`POST /api/v2/cases/{case_id}:propose-acceptance-criteria`（L488-508，201，PROPOSED，proposer=prn_...R102）。
5. **get（readiness 下限）**：`GET .../acceptance-criteria` → `NEEDS_ACCEPTANCE_CRITERIA`（L510-520）。
6. **confirm**：`POST /api/v2/acceptance-criteria/{acr}:confirm`，先重新签发 confirmer 凭证（issued_at=now ≥ proposed_at，L524-531），→ CONFIRMED（L532-552）。
7. **readiness 上限**：confirm 后再 get → `PENDING_MATERIALIZATION`，**绝不 READY**（L554-564）。
8. **越权 403**：confirmer（wrong scope）调 bind → **403**（L566-583）。

负路径（§17.5 其余 fail-closed 条目）覆盖分布：stale 凭证 → `REAUTHENTICATION_REQUIRED`（acceptance e2e L767-834，服务层）；non-human/wrong proposer/expired/audit 失败/immutable/幂等（unit `test_v5_case_binding_acceptance.py`：non_human_cannot_confirm L1134、proposer_cannot_self_confirm L1173、reauth L1216、audit_failure_rollback L783、immutable L835/L1331、replay L1376）；tampered → fail-closed（R3 `test_r3_tampered_version_set_fails_closed_postgres` L352）；并发 CAS（R3 `test_r3_concurrent_record_cas_exactly_one_winner_postgres` L296，针对 system-version-set）。

**例外（P0-1）**：§17.5 要求 "duplicate confirmation ... 与 concurrent confirm 全 fail closed"，但 acceptance confirm 对**同一 PROPOSED revision 的重复/并发确认**既无服务层检查、也无 DB 约束、更无测试覆盖——详见 P0-1。

## 3. from-issue skip 核对（任务要求：确认 skip reason 合理 + CLI 单元测试覆盖）

- skip 位于 `tests/integration/test_v5_case_binding_acceptance_postgres.py:962-967`；`git log -L` 证实该 skip 在 base `f266980`（R2-era，commit 18482f8）即存在；R4 更新了 reason（由 "intents 未激活 CLI_USAGE_INVALID" 改为 "CLI v1 workspace header 处理 WORKSPACE_ACCESS_DENIED"），与 R4 激活后的现实一致。
- **CLI 单元测试确实覆盖 from-issue 组合**：`cli/tests/test_cli_surface.py::test_case_from_issue_composes_canonical_intents`（L1469）以 mock HTTP 断言 signals.submit → acceptance get → bind → propose 的精确调用序列、不自动 confirm、重试确定性。
- 影响面辨析：`tests/integration/test_stage1a_public_cli_postgres.py`（真实 uvicorn + 真实安装 CLI，v1 `signal submit`/`capabilities get`）**通过**（我重跑 1 passed），证明 v1 CLI 头处理总体健康；故 from-issue 的 WORKSPACE_ACCESS_DENIED 是 from-issue 流程特有的（v1 signal 的 project/environment 作用域或测试种子问题），非普遍性 CLI 回归。
- 结论：skip reason 合理、被 CLI 单元测试与 R4 HTTP journey 覆盖 —— **此项通过**；但 R4 新增的 `case from-issue` 命令缺少真实服务器 e2e 证明，记为 **P1-2**。

## 4. alembic / create_all / migration 013

- `alembic heads` = `013 (head)` ✓（单 head）。
- create_all 禁止断言存在：`tests/integration/test_v5_case_binding_acceptance_postgres.py:556,1005`（`side_effect=AssertionError("v5_1c.must_not_use_create_all")`、`"v5_1c.e2e_must_not_use_create_all"`），另有 stage1a/1b 等多处同模式断言 ✓。
- `app/main.py:68` 的 `Base.metadata.create_all` 位于 `create_tables: bool = False` 参数之后（L41、L67），默认关闭；为 pre-existing 模式，非 R4 引入，不计发现。
- migration 013：`git diff f266980 df86662 -- control-plane/alembic` 为空 → **013 未被 R4 改动** ✓（`013_v5_standalone_version_lineage.py` 为 R3 产物）。

## 5. 其他交叉核对（R4 声明 vs 实际）

- **5 个 1C intent FROZEN_R4**：intent-registry.yaml L35 + L623/644/667/685/707：cases.bind-application、case-application-bindings.get、acceptance-criteria.propose/get/confirm ✓；`implementation_status=IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER` ✓（非 NOT_IMPLEMENTED，R4 实现后标记，等待本 verifier）。
- **19 激活**：capability-manifest `enabled_intent_count=19`（enabled list 19 条含 5 个 R4 intent）✓；operation-manifest `activated_intent_count=19` ✓；compiler `ACTIVATED_WIRE_STATUSES` 加入 FROZEN_R4（contracts/compiler/activated_operations.py）✓；control-plane C4 allowlist 测试同步 14→19 ✓。
- **路由注册**：`app/api/public_v5.py` 将 5 个 handler 从 `_unregistered_*` 注册为带 operation_id 的 @router 端点（bindCaseApplication/getCaseApplicationBinding/proposeAcceptanceCriteria/getAcceptanceCriteria/confirmAcceptanceCriteria）✓；`v5_generated_wire.py` request/response intent 校验表补齐 5 个 ✓；openapi.yaml 生成 5 个新路径 ✓。
- **readiness 语义代码**：`app/services/acceptance.py` R4 改动将 `READY if confirmed else NEEDS_ACCEPTANCE_CRITERIA` 改为 `PENDING_MATERIALIZATION if confirmed else NEEDS_ACCEPTANCE_CRITERIA`（L625-645），next_action 仅 NEEDS 时给出 ✓；测试断言同步 READY→PENDING_MATERIALIZATION（3 处，断言加强非弱化）✓。
- **CLI case 命令族**：bind-application / application-binding get / acceptance-criteria propose|get|confirm / from-issue 均已实现并有专项测试（test_cli_surface.py L968-1706 区域），API-major 守卫（v1 动作拒 v2、v2 动作拒 v1）有测试 ✓；CLI client 对 5 个新 intent 增加 receipt/case_id 绑定校验（fail-closed）✓。
- `git diff --check f266980 df86662` 干净 ✓。

## 6. 发现（P0 / P1 / Observation）

### P0-1 — 重复/并发确认未 fail-closed（破坏 §17.5 验收）

- 位置：`control-plane/app/services/acceptance.py` confirm()（约 L396-527）；表模型 `control-plane/app/models/v5_tables.py:895-980`；DDL `control-plane/alembic/versions/010_application_case_binding_acceptance.py:118-185`。
- 问题：confirm 只校验被引用的 revision 仍是 PROPOSED（`_load_proposed_revision`，L529-553），没有任何"该 proposal 是否已被确认"的检查；`acceptance_criteria_revisions` 无针对 `exact_previous_proposed_revision_binding` 或 (case_id, case_revision, confirmation_status) 的唯一/部分索引。同一 PROPOSED revision 用不同幂等键确认两次 → 产生**两条 CONFIRMED revision**；并发确认同理双双成功（无 CAS）。幂等只挡同键重放。
- 违反：Master §17.5 acceptance "duplicate confirmation ... 与 concurrent confirm 全 fail closed"。
- 覆盖：无任何测试（unit 列表无 duplicate/concurrent confirm 用例；R4 journey 只 confirm 一次；R3 并发测试仅针对 system-version-set）。
- 备注：confirm 服务本体为 R1-R3（V5-0C）产物，但 §17.5 是 R4 的验收契约，且 R4 提交声明 "fail closed" 范围未含此项，故按验收缺口定 P0。

### P0-2 — live 读模型仍返回 READY（违反 §17.5 界限与提交自身声明）

- 位置：`control-plane/app/services/read_views.py:953`（`case_v5_readiness()`：`if confirmed: readiness = "READY"`）；live 路由 `control-plane/app/api/read_views.py:83-97`（`GET /v1/cases/{case_id}/v5-readiness`，router 挂载于 `app/main.py:91`）。
- 问题：Console 的 case governance 投影在存在 CONFIRMED 验收时报告 `READY`，而 §17.5 明确 "readiness 最大到 NEEDS_ACCEPTANCE_CRITERIA 或 PENDING_MATERIALIZATION；只有 V5-4A exact ResolutionContract + executable BadcaseSpec 才能 READY"。R4 提交消息声称 "the read model bounds readiness at NEEDS_ACCEPTANCE_CRITERIA / PENDING_MATERIALIZATION - never READY"，与该 live 表面矛盾。
- 覆盖：`grep -rn "v5-readiness|case_v5_readiness" tests/` 无任何测试（该路由完全无测试覆盖，READY 残留无从被发现）。
- 备注：该文件非 R4 改动范围（R4 只改了 acceptance.py），属 R4 遗漏的 READY 面；同时 `read_views.py:887` docstring 仍写 "NEEDS_ACCEPTANCE_CRITERIA / READY"。

### P1-1 — manifest cli 声明与 CLI 实际命令面不一致（acceptance-criteria.confirm）

- 位置：`contracts/v5/intent-registry.yaml:701`（`cli: acceptance-criteria confirm`）；生成物 `contracts/v5/generated/operation-manifest.json` 同步携带该 standalone 声明；CLI 实际面为 `case acceptance-criteria confirm`，在 `cli/src/caseloop_cli/_generated/operation_manifest.py:496-503` 做了归一化，allowlist 测试 `control-plane/tests/test_v5_c4_allowlist_diff.py`（约 L283-291）专门特判。
- 问题：同一家族的 propose/get 声明为 `case acceptance-criteria propose/get`，confirm 却声明 standalone；按 manifest 生成的 CLI 面与真实 CLI 不符（consumers 会生成 `caseloop acceptance-criteria confirm`）。行为上由归一化+测试锚定，不产生运行时错误，但属于 §17.11 提示的 generated/CLI 不一致面。
- 建议：intent-registry 改 `cli: case acceptance-criteria confirm` 并重生成，简化特判。

### P1-2 — R4 新增 `case from-issue` 无真实服务器 e2e 证明（skip 遗留）

- 位置：`control-plane/tests/integration/test_v5_case_binding_acceptance_postgres.py:962-967`（skip）；CLI `cli/src/caseloop_cli/main.py` from-issue 编排（约 L906-1230）。
- 问题：R4 新增的 from-issue 命令唯一真实 e2e 仍被 skip（WORKSPACE_ACCESS_DENIED），原因已声明、CLI 单元测试与 HTTP journey 覆盖其组合（§3 已确认合理）。但该新特性的端到端正确性（真实 v1 信号路径下的 workspace/project/environment 作用域）尚未被任何真实服务器测试证明。
- 建议：修复 from-issue v1 路径作用域问题后恢复 e2e，或在 R4 后续 slice 明确承担。

### P1-3 — canonical 文档命令跑出 30 个迁移单测失败；提交 "unit 994" 声明不可复现（pre-existing，非 R4 引入）

- 位置：`control-plane/alembic/env.py:20-22`（`db_url = os.environ.get("DATABASE_URL"); if db_url: config.set_main_option("sqlalchemy.url", db_url)`）——base f266980 逐字节相同，R4 未触碰 alembic 与 `tests/unit/test_migrations.py`/`test_stage1_migrations.py`。
- 问题：AGENTS.md 文档命令（`CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=... pytest -q`）会使所有 SQLite 迁移单测被劫持到 PG 而失败（实测 30 failed；单测隔离验证：仅设 DATABASE_URL 即失败，不设则通过）。因此 canonical 全量 = 30 failed, 1007 passed, 1 skipped；且提交声明的 "unit 994" 在任何文档化调用下均不可复现（实测无 env：895+12；含 env：892+30；unit+C4 无 env：910+12）。
- 建议：env.py 的 env 覆盖改为仅当 config 未显式设置 URL 时生效（或迁移单测不依赖 env）；R4 报告数字注明确切调用与失败集。

### P1-4 — 过期 docstring 描述 R4 前行为

- 位置：`control-plane/app/services/acceptance.py:9`（"a case with a confirmed revision for its exact binding is READY, otherwise NEEDS_ACCEPTANCE_CRITERIA"）；`control-plane/app/services/read_views.py:887`（"NEEDS_ACCEPTANCE_CRITERIA / READY"）。
- 问题：R4 将 readiness 改为 PENDING_MATERIALIZATION 后未同步模块 docstring；read_views 处与 P0-2 同源。
- 建议：随 P0-2/P1-1 一并更新。

### Observation（非 P0/P1）

- 验证期间 `cli/uv.lock`（110KB，15:02 创建）作为 untracked 文件出现；base 与 HEAD 均无，非本人或任何 R4 测试产生（`_install_cli` 使用 pip，不产 uv.lock），疑似并发 verifier 的 `uv` 调用产物。请主 agent 确认清理并防止入库。
- conformance 的 15 个失败（test_quality_api.py）为 quality-api 服务未启动（127.0.0.1:8080 refused）的 live 前置失败，AGENTS.md 明确此类连接失败不算 PASS；与 R4 无关，557 passed 与提交声明一致。

## 7. Verdict

**CONDITIONAL FAIL（Gate 未过）** —— 核心 journey（任务 ①-⑤）全部通过且覆盖 §17.5 主路径，但存在 **2 个 P0** 违反 R4 验收契约：

1. **P0-1** 重复/并发确认未 fail-closed（§17.5 "duplicate confirmation / concurrent confirm 全 fail closed" 未满足，且无测试）；
2. **P0-2** live 读模型 `GET /v1/cases/{case_id}/v5-readiness` 在 confirmed 后返回 READY（§17.5 readiness 界限与 R4 提交自身声明被违反，且该路由零测试覆盖）。

P1 计数：**4**（P1-1 manifest/CLI 声明不一致；P1-2 from-issue e2e skip 遗留；P1-3 canonical 命令 30 失败 + "unit 994" 不可复现（pre-existing）；P1-4 过期 docstring）。

P0 清零并补齐对应测试后，本角度可转 PASS；P1 建议在 R4 修复或显式登记后续 slice。
