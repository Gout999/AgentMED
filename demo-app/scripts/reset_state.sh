#!/usr/bin/env bash
# reset_state.sh —— 一键清测试残留 + 恢复基线 VersionSet active（幂等，可重复跑）
#
# 背景：conformance / integration 测试会创建并 promote 自己的 VersionSet，
#   把基线 vs_baseline0000000001 顶成 superseded、active 变成 v-test-* 残留；
#   且测试版本的 model 可能是 step-2-16k 等不可用模型，导致 /chat 全挂（provider_error）。
# 本脚本：清掉全部非基线残留（versionsets/transitions/operations/idempotency/
#   chat_logs/feedback/fault_state），恢复基线 active，并验证输出。
#
# 用法：bash demo-app/scripts/reset_state.sh
#   前提：postgres 容器 caseloop-postgres 正在运行（compose up -d postgres）。
#   可选：PSQL="psql ..." 环境变量覆盖 psql 调用（默认 docker exec 进容器）。
set -euo pipefail

PSQL_CMD="${PSQL:-docker exec -i caseloop-postgres psql -U caseloop -d demo_app -v ON_ERROR_STOP=1 -q}"

echo "==> 1. 检查基线 vs_baseline0000000001 是否存在"
BASE_COUNT="$(echo "SELECT count(*) FROM versionsets WHERE versionset_id='vs_baseline0000000001';" | $PSQL_CMD -t -A)"
if [ "$BASE_COUNT" != "1" ]; then
  echo "!! 基线 vs_baseline0000000001 不存在（数据库可能被整库重置过）。" >&2
  echo "!! 请先启动/重启 demo-app 容器，其启动 init_app 会自动重建表/种子/基线：" >&2
  echo "!!   docker compose -f deploy/compose.yaml up -d demo-app" >&2
  exit 1
fi

echo "==> 2. 清除测试残留（非基线 versionsets 及其关联行 + 故障注入）"
$PSQL_CMD -c "
BEGIN;
DELETE FROM transitions      WHERE versionset_id <> 'vs_baseline0000000001';
DELETE FROM operations       WHERE versionset_id <> 'vs_baseline0000000001';
DELETE FROM idempotency      WHERE (resource_type='versionset' AND resource_id <> 'vs_baseline0000000001')
                              OR (resource_type='operation' AND resource_id NOT IN (SELECT operation_id FROM operations));
DELETE FROM chat_logs        WHERE versionset_id IS NOT NULL AND versionset_id <> 'vs_baseline0000000001';
DELETE FROM feedback         WHERE versionset_id IS NOT NULL AND versionset_id <> 'vs_baseline0000000001';
DELETE FROM versionsets      WHERE versionset_id <> 'vs_baseline0000000001';
DELETE FROM fault_state;
COMMIT;"

echo "==> 3. 恢复基线 active"
$PSQL_CMD -c "
BEGIN;
UPDATE versionsets
SET status='active',
    canary_percent=100,
    canary_started_at=NULL,
    updated_at=now()
WHERE versionset_id='vs_baseline0000000001';
COMMIT;"

echo "==> 4. 验证：active 应为基线（prompt v1.4.2 / model step-3.7-flash）"
echo "SELECT versionset_id, status, content->'prompt'->>'version' AS prompt_ver, content->'model'->>'model' AS model FROM versionsets ORDER BY status, versionset_id;" | $PSQL_CMD

echo "reset_state.sh 完成 ✓"
