# `contracts/` 契约地图

[返回 Wiki 索引](INDEX.md)

> 状态：Phase 0B 的 v3 契约是客服参考纵切的实施基线。`contracts/v4/` 已通过 Stage 0 与 Stage 1 Entry contract-only verifier。S1A 的 migration、HTTP/CLI 和 no-trace runtime 已在 `22c23f8` 完成并通过独立 verifier/evidence；这只是本地 runtime，不是 provider/Agent/live 证明。S1B 尚未实现，provider-live 还受凭证轮换、单独授权和 V5 scope review 阻塞。

施工顺序：先确定变更涉及哪份契约，再精读文件和对应 conformance test。本页只做导航，不能覆盖原契约。

## 当前 v3 契约资产

| 路径 | 内容 | 主要消费者 |
|---|---|---|
| [quality-api/openapi.yaml](../contracts/quality-api/openapi.yaml) | 小智客服参考应用的 Quality API v2：VersionSet、CAS / idempotency、异步 operation、logs、feedback 与 B1–B4 内部注入 | demo-app、Release Controller、conformance |
| [events/events.yaml](../contracts/events/events.yaml) | 七聚合领域事件、统一 envelope、事务 outbox integration event 与客服投诉接入 | control-plane、MCP、outbox consumers |
| [events/state-machines.yaml](../contracts/events/state-machines.yaml) | Case、Experiment、ChangeSet、Eval、Release、Notification、Trust 七个 v3 状态机和失败语义 | control-plane、测试与审计 |
| [schemas/workorder.schema.json](../contracts/schemas/workorder.schema.json) | `WorkOrder` v0.1.0，不可变候选变更和 canonical hash 绑定 | repair proposal、Gate、Approval、Release |
| [schemas/approval.schema.json](../contracts/schemas/approval.schema.json) | `ApprovalGrant` v0.1.0，审批对象、nonce、expiry、proof | approval authority、Release Controller |
| [schemas/evidence-bundle.schema.json](../contracts/schemas/evidence-bundle.schema.json) | `EvidenceBundle` v0.1.0，冻结实验与逐探针结果 | attribution、Gate、归档 |
| [schemas/attribution-report.schema.json](../contracts/schemas/attribution-report.schema.json) | `AttributionReport` v0.1.0，5-cell 结果、效应量、CI 与三态裁决 | attribution、Gate |
| [schemas/gate-report.schema.json](../contracts/schemas/gate-report.schema.json) | `GateReport` v0.2.0，规则/裁判/确定性/live 分类与不可变绑定 | eval-harness、control-plane、WorkOrder |
| [schemas/trust-ledger-entry.schema.json](../contracts/schemas/trust-ledger-entry.schema.json) | `TrustLedgerEntry` v0.1.0，risk、autonomy、epoch 与 Wilson 结果 | Trust Ledger |
| [schemas/b1-run-manifest.schema.json](../contracts/schemas/b1-run-manifest.schema.json) | `B1RunManifest` v0.1.0，B1 evidence package 的 artifact 引用与运行元数据 | replay/live runner、validator |

## v4 Stage 0 target contracts

| 路径 | 内容 | 状态边界 |
|---|---|---|
| [v4/README.md](../contracts/v4/README.md) | v4 contract 版本、边界和运行命令 | target contract，不证明 API 已上线 |
| [v4/aggregate-ownership.yaml](../contracts/v4/aggregate-ownership.yaml) | Signal、Case、Work、Proposal、Evidence、Gate 与外部操作的唯一 owner/cutover | S1A 对应 PG authority path 已实现并验证；其他 owner 仍是 target |
| [v4/intent-registry.yaml](../contracts/v4/intent-registry.yaml) | 14 个 target public intent：8 个 S1A/S1B `FROZEN`，6 个后续 Stage `SKELETON`；含 scope、transport mapping 与 activation Stage | 已实现 5 个 S1A HTTP/CLI intent；S1B 3 个 intent 与后续 skeleton 仍不得 advertise |
| [v4/openapi/public-api.yaml](../contracts/v4/openapi/public-api.yaml) | Public API v1 OpenAPI 3.1：5 个 S1A 与 3 个 S1B frozen operation | 5 个 S1A route 已实现并验证；OpenAPI 中的 S1B operation 不是 runtime 证明 |
| [v4/events/](../contracts/v4/events/) | WorkerTask/Attempt/Gate/Capability/ExternalOperation 等事件与恢复状态机 | target event grammar；不是运行日志 |
| [v4/schemas/](../contracts/v4/schemas/) | Signal、AgentRunRef、TraceEvidenceReceipt、Resolution/Candidate/Evaluation、WorkerTask/Attempt/Proposal 等 JSON Schema | S1A no-trace subset 已有本地 verified runtime；其余 target schemas 与 fixtures 都不是 live evidence |

## Stage 1 切片状态边界

| 切片 | 当前事实 | 仍缺什么 |
|---|---|---|
| S1A · authenticated maintainer report without trace | `DONE (LOCAL RUNTIME)`：`007`、本地 bootstrap、5 个 HTTP/CLI intent，以及同事务 Signal/Case link/`UNKNOWN` receipt/event/audit/outbox/idempotency 路径 | provider、Agent、外部与 production facets 均为 `NOT_RUN`；evidence 见 `evidence/v4/stage-1/maintainer-intake/` |
| S1B · Langfuse read + CaseLoop OTel write/readback | `FROZEN` wire contract only；connector、cursor/DLQ、真实 Langfuse 读取与独立 sink 回读均未实现 | 008、runtime、测试，以及轮换后的隔离凭证和单独 live 授权；当前 provider-live 为 `BLOCKED` |

## Fixtures 与样例

| 路径 | 用途 | 状态边界 |
|---|---|---|
| [fixtures/b1-prompt-regression.yaml](../contracts/fixtures/b1-prompt-regression.yaml) | B1 prompt 回归 ground truth | 当前 v3 参考纵切 |
| [fixtures/b2-kb-regression.yaml](../contracts/fixtures/b2-kb-regression.yaml) | B2 知识回归 | Phase 2 fixture；不代表全链已实现 |
| [fixtures/b3-model-params-regression.yaml](../contracts/fixtures/b3-model-params-regression.yaml) | B3 模型参数漂移 | Phase 2 fixture；不代表全链已实现 |
| [fixtures/b4-interaction.yaml](../contracts/fixtures/b4-interaction.yaml) | B4 多因素交互 | Phase 2 fixture；不代表全链已实现 |
| [fixtures/probes-customer-service.yaml](../contracts/fixtures/probes-customer-service.yaml) | 小智客服 discovery / hidden / unaffected 探针 | 客服参考数据集，不是通用 Agent schema |
| [fixtures/samples/](../contracts/fixtures/samples/) | WorkOrder、Approval、Attribution、Evidence、Gate、Trust 与 B1 manifest 的合法样例 | schema conformance；不是 live evidence |
| [wilson/wilson-vectors.json](../contracts/wilson/wilson-vectors.json) | Wilson 区间测试向量，包括 3/3 下界约 `0.438` | Trust 统计口径 |

## Conformance 与待定项

- [conformance/README.md](../contracts/conformance/README.md)：各测试文件职责和运行方法。
- [conformance/test_quality_api.py](../contracts/conformance/test_quality_api.py)：针对实际 Quality API 的读写契约测试；服务连接失败是前置失败，不是 pass。
- [conformance/test_schemas.py](../contracts/conformance/test_schemas.py)：Schema、样例、反例、hash 与事件资产自洽。
- [conformance/test_wilson.py](../contracts/conformance/test_wilson.py)：统计口径。
- `conformance/test_v4_*.py`：v4 schema、owner、intent/OpenAPI、state/recovery 与 v3→v4 cutover target contract。
- [conformance/LAST-RUN.md](../contracts/conformance/LAST-RUN.md)：2026-08-08 Phase 0B 历史实跑快照；当前测试状态以 [PROJECT_STATE](../docs/context/PROJECT_STATE.md) 为准。
- [OPEN-QUESTIONS.md](../contracts/OPEN-QUESTIONS.md)：当前 v3 契约中仍需显式裁决的歧义；不得由实现悄悄决定。

## 当前 v3 必须遵守的约定

- hash：契约指定的 canonical JSON / JCS + SHA-256；不得用展示文本或重新排序后的猜测值替代。
- 写面：CAS revision + `Idempotency-Key` + 异步 `operation_id` 查询；只有 Release Controller 有权调用。
- 发布：WorkOrder、ApprovalGrant、GateReport / evidence、目标 revision、nonce 和 expiry 必须逐项匹配。
- 失败闭锁：`FAILED`、`INCONCLUSIVE`、`ERROR`、`UNKNOWN`、缺失、过期或不匹配都不能发布。
- 当前 v3 GateReport：严格分开 rule、judge、deterministic tests 与 live-provider。v4 使用 canonical facets；target Attempt/Proposal/causation schemas 已落盘，但 runtime 与 `agent-causal` live 尚未证明。
- 归因：只有 `ATTRIBUTED` 可进入修复；`INCONCLUSIVE` 补实验或人工接管，`CONFOUNDED` 按协议扩大实验。
- Trust：一次动作一个样本，多探针不能增加样本量；R2 永远逐次审批。

## 已进入 target contract、尚未闭合的边界

下列内容已经由 PRD v2 / plan v4 批准；能否进入某个已实现能力仍取决于对应 migration、runtime、test、evidence 和 cutover：

| 候选契约 | 需要定义的核心内容 |
|---|---|
| `Signal Adapter` | 来源身份、去重、原始 Agent Run / trace 引用、隐私与 severity |
| `TraceSource` | provider/project、查询窗口与水位、分页去重、root trace、输入/输出/模型/工具、fetch receipt |
| `Trace Completeness` | 采样、脱敏、缺失 span、留存、权限、时间覆盖与 `UNKNOWN` 语义 |
| `Agent VersionSet` | 仍需在后续 additive contract 冻结 prompt、model、tool、skill、memory/knowledge、harness、policy、environment 的最小可重放集合 |
| `Proposal Causation` | Agent 输入快照、原生 Worker/model/tool receipts、pre-action proposal 与后续权威事件的因果绑定 |
| `Closure / Release Adapter` | 不同 Agent runtime 的发布、回滚、停止、告警和结果回传语义 |
| CaseLoop 自身 trace 出站 | 标准传播、service/tenant 标识、脱敏、采样与 Langfuse adapter 配置 |

表中 Signal 的 S1A no-trace 子集已完成本地 runtime、verifier、evidence 与 commit；TraceSource、带 locator 的 Trace Completeness、Connector、完整分布式 trace、Closure/Release Adapter 和 CaseLoop 自身 trace 出站仍待 S1B、后续 Stage 或 V5 scope 裁决。target contract 只能证明我们已明确语义，不能证明尚未实现的真实调用路径或 live。
