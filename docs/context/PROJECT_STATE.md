# Project State

- `repository_snapshot`: `22c23f89708471e3f1cb8ca893414733a839a1bd`
- `last_independently_verified_evidence_subject_commit`: `22c23f89708471e3f1cb8ca893414733a839a1bd`
- `stage0_documentation_commit`: `b7889a7e200f76bd5985188ff6fb7e9e1860fd28`
- `previous_v3_evidence_commit`: `cef1598b4ac1d42fdd4f206c5747eb89a06f24fc`
- `active_branch`: `codex/v4-foundation`
- `origin/main commit`: `81ae70654eeef55d60e96cac90e181609dea4f29`
- `local main commit`: `81ae70654eeef55d60e96cac90e181609dea4f29`
- `collaborator_sync_merge`: `4b5377dae317eaeadbf23ab85481881096b6d6d2`
- `working_tree`: after the Stage 1A implementation and closure commits, the only intended dirty paths are the two preserved historical WIP groups: six tracked judge/live files plus five untracked tests/adapters. Stage 1A implementation/evidence is committed separately and must not be recombined with those historical code changes.
- `last_updated`: `2026-08-10` (Asia/Shanghai)

## Current product and requirements state

- User-ratified direction: CaseLoop is a long-term open-source AI Agent quality and change-governance project. Target-user needs decide scope; competition, market gaps and feature overlap do not.
- Product baseline: `docs/product-principles.md`. The approved `docs/plan-v4.md` is the target architecture, dependency order, and new-development baseline. `docs/plan-v3.md`, `docs/spec.md`, existing contracts, migrations, and executable tests remain the implemented customer-service Scenario Pack compatibility baseline until explicitly migrated.
- Confirmed later requirement, not implemented: CaseLoop self-observability through Langfuse and a pluggable TraceSource that can retrieve a governed Agent's Langfuse input/output/model/tool evidence with explicit completeness state.
- The first reference workflow remains customer service. The authenticated no-trace Signal intake subset is implemented and locally verified in S1A. Closure/VersionSet generalization, Langfuse as the first TraceSource, coding Agent governance, real-Agent/exporter acceptance and the two-Team direction remain approved targets, not current runtime capabilities.
- `docs/plan-v4.md`, backed by `docs/research/demand/caseloop-v4-small-team-adoption.md`, is approved. Stage 0 and Stage 1 Entry are `DONE (contract-only)`; Stage 1A is `DONE (local runtime)` at `22c23f8`, with independent evidence. All provider, Agent runtime, repository, human-authorized external and production facets are `NOT_RUN`.
- The user is now inventorying a possible V5 expansion from Agent governance to AI-system governance. S1B through S7 are frozen until that requirement set is reviewed and a compatibility/supersession decision is recorded; no V5 details are inferred here.
- The Wiki is aligned to the S1A closure boundary. It retains AgentTeams as a versioned internal adapter, Langfuse as a planned dual integration, platform evidence export as distinct from real Agent causal execution, and v3 assets as the current implemented compatibility baseline.
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
  One action creates one Trust sample; 3/3 still fails the 0.9 Wilson threshold.
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

Stage 1A is closed. The current product activity is requirements intake for the possible V5 AI-system-governance expansion; S1B through S7 must not start until the scope decision is recorded. Live-provider calls and external writes remain separately gated.

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
  conformance/test_schemas.py conformance/test_wilson.py conformance/test_v4_*.py -q
```

If FileProvider has offloaded an ignored virtual environment, rebuild it under
`/tmp` and rerun the same command. Do not classify a blocked import as a pass.

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
