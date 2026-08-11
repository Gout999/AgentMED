# C3 capability/import-cycle adjudication inputs

C3 eliminated the import cycles, wired capability decisions to the C1
activated-operation output, added a mechanically checked module/lane map and
removed dead cross-owner code and global session fallbacks. This file records
the findings that were deliberately **not** forcibly unified, because
unifying them would change observable bytes, cardinality, error codes or
accepted behavior. Each entry is a C4/C5 adjudication input.

## 1. Coordinator still writes Environment/DependencyEdge directly

`V5ManifestImportCoordinator._record_owned_catalog_record` continues to write
environment/dependency-edge rows itself instead of calling
`ApplicationCatalogService.register_environment/record_dependency_edge`. A
function-level comparison showed the two paths differ in seven observable
ways, so direct consolidation would change bytes/cardinality:

1. transaction_id: the coordinator reuses the manifest-level transaction_id
   for every child audit/event/receipt; the catalog service allocates a fresh
   one per record.
2. composed-event path: the coordinator consumes the composition capability
   and emits `append_composed_manifest_record_event`; the catalog service
   uses the plain `append_event` (no capability consumption).
3. idempotency/command-audit cardinality: the catalog service adds one
   idempotency receipt and one command audit per record; the coordinator
   intentionally does not (manifest-level idempotency is owned by
   SystemVersionsService).
4. controller-audit trace_id: catalog passes `trace_id=request_id`; the
   coordinator does not.
5. error-code surface: `INTERNAL_ERROR` vs `v5.manifest.controller_invalid` /
   `v5.manifest.composition_failed`; `RESOURCE_NOT_FOUND` vs
   `v5.manifest.environment_application_not_active` /
   `v5.manifest.edge_endpoint_invalid`.
6. trust-role set: the coordinator allows `trusted_builder`; the catalog
   `_CATALOG_TRUST_ROLES` does not.
7. pre-checks: the catalog service rejects duplicate env names and
   self/cyclic edges (with graph advisory lock); the manifest path relies on
   bootstrap-empty + workspace lock and currently accepts cyclic manifests.

Recommended direction: add a "composed manifest mode" to
`ApplicationCatalogService` (injected manifest transaction_id, no per-record
idempotency/command audit, composed-event path, `v5.manifest.*` error codes)
instead of reusing the public register methods. That is new interface work for
C4/C5, not a C3 rename.

## 2. Function-level import edges retained for the composition primitive

`v4_event_store` and `v5_lifecycle_authority` import
`consume_activation_composition_capability` from `app.services.v5_composition`
at function level. Hoisting them to module level would create a module-level
cycle (`v5_composition → v5_authority → v4_event_store → v5_composition`), so
the function-level edges stay. The checker (`scripts/check_import_graph.py`)
now proves: zero module-level cycles, zero function-level cycles, 100% lane
coverage, zero direct-table-access violations (pg_advisory/alembic_version
whitelisted).

## 3. Capability table is manifest-derived but `v5_capability_models` stays authoritative

`v5_capabilities.py` now derives the 11-intent allowlist from
`contracts/v5/generated/operation-manifest.json` (fail-closed on missing or
inconsistent manifests; errors `v5.capabilities.operation_manifest_unavailable`
/ `_invalid`). The wire shape, filter semantics (scope/principal/trust-roles),
`V5EnabledIntent` validators and audit action are unchanged, so the
capabilities response bytes and cardinality are identical to the previously
hardcoded table (9 unit tests pin this). Note: the derived table adds an
explicit `execution_mode` key per intent; output bytes are unchanged.

## 4. Dead code kept (low risk)

- `system_versions._build_projection_row`: the four catalog branches
  (AI_APPLICATION/ENVIRONMENT/SYSTEM_COMPONENT/DEPENDENCY_EDGE) were
  unreachable and were deleted. The four `_SPECS` catalog entries remain
  (same unreachable status) and should be cleaned in C5.
- `db.session_scope` remains (zero control-plane callers, keeps a
  `get_session_factory()` fallback); it is outside the C3 allowlist.

## 5. Test adaptation

`tests/unit/test_public_v4_api.py` streaming fixture now sets
`app.state.session_factory` explicitly. Previously it relied on the deleted
global `get_session_factory()` fallback, which resolved to the real PG dev
database URL — the adaptation removes a hidden unit-test dependency on PG
without weakening any assertion.

## Verdict

No stop condition triggered: cycles eliminated (mechanically checked), no
global-session fallback in public paths, no new cross-owner table access,
capability resolution consumes the C1 activated-operation set and cannot
activate an uncompiled operation (manifest fail-closed), single-UoW and
flush-only semantics preserved (876 unit + 547 conformance + checker PASS).
The coordinator decomposition into application/domain/repository layers and
the composed-manifest catalog mode are the C4/C5 continuation of this file.
