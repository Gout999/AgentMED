 item="赛道3：Agent 失败归因的学术研究前沿。调研对象：ICML 2025 及之后关于 multi-agent system failure attribution 的论文（如 Who&amp;When、自动化失败归因基准）、agent 失败分类法（MAST、TRAIL、agent failure mode taxonomies）、LLM-as-judge 做归因的可靠性研究、反事实/对照实验思想在 LLM 评估中的应用（causal inference for LLM eval）。重点回答：①学术界对'agent 失败自动归因'做到什么程度，SOTA 方法是什么、准确率多少、局限是什么 ②失败分类法里哪些类别对应我们的 prompt/skill-context/model 三层，哪些是我们没覆盖的（如规划失败、工具调用格式失败、多轮退化）③有没有人用'对照实验/消融'而不是'读日志猜'做归因——我们的双臂实验+Wilson 置信区间方法在学术坐标系里处于什么位置" outcome="completed">调研完成，共执行 15 次搜索/抓取，覆盖全部必答问题。以下为报告。

---

# 赛道3 调研报告：Agent 失败归因的学术研究前沿

（检索时间：2026 年 8 月；来源以 2025–2026 论文为主）

## 1. 全景地图

这个赛道在 2025 年突然成形，核心事件是 ICML 2025 Spotlight 论文 **Who&When** 首次把"多 agent 系统自动失败归因"定义为独立研究任务并给出基准。此后一年里分化出四条技术路线：**(a) 读日志猜**——LLM judge 直接读轨迹定位责任 agent/步骤（Who&When 自带的三种基线、A2P 脚手架）；**(b) 训练专用归因器**——用反事实重放+故障注入造数据，RL 训练轻量 tracer（AgenTracer-8B）；**(c) 统计/谱分析**——借鉴传统软件工程 spectrum-based fault localization，靠多次执行的方差算"可疑度"（FAMAS）；**(d) 因果干预**——把 agent 运行建成结构因果模型，做 do() 干预+重跑测效应（Causal Agent Replay、Ma et al. 的 Shapley+因果发现）。平行支线是**失败分类法**（MAST、TRAIL、SWE-EVO 失败标注、AdaMAST 自适应分类法）和 **LLM-as-judge 可靠性研究**（大量偏差量化工作）。总体判断：学术 SOTA 的步级归因准确率仍 <50%，"读日志猜"路线已被多篇论文证明不可靠，"干预/重跑做归因"正是 2026 年最前沿方向——与 CaseLoop 的双臂实验思想同构。

关键对象清单：Who&When（基准）、MAST（分类法）、TRAIL（基准+分类法）、TraceElephant（全观测基准，ACL 2026）、AgenTracer（训练式归因器）、FAMAS（谱分析）、A2P（因果脚手架 prompting）、Ma et al.（Shapley+因果发现）、Causal Agent Replay（do 干预归因）、AdaMAST/ATLAS（自适应分类法工具）、SWE-EVO 失败分析（编码 agent 专项）、Behavioral Drivers（编码 agent 大规模轨迹研究）。

## 2. 逐对象速览

**Who&When（ICML 2025 Spotlight，任务定义者+基准）**【事实】
127 个 LLM 多 agent 系统的失败日志，标注责任 agent、决定性错误步骤、自然语言原因。三种基线（all-at-once / step-by-step / binary search）最好成绩：**agent 级 53.5%、步级仅 14.2%**，部分方法低于随机；o1、DeepSeek-R1 也达不到实用水平。被引 174+（截至 2026.8）。[arXiv:2505.00212](https://arxiv.org/abs/2505.00212)（2025.4）；[GitHub](https://github.com/ag2ai/Agents_Failure_Attribution)；[Synced 报道 2025.6](https://syncedreview.com/2025/06/16/researchers-from-psu-and-duke-introduce-multi-agent-systems-automated-failure-attribution/)

**MAST（失败分类法事实标准）**【事实】
Berkeley Cemri et al.，基于 7 个开源 MAS、200 条专家标注轨迹（后续版本扩展至 1,600+ 标注轨迹），Grounded Theory 归纳出 **14 种失败模式、3 大类**：FC1 规约/系统设计问题（违任务规约 10.98%、步骤重复 17.14%、丢失对话历史等）、FC2 agent 间错位（信息截留、忽略他者输入、推理-行动不匹配等）、FC3 验证与终止失败（过早终止 7.82%、无验证、错误验证 6.66%）。标注者一致性 κ=0.88。被引 627+，NeurIPS 2025 poster。[arXiv:2503.13657](https://arxiv.org/abs/2503.13657)（2025.3）；[Berkeley 项目页](https://sky.cs.berkeley.edu/project/mast/)；[NeurIPS 页面](https://neurips.cc/virtual/2025/poster/121528)

**TRAIL（轨迹调试基准，含编码/检索场景）**【事实】
148 条人工标注的 OpenTelemetry 轨迹（1,987 span，575 条含错），覆盖单/多 agent、SWE 与开放检索任务，附带形式化错误分类法。最强模型 Gemini-2.5-Pro 仅 **11%** 准确率——长上下文 LLM 做轨迹调试基本不可用。[arXiv:2505.08638](https://arxiv.org/abs/2505.08638)（2025.5）；数据被 [AgentCompass](https://arxiv.org/html/2509.14647v1) 等工业评估框架复用。

**AgenTracer（训练式归因器，当前最强之一）**【事实】
用**反事实重放+程序化故障注入**自动标注失败轨迹（TracerTraj 数据集），再多粒度 RL 训练 8B tracer。在 Who&When 上比 Gemini-2.5-Pro / Claude-4-Sonnet 高至多 **18.18%**；其归因反馈回灌 MetaGPT/MaAS 带来 4.8–14.2% 任务成功率提升。注意它声明 SOTA 推理 LLM 在其设定下准确率"普遍低于 10%"。[arXiv:2509.03312](https://arxiv.org/abs/2509.03312)（2025.9）

**FAMAS（谱分析路线代表）**【事实】
首个 spectrum-based MAS 失败归因：多次重复执行同一任务，从轨迹变异中用"可疑度公式"（agent 行为组×动作行为组）估计每个动作致败概率，在 Who&When 上胜过全部 12 个基线。思路源自传统软件工程 SBFL——**统计多次运行但不干预**，仍是相关性的。[arXiv:2509.13782](https://arxiv.org/abs/2509.13782)（2025.9）

**Ma et al.（因果推断归因）**【事实】
首个多粒度因果推断框架：性能因果反转原则+Shapley 值分配 agent 级责任；CDC-MAS 因果发现算法定位关键步骤（应对 MAS 数据非平稳性）；归因结果驱动自动优化建议并用反事实仿真验证。Who&When/TRAIL 上步级准确率最高 **36.2%**，优化建议平均提升任务成功率 22.4%。[arXiv:2509.08682](https://arxiv.org/abs/2509.08682)（2025.9）

**A2P（Abduct-Act-Predict 因果脚手架）**【事实】
不做训练，用 Pearl 因果层级（溯因→干预→预测）把归因从模式匹配重构为结构化反事实推理的单次 prompting。Who&When 算法生成子集步级 **47.46%**（基线 16.67% 的 2.85 倍），手工子集 29.31%。证明"引导 LLM 做反事实"比"让 LLM 直接猜"显著好，但仍未过半。[arXiv:2509.10401](https://arxiv.org/abs/2509.10401)（2025.9）

**Causal Agent Replay / CAR（与 CaseLoop 思想最接近的工作）**【事实】
2026.6 预印本：把 agent 运行建成结构因果模型，对某步做 do() 干预、在同一随机策略下向前重跑、测结果分布漂移；定义干预代数、单步对比估计器、预算受限 Monte-Carlo Shapley，**每个效应都报置信区间**。在植入 ground truth 的合成 SCM 上验证（对比估计器能找回枢纽步骤，Shapley 效率总和 0.909 vs 解析解 0.91）。论文明说"LLM-judge 归因是相关性的、不可靠的（Who&When 步级 SOTA 约 14%）"。**局限：只在合成数据上验证，未在真实基准上跑分。**开源。[arXiv:2606.08275](https://arxiv.org/abs/2606.08275)（2026.6）

**TraceElephant / "Seeing the Whole Elephant"（ACL 2026）**【事实】
220 案例基准，核心论点：失败归因应在**全执行可观测性**下研究。完整轨迹（输入+输出+上下文）比仅输出轨迹归因准确率提升**最高 76%**。直接支撑 CaseLoop Quality API 要求完整取证的契约设计。[arXiv:2604.22708](https://arxiv.org/abs/2604.22708)（2026.4）；[GitHub](https://github.com/TraceElephant/TraceElephant)

**Behavioral Drivers（编码 agent 最大规模轨迹研究）**【事实】
19 个 agent（8 框架×14 模型）、500 任务、9,374 条轨迹：①12 个从未被解决的任务只需简单 patch，失败源于架构推理与领域知识缺口；②**"轨迹越长越易失败"在控制任务难度后方向反转**——相关性归因的经典混杂陷阱；③**LLM 是结果与行为的首要驱动因素**，框架 prompt 的影响随模型变强而递减。[arXiv:2604.02547](https://arxiv.org/abs/2604.02547)（2026.4）

**AdaMAST/ATLAS（自适应分类法，MAST 团队后续工具化）**【事实】
从目标系统自身日志学出项目专属失败分类法（冷启动用 MAST 14 码），每条失败码都有日志引文背书，已集成 Claude Code/Codex 做门控反思。论文《Fantastic Adaptive Taxonomies and How to Use Them》。[GitHub](https://github.com/multi-agent-systems-failure-taxonomy/ATLAS)（2026.5）；[AdaMAST 仓库](https://github.com/multi-agent-systems-failure-taxonomy/AdaMAST)（2026.7）

**SWE-EVO 失败标注（编码 agent 专项分类法）**【事实】
对未解决实例用 judge 模型标单一主因：语法错误/工具调用破损、逻辑错误、需求误解、循环、过早放弃；发现**更强模型主要败在指令遵循**（误读 release note 细节）。[arXiv:2512.18470](https://arxiv.org/html/2512.18470v2)（2025.12）

**LLM-as-judge 可靠性（归因场景 vs 偏好场景的巨大落差）**【事实】
偏好排序场景 GPT-4 judge 与人类一致性 >80%，但存在位置偏差、冗长偏差、自偏好（[Zheng et al. 2023](https://arxiv.org/html/2602.13110v3) 引述；[Gu et al. survey](https://arxiv.org/html/2606.30219v2)）；Ye et al. 在 15 个 judge、15 万实例上量化偏差；Shankar et al. 发现"criteria drift"。**而在归因/调试场景，同样技术的准确率崩到 11–14%**（TRAIL、Who&When）。2026.7 预印本 [One Reflex, Two Answers](https://www.preprints.org/manuscript/202607.1745)（未经同行评审，注意）进一步声称模型裁判无法区分"局部化原因 vs 分布式原因"。

## 3. 与 CaseLoop 的对照表

| 对象/思想 | 直接可用 | 需改造可用 | 确认是缺口 |
|---|---|---|---|
| Who&When / TRAIL / TraceElephant 基准 | 作为 CaseLoop-for-Agents 归因模块的**验收考题**（已有 ground truth） | | |
| MAST 14 模式 + TRAIL 错误分类法 | 失败模式词汇表，喂给取证与案例库分类 | | |
| AdaMAST 自适应分类法思路 | | 从"固定分类法"改为"内置 MAST 起步+从自身日志长出专属分类法" | |
| "读日志猜"式 LLM judge 归因 | | 只能当**假设生成器**（举证线索），不能当裁决者——学术数据（11–14%）为 CaseLoop "裁判≠运动员+实验归因"提供了硬证据 | |
| AgenTracer 反事实重放+故障注入 | | 故障注入可用于**探针集构造**（合成已知根因的失败案例，检验归因模块） | |
| 干预式归因（CAR、A2P、Ma et al.） | 思想与双臂实验同构，互为佐证 | | **配置级（版本快照×冻结探针×Δ+CI）干预归因无学术对应物——这是 CaseLoop 的差异位** |
| Wilson 下界信任账本 | | | **无学术对应物**；学术最接近的是 evalstats/PromptStats 的成对统计检验，但没人把统计显著性接到"放权/晋升"治理语义上 |
| 步级归因（哪个 agent/哪一步错了） | | | **CaseLoop 现有三层（prompt/知识库/模型）是配置级归因，agent 场景需要补"轨迹内步级定位"这一粒度** |
| 全轨迹取证（OTel span 级） | TraceElephant 证明全观测 +76%，支撑 Quality API 契约要求完整 trace | | |

## 4. 关键事实清单

1. 学术 SOTA 自动失败归因：agent 级 53.5%、步级 14.2%（Who&When 基线，ICML 2025 Spotlight）——[arXiv:2505.00212](https://arxiv.org/abs/2505.00212)
2. 最强通用 LLM 做轨迹调试仅 11%（TRAIL，Gemini-2.5-Pro）——[arXiv:2505.08638](https://arxiv.org/abs/2505.08638)
3. 当前最高步级归因：A2P 47.46%（AG 子集）、Ma et al. 36.2%——[2509.10401](https://arxiv.org/abs/2509.10401)、[2509.08682](https://arxiv.org/abs/2509.08682)
4. 专用训练的 8B tracer 反超旗舰大模型 18.18%，归因反馈可回灌提升系统 4.8–14.2%——[AgenTracer, arXiv:2509.03312](https://arxiv.org/abs/2509.03312)
5. MAST：14 模式 3 大类，验证类失败（过早终止+无验证+错验证）合计占比可观，κ=0.88，被引 627+——[arXiv:2503.13657](https://arxiv.org/abs/2503.13657)、[Berkeley](https://sky.cs.berkeley.edu/project/mast/)
6. 全观测轨迹比部分观测归因准确率提升最高 76%——[TraceElephant, arXiv:2604.22708](https://arxiv.org/abs/2604.22708)（ACL 2026）
7. 已有人用 do() 干预+重跑+置信区间做归因（CAR），但仅在合成数据验证——[arXiv:2606.08275](https://arxiv.org/abs/2606.08275)
8. 编码 agent 研究中模型层是首要驱动、prompt 层影响随模型变强递减；"轨迹长→失败"是混杂伪相关——[arXiv:2604.02547](https://arxiv.org/abs/2604.02547)
9. LLM judge 在偏好场景与人类一致性 >80%，但有系统性偏差（位置/冗长/自偏好/criteria drift）——[Gu et al. survey 引述](https://arxiv.org/html/2606.30219v2)、[SCOPE 引述](https://arxiv.org/html/2602.13110v3)
10. 编码 agent 失败专项分类：工具调用破损、逻辑错、需求误解、循环、过早放弃；强模型主要败在指令遵循——[SWE-EVO, arXiv:2512.18470](https://arxiv.org/html/2512.18470v2)

## 5. 对 CaseLoop-for-Agents 的设计启示

1. **保留并强化"实验归因"路线——这是学术坐标系里的前沿而非异类。** 2025–2026 文献已达成共识：读日志的 LLM judge 步级归因 11–14%，不可靠；方向正确的做法（A2P、CAR、Ma et al.）全部引入反事实/干预。CaseLoop 的双臂对照+Δ效应量+95% CI 与 CAR 的 do()+重跑+CI 同构，但 CaseLoop 工作在**配置级**（版本快照×冻结探针），学术界做的是**轨迹步级**——粒度互补，且配置级干预归因目前无学术对应物，可作为对外叙事差异点。注意 CAR 开源，若其成熟可直接借鉴其 Shapley 信用分摊处理"一次快照改多个组件"的情形；更稳妥的纪律是坚持单因素对照。
2. **三层归因模型要扩层。** prompt/skill-context/model 三层在 MAST+SWE-EVO 坐标系里大致对应 FC1 规约问题、工具调用格式失败、模型能力层，但漏了：规划失败/任务脱轨、循环与多轮退化（上下文耗尽）、过早终止，以及多 agent 场景的 FC2 协同错位（对应 harness/编排层）。建议版本快照显式纳入 harness/编排配置，失败分类直接内置 MAST 14 码冷启动。
3. **LLM 归因器降级为"举证助手"，裁决权留给实验。** 学术数据给 CaseLoop 既有铁律提供了量化背书；可集成 AgenTracer-8B 这类专用 tracer 生成归因假设（查哪个层、哪个组件），再交给双臂实验证实/证伪——三态裁决的机制不变。
4. **Quality API 契约必须强制全轨迹取证。** TraceElephant 证明全观测带来 +76% 归因准确率；agent 场景的"给日志"应要求 OTel span 级完整 trace（输入+输出+工具 I/O+环境状态），否则归因模块一出生就是半盲。
5. **探针集构造可用故障注入。** AgenTracer 用程序化故障注入批量制造"已知根因的失败"，这正是检验 CaseLoop 归因模块自身的回归考题来源（类似现有客服场景三案的泛化验证，但可规模化合成）。
6. **用学术基准做归因模块的独立验收。** Who&When/TRAIL 是带 ground truth 的现成考题，CaseLoop-for-Agents 若补步级归因能力（轨迹内定位），应在这两个基准上报数，以第三方可复验的方式证明不低于 SOTA。

## 6. 一句话结论

学术界刚把"agent 失败自动归因"立为正式任务一年，读日志路线的天花板已被钉死在步级 ~14–47%，而"干预+重跑+置信区间"正是 2026 年公认的前沿方向——CaseLoop 的实验归因架构在学术坐标系里不是追赶者而是同向先行者，真正要补的课是步级归因粒度、失败分类法扩层（MAST 词汇）和全轨迹取证契约。</subagent>
