# Last Handoff

> 历史 handoff 已归档到
> [`docs/archive/context/LAST_HANDOFF-history-through-2026-08-11.md`](../archive/context/LAST_HANDOFF-history-through-2026-08-11.md)。
> 本文件只保留一个 current handoff。

## 2026-08-13 V5-2A review remediation handoff (current)

- `primary_truth`: `/Users/xiejiachen/caseloop-wt-v5-convergence` was
  fast-forwarded from `92bde3c` through `cb89cad`; the exact merged result passed
  `verify_convergence.sh` 8/8. The branch has not been pushed.
- `reviewed_candidate`: GitHub branch `origin/codex/v5-2a-work-kernel@6b30a83`
  (12 commits over `92bde3c`). No open PR/CI object represented the branch during
  this review, so the exact branch head was the review subject.
- `original_verdict`: NO-GO. Adversarial tests proved cross-Task attempt mutation,
  acceptance of caller-asserted reconcile receipts, caller-controlled idempotency
  binding, cross-Task accepted Proposal use, and an unbounded/non-recovering Work
  dispatcher. The recorded full gate did not include the Work PostgreSQL file.
- `remediation_subject`: `3677be3a97daa1b28d2e4835b7665bd388449006`
  on local branch `codex/v5-2a-review-remediation`. It adds exact Task/current-attempt
  and Proposal binding, server-owned request fingerprints, persisted outcome-bound
  terminal receipts, WAITING_RETRY cancellation closure, leased dispatcher claims,
  retry/backoff/DEAD, immutable delivery receipt + audit, a fixed worker and s8 Work
  PostgreSQL enforcement.
- `verified`: focused Work 47; PostgreSQL Work Kernel/relay 8; PostgreSQL migration
  13; import-graph tests 20 plus checker PASS; Compose config PASS; unified gate
  8/8 PASS twice after the final receipt hardening. A separate detached clean
  checkout of exact subject `3677be3` also passed 8/8 and remained clean; the
  fast-forwarded convergence head `cb89cad` then passed another complete 8/8 gate.
- `evidence`: `evidence/v5/stage-2/work-kernel/v5-2a-review-remediation_20260812T165557Z_local/`.
- `post_merge_evidence`: `evidence/v5/stage-2/work-kernel/v5-2a-post-merge_20260812T172809Z_local/`.
- `facet_truth`: `contract=PASS`, deterministic/local-PostgreSQL `replay=PASS`;
  `domain-provider-live`, `agentteams-native`, `claude-runtime-live`, `agent-causal`,
  `repo-sandbox`, `human-authorized-external` and `production-canary` are `NOT_RUN`.
- `decision`: locally merged by user authorization via `--ff-only`; merge-result
  gate PASS. Nothing was pushed. V5-2B+ remain locked pending their own entry decision.
- `non_blocking_existing_risk`: clean `npm audit --omit=dev` reports two moderate
  React Router advisories; the automated fix is a major-version upgrade. Neither
  Console dependency file changed in `92bde3c..3677be3`.

## 2026-08-12 V5-2A Work Kernel closure handoff (superseded by the local review remediation above)

- `scope`: V5-2A Durable Work Kernel (Master §6), all six sub-packages 2A-0 → 2A-5
  constructed and closed on branch `codex/v5-2a-work-kernel` (base `92bde3c`).
- `decision`: D-016 (`docs/decisions/D-016-v5-work-event-schema-major-routing.md`)
  adjudicates the 2A-0 open question: Work events use the major-2 envelope on the
  dedicated `v5.work.events` outbox channel; V4 semantic owners and state machines
  are reused by reference, never copied; V4 payloads are never reinterpreted.
  Recorded as an engineering decision under the 2026-08-12 product-owner
  delegation, not as a product-owner decision.
- `semantic_series`: `9fa5363` (2A-0 contract freeze) → `0c1528c` (2A-1 six tables
  + migration 014) → `fa39646` (2A-2 claim/fencing engine) → `a626bd7` (2A-3
  channel dispatcher + reaction ledger) → `8c5f328` (2A-4 deterministic fixture
  executor) → `e46d05a` (2A-5 PostgreSQL closure proofs).
- `verified`: compiler determinism CLEAN; compiler 18; conformance 559;
  control-plane unit + wave checkers 1021 passed / 13 PG-gated skips; CLI 130;
  Console 20 + build; import-graph PASS (0 cycles / 0 unclassified); disposable
  PostgreSQL 18.4 migration matrix 13 passed and integration matrix 13 passed,
  including the 2A-5 exit proofs: concurrent double-claim mints exactly one
  lease (no double lease), proposal accept without downstream command rejected
  (no ghost success), and re-claim after ambiguous outcome blocked until
  reconcile (no ambiguous retry).
- `facet_truth`: `contract=PASS`, `replay=PASS` (deterministic fixture executor,
  no model/provider).  All other seven facets `NOT_RUN` — no provider keys on
  this machine, and Master §546 keeps Agent/provider liveness out of 2A scope.
- `environment`: this machine has **no Docker**; the disposable PostgreSQL used
  for every PG journey is npm `embedded-postgres` 18.4 on 127.0.0.1:55432
  (scratch database `control_plane_test`, fsync off).  The s8 gate's existing
  member `tests/integration/test_v5_application_catalog_postgres.py` is NOT_RUN
  here: it deadlocks via a pre-existing leaked connection vs `DROP SCHEMA`
  (root-caused; registered as `wiki/build-guide.md` G20, independent follow-up).
- `not_done`: no public route/CLI/capability is activated for the Work Kernel
  (all transports remain FORBIDDEN per D-016); V5-2B (async public intents) is
  the next package in the Master chain and remains locked until 2A is merged.
- `merge_state`: branch is pushed for review; merging to `main` and any PR
  ceremony remain with the product owner.

## 2026-08-12 V5 post-convergence execution handoff (superseded by the V5-2A closure above; retained for its R1/R2 and C0–C5 facts)

- `decision`: product owner accepted D-015. V5 is now the default design and construction baseline
  for every new product/domain change. V3 is the implemented compatibility lane; V4 is the V4
  compatibility lane. This is not an operational runtime cutover and does not change the public or
  CLI default major.
- `verified_subjects`: R1 authority/event foundation is committed at `8e21693`; R2 catalog and
  bootstrap-only first graph is committed at `c838b2b`. Both passed clean post-commit independent
  verification with P0=0/P1=0. R2 verification recorded contracts 541, control-plane unit 876 plus
  12 explicitly PG-gated skips, CLI 118, Console 17 plus build, and disposable PostgreSQL 17/17.
- `facet_truth`: only `contract=PASS` and the explicitly bounded R1/R2 `replay=PASS`. The seven
  `domain-provider-live`, `agentteams-native`, `claude-runtime-live`, `agent-causal`, `repo-sandbox`,
  `human-authorized-external`, and `production-canary` facets remain `NOT_RUN`.
- `checksum_policy`: the product owner deferred SHA-256 and digest-bearing final evidence until the
  final whole-project closure stage. R1/R2 therefore remain `VERIFYING`; do not relabel them DONE or
  fabricate an interim checksum bundle.
- `subject_time_metadata`: contract-overlay labels such as
  `IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER` describe the R1/R2 semantic subject before its verifier
  ran. Their current verifier truth is PASS/P0=0/P1=0 above; C1 owns status/activation metadata
  single-sourcing and must not change the 11-intent surface while doing so.
- `execution_baseline`: branch `codex/v5-convergence`; D-015; and
  `docs/plans/v5-master-execution-plan.md` version `2026-08-12.10`, especially §17. The Master is
  the single highest engineering execution orchestration below AGENTS/product/ADR/contracts. The
  architecture remains a modular monolith with one PostgreSQL business UoW. Compatibility/shared
  infrastructure has no independent domain-success authority.
- `preserved_wip`: the original `codex/v4-foundation` worktree remains untouched with 144 mixed
  status entries. Its exact path inventory is
  [`V5_CONVERGENCE_WIP_INVENTORY.md`](V5_CONVERGENCE_WIP_INVENTORY.md). No listed path may be
  bulk-promoted into the convergence branch.
- `active_surface`: R2 exposes exactly the frozen 11-intent V2 surface only when V2 is explicitly
  selected. D2 froze `system-versions.record/get/diff` as `FROZEN_FOR_IMPLEMENTATION /
  NOT_IMPLEMENTED` (contract-only, no transport, capability discovery stays hidden). Standalone
  activation, second VersionSet record/get/diff runtime, R4 Case/Acceptance routes, V5-2+,
  MCP/A2A, provider/live and production surfaces remain hidden or unimplemented.
- `next_action`: V5-2A construction is handed to the remote construction machine per the
  product-owner division of labor (2026-08-12): remote writes all code from V5-2A-0
  contract/owner/migration freeze onward (Master §17.6 Durable Work Kernel); local runs the
  disposable-PostgreSQL journeys, any live facet, and final closure. Remote has no Docker and
  no provider keys, so its green runs never include PG journeys; those stages stay
  `VERIFYING (replay=LOCAL_VERIFICATION_PENDING)` until local re-runs them. The remote
  briefing, safe verification subset, and known pitfalls are in
  [`V5_REMOTE_CONSTRUCTION_HANDOFF.md`](V5_REMOTE_CONSTRUCTION_HANDOFF.md).
- `r4_first_case_closure`: `DONE (contract + R4-scoped replay)` at semantic series `df86662` +
  `7c17391` + `365c2c8`. Five 1C intents activated; issue-source→case→bind→propose→confirm with
  immutable authority records and readiness bounded at NEEDS_ACCEPTANCE_CRITERIA /
  PENDING_MATERIALIZATION (never READY before V5-4A); duplicate confirmation fails closed; CLI
  case command family incl. local from-issue. Verifier PASS P0=0/P1=0; unit 996+12 PG skips; CLI
  130; conformance 557; compiler 18; disposable PG 31+1 R2-skip. The post-report remediation
  (`365c2c8`) was confirmed by a second detached clean-checkout pass on 2026-08-12 with all
  gates green. Evidence:
  `evidence/v5/stage-1/first-system-case/r4firstcase_20260812T072114Z_365c2c8/`.
- `local_closure_2026_08_12`: the convergence worktree moved from `/private/tmp` to
  `~/caseloop-wt-v5-convergence` (branch `codex/v5-convergence`, clean). Local code
  construction pauses here; remote construction starts from the commit containing
  `V5_REMOTE_CONSTRUCTION_HANDOFF.md`.
- `r3_full_closure`: `DONE (contract + R3-scoped replay)` at semantic series `33ea7e6` +
  `caed0eb` + `18482f8`. Activated `FROZEN_R3` record/get/diff with exact-previous CAS lineage
  under a workspace/application/environment advisory lock, one PG UoW (event/outbox/audit/
  receipt/idempotency), reference-only validation, recursive get verification, deterministic
  D2-shaped diff, migration 013, HTTP/CLI/capability activation. Verifier PASS P0=0/P1=0;
  unit 995+12 PG skips; CLI 120; conformance 557; compiler 18; disposable PG 30+1 R4-skip.
  Evidence:
  `evidence/v5/stage-1/system-version/r3full_20260812T050342Z_caed0eb/`.
- `d2_closure`: `DONE (contract-only)` at semantic series `5717124` + `4852664`. Freezes the
  complete `system-versions.record/get/diff` bundle: JSON Schema 2020-12 wire, authority,
  idempotency, lineage/CAS, event/outbox/audit/receipt, capability discovery stays hidden, and a
  non-trivial two-VersionSet diff fixture (changed/added/removed + topology edge change) with the
  13-vector adversarial matrix. Post-commit verifier PASS P0=0/P1=0; focused D2 10; full
  contract suite 557; compiler 18; `compiler emit` deterministic with zero generated diff; YAML
  dup-key/link/secret-PII 0; only `contract=PASS`, other 8 facets `NOT_RUN`. SHA/evidence digest
  remains `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`. Evidence:
  `evidence/v5/decision-gates/d2-complete-version-graph-contract/d2versiongraph_20260812T020335Z_4852664/`.
- `c0_closure`: `DONE` at `903e954` (semantic series `a14784a` + `903e954`; post-commit verifier
  PASS, P0=0/P1=0; not a runtime/public cutover; R2 stays `contract/replay PASS only` /
  `VERIFYING`; SHA/evidence digest remains `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`).
- `c1_closure`: `DONE` at `3dc7339` (JSON Schema 2020-12 wire contracts + compiler + generated
  manifests + corpus; post-commit verifier PASS, P0=0/P1=0; determinism PASS; C4/C5 remediation
  now requires generated structural and semantic validation to both accept; SHA/evidence digest remains
  `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`; divergences recorded in
  contracts/v5/c1-shadow-findings.md).
- `c2_closure`: `DONE` at `d2c3f18` (foundation records/events/bindings/receipts/graph extracted
  from v4_event_store and v5_authority; re-exports keep all importers working; legacy in-service
  graph checks retained and recorded in contracts/v5/c2-foundation-findings.md; post-commit
  verifier PASS, P0=0/P1=0; SHA/evidence digest remains
  `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`).
- `c3_closure`: `DONE` at `3adaac0` (SCC broken; capability manifest-derived;
  scripts/check_import_graph.py; adjudication recorded in
  contracts/v5/c3-capability-import-adjudication.md; post-commit verifier PASS, P0=0/P1=0;
  SHA/evidence digest remains `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`).
- `c4_closure`: `DONE` at `1d7b59c`; `b6fa629` removes winner/fallback ambiguity so generated
  structural and semantic validation must both accept. Findings in
  contracts/v5/c4-transport-cutover-adjudication.md; post-commit
  verifier PASS, P0=0/P1=0; SHA/evidence digest remains
  `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`).
- `c5_closure`: original semantic package `19f26bf`, repaired at `b6fa629`. Remediation removes
  duplicate manifest catalog construction, makes generated structural + semantic validation jointly
  fail-closed, adds the real `ENABLE_PUBLIC_V5` rollback switch, and makes the unified gate include
  Console and explicit disposable PostgreSQL. Detached clean `verify_convergence.sh` is 8/8 PASS:
  compiler 18, contracts 547, control-plane 998 collected/12 safety skip, CLI 118, Console 20+build,
  PostgreSQL 17/17.
- `convergence_series`: `COMPLETE / REMEDIATED` (C0 `a14784a`+`903e954`/`e809d5c`, C1 `3dc7339`/`e120f98`,
  C2 `d2c3f18`/`8e60134`, C3 `3adaac0`/`fdf57d7`, C4 `1d7b59c`/`eed1a37`, C5 `19f26bf` + `b6fa629`;
  SHA/evidence digest remains `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`).
- `authorization_boundary`: local documentation, contract, code, test, precise staging and semantic
  commits within the accepted convergence plan are authorized. Push, PR, paid provider calls, live,
  human approval, production or other external writes still require separate authorization.
