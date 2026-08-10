# v3 to v4 cutover review

Result: **PASS for Stage 0 contract freeze**.

- Existing v3 customer-service contracts and the seven implemented aggregates were not rewritten.
- v4 lives under `contracts/v4/` with independent ownership, event, state-machine and schema namespaces.
- The contract forbids one task from holding both a v3 Case lease and a v4 WorkerTask lease.
- Runtime cutover, data backfill and migration rollback are not implemented in Stage 0.
- The legacy schema and Wilson suites passed 29 tests at contract commit `6604712b37409cf679dfb43ce97fb9882efdc713`.
