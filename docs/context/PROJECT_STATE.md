# Project State

- `last_verified_commit`: `89ea66750dfed3df6feeed493d9e2499ef77cdbe`
- `active_branch`: `codex/p0-close-b1-loop`
- `origin/main commit`: `89ea66750dfed3df6feeed493d9e2499ef77cdbe`
- `working_tree`: P0-1 implementation is independently verified and ready for its semantic commit; P0-2 is the sole active work item
- `last_updated`: `2026-08-08T22:40:51+10:00`

## Verified completed capabilities

- P0-1 implementation now executes repository-owned contract/replay suites and evaluates the exact WorkOrder-bound VersionSet. The authoritative GateReport is schema/conclusion checked, canonically hashed, persisted, and sealed to the WorkOrder hash, target revision, dataset/version/digest, suite digest, and evidence digest.
- Release start and every Quality write revalidate WorkOrder, initial ApprovalGrant, GateReport, revision, nonce, and expiry. Canary/promote/rollback require distinct action-scoped R2 grants. Invalid/missing/tampered/unknown state fails closed.
- UNKNOWN is now an authoritative event-sourced Release state from every Quality write source state. Reconcile first replays the exact original idempotent operation and remains UNKNOWN until operation-bound `status.history` proves the result; delayed apply and receipt-identity regressions are covered.
- Post-canary GateReports are revalidated after action approval and immediately before promote/rollback. Quality lifecycle execution binds the WorkOrder-approved active baseline digest and checks it inside the global PostgreSQL lock; a two-candidate test proves one operation succeeds and the other remains canary.
- Gate read projections validate storage integrity; corruption is explicitly `integrity_error`, not PASS.
- `console` produces a successful production build.
- Phase 1 is documented as a fixed warm pool; no dynamic-scaling completion claim is made.
- Independent read-only P0-1 verification found no material blocker, hard-coded production PASS, empty-result success, exception fail-open, receipt misattribution, or post-approval GateReport bypass.

## Proven gaps

- Most domain transitions do not write an outbox event. The relay only logs and marks sent; Trust Ledger is disconnected and stored separately under MCP.
- Console does not consume the existing T8 gate/trust/workorder/event views and contains placeholder/static health data.
- No single reproducible command proves the entire B1 loop or creates the required final evidence bundle.
- Eval-harness B1 attribution still calls Quality write-side fault injection directly; this must move behind Release Controller ownership in P0-4.

## Work queue

- `current_only_in_progress`: P0-2 — connect transactional outbox, Trust Ledger, notification/archive, and audit.
- `next`: P0-3 — connect Console to the authoritative T8 read endpoints after P0-2 read models stabilize.
- Full acceptance criteria, dependencies, tests, evidence, and commits are tracked in `PLANS.md`.

## Actual runnable commands

```bash
git status
git branch -vv
git fetch --all --prune
git log --oneline --decorate -15 --all

cd control-plane
DATABASE_URL=postgresql+psycopg://gout@127.0.0.1:5432/control_plane_test .venv/bin/python -m pytest -q
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090

cd ../demo-app && .venv/bin/python -m pytest tests/unit -q
cd ../eval-harness && .venv/bin/python -m pytest -q
cd ../mcp-servers && .venv/bin/python -m pytest -q
cd ../contracts && ../eval-harness/.venv/bin/python -m pytest conformance/test_schemas.py conformance/test_wilson.py -q
cd ../demo-app && DEMO_TEST_DATABASE_URL=postgresql+psycopg://gout@127.0.0.1:5432/control_plane_test .venv/bin/python -m pytest tests/integration/test_quality_concurrency.py -q
cd ../console && npm run build
```

For container/live operation, use `docker compose -f deploy/compose.yaml ...` only after the Docker daemon is running. Demo/provider E2E additionally needs `STEPFUN_API_KEY`; Quality API and control-plane base URLs/tokens must match each component's `.env.example`.

## Latest test results

| Scope | Result | Classification |
|---|---|---|
| control-plane full against explicit scratch PostgreSQL | 161 passed | unit + integration |
| demo-app unit | 36 passed | offline unit |
| demo-app PostgreSQL lifecycle concurrency | 2 passed | same-target acceptance + different-candidate promote: one success, one fail closed |
| eval-harness full | 69 passed, 4 skipped | offline/replay pass; live skipped |
| MCP full | 54 passed, 1 skipped | offline/unit pass; live smoke skipped |
| contracts schema + Wilson | 26 passed | offline contract assets |
| Console `npm run build` | passed | production compile |
| agents soul-sync | passed | static validation |
| Python compileall | passed | all Python services, scripts, and tests |
| Alembic 001→002→001→002 | passed | dedicated `control_plane_migration_test` PostgreSQL round trip |
| Compose config | passed | explicit non-secret test values supplied |
| Quality API full conformance with app down | 26 passed, 15 failed | 15 live endpoint connection failures |
| demo-app integration with app down | 6 failed, 2 skipped | live endpoint connection failures; PG tests skipped without explicit URL in this command |

## External blockers and credentials

- Docker CLI is installed, but the Docker daemon is not running.
- `STEPFUN_API_KEY` and `JUDGE_MODEL` are unset, so live athlete/judge execution cannot be claimed. `CASELOOP_B1_FAULT_VERSIONSET_ID` is also absent; a live B1 fault candidate must be provisioned by Release Controller.
- Native PostgreSQL 16 is available, but pgvector is not installed; demo-app's native schema cannot currently be initialized there.
- These blockers do not prevent contract/replay implementation or P0-1 through P0-3 code and tests.
