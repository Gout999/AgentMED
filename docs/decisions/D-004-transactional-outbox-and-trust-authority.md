# D-004: Transactional Outbox and Trust Authority

- Status: Accepted
- Date: 2026-08-08
- Scope: Phase 1 P0-2

## Context

`docs/plan-v3.md` makes the deterministic control plane authoritative for
state, idempotency, audit, and release decisions. The prior implementation
wrote only two of roughly forty domain transitions to an outbox, then marked
them SENT through a logging sink without a receipt. Trust writes existed only
in an MCP demo table and had no production caller.

The event contract also defined `case.closed` as the archival event, while the
Python/YAML state transition closed a Case directly on `notification.sent`.
That allowed an ACK-shaped transition without proving a provider receipt.

## Decision

1. Every required integration event is projected by `EventStore` in the same
   transaction as its lowercase aggregate event. The outbox row binds the
   source event id and sequence, stable uppercase event name, channel,
   payload, and payload digest.
2. Phase 1 runs one fixed dispatcher worker. PostgreSQL claims use row locks,
   `SKIP LOCKED`, a lease, and a claim token. This is not dynamic scaling.
   A claim is eligible only when no lower `source_event_seq` for the same
   aggregate remains unsent. A DEAD predecessor freezes later deliveries for
   explicit human repair rather than allowing causal reordering.
3. Delivery success requires an immutable receipt bound to the exact outbox id
   and payload digest. Logging is not a delivery sink. Pre-003 logging-only
   SENT rows are migrated to DEAD with an explicit unverifiable-receipt reason.
4. The control-plane `trust_ledger` and append-only
   `trust_ledger_entries` tables are authoritative. The legacy MCP Trust module
   remains contract/replay-only and is not a production writer.
5. Trust consumes real terminal/UNKNOWN Release events. Entries are unique by
   source event and by action reference; multiple probes within one release
   action cannot increase trials. UNKNOWN moves Trust to BLOCKED_UNKNOWN.
6. Release outcomes are R2 and remain MANUAL. Statistical evidence is still
   recorded; 3/3 has a Wilson two-sided 95% lower bound near 0.438 and is
   denied. R2 would remain per-action approval even above the threshold.
7. Only the dispatcher may accept a notification receipt. A valid ACK writes
   `notification.sent` and `case.closed` atomically with audit and their
   `NOTIFICATION_SENT`/`CASE_ARCHIVED` outbox rows. The direct sent REST route
   is removed. Invalid receipts dead-letter the notification and escalate the
   Case.
8. Causal links bind to the immediate evidence-producing event: provider
   receipt digest -> `notification.sent` -> `case.closed`, Release outcome ->
   `trust.evidence_recorded` -> `trust.promotion_denied`, and completed Gate
   track events -> the terminal event projected as `GATE_COMPLETED`.

## Consequences

- An audit outage, unknown channel, digest mismatch, missing receipt,
  unresolved predecessor, or consumer ambiguity cannot become SENT.
- A crash after provider acceptance retries the same outbox id; adapter
  idempotency returns the original receipt.
- `feishu-mock` is available only when explicitly selected for
  contract/replay. Live Feishu remains an external credential/integration
  requirement and is reported separately.
- Console Trust reads now use the authoritative control-plane tables rather
  than the disconnected MCP shadow schema.
