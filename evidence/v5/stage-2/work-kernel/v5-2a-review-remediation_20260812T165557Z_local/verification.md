# V5-2A review remediation verification

- Run: `v5-2a-review-remediation_20260812T165557Z_local`
- Reviewed candidate: `6b30a83` (`origin/codex/v5-2a-work-kernel`)
- Remediation subject: `3677be3a97daa1b28d2e4835b7665bd388449006`
- Local branch: `codex/v5-2a-review-remediation`
- Base: `92bde3c`
- GitHub state observed during review: no open pull request or CI run represented this branch; the review target was the remote branch head itself.

## Review verdict

The original candidate was **NO-GO**. It passed its recorded happy-path gate but failed adversarial review at authority and recovery boundaries. The local remediation subject is **GO for merge consideration** within V5-2A's `contract` and deterministic `replay` scope. It has not been pushed or merged.

Closed findings:

1. **P0 — cross-task attempt mutation**: cancel, UNKNOWN and reconcile accepted an `attempt_id` without proving it belonged to the requested Task. All attempt commands now bind workspace + Task + attempt, and recovery/cancel paths also require the current attempt.
2. **P0 — caller-asserted terminal receipt**: completion/reconcile trusted a non-empty digest. The controller now persists an exact worker/task/attempt/fence/outcome receipt first and accepts only that canonical digest.
3. **P0 — unbounded dispatcher failure**: `max_delivery_attempts` was unused; stale `PROCESSING` claims were never reclaimed; no retry/DLQ worker existed. The fixed worker now commits leased claims separately, reclaims expired claims, applies exponential backoff, terminates at `DEAD`, preserves aggregate order and uses a reaction ledger for redelivery idempotency.
4. **P1 — caller-controlled request fingerprint**: Work request idempotency now derives a canonical body binding server-side and serializes the PostgreSQL absent-row race with an advisory transaction lock.
5. **P1 — cross-task accepted Proposal**: completion/reconcile now verify Proposal Task and optional attempt bindings.
6. **P1 — mutable ACK only**: every Work outbox `SENT` transition now writes an immutable `OutboxDeliveryReceipt` and a receipt-bound audit in the same transaction.
7. **P1 — incomplete closure gate and stale status**: the unified s8 gate now includes `test_v5_work_kernel_postgres.py`; import-graph ownership and advisory-lock guards include the new worker/kernel call site.

Additional state repair: cancelling a `WAITING_RETRY` Task no longer leaves it stuck in `CANCEL_REQUESTED` or invent a second terminal transition for its already failed attempt.

## Verification

| Check | Result |
|---|---|
| Focused Work unit/state-machine/fixture/dispatcher | 47 passed |
| Work Kernel + reaction relay on disposable PostgreSQL | 8 passed |
| PostgreSQL migration matrix | 13 passed |
| Import-graph mechanical suite | 20 passed; checker PASS, 0 cycles, 100% lane coverage, 0 direct-table violations |
| Compose rendering with test-only placeholder secrets | PASS |
| Unified `scripts/verify_convergence.sh` | 8/8 sections PASS |
| Detached clean `3677be3` unified gate | 8/8 sections PASS; clean before/after |
| Unified control-plane section | 1047 passed, 13 explicitly PostgreSQL-gated skips |
| Unified PostgreSQL section | migration 13 passed; integration 17 passed, including 8 Work tests |

The first unified run after remediation correctly failed on two mechanical registration omissions: the new worker had no import lane, and the advisory-lock test still asserted the old count. Those were fixed and independently rerun before the two final 8/8 full-gate passes. No assertion was weakened and no test was deleted.

## Facet truth

- `contract=PASS`
- `replay=PASS` — deterministic fixture and local PostgreSQL control-plane paths only
- `domain-provider-live=NOT_RUN`
- `agentteams-native=NOT_RUN`
- `claude-runtime-live=NOT_RUN`
- `agent-causal=NOT_RUN`
- `repo-sandbox=NOT_RUN`
- `human-authorized-external=NOT_RUN`
- `production-canary=NOT_RUN`

No provider call, paid service, human approval, production write, push, PR creation or merge was performed.

Dependency observation outside the V5-2A diff: clean `npm ci` followed by
`npm audit --omit=dev` reported two moderate React Router advisories whose
available automated fix crosses to major version 7. `92bde3c..3677be3` does
not change `console/package.json` or `console/package-lock.json`; this is a
pre-existing non-blocking follow-up, not evidence against or for V5-2A.

## Workspace isolation

The remediation was constructed only in `/tmp/caseloop-v5-2a-review.G9m8Dj`.
The primary worktree `/Users/xiejiachen/caseloop-wt-v5-convergence` remained
clean at `codex/v5-convergence@92bde3c` throughout. A separate detached
checkout at `/tmp/caseloop-v5-2a-detached-3677be3` verified the exact committed
subject and remained Git-clean after the gate.
