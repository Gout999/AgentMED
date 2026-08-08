-- P0-1: real gate execution metadata. Idempotent for existing deployments.
ALTER TABLE mcp_eval_runs ADD COLUMN IF NOT EXISTS target_versionset_id TEXT NOT NULL DEFAULT '';
ALTER TABLE mcp_eval_runs ADD COLUMN IF NOT EXISTS target_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE mcp_eval_runs ADD COLUMN IF NOT EXISTS dataset_id TEXT NOT NULL DEFAULT '';
ALTER TABLE mcp_eval_runs ADD COLUMN IF NOT EXISTS dataset_version TEXT NOT NULL DEFAULT '';
ALTER TABLE mcp_eval_runs ADD COLUMN IF NOT EXISTS dataset_digest TEXT NOT NULL DEFAULT '';
ALTER TABLE mcp_eval_runs ADD COLUMN IF NOT EXISTS evidence_digest TEXT NOT NULL DEFAULT '';
ALTER TABLE mcp_eval_runs ADD COLUMN IF NOT EXISTS candidate_digest TEXT NOT NULL DEFAULT '';
