# D-012 — v3/v4 Cutover and Compatibility

Status: **ACCEPTED**
Date: 2026-08-10

## Decision

v4 is introduced by expand-and-route, not by rewriting v3 tables, seven state machines or historical evidence in place.

1. v3 `plan-v3.md`, contracts, migrations and tests remain the compatibility baseline for the customer-service Scenario.
2. v4 schemas and events use `contracts/v4/`, explicit major/schema/event versions and globally prefixed identifiers.
3. Stage 0 performs no database migration.
4. Stage 1 adds new Signal/TraceSource tables without changing v3 inbox/complaint/lease semantics.
5. Stage 2 adds WorkerTask/Attempt leases. A task is routed to exactly one lease authority; v3 Case lease and v4 WorkerTask lease can never both authorize the same work.
6. In-flight v3 work is drained, explicitly terminalized, or handled by a tested reconciler before routing that workload to v4.
7. Rollback disables v4 routing/workers and preserves append-only v4 records; it never backfills v4 results into v3 as invented success.

The known v3 runner recovery debt is an explicit cutover gate, not a note: before a v3 Scenario workload can reuse the v4 Candidate path or reach a v4 external operation, every earlier run for that routing key must be provably terminal, or a tested reconciler must recover/terminalize it after crashes at Case creation, promotion and notification. Until that gate passes, the workload remains v3/Shadow-only and no v4 WorkerTask lease is issued. A cutover receipt binds the exact route version, routing key, prior lease authority, new lease authority and reconciliation evidence digest.

## Compatibility contract

- public HTTP uses a major version and payload `schema_version`;
- events include `event_version` and additive-within-major rules;
- CLI and server negotiate supported versions;
- adapters publish capability and failure matrices;
- N/N-1 conformance runs before a release drops compatibility;
- migrations use expand → backfill → cutover → contract, with backup/restore evidence where downgrade is impossible.
- conformance must inject crashes before and after each authority handoff and prove that no routing key ever has both an active v3 Case lease and v4 WorkerTask lease.

## Historical evidence

Historical provider/domain live evidence remains historical evidence. It is neither erased nor upgraded to a new `agent-causal` contract. A new run is required only when changed code, provider path, acceptance contract or reproducibility need affects the claim.

`scripts/b1_live/agent_trace.py` is treated as a replay/platform fixture or quarantined until rewritten as a read-only exporter. Its self-created task/ack/submit artifacts cannot satisfy v4 agent-causal acceptance.
