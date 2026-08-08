#!/usr/bin/env bash
# CaseLoop mcp-servers smoke：起本地 5 个 MCP server，逐一 call 关键工具（spec §9）。
#
# 前置：
#   - .venv 已建好（python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt）
#   - 可选：Postgres(127.0.0.1:5432) + control-plane（未起则 case/release 相关工具标记 degraded）
# 用法：
#   CONTROL_PLANE_BASE_URL=http://127.0.0.1:8090 bash scripts/smoke.sh
set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
CLIENT_CMD=("$PY" scripts/mcp_client.py)

PASS=0
FAIL=0
PIDS=()

say()  { printf '\n\033[1;36m==== %s ====\033[0m\n' "$*"; }
ok()   { printf '\033[32m  PASS\033[0m  %s\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '\033[31m  FAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
degraded() { printf '\033[33m  SKIP\033[0m  %s（依赖未就绪）\n' "$*"; }

# 调 MCP 工具：call <port> <tool> <json> <label>
call() {
  local port="$1" tool="$2" args="$3" label="$4"
  local out
  out=$("${CLIENT_CMD[@]}" "$port" "$tool" "$args" 2>&1)
  if echo "$out" | grep -q '"ok": true'; then
    ok "$label"
    echo "      $out"
  else
    fail "$label"
    echo "      $out"
  fi
}

call_expect_error() {
  local port="$1" tool="$2" args="$3" label="$4"
  local out
  out=$("${CLIENT_CMD[@]}" "$port" "$tool" "$args" 2>&1)
  if echo "$out" | grep -q '"isError": true'; then
    ok "${label}（预期拒绝）"
    echo "      $out"
  else
    fail "$label"
    echo "      $out"
  fi
}

# ---------- 环境 ----------
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane}"
PG_UP=0
if nc -z 127.0.0.1 5432 >/dev/null 2>&1; then
  PG_UP=1
else
  say "Postgres 未就绪 → 使用 SQLite（/tmp/caseloop-mcp-smoke.db）"
  export DATABASE_URL="sqlite:////tmp/caseloop-mcp-smoke.db"
fi

# control-plane 探测
export CONTROL_PLANE_BASE_URL="${CONTROL_PLANE_BASE_URL:-}"
if [ -z "$CONTROL_PLANE_BASE_URL" ]; then
  for c in http://127.0.0.1:8090 http://127.0.0.1:18090; do
    if curl -sf -m 2 "$c/healthz" >/dev/null 2>&1; then
      export CONTROL_PLANE_BASE_URL="$c"
      break
    fi
  done
fi
if [ -n "$CONTROL_PLANE_BASE_URL" ]; then
  say "control-plane: $CONTROL_PLANE_BASE_URL"
else
  say "control-plane 未探测到 → case/release 相关工具按 degraded 报告"
fi

# ---------- 迁移 ----------
say "数据库迁移"
"$PY" scripts/run_migrations.py || echo "  (migration 警告：若为 SQLite 正常)"

# ---------- 准备 case ----------
CASE_ID=""
if [ -n "$CONTROL_PLANE_BASE_URL" ]; then
  say "通过 control-plane 立案（取真实 case_id）"
  RESP=$(curl -sf -m 5 -X POST "$CONTROL_PLANE_BASE_URL/v1/complaints" \
    -H "Content-Type: application/json" \
    -d '{"source":"webhook","text":"用户反馈：订单状态与售后政策不一致，客服 13912345678 请退款","channel":"feishu-mock:oc_demo:","complainant_ref":"feishu:ou_demo"}' 2>/dev/null) \
    || RESP=""
  CASE_ID=$(echo "$RESP" | jq -r '.case_id // empty' 2>/dev/null || true)
  if [ -n "$CASE_ID" ]; then ok "立案 case_id=$CASE_ID"; else fail "立案失败"; fi
fi

# ---------- 起 5 个 server ----------
say "启动 5 个 MCP server（:8001–8005）"
start_server() {
  local name="$1" port="$2"
  if lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    degraded "port $port 已被占用，跳过启动 $name"
    return
  fi
  "$PY" -m "servers.$name" >"/tmp/mcp-$name.log" 2>&1 &
  PIDS+=("$!")
  echo "  started servers.$name pid=$! port=$port"
}
start_server case_admin 8001
start_server release_admin 8002
start_server eval_runner 8003
start_server notification 8004
start_server casebase_knowledge 8005
sleep 3

# ---------- 1) mcp-case-admin ----------
say "mcp-case-admin（:8001）"
call 8001 case.list '{"limit":3}' "case.list 列案件"
if [ -n "$CASE_ID" ]; then
  call 8001 case.get "{\"case_id\":\"$CASE_ID\"}" "case.get 读案件"
  call 8001 case.claim "{\"worker_id\":\"collector-smoke\",\"case_id\":\"$CASE_ID\"}" "case.claim 领单（fencing token）"
  call 8001 case.submit_suggestion "{\"case_id\":\"$CASE_ID\",\"fencing_token\":1,\"kind\":\"triage\",\"payload\":{\"layer\":\"kb\"},\"evidence_refs\":[]}" "case.submit_suggestion 建议事件"
  call 8001 case.timeline "{\"case_id\":\"$CASE_ID\",\"limit\":5}" "case.timeline 案件时间线"
else
  degraded "case.get/claim/submit/timeline"
fi
call 8001 app.logs '{"app":"demo-app","limit":1}' "app.logs 取证（已脱敏）"
call 8001 app.feedback '{"app":"demo-app","limit":1}' "app.feedback 取证"

# ---------- 2) mcp-eval-runner ----------
say "mcp-eval-runner（:8003）"
WO_ID=""; EVAL_ID=""; REPORT_HASH=""; GATE_VERDICT=""
export QUALITY_API_BASE_URL="${QUALITY_API_BASE_URL:-http://127.0.0.1:8080}"
QUALITY_READ_TOKEN="${QUALITY_READ_TOKEN:-conformance-read-token}"
TARGET_JSON=$(curl -sf -m 5 "$QUALITY_API_BASE_URL/v2/versionsets?status=active&limit=1" \
  -H "Authorization: Bearer $QUALITY_READ_TOKEN" 2>/dev/null | jq -c '.items[0] // empty' 2>/dev/null || true)

if [ -n "$CASE_ID" ] && [ -n "$CONTROL_PLANE_BASE_URL" ] && [ -n "$TARGET_JSON" ]; then
  TARGET_ID=$(echo "$TARGET_JSON" | jq -r '.versionset_id')
  TARGET_REVISION=$(echo "$TARGET_JSON" | jq -r '.revision')
  TARGET_DIGEST=$(echo "$TARGET_JSON" | jq -r '.digest')
  PROMPT_DIGEST=$(echo "$TARGET_JSON" | jq -r '.content.prompt.digest')
  KB_DIGEST=$(echo "$TARGET_JSON" | jq -r '.content.kb_manifest.manifest_digest')
  MODEL_DIGEST=$(echo "$TARGET_JSON" | jq -r '.content.model.digest')
  DIFF_CONTENT="smoke gate against exact active target"
  DIFF_DIGEST="sha256:$(printf '%s' "$DIFF_CONTENT" | shasum -a 256 | awk '{print $1}')"
  DRAFT_ARGS=$(jq -nc \
    --arg case_id "$CASE_ID" --arg target_id "$TARGET_ID" --argjson target_revision "$TARGET_REVISION" \
    --arg target_digest "$TARGET_DIGEST" --arg prompt_digest "$PROMPT_DIGEST" \
    --arg kb_digest "$KB_DIGEST" --arg model_digest "$MODEL_DIGEST" \
    --arg diff_content "$DIFF_CONTENT" --arg diff_digest "$DIFF_DIGEST" \
    '{case_id:$case_id,target:{app:"xiaozhi-cs",layer:"prompt"},
      input_versions:{prompt_digest:$prompt_digest,kb_manifest_digest:$kb_digest,model_digest:$model_digest},
      diff:{format:"unified_diff",content:$diff_content,digest:$diff_digest},
      single_factor_declaration:"single layer prompt only",
      base_versionset_digest:$target_digest,target_versionset_digest:$target_digest,
      target_versionset_id:$target_id,target_revision:$target_revision}')
  DRAFT=$("${CLIENT_CMD[@]}" 8002 workorder.draft "$DRAFT_ARGS" 2>&1)
  if echo "$DRAFT" | grep -q '"ok": true'; then
    ok "workorder.draft 以真实 VersionSet 起草"
    WO_ID=$(echo "$DRAFT" | jq -r '.workorder_id')
    GRUN=$("${CLIENT_CMD[@]}" 8003 gate.run "{\"workorder_id\":\"$WO_ID\"}" 2>&1)
    if echo "$GRUN" | grep -q '"ok": true'; then
      ok "gate.run 完成真实 contract/replay/live 评测"
      EVAL_ID=$(echo "$GRUN" | jq -r '.eval_id')
      REPORT_HASH=$(echo "$GRUN" | jq -r '.report_hash')
      GATE_VERDICT=$(echo "$GRUN" | jq -r '.verdict')
      call 8003 gate.report "{\"eval_id\":\"$EVAL_ID\"}" "gate.report 双轨报告"
    else
      fail "gate.run"; echo "      $GRUN"
    fi
  else
    fail "workorder.draft"; echo "      $DRAFT"
  fi
else
  degraded "真实 gate（需 control-plane、active VersionSet 与 case）"
fi
if [ -n "$CONTROL_PLANE_BASE_URL" ]; then
  call 8003 experiment.plan "{\"case_id\":\"$CASE_ID\",\"matrix\":\"5cell\"}" "experiment.plan 实验计划"
else
  degraded "experiment.plan"
fi

# ---------- 3) mcp-release-admin ----------
say "mcp-release-admin（:8002）"
if [ -n "$WO_ID" ] && [ -n "$REPORT_HASH" ]; then
  if [ "$GATE_VERDICT" = "passed" ]; then
    call 8002 gate.submit "{\"workorder_id\":\"$WO_ID\",\"eval_id\":\"$EVAL_ID\",\"report_hash\":\"$REPORT_HASH\"}" "gate.submit 门禁报告提交"
    call 8002 workorder.freeze "{\"workorder_id\":\"$WO_ID\",\"fencing_token\":1}" "workorder.freeze 定稿（hash 绑定）"
    APPR=$("${CLIENT_CMD[@]}" 8002 approval.request "{\"workorder_id\":\"$WO_ID\",\"evidence_summary\":\"gate passed\",\"channel\":\"feishu\"}" 2>&1)
    if echo "$APPR" | grep -q '"ok": true'; then
      ok "approval.request 提请审批"
      APPR_ID=$(echo "$APPR" | jq -r '.approval_id')
      call 8002 approval.status "{\"approval_id\":\"$APPR_ID\"}" "approval.status 审批状态"
    else
      fail "approval.request"; echo "      $APPR"
    fi
    call 8002 workorder.get "{\"workorder_id\":\"$WO_ID\"}" "workorder.get 读工单"
  else
    call_expect_error 8002 gate.submit "{\"workorder_id\":\"$WO_ID\",\"eval_id\":\"$EVAL_ID\",\"report_hash\":\"$REPORT_HASH\"}" "gate.submit 对 $GATE_VERDICT 门禁 fail-closed"
    degraded "workorder.freeze/approval（live provider 门禁未通过）"
  fi
else
  degraded "workorder 全流程（需 control-plane + gate）"
fi
call 8002 release.request_canary '{"release_id":"rel_smoke","percent":5}' "release.request_canary（R2 需逐次审批）"
call 8002 release.request_rollback '{"release_id":"rel_smoke","reason":"demo"}' "release.request_rollback（R2 需逐次审批）"

# ---------- 4) mcp-notification（feishu-mock） ----------
say "mcp-notification（:8004）"
NOTIF=$("${CLIENT_CMD[@]}" 8004 feishu.reply_origin '{"case_id":"case_smoke_notif","text":"已修复，请验证","refs":["feishu-mock:oc_smoke:fm_abc"]}' 2>&1)
if echo "$NOTIF" | grep -q '"ok": true'; then
  ok "feishu.reply_origin 原群回复（thread_ref=feishu-mock:<room>:<msg_ref>）"
  echo "      $NOTIF"
else
  fail "feishu.reply_origin"; echo "      $NOTIF"
fi
call 8004 feishu.approval_card "{\"approval_id\":\"appr_smoke\",\"workorder_hash\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"evidence_summary\":\"gate passed\",\"expiry\":\"2026-08-07T23:00:00+00:00\"}" "feishu.approval_card 审批卡片"
call 8004 feishu.weekly_report '{"report":{"week":"2026-W32","total_cases":3,"attributed":1,"fixed":1}}' "feishu.weekly_report 质量周报"
call 8004 matrix.log '{"room":"internal","text":"smoke 留痕"}' "matrix.log 对内留痕"
MSG=$(curl -sf -m 3 "http://127.0.0.1:8004/api/messages?channel=feishu-mock&limit=1" 2>/dev/null)
if [ -n "$MSG" ] && echo "$MSG" | grep -q '"items"'; then
  ok "REST GET /api/messages（mock 群消息日志）"
else
  fail "REST GET /api/messages"
fi

# ---------- 5) mcp-casebase-knowledge ----------
say "mcp-casebase-knowledge（:8005）"
call 8005 kb.upsert '{"doc_type":"case","content":"用户投诉订单状态与售后政策不一致 badcase","metadata":{"app":"xiaozhi-cs","fault_layer":"kb"},"idempotency_key":"smoke-kb-1","actor":"case-officer"}' "kb.upsert 案例入库（幂等键）"
call 8005 kb.upsert '{"doc_type":"probe_pack","content":"[probe] 订单状态 售后政策","metadata":{"app":"xiaozhi-cs","kind":"holdout","name":"holdout-v1","probe_set_digest":"sha256:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},"idempotency_key":"smoke-holdout-1","actor":"case-officer"}' "kb.upsert holdout 回放集"
call 8005 kb.search '{"query":"订单 售后","top_k":5}' "kb.search 全文检索（degraded=fulltext_only）"
call 8005 kb.badcase_search '{"query":"投诉 不一致","top_k":3}' "kb.badcase_search 相似 badcase"
call 8005 kb.holdout_get '{"holdout_name":"holdout-v1"}' "kb.holdout_get holdout 查询"
call_expect_error 8005 kb.upsert '{"doc_type":"case","content":"x","metadata":{},"actor":"repairer"}' "kb.upsert 非案例官 ACL 拒绝"

# ---------- trust-ledger 模块演示 ----------
say "trust-ledger 模块（库，非独立 server）"
"$PY" scripts/trust_demo.py && ok "trust-ledger MVP：3/3 拒绝晋升 + R2 逐次审批 + SUSPENDED 冷却" || fail "trust-ledger MVP 演示"

# ---------- 清理 ----------
say "清理进程"
if [ "${#PIDS[@]}" -gt 0 ]; then
  for pid in "${PIDS[@]}"; do kill "$pid" >/dev/null 2>&1 || true; done
fi

say "smoke 汇总：PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] && echo "SMOKE_OK" || echo "SMOKE_HAS_FAILURES"
exit "$FAIL"
