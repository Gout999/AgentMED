# Stage 1A Maintainer Intake Independent Verification

Result: **PASS**

An independent read-only verifier reran the stable suites, the disposable PostgreSQL paths and 232 coordinated attack cases. No Stage 1A P0 remained.

The verifier confirmed:

- opaque bearer authentication binds canonical principal claims, active time, audience, workspace and scope; duplicate JSON/header and oversized streaming bodies fail before domain writes;
- a manual no-trace report creates one Signal, one OPEN QualityCase with `NEEDS_CORRELATION`, one SignalCaseLink and one `UNKNOWN`/`NO_LOCATOR` TraceEvidenceReceipt without an AgentRunRef;
- same-key replay returns the immutable original response while cross-key duplicates and drift follow the frozen idempotency contract under real PostgreSQL concurrency;
- SourceConnection, command audit, projection self-hashes, AuthorityReceipt, controller registration, event, outbox and four-event causation are exact-bound and fail closed when deleted, swapped or coordinately re-sealed;
- public reads revalidate the authoritative Signal/Case/Link/Evidence graph, including Evidence-to-Signal binding, rather than trusting projection hashes alone;
- the legacy v3 dispatcher cannot claim, mutate or be blocked by v4 outbox rows;
- the installed CLI rejects malformed or contradictory success/error envelopes and recomputes the evidence receipt digest;
- migration, strict bootstrap, PostgreSQL concurrency and installed CLI-to-loopback HTTP integration all complete on the guarded disposable database and clean it afterward.

The verifier also reran contracts, demo, eval-harness, MCP and Console checks. Provider/live-only tests remained explicitly skipped or `NOT_RUN`; no AgentTeams, Claude runtime, repository, human-authorized external or production path was invoked.
