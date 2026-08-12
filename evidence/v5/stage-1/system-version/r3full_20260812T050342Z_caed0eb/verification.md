# V5 R3-full second VersionSet and semantic diff runtime verification

Status: **PASS (contract + R3-scoped replay)**

Subject: `caed0eb`

This bundle closes the R3-full runtime package (Master §17.4). The standalone
`system-versions.record/get/diff` intents were activated (`FROZEN_R3`) and
implemented on the D2-frozen contract: record writes the next immutable
SystemVersionSet with exact-previous CAS lineage under a
workspace/application/environment advisory lock, in one PostgreSQL unit of
work covering event/outbox/controller-audit/AuthorityReceipt/idempotency;
get recursively re-verifies the version set graph; diff produces the
deterministic D2-shaped result and rejects self/cross-application
comparisons. Migration 013 adds the lineage column.

The semantic series is:

- `33ea7e6` — runtime implementation (contracts activation, migration 013,
  models, services, HTTP routes, CLI commands, capability discovery, tests);
- `caed0eb` — integration head assertion advance to 013;
- `18482f8` — verifier P1 remediation (environment ACTIVE and cross-
  application binding rejection with pinning tests, unknown-commit-outcome
  fail-closed test, installed-CLI e2e incl. the local `init` command,
  journey assertion hardening, R2-era head/seed fixes, frozen
  different-key-same-digest CONFLICT contract restored).

Detached clean-checkout verification passed:

- compiler determinism: zero generated diff (14 activated intents);
- compiler tests: 18 passed;
- full V3/V4/V5 conformance: 557 passed;
- control-plane unit + C1–C5 wave checkers: 992 passed, 12 PG-gated skips;
- CLI: 120 passed;
- disposable PostgreSQL journeys: 8 passed — bootstrap → standalone record →
  GET of both version sets → non-trivial deterministic diff → same-key replay
  with zero new facts → concurrent record CAS with exactly one winner →
  tampered version set fails closed (500);
- YAML duplicate-key / Markdown link / secret-PII scans: 0 findings;
- clean checkout before and after: 0 dirty paths;
- independent verifier: PASS, P0=0, P1=0.

Facets: `contract=PASS`; R3-scoped `replay=PASS` (disposable PostgreSQL
journey on the real HTTP/CLI stack). `domain-provider-live`,
`agentteams-native`, `claude-runtime-live`, `agent-causal`, `repo-sandbox`,
`human-authorized-external`, and `production-canary` remain `NOT_RUN`. This
unlocks R4 construction; R4 must independently implement and evidence the
first system case closure.
