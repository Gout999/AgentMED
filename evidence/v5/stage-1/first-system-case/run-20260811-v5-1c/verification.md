# V5-1C First System Case — Verification Evidence

> **SUPERSEDED / REJECTED AS CLOSURE EVIDENCE (2026-08-11 review).** This file's
> original Exit claim (`confirm => READY`) and placeholder
> `exact_resolution_contract_binding` are false under the accepted dependency
> order: V5-1C confirmation remains non-executable until V5-4 materializes an
> exact ResolutionContract. The run also predates server-derived trust roles,
> fresh durable reauthentication, project/environment authorization, exact
> event bindings and the 011/012 migrations. Preserve the raw logs as
> historical output only; do not use this bundle to mark V5-1C `DONE`.

- Stage: V5-1C（ApplicationCaseBinding + Manual/GitHub Issue 只读 SourceConnection +
  `caseloop case from-issue` + acceptance propose/get/confirm + CaseReadiness +
  Console Case 页增强）
- Run ID: `run-20260811-v5-1c`
- Commit: `feat(v5): bind quality cases to ai applications`
- Date: 2026-08-11（local）

## Acceptance commands

| Command | Result | Count |
|---|---|---|
| `control-plane pytest tests/unit` | PASS | 763 passed |
| `control-plane pytest tests/integration`（PG 127.0.0.1:5432 control_plane_test, CASELOOP_ALLOW_INTEGRATION_RESET=true） | PASS | 24 passed |
| `cli pytest tests`（via control-plane .venv） | PASS | 98 passed |
| `contracts conformance`（test_schemas.py test_wilson.py test_v4_*.py test_v5_*.py，不含 live-gated test_quality_api.py） | PASS | 517 passed |
| `console npm run build` | PASS | tsc + vite build OK |
| `console npm test` | PASS | 7 passed |
| One-shot `alembic upgrade head` on disposable PG | PASS | head = 010 |
| Old S1A / V5-1A / V5-1B suites | PASS | unit 763（含 test_public_v4_api / test_v5_application_catalog / test_v5_system_versions / test_v5_case_binding_acceptance）+ integration 24（含 stage1a + v5 catalog + v5 versions + v5-1c） |

## blueprint V5-1C Verification（docs/plans/v5-progressive-delivery.md §3）逐条覆盖

| Verification item | Coverage |
|---|---|
| issue prompt injection | `issue_source.normalize_issue_snapshot` 检测 instruction markers / non-text attachment markers，`IssueSourceSnapshot.instruction_markers_detected` 持久化；单测 `test_issue_snapshot_prompt_injection_is_data_only` 断言注入文本仅作为 data 保存、summary 只用标题；E2E 草稿 `expected_behavior.untrusted=True` |
| duplicate webhook / edited / deleted source | `IssueSourceSnapshot` 唯一键 (workspace, case, repo, number) + digest 冲突即 CATALOG_CONFLICT（同 issue 不同内容不可静默覆盖）；edited/deleted flag 持久化并展示；from-issue 确定性 source_event_id + idempotency key 使重试不产生重复 Case（E2E 断言 retry 返回同一 case） |
| malicious attachment | attachment 只以 ArtifactRef（uri/digest/media_type）进入 signal content，原始 payload 存 snapshot（data only），不参与 instruction/acceptance truth |
| Signal/Case immutable digest 不被 application link 改写 | `cases.bind-application` 只新增 immutable binding 记录；单测+集成断言 case snapshot_payload / record_digest / state=OPEN 在 bind 前后逐字节一致 |
| application/version 不可确定时 Case 仍 OPEN | bind 不改 case lifecycle；declared version 可留 UNKNOWN（`declared_system_version_set_binding_or_unknown` null 或 UNKNOWN 标记）；集成断言 case 仍 OPEN |
| 明确 Issue 可形成 executable badcase / 模糊 Issue → NEEDS_ACCEPTANCE_CRITERIA + 下一步，不能启动 Gate | CaseReadiness 投影：无 confirmed revision → NEEDS_ACCEPTANCE_CRITERIA + next_action=CONFIRM_ACCEPTANCE_CRITERIA；合同层 `needs_acceptance_criteria_blocks_gate_pass` 已由 conformance/test_v5_first_slice.py 冻结；runtime Gate 尚未存在（如实声明） |
| Agent proposal 不能自我确认 / confirmed revision 不能原位改写 | confirm 仅 human + `acceptance_criteria:confirm` scope + reauthentication（凭证 issued_at ≥ proposal.proposed_at）；proposer≠confirmer 强制；单测覆盖非 human 拒绝、proposer 自确认拒绝、未 reauth 拒绝、DB CHECK 防止原位改写（before_update guard + 状态形状约束） |
| no remote comment/claim/fork/push/PR | Issue 只读：CLI 用只读 GET（或本地 snapshot 文件），无任何写远程动作；仓库无 remote 写代码路径 |

## Exit / rollback

- Exit：simonw/llm issue #1466（schema_dsl IndexError，可执行 badcase）经
  `caseloop case from-issue` 形成 First System Case，绑定到 llm-cli 应用，
  产生 PROPOSED 草稿；reauthenticated human 经 CLI confirm 后 readiness=READY。
- 人类时间/阻塞原因：从 source 到确认无阻塞；issue 文本仅作为 data。
- Evidence：`evidence/v5/stage-1/first-system-case/run-20260811-v5-1c/`
- Commit：`feat(v5): bind quality cases to ai applications`（禁止 push）。

## Facets

| Facet | 状态 |
|---|---|
| contract | PASS（conformance 517；契约未改动） |
| replay | PASS（CLI 单测 98 含 from-issue 编排；wire samples 全链路） |
| domain-provider-live | NOT_RUN（未接真实 provider；GitHub 仅只读 GET 一次用于取证快照） |
| agentteams-native | NOT_RUN（无 AgentTeams） |
| claude-runtime-live | NOT_RUN |
| agent-causal | NOT_RUN |
| repo-sandbox | NOT_RUN（本 stage 无 sandbox 观察） |
| human-authorized-external | NOT_RUN |
| production-canary | NOT_RUN |

## 诚实的不确定性

- **ResolutionContract 运行时未物化**：`exact_resolution_contract_binding` 以
  `materialization: DECLARED_BY_CASE` 记录（V5-4 前无 ResolutionContract 表），
  wire 形状完整但契约运行时延后。
- **maintainer/domain_reviewer 解释**：当前运行时无服务端 trust-role registry，
  confirm 的「human maintainer/domain reviewer」由 `acceptance_criteria:confirm`
  scope（仅注册时授予）承载；reauthentication 解释为「确认凭证 issued_at 不早于
  提案时间」。均在契约允许的 DRAFT 解释内，已在代码注释与本文档声明。
- **Case 状态机**：S1A QualityCase 仍为 v4 生命周期；V5 binding/readiness 是
  加性投影，不新建 case lifecycle。
- **Issue fetch**：CLI 默认只读 GET GitHub API 并本地缓存；无凭据/断网时可用
  `--snapshot-file`（E2E 即用本地 fixture）。
