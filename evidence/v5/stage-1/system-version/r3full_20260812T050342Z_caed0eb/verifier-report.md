# Independent verifier report — V5-R3-full gate, remediation re-review

- Verifier: `Kimi Code independent sub-agent (V5-R3-full gate POST_COMMIT_VERIFY, remediation 18482f8 re-review)`
- Subject: `18482f87665ae344701e8c6a3a254eebfcf6177a` (`18482f8 test(control-plane): resolve R3 verifier P1 findings`)
- Direct parent: `caed0ebfeb527dfc1b616745bb51e2fc2521a272` (`caed0eb test(control-plane): advance integration head assertions to 013`)
- Cumulative base (R3-full): `f5fce73` (unchanged from the prior 33ea7e6/caed0eb verification)
- Worktree: `/private/tmp/caseloop-r3-verify.K0Q6g6/checkout` — detached at `18482f8`, `git status` clean except the untracked evidence bundle (allowed)
- Verdict: **PASS**
- P0: `0`
- P1: `0` (all 8 P1 findings from the three prior verifiers are closed or recorded as accepted observations; remediation introduces no new P0/P1)

## Summary

The remediation commit `18482f8` is a 10-file delta over `caed0eb` (+223/−39) that resolves the
8 P1 findings raised by the three independent verifiers on `33ea7e6`/`caed0eb` (P0=0, P1=8):
two runtime fail-open gaps are fixed in the service with two new pinning unit tests, four
journey coverage gaps are closed (stale head assertion, installed-CLI e2e for the R3 intents,
unknown-commit-outcome test, strengthened diff assertions), the R2-era digest-replay test
expectation is corrected to the frozen contract, three stage1a/case-binding PG suites advance
their head assertion to 013, and the `case from-issue` CLI test is skipped with an explicit R4
reason. The two observation items (runtime-P1-3, contracts-P1-1) are recorded, not fixed, per
the remediation brief. The diff touches exactly the 10 files implied by the P1 findings — no
unrelated changes. Every re-run gate is green.

## P1 closure confirmations (8/8)

### runtime-P1-1 — `_require_environment` must require `lifecycle_state == "ACTIVE"` — CLOSED

`control-plane/app/services/system_versions.py:2780-2792`: the environment guard now rejects
`environment.lifecycle_state != "ACTIVE"` (fail-closed, same opaque reason
`ENVIRONMENT_UNKNOWN_OR_CROSS_APPLICATION` as the unknown/cross-workspace/cross-application
cases). Pinned by the new unit test `test_record_rejects_non_active_environment`
(`tests/unit/test_v5_system_versions_record.py`), which flips the persisted row to `RETIRED` via
raw SQL (ORM immutability guard noted) and asserts `VALIDATION_FAILED` +
`ENVIRONMENT_UNKNOWN_OR_CROSS_APPLICATION`. The test passes (re-run below). `RETIRED` is a
legal `Environment.lifecycle_state` value (`ck_environment_lifecycle` allows `ACTIVE`/`RETIRED`),
so the check is meaningful, not dead code.

### runtime-P1-2 — cross-application same-workspace bindings must be rejected — CLOSED

Both validators now take the request `application_id` and compare `row.application_id` against
it (`system_versions.py:2793-2836` component revision bindings → `REQUEST_INVALID`
`UNKNOWN_COMPONENT_REVISION_BINDING`; `:2844-2862` topology binding →
`UNKNOWN_TOPOLOGY_REVISION_BINDING`). Both `ComponentRevision` and `TopologyRevision` carry a
persisted `application_id` column (verified in `app/models/v5_tables.py`), so the comparison is
against real data. Each validator has exactly one caller (`record_system_version`, updated at
`:2471`/`:2474`), so the signature change is complete with no stale call sites. Pinned by the new
unit test `test_record_rejects_cross_application_component_revision`, which passes a foreign
`app_01J0000000000ZZZZ` id and asserts the rejection. Passes (re-run below).

### runtime-P1-3 — get/diff edge-level recursion depth — RECORDED, not fixed (accepted)

No change, as briefed: the frozen D2 profile's `recursive_verification_layers` does not
enumerate dependency-edge rows and there is no D2 adversarial case for edge tamper; the import
path already does per-edge verification. This is a documented depth observation, not a
correctness defect (tampered edges do not alter get/diff output, which reads the pinned bindings
inside the topology row). Accepted as an observation.

### journey-P1-1 — stale head assertion + CLI command naming in `test_v5_system_versions_postgres.py` — CLOSED

- Head assertion `version_num == "010"` advanced to `"013"` (`test_v5_system_versions_postgres.py:315`), matching the other R2-era PG suites and the R3 head (the R3 PG journey already asserts 013 after `alembic upgrade head`).
- The installed-CLI leg now uses the real command names: `system-version get --system-version-set-id` and `system-version record` (replacing the old `system-manifest get/diff` invocations). The `system-version` record/get/diff subparsers exist in `cli/src/caseloop_cli/main.py:227-229` and the `system-versions.diff` path is exercised at MockTransport level in `cli/tests/test_cli_surface.py:785` (`/api/v2/system-versions:diff`). Both tests in this file now pass.

### journey-P1-2 — installed-CLI e2e leg for the R3 intents — CLOSED

`test_v5_1b_cli_init_and_manifest_import_e2e` now runs the full installed-CLI + real server +
PG flow: git-workload discovery via `caseloop init` (local command, exit 0, emits `_discovery`
with `APPLICATION_CODE` components) → `system-manifest import` (BOOTSTRAP, generation 1) →
`system-version get` (id round-trip) → `system-version record` with a deliberately stale
`exact-previous-version-set` binding (wrong digest), which the server rejects fail-closed with
`expected_exit=12` (= `ExitFamily.CONFLICT`, verified: 409 → CONFLICT in `cli/src/caseloop_cli/client.py`) and error code `CATALOG_CONFLICT`. Supporting fixes: the seeded bearer token now carries the `system_versions:record` scope (`IMPORT_SCOPES`), the catalog binding is granted `trust_roles=["integrator"]` (matching the record trust role), and the seed is committed with an explicit `session.commit()` after the create_all guard, so the CLI record request is authorized against persisted rows. The CLI `init` implementation is a local-only discovery command in `cli/src/caseloop_cli/main.py:410-416` (no API URL/credential/HTTP dependency); the CLI surface test was updated accordingly (`test_r3_init_is_local_and_validate_remains_available` asserts exit 0 + zero HTTP requests + `_discovery` payload, and `test_help_exposes_only_frozen_stage1a_cli_commands` now allows `init`).

### journey-P1-3 — unknown-commit-outcome focused test — CLOSED

New unit test `test_record_unknown_commit_outcome_never_forges_duplicate`
(`tests/unit/test_v5_system_versions_record.py`): seeds a `PENDING` idempotency row with a
matching fingerprint, then records → asserts `DEPENDENCY_UNAVAILABLE` and zero new
`SystemVersionSet` rows (count stays 1). This pins the fail-closed semantics of
`public_idempotency.py` for the system-versions.record path. Passes (re-run below).

### journey-P1-4 — journey diff assertions strengthened — CLOSED

`test_v5_system_versions_r3_postgres.py` diff block now pins: `removed` exactly equals the
single dropped component-revision binding id (`removed_ids == {import_body...bindings[1].id}`),
`added == []`, `changed == []`, `topology_changes == []`, and
`assurance_delta.identity_assurance_changes` has exactly one entry per removed component with
`-> None` suffix (the removed component's identity assurance disappears with it) — matching the
subset-record semantics (shared component revisions + same topology binding). These assertions
would now catch a regression that silently drops or garbles any diff section.

### contracts-P1-1 — `r2_wire_profiles/capabilities_get` allowlist 11→14 — RECORDED, not fixed (accepted)

No change, as briefed: extending `capabilities_get.contract_allowlist_exact_count` 11→14 is a
necessary consequence of enabling capability discovery for R3-full (the three system-versions
intents must be advertised), and the R2 conformance test asserts exactly `11 + 3 = 14` while the
authoritative R2 overlay `activated_contract_intents` stays 11. This is a deliberate, documented
activation change, not a functional defect. Accepted as an observation.

## Extra verification notes (R2-era fixes in the remediation)

- **Digest-replay semantics corrected**: `test_v5_1b_pg_import_atomic_rollback_and_replay` no
  longer expects a same-digest/different-key replay of the same version set; it now asserts the
  frozen contract's `CATALOG_CONFLICT` for `different_key_even_same_manifest_digest` (replay is
  bound to (key, body)), then rolls back. This aligns the test with the contract rather than
  weakening any assertion.
- **Head assertion cleanup completed**: stage1a bootstrap / public-CLI / signal-concurrency
  (3 tests) and case-binding acceptance (1 test) all advance `version_num == "010"` → `"013"`.
  The case-binding PG seed additionally grants `trust_roles=["integrator"]` to the binder
  catalog binding.
- **`case from-issue` CLI e2e skipped with reason**: `@pytest.mark.skip` documents that V5-1C
  `case from-issue` activates in R4; until then the CLI fail-closes with `CLI_USAGE_INVALID`.
  The suite re-run below shows exactly this 1 skip and no error.

## Remediation diff scope

`git diff caed0eb 18482f8 --stat` → exactly the 10 files implied by the P1 findings:

```
 cli/src/caseloop_cli/main.py                       |  12 +++
 cli/tests/test_cli_surface.py                      |  17 ++--
 control-plane/app/services/system_versions.py      |  23 +++--
 control-plane/tests/integration/test_stage1a_bootstrap_postgres.py          |  2 +-
 control-plane/tests/integration/test_stage1a_public_cli_postgres.py         |  2 +-
 control-plane/tests/integration/test_stage1a_signal_concurrency_postgres.py |  2 +-
 control-plane/tests/integration/test_v5_case_binding_acceptance_postgres.py |  7 +-
 control-plane/tests/integration/test_v5_system_versions_postgres.py         | 85 ++++++++++++++-----
 control-plane/tests/integration/test_v5_system_versions_r3_postgres.py      | 13 ++-
 control-plane/tests/unit/test_v5_system_versions_record.py                  | 99 ++++++++++++++++++++++
 10 files changed, 223 insertions(+), 39 deletions(-)
```

No contracts, generated artifacts, migrations, or unrelated files were touched. The two
observation items (runtime-P1-3, contracts-P1-1) correctly have no code delta.

## Re-run results (actual, at 18482f8)

| Gate | Command (run from the checkout) | Result |
|---|---|---|
| Worktree state | `git rev-parse HEAD` → `18482f87665ae344701e8c6a3a254eebfcf6177a`; `HEAD~1` → `caed0eb`; `git status --porcelain` | clean + untracked evidence bundle |
| Compiler emit determinism | `cd contracts/compiler && PYTHONPATH=.. python3 -m compiler emit && git -C .. diff --exit-code -- v5/generated/` | exit 0, 4 artifacts; **zero diff** |
| Contracts conformance | `cd contracts && /Users/xiejiachen/caseloop/eval-harness/.venv/bin/python -m pytest conformance/test_schemas.py conformance/test_wilson.py conformance/test_v4_*.py conformance/test_v5_*.py -q` | **557 passed** in 13.09s |
| Control-plane unit + C1–C5 | `cd control-plane && env -u CASELOOP_ALLOW_INTEGRATION_RESET -u DATABASE_URL /Users/xiejiachen/caseloop/control-plane/.venv/bin/python -m pytest tests/unit tests/test_v5_c1_shadow_parity.py tests/test_v5_c2_foundation.py tests/test_v5_c2_graph.py tests/test_v5_c3_import_graph.py tests/test_v5_c4_allowlist_diff.py tests/test_v5_c4_fallback_drill.py tests/test_v5_c5_rollback_drill.py -q` | **995 passed, 12 skipped** in 35.01s (skips = documented disposable-PG reset opt-in) |
| New P1-pinning unit tests | `pytest tests/unit/test_v5_system_versions_record.py -q -k "non_active or cross_application or unknown_commit"` | **3 passed** |
| CLI suite | `cd cli && /Users/xiejiachen/caseloop/control-plane/.venv/bin/python -m pytest tests -q` | **120 passed** in 1.01s |
| Control-plane integration (PG) | `cd control-plane && env CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test /Users/xiejiachen/caseloop/control-plane/.venv/bin/python -m pytest tests/integration/ -q` | **30 passed, 1 skipped** in 29.48s (skip = `case from-issue` CLI, R4 scope, reason verified) |

The PG integration run covers the two previously-red R2-era tests
(`test_v5_1b_pg_import_atomic_rollback_and_replay`,
`test_v5_1b_cli_init_and_manifest_import_e2e`) — both now pass on real PostgreSQL at head 013
with the corrected digest-replay contract and the installed-CLI record/get/init journey.

## Conclusion

The remediation `18482f8` resolves every actionable P1 (6 fixed + verified by focused tests, 2
recorded as accepted observations) with a tightly scoped 10-file delta and introduces no new
P0/P1. All specified re-run gates are green (compiler emit zero-diff, conformance 557, unit+C1–C5
995, CLI 120, PG integration 30+1skip). No assertions were weakened or tests deleted; the
digest-replay expectation change aligns the test with the frozen contract. Verifier grants
**PASS (P0=0, P1=0) for the remediation gate**. Live/Agent/external/production facets remain
`NOT_RUN` per §17.4.
