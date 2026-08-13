# Real CLI Exit command record

All commands used a temporary local PostgreSQL database and a loopback HTTP server.
The bearer value is intentionally omitted from this record.

```text
embedded PostgreSQL 18.1 -> 127.0.0.1:55432/control_plane_real_cli_test
alembic upgrade head -> 015
uv run --isolated --with './cli' caseloop --api-version 2 case investigate <case_id> --case-digest <case_digest> --idempotency-key real-agent-cli-start-20260813-2
uv run --isolated --with './cli' caseloop --api-version 2 case investigate <case_id> --case-digest <case_digest> --idempotency-key real-agent-cli-start-20260813-2 --follow --timeout-seconds 60
# Ctrl-C sent to the second process; result: detached=true, no cancel request
uv run --isolated --with './cli' caseloop --api-version 2 operation follow op_01KZWF4TJFVZZ6QRE2M33G2F1Z --timeout-seconds 30
uv run --isolated --with './cli' caseloop --api-version 2 operation get op_01KZWF4TJFVZZ6QRE2M33G2F1Z
uv run --isolated --with './cli' caseloop --api-version 2 operation list --limit 10
# Repeated case investigate with the same key returned idempotency.replayed=true
# Credential state was then changed to REVOKED in the disposable database.
uv run --isolated --with './cli' caseloop --api-version 2 operation get op_01KZWF4TJFVZZ6QRE2M33G2F1Z
# Result after revocation: exit 10, TOKEN_REVOKED
```

The completion step used the existing `WorkKernelService` directly in a separate
local process. It did not call a provider, model, MCP server or production endpoint.
