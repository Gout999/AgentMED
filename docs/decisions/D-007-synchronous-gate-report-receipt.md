# D-007: do not synthesize asynchronous Gate lifecycle events

- Status: accepted
- Date: 2026-08-08

## Context

Phase 1 runs Gate evaluation synchronously in a fixed warm-pool MCP runner. The
Control Plane receives the completed immutable GateReport only after the real
rule, judge, contract/replay, and live-provider tracks return. The previous
registration path manufactured `eval.started` and per-track completion events
at import time, including lease fields that the Control Plane had never issued.
Those events did not satisfy the event contract and overstated the available
execution history.

## Decision

Keep the asynchronous lifecycle contract for a future controller-issued runner
lease, but do not emit those events from the synchronous Phase 1 path. The
Control Plane now records:

1. the actual registration request as `eval.requested`;
2. one `eval.report_received(execution_mode=completed_report_import)` receipt
   containing the immutable report/candidate hashes, target digests, track
   statuses and deterministic counts;
3. the validated terminal `eval.passed|failed|error` event; and
4. `eval.bound` only after the exact WorkOrder hash is known.

The report remains subject to full schema, evidence, Quality log, target
revision, judge/athlete separation, and WorkOrder binding validation before it
can authorize release.

## Consequences

- Evidence no longer claims lease/start/track receipts that were not observed.
- Dynamic runner scheduling or scaling is not claimed for Phase 1.
- A future asynchronous runner must use controller-issued leases and emit the
  existing progressive lifecycle events rather than this import transition.
