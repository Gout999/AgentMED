# P0-3 Console Dependency Audit

`npm audit --audit-level=high` exited 1 on 2026-08-09 with **4 moderate** and
**1 high** advisory:

- the Vite 5 line depends on an affected esbuild range;
- the React Router 6 line is within the reported React Router advisory range.

The automated remediation proposes `vite@8.2.1` and
`react-router-dom@7.18.2`, both breaking major upgrades. P0-3 preserves the
collaborator's React 18 / Router 6 / Vite 5 architecture and applies only
same-major patch updates (`react-router-dom@6.30.4`, `vite@5.4.21`,
`postcss@8.5.26`) plus exact test dependencies. The remaining audit result is
an explicit P1 dependency-migration risk, not a hidden successful check.

The P0-3 real-stack dev server binds only `127.0.0.1`; no public development
server was used as evidence.
