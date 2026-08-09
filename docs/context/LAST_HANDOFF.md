# Last Handoff

- `round_goal`: implement and prove the P0-4 B1 vertical loop without replacing
  live-provider evidence with mocks or hard-coded success.
- `actual_changes`: added authoritative complaint injection/dedup, frozen
  VersionSets and probes, deterministic attribution, immutable WorkOrder/Gate/
  Approval bindings, stage/canary/promote receipts, transactional closure,
  notification/outbox/Trust integration, complete manifest validation, fixed
  Phase 1 AgentTeams handoffs and attestation, separate replay/live commands,
  lease heartbeats, and official provider-origin checks before live Gate
  persistence. The replay produced a digest-verified Evidence Bundle. The live
  command produced a blocked preflight report and made no provider calls.
- `key_files`: `scripts/run_b1_replay.py`, `scripts/run_b1_live.py`,
  `scripts/validate_b1_run.py`, `control-plane/app/services/b1_orchestrator.py`,
  `control-plane/app/services/gate_service.py`,
  `eval-harness/eval_harness/gate.py`, `eval-harness/scripts/run_gate.py`,
  `mcp-servers/servers/eval_runner.py`, `contracts/b1-run-manifest.schema.json`,
  `Makefile`, and `evidence/p0/p0-4-b1/`.
- `test_results`: independent fake-success verifier PASS; control-plane 313 unit
  and 15 integration; demo 40 unit and 2 PostgreSQL concurrency; eval-harness
  76 passed/4 live-only skipped; MCP 119 passed/1 live-only skipped; contracts
  offline 28 passed. Clean committed-tree replay passed 28 contract and 28
  replay assertions, then independently validated 22 required artifacts and 135
  probe outputs. Full live probes retained 15 Quality and 6 demo connection
  failures. Live B1 preflight exited 2 with explicit missing inputs.
- `unfinished`: P0-4 acceptance remains BLOCKED. No live provider run exists,
  and interrupted live execution is not yet durably resumable/terminalized after
  Case creation or after promotion.
- `resume_from`: add authoritative recovery checkpoints and reconciliation for
  Case, Experiment, and Release; test interruption immediately after Case
  creation and after promotion; then supply the exact live preflight inputs and
  rerun `make demo-b1-live`.
- `commit_hash`: P0-4 implementation/proof
  `b5ef38fbc1a0da90bf766f0590763be5d1118e74`; P0-3 `a08c005`; P0-2
  `8e237e3`; P0-1 `4cd6e64`.
