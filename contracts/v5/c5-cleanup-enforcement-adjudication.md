# C5 cleanup/enforcement adjudication inputs

C5 removed the compatibility duplication proven obsolete by C4, made the
shadow-only boundary rules effective, and added recovery verification. This
file records the retention decisions and the honest verification boundary.

## 1. Removed (reachability + compatibility proof)

- `cli/src/caseloop_cli/_generated/case_v2.py` (199 LOC): zero importers in the
  whole repo and in git history since `c838b2b`; the 11-intent manifest has no
  case/acceptance operations; CLI tests pin those paths as unreachable.
- `cli/src/caseloop_cli/main.py` init/from-issue dead block (~351 LOC
  incl. `_cmd_init`, `_cmd_case_from_issue`, issue helpers, `_canonical_digest`,
  dead imports `urllib.request`/`discover`/`hashlib`/`rfc8785`, and
  `_IDS["binding"]`/`_IDS["acceptance_revision"]`): never registered in
  `build_parser`, never dispatched in `run`; tests pin the hidden surface.
  `discovery.py` was retained because `cli/tests/test_discovery.py` imports it.
- `application_catalog._MUTATION_INTENTS` (zero references).
- `db.session_scope` (zero control-plane callers; mcp-servers use their own
  `common/db.py`). This resolves the C3 adjudication entry that listed it.

Retained with justification: the seven `_unregistered_*` handlers in
`public_v5.py` (reserved R3-full/R4 transport shells whose services are
directly tested; deletion lacks a C4-parity "obsolete" proof and would force
R4 to rewrite the wire surface), the live `_generated/` response models
(public_v1/public_error_v1/public_v2/manifest_v2 — runtime pydantic authority
per C4 §1), and the `_SPECS` catalog entries (the lookup table is live;
per-kind unreachability was not proven, so no removal by count).

## 2. Enforcement is now effective

- `install_route_manifest_check` is fail-closed at import time: a manifest↔route
  mismatch or unavailable manifest aborts the `public_v5` import
  (`RouteManifestMismatchError` / `V5CapabilitiesManifestError` propagate).
  The two tests that asserted the old log-and-keep-serving fallback were
  rewritten to assert fail-closed; the C4 fallback drill now verifies that the
  gate itself does not mutate the router table while remaining strict.
- `scripts/verify_convergence.sh` (+ `make check-v5`) is the unified C5
  enforcement entry: compiler determinism (re-emit must be byte-identical),
  compiler tests, conformance (incl. `test_v5_*`), control-plane unit + the six
  wave checkers, the import-graph checker, and the CLI suite.
- The stale `TODO C4` text in the OpenAPI emitter (and in the generated
  `openapi.yaml` description and the pinned test) was replaced with the C4
  decision wording; regeneration is deterministic.

## 3. Recovery verification

- New `test_v5_c5_rollback_drill.py`: there is no V5 kill-switch today, so the
  drill constructs an app without the v5 router and asserts: V1 capabilities
  and error-envelope bytes identical (masked per-request `audit_ref`/
  `generated_at`, per the C4 drill convention), the disabled construction
  actually removes the v5 surface (baseline 401 vs disabled 404), v5 write
  paths produce zero outbox/event rows and return standard 404 bodies, and the
  alembic head stays `012`.
- New `docs/plans/v5-migration-recovery.md`: documents the recovery path for
  the 011/012 legacy-V5-history preflight failures (export → digest/shape
  verify → audited replay → re-run preflight → upgrade), roll-forward-first,
  and the downgrade-blocked disposition. This closes the "error code without a
  recovery guide" gap.

## 4. Honest verification boundary

- The 12 PG-gated migration tests and the PG integration suites require a
  disposable PostgreSQL (compose in `deploy/`); without one they remain
  `NOT_RUN` (never described as PASS). Unit-level migration tests (26 cases,
  SQLite) are green.
- The corpus covers the structural wire space; the divergences recorded in
  `contracts/v5/c1-shadow-findings.md` remain the C4/final-closure
  adjudication set.

## Verdict

Zero P0/P1. No stop condition triggered: removal was reachability- and
compatibility-proven, not count-based; enforcement is effective and has a
single reproducible entry point; recovery drill and migration recovery guide
exist; module graph/compiler output/ownership checks pass from a clean
checkout; V3/V4 compatibility and the V5 contract/replay suites pass;
status files agree that D2 stays locked until this wave's gate review passes.
The architecture-convergence series C0–C5 is complete.
