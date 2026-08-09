 item="赛道1：Agent 评测与回归测试工具全景。调研对象：SWE-bench/SWE-bench Verified/Multimodal、Terminal-Bench、τ-bench(τ2)、GAIA、OSWorld、WebArena、AgentBench 等任务型 benchmark 的任务构造与判分方式（测试判分 vs LLM judge vs 人工）；Braintrust、promptfoo、DeepEval、Patronus、LangSmith evaluation、Ragas 等工具的回归测试能力（能不能把历史 badcase 变成 CI 里的回归集？怎么判分？数据集版本管理怎么做？）。重点回答：①可重放冻结任务集的工程做法（docker 快照？环境固化？种子数据？）②判分器的可靠性数据（LLM judge 准确率/一致性，test-based 判分的局限）③有没有工具把'线上发现的 badcase 自动固化为回归考题'做成了闭环，还是都要人工整理" outcome="completed">调研完成，共执行 16 次搜索 + 2 次全文抓取，关键声明均已双源交叉验证。以下为赛道 1 报告。

---

# 赛道 1 调研报告：Agent 评测与回归测试工具全景

## 1. 全景地图

这个赛道呈三层结构：**（A）任务型 benchmark**——提供冻结任务集+可执行判分，回答"agent 行不行"，代表是 SWE-bench 家族、Terminal-Bench、τ-bench 系列、OSWorld、WebArena、GAIA、AgentBench；**（B）评测工程平台**——把"数据集+判分器+CI 门禁"产品化，回答"怎么把评测搬进开发流程"，代表是 Braintrust、promptfoo（已被 OpenAI 收购）、DeepEval、LangSmith、Langfuse、Ragas、Patronus、Arthur；**（C）判分方法学**——研究"判分器本身可不可信"，代表是 MT-Bench（LLM-as-judge 奠基）、Alt-Test（裁判统计校验）、τ-bench 的 pass^k（一致性度量）。2025–2026 年最重要的事件是 **SWE-bench 体系信用崩塌**：OpenAI 先后弃用 Verified（2026-02）并撤回对 Pro 的推荐（2026-07），核心原因正是"判分器（测试用例）本身有缺陷"——这与 CaseLoop 的核心命题直接相关。

## 2. 逐对象速览

**SWE-bench / Verified / Multimodal / Pro（任务型，test-based 判分）**
真实 GitHub issue + 仓库自带测试判分（FAIL_TO_PASS + PASS_TO_PASS），官方 harness 用**每实例独立 Docker 环境**执行判分。Verified（2024-08）由 93 名开发者标注 1699 题筛出 500 题（剔除 68.3% 无效题）。**关键事实：OpenAI 2026-02-23 宣布弃用 Verified，审计 138 道 o3 持续失败题发现 59.4% 是测试缺陷（过严/测未说明的功能），且前沿模型凭 task ID 可复现 gold patch（污染）**；2026-07-08 又撤回对 SWE-bench Pro 的推荐，审计称约 30% 公开题"broken"。Multimodal 为 617 道 JS 视觉题（83% 必须看图）。来源：[OpenAI 官方声明 2026-02-23](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)、[Investing.com 2026-07-08](https://ng.investing.com/news/stock-market-news/openai-retracts-swebench-pro-coding-benchmark-recommendation-93CH-2591831)、[PROBE 论文佐证 Docker harness](https://arxiv.org/html/2604.01518v2)、[SWE-bench Illusion 2025-04](https://arxiv.org/html/2506.12286v3)。

**Terminal-Bench 1.0 → 2.0（任务型，容器内测试判分）**
终端任务 benchmark：每任务一个**专用 Docker 环境 + 人工验证的参考解 + 一组测试用例**，2025-05 首发 80 题；2.0 为 89 道人工核验难题（2026）。配套 Harbor 框架负责任务编排，并内置 **Oracle agent 跑参考解以验证"题目可解且判分器正确"**——这是"判分器自身被评测"的工程样板。来源：[tbench.ai 发布公告 2025-05-19](https://www.tbench.ai/news/announcement)、[Snorkel Terminal-Bench 2.0 2026-07-28](https://snorkel.ai/leaderboard/terminal-bench-2-0/)、[MDArena 论文对 Harbor/Oracle 的描述](https://arxiv.org/html/2608.02642v1)。

**τ-bench / τ2-bench / τ3（任务型，模拟环境终态判分）**
Sierra 出品的 tool-agent-user 交互 benchmark：环境完全程序化模拟，判分默认 `reward_basis=["DB","COMMUNICATE"]`——**参考轨迹只在全新 gold 环境重放得到目标 DB 终态哈希，agent 走任何等价路径都算对**（刻意不要求轨迹匹配）；NL_ASSERTION（LLM judge）仅为可选项。**首创 pass^k 一致性度量**：GPT-4o 从 pass^1 的 60% 掉到 pass^8 的 25%。τ3（2026-07）的 v1.0.1 判分修复声明极具参考价值：**题库出错后全量重判、旧分数不可比、用 git tag 固定判分版本**。来源：[τ2 evaluation.md 2025-06](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)、[τ3 README v1.0.1 grading update](https://github.com/sierra-research/tau2-bench/blob/main/README.md)、[pass^k 引用 2026-02](https://arxiv.org/html/2602.11619v2)。

**OSWorld / OSWorld-Verified / 2.0（任务型，执行态判分）**
真实虚拟机（VMware/QEMU/AWS）里的 369 道桌面任务，用执行脚本读取 VM 状态判分。2025-07-28 发布 Verified 版修复 300+ 问题并收紧判分；2.0（2026-06）扩展为 108 道长程任务。社区有复现性争议（GitHub issue #380）。来源：[xlang.ai 公告 2025-07-28](https://xlang.ai/blog/osworld-verified)、[OSWorld 论文](https://arxiv.org/abs/2404.07972)、[arXiv 2.0 2026-06-28](https://arxiv.org/html/2606.29537v1)。

**WebArena / GAIA / AgentBench（任务型，程序化+混合判分）**
WebArena 需自托管 docker 化网站群，判分=程序化功能检查（URL/元素定位/字符串匹配）+少量 LLM 评估，**有状态、任务间残留会污染复现**（H Company 技术报告）；GAIA 466 题，归一化后 exact match，但公开答案导致可被"背题"刷到 98%（moogician 实测）；AgentBench（ICLR 2024）8 类环境基本程序化判分。来源：[H Company Surfer2 报告 2025-10](https://assets.hcompanyprod.fr/surfer2_tech_report_oct_25.pdf)、[moogician 博客 2026-04-08](https://moogician.github.io/blog/2026/trustworthy-benchmarks-cont/)、[GAIA 判分说明 2026-06-15](https://qaskills.sh/blog/gaia-benchmark-ai-agents-explained-2026)。

**promptfoo（工程平台，YAML 断言+CI）**
CLI-first 开源评测/红队工具：YAML 定义测试，断言含确定性检查（contains/regex/schema）与 model-graded（LLM judge），原生跑在 CI。**2026-03-09 被 OpenAI 收购**（官方+TechCrunch+Crunchbase 三源确认；Zylos 称 $86M 未能交叉验证，标【未经证实】）。来源：[OpenAI 官方 2026-03-09](https://openai.com/index/openai-to-acquire-promptfoo/)、[Crunchbase 2026-03-25](https://news.crunchbase.com/ma/data-openai-2023-2026-acquisitions-open-source-astral-promptfoo/)。

**Braintrust（工程平台，数据集版本+半自动闭环）**
eval 生命周期平台：**数据集是一等公民、按 experiment 绑定版本**；生产日志可**一键提升为数据集条目**；内置 AI 助手 **Loop（beta）能从生产日志自动生成判分器和回归数据集**；Topics 做失败聚类；有 CI 门禁。是目前"badcase→回归考题"自动化程度最高的商用产品，但 Loop 仍 beta 且"期望行为"仍需人定义。来源：[Braintrust Loop 文档 2026-07-16](https://www.braintrust.dev/docs/loop)、[Loop 博客 2025-11-24](https://www.braintrust.dev/blog/loop)、[Braintrust vs Langfuse 对比 2026-07-13](https://qaskills.sh/blog/braintrust-vs-langfuse)。

**DeepEval / LangSmith / Langfuse / Ragas（工程平台）**
DeepEval：pytest 原生、50+ 指标、CI/CD 集成是其主打（Thoughtworks Radar 2025-11 评 Trial）；LangSmith：**数据集自动版本化**（每次增删改生成新版本）、trace 可"Add to Dataset"、online evaluators 对生产流量自动打分，官方话术即"把生产失败立即转成永久回归测试"；Langfuse：trace→dataset + 人工标注队列（annotation queues）；Ragas：RAG 指标为主，TestsetGenerator 做**合成考题生成**，并有官方教程教你**用人工标注对齐/校准 LLM judge**。来源：[LangSmith manage-datasets 文档（2026-08 访问）](https://docs.langchain.com/langsmith/manage-datasets)、[LangChain agent observability 2026-04-07](https://www.langchain.com/resources/agent-observability)、[Ragas judge 对齐教程 2025-10-08](https://docs.ragas.io/en/stable/howtos/applications/align-llm-as-judge/)、[DeepEval CI 文档 2026-07-24](https://deepeval.com/docs/evaluation-unit-testing-in-ci-cd)。

**Patronus AI / Arthur（垂直评测与闭环倡导者）**
Patronus：专用判分模型（Lynx 幻觉检测、GLIDER 裁判）、Percival agent 调试器（20+ 失败模式）；**2026-06-25 获 $50M B 轮（累计 $70M）转向"数字世界模型"仿真评测**——即"为 agent 造可重放的仿真环境"正在成为资本押注方向。Arthur：2026-06-12 发布 ADLC 闭环方法论（生产失败→trace→考题→golden dataset→CI 门禁），并给出判分最佳实践（**判分要二元、要具体、能用确定性检查就不用 LLM judge**）。来源：[TechCrunch 2026-06-25](https://techcrunch.com/2026/06/25/patronus-ai-lands-50m-to-build-digital-worlds-that-stress-test-ai-agents/)、[Arthur 闭环长文 2026-06-12](https://www.arthur.ai/column/regression-test-datasets-ai-agents-production-failures)。

**判分方法学（LLM judge 可靠性）**
MT-Bench（NeurIPS 2023）：GPT-4 裁判与人类一致率 ~80%，接近人与人之间的一致水平，但有自我偏好/冗长偏好等系统性偏差；Alt-Test（Calderon et al., ACL 2025）给出"judge 能否替代人类标注"的统计检验框架，已成判分器校准的标准做法；实证研究显示 judge 与人类 binary 判断的 κ 可达 0.79–0.82，但主观维度一致性显著更低。来源：[Zheng et al. 2023](https://arxiv.org/abs/2306.05685)、[Calderon et al. 2025](https://arxiv.org/pdf/2501.10970)、[LLM judge 验证实例 2025-11](https://www.arxiv.org/pdf/2511.12014)。

## 3. 与 CaseLoop 的对照

**直接可用（平移即可）**
- **冻结任务集的工程做法已完全成熟**：SWE-bench 每实例 Docker、Terminal-Bench 每任务 Docker+参考解、τ2 全程序化模拟环境、OSWorld VM 快照——CaseLoop-for-Agents 的"探针"照此构建即可，推荐 Docker 镜像 + 种子数据脚本 + 镜像 digest 锁定。
- **终态判分优于轨迹判分**：τ2 的 DB 哈希终态比对是最干净的确定性判分范式，与 CaseLoop"确定性控制面"铁律同构。
- **判分二元化、规则优先、LLM judge 兜底**：Arthur/promptfoo/DeepEval 的共识做法 = CaseLoop 双轨门禁的"规则轨"。
- **pass^k 多次独立重跑**：直接适配 CaseLoop 信任账本的二项统计（Wilson 下界），解决 agent 非确定性下"一次过≠修好"。
- **CI 门禁**：promptfoo/DeepEval/Braintrust 的"数据集+判分器+阈值→block merge"模式可直接映射。

**需改造可用**
- **数据集版本管理**（LangSmith 自动版本化、Braintrust 按 experiment 绑版本、τ3 用 tag 固定判分版本）：可用，但 CaseLoop 需升级为**与"agent 配置快照 hash"绑定的考题版本**——业界版本管理绑的是数据集本身，不绑被测对象配置。
- **badcase→考题转化**（LangSmith Add-to-Dataset、Braintrust 一键提升+Loop 生成）：半自动，**"期望行为/判分标准"仍需人工定义**，CaseLoop 的取证+归因 agent 可自动化这一段，但题库入库仍需人工确认环节。
- **LLM judge 校准**（Alt-Test 统计检验、Ragas/Galileo 校准流程）：CaseLoop 的"裁判≠运动员"要落地，必须给裁判建校准集+定期统计审计，这部分方法学现成但需自行工程化。

**确认是缺口（无人做好）**
- **全自动"线上 badcase→版本化回归考题"闭环**：Arthur 是方法论+半自动工具，Braintrust Loop 最接近但仍是 beta 辅助生成，**没有任何工具做到"立案-去重-取证-固化-入题库"全自动**（【事实】基于上述产品能力边界；【推断】这正是 CaseLoop 非 LLM 控制面立案机制的差异化空间）。
- **判分结果的统计裁决**：所有 benchmark/平台都只报 pass rate，**没有 Δ效应量、95% CI、三态裁决**——双臂对照归因实验在评测工具链里完全不存在。
- **判分器自身的质量保证体系**：SWE-bench 59.4%/30% 判分缺陷、GAIA 被背题、WebArena 状态污染——业界只有事后审计，**没有工具把"判分器可信度"作为一等公民持续监控**（Terminal-Bench 的 Oracle agent 是唯一雏形）。
- **信任账本/放权机制、不可变工单 hash 绑定**：评测赛道无任何对应物。

## 4. 关键事实清单

1. OpenAI 2026-02-23 弃用 SWE-bench Verified：138 道持续失败题中 59.4% 为测试缺陷，前沿模型可凭 task ID 复现 gold patch（[openai.com](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)，2026-02-23）。
2. OpenAI 2026-07-08 撤回 SWE-bench Pro 推荐：约 30% 公开题存在设计缺陷（[Investing.com](https://ng.investing.com/news/stock-market-news/openai-retracts-swebench-pro-coding-benchmark-recommendation-93CH-2591831)、[startuphub.ai](https://www.startuphub.ai/ai-news/artificial-intelligence/2026/openai-flags-major-flaws-in-swe-bench-pro)，2026-07-08/11）。
3. τ2-bench 判分=终态 DB 哈希比对+必说信息子串匹配，刻意不做轨迹匹配；NL judge 仅可选（[官方文档](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md)，2025-06）。
4. τ-bench pass^k：GPT-4o pass^1=60% → pass^8=25%（[arXiv 引用](https://arxiv.org/html/2602.11619v2)，原始论文 Yao et al. 2024, arXiv:2406.12045）。
5. Terminal-Bench 每任务专用 Docker+人工验证参考解+测试；Harbor 的 Oracle agent 机制用于验证题目可解且判分器正确（[tbench.ai](https://www.tbench.ai/news/announcement) 2025-05-19；[arXiv](https://arxiv.org/html/2608.02642v1) 2026-07-31）。
6. OSWorld-Verified 修复 300+ 判分/任务问题（[xlang.ai](https://xlang.ai/blog/osworld-verified)，2025-07-28）。
7. MT-Bench：GPT-4 裁判与人类一致率约 80%，≈人际一致水平；Alt-Test（ACL 2025）提供裁判替代人类的统计检验（[arXiv:2306.05685](https://arxiv.org/abs/2306.05685)；[arXiv:2501.10970](https://arxiv.org/pdf/2501.10970)）。
8. LangSmith 数据集自动版本化；生产 trace 可转数据集条目（[官方文档](https://docs.langchain.com/langsmith/manage-datasets)，截至 2026-08）。
9. Braintrust Loop（beta）可从生产日志生成判分器与回归数据集（[官方博客](https://www.braintrust.dev/blog/loop)，2025-11-24；[文档](https://www.braintrust.dev/docs/loop)，2026-07-16）。
10. OpenAI 2026-03-09 收购 promptfoo（金额未官方披露）（[openai.com](https://openai.com/index/openai-to-acquire-promptfoo/)；[Crunchbase](https://news.crunchbase.com/ma/data/openai-2023-2026-acquisitions-open-source-astral-promptfoo/)，2026-03）；Patronus 获 $50M B 轮押注仿真评测世界模型（[TechCrunch](https://techcrunch.com/2026/06/25/patronus-ai-lands-50m-to-build-digital-worlds-that-stress-test-ai-agents/)，2026-06-25）。

## 5. 对 CaseLoop-for-Agents 的设计启示

1. **判分器质量是系统级风险，CaseLoop 的"三态裁决+置信区间"必须从可选升级为卖点**。SWE-bench 两连崩证明：连业界最成熟的 test-based 判分都有 30–60% 缺陷率，"通过/未通过"本身就是不确定测量。业界全部工具只报 pass rate，CaseLoop 的 Δ效应量+95%CI 是真空地带——保留并强化。
2. **探针设计采用"终态判分"，放弃"轨迹判分"**。τ2 的官方文档明确：把参考轨迹当唯一正确解是"强假设"，绝大多数任务刻意不用。CaseLoop 冻结任务集应判"环境终态+必说信息"，不判 agent 怎么走的——这也能避免修复后换路径被误判回归。
3. **考题库必须私有+版本化+持续轮换，且与配置快照 hash 绑定**。SWE-bench 的污染教训（模型背题）说明公开静态题库必然失效；τ3 用 git tag 固定判分版本、出错全量重判的做法值得照抄。LangSmith/Braintrust 的数据集版本管理可参考，但它们不绑"被测配置"，这部分要自建。
4. **信任账本与 pass^k 天然同构，直接复用**。agent 非确定性下业界共识是 k 次独立重跑（pass^8 比 pass^1 严苛得多）；CaseLoop 的 Wilson 下界正是同一统计思想，无需改造，只需把"多次重跑"写进探针执行协议。
5. **"badcase 自动固化为回归考题"是确认的最大空白，且 CaseLoop 恰好补齐**。Braintrust Loop 证明了市场需求（从日志生成考题/判分器），但它停在"AI 辅助人工整理"；Arthur 明确了六步流程但其中分类、清洗、定义期望行为全靠人。CaseLoop 的"非 LLM 控制面立案（去重幂等）+取证+不可变工单"是全流程自动化的缺失拼图——这是主战场，不要硬凹，是真缺口。
6. **给 LLM 裁判配"校准集+Alt-Test 定期审计"**。"裁判≠运动员"解决独立性，但 MT-Bench 揭示的自我偏好/冗长偏差和 Alt-Test 提供的统计校验说明：裁判本身需要被持续证伪。建议在案例库中保留人工金标准子集，定期对裁判做一致性审计，审计结果也进信任账本。

## 6. 一句话结论

任务型 benchmark 已经教会业界"怎么冻结环境、怎么判分"（Docker 快照+终态判分+多次重跑可直接平移），工程平台已经教会业界"怎么把考题搬进 CI"（数据集版本化+门禁可直接平移），但 **SWE-bench 的崩塌证明判分器本身不可信、且没有任何工具把"线上 badcase 自动固化为带统计裁决的版本化回归考题"做成闭环——这恰恰是 CaseLoop 已经建成的东西**。</subagent>
