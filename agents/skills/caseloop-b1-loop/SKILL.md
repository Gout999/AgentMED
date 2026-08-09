---
name: caseloop-b1-loop
description: Execute and evidence the Phase 1 B1 prompt-regression loop through role-scoped CaseLoop MCP tools.
assign_when: A caseloop-team task references fixture B1, a Case ID, or a prompt-regression investigation.
---

# CaseLoop B1 loop

Use this skill only inside the fixed Phase 1 `caseloop-team` warm pool. It does
not grant authority: the Control Plane remains the source of lifecycle state,
Release Controller remains the only Quality API writer, and a human approval
adapter remains the only ApprovalGrant authority.

## Required execution discipline

1. Acknowledge the assigned AgentTeams task with `taskflow(ack_task)` before
   reading or changing CaseLoop state. Keep the returned task receipt.
2. Use only the MCP projection declared for your exact Worker in
   `agents/team.yaml`. A missing tool, `FORBIDDEN`, `LEASE_LOST`, `UNKNOWN`,
   timeout, empty receipt, or digest mismatch is a blocking result.
3. Treat model text as a suggestion. Re-read the authoritative Case,
   Experiment, Gate, WorkOrder, Release, Notification, and Trust projections
   before reporting completion.
4. Write handoff artifacts under `shared/tasks/{task-id}/`; submit them only
   through `taskflow(submit_task)`. Messages contain the relative path, digest,
   and summary, never credentials or storage-internal paths.
5. Keep the Matrix dispatch, progress, and completion event IDs plus the
   taskflow acknowledgement/submission receipt IDs. These are mandatory live
   evidence and must bind the same task, room, worker, Case, and source event or
   audit IDs. Task, acknowledgement, and submission receipt IDs are unique per
   role and must never be copied across the six workers.
6. Do not claim dynamic scaling. At most two Workers are active concurrently;
   attribution and repair use the already-provisioned warm pool.

## Role checkpoints

- `quality-officer`: confirm the Case is dispatched and coordinate the serial
  handoff; export the dispatch intent before collector work; never decide Gate
  or Release state.
- `collector`: bind the original complaint transaction and evidence digest;
  export that complaint evidence before attribution; never synthesize text.
- `attributionist`: claim the exact lease, freeze VersionSets/probes, execute
  the exported experiment plan, execute every immutable trial, and accept only
  the Control Plane's attribution verdict.
- `repairer`: create a candidate and immutable WorkOrder under the exact lease;
  export the repair proposal before candidate creation and the immutable
  WorkOrder before registration; never attach or replace a GateReport.
- `gatekeeper`: run both rule and judge tracks, require provider receipts for a
  live Gate, export an exact request before initial and post-canary evaluation,
  and treat every non-pass/unknown result as a veto.
- `case-officer`: verify the provider notification receipt, Case archive, and
  one-action-one-sample Trust denial; export the original-thread closure intent
  before notification and only then close the AgentTeams task.

## Completion record

For the live evidence adapter, return one record per role containing exactly:
`role`, `task_id`, `ack_receipt_id`, `submit_receipt_id`,
`matrix_event_ids`, `skill`, `source_ids`, and `artifact_ref`. The referenced
JSON handoff must bind the same role, task, session, Case, and source IDs. The
handoff `payload.product_refs` must exactly enumerate that role's immutable
pre-action products. The repairer handoff must additionally reference the exact
immutable WorkOrder artifact produced by its task; the live runner only
validates and submits that artifact and must never author a replacement. The
`skill` digest must match this repository file. Missing or duplicate
AgentTeams/taskflow/Matrix/skill/work-product evidence must be reported as
blocked; a direct script execution is not a substitute.

Every start, pre-action, WorkOrder, and completion receipt must be signed by
the deployment-pinned Ed25519 evidence-export key. The runner has only the
public key and rejects unsigned, re-signed, or mutated receipts.

The Agent runtime has no domain authority. It proposes these immutable
products; the deterministic CaseLoop executor validates them and performs
state transitions. Post-action source IDs in the completion handoff are
observations for reconciliation, not a claim that an LLM directly authored
authoritative state.
