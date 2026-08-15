#!/usr/bin/env bash
# 注册 12 个 agentmed MCP 投影到 Higress 网关（平台原生 mcp-proxy 机制）。
# 控制台: http://127.0.0.1:18001（会话登录 /session/login）。
set -euo pipefail
cd "$(dirname "$0")/.."
CONSOLE="http://127.0.0.1:18001"
COOKIE="/tmp/higress-cookie"

# 登录（复用已有会话则跳过）
if ! curl -s -o /dev/null -w '%{http_code}' "$CONSOLE/v1/service-sources" -b "$COOKIE" | grep -q 200; then
  curl -s -c "$COOKIE" -X POST "$CONSOLE/session/login" -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"AgentMEDAdmin2026"}' > /dev/null
  echo "logged in"
fi

register() {
  local name="$1" port="$2"; shift 2
  local consumers=("$@")
  local token
  token="$(cat "var/tokens/$name.token")"

  # 1) DNS service source
  curl -s -X POST "$CONSOLE/v1/service-sources" -b "$COOKIE" -H 'Content-Type: application/json' \
    -d "{\"type\":\"dns\",\"name\":\"$name-proxy\",\"domain\":\"host.docker.internal\",\"port\":$port,\"protocol\":\"http\"}" > /dev/null

  # 2) mcpServer（mcp-proxy + 后端令牌 header）
  local yaml
  yaml=$(cat <<YEOF
server:
  name: $name
  config:
    type: mcp-proxy
    transport: http
    mcpServerURL: http://host.docker.internal:$port/mcp
    timeout: 120
  securitySchemes:
  - id: UpstreamAuth0
    type: apiKey
    in: header
    name: X-AgentMED-Gateway-Token
    defaultCredential: \"$token\"
  defaultUpstreamSecurity:
    id: UpstreamAuth0
YEOF
)
  local raw body
  raw=$(printf '%s' "$yaml" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')
  local consumers_json="[\"manager\""
  for c in "${consumers[@]}"; do consumers_json="$consumers_json,\"$c\""; done
  consumers_json="$consumers_json]"
  body=$(python3 -c "
import json, sys
name = sys.argv[1]; raw = sys.argv[2]; consumers = json.loads(sys.argv[3])

print(json.dumps({

  'name': name, 'description': name + ' MCP Proxy Server (http)',

  'type': 'OPEN_API', 'rawConfigurations': raw, 'mcpServerName': name,

  'domains': ['aigw-local.agentteams.io'],

  'services': [{'name': name + '-proxy.dns', 'port': $port, 'weight': 100}],

  'consumerAuthInfo': {'type': 'key-auth', 'enable': True, 'allowedConsumers': consumers},

}))" "$name" "$raw" "$consumers_json")
  curl -s -X PUT "$CONSOLE/v1/mcpServer" -b "$COOKIE" -H 'Content-Type: application/json' -d "$body" | head -c 200
  echo " <- $name"
}

register mcp-agentmed-admin-quality-officer 8101 worker-quality-officer
register mcp-agentmed-admin-collector 8201 worker-collector
register mcp-agentmed-admin-case-officer 8301 worker-case-officer
register mcp-agentmed-admin-attributionist 8401 worker-attributionist
register mcp-agentmed-admin-repairer 8501 worker-repairer
register mcp-agentmed-release-gatekeeper 8102 worker-gatekeeper
register mcp-agentmed-release-repairer 8202 worker-repairer
register mcp-agentmed-eval-gatekeeper 8103 worker-gatekeeper
register mcp-agentmed-eval-attributionist 8203 worker-attributionist
register mcp-agentmed-notify-quality-officer 8104 worker-quality-officer
register mcp-agentmed-notify-case-officer 8204 worker-case-officer
register mcp-agentmed-casebase-knowledge 8005 worker-case-officer
echo "12 servers registered"
