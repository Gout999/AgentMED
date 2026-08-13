# V5-2B real shell-agent CLI Exit verification

- Run: `v5-2b-public-operations_20260813T022744Z_real_cli_exit_local`
- Semantic subject: `8c71d245137acf69a667104a0f3c833de9416bf9`
- Branch: `codex/v5-convergence`
- Verdict: local contract/replay **PASS**; real shell-capable Agent CLI Exit **PASS**; provider and production facets remain **NOT_RUN**.

## Exit journey

1. Started a live local control-plane HTTP process on disposable PostgreSQL 18.1, migrated to Alembic head `015`.
2. Seeded an isolated `external_agent` principal with only `investigations:start`, `operations:read`, and `operations:cancel`; the raw bearer was never persisted.
3. A separate CLI process invoked `case investigate` and received durable operation `op_01KZWF4TJFVZZ6QRE2M33G2F1Z`.
4. A second CLI process invoked `case investigate --follow` with the same idempotency key. Ctrl-C returned `detached=true` and the last observation remained `SUBMITTED`; no cancel request was emitted.
5. A local deterministic Work executor claimed and completed the same task with a receipt-bound `INVESTIGATION_REPORT` artifact.
6. A fresh CLI process invoked `operation follow <operation_id>`, then `operation get` and `operation list`; all observed the same operation as `COMPLETED` with the same artifact binding.
7. Repeating `case investigate` with the original idempotency key returned `replayed=true` and the original immutable admission receipt, while fresh reads reflected the current completed projection.
8. The isolated credential was revoked directly in the disposable database. A subsequent fresh CLI `operation get` failed closed with exit family `10` and `TOKEN_REVOKED`.

## Durable evidence

The final database query found exactly one AutomationRequest, one WorkTask and one
WorkAttempt for the journey. The WorkTask was `COMPLETED` at revision 3; the attempt
was `SUCCEEDED` at revision 6. Nine major-2 events, nine authority receipts and nine
audit rows covered the task/attempt transition chain. One public idempotency row bound
the start key to the same operation. The final public credential state was `REVOKED`.

The CLI response carried the exact current WorkTask and WorkAttempt bindings plus the
artifact digest `sha256:8945f5250ebe6c1fa57c77de6ef548f86a892789605f29633f0df5c4a2fc3507`.
The artifact payload explicitly records `provider_live=false`; this is local Agent/Work
causality evidence, not provider-live or production evidence.

## Preflight corrections

The first disposable seed was rejected as expected because its temporary `jti_digest`
used non-hex characters, and its fixed controller `valid_from` was in the future.
Both were corrected in the disposable harness only. No production code or fail-closed
authority check was weakened.

## Boundary decision

V5-2B's explicit Exit is now satisfied locally. V5-2B can be marked `DONE_LOCAL`.
V5-2C remains locked pending its own current protocol/version review and separate
Agent-native transport work. No MCP/A2A adapter, paid provider call, human approval,
production write, push or PR was performed.
