# Project State

- `last_verified_commit`: `b5ef38fbc1a0da90bf766f0590763be5d1118e74`
- `active_branch`: `codex/p0-close-b1-loop`
- `origin/main commit`: `a6de5cc1a06d6967634676b2661da7d2e46d287b`
- `local main commit`: `89ea66750dfed3df6feeed493d9e2499ef77cdbe`
- `collaborator_sync_merge`: `4b5377dae317eaeadbf23ab85481881096b6d6d2`
- `working_tree`: P0-4 implementation is committed. Formal evidence was generated from a clean sparse snapshot at that exact commit; this context/evidence update is the only intended follow-up delta. macOS FileProvider makes broad status scans unreliable, so intended files are hash-verified before commit.
- `last_updated`: `2026-08-09T17:44:27+10:00`

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
- Live Gate persistence now requires the official StepFun origin in both the
  evaluator response and independent Quality log. Missing or stub origins fail
  before GateReport persistence. The independent verifier found no remaining
  fake-success path in this boundary.
- Replay and live commands are separate. Replay adapters are labelled; the live
  command fails during preflight when genuine inputs are absent and never falls
  back to replay.

## Proven gaps

- P0-4 is not accepted as DONE because no live-provider B1 run has been made.
  Its report truthfully records the missing credentials, deployed endpoints,
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

## Work queue

- `current_only_in_progress`: P0-4 live closure — add authoritative resumable or
  terminal failure reconciliation for Case, Experiment, and Release without
  weakening approval, revision, gate, or audit bindings.
- `next`: test interruption after Case creation and after promotion, prove
  idempotent resume/compensation, then rerun `make demo-b1-live` when the exact
  external inputs in its preflight report are supplied.
- Full acceptance criteria, dependencies, evidence, and commits are in
  `PLANS.md`.

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
DATABASE_URL=postgresql+psycopg://gout@127.0.0.1:5432/control_plane_test \
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python -m pytest tests/integration -q
.venv/bin/alembic upgrade head

cd ../eval-harness
.venv/bin/python -m pytest -q

cd ../mcp-servers
.venv/bin/python -m pytest -q

cd ../contracts
../eval-harness/.venv/bin/python -m pytest \
  conformance/test_schemas.py conformance/test_wilson.py -q
```

If FileProvider has offloaded an ignored virtual environment, rebuild it under
`/tmp` and rerun the same command. Do not classify a blocked import as a pass.

## Latest test results

| Scope | Result | Classification |
|---|---|---|
| control-plane unit | 313 passed | offline/unit |
| control-plane integration | 15 passed | PostgreSQL/integration |
| demo-app unit | 40 passed | offline/unit |
| demo-app PostgreSQL concurrency | 2 passed | real PostgreSQL lifecycle |
| eval-harness full | 76 passed, 4 skipped | offline/replay; live skipped |
| MCP full | 119 passed, 1 skipped | offline/unit; live smoke skipped |
| contracts offline | 28 passed | contract/replay |
| focused Gate release chain | 33 passed | provider-origin and fail-closed binding |
| focused read views | 33 passed | projection integrity |
| clean committed-tree B1 replay | 28 contract + 28 replay passed | 22 required artifacts; 135 probe outputs |
| full Quality conformance | 28 passed, 15 failed | failures are connection refused at `127.0.0.1:8080` |
| demo live E2E | 6 failed | failures are connection refused at `127.0.0.1:8080` |
| live B1 preflight | exit 2, blocked | expected fail-closed result; no provider call or replay fallback |

## External blockers and required inputs

The exact live report requires: `STEPFUN_API_KEY`, `JUDGE_MODEL`,
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
