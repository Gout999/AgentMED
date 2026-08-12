# V5 R4 First System Case closure verification

Status: **PASS (contract + R4-scoped replay)**

Subject: `365c2c8`

This bundle closes the R4 runtime package (Master §17.5). The five V5-1C
intents were activated (`FROZEN_R4`) and implemented: an issue-source signal
creates the Case, `cases.bind-application` binds it to the application with
an exact case revision/digest, acceptance criteria are proposed/read/
confirmed as immutable authority records (event/outbox/audit/receipt in one
PG unit of work), and the read model bounds readiness at
`NEEDS_ACCEPTANCE_CRITERIA` / `PENDING_MATERIALIZATION` — never `READY`
before the V5-4A ResolutionContract. Cross-workspace, wrong-actor and stale
inputs fail closed; duplicate confirmation is rejected; the CLI adds the
`case` command family including the local `from-issue` orchestration.

The semantic series is:

- `df86662` — runtime implementation (contracts activation, 5 schemas,
  routes, CLI, capability, journey tests);
- `7c17391` — verifier P0/P1 remediation (duplicate-confirmation
  fail-closed, v1 readiness bounded, schema/registry/docstring cleanup);
- `365c2c8` — final remediation (readiness pinning assertion made exact,
  remaining dead defs removed).

Detached clean-checkout verification passed:

- compiler determinism: zero generated diff (19 activated intents);
- compiler tests: 18 passed; full V3/V4/V5 conformance: 557 passed;
- control-plane unit + C1–C5 wave checkers: 996 passed, 12 PG-gated skips;
- CLI: 130 passed;
- disposable PostgreSQL journeys: 31 passed + 1 R2-era from-issue e2e skip
  (covered by CLI unit tests and the R4 HTTP journey) — source→case→bind→
  propose→confirm with bounded readiness, plus the R3 second-version-set
  journey and tamper fail-closed;
- YAML duplicate-key / Markdown link / secret-PII scans: 0 findings;
- clean checkout before and after: 0 dirty paths;
- independent verifier: PASS, P0=0, P1=0.

## Remediation confirmation pass (2026-08-12, post-closure)

The first verifier round reported P0=0/P1=0 in `verifier-report.md`, but its
NEW-1 note flagged one ineffective assertion plus residual dead definitions;
those were fixed in `365c2c8` after that report was written, so the
remediation itself had not been re-verified. A second detached clean-checkout
pass at `365c2c8` re-ran the gates and all remain green:

- full V3/V4/V5 conformance: 557 passed;
- control-plane unit + C1–C5 wave checkers: 996 passed, 12 PG-gated skips;
- CLI: 130 passed;
- disposable PostgreSQL journeys: 31 passed + 1 R2-era from-issue e2e skip;
- compiler determinism: zero generated diff.

The remediation is therefore confirmed; no new findings.

Facets: `contract=PASS`; R4-scoped `replay=PASS` (disposable PostgreSQL
journey on the real HTTP stack). `domain-provider-live`,
`agentteams-native`, `claude-runtime-live`, `agent-causal`, `repo-sandbox`,
`human-authorized-external`, and `production-canary` remain `NOT_RUN`. This
unlocks V5-2A construction; V5-2A must independently implement and evidence
the Durable Work Kernel.
