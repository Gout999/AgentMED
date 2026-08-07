---
name: spike-echo
description: Phase 0A 验证用的最小自定义 Skill——确认自定义技能挂载链路可用。当被问到 "echo" 或被要求验证 spike-echo 时使用。
---

# spike-echo

一个最简自定义 Skill，用于验证「自定义 Skill 推送到 Worker MinIO workspace 后可被发现并调用」。

## 何时使用

- 消息中出现 `spike-echo`、`echo 验证`、`验证技能挂载` 时。

## 做什么

1. 回复一段固定格式的确认文本：`SPIKE-ECHO OK | worker=<自己的 Worker 名> | ts=<当前时间>`。
2. 如果任务里带了 payload，原样回显在第二行：`payload=<原文>`。

## 边界

- 不做任何写操作、不调用外部服务。
- 只用于 spike 验证，不承载业务逻辑。
