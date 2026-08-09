# D-005: Isolated B1 Replay and Evidence Manifest

- Status: Accepted
- Date: 2026-08-09
- Scope: Phase 1 P0-4

## Context

The B1 acceptance proof must traverse the production deterministic services,
including attribution authority, Gate binding, Release Controller operations,
transactional outbox, notification receipts, archive, and Trust accounting.
The local environment does not have live StepFun/judge credentials, immutable
live B1 VersionSet ids, live Feishu credentials, or human approval authority.

A replay that labels missing provider execution as live success would violate
the fail-closed Gate contract. Conversely, requiring external credentials for
every contract test would make the deterministic control-plane closure
unrepeatable and would conceal which boundary was actually proven.

## Decision

1. The default Gate policy remains `live`. A failed, skipped, unavailable,
   inconclusive, error, or unknown provider track cannot authorize release.
2. `isolated-replay` is a separate, explicit Gate policy used only by the B1
   replay command. It accepts a provider track only when that track says
   `skipped` with provider `replay-not-live` or `external-blocked`, while rule,
   judge, and deterministic replay tracks must independently pass. A live
   candidate cannot use this profile. The profile is part of the hashed
   GateReport, must match controller configuration, requires an explicit
   `ALLOW_ISOLATED_REPLAY_GATE=true`, and is rejected unless the controller is
   using its isolated SQLite database.
3. The replay runner uses recorded provider responses, a deterministic recorded
   judge, a contract-only Quality lifecycle adapter, and a Feishu mock adapter.
   These adapters implement the production contracts; state transitions still
   go through production control-plane services and transactional persistence.
4. The live command never falls back to replay. Missing credentials, ids,
   approvals, deployed release authority, or Feishu integration produce a
   machine-readable `blocked` report and a non-zero exit.
5. A self-contained `B1RunManifest` indexes every required artifact by relative
   reference, media type, and SHA-256 digest. Validation reloads the artifacts
   and rechecks their bindings; embedding a success string is not evidence.
6. Contract/replay and live-provider reports remain separate. Phase 1 continues
   to use a fixed warm/dispatcher pool and makes no dynamic-scaling claim.

## Consequences

- `make demo-b1-replay` is deterministic and repeatable without external
  secrets, but its output is explicitly not live-provider evidence.
- `make demo-b1-live` is an honest external integration probe and remains
  blocked until real authority and credentials are supplied.
- Production Gate behavior is unchanged and fail-closed. Selecting an unknown
  policy profile, omitting a track, or presenting a live candidate as replay is
  rejected.
- Release, notification, audit, outbox, Trust, and archive evidence can be
  verified as one causal run without granting an evaluator Quality write access.
