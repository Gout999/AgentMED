# V5-1A/B/C project-review remediation — worktree evidence

- Status: `IN_PROGRESS / NO_GO_FOR_STAGE_DONE`
- Base committed HEAD: `4a0a421cc669bf98d9b882d149d5d3df4c8dc36e`
- Subject commit: `null` (uncommitted repair worktree)
- Date: 2026-08-11, Asia/Hong_Kong
- Scope: project-wide review findings that directly block the V5-1C First
  System Case, plus prerequisite 1A/1B and local-stack repairs.

This bundle supersedes the closure interpretation of the earlier 1A/1B/1C
verification files. It is deliberately not a completion bundle: there is no
subject commit or digest-bearing artifact manifest, the independent verifier
applies only to this dirty remediation worktree, and the working tree also
contains separately owned product/eval/live WIP.

## Repaired boundaries

- V5 events use contract major 2 with exact subject and dependent bindings;
  migrations 011/012 harden trust roles, Acceptance state and event envelope.
- Catalog/version/binding/Acceptance mutations recheck server-owned roles,
  project/environment grants, exact revision/digest and authority receipts;
  audited denials persist without committing business mutations.
- SystemVersionSet replay/diff/provenance/dataset/dirty-worktree and import
  concurrency paths fail closed.
- Acceptance no longer invents a ResolutionContract. CONFIRMED remains
  `PENDING_MATERIALIZATION` and non-executable until V5-4.
- Supported local management rotates the operator credential after manifest
  import and issues an independent owner credential after proposal.
- V5 QualityCase records are visible in the Console and corrupt projections
  render `UNKNOWN` rather than false success.
- `/api/v2/capabilities` and explicit-major CLI discovery advertise the exact
  implemented 17-intent allowlist filtered by both scope and principal type;
  later skeletons remain undiscoverable.
- Compose secrets/readiness/loopback defaults, demo-app Alembic ownership,
  OAuth secret separation and notification database routing were hardened.

## Focused verification performed after repair

| Slice | Command/result |
|---|---|
| 1C authority + migrations + evidence projection | `control-plane: pytest test_v5_case_binding_acceptance.py test_migrations.py test_read_views_evidence.py` → 39 passed |
| 1A/1B + V5 HTTP/capability | `control-plane: pytest test_v5_application_catalog.py test_public_v5_api.py test_v5_capabilities.py test_v5_system_versions*.py` → 65 passed; final scope+principal-type capability slice → 18 passed |
| CLI exact wire and workflows | `cli: pytest test_cli_surface.py` → 36 passed |
| CLI dual-major capability transport | post-contract field alignment: `cli: pytest test_cli_surface.py test_transport_and_retry.py -k 'capabilities or test_capabilities_v2'` → 5 passed, 83 deselected |
| Current V5 runtime overlay | `contracts: pytest conformance/test_v5_*.py` → 74 passed |
| Local three-stage bootstrap | delegated focused run → 11 passed |
| PostgreSQL 1A/1B/1C journeys | isolated ephemeral PostgreSQL on `127.0.0.1:55439/control_plane_test`; three V5 integration files → 5 passed in 18.73s; container removed |
| Independent verifier | PASS for the final review-remediation worktree after closing the principal-type capability P1; not a per-stage post-commit closure verifier |
| Console/read projections | delegated focused runs → backend 19 + 6 + 5 passed; Console validator 9 passed; production build passed |
| Operations repairs | delegated focused runs: readiness/config, demo auth/migration, notification DB isolation and Compose static checks passed |
| Static | `py_compile` and `git diff --check` passed |

The delegated results above were produced by parallel review agents in the
same worktree. They were not inflated into a combined total because several
slices overlap.

## Diagnostic history and blockers

- A first arbitrary-database-name attempt was refused by the test safety guard.
  The isolated exact-name run then caught legacy JSON `null` source flags and a
  CLI/server request-fingerprint mismatch. Both were repaired before the final
  5/5 PostgreSQL pass; refused/failed diagnostic attempts are not counted as PASS.
- Completion commit and artifact/run-manifest digest: absent.
- Per-stage post-commit verifier: absent until the semantic commits exist.
- Frozen lifecycle mismatch remains: AIApplication/SystemComponent target
  starts REGISTERED then activates; current register/import writes ACTIVE.
- Standalone second-version `system-versions.record` is not implemented.
- V5 outbox delivery is V5-2; ResolutionContract materialization is V5-4.
- D-013 remains untracked, so a clean checkout still breaks the product
  authority link.

## Evidence facets

| Facet | Status |
|---|---|
| contract | PASS (current partial runtime overlay only) |
| replay | PASS (focused deterministic unit/wire paths only) |
| domain-provider-live | NOT_RUN |
| agentteams-native | NOT_RUN |
| claude-runtime-live | NOT_RUN |
| agent-causal | NOT_RUN |
| repo-sandbox | NOT_RUN |
| human-authorized-external | NOT_RUN |
| production-canary | NOT_RUN |

Exit decision: **NO-GO for marking V5-1A/B/C DONE or starting V5-2** until the
lifecycle decision, digest-bearing manifest, per-stage semantic commits and
their post-commit verifiers are complete.
