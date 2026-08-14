-- CaseLoop 逻辑库初始化（清理 A1：demo-app 库随被治理应用迁移 AgentMED 退役）
-- 容器入口：/docker-entrypoint-initdb.d/

CREATE DATABASE control_plane;
CREATE DATABASE control_plane_test;  -- integration 测试专用 scratch 库（S0-005：禁指活库）
CREATE DATABASE casebase;

\c casebase
-- CREATE EXTENSION IF NOT EXISTS vector;  -- Phase 1 未启用向量检索；plain postgres 镜像不含该扩展

