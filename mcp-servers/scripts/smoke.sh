#!/usr/bin/env bash
# Deterministic projection smoke. This is an ACL/startup check, not B1 evidence.
set -uo pipefail

cd "$(dirname "$0")/.."
PY="${MCP_SMOKE_PYTHON:-$(pwd)/.venv/bin/python}"
PASS=0
FAIL=0
PIDS=()

ok() { printf 'PASS %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf 'FAIL %s\n' "$1"; FAIL=$((FAIL + 1)); }

cleanup() {
  set +u
  for pid in "${PIDS[@]}"; do
    [ -n "$pid" ] && kill "$pid" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

if [ ! -x "$PY" ]; then
  printf 'missing %s; create the documented Python 3.11 virtualenv first\n' "$PY"
  exit 2
fi

export DATABASE_URL="${DATABASE_URL:-sqlite:////tmp/agentmed-mcp-projection-smoke.db}"
"$PY" scripts/run_migrations.py >/tmp/agentmed-mcp-smoke-migrate.log 2>&1 || {
  fail "database migration"
  exit 1
}

wait_port() {
  local port="$1" attempts=0
  while [ "$attempts" -lt 900 ]; do
    if nc -z 127.0.0.1 "$port" >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 0.1
  done
  return 1
}

start_projection() {
  local name="$1" module="$2" profile="$3" port_var="$4" port="$5"
  local worker="$6" expected_json="$7" needs_role="$8" needs_gate="$9"
  local backend_token role_token gate_token consumer output actual expected code

  backend_token="$(openssl rand -hex 32)"
  consumer="worker-$profile"
  role_token=""
  gate_token=""
  [ "$needs_role" = "yes" ] && role_token="role-$name-smoke-token"
  [ "$needs_gate" = "yes" ] && gate_token="gate-$name-smoke-token"

  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    fail "$name port $port already occupied"
    return
  fi

  env \
    DATABASE_URL="$DATABASE_URL" \
    MCP_TOOL_PROFILE="$profile" \
    MCP_WORKER_ID="$worker" \
    MCP_EXPECTED_CONSUMER="$consumer" \
    MCP_GATEWAY_BACKEND_TOKEN="$backend_token" \
    CONTROL_PLANE_ROLE_TOKEN="$role_token" \
    GATE_AUTHORITY_TOKEN="$gate_token" \
    PYTHONPYCACHEPREFIX=/tmp/agentmed-mcp-smoke-pycache \
    "$port_var=$port" \
    "$PY" -m "servers.$module" >"/tmp/$name.log" 2>&1 &
  PIDS+=("$!")

  if ! wait_port "$port"; then
    fail "$name startup"
    return
  fi
  ok "$name startup"

  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$port/mcp" -d '{}')"
  if [ "$code" = "403" ]; then ok "$name rejects direct backend"; else fail "$name rejects direct backend"; fi

  code="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$port/mcp" \
    -H "X-AgentMED-Gateway-Token: $backend_token" \
    -H 'X-Mse-Consumer: worker-wrong' -d '{}')"
  if [ "$code" = "403" ]; then ok "$name rejects cross-consumer"; else fail "$name rejects cross-consumer"; fi

  output="$(MCP_GATEWAY_BACKEND_TOKEN="$backend_token" MCP_EXPECTED_CONSUMER="$consumer" \
    "$PY" scripts/mcp_client.py "$port" list 2>&1)"
  actual="$(printf '%s' "$output" | jq -c '.tools | sort' 2>/dev/null || true)"
  expected="$(printf '%s' "$expected_json" | jq -c 'sort')"
  if [ "$actual" = "$expected" ]; then
    ok "$name exact tool allowlist"
  else
    fail "$name exact tool allowlist expected=$expected actual=$actual"
  fi
}

start_projection mcp-agentmed-admin-quality-officer case_admin quality-officer CASE_ADMIN_PORT 8101 quality-officer \
  '["case.list","case.get","case.timeline","case.claim","case.submit_suggestion","case.escalate"]' yes no
start_projection mcp-agentmed-admin-collector case_admin collector CASE_ADMIN_PORT 8201 collector \
  '["case.get","app.logs","app.feedback"]' no no
start_projection mcp-agentmed-admin-case-officer case_admin case-officer CASE_ADMIN_PORT 8301 case-officer \
  '["case.get"]' no no
start_projection mcp-agentmed-admin-attributionist case_admin attributionist CASE_ADMIN_PORT 8401 eval-runner \
  '["case.get","case.claim","app.logs"]' yes no
start_projection mcp-agentmed-admin-repairer case_admin repairer CASE_ADMIN_PORT 8501 repairer \
  '["case.get","case.timeline","case.claim"]' yes no
start_projection mcp-agentmed-release-gatekeeper release_admin gatekeeper RELEASE_ADMIN_PORT 8102 gatekeeper \
  '["workorder.get","gate.submit","approval.request","approval.status","release.get"]' yes no
start_projection mcp-agentmed-release-repairer release_admin repairer RELEASE_ADMIN_PORT 8202 repairer \
  '["versionset.list","versionset.get","candidate.create","workorder.draft","workorder.freeze","workorder.get","release.get"]' yes no
start_projection mcp-agentmed-eval-gatekeeper eval_runner gatekeeper EVAL_RUNNER_PORT 8103 gatekeeper \
  '["gate.run","gate.run_verification","gate.report"]' yes yes
start_projection mcp-agentmed-eval-attributionist eval_runner attributionist EVAL_RUNNER_PORT 8203 eval-runner \
  '["versionset.list","versionset.get","experiment.plan","experiment.run","experiment.execute","experiment.report","probe.freeze"]' yes no
start_projection mcp-agentmed-notify-quality-officer notification quality-officer NOTIFICATION_PORT 8104 quality-officer \
  '["matrix.log"]' no no
start_projection mcp-agentmed-notify-case-officer notification case-officer NOTIFICATION_PORT 8204 case-officer \
  '["feishu.reply_origin","feishu.weekly_report","matrix.log"]' yes no
start_projection mcp-agentmed-casebase-knowledge casebase_knowledge case-officer CASEBASE_PORT 8005 case-officer \
  '["kb.search","kb.get","kb.upsert","kb.badcase_search","kb.holdout_get"]' no no

printf 'projection smoke: PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
