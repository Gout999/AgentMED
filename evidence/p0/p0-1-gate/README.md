# P0-1 Gate and Release Authorization Evidence

Verified on 2026-08-08 from takeover base
`89ea66750dfed3df6feeed493d9e2499ef77cdbe` on
`codex/p0-close-b1-loop`.

## What this evidence proves

- `gate.run` executes repository-owned contract/replay commands and the exact
  WorkOrder-bound candidate VersionSet. Empty suites, provider errors,
  evaluator deadlines, missing judge evidence, replay-as-live, and unavailable
  live checks resolve to `error`, never PASS.
- The deterministic control plane validates the GateReport schema and track
  conclusion, persists its canonical hash, and binds it to the final
  WorkOrder hash, target VersionSet digest/revision, dataset id/version/digest,
  regression suite digest, evidence manifest digest, and candidate digest.
- Release start and every Quality API write revalidate the immutable
  WorkOrder, initial ApprovalGrant, GateReport binding, target revision, nonce,
  and expiry. Canary, promote, and rollback each require a separate human R2
  ApprovalGrant bound to the exact action parameters and controller operation.
- Missing, failed, inconclusive/skipped, error, unknown, drifted, or tampered
  gate/receipt state fails closed. UNKNOWN is an authoritative Release state
  with a `release.unknown_detected` event and must reconcile before retry.
- Reconcile replays the original request with the original Idempotency-Key and
  remains UNKNOWN until `status.history` proves the exact Quality operation.
  Delayed application cannot strand local state outside UNKNOWN, and lifecycle
  events carry the real Quality operation id rather than the local controller id.
- The persisted post-canary GateReport is re-hashed and re-bound immediately
  before promote/rollback, including after the action grant was issued.
- Quality API operation acceptance and execution use locked CAS checks.
  Promote also binds the WorkOrder-approved active baseline digest and checks it
  inside the global PostgreSQL active-set lock. Of two candidates approved
  against the same baseline, exactly one operation succeeds; the other fails
  and remains canary.
- Console gate reads validate persisted GateReport integrity before projecting
  status; corruption appears as `integrity_error`/`verdict=error`, never raw
  PASS.

## Acceptance test mapping

| Required scenario | Executable proof |
|---|---|
| Normal pass | `eval-harness/tests/unit/test_gate.py::test_gate_all_pass`; `control-plane/tests/unit/test_gate_release_chain.py::test_exact_gate_target_allows_release_start` |
| Rule-track failure | `eval-harness/tests/unit/test_gate.py::test_gate_rule_track_fails_on_probe` |
| Judge-track failure | `eval-harness/tests/unit/test_gate.py::test_gate_judge_scores_recorded` |
| Evaluator timeout | `mcp-servers/tests/test_gate_flow.py::test_evaluator_timeout_is_persisted_as_error`; `eval-harness/tests/unit/test_gate.py::test_gate_judge_timeout_is_persistable_error` |
| WorkOrder hash mismatch | `control-plane/tests/unit/test_gate_release_chain.py::test_gate_workorder_hash_binding_mismatch_blocks_approval` |
| Missing GateReport | `control-plane/tests/unit/test_gate_release_chain.py::test_missing_gate_report_rejects_workorder` |
| Tampered persisted PASS | `control-plane/tests/unit/test_gate_release_chain.py::test_persisted_gate_report_tamper_fails_closed`; `control-plane/tests/unit/test_read_views.py::test_gates_fail_closed_when_persisted_report_is_tampered` |
| Post-canary report tamper | `control-plane/tests/unit/test_release_service.py::test_lifecycle_write_revalidates_post_canary_gate_after_action_approval` |
| Delayed UNKNOWN/reconcile | `control-plane/tests/unit/test_release_service.py::test_reconcile_keeps_delayed_promote_unknown_until_exact_operation_applies` |
| Remote receipt identity | `control-plane/tests/unit/test_release_service.py::test_reconcile_applied_promote_persists_promoted_receipt`; `test_reconcile_fails_closed_on_failed_operation_with_visible_transition` |
| Different-candidate concurrent promote | `demo-app/tests/integration/test_quality_concurrency.py::test_postgres_concurrent_promotes_across_candidates_preserve_single_active` |

## Verified commands and outcomes

- `control-plane`: explicit scratch PostgreSQL full suite — **161 passed**.
- `demo-app`: unit — **36 passed**.
- `demo-app`: explicit scratch PostgreSQL concurrency — **2 passed**.
- `eval-harness`: full suite — **69 passed, 4 live skipped**.
- `mcp-servers`: full suite — **54 passed, 1 live skipped**.
- Offline schema/Wilson contracts — **26 passed**.
- Console `npm run build` — passed.
- Python `compileall` for all Python services/tests — passed.
- Alembic `001 -> 002 -> 001 -> 002` round trip on the dedicated
  `control_plane_migration_test` database — passed;
  `gate_reports` and `controller_operations.approval_id` verified present.
- `docker compose ... config --quiet` with explicit non-secret test values — passed.
- Agent soul synchronization and `git diff --check` — passed.

## Live-provider report

No live-provider PASS is claimed. `STEPFUN_API_KEY` and `JUDGE_MODEL` are not
available, the Docker daemon is not running, and native PostgreSQL lacks
pgvector. The live Quality API conformance attempt therefore reported
**15 failed, 26 passed** (all 15 failures were connection refused to
`127.0.0.1:8080`). Demo live integration reported **6 failed, 2 skipped** for
the same unavailable service/credential prerequisites. Eval live tests were
reported separately as four skips.

The repeatable live gate command is:

```bash
cd eval-harness
.venv/bin/python scripts/run_gate.py \
  --versionset-id <release-controller-created-candidate> \
  --out-dir evidence/gate-<candidate-id>
```

It only uses Quality API read endpoints, does not follow the active pointer,
does not inject/reset faults, and exits nonzero unless every required track is
passed.
