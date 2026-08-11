# contracts/v5/generated — deterministic V5 transport authority

These files are produced by the C1 activated-operation compiler
(`contracts/compiler/`), which reads `contracts/v5/intent-registry.yaml` and
`contracts/v5/schemas/*.schema.json` and emits the activated-operation and
capability manifests. After C5 remediation, these artifacts are effective for
activated-operation identity, route/capability/CLI discovery, OpenAPI and
structural wire validation. Runtime semantic validators remain an additional
fail-closed layer for cross-field and cross-record invariants; they cannot
override a generated rejection.

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
- `capability-manifest.json` — the effective capability discovery source
  (name/scope/execution_mode with http=true and cli=true).

Generation does not activate an intent by itself. Only registry entries that
already satisfy the frozen activated-operation rule enter these files, and the
runtime fails closed if registered routes or capability discovery drift from
the emitted set.
