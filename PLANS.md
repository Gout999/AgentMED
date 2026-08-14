# CaseLoop Delivery Plan

Status values: `TODO`, `IN_PROGRESS`, `VERIFYING`, `BLOCKED`, `DONE`. `VERIFYING`
means a semantic subject exists but its post-commit evidence/verifier closure is still pending.

## Current milestone

CaseLoop 的 V5 产品边界已经由 D-013 接受：产品升级为**面向 AI 应用的 Agent-native
治理运营控制面**，顶层对象是 `AIApplication`，Agent 是 `SystemComponent`。V5 的 PRD、
架构、progressive blueprint 与 `contracts/v5/` 已经形成施工基线。两份独立产品/
评测 review 的共同修改已经得到用户确认：First Useful Case 先形成可信验收标准，区分
代码与 AI 行为 workload，CLI 降低接入成本，Gate 要经真实反例/正例验证，只有可部署
target 才进入 release/observed/rollback。

**V5-0 合同基线已冻结并解冻 runtime 施工**：V5-0B（`8dd25ca`，system governance
ownership）与 V5-0C（`b3727d7`，first system wire slice）均于 2026-08-11 通过独立
验收（0B 含验收方补 13 个对抗机检；0C FAIL 清单为空）。离线最终计数：v3+v4+v5 合并
517 passed；v3/v4 独立 449 passed；V5 focused 68 passed。此后 1A/1B/1C 已产生 runtime
提交，但 2026-08-11 的全项目复核否决了原 closure：机器合同、exact binding、服务端
trust role、fresh reauthentication、CaseReadiness、Console 入口、Compose 和证据台账存在
断链。当前 repair worktree 已通过独立 verifier，但在新的 completion commit、
digest-bearing evidence manifest 与逐 stage post-commit verifier 形成前，1A/1B/1C 均保持
`IN_PROGRESS`，不得从旧 evidence 推导 `DONE`。
live/provider/Agent/repository/human/production facets 仍为 `NOT_RUN`。

最后有完成提交与独立证据的通用 runtime 仍是 V4 Stage 1A：**Stage 0 和 Stage 1 Entry
为 `DONE (contract-only)`；Stage 1A — authenticated maintainer report without trace —
为 `DONE (local runtime)`**，commit `22c23f8`。V5-1A/B/C 的本地代码与聚焦测试存在，
但仍是未提交的 closure repair，不证明 provider、Agent、repository、human-authorized
external 或 production 能力。

- Product and scope baseline: `docs/product-principles.md`.
- V5 stage construction baseline: `docs/plan-v5.md` + `docs/plans/v5-progressive-delivery.md` + 已冻结的 `contracts/v5/`（含 V5-0B/0C 两个 freeze commit）。`docs/plan-v4.md` 保留为 V4 兼容性基线；v3 保留为已实现兼容基线。
- Current V5 execution orchestration: `docs/plans/v5-master-execution-plan.md`; document authority and archive policy: `docs/README.md` + `docs/archive/README.md`。
- Current implemented compatibility baseline: `docs/plan-v3.md`, `docs/spec.md`, existing `contracts/`, migrations, and executable tests. The v3 customer-service workload and its evidence remain valid historical assets until explicitly migrated; v4 approval does not make proposed capabilities implemented.
- D-013 保留 V4 Durable Work、Candidate/Gate/WorkOrder、Approval、ExternalOperation、
  audit 与 recovery 铁律，但把 Langfuse、AgentTeams、Claude Code 和 Coding Team 改为
  可插拔 Adapter；真实外部 Agent 经 CLI 调用即可满足比赛首条 Agent-native 证明。
- Local branch, document, contract, code, and test construction is authorized. Push, PR, paid provider calls, human approvals, and production or other external writes still require their own authorization.
- V5 stage 施工已解冻：V5-1 及之后 stage 在本地分支按 blueprint 依赖图逐 stage 施工，每 stage 需 focused tests + verifier + evidence + semantic commit。V4 S1B–S7 仍冻结。
- Security prerequisite for the next live/provider run: rotate the potentially exposed StepFun, Feishu, and internal authority/read/write/role credentials after a resolved Compose configuration expanded values into a private tool log. No secret value may enter Git or evidence. Rotation is not part of Stage 0 and remains unperformed; live execution is blocked until it is complete and a redacted preflight passes.

## V5 design preparation

| Priority | Work item | Status | Dependencies | Acceptance criteria | Required tests | Evidence | Completion commit |
|---|---|---|---|---|---|---|---|
| DOC-R0 | Documentation authority recovery and Master Execution Plan | DONE | Current review findings; preserved dirty WIP inventory | one current handoff; stale status/preconstruction snapshots archived with provenance; active docs agree on V5 accepted baseline vs partial runtime; Master Plan has work packages, stop gates, evidence/commit protocol and independent verifier | detached subject clean before/after；30/30 paths；138 tracked Markdown / 186 local links / 0 broken；status/archive/secret/PII 0；independent PASS P0=0/P1=0 | [`r0docs_20260811T104032Z_4d15c1c`](evidence/v5/stage-0/documentation-authority/r0docs_20260811T104032Z_4d15c1c/)；all runtime facets `NOT_RUN` | `4d15c1c81180386fa4852a53f8b8847e74cda050` |
| V5-0A | Product boundary and supersession ADR | DONE | User-confirmed AI-application governance and Agent-native direction | D-013 and its product-principles/plan referrers are tracked and reconstructable from the verified clean subject | included in DOC-R0 detached clean-checkout verification | same R0 evidence；no runtime facet | `4d15c1c81180386fa4852a53f8b8847e74cda050` |
| V5-0B | Domain, owner and lifecycle draft contracts | DONE | V5-0A | Common authority envelope + V5 exact binding; schema-major-2 Candidate/Eval/Gate/WorkOrder/Approval/Operation keep V4 logical owners; additive acceptance/BadcaseSpec owned by ResolutionContract; workload/applicability; verification-only PASS cannot create WorkOrder; release remains ExternalOperation-rooted; Desired/Observed/Effect stay separate | Focused V5 suite 68 passed (incl. 13 adversarial checks added by the verifier); v3+v4 independent 449 passed; v3+v4+v5 combined 517 passed | contract-only freeze; runtime facets `NOT_RUN` | freeze commit `8dd25ca`; independent acceptance PASS 2026-08-11 |
| V5-0C | Compatibility and first wire slice review | DONE | V5-0B | `/api/v1` history stays unchanged; all V2 transports disabled; explicit CLI/API major; CLI `init`/`case from-issue` only compose canonical intents; trusted atomic manifest import reaches empty V5 domain tables with seeded V4 trust roots and an existing V4 Case; application and acceptance bindings have read/write paths; resource visibility and principal allowlists fail closed | compatibility, bootstrap, acceptance provenance/confirmation, workflow retry, principal, visibility, no-route, A2A/MCP mapping and adversarial binding checks in the revised focused suite | contract-only freeze; no route/migration; runtime facets `NOT_RUN` | freeze commit `b3727d7`; independent acceptance PASS 2026-08-11, FAIL list empty |
| V5-1A | Application catalog closure repair | IN_PROGRESS | V5-0B/0C | AIApplication/Environment/SystemComponent/DependencyEdge 的 auth、exact binding、major-2 event、read replay、audit 与 transport 一致；当前 frozen lifecycle 的 REGISTERED→ACTIVE 与 runtime 直写 ACTIVE 差异必须在 closure 前决策 | focused service/HTTP/migration/PG + verifier | prior closure bundle is not accepted closure evidence; remediation remains uncommitted WIP | pending |
| V5-1B | SystemVersionSet closure repair | IN_PROGRESS | V5-1A authority slice；Master Plan D2 | manifest import 的 project/trust-role、digest/provenance、dataset role、GET/diff、并发 bootstrap 均 fail closed；第二 VersionSet 用户价值必须先冻结/激活 standalone `system-versions.record` wire，未通过 D2 时不得宣称真实 diff closure | focused unit/HTTP/diff/discovery + disposable PG race | prior closure bundle is not accepted closure evidence; remediation remains uncommitted WIP | pending |
| V5-1C | First System Case closure repair | IN_PROGRESS | V5-1A/1B repair interfaces | target local bootstrap→credential rotation→Signal/Case→binding→proposal→fresh owner reauth→confirm；Console 可见；confirm 后仍等待 V5-4 ResolutionContract，不能 READY | focused service/CLI/Console/contracts + disposable PG full journey + verifier | prior closure bundle is not accepted closure evidence; remediation remains uncommitted WIP | pending |
| V5-2+ | 后续 V5 runtime stages | TODO | R0–R4 各自完成证据与语义提交 | 严格按 Master Plan 和 blueprint 依赖图逐 stage；V5-2 durable work/event delivery 与 V5-4 ResolutionContract 不得被 1C 占位符绕过 | stage-specific | pending | pending |

## Active v4 delivery

| Priority | Work item | Status | Dependencies | Acceptance criteria | Required tests | Evidence | Completion commit |
|---|---|---|---|---|---|---|---|
| V4-S0 | Freeze v4 language, authority, contracts, and migration semantics | DONE | Approved `docs/plan-v4.md`; v3 compatibility baseline; current WIP inventory | PRD v2 and v4 plan are authoritative without claiming implementation; Aggregate Ownership, Run Semantics, Capability, WorkOrder, intent/auth/idempotency/error/compatibility and v3-to-v4 cutover rules are frozen; existing quality Team and future coding Team are unambiguous | 397 v3+v4 tests; JSON/YAML/Python/docs checks; independent verifier including authority-equivocation attacks | `evidence/v4/stage-0/contracts/s0contracts_20260810T085840Z_6604712/` | docs `b7889a7e200f76bd5985188ff6fb7e9e1860fd28`; contracts `6604712b37409cf679dfb43ce97fb9882efdc713` |
| V4-S1E | Freeze the complete public wire contract | DONE | V4-S0; Stage 1 entry audit | S1A/S1B intents are typed and `FROZEN`; later intents remain undiscoverable `SKELETON`; public auth/error/idempotency/async/cursor contracts fail closed; no-trace evidence is truthfully `UNKNOWN` without an AgentRunRef | 449 v3+v4 tests; 7 coordinated attack groups; JSON/YAML/Python/diff checks; independent verifier PASS | `evidence/v4/stage-1/wire-contract/s1wire_20260810T114213Z_070ba20/` | `070ba200acc09dfbcb725cc3466ef3ebd1e4f6fd` |
| V4-S1A | Authenticated maintainer report without trace | DONE | V4-S1E PASS; migration review | HTTP/CLI mutation transactionally writes Signal, QualityCase link, `UNKNOWN` TraceEvidenceReceipt, event, audit and idempotency receipt without inventing AgentRunRef | 691 control-plane unit; 232 coordinated attacks; 449 contracts; 81 CLI; 4 disposable PostgreSQL integration tests; independent verifier PASS | `evidence/v4/stage-1/maintainer-intake/s1a_20260810T143055Z_22c23f8/` | `22c23f89708471e3f1cb8ca893414733a839a1bd` |
| V4-S1B | Langfuse read plus CaseLoop OTel write/readback | BLOCKED | V4-S1A; rotated isolated source/sink credentials; separate live authorization | Governed Agent evidence can be fetched through the Langfuse TraceSource; CaseLoop exports its own trace to a separate sink and verifies readback; retention/masking/UNKNOWN remain explicit | connector contract, cursor/DLQ, masking, export/readback, clean-machine and separately reported live-provider evidence | pending | pending |

## Legacy v3 delivery record

| Priority | Work item | Status | Dependencies | Acceptance criteria | Required tests | Evidence | Completion commit |
|---|---|---|---|---|---|---|---|
| P0-1 | Remove gate fake-pass and release bypasses | DONE | Existing gate schema, eval-harness, WorkOrder and Release Controller | GateReport comes from actual rule/judge/test execution; is persisted and bound to WorkOrder hash, target revision, evaluation dataset/version, and evidence digest; release rejects failed, inconclusive, error, unknown, missing, or mismatched reports | Pass; rule fail; judge fail; evaluator timeout; WorkOrder hash mismatch; missing/tampered GateReport; R2 action grant; UNKNOWN receipt accuracy; PostgreSQL same/different-candidate concurrency | `evidence/p0/p0-1-gate/` | implementation `4cd6e64b2af21ccea83506d2f4cab687bb76eb76`; ratified contract hardening `10d707445b6a02da2d1451e6d6561e875c782d07` |
| P0-2 | Connect transactional outbox, Trust Ledger, notification, archive, and audit | DONE | P0-1 authoritative release outcome | Required domain events enter outbox in the business transaction and preserve per-aggregate causal order; dispatcher is retry-safe; Trust consumes real outcomes exactly once per action; 3/3 Wilson lower bound denies promotion; audit failures roll back | Event coverage; Gate terminal contract; dispatcher retry/duplicate/order race; probe coalescing; Trust 3/3 denial; audit failure; integration/replay | `evidence/p0/p0-2-outbox-trust/` | implementation `8e237e30dbbda7e05d78656047efd4c3d0703653`; review compatibility hardening `10d707445b6a02da2d1451e6d6561e875c782d07` |
| P0-3 | Wire Console to real T8 read endpoints | DONE | Stable control-plane read models from P0-1/P0-2 | Production UI reads Case, Experiment, Gate, Release, Notification, Trust, Evidence; no static success fixture; contract-aligned states; loading/empty/error/retry/partial/UNKNOWN | API runtime-guard and request-race tests; backend read-model integrity tests; real PostgreSQL/FastAPI/Vite/Chromium integration; build | `evidence/p0/p0-3-console/` | `a08c0056691b3acdafb43fd0a8b1417d10985fe6` |
| P0-4 | Repeatable B1 end-to-end loop and Evidence Bundle | BLOCKED | P0-1 through P0-3; exact live acceptance inputs; durable retry recovery | Replay traverses injection, dedup, frozen protocol, attribution=prompt, immutable WorkOrder, real gate, approval binding, stage/canary/promote, contract-compatible notification, archive, Trust denial, and a digest-verified Evidence Bundle. The user reports an earlier live run, which remains historical live evidence; independent P0 acceptance remains blocked because the committed package does not contain a run validated against the exact acceptance contract and interrupted runs cannot yet always resume or terminalize safely. | `demo-b1-replay`; `demo-b1-live` separately when a changed path or explicit acceptance target requires it; contract/replay; live-provider report; end-to-end assertions | `evidence/p0/p0-4-b1/`; `evidence/p0/p0-4-b1-live/` | implementation `b5ef38fbc1a0da90bf766f0590763be5d1118e74`; portable persisted proof `cef1598b4ac1d42fdd4f206c5747eb89a06f24fc`; independent live acceptance blocked |
| P1 | Deferred non-P0 implementation improvements | TODO | Ratified requirements and relevant P0 dependencies | Plan implementation only after the corresponding requirements are frozen; no Phase 1 dynamic-scaling or general-Agent claim | To be derived from approved contracts and verified code | pending | pending |
| P1 | Align non-terminal eval lifecycle events with the frozen event contract | TODO | Explicit runner/lease/fencing metadata at Gate execution boundary | `eval.requested`, `eval.started`, and both track-completed events carry every contract-required field and immediate causation; no invented runner metadata | Runtime event-contract validation for the four event types | pending | pending |
| P1 | Resolve PR #1 non-blocking lifecycle debt | TODO | P0 review merged | Reset B1 fault state without `KeyError`; rotate Trust epochs; close abandoned `REQUESTED` releases; refuse unknown outbox adapters; remove the Console artifact-store sentinel assumption; align remaining StepFun docs | Regression tests for each changed boundary | pending | pending |

## Historical verified v3 takeover baseline

- `main`, `origin/main`, and takeover HEAD were all `89ea66750dfed3df6feeed493d9e2499ef77cdbe` after the initial `git fetch --all --prune` on 2026-08-08. During P0-2, `origin/main` advanced to `a6de5cc1a06d6967634676b2661da7d2e46d287b`; it was merged without conflict or history rewriting at `4b5377dae317eaeadbf23ab85481881096b6d6d2`.
- All origin feature branches were already merged into `origin/main` by ancestry; no unpublished local collaborator branch was modified.
- P0-1 passed independent read-only verification and is committed at `4cd6e64`. P0-2 passed independent read-only verification at `8e237e3`. P0-3 also passed independent verification: control-plane 190; demo unit 36; demo PostgreSQL concurrency 2; eval-harness 69/4 live-only skipped; MCP 54/1 live-only skipped; schema/Wilson 26; Console 7; real-stack browser 1. The current non-overlapping proof totals 385 passed, 0 failed, and 5 explicitly live-only skipped.
- Unavailable-service probes were retained as failures: Quality API contract 15 connection failures, demo integration 6 connection failures, MCP smoke 4 passed/16 failed with control-plane/default database unavailable.
- At that verification snapshot, the Docker daemon, `STEPFUN_API_KEY`, `JUDGE_MODEL`, and a native PostgreSQL `vector` extension were unavailable. Live Quality conformance recorded 15 connection failures/26 offline passes; demo live integration recorded 6 connection failures/2 skips. These are snapshot facts, not claims about the user's later live run or current machine state.
- PR #1 blocking review remediation is implemented at `10d7074`, `4ffa942`, and `d0fb6cc`: GateReport is versioned `0.2.0`; D-002 through D-007 carry master-controller ratification; Feishu digest compatibility is fail-closed; dead MCP mutations are unregistered; replay exports persisted WorkOrder/Gate rows and uses one frozen dataset identifier; repository evidence resolution is root-bound.
- Portable replay evidence is committed at `cef1598`. Its 22 manifest artifacts all use `repo:///` references, its persisted WorkOrder and two GateReport rows pass independent binding validation, and its 135 probe outputs are digest-verified without `--allow-dirty`.
- Latest full suites after review remediation: control-plane 329 distinct tests passed across their required PostgreSQL/SQLite environment split; eval-harness 76 passed/4 live-only skipped; MCP 121 passed/1 live-only skipped; contracts 29 passed; Console 7 passed and production build passed. The formal B1 replay embedded 29 contract and 28 replay passes; live-provider remained explicitly blocked.
- The live B1 preflight is intentionally blocked before provider calls when credentials, deployed endpoints, fresh human approval grants, independent AgentTeams trace attestation, and post-injection Feishu message input are absent. Replay does not satisfy live acceptance.
