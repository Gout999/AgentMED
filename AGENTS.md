# CaseLoop Engineering Rules

## Authority and safety

- `docs/product-principles.md` is the product-strategy and scope baseline. Under D-015, `docs/plan-v5.md` + `docs/plans/v5-progressive-delivery.md` + the frozen `contracts/v5/` namespace are the default design and construction baseline for all new product and domain development. `docs/plan-v4.md` is a V4 compatibility baseline only. `docs/plan-v3.md`, `docs/spec.md`, existing v3 contracts, migrations, and executable tests form the implemented v3 compatibility lane until an explicit migration, contract, route, and test replace each boundary. A plan does not prove a capability is implemented or live. Research reports, competition material, and handoff snapshots do not override these layers. Resolve real conflicts explicitly; do not let an LLM invent authoritative state.
- V5 construction baseline (accepted 2026-08-11): the frozen `contracts/v5/` namespace includes the V5-0B freeze commit `8dd25ca` (system governance ownership) and the V5-0C freeze commit `b3727d7` (first system wire slice), independently accepted on 2026-08-11. Making V5 the default for new design and construction does not change the public API or CLI default major, activate any route or capability, retroactively rewrite V3/V4 compatibility behavior, or prove the complete V5 runtime. Every runtime facet remains `NOT_RUN` until its real path produces the required evidence.
- Architecture convergence is a prerequisite to further domain expansion. D2, R3, R4, and V5-2+ are paused until the convergence gate closes. The target is a modular monolith with one PostgreSQL unit of work for each authoritative business transaction; compatibility facades, shared helpers, projections, transports, and adapters may preserve or translate behavior but may not own domain success. Each convergence wave must preserve public wire behavior, authorization order, error semantics, transaction boundaries, event/outbox/receipt/audit bytes and cardinality, idempotency, and replay behavior; do not combine a behavior-changing feature or contract migration with a structural wave.
- V5 stages are built one at a time following the blueprint dependency graph after applicable convergence gates are closed. Each stage requires focused tests, a verifier pass, evidence under `evidence/v5/`, and its own semantic commit before the next stage may start. Push, PR, paid provider calls, human approvals, and production or other external writes still require their own authorization. Honest uncertainty belongs in commit bodies and `evidence/`, never hidden from a stage report.
- Product scope is driven by target-user needs. Existing open-source or commercial implementations are references, integration candidates, or lawful reuse candidates; overlap and market competition are not reasons to omit a needed capability. Preserve license notices and use an independent implementation when reuse is not license-compatible.
- PostgreSQL control-plane state is authoritative for lifecycle, permissions, idempotency, approvals, release decisions, and audit. LLM output is limited to suggestions, hypotheses, and candidate artifacts.
- Only Release Controller may call Quality API write endpoints.
- A release must validate the exact immutable WorkOrder, ApprovalGrant, GateReport/evidence binding, target revision, nonce, and expiry. Missing, mismatched, `FAILED`, `INCONCLUSIVE`, `ERROR`, or `UNKNOWN` evidence fails closed.
- Audit failure fails the enclosing business transaction. Never turn exceptions, empty results, skipped live checks, or unknown state into success.
- The v3 Phase 1 reference implementation uses the fixed warm pool. Do not claim dynamic autoscaling.
- Mocks/fakes are allowed only in clearly named unit, contract, or replay tests. Never present them as live-provider or production evidence.
- Use one canonical evidence-facet vocabulary and report each facet separately: `contract`, `replay`, `domain-provider-live`, `agentteams-native`, `claude-runtime-live`, `agent-causal`, `repo-sandbox`, `human-authorized-external`, and `production-canary`. Platform evidence export is a read-only evidence category, not a success facet and never a substitute for `agent-causal`.

## Repository workflow

- Do not force-push, rewrite public commits, delete collaborators' branches, or commit directly on `main`.
- Preserve unrelated local changes. Never weaken assertions or delete tests to make a build pass.
- Each P0 or v4 Stage closure gets focused tests, a verifier pass, evidence under `evidence/p0/` or `evidence/v4/stage-<n>/`, updated context files, and its own semantic commit.
- Use migrations for persistent schema changes. Do not rely on development-only `create_all` as the deployment path.

## Local setup and commands

- Python services use a local `.venv` and their pinned `requirements*.txt`.
- Console uses `npm ci`, `npm run build`, and its configured test command once present.
- Control-plane database migration:
  `cd control-plane && .venv/bin/alembic upgrade head`
- Control-plane startup:
  `cd control-plane && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8090`
- Fixed Phase-1 outbox worker:
  `cd control-plane && .venv/bin/python -m app.workers.outbox`
- Demo app startup is normally through `docker compose -f deploy/compose.yaml up -d --build postgres demo-app`; live use requires `STEPFUN_API_KEY`.
- Control-plane tests must use an explicit disposable database, for example:
  `cd control-plane && CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test .venv/bin/python -m pytest -q`
- Unit/offline suites:
  - `cd demo-app && .venv/bin/python -m pytest tests/unit -q`
  - `cd eval-harness && .venv/bin/python -m pytest -q`
  - `cd mcp-servers && .venv/bin/python -m pytest -q`
  - `cd contracts && ../eval-harness/.venv/bin/python -m pytest conformance/test_schemas.py conformance/test_wilson.py conformance/test_v4_*.py -q`
  - `cd demo-app && DEMO_TEST_DATABASE_URL=postgresql+psycopg://gout@127.0.0.1:5432/control_plane_test .venv/bin/python -m pytest tests/integration/test_quality_concurrency.py -q`
  - `cd console && npm run build`
- Exact live candidate gate:
  `cd eval-harness && .venv/bin/python scripts/run_gate.py --versionset-id <id> --out-dir evidence/gate-<id>`
- Live contract/integration tests require their documented services and credentials. A connection failure is a failed live prerequisite, not a pass.

## Definition of done

A capability is done only when the real call path is implemented, authoritative records and audit evidence are persisted transactionally, idempotency and fail-closed behavior are tested, relevant unit/integration/contract/replay suites pass, live-provider status is reported honestly, verifier findings are resolved, and `PLANS.md`, `docs/context/PROJECT_STATE.md`, and `docs/context/LAST_HANDOFF.md` identify the evidence and completion commit.

`NOTIFICATION_ADAPTER=feishu-mock` is contract/replay-only. The default is disabled and must fail closed; never describe it as live Feishu.
