# CaseLoop CLI

This package is the machine-oriented Stage 1A client for the frozen CaseLoop
public HTTP contract. It currently exposes only:

- `capabilities get`
- `signal submit` and its `report` alias
- `case get` and `case timeline`
- `evidence get`

It does not advertise project bootstrap, source adapters, S1B operations, or
later-stage skeleton intents.

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
