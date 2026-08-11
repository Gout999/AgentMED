# D-014 — V5 Application and Component Activation Lifecycle

Status: **ACCEPTED / NOT IMPLEMENTATION PROOF**
Date: 2026-08-11
Decider: product owner
Decision: **Option A — preserve `REGISTERED → ACTIVE`**

## Context

The V5 contract freeze at `8dd25ca` and `b3727d7` defines both
`AIApplication` and `SystemComponent` as aggregates that are created in
`REGISTERED` and enter `ACTIVE` only through a separate owner command and
event. The current, unclosed V5-1 runtime work instead creates both resources
directly as `ACTIVE` revision 1. Migration 008 and the current projections also
exclude `REGISTERED` from their database constraints.

That mismatch cannot be closed by changing a response constant. A later
activation must remain an independently auditable authoritative revision, and
the registration revision must remain replayable after the current head moves
to revision 2. A `ComponentRevision` also needs an exact, durable binding to the
active catalog revision whose identity it versions.

On 2026-08-11 the product owner explicitly selected Option A. This ADR freezes
the decision and contract consequences. It does not prove that a migration,
runtime path, public route, CLI command, or capability is implemented.

## Decision

### Authoritative lifecycle revisions

1. `applications.register` creates immutable application revision 1 in
   `REGISTERED` and emits `application.registered`.
2. `applications.activate` creates immutable application revision 2 in
   `ACTIVE` and emits `application.activated` with exact previous and new
   application bindings.
3. `system-components.register` creates immutable component revision 1 in
   `REGISTERED` and emits `system_component.registered`.
4. `system-components.activate` creates immutable component revision 2 in
   `ACTIVE` and emits `system_component.activated` with exact previous and new
   component bindings.
5. Every revision has its own record envelope, digest, AuthorityReceipt,
   event, transactional outbox record, and audit evidence. The revision-1
   record is not overwritten when revision 2 is created.
6. PostgreSQL append-only lifecycle revision history is authoritative. A
   current-head row or view is a projection and cannot prove a historical exact
   binding. Authority validation resolves `(kind, id, revision, digest)` from
   immutable history.

The recommended, non-normative persistent history names are
`ai_application_lifecycle_revisions` and
`system_component_lifecycle_revisions`. This ADR freezes append-only historical
authority, not physical table names. The exact schema and names are deferred to
R1 and must use the actual Alembic head at construction time.

### Activation authority and concurrency

- `application-catalog-controller` remains the sole owner of both register and
  activate commands.
- Activation is workflow-only. It may be requested only by
  `manifest_import_coordinator` inside the same PostgreSQL unit of work as one
  exact, authenticated `system-manifests.import` request. The workflow binding
  includes the request digest, manifest digest, idempotency key, workspace, and
  exact initiating human-or-service principal. An `internal_controller` cannot
  invoke activation independently, even if it holds an activation-shaped
  scope.
- Activation obtains and locks the exact current head, requires revision 1 in
  `REGISTERED`, and compare-and-swaps to revision 2 in `ACTIVE`.
- The exact previous binding is derived from the locked authoritative history;
  it is never accepted as caller authority.
- Wrong state, stale binding, cross-workspace or cross-application binding,
  expired controller registration, duplicate activation, or a concurrent loser
  fails closed and is audited.
- Agent or LLM output may propose catalog data but cannot activate a resource or
  manufacture a lifecycle revision.

### ComponentRevision binding

`ComponentRevision` MUST persist `exact_system_component_binding`. The binding
must identify the same-workspace, same-application current authoritative
`SystemComponent` lifecycle revision in `ACTIVE`, including its immutable
digest, at the time the ComponentRevision is recorded. The first manifest binds
revision 2. A later deprecate/reactivate sequence may make a later ACTIVE
revision authoritative (for example, revision 4), so revision 2 is not a global
constant. A historical ACTIVE revision that is no longer current, a DEPRECATED
or RETIRED revision, a component ID,
a mutable current-head projection without exact history, or a non-ACTIVE
revision is insufficient.

### Trusted manifest transaction

The trusted manifest coordinator invokes the following owner commands in one
local PostgreSQL unit of work:

1. `applications.register`
2. `applications.activate`
3. `environments.register`
4. for each component, `system-components.register`
5. for each component, `system-components.activate`
6. `dependency-edges.record`
7. `component-revisions.record`
8. `topology-revisions.record`
9. `system-versions.record`
10. `bootstrap-attestations.record`
11. `system-assignments.record`

Application and component registration/activation therefore precede every
dependent immutable record. Any record, event, outbox, audit,
AuthorityReceipt, or idempotency failure rolls back the entire manifest unit of
work. Retrying the same manifest digest returns the same terminal result and
must not mint another activation revision.

The lifecycle subject and AuthorityReceipt identify the
`application-catalog-controller` as the authoritative actor. A separate command
audit identifies the exact initiating human-or-service principal and binds the
authenticated `system-manifests.import` request digest, manifest digest,
workspace, and idempotency key. Both layers are required in the same unit of
work; neither may substitute for the other.

### Public transport boundary

Standalone public `applications.activate` and `system-components.activate`
remain deferred. They are internal owner commands used by the trusted manifest
workflow and MUST NOT appear in HTTP, CLI, SDK, MCP, A2A, or capability
discovery until a later explicit wire freeze and runtime acceptance.

`applications.register` returns the `REGISTERED` revision it creates. A trusted
manifest response returns the resulting `ACTIVE` revision 2. A register intent
must never silently invoke activation.

`Environment` remains initially `ACTIVE` revision 1 and is outside this
decision. Later environment retirement/restoration needs its own stage closure.

## Alternatives considered

### Option B — create directly as ACTIVE

Rejected. It is smaller to implement but changes the frozen state machines,
removes explicit activation authority, and makes registration indistinguishable
from approval to enter active versioning workflows.

### Update the current row in place

Rejected. Replacing revision 1 or its digest destroys immutable history and
prevents replay of the registration event and AuthorityReceipt.

### Emit an activation event without an authoritative activation revision

Rejected. An event without an exact immutable subject record cannot establish
authoritative lifecycle state.

### Treat legacy ACTIVE revision 1 as REGISTERED or synthesize activation

Rejected. Relabelling the row, changing its digest, or manufacturing a receipt
would reinterpret immutable history.

### Keep only the mutable current row as authority

Rejected. Once the head becomes revision 2, the old revision-1 event and
receipt could no longer be independently verified.

### Dual-write old and new lifecycle authorities

Rejected. The same lifecycle fact must have one owner and one authoritative
history per contract major.

## Migration and recovery boundary

The R1 migration MUST run a preflight before schema mutation.

- A populated previous-head database with v3/v4 history but no V5 catalog or
  V5 authority history may upgrade normally.
- Any database containing direct-`ACTIVE` V5 Application/Component rows or
  their events, outbox entries, AuthorityReceipts, VersionSet records, Case
  bindings, or acceptance references must fail with a stable recovery-required
  error. It must not partially migrate or backfill those records.
- Disposable development databases are rebuilt.
- Durable databases require export and verification of the immutable graph,
  replay into a fresh database under the new lifecycle, an explicit old-to-new
  identity map for every V5-1B/V5-1C reference, source/target reconciliation,
  and evidence-backed cutover.
- Migration 012 and historical event envelopes are not rewritten or relabelled.
- Downgrade is allowed only before any new lifecycle revision or activation
  event/receipt exists. Otherwise recovery is roll-forward.

If no durable environment exists, the stage evidence must prove that inventory
and mark durable recovery `N/A`; absence cannot be assumed.

## Compatibility

- V3 and V4 tables, routes, payloads, events, receipts, and evidence remain
  unchanged.
- This is a correction to an unclosed V5-1 runtime mismatch, not a claim that
  old local V5 behavior was compatible or production-ready.
- Contract consumers must tolerate `REGISTERED` in Application and Component
  lifecycle records. Manifest-produced Application/Component envelopes advance
  from revision 1 to revision 2.
- Pre-decision V5 idempotency or authority history cannot be replayed across the
  semantic boundary; the migration preflight routes it to explicit recovery.
- Public activation transports remain unimplemented and undiscoverable.

## Security consequences

- Authentication, workspace/project scope, server-derived trust role, active
  controller registration, exact previous binding, and CAS state are all
  required; missing or `UNKNOWN` authority fails closed.
- A controller event does not replace the initiating principal's command audit.
- Audit, event, outbox, receipt, and business record share the enclosing
  transaction. Audit failure fails the business transaction.
- ComponentRevision creation independently revalidates the exact current
  authoritative ACTIVE SystemComponent lifecycle binding; it does not trust
  manifest ordering or a fixed revision number alone.
- An initial manifest binds ACTIVE revision 2. After deprecate/reactivate, a new
  ComponentRevision binds the new exact current ACTIVE revision (for example,
  revision 4) and rejects stale historical ACTIVE revision 2, DEPRECATED
  revision 3, and every RETIRED revision.
- Possession of an internal scope does not authorize direct activation. The
  exact authenticated import request and its initiating-principal audit are
  mandatory workflow authority.

## Stop gates

R1/R2 MUST stop if any of the following is true:

- contract files omit either activation command or disagree on command order;
- revision 1 and revision 2 cannot both be replayed after head advancement;
- ComponentRevision lacks a persisted exact current authoritative ACTIVE
  lifecycle binding;
- activation can bypass the canonical controller, exact previous binding, or
  compare-and-swap;
- activation can be invoked outside the exact authenticated manifest-import
  transaction, or its controller subject/receipt and initiating-principal audit
  are not both present;
- a failed manifest leaves any partial record, event, outbox, audit, receipt, or
  idempotency result;
- populated legacy history cannot be classified or fully recovered;
- a public transport or capability advertises standalone activation without its
  own freeze and runtime evidence;
- focused migration, contract, unit, PostgreSQL concurrency, tamper, and replay
  verification or independent review is not PASS.

## Verification required before implementation closure

- contract conformance proves the two revision sequences, exact bindings,
  manifest ordering, public-deferred boundary, and adversarial failures;
- fresh and populated migration tests prove normal upgrade, fail-closed legacy
  detection, downgrade guards, and zero partial rewrite;
- unit and PostgreSQL integration tests prove owner authorization, audit
  rollback, idempotent replay, historical receipt replay, tamper rejection, and
  exactly-one concurrent activation;
- CLI/Console/schema tests accept `REGISTERED` without advertising activation;
- evidence records commands, exit codes, database kind, commit identity, and an
  independent verifier result.

This decision freezes only the `contract` facet. `replay`,
`domain-provider-live`, `agentteams-native`, `claude-runtime-live`,
`agent-causal`, `repo-sandbox`, `human-authorized-external`, and
`production-canary` remain `NOT_RUN` until separately evidenced.
