# D-015 · V5 default development baseline and V3/V4 compatibility lanes

> Status: **ACCEPTED / NOT RUNTIME CUTOVER PROOF**
>
> Accepted: 2026-08-11
>
> Scope: repository design, construction defaults, module ownership, convergence sequencing,
> and compatibility preservation

## Context

D-013 accepted the AI-system governance product boundary and the V5 target. V5-0B/0C froze
the system-governance ownership and first wire contracts. D-014 then preserved the explicit
`REGISTERED → ACTIVE` lifecycle and manifest-only activation authority. Those decisions made V5
the coherent direction for new product work, but they did not by themselves make the repository
architecture, public transport default, or complete runtime V5-native.

The repository still contains three necessary bodies of code:

- an implemented V3 Scenario compatibility surface and its historical evidence;
- a V4 compatibility surface, including the completed S1A maintainer-intake path and reusable
  governance safety contracts;
- the V5 system-governance contracts and partially constructed runtime.

Treating all three as competing new-development baselines creates duplicate owners, version-name
leakage, mixed transaction boundaries, and accidental claims that a target contract is running.
Deleting or silently rewriting V3/V4 would instead break real compatibility and provenance.

## Decision

V5 is the default **design and construction baseline for all new product and domain development**.
V3 and V4 remain explicit compatibility lanes. They keep their existing public behavior,
authoritative history, tests, evidence and supported repairs, but are not default homes for new V5
domain semantics.

This is a development-baseline decision, not a runtime cutover:

- the public API and CLI default major do not change;
- no HTTP route, CLI command, capability, SDK, MCP or A2A surface is activated by this ADR;
- existing `/api/v1` payload, error, audit, receipt, digest and replay behavior remains compatible;
- no V3/V4 record, event or receipt is reinterpreted as a V5 fact;
- no V5-2 or later capability is implemented or advertised;
- a plan, frozen contract or structural refactor is not implementation evidence.

## Authority order

For new product and domain construction, contributors must read authority in this order:

1. `AGENTS.md` for repository engineering authority, safety and Definition of Done;
2. `docs/product-principles.md` and D-013 for product scope;
3. this ADR for the default-development and compatibility-lane decision;
4. `docs/plan-v5.md`, `docs/plans/v5-progressive-delivery.md` and
   `docs/plans/v5-master-execution-plan.md` for target architecture and execution order;
5. `contracts/v5/` for owners, state, events and wire contracts;
6. `PLANS.md`, `docs/context/PROJECT_STATE.md` and `docs/context/LAST_HANDOFF.md` for current facts.

`docs/plan-v4.md` is the V4 compatibility baseline. `docs/plan-v3.md`, `docs/spec.md`, existing V3
contracts, migrations and executable tests are the implemented V3 compatibility baseline. Their
historical claims remain valid within their original scope; neither lane overrides V5 for new
domain design.

## Compatibility lanes

| Lane | Allowed work | Forbidden work |
|---|---|---|
| V5 default | New product/domain resources, commands, events, schema-major-2 profiles, explicit V2 transports and stage-owned read models | Claiming a target is runtime; bypassing stage Entry; reusing a compatibility façade as domain owner |
| V4 compatibility | Defect, security, recovery and compatibility fixes; maintained V4 logical safety contracts; explicit bridge behavior | New V5 product aggregates, duplicate V5 lifecycle owners, silent V1/V2 behavior changes |
| V3 compatibility | Defect, security, replay, recovery and preservation of the implemented Scenario workload | New V5 domain semantics, rewriting immutable history, borrowing V5 evidence to upgrade V3 claims |
| Shared infrastructure | Major-aware authentication, audit, idempotency, event/outbox, database, canonicalization and transport plumbing with one named owner | Domain success authority, implicit cross-lane writes, a helper choosing business truth |

A compatibility façade may authenticate, validate, translate, dispatch to the canonical owner and
render a compatible response. It may not own lifecycle state, manufacture success, weaken exact
bindings, or create a second audit/idempotency/event truth.

## Architecture invariant

The convergence target is a **modular monolith** in the control plane:

- one deployable control-plane process boundary for the authoritative core;
- one PostgreSQL database and one PostgreSQL unit of work for each authoritative business
  transaction;
- modules own domain commands and facts; projections, façades, transports and adapters do not;
- required record, event, outbox, audit, authority receipt and idempotency terminal result are
  committed or rolled back together;
- external providers and Agent transports remain adapters around canonical intents, not new owners.

This decision does not authorize splitting the authoritative transaction across services, adding a
second database as business truth, or coordinating required facts by eventual consistency.

## Current R2 boundary

The accepted interim statement for R2 is limited to `contract=PASS` and `replay=PASS` for its exact
scope. It is not complete V5 runtime proof, does not close R3-full, and does not activate standalone
`system-versions.record/get/diff`.

By explicit product-owner instruction, final subject SHA recording and evidence-bundle digest
finalization are deferred to the final project closure stage. No convergence wave may fabricate,
guess or backfill those values. The deferral must remain visible as
`DEFERRED_BY_OWNER_TO_FINAL_PROJECT_CLOSURE`; it cannot be rewritten as PASS or used as evidence for
live/provider/Agent/external/production facets.

## Architecture convergence gate

D2, R3, R4 and V5-2 or later work remain locked while C0–C5 execute:

- **C0 — authority, clean branch, WIP inventory and characterization:** accept this ADR, freeze the
  wave plan, establish a clean convergence branch, inventory all pre-existing WIP and characterize
  current wire/runtime/module behavior. C0 is not closed merely because these files exist.
- **C1 — single-source wire and activated-operation compiler:** make JSON Schema 2020-12 plus the
  intent registry the canonical wire/operation input; compile only activated operations; run
  generated and legacy validators together in shadow without changing public behavior.
- **C2 — foundation extraction:** extract shared records, event specifications and the graph
  verifier behind stable major-aware interfaces. C2 follows wire single-sourcing and may not absorb
  domain behavior.
- **C3 — capability/import-cycle elimination and coordinator/service decomposition:** remove import
  cycles and hidden capability ownership, then decompose coordinators/services around the canonical
  application interfaces while preserving one PostgreSQL UoW.
- **C4 — generated transport cutover:** cut OpenAPI, Python, TypeScript, CLI, router and capability
  façades to generated activated-operation artifacts with shadow parity and an explicit rollback.
- **C5 — compatibility cleanup, effective enforcement and recovery verification:** remove only
  verified obsolete compatibility duplication, enforce the new boundaries, prove recovery and
  close unresolved P0/P1 findings.

Each wave requires its own exact allowlist, non-goals, verification, rollback and independent
review. Structural work and behavior-changing feature work may not share a semantic change.

## Hard stop gates

Any of the following is an immediate `NO-GO` for the active convergence wave:

- two modules can author the same domain fact or lifecycle transition;
- a façade, projection, transport, adapter or shared helper can manufacture domain success;
- a required transaction is split across databases or commits required facts in separate units of
  work;
- public request/response/error/audit/receipt/idempotency/replay behavior changes without a
  separately admitted contract migration;
- a V3/V4 immutable record, event or digest is rewritten or reinterpreted;
- V2 becomes the implicit public default or a locked intent becomes discoverable;
- an unresolved import cycle requires service-locator, global-session or hidden repository access;
- structural refactoring is used to claim D2, R3, R4, V5-2+ or a live facet.

## D2 and later re-entry

D2 may resume only after C0–C5 have independently satisfied their exits and current status files
agree on the result. D2 must freeze the complete second-version graph-recording bundle, not an
isolated endpoint. R3-full starts only after that D2 contract passes independent review. R4 and
V5-2+ remain locked behind their existing Master Plan dependencies; the convergence work neither
implements them nor weakens those gates.

If D2 is deferred, only the already bounded workspace-initial bootstrap behavior may remain. That
holding lane does not prove a second VersionSet or semantic diff and unlocks no later development
stage by implication.

## Rollback

This ADR may be superseded only by another explicit decision; historical records are not deleted.
If convergence finds an ownership, transaction, migration or compatibility P0:

1. stop the affected V5 mutation route, discovery entry and dispatcher;
2. keep supported V3/V4 compatibility behavior available where it remains safe;
3. preserve append-only V5 facts, receipts, audits and evidence;
4. restore the last verified module boundary or use a roll-forward repair;
5. do not perform a destructive database downgrade or silently make V4 the new-development
   baseline again;
6. re-enter only through the failed wave's full verification gate.

## Consequences

New work has one default architecture and one target vocabulary. Compatibility remains a supported
engineering responsibility rather than an abandoned code path. The cost is an explicit convergence
pause before domain expansion and stricter classification of shared code. That cost is accepted to
avoid accumulating V5 behavior inside V3/V4 modules or building later stages on ambiguous authority.
