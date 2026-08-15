---
name: langfuse-inspect
description: Use when a task requires Langfuse trace or score evidence, when verifying whether a model call or agent run was traced, when collecting supplementary evidence with trace IDs and deep-links, or when the coordinator explicitly asks to inspect Langfuse data. Read-only inspection only.
---

# Langfuse Inspect

对 Agent Station 项目（project `goai-agent-station`）的自托管 Langfuse 实例做只读
取证查询。本技能只授权查询，不产生任何写操作，也不把 Langfuse 当作权威状态源——
trace/score 只是证据素材，结论仍以任务协议与控制面状态为准。

## 凭证与端点

凭证在环境文件中，先加载再查询，任何情况下不得在消息或产物中输出 key：

```bash
set -a; . /root/.copaw-worker/$AGENTTEAMS_WORKER_NAME/.copaw/workspaces/default/config/langfuse.env; set +a
```

- 基址：`LANGFUSE_BASE_URL`（容器内应为 `http://host.docker.internal:3001`）
- 认证：`curl -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY"`（Basic auth）
- Web UI 深链：`http://localhost:3001/trace/{traceId}`

## 可用端点（Langfuse v4 events_only）

1. 观测列表（唯一可用的 trace/span 读取端点）：

```bash
curl -s -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "$LANGFUSE_BASE_URL/api/public/v2/observations?fromStartTime=<UTC开始>&toStartTime=<UTC结束>&limit=50&page=1"
```

   常用过滤参数：`type`（GENERATION/SPAN/EVENT/AGENT）、`name`、`traceId`、
   `environment`、`userId`、`sessionId`。按 `traceId` 过滤可聚合同一链路全部 span。

2. 评分列表：

```bash
curl -s -u "$LANGFUSE_PUBLIC_KEY:$LANGFUSE_SECRET_KEY" \
  "$LANGFUSE_BASE_URL/api/public/v3/scores?fromTimestamp=<UTC开始>&toTimestamp=<UTC结束>&limit=50&page=1"
```

   可选过滤：`name`、`source`、`type`、`dataType`。

注意：v1 的 `/api/public/traces`、`/api/public/scores`、`/api/public/v2/traces`、
`/api/public/v2/projects` 在本实例不可用（返回 HTML 或弃用提示），不要使用。

## 查询纪律

- 时间范围一律用 UTC ISO-8601（`2026-08-14T00:00:00Z` 形式），并记录在证据里。
- 空结果只表示「该时间窗/过滤条件内没有数据」，**不得**表述为全局没有事件；
  需要时扩大时间窗或放宽过滤后复查。
- HTTP 429/5xx 指数退避重试（1s/2s/4s…），连续失败 3 次后如实报 BLOCKED，
  不伪造数据。
- 输出证据摘要必须含：traceId、observation id、时间、type/name、关键字段（如
  latency、input/output 摘要）与 deep-link；结果仅作为补充证据引用，不替代
  taskflow/projectflow 的权威状态。

## 禁止事项

- 禁止使用 secret key 创建 trace/score/generation 或调用任何写端点（本技能只有只读用途）。
- 禁止打印凭证、把凭证写进任务产物或 Matrix 消息。
- 禁止把 trace 内容当作验收结论；验收仍以任务协议的确定性判定为准。