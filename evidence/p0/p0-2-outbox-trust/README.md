# P0-2 Transactional Outbox, Trust, Notification, and Archive Evidence

Verified on 2026-08-08 on `codex/p0-close-b1-loop`, starting from the P0-1
commit `4cd6e64b2af21ccea83506d2f4cab687bb76eb76`.

## What this evidence proves

- The authoritative lowercase aggregate event and its critical uppercase
  integration event are written in the same database transaction. Every
  outbox message binds `source_event_id`, `event_type`, channel, exact JSON
  payload, and `payload_digest`.
- The frozen catalog covers `CASE_CREATED`, `ATTRIBUTION_DECIDED`,
  `GATE_COMPLETED`, `RELEASE_STARTED`, `RELEASE_PROMOTED`,
  `RELEASE_ROLLED_BACK`, `RELEASE_UNKNOWN`, `NOTIFICATION_SENT`, and
  `CASE_ARCHIVED`.
- Each delivery stores the source aggregate sequence. A later event for the
  same aggregate cannot be claimed while an earlier delivery is PENDING,
  PROCESSING, or DEAD. PostgreSQL and deterministic reverse-dispatch tests
  prove `RELEASE_PROMOTED` cannot overtake `RELEASE_UNKNOWN`.
- The fixed Phase-1 dispatcher claims work with a row lock,
  `FOR UPDATE SKIP LOCKED`, claim token, and lease. Delivery ACK, domain
  consumer effects, immutable receipt, and audit commit together. Unknown
  channels, mismatched digests/receipts, and audit outages cannot become SENT.
- Trust is authoritative in control-plane PostgreSQL. It consumes real
  Release Controller outcome events rather than a demo script. Its immutable
  entries are unique by source event and by release action, so retry and
  multiple probes in one action cannot inflate trials.
- A three-success sample remains MANUAL and denied: the Wilson two-sided 95%
  lower bound is about `0.438494`, below `0.9`. R2 remains per-action approval
  even with more evidence.
- `RELEASE_UNKNOWN` moves Trust to `BLOCKED_UNKNOWN`, records no success
  sample, and prevents a later outcome from silently clearing the block.
- Terminal Gate events now satisfy the frozen payload contract for
  `eval.passed`, `eval.failed`, and non-retryable `eval.error`; the exact
  `GATE_COMPLETED` envelope preserves the terminal event payload and binds its
  causation to the completed track event.
- A notification reaches SENT only through an adapter receipt bound to the
  exact outbox id and payload digest. The same transaction appends
  `notification.sent`, `case.closed`, `NOTIFICATION_SENT`, `CASE_ARCHIVED`,
  receipts, and audits. Invalid receipts dead-letter and escalate the Case.
- A provider-success/local-ACK-crash replay uses the same outbox id. The
  contract/replay Feishu mock returns the original receipt and does not perform
  a second send.
- The old unauthenticated/fake notification SENT endpoint is absent. The
  manual relay endpoint requires internal authority; production operation is
  the fixed dispatcher process.

## Acceptance test mapping

| Requirement | Executable proof |
|---|---|
| Nine critical event mappings and atomic outbox rows | `control-plane/tests/unit/test_outbox_dispatcher.py::test_required_domain_events_are_transactionally_enveloped` |
| Contract-aligned terminal Gate envelope and causation | `test_gate_completed_envelope_matches_terminal_event_contract` (passed/failed/error) |
| UNKNOWN cannot be overtaken on one aggregate | `test_same_aggregate_unknown_cannot_be_overtaken_by_later_promote`; `control-plane/tests/integration/test_outbox_concurrency.py::test_concurrent_dispatchers_preserve_unknown_before_promote` |
| Business/event/outbox rollback on audit failure | `test_audit_failure_rolls_back_case_event_and_outbox` |
| Retry plus one immutable receipt | `test_dispatcher_retries_without_duplicate_receipt` |
| Retry during Trust transaction cannot double-count | `test_trust_consumer_retry_rolls_back_first_attempt_and_counts_once` |
| 3/3 Wilson denial and schema-valid append-only entries | `test_release_events_feed_trust_once_and_three_of_three_is_denied` |
| One action/many probes is one sample | same test, including a second source for the same `action_ref` |
| UNKNOWN blocks Trust | `test_release_unknown_blocks_trust_and_later_outcome_fails_closed` |
| Notification receipt closes and archives | `test_notification_receipt_archives_case_and_emits_both_domain_events` |
| Provider success before local ACK | `test_provider_success_before_ack_is_retried_with_same_idempotency_key` |
| Invalid receipt fail-closed | `test_invalid_notification_receipt_dead_letters_and_escalates_case` |
| Generic close/fake sent path unavailable | `test_case_closed_cannot_be_forged_through_generic_transition`; `test_outbox_relay_requires_internal_authority_and_has_no_fake_sent_route` |
| Concurrent PostgreSQL claims do not duplicate Trust | `control-plane/tests/integration/test_outbox_concurrency.py` |
| Real ReleaseService promote/rollback feed Trust | `control-plane/tests/integration/test_acceptance.py::test_scenario_3_gray_release_promote_and_rollback` |

## Verified results

- Control-plane full unit/integration suite against explicit scratch
  PostgreSQL: **179 passed**.
- Demo application unit: **36 passed**.
- Demo PostgreSQL lifecycle concurrency: **2 passed**.
- Eval harness: **69 passed, 4 live-provider skipped**.
- MCP: **54 passed, 1 live smoke skipped**.
- Offline schema/Wilson contracts: **26 passed**.
- Aggregate executable tests: **366 passed, 5 live-only skipped**.
- Console production build: passed.
- Python compileall and `git diff --check`: passed.
- Compose configuration with explicit non-secret test values: passed.
- Alembic `002 -> 003 -> 002 -> 003` plus `alembic check`: passed on the dedicated
  `control_plane_migration_test` database. A pre-003 receiptless SENT row was
  verified to become `DEAD / LEGACY_UNATTRIBUTED` rather than remain a fake
  success.

JUnit reports generated by the focused P0-2 commands are stored alongside this
file. Live provider status is deliberately separated in
`live-provider-report.md`.
