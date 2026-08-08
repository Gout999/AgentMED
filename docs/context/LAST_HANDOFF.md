# Last Handoff

- `round_goal`: close P0-3 by wiring the production Console to authoritative
  T8/control-plane reads with fail-closed runtime semantics and real-stack proof.
- `actual_changes`: replaced static/placeholder screens with typed Case,
  Experiment, WorkOrder, Gate, Release, Notification, Trust, and Evidence reads;
  added loading/empty/error/retry/stale/UNKNOWN states; made WorkOrder the
  immutable read authority; validated WorkOrder columns, Gate binding, active
  VersionSet shape, and API response schemas; removed raw WorkOrder/Evidence
  content; added route-key race isolation; added real Operations view and
  PostgreSQL/FastAPI/Vite/Chromium integration.
- `key_files`: `control-plane/app/services/read_views.py`,
  `control-plane/app/api/read_views.py`, `console/src/lib/api.ts`,
  `console/src/lib/validators.ts`, `console/src/hooks/usePageData.ts`,
  `console/src/pages/`, `console/scripts/run-real-stack-test.sh`, and
  `evidence/p0/p0-3-console/`.
- `test_results`: independent verifier PASS; control-plane 190; P0-3 focused 42;
  Console 7 plus build; real stack 1; contracts 26. With previously rerun
  service suites the non-overlapping total is 385 passed, 0 failed, 5
  live-only skipped. npm audit still reports 4 moderate and 1 high advisory,
  documented as a cross-major Router/Vite migration debt.
- `unfinished`: P0-4 B1 authority-safe vertical loop and final run manifest.
  Live StepFun/judge and Feishu remain externally blocked and are not represented
  by replay substitutes.
- `resume_from`: remove eval-runner Quality write authority, model bad/fixed
  states as exact immutable VersionSets, enforce authoritative frozen
  attribution, then reuse Gate/Approval/Release/Outbox/Notification/Trust for
  the replay command.
- `commit_hash`: P0-3 `a08c0056691b3acdafb43fd0a8b1417d10985fe6`;
  P0-2 `8e237e3`; P0-1 `4cd6e64`.
