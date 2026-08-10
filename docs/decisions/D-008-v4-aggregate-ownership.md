# D-008 — v4 Aggregate Ownership

Status: **ACCEPTED**

Date: 2026-08-10

Scope: v4 new development; v3 aggregates remain compatible until explicit cutover.

## Decision

Every durable business fact has exactly one PostgreSQL-backed aggregate owner. Adapters, Agent runtimes, Matrix/MinIO, CLI/MCP clients and read models cannot mutate another aggregate by inference.

| Aggregate owner | Owns | Accepts commands | Emits facts |
|---|---|---|---|
| Signal Controller | source dedupe, normalization, acknowledgement, correlation state | submit/ack/link/supersede/close/reopen Signal | `signal.*`, `signal_case_link.*` |
| Source Controller | `SourceConnection` configuration and each `SourceSyncRun`/doctor execution | register/update/disable connection; request/start/terminalize/reconcile source run | `source_connection.*`, `source_sync_run.*` |
| Case Controller | domain case lifecycle and accepted investigation outcomes | open/triage/accept finding/resolve/reopen | `case.*` |
| Automation Request Controller | investigation admission, caller budget and best-effort stop request only | start/admit/reject/update budget/request stop | `automation_request.*` |
| Work Controller | WorkerTask, Attempt, lease/fence, retry/cancel/reconcile | request/claim/heartbeat/record terminal/request cancel/reconcile | `work.*`, `attempt.*` |
| Proposal Controller | typed Proposal/Finding/Intent and acceptance decision | submit/accept/reject | `proposal.*`, `finding.*` |
| Evidence Controller | immutable source/artifact receipts and completeness | record/verify/supersede receipt | `evidence.*` |
| Evaluation/Gate Controller | frozen EvaluationPlan and deterministic Gate status | freeze/run/record track/terminalize | `evaluation.*`, `gate.*` |
| Approval Controller | human ApprovalGrant bound to exact target hash | request/decide/revoke/expire | `approval.*` |
| `scoped-executor-controller` | the single PostgreSQL-owned `ExternalOperation` lifecycle, exact target/action binding, idempotency and unknown-outcome reconciliation | `external-operations.request`, `external-operations.request-rollback`, `external-operations.reconcile`; dispatch a typed Executor only after validating the exact WorkOrder and grant | `external_operation.*` |
| Closure Controller | the separate `ClosureDelivery` lifecycle; not the `ExternalOperation` aggregate | request/reconcile delivery | `closure.*` |

`scoped-executor-controller` is the only aggregate owner for scoped external operations. Repo, Release, Closure and Distribution are typed, credential-holding Executors/Adapters selected by operation kind. They perform the bounded side effect and return provider receipts; they do not each own an `ExternalOperation`, infer its terminal state, or write its lifecycle independently. The Release typed executor remains the only path allowed to call Quality API write endpoints. `ClosureDelivery` remains a separate domain aggregate owned by Closure Controller; a Closure adapter used to perform a scoped external action does not acquire aggregate ownership.

`DurableWorkCoordinator` consumes committed events, records reaction idempotency, and requests new work. It does not own a cross-domain `current_stage`, decide Gate success, or mark a Case resolved. `AutomationRequest` is not that missing mega-aggregate: it owns only admission, budget and the caller's stop request; execution and every domain terminal fact remain with Work, Experiment, Gate, `ExternalOperation`, `ClosureDelivery` and Case owners. `AutomationRunView` is a read-only projection.

Every public mutation carries one `command_target {resource, command}`. The resource must be a non-projection entry in `contracts/v4/aggregate-ownership.yaml`, the command must occur in exactly that resource's command list, and that resource has exactly one owner. Queries omit `command_target`. In particular:

- `sources.doctor` creates/targets a `SourceSyncRun` through `source-sync-runs.request-doctor`; the run references but does not mutate its `SourceConnection` by inference;
- `investigations.start` and `work.stop-request` target distinct `AutomationRequest` commands;
- `releases.rollback-request` targets `external-operations.request-rollback`; lifecycle and reconciliation remain with `scoped-executor-controller`, while the selected Release Executor/Adapter only performs the bounded action and returns its receipt. Neither the caller nor a read model owns the result.

## Transaction rule

When a Controller accepts an Agent Proposal, it writes the `ProposalDecision` and the first downstream causation event in the same PostgreSQL transaction. An outbox record is part of that transaction. Audit failure fails the transaction.

## Required invariants

1. One command has one aggregate owner.
2. Every public mutation names exactly one owned command target; public queries name none.
3. A projection exposes no mutation command and can never be a command target.
4. A downstream event names its immediate `causation_id` and accepted proposal where applicable.
5. IDs are globally prefixed until event uniqueness includes aggregate type.
6. Unknown external outcome never becomes success without reconciliation.
7. Adapter-local state can aid transport recovery but cannot override aggregate state.
8. A typed Repo/Release/Closure/Distribution Executor can hold only its target credential and action implementation; it cannot become a second `ExternalOperation` owner.

## Consequences

- `scripts/run_b1_live.py` may inject a Signal, wait and validate; it cannot sequence domain transitions itself.
- Matrix, MinIO and AgentTeams CR state are evidence inputs, not CaseLoop lifecycle authority.
- Repo/Release/Closure/Distribution adapters return typed receipts to `scoped-executor-controller`; adapter success styling or a provider response alone cannot terminalize `ExternalOperation` outside that owner.
- New v4 contracts live under `contracts/v4/`; existing v3 event/state contracts are not edited in place.
