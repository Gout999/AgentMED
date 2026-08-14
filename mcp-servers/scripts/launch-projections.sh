#!/usr/bin/env bash
# 启动 12 个角色投影（flow-first 第 1 步 MCP 工具面）。
# 端口/worker/角色与 scripts/smoke.sh 的投影表一致；凭证来自 mcp-servers/.env。
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a
PY="${MCP_SMOKE_PYTHON:-$(pwd)/.venv/bin/python}"
mkdir -p var/logs

start_p() {
  local name="$1" module="$2" profile="$3" port_var="$4" port="$5" worker="$6" role_tok="$7" gate_tok="$8"
  local rt="" gt=""
  if [ "$role_tok" = "yes" ]; then
    rt=$(eval echo "\$ROLE_TOKEN_$(echo "$profile" | tr 'a-z-' 'A-Z_')")
    [ -n "$rt" ] || { echo "missing ROLE_TOKEN for $profile"; exit 1; }
  fi
  if [ "$gate_tok" = "yes" ]; then
    gt=$(grep -E '^GATE_AUTHORITY_TOKEN=' "$(cd .. && pwd)/deploy/.env" | cut -d= -f2-)
  fi
  local bt
  mkdir -p var/tokens
  if [ -f "var/tokens/$name.token" ]; then
    bt="$(cat "var/tokens/$name.token")"
  else
    bt="$(openssl rand -hex 32)"
    printf '%s' "$bt" > "var/tokens/$name.token"
  fi
  env \
    MCP_GATEWAY_BACKEND_TOKEN="$bt" \
    MCP_HOST=127.0.0.1 \
    MCP_TRUST_GATEWAY_CONSUMER=true \
    DATABASE_URL="$DATABASE_URL" \
    CONTROL_PLANE_BASE_URL="$CONTROL_PLANE_BASE_URL" \
    QUALITY_API_BASE_URL="$QUALITY_API_BASE_URL" \
    QUALITY_READ_TOKEN="$QUALITY_READ_TOKEN" \
    CASELOOP_QUALITY_API_TIMEOUT_SECONDS="$CASELOOP_QUALITY_API_TIMEOUT_SECONDS" \
    STEPFUN_API_KEY="$STEPFUN_API_KEY" \
    JUDGE_MODEL="$JUDGE_MODEL" \
    GATE_EVALUATION_TIMEOUT_SECONDS="$GATE_EVALUATION_TIMEOUT_SECONDS" \
    MCP_TOOL_PROFILE="$profile" \
    MCP_WORKER_ID="$worker" \
    MCP_EXPECTED_CONSUMER="worker-$profile" \
    CONTROL_PLANE_ROLE_TOKEN="$rt" \
    GATE_AUTHORITY_TOKEN="$gt" \
    "$port_var=$port" \
    nohup "$PY" -m "servers.$module" > "var/logs/$name.log" 2>&1 &
  echo "started $name :$port (profile=$profile worker=$worker)"
}

# 依次启动（按 smoke.sh 投影表）
start_p mcp-case-admin-quality-officer case_admin quality-officer CASE_ADMIN_PORT 8101 quality-officer yes no
start_p mcp-case-admin-collector case_admin collector CASE_ADMIN_PORT 8201 collector no no
start_p mcp-case-admin-case-officer case_admin case-officer CASE_ADMIN_PORT 8301 case-officer no no
start_p mcp-case-admin-attributionist case_admin attributionist CASE_ADMIN_PORT 8401 eval-runner yes no
start_p mcp-case-admin-repairer case_admin repairer CASE_ADMIN_PORT 8501 repairer yes no
start_p mcp-release-admin-gatekeeper release_admin gatekeeper RELEASE_ADMIN_PORT 8102 gatekeeper yes no
start_p mcp-release-admin-repairer release_admin repairer RELEASE_ADMIN_PORT 8202 repairer yes no
start_p mcp-eval-runner-gatekeeper eval_runner gatekeeper EVAL_RUNNER_PORT 8103 gatekeeper yes yes
start_p mcp-eval-runner-attributionist eval_runner attributionist EVAL_RUNNER_PORT 8203 eval-runner yes no
start_p mcp-notification-quality-officer notification quality-officer NOTIFICATION_PORT 8104 quality-officer no no
start_p mcp-notification-case-officer notification case-officer NOTIFICATION_PORT 8204 case-officer yes no
start_p mcp-casebase-knowledge casebase_knowledge case-officer CASEBASE_PORT 8005 case-officer no no
echo "12 projections launched"
