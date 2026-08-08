# CaseLoop Delivery Plan

Status values: `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`.

## Current milestone

Close the existing Phase 1 P0 gaps and produce a trustworthy, repeatable B1 vertical-loop proof. Scope expansion is deferred.

| Priority | Work item | Status | Dependencies | Acceptance criteria | Required tests | Evidence | Completion commit |
|---|---|---|---|---|---|---|---|
| P0-1 | Remove gate fake-pass and release bypasses | DONE | Existing gate schema, eval-harness, WorkOrder and Release Controller | GateReport comes from actual rule/judge/test execution; is persisted and bound to WorkOrder hash, target revision, evaluation dataset/version, and evidence digest; release rejects failed, inconclusive, error, unknown, missing, or mismatched reports | Pass; rule fail; judge fail; evaluator timeout; WorkOrder hash mismatch; missing/tampered GateReport; R2 action grant; UNKNOWN receipt accuracy; PostgreSQL same/different-candidate concurrency | `evidence/p0/p0-1-gate/` | P0-1 semantic commit (see branch log; hash recorded after commit) |
| P0-2 | Connect transactional outbox, Trust Ledger, notification, archive, and audit | IN_PROGRESS | P0-1 authoritative release outcome | Required domain events enter outbox in the business transaction; dispatcher is retry-safe; Trust consumes real outcomes exactly once per action; 3/3 Wilson lower bound denies promotion; audit failures roll back | Event coverage; dispatcher retry/duplicate; probe coalescing; Trust 3/3 denial; audit failure; integration/replay | `evidence/p0/p0-2-outbox-trust/` | pending |
| P0-3 | Wire Console to real T8 read endpoints | TODO | Stable control-plane read models from P0-1/P0-2 | Production UI reads Case, Experiment, Gate, Release, Notification, Trust, Evidence; no static success fixture; contract-aligned states; loading/empty/error/retry/partial/UNKNOWN | API client tests; at least one frontend/backend integration; build | `evidence/p0/p0-3-console/` | pending |
| P0-4 | Repeatable B1 end-to-end loop and Evidence Bundle | TODO | P0-1 through P0-3 | B1 traverses injection, dedup, frozen protocol, attribution=prompt, immutable WorkOrder, real gate, approval binding, stage/canary and promote or rollback, contract-compatible notification, archive, Trust denial, complete Evidence Bundle | `demo-b1-replay`; live command separately; contract/replay; live-provider report; end-to-end assertions | `evidence/p0/p0-4-b1/` | pending |
| P1 | Deferred non-P0 improvements | TODO | All P0 items | Only plan after B1 proof is complete; no Phase 1 dynamic-scaling claim | To be derived from verified code | pending | pending |

## Verified takeover baseline

- `main`, `origin/main`, and takeover HEAD were all `89ea66750dfed3df6feeed493d9e2499ef77cdbe` after `git fetch --all --prune` on 2026-08-08.
- All origin feature branches were already merged into `origin/main` by ancestry; no unpublished local collaborator branch was modified.
- P0-1 passed independent read-only verification with no material blocker. Current verification: control-plane 161 passed; demo unit 36 passed; demo PostgreSQL concurrency 2 passed (one concurrent promote succeeds, the stale-baseline operation fails); eval-harness 69 passed and 4 live-provider skipped; MCP 54 passed and 1 skipped; contract schema/Wilson 26 passed; Console production build, compose config, migrations, compileall, and soul-sync passed.
- Unavailable-service probes were retained as failures: Quality API contract 15 connection failures, demo integration 6 connection failures, MCP smoke 4 passed/16 failed with control-plane/default database unavailable.
- Live prerequisites currently absent: Docker daemon, `STEPFUN_API_KEY`, `JUDGE_MODEL`, and a native PostgreSQL `vector` extension. Live Quality conformance remains 15 connection failures/26 offline passes; demo live integration remains 6 connection failures/2 skips.
