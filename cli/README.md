# CaseLoop CLI

This package is the machine-oriented client for the frozen V4 Stage 1A public
contract and the explicitly selected R2 V5 overlay.

Default/V1 commands:

- `capabilities get`
- `signal submit` and its `report` alias
- `case get` and `case timeline`
- `evidence get`

With `--api-version 2`, capability discovery advertises only the implemented
R2 public surface: Application register/get/list, Environment, SystemComponent
and DependencyEdge register/get, plus authenticated one-shot SystemManifest import. Local manifest
validation performs no HTTP request, needs no credential, is not a server capability, and does not
prove server acceptance.

Standalone Application/SystemComponent activation, standalone second-version
recording, system-version read/diff discovery, Case/Acceptance workflows,
V5-2+, approval and release are not R2 capabilities and are not advertised.
The R2 CLI exposes only `system-manifest validate` and `system-manifest import`;
the former `record`, version `get`, and `diff` actions are intentionally absent.

## Install and configure

```bash
python -m pip install ./cli
```

A profile contains identifiers and an endpoint, never a bearer token:

```yaml
api_url: http://127.0.0.1:8090
workspace_id: ws_01J0000000000001
source_id: src_01J0000000000001
reporter_ref: maintainer-01J0000000000001
token_env: CASELOOP_PUBLIC_TOKEN
```

Remote endpoints require HTTPS. Plain HTTP is accepted only for loopback
development. Credentials may come from the selected environment variable,
`--token-stdin`, or a current-user-owned, non-symlink regular file with exact
mode `0600` passed through `--token-file`. The token itself is never accepted
as an argument or profile field.

```bash
caseloop --profile .caseloop/config.yaml capabilities get

caseloop --profile .caseloop/config.yaml --api-version 2 capabilities get

caseloop --profile .caseloop/config.yaml --api-version 2 application list \
  --project-id proj_... --limit 50

caseloop --profile .caseloop/config.yaml signal submit \
  --summary "The agent chose the wrong tool" \
  --body "No trace is available" \
  --privacy INTERNAL
```

The first signal slice is deliberately a no-trace maintainer report and permits
only `PUBLIC` or `INTERNAL` content. When an idempotency key is supplied
explicitly, `--source-event-id` and `--occurred-at` must also be explicit.
Automatically generated event data and request bytes remain unchanged across
the bounded transport retry loop.

Successful responses are stable JSON on stdout. Machine-classified failures
are stable JSON on stderr. Exit families are: `0` success, `2` input, `3`
configuration, `10` authentication/authorization, `11` not found, `12`
conflict, `20` temporary, `21` remote failure, and `22` protocol failure.
