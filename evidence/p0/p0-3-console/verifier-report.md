# P0-3 Independent Read-only Verifier

- Result: **PASS**
- Verified at: `2026-08-09T01:12:00+10:00`
- Base: `02b97ddee82e53b5d2e62fababc841ac3d1acc35`
- Collaborator base: `origin/main=a6de5cc1a06d6967634676b2661da7d2e46d287b`

The second verifier pass found no P0-3 blocker, production fixed-PASS path,
fixture masquerading as production data, browser write authority, raw
WorkOrder payload/Evidence value exposure, or new permission bypass.

## Findings closed

1. WorkOrder row identity, Case, and channel are checked against the immutable
   JCS-hashed payload; a mismatch hides untrusted projection fields.
2. Malformed active VersionSet responses fail closed in the backend, runtime
   API guard, and TopBar.
3. WorkOrder list responses omit the complete payload; Evidence responses omit
   raw values and inline diff content.
4. Route/filter request keys abort and clear prior reads. A deterministic late
   response test and a real mounted-route switch test cover stale-data races.
5. `AWAITING_APPROVAL` is mapped using the backend contract name.
6. Gate reports expose `VERIFIED`, `UNBOUND`, or `UNKNOWN`. Cross-field runtime
   validation rejects an `UNKNOWN + completed + passed` object, and only a
   `VERIFIED` binding can use a green verdict in the UI.
7. Scratch database creation and deletion use explicit admin host, port, user,
   and maintenance database parameters.
8. Every production API client method validates the response shape at runtime.
9. `promotion_eligible=false` is labelled `not eligible`, separately from a
   real Trust denial audit.

## Independent commands and results

- Backend focused read projections: `42 passed`.
- Console unit/API/request-race tests: `7 passed`.
- Console production build: passed.
- `bash -n console/scripts/run-real-stack-test.sh`: passed.
- `git diff --check`: passed.
- Existing real-stack evidence: `1 passed`; no route interception.
- Trace SHA-256:
  `46f3876766b0974f14b1d8263a68e5265b5212c38e898bc977292db0849dbcfa`.
- Trace-embedded spec and current spec SHA-256:
  `4e9929f4d731ba7aa48e9779c90f73dca111d8147d576bd6ecff57cb88596f8f`.
- No remaining listener on ports `18090` or `5173`; no P0-3 scratch database.

Live Quality/StepFun and live Feishu were not exercised and are not reported
as passing. Their status remains in `live-provider-report.md`.
