# D-002: GateReport 与最终 WorkOrder 的无环绑定

- 状态：Accepted
- 日期：2026-08-08
- 范围：P0-1 评测门禁与发布控制

## 冲突

`GateReport` 必须先完成，随后其 digest 才能写入最终 `WorkOrder`；而最终
`WorkOrder.hash` 又包含该 GateReport digest。若要求 GateReport JSON 同时包含最终
`WorkOrder.hash`，会形成无法计算的 hash 循环。现有 contracts 没有定义这个反向字段。

## 决策

控制面先把 GateReport 内容、候选 VersionSet id/digest/revision、评测集版本和 evidence
manifest 持久化并分别计算 `report_hash`、`candidate_digest`、`evidence_digest`。登记最终
WorkOrder 时，在同一数据库事务内计算 `binding_digest`，覆盖上述 digest、最终
`workorder_hash` 与 target revision。

审批、ChangeSet 和每次 Quality API 写操作都重新验证该 binding；任何缺失、漂移、
未知状态或 hash 不一致均拒绝执行。post-canary 验证使用另一份 GateReport，并绑定当时
实际远端 revision，不能覆盖审批前 GateReport。

## 后果

- 不修改 GateReport JSON 后仍能把 ApprovalGrant、WorkOrder、evidence 和 target revision
  绑定在一个可重算的权威记录中。
- GateReport 注册表成为控制面权威源；MCP 投影不能决定发布状态。
- 数据库事务和审计写入失败必须整体回滚，发布前会再次验证持久化内容。
