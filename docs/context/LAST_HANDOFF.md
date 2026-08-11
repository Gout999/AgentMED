# Last Handoff

> 历史 handoff 已归档到
> [`docs/archive/context/LAST_HANDOFF-history-through-2026-08-11.md`](../archive/context/LAST_HANDOFF-history-through-2026-08-11.md)。
> 本文件只保留一个 current handoff。

## 2026-08-11 V5 default-development baseline and convergence handoff (current)

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
- `execution_baseline`: branch `codex/v5-convergence`; D-015;
  `docs/plans/v5-architecture-convergence.md`; and the convergence gate in the Master Plan. The
  architecture remains a modular monolith with one PostgreSQL business UoW. Compatibility/shared
  infrastructure has no independent domain-success authority.
- `preserved_wip`: the original `codex/v4-foundation` worktree remains untouched with 144 mixed
  status entries. Its exact path inventory is
  [`V5_CONVERGENCE_WIP_INVENTORY.md`](V5_CONVERGENCE_WIP_INVENTORY.md). No listed path may be
  bulk-promoted into the convergence branch.
- `active_surface`: R2 exposes exactly the frozen 11-intent V2 surface only when V2 is explicitly
  selected. Standalone activation, second VersionSet record/get/diff, R4 Case/Acceptance routes,
  V5-2+, MCP/A2A, provider/live and production surfaces remain hidden or unimplemented.
- `next_action`: C4 closed: semantic subject `1d7b59c` (transports cut to generated artifacts:
  OpenAPI 3.1 document, TS module, route-registry gate, CLI manifest-derived commands, console
  dual-guard shadow; Python parity extended to 11 ops/103 cases), post-commit verifier PASS
  (P0=0/P1=0). Stop and review the C4 gate before beginning C5 (`ELIGIBLE / NOT STARTED`). D2,
  R3-full, R4 and V5-2+ remain blocked through convergence.
- `c0_closure`: `DONE` at `903e954` (semantic series `a14784a` + `903e954`; post-commit verifier
  PASS, P0=0/P1=0; not a runtime/public cutover; R2 stays `contract/replay PASS only` /
  `VERIFYING`; SHA/evidence digest remains `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`).
- `c1_closure`: `DONE` at `3dc7339` (JSON Schema 2020-12 wire contracts + compiler + generated
  manifests + corpus; post-commit verifier PASS, P0=0/P1=0; determinism PASS; legacy validators
  and routes remain authoritative until C4; SHA/evidence digest remains
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
- `c4_closure`: `DONE` at `1d7b59c` (generated transports authoritative; per-surface fallback
  verified by drills; findings in contracts/v5/c4-transport-cutover-adjudication.md; post-commit
  verifier PASS, P0=0/P1=0; SHA/evidence digest remains
  `DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`).
- `authorization_boundary`: local documentation, contract, code, test, precise staging and semantic
  commits within the accepted convergence plan are authorized. Push, PR, paid provider calls, live,
  human approval, production or other external writes still require separate authorization.
