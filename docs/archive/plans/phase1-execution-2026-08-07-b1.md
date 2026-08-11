# Phase 1 施工排期（B1 纵切全闭环）

> 归档状态：**HISTORICAL / SUPERSEDED**
>
> 原路径：`docs/plans/phase1-execution.md`
>
> 本文记录 2026-08-07 的 v3/B1 分工快照。当前 V5 施工以
> [`v5-master-execution-plan.md`](../../plans/v5-master-execution-plan.md) 为准；
> 本文不能证明当前 runtime、stage、Agent、provider、live 或 evidence 状态，也不得作为
> 当前施工 authority。

> 日期：2026-08-07 ｜ 主控：Kimi（规划/验收）｜ 执行：Claude Code（`claude -p` headless）+ Grok（grok_run）
> 前置已就绪：contracts 冻结稿、D-001 裁决、0A Spike（含 S0-001~004 平台事实）、PRD/spec 草案
> 铁律回顾：contracts 变更须主控批准；密钥不入库；不发明 plan-v3 之外的架构决策

## 共同约定（每个任务 brief 不必重复）

- 语言统一 **Python 3.11+**（demo-app / control-plane / eval-harness / mcp-servers 全部 FastAPI + pytest）；
  依赖钉版本（requirements.txt 或 pyproject + lock），禁止引入清单外新依赖（先报主控）
- 存储：一个 postgres+pgvector 容器（deploy/compose.yaml），三个逻辑库：`control_plane`、`demo_app`、`casebase`
- 配置走环境变量 + `.env.example` 模板；StepFun key 从环境注入，绝不写死
- LLM 调用纪律（D-001 三）：集中限速器 8 RPM、429 指数退避、temperature=0 + 探针 digest 记录
- 每个组件自带 `/healthz`；测试分 unit（无外部依赖）与 integration（需 compose 环境）两层
- commit：`<type>(<scope>): 中文摘要`；完成任务时自行 commit + push main
- 交接物：组件 README（跑法、环境变量、API 摘要）+ 测试实跑结果摘要

## 反剧本设计约束（贯穿全部 Agent 相关交付）

1. 状态机只长在控制面；Agent 的 SOUL 只给角色/边界/工具/质量标准，**不给步骤模板**
2. 归因师/修复师的产出由 LLM 自由生成（实验设计建议、修复 diff 起草），控制面只验证格式与门禁
3. 泛化验收：e2e 必须包含一条**训练外措辞投诉**和一个**未见过的故障变体**，Agent 须照常闭环
4. 换应用=换 fixtures（探针集/KB 种子），不改一行治理层代码——T1 交付时以此自查

## Wave 1（并行，只依赖 contracts）

### T1 demo-app「小智客服」→ Claude Code
- scope：`demo-app/`、`deploy/compose.yaml`（demo_app 库部分）、不碰其他目录
- 输入：contracts/quality-api/openapi.yaml、contracts/fixtures/probes-customer-service.yaml、
  contracts/conformance/、wiki/（project-brief、decisions、environment）、D-001
- 交付：FastAPI RAG 客服（3C 数码；售后政策/产品参数/物流规则种子 KB ≥30 条；pgvector 表先建、
  检索用全文+元数据过滤，向量列预留 1024）；prompt git 化版本管理；LLM 真实调用 StepFun
  （集中限速器 8 RPM + 429 退避）；Quality API v2 全端点（写面 CAS/idempotency/异步 operation）；
  /logs、/feedback（cursor 分页）；B1–B4 注入端点（x-internal）；OTel 埋点
- 验收：**contracts/conformance 39 测全绿**（含 B1 注入链路）+ 自测 README
- 体量：大；允许 claude 多轮续作（同一 session --continue）

### T2 control-plane → Grok
- scope：`control-plane/`、`deploy/compose.yaml`（control_plane 库部分，与 T1 协调同一文件——
  约定 T2 先出 compose 骨架，T1 在其上追加 demo-app 服务，冲突时 T1 后改）
- 输入：contracts/events/（events.yaml + state-machines.yaml）、contracts/schemas/、
  docs/spec.md §7（数据存储）、D-001、wiki/
- 交付：Case Controller（PG aggregate/event/inbox/outbox、CAS、lease 60s+fencing token、
  七状态机之 Case/Notification/Release 实现、UNKNOWN→reconcile 退避）+
  Release Controller 骨架（Quality API 写面唯一入口，调 demo-app 的客户端按 openapi.yaml 生成）+
  REST API（cases/experiments/changesets/releases 的 CRUD + 状态迁移端点）
- 验收：unit 全绿 + integration：compose 起 PG 后跑通"立案→领单→完结"与"draft→canary→promote|rollback"状态流
- 体量：大；Grok 单 session 完成，修订走 session 续接

## Wave 2（并行，依赖 contracts + Wave 1 接口）

### T3 eval-harness → Grok
- scope：`eval-harness/`
- 输入：contracts/fixtures/（b1、probes）、contracts/schemas/（attribution-report、evidence-bundle、
  gate-report）、docs/spec.md §4（归因协议）、D-001（n=5、δ_min=0.2、newcombe_wilson_diff）
- 交付：回归评测跑分器；双轨门禁（规则轨 + 裁判轨，裁判≠运动员 digest 断言，contract/replay
  与 live E2E 分开报告）；5-cell 对照实验执行器（探针冻结 digest、随机臂序、n=5、pacing 8 RPM、
  unaffected controls、输出 Δ+95%CI+三态裁决，INCONCLUSIVE 重试 2 次上限）；变异巡检器（单次版）；
  质量周报生成器
- 验收：对 B1 fixtures 跑出 ATTRIBUTED + fault_layer=prompt 的实验报告（schema 校验通过）

### T4 mcp-servers + trust-ledger → Claude Code
- scope：`mcp-servers/`
- 输入：docs/spec.md §9（MCP 清单）、contracts/schemas/（workorder/approval/trust-ledger-entry/
  gate-report）、contracts/wilson/、S0-004（MCP 注册调用路径）、D-001（Q7 server_recorded、Q8 冷却+人工）
- 交付：5 个 MCP server（case-admin/release-admin/eval-runner/notification(feishu-mock)/
  casebase-knowledge，streamable-http，PathRewrite 模式照 spike-mcp）+ trust-ledger 模块
  （Wilson 双侧、epoch 原始整数、一次动作=一样本、晋升判据、拒绝晋升事件）+
  重写的 common（审批 hash+nonce+expiry 一次性消费防重放；审计写库失败即拒业务）
- 验收：contracts/wilson 全向量过 + 防重放/审计失败即拒的专项测试 + MCP 冒烟脚本

## Wave 3（串行收口）

### T5 agents/ 定义（主控设计 SOUL 骨架 → Claude 成稿 → 主控审）
- team.yaml（4 常设 CR + 弹性模板 + Team/Human CR）+ 6 SOUL.md + 安装 runbook
- SOUL 按"反剧本约束"写：角色/边界/工具/质量 bar；交接路径照 S0-003（shared/tasks/{task-id}/）
- MCP 挂载照 S0-004 真实路径；串行编排（活跃 worker ≤2）写进质量官 SOUL

### T6 e2e 全闭环（主控指挥，Claude 执行脚本）
投诉(feishu mock)→立案→归因（ATTRIBUTED/prompt）→修复起草→门禁→审批→灰度→回全量→
回复原群→归档→信任记账拒绝晋升；含训练外措辞+未见故障变体两条泛化用例；证据落 evidence/phase1/

## Wave 4

### T7 console 前端（主控自己做：Next.js 或轻量 SPA）
案例时间线、实验报告、审批中心（WorkOrder hash 展示）、信任账本、门禁报告可视化；视觉自验

## 风险与备注
- T1/T2 共改 deploy/compose.yaml：T2 先出骨架（本波次开始 1h 内），T1 rebase 追加
- RPM=10 是全局约束：任何并行演示编排由控制面串行化
- 裁判模型暂用 StepFun（digest 不同即可满足"裁判≠运动员"字面要求；用户后续给异构模型即切）
- spec/prd 为草案，施工中发现的文档错误直接修并 commit（docs(<scope>)）
