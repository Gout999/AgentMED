# Migration 003 Report

The dedicated scratch database `control_plane_migration_test` was recreated
and exercised with the following sequence:

1. Upgrade a clean database to revision 002.
2. Upgrade 002 to 003 and verify outbox causal/claim/receipt columns plus
   `outbox_delivery_receipts` and `trust_ledger_entries`.
3. Downgrade 003 to 002.
4. Upgrade again to 003 and verify Alembic head is `003`.
5. Recreate at 002, insert legacy receiptless outbox rows marked SENT and
   SENDING, upgrade to 003, then repeat the 003→002→003 round trip.
6. Run `alembic check` against the migrated PostgreSQL schema; no new upgrade
   operations were detected.

Final legacy-row result:

```text
obx_legacy_sending | DEAD | LEGACY_UNATTRIBUTED | source_event_seq=0 |
pre-003 logging delivery has no verifiable receipt
obx_legacy_sent    | DEAD | LEGACY_UNATTRIBUTED | source_event_seq=0 |
pre-003 logging delivery has no verifiable receipt
```

An initial draft of migration 003 exposed a SQLAlchemy bind-parameter parse
error in an unnecessary JSON seed statement. The migration transaction rolled
back cleanly to 002; the seed was removed, and the full sequence above then
passed. The service creates and locks the Trust row transactionally, so the
removed seed is not a runtime dependency.
