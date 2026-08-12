# Independent verifier report

- Verifier: `Kimi Code independent sub-agent (D2 gate POST_COMMIT_VERIFY, remediation re-review)`
- Subject: `4852664fe137c18ebc9a795e1740019391f95061`
- Direct parent: `5717124ad9ad309d035276c17af199374e64b030`
- D2 cumulative base: `d8f876a993167cc08b47a987df3c0e8ca31eefec`
- Verdict: **PASS**
- P0: `0`
- P1: `0`

Re-review of the D2 remediation commit. The detached checkout is clean at the subject commit
(only the evidence bundle directory is untracked); the remediation is a 3-file delta over the
previously verified subject `5717124` and touches exactly the files implied by the two prior P1
findings — `contracts/v5/fixtures/system-versions-graph.yaml`,
`contracts/conformance/test_v5_d2_version_graph_contract.py`, and
`contracts/v5/schemas/system-versions.get.schema.json` (+45/−88). No new files, no runtime code,
no generated artifacts, no other contract files were touched.

**P1-1 closed** — the fixture now pins the previously under-specified component lifecycle
revision: every `component_revisions` row carries `exact_system_component_binding`
(`kind: SYSTEM_COMPONENT`, `id` = the row's `component_id`, `revision: 2` = the component's
current ACTIVE D-014 lifecycle revision), with a comment documenting the model — component
revision records are immutable and version sets reference the records themselves, while
`stale_historical_active_revision_is_forbidden` applies to the component lifecycle binding, not
to the referenced records. The new conformance assertions pin this (`kind`, `id`, and
`revision == current_authoritative_revision == 2` per row). The binding shape matches the
`exact_record_binding_v5` base profile (required `kind/id/revision/digest`,
`SYSTEM_COMPONENT` in the allowed kinds list). The fixture is now internally consistent with the
stale-revision semantics it names, and the former head/row contradiction (data/tool declaring
head 2 without per-row support) is resolved.

**P1-2 closed** — `system-versions.get.schema.json` no longer duplicates
`recordedSystemVersionSet` and its sub-definitions; `$defs.recordedSystemVersionSet`,
`exactComponentRevisionBinding`, `exactTopologyRevisionBinding`, `identityAssuranceSummary`, and
`identityAssuranceEntry` are now single cross-file `$ref`s to
`system-versions.record.schema.json#/$defs/*` (`error` keeps its `common.schema.json` ref). All
five `$ref` targets exist in the record schema; the get document is meta-valid; and an explicit
`referencing.Registry` check confirmed the relative refs resolve through the registered `$id`s
(`https://caseloop.dev/schemas/v5/…`) — validating a `recordedSystemVersionSet` instance through
the get path produced no resolution error and still enforces `additionalProperties: false`
through the ref chain.

All gates re-verified at the subject commit: focused D2 conformance 10/10, full V3/V4/V5
conformance 557/557, compiler 18/18, compiler `emit` deterministic with zero generated diff.
The verifier grants `contract=PASS` only; every other canonical facet remains `NOT_RUN`.

## Verification commands (actual results)

1. `git rev-parse HEAD` → `4852664fe137c18ebc9a795e1740019391f95061`; `git rev-parse HEAD^` → `5717124ad9ad309d035276c17af199374e64b030`; `git status --porcelain` → only `?? evidence/v5/decision-gates/…` (bundle, allowed)
2. `git diff 5717124 4852664` → exactly 3 files: fixture (+17/−6 incl. comment), D2 conformance test (+12), get schema (−88/+17 → 104 lines collapsed to cross-file `$ref`s)
3. `cd contracts && .venv python -m pytest conformance/test_v5_d2_version_graph_contract.py -q` → **10 passed in 0.31s**
4. `cd contracts && .venv python -m pytest conformance/test_schemas.py conformance/test_wilson.py conformance/test_v4_*.py conformance/test_v5_*.py -q` → **557 passed in 13.06s**
5. `cd contracts/compiler && PYTHONPATH=.. python3 -m pytest tests -q` → **18 passed in 0.65s**
6. `cd contracts/compiler && PYTHONPATH=.. python3 -m compiler emit && git -C .. diff --exit-code -- v5/generated/` → emit exit 0, **zero diff** (deterministic)
7. Referencing check: all 5 cross-file `$ref` targets present in record schema; get schema meta-valid; instance validation through the get→record ref chain resolved with no `RefResolutionError`; `additionalProperties` still enforced through the chain → **PASS**

## P0 findings

- none

## P1 findings

- none (prior P1-1 fixture lifecycle-binding under-specification and P1-2 get-schema `$def`
  duplication are both resolved by this remediation; no new issues introduced)
