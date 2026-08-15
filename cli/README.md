# AgentMED CLI

This package is the machine-oriented client for the frozen V4 Stage 1A public
contract and the current, explicitly selected V5-1 runtime overlay.

Default/V1 commands:

- `capabilities get`
- `signal submit` and its `report` alias
- `case get` and `case timeline`
- `evidence get`

With `--api-version 2`, the client also exposes the currently allowlisted V5-1
commands for capability discovery, Application/Environment/SystemComponent/
DependencyEdge register/get, one-shot system manifest import/get/diff, Case
application binding, AcceptanceCriteria propose/get/confirm, `init`, and
`case from-issue`. Standalone second-version `system-versions.record`, V5-2+
operations, Public MCP/A2A, approval, and release are not implemented.

## Install and configure

```bash
python -m pip install ./cli
```

A profile contains identifiers and an endpoint, never a bearer token:

```yaml
# Native uvicorn default. The repository Compose profile publishes the same
# control plane at http://127.0.0.1:18090.
api_url: http://127.0.0.1:8090
workspace_id: ws_01J0000000000001
source_id: src_01J0000000000001
reporter_ref: maintainer-01J0000000000001
token_env: AGENTMED_PUBLIC_TOKEN
```

Remote endpoints require HTTPS. Plain HTTP is accepted only for loopback
development. Credentials may come from the selected environment variable,
`--token-stdin`, or a current-user-owned, non-symlink regular file with exact
mode `0600` passed through `--token-file`. The token itself is never accepted
as an argument or profile field.

```bash
agentmed --profile .agentmed/config.yaml capabilities get

agentmed --profile .agentmed/config.yaml --api-version 2 capabilities get

agentmed --profile .agentmed/config.yaml signal submit \
  --summary "The agent chose the wrong tool" \
  --body "No trace is available" \
  --privacy INTERNAL
```

The local V5 first-case management flow, including credential rotation and
fresh owner reauthentication, is documented in
[`control-plane/V5_FIRST_CASE_LOCAL.md`](../control-plane/V5_FIRST_CASE_LOCAL.md).
Credential issuance is deliberately not a public CLI or HTTP capability.

The first signal slice is deliberately a no-trace maintainer report and permits
only `PUBLIC` or `INTERNAL` content. When an idempotency key is supplied
explicitly, `--source-event-id` and `--occurred-at` must also be explicit.
Automatically generated event data and request bytes remain unchanged across
the bounded transport retry loop.

Successful responses are stable JSON on stdout. Machine-classified failures
are stable JSON on stderr. Exit families are: `0` success, `2` input, `3`
configuration, `10` authentication/authorization, `11` not found, `12`
conflict, `20` temporary, `21` remote failure, and `22` protocol failure.
