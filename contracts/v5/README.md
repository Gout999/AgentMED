# CaseLoop V5 contract namespace

> Status: **ACCEPTED V5 CONSTRUCTION BASELINE / V5-1A/B/C WORKTREE OVERLAY / STAGES IN_PROGRESS**

This namespace keeps the V5 AI-application governance boundary and its current
runtime activation overlay machine-readable. The V5-0B (`8dd25ca`, system governance
ownership) and V5-0C (`b3727d7`, first system wire slice) freezes are accepted
as the historical construction baseline. The additive runtime overlay records
only the V5-1A/1B/1C HTTP and CLI paths that now exist; it does not retroactively
turn the V5-0C contract freeze into runtime evidence.

## Authority

- `docs/product-principles.md` and D-013 define the accepted product boundary.
- `docs/prd-v5.md`, `docs/plan-v5.md`, the progressive blueprint, and these files are the
  accepted V5 stage construction baseline as of 2026-08-11 (freeze commits `8dd25ca` + `b3727d7`).
- V4 Stage 1A remains the compatibility public slice under `/api/v1`.
- The explicit allowlist in `intent-registry.yaml#runtime_overlay` activates
  the implemented V5-1A/1B/1C `/api/v2` routes and matching CLI commands only.
- Audited V5 capability discovery is active through `capabilities.get` over the
  explicit-major HTTP and CLI paths. Standalone `system-versions.record`, SDK,
  MCP, A2A, and every V5-2+ public intent remain `NOT_IMPLEMENTED`.
- The current migration head is `012`. Bootstrap-only owner calls used inside
  atomic manifest import are not standalone public routes or CLI activation.
- V5-1C AcceptanceCriteria confirmations carry
  `resolution_contract_binding_status=PENDING_MATERIALIZATION`. Until the V5-4
  ResolutionContract owner materializes an exact executable contract, the Case
  remains `NEEDS_ACCEPTANCE_CRITERIA`, never `READY`.
- V3 and V4 lifecycle contracts remain authoritative for every fact they
  already own. Because their JSON Schemas are closed, V5 proposes strict
  schema-major-2 system profiles with the same logical Controller owners and
  compatible lifecycle semantics; it neither inherits incompatible V4 fields
  nor creates parallel lifecycles. The human Approval lifecycle is defined in
  V5 because V4 named that target resource but did not provide its executable
  state machine.

## Hard invariants

1. `SystemVersionSet` is an immutable declaration of runtime composition. It
   never contains the governance-side `EvaluationBundle`.
2. Evaluation binds the exact base and target VersionSets, CandidateRevision,
   and EvaluationBundle independently.
3. `SystemAssignment` is desired state and is owned only by Version Controller.
   Executor success cannot mutate or prove observed state.
4. `ObservedStateSnapshot` and external-effect receipts are independent
   evidence. Missing verification remains `UNKNOWN`.
5. `SystemEpisodeView` and `SystemReleaseView` are provenance-preserving read
   models with no commands. Gate and attribution bind an immutable
   `SystemEpisodeSnapshot`, never a mutable projection.
6. Every authoritative V5 record uses the common workspace/revision/digest/
   AuthorityReceipt envelope and the V5 exact-binding kind space.
7. Candidate, EvaluationPlan, GateReport, WorkOrder, human ApprovalGrant,
   CapabilityLease, and ExternalOperation use schema-major-2 profiles while
   retaining their V4 logical owners; Approval receives its missing explicit
   target lifecycle. V5 adds no ChangeSet or release state machine.
8. `ReleasePlan` is frozen before Gate. Gate, WorkOrder, Approval, target,
   generation, nonce, and expiry are bound in that order; the Executor cannot
   add rollout or recovery parameters after approval.
9. Release and rollback both use `SystemExternalOperation`. Direct assignment
   restore/resume is forbidden, and success requires an independently observed
   complete match rather than a desired-state readback.
10. HTTP, CLI, MCP, A2A, SDK, and Console are adapters to the same canonical
   intents. Protocol task completion is not Gate PASS or release success.
11. A trusted one-shot manifest import reaches the first system state only by
    invoking each canonical owner inside one local PostgreSQL transaction; it
    cannot silently synthesize prerequisite resources.

Green V5 conformance tests prove only that these documents agree. They
are `contract` evidence, never runtime, provider-live, Agent-causal, external,
or production evidence.

Run the complete V5 contract checks with:

```bash
cd contracts
../eval-harness/.venv/bin/python -m pytest conformance/test_v5_*.py -q
```
