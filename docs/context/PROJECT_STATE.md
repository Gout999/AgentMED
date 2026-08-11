# Project State

- `r0_documentation_subject`: `4d15c1c81180386fa4852a53f8b8847e74cda050` (independent detached-checkout PASS; P0=0/P1=0)
- `r0_evidence`: `evidence/v5/stage-0/documentation-authority/r0docs_20260811T104032Z_4d15c1c/`
- `d1_lifecycle_subject`: `798531af539cd37e797723f2985d55c70fa1046e` (semantic series starts at `66052a1`; independent detached-checkout PASS; P0=0/P1=0)
- `d1_lifecycle_evidence`: `evidence/v5/decision-gates/d1-application-component-lifecycle/d1lifecycle_20260811T123512Z_798531a/` (`contract=PASS`; other 8 facets `NOT_RUN`)
- `r1_authority_subject`: `8e216939f9126b0bcef57b8ce9d292c27ba23717` (independent clean post-commit PASS; foundation-scoped `contract/replay=PASS`; other 7 facets `NOT_RUN`)
- `r2_catalog_bootstrap_subject`: `c838b2bcefb80c8458aefa17934e190a5d8485f3` (independent detached clean post-commit PASS; P0=0/P1=0)
- `c0_convergence_subject`: `903e954` (semantic series starts at `a14784a`; C0 DONE; independent clean post-commit PASS; P0=0/P1=0; cumulative `c838b2b..903e954` = 2 commits / 17 paths; SHA/evidence digest DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE)
- `c1_wire_subject`: `3dc7339` (C1 DONE; single-source wire + activated-operation compiler; post-commit verifier PASS; determinism PASS; shadow parity 103 corpus cases legacy<->generated agree; full conformance 547; control-plane unit 876 + 12 PG-gated skips)
- `c2_foundation_subject`: `d2c3f18` (C2 DONE; foundation records/events/bindings/receipts/graph; import-safe no domain imports; post-commit verifier PASS; unit 876 + 12 PG-gated skips; conformance 547; findings in contracts/v5/c2-foundation-findings.md)
- `c3_import_capability_subject`: `3adaac0` (C3 DONE; SCC broken via v5_composition; capability derives from C1 operation-manifest; scripts/check_import_graph.py PASS: 0 cycles, lane coverage 100%, 0 direct-table violations; post-commit verifier PASS; findings in contracts/v5/c3-capability-import-adjudication.md)
- `c4_transport_subject`: `1d7b59c` (C4 DONE; generated openapi.yaml + TS module; route-registry gate; CLI manifest-derived; console dual-guard shadow+fallback; Python parity extended 11 ops/103 cases; post-commit verifier PASS; findings in contracts/v5/c4-transport-cutover-adjudication.md)
- `c5_cleanup_subject`: `19f26bf` (C5 DONE; cleanup/enforcement/recovery; verify_convergence.sh ALL SECTIONS PASS; unit 982 + 12 PG-gated skips; conformance 547; CLI 118; compiler 18; findings in contracts/v5/c5-cleanup-enforcement-adjudication.md)
- `convergence_series`: `COMPLETE` (C0–C5 closed; D2 `ELIGIBLE / NOT STARTED` after C5 gate review; R3-full, R4, V5-2+ remain LOCKED)
- `r2_verification`: contracts 541; control-plane unit 876 plus 12 explicitly PG-gated skips; CLI 118; Console 17 plus build; disposable PostgreSQL 17/17
- `checksum_policy`: SHA-256 and digest-bearing final evidence are deferred by the product owner until final whole-project closure; this deferral does not convert an unrun facet to PASS
- `pre_r0_baseline`: `4a0a421cc669bf98d9b882d149d5d3df4c8dc36e`
- `last_independently_verified_semantic_subject_commit`: `19f26bf` (C5 cleanup/enforcement subject)
- `last_digest_closed_evidence_subject_commit`: `798531af539cd37e797723f2985d55c70fa1046e` (V5-D1 contract-only; retained separately because R1/R2 checksums are owner-deferred)
- `stage0_documentation_commit`: `b7889a7e200f76bd5985188ff6fb7e9e1860fd28`
- `previous_v3_evidence_commit`: `cef1598b4ac1d42fdd4f206c5747eb89a06f24fc`
- `active_branch`: `codex/v5-convergence`
- `origin/main commit`: `81ae70654eeef55d60e96cac90e181609dea4f29`
- `local main commit`: `81ae70654eeef55d60e96cac90e181609dea4f29`
- `collaborator_sync_merge`: `4b5377dae317eaeadbf23ab85481881096b6d6d2`
- `working_tree`: C0 closed at `903e954` (semantic series `a14784a` + `903e954`); C1 closed at `3dc7339` (single-source wire + activated-operation compiler; post-commit verifier PASS P0=0/P1=0). C2 closed at `d2c3f18` (foundation records/events/bindings/receipts/graph; post-commit verifier PASS P0=0/P1=0). C3 closed at `3adaac0` (import cycles broken; capability wired to C1 output; import-graph checker PASS). C4 closed at `1d7b59c` (transports cut to generated artifacts; route-registry gate; CLI/TS/OpenAPI shadow+fallback). C5 closed at `19f26bf` (cleanup, effective enforcement, recovery; verify_convergence.sh ALL SECTIONS PASS). **Architecture convergence series C0–C5 COMPLETE.** D2 `ELIGIBLE / NOT STARTED`; R3-full, R4 and V5-2+ remain locked. The original `codex/v4-foundation` mixed WIP is preserved and excluded; its exact 144-entry snapshot is recorded in `docs/context/V5_CONVERGENCE_WIP_INVENTORY.md`.
- `last_updated`: `2026-08-11` (Asia/Hong_Kong)

## Current V5 convergence truth (supersedes the historical repair snapshot below)

- D-015 is the product-owner decision that makes V5 the default design and construction baseline
  for all new product/domain work. V3 and V4 remain explicit compatibility lanes. This is a
  development-authority switch, not an operational runtime cutover.
- The public and CLI default major remains unchanged. Existing V1/V3/V4 behavior remains a
  compatibility boundary; `/api/v2` remains explicit. D-015 does not activate any route,
  capability, migration, provider, Agent, external write or production surface.
- R1 is committed at `8e21693` and independently verified within its authority/event-foundation
  scope. R2 is committed at `c838b2b` and independently verified for the exact 11-intent catalog
  and bootstrap-only first graph. R2 does not implement R3-full second VersionSet, get/diff, R4,
  V5-2+, observed runtime or release.
- R1/R2 checksum-bearing evidence remains intentionally deferred to final whole-project closure by
  owner decision. Until then both remain `VERIFYING`, even though their clean post-commit verifier
  findings are P0=0/P1=0. Only `contract` and the explicitly bounded `replay` facet are PASS; the
  other seven canonical facets remain `NOT_RUN`.
- R1/R2 contract-overlay fields that still say `IMPLEMENTED_PENDING_POST_COMMIT_VERIFIER` are
  subject-time metadata frozen inside those semantic commits. The verifier has since passed; the
  current project status is this file, `PLANS.md` and `LAST_HANDOFF.md`. C0 does not rewrite runtime
  overlays merely to change a status label; C1 must make status/activation metadata mechanically
  single-sourced without changing the exact 11-intent surface.
- The current execution baseline is D-015 plus `docs/plans/v5-architecture-convergence.md` and the
  convergence section of `docs/plans/v5-master-execution-plan.md`. C0-C5 execute in order using a
  modular monolith and one PostgreSQL business UoW. D2, R3-full, R4 and V5-2+ are paused until the
  convergence gate releases them. **Architecture convergence C0–C5 is COMPLETE** (`903e954`, `3dc7339`, `d2c3f18`, `3adaac0`, `1d7b59c`, `19f26bf`; verifier PASS each; verify_convergence.sh ALL SECTIONS PASS). D2 is `ELIGIBLE / NOT STARTED`; R3-full, R4, V5-2+ remain locked.
- The original dirty worktree is not a candidate baseline. It remains preserved on
  `codex/v4-foundation`; C0 uses `codex/v5-convergence` and exact allowlists. No path from the
  preserved WIP inventory may be promoted without clean-base hunk reconstruction and provenance
  review.

## Historical pre-R1 repair snapshot (superseded; provenance only)

- Current execution orchestration is
  `docs/plans/v5-master-execution-plan.md`. `docs/README.md` defines the document authority and
  archive policy; old cumulative handoffs and preconstruction snapshots are historical only. The
  Master Plan independent static execution review passed on 2026-08-11. R0 is now `DONE`: semantic
  subject `4d15c1c81180386fa4852a53f8b8847e74cda050` passed detached clean-checkout verification,
  with a digest-bearing manifest and independent P0=0/P1=0 report under the R0 evidence path.

- Commits through `4a0a421` contain V5-1A/B/C implementation work, but the project-wide review
  rejected the prior closure, so the tracked 1A/1B/1C bundles are not accepted closure authority.
  Worktree-only banners and remediation evidence are deliberately excluded from R0; the old raw
  artifacts remain unchanged until their owning runtime stage records a revocation/index update.
- The current worktree repairs major-2 V5 event envelopes (migrations 011/012), exact revision and
  record-digest bindings, server-owned trust roles, project/environment authorization, durable
  denial audits, version/provenance/dataset validation, fresh post-proposal owner credentials,
  honest pending ResolutionContract semantics, V5 Case Console visibility, `/api/v2/capabilities`,
  Compose/readiness/secret defaults, demo-app Alembic ownership and notification DB isolation.
- The repair worktree includes an R4-owned First System Case local runbook; it is deliberately
  excluded from R0 and is not yet a clean-checkout capability. Credential issuance remains a local PostgreSQL management
  operation; it is intentionally not exposed as public HTTP. Confirmation is authoritative human
  input but remains `NEEDS_ACCEPTANCE_CRITERIA` until V5-4 materializes the exact
  ResolutionContract.
- Current transport discovery advertises only the implemented 1A/1B/1C allowlist. Standalone
  `system-versions.record`, V5-2 durable work/V5 outbox delivery, V5-4 ResolutionContract and all
  later Agent/MCP/A2A/release stages remain `NOT_IMPLEMENTED`.
- D1 lifecycle decision is closed contract-only. The owner selected Option A: immutable revision 1
  `REGISTERED` followed by manifest-only revision 2 `ACTIVE`, with exact CAS, dual initiator/controller
  authority and current-authoritative-ACTIVE ComponentRevision binding. Current register/import
  runtime still writes `ACTIVE` directly, so the runtime mismatch remains open work for R1/R2; D1
  evidence does not prove migration or runtime implementation.
- Current verification is focused/local only. Five V5 PostgreSQL repair journeys now pass in a
  dedicated ephemeral loopback container, including the installed-CLI First System Case and the
  concurrent first-import race; the container was removed afterward. An independent verifier passed
  the final remediation worktree after the scope/type capability filter was repaired. There is still
  no completion commit, digest-bearing run manifest, per-stage post-commit verifier or live facet.
  Therefore V5-1A/B/C are `IN_PROGRESS`, not `DONE`.
- `docs/decisions/D-013-v5-ai-system-governance-and-agent-native-control-plane.md` and its
  referrers are in the verified R0 authority-recovery subject. The clean-checkout product-authority
  chain is closed; this proves documentation authority only, not V5 runtime.

## Current product and requirements state

- User-ratified direction: CaseLoop is a long-term open-source, Agent-native governance and
  operations control plane for AI applications. `AIApplication` is the top-level governed object;
  Agent is one component. Target-user needs decide scope; competition, market gaps and overlap do not.
- Product baseline: `docs/product-principles.md`; D-013 records the accepted V5 product boundary.
  Two independent product/evaluation reviews converged on a partial GO and the user accepted their
  shared changes on 2026-08-11: confirmed acceptance before Gate, code-vs-AI-behavior workload
  profiles, CLI-first onboarding, Gate reality checks, and release only for deployable targets.
  `docs/prd-v5.md`, `docs/plan-v5.md` and the progressive blueprint record that direction.
  On 2026-08-11 the V5-0 contract baseline was frozen (V5-0B `8dd25ca`, V5-0C `b3727d7`) and
  independently accepted. D-015 now makes the V5 authority chain the default for all new
  product/domain design and construction. `docs/plan-v4.md` remains the V4 compatibility baseline;
  v3 code/contracts/migrations/tests remain the implemented Scenario compatibility baseline. This
  does not make unfinished V5 stages implemented or change an operational default major.
- Confirmed later requirement, not implemented: CaseLoop self-observability through Langfuse and a pluggable TraceSource that can retrieve a governed Agent's Langfuse input/output/model/tool evidence with explicit completeness state.
- The first reference workflow remains customer service. The authenticated no-trace Signal intake
  subset is implemented and locally verified in S1A. R2 now provides the independently verified
  V5 Application Catalog and bootstrap-only first declared graph at `c838b2b`. Standalone second
  VersionSet/get/diff, Case binding/Acceptance closure, BadcaseSpec/ResolutionContract,
  SystemEpisodeView/Snapshot, durable operations, system Gate, Agent-native async transports,
  local/shadow release and observed-runtime verification remain target-only and unimplemented.
- `docs/plan-v4.md`, backed by `docs/research/demand/caseloop-v4-small-team-adoption.md`, is approved. Stage 0 and Stage 1 Entry are `DONE (contract-only)`; Stage 1A is `DONE (local runtime)` at `22c23f8`, with independent evidence. All provider, Agent runtime, repository, human-authorized external and production facets are `NOT_RUN`.
- D-013 has closed the high-level V5 scope decision. The accepted baseline defines the target
  Desired/Observed/Effect split, schema-major-2 V4 lifecycle reuse, `/api/v2` compatibility boundary, first wire
  slice, CLI-first competition proof, verification-only Candidate path and deployable-service
  local/shadow golden path. The V5-0 contract baseline (V5-0B `8dd25ca`, V5-0C `b3727d7`) is
  frozen and independently accepted. Feature progression is now paused behind C0-C5 architecture
  convergence; D2/R3/R4 and V5-2+ resume only after the convergence gates. V4 S1B–S7 remain frozen.
- `contracts/v5/` preserves the historical non-routable V5-0C freeze and adds the exact current R1
  foundation/R2 11-intent overlay plus D-015 compatibility-lane semantics. The historical
  68/449/517 counts remain V5-0 evidence only; the current R2 verification counts are recorded at
  the top of this file. MCP/A2A, Agent execution and live source remain absent;
  `test_quality_api.py` is still `NOT_RUN`.
- The two later independent reports found a real target problem but also showed that acceptance
  criteria, first-use cost and Gate reality validity need to precede governance ceremony. Their
  changes were accepted without deleting the broader V5 target or weakening release authority.
  The revised exact schemas, owner acceptance, conformance evidence and construction authorization
  were completed by the V5-0B/0C freeze and their independent acceptance on 2026-08-11.
- The Wiki is being aligned to the accepted V5 construction baseline while preserving V4 S1A and v3 facts.
- The user authorized local branching, planning, contracts, code, and tests. Push, PR, paid provider calls, human approval, and production or other external writes remain separately gated.
- The user's earlier live demo and the independently verifiable P0-4 live acceptance are different evidence claims. A prior run does not need to be repeated merely because time passed; rerun only when the acceptance target, code/provider path, evidence contract or reproducibility requirement makes a new run necessary.

## Security prerequisite before the next live run

- During a read-only Docker Compose inspection on 2026-08-10, a resolved configuration command expanded secret-bearing environment values into the private tool log. No value was copied into repository files, evidence, or this handoff, but the affected credentials must be treated as potentially exposed.
- Before any subsequent provider/live run, rotate the StepFun credential, Feishu app secret, and internal authority/read/write and role-token material referenced by `deploy/.env` or `.env.b1-live`; update the local secret stores without committing their values, then re-run a redacted credential/preflight check.
- Rotation has **not** been performed or authorized through the Stage 1A closure. Until it is complete, provider/live execution is blocked; offline contracts, documentation, and tests may continue.

## Current AgentTeams runtime snapshot

This is the observed 2026-08-10 local platform snapshot and must be rechecked before a later live claim:

- AgentTeams Manager and Controller were running. The `caseloop-team` object reported `Active`, but `leaderReady=false` and `readyWorkers=0`; no CaseLoop Agent task was executing.
- All six formal Workers — `quality-officer`, `collector`, `attributionist`, `repairer`, `gatekeeper`, and `case-officer` — were `Sleeping`. Their current reference runtime/model is CoPaw with StepFun `step-3.7-flash`.
- Three older `spike-*` Workers were also sleeping and are not members of the formal CaseLoop Team. `caseloop-approver` is a Human approval identity, not an Agent.
- No Claude Code Agent or GLM Agent was deployed in AgentTeams. The proposed three-role `caseloop-coding-team` had not been created; it is a Stage 2 target, not current capability.
- The deployed MCP projection names differed from `agents/team.yaml`, and the inspected runtime response did not expose enough Skill state to prove the repository-declared Skill was loaded or called. Desired manifests and observed deployment state must remain separate.

## Verified completed capabilities

- P0-1 (`4cd6e64`) removes gate fake-pass and release bypasses. GateReport is
  derived from explicit rule, judge, deterministic, and live-provider tracks,
  persisted with immutable bindings, and rejected for every non-PASS or unknown
  condition.
- P0-2 (`8e237e3`) transactionally connects the required domain events to the
  outbox, idempotent dispatch, audit, notification/archive, and Trust Ledger.
  One action creates one Trust sample; 3/3 still fails the 0.9 Wilson threshold. This is retained
  v3 Trust accounting behavior, not a default V5 Candidate-quality threshold or sample-size rule.
- P0-3 (`a08c005`) connects the production Console to authoritative T8 read
  models with runtime guards and loading, empty, error, retry, partial, stale,
  and UNKNOWN states. The Console remains read-only.
- P0-4 replay implementation (`b5ef38f`) now executes B1 from badcase injection
  through Feishu complaint dedup, frozen VersionSets/probes, authoritative
  prompt attribution, immutable WorkOrder, actual Gate tracks, bound human
  approval, stage/canary/promote, notification, archive, Trust denial, and a
  digest-validated final manifest.
- PR #1 contract changes are now formally distinguishable and ratified:
  GateReport uses schema version `0.2.0`, and D-002 through D-007 record the
  master-controller ratification required by review.
- Replay evidence is checkout-portable: all 22 manifest artifacts use
  root-bound `repo:///` references. The committed run exports one persisted
  WorkOrder and two persisted GateReports, both bound to the same frozen
  `probes-customer-service-v1` dataset and independently validated.
- `feishu.reply_origin` accepts the legacy missing-digest form only for an
  inline `data:` body whose digest can be recomputed locally. External body
  references without a digest fail closed. The three obsolete mutating MCP
  tools identified in review are no longer registered.
- Live Gate persistence now requires the official StepFun origin in both the
  evaluator response and independent Quality log. Missing or stub origins fail
  before GateReport persistence. The independent verifier found no remaining
  fake-success path in this boundary.
- Replay and live commands are separate. Replay adapters are labelled; the live
  command fails during preflight when genuine inputs are absent and never falls
  back to replay.
- Stage 1A (`22c23f8`) implements the first public v4 vertical slice: guarded
  migration 007, strict local bootstrap, five authenticated HTTP/CLI intents,
  and a transactional no-trace Signal → OPEN QualityCase/Link → `UNKNOWN`
  Evidence graph with immutable idempotency, audit, outbox and authority
  validation. Independent local verification passed; no AgentRunRef is invented.

## Proven gaps at the last independently verified P0 evidence snapshot

- At `cef1598`, P0-4 was not accepted as DONE because that evidence package did
  not contain an independently validated live-provider B1 run. Its report records the missing credentials, deployed endpoints,
  post-injection Feishu input, fresh human ApprovalGrants, and independent
  AgentTeams trace attestation.
- Live recovery is not yet durable across every interruption. A failure after
  Case creation but before closure can leave Case/Experiment/Release records
  that a fresh invocation cannot safely resume or terminalize. A failure after
  promote but before notification/evidence also skips compensation and cannot
  satisfy the fresh-run lifecycle precondition.
- Demo canary operates on the exact candidate and post-canary probes; it is not
  evidence of percentage-routed production traffic.
- Non-terminal `eval.requested`, `eval.started`, and track-completed event
  metadata remains explicitly tracked as P1 contract debt.

## Historical P0 work queue

- `previous_only_in_progress`: P0-4 live closure remained blocked at the above evidence snapshot — add
  authoritative resumable or terminal failure reconciliation for Case,
  Experiment, and Release without weakening approval, revision, gate, or audit
  bindings.
- `next`: test interruption after Case creation and after promotion, prove
  idempotent resume/compensation, then rerun `make demo-b1-live` when the exact
  external inputs in its preflight report are supplied.
- Full acceptance criteria, dependencies, evidence, and commits are in
  `PLANS.md`.

V4 Stage 1A is closed. The V5 product boundary, review-driven product adjustments, and the frozen
V5-0 contract baseline (V5-0B `8dd25ca`, V5-0C `b3727d7`) are recorded and independently accepted.
**Architecture convergence series C0–C5 is complete** (`903e954`, `3dc7339`, `d2c3f18`, `3adaac0`, `1d7b59c`, `19f26bf`; verifier PASS P0=0/P1=0 each). D2 is `ELIGIBLE / NOT STARTED`; R3-full, R4 and V5-2+ remain locked. R1/R2 semantic subjects are
clean-verifier PASS but remain `VERIFYING` because checksum-bearing final evidence is owner-deferred.
D2/R3/R4/V5-2+ and V4 S1B-S7 remain frozen. Live-provider calls and external writes remain
separately gated.

## Actual runnable commands

```bash
git fetch --all --prune
git status --short --branch
git log --oneline --decorate -15 --all

make demo-b1-replay
make demo-b1-live

cd control-plane
AUDIT_JSONL_PATH=/tmp/caseloop-control-plane-audit.jsonl \
PYTHONPYCACHEPREFIX=/tmp/caseloop-control-plane-pycache \
DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test \
.venv/bin/python -m pytest tests/unit -q
CASELOOP_ALLOW_INTEGRATION_RESET=true \
DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test \
.venv/bin/python -m pytest tests/integration -q
.venv/bin/alembic upgrade head

cd ../eval-harness
.venv/bin/python -m pytest -q

cd ../mcp-servers
.venv/bin/python -m pytest -q

cd ../contracts
../eval-harness/.venv/bin/python -m pytest \
  conformance/test_schemas.py conformance/test_wilson.py \
  conformance/test_v4_*.py conformance/test_v5_*.py -q
```

If FileProvider has offloaded an ignored virtual environment, rebuild it under
`/tmp` and rerun the same command. Do not classify a blocked import as a pass.

## Independently verified V5-0 contract freeze record

| Scope | Result | Classification |
|---|---|---|
| V5-0B `8dd25ca` system governance ownership | independent acceptance PASS; verifier added 13 adversarial checks, all green | contract-only freeze |
| V5-0C `b3727d7` first system wire slice | independent acceptance PASS; FAIL list empty | contract-only freeze |
| v3 + v4 + v5 combined offline | 517 passed, 0 failed, 0 skipped | contract only |
| v3 + v4 independent | 449 passed | contract only |
| V5 focused suite | 68 passed (test_v5_adversarial + test_v5_compatibility + test_v5_draft + test_v5_first_slice) | contract only |
| `conformance/test_quality_api.py` | NOT_RUN; requires a live service | live, not run |

Evidence: freeze commits `8dd25ca2c0876632778827c9cbfb9dc14a169f1a` and
`b3727d72e9251bbf4d92047c02512d38eb976cf5`. Only `contract=PASS`; every V5
runtime/provider/Agent/repository/human/production facet is `NOT_RUN`. The old
`trusted_attestor` mismatch was a repair finding and is not an open current-state blocker; the
current runtime/contract allowlists must still be revalidated in each stage's post-commit evidence.

## Independently verified v4 Stage 0 contract record

| Scope | Result | Classification |
|---|---|---|
| v3 + v4 contract suite | 397 passed, 0 failed, 0 skipped | contract only |
| v3 compatibility subset | 29 passed | existing schema/Wilson baseline retained |
| v4 JSON/YAML/Python | 218 JSON, 5 YAML, 15 Python files passed | static contract validation |
| documentation links/SOUL/diff | 111 local links, SOUL sync and diff-check passed | repository consistency |
| independent verifier | PASS; 0 remaining P0/P1 | repeated event/audit keys and same `(kind,id,revision)` equivocation attacks rejected |

Evidence: `evidence/v4/stage-0/contracts/s0contracts_20260810T085840Z_6604712/`. Only `contract=PASS`; every runtime/provider/Agent/repository/human/production facet is `NOT_RUN`.

## Independently verified v4 Stage 1 Entry record

| Scope | Result | Classification |
|---|---|---|
| v3 + v4 contract suite | 449 passed, 0 failed, 0 skipped | contract only |
| coordinated wire attacks | 7 attack groups rejected | independent verifier |
| v4 JSON/YAML/Python | 244 JSON, 5 YAML, 19 Python files passed | static contract validation |
| runtime/provider/external facets | all `NOT_RUN` | no capability claim |

Evidence: `evidence/v4/stage-1/wire-contract/s1wire_20260810T114213Z_070ba20/`; subject commit: `070ba200acc09dfbcb725cc3466ef3ebd1e4f6fd`.

## Independently verified v4 Stage 1A record

| Scope | Result | Classification |
|---|---|---|
| control-plane unit | 691 passed | local deterministic runtime |
| coordinated authority/auth/idempotency/read attacks | 232 passed | replay; independent verifier |
| v3 + v4 contracts | 449 passed | contract |
| CLI | 81 passed; clean wheel install/import/help passed | local client/runtime |
| disposable PostgreSQL | 4 passed; cleanup left 0 public tables/types and 0 active sessions | local integration, not provider-live |
| demo/eval/MCP/Console | 40; 85 + 4 live skips; 121 + 1 live skip; build PASS | compatibility regression; live skips remain NOT_RUN |
| live/Agent/repository/human/production facets | all `NOT_RUN` | no external capability claim |

Evidence: `evidence/v4/stage-1/maintainer-intake/s1a_20260810T143055Z_22c23f8/`; implementation commit: `22c23f89708471e3f1cb8ca893414733a839a1bd`.

## Last independently verified v3 test baseline

| Scope | Result | Classification |
|---|---|---|
| control-plane full | 329 passed across the required environment split | 328 passed with PostgreSQL; the SQLite-only migration test passed separately without `DATABASE_URL` |
| eval-harness full | 76 passed, 4 skipped | offline/replay; live skipped |
| MCP full | 121 passed, 1 skipped | offline/unit; live smoke skipped |
| contracts offline | 29 passed | schema/Wilson contract suite |
| Console | 7 passed; production build passed | API/runtime guards and Vite bundle |
| clean committed-tree B1 replay | 29 contract + 28 replay passed | 22 portable artifacts; 1 persisted WorkOrder; 2 persisted GateReports; 135 probe outputs |
| independent B1 validator | verified | all artifact/probe digests and persisted Gate/WorkOrder bindings checked |
| full Quality conformance | 28 passed, 15 failed | failures are connection refused at `127.0.0.1:8080` |
| demo live E2E | 6 failed | failures are connection refused at `127.0.0.1:8080` |
| live B1 preflight | exit 2, blocked | expected fail-closed result; no provider call or replay fallback |

## Legacy P0 live-acceptance inputs

The exact legacy P0 live-acceptance report requires: `STEPFUN_API_KEY`, `JUDGE_MODEL`,
`CASELOOP_B1_BAD_VERSIONSET_ID`, `CASELOOP_B1_GOOD_VERSIONSET_ID`,
`CASELOOP_QUALITY_API_BASE_URL`, `CASELOOP_READ_TOKEN`,
`CONTROL_PLANE_BASE_URL`, `CONTROL_PLANE_TOKEN`, `GATE_AUTHORITY_TOKEN`,
`CASELOOP_B1_APPROVAL_COMMAND`, `CASELOOP_B1_AGENT_TRACE_COMMAND`,
`CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY`, and
`CASELOOP_B1_FEISHU_MESSAGE_COMMAND`.

The commands must provide a post-injection Feishu message, three fresh human
ApprovalGrants, and an independently signed AgentTeams v1.2.1 trace. Live
Quality/control-plane/Feishu endpoints must also be reachable. None is replaced
with mock success.
