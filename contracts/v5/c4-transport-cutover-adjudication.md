# C4 generated-transport-cutover adjudication inputs

C4 cut the five transport surfaces over to the C1 generated artifacts
(`contracts/v5/generated/{operation,capability}-manifest.json` +
`contracts/v5/schemas/*.schema.json`) with shadow comparison and explicit
per-surface fallback. This file records the boundary decisions and the
findings that remain open for C5/final closure.

## 1. Python wire surface: generated structure plus semantic validation

The C1 shadow harness now covers all 11 activated intents across every corpus
direction (103 cases; the legacy N/A directions — `applications.list/query`
and the five GET `request` directions — are declared explicitly and still
enforced by the generated validators). The runtime request/response boundary
now validates every activated body and response against the emitted JSON
Schema before running the service-internal Pydantic semantic layer. The C1
divergences recorded in
`contracts/v5/c1-shadow-findings.md` (cross-field constraints, receipt
intent-binding, revision/lifecycle coupling, manifest discriminator) mean the
2020-12 schemas are strictly weaker than the legacy models on several
corners, so Pydantic remains a second fail-closed layer for invariants the
structural schema cannot express. Both layers must pass; neither can override
the other, and responses are structurally validated before commit.

## 2. OpenAPI surface: generated artifact is authoritative, runtime doc is legacy shadow

`contracts/v5/generated/openapi.yaml` (OpenAPI 3.1) is emitted deterministically
by the compiler from the operation manifest + schemas (11 operations, 10
paths, `x-caseloop-{intent,scope,wire-status,delivery-slice}` extensions,
external `../schemas/...` `$ref`s, query parameters for `applications.list`,
no unactivated intents). The runtime `/openapi.json` remains FastAPI-generated
because it is one merged v3/v1/v2 document; generating the full merged doc is
out of C4 scope. Parity is enforced by tests: the v2 slice of
`app.openapi()` must match the generated artifact's path/method/operationId
set, and the route registry gate (`app/api/v5_route_registry.py`) fails
closed on any drift before the V5 router can be imported.

## 3. Router surface: registration facts stay in code, manifest is identity source

`check_registered_v5_routes` asserts the 11 registered routes equal the
manifest `http` entries (method/path/operation_id, both directions) at import
time; on mismatch the hook aborts V5 router import. The seven `_unregistered_*` handlers
stay unregistered (404-pinned by tests).

## 4. TS/console surface: structural guard plus semantic guard

`console/src/lib/generated/applications.list.ts` is a compiler-emitted,
deterministic TS module (types + guards, no new dependencies). The console
runs it before the semantic guard; on disagreement it logs and rejects the
response. The generated guard is structural: cross-record
invariants (workspace matching, unique items, edge endpoints within the
component set, `from != to`) are not expressible in the 2020-12 schemas and
remain the semantic guard's job. Only the `applications.list` surface is a V2
Console consumer;
the v1 console pages are untouched.

## 5. CLI surface: manifest-derived commands with a frozen fallback

The CLI derives its v2 command tree, `_operation_spec` and receipt
resource-kind mapping from the operation manifest (fail-closed on invalid
manifests; `FALLBACK_USED` flag + frozen literal table when the manifest is
undiscoverable outside the repo). Retained Pydantic response models are
semantic validators selected by the generated intent; they are not an
operation/capability source. `system-manifest validate` remains the one
explicit local-utility exception (no wire call, not in the manifest). v1
commands, default major=1 and the explicit `--api-version 2` gate are
unchanged.

## 6. Test comparisons

The fallback-drill tests compare response bytes with `audit_ref` masked (a
fresh unique receipt per request by design); shape/presence are still
asserted. The lane map in `scripts/check_import_graph.py` now classifies
`app.api.v5_route_registry` as transport.

## Verdict

No stop condition triggered: generated artifacts share one operation identity
and closed schema; only activated operations produce routes, CLI commands,
help and discovery entries (allowlist-diff tests pin this against the
generated files); public API/CLI default major unchanged; V3/V4 preserved
paths keep their payload/error/audit/receipt/digest/replay behavior (unit 977
+ 12 PG-gated skips, conformance 547, CLI 118, compiler 18, checker PASS);
authorization/visibility precede disclosure; no generated façade performs a
second domain write or fabricates success. The runtime validation and
merged-OpenAPI decisions above are the documented cutover boundary.
