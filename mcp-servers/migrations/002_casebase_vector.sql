-- 案例库向量列（Phase 2 预留；plain postgres 无 pgvector 扩展时跳过，检索走全文+元数据）
-- 本文件由 run_migrations.py 单独容错执行。
CREATE EXTENSION IF NOT EXISTS vector;
ALTER TABLE mcp_casebase ADD COLUMN IF NOT EXISTS embedding vector(1024);
CREATE INDEX IF NOT EXISTS ix_mcp_casebase_embedding ON mcp_casebase USING ivfflat (embedding vector_cosine_ops);
