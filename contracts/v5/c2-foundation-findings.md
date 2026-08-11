# C2 foundation-extraction adjudication inputs

C2 extracted closed record primitives, major-aware event specifications,
exact-binding validation and the canonical graph verifier into
`control-plane/app/foundation/` (records, events, bindings, receipts, graph).
The foundation defines data and verification mechanics only and never imports a
domain service, API, CLI, Console or adapter (enforced by
`control-plane/tests/test_v5_c2_foundation.py`). Existing
event/outbox/audit/receipt/idempotency bytes and cardinality are unchanged
(control-plane unit baseline 876 passed + 12 PG-gated skips, identical to
R2/C1).

This file records the findings that were **not** forcibly unified during C2,
because unifying them would change observable error semantics, cardinality or
wire bytes. Each is a C3/C5 adjudication input, not a hidden debt.

## 1. system_versions.py inline graph validation retained (5 sites)

`_validate_receipt_backed_record`, `_validate_lifecycle_graph_subject`,
`_require_exact_binding`, the child-binding traversals and the manifest
receipt/event/audit/outbox cardinality block all fail with
`SystemVersionsError` (with `rollback_required=True`) and/or depend on
`authority.validate_receipt_binding` / a manifest-specific cardinality formula
(`7 + 3n + edges + 3`). None is byte-equivalent to a foundation primitive, so
all were kept. To consolidate in C3, the foundation contract needs:
(a) an expected-value exact-binding assertion primitive; (b) an explicit
`GraphVerificationError.failure_kind → SystemVersionsError reason` mapping
layer preserving `rollback_required=True`; (c) decoupling the pure
record-chain walk from authority receipt/lifecycle validation.

## 2. v5_authority lifecycle-chain check not switched to graph.verify_lifecycle_chain

`_validate_lifecycle_history_row` performs more than the foundation verifier:
cryptographic digest re-verification (`assert_v5_record_digest`), the full
envelope field set (`schema_version`/`immutable`/`hash_rule` markers),
revision-1 `REGISTERED` semantics, `SYSTEM_COMPONENT` application_id
continuity and scalar envelope↔row binding. `graph.verify_lifecycle_chain` is
a strict subset (it compares stored strings only). Switching would drop
checks and change error codes; the service method was retained and its
envelope part now uses `foundation.records` primitives.

## 3. v5_lifecycle_authority inline closed-shape check retained

The `set(previous) != {kind,id,revision,digest}` check (error code
`v5.lifecycle.activation_previous_invalid`, pinned by a unit test) is weaker
than `bindings.validate_binding` (which also enforces kind enum, revision>=1,
digest pattern). Replacing it would change the error type/code for malformed
input, so it was kept. `application_catalog.py` has no inline binding-shape
checks (all binding resolution goes through `V5AuthorityService`).

## 4. require_exactly_one: single implementation (resolved)

`app/foundation/graph.py` is the canonical implementation
(`GraphVerificationError("cardinality")`). `app/foundation/events.py` keeps a
legacy shim that delegates to it and translates the cardinality failure to
`V4EventIntegrityError(what)` so stage-1 callers keep their exact error codes.
Verified by `test_require_exactly_one_single_implementation`.

## 5. Additive signature changes (documented)

- `events.validate_exact_binding(value, *, contract_major)` — `contract_major`
  must be 2; otherwise fail-closed with the new code
  `v5.exact_binding_major_unsupported`. Existing paths pass 2 and are
  byte-identical.
- Route tables are 16 v4 + 11 v5 entries (exploration estimated 14+12); counts
  follow the moved source verbatim.
- Event type names may repeat across majors; the major is distinguished by the
  route record type and version markers, not by the name (asserted in
  `test_events_route_tables_are_major_aware`).
- `foundation.graph` maps cross-owner violations to
  `failure_kind="cross_workspace"` (the contract has no separate
  `cross_owner` kind); the detail string marks `cross-owner`.

## 6. Deferred cleanups (C5 candidates)

- `v5_lifecycle_authority.py` still carries its own inline envelope validation
  (`_RECORD_ENVELOPE_FIELDS` + `assert_v5_record_digest`); unify with
  `foundation.records` in a later wave.
- The JCS canonicalization implementations in `app/utils/v4_integrity.py` /
  `v5_integrity.py` and `contracts/conformance/v4_integrity.py` remain twin
  implementations; byte consistency is anchored by the golden-digest test
  (`test_record_digest_is_deterministic_and_self_excluding`).

## Verdict

No stop condition triggered: the foundation imports no domain service/API/CLI/
Console/adapter; event specs preserved; wire bytes, cardinality, error codes
for existing paths and the transaction boundaries are unchanged; V3/V4/V5
behavior regression is green (unit 876 + 12 skips; conformance 547; C1 shadow
parity 103 cases). The retained in-service checks are documented above for the
C3 decomposition wave.
