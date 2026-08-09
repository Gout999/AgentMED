# P0-3 Live-provider Report

No live-provider success is claimed by P0-3.

- The browser integration is a real local PostgreSQL + FastAPI + Vite +
  Chromium test, but it does not start the external Quality/StepFun provider.
  `/v1/env` therefore truthfully returns `demo_app=unavailable`, and the TopBar
  displays red `UNKNOWN`.
- `STEPFUN_API_KEY`, `JUDGE_MODEL`, and a provisioned live B1 target are absent.
  Live athlete/judge execution remains blocked.
- A real Feishu adapter and credentials are absent. The integration complaint
  uses the explicitly labelled
  `feishu-mock:contract-replay:console-e2e` contract value; it does not send a
  message and is not live Feishu evidence.
- Docker CLI is installed but its daemon was not available in the verified
  environment. Native local PostgreSQL was sufficient for the Console
  real-stack path.

Contract/replay and local real-stack results are reported in `README.md` and
`verification-summary.json`; they must not be reclassified as provider-live.
