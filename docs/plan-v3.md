# CaseLoop —— 最终目标完整实现方案（终态蓝图 + 分阶段推进）v3

> 本版已吸收外部技术审查 F1–F8 全部修订（审查裁决：T0–T10 保留，补齐控制面/统计协议/真实 Spike 后 Conditional Build GO）。
> 核心架构原则：**确定性控制面 + 概率性执行面**——AI 负责动脑子，系统负责管规矩。
> 过程性材料（初赛简介/PPT、演示脚本）单独放 `docs/competition/`。

## 0. 系统定位

**CaseLoop：AI 应用质量自治底座。** 任何 LLM 应用实现 Quality API 契约即可被纳管；多 Agent 团队（AgentTeams 编排）自动完成 badcase 全生命周期闭环：**投诉进来 → 对照实验归因 → 自由起草修复 → 评测门禁 → 灰度发布 → 回复投诉原处 → 沉淀为评测与知识资产**。

核心理念：信任是挣来的且有数学纪律（Trust Ledger）；组织是活的（为 AgentTeams 补上弹性面）；每次事故让系统变强（案例即资产、Skill 自演化）；**LLM 永远不是状态与权限的权威源**。

## 1. 架构决策（T0–T10 修订版）

| # | 决策 | 结论 |
|---|------|------|
| T0 | 产品形态 | 治理层，Quality API 契约（§2.3.1 强化版）纳管任意 LLM 应用 |
| T1 | 被治理应用 | 自建演示应用「小智客服」（LLM 全部真实调用 StepFun，无 mock 层）；真实 SaaS 预留接入 |
| T2 | Agent 组织 | 4 常设 + 2 类弹性。**修正**：AgentTeams v1.2.1 无原生 autoscaling（Team 仅静态 workerMembers）→ 弹性面由自建受限 RBAC 的 **Caseload Controller** 实现（drain 语义），定位为"补齐 AgentTeams 缺失能力，可回馈上游"；Phase 1 用固定 warm pool 不宣称动态扩缩 |
| T3 | 触发 | 事件驱动 + 轮询兜底，统一经 Case Controller **inbox 去重**后立案 |
| T4 | 归因 | 两段式 + **严格实验协议**（§2.3.2）：5-cell 最小矩阵，Δ效应量+95%CI，ATTRIBUTED/INCONCLUSIVE/CONFOUNDED 三态裁决；废除"置信≥0.8"未定义指标 |
| T5 | 修复 | 三通道 + 自由起草；修复产物=**不可变 WorkOrder**（hash 绑定目标/输入版本/diff/门禁报告/expiry/nonce），审批即批此 WorkOrder 防掉包 |
| T6 | 评测门禁 | 双轨（规则+LLM 裁判，裁判模型≠运动员模型）；contract/replay 确定性测试与 live-provider E2E **分开报告** |
| T7 | IM 拓扑 | 飞书对外，Matrix 对内，对外动作双向留痕 |
| T8 | 信任账本 | **risk_class × autonomy_state 二维拆分**（§2.3.4）；Wilson 双侧口径、evidence epoch 原始整数计数、一次动作=一个样本；MVP 演示"记账但拒绝晋升"（3/3 下界 0.44<0.9） |
| T9 | 存储观测 | 案例库 pgvector（PolarDB 同接口）；**审计权威源=数据库**，audit.jsonl 仅导出物；OTel→AgentLoop/LoongSuite |
| T10 | 创新轴节奏 | I1 MVP 兑现（记账+拒绝晋升）；I2 巡检 MVP 兑现单次版；I3 Skill 演化 Phase 2 兑现到"候选+holdout 回放+人工批准"（供应链未完备前不自动上架） |

## 2. 终态系统蓝图

### 2.1 责任边界（控制面/执行面分离）

```
飞书/Webhook/Poll → [Case Controller]  ← 非 LLM：状态/租约/幂等/outbox（唯一事实源）
                        ↓ 派单
              [AgentTeams 协作执行层] 4 常设 + 弹性 Worker（可替换适配层）
                        ↓
              [隔离 Experiment Runner] → [Eval/Gate]
                        ↓
              [WorkOrder + ApprovalGrant]（不可变，hash 绑定）
                        ↓
              [Release Controller]     ← 非 LLM：唯一可调 Quality API 写面
                        ↓
              [Quality API: VersionSet/Canary/Rollback]
  [Caseload Controller] → AgentTeams（受限 RBAC）
  [Trust/Audit/Evidence] ← 全程事件回流
```

**叙事纪律（比赛策略，书面与口头统一执行）**：所有对外材料前半场统一口径为"以 AgentTeams 为协同设计基点深度映射（Team CR/SOUL/声明式 MCP/Matrix 留痕/动态 Worker 管理），契约化集成使替换仅作灾备设计"——"可替换适配层"字样不出现在任何材料的前半场，仅作为工程成熟度的后段加分点。PPT 必须先充分展示 AgentTeams 逐项映射，再讲架构解耦。

### 2.2 Agent 组织

常设 4（质量官/采集员/守门员/案例官）+ 弹性 2 类（归因师/修复师）。**质量官不再"维护状态机"**——它是 Case Controller 的领单 Worker：控制面持有全部权威状态，Agent 只产出建议与产物。扩缩容统一口径为"**Agent 申请、控制面决策执行**"：质量官根据 caseload 向 Caseload Controller 申请扩缩容，Controller 按 `desired=clamp(minWarm, budgetCap, ⌈active_leases/concurrency⌉+⌈ready_cases·p95/drain_horizon⌉)` 决策并执行（对外材料禁用"质量官现场创建"的简化说法）。缩容必须经 `DRAINING→停止新 claim→lease=0→outbox 清空→摘出 Team→Sleep/Delete→资源凭证对账`（AgentTeams v1.2.1 删除失败部分 non-fatal，不能以 CR 消失为成功依据）。

**Agent 冲突仲裁规则**（评委追问点）：Agent 之间结论冲突时不靠投票不靠资历——①守门员对"是否放行"有一票否决权，优先于任何其他 Agent 的结论；②归因置信不足（INCONCLUSIVE/CONFOUNDED）时不得进入修复阶段，补实验或升级人工；③一切分歧以 Case Controller 的权威状态与实验数据为最终裁决依据，LLM 结论永远只是"建议"。

### 2.3 核心机制

1. **Quality API 契约（强化）**：不可变 `VersionSet{prompt, KB manifest, model+params}`；`draft → stage → canary → promote | rollback → status`；写面支持 `If-Match/expected_revision`、idempotency-key、异步结果查询；**仅 Release Controller 可调写面**；另两接口 `GET /logs`、`GET /feedback` 不变。契约以 OpenAPI 冻结，附 conformance suite。
2. **归因实验协议**：最小矩阵 `C=(P1,K1,M1)`、`RP=(P0,K1,M1)`、`RK=(P1,K0,M1)`、`RM=(P1,K1,M0)`、`G=(P0,K0,M0)`；多臂同恢复或均不恢复→扩 2³ 全因子识别交互。探针提前冻结，分 discovery/hidden confirmation；含 unaffected controls、随机臂序、重复调用、完整版本 digest。输出 Δ效应量+95%CI+三态裁决；INCONCLUSIVE→补实验/升级人工，CONFOUNDED→强制全因子。
3. **修复与发布**：三通道（prompt git 化/KB 修订/模型参数切换）；修复师自由起草，产物为不可变 WorkOrder；单变量纪律；Release Controller 执行灰度/验证/全量/回滚，全部经 CAS revision。
4. **信任账本（修正版）**：
   ```
   risk_class      = R0_READ | R1_REVERSIBLE_WRITE | R2_HIGH_IMPACT
   autonomy_state  = MANUAL | ELIGIBLE | AWAITING_CONFIRMATION |
                     AUTO_ENABLED | SUSPENDED | BLOCKED_UNKNOWN
   ```
   仅白名单内 R1 动作可晋升 AUTO_ENABLED；R2 永远逐次审批；验证失败→SUSPENDED+冷却。晋升判据：当前 evidence epoch 的 Wilson **双侧** 95% 下界 >0.9（一次动作中的多条探针只算一个样本）；系统攒够证据→飞书带证据表提请→人确认生效。MVP 演示口径：**记账但拒绝晋升**。
5. **七个子状态机**：Case / Experiment / ChangeSet / Eval / Release / Notification / Trust，各自含失败语义（重复与合并、审批拒绝/过期、Worker 丢失、发布 UNKNOWN→reconcile、回滚失败、通知失败、人工接管）。
6. **评测门禁**：规则轨+裁判轨；门禁报告作为 WorkOrder 的必要附件；确定性 contract/replay 测试与 live E2E 分离报告。
7. **变异巡检**：变异算子库→探测用例→周期攻击→质量周报（飞书）+自评分趋势（检出率/归因准确率/门禁拦截率/一次通过率）。
8. **能力变更工程化（eval 驱动的自进化 + 可插拔）**：Skill、MCP 工具、归因规则等 **Agent 自身能力的变更，与应用修复走同一条 eval 门禁管道**：候选起草（案例官）→ 历史 badcase 时序 holdout 回放 → **人工批准**上架 → 能力注册中心统一版本化（版本/发布/回滚/质量评估四要素内建）→ 声明式挂载/卸载（可插拔）。叙事核心：**门禁不只考应用，也考 Agent 自己——一切变更皆候选，一切上架必经考**。自动上架须待供应链完备（source commit、license、digest、权限、SBOM、签名、撤销机制）。
9. **安全合规**：PII 入口脱敏；审批绑定 WorkOrder hash+nonce+expiry（防重放）；审计入 DB 权威存储、失败即拒业务（不放行）；Higress 凭证托管。**zeroops 的 common/approval.py（静态 token 可重放）与 audit.py（失败放行）不直接沿用，重写仅参考。**

## 3. 组件清单

| 组件 | 内容 |
|------|------|
| `demo-app/` | 小智客服（FastAPI RAG，prompt git 版本化，pgvector KB，LLM 真实调用 StepFun，反馈端点，OTel，Quality API v2 实现，B1–B4 注入端点） |
| `control-plane/` | **Case Controller**（PG aggregate/event/inbox/outbox、CAS、lease、fencing token、UNKNOWN→reconcile）、**Release Controller**、**Caseload Controller**（Phase 2）、Experiment Runner |
| `eval-harness/` | 回归评测集、双轨跑分、对照实验执行器（§2.3.2 协议）、变异巡检器、质量周报 |
| `mcp-servers/` | 5 MCP + trust-ledger 模块 + 重写的 common（审批防重放、审计失败即拒） |
| `agents/` | team.yaml（4 常设 CR + 弹性模板 + Team/Human CR）+ 6 SOUL.md + 安装 runbook；钉 AgentTeams **v1.2.1** |
| `contracts/` | Quality API OpenAPI、事件/状态定义、WorkOrder/Approval schema、Evidence Bundle schema、B1–B4 ground-truth fixtures、Wilson 测试向量、conformance suite |
| `casebase/` | pgvector schema、种子数据、入库/检索工具 |
| `deploy/` | docker-compose（app+pgvector+mcp+feishu mock）；AgentTeams 本地/Helm |
| `docs/` | 本方案、PRD、skills、mcp-contracts、agent-identity、competition/（过程材料） |
| 仓库卫生 | **git 仓库 Day 1、Apache-2.0 LICENSE、依赖锁定、evidence/ 目录**；zeroops 文档显式标记 superseded |

## 4. 分阶段推进路线

**Phase 0A · Build-Go Spike（8/8 前）**：建 git 仓库+LICENSE+依赖锁+evidence 目录；钉 AgentTeams v1.2.1/commit；真机跑通 3 Agent+1 Skill+1 MCP+Matrix artifact 交接+Worker sleep/wake+重启恢复。出口：Spike 证据（日志/截图），摸清本机 `dev` controller 兼容性。

**Phase 0B · 机器可验契约（8/8–8/9）**：冻结 contracts/ 全部内容；PRD/spec 定稿；zeroops 文档标 superseded。出口：conformance suite 可对空实现跑红。

**Phase 1 · 一条可信 B1 纵切（8/9–8/16，对应初赛）**：demo-app+eval-harness；Case/Release Controller；5 MCP+trust-ledger；agents/ 定义；mcp-feishu 最小三能力（用户飞书凭证为前置）；**固定 warm pool**。证据：B1 全闭环（含飞书原群回复）、单层归因实验报告（Δ+CI+裁决）、门禁/灰度/回滚、信任记账但拒绝晋升、单次巡检周报；contract/replay 与 live E2E 分开报告；初赛三件套。出口：e2e 全绿+初赛提交。

**Phase 2 · 横向证明（8/16–9/3，对应复赛）**：B2–B4 全场景；第二个极小 Quality API 适配器（证明可复制性）；真实 create/drain/remove（Caseload Controller）；周期巡检；Skill 候选+holdout 回放+人工批准；复赛提交。出口：可运行 Demo+评测报告。

**Phase 3 · 决赛集成与硬化（9/3–9/22）**：LoongSuite/PolarDB；故障演练；SLO；备份恢复；安全与租户隔离。**不宣称"生产完成"**。出口：决赛演示就绪。

**Phase 4 · 真实 SaaS pilot**：第二租户、真实回滚、数据删除/SLO 达标后才给 Production Go。

## 5. 依赖与风险

- LLM Key：StepFun（Kimi workspace ACLteam 环境），构建时接入 Higress；演示确定性靠 temperature=0+冻结探针集。
- 飞书自建应用凭证：Phase 1 前置（用户操作）。
- 外部未知：双模型（运动员/裁判）额度、飞书权限、LoongSuite/PolarDB 配额、本机 `dev` controller 与 v1.2.1 兼容性——0A Spike 负责扫雷。
- 9 天窗口：0A+0B+Phase 1 紧张；如挤压，优先保"契约冻结+B1 纵切证据"，PPT 承载完整愿景。

## 6. 验证

- conformance suite（契约级，确定性）+ MCP 冒烟 + e2e 全闭环断言（归因裁决=ATTRIBUTED 且故障层=prompt、门禁、灰度、回复原群、归档）。
- 信任账本：Wilson 测试向量全过；三轮 B1 后断言**拒绝晋升**事件（下界 0.44<0.9）。
- 巡检：周报含变异用例数/检出率/门禁拦截率。

## 7. 评审对齐必含（初赛 PPT/简介与 spec 文档的强制内容清单）

> 来源：主办方意图符合度评估（加权 7.3/10，Skill 维度最弱）。以下全部为纯叙事工作，必须在初赛材料中兑现。

1. **Skill 九要素清单表**：8 个 Skill（采集分诊/启发式归因/对照实验/修复原语/评测门禁/案例沉淀/变异巡检/信任管理）逐行填：输入输出、调用条件、依赖工具、失败处理、安全边界、复用价值、版本/发布/回滚/质量评估、与协同流程关系——官方评审补充明示这是核验重点。
2. **AgentTeams 五要素逐项映射表**：角色编排→Team CR+SOUL、任务拆解→控制面领单派单、上下文传递→MinIO artifact+Matrix 引用、协同执行→声明式 MCP+Higress 凭证、状态追踪→CR status+房间置顶；附 §2.2 冲突仲裁规则。
3. **未采用工具交代段**：Nacos/RocketMQ/云 Skills/ModelScope 各一句"替换原因+接口兼容+迁移成本"（如 PG outbox 替代 RocketMQ 的取舍）；StepFun 选型主动解释为"刻意异构证明治理层模型无关，可一键切 Qwen"；复赛前置：接入至少 1 个阿里云官方云 Skill、把 Caseload Controller 整理为给 AgentTeams 的 issue/PR 计划写入开源计划。
4. **量化价值与长期叙事**：补 MTTR/人力基线数字（行业数据或自测）；一段杭州落地/长期计划（呼应生态沉淀意图）；"区别于方向二智能客服"一句话（我们是 AI 应用质量治理 meta 层，不是客服工单系统）。
5. **评委挑战应对口径**：注入故障（B1–B4）定位为"可重复演示+ground-truth 使归因结论可机器验证"，并补真实 badcase 语料来源背书；"拒绝晋升"备答"autonomy 是光谱，R1 白名单晋升路径已设计，拒绝是统计纪律的证据"；飞书凭证若卡住，feishu mock 为明示的降级演示路径。
