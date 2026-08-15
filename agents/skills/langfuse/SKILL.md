---
name: langfuse
description: Langfuse credential and endpoint quick reference for agentmed evidence work. Read-only.
assign_when: Any task needs Langfuse API access details or credential handling rules.
---

# langfuse（agentmed 版）

凭证在 `config/langfuse.env`（chmod 600）：LANGFUSE_BASE_URL / LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY。
HTTP Basic auth（pk:sk）。只读纪律：不写 scores/observations，凭证不入聊天、不入产物。

- v2 观测：`GET {base}/api/public/v2/observations?limit=N`、`GET .../traces/{traceId}`、`GET .../observations/{id}`
- v3 评分：`GET {base}/api/public/v3/scores?limit=N`

具体取证流程见 `ingest-langfuse`，精确单查见 `query-langfuse`。
