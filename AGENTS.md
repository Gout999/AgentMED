# CaseLoop Engineering Rules

## Authority and safety

- `docs/plan-v3.md`, `contracts/`, and executable tests are the requirements baseline. Resolve real conflicts explicitly; do not let an LLM invent authoritative state.
- PostgreSQL control-plane state is authoritative for lifecycle, permissions, idempotency, approvals, release decisions, and audit. LLM output is limited to suggestions, hypotheses, and candidate artifacts.
- Only Release Controller may call Quality API write endpoints.
- A release must validate the exact immutable WorkOrder, ApprovalGrant, GateReport/evidence binding, target revision, nonce, and expiry. Missing, mismatched, `FAILED`, `INCONCLUSIVE`, `ERROR`, or `UNKNOWN` evidence fails closed.
- Audit failure fails the enclosing business transaction. Never turn exceptions, empty results, skipped live checks, or unknown state into success.
- Phase 1 uses the fixed warm pool. Do not claim dynamic autoscaling.
- Mocks/fakes are allowed only in clearly named unit, contract, or replay tests. Never present them as live-provider or production evidence.
- Report contract/replay and live-provider E2E separately.

## Repository workflow

- Do not force-push, rewrite public commits, delete collaborators' branches, or commit directly on `main`.
- Preserve unrelated local changes. Never weaken assertions or delete tests to make a build pass.
- Each P0 closure gets focused tests, a verifier pass, evidence under `evidence/p0/`, updated context files, and its own semantic commit.
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
  `cd control-plane && DATABASE_URL=postgresql+psycopg://gout@127.0.0.1:5432/control_plane_test .venv/bin/python -m pytest -q`
- Unit/offline suites:
  - `cd demo-app && .venv/bin/python -m pytest tests/unit -q`
  - `cd eval-harness && .venv/bin/python -m pytest -q`
  - `cd mcp-servers && .venv/bin/python -m pytest -q`
  - `cd contracts && ../eval-harness/.venv/bin/python -m pytest conformance/test_schemas.py conformance/test_wilson.py -q`
  - `cd demo-app && DEMO_TEST_DATABASE_URL=postgresql+psycopg://gout@127.0.0.1:5432/control_plane_test .venv/bin/python -m pytest tests/integration/test_quality_concurrency.py -q`
  - `cd console && npm run build`
- Exact live candidate gate:
  `cd eval-harness && .venv/bin/python scripts/run_gate.py --versionset-id <id> --out-dir evidence/gate-<id>`
- Live contract/integration tests require their documented services and credentials. A connection failure is a failed live prerequisite, not a pass.

## Definition of done

A capability is done only when the real call path is implemented, authoritative records and audit evidence are persisted transactionally, idempotency and fail-closed behavior are tested, relevant unit/integration/contract/replay suites pass, live-provider status is reported honestly, verifier findings are resolved, and `PLANS.md`, `docs/context/PROJECT_STATE.md`, and `docs/context/LAST_HANDOFF.md` identify the evidence and completion commit.

`NOTIFICATION_ADAPTER=feishu-mock` is contract/replay-only. The default is disabled and must fail closed; never describe it as live Feishu.
