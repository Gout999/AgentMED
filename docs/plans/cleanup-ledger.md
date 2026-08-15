# 清理账本（以当前闭环为唯一核心）

> owner 授权（2026-08-14 睡前）：闭环跑通后，未使用的业务链路删除、重复代码合并删除、
> 屎山清理——测试+提交证据。本账本先列清单，逐项动手后更新状态。

## A. 未使用业务链路（删除候选）

| # | 目标 | 证据 | 处置 |
|---|---|---|---|
| A1 | demo-app（小智客服）+ Quality API 路径：compose demo-app 服务、demo-app/ 应用、mcp case_admin 的 app.logs/app.feedback、eval-harness QualityAPIClient | 被治理对象已换 Agent Station（D-015）；二轮取证中 app.logs/app.feedback 实测 DEPENDENCY_UNAVAILABLE（Quality API 不在） | 删除（compose/代码/tool） |
| A2 | feishu 通知链路：mcp notification feishu.reply_origin/weekly_report、control-plane notifications/adapters.py feishu 分支、case_admin feishu inbound routes | 审批/通知已走 Matrix 原生通道（D-015）；feishu 是 mock 且未配置 | 删除（保留 Matrix 通道） |
| A3 | B1 live 脚本组：scripts/run_b1_live.py(3.5K)、validate_b1_run.py(2.7K)、run_b1_replay.py(1.6K)、b1_live/ | 手工模拟团队闭环的老演示脚本；团队已在真实跑（flow-first） | ✅ 已删（fe21652 + 94269d5：脚本+harness 测试，-1.5K 测试行；CP unit 787 绿） |
| A4 | mcp-servers/trust_ledger/（legacy contract/replay 库） | README 自认「非生产权威写路径」；Wilson 契约在生产路径不复用 | ✅ 已删（库+表+trust_demo+2 测试；mcp tests 105 绿） |
| A5 | casebase 向量列/ivfflat 索引（002_casebase_vector.sql） | Phase 2 预留，未启用（检索走全文+元数据）；plain PG 无扩展 | ✅ 已删（迁移文件 + run_migrations 容错代码；mcp tests 105 绿） |
| A6 | agent-station deploy-langfuse-skill.sh + runtime/copaw/skills/langfuse-inspect | 复核：s0-revision-team 正在使用该 skill（skill_pool 实测含 langfuse-inspect）；S0 证据链引用它 | 保留（撤回删除，s0 团队在用的活资产）；脚本已 fail-closed 化 |

## B. 重复代码（合并删除候选）

| # | 目标 | 证据 | 处置 |
|---|---|---|---|
| B1 | W2a：v4_event_store(1063) 与 event_store(269) 双实现 | 两个事件存储实现均在活（v5 权威/目录/绑定 vs case/release/gate） | 合并重构（专用分支 + diff 证据） |
| B2 | W2b：authority(812) 与 v5_authority(1273) 双服务 | 两套 authority/receipt 链均活 | 合并重构 |
| B3 | W2c：audit.py 与 v4_audit.py 双写器（同一 Audit 表） | 两写器共写一张表 | 合并 |
| B4 | canonicalJson/sha256Hex 双份（manifest.mjs vs contracts hashing） | 审计 C-#4 已确认逐字重复 | 统一 import（S0 冻结面不动） |
| B5 | 控制面 ORM 三表文件 v4_tables/v5_tables/tables | 复核：分层非重复（保留） | 不删（已复核） |

## C. 屎山/过度工程

| # | 目标 | 证据 | 处置 |
|---|---|---|---|
| C1 | acceptance.py(1542)「V5-4 前不可执行」 | 自述不可执行且 fail-closed；首轮闭环未用 | 冻结（保留 API 面）或删除（等 owner 醒后确认面） |
| C2 | trust_service Wilson 双侧 95% 统计(397) | 对薄层过度；闭环只用计数语义 | 简化为计数账本 |
| C3 | release_service.py(3652 单文件) | 出口 3 逻辑 + 审批/nonce/reconcile 混合 | 拆分（出口 3 部分冻结） |
| C4 | proxy 内嵌 OTLP 遥测（agent-station） | 与平台追踪重叠（C16） | 剥离 |
| C5 | 7 状态机 + 双版本事件溯源 | 复核后状态机为领域代码（保留）；V4 事件源与 B1 合并 | 合并（与 B1 同批） |

## 执行顺序

1. 环路二轮 PASS 落定（出口 2 双分支演示完成）后动手；
2. A 类（纯删除，测试随行）→ B 类（合并重构，专用分支）→ C 类（精简）；
3. 每项 = 删除/合并 + 测试绿 + 语义提交 + 本账本状态更新；
4. 涉及运行面的（A1/A2）在团队运行期外窗口执行。

## B 类 diff 证据（2026-08-14 夜）

- **B1/W2a**：event_store(269, 内部聚合 CAS+重放) vs v4_event_store(1063, 公开 wire 完整性+幂等)——不同消费者不同语义，合并=共享 outbox/幂等原语的专项重构，非删；
- **B2/W2b**：AuthorityService vs V5AuthorityService——平行类（resolve_controller+校验链），V5 版多 v5-subject/事件依赖校验；合并=统一共享核+保留 V5 扩展；
- **B3/W2c**：AuditService(内部+JSONL) vs V4AuditService(公开完整性)——双 record() 写同一 Audit 表；合并=单写器+完整性选项；
- 结论：三项均为**行为敏感的合并重构**，需专用分支+diff 证据+测试对拍；夜间环路运行期不做，留待环路全绿后或 owner 醒后。

## 状态

- [x] A3 已执行（环路运行中完成，不影响演示）
- [x] A4 已执行
- [x] A5 已执行
- [x] A6 复核保留（s0 团队活资产）
- [ ] A1/A2 待环路落定（涉及运行面）
- [ ] B1/B2/B3 diff 证据已出，合并留待专用分支
- [ ] C1-C5 待环路落定

## 执行记录（2026-08-15 凌晨，环路出口2 达成后）

- **A1 ✅ 已执行**（147ec47）：demo-app 全部删除（-7216 行：应用+prompts+KB+alembic+tests）；
  compose demo-app 服务移除；postgres init 移除 demo_app 库；compose QUALITY_API_BASE_URL
  → AgentMED 评估面；变异巡检（mutation patrol，只巡逻 demo-app prompt 且无闭环消费者）删除；
  三处 Quality API 默认 8080 → 8088。测试证据：cp 790 / mcp 106 / harness 81 / conformance 24 全绿。
- **A2 ⚠️ 部分执行**（3b8068c）：删除 feishu.weekly_report（明确 feishu-mock 辅助适配器）+
  死代码 _parse_feishu_mock_ref。保留：feishu.reply_origin（实为 release closure-context 排队，
  channel-agnostic，工具名保留兼容 worker 技能）；control-plane notifications/adapters.py feishu
  分支 + case_admin feishu inbound 路由——它们的替代是「Matrix 原生通知」（段7 通知走平台 channels），
  属 flow-level 决策（D-015 下一迭代口径），非纯删除；留待 owner 醒后确认再动。
- **B1/B2/B3**：diff 证据已在账本，合并重构需专用分支——维持 defer。
- **C1**（acceptance.py 冻结面）**C3**（release_service 拆分）**C5**（与 B1 同批）：维持 defer（owner 醒后确认面）。
- **C2**（Wilson 统计简化）：行为敏感重构，留待专用分支。
