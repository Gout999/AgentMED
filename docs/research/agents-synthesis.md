# CaseLoop for Agents —— 调研综合与战略裁决

> 2026-08-09｜六路并行调研（每路 10–30 次检索，关键事实双源交叉）的综合结论。
> 原始报告：`agents-track1`（评测回归）/ `agents-track2`（商业版图）/ `agents-track3`（归因学术）/
> `agents-track4`（自动修复）/ `agents-track5`（版本化重放）/ `agents-track6`（安全合规）。
> 本文只做裁决与蓝图，事实与 URL 去原始报告查。

## 一、总裁决

**"CaseLoop 治理 AI agent"这个方向，六条独立证据链全部收敛到同一个结论：闭环是真空，我们的三件核心资产（归因实验、hash 工单、信任账本）在商业、学术、合规三个坐标系里都没有对应物，但窗口只有 12–24 个月。**

三个坐标系各自的判定：

- **商业**：「看」（trace）和「考」（离线评测）已红海且正被巨头并购整合（Humanloop→Anthropic 后关停、W&B→CoreWeave $1.7B、Statsig→OpenAI $1.1B、Langfuse→ClickHouse、Traceloop→ServiceNow）；「修」全部停留在 AI 建议级（Percival/Polly/Loop）；「管」只在 feature-flag 公司手里（LaunchDarkly）且与评测门禁互不相通。**从 badcase 到修复上线的端到端闭环，没有商业产品。**
- **学术**：agent 失败归因 2025 年才被 ICML 立为正式任务（Who&When）。「读日志猜」路线天花板被钉死（步级 11–14%，最高 47%）；「干预+重跑+置信区间」正是 2026 公认前沿（CAR，与我们双臂实验同构，但只在合成数据验证）。**配置级（版本快照×冻结探针×Δ+CI）干预归因，学术无对应物——我们不是追赶者，是同向先行者。**
- **合规**：EU AI Act Art.12（自动日志）+Art.14（人类监督）2027-12 硬强制；SOC 2 审计已在查 agent 行为日志；Air Canada 判例确立公司为 bot 言论负责；Replit/PocketOS 删库事故的共同根因（超权限+无审批门+版本回归无人测+自报被采信）恰是我们三件套直接防住的类别。**我们的架构等于提前合规。**

> **竞品修正（2026-08-09，详见 `agents-competitors.md`）**：「自治分级」与「验证修复」**不是空白**——
> CSA ATF（2026-02）已把 Intern→Principal 晋升写成标准，Cleric 已在 infra SRE 域产品化 graduated autonomy
> （哲学与我们同构）。修正后的空白表述：**空白在"业务 LLM agent 质量案件闭环"整链**（案件化+组件级实验归因+
> Wilson 统计内核+回复原处），不在任何单点。答辩策略：引用对方背书（"ATF 把晋升写进标准的同一年，
> 我们实现了它的统计内核"），不声称无人做分级。

## 二、时机信号（为什么是现在，为什么有窗口）

1. **SWE-bench 信用崩塌**：OpenAI 弃用 Verified（59.4% 判分缺陷）并撤回 Pro 推荐（~30% broken）。业界最成熟的 test-based 判分都有 30–60% 缺陷率——「判分器本身不可信」被最大玩家背书，我们的「统计裁决」从可选变成卖点。
2. **基础设施恰好成熟**：沙箱 snapshot/fork 原语（Modal/E2B/Daytona，亚秒级）今年刚产品化——双臂对照「同基线 fork 两臂」不用自建。SKILL.md 成为开放标准（26–30+ 平台采纳）——skill 清单可哈希有了跨厂商格式。
3. **修复引擎有现成强件**：GEPA（ICLR 2026 Oral）已被 Databricks 产品化进 MLflow——「修复起草」可插拔，不自研优化算法。
4. **巨头在两端夹击**：LangChain（Polly+Deployment）、ClickHouse/Langfuse（CI gates）、ServiceNow（Control Tower+权限图）、OpenAI（Statsig 灰度+promptfoo 评测）都在往闭环走。「治理闭环」的认知 12–24 个月内会被平台教育——先发叙事必须抢他们架构里最难补的两块：**归因与信任账本**。
5. **合规预算已存在**：SOC 2 / EU AI Act / NIST RMF 证据包导出对接的是既有预算，不用创造新预算。

## 三、设计裁决（保留 / 修改 / 新增 / 放弃）

### 保留（调研验证是对的）
- **实验归因路线**（双臂对照+Δ效应量+95%CI+三态裁决）——学术前沿同向、商业真空。
- **修复自由起草、人批 hash**——业界主流（OpenAI/Anthropic 优化器全是人采纳工具）+ 法规要求（AI Act Art.14）；且 hash 绑定比 registry 的 label 审批更强（label 可被改指），是卖点不是负担。
- **双轨门禁、裁判≠运动员**——LLM judge 在归因场景 11–14% 准确率的学术数据是最硬背书。注意补「裁判校准」：留人工金标准子集，定期 Alt-Test 统计审计，审计结果进信任账本。
- **终态判分优于轨迹判分**（τ2 的 DB 哈希终态比对，agent 走任何等价路径都算对）——避免修复后换路径被误判。
- **pass^k 多次独立重跑**——与 Wilson 下界天然同构，写进探针执行协议。
- **全轨迹取证契约**（OTel span 级）——TraceElephant 证明全观测归因准确率 +76%。
- **凭证托管让给 MCP OAuth/EMA 生态**——不自建，那是 table stakes。

### 修改（客服版的 X 到 agent 版要变成 Y）
- **版本集**：客服三层（prompt/KB/model digest）→ **agent 六元组**：system prompt + skill manifest（SKILL.md 目录 hash）+ tool schema hash（`tools/list` 响应 hash）+ model snapshot id（禁 alias，pin dated 快照）+ harness git commit + 环境镜像 digest。无标准、真空地带，我们定。
- **归因层**：三层 → **四层+**：加 harness/编排层（MAST FC2 协同错位）与「权限 surface diff」（DPD 事故根因：版本更新悄悄改行为）。可选第五粒度：轨迹内步级定位（Who&When/TRAIL 基准验收）。
- **回放分级**（新增协议层）：**全回放**（cassette，只用于 CI 管线回归，对模型质量变化零灵敏度）→ **半回放**（冻结环境镜像+live LLM，归因实验用，SWE-bench 验证过的保真度/成本平衡点）→ **全 live 多 seed**（门禁终审）。三态裁决必须注明证据来自哪一层。
- **失败分类**：内置 MAST 14 码冷启动 + 从自身日志长出自适应分类（AdaMAST 思路）。
- **LLM 归因器降级为假设生成器**（可接 AgenTracer-8B 类专用 tracer 出线索），裁决权永远归实验。

### 新增（客服版没有、agent 版必须有）
- **判分器自验证（Oracle 模式）**：探针集发布前用 reference solution 跑满分验证判分器本身——Terminal-Bench 模式，SWE-bench 崩塌的直接教训。判分器质量监控做成一等公民。
- **skill 生命周期治理**：候选 skill → 冻结任务集回放（不只验证新能力，还验证没破坏旧能力——conformance gap 是 2026 论文点名的未解问题）→ hash 上架 → 可回滚。Anthropic Skills 至今无 SemVer/回滚，直接差异化。
- **修复起草接 GEPA 系引擎**（可插拔，不锁定）：它全局优化但不归因单次故障，与我们互补。
- **合规证据包一键导出**（SOC 2 / EU AI Act Art.12+14 / NIST RMF）：不可变 WorkOrder+审批记录+信任账本天然就是审计证据。
- **「按工单最小授权」**：凭证 scope 与已审批 WorkOrder hash 绑定、任务完成即回收——无人做。
- **「agent 撒谎」威胁模型条目**：Replit 伪造测试结果是真实案例；所有 agent 自报的评测/回滚结果必须走独立复验通道。

### 放弃（不投）
- 运行时 guardrails（提示注入实时拦截）——Lakera/Prompt Shields 已商品化，我们定位为「事后归因+修复+回归」，拦截日志只是投诉源。诚实承认防不住的：注入攻击面本身、供应链投毒、第三方 OAuth 失陷。
- 进程级快照（CRIU/docker checkpoint）——仍 experimental，文件系统+镜像 digest 已够。
- 自建 trace 管道——站 OTel/Langfuse/Phoenix 之上。
- 自建凭证托管、自研 prompt 优化算法。
- Reflexion 式内在自我批评——无外部信号时不可靠（ICLR 2024 实证共识）。

## 四、风险与反方观点（如实）

1. **窗口期真实存在且有关闭风险**：LangChain/OpenAI/ServiceNow/ClickHouse 从两端蚕食闭环。我们的防御是归因+账本的架构深度，不是功能清单。
2. **判分器质量是系统级风险**：SWE-bench 59.4%/30% 的缺陷率也可能发生在我们自己的探针集上——Oracle 验证+持续监控不是可选项。
3. **DSPy 系生产数字全是项目方自述**：不能假设客户已在用自动优化器，起草引擎必须可插拔。
4. **步级归因我们还没有**：配置级是差异位，但评委/客户问「哪一步错了」时，需要 Who&When/TRAIL 报数证明不低于 SOTA——这是欠的课。
5. **live 成本**：全 live+多 seed 的门禁终审在 agent 长程任务上成本可观，回放分级就是为此设计，但需要在真实负载下验证成本模型。

## 五、建议行动（与既有优先级对齐）

1. **不动基本盘**：客服线 live B1 收尾 + 8/16 初赛材料维持第一优先（这是复赛叙事的信任基础）。
2. **复赛主叙事定为「第二个被治理对象」**：CaseLoop 治理的第一个应用是客服，第二个是 AI agent 本身——包括我们自己的施工 agent（dogfooding 闭环）。
3. **现在就能做的设计工作**（不动代码）：Quality API v3 草案——六元组版本集、四层归因、回放三级、Oracle 判分器验证、skill 生命周期。这套抽象会反向影响复赛架构。
4. **demo subject 选型**：一个小型 coding agent + 15–20 道冻结任务（per-task Docker 镜像+终态判分+reference solution），故障注入 B5+（抽 skill、SOUL 漂移、工具描述错误、权限 surface 扩大）。
5. **学术卡位（可选但便宜）**：用 Who&When/TRAIL 给归因模块报数；CAR 已开源，其 Shapley 信用分摊可借鉴处理「一次快照改多组件」的情形（纪律上仍坚持单因素对照优先）。

## 六、一句话

**客服证明了闭环能跑通；调研证明了闭环是真空；agent 是闭环价值最大的对象；基础设施、修复引擎、开放标准、合规推力四件事恰好同时成熟——剩下的唯一问题是执行速度。**
