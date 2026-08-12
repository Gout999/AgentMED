# No durable external receipt export

Tests assert local event, outbox, audit, authority and idempotency receipts in SQLite
and disposable PostgreSQL. The test database is reset and no external/production
receipt is exported. This is `replay` evidence, not a live facet.
