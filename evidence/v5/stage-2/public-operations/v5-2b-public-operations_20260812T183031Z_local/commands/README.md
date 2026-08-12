# Commands

- Unified V5-2B verifier: `CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test PYTHON=<control-python> CONTRACT_PYTHON=<contract-python> bash scripts/verify_v5_2b.sh`
- Detached dependency install: `cd <detached>/console && npm ci --ignore-scripts`
- Production dependency audit: `cd <detached>/console && npm audit --omit=dev --json`
- Clean-subject checks: `git status --porcelain && git rev-parse HEAD` before and after the detached verifier.

`<control-python>` and `<contract-python>` refer to the repository-pinned local
virtual-environment interpreters. The PostgreSQL URL names the disposable local test
database only. No provider or production credential was used or recorded.
