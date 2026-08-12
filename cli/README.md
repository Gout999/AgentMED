# CaseLoop CLI

This package is the machine-oriented client for the frozen V4 Stage 1A public
contract and the activated V5 public-operation manifest.

Default/V1 commands:

- `capabilities get`
- `signal submit` and its `report` alias
- `case get` and `case timeline`
- `evidence get`

With `--api-version 2`, capability discovery and the generated operation
manifest expose the implemented 23-operation surface: Application Catalog,
SystemManifest import, SystemVersion record/get/diff, Case binding and
Acceptance Criteria workflows, plus V5-2B investigation and durable Operation
commands. Local manifest validation performs no HTTP request, needs no
credential, is not a server capability, and does not prove server acceptance.

Approval and release operations remain unavailable and are not advertised.
An Operation reaching `COMPLETED` means that its Work attempt has a trusted,
receipt-bound structured artifact; it does not mean that a Gate or Release has
passed. `operation cancel` records a stop request. Stopping `wait` or `follow`
with Ctrl-C only detaches the client and never sends that request.

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

caseloop --profile .caseloop/config.yaml --api-version 2 case investigate \
  case_... --case-revision 1 --case-digest sha256:... \
  --instructions "Investigate the regression" --follow

caseloop --profile .caseloop/config.yaml --api-version 2 operation get op_...

caseloop --profile .caseloop/config.yaml --api-version 2 operation list --limit 50

caseloop --profile .caseloop/config.yaml --api-version 2 operation follow \
  op_... --timeout-seconds 300

caseloop --profile .caseloop/config.yaml --api-version 2 operation cancel \
  op_... --reason "Operator requested a stop"

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
