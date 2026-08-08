# CaseLoop Delivery Plan

Status values: `TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`.

## Current milestone

Close the existing Phase 1 P0 gaps and produce a trustworthy, repeatable B1 vertical-loop proof. Scope expansion is deferred.

| Priority | Work item | Status | Dependencies | Acceptance criteria | Required tests | Evidence | Completion commit |
|---|---|---|---|---|---|---|---|
| P0-1 | Remove gate fake-pass and release bypasses | DONE | Existing gate schema, eval-harness, WorkOrder and Release Controller | GateReport comes from actual rule/judge/test execution; is persisted and bound to WorkOrder hash, target revision, evaluation dataset/version, and evidence digest; release rejects failed, inconclusive, error, unknown, missing, or mismatched reports | Pass; rule fail; judge fail; evaluator timeout; WorkOrder hash mismatch; missing/tampered GateReport; R2 action grant; UNKNOWN receipt accuracy; PostgreSQL same/different-candidate concurrency | `evidence/p0/p0-1-gate/` | `4cd6e64b2af21ccea83506d2f4cab687bb76eb76` |
| P0-2 | Connect transactional outbox, Trust Ledger, notification, archive, and audit | DONE | P0-1 authoritative release outcome | Required domain events enter outbox in the business transaction and preserve per-aggregate causal order; dispatcher is retry-safe; Trust consumes real outcomes exactly once per action; 3/3 Wilson lower bound denies promotion; audit failures roll back | Event coverage; Gate terminal contract; dispatcher retry/duplicate/order race; probe coalescing; Trust 3/3 denial; audit failure; integration/replay | `evidence/p0/p0-2-outbox-trust/` | `8e237e30dbbda7e05d78656047efd4c3d0703653` |
| P0-3 | Wire Console to real T8 read endpoints | DONE | Stable control-plane read models from P0-1/P0-2 | Production UI reads Case, Experiment, Gate, Release, Notification, Trust, Evidence; no static success fixture; contract-aligned states; loading/empty/error/retry/partial/UNKNOWN | API runtime-guard and request-race tests; backend read-model integrity tests; real PostgreSQL/FastAPI/Vite/Chromium integration; build | `evidence/p0/p0-3-console/` | pending semantic commit |
| P0-4 | Repeatable B1 end-to-end loop and Evidence Bundle | IN_PROGRESS | P0-1 through P0-3 | B1 traverses injection, dedup, frozen protocol, attribution=prompt, immutable WorkOrder, real gate, approval binding, stage/canary and promote or rollback, contract-compatible notification, archive, Trust denial, complete Evidence Bundle | `demo-b1-replay`; live command separately; contract/replay; live-provider report; end-to-end assertions | `evidence/p0/p0-4-b1/` | pending |
| P1 | Deferred non-P0 improvements | TODO | All P0 items | Only plan after B1 proof is complete; no Phase 1 dynamic-scaling claim | To be derived from verified code | pending | pending |
| P1 | Align non-terminal eval lifecycle events with the frozen event contract | TODO | Explicit runner/lease/fencing metadata at Gate execution boundary | `eval.requested`, `eval.started`, and both track-completed events carry every contract-required field and immediate causation; no invented runner metadata | Runtime event-contract validation for the four event types | pending | pending |

## Verified takeover baseline

- `main`, `origin/main`, and takeover HEAD were all `89ea66750dfed3df6feeed493d9e2499ef77cdbe` after the initial `git fetch --all --prune` on 2026-08-08. During P0-2, `origin/main` advanced to `a6de5cc1a06d6967634676b2661da7d2e46d287b`; it was merged without conflict or history rewriting at `4b5377dae317eaeadbf23ab85481881096b6d6d2`.
- All origin feature branches were already merged into `origin/main` by ancestry; no unpublished local collaborator branch was modified.
- P0-1 passed independent read-only verification and is committed at `4cd6e64`. P0-2 passed independent read-only verification at `8e237e3`. P0-3 also passed independent verification: control-plane 190; demo unit 36; demo PostgreSQL concurrency 2; eval-harness 69/4 live-only skipped; MCP 54/1 live-only skipped; schema/Wilson 26; Console 7; real-stack browser 1. The current non-overlapping proof totals 385 passed, 0 failed, and 5 explicitly live-only skipped.
- Unavailable-service probes were retained as failures: Quality API contract 15 connection failures, demo integration 6 connection failures, MCP smoke 4 passed/16 failed with control-plane/default database unavailable.
- Live prerequisites currently absent: Docker daemon, `STEPFUN_API_KEY`, `JUDGE_MODEL`, and a native PostgreSQL `vector` extension. Live Quality conformance remains 15 connection failures/26 offline passes; demo live integration remains 6 connection failures/2 skips.
