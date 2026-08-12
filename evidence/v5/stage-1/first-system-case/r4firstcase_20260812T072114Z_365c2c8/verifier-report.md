# Verifier Report — R4-full gate remediation (7c17391)

- **Verifier**: independent verifier（R4-full gate 复核，对 remediation 提交的复查）
- **Subject**: `7c17391` `test(control-plane): resolve R4 verifier P0/P1 findings`
- **Parent**: `df86662`（被 remediation 的提交；三个 verifier 对其给出 2 P0 + 多 P1）
- **Base**: `df86662`（diff `df86662..7c17391`；其上 `f266980`）
- **Verdict**: **CONDITIONAL PASS** — 2 个 P0 的运行时语义均已正确修复、全量套件绿；但存在 **1 个新 P1（P0-2 钉死测试断言无效）** 与 **2 项 P1 部分未关闭（P1① 残留死定义、P1④ read_views docstring 未改）**，建议下个 wave 收敛后转 PASS
- **P0**: 0（运行时）；**P1**: 新增 1（NEW-1 无效断言）+ 部分未关闭 2（P1①、P1④）+ 观察 5

---

## 1. 验证环境

- worktree `/private/tmp/caseloop-r4-verify.37W4g6/checkout`，HEAD = `7c17391ddcf2cdd6ef1834b8ca2a0ea2870c3600`（detached，干净，唯一 untracked 为本 evidence 目录）
- venv `/Users/xiejiachen/caseloop/control-plane/.venv/bin/python`；conformance 用 `/Users/xiejiachen/caseloop/eval-harness/.venv/bin/python`
- PG：`DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test`，`CASELOOP_ALLOW_INTEGRATION_RESET=true`（仅集成/专项用）

## 2. diff 范围核对（git diff df86662 7c17391 --stat）

```
 contracts/v5/generated/operation-manifest.json     |    2 +-
 contracts/v5/intent-registry.yaml                  |    2 +-
 .../case-application-bindings.get.schema.json      |  133 ----
 .../v5/schemas/cases.bind-application.schema.json  |  133 ----
 control-plane/app/services/acceptance.py           |   21 +-
 control-plane/app/services/read_views.py           |    4 +-
 .../tests/unit/test_v5_case_binding_acceptance.py  |  100 ++
 7 files changed, 125 insertions(+), 270 deletions(-)
```

**通过**：仅 7 个文件，全部属于 P0/P1 修复范围，无无关改动。

## 3. P0 关闭确认

### P0-1 重复/并发确认 fail-closed —— **关闭（含并发窗口观察）**
- 代码 `control-plane/app/services/acceptance.py:460-478`：`confirm()` 在 `_load_proposed_revision()` 之后、REAUTHENTICATION 检查之前，遍历本 workspace 内所有 `CONFIRMED` revision，若某条已确认记录的 `exact_previous_proposed_revision_binding.id == 本 proposal 的 acceptance_criteria_revision_id`，则抛 `AcceptanceError("VALIDATION_FAILED", {"reason": "DUPLICATE_CONFIRMATION"})`。
- 数据依据：`_write_revision` 写入的 CONFIRMED 记录带 `exact_previous_proposed_revision_binding={"id": proposed.acceptance_criteria_revision_id, ...}`（acceptance.py:528-533），模型列存在（`app/models/v5_tables.py:963` `exact_previous_proposed_revision_binding: Mapped[Optional[dict]]`），匹配逻辑成立。
- 测试有效性 ✓：`test_duplicate_confirmation_fails_closed` 用**新幂等键**二次 confirm 同一 proposal（幂等不重放）→ 必须走到 DUPLICATE 分支才返回 `VALIDATION_FAILED`/`DUPLICATE_CONFIRMATION`；断言 `details == {"reason": "DUPLICATE_CONFIRMATION"}` 非恒真。专项重跑 2 passed。
- **观察 OBS-1（非阻塞）**：仅服务层检查、无 DB 唯一约束，多 worker 并发下同 proposal 的两个 confirm 仍存在 TOCTOU 双写窗口（原 verifier-runtime P0-1 建议「DB 唯一约束或显式契约说明」，二者均未落实）。单 worker/顺序场景已 fail-closed。

### P0-2 v1 readiness 上限 —— **运行时关闭，但钉死测试无效（新 P1）**
- 代码 `control-plane/app/services/read_views.py:951-957`：`if confirmed: readiness = "PENDING_MATERIALIZATION"`，不再输出 `READY`；与 v2 路径（acceptance.py:650）一致，`CaseReadiness` Literal 含该值（v5_models.py:1047）。运行时修复正确。
- **NEW-1（新 P1）**：钉死测试 `test_read_views_readiness_never_reports_ready_after_confirm`（test_v5_case_binding_acceptance.py:1502-1527）断言为 `assert readiness != "READY"`，而 `readiness = case_v5_readiness(...)` 返回的是 **dict**（read_views.py:1008-1019，`case_readiness` 只是其中一个键）。Python 中 `dict != str` **恒为 True** —— 断言无条件通过，无论函数返回 `"READY"` 与否，**P0-2 的回归防护实际未钉住任何行为**（该路由此前零测试覆盖，v1 投影仍是唯一无有效防护的表面）。建议改为 `assert readiness["case_readiness"] == "PENDING_MATERIALIZATION"`。

## 4. P1 关闭确认

| P1 | 状态 | 依据 |
|---|---|---|
| ① bind/binding.get schema 移除死定义 `acceptanceCriteriaRevisionRecord` | **部分关闭** | 两个 schema 各删 133 行，`acceptanceCriteriaRevisionRecord` 已删除 ✓；但实测仍残留不可达死定义：`exactResolutionContractBinding`（**仍是旧形状**：`required [kind,id,revision,digest]`、无 `materialization/case_binding`，与 acceptance-criteria schema 同名异形）、`idAcceptanceCriteriaRevisionId`、`recordEnvelopeRef`（get 另残留 `issueSnapshot`/`query`）——原 P1-2 指出的「同名异形维护陷阱」未根除（可达性分析见下） |
| ② bind request `declared_system_version_set_binding_or_unknown` optional | **目标状态成立（无 diff）** | 实测 **df86662 中该字段本就不在 required**（`$defs.request.required` 仅 6 项：schema_version/case_id/case_revision/case_digest/application_id/environment_id），7c17391 亦不在；diff 无任何 required 修改。提交信息声称「is now optional」与实际 diff 不符（OBS-2）。契约/运行时「缺省」语义一致 ✓；但「非 null」仍不一致（schema `anyOf [binding, UNKNOWN]` 无 null，运行时 `= None` 允许传 null）——原 verifier 自标非阻塞，残留为 OBS-3 |
| ③ intent-registry cli → `case acceptance-criteria confirm` | **关闭（含观察）** | intent-registry.yaml 1 行替换（diff 的 2 +- 即 1 行 replace）；operation-manifest.json 重生成仅该字段 1 行（编译器重跑零 diff 证实）；CLI 实际命令面一致（main.py:327 `("case","acceptance-criteria")`、1441/1492 分发）。**OBS-4**：CLI `_generated/operation_manifest.py`（manifest loader）内嵌 frozen fallback 表仍为旧 `"cli": "acceptance-criteria confirm"`，靠 497-499 行归一化补偿（standalone → case 嵌套）保持等价，功能无碍但表未同步 |
| ④ stale READY docstring 修正 | **部分关闭** | `acceptance.py:9` 已改为 "PENDING_MATERIALIZATION after confirmation" ✓；`read_views.py:886-887` docstring 仍写 "(NEEDS_ACCEPTANCE_CRITERIA / READY)"（未列入 PENDING_MATERIALIZATION），**未修正** ✗ |

可达性分析（request/response/error 根，递归 $ref）：

```
cases.bind-application:            reachable = {applicationCaseBindingRecord, error, exactCaseBinding, idCaseBindingId, idCaseId, issueSnapshot, request, response, systemVersionSetBindingOrUnknown}; DEAD = {exactResolutionContractBinding, idAcceptanceCriteriaRevisionId, recordEnvelopeRef}
case-application-bindings.get:     reachable = {applicationCaseBindingRecord, error, exactCaseBinding, idCaseBindingId, idCaseId, request, response, systemVersionSetBindingOrUnknown}; DEAD = {exactResolutionContractBinding, idAcceptanceCriteriaRevisionId, issueSnapshot, query, recordEnvelopeRef}
```

## 5. 重跑结果（实际命令 + 输出）

| 命令 | 结果 |
|---|---|
| `cd contracts/compiler && PYTHONPATH=.. python3 -m compiler emit && git -C .. diff --exit-code -- v5/generated/` | **零 diff**（emit 重生成 operation-manifest/capability-manifest/openapi/ts，与提交一致）；`cd contracts && eval-harness venv pytest compiler -q` → **18 passed** |
| `cd contracts && … pytest conformance/test_schemas.py conformance/test_wilson.py conformance/test_v4_*.py conformance/test_v5_*.py -q` | **557 passed in 16.54s** |
| `cd control-plane && env -u CASELOOP_ALLOW_INTEGRATION_RESET -u DATABASE_URL $VENV pytest tests/unit tests/test_v5_c4_allowlist_diff.py tests/test_v5_c4_fallback_drill.py -q` | **912 passed, 12 skipped**（12 个 skip 均为显式 PG reset opt-in 才跑的迁移测试，符合预期；无失败） |
| `cd cli && $VENV python -m pytest tests -q` | **130 passed in 1.47s** |
| `cd control-plane && env CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=… $VENV pytest tests/integration/test_v5_case_binding_r4_postgres.py -q` | **1 passed**（该文件仅 1 个大 journey 测试） |
| 专项 `pytest tests/unit/test_v5_case_binding_acceptance.py::test_duplicate_confirmation_fails_closed …::test_read_views_readiness_never_reports_ready_after_confirm -q` | **2 passed in 0.11s** |

工作树在重跑后保持干净（`git status` 仅 untracked evidence 目录）。

## 6. 新发现汇总

- **NEW-1（P1）**：P0-2 钉死测试断言恒真（dict ≠ str），回归防护失效 —— remediation 声称 "pinned by a new read_views unit test" 与实际不符。
- **OBS-1**：P0-1 并发 TOCTOU 窗口（无 DB 唯一约束）。
- **OBS-2**：P1② 提交信息与 diff 不符（required 本就不含该字段，无修改）。
- **OBS-3**：P1② null 语义不一致（schema 拒绝 null，运行时接受）仍存（原非阻塞标注）。
- **OBS-4**：P1③ CLI frozen fallback 表 cli 字符串未同步（归一化补偿等价）。

## 7. 结论

P0 运行时语义全部修复且经全量套件验证；无新 P0。**CONDITIONAL PASS**：需关闭项为 NEW-1（P0-2 断言改 `readiness["case_readiness"] == "PENDING_MATERIALIZATION"`，1 行）、P1④（read_views.py:887 docstring，1 行）；建议项为 P1① 残留死定义清理、OBS 系列。以上关闭后即可转 PASS。
