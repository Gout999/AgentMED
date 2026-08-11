# C1 shadow-parity adjudication inputs

C1 runs the generated JSON Schema 2020-12 wire contracts in shadow beside the
legacy Pydantic models and records any accept/reject divergence instead of
silently coercing it (v5-architecture-convergence.md#C1). The curated corpus
(`contracts/v5/corpus/`) contains only cases where both validators agree; the
divergences below are known, reproduced findings. C4/C5 adjudicated them with
a layered fail-closed boundary: generated JSON Schema owns structural wire
acceptance, while Pydantic/TypeScript validators retain semantic and aggregate
invariants. Both layers must accept and neither may override the other's
rejection. This does not change the public major.

## 1. Structural wire shapes: schema scope

JSON Schema 2020-12 cannot express cross-field or cross-collection equality
constraints. The following legacy invariants have **no schema counterpart** and
are deliberately absent from the corpus:

- `DependencyEdgeRecordRequest.edge_is_not_self` (`from != to`).
- `ApplicationListResponse.items_match_workspace_and_are_unique` and
  `ApplicationListItem.graph_bindings_match_application` (child records bound
  to the same workspace/application; edge endpoints members of the component
  set).
- Manifest request-level business constraints (`SystemManifestImportRequest`):
  unique logical names, at least one `APPLICATION_CODE`, edge endpoints exist,
  acyclic graph, `approver_policy` is a `POLICY` component.
- `enabled_intents` uniqueness is by `name` in legacy; the schema uses
  `uniqueItems` (whole-object). Same-name/different-scope duplicates are
  schema-valid / legacy-invalid.

Disposition: these are semantic service/aggregate invariants, not wire shape.
They remain in the semantic validator/service layer and its tests; C1 does not
attempt an unsound schema encoding. C4/C5 require generated structural
acceptance before that semantic layer runs.

## 2. V5IdempotencyReceipt intent-binding (legacy stricter)

`V5IdempotencyReceipt.intent_binding_is_exact` couples the receipt to its
intent: `resource.kind`, `resource.id` prefix, `operation_id == null` and
`status == "COMPLETED"` are per-intent. The `idempotencyReceipt` `$defs` is
structural only (kind/status enums, op_ or null, generic id pattern), so an
inconsistent receipt can be schema-valid / legacy-invalid.

Disposition: corpus valid cases are authored on the semantic-strict side. The
semantic validator remains authoritative for this cross-field rule; the
compiler supplies the activated intent and structural receipt shape first.

## 3. Record revision/lifecycle coupling (schema stricter on rev >= 2)

`records.schema.json` couples the previous-binding shape to `lifecycle_state`
(REGISTERED ↔ null binding; ACTIVE/ARCHIVED/… ↔ exact binding), while the
legacy serializers and validators couple it to `record_envelope.revision`
(rev 1 ↔ null binding + REGISTERED; rev >= 2 ↔ exact binding, lifecycle not
re-checked). Divergent corners (verified):

- rev 2 + exact binding + `REGISTERED`: schema invalid, legacy valid.
- rev 2 + null binding + `REGISTERED`: schema valid, legacy invalid.
- rev 1 + exact binding + `ACTIVE`: schema valid, legacy invalid.

Disposition: the schema's lifecycle↔binding coupling matches the domain model
(`domain-model.yaml#lifecycle_revision_contract`); the legacy rev>=2 laxness is
a gap. Schema is kept as the stricter domain truth. Runtime acceptance now
requires both schema and semantic validation, so every divergent corner fails
closed without silently widening either layer.

## 4. Capability `execution_mode` per intent (legacy stricter)

`V5EnabledIntent.transports_are_real` requires `system-manifests.import` →
`synchronous_local_transaction` and every other activated intent →
`synchronous`. The `enabledIntent` schema does not encode the name→mode rule.

Disposition: the intent registry and generated capability manifest own the
per-intent execution mode; the semantic model additionally verifies it.

## 5. Manifest/specification constraints (legacy stricter)

- `ManifestRevisionSpec` discriminator: `IMMUTABLE_DIGEST` requires
  `content_digest`; the other identity-assurance values have their own
  combinations. Schema does not encode the discriminator.
- `SystemAssignmentRecord`: legacy requires `generation == 1` and
  `requested_by_external_operation_id is None` for the bootstrap result;
  schema only requires `generation >= 1`.

Disposition: recorded. Corpus valid cases follow legacy.

## 6. exact_observation_receipt_binding (schema stricter)

In the request-side `ManifestRevisionSpec`, the schema types
`exact_observation_receipt_binding` as `exactV5EvidenceBinding | null`, while
the legacy model declares `dict[str, Any] | None` for that request field. A
non-conforming dict is legacy-valid / schema-invalid.

Disposition: the schema's tighter shape is the structural contract. Because
generated validation runs before semantic validation, the legacy open dict can
no longer admit this invalid shape on an activated transport.

## 7. date-time format enforcement

`format: date-time` is enforced by the harness via
`FormatChecker` (requires `rfc3339-validator`, added to
`contracts/conformance/requirements.txt`). Without that package jsonschema
silently skips the format and schema-validates non-RFC3339 strings that legacy
`AwareDatetime` rejects.

Disposition: resolved — the harness and the conformance dependency now enforce
it.

## Verdict

All divergences are recorded and adjudicated without silent coercion. The C1
corpus and shadow harness characterize the boundary; C4/C5 make generated
structural validation and semantic validation jointly mandatory and
fail-closed. The default public and CLI major remains unchanged.
