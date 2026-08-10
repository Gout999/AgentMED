# CaseLoop v4 contract namespace

This directory is the executable contract namespace for the v4 product
direction. It does not replace the current v3 implementation baseline in
`contracts/events/`, `contracts/quality-api/`, or `contracts/schemas/`.

## Authority and status

- `docs/product-principles.md` defines product scope and strategy.
- `docs/plan-v3.md`, the existing contracts, and executable tests remain the
  current implementation baseline until a documented cutover.
- Files under `contracts/v4/` describe the target v4 boundary. A green schema
  or conformance test is contract evidence, not runtime or live-provider
  evidence.
- PostgreSQL remains authoritative for lifecycle, permissions, idempotency,
  approvals, decisions, release state, and audit.
- Agent, model, Skill, MCP, Matrix, MinIO, Langfuse, and exporter output is
  evidence or a proposal. None of it is authoritative lifecycle state.

## Stage 0 contract slices

The current Stage 0 slices freeze:

1. aggregate ownership and the v3-to-v4 lease cutover invariant;
2. the public intent registry and transport exposure rules;
3. an OpenAPI 3.1 skeleton for the registered public HTTP intents;
4. common identifiers, public errors, idempotency receipts, SignalEnvelope,
   AgentRunRef, and TraceEvidenceReceipt;
5. ResolutionContract, pre-build CandidateContract and EvaluationPlan,
   pre-controlled-action typed Proposal and ProposalDecision, WorkerTask and Attempt, then
   the post-success immutable CandidateRevision;
6. AgentManifest, ParticipationManifest, CapabilityLease, typed GateReport,
   post-Gate v4 WorkOrder, SkillManifest, and MCPManifest;
7. the event catalog and reachable fail-closed state machines, including
   WorkerTask claim creating the Work Controller-owned Attempt, Proposal
   acceptance plus its first downstream event in one transaction, explicit
   `UNKNOWN` reconciliation, and WorkOrder creation only from an exact `PASS`
   GateReport whose required track set is complete and receipt-backed;
8. ControllerRegistration trust roots and one-way, post-record AuthorityReceipt
   bindings for every Controller-recorded snapshot in this Stage 0 slice;
9. positive fixtures and targeted negative mutation fixtures plus deterministic
   schema, ownership, causality, permission-intersection, and cutover tests.

`common-defs.schema.json` also freezes one canonical evidence-facet vocabulary:
`contract`, `replay`, `domain-provider-live`, `agentteams-native`,
`claude-runtime-live`, `agent-causal`, `repo-sandbox`,
`human-authorized-external`, and `production-canary`. Facets are separate
claims, not aliases or combinable success labels.

No migration or runtime implementation is implied by this directory.

## Exact executable chain and Stage boundary

Stage 0 freezes one acyclic, content-addressed chain:

`ResolutionContract -> CandidateContract + EvaluationPlan -> WorkerTask(r1 queued)
-> WorkerTask(r2 claimed) -> Attempt(r1 created) -> CapabilityLease(r1 issued)
-> Attempt(r4 sandbox output sealed) -> ChangeProposal -> ProposalDecision
-> Attempt(r5 terminal) -> CandidateRevision -> GateReport -> WorkOrder`.

CandidateContract and EvaluationPlan are frozen before candidate production.
The Agent may generate and seal a candidate artifact inside its isolated
sandbox before Proposal submission. A ChangeProposal binds both the pre-build
contract and that exact `OUTPUT_RECORDED` Attempt snapshot; it is not merely a
plan artifact and never points to a future CandidateRevision. ProposalDecision
must precede the controlled downstream action (for example Controller, repo, or
release mutation), not the sandbox generation that produced the proposal.
Only after acceptance and Attempt success may the Proposal Controller record
CandidateRevision, binding the exact accepted Proposal, terminal producer
Attempt, output, base, target, and diff. GateReport and WorkOrder then bind that
exact revision.

`EvaluationPlan.judge_policy.judge_model_policy_digest` and
`threshold_digest` are Stage 0 content-addressed policy pointers, not proof that
a runtime used those bytes. Before Stage 3 may accept a real Judge track, the
typed execution contract must bind the former to the exact Judge
AgentManifest/model-call policy and the latter to the exact threshold/decision
receipt. Until then, an opaque matching digest is contract-only evidence and
cannot satisfy a live Gate.

Every mutable Controller aggregate is represented by append-only, whole-record
revision snapshots. `previous_snapshot` binds the exact immediately preceding
`{kind,id,revision,digest}`. Capability issuance binds the historical resource
snapshot available at issuance: dispatch binds queued WorkerTask r1 and runtime
authority binds CREATED Attempt r1. A terminal Attempt or WorkerTask digest may
not be backfilled into an earlier lease. Valid bundles retain both issuance or
claim snapshots and current terminal snapshots.

Controller authority is deliberately acyclic. The Controller preallocates an
`authority_receipt_id`, writes that id into the business record, computes the
business self-hash, and then records an immutable AuthorityReceipt in the same
PostgreSQL transaction. The receipt binds the exact subject, active
ControllerRegistration, owner, Controller principal, command, event,
transaction, and audit reference, then self-hashes. The subject never stores
the receipt digest. ControllerRegistration is the non-recursive trust root;
AuthorityReceipt is recording proof, not CapabilityLease, ApprovalGrant, or
execution authority. Offline fixtures prove only the `contract` facet and
cannot prove that a runtime event or audit row was actually authoritative.

The formal GateReport terminal statuses are `PASS`, `FAILED`, `INCONCLUSIVE`,
`ERROR`, and `UNKNOWN`. `PASS` holds if and only if every exact required
EvaluationPlan track has one receipt with `COMPLETE` completeness and `PASS`
status. Missing, duplicate, substituted, partial, error, or unknown track
evidence cannot promote a WorkOrder. Resolution required acceptance criteria
are canonically digested into the EvaluationPlan and their verifier kinds must
remain required Gate tracks.

The Stage 0 exact executable chain ends at WorkOrder. ApprovalGrant,
PolicyGrant, accepted `ACTION_EXECUTION`, and ExternalOperation semantics are
Stage 4 work. Their current shapes or pointers are not execution authority;
the v4 semantic validator stably rejects `ACTION_EXECUTION` acceptance until
that later stage is implemented.

## Canonical integrity profile

Immutable chain records declare
`jcs-rfc8785-v1+sha256(excluding:/<self-digest-field>)`. Conformance uses the
pinned Apache-2.0 `rfc8785==0.1.4` implementation: Unicode is preserved without
normalization and object names use RFC 8785 UTF-16 ordering. The CaseLoop
profile rejects floating-point values, non-finite values, lone surrogates, and
unsafe integers; a missing dependency fails closed and has no fallback
serializer.

Only a record's declared top-level self digest uses this exclusion rule. The
self digests in this slice include `signal_digest`, `receipt_digest` on
TraceEvidenceReceipt and IdempotencyReceipt, `agent_run_ref_digest`, and the
manifest/aggregate digests. Content anchors remain distinct:
`AgentRunRef.locator_digest` hashes only `locator`,
`TraceEvidenceReceipt.source_payload_digest` hashes provider response content,
`MCPManifest.tool_catalog_digest` hashes only the tool catalog, and
idempotency request/response digests hash their respective wire content. None
of those content anchors is the containing record's self-hash. An
IdempotencyReceipt is an immutable wire-response receipt: every replay returns
the original immutable receipt byte-for-byte with the same id and self-hash.
`replayed` belongs to per-delivery
metadata outside that record. Authoritative PostgreSQL idempotency state
remains a separate Controller record.
SignalEnvelope is a client-computable immutable submission envelope: its
`signal_digest` is the envelope self-hash, but it does not claim a
ControllerRegistration or AuthorityReceipt. Controller acceptance must create
a separate authoritative snapshot, event, audit row, and post-record receipt;
the submitted envelope alone proves no runtime authority.

Each registered intent has a machine-readable `execution_mode` and per-transport
activation stage. HTTP and CLI activate at `first_stage`; a non-null Public MCP
or A2A mapping activates at Stage 6, while a null mapping has a null stage.
`signal submit` is the canonical CLI command and `report` is only its alias.
The frozen S1A/S1B source/evidence commands are `capabilities get`,
`source capabilities`, `source doctor`, `source sync-run get`, and
`evidence get`; adjacent command-directory names are not implemented merely
because they appear in a roadmap.

## Stage 1 wire-entry extension

The Registry is a stage-aware target catalog. `FROZEN` S1A/S1B intents have
non-null request/response `field_contract_ref` values and are the only
operations present in `openapi/public-api.yaml`. Stage 2/4 entries remain
`SKELETON` with null field contracts; they cannot generate routes, CLI/SDK
methods, or capability discovery results.

Every frozen operation requires bearer authorization, a workspace header, and
an exact contract-version header. The server resolves principal, audience,
workspace/project/environment, scopes, time bounds, and revocation before it
trusts the requested workspace. Raw token and raw jti are never stored or
returned. Auth-before-workspace failures use `workspace_id:null`; an audit/DB
commit failure uses `audit_ref:null` and rolls back the business transaction.

The no-trace maintainer path is explicit evidence, not a fake Agent run:
`TraceEvidenceReceipt.collection_mode=NO_LOCATOR`, `query=null`, both AgentRunRef
binding fields null, every canonical requested trace field `MISSING`, and
`completeness=UNKNOWN` with non-retryable `NO_TRACE_LOCATOR`. The receipt binds
the exact Signal id and digest. QualityCase remains `OPEN`; correlation and
triage are orthogonal statuses (`NEEDS_CORRELATION`, `UNTRIAGED`).

Signal intake is a four-owner transaction: Signal record, QualityCase,
SignalCaseLink, and TraceEvidenceReceipt each receive their own Controller
record and AuthorityReceipt, while `signal.received`, `case.opened`,
`signal_case_link.linked`, `evidence.recorded`, audit, outbox, and idempotency
commit together. The source must be an `ACTIVE` manual SourceConnection bound
to the authenticated workspace. v4 routing is keyed by contract version,
aggregate type, and event type on a separate channel; v3 event-name-only
routing is forbidden for this slice.

Runtime implementation must pin `rfc8785==0.1.4` and reuse the contract's
no-float, one-self-field-exclusion profile. A legacy sorted-JSON digest is not a
v4 immutable-record hash. PostgreSQL audit is authoritative; JSONL/export is
after-commit/outbox-only or disabled so rollback cannot leave a ghost success.

## Compatibility rules

- v3 and v4 contracts are versioned side by side; v4 does not silently widen a
  v3 payload or state machine.
- Public HTTP uses `/api/v1`. Existing `/v1` routes remain implementation and
  internal/demo surfaces until explicitly migrated.
- Public commands carry `schema_version`, workspace identity, authenticated
  principal identity, a stable intent name, and an idempotency key where the
  intent registry marks it required.
- Connector `source_event_id` deduplication is distinct from public command
  idempotency.
- A unit of work can use a v3 Case lease or a v4 WorkerTask lease, never both.
  Rollback stops or drains the v4 path; it does not recreate a v3 lease for an
  already migrated task.
- The server supports the current public contract and the immediately previous
  compatible minor contract. Breaking changes require a new major API path.

## Verification

From the repository root:

```bash
eval-harness/.venv/bin/python -m pytest \
  contracts/conformance/test_v4_*.py -q
```

The existing v3 schema and Wilson tests must remain green independently.
