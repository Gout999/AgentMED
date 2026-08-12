# 施工指南

[返回 Wiki 索引](INDEX.md)

> 适用范围：当前 v3 客服参考实现及其向通用 Agent 产品演进的工作。产品范围、当前实施基线和运行事实必须分层；本页不自行创造需求。

## 开工前的权威顺序

1. 产品范围与口径：[产品原则](../docs/product-principles.md)。
2. 工程规则与 V5 执行：[AGENTS.md](../AGENTS.md)、[文档索引](../docs/README.md)、
   [Master Execution Plan](../docs/plans/v5-master-execution-plan.md) 和任务涉及的 contracts。
3. 当前任务和事实：[PLANS](../PLANS.md)、[PROJECT_STATE](../docs/context/PROJECT_STATE.md)、Git 状态与真实 evidence。
4. 研究、比赛、Wiki 与交接只提供参考；冲突必须显式提出，不能让 Agent 猜成权威决定。

通用 Agent 新能力必须先进入新版 PRD / plan / contracts。Langfuse 或第二 workload 的需求已被确认，不等于可以跳过契约阶段直接施工。

## 当前 v4 施工切片

| 切片 | 当前状态 | 口径 |
|---|---|---|
| S1A · authenticated maintainer report without trace | `DONE (LOCAL RUNTIME)` | `007`、本地 bootstrap、5 个 HTTP/CLI intent 和 PostgreSQL 事务链已在 `22c23f8` 完成；独立 verifier 与 evidence 通过，但所有 provider/Agent/外部/生产 facet 仍为 `NOT_RUN` |
| S1B · Langfuse read + CaseLoop OTel write/readback | `NOT IMPLEMENTED / PROVIDER-LIVE BLOCKED` | 当前只有 frozen wire contract；connector、008、真实读取和独立 sink 回读尚未施工，且 live 前必须轮换隔离凭证并取得单独授权 |

Stage 1 只有在 S1A 与 S1B 各自满足测试、证据和验收边界后才能整体关闭；本地 PostgreSQL/loopback 通过不等于 `domain-provider-live`。

V4 S1B–S7 继续冻结。D-013/D-015 和 V5 construction baseline 已接受；C0–C5 收敛已
完成并经 `b6fa629` 复核，D2（contract-only）、R3-full 与 R4 已关闭，当前唯一执行入口是
V5-2A（Master §17.6），代码施工按 2026-08-12 owner 裁决在远端机进行，本地负责
disposable-PostgreSQL journey、live facet 与最终收口（见
[V5 远端施工交接](../docs/context/V5_REMOTE_CONSTRUCTION_HANDOFF.md)）。V5-2B+ 按前驱链
锁定，并另行冻结对应 contract/runtime activation；不得从“baseline accepted”推导
route、worker、Adapter 或 Console 已完成。

## 工具无关的角色分工

- **任务控制者**：确认目标和权限，拆分无重叠 scope，维护决策与验收口径。
- **实现者**：在指定文件范围内实现并运行 focused tests，不触碰无关工作树改动。
- **独立验证者**：只读复核代码、契约、失败路径与 evidence；不能把实现者自述当结果。
- **运行操作者**：只在获得相应授权和真实前置后执行迁移、容器、provider 或 live 流程。

所用模型或工具可以替换，不属于项目架构。

## 并行与文件纪律

- 按不相交的文件或组件 scope 委派；任务必须写明目标、可碰/不可碰边界、验收标准和输出位置。
- 共享工作树先看 `git status --short --branch`。已有改动属于用户或其他任务，禁止 reset、清理、覆盖或混入本次提交。
- 契约变化必须同步 schema / OpenAPI、conformance、迁移、上下游实现和 context；不能只改一侧“先跑起来”。
- 持久化 schema 变化必须用 migration；不以开发态 `create_all` 充当部署方案。

## Git 规则

- 不直接提交或推送 `main`；不 force-push、不改写公共提交、不删协作者分支。
- 使用 `codex/*` 或用户指定分支，每个 P0/Stage 闭环使用聚焦语义提交；本地 S1A 收尾提交已获授权，push、PR、merge 与外部写仍需单独授权。
- commit message 建议 `<type>(<scope>): 中文摘要`，其中 type 可为 `feat`、`fix`、`docs`、`test`、`chore`。
- 密钥永不入库。发现疑似泄漏时停止传播、报告影响范围并轮换；不要把 secret 写进 evidence 或 PR。

## 验证与证据分级

| 轨道 | 可以使用 | 能证明什么 |
|---|---|---|
| unit | mock / fake | 局部逻辑与失败语义 |
| contract | schema fixture、stub server | 接口和资产一致性 |
| replay | 录制 provider、确定性 judge、fixture | 在已标明替身下的可重复闭环 |
| domain-provider-live | 真实 provider、数据库、Quality API、通知与 receipt | 真实外部调用和确定性控制面链路 |
| agent-causal | 真实内部 Worker、模型/Skill/MCP receipt、pre-action proposal 和 causation binding | Agent 对后续动作有可验证的因果贡献 |

- 连接失败、skip、空结果、未知状态、缺失 receipt、digest 不匹配都不能记作 pass。
- Matrix event、MinIO object、CR、截图或 exporter 签名只能证明对应平台行为，不能单独证明 Agent 阅读、推理、创作或因果参与。
- P0 证据放在 `evidence/p0/`，绑定精确 commit、契约版本、命令、外部 provider identity 与 artifact digest。
- 发布必须逐项验证不可变 WorkOrder、ApprovalGrant、GateReport/evidence、revision、nonce 和 expiry；任一失败或 UNKNOWN 都 fail closed。

## live 与重跑纪律

- 历史 live 不因为时间经过就自动无效。仅在代码/契约/provider 路径改变、原证据无法复验、或新验收目标要求时重跑。
- `make demo-b1-replay` 与 `make demo-b1-live` 是不同证据轨，不能互相顶替。
- live 前置缺失时停在 preflight；不得用 replay、机器人自发消息、自动批准或 exporter 造同形回执绕过。
- 完整 v3 栈包含固定 `outbox-dispatcher`。只启动 API 而不消费事务 outbox，不能宣称通知、archive 或 Trust 闭环完成。
- live 前先取得干净、可追溯的提交快照；不要用 `--allow-dirty` 把测试开关变成验收捷径。

## 环境与凭证安全

- 每个进程只注入各自 `.env.example` 所需的最小变量；不要 `source` 其他仓库或未知整份 `.env`。
- 测试数据库必须显式指定 disposable URL。会 reset schema 的 control-plane PostgreSQL integration suite 还必须显式设置 `CASELOOP_ALLOW_INTEGRATION_RESET=true`；若 shell 已有生产/演示 `DATABASE_URL`，测试可能沿用它。
- `docker compose config` 可能展开 secret 并打印到日志；共享会话不要运行未脱敏的完整输出。
- CaseLoop 原生 control-plane 默认宿主 `8090`；Compose profile 是宿主 `18090 → 容器 8090`；AgentTeams controller 的容器内 `8090` 属于另一个地址空间。
- MCP 隔离测试使用 [mcp-servers/README.md](../mcp-servers/README.md) 中的完整受信 backend smoke，包含所需 token 和 consumer 约束；不要复制过期裸命令。
- `demo-app/scripts/reset_state.sh` 会改变或删除演示数据，只能用于明确 disposable 环境；记录运行前后 VersionSet，不把“保持故障注入”当通用收尾规则。

## AgentTeams v1.2.1 历史运维地雷

以下来自 2026-08-08 的特定本机 profile，执行前必须在当前版本复验。通用产品不依赖这些内部技巧；详情和证据边界见 [platform-agentteams.md](platform-agentteams.md)。

1. 宿主长驻 MCP server 不应从会被清理的临时 worktree 启动；control-plane 容器重建后，旧 httpx 连接池可能仍指向死连接。
2. AgentTeams 内部 Worker 的单线程 ReAct loop 可能延迟新消息；重启窗口存在消息已推进 sync token 但未处理的风险。
3. Matrix `m.mentions` 必须指向真实 Worker MXID；管理侧代发只允许平台诊断，不能充当 Agent 执行 evidence。
4. 对“工具不可用”先做受信 MCP 隔离测试，区分 Agent 参数构造错误与服务缺陷。
5. macOS 系统代理可能让 httpx loopback 请求走代理而返回 502；`curl` 通不代表应用连接通。仓库内部客户端按代码要求禁用环境代理，并显式配置 `NO_PROXY`。

## 发现即回写

- 产品或范围决策 → `docs/product-principles.md` / 新版 PRD，经用户确认后更新 [decisions.md](decisions.md)。
- AgentTeams 版本化平台事实 → [platform-agentteams.md](platform-agentteams.md)。
- 契约歧义 → [contracts/OPEN-QUESTIONS.md](../contracts/OPEN-QUESTIONS.md)。
- 环境变化 → [environment.md](environment.md)。
- 当前工作、阻塞、测试与 evidence → [PLANS](../PLANS.md) 和 `docs/context/`。

## 历史平台改进审计（2026-08-08 至 2026-08-09）

> G1–G17 是当时 e2e / PR #1 的审计快照，保留作溯源，不是当前任务队列。当前优先级、完成状态与复验结果以 `PLANS.md`、模块 issue 和最新代码/测试为准。

| # | 缺口 | 实战证据 | 建议 |
|---|------|---------|------|
| G1 | eval-runner 无 probe.list 工具 | 归因师 RBAC 拿不到冻结探针清单，靠主控喂 | eval-runner 加自描述执行清单工具 |
| G2 | conformance 套件收尾不还原 demo-app | 复跑后 active 版本集留 v-test-* 残留，chat 兜底 | 套件 teardown 自动 reset_state |
| G3 | heartbeat 抑制域工作 | 质量官醒后收 heartbeat "do not do domain work"，8 分钟静默未执行主控指令 | heartbeat 与域消息分优先级，或限定"仅本条心跳回合" |
| G4 | worker JWT 1h 过期不自愈 | agt 401 后 4 小时无人管，手工 docker rm 重建 | controller 周期重铸或 sidecar 自刷 |
| G5 | 门禁规则轨不对账 live digest | 修复师伪造 digest（模式补全值）规则轨放行，hash_binding 只查内部一致性 | ✅ **已修（PR#1）**：gate_service 用 quality.get_versionset 对账 identity/digest/revision + 逐探针要求 digest 与 live 版本集内容一致 + get_log 逐条核对 provider 日志；attach_gate 改服务端对账 |
| G6 | 绑定层错误表象脱节 | quality 绑定失败→502 quality_api_error，与"digest 不存在"根因脱节（注：本次实为代理拦截，见地雷#9；但绑定层若失败同样 502，仍值得改） | 绑定失败返 422+具体不匹配字段 |
| G7 | release 生命周期无 noop-close | B1 为运行时偏离（target==active declared），stage/canary 合法拒绝，release 永卡 REQUESTED | 加 reconcile/noop-close 迁移；WorkOrder diff 增 runtime_reconcile 类型。**部分修复（PR#1）**：candidate 创建+reconcile 已消解 B1 卡死根因，REQUESTED 无出口的路径仍开放 |
| G8 | case 无 close/resolve 迁移 | case_admin 工具面无关闭，本案终态只能停 ESCALATED | ✅ **已修（PR#1）**：case.closed 必须 receipt 绑定（causation=确切的 notification.sent 事件） |
| G9 | 信任账本无 MCP 写入工具 | case-officer 无账面可写，用 Markdown 文档顶替并宣称"平衡"（宣告≠执行复发） | ✅ **已修（PR#1）**：选"发布完成事件平台自动记账"路径，outbox dispatcher 驱动 trust_service.record_outcome（幂等+冲突拒绝）；旧 trust_ledger 库降级为 legacy contract/replay 专用 |
| G10 | 模型错误直接上墙 | StepFun RPM 错误原文（含内部路径）贴进房间 | copaw channel 错误包装或静默重试 |

### PR#1 review 遗留（2026-08-09 主控验收登记，均为 follow-up 不阻塞）

| # | 缺口 | 实战证据 | 建议 |
|---|------|---------|------|
| G11 | demo-app `/admin/reset` B1 切版本集后 500 | faults.py:439-446 `reset_faults` 抛 KeyError，routers/admin.py:84-92 未捕获；conformance 顺序跑 flake 根源（B1 注入测试单跑过连跑挂） | admin 端点捕获映射 409/422；套件 teardown 联动 G2 |
| G12 | trust evidence_epoch 硬编码 1 | trust_service.py:335-357 `_locked_row` epoch=1，全库无轮转逻辑 | Phase 2 补 epoch 轮转语义 |
| G13 | outbox adapter 未知名静默回落 Disabled | outbox_relay.py:143-164 配置拼错完全不可见（方向 fail-closed 正确） | worker 启动时对未知名 adapter 直接 refuse |
| G14 | replay/live 门禁 dataset_id 不一致 | replay 字面量 "b1-canary-observation" vs live `probe_set.probe_set_id`（PR#1 已对 persisted 绑定做独立校验，字面量本身未统一） | live B1 收尾时统一口径 |
| G15 | console evidence guard 钉死 unavailable | validators.ts:255,260 要求 artifact_store==="unavailable" 才算合法，后端将来真接 artifact store 会把健康响应判 invalid_response | 接真 store 时同步放开 guard；已记 OPEN-ISSUES 候选 |
| G16 | 决策编号撞车 | docs/decisions/ 存在两个 D-002（executor-routing 旧 / gate-workorder-binding 新） | 新文件改号或旧文件归档 |
| G17 | 代码注释语言分裂 | PR#1 新模块（gate_service/trust_service/outbox_relay）全英文 docstring，旧代码中文 | 后续统一，不专项处理 |
