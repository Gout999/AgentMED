# Project State

- `last_verified_commit`: `02b97ddee82e53b5d2e62fababc841ac3d1acc35`
- `active_branch`: `codex/p0-close-b1-loop`
- `origin/main commit`: `a6de5cc1a06d6967634676b2661da7d2e46d287b`
- `collaborator_sync_merge`: `4b5377dae317eaeadbf23ab85481881096b6d6d2`
- `working_tree`: P0-3 implementation, tests, verifier PASS, and evidence are complete and awaiting the semantic commit
- `last_updated`: `2026-08-09T01:12:00+10:00`

## Verified completed capabilities

- P0-1 (`4cd6e64`) removed release fake-pass/bypass paths. GateReport is generated
  from explicit rule, judge, deterministic, and live-provider tracks; persisted;
  and bound to WorkOrder hash, candidate revision, dataset/version/digest, and
  evidence digest. Missing, failed, inconclusive, error, skipped, unknown, or
  mismatched state fails closed.
- P0-2 (`8e237e3`) connects nine required domain events to a transaction-bound,
  causally ordered outbox. Dispatcher retries are idempotent; Trust consumes
  actual Release outcomes once per action; 3/3 has Wilson lower `0.438494` and
  remains ineligible; notification receipt and Case archive are atomic.
- P0-3 production Console now reads authoritative Case, Experiment, WorkOrder,
  Gate, Release, Notification, Trust, and Evidence endpoints. Static fixtures,
  fixed success state, and raw WorkOrder/Evidence content are absent.
- Backend read models revalidate WorkOrder payload plus identity/case/channel
  columns, Gate schema/hash/final binding, and active VersionSet shape. A valid
  but unbound Gate is `UNBOUND`; corrupt or incomplete binding is `UNKNOWN`.
- Every Console API method applies a runtime response guard. Cross-field Gate
  state is constrained so only `VERIFIED + completed` may render its verdict;
  route/filter keys abort, clear, and reject late prior-route reads.
- UI paths include independent loading, empty, error, retry, stale, partial,
  and UNKNOWN states. The browser remains read-only; Release Controller retains
  all Quality write authority.
- P0-3 independent verifier passed after two rounds. Evidence is under
  `evidence/p0/p0-3-console/`.

## Proven gaps

- No single command yet proves the complete B1 vertical loop or emits a
  digest-verified final run manifest.
- Existing B1 harness calls admin fault injection/reset directly and can alter
  prompt content without changing the active VersionSet identity. It cannot be
  used as final evidence or as a production Quality writer.
- Experiment completion currently accepts caller-supplied verdict/deltas and
  does not enforce frozen VersionSet/probe/repetition completeness or rerun the
  deterministic attribution decision in the control plane.
- Generic Case transition calls can still self-assert attribution and other
  domain-owned states; Experiment, ChangeSet, Release, and Notification do not
  yet own every Case projection transition transactionally.
- Release Controller lacks an explicit candidate-creation wrapper and a public
  action-approval-context endpoint for canary/promote grants.
- The current canary lifecycle operates on the exact candidate and post-canary
  probes but demo-app `/chat` does not route real traffic by canary percentage;
  evidence must not claim traffic canary until that exists.
- The attribution EvidenceBundle schema is intentionally narrow. P0-4 needs a
  separate final B1 run manifest contract rather than adding unrelated release,
  approval, notification, and Trust fields to it.
- Non-terminal `eval.requested`, `eval.started`, and track-completed events still
  have lifecycle-contract debt tracked as P1; metadata must not be invented.

## Work queue

- `current_only_in_progress`: P0-4 — implement the authority-safe, repeatable B1
  replay loop and complete Evidence Bundle/run manifest.
- `next`: remove B1 experiment write authority from eval-runner; freeze exact
  VersionSets/probes and make attribution completion authoritative.
- Full acceptance criteria, dependencies, tests, evidence, and commits remain
  in `PLANS.md`.

## Actual runnable commands

```bash
git fetch --all --prune
git status --short --branch
git log --oneline --decorate -15 --all

cd control-plane
AUDIT_JSONL_PATH=/tmp/caseloop-control-plane-audit.jsonl \
PYTHONPYCACHEPREFIX=/tmp/caseloop-control-plane-pycache \
DATABASE_URL=postgresql+psycopg://gout@127.0.0.1:5432/control_plane_test \
.venv/bin/python -m pytest -q
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090

cd ../console
npm ci
npm test -- --run
npm run build
npm run test:integration

cd ../contracts
../eval-harness/.venv/bin/python -m pytest \
  conformance/test_schemas.py conformance/test_wilson.py -q
```

If macOS has offloaded an ignored `.venv` as `dataless`, rebuild an isolated
environment instead of treating a blocked import as a test result:

```bash
uv venv /tmp/caseloop-control-plane-verify --python 3.12
uv pip install --python /tmp/caseloop-control-plane-verify/bin/python \
  -r control-plane/requirements.txt
CASELOOP_CONTROL_PLANE_VENV=/tmp/caseloop-control-plane-verify \
  npm --prefix console run test:integration
```

## Latest test results

| Scope | Result | Classification |
|---|---|---|
| control-plane full against explicit test PostgreSQL | 190 passed | unit + integration |
| P0-3 backend focused | 42 passed | read-model integrity/fail-closed |
| demo-app unit | 36 passed | offline unit |
| demo-app PostgreSQL lifecycle concurrency | 2 passed | real PostgreSQL lifecycle |
| eval-harness full | 69 passed, 4 skipped | offline/replay; live skipped |
| MCP full | 54 passed, 1 skipped | offline/unit; live smoke skipped |
| contracts schema + Wilson | 26 passed | offline contracts |
| Console API/runtime/request-race | 7 passed | unit/contract |
| Console real stack | 1 passed | PostgreSQL + Alembic + FastAPI + Vite + Chromium, no interception |
| Console production build | passed | TypeScript + Vite bundle |
| Python compileall, soul-sync, bash syntax, diff check | passed | static verification |
| npm audit | 4 moderate, 1 high | known Router 6/Vite 5 cross-major migration debt; not hidden |

Non-overlapping executable total: **385 passed, 0 failed, 5 live-only skipped**.

## External blockers and credentials

- Docker CLI is installed, but the daemon is currently unavailable.
- `STEPFUN_API_KEY`, `JUDGE_MODEL`, and
  `CASELOOP_B1_FAULT_VERSIONSET_ID` are unset. Live athlete/judge/B1 execution
  is not available and is not reported as passing.
- `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are unset; live Feishu is blocked.
  `feishu-mock` remains contract/replay-only.
- Native PostgreSQL is available but reports no `vector` extension; this does
  not block isolated control-plane/replay work.
- These blockers do not prevent implementing or proving P0-4 replay.
