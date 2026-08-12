# Commands

- unit: env -u CASELOOP_ALLOW_INTEGRATION_RESET -u DATABASE_URL python -m pytest tests/unit/test_v5_work_kernel.py tests/unit/test_v5_work_dispatcher.py tests/unit/test_v5_work_fixture_executor.py -q
- postgres: CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:55432/control_plane_test python -m pytest tests/integration/test_v5_work_kernel_postgres.py -q
- gate: PYTHON=control-plane/.venv/bin/python CONTRACT_PYTHON=eval-harness/.venv/bin/python bash scripts/verify_convergence.sh
