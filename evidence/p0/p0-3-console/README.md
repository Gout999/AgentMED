# P0-3 Authoritative Console Evidence

Verified on 2026-08-09 on `codex/p0-close-b1-loop`. The P0-3 base commit is
`02b97ddee82e53b5d2e62fababc841ac3d1acc35`; that commit contains collaborator
`origin/main` at `a6de5cc1a06d6967634676b2661da7d2e46d287b` through the non-rewriting merge
`4b5377dae317eaeadbf23ab85481881096b6d6d2`.

## What this evidence proves

- The production Console reads the existing T8 endpoints plus the existing
  Case, Experiment, Release, and Notification aggregate endpoints through the
  real `/api` proxy path. It has no production fixture or hard-coded green
  status.
- WorkOrder projection authority is the immutable `workorders` row. ChangeSet
  contributes lifecycle state only. WorkOrder identity/case/channel columns and
  JCS payload hash, GateReport schema/hash, WorkOrder binding, target revision,
  and binding digest are revalidated before a trustworthy state is shown. The
  read model does not return the complete WorkOrder payload.
- Corrupt WorkOrder or GateReport input projects as `integrity_error` /
  `UNKNOWN`. It does not expose corrupted Gate artifact refs and cannot be
  rendered as PASS.
- Evidence projection exposes only recorded refs and valid
  `sha256:<64-lowercase-hex>` digests. Domain IDs such as `complainant_ref` are
  not relabelled as evidence, and inline diff/content values are not returned.
  Artifact content remains explicitly `UNKNOWN` because no artifact
  fetch/verification service exists.
- Case events include sequence, actor, causation, correlation, trace, payload,
  and evidence refs. Case list rows include the aggregate title without
  client-side N+1 requests.
- Pages independently expose loading, empty, initial error with retry,
  background-refresh stale error, and backend `UNKNOWN`/partial states.
  Experiment confidence intervals remain `UNKNOWN` when required statistics
  are absent; Gate skipped/error/integrity states are never coerced to PASS.
  Valid reports without a final WorkOrder binding are explicitly `UNBOUND`,
  never an overall green result.
- Every API method applies a runtime response guard. Malformed objects fail as
  `invalid_response`; route/filter request keys abort and clear old data, and a
  deterministic race test proves a late prior-route response cannot replace
  the current route.
- Console remains read-only. It has no approval or Quality API write client, so
  Release Controller remains the only Quality write authority.

## Executable proof

| Requirement | Proof |
|---|---|
| Typed endpoint paths, runtime schemas, aborts, FastAPI errors, network/malformed response handling | `console/src/lib/api.test.ts` |
| Route-key reset and late-response race | `console/src/hooks/usePageData.test.tsx` |
| Immutable WorkOrder authority and tamper handling | `control-plane/tests/unit/test_read_views.py` WorkOrder integrity cases |
| Gate schema/hash/binding fail closed | `control-plane/tests/unit/test_read_views.py` Gate and Evidence integrity cases |
| Valid digest classification and domain-ref exclusion | `control-plane/tests/unit/test_read_views.py` Evidence classification cases |
| Case title list projection | `control-plane/tests/unit/test_case_service.py` |
| Real browser/backend/database path | `console/tests/integration/real-stack.spec.ts` through `console/scripts/run-real-stack-test.sh` |

The integration command starts a uniquely named scratch PostgreSQL database,
runs Alembic to head, starts real FastAPI and Vite processes, creates a
complaint through Vite `/api`, and loads its Case detail in headless Chromium.
It uses no route interception. The recorded Case was
`case_01KZGYT32G1NKH6KJ05548TYXK`; it then changed route in the same mounted
detail component to `case_01KZGYT3378AH57SV8045DW69N`. Chromium rendered both
real titles and the server-projected overview rows, plus the first Case's
`complaint.received` sequence/actor/text reference, and the API readback matched
the PostgreSQL-backed event. Cleanup verified both ports free and no P0-3
scratch databases remaining.

## Verified results

- Control-plane full suite on explicit test PostgreSQL: **190 passed**.
- Demo unit: **36 passed**; demo PostgreSQL lifecycle concurrency: **2 passed**.
- Eval harness: **69 passed, 4 live-provider tests skipped**.
- MCP servers: **54 passed, 1 live smoke skipped**.
- Offline schema/Wilson contracts: **26 passed**.
- Console API client/hook race tests: **7 passed**; real-stack browser integration: **1 passed**.
- Non-overlapping executable total: **385 passed, 0 failed, 5 live-only skipped**.
- Console TypeScript/production build, Python compileall, soul-sync, and
  `git diff --check`: passed.
- `npm audit --audit-level=high`: failed with 4 moderate and 1 high advisory.
  Clearing them requires breaking Router/Vite major upgrades; P0-3 retains the
  collaborator's React 18 / Router 6 / Vite 5 line and records the debt in
  `dependency-audit.md` rather than changing architecture under this closure.

## Stored artifacts

- `playwright-report.json`: machine-readable 1/1 pass report with attached
  authoritative Case JSON and browser screenshot.
- `playwright/real-stack-renders-a-real--6a74e-Vite-FastAPI-and-PostgreSQL/trace.zip`:
  Playwright trace, SHA-256
  `46f3876766b0974f14b1d8263a68e5265b5212c38e898bc977292db0849dbcfa`.
- `audit.jsonl`, `migration.log`, `control-plane.log`, and `vite.log`: exact
  real-stack run logs.
- `manual-case-detail.png`: manual real-stack browser QA screenshot, SHA-256
  `8f6f23e751d1f86fee7b22fb49e797c9fb3f81946a7bfc0c58a41ccd263c8077`.
- `manual-audit.jsonl`: audit emitted during the manual browser QA run.
- `verification-summary.json`, `verifier-report.md`, `live-provider-report.md`,
  and `dependency-audit.md`: classification and review records.
