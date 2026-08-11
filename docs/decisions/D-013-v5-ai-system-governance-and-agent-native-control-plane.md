# D-013 — V5 AI-system governance and agent-native control plane

Status: **ACCEPTED PRODUCT BOUNDARY / V5 CONSTRUCTION BASELINE ACCEPTED**
Date: 2026-08-10
Decider: product owner

## Context

V4 Stage 1A has implemented a narrow but reusable public foundation: authenticated
HTTP/CLI intake records a `Signal`, opens a `QualityCase`, records an honest
no-locator `UNKNOWN` evidence receipt, and commits idempotency, audit, outbox and
authority records transactionally. Later V4 stages remain unimplemented. The V4
construction order assumed that CaseLoop would first build and prove its own
quality and coding Agent Teams, then expose the system to other Agents near the
end of the roadmap.

The product owner has clarified a broader and more important boundary. CaseLoop
must govern the complete AI-enabled application system, not only one Agent, and
must be consumable as a capability by the user's existing Agents, CI and AI
applications. A human Console remains required for supervision and authority,
but it is not the only or primary integration surface.

## Decision

CaseLoop V5 is an **agent-native governance and operations control plane for AI
applications**.

1. The governed top-level resource becomes `AIApplication`; an Agent remains a
   first-class `SystemComponent` rather than the whole product boundary.
2. V5 introduces immutable `SystemVersionSet`, a mutable cross-component
   `SystemEpisodeView` plus immutable evidence snapshots, a system-level
   `SystemCandidateRevision`, and independent `EvaluationBundle`. V4's closed
   Candidate/Evaluation/Gate/WorkOrder schemas are not extended in place;
   system records use schema-major-2 profiles while retaining the same logical
   owners and compatible lifecycle semantics. V5 explicitly defines the human
   Approval lifecycle that the V4 target chain named but did not implement.
3. Candidate verification and release authorization share the same evidence/Gate
   kernel but are different purposes. A verification-only PASS may end as
   `VerifiedCandidate / NOT DEPLOYED` and cannot create a WorkOrder. When a
   deployable target is explicitly requested, release order is `Candidate +
   pre-Gate ReleasePlan → release-authorization Gate PASS → WorkOrder → human
   Approval → scoped ExternalOperation → Assignment → independent observation`.
   The Executor cannot author plan parameters after approval; release and
   rollback never obtain a direct Assignment mutation path.
4. Desired configuration, observed runtime state and external effects are
   recorded separately. None may be inferred from either of the others.
5. The deterministic PostgreSQL governance kernel remains authoritative.
   External or internal Agents may submit Signals, evidence, hypotheses,
   Findings, Proposals and candidate artifacts; they do not own approval,
   release arbitration or authoritative lifecycle state.
6. Canonical application intents are the product core. HTTP, CLI, MCP, A2A,
   SDK and Console are adapters with the same resource, idempotency, error,
   authority and audit semantics.
7. A real external Agent consuming the CLI is sufficient for the first
   Agent-native competition proof. A2A and Public MCP become independent early
   increments only after durable work exists; they do not block the governance
   kernel. Human approval and internal release execution are never
   model-invocable capabilities.
8. The V4 Stage 2A durable work, Stage 3 candidate/evaluation and Stage 4
   approval-bound external-operation invariants are carried into V5. Claude
   Code, AgentTeams and the proposed coding Team become optional runtime/client
   adapters rather than prerequisites for the governance kernel.
9. V4 Stage 1A remains implemented and compatible. V5 is introduced by
   expand-and-route; it does not rewrite v3 or v4 tables, histories, evidence or
   public receipts in place.
10. First Useful Case records the source and human confirmation of acceptance
    criteria. Missing criteria remain an additive `NEEDS_ACCEPTANCE_CRITERIA`
    readiness condition; an Issue or Agent proposal cannot manufacture the
    authoritative expected behavior.
11. CLI-first onboarding may discover a manifest and compose Case intake intents,
    but discovery only produces a draft/`UNKNOWN`. Human confirmation and the
    canonical controllers remain authoritative; the helper creates no new owner.

The exact V5 schemas and construction stages were subsequently reviewed and accepted on
2026-08-11 as the V5 stage construction baseline, including freeze commits `8dd25ca` and
`b3727d7`. That acceptance authorizes staged local construction only; it does not itself prove
runtime, provider, Agent, external-operation, or production capability. Current execution is
orchestrated by `docs/plans/v5-master-execution-plan.md` and reported in `PLANS.md` and
`docs/context/`.

## Alternatives considered

### Continue V4 Stage 1B through Stage 7 unchanged

- **Pros**: already documented; preserves the previous dependency order.
- **Cons**: makes CaseLoop's own Coding Team and specific runtimes central;
  delays Agent-native consumption; continues to model one Agent as the top-level
  governed resource.
- **Why not**: it does not match the confirmed AI-application-system product
  boundary or the intended integration model.

### Build a new V5 stack beside v3 and v4

- **Pros**: avoids compatibility constraints during early prototyping.
- **Cons**: creates a third Signal/Case/Gate/Release authority; duplicates
  migrations and public identities; makes audit and rollback ambiguous.
- **Why not**: incompatible lifecycle authorities are more dangerous than a
  deliberate expand-and-route migration.

### Turn CaseLoop into a full CMDB, observability platform, runtime and ITSM

- **Pros**: one product appears to contain every governance feature.
- **Cons**: unbounded scope; duplicates mature systems; weakens the exact AI
  version/evidence/change transaction that CaseLoop must own.
- **Why not**: V5 owns AI-system governance facts and integrates generic asset,
  telemetry, IAM, ticketing, deployment and billing systems through adapters.

## Consequences

### Positive

- Existing Agents can consume CaseLoop without moving their workflow into the
  Console or adopting AgentTeams.
- The product can govern failures caused by application code, model, Prompt,
  RAG, tool, policy, memory or their interaction.
- V4 1A and the v3 reference Gate/Release assets remain useful compatibility
  foundations rather than being discarded.
- The competition workload can prove a real GitHub Issue to system-level local
  verification loop without requiring an upstream write.

### Negative

- V5 requires new system-level identity, topology, version and execution
  contracts before feature implementation resumes.
- Cross-component release and rollback require explicit partial/`UNKNOWN`
  semantics and compensation; they cannot be presented as one atomic external
  transaction.
- Existing Agent-specific UI, objects and metrics need compatibility views and
  staged migration.

### Risks and mitigations

- **Scope explosion**: keep generic CMDB, SRE, ITSM, IAM, billing and GRC as
  adapters; make the first system manifest intentionally small.
- **Third authority stack**: additive migrations, route-version receipts and
  one-owner contracts are mandatory before cutover.
- **Legacy naming collision**: V5 does not reuse the v3 `ChangeSet` name for a
  new lifecycle. The v3 aggregate remains a Scenario compatibility object;
  V5 composes `CandidateRevision`, `WorkOrder` and `ExternalOperation` instead.
- **Transport-driven architecture**: freeze canonical intents before MCP/A2A;
  transport task completion never implies Gate PASS or release success.
- **Skipping safety foundations**: durable work, exact Gate binding, approval,
  audit, reconciliation and compensation remain hard dependencies.
- **Overclaiming**: all V5 documents and contracts remain target-only until real
  migrations, services, tests and evidence close the corresponding stage.

## Compatibility

- D-008 through D-012 remain active for aggregate ownership, runtime causality,
  capability/secret separation, Gate-to-WorkOrder binding and expand-and-route
  migration unless a later V5 ADR explicitly supersedes an individual rule.
- V3 customer-service contracts and evidence remain historical/compatibility
  facts; V5 does not upgrade them to system-governance evidence.
- V4 Stage 1A routes and receipts remain served under their existing contract.
  New V5 operations may reference them but cannot mutate their recorded facts.
