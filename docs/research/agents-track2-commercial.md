 item="赛道2：Agent 可观测与治理的商业版图。调研对象：LangSmith、Langfuse、Braintrust、AgentOps、Helicone、Arize Phoenix、Weights&amp;Biases Weave、Patronus AI、Galileo、Freeplay、Vellum、LangWatch、Keywords AI、Traceloop/OpenLLMetry。逐个回答：①它属于'看'(观测/trace)、'考'(离线评测)、'修'(自动修复/优化)、'管'(权限/审批/发布治理)哪几类 ②定价与免费额度 ③最新融资轮次与金额(2024-2026) ④客户案例类型。重点回答：'从发现 badcase 到修复上线'的闭环有没有商业产品在做？prompt/配置的版本管理与发布审批(prompt CMS、Humanloop、PromptLayer、Agenta、Pezzo)有没有跟评测门禁和灰度发布打通？缺口在哪一层" outcome="completed">调研完成，共执行 30+ 次有效搜索与 2 次官网抓取，关键事实均双源交叉。以下为赛道 2 报告。

---

# 赛道 2 调研报告：Agent 可观测与治理的商业版图

调研时间：2026-08；来源以 2025–2026 年官方公告/权威媒体为主。标注约定：【事实】=有来源，【推断】=我的判断。

## 1. 全景地图

这个赛道在 2024–2026 年经历了"爆发→整合"的完整周期：**"看"（trace/观测）和"考"（离线评测）已卷成红海，且头部资产正被模型厂和云厂密集收购**——Humanloop→Anthropic、W&B→CoreWeave（$1.7B）、Statsig→OpenAI（$1.1B）、Langfuse→ClickHouse、Traceloop→ServiceNow；**"修"（自动修复）刚起步，全部停留在"AI 给建议"层级**（Patronus Percival、LangSmith Polly、Braintrust Loop）；**"管"（审批/灰度/发布治理）主要不在 LLMOps 厂商手里，而在 feature-flag 公司手里**（LaunchDarkly AI Configs），独立做发布治理的 Adaline 是新面孔。没有一家商业产品做"从发现 badcase 到修复上线"的端到端闭环。

分类清单（看/考/修/管）：

- **看+考为主**：LangSmith、Langfuse、Braintrust、Arize(Phoenix/AX)、W&B Weave、AgentOps、Helicone、Traceloop(OpenLLMetry)、Keywords AI/Respan、LangWatch、Galileo
- **考+修建议**：Patronus AI（Percival）、Braintrust（Loop）、LangSmith（Polly）、LangWatch（auto-optimization）、Freeplay
- **Prompt CMS+发布**：PromptLayer、Agenta（开源）、Pezzo（已停滞）、Humanloop（已关停）、Freeplay、Vellum、Adaline
- **管（灰度/审批）**：LaunchDarkly AI Configs、Statsig（已归 OpenAI）、Adaline；Langfuse/Braintrust 只有环境标签级"发布"，无灰度

## 2. 逐对象速览

**LangSmith（LangChain）** — 看+考+修（建议）+部分管。agent 工程平台：trace、evals、prompt commits、Deployment（原 LangGraph Platform）、Insights Agent（自动归类行为模式）、Polly（AI 调试助手，2025-12 推出、2026-04 GA）。定价：Developer $0（1 席、5k traces/月）、Plus $39/席/月（10k traces）、Enterprise 定制（[官方定价](https://www.langchain.com/pricing)；[inference.net 2026-06](https://inference.net/content/langsmith-pricing/)）。融资：2025-10 $125M Series B @ $1.25B，IVP 领投（[官方博客 2025-10-20](https://www.langchain.com/blog/series-b)；TechCrunch 先期报道估值 $1B）。客户：Replit、Clay、Harvey、Rippling、Cloudflare、Workday、Cisco；自称 35% Fortune 500 在用。

**Langfuse** — 看+考+部分管。开源（MIT 核心）AI 工程平台：trace、prompt 管理（label 发布：production/staging，**无内置百分比灰度**）、LLM-as-judge、datasets、experiments；**2026-05 上线 CI/CD gates**（GitHub Actions 跑 experiments 卡发布，[changelog](https://langfuse.com/changelog/2026-05-25-experiment-ci-cd-gates)）。定价：Hobby $0（50k units/月）、Core $29/月、Pro $199/月、Enterprise $2,499/月（[官方定价](https://langfuse.com/pricing)）。融资：仅 $4M seed（2023-11）；**2026-01 被 ClickHouse 收购**（[官方 press 页](https://langfuse.com/press)，与 ClickHouse $400M D 轮同期宣布；早期"Langfuse $50M Series B"传闻为不实信息）。客户：Canva、Khan Academy、Merck、SumUp、Cresta；21 家 Fortune 50、129 家 Fortune 500。

**Braintrust** — 看+考+修（建议）+部分管。最接近"半闭环"的产品：trace→一键转 eval 数据集→CI 门禁（官方 GitHub Action，[run-in-ci 文档](https://www.braintrust.dev/docs/evaluate/run-in-ci)）→Loop agent（自主跑评测、生成测试用例、迭代 prompt，计入定价表）→Environments 环境标签（Pro 层）。定价：Starter $0（10K scores/月）、Pro $249/月平台费、Enterprise 定制（[官方定价](https://www.braintrust.dev/pricing)）。融资：$5.1M seed（2023）→$36M A（2024-10，a16z，$150M post）→**$80M B（2026-02-17，ICONIQ，$800M 估值）**（[官方](https://www.braintrust.dev/blog/announcing-series-b)；[SiliconANGLE](https://siliconangle.com/2026/02/17/braintrust-lands-80m-series-b-funding-round-become-observability-layer-ai/)）。客户：Notion、Stripe、Vercel、Airtable、Zapier、Ramp、Dropbox、Cloudflare、Replit、Coursera。注意：2026-05 发生过云环境安全事件，要求客户轮换 API key（7wData 记录）。

**AgentOps（公司名 Agency）** — 纯看。agent 会话录制/回放/成本归因，400+ 框架接入。定价：Basic $0（5k events/月）、Pro $40/月起、Enterprise 定制（[inference.net 2026-06](https://inference.net/content/agentops-alternatives/)，官网首页口径）。融资：$2.6M pre-seed（2024-08-28，645 Ventures 领投，[PR Newswire](https://www.prnewswire.com/news-releases/agency-ai-raises-2-6m-in-pre-seed-funding-to-revolutionize-ai-agent-development-302233294.html)）。客户：长尾开发者（官网宣称 10,000+ 开发者），无显著企业案例。

**Helicone** — 看（gateway 型）。AI Gateway+观测，一行 baseURL 接入，开源可自托管。定价：Free 10k req/月、Pro ~$79/月、Team ~$799/月（第三方整理 [bytepulse 2026-07](https://bytepulse.io/helicone-vs-langfuse-2026/)，官网 [pricing](https://www.helicone.ai/pricing)）。融资：~$5M seed（2024-09，$25M 估值，YC/Village Global/FundersClub；[salestools](https://salestools.io/en/report/helicone-5m-seed) 与 [ai-evals.tools 2025-08](https://ai-evals.tools/editorial/llm-evals-observability-company-acquisitions) 一致；网传"$30M Series A"未能证实）。客户：开发者/初创为主。

**Arize（Phoenix + AX）** — 看+考。Phoenix 开源（ELv2）trace/eval；AX 商业 SaaS 主打 agentic evaluation。定价：AX Free $0（25k spans/月、15 天留存）、Pro $50/月起、Enterprise 定制（[官方定价](https://arize.com/pricing/)）。融资：**$70M Series C（2025-02-20，Adams Street 领投，M12/Datadog/PagerDuty 参投）**，累计 ~$135M，估值报道 >$1B（TechCrunch 估计值）（[PR Newswire](https://www.prnewswire.com/news-releases/arize-ai-secures-70m-series-c-to-fix-ais-biggest-problem-making-llms-and-ai-agents-work-in-the-real-world-302381601.html)；[官方博客](https://arize.com/blog/arize-ai-raises-70m-series-c-to-build-the-gold-standard-for-ai-evaluation-observability/)）。客户：传统 ML 观测时代积累的大企业群，正转向 agentic AI【推断：其 LLM 客户案例密度低于 LangSmith/Braintrust】。

**W&B Weave** — 看+考。开源（Apache-2.0）+ 托管，trace/evals/leaderboards；2025-06 上线 Online Evaluations（生产流量在线打分，[官方公告](https://wandb.ai/wandb_fc/product-announcements-fc/reports/Ship-AI-with-confidence-Introducing-W-B-Weave-Online-Evaluations--VmlldzoxMzI2ODEzNg)）。定价随 W&B：Free $0、Pro $60/月起、Enterprise 定制（[wandb.ai/pricing](https://wandb.ai/site/pricing/)）。资本事件：W&B 累计融资 $250M，**2025-05-05 被 CoreWeave 以 $1.7B 完成收购**（[CoreWeave 官方](https://www.coreweave.com/blog/coreweave-completes-acquisition-of-weights-biases)；[maginative 2025-03-04](https://www.maginative.com/article/coreweave-acquires-weights-biases-in-a-1-7-billion-ai-cloud-play/)）。客户：W&B 既有 ML 平台客户群。

**Patronus AI** — 考+修（建议）。做 LLM 评测/红队/guardrails；**2025-05-14 发布 Percival：自动分析 agent trace、识别 20+ 故障模式（推理/规划/执行/工具误用）并给出修复建议**，客户 Nova AI 案例：调试时间 1 小时→1 分钟、自动 prompt 建议修复 3 个故障、SAP 工具数据集准确率 +60%（[VentureBeat 2025-05-14](https://venturebeat.com/ai/patronus-ai-debuts-percival-to-help-enterprises-monitor-failing-ai-agents-at-scale)；[官方 Nova 案例](https://www.patronus.ai/case-studies/nova-ai-using-patronus-ais-percival-to-auto-optimize-ai-agents-for-code-generation)）。融资：$17M A（2024-05）→**$50M B（2026-06-25，Greenfield Partners 领投，Forge 显示 post ~$443M）**，累计 $70M（[TechCrunch](https://techcrunch.com/2026/06/25/patronus-ai-lands-50m-to-build-digital-worlds-that-stress-test-ai-agents/)；[PR Newswire](https://www.prnewswire.com/news-releases/patronus-ai-raises-50-million-series-b-and-unveils-first-digital-world-models-for-ai-agent-training-and-simulation-302811248.html)）。客户：Fortune 500 企业+AI 公司，新方向是 Digital World Models（agent 训练/仿真环境）。

**Galileo** — 看+考+运行时护栏。Evaluation Intelligence 定位，Luna 小模型裁判、Agent Reliability Platform（2025-07 免费层发布，[PR Newswire](https://www.prnewswire.com/news-releases/galileo-announces-free-agent-reliability-platform-302508172.html)），runtime guardrails 仅 Enterprise。定价：Free 5K traces/月、Pro $100/月（50K traces）、Enterprise 定制（[官方定价](https://galileo.ai/pricing)；[Braintrust 对比文 2026-04](https://www.braintrust.dev/articles/braintrust-vs-galileo-ai)）。融资：$45M B（2024-10-15，Scale VP 领投，[官方](https://galileo.ai/blog/announcing-our-series-b)；[PR Newswire](https://www.prnewswire.com/news-releases/galileo-raises-45m-series-b-funding-to-bring-evaluation-intelligence-to-generative-ai-teams-everywhere-302276383.html)）。客户：大型企业 GenAI 团队。

**Freeplay** — 考+Prompt CMS+部分管。"AI 工程团队的 ops 平台"：prompt 管理/数据集/LLM 裁判/实验/观测/data review，**支持"不改代码部署 prompt 更新"+"工程师掌控上线内容"的审核流**（[官网](https://freeplay.ai/)；[docs](https://docs.freeplay.ai/core-concepts/prompt-management/managing-prompts)）。定价：Free 层 + Growth $500/月（[G2 2025-06](https://www.g2.com/products/freeplay-freeplay/pricing)）。融资：2025-06 官宣 **$5.6M 新融资 + GA**（[Matchstick 2025-06-03](https://www.matchstick.vc/news?7eb355c9_page=2)）。客户：顶级初创到 Fortune 100。

**Vellum** — 考+Prompt CMS+部署。企业 AI 开发平台：workflow builder、evaluations（Drata 案例：100+ 题测试套件卡每次 AI 更新，[官方案例](https://www.vellum.ai/blog/how-drata-built-an-enterprise-grade-ai-solution-with-vellum)）、deployments。【事实+推断】2025–2026 年明显向"托管 agent 员工"产品转型——官网定价页已变为算力+credits 模式（[pricing](https://www.vellum.ai/pricing)），LLMOps 平台保留 Base 免费+Pro 付费（[docs/pricing](https://www.vellum.ai/docs/pricing)）。融资：$20M A（2025-07-10，Leaders Fund 领投，[官方](https://www.vellum.ai/blog/announcing-our-20m-series-a)；[SiliconANGLE](https://siliconangle.com/2025/07/11/enterprise-ai-development-platform-vellum-raises-20m-help-businesses-deploy-apps-faster/)）。客户：Swisscom、Redfin、Drata、Headspace 等 150+ 公司。

**LangWatch** — 看+考+修（建议）。开源（AGPL）+ 云：observability、evals、**Scenario（agent 用户模拟测试）**、Prompt Studio、auto-optimization（官宣融资时即以此为主打）。定价：免费层 + €29/月起 + $34/核心席/月 + $6/100k events（[官方定价](https://langwatch.ai/pricing)）。融资：€1M pre-seed（2025-02-25，Passion Capital 领投，[官方](https://langwatch.ai/blog/langwatch-ai-announcing-1m-funding-round-to-bring-the-power-of-evaluations-to-ai-teams)；[vestbee](https://vestbee.com/insights/articles/lang-watch-raises-1-m)）。客户：Roojoom、HolidayHero 等中小客户。

**Keywords AI → Respan** — 看。YC W24；**2026-03-18 改名 Respan 并宣布 $5M seed**，定位"proactive observability for AI agents"（[SiliconANGLE](https://siliconangle.com/2026/03/18/respan-raises-5m-bring-proactive-observability-ai-agents/)；[YC 页面](https://www.ycombinator.com/companies/industry/aiops)）。客户：初创/中型 AI 应用团队。

**Traceloop / OpenLLMetry** — 看。OpenLLMetry 是 OTel 标准的 LLM 插桩开源项目（30+ 提供商，IBM/Dynatrace/Cisco 采用）；商业平台有 PR 质量门禁（被收购前）。融资：$6.1M seed（2025-05-27，Sorenson Capital 领投，[Business Wire](https://www.businesswire.com/news/home/20250527864827/en/Traceloop-Launches-to-Replace-Vibes-and-Prompting-With-Data-and-Insight)）；**2026-03 加入 ServiceNow**（据报道 $60–80M，单一来源标注），并入 AI Control Tower 做 agent 运行时观测（[官方博客](https://traceloop.com/blog/traceloop-is-joining-servicenow)；[ServiceNow 新闻稿 2026-05-05](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-expands-AI-Control-Tower-to-discover-observe-govern-secure-and-measure-AI-deployed-across-any-system-in-the-enterprise/default.aspx)）。

**PromptLayer** — Prompt CMS+考+部分管。prompt registry（release labels：prod/staging 移动即发布/回滚）、eval pipelines、**A/B testing with live traffic routing**（有真实流量分流的初级灰度）、GitOps CI/CD 集成（[registry 文档](https://docs.promptlayer.com/features/prompt-registry/overview)；[deployment 文档](https://docs.promptlayer.com/onboarding-guides/deployment-strategies)）。定价：Free $0（5 用户/2.5k req/10 prompts）、Pro $49/月、Team $500/月、Enterprise 定制（[usagepricing 2026-06](https://www.usagepricing.com/blueprint/promptlayer)）。融资：$4.8M seed（2025-02-07，ScOp VC，[TechCrunch](https://techcrunch.com/2025/02/07/promptlayer-is-building-tools-to-put-non-techies-in-the-drivers-seat-of-ai-app-development/)）。客户：非技术 prompt 负责人/PM 导向的团队。

**Humanloop（已关停）** — 曾是"Prompt CMS+考+管"最全者：版本化 prompt、deployments、在线/离线评测、CI/CD 集成。融资累计约 $8M（[Dealroom](https://app.dealroom.co/news/feed/anthropic-acquires-humanloop-co-founders-8m-raised) 等多源一致；CheckThat 单独声称有 $36.2M Series A，与多源矛盾，存疑）。**2025-08-13 Anthropic acqui-hire 团队（不含 IP），2025-09-08 平台关停删数据**（[TechCrunch](https://techcrunch.com/2025/08/13/anthropic-nabs-humanloop-team-as-competition-for-enterprise-ai-talent-heats-up/)；[Prompt Assay 迁移指南](https://promptassay.ai/blog/migrate-from-humanloop)）。历史客户：Gusto、Duolingo、Vanta、Filevine。它的退出直接制造了 prompt 治理市场真空。

**Agenta** — Prompt CMS+考（开源 MIT）。playground、prompt 管理、evals、observability，2026 年仍活跃（v0.104，RBAC 进入 OSS）。融资：~$1.08M seed（[Synaptic 2026-05](https://synaptic.com/resources/tip-offs/ai-agent-infra-startups-and-founders)）。**Pezzo** — 开源 prompt 管理，【事实】2025-06 后开发基本停止（[nolist.ai review 2026-03](https://nolist.ai/item/pezzo)），可视为已掉队。

**LaunchDarkly AI Configs（重点补充对象）** — 管（灰度+审批）。把 prompt/模型配置当 feature flag 管：**progressive rollout（1%→100% 渐进放量）、approvals（2025-09 GA）、实验、Release Guardian 自动监控 flag 变更的生产指标**（[官方博客 2025-09-02](https://launchdarkly.com/blog/introducing-agents-trends-approvals-ai-configs/)；[progressive rollout 文档](https://launchdarkly.com/docs/home/releases/create-progressive-rollouts)）。【推断】这是目前市场上唯一原生的"prompt 灰度发布+审批"产品，但它不做 LLM 质量裁判、不做归因。

**Statsig / Adaline（补充）** — Statsig（实验/灰度平台）2025-05 融 $100M @ $1.1B，**2025-09-02 被 OpenAI 以 $1.1B 全股票收购**（[OpenAI 官方](https://openai.com/index/vijaye-raji-to-become-cto-of-applications-with-acquisition-of-statsig/)；[GeekWire](https://www.geekwire.com/2025/openai-acquires-statsig-for-1-1b-names-ceo-to-key-exec-role-in-surprise-exit-for-seattle-area-unicorn/)）。Adaline 是新出现的 **prompt 发布治理**产品：environments、approvals、promotion、rollback、**eval gates 可阻断 promotion**（[官网对比文 2026-04](https://www.adaline.ai/blog/promptlayer-alternative)，厂商自述，成熟度待验证）。

## 3. 与 CaseLoop 的对照表

| 直接可用（平移） | 需改造可用 | 确认是缺口 |
|---|---|---|
| OTel/GenAI trace 管道作取证数据源（OpenLLMetry 已成事实标准，Langfuse/Phoenix 可自托管） | 用户反馈收集（Langfuse/LangSmith feedback）→ 接立案去重幂等才是投诉入口 | **归因层**：双臂对照实验+Δ效应量+95%CI+三态裁决——无任何商业产品（现有全部是对比打分，无统计推断） |
| LLM-as-judge 做双轨评测第二轨（Langfuse/Braintrust/Patronus 裁判能力成熟） | AI debugger（Percival/Polly/Loop）→ 降级为"假设生成器"，其建议必须进工单过门禁，不能直接生效 | **不可变 WorkOrder（hash 绑定、"批的是 hash"）**——Adaline/LaunchDarkly 有审批流但无不可变工件 |
| CI 评测门禁模式（Braintrust eval-action、Langfuse CI/CD gates 2026-05）——市场已接受"考不过不上线" | 在线评测（Weave Online Evals、Langfuse 生产打分）→ 改造成灰度期质量哨兵 | **信任账本（Wilson 下界放权计量）**——全市场无人做 |
| prompt 版本管理+label 发布（Langfuse/PromptLayer）做版本集的 prompt 分量 | LaunchDarkly 式 progressive rollout → 借鉴其灰度编排，但绑回 CaseLoop 门禁与裁判 | **agent 配置快照整体版本化**（prompt+skills+工具 schema+模型+harness）——prompt CMS 全是 prompt 粒度 |
| 事故转回归数据集（Braintrust trace→dataset 一键转化已验证此模式） | | **投诉→回复原处的业务闭环**（立案-处置-回复-记账全链）——无人做 |

## 4. 关键事实清单（10 条）

1. LangChain 2025-10-20 融 $125M Series B @ $1.25B（IVP），定位"agent engineering platform"，LangSmith 上线 Insights Agent 与 Polly AI 调试助手 — [官方](https://www.langchain.com/blog/series-b)、[Polly GA 2026-04](https://www.langchain.com/blog/polly-langsmith-ga)
2. Langfuse 2026-01 被 ClickHouse 收购（此前仅融 $4M），客户含 21 家 Fortune 50；2026-05 上线 CI/CD 评测门禁 — [press 页](https://langfuse.com/press)、[changelog](https://langfuse.com/changelog/2026-05-25-experiment-ci-cd-gates)
3. Braintrust 2026-02-17 融 $80M Series B @ $800M（ICONIQ），Loop agent 可自主迭代 prompt，客户 Notion/Stripe/Vercel — [官方](https://www.braintrust.dev/blog/announcing-series-b)、[SiliconANGLE](https://siliconangle.com/2026/02/17/braintrust-lands-80m-series-b-funding-round-become-observability-layer-ai/)
4. Patronus 2026-06-25 融 $50M Series B（Greenfield）；Percival（2025-05）是业界首个 agent 故障自动归因+修复建议产品 — [TechCrunch](https://techcrunch.com/2026/06/25/patronus-ai-lands-50m-to-build-digital-worlds-that-stress-test-ai-agents/)、[VentureBeat](https://venturebeat.com/ai/patronus-ai-debuts-percival-to-help-enterprises-monitor-failing-ai-agents-at-scale)
5. Anthropic 2025-08-13 acqui-hire Humanloop 团队，平台 2025-09-08 关停 — [TechCrunch](https://techcrunch.com/2025/08/13/anthropic-nabs-humanloop-team-as-competition-for-enterprise-ai-talent-heats-up/)
6. OpenAI 2025-09-02 以 $1.1B 收购 Statsig（灰度/实验平台） — [OpenAI](https://openai.com/index/vijaye-raji-to-become-cto-of-applications-with-acquisition-of-statsig/)
7. CoreWeave 2025-05-05 完成 $1.7B 收购 W&B（Weave 母公司） — [CoreWeave](https://www.coreweave.com/blog/coreweave-completes-acquisition-of-weights-biases)
8. ServiceNow 2026-03 收购 Traceloop（据报道 $60–80M）并入 AI Control Tower 治理面 — [Traceloop](https://traceloop.com/blog/traceloop-is-joining-servicenow)、[ServiceNow](https://newsroom.servicenow.com/press-releases/details/2026/ServiceNow-expands-AI-Control-Tower-to-discover-observe-govern-secure-and-measure-AI-deployed-across-any-system-in-the-enterprise/default.aspx)
9. Arize 2025-02-20 融 $70M Series C（累计 ~$135M）主打 agentic evaluation — [PR Newswire](https://www.prnewswire.com/news-releases/arize-ai-secures-70m-series-c-to-fix-ais-biggest-problem-making-llms-and-ai-agents-work-in-the-real-world-302381601.html)
10. LaunchDarkly AI Configs 2025-09 上线 approvals，是市场唯一原生"prompt 渐进放量+审批"产品 — [官方](https://launchdarkly.com/blog/introducing-agents-trends-approvals-ai-configs/)

## 5. 对 CaseLoop-for-Agents 的设计启示

1. **端到端闭环主线必须保留，它是空位**："考→CI 门禁"已被 Braintrust/Langfuse 验证、"管→灰度→审批"已被 LaunchDarkly 验证，但没人把"发现→归因→修复→门禁→审批→灰度"串成一条流水线。单点功能我们都不该重造，价值在串联与状态机权威（确定性控制面）。
2. **归因（双臂对照实验）是全市场真空，可做核心差异**：Percival/Polly/Loop 证明了"AI 给修复建议"的市场需求成立，但它们都是黑盒建议、无统计归因、无三态裁决，且建议可直接被人随手采纳——无工件约束。我们的 Δ+CI+裁决+不可变工单应正面打这个差异，而非和他们比"建议质量"。
3. **取证层不自建，站在 OTel/Langfuse/Phoenix 之上**：OpenLLMetry 已是插桩事实标准（被 IBM/Dynatrace 采用），trace 存储和可视化是红海，重造毫无价值；CaseLoop 的 Quality API 契约应规定"给日志"= 标准 OTel GenAI span。
4. **版本集定义要扩到 agent 配置快照，这是 prompt CMS 的教训**：所有 prompt CMS（含已死的 Humanloop）只版本化 prompt 文本+模型参数；skills、工具 schema、harness 的版本化无人做。agent 场景下"改工具 schema"和"改 prompt"同等地改变行为，快照必须整体 hash。
5. **"修"保持自由起草+建议级定位，不追自动优化**：市场上所有自动优化（Loop、Percival 建议、LangWatch DSPy 优化）都只优化 prompt 一个变量；CaseLoop 的三层归因（prompt/知识库/模型）+ 工单约束本身就是更严的框架，不要被"auto-optimize"叙事带偏。
6. **窗口期有限，警惕平台方内置**：LangChain（Polly+Insights Agent+Deployment）、ClickHouse/Langfuse（CI gates 刚上线）、ServiceNow（Control Tower+Traceloop+Veza 权限图）都在向闭环方向移动，OpenAI 直接买走了灰度层（Statsig）。"治理闭环"的认知会在 12–24 个月内被这些平台教育，先发叙事要抢"归因+信任账本"这两个他们架构里最难补的部分。

## 6. 一句话结论

商业版图上"看"和"考"已是红海并正被巨头并购整合，"修"全部停留在 AI 建议级，"管"的灰度与审批只在 feature-flag 公司手里且与评测门禁互不相通——**从 badcase 到修复上线的端到端闭环没有商业产品在做，其中归因实验、不可变工单、信任账本三层是彻底空白，但这个空白正被 LangChain、ClickHouse、ServiceNow、OpenAI 从两端快速蚕食**。</subagent>
