-- CaseLoop 三逻辑库初始化（T2 骨架；T1 可追加 demo-app 表）
-- 容器入口：/docker-entrypoint-initdb.d/

CREATE DATABASE control_plane;
CREATE DATABASE control_plane_test;  -- integration 测试专用 scratch 库（S0-005：禁指活库）
CREATE DATABASE demo_app;
CREATE DATABASE casebase;

\c casebase
-- CREATE EXTENSION IF NOT EXISTS vector;  -- Phase 1 未启用向量检索；plain postgres 镜像不含该扩展

-- T1 demo-app 库：pgvector 扩展（Phase 1 预留向量列，检索暂用全文+元数据过滤）
\c demo_app
-- CREATE EXTENSION IF NOT EXISTS vector;  -- Phase 1 未启用向量检索；plain postgres 镜像不含该扩展
