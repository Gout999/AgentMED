# 比赛组件映射（评委核验清单应答纸）

> **历史比赛材料（2026-08-09）**：本文只说明当时的客服演示映射，不是当前 V5 产品、
> runtime 或完成状态。当前入口见 [`../README.md`](../README.md)。
>
> 2026-08-09｜一页对齐主办方核验点。每条都钉仓库里的真东西，不编。
> 用途：初赛材料附录 + 答辩速查。主文档：README / `docs/research/agents-synthesis.md`。

## 1. AgentTeams 五要素逐项映射

| 五要素 | 我们的实现 | 仓库证据 |
|---|---|---|
| **角色编排** | 4 常设（质量官/采集员/守门员/案例官）+ 2 弹性（归因师/修复师），Team CR 声明式编排，每人一份 SOUL.md | `agents/team.yaml`、`agents/souls/`（6 份） |
| **任务拆解** | 质量官领单后按案件阶段拆解委派；**拆解规则不进 LLM 自由心证**——控制面状态机约束可行动作 | `control-plane/` case 状态机；`agents/souls/quality-officer.md` |
| **上下文传递** | Matrix 房间消息引用 + 案件 artifact（取证包/实验报告/WorkOrder）持久化后传引用不传全文，房间置顶同步状态 | Matrix 房间 `!sxPUX2qmXTlXmG5WL3`（151 条协作实录在 `evidence/phase1/e2e-t6c-room-log-final.json`） |
| **协同执行** | 声明式 MCP 挂载，**按角色投影工具面**（每个 worker 只见自己 RBAC 允许的工具）；LLM 凭证由 Higress 网关托管，worker 零密钥 | `agents/team.yaml` 各 worker 的 `mcpServers` 段；`mcp-servers/`（5 server） |
| **状态追踪** | 案件/实验/工单/发布状态唯一权威源 = 控制面 PG（事件溯源），房间只读投影；console 可视化 | `control-plane/`；`console/`；`evidence/phase1/console-cases-t6c.png` |

**冲突仲裁规则**（评委常问）：守门员一票否决放行 ＞ 一切；归因置信不足（INCONCLUSIVE/CONFOUNDED）不得进修复；同一 case 租约互斥，重复投诉 inbox 去重只立一案。

## 2. Skill 九要素映射（8 个能力域）

说明：8 个能力域当前以「SOUL 角色 + MCP 工具投影」形态运行，正式 SKILL.md 打包已有样板
（`agents/skills/agentmed-b1-loop/SKILL.md`，全部 worker 引用）。版本/发布/回滚/质量评估
四要素对所有能力域统一适用，见表后。

| 能力域 | 调用条件 | 输入 → 输出 | 依赖工具 | 失败处理 | 安全边界 |
|---|---|---|---|---|---|
| 采集分诊 | 新投诉立案 | 投诉原文+版本观测 → 取证包（digest 分组+证据缺口标注） | mcp-observation（/logs、/feedback、/versions） | 观察异常如实上报，不强扭剧本（T6c-A 实证） | 只读；PII 脱敏；不预设故障层 |
| 启发式归因 | 取证完成 | 取证包 → 归因假设（先验层+实验设计） | 案例库检索（历史 postmortem） | 假设仅是假设，裁决权归实验 | LLM 归因器=举证助手，非裁决者 |
| 对照实验 | 假设成立 | 冻结探针集×双臂版本 → ATTRIBUTED/INCONCLUSIVE/CONFOUNDED+Δ+95%CI | mcp-agentmed-eval（plan/freeze/run/report） | 平台失败如实 BLOCKED（T6c-A 实证）；统计不足即拒 | 探针集平台冻结；实验不可改题 |
| 修复原语 | 实验 ATTRIBUTED | 归因结论 → 修复草稿 → 不可变 WorkOrder（hash 绑定） | mcp-change（changeset/workorder） | digest 必须对账 live 观测（G5 已修）；伪造即拒 | 无生产写权限，只能起草 |
| 评测门禁 | WorkOrder freeze | 回归集+裁判轨 → GateReport（绑工单 hash） | mcp-agentmed-eval、裁判模型（≠运动员） | 任一轨非 passed 即拒；UNKNOWN 不推断成功 | fail-closed；裁判独立校准 |
| 案例沉淀 | 发布完成 | 案件全程 → postmortem 入库+回归考题候选 | mcp-agentmed-casebase（kb.upsert，幂等） | 归档必须 receipt 绑定，无回执不归档 | 入库候选需回放验证才上架 |
| 变异巡检 | 周期触发 | 历史 badcase 变异 → 攻击报告 → 新考题 | mcp-agentmed-eval、案例库 | 变异用例入库同走回放验证 | 只攻击 staging，不碰生产 |
| 信任管理 | 每次动作记账 | 动作结果 → 账本样本 → 晋升/拒绝裁决 | trust ledger（自动记账，PR#1） | 证据不足一律拒绝（3/3 LB=0.4385 实证） | 高风险动作永远逐次人审 |

**统一适用的四要素**：
- **版本**：能力定义随仓库 git 版本化（SOUL/SKILL.md/工具投影同仓 hash）；发布=PR 评审合并
- **回滚**：git revert + 控制面拒绝加载不明 hash 的能力定义
- **质量评估**：能力域每次使用都在信任账本记账（成功/失败原始计数），Wilson 下界量化可信
- **与协同流程关系**：上表 8 域即闭环流水的 8 个工位，衔接由控制面状态机驱动而非 agent 自发

## 3. 未采用工具交代（一段式）

Nacos：配置中心对单机演示过重，prompt/配置版本化用 git+digest 锚定替代，迁移成本=一个 adapter；
RocketMQ：outbox 模式（PG 同事务，PR#1 已实现事务性外发）覆盖演示规模的消息可靠性，RocketMQ 是
规模化后的 outbox relay 替换件而非缺失件；云 Skills：演示期用自研 MCP 工具面保证契约可控，
复赛前置计划接入至少 1 个阿里云官方云 Skill；ModelScope：模型走 StepFun API（异构裁判需要），
本地小模型（裁判校准集/脱敏）预留 ModelScope 接入点。StepFun 选型是刻意异构——证明治理层模型无关，
可一键切 Qwen。

## 4. 量化价值（演示实测口径，非生产统计）

- 三起真实案件从立案到修复实测恢复：**约 45–50 分钟/案**（含人工审批等待与一次纠偏）；
  对照人肉流程行业基准（发现→定位→修复→验证→回复，小时~天级），**MTTR 压缩一个数量级起**。
- 归因准确率：3/3 ATTRIBUTED 全部命中真实注入层（含训练外措辞与未见故障层两起泛化验证）。
- 人力投入：全过程人工动作仅「审批点一次同意」+ 平均 0.3 次纠偏/案。

## 5. 评委挑战应对（速查）

- **"和方向二智能客服的区别"**：客服只是我们治理的第一个应用；我们是 AI 应用质量治理 meta 层，
  第二个被治理对象（agent 本身）见复赛叙事与 `docs/research/agents-synthesis.md`。
- **"注入故障是不是自导自演"**：B1–B4 是"可重复演示+ground-truth 使归因结论可机器验证"的设计；
  泛化用例（训练外措辞/未见故障层）正是为反"剧本复读"质疑而设，证据可第三方复验。
- **"信任分级已有 CSA ATF / Cleric"**：引用背书而非否认——"ATF 把晋升写进标准的同一年，
  我们实现了它的统计内核"。完整话术表：`docs/research/agents-competitors.md` §3。
- **"拒绝晋升是不是功能缺失"**：autonomy 是光谱，R1 白名单晋升路径已设计；拒绝是统计纪律的证据。
