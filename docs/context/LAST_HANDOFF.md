# Last Handoff

## 2026-08-10 v4 Stage 0 closure handoff

- `current_repo`: `codex/v4-foundation` at contract completion `6604712b37409cf679dfb43ce97fb9882efdc713`, based on `origin/main` `81ae70654eeef55d60e96cac90e181609dea4f29`. The v4 documents are committed at `b7889a7e200f76bd5985188ff6fb7e9e1860fd28`; the only preserved dirty code after the evidence/context commit is the separate judge-provider and live-B1 WIP.
- `product_decision`: CaseLoop is a long-term open-source AI Agent quality and change-governance project. Target-user needs, safety and closed-loop completeness decide scope; closed-source competitors, market gaps and overlapping features do not.
- `authority`: product strategy is in `docs/product-principles.md`; approved v4 new development follows `docs/plan-v4.md`; `docs/plan-v3.md`, `docs/spec.md`, existing contracts, migrations, and tests remain the implemented v3 compatibility baseline until explicit migration. Plans do not prove runtime completion; research and this handoff are not implementation authority.
- `first_new_requirement`: Langfuse has two roles—CaseLoop's own trace backend and the first TraceSource adapter for retrieving a governed Agent's input/output/model/tool evidence. This is confirmed direction, not current implementation.
- `causality_decision`: real Agent causal execution and post-action platform evidence export have separate acceptance tracks. An exporter cannot create task/ack/submit/human artifacts and then use them as proof of the events it manufactured.
- `workload_scope`: customer service remains the first reference workload. Coding Agent governance with Claude Code as a controlled child execution backend is the approved second workload; it must use general Signal/WorkerTask/Attempt/Proposal/Gate contracts rather than a separate script state machine.
- `team_state`: the current `caseloop-team` is six CoPaw/StepFun `step-3.7-flash` quality-governance Workers. At the observed snapshot all six were `Sleeping`, `leaderReady=false`, and `readyWorkers=0`; Manager/Controller were running, but no Agent task was executing. No Claude Code or GLM Agent was deployed. The three-role `caseloop-coding-team` is approved but not yet created.
- `v4_status`: `docs/plan-v4.md` is the approved target architecture and construction baseline. Stage 0 is `DONE (contract-only)` with 397 passing tests and an independent verifier. Database migration, public API, Langfuse, coding Team, Claude runtime and live evidence remain unimplemented. Stage 1 Entry wire-contract freeze is `IN_PROGRESS`; Stage 1 runtime is still blocked.
- `wiki_alignment`: the `wiki/` hierarchy and terminology are aligned for Stage 0. It separates the governed Agent, CaseLoop internal Worker, deterministic Controller/Executor and read-only Exporter; preserves AgentTeams as a versioned adapter; and labels target contracts as contract-only rather than implemented.
- `stage0_evidence`: `evidence/v4/stage-0/contracts/s0contracts_20260810T085840Z_6604712/`. Only `contract=PASS`; every runtime/provider/Agent/repository/human/production facet is `NOT_RUN`.
- `stage0_verification`: 397 v3+v4 contract tests, 218 JSON documents, 5 YAML documents, 15 Python files, 111 local Markdown links, SOUL sync and diff-check passed. The independent verifier reproduced and then confirmed closure of duplicate event/audit and same-record-identity equivocation attacks; remaining Stage 0 P0/P1 is zero.
- `next_action`: finish the complete Stage 1 public wire contract before any migration/runtime work. The first runtime slice is an authenticated maintainer report without trace that truthfully produces `UNKNOWN` evidence; Langfuse read and CaseLoop OTel write/readback follow after that slice.
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
