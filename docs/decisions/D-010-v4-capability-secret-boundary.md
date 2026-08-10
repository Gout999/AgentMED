# D-010 — Capability and Secret Boundary

Status: **ACCEPTED**
Date: 2026-08-10

## Decision

External Agent, connector, runtime, human and internal Controller principals are distinct. Credentials are never inferred from a claimed role and never forwarded across trust boundaries.

Public tokens are workspace/project/environment scoped and audience-bound. Minimum scopes are:

```text
signals:write
cases:read
runs:start
runs:read
runs:cancel
artifacts:read
sources:read
sources:manage
approvals:request
approvals:decide:human
repo_changes:request
releases:request
external_operations:execute:internal
policies:manage
```

Agent/service tokens cannot receive `approvals:decide:human` or `external_operations:execute:internal`. `repo_changes:request` and `releases:request` create non-authoritative requests; they never grant execution authority. Release, Repo, Closure and Skill Distribution credentials stay inside their deterministic Executors.

## Secret handling

- database records store `credential_ref` and capability metadata, not plaintext secret;
- interactive credentials enter through stdin/keyring/env or a `0600` secret file, not argv or checked-in config;
- evidence and logs record redacted provider/origin/credential-reference digests, never secret values;
- CapabilityLease is one-time or short-lived and always binds its issuance basis, exact resource snapshot, permission intersection, budget and expiry. `DISPATCH_CLAIM` may be issued before an Attempt exists; `ATTEMPT_RUNTIME` binds the claimed Attempt; neither is described by the looser phrase “all runtime grants bind an Attempt”;
- revocation, rotation, lost-key and unknown-outcome behavior are audited and fail closed.

Langfuse project credentials are treated as general project-scope secrets unless an explicitly verified read-only mechanism exists. CaseLoop enforces a read-operation allowlist and never markets the underlying key as fine-grained read-only.

## External write boundary

The first real GitHub case requires separate human authorization for comment/claim, fork, push, draft PR, close and merge. A local verified patch grants none of these actions. Later PolicyGrant automation is limited by risk, repo, base revision, diff digest, action, budget, blast radius, expiry and kill switch.

## Stage 0 boundary and canonical names

`ApprovalGrant` is the canonical human-approval record name; “human approval grant” is descriptive text, not a second schema. `CapabilityLease(grant_kind=ACTION_EXECUTION)` is the canonical action capability; `ActionCapability` is not a separate object.

Stage 0 freezes only CapabilityLease shape, permission-intersection rules and the WorkOrder `required_authorization` pointer. `ApprovalGrant`, `PolicyGrant`, accepted `ACTION_EXECUTION` and actual ExternalOperation authority are Stage 4 contracts. Until Stage 4 lands, the semantic validator must hard-reject `ACTION_EXECUTION`; a shape-valid lease is not execution authorization.
