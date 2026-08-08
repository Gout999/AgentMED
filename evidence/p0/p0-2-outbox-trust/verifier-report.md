# Independent Read-only Verifier Report

- Scope: P0-2 transactional outbox, Trust, notification/archive, audit, and
  critical `GATE_COMPLETED` projection
- Result: passed; no material P0-2 blocker remains
- Repository writes by verifier: none

## Executed proof

- Full control-plane suite: **179 passed**.
- Focused outbox, Gate, and notification tests: **35 passed**.
- PostgreSQL concurrent dispatcher and real Release-to-Trust tests:
  **3 passed**.
- Alembic: `003 (head)`; `alembic check` reported no new upgrade operations.
- `git diff --check`: passed.

## Material findings resolved before sign-off

1. `case.closed` now directly cites its `notification.sent` event, whose own
   causation is the canonical provider receipt digest.
2. `trust.promotion_denied` now directly cites the preceding
   `trust.evidence_recorded` event.
3. ORM indexes and migration 003 now describe the same constraints and
   indexes; autogenerate is clean.
4. Terminal `eval.passed`, `eval.failed`, and non-retryable `eval.error`
   payloads now contain their required contract fields and cause the exact
   `GATE_COMPLETED` envelope.
5. A reproduced two-worker race had allowed a later `RELEASE_PROMOTED` to
   overtake an earlier `RELEASE_UNKNOWN`. `source_event_seq` and the claim
   predecessor guard now preserve per-aggregate causal order. The PostgreSQL
   regression proves final Trust remains `BLOCKED_UNKNOWN` with 0/0 samples;
   the later promotion is DEAD rather than counted.

## Recorded nonblocking debt

The pre-existing non-terminal eval lifecycle events (`eval.requested`,
`eval.started`, and the two track-completed events) still do not populate every
field described by `contracts/events/events.yaml`. The critical terminal
projection is aligned and tested; the broader lifecycle mismatch remains
explicit debt and is not represented as completed.
