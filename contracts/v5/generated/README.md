# contracts/v5/generated — C1 compiler output (deterministic candidates)

These files are produced by the C1 activated-operation compiler
(`contracts/compiler/`), which reads `contracts/v5/intent-registry.yaml` and
`contracts/v5/schemas/*.schema.json` and emits the activated-operation and
capability manifests. They are **candidate single sources** for C4 transport
cutover and for shadow-parity comparison with the legacy
`control-plane/app/services/v5_capabilities.py` allowlist and route decorators.

## Regeneration

```bash
cd contracts/compiler && python3 -m compiler emit
# or from the repository root:
PYTHONPATH=contracts python3 -m compiler emit
```

`python3 -m compiler check` validates inputs and prints the activated set
without writing. Compiler output is deterministic: re-running `emit` must
reproduce byte-identical files (no timestamps, no absolute paths, no
environment identity). A regeneration that changes bytes requires a new
semantic commit plus an adjudicated parity finding; it is not a silent update.

## Files

- `operation-manifest.json` — one entry per activated intent with registry
  metadata (method/path/operation_id/scope/principal/idempotency/capability)
  and JSON Schema 2020-12 request/response/error references.
- `capability-manifest.json` — the candidate capability discovery surface
  (name/scope/execution_mode with http=true and cli=true), mirroring the
  legacy `v5_capabilities.py` table.

This is not a route/capability activation. `activation_flags` in the intent
registry remain false; legacy validators and routes stay authoritative until
C4.
