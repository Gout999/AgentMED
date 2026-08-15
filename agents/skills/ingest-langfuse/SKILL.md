---
name: ingest-langfuse
description: Collect Langfuse evidence for a complaint Case. Read scores (v3), observations and traces (v2) with read-only credentials, and map them to the case evidence gaps (trace.input/output, observations.model/tools).
assign_when: A Case has correlation_status NEEDS_CORRELATION or missing evidence fields referencing langfuse.
---

# ingest-langfuse（agentmed 版）

取证：从 Langfuse 拉观测补 Case 证据缺口。只读，不写 Langfuse。

## 凭证

`config/langfuse.env`（chmod 600）：LANGFUSE_BASE_URL / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY。
v2 观测用 Basic auth（pk:sk）；v3 scores 用 Basic auth。

## 端点（本机实测）

- `GET {base}/api/public/v3/scores?limit=N` → 负分列表（id/name/value/timestamp）；
- `GET {base}/api/public/v2/observations?limit=N` → 观测（id/traceId/type/name/input/output）；
- `GET {base}/api/public/v2/traces/{traceId}` → trace 详情。

## 流程

1. `case.get(case_id)`：读 evidence 缺口清单（missing_fields）；
2. 按 case 的 score_id / trace_id 线索拉 Langfuse 数据；
3. 将证据写入 `shared/tasks/{task-id}/evidence/`（原始 JSON + 摘要），消息里只传「路径 + 摘要 + digest」；
4. 缺口补齐情况交 `case.submit_suggestion(kind=...)` 或由 leader 汇入时间线；
5. 拉不到的数据显式标 `evidence_gap=true`，不伪造、不阻塞。

## 纪律

- 只读：绝不写 scores/observations；
- 凭证不入消息、不入产物；
- PII 脱敏与 control-plane 口径一致。
