# Commands

- Focused Work: `cd control-plane && env -u CASELOOP_ALLOW_INTEGRATION_RESET -u DATABASE_URL <control-python> -m pytest tests/unit/test_v5_work_kernel.py tests/unit/test_v5_work_dispatcher.py tests/unit/test_v5_work_fixture_executor.py tests/unit/test_v5_work_state_machine_conformance.py -q`
- Work PostgreSQL: `cd control-plane && CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:55432/control_plane_test <control-python> -m pytest tests/integration/test_v5_work_kernel_postgres.py -q`
- Migration PostgreSQL: `cd control-plane && CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:55432/control_plane_test <control-python> -m pytest tests/unit/test_migrations.py -k postgresql -q`
- Import graph: `cd control-plane && <control-python> -m pytest tests/test_v5_c3_import_graph.py -q && <control-python> scripts/check_import_graph.py`
- Compose: render `deploy/compose.yaml` with test-only placeholder values for all required secrets and `docker compose ... config --quiet`.
- Unified gate: `CASELOOP_ALLOW_INTEGRATION_RESET=true DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:55432/control_plane_test PYTHON=<control-python> CONTRACT_PYTHON=<contract-python> bash scripts/verify_convergence.sh`

`<control-python>` and `<contract-python>` denote the repository-pinned virtual-environment interpreters. No credential value is recorded in evidence.
