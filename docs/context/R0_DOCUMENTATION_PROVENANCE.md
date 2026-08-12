# R0 Documentation Authority Provenance

> Status: **SEMANTIC SUBJECT INVENTORY / VERIFIED (2026-08-12 更正)** —— semantic subject
> `4d15c1c81180386fa4852a53f8b8847e74cda050` 已通过 detached clean-checkout 独立复核
> （P0=0/P1=0），R0 记为 `DONE`；证据见
> `evidence/v5/stage-0/documentation-authority/r0docs_20260811T104032Z_4d15c1c/`。
> checksum-bearing final evidence 仍按 owner 指示延后至全项目最终收口。
>
> Pre-R0 baseline: `4a0a421cc669bf98d9b882d149d5d3df4c8dc36e`
>
> This inventory defines the exact documentation-authority subject. It does not
> include runtime, contract activation, migration, evidence, live/provider or
> presentation work and does not make any runtime evidence facet pass.

## Included paths

The semantic subject contains exactly these paths. `README.md` and
`docs/prd-v5.md` use the narrower hunk boundaries below; every other path is
included as a whole file.

```text
README.md
PLANS.md
STATUS.md
docs/README.md
docs/archive/README.md
docs/archive/context/LAST_HANDOFF-history-through-2026-08-11.md
docs/archive/context/V5_CONSTRUCTION_CONTEXT-2026-08-10.md
docs/archive/decisions/D-002-executor-routing-2026-08-07.md
docs/archive/plans/phase1-execution-2026-08-07-b1.md
docs/archive/status/STATUS-2026-08-09-pr1.md
docs/competition/component-mapping.md
docs/context/LAST_HANDOFF.md
docs/context/PROJECT_STATE.md
docs/context/R0_DOCUMENTATION_PROVENANCE.md
docs/context/V5_CONSTRUCTION_CONTEXT.md
docs/decisions/D-002-executor-routing.md
docs/decisions/D-013-v5-ai-system-governance-and-agent-native-control-plane.md
docs/plan-v5.md
docs/plans/phase1-execution.md
docs/plans/v4-progressive-delivery.md
docs/plans/v5-master-execution-plan.md
docs/plans/v5-progressive-delivery.md
docs/plans/wave3-soul-design.md
docs/prd-v5.md
docs/product-principles.md
wiki/INDEX.md
wiki/build-guide.md
wiki/decisions.md
wiki/project-brief.md
wiki/v4-execution-map.md
```

### Selected README hunks

Included:

- AI-application / Agent-native product identity and honest partial-runtime status;
- accepted V5 authority chain and Master Plan navigation;
- optional Adapter boundary and the v3 Trust-accounting clarification;
- the corrected ordering through confirmed AcceptanceCriteria, V5-4 exact
  ResolutionContract, executable BadcaseSpec, Candidate and guarded release;
- contributor navigation and the general `docs/` directory description.

Excluded for R5 or later runtime/contract closure:

- `deploy/.env.example`, Compose commands, host/port and deployment semantics;
- the `contracts/` directory claim about the current runtime overlay.

### Selected PRD hunks

Included:

- accepted baseline with stage-partial implementation status;
- V5-4 ResolutionContract before executable badcase;
- the corresponding success metric.

Excluded for R2/R3 contract/runtime activation:

- current manifest-import and version-recorder role/permission allowlists.

## Explicitly excluded dirty groups

The following paths or groups may remain dirty in the shared worktree, but their
intersection with the semantic subject must be empty:

```text
cli/README.md
cli/src/**
cli/tests/**
console/README.md
console/src/**
contracts/conformance/README.md
contracts/conformance/test_v5_*.py
contracts/v5/README.md
contracts/v5/**/*.yaml
control-plane/.env.example
control-plane/README.md
control-plane/V5_FIRST_CASE_LOCAL.md
control-plane/alembic/**
control-plane/app/**
control-plane/tests/**
demo-app/**
deploy/**
eval-harness/**
evidence/v5/**
mcp-servers/**
scripts/**
docs/presentation/**
wiki/contracts-map.md
```

The four pre-existing V5 stage-1 evidence bundles are historical artifacts and
must not be rewritten inside R0. The uncommitted remediation bundle is WIP, has
no accepted subject commit or manifest digest, and is not closure evidence.

## Post-commit closure protocol

The semantic commit is not self-verifying. A detached clean checkout must bind
its exact full hash and parent, prove the path set equals this inventory, run
tracked Markdown link, status-drift, archive-provenance, diff, secret and PII
scans, and receive an independent verifier verdict with P0/P1 equal to zero.
All nine canonical runtime evidence facets remain `NOT_RUN` for R0. A separate
evidence/status commit records the manifest, artifact digests, verifier report
and final R0 verdict.
