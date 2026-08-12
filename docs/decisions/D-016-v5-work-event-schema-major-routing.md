# D-016 — V5 Work Event Schema-Major Routing

Status: **ACCEPTED (engineering decision) / NOT IMPLEMENTATION PROOF**
Date: 2026-08-12
Decider: construction agent under product-owner delegation of 2026-08-12
Decision: **Work events use the major-2 envelope and reuse the V4 semantic owner and state machines**

## Context

Master §6 (`docs/plans/v5-master-execution-plan.md`) makes 2A-0 a decision
package before any Work Kernel code exists. Its exact requirement:

> 裁决并冻结 Work 事件复用 V4 event contract,还是新增 schema-major-2 system
> profile;在该裁决前不得把 V5 catalog/event envelope 自动套给
> WorkerTask/Attempt

The product owner delegated V5-2A construction, including this adjudication, on
2026-08-12. This is an engineering decision recorded for review, not a product
scope decision, and it does not carry product-owner authority. It may be
overturned before 2A-1 lands without rewriting any committed fact, because no
Work runtime exists yet.

### Verified state at decision time

- `contracts/v5/aggregate-ownership.yaml:5-31` already declares
  `mode: semantic_reuse_with_schema_major_routing` and already lists
  `worker_task`, `attempt`, `proposal`, `proposal_decision`, `capability_lease`
  and `external_operation` under `imports.v4.resources`, together with
  `no_state_machine_copy: true` and
  `closed_json_schemas_are_not_inherited: true`.
- `contracts/v4/aggregate-ownership.yaml:212-288,378,464` defines the owners,
  commands, events and `explicitly_does_not_own` sets for those aggregates.
  `worker_task` and `attempt` are owned by `work-controller`, `proposal` and
  `proposal_decision` by `proposal-controller`, `capability_lease` by
  `capability-controller`, `external_operation` by
  `scoped-executor-controller`.
- `contracts/v4/events/state-machines.yaml:41-94` defines the `worker_task`
  (8 states / 13 transitions) and `attempt` (10 states / 19 transitions)
  machines, including the `UNKNOWN → reconcile` transitions guarded by
  `trustworthy_terminal_receipt`.
- `control-plane/app/models/v4_tables.py:122-141` (`ck_events_v4_context`)
  already partitions one `events` table into three mutually exclusive shapes.
  The `v4` branch requires `event_contract_major`, `routing_key`,
  `exact_subject_binding` and `authority_receipt_id` to be **NULL**; the `v5`
  branch requires all of them to be **NOT NULL** with
  `event_contract_major = 2` and `event_version = '2.0'`.
- `control-plane/app/models/v4_tables.py:142-172` gives `legacy`, `v4` and `v5`
  their own partial unique indexes on `seq`, so the two majors never share a
  sequence.
- `control-plane/app/foundation/events.py` registers **zero** Work-domain
  routes today (`grep -c 'work\.\|attempt\.'` returns 0). There is no Work
  runtime, no Work row and no Work event in any environment.

## Decision

Work-domain events are appended with `contract_version = 'v5'`,
`event_contract_major = 2`, `event_version = '2.0'` and the full
`event_envelope_v5` field set, while the aggregate owners, commands, event
names, state machines and failure semantics are reused verbatim from the V4
contract by reference.

Concretely:

1. **Envelope**: major-2. `contracts/v5/events.yaml#event_envelope_v5` already
   declares `applies_to: every_event_in_this_major_2_catalog`, so Work events
   inherit it without a new envelope definition.
2. **Owner and state machine**: referenced from the V4 contract, never copied.
   `contracts/v5/events.yaml#v5_2a_work_kernel_contract.schema_major_routing`
   records the source paths.
3. **V4 compatibility**: existing V4 Work fixtures, schemas and any future
   `contract_version = 'v4'` Work row stay addressable and are never
   reinterpreted. The two majors are isolated by the existing CHECK constraint
   and by per-major partial unique indexes.
4. **No mixing**: one aggregate may not emit both majors inside a single
   transaction.

### Why not reuse the V4 event contract

The V4 branch of `ck_events_v4_context` forces `authority_receipt_id IS NULL`.
Master §6 2A-2 requires the claim transaction to record an authority receipt
together with the attempt, event, audit and outbox rows, and Master §4.2 item 5
(`docs/plans/v5-master-execution-plan.md:233-236`) requires every V5 domain
event to carry `contract_major=2`, `event_version=2.0` and named self plus
required dependent exact bindings. A V4-profile Work event therefore could not
bind its own authority receipt at all. Reuse is not merely less convenient — it
is structurally incompatible with the required claim transaction, and it would
also violate that closure gate.

### Why not a third profile

A separate "system profile" would need its own envelope, its own constraint
branch, its own sequence partition and its own replay validator, for no
behavioural gain: major-2 already provides exact subject binding, routing key
and authority receipt binding. `contracts/v5/aggregate-ownership.yaml` also
already names the chosen mode, so a third profile would contradict a frozen
contract.

## Consequences

- 2A-1 adds migration `014` (current Alembic head is `013`) for the Work
  aggregates; Work events reuse the existing `events`, `outbox` and
  `authority_receipts` tables under the `v5` branch, so no new event table is
  introduced.
- 2A-2 must issue an attempt-scoped runtime capability, an authority receipt, a
  domain event, a controller audit row and an outbox row inside the same
  PostgreSQL unit of work as the attempt row, or roll the whole claim back.
- The `forbidden_writers` matrix in
  `contracts/v5/events.yaml#v5_2a_work_kernel_contract` is now contract, not
  convention: Coordinator, Executor, Adapter and Exporter may never append a
  domain event or own domain success.
- Nothing is activated. `transports` are all `FORBIDDEN` and
  `activation_status` is `NOT_ACTIVATED`, matching the D2 precedent for
  `FROZEN_FOR_IMPLEMENTATION`.

## What this decision does not prove

It does not prove that any migration, table, service, route, CLI command,
capability or dispatcher is implemented. It does not create any `agent-causal`,
`domain-provider-live`, `agentteams-native` or `production-canary` evidence;
those facets remain `NOT_RUN`. Master §546 explicitly keeps Agent and provider
liveness out of V5-2A scope.
