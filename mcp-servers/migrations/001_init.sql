-- AgentMED mcp-servers 自有表（mcp_* 前缀，与 control-plane 公共 schema 无冲突）
-- 幂等：全部 IF NOT EXISTS；由 scripts/run_migrations.py 执行。

CREATE TABLE IF NOT EXISTS mcp_approval_grants (
  approval_id     TEXT PRIMARY KEY,
  workorder_id    TEXT NOT NULL,
  workorder_hash  TEXT NOT NULL,
  nonce           TEXT NOT NULL UNIQUE,
  status          TEXT NOT NULL DEFAULT 'pending',
  decision        TEXT,
  approver        JSONB NOT NULL DEFAULT '{}',
  expiry          TIMESTAMPTZ NOT NULL,
  decided_at      TIMESTAMPTZ,
  nonce_consumed  BOOLEAN NOT NULL DEFAULT FALSE,
  consumed_at     TIMESTAMPTZ,
  proof           JSONB NOT NULL DEFAULT '{}',
  audit_uri       TEXT NOT NULL DEFAULT '',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mcp_approval_workorder_id ON mcp_approval_grants (workorder_id);

CREATE TABLE IF NOT EXISTS mcp_audit (
  audit_id      TEXT PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor         TEXT NOT NULL,
  action        TEXT NOT NULL,
  target        TEXT NOT NULL,
  params_digest TEXT NOT NULL,
  result        TEXT NOT NULL,
  error_code    TEXT,
  trace_id      TEXT NOT NULL,
  evidence_refs JSONB
);

CREATE TABLE IF NOT EXISTS mcp_notification_messages (
  message_id  TEXT PRIMARY KEY,
  channel     TEXT NOT NULL,
  room        TEXT NOT NULL,
  thread_ref  TEXT,
  text        TEXT NOT NULL,
  msg_ref     TEXT,
  outbox_id   TEXT UNIQUE,
  status      TEXT NOT NULL DEFAULT 'delivered',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mcp_notif_room_ts ON mcp_notification_messages (room, created_at);

CREATE TABLE IF NOT EXISTS mcp_casebase (
  doc_id           TEXT PRIMARY KEY,
  doc_type         TEXT NOT NULL,
  content          TEXT NOT NULL,
  metadata         JSONB NOT NULL DEFAULT '{}',
  idempotency_key  TEXT UNIQUE,
  version          INTEGER NOT NULL DEFAULT 1,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mcp_casebase_doc_type ON mcp_casebase (doc_type);

CREATE TABLE IF NOT EXISTS mcp_eval_runs (
  eval_id       TEXT PRIMARY KEY,
  workorder_id  TEXT NOT NULL,
  suite_digest  TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'queued',
  report        JSONB,
  report_hash   TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mcp_eval_workorder_id ON mcp_eval_runs (workorder_id);

CREATE TABLE IF NOT EXISTS mcp_workorders (
  workorder_id      TEXT PRIMARY KEY,
  case_id           TEXT NOT NULL,
  channel           TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'DRAFT',
  draft_payload     JSONB NOT NULL DEFAULT '{}',
  frozen_payload    JSONB,
  hash              TEXT,
  gate_report_ref   TEXT,
  gate_report_digest TEXT,
  created_by        TEXT NOT NULL DEFAULT 'agent',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mcp_workorders_case_id ON mcp_workorders (case_id);

CREATE TABLE IF NOT EXISTS mcp_suggestions (
  suggestion_id  TEXT PRIMARY KEY,
  case_id        TEXT NOT NULL,
  worker_id      TEXT NOT NULL,
  fencing_token  BIGINT,
  kind           TEXT NOT NULL,
  payload        JSONB NOT NULL DEFAULT '{}',
  evidence_refs  JSONB NOT NULL DEFAULT '[]',
  status         TEXT NOT NULL DEFAULT 'recorded',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mcp_suggestions_case_id ON mcp_suggestions (case_id);

CREATE TABLE IF NOT EXISTS mcp_approval_requests (
  approval_id     TEXT PRIMARY KEY,
  workorder_id    TEXT NOT NULL,
  workorder_hash  TEXT NOT NULL,
  nonce           TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  evidence_summary TEXT NOT NULL DEFAULT '',
  channel         TEXT NOT NULL DEFAULT 'feishu',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_mcp_appr_req_workorder_id ON mcp_approval_requests (workorder_id);

