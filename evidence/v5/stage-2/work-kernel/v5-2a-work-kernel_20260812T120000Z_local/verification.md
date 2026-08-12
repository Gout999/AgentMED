# V5-2A Work Kernel — Closure Verification

Run: `v5-2a-work-kernel_20260812T120000Z_local`
Subject: `e46d05a` on `codex/v5-2a-work-kernel` (semantic series `9fa5363` → `e46d05a`, base `92bde3c`)

## What was closed

Master §6 V5-2A (Durable Work Kernel), sub-packages 2A-0 through 2A-5:

| Sub-package | Commit | Content |
|---|---|---|
| 2A-0 contract + ownership freeze | `9fa5363` | D-016 adjudication (major-2 envelope + dedicated `v5.work.events` channel; V4 owners/state machines reused by reference); 27-event frozen catalog verified identical to V4 ownership lists; forbidden-writer matrix; all transports FORBIDDEN |
| 2A-1 schema + migration | `0c1528c` | Six tables (work_tasks, work_attempts, work_attempt_capabilities, work_proposals, work_proposal_decisions, work_reaction_ledger); migration 014; outbox check widened for the work channel; alembic check zero-drift |
| 2A-2 claim + fencing engine | `fa39646` | WorkKernelService: request/claim/heartbeat/start/output/complete/fail/unknown/reconcile/cancel + proposal submit/decide; one unit of work per command (projection + event + audit + receipt + outbox) |
| 2A-3 outbox + reaction ledger | `a626bd7` | WorkReactionDispatcher on `v5.work.events`: causal order, digest re-verify, tamper→DEAD, cancel reaction submits only `attempts.cancel`, redelivery idempotent |
| 2A-4 fixture executor | `8c5f328` | Deterministic executor (pure input→output), crash drills before/after claim and after output, mandatory rejections |
| 2A-5 closure | `e46d05a` | Real-PostgreSQL proofs below |

## Exit criteria (Master §6 2A-5) — verified on real PostgreSQL 18.4

1. **No double lease**: two workers racing one task → exactly one claim commits
   (attempt ×1, capability ×1, fence token 1); loser receives
   `v5.work.lease_held`.  Distinct tasks race cleanly (4/4).
2. **No ghost success**: `proposals.decide(accept=True)` without a downstream
   owner command is rejected (`v5.work.downstream_intent_required`); the
   proposal stays `SUBMITTED`, zero decision rows.  An accepted decision
   writes decision + reaction-ledger row + event + audit + receipt + outbox
   in one transaction.
3. **No ambiguous retry**: lease expiry under an active attempt fails closed
   into `UNKNOWN`/`BLOCKED_UNKNOWN`; re-claim is rejected with
   `reconcile_required`; only an explicit reconcile (terminal receipt digest)
   reopens the task, and the retry mints attempt 2 with fence token 2.

## Full gate result on this tree

- s1 compiler determinism: PASS (zero diff)
- s2 compiler tests: 18
- s3 conformance: 559 (557 + 2 new V5-2A guards)
- s4 control-plane unit + C1–C5 wave checkers: 1021 passed, 13 PG-gated skips
- s5 import-graph: PASS
- s6 CLI: 130
- s7 Console: 20 + build
- s8 disposable PostgreSQL: migration matrix 13 passed; integration matrix
  13 passed (lifecycle 3, R2/R3/R4 5, work-kernel closure 5)

## Facet report (honest)

`contract=PASS`, `replay=PASS` (deterministic fixture executor only).
All live/provider/Agent/human/production facets: **NOT_RUN** — no provider
keys exist on this machine, and Master §546 keeps Agent/provider liveness out
of V5-2A scope.

## Independent verifier pass and remediation

The post-commit independent verifier returned **FAIL (P0=1, P1=1)** on the
first series:

- **P0 (state machine)**: eight reachable kernel transitions escaped the
  verbatim-reused V4 machines (e.g. `start_attempt` skipped STARTING;
  fail/cancel/unknown accepted source states the V4 machine forbids;
  reconcile-exhaust jumped BLOCKED_UNKNOWN→EXHAUSTED directly).
- **P1 (evidence count)**: the unit count recorded mid-construction (1021)
  did not match the verifier's measured count.

Remediation commit `7ca8f80`: every transition now uses the V4-legal source
sets; `start_attempt` walks CREATED→STARTING→RUNNING with both events;
reconcile-exhaust walks WAITING_RETRY→EXHAUSTED as a second hop with its own
event; lease-expiry recovery splits never-started attempts (cancel hops — no
execution, no ambiguity) from started ones (UNKNOWN fail-closed); the
LEASED→LEASED re-claim hop is eliminated.  Root-cause guard added:
`test_v5_work_state_machine_conformance.py` replays every scenario's
persisted event stream through the frozen V4 machines and requires the walked
terminal state to equal the projection state — any future illegal hop fails
mechanically.  Post-remediation counts: control-plane unit + wave checkers
**1035 passed / 13 PG-gated skips**; work-kernel focused unit 35 passed; PG
closure 5/5; integration matrix 13/13; conformance 559; import-graph PASS.

## Known exclusion (pre-existing, not from this stage)

`tests/integration/test_v5_application_catalog_postgres.py` (s8 member):
**NOT_RUN** — it deadlocks on this machine via a leaked connection
(idle-in-transaction on `alembic_version`) against the fixture's
`DROP SCHEMA public CASCADE`.  Reproduced and root-caused independently of
V5-2A; tracked as `wiki/build-guide.md` G20.  It exercises the R2 catalog CLI
loopback, not the Work Kernel, so the 2A closure claims are unaffected.

## Environment

No Docker on this machine; disposable PostgreSQL 18.4 was provided via npm
`embedded-postgres` on 127.0.0.1:55432 (fsync off, scratch database
`control_plane_test`).  Python 3.13.13, Node 20.20.2.
