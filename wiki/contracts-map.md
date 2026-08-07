# contracts/ 契约地图

> 状态：**冻结中**（Phase 0B）。落地后本页刷新字段速查。
> 施工读契约的顺序：先看本页定位文件，再精读对应文件本身——以文件为准，本页只做导航。

## 目录结构（规划）

| 路径 | 内容 | 谁消费 |
|------|------|--------|
| `quality-api/openapi.yaml` | Quality API v2：VersionSet、生命周期、CAS/idempotency、/logs、/feedback、B1–B4 注入端点 | demo-app（实现）、Release Controller（调用）、conformance |
| `events/events.yaml` | 七聚合领域事件（event_type/payload/causation_id） | control-plane、mcp-servers |
| `events/state-machines.yaml` | 七状态机：状态枚举+迁移表+失败语义 | control-plane（实现）、eval-harness（断言） |
| `schemas/workorder.schema.json` | 不可变 WorkOrder，hash 绑定规则（canonical JSON+SHA-256） | 修复师产物、门禁、审批 |
| `schemas/approval.schema.json` | ApprovalGrant（hash+nonce+expiry，一次性 nonce） | 审批流、Release Controller |
| `schemas/evidence-bundle.schema.json` | 归因证据包 | 归因师、归档、演示 |
| `schemas/attribution-report.schema.json` | 5-cell 结果 + 三态裁决 | 归因师、守门员 |
| `schemas/trust-ledger-entry.schema.json` | 信任账本条目（二维枚举、epoch 计数、Wilson 下界） | trust-ledger 模块 |
| `schemas/gate-report.schema.json` | 门禁报告（双轨、确定性/live 分离） | 守门员、WorkOrder 附件 |
| `fixtures/b1-*.yaml` | B1 ground-truth（预期归因层=prompt，裁决=ATTRIBUTED） | eval-harness、demo-app 注入端点 |
| `fixtures/b2..b4-*.yaml` | B2–B4 简版（Phase 2，draft） | 同上 |
| `fixtures/probes-customer-service.yaml` | 小智客服探针集（discovery/hidden/unaffected） | eval-harness、归因实验 |
| `wilson/wilson-vectors.json` | Wilson 测试向量（含 3/3→下界≈0.438） | trust-ledger 测试 |
| `conformance/` | pytest 契约测试套件（对空实现跑红） | 所有人、CI |
| `OPEN-QUESTIONS.md` | 契约歧义集中清单 | 主控裁决 |

## 关键约定（所有实现必须遵守）
- hash 规范：canonical JSON（key 排序、无空白、UTF-8）+ SHA-256 hex
- 写面三件套：`If-Match/expected_revision`（CAS）+ `idempotency-key` + 异步 `operation_id` 查询
- 门禁报告必须拆 contract/replay（确定性）与 live-provider E2E 两类结果
- 信任账本：一次动作=一个样本（多条探针合并计 1）；evidence epoch 记原始整数计数
- 归因：只有 ATTRIBUTED 能进修复；INCONCLUSIVE→补实验/升级人工；CONFOUNDED→2³ 全因子
