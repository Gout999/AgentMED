# Last Handoff

## 2026-08-10 v4 Stage 1 Entry closure handoff

- `current_repo`: `codex/v4-foundation` at Stage 1 Entry contract completion `070ba200acc09dfbcb725cc3466ef3ebd1e4f6fd`, based on `origin/main` `81ae70654eeef55d60e96cac90e181609dea4f29`. The only preserved dirty code after the evidence/context commit is the separate judge-provider and live-B1 WIP.
- `product_decision`: CaseLoop is a long-term open-source AI Agent quality and change-governance project. Target-user needs, safety and closed-loop completeness decide scope; closed-source competitors, market gaps and overlapping features do not.
- `authority`: product strategy is in `docs/product-principles.md`; approved v4 new development follows `docs/plan-v4.md`; `docs/plan-v3.md`, `docs/spec.md`, existing contracts, migrations, and tests remain the implemented v3 compatibility baseline until explicit migration. Plans do not prove runtime completion; research and this handoff are not implementation authority.
- `first_new_requirement`: Langfuse has two roles—CaseLoop's own trace backend and the first TraceSource adapter for retrieving a governed Agent's input/output/model/tool evidence. This is confirmed direction, not current implementation.
- `causality_decision`: real Agent causal execution and post-action platform evidence export have separate acceptance tracks. An exporter cannot create task/ack/submit/human artifacts and then use them as proof of the events it manufactured.
- `workload_scope`: customer service remains the first reference workload. Coding Agent governance with Claude Code as a controlled child execution backend is the approved second workload; it must use general Signal/WorkerTask/Attempt/Proposal/Gate contracts rather than a separate script state machine.
- `team_state`: the current `caseloop-team` is six CoPaw/StepFun `step-3.7-flash` quality-governance Workers. At the observed snapshot all six were `Sleeping`, `leaderReady=false`, and `readyWorkers=0`; Manager/Controller were running, but no Agent task was executing. No Claude Code or GLM Agent was deployed. The three-role `caseloop-coding-team` is approved but not yet created.
- `v4_status`: `docs/plan-v4.md` is the approved target architecture and construction baseline. Stage 0 and Stage 1 Entry are `DONE (contract-only)`. The Entry contract has 8 frozen S1A/S1B intents, 6 undiscoverable future skeleton intents, truthful no-trace `UNKNOWN`, and fail-closed public identity/idempotency/event routing. Database migration, public API implementation, Langfuse, coding Team, Claude runtime and live evidence remain unimplemented. Stage 1A is `IN_PROGRESS`.
- `wiki_alignment`: the `wiki/` hierarchy and terminology are aligned for Stage 0. It separates the governed Agent, CaseLoop internal Worker, deterministic Controller/Executor and read-only Exporter; preserves AgentTeams as a versioned adapter; and labels target contracts as contract-only rather than implemented.
- `stage0_evidence`: `evidence/v4/stage-0/contracts/s0contracts_20260810T085840Z_6604712/`. Only `contract=PASS`; every runtime/provider/Agent/repository/human/production facet is `NOT_RUN`.
- `stage0_verification`: 397 v3+v4 contract tests, 218 JSON documents, 5 YAML documents, 15 Python files, 111 local Markdown links, SOUL sync and diff-check passed. The independent verifier reproduced and then confirmed closure of duplicate event/audit and same-record-identity equivocation attacks; remaining Stage 0 P0/P1 is zero.
- `stage1_entry_evidence`: `evidence/v4/stage-1/wire-contract/s1wire_20260810T114213Z_070ba20/`. Only `contract=PASS`; every runtime/provider/Agent/repository/human/production facet is `NOT_RUN`.
- `stage1_entry_verification`: 449 v3+v4 contract tests, 244 JSON documents, 5 YAML documents, 19 Python files and diff-check passed. The independent verifier rejected all seven attack groups covering skeleton discovery, no-trace fabrication, principal scope/time/tenant, v4 routing, pre-auth workspace, response inflation and mutable replay receipts.
- `next_action`: implement migration 007 and the authenticated maintainer report without trace. It must transactionally create Signal, OPEN QualityCase with `NEEDS_CORRELATION`, SignalCaseLink, `UNKNOWN` TraceEvidenceReceipt, four events, AuthorityReceipts, PG audit and immutable idempotency result without inventing AgentRunRef. Langfuse read and CaseLoop OTel write/readback follow only after this slice passes.
- `live_wording`: the user confirms an earlier live run was completed. Preserve that history. Do not conflate it with a new acceptance run, and do not rerun solely because it is old; rerun only for changed code/provider paths, a different evidence contract, or a reproducibility/acceptance need.
- `security_blocker`: a read-only Compose inspection expanded secret-bearing environment values into a private tool log. No value was copied into repository files or evidence, but StepFun, Feishu, and internal authority/read/write/role credentials must be treated as potentially exposed and rotated before the next live/provider run. Rotation has not been performed or authorized; Stage 0 offline work can continue.
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
