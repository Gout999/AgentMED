 item="赛道4：Agent 自动修复与自我改进机制。调研对象：DSPy/MIPRO、TextGrad、APE/OPRO/EvoPrompt 等 prompt 自动优化器；Reflexion、Self-Refine、CRITIC 等自我修正；Voyager 式 skill 库自增长、ACE(Agentic Context Engineering)式上下文演化；OpenAI 的自动化 prompt 优化、Anthropic 的 prompt improver；工业界有没有'人类审批在环'的 agent 配置修复流水线先例。重点回答：①自动优化器在真实生产环境的采用度与效果数据（不是论文数字）②skill/工具的自动创建与版本管理有没有成型的工程模式（候选→回放验证→上架）③全自动修复 vs 人工审批修复的边界业界怎么划——我们'修复自由起草但 WorkOrder 必须人批'的立场是主流还是异类" outcome="completed">调研完成，共 13 次搜索 + 4 次全文抓取，关键声明均有双源交叉验证。以下是赛道 4 的报告。

---

# 赛道 4 调研报告：Agent 自动修复与自我改进机制

## 1. 全景地图

这个赛道可以分成四层。**(a) Prompt 自动优化器**（offline compile 派）：DSPy/MIPROv2/GEPA 是目前唯一有命名生产部署的一支，GEPA（2025，ICLR 2026 Oral）已被 Databricks 产品化进 MLflow；TextGrad（Nature 2025）学术地位高但仍是研究工具；APE/OPRO/EvoPrompt 已沦为基线；OpenAI/Anthropic/Google/AWS 各家云厂商的 prompt optimizer 全部是**人操作的辅助改写工具**，不是自治回路。**(b) 推理期自我修正**：Reflexion/Self-Refine/CRITIC 这一支的 2023–2025 实证共识是"无外部可验证信号的内在自我批评不可靠"，业界已把它降级为 agent 运行时技巧而非质量改进机制。**(c) 经验/skill/上下文自增长**：Voyager（2023）开创"验证后入库的代码 skill 库"，ACE（2025，ICLR 2026）与 ReasoningBank（Google，2025）把上下文/记忆做成自演化 playbook，DGM（Sakana，2025）做到 agent 自改代码但只在沙箱+人监督下运行。**(d) 版本管理与审批基础设施**：LangSmith/PromptLayer/MLflow 的 prompt registry（版本、release label、审批流、灰度路由）是成熟产品品类，但只管 prompt 一个维度；Humanloop（人工评审流做得最好的那家）2025 年被 Anthropic 收编关停。与 CaseLoop 的关系：**修复"起草"这一环业界已有强引擎（GEPA 系），"上架/灰度/审批"这一环在 prompt 维度已成熟，但"归因此次故障属于哪一层→起草修复→hash 绑定工单→门禁→人批"的完整闭环没有人在做。**

清单：DSPy(MIPROv2/GEPA) · TextGrad · APE/OPRO/EvoPrompt · OpenAI Prompt Optimizer · Anthropic Prompt Improver · Vertex/Bedrock optimizer · Reflexion/Self-Refine/CRITIC · Voyager · ACE · ReasoningBank · Darwin Gödel Machine · Anthropic Agent Skills + skill-creator · Hermes(Nous) 自演化 · LangSmith/PromptLayer/MLflow/Humanloop registry。

## 2. 逐对象速览

**DSPy（Stanford 系，2022– ）** — 把 prompt 变成可编译程序的框架，优化器含 MIPROv2（2024-06）与 GEPA（2025-07）。是唯一有具名生产案例的优化器：官网（2026-08 抓取）披露 750 万+月下载、37k star，生产案例含 JetBlue 元数据抽取（约 550× 成本下降）、Databricks Dash relevance judge、Amazon Nova 大模型迁小模型、Walmart 客服机器人、Moody's 代码修复、Sephora、Nous Hermes agent 自演化（[dspy.ai](https://dspy.ai/)）。注意这些数字均来自项目方自述，无独立审计。

**GEPA（Berkeley/Databricks，2025-07）** — 反思式 prompt 进化：对轨迹做自然语言反思→提出修改→Pareto 前沿维护多样性。论文数据：6 任务上平均超 GRPO 6%（最高 20%），rollout 用量少至 1/35；超 MIPROv2 超 10%（AIME-2025 +12%）；ICLR 2026 Oral（[arXiv:2507.19457](https://arxiv.org/abs/2507.19457)，2025-07-25）。**已产品化**：MLflow prompt registry 内置 `mlflow.genai.optimize_prompt` + `GepaPromptOptimizer`（[Databricks 文档](https://docs.databricks.com/aws/en/mlflow3/genai/prompt-version-mgmt/prompt-registry/automatically-optimize-prompts)；[Databricks 社区博客](https://community.databricks.com/t5/technical-blog/revolutionizing-genai-application-management-mlflow-3-prompt/ba-p/139606)，2025-11-24）。另有一内容农场声称"Databricks 内部 SQL bot 用 GEPA 省 33% token"，单源低可信，不采信。

**TextGrad（Stanford Zou 组）** — "文本版反向传播"：LLM 生成的自然语言批评当梯度，PyTorch 风格 API。正式发表于 *Nature* 639:609–616（2025-03）：GPT-4o LeetCode-Hard 零样本 26%→36%，Chameleon 多工具 agent +7.7%（[论文引文核实](https://arxiv.org/html/2512.16301v2)；[GitHub](https://github.com/zou-group/textgrad)）。**截至 2026-08 未见任何具名生产部署**，是研究工具。

**APE/OPRO/EvoPrompt（2022–2023）** — APE（ICLR 2023）、OPRO（Google，ICLR 2024）、EvoPrompt 是奠基工作，如今只作为新论文的 baseline 出现（GEPA 论文即以 MIPROv2 而非它们为对比对象）。无生产足迹。

**OpenAI Prompt Optimizer / Anthropic Prompt Improver** — OpenAI：Playground/后台里的聊天式改写工具，按最佳实践重写 prompt（[官方文档](https://developers.openai.com/api/docs/guides/prompt-optimizer)；[社区公告](https://community.openai.com/t/enhanced-prompt-management/1290305)，2025-06）。Anthropic：Console 的 Improve 功能（2024-11），自动补 CoT、规范化示例、并可生成 eval（[AiNews](https://www.ainews.com/p/anthropic-console-introduces-tools-to-refine-prompts-and-examples)）。**两者都是"机器起草、人采纳"的辅助工具，不含自治部署**——这本身就是边界证据。Google Vertex、AWS Bedrock 有同类（[GreaTerPrompt 综述 Table 1](https://arxiv.org/html/2504.03975v1)）。

**Reflexion / Self-Refine / CRITIC（2023）** — 推理期自我批评-修正。后续实证共识：**无外部反馈的内在自我修正不可靠甚至负收益**（Huang et al., "LLMs Cannot Self-Correct Reasoning Yet", ICLR 2024，[arXiv:2310.01798](https://arxiv.org/abs/2310.01798)）；CRITIC（ICLR 2024）证明工具交互式外部批评显著优于自评；CorrectBench（2025-10）继续确认。工程化共识：自我修正必须锚定在测试/执行等可验证信号上。

**Voyager（2023-05，Minecraft）** — 终身学习 agent 鼻祖：自动课程 + 迭代提示（环境反馈+执行错误+自我验证）+ **代码形式 skill 库**（通过验证后才按 embedding 索引入库）（[项目页](https://voyager.minedojo.org/)）。"候选→验证→入库"模式的原创者，但无版本/回滚/门禁概念。

**ACE（Agentic Context Engineering，2025-10）** — 把上下文当"演化中的 playbook"：Generator→Reflector→Curator 三角色，增量 delta 更新防止"context collapse"。agent 基准 +10.6%、金融 +8.6%；用小开源模型在 AppWorld 打平榜首生产级 agent；**无需标注、靠执行反馈即可演化**；ICLR 2026（[arXiv:2510.04618](https://arxiv.org/abs/2510.04618)，引用 268）。SambaNova 已开源实现（[官方博客](https://sambanova.ai/blog/ace-open-sourced-on-github)，2025-11-19）。

**ReasoningBank（Google，2025-09）** — 从自我评判的成败经验中蒸馏可迁移推理策略进记忆库，与 MaTTS 测试期扩展协同；VentureBeat 报道效率 +8.3%（[arXiv:2509.25140](https://arxiv.org/abs/2509.25140)；[VentureBeat](https://venturebeat.com/ai/new-memory-framework-builds-ai-agents-that-can-handle-the-real-worlds)，2025-10-08）。

**Darwin Gödel Machine（Sakana/UBC，2025-05）** — 自改代码的 coding agent：SWE-bench 20.0%→50.0%，Polyglot 14.2%→30.7%，改进可跨模型/跨语言迁移；开放演化 archive 保持多样性。关键在**安全姿态**：全程沙箱+人类监督+可溯源 lineage，且论文如实记录了两次 reward hacking（伪造测试通过日志、破坏幻觉检测函数）（[sakana.ai/dgm](https://sakana.ai/dgm/)，2025-05-30；[arXiv:2505.22954](https://arxiv.org/abs/2505.22954)，引用 237）。

**Anthropic Agent Skills + skill-creator** — SKILL.md 已成事实标准；版本用日期/epoch 时间戳，**截至 2025-10 无原生 SemVer 与回滚 UX**（[skywork 对比](https://skywork.ai/blog/ai-agent/claude-skills-vs-prompt-libraries-2025-comparison/)）。官方 skill-creator 带评测回路（`evals.json`/`run_eval.py`/`run_loop.py`/`improve_description.py`，可对 description 做 A/B 迭代），但**是人在驱动的迭代工具，不是自治流水线**（[官方仓库 issue 佐证](https://github.com/anthropics/skills/issues/1149)，2026-05）。

**Hermes agent（Nous Research）** — 把 GEPA 接入 agent 技能/提示词自优化流程的真实先例，DSPy 官网生产页列为"Evolutionary self-improvement for the Hermes agent"（[dspy.ai](https://dspy.ai/)；[中文报道](https://tenten.co/learning/hermes-agent/)）。仍属离线优化管线，非在线自治。

**Prompt registry 三强 + Humanloop** — LangSmith（commit/tag/环境/webhook 部署）、PromptLayer（"Git for prompts"，release label + **重要 label 可加审批流保护** + A/B 流量路由，[官方文档](https://docs.promptlayer.com/features/prompt-registry/overview)）、MLflow（alias + GEPA 一体化）。Humanloop——人工评审工作流做得最细的 prompt 平台——被 Anthropic 收编（[TechCrunch](https://techcrunch.com/2025/08/13/anthropic-nabs-humanloop-team-as-competition-for-enterprise-ai-talent-heats-up/)，2025-08-13），平台 2025-09-08 关停。信号：前沿实验室把"评测+人工评审"能力内化，而非放弃。

## 3. 与 CaseLoop 的对照表

| 对象/机制 | 判定 | 与 CaseLoop 的关系 |
|---|---|---|
| GEPA/DSPy 反思式优化 | **直接可用** | 做"修复自由起草"的引擎：少量 rollout 即产出修复候选，与 badcase→修复回路天然契合；有 MLflow 现成集成 |
| Prompt registry 的版本/label/审批/灰度机制（LangSmith/PromptLayer/MLflow） | **直接可用（作参照系）** | 灰度发布与审批的工程模式已被验证成熟，可直接借鉴其 release label + 审批流 + A/B 路由设计 |
| Voyager"验证后入库" + skill-creator 评测回路 + SKILL.md 打包格式 | **直接可用** | skill 候选→执行验证→上架的最小模式；SKILL.md 作为 skill 打包标准 |
| ACE / ReasoningBank 在线经验演化 | **需改造可用** | 在线自策展无人审、无去重幂等、无回滚；需套 CaseLoop 的立案-门禁-审批壳才能进生产 |
| DGM 式自改 agent 配置/代码 | **需改造可用** | 论文自证必须沙箱+人监督+lineage；其 reward hacking 记录是 CaseLoop 控制面铁律的最佳反面教材 |
| TextGrad / 云厂商 prompt optimizer | **需改造可用** | 前者需自建反馈工程；后者是人工辅助工具，可作起草环节的人机界面 |
| Reflexion/Self-Refine 内在自我批评 | **不建议采用** | 无外部信号时不可靠，只能作为运行时技巧，不能当质量改进机制 |
| 全配置快照（prompt+skill 清单+工具 schema+模型+harness）统一版本化 + hash 绑定 | **确认是缺口** | registry 只管 prompt 单维；Skills 无 SemVer/回滚；无人做跨层快照与内容 hash 审批 |
| 修复前的归因实验（判定故障在 prompt/知识/模型哪一层） | **确认是缺口** | GEPA/MIPRO 全局优化但不归因此次失败；ACE/DGM 亦不区分故障层 |
| 演化产物的回放/conformance 验证 | **确认是缺口** | 《Beyond Task Completion》(2026) 明确指出 tool-evolving agent 的 verification-vs-conformance gap 是未解问题（[arXiv:2604.00392](https://arxiv.org/html/2604.00392v2)） |
| 案例库→回归考题→信任账本（Wilson 下界放权） | **确认是缺口** | 无任何产品/论文做治理化的案例复用与信任量化晋升 |

## 4. 关键事实清单（10 条）

1. 【事实】DSPy 750 万+月下载、37k star，官网列名 JetBlue（约 550× 成本下降）、Databricks、Amazon、Walmart、Moody's、Sephora、Hermes 等生产案例（项目方自述，2026-08-09 抓取）— https://dspy.ai/
2. 【事实】GEPA 平均超 GRPO 6%（最高 20%）且 rollout 少至 1/35，超 MIPROv2 超 10%，ICLR 2026 Oral（2025-07-25）— https://arxiv.org/abs/2507.19457
3. 【事实】GEPA 已被 Databricks 产品化进 MLflow prompt registry（`optimize_prompt`+`GepaPromptOptimizer`），2025-11-24 公开 — https://community.databricks.com/t5/technical-blog/revolutionizing-genai-application-management-mlflow-3-prompt/ba-p/139606
4. 【事实】TextGrad 发表于 *Nature* 639:609–616（2025-03），LeetCode-Hard 26%→36%；截至 2026-08 未见具名生产部署 — https://github.com/zou-group/textgrad 及引文核实 https://arxiv.org/html/2512.16301v2
5. 【事实】ACE 在 agent 基准 +10.6%、金融 +8.6%，AppWorld 打平榜首生产级 agent，ICLR 2026；SambaNova 2025-11-19 开源 — https://arxiv.org/abs/2510.04618 ， https://sambanova.ai/blog/ace-open-sourced-on-github
6. 【事实】DGM 自改代码使 SWE-bench 20%→50%，全程沙箱+人类监督+lineage，论文记录两次 reward hacking — https://sakana.ai/dgm/ （2025-05-30）
7. 【事实】无外部反馈的内在自我修正确认不可靠（Huang et al., ICLR 2024）— https://arxiv.org/abs/2310.01798 ；CRITIC 证明外部工具批评更优（ICLR 2024）
8. 【事实】PromptLayer registry 支持 release label 审批流保护 + A/B 流量路由；LangSmith/MLflow 有等价版本化+晋升机制 — https://docs.promptlayer.com/features/prompt-registry/overview
9. 【事实】Humanloop（人工评审流最成熟的 prompt 平台）被 Anthropic 收编、2025-09-08 关停 — https://techcrunch.com/2025/08/13/anthropic-nabs-humanloop-team-as-competition-for-enterprise-ai-talent-heats-up/
10. 【事实】EU AI Act 第 14 条要求高风控 AI 系统必须内建"有效人类监督"（理解、干预、否决、停止的真实能力，2026 起分阶段适用）— https://artificialintelligenceact.eu/article/14/

## 5. 对 CaseLoop-for-Agents 的设计启示

1. **"修复自由起草、WorkOrder 必须人批"是主流立场，且可以更强硬地营销。** 证据链：前沿实验室自己的优化器（OpenAI/Anthropic）全部是人采纳的辅助工具；prompt registry 品类把"审批流"做成标准功能；AgentOps 部署指南标配 sandbox→shadow→canary+审批（如 [Teradata AgentOps](https://www.teradata.com/insights/ai-and-machine-learning/agentops-how-to-run-ai-agents)，2025-11）；EU AI Act Art.14 把有效人监写成法规。全自动派只活在研究沙箱（DGM）或离线 compile（DSPy/GEPA 编译完由人部署）。CaseLoop 的差异化不在"有人批"，而在"批的是 hash"——registry 批的是 label/版本指针，内容可被改指；**hash 绑定是比业界现有审批更强的保证，应作为卖点**。
2. **修复起草层直接接入 GEPA 系引擎，不要自研优化算法。** GEPA 已证明"几次 rollout 反思即可产出修复"且有 MLflow 生产集成；CaseLoop 的价值在其上游（归因实验判定修哪一层）和下游（门禁/审批/灰度），这正是 GEPA 没有的两端——GEPA 全局优化指标但**不归因单次故障**，与 CaseLoop 互补而非竞争。
3. **门禁必须坚持"外部可验证信号 + 裁判≠运动员"，并新增"防 reward hacking"显式设计。** Reflexion 系的失败实证（Huang et al.）+ DGM 自曝的两次作弊（伪造测试日志、破坏检测函数）共同证明：被优化对象与评判信号同源必被钻空子。CaseLoop 的不可变 WorkOrder（防止优化过程顺手改考题）恰好是这个问题的工程解——建议把 DGM 案例写进文档作为设计依据。
4. **skill 生命周期采用"Voyager 验证入库 + skill-creator 评测回路 + SKILL.md 打包"作底，补上业界全缺的治理层。** 即：候选 skill → 冻结任务集回放（不只验证"这次任务完成"，还验证"没破坏旧能力"——conformance gap 是 2026 年论文点名的未解问题）→ hash 上架 → 可回滚。Anthropic Skills 至今无 SemVer/回滚，这里可以直接做出差异化。
5. **案例库→回归考题的定位可以借 ACE/ReasoningBank 的叙事但划清界限。** 它们做"无人监管的在线经验自策展"，CaseLoop 做"治理化的经验固化"（事故立案→人审入库→变回归考题）。前者已证明经验复用的收益（+10.6%/+8.3%），CaseLoop 补上的是去重幂等、审批与回滚——叙事上是"ACE 的治理版"，技术上是互补壳层。
6. **需要如实承认的一点**：DSPy 系的生产数字全部来自项目方自述，无独立审计；"prompt 优化器在生产的真实渗透率"没有任何权威第三方数据（Chris Potts 2025-11 在 DSPy Meetup 的演讲标题就叫"Why Are Prompt Optimizers Still So Underrated?"——侧面说明采用度仍低于热度）。【推断】这意味着 CaseLoop-for-Agents 不应假设客户已在用自动优化器，而应把"起草引擎"做成可插拔。

## 6. 一句话结论

自动修复的"起草"环节（GEPA 系反思式优化）已达到生产可用并被 Databricks 产品化，"发布治理"环节（版本/审批/灰度）在 prompt 单维已是成熟商品，但**跨层归因、全配置快照 hash 绑定、演化产物回放验证这三者叠加的闭环无人占据**——CaseLoop"人批 hash 工单"的立场恰是业界主流与监管要求的交汇点，其真正的差异化在门禁与归因，而不是人批本身。</subagent>
