# P0-2 Live-provider Report

No live-provider PASS is claimed.

- The real Feishu adapter and credentials are not present. Production defaults
  to `NOTIFICATION_ADAPTER=disabled`, which fails closed.
- `feishu-mock` is explicitly limited to contract/replay. It implements the
  same receipt and idempotency boundary but is not evidence of a real Feishu
  message.
- `STEPFUN_API_KEY` and `JUDGE_MODEL` remain unavailable, and the Docker daemon
  is not running. Those conditions do not prevent the deterministic outbox,
  Trust, receipt, audit, and archive paths from being verified locally.

The live Feishu E2E remains an external prerequisite for P0-4 and must be
reported separately from the passing contract/replay suite.
