# V5-R4-full Gate 独立 Verifier 报告（ANGLE=contracts）

## Verifier 身份

- 角色：V5-R4-full gate 独立 verifier，ANGLE=`contracts`（R4 契约层核对）。
- 只读约束：除本报告文件外未写任何文件（详见"恢复操作"一节）。
- 时间：2026-08-12（session 日期）。
- 环境：
  - worktree：`/private/tmp/caseloop-r4-verify.37W4g6/checkout`（detached）
  - HEAD：`df86662e762e431ffdc3442cb4e79b9ba0001615`（`feat(v5): first system case binding and acceptance criteria`）
  - base：`f266980b0eb1afbe545b4407ae7fe88038dd2d41`
  - venv：`/Users/xiejiachen/caseloop/control-plane/.venv/bin/python`
  - conformance venv：`/Users/xiejiachen/caseloop/eval-harness/.venv/bin/python`
  - PG：`DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test`、`CASELOOOP_ALLOW_INTEGRATION_RESET=true`

## Subject

提交 `df86662`（R4 语义提交，base `f266980`）：

- 5 个 intent 激活为 `wire_status: FROZEN_R4`；
- case binding / acceptance criteria runtime；
- readiness 界限（`PENDING_MATERIALIZATION`，绝不 `READY`）；
- CLI case 命令族；
- 编译产物/契约/测试同步更新（41 文件，+5960/-271）。

## Verdict

**PASS（无 P0）**。

R4 契约层六项核对全部通过，运行时语义（bind/propose/confirm/readiness）经代码走查与 PG journey 测试确认与 Master §17.5 一致。发现 5 项 P1（无破坏验收/正确性/安全的 P0）：

- P1 计数：**5**
- P0 计数：**0**

## 重跑命令实际结果

| # | 检查项 | 命令 | 实际结果 |
|---|--------|------|----------|
| 前置 | detached 干净 | `git status` | `nothing to commit, working tree clean`（detached） |
| 前置 | HEAD/base | `git rev-parse HEAD` | `df86662e762e431ffdc3442cb4e79b9ba0001615`；`f266980` 可解析 |
| ① | intent-registry 状态 | `grep wire_status: FROZEN_R4` | 恰好 5 个：cases.bind-application、case-application-bindings.get、acceptance-criteria.propose/confirm/get，全部 `implementation_status: IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER`；全文件 `wire_status: READY` = 0（PENDING_MATERIALIZATION 界限成立） |
| ② | r2 allowlist | 读 `v5/intent-registry.yaml#r2_application_catalog_contract.activated_contract_intents` | 仍 11 项（capabilities.get 起至 system-manifests.import），未膨胀 |
| ③ | 编译器确定性与激活数 | `cd contracts/compiler && PYTHONPATH=.. python3 -m compiler emit`；`git -C .. diff --exit-code -- v5/generated/` | emit 成功；`ZERO_DIFF_OK`；operation-manifest 19 个 operation；capability-manifest `enabled_intent_count=19`、`disabled_intents=[]` |
| ④ | conformance 全绿 | `cd contracts && /Users/xiejiachen/caseloop/eval-harness/.venv/bin/python -m pytest conformance/test_schemas.py conformance/test_wilson.py conformance/test_v4_*.py conformance/test_v5_*.py -q` | **557 passed in 13.67s** |
| ⑤ | openapi 5 新路径 | 抽查 `v5/generated/openapi.yaml` | 5 个路径的 operationId/x-caseloop-intent/scope/wire-status/参数与 intent-registry 逐项一致（详见下节） |
| ⑥ | 5 新 schema | meta-valid + $ref 解析 + 与 `v5_models.py` 一致性 | 全部 meta-valid（含在 557 全绿内）；所有 `$ref` 目标存在；`exactCaseBinding`/可到达的 `exactResolutionContractBinding` 形状与运行时一致（详见下节） |
| 附加 | PG journey | `cd control-plane && DATABASE_URL=... CASELOOP_ALLOW_INTEGRATION_RESET=true .venv/bin/python -m pytest tests/integration/test_v5_case_binding_r4_postgres.py -q` | **1 passed in 0.81s**（覆盖 import→signal→bind→binding-get→propose→get(NEEDS_ACCEPTANCE_CRITERIA)→confirm(重认证)→get(PENDING_MATERIALIZATION)→403 负路径） |
| 附加 | R4 单测 | `tests/unit/test_v5_case_binding_acceptance.py`；`tests/test_v5_c4_allowlist_diff.py tests/test_v5_c4_fallback_drill.py tests/unit/test_v5_capabilities.py`；`tests/unit/test_public_v5_api.py` | 25 / 24 / 43 passed（全绿） |
| 附加 | CLI | `cd cli && /Users/xiejiachen/caseloop/control-plane/.venv/bin/python -m pytest -q` | **130 passed in 1.78s** |
| 附加 | compiler | `cd contracts/compiler && PYTHONPATH=.. .venv/bin/python -m pytest -q` | **18 passed in 1.47s** |

> 说明：CLI 测试在 `uv run`（临时 uv 环境，Python 3.14）下收集失败——`ModuleNotFoundError: No module named 'rfc8785'`（uv 未装项目依赖）。这是临时环境缺依赖，非代码缺陷；用项目 venv 重跑 130 全绿。恢复操作：本次 uv 运行意外生成了 `cli/uv.lock`，已按只读铁律 `rm -f cli/uv.lock` 恢复，`git status` 现仅剩 evidence 目录。

## 六项核对详情

### ① intent-registry.yaml（5 个 FROZEN_R4）

`contracts/v5/intent-registry.yaml` 顶层 32 个 intent 的状态分布：

```
FROZEN_R2      -> IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER (10)
FROZEN_R3      -> IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER (3)
FROZEN_R2_R3_BOOTSTRAP -> IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER (1)
FROZEN_R4      -> IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER (5)
DRAFT          -> NOT_IMPLEMENTED (13)
```

5 个 R4 intent 的 wire 元数据（method/path/operation_id/scope/query 参数）与提交声明一致。全文件 `wire_status: READY` 计数为 0。

### ② r2 allowlist

`r2_application_catalog_contract.activated_contract_intents` 仍为 11 项（capabilities.get、applications.register/get/list、environments.register/get、system-components.register/get、dependency-edges.record/get、system-manifests.import）。R4 未动 R2 冻结面。

### ③ 编译器确定性（19 激活）

- `compiler emit` 后 `v5/generated/` 零 diff（含 ts 产物）。
- `contracts/compiler/activated_operations.py` 将 `FROZEN_R4` 加入 `ACTIVATED_WIRE_STATUSES`；`emitters.py` 的 `PATH_PARAMETER_ID_DEFS` 新增 `case_id`/`acceptance_criteria_revision_id`。
- operation-manifest 19 个 operation，capability-manifest `enabled_intent_count=19`、`disabled_intents=[]`，5 个 R4 intent 均带 `FROZEN_R4`。
- `schema-profiles.yaml` 的 `contract_allowlist_exact_count` 14→19、名单补齐 5 个新 intent，与 C4 测试（`test_v5_c4_allowlist_diff.py`）同步。

### ④ conformance 557 全绿

即上表。`test_v5_c1_schemas.py` 断言 21 个 schema 文件（16+5）、`activated_intent_count == 19`；`test_v5_compatibility.py` 把 `FROZEN_R4` 纳入实现状态判定；`test_v5_d2_version_graph_contract.py` 增加 R4 五 intent 断言，均通过。

### ⑤ openapi.yaml 5 新路径

| 路径 | operationId | scope | delivery_slice | 参数 |
|------|-------------|-------|----------------|------|
| POST `/api/v2/cases/{case_id}:bind-application` | bindCaseApplication | cases:bind | V5-1C | path case_id（`^case_[0-9A-Za-z]{8,64}$`） |
| GET `/api/v2/cases/{case_id}/application-binding` | getCaseApplicationBinding | cases:read | V5-1C | path case_id；query case_revision(int≥1)、case_digest(required) |
| POST `/api/v2/cases/{case_id}:propose-acceptance-criteria` | proposeAcceptanceCriteria | acceptance_criteria:propose | V5-0C | path case_id |
| POST `/api/v2/acceptance-criteria/{acceptance_criteria_revision_id}:confirm` | confirmAcceptanceCriteria | acceptance_criteria:confirm | V5-0C | path acr_（`^acr_[0-9A-Za-z]{8,64}$`） |
| GET `/api/v2/cases/{case_id}/acceptance-criteria` | getAcceptanceCriteria | acceptance_criteria:read | V5-0C | path case_id；query case_revision(required) |

operationId/scope/参数均与 intent-registry 逐项一致。`$ref` 指向 `../schemas/*.schema.json`，未内联。

### ⑥ 5 个新 schema（meta-valid + $ref 可解析 + 与 v5_models 一致）

- 5 文件均为 draft 2020-12、meta-valid（含在 557 全绿中）。
- 所有 `$ref` 目标存在：`common.schema.json#/$defs/{schemaVersion2,idCaseId,idCaseBindingId,idAcceptanceCriteriaRevisionId,idApplicationId,idEnvironmentId,idWorkspaceId,idPrincipalId,idRequestId,auditRef,digest,recordEnvelope,errorEnvelope,idempotencyDelivery,systemVersionSetBindingOrUnknown}`、`system-manifests.import.schema.json#/$defs/exactSystemVersionSetBinding` 均存在。
- 形状核对：
  - `exactCaseBinding`（schema：`{case_id, case_revision, case_digest}` 必填、additionalProperties:false）与运行时写入值一致（`case_binding.py`、`acceptance.py` 均写 `{"case_id":…,"case_revision":…,"case_digest":…}`）。
  - **可到达**的 `exactResolutionContractBinding`（propose/confirm/get 经 `acceptanceCriteriaRevisionRecord` 引用）形状 `{kind:const RESOLUTION_CONTRACT, revision:int|null, digest:digest|null, materialization:const DECLARED_BY_CASE, case_binding:{case_id,case_revision,case_digest}}` 与运行时 `_RESOLUTION_CONTRACT_BINDING + case_binding`（`acceptance.py:91` 附近、`_write_revision`）一致。
  - `systemVersionSetBindingOrUnknown`（`anyOf [exactSystemVersionSetBinding, const UNKNOWN]`）与 common.schema.json#634 一致；运行时类型 `dict[str,Any] | Literal["UNKNOWN"] | None` 与之相容（见 P1-3 的 None 宽松点）。
- `v5_models.py` 的 `CaseReadiness` 已含 `PENDING_MATERIALIZATION`；`V5IdempotencyReceipt` 新增 propose/confirm 两个 intent 的资源校验分支。

## P0 / P1 清单

### P0（0）

无。

### P1（5）

1. **P1 · `app/services/read_views.py:954-957`（`case_v5_readiness`）**：内部 v1 投影 `/api/v1/cases/{case_id}/v5-readiness` 在存在 CONFIRMED revision 时仍返回 `case_readiness: "READY"`。R4 只修了 v2 契约路径（`acceptance.py:631` 改为 `PENDING_MATERIALIZATION`），未同步此内部投影，与 Master §17.5"confirmation alone never reaches READY（需 V5-4A ResolutionContract）"相矛盾。该端点仅供 Console 内部投影（非 v2 契约），故为 P1；若 Console 依此展示"READY"将误导用户。
2. **P1 · `contracts/v5/schemas/cases.bind-application.schema.json:40-66` 与 `case-application-bindings.get.schema.json:40-66`（`exactResolutionContractBinding` 定义）**：这两个文件内的 `exactResolutionContractBinding` 是旧形状 `{kind, id(必填), revision, digest}`（id 必填、revision/digest 非空），与 acceptance-criteria.{propose,confirm,get} 及运行时实际形状 `{kind, revision|null, digest|null, materialization, case_binding}` 不一致。经引用可达性分析（request/response/error 树遍历），该定义在这两个文件内**不可达**（`acceptanceCriteriaRevisionRecord` 为死定义），因此无线上影响，但同名同义定义在冻结契约集中出现两种形状，属维护陷阱（后续按名引用会踩错）。
3. **P1 · `contracts/v5/schemas/cases.bind-application.schema.json`（request）与 `control-plane/app/public_api/v5_models.py:1051-1053`（`SystemVersionSetBindingOrUnknown`）**：schema 要求 `declared_system_version_set_binding_or_unknown` 必填且非 null（`anyOf [binding, UNKNOWN]`）；运行时模型 `= None` 默认允许缺省/传 null。即"缺省该字段"的请求运行时放行、schema 校验拒绝——契约/运行时宽松度不一致。属于标注的 draft 解释（field_contract_ref=null），但建议明确。
4. **P1 · `contracts/v5/intent-registry.yaml:622-624/643-645 vs 667-708（delivery_slice 标注）**：5 个 R4 intent 在 `compatibility.yaml` 的 V5-0C first-slice（count 10）中一并冻结，但 registry 中 bind/binding.get 标 `V5-1C`、acceptance-criteria.* 标 `V5-0C`；提交信息与 gate brief 又称"五个 V5-1C intent"。标注口径不一致（同一冻结集两种 slice 名），纯元数据，无运行时影响。
5. **P1 · `control-plane/app/services/acceptance.py:confirm()`（403-520）与 `app/models/v5_tables.py:895-928`**：同一 PROPOSED revision 可被不同幂等键的 confirm 再次确认，生成第二条 CONFIRMED revision（PROPOSED 行保持 PROPOSED、确认产生新行，无唯一约束阻止同一 proposal 链到多条 CONFIRMED）。幂等仅防同 key 重放；语义上"additive/immutable"可辩护，但 §17.5 未见明确授权"一 proposal 多 confirm"，且无测试覆盖该路径（现有单测覆盖 non-human/proposer-self/no-reauth/immutable/rewrite，无 double-confirm）。建议：DB 唯一约束或显式契约说明。

## 问题详情（逐条定位）

### P1-1 read_views 内部投影仍 READY

- 位置：`control-plane/app/services/read_views.py:954-957`
- 代码：`confirmed = [row for row in revisions if row.confirmation_status == "CONFIRMED"]` / `if confirmed: readiness: str = "READY" else: readiness = "NEEDS_ACCEPTANCE_CRITERIA"`
- 端点：`app/api/read_views.py:80-97` `GET /api/v1/cases/{case_id}/v5-readiness`（内部投影，注释"V5-1C case governance read model for the Console"）
- 证据：R4 提交 diff 中 `acceptance.py` 将 v2 路径从 `READY` 修为 `PENDING_MATERIALIZATION`，`read_views.py` 未在本提交改动（`git show --stat` 无此文件）。base `f266980` 的 v2 路径原本同样返回 READY，R4 已修 v2、漏修内部投影。
- 建议：`read_views.case_v5_readiness` 改为与 `acceptance.py` 相同判定（`PENDING_MATERIALIZATION` if confirmed else `NEEDS_ACCEPTANCE_CRITERIA`），或删除该字段。

### P1-2 死定义携带冲突形状

- 位置：`contracts/v5/schemas/cases.bind-application.schema.json:40-66`、`case-application-bindings.get.schema.json:40-66`（`exactResolutionContractBinding`）；两文件的 `acceptanceCriteriaRevisionRecord`（168-299 行）均为不可达死定义。
- 证据：可达性分析——`cases.bind-application` 可达 def 仅 `{applicationCaseBindingRecord, issueSnapshot}`；`case-application-bindings.get` 仅 `{applicationCaseBindingRecord}`。死定义 `acceptanceCriteriaRevisionRecord` 内引用的 `exactResolutionContractBinding` 形状与 acceptance-criteria 三个 schema/运行时不一致（`required: [kind,id,revision,digest]` vs `[kind,revision,digest,materialization,case_binding]`）。
- 建议：从 bind/binding.get 两个 schema 删除未使用的 acceptance-criteria 词表（`acceptanceCriteriaRevisionRecord`、`idAcceptanceCriteriaRevisionId`、`recordEnvelopeRef` 等），只保留各自响应所需 def，消除同名异形。

### P1-3 SystemVersionSetBindingOrUnknown 可缺省

- 位置：`contracts/v5/schemas/cases.bind-application.schema.json:324-326/338-345`（request 必填且 anyOf 无 null 成员）vs `control-plane/app/public_api/v5_models.py:1051-1053`（`= None` 默认）与 `CaseBindApplicationRequest`（v5_models.py:1075，默认 None）。
- 影响：缺少该字段的 bind 请求运行时接受（写 None），但 schema 校验拒绝——strict 消费者会与运行时不一致。
- 建议：二选一——schema 允许 null，或运行时必填。

### P1-4 delivery_slice 标注口径

- 位置：`contracts/v5/intent-registry.yaml:622-624`（cases.bind-application V5-1C）、643-645（case-application-bindings.get V5-1C）、667/685/707（acceptance-criteria.* V5-0C）；`contracts/v5/compatibility.yaml:282-299`（V5-0C first_slice count 10 含全部 5 个）。
- 提交信息称"five V5-1C intents"，registry 实际 3 个为 V5-0C。两种 slice 名均可辩护（冻结源 slice vs 交付 slice），但同一集合内口径不统一，且与提交信息矛盾。

### P1-5 double-confirm 语义缺口

- 位置：`control-plane/app/services/acceptance.py:403-520`（confirm：`_load_proposed_revision` 只要求目标为 PROPOSED；确认写新行不改原行）、`app/models/v5_tables.py:905-928`（无约束阻止同 proposal 多 CONFIRMED）。
- 现有测试（`test_v5_case_binding_acceptance.py:1134-1331`）覆盖 non-human、self-confirm、no-reauth、新不可变行、原位不可改写，未覆盖同 proposal 二次 confirm（异 key）。
- 影响：两次异 key confirm 生成两条 CONFIRMED revision；readiness 投影只看"存在 CONFIRMED"，功能不受破坏，但 revision 集膨胀、链语义变含糊。

## 恢复操作记录

- `uv run pytest`（尝试跑 CLI 测试）在临时 uv 环境下生成 `cli/uv.lock`；按只读铁律已 `rm -f` 删除，`git status` 恢复为仅 evidence 目录 untracked。

## 结论

R4-full 契约层核验 PASS：

- 5 个 FROZEN_R4 intent、0 READY（readiness 界限成立）、r2 allowlist 11、19 激活、compiler 零 diff、conformance 557 全绿、5 新路径/5 新 schema 与 registry 及运行时一致。
- 运行时（bind/propose/confirm/readiness/重认证/幂等/失败闭合）经代码走查 + PG journey + 单测确认符合 §17.5。
- P0=0、P1=5，全部为一致性/卫生/口径类问题，不阻塞 R4 验收，建议在后续 wave（C0-C5 收敛）中清理。
