# mcp-servers OPEN-ISSUES（T4 施工遗留 / 待主控裁决）

## 一、按 D-001 已裁决的实现口径（无需再判，仅记录）

| # | 事项 | 现状 |
|---|------|------|
| 1 | 向量检索 | Phase 1 全文+元数据过滤，`kb.search` 返回 `degraded:"fulltext_only"`；pgvector 列已建（migration），Phase 2 启用（D-001 #12） |
| 2 | live provider E2E | MVP 无真实 LLM 通道，`gate.report` 中 `live_provider_e2e.status="skipped"`，不伪造结果（D-001 #3）；门禁放行由守门员/控制面按策略裁决 |
| 3 | ApprovalGrant proof | Q7 MVP：`server_recorded` + audit URI；HMAC/签名列为 Phase 3 硬化项 |
| 4 | SUSPENDED 冷却 | 24h；计数清零开新 epoch；必须人工确认才 reinstate（Q8，不自动恢复） |
| 5 | ApprovalGrant TTL | 30min（D-001 #10），grant 时强制，validate 时复查 |

## 二、实现取舍（施工判断，主控可复核）

| # | 取舍 | 理由 | 建议 |
|---|------|------|------|
| O1 | `workorder.freeze` 未提供 `base/target_versionset_digest` 时由 `input_versions`/`diff` 派生 | spec §9.4 draft 参数不含 digest；Release Controller 执行时以 Quality API 权威版本核对 | 如需更严格，draft 入参可要求显式 digest |
| O2 | `case.submit_suggestion` 建议事件落本地 `mcp_suggestions` + 审计，不直接迁移控制面状态 | spec §9.3「建议写入仅产生建议事件，不直接改状态」；控制面裁决后经自身 transitions 迁移 | 若需控制面直接记录建议，可在 control-plane 加建议端点（不在本 scope） |
| O3 | `approval.status` 读控制面 `approvals` 表（公共 schema，只读），无表时降级为本地 pending | 控制面无 `GET /v1/approvals/{id}` 端点 | 后续可给 control-plane 补只读端点 |
| O4 | `case.claim`/`case.escalate` 写操作 fencing token 透传，过期由控制面判定（`LEASE_LOST`） | 与 control-plane `lease_lost`/`lease_conflict` 错误映射一致 | — |
| O5 | 工具级 ACL 由 Higress 网关按消费者令牌执行；server 侧文档声明 + `kb.upsert` 本地 actor 校验 | 与 spec §9.2 三层鉴权第一层一致；第二/三层在网关与 ApprovalGrant | — |
| O6 | `gate.run` 同步完成确定性评测，返回 `queued` 句柄，`gate.report` 立即可取 | 满足「异步任务句柄」契约面；确定性评测瞬时完成 | 若需真异步，可接 worker 队列（Phase 2） |
| O7 | notification mock 用 PG/SQLite 群消息日志 + REST 查询端点 | spec §9.6「接口签名与真飞书一致」；真实凭证 Phase 1 前置（D-001 #14） | — |

## 三、风险与依赖

| # | 风险 | 缓解 |
|---|------|------|
| R1 | control-plane 未运行/未迁移时 case-admin/release-admin 上游调用返回 `DEPENDENCY_UNAVAILABLE`/读表降级 | `app.logs/feedback` 返回 `evidence_gap=true` 不阻塞；smoke 对 control-plane 做探测 |
| R2 | WorkOrder hash 的 JCS 子集不支持换行/非 ASCII 内联 diff（与 control-plane 一致） | 文档明示用 `content_ref`；`workorder.draft` 校验 diff 二选一 |
| R3 | smoke 会向 control-plane 库写入 case/workorder/approval 数据 | 幂等键 + 每次新建投诉；不影响主控验收（可重跑） |
| R4 | `weekly_report` 幂等键默认 `weekly:{week}`；同键二次调用返回 duplicate | 符合 §9.2 幂等语义；审批由控制面/Console 处理，不注册死 MCP 工具 |

## 四、尚未实现（按契约属 Phase 2 / 外部依赖）

- `experiment.run` 需 runner 领单（control-plane `start` 需 lease+fencing_token）；MVP 仅验证
  `experiment.plan`/`experiment.report`。
- `release.get` 按 case_id 需 control-plane `aggregates` 表存在；无发布时返回 `NOT_FOUND`。
- 向量嵌入与 ivfflat 索引（`mcp_casebase.embedding`）已建列，索引在 migration 中预留，检索未启用。
