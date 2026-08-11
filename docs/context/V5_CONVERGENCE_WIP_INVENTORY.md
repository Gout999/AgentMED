# V5 Convergence WIP Inventory

Status: **PRESERVED / EXCLUDED FROM C0 BASELINE**

Snapshot date: 2026-08-11 (Asia/Hong_Kong)  
Source worktree: original local repository worktree (absolute path intentionally omitted)  
Source branch: `codex/v4-foundation`  
Source HEAD: `c838b2bcefb80c8458aefa17934e190a5d8485f3`  
Clean convergence worktree: temporary clean worktree (absolute path intentionally omitted)  
Convergence branch: `codex/v5-convergence`

## Purpose

This inventory records the pre-existing mixed worktree at the moment the V5
architecture-convergence baseline was created. None of the paths below belongs
to the C0 semantic subject. They remain owned by their original WIP lanes and
must not be bulk-added, reset, rewritten, or used as completion evidence.

The clean convergence branch starts from the independently verified R2 semantic
content at `c838b2b`. It does not absorb later R3/R4/R5, live/provider,
presentation, evidence-repair, migration-numbering, or other mixed local edits
listed here.

## Snapshot summary

- Total status entries: **144**
- Status codes (spaces rendered as `·`):

- `·D`: 12
- `·M`: 105
- `??`: 27

- Top-level path groups:

- `control-plane`: 51
- `contracts`: 22
- `demo-app`: 17
- `cli`: 10
- `console`: 9
- `evidence`: 8
- `docs`: 6
- `eval-harness`: 6
- `scripts`: 6
- `deploy`: 3
- `mcp-servers`: 3
- `PLANS.md`: 1
- `README.md`: 1
- `wiki`: 1

## Exact preserved status

The following is the exact output of
`git status --porcelain=v1 --untracked-files=all` captured from the source
worktree. It is provenance only, not an implementation or verification claim.

```text
 M PLANS.md
 M README.md
 M cli/README.md
 M cli/src/caseloop_cli/_generated/case_v2.py
 M cli/src/caseloop_cli/_generated/manifest_v2.py
 M cli/src/caseloop_cli/_generated/public_v2.py
 M cli/src/caseloop_cli/client.py
 M cli/src/caseloop_cli/discovery.py
 M cli/src/caseloop_cli/main.py
 M cli/tests/test_cli_surface.py
 M cli/tests/test_discovery.py
 M cli/tests/wire_samples.py
 M console/DATA-MAP.md
 M console/src/lib/api.test.ts
 M console/src/lib/api.ts
 M console/src/lib/types.ts
 M console/src/lib/validators.ts
 D console/src/pages/ApplicationsPage.test.tsx
 M console/src/pages/ApplicationsPage.tsx
 M console/src/pages/CaseDetailPage.tsx
 M contracts/conformance/README.md
 M contracts/conformance/test_v5_adversarial.py
 M contracts/conformance/test_v5_compatibility.py
 M contracts/conformance/test_v5_draft.py
 M contracts/conformance/test_v5_first_slice.py
 D contracts/conformance/test_v5_r1_authority_foundation.py
 D contracts/conformance/test_v5_r2_application_catalog_contract.py
 M contracts/v5/README.md
 M contracts/v5/aggregate-ownership.yaml
 M contracts/v5/compatibility.yaml
 M contracts/v5/domain-model.yaml
 M contracts/v5/events.yaml
 M contracts/v5/fixtures/acceptance-wire.yaml
 M contracts/v5/fixtures/application-catalog.yaml
 M contracts/v5/fixtures/bootstrap-import-atomic.yaml
 M contracts/v5/fixtures/first-system-case.yaml
 M contracts/v5/fixtures/first-wire-slice.yaml
 M contracts/v5/fixtures/onboarding-orchestration.yaml
 M contracts/v5/intent-registry.yaml
 M contracts/v5/schema-profiles.yaml
 M contracts/v5/state-machines.yaml
 M control-plane/.env.example
 M control-plane/README.md
 D control-plane/alembic/versions/011_v5_lifecycle_authority_foundation.py
 M control-plane/alembic/versions/012_v5_event_envelope.py
 M control-plane/app/api/cases.py
 M control-plane/app/api/public_v5.py
 M control-plane/app/bootstrap/v5_catalog_local.py
 M control-plane/app/config.py
 M control-plane/app/main.py
 M control-plane/app/models/v4_tables.py
 M control-plane/app/models/v5_tables.py
 M control-plane/app/public_api/v5_capability_models.py
 M control-plane/app/public_api/v5_models.py
 M control-plane/app/services/acceptance.py
 M control-plane/app/services/application_catalog.py
 M control-plane/app/services/case_binding.py
 M control-plane/app/services/case_service.py
 M control-plane/app/services/issue_source.py
 M control-plane/app/services/read_views.py
 M control-plane/app/services/system_versions.py
 M control-plane/app/services/v4_event_store.py
 D control-plane/app/services/v5_application_list.py
 M control-plane/app/services/v5_authority.py
 M control-plane/app/services/v5_capabilities.py
 D control-plane/app/services/v5_lifecycle_authority.py
 D control-plane/app/services/v5_manifest_import_coordinator.py
 M control-plane/tests/integration/test_stage1a_bootstrap_postgres.py
 M control-plane/tests/integration/test_stage1a_public_cli_postgres.py
 M control-plane/tests/integration/test_stage1a_signal_concurrency_postgres.py
 M control-plane/tests/integration/test_v5_application_catalog_postgres.py
 M control-plane/tests/integration/test_v5_case_binding_acceptance_postgres.py
 D control-plane/tests/integration/test_v5_lifecycle_authority_postgres.py
 D control-plane/tests/integration/test_v5_r2_manifest_activation_postgres.py
 M control-plane/tests/integration/test_v5_system_versions_postgres.py
 M control-plane/tests/unit/test_case_service.py
 M control-plane/tests/unit/test_migrations.py
 M control-plane/tests/unit/test_public_v5_api.py
 M control-plane/tests/unit/test_v5_application_catalog.py
 D control-plane/tests/unit/test_v5_application_list.py
 M control-plane/tests/unit/test_v5_capabilities.py
 M control-plane/tests/unit/test_v5_case_binding_acceptance.py
 D control-plane/tests/unit/test_v5_event_foundation.py
 D control-plane/tests/unit/test_v5_lifecycle_authority.py
 M control-plane/tests/unit/test_v5_system_versions.py
 M control-plane/tests/unit/test_v5_system_versions_diff_vectors.py
 M control-plane/tests/unit/test_v5_system_versions_http.py
 M demo-app/Dockerfile
 M demo-app/OPEN-ISSUES.md
 M demo-app/README.md
 M demo-app/app/db.py
 M demo-app/app/main.py
 M demo-app/app/routers/quality.py
 M demo-app/app/seeding.py
 M demo-app/requirements.txt
 M demo-app/tests/unit/test_auth.py
 M deploy/README.md
 M deploy/compose.yaml
 M docs/context/LAST_HANDOFF.md
 M docs/context/PROJECT_STATE.md
 M docs/plans/v5-master-execution-plan.md
 M docs/prd-v5.md
 M eval-harness/.env.example
 M eval-harness/README.md
 M eval-harness/eval_harness/config.py
 M eval-harness/eval_harness/gate.py
 M eval-harness/eval_harness/llm.py
 M evidence/v5/README.md
 M evidence/v5/stage-1/application-catalog/run-20260811-v5-1a/verification.md
 M evidence/v5/stage-1/first-system-case/run-20260811-v5-1c/verification.md
 M evidence/v5/stage-1/system-version/run-20260811-v5-1b/acceptance-v5-1b.md
 M evidence/v5/stage-1/system-version/run-20260811-v5-1b/verification.md
 M mcp-servers/servers/notification.py
 M mcp-servers/tests/test_config.py
 M mcp-servers/tests/test_notification_controller.py
 M scripts/run_b1_live.py
 M wiki/contracts-map.md
?? console/README.md
?? contracts/conformance/test_v5_runtime_overlay.py
?? control-plane/V5_FIRST_CASE_LOCAL.md
?? control-plane/alembic/versions/011_v5_1c_authority_hardening.py
?? control-plane/tests/unit/test_read_views_evidence.py
?? control-plane/tests/unit/test_readiness.py
?? control-plane/tests/unit/test_v5_first_case_local_bootstrap.py
?? demo-app/alembic.ini
?? demo-app/alembic/env.py
?? demo-app/alembic/script.py.mako
?? demo-app/alembic/versions/001_initial_demo_schema.py
?? demo-app/app/schema.py
?? demo-app/docker-entrypoint.sh
?? demo-app/scripts/verify_schema_adoption.py
?? demo-app/tests/unit/test_migrations.py
?? deploy/.env.example
?? "docs/presentation/CaseLoop-\345\256\214\346\225\264\344\272\247\345\223\201\345\217\231\344\272\213\346\226\207\346\241\243.md"
?? "docs/presentation/CaseLoop-\351\241\271\347\233\256\344\277\241\346\201\257\350\257\264\346\230\216\344\271\246.md"
?? eval-harness/tests/unit/test_llm.py
?? evidence/v5/stage-1/first-system-case/review-remediation-20260811/run-manifest.json
?? evidence/v5/stage-1/first-system-case/review-remediation-20260811/verification.md
?? evidence/v5/stage-1/first-system-case/review-remediation-20260811/verifier-report.md
?? scripts/b1_live/agent_trace.py
?? scripts/b1_live/approval_grant.py
?? scripts/b1_live/feishu_message.py
?? scripts/b1_live/test_b1_live_adapters.py
?? scripts/v5_1c_verify_recompute_digest.py
```

## Handling rules

1. C0 and later convergence waves use only clean, stage-specific worktrees and
   exact path allowlists.
2. A path from this inventory may enter a later stage only through explicit
   hunk reconstruction against that stage's clean base and an independent
   provenance review.
3. Historical evidence, presentation, live-provider, credential, or external
   execution artifacts never become runtime proof merely because they are
   present in this worktree.
4. No checksum or evidence digest is generated for this snapshot. The product
   owner deferred SHA-256 and digest-bearing final evidence until the final
   whole-project closure stage.
5. Removing or superseding any preserved WIP requires a separate owner decision;
   C0 performs no destructive cleanup.
