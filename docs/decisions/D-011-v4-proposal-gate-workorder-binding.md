# D-011 — Proposal, Gate and WorkOrder Binding

Status: **ACCEPTED**
Date: 2026-08-10

## Decision

Agent creativity ends at a typed, content-addressed Candidate Proposal. The Controller validates and freezes it; Evaluation/Gate independently decides whether it is eligible for execution; WorkOrder is created only after the exact Candidate has a terminal `PASS` Gate.

The immutable chain is:

```text
input snapshot + AgentVersionSet
→ ResolutionContract
→ CandidateContract(planned revision + base + allowed scope)
→ EvaluationPlan + visible/hidden dataset digests
→ sandbox Attempt output sealed
→ pre-controlled-action ChangeProposal + ProposalDecision
→ exact Attempt terminal + CandidateRevision(artifact + diff + target)
→ track receipts + GateReport
→ WorkOrder(target, base revision, diff, scope, nonce, expiry)
→ ApprovalGrant or PolicyGrant
→ ExternalOperation receipt
```

Every link carries schema/version and upstream digest. CandidateContract and EvaluationPlan are frozen before the Generator sees the task. The Generator may produce and seal a candidate artifact only inside its isolated sandbox. ChangeProposal then binds that exact `OUTPUT_RECORDED` Attempt snapshot and is accepted or rejected before the first controlled Controller, repository, release, or other external action; it is not a plan artifact and never references a future CandidateRevision. CandidateRevision exists only after the accepted Proposal's exact Attempt terminates; it binds back to the CandidateContract, terminal producer Attempt and Proposal content. Any changed byte creates a new CandidateRevision, Gate and WorkOrder. `FAILED / INCONCLUSIVE / ERROR / UNKNOWN`, missing, expired or mismatched evidence fails closed.

Stage 0 的可执行 conformance chain 到 `WorkOrder-v4` 为止。它只冻结 `required_authorization` 指针与 CapabilityLease 的结构边界；`ApprovalGrant`、`PolicyGrant`、`ACTION_EXECUTION` 接受语义及真实 `ExternalOperation` 全部推迟到 Stage 4。Stage 0 semantic validator 必须稳定拒绝 `ACTION_EXECUTION`，不能把上图末两步描述成已实现或已验证。

## Evaluator separation

The Evaluator Agent can submit Finding and operate a sandbox but cannot produce Gate authority. Deterministic tests/rules and an independent model judge are separate tracks. The judge must have a verified independence key from the final Generator. Missing independence is `ERROR` or `HUMAN_REQUIRED`, never automatic pass.

Generator cannot read hidden holdout labels/content, change the frozen EvaluationPlan, approve itself, or modify tests to manufacture success. Changes to evaluator assets require a separate governed Candidate and cannot ship in the same release as the capability under evaluation.

## v3 compatibility

Existing v3 WorkOrder/Gate contracts remain valid for the customer-service Scenario. A Scenario Adapter translates only documented fields; it cannot reinterpret a pre-Gate object as a v4 executable WorkOrder. Cutover is versioned and tested.
