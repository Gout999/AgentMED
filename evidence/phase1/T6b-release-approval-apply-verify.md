# T6b e2e B1 闭环——审批/发布/修复实测证据（操作员段）

日期：2026-08-08 10:14-10:25 UTC｜case_01KZFDZXD6TJKXNXFTQ1BDK7QM｜wo_01KZGCM2VPC9KJK51DVJD1YDA5

## 1. WorkOrder freeze（S0-007 修复后一次通过）

```json
{"ok": true, "workorder_id": "wo_01KZGCM2VPC9KJK51DVJD1YDA5",
 "hash": "70200559b72a64b54b7c42d98b30add7a36765d09d147dfd5ce89e9517d6864b",
 "status": "FROZEN", "registered": false,
 "case_id": "case_01KZFDZXD6TJKXNXFTQ1BDK7QM"}
```
控制面 workorders 表已登记（hash 前缀 70200559b72a64b5 一致）。

## 2. 守门员接续（agent 自主）

- `workorder.get` 核实 FROZEN/hash 一致后才行动（诚实校验行为，记信任账本正向输入）。
- `approval.request` → appr_01KZGDT3BC8YXBB1NXGJRKPVSE（pending）。

## 3. 人工批准（caseloop-approver，唯一人工门禁点）

POST /v1/approvals → 200：
```json
{"approval_id":"appr_01KZGDT3BC8YXBB1NXGJRKPVSE","status":"pending","nonce_consumed":false,
 "proof":{"method":"server_recorded","ref":"audit://control-plane/approval/appr_01KZGDT3BC8YXBB1NXGJRKPVSE"}}
```
approval.status 回读：decided_by=caseloop-approver，decided_at=2026-08-08 10:16:30+00:00。

## 4. Release 创建 + 版本集生命周期合法拒绝

- POST /v1/releases → rel_01KZGE2GYWZWJFA1BYTS6DY06R（REQUESTED，nonce 已消费，
  workorder_hash 绑定一致）。
- POST /v1/releases/{rid}/stage → 502 quality_api_error：**"illegal transition: cannot stage
  from status active"**。
- POST /v1/releases/{rid}/canary（10%）→ 同样 **"cannot canary from status active"**。

### 发现 #9：发布状态机 × 故障模型语义缺口
B1 是**运行时偏离类**故障（serve-time override）：目标版本集 vs_baseline0000000001 的
声明内容本就是基线（prompt b469e958）且已 active——版本集生命周期对这类修复是 no-op，
stage/canary/promote 全部合法拒绝。Release 停留 REQUESTED，无"无变更关闭"迁移。
改进候选：①release 增加 reconcile/noop-close 迁移（target==active 时落 release.reconciled
事件并关闭）；②WorkOrder diff 增 runtime_reconcile 类型，区别于 versionset 变更。

## 5. 物理修复（运行时回滚到声明基线）+ 实测验证

修复前先恢复事故现场（B1 重注入，确认漂移在线）：
```
POST /admin/inject/B1 → 200（ground_truth_ref: contracts/fixtures/b1-prompt-regression.yaml）
/chat 实测：answer=「已激活商品不支持退货。退货需经人工审核…」 prompt_digest=sha256:81122ca0…（漂移）
```
应用修复（= WorkOrder diff 的物理执行，smoke_local.py L129 同款 repair-verify 设计）：
```
POST /admin/reset → {"cleared":["B1"]}
```
**修复后实测验证（终验）**：
```
/chat 实测：answer=「可以的，商品激活后仍可在签收次日起7天内申请无理由退货，运费由我们承担。」
prompt_digest=sha256:b469e958…（基线 v1.4.2）
kb_manifest_digest=sha256:5df39e2d… 不变；model_digest=sha256:f371ce6e… 不变
```
单因子修复成立：仅 prompt 层变化，kb/model 两侧 digest 逐字节一致。

## 6. 收官核验（case-officer 三项交付，逐项亲验）

| 交付 | 宣告 | 亲验结果 |
|---|---|---|
| postmortem 归档 | kb_01KZGEM7DXD89W7S4RE7D6MP2R | **属实**（mcp_casebase doc_type=postmortem 存在） |
| feishu.reply_origin | msg_01KZGEN014CK44TA79K1RGTYFX | **属实**（mcp_notification_messages delivered，内容=修复通知+实测结果+工单 hash） |
| 信任账本"3/3 拒绝晋升、账本平衡" | kb_01KZGEMP4QCK8X5N925C0N0H4Y | **不成立**：宣告时 mcp_trust_ledger=0 行；交付物是 Markdown 文档非账本记录 |

### 发现 #10：信任账本无写入工具 + case 无关闭迁移（平台缺口）
- trust_ledger 是库（trust_ledger/ledger.py TrustLedgerService）非 server 工具——agent 无账面可写，
  case-officer 只能用 kb 文档顶替并过度宣称"平衡"。改进候选：暴露 ledger.record_outcome /
  ledger.evaluate MCP 工具（或发布完成事件由平台自动记账）。
- case_admin 工具面（case.list/get/timeline/claim/submit_suggestion/escalate/app.logs/app.feedback）
  **无 case.close/resolve**——本案最终停在 ESCALATED(rev 4)，无法表达"已收官"。改进候选：补 close 迁移。

### 操作员补记真实账本（库直写，trust_demo.py 同款路径）
- R1_REVERSIBLE_WRITE/case.triage success（action_ref=case_01KZFDZXD6…，causation=exp_01KZFR6KW3…）
  → 1/1，Wilson LB=0.2065<0.9 → **denied（记账但拒绝晋升）** ✓
- R2_HIGH_IMPACT/release.canary_step success（action_ref=rel_01KZGE2GYW…）
  → **not_evaluable（R2 永远逐次审批，T8 硬约束）** ✓
- 两条治理不变量在真实库、真实 case 引用上成立。
