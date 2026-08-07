# OPEN-QUESTIONS —— 契约待定项汇总

Phase 0B 冻结时 plan-v3 未细化、契约中按合理默认处理并标注 `# 【待定】` 的歧义点。
每项给出：当前契约默认、所在文件、建议决策时点。决策后应更新对应文件并移除标注。

| # | 待定项 | 当前契约默认 | 位置 | 建议决策时点 |
|---|--------|--------------|------|--------------|
| Q1 | operation 记录 TTL（410 operation_expired 的时限） | 默认 24h | `quality-api/openapi.yaml`（getOperation、Operation.expires_at） | Phase 1 Release Controller 实现前 |
| Q2 | GET /logs、/feedback 的分页与过滤字段 | cursor 分页 + 时间窗 + versionset_id/rating 过滤 | `quality-api/openapi.yaml`（/v2/logs、/v2/feedback） | Phase 1 demo-app 实现前 |
| Q3 | 灰度验证判据（canary verification：探针回放+阈值、最短观察窗） | 结构预留 {probe_set_digest, min_observation_minutes}，判据未定义 | `quality-api/openapi.yaml`（CanaryRequest.verification） | Phase 1 灰度演示前 |
| Q4 | poll 来源无稳定 external_id 时 inbox 去重键的内容归一化规则（大小写/空白/PII 脱敏先后序） | dedup_key=sha256(source\|external_id)；归一化规则未定 | `events/events.yaml`（ingestion.dedup） | Phase 1 poll 兜底实现前 |
| Q5 | 通知「回复投诉原处」的通道定位字段（飞书 chat_id + thread/root_id 具体映射） | thread_ref 不透明字符串 | `events/events.yaml`（notification.queued） | Phase 1 mcp-feishu 三能力实现前 |
| Q6 | Δ效应量 95%CI 的计算方法（差值 Wilson / Newcombe / 其他） | method 字段如实记录；样例用 wilson_score_bounds_conservative_diff | `schemas/evidence-bundle.schema.json`、`schemas/attribution-report.schema.json` | Phase 1 归因实验执行器实现前 |
| Q7 | ApprovalGrant 的证明机制（server_recorded / HMAC / 非对称签名） | 仅要求可回溯引用（audit URI） | `schemas/approval.schema.json`（proof） | Phase 1 审批链路实现前；mcp-servers 重写 common/approval.py 时一并定 |
| Q8 | evidence epoch 的滚动边界细节（冷却时长、SUSPENDED 解除后是否人工确认才 reinstate） | 冷却结束 → 新 epoch 计数清零；冷却时长未定 | `events/state-machines.yaml`（trust 机） | Phase 1 trust-ledger 模块实现前 |

## 已按 plan-v3 定死、不属于待定的事项（防重新打开）

- 样本口径：一次动作=一个样本，原始整数计数，不平滑不衰减（T8）。
- Wilson：**双侧** 95%，z=1.96；晋升判据下界>0.9；3/3 拒绝晋升。
- 裁决三态 ATTRIBUTED/INCONCLUSIVE/CONFOUNDED，废除「置信≥0.8」。
- 写面仅 Release Controller（quality:write 只签发给它）。
- 门禁双轨 + 裁判模型≠运动员模型 + contract/replay 与 live E2E 分开报告。
- 审批绑定 workorder_hash + nonce + expiry；nonce 一次性消费。
