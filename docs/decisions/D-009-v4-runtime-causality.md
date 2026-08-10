# D-009 — Runtime Identity, Delegation, Causality and Recovery

Status: **ACCEPTED**
Date: 2026-08-10

## Decision

CaseLoop distinguishes the governed Agent, the CaseLoop Worker, the runtime session, the model provider call, the Controller and the scoped Executor. A successful wrapper process or platform task does not prove an Agent caused a business outcome.

The existing `caseloop-team` remains the six-role quality governance Team. A separate `caseloop-coding-team` will contain `coding-planner`, `coding-generator` and `coding-reviewer`. Cross-Team handoff occurs only through a Controller-created child WorkerTask and outbox event.

## Claude Code delegation

When an AgentTeams Worker needs Claude Code:

1. the Worker claims a parent WorkerTask with its own principal, lease and fence;
2. it submits a typed DelegationProposal;
3. the Proposal Controller accepts it and, in the same PostgreSQL transaction as the first downstream causation event, asks the Work Controller to create a child WorkerTask and issue a one-time `CapabilityLease(grant_kind=DISPATCH_CLAIM)` bound to the accepted DelegationProposal, child task, executor principal, adapter audience, budget, nonce and expiry;
4. a host-side ClaudeCodeRuntimeAdapter uses only that dispatch/claim lease to launch an isolated native session for the dispatched child WorkerTask;
5. the native session consumes the one-time dispatch/claim lease to claim the child WorkerTask; the Work Controller atomically creates the child Attempt and lease/fence, then issues a distinct `CapabilityLease(grant_kind=ATTEMPT_RUNTIME)` bound to the child task, child Attempt, ResolutionContract, effective permissions, budget, nonce and expiry;
6. that child Attempt records separate model resolution and model call receipts plus Skill/MCP/tool/process receipts, and authors a pre-action ChangeProposal;
7. the Controller accepts/rejects; only accepted Proposal plus downstream causation can advance the domain.

Required identity fields include `assigned_team_id`, `team_manifest_digest`, `role_manifest_digest`, `parent_worker_task_id`, `parent_attempt_id`, `requested_by_principal`, `executor_principal`, `runtime_kind`, `runtime_session_id`, `runtime_adapter_digest`, `requested_model`, `resolved_provider/model`, `model_resolution_receipt_digest`, `model_call_receipt_digest`, `repo/base_revision/worktree`, `dispatch_claim_capability_grant_id`, and `attempt_runtime_capability_grant_id`.

The two CapabilityLeases are not interchangeable. `DISPATCH_CLAIM` has no `bound_attempt_id` and cannot authorize model/tool execution; it is consumed by the one child claim. `ATTEMPT_RUNTIME` requires the newly created `bound_attempt_id` and cannot dispatch or claim another task. Neither is an `ACTION_EXECUTION` grant or external-write authority.

Claude Code is an execution harness. It is not counted as another participating Agent unless represented by its own principal, claimed child WorkerTask, Work Controller-created Attempt and receipts. The parent Worker is delegator, not patch author; it may consume the accepted child result and terminalize its parent task, but it cannot re-author the child ChangeProposal.

## State and recovery

Transport dispatch records `PENDING / SENT / ACKED / UNKNOWN / FAILED` separately from business work.

WorkerTask states:

```text
QUEUED → LEASED → COMPLETED
              ↘ WAITING_RETRY → LEASED
              ↘ CANCEL_REQUESTED → CANCELLED
              ↘ EXHAUSTED | BLOCKED_UNKNOWN
```

Attempt states:

```text
CREATED → STARTING → RUNNING → OUTPUT_RECORDED → SUCCEEDED
                    ↘ FAILED | TIMED_OUT
                    ↘ CANCEL_REQUESTED → CANCELLED
                    ↘ UNKNOWN
```

`UNKNOWN` is mandatory when a process or provider may have acted but no trustworthy terminal receipt exists. Reconciliation precedes any new Attempt. Model/provider fallback always creates a new Attempt; it never mutates the identity of the previous Attempt.

## Runtime safety

- isolated worktree and runtime-owned `0600` config;
- fixed or feature-probed Claude Code version;
- `--bare`, `dontAsk`, strict MCP, exact tool allowlist, structured stream output, external schema validation, budget and host wall timeout;
- no `--dangerously-skip-permissions`, personal keychain/config mount, GitHub/SSH credential, approval, release or production token;
- SIGTERM then bounded SIGKILL targets the process group; cancellation does not assert external side effects were rolled back.

## Acceptance failures

Worker off, exporter-only, wrong role, expired fence, receipt replay/mismatch, Skill installed but not called, action-before-Proposal, wrapper success with Claude failure, truncated stream, exit 0 with invalid schema, silent model switch, or failed Decision transaction cannot produce `agent-causal` success.
