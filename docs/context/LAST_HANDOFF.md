# Last Handoff

## 2026-08-11 V5-0 closure handoff

- `closure`: V5-0 contract baseline is frozen and accepted as the V5 stage construction baseline.
  V5-0B `8dd25ca` (system governance ownership) and V5-0C `b3727d7` (first system wire slice)
  both passed independent acceptance on 2026-08-11; V5-0B acceptance included 13 adversarial
  checks added by the verifier, and the V5-0C FAIL list was empty.
- `offline_counts`: v3+v4+v5 combined 517 passed; v3/v4 independent 449 passed; V5 focused
  68 passed. `conformance/test_quality_api.py` requires a live service and is `NOT_RUN`.
  These are contract-only counts; no V5 runtime facet exists yet.
- `authority_switch`: `AGENTS.md` now names `docs/plan-v5.md` + `docs/plans/v5-progressive-delivery.md`
  + the frozen `contracts/v5/` as the V5 stage construction baseline; `docs/plan-v4.md` remains the
  V4 compatibility baseline and v3 remains the implemented compatibility baseline. V5 stages are
  unfrozen on the local branch, built one at a time per the blueprint dependency graph, each with
  focused tests, verifier, evidence and semantic commit. Push/PR/paid provider/human approval/
  external writes still require their own authorization.
- `next_action`: V5-1A (Application catalog) — migration for AIApplication/Environment/
  SystemComponent/DependencyEdge, `/api/v2` register/get, Console Applications read model. V5-1B
  and later follow the blueprint graph; V4 S1B–S7 remain frozen.
- `honest_uncertainty`: contract freeze is not runtime implementation — every V5 runtime/live
  facet stays `NOT_RUN`. Known V5-1B alignment item carried from the V5-0C acceptance: `trusted_attestor`
  is still dangling in the trust-role lists of `system-versions.record` and `bootstrap_attestation`
  and must be aligned with `catalog_admin` during V5-1B. Process convention agreed with the verifier:
  subsequent constructors record honest uncertainty in the commit body or under `evidence/`, not
  only in prose.
- `boundary`: offline-only closure. No live provider call, no remote write, no push; the preserved
  judge/live WIP groups remain untouched and separate.

## 2026-08-11 V5 design-preparation handoff

- `decision`: D-013 accepts CaseLoop V5 as an Agent-native governance operations control plane for
  complete AI applications. The top-level governed object is `AIApplication`; Agent, model, Prompt,
  RAG, tool, policy, memory and runtime are versioned components.
- `prepared_documents`: `docs/prd-v5.md`, `docs/plan-v5.md`,
  `docs/plans/v5-progressive-delivery.md` and D-013 now describe the target, migration and cold-start
  construction sequence. They remain `DRAFT / NOT IMPLEMENTED` and do not yet replace the
  `AGENTS.md` construction baseline.
- `prepared_contracts`: `contracts/v5/` contains a non-routable `/api/v2` draft domain model,
  authority/owner/state/event contracts, schema-major-2 system profiles, intent registry, bootstrap
  first-case fixture and `/api/v1` compatibility rules. The fixture starts with empty V5 domain
  tables plus seeded V4 Controller trust roots and an existing V4 Case, not a wholly empty database.
  The focused V5 draft suite passes 36 tests; the combined v3/v4/V5 offline suite passes 485.
- `bounded_review`: independent read-only closure PASS found the competition golden path reachable,
  the broader V5 product scope preserved, and current/target claims honest. This accepts the design
  preparation package for handoff only; it does not authorize migration, route or runtime work.
- `product_review_revision`: two subsequent independent product/evaluation reports converged on a
  partial GO. The user accepted their shared changes: First Useful Case now requires confirmed
  acceptance or an honest `NEEDS_ACCEPTANCE_CRITERIA`; code and AI-behavior workloads have different
  applicable evaluation/release depth; CLI discovery reduces first-use work; Gate must be checked
  against adversarial false fixes and real accepted fixes. This updates draft product semantics only.
- `authority_model`: V5 uses schema-major-2 Candidate/Evaluation/Gate/WorkOrder/Approval/
  Capability/ExternalOperation profiles while keeping the V4 logical owners and compatible
  lifecycles; V5 fills the human Approval lifecycle that V4 named but did not implement. It does
  not inherit closed V4 JSON schemas or create a third Case/Gate/Release stack. ReleasePlan is sealed
  before Gate; Assignment changes only from an exact root ExternalOperation, and observed runtime/
  external effect require independent evidence.
- `version_and_eval`: `SystemVersionSet` contains only declared runtime composition and immutable
  topology. EvaluationBundle stays independent; EvaluationPlan/Gate binds exact base/target
  VersionSets, CandidateRevision, ResolutionContract/confirmed badcase, EvaluationBundle and immutable
  Episode snapshots. Candidate-verification PASS produces no WorkOrder; release-authorization PASS
  additionally exact-binds a pre-sealed ReleasePlan and is the only Gate that may create a WorkOrder.
  Deterministic code checks may mark Judge `N/A`; non-deterministic thresholds come from workload
  baselines and an uncalibrated Judge is advisory only. Required non-PASS evidence still fails closed.
- `agent_native`: a real external Agent calling the CLI is sufficient for the first competition
  proof. Public MCP/A2A are later increments after Durable Work; target A2A methods are
  `SendMessage/GetTask/ListTasks/CancelTask`, and Task completion never implies Gate PASS.
- `competition_scope`: CLI-discovered and human-confirmed manifest import, single
  workspace/application/environment, a real locally deployable AI-application Issue, confirmed
  badcase with base-fail/target-pass, exactly one main change surface, regression/holdout/anti-overfit/
  repo-sandbox evidence, pre-sealed ReleasePlan, release-authorizing Gate, human approval, local target
  adapter, independent observed digest, post-release Gate, rollback drill and one Case detail Console.
  A normal library/code Issue may be a verification-only control and must end `NOT DEPLOYED`. Generic catalog/graph,
  full attribution, operation list/cancel, three workspaces and A2A/MCP runtime are not the contest gate.
  This is delivery sequencing rather than product-scope deletion; the wider multi-component,
  continuous-source, Console, Agent-protocol and operations modules remain V5 targets. No upstream
  comment, fork, push or PR is implied.
- `current_runtime_truth`: unchanged at V4 S1A commit `22c23f8`; the only implemented generalized
  V4 slice is authenticated HTTP/CLI Signal→QualityCase→honest `UNKNOWN` evidence. The previously
  verified v3 P0-1 through P0-3 and replay compatibility runtime remain present. All V5 runtime/live
  facets are `NOT_RUN`.
- `working_tree_boundary`: preserve the unrelated judge/live WIP listed in PROJECT_STATE. The V5
  preparation files are not committed, staged or pushed by this handoff.
- `next_action`: realign `contracts/v5/`, the intent registry and first-case fixtures with confirmed
  acceptance provenance, workload/applicability, verification-only Gate and CLI workflow semantics;
  add positive/adversarial conformance coverage and repeat independent review. Only after that review
  may the owner update `AGENTS.md`/construction authority and begin migrations. Until then, do not
  start V4 S1B–S7 or V5 runtime implementation.

## 2026-08-10 v4 Stage 1A closure handoff

- `current_repo`: `codex/v4-foundation` at Stage 1A implementation `22c23f89708471e3f1cb8ca893414733a839a1bd`, based on the Stage 1 Entry closure `0a2bcca6eb756e3cf53062836aefb378c0ee1b81`. The only preserved dirty code after the closure commit is the separate judge-provider and live-B1 WIP.
- `product_decision`: CaseLoop is a long-term open-source AI Agent quality and change-governance project. Target-user needs, safety and closed-loop completeness decide scope; closed-source competitors, market gaps and overlapping features do not.
- `authority`: product strategy is in `docs/product-principles.md`; approved v4 new development follows `docs/plan-v4.md`; `docs/plan-v3.md`, `docs/spec.md`, existing contracts, migrations, and tests remain the implemented v3 compatibility baseline until explicit migration. Plans do not prove runtime completion; research and this handoff are not implementation authority.
- `first_new_requirement`: Langfuse has two roles—CaseLoop's own trace backend and the first TraceSource adapter for retrieving a governed Agent's input/output/model/tool evidence. This is confirmed direction, not current implementation.
- `causality_decision`: real Agent causal execution and post-action platform evidence export have separate acceptance tracks. An exporter cannot create task/ack/submit/human artifacts and then use them as proof of the events it manufactured.
- `workload_scope`: customer service remains the first reference workload. Coding Agent governance with Claude Code as a controlled child execution backend is the approved second workload; it must use general Signal/WorkerTask/Attempt/Proposal/Gate contracts rather than a separate script state machine.
- `team_state`: the current `caseloop-team` is six CoPaw/StepFun `step-3.7-flash` quality-governance Workers. At the observed snapshot all six were `Sleeping`, `leaderReady=false`, and `readyWorkers=0`; Manager/Controller were running, but no Agent task was executing. No Claude Code or GLM Agent was deployed. The three-role `caseloop-coding-team` is approved but not yet created.
- `v4_status`: Stage 0 and Stage 1 Entry are `DONE (contract-only)`. Stage 1A is `DONE (local runtime)`: migration 007, strict bootstrap, five authenticated HTTP/CLI intents and the transactional no-trace Signal/Case/Link/`UNKNOWN` Evidence path are implemented. S1B through S7 remain unimplemented or blocked; no provider, Agent runtime, repository or production capability follows from S1A.
- `wiki_alignment`: the `wiki/` hierarchy and terminology are aligned through S1A. It separates the governed Agent, CaseLoop internal Worker, deterministic Controller/Executor and read-only Exporter; preserves AgentTeams as a versioned adapter; and labels later target contracts as unimplemented.
- `stage0_evidence`: `evidence/v4/stage-0/contracts/s0contracts_20260810T085840Z_6604712/`. Only `contract=PASS`; every runtime/provider/Agent/repository/human/production facet is `NOT_RUN`.
- `stage0_verification`: 397 v3+v4 contract tests, 218 JSON documents, 5 YAML documents, 15 Python files, 111 local Markdown links, SOUL sync and diff-check passed. The independent verifier reproduced and then confirmed closure of duplicate event/audit and same-record-identity equivocation attacks; remaining Stage 0 P0/P1 is zero.
- `stage1_entry_evidence`: `evidence/v4/stage-1/wire-contract/s1wire_20260810T114213Z_070ba20/`. Only `contract=PASS`; every runtime/provider/Agent/repository/human/production facet is `NOT_RUN`.
- `stage1_entry_verification`: 449 v3+v4 contract tests, 244 JSON documents, 5 YAML documents, 19 Python files and diff-check passed. The independent verifier rejected all seven attack groups covering skeleton discovery, no-trace fabrication, principal scope/time/tenant, v4 routing, pre-auth workspace, response inflation and mutable replay receipts.
- `stage1a_evidence`: `evidence/v4/stage-1/maintainer-intake/s1a_20260810T143055Z_22c23f8/`. `contract=PASS` and the narrowly scoped deterministic local `replay=PASS`; every provider/Agent/repository/human/production facet is `NOT_RUN`.
- `stage1a_verification`: independent PASS with 691 control-plane unit tests, 232 coordinated attack replays, 449 contracts, 81 CLI tests and 4 disposable PostgreSQL integrations. Demo 40, eval 85 with 4 live skips, MCP 121 with 1 live skip and Console build also passed. The PostgreSQL runs cleaned all public tables/types and active sessions.
- `next_action_at_that_snapshot`: requirements intake for a possible V5 expansion. This historical
  action is superseded by the V5 design-preparation handoff above; S1B–S7 remain paused.
- `live_wording`: the user confirms an earlier live run was completed. Preserve that history. Do not conflate it with a new acceptance run, and do not rerun solely because it is old; rerun only for changed code/provider paths, a different evidence contract, or a reproducibility/acceptance need.
- `security_blocker`: a read-only Compose inspection expanded secret-bearing environment values into a private tool log. No value was copied into repository files or evidence, but StepFun, Feishu, and internal authority/read/write/role credentials must be treated as potentially exposed and rotated before the next live/provider run. Rotation has not been performed or authorized; local offline work may continue.
- `action_boundary`: the user authorized the local foundation branch and progressive document, contract, code, and test construction. Push, PR, paid provider calls, human approvals, and production or other external writes remain separately gated and must not be inferred from this authorization.

## Historical PR #1 handoff (preserved snapshot)

- `round_goal`: resolve every blocking item in the PR #1 master-controller
  review, publish third-party-verifiable replay evidence, and mark the PR ready
  without merging it.
- `actual_changes`: versioned GateReport as `0.2.0`; recorded master-controller
  ratification in D-002 through D-007; added a fail-closed Feishu digest
  compatibility path and removed three obsolete registered MCP mutations;
  exported persisted WorkOrder/GateReport rows from replay; unified the frozen
  dataset binding; made nested and manifest evidence references root-bound
  `repo:///` URIs; and taught isolated replay attribution to resolve only
  repository-contained evidence. Replaced the old machine-specific B1 run with
  a clean, portable run.
- `key_files`: `contracts/schemas/gate-report.schema.json`,
  `docs/decisions/D-002-gate-workorder-binding.md` through
  `docs/decisions/D-007-sync-gate-fail-closed.md`,
  `mcp-servers/servers/notification.py`,
  `mcp-servers/servers/release_admin.py`,
  `control-plane/app/services/attribution.py`, `scripts/run_b1_replay.py`,
  `scripts/validate_b1_run.py`, and `evidence/p0/p0-4-b1/`.
- `test_results`: control-plane 329 distinct tests passed across the required
  PostgreSQL/SQLite environment split; contracts 29 passed; eval-harness 76
  passed/4 live-only skipped; MCP 121 passed/1 live-only skipped; Console 7
  passed and production build passed. The clean replay embedded 29 contract and
  28 replay passes. Independent validation verified all 22 portable artifacts,
  both persisted GateReports, one persisted WorkOrder, and 135 probe outputs.
- `unfinished`: P0-4 independent live acceptance remains `BLOCKED`. At this
  historical evidence snapshot, the committed package did not contain a live
  StepFun/Quality/Control Plane/Feishu run with fresh human approval and an
  independently signed AgentTeams trace. This does not deny the user's later
  or separately retained live run. Crash recovery after Case
  creation and after promotion is not yet durable. PR #1 non-blocking minor
  debt remains in `PLANS.md`.
- `resume_from`: obtain the exact live preflight inputs or first implement and
  test durable reconciliation after Case creation and after promotion. Keep
  `make demo-b1-live` separate from replay and fail closed on every missing
  authority or provider receipt.
- `commit_hash`: contract/review hardening
  `10d707445b6a02da2d1451e6d6561e875c782d07`; nested portable refs
  `4ffa9425db9d514f4dc962f3b0faabb0bc6656e0`; root-bound replay resolver
  `d0fb6ccb336a13982bc882e5b80c8705b57b62d7`; portable evidence
  `cef1598b4ac1d42fdd4f206c5747eb89a06f24fc`.
