# D-006: post-canary `error` is recorded and requires approved compensation

- Status: accepted
- Date: 2026-08-08

## Context

`GateReport.overall_status` permits `passed`, `failed`, and `error`, while the
release event contract previously permitted only `passed|failed` for post-canary
verification. A real evaluator timeout or provider failure therefore could not
be attached to the release. The release remained `CANARYING`, even though
promotion was no longer safe.

## Decision

The Release Controller records an integrity-checked post-canary `error` as
`release.verification_completed(result=error)`. It never treats `error` as a
test failure and never permits promotion. A rollback from this state requires a
new action-scoped R2 ApprovalGrant bound to the release, exact target revision,
verification report, reason, and rollback digest. The state-machine guard is
`verification=error` and the Quality API write remains exclusive to the Release
Controller.

Missing, malformed, or unbound GateReports remain non-actionable and fail
closed; they do not manufacture a verification result.

## Consequences

- Operators can safely compensate after infrastructure-indeterminate canary
  verification without relabelling it as a deterministic regression failure.
- Promotion still requires exactly `verification=passed`.
- Contract/replay tests cover the new transition separately from live-provider
  E2E.
