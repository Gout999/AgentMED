# V5-2B public operations verification

- Run: `v5-2b-public-operations_20260812T183031Z_local`
- Semantic subject: `8c71d245137acf69a667104a0f3c833de9416bf9`
- Branch: `codex/v5-convergence`
- Verdict: local contract/replay **PASS**; real shell-capable Agent CLI exit **NOT_RUN**; V5-2C remains **LOCKED**.

## Implemented boundary

The subject activates `investigations.start` and `operations.get/list/cancel-request`
on explicit API major 2. `AutomationRequest` is a durable admission/stop-request
projection bound by PostgreSQL foreign keys to the exact case binding, application,
environment, requester and V5-2A WorkTask. It does not own execution or terminal
state. Reads revalidate the current AutomationRequest, WorkTask and WorkAttempt
digest plus authority-receipt/event chain before projecting an operation.

The CLI exposes `case investigate`, `operation get/list/cancel/wait/follow`.
Timeout and Ctrl-C stop only the client wait. Cancel creates a durable stop request;
it never fabricates terminal cancellation. `COMPLETED` requires a receipt-bound
structured artifact and does not mean Gate or Release PASS.

## Verification result

| Check | Result |
|---|---|
| Compiler + all non-live contract conformance | 578 passed; activated operation/capability parity 23/23 |
| Control-plane unit + C1-C5 wave checks | 1058 passed; 14 explicit PostgreSQL-gated skips |
| Import graph | PASS; 0 cycles; 100% lane coverage; 0 direct-table violations |
| CLI | 133 passed |
| Console | 20 passed; production build PASS |
| Disposable PostgreSQL migrations | 14 passed; 33 deselected |
| Disposable PostgreSQL runtime matrix | 19 passed |
| Exact detached subject | verifier 4/4 PASS; worktree clean before and after |

The PostgreSQL operation tests prove same-key concurrent admission produces one
operation, a fresh engine/session observes the durable operation after pool disposal,
cancel remains a request, and completion-versus-cancel serialization yields one
authoritative outcome without a false artifact.

## Stop gate

This run did not launch an independent external Agent or provider. The CLI tests use
the real generated models/client semantics with an in-process mock HTTP transport;
the API proof uses FastAPI TestClient with the real credential resolver and database
transaction seam. Those are local contract/replay evidence, not `agent-causal`.

The next authorized action requiring an actual test is the V5-2B Exit journey:

1. start a live local control-plane on disposable PostgreSQL;
2. issue an isolated external-Agent credential with only the required V2 scopes;
3. have a real shell-capable external Agent invoke the built CLI to start an investigation;
4. disconnect the CLI process without cancellation, reconnect by operation id, and observe the same durable operation and structured artifact;
5. preserve the audit/event/authority/idempotency evidence and revoke the test credential.

Until that journey passes, `agent-causal=NOT_RUN` and V5-2C Entry is locked. No MCP/A2A
adapter, provider call, paid service, human approval, production write, push or PR was
performed.

## Known non-blocking observations

- Detached `npm ci` reported 5 advisories including dev dependencies. A separate
  `npm audit --omit=dev` reported 2 moderate production dependency advisories.
  This stage does not modify `console/package.json` or `console/package-lock.json`.
- `contracts/conformance/test_quality_api.py` is an intentionally live prerequisite
  for the separate legacy demo-app Quality API. It was not run by the V5-2B verifier
  and is not counted as a pass.
