# CaseLoop V5 draft contract namespace

> Status: **ACCEPTED V5 CONSTRUCTION BASELINE (2026-08-11; freeze `8dd25ca` + `b3727d7`) / NOT IMPLEMENTED**

This namespace makes the proposed V5 AI-application governance boundary
machine-readable before any route, migration, worker, Adapter, Console page, or
live capability is implemented. The V5-0B (`8dd25ca`, system governance
ownership) and V5-0C (`b3727d7`, first system wire slice) freezes are accepted
as the V5 stage construction baseline; acceptance is contract-only and does not
make any runtime facet exist.

## Authority

- `docs/product-principles.md` and D-013 define the accepted product boundary.
- `docs/prd-v5.md`, `docs/plan-v5.md`, the progressive blueprint, and these files are the
  accepted V5 stage construction baseline as of 2026-08-11 (freeze commits `8dd25ca` + `b3727d7`).
- V4 Stage 1A remains the current implemented public slice under `/api/v1`.
- V5 system resources target `/api/v2`; no V5 route or capability discovery is
  enabled by this directory.
- V3 and V4 lifecycle contracts remain authoritative for every fact they
  already own. Because their JSON Schemas are closed, V5 proposes strict
  schema-major-2 system profiles with the same logical Controller owners and
  compatible lifecycle semantics; it neither inherits incompatible V4 fields
  nor creates parallel lifecycles. The human Approval lifecycle is defined in
  V5 because V4 named that target resource but did not provide its executable
  state machine.

## Proposed hard invariants

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

Green V5 draft conformance tests prove only that these documents agree. They
are `contract` evidence, never runtime, provider-live, Agent-causal, external,
or production evidence.

Run the focused draft check with:

```bash
cd contracts
../eval-harness/.venv/bin/python -m pytest conformance/test_v5_draft.py -q
```
