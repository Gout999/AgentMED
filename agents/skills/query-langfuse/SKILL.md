---
name: query-langfuse
description: Query one specific Langfuse trace or observation by id for evidence or verification. Read-only.
assign_when: A task needs one exact trace/observation detail by id (deep-link or content verification).
---

# query-langfuse（agentmed 版）

按 id 精确查一条 trace/observation，用于证据核验。

- `GET {base}/api/public/v2/traces/{traceId}`
- `GET {base}/api/public/v2/observations/{observationId}`

凭证同 ingest-langfuse（config/langfuse.env，Basic auth）。只读；结果摘要进 shared/tasks 交付物。
