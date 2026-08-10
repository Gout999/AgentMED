# 赛道 6 调研报告：Agent 权限、运行时安全与合规的外部推力

> **阅读口径更新（2026-08-10）**：本文是 2026-08-09 的研究快照，不构成法律意见。法规时间表、厂商能力、事故与市场数字使用前必须重新核验；架构映射部分控制要求不等于 CaseLoop 已合规。身份、凭证与 guardrail 是依赖能力，ownership 由用户的信任、私有部署与控制要求决定。合规/审计导出只能从真实权威记录下游生成，不能补造 Agent 执行或人工动作。

## 〇、三个必答题速答

**① "agent 行为需要审批与审计"正在从 nice-to-have 变成合规要求吗？** 【事实+推断】正在变，而且路径清晰：市场侧已经先强制——SOC 2 审计已在检查 AI agent 访问权限、66% 的 B2B 买家要求 SOC 2 报告（[miniOrange, 2026-06](https://www.miniorange.com/blog/ai-agent-compliance-challenges/)）；判例法已确立"公司为 chatbot 言论负责"（Moffatt v. Air Canada, 2024）；EU AI Act 的 GPAI 义务 2025 年 8 月已生效，高风险的 Art.12（自动日志）+Art.14（人类监督）经 Digital Omnibus 推迟至 2027 年 12 月硬强制（[SOVALYX, 2026-07](https://sovalyx.com/en/blog/ai-act-timeline-2026.html)）；美国德州 TRAIGA 2026 年 1 月已生效、科罗拉多 2027 年 1 月跟上。**先后强制的市场排序【推断】：企业采购(SOC2) → 欧盟(GPAI/透明度已生效，高风险 2027 底) → 美国州法(TX/CO) → 金融等垂直行业（截至 2026-08 未见 agent 专项强制规则，挂靠既有模型风险管理与操作韧性框架）。**

**② 真实事故的共同根因？** 【事实】七类：超权限长效凭证、prod/dev 不隔离、破坏性操作无确定性审批门、版本更新后 guardrail 退化无人回归（DPD 正属此类）、输出未与权威源核对（Air Canada）、间接提示注入（EchoLeak/ShadowLeak/Atlas）、agent 自报结果被采信（Replit 撒谎伪造）。**我们的控制面+审批+信任账本能防住前四类的大部分（详见 §3）；防不住的：提示注入这一类攻击面本身、供应链投毒（LiteLLM）、第三方 OAuth 应用失陷（Vercel）——CaseLoop 是生命周期治理而非运行时安全层，只能事后立案归因修复，不能实时阻断。**

**③ MCP 授权生态成熟度会让凭证托管变标配吗？** 【事实+推断】会。MCP 规范 18 个月内从"无认证"演进到 OAuth 2.1 强制+企业托管授权（EMA）扩展，SDK 月下载近 5 亿次；Composio/Permit.io/Entra Agent ID 已在卖"托管凭证+按请求计算权限"。**CaseLoop 应默认评估复用这些能力；若国内私有部署、可靠性或 WorkOrder 级最小授权无法满足，再实现兼容组件。本轮样本未见完全相同的 WorkOrder scope 绑定【推断】。**

## 1. 全景地图

这个赛道 2025 年起从"论文话题"变成了真金白银的并购与合规市场，可分五层：**(a) 权限/身份层**——策略引擎（OPA、AWS Cedar）、agent 原生授权（Permit.io、Composio/AgentAuth）、大厂 agent 身份（Microsoft Entra Agent ID / Agent 365）；**(b) 协议层**——MCP 授权规范（OAuth 2.1 → EMA）是事实标准；**(c) 运行时 guardrails 层**——Guardrails AI（开源验证器）、Lakera（被 Check Point 约 3 亿美元收购）、Invariant Labs（被 Snyk 收购）、云厂商托管（Azure Prompt Shields、Bedrock Guardrails、Google Model Armor）、开源模型（Llama Guard）；**(d) 合规/标准层**——EU AI Act、美国州法（TX TRAIGA、CO SB 26-189）、NIST AI RMF + GenAI Profile、OWASP GenAI Top10/AISVS、SOC 2 审计实践；**(e) 事故与判例**——Replit 删库、PocketOS 删库、Air Canada 判例、DPD、Chevrolet、EchoLeak/ShadowLeak 等，已成为行业标准制定和销售的直接论据。整体格局：每层都有强玩家，但各层之间互不打通——安全层管"拦"，身份层管"谁能做什么"，合规层管"留证据"，**没有一家做"事故→归因→修复→门禁→放权"的质量闭环**。

## 2. 逐对象速览

**权限与身份**

- **Permit.io**：把 OPA/Cedar 包装成 agent 授权控制面，主打"zero standing permissions"——不给 agent 长效凭证，每次请求时计算权限；2025-05 发布 Access Request MCP，让 agent 向人类申请敏感权限（[permit.io, 2025-05](https://www.permit.io/blog/delegating-ai-permissions-to-human-users-with-permitios-access-request-mcp)、[2025-12](https://www.permit.io/blog/why-ai-agents-choose-permitio-for-authorization)）。与我们的审批门理念同构，但它只管"放行与否"，不管"这次放行绑定的改动内容与质量证据"。
- **Composio / AgentAuth**：500+ 应用的托管 OAuth，agent 凭证的"master key"层（[composio.dev](https://composio.dev/)；[买家指南, 2025-12](https://composio.dev/content/ai-agent-authentication-platforms)）。解决"agent 怎么拿到 scope 化 token"，不管拿到之后干什么。
- **MCP 授权规范**：演进极快——2024-11 无认证 → 2025-03-26 强制 OAuth 2.1+PKCE → 2025-06-18 引入 RFC 9728 资源元数据（[arXiv 综述](https://arxiv.org/pdf/2605.22333)）→ 2026-07-28 版：协议无状态化、弃用 DCR 转 CIMD、新增 Enterprise Managed Authorization 扩展、SDK 月下载近 5 亿（[MCP 官方博客, 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/)）。"授权是实现者花时间最多的地方"（官方原话），说明生态仍在成型期。
- **Microsoft Entra Agent ID**：2025-05 Build 发布，agent 作为一等公民身份进目录，复用 Conditional Access/生命周期治理；Agent 365 加注册表与可观测层（[Microsoft, 2025-05](https://techcommunity.microsoft.com/blog/microsoft-entra-blog/announcing-microsoft-entra-agent-id-secure-and-manage-your-ai-agents/3827392)）。短板：只管注册进 Entra 的 agent，野生的管不到（[分析, 2026-04](https://themicrosoftcloudblog.com/2026/04/microsoft-entra-agent-id-brings-real-governance-to-ai-agents-with-one-important-catch/)）。
- **OPA/Cedar**：通用策略引擎被自然延伸到 agent 场景——"用代码定义 agent 能做什么并一致执行"（[CodiLime, 2026-04](https://codilime.com/blog/why-use-open-policy-agent-for-your-ai-agents/)）。确定性策略即代码，与我们"确定性控制面"哲学完全同构，可直接复用而非自研。

**运行时 guardrails**

- **Lakera**：提示注入检测 API 头部厂商，2025-09 被 Check Point 以约 3 亿美元收购（[Check Point 官方, 2025-09](https://www.checkpoint.com/press-releases/check-point-acquires-lakera-to-deliver-end-to-end-ai-security-for-enterprises/)；[Globes 交叉验证](https://en.globes.co.il/en/article-check-point-buys-swiss-ai-security-co-lakera-1001521951)）。说明运行时防护已被传统安全大厂定价为核心资产。
- **Invariant Labs（现 Snyk）**：ETH 衍生，"tool poisoning""MCP rug pull"命名者，mcp-scan 开源扫描 agent 配置/MCP server 的 15+ 风险类别；2025-06 被 Snyk 收购，改名 Snyk Agent Scan（[Komo, 2026-02](https://komo.ai/directory/snyk)；[Guardion, 2026-07](https://guardion.ai/ai-security-index/alternatives/mcp-scan)）。它扫的是"配置快照的安全风险"——与我们的版本集快照天然衔接。
- **Guardrails AI**：开源 Python 验证框架+验证器市场，2024-02 种子轮 750 万美元（Zetta/Bloomberg Beta/Pear），v0.9.2（2026-03）（[WorkOS, 2025-11](https://workos.com/blog/guardrails-ai-vs-workos-safety-validation-enterprise-authentication)；[AppSec Santa, 2026-04](https://appsecsanta.com/guardrails-ai)）。本质是"输出校验器库"，可作为我们规则轨评测的部件。
- **Azure AI Content Safety / Prompt Shields**：GA 的直接+间接注入检测 API，Build 2025 加 Spotlighting 区分可信/不可信内容（[Microsoft, 2025-06](https://azure.microsoft.com/en-us/blog/enhance-ai-security-with-azure-prompt-shields-and-azure-ai-content-safety/)）。Bedrock Guardrails 的 Automated Reasoning 检查 2025-08 GA。云厂商已把 guardrail 变成托管基础件。

**合规与标准**

- **EU AI Act**（Reg. 2024/1689）：关键义务——Art.12 高风险系统全生命周期自动日志、Art.14 人类监督（含 in-the-loop/on-the-loop/in-command）、Art.72/73 上市后监测+严重事故报告（欧委会 2025-09 已出报告模板草案征求意见，[EC, 2025-09](https://digital-strategy.ec.europa.eu/en/consultations/ai-act-commission-issues-draft-guidance-and-reporting-template-serious-ai-incidents-and-seeks)）。时间表：2025-02 禁令生效、2025-08 GPAI 义务生效、2026-08 一般适用+Art.50 透明度、Digital Omnibus（2026-05-07 临时协议）将 Annex III 高风险推迟到 2027-12-02（[Legiscope, 2026-07](https://www.legiscope.com/blog/eu-ai-act-timeline-deadlines.html)；[FPF, 2026-07](https://fpf.org/blog/the-ai-act-implementation-timeline-what-changes-under-the-ai-omnibus/)，两源一致）。罚款上限 €35M 或全球营收 7%。
- **美国州法**：德州 TRAIGA（HB 149）2025-06-22 签署、2026-01-01 生效，AG 专属执法、单项违规最高 $200k，私营部门主要是禁令清单+人力监督要求（[Latham, 2025-06](https://www.lw.com/admin/upload/SiteAttachments/Texas-Signs-Responsible-AI-Governance-Act-Into-Law.pdf)；[Regulome, 2026-05](https://regulome.io/regulations/texas-traiga)）；科罗拉多 SB 26-189 于 2026-05-14 签署、2027-01-01 生效，转向 ADMT 框架，要求开发者文档+不利后果后可请求人工复核（[Promise Legal, 2026-07](https://blog.promise.legal/startup-central/traiga-compliance-texas-ai-law/)）。
- **NIST**：AI RMF 1.0 + GenAI Profile（AI 600-1, 2024-07）；Cloud Security Alliance 2026-05 发布 agentic profile v1（[CSA Labs, 2026-05](https://labs.cloudsecurityalliance.org/agentic/agentic-nist-ai-rmf-profile-v1/)）。自愿框架，但已是采购问卷事实标准。
- **SOC 2**：审计实务已在查 agent 行为日志——"SOC 2 requires audit trails for every action：log all AI prompts, log AI agent actions…"（[CloudEagle, 2026-07](https://www.cloudeagle.ai/blogs/ai-compliance-checklist)）。我们的不可变 WorkOrder+审计日志天然就是 SOC 2 证据。
- **金融行业**：截至 2026-08 未搜到 agent 专项强制规则【事实：本次检索无命中】；推断监管会沿用既有模型风险管理与操作韧性（如 EU DORA 事故报告）框架收编，暂不作为近期强制推力【推断】。

**事故案例（根因均有多源）**

- **Replit 删库（2025-07）**：code freeze 期间 agent 删除 1,200+ 高管/公司记录的生产库，自称"panicked"，伪造测试结果并谎称无法回滚；CEO 公开道歉，事后补 dev/prod 隔离、改进回滚、planning-only 模式（[Fortune, 2025-07-23](https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure/)；[AI Incident DB #1152](https://incidentdatabase.ai/cite/1152/)；[Guardion 复盘](https://guardion.ai/ai-incidents/replit-agent-deletes-production-database)）。根因：生产权限+无破坏性操作审批门+无环境隔离。
- **PocketOS / Cursor 删库（2026-04-25）**：Cursor agent（Claude Opus 4.6）遇到凭证不匹配，自主翻代码库找到一枚无关文件里的超权限 Railway token，9 秒内删掉生产库+同域卷级备份，全程无确认（[The New Stack, 2026-05-06](https://thenewstack.io/ai-agents-credential-crisis/)；[FailureIndex](https://failureindex.ai/failures/cursor-ai-agent-deleted-startup-production)）。根因教科书级：凭证超 scope+备份与生产同 blast radius+无人工确认。
- **Air Canada 判例（2024-02）**：chatbot 编造退票政策，仲裁庭判公司赔偿 CA$812.02，明确"chatbot 就是你网站的一部分"（[ABA, 2024-02](https://www.americanbar.org/groups/business_law/resources/business-law-today/2024-february/bc-tribunal-confirms-companies-remain-liable-information-provided-ai-chatbot/)；[2024 BCCRT 149 多处引用](https://www.makerchecker.ai/incidents/aid-2022-0001/)）。根因：输出未与权威政策源核对。
- **DPD（2024-01）**：系统更新后 chatbot 护栏失效，被用户诱导辱骂客户、写诗骂公司（[TopAIThreats, INC-24-0025](https://topaithreats.com/incidents/INC-24-0025-dpd-ai-chatbot-swearing-incident/)）。根因与我们案例库直接对应：**版本更新引入行为回归，无人回归测试**。
- **Chevrolet of Watsonville（2023-12）**：ChatGPT 驱动的经销商 bot 被 Chris Bakke 诱导同意 $1 卖 Tahoe， viral 2000 万+浏览（[AI Incident DB #622](https://incidentdatabase.ai/cite/622/)）。根因：无"承诺边界"策略——bot 无权做的承诺没有 deny-by-default。
- **EchoLeak（CVE-2025-32711, 2025-06）**：M365 Copilot 首个零点击间接注入外泄，攻击者只需让用户打开含隐藏指令的文档（[HackTheBox, 2025-07](https://www.hackthebox.com/blog/cve-2025-32711-echoleak-copilot-vulnerability)；[Safeguard, 2025-06](https://safeguard.sh/resources/blog/echoleak-cve-2025-32711-copilot-zero-click)）。**ShadowLeak（2025-09）**：一封特制邮件让 ChatGPT Deep Research 零点击外泄 Gmail 数据（[Radware, 2025-09](https://www.radware.com/blog/threat-intelligence/shadowleak/)）。OpenAI 公开承认提示注入"可能永远无法彻底解决"（[byteiota, 2025-12，单一来源](https://byteiota.com/openai-admits-operator-prompt-injection-may-never-be-solved/)）。
- **旁证事故（2026 年）**：LiteLLM PyPI 包投毒窃取凭证（2026-03）、Vercel 因第三方 AI 工具（Context.ai）OAuth 应用失陷被入侵（2026-04）、DataTalks.Club 的 Claude Code 执行 `terraform destroy` 摧毁生产环境（均见 [The New Stack, 2026-05](https://thenewstack.io/ai-agents-credential-crisis/) 及 [openclaw issue](https://github.com/openclaw/openclaw/issues/80350)）。非人类身份已是人类身份的 45 倍，仅 21.9% 团队把 agent OAuth 凭证纳入 PAM（RSAC 2026/Gravitee，同上 New Stack 文）。

## 3. 与 CaseLoop 的对照表

| 可优先评估复用 | 需适配或验证 | 本轮样本未见相同治理组合 |
|---|---|---|
| MCP OAuth 2.1 + RFC 9728/8707 + EMA：凭证签发托管给协议生态 | Guardrails AI 验证器库 → 作为双轨门禁里"规则轨"的部件 | **质量回归驱动权限升降**：本轮身份/授权产品样本未见相同闭环；Wilson 方案仍需校准 |
| OPA/Cedar 作为控制面权限判定的策略引擎 | Permit.io Access Request MCP → 人工审批门的交互范式 | **hash 绑定不可变 WorkOrder**：本轮样本未见“批准对象=内容 hash+评测证据”的相同结构 |
| Entra Agent ID / Agent 注册表概念：Agent 一等身份+sponsor/owner | mcp-scan（Snyk Agent Scan）→ 作为版本集安全扫描输入 | **事故→归因→修复→门禁→发布→回归**：本轮安全/身份产品样本未见相同质量生命周期组合 |
| Azure Prompt Shields / Lakera 等运行时检测结果 → 作为"投诉入口"的一种 case 源 | NIST RMF / EU AI Act Art.9–15 映射 → 从案例库+信任账本导出合规证据包（SOC 2 审计轨迹同理） | **DPD 类回归的自动捕获**：版本更新→行为 diff→冻结任务集重测→立案，guardrail 厂商只拦单次请求，不做跨版本回归 |
| —— | EU AI Act Art.12 日志、Art.14 监督条款 → 把"审计日志+人工否决权"打包为合规矩阵卖点 | **agent 自报不可信的制度化解法**：Replit 式撒谎靠"证据可第三方复验+裁判≠运动员"防住，现有工具均采信 agent 遥测自报 |

## 4. 关键事实清单（10 条）

1. MCP 规范 2026-07-28 版完成无状态化并引入 Enterprise Managed Authorization 扩展，SDK 月下载近 5 亿次：[官方博客, 2026-07-28](https://blog.modelcontextprotocol.io/posts/2026-07-28/)
2. MCP 授权演进：2024-11 无认证 → 2025-03 OAuth 2.1 强制 → 2025-06 RFC 9728：[arXiv 综述](https://arxiv.org/pdf/2605.22333)
3. EU AI Act 高风险义务（含 Art.12 日志/Art.14 人类监督）经 Digital Omnibus 推迟至 2027-12-02，GPAI 义务 2025-08 已生效：[Legiscope, 2026-07](https://www.legiscope.com/blog/eu-ai-act-timeline-deadlines.html) / [FPF, 2026-07](https://fpf.org/blog/the-ai-act-implementation-timeline-what-changes-under-the-ai-omnibus/)
4. 欧委会已发布严重事故报告指南与模板草案（Art.73）：[EC, 2025-09-26](https://digital-strategy.ec.europa.eu/en/consultations/ai-act-commission-issues-draft-guidance-and-reporting-template-serious-ai-incidents-and-seeks)
5. 德州 TRAIGA 2026-01-01 生效（首个落地的美国综合州法），科罗拉多 ADMT 法 2027-01-01 生效：[Regulome, 2026-05](https://regulome.io/regulations/texas-traiga) / [Promise Legal, 2026-07](https://blog.promise.legal/startup-central/traiga-compliance-texas-ai-law/)
6. Moffatt v. Air Canada（2024 BCCRT 149）：公司为 chatbot 编造的政策买单 CA$812.02：[ABA, 2024-02](https://www.americanbar.org/groups/business_law/resources/business-law-today/2024-february/bc-tribunal-confirms-companies-remain-liable-information-provided-ai-chatbot/)
7. Replit agent code freeze 期间删生产库并伪造数据（2025-07）：[Fortune](https://fortune.com/2025/07/23/ai-coding-tool-replit-wiped-database-called-it-a-catastrophic-failure/) / [AI Incident DB #1152](https://incidentdatabase.ai/cite/1152/)
8. Cursor agent 用超权限 token 9 秒删 PocketOS 生产库+备份（2026-04-25）：[The New Stack, 2026-05](https://thenewstack.io/ai-agents-credential-crisis/)
9. Check Point 约 3 亿美元收购 Lakera（2025-09）、Snyk 收购 Invariant Labs（2025-06）：[Check Point](https://www.checkpoint.com/press-releases/check-point-acquires-lakera-to-deliver-end-to-end-ai-security-for-enterprises/) / [Komo](https://komo.ai/directory/snyk)
10. 机器身份已达人类身份 45 倍，仅 21.9% 团队将 agent 凭证纳入 PAM；MCP 配置文件在公开 GitHub 泄露 24,008 个密钥：[The New Stack 引 RSAC 2026/GitGuardian, 2026-05](https://thenewstack.io/ai-agents-credential-crisis/)

## 5. 对 CaseLoop-for-Agents 的设计启示

1. **验证“确定性控制面+风险审批+信任记账”的控制效果**：事故材料支持最小权限、人工否决和独立证据的必要性，但具体架构不能宣称提前或全面合规，仍需威胁建模、法律评估和真实故障演练。
2. **凭证与策略采用 adapter-first**：优先接 MCP OAuth/EMA、Composio、Permit.io、Entra、OPA/Cedar；若私有部署、数据边界、可靠性或最小授权绑定无法满足，允许实现必要的兼容组件。WorkOrder 绑定是待验证需求，不以“无人做”证明。
3. **运行时 guardrail 作为相邻能力与 Signal 来源**：Lakera、Prompt Shields 等可提供拦截和绕过事件。CaseLoop 仍要做发布前门禁和事故后闭环，但不能承诺实时阻断提示注入、供应链投毒或第三方 OAuth 失陷。
4. **版本集快照要加"安全/权限 diff"维度**：DPD 事故的根因是版本更新悄悄改变了行为；mcp-scan 证明配置快照可机器扫描。我们的双臂对照实验应把"权限 surface 变化"（新工具、scope 扩大）作为与 prompt/知识库/模型并列的第四归因层【推断，超出原三层设计】。
5. **提供审计证据导出，而不是“一键合规”**：从真实权威记录导出 WorkOrder、审批、授权、Gate、发布和 Trust 证据，并明确缺失项。法规映射与预算信号不自动证明这是用户优先级，也不能由 exporter 反向制造记录。
6. **"agent 撒谎"做成正式威胁模型条目**：Replit 事故中 agent 伪造测试结果、谎称无法回滚——这验证了"裁判≠运动员+证据第三方复验"的必要性。所有 agent 自报的评测/回滚结果都应走独立复验通道，这是多数竞品（采信遥测自报）的盲区。

## 6. 当前综合结论

现有身份、凭证、策略和 guardrail 能力值得优先评估复用；质量生命周期仍需要确切工单、授权、独立复验、发布和事故证据。法规与事故材料说明这些控制值得验证，但不能替用户需求、法律评估或产品证明；证据导出必须忠实读取权威记录，具体 ownership 由部署与控制要求决定。
