# contracts/v5/schemas — V5 activated wire schemas (JSON Schema 2020-12)

C1 single-source-wire wave artifact: one JSON Schema 2020-12 contract per
**activated** V5 public intent, covering request body, optional query
parameters, response and the shared error envelope. These schemas are the
canonical wire-shape source that the C1 activated-operation compiler consumes
and that later waves (C4) cut transports over. They are derived **from** the
current implemented shapes (`control-plane/app/public_api/v5_models.py` +
`schema-profiles.yaml#r2_wire_profiles`) and must stay byte-compatible with
the legacy validators until C4.

## Naming and structure

- `<intent>.schema.json` — one file per activated intent (dots and dashes in
  the intent name are preserved verbatim, e.g. `system-components.get.schema.json`).
- `common.schema.json` — shared `$defs`: id formats, enums, record envelope,
  idempotency receipt/delivery and the error envelope. Intent files reference
  it via relative `$ref` (`common.schema.json#/$defs/...`).

Every intent file has the same shape:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://caseloop.dev/schemas/v5/<intent>.schema.json",
  "title": "<intent>",
  "$defs": {
    "request":  { ... },        // request BODY shape; { "type": "null" } when the intent has no body
    "query":    { ... },        // OPTIONAL; present only when the intent has query parameters
    "response": { ... },        // success response body
    "error":    { "$ref": "common.schema.json#/$defs/errorEnvelope" }
  }
}
```

## Conventions (must hold for every file)

- `$schema` is always `https://json-schema.org/draft/2020-12/schema`; `$id`
  is always `https://caseloop.dev/schemas/v5/<intent>.schema.json`.
- Every object schema sets `additionalProperties: false` (the legacy models
  use `extra="forbid"`; the wire profiles declare `additional_properties: false`).
- Pydantic `Literal` values become `const` or `enum`; `StrictBool` validated to
  `True` becomes `const: true`; `StrictInt` stays `integer`.
- Id formats and shared enums are referenced from `common.schema.json#/$defs/...`
  — never re-declared inline.
- Date/time fields use `{ "type": "string", "format": "date-time" }`
  (`AwareDatetime`).
- `application_lifecycle_constraint` / `component_lifecycle_constraint`
  (revision 1 `REGISTERED`) are structural facts of the response record, not
  extra schema keywords; records carry the full `record_envelope` via the
  `recordEnvelope` `$defs`.
- These files are hand-maintained C1 schema source. Do not regenerate them from
  Pydantic; the C1 compiler reads them read-only and emits candidate derived
  artifacts elsewhere (`contracts/v5/generated/`).

## Validation and parity

- Meta-validation: every file must pass `Draft202012Validator.check_schema`
  and declare the 2020-12 dialect (pattern in
  `contracts/conformance/test_schemas.py`).
- Cross-file `$ref` resolution uses `referencing.Registry` with an
  `https://caseloop.dev/schemas/v5/` prefix mapped onto this directory (pattern
  in `contracts/conformance/test_v4_schemas.py`).
- Parity: the C1 shadow harness validates the shared corpus with both the
  generated 2020-12 validators and the legacy Pydantic models and records any
  accept/reject mismatch. Zero mismatches is a C1 exit condition.

## Current files

- `common.schema.json`
- `capabilities.get.schema.json`
- per-intent schemas for the other ten activated intents (added by C1).
