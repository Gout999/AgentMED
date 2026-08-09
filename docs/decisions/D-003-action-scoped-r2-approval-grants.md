# D-003: R2 发布动作使用逐次、动作域 ApprovalGrant

- 状态：Accepted
- 日期：2026-08-08
- 主控批准：已于 PR #1 验收复核 ratified（2026-08-09）

## 背景

`docs/plan-v3.md` 与 Trust 状态机要求 `R2_HIGH_IMPACT` 永远逐次人工审批；原
`approval.schema.json` 只描述一次 WorkOrder nonce，无法同时表达 canary、promote、
rollback 各自的目标 revision 与参数。复用首次 grant 会令一次授权无限覆盖后续动作，
而为每个动作复制 WorkOrder nonce 又会破坏 nonce 唯一消费规则。

## 决定

保留无 `authorization` 的 ApprovalGrant 作为首次 release 创建授权，其 nonce 必须等于
WorkOrder nonce。为 canary、promote、rollback 扩展同一契约的可选 `authorization`：

- 每个动作使用独立 UUID nonce；
- 绑定 `action`、`release_id`、`target_revision`、规范化 `params` 与 `params_digest`；
- promote 的 `params` 必须额外绑定 WorkOrder 的 `base_versionset_digest` 为
  `expected_active_digest`；Quality API 在全局 active-set 锁内核对，避免两个不同候选
  虽被串行执行却先后掉包同一基线；
- 每次 Quality API 写入前由 Release Controller 行锁消费该 grant；
- ControllerOperation 持久化 `approval_id`，UNKNOWN reconcile 只接受当初由同一 operation
  消费且完整绑定未漂移的 grant；
- stage 作为首次授权覆盖的内部可逆准备动作，不新增 R2 grant，但仍逐次复核 WorkOrder、
  初始 grant、GateReport 与 TTL。

## 后果

旧的首次 ApprovalGrant 样例继续通过契约；新增 action-grant 样例和 conformance test。
调用 canary、promote、rollback 的控制面 API 必须提交 `approval_id`。相同 grant 不可驱动
第二个 operation；同一 Idempotency-Key 的同操作重放则返回原 operation，不重复消费。
并发 promote 中只有仍以获批基线为 active 的第一个操作可成功；其余操作以
`revision_conflict` 失败且候选保持 canary。
