# CaseLoop for Agents —— 竞品对照与定位修正

> 2026-08-09｜外部竞品分析（队友侧 AI 产出）+ 主控核验。
> 结论：**分析属实，定位需修正——空白不在"信任分级/验证修复/门禁"单点（已被 Cleric、CSA ATF、eval 平台局部占领），
> 空白在"业务 LLM agent 质量案件闭环"整链。**

## 0. 主控核验记录（两个 P0 威胁亲验）

| 威胁 | 核验结果 | 关键源 |
|---|---|---|
| Cleric「graduated autonomy」 | **属实**。官网："Graduated autonomy by problem type. Accuracy scores per problem class determine where the agent acts independently and where it escalates"；"Verifies fixes against the live environment. Accuracy is measured, not claimed"。域=infra SRE（告警/工单→调查→修复→线上验证），Gartner Cool Vendor；无组件归因实验、无投诉→回复、无统计置信机制 | [cleric.ai](https://cleric.ai/)、[State of AI SRE](https://cleric.ai/resources/reports/the-state-of-ai-sre)、[verifying-fixes 2026-07-22](https://cleric.ai/blog/verifying-fixes) |
| CSA ATF「自治晋升标准」 | **属实**。CSA 零信任工作组 2026-02-02 发布，四级 Intern→Principal，显式晋升+降级门，"autonomy is earned"。是**框架/标准不是产品**：未规定统计内核（无 Wilson/证据纪元/逐动作样本），Level 2 仅提"quantitative thresholds calibrated through operation" | [CSA 2026-02-02](https://cloudsecurityalliance.org/blog/2026/02/02/the-agentic-trust-framework-zero-trust-governance-for-ai-agents)、[Cequence 论文转述](https://www.cequence.ai/wp-content/uploads/2026/05/Agentic-Zero-Trust-Research-Paper-v3.pdf) |

## 1. 最接近的 5 个对手（agent 口径）

### ① Cleric —— agent 化 SRE，最像"验证修复 + 按表现放权"

- **做什么**：生产 agent：调查 → 修 → 用线上信号验证是否真修好；按问题类 accuracy 做 graduated autonomy（哪类可自治、哪类必须升级）
- **对照 CaseLoop**：立案(告警)✅ 取证✅ 修复验证✅ 自治分级✅ · prompt/KB/model 组件实验归因❌ · 用户投诉→回复❌ · Wilson 账本❌ · 确定性质量控制面❌
- **重叠**："修完要验证""自治是赚来的"
- **差异**：对象是 infra 运维 agent 的行动结果，不是企业业务 LLM agent 的质量案件
- **源**：cleric.ai · 验证修复 2026-07-22 · State of AI SRE 2026-05-13

### ② Microsoft Agent 365 + CSA Agentic Trust Framework —— agent 控制面 + 晋升标准

- **做什么**：Agent 365：observe / govern / secure 企业 agent 舰队。CSA ATF：Intern→Principal，五道晋升门，事故可降级
- **对照 CaseLoop**：身份/策略/可见性/自治分级框架✅ · 质量 badcase 案件❌ · 统计实验归因❌ · 双轨评测门禁❌ · 回归考题闭环❌
- **重叠**："信任/自治分级"心智已被标准吃掉
- **差异**：安全与合规控制面，不是 agent 输出/行为质量的统计治理 OS
- **源**：Agent 365 · CSA ATF 2026-02-02

### ③ AIR（学术）—— 专门面向 LLM agent 的 incident response

- **做什么**：首个 LLM agent 事故响应框架：运行时检测 → 遏制/恢复 → 生成 guardrail 防复发（code / embodied / computer-use agent）
- **对照 CaseLoop**：detect/contain/recover/eradicate✅ · 企业投诉案件❌ · 组件对照实验归因❌ · 审批灰度信任账本❌ · 产品化控制面❌
- **重叠**："agent 出事不能只有预防，要有响应生命周期"
- **差异**：安全/行为事故 IR，不是质量治理 + 实验归因 + 发布权
- **源**：arXiv:2602.11749v2，2026-06-20

### ④ Credo Agent Governor / Cisco AI Defense / 同类 harness 治理 —— "事前配权"，不是"事后质量案件"

- **做什么**：政策 → harness 配置（hooks/skills/权限）；红队、runtime 防护、zero trust for agents
- **对照 CaseLoop**：边界与策略✅ · 线上 badcase→归因→修→门禁晋升❌
- **重叠**：agent 必须被治理，不能裸奔
- **差异**：策略配置/安全防护，不是质量故障的统计闭环
- **源**：Credo Agent Governor 2026-07-14 · Cisco AI Defense 2026-03

### ⑤ Maxim / Galileo / Confident AI（agent eval 层）—— 轨迹评测闭环，停在"工程 eval"

- **做什么**：agent simulation、tool-call/trajectory 评测、生产失败进数据集、release gate 话术
- **对照 CaseLoop**：轨迹观测+回归+门禁 部分✅ · 案件工单❌ · 三层实验归因❌ · Wilson 晋升❌ · 用户回复闭环❌
- **重叠**："agent 多步失败要回流成测试"
- **差异**：评测平台，不是 agent 质量事故操作系统
- **源**：Maxim agent 2026 · Galileo governance 2026-04-06 · Confident AI HITL 2026-07

**Braintrust 一句话重定位**：对 agent 有 trace/tool 级 review 和 CI 门禁，但仍是 eval 优化环，不是 agent 案件治理 + 自治晋升账本。可作基建，不作主对手。

**中文侧（agent）**：政策/标准在推"智能体分类分级、可信、全栈评估"（信通院关键词 2026、网信等意见）；产业偏安全检测/合规/评测基准，未见"业务投诉→组件实验归因→修复门禁→自治晋升"产品。
源：[信通院 2026 十大关键词](https://www.secrss.com/articles/91447) · [智能体规范意见 2026-05](https://www.cac.gov.cn/2026-05/08/c_1779979789523320.htm)

## 2. 红旗清单（按威胁）

| 级 | 发现 | 对差异化的打击点 |
|---|---|---|
| 🔴 P0 | CSA ATF 自治晋升 | "信任分级/晋升"不是空白，是标准；要钉 **Wilson + 质量案件证据**，不是"我们发明了分级" |
| 🔴 P0 | Cleric：验证修复 + graduated autonomy | 哲学同构；只能用**域差异**（业务 LLM agent vs infra agent）+ 统计内核 + 归因实验打 |
| 🟠 P1 | AIR：agent IR 学术闭环 | "agent 事故响应"已被命名；钉**质量归因+发布权**，不是"我们第一个做 agent IR" |
| 🟠 P1 | Agent 365 / Cisco / Credo | 巨头吃控制面/安全心智；别用 "governance" 单打，要用 **quality case OS** |
| 🟡 P2 | Maxim/Galileo/LangSmith agent eval | 门禁+回归被 eval 平台覆盖；别说"没人做门禁" |
| 🟡 P2 | 中文"智能体可信/分级"政策与评测 | 监管话语占位；商业闭环仍空 |
| 🟢 仍硬 | 业务 agent 坏结果 → 案件 → prompt/KB/模型（+tool/skill/memory）对照实验三态裁决 → 双轨门禁 → 灰度 → 回用户 → 回归考题 → Wilson 晋升 | **整链仍未见** |

## 3. 答辩话术（评委挑战应对）

| 对手 | 一句 |
|---|---|
| Cleric | "Cleric 治理的是运维 agent 对基础设施的动作；CaseLoop 治理的是业务 LLM agent 对用户与工具的行为质量——同一纪律，不同系统层。" |
| Agent 365 / CSA | "他们解决谁能跑、能碰什么、自治级别框架；我们解决一次 agent 搞砸之后，如何用统计实验证明修对了，并决定能否升自治。" |
| AIR | "AIR 是 agent 安全事故的 detect–contain–recover；我们是 agent 质量案件的 attribute–gate–promote–reply。" |
| Credo / Cisco | "他们把政策编译进 harness；我们把线上失败编译成可审计案件与晋升证据。" |
| Maxim / Galileo / Braintrust | "他们优化轨迹评测与发布门禁；我们把 agent 故障变成工单化归因与信任账本，评测只是双轨门禁里的一环。" |

**总开场（修正后）**：
"2026 年 agent 诊断/观测/安全控制面都不是空白；空白在业务 LLM agent 的质量事故闭环——立案、组件级对照实验归因、双轨门禁、灰度、回用户、Wilson 自治晋升。我们不做又一个 agent eval，做 agent 的质量控制面。"

## 4. 空白论断修正

| 原话 | agent 口径结论 |
|---|---|
| "诊断红海，验证治理空白" | **不成立**。验证/自治/控制面已被 Cleric、CSA、Agent 365、eval 门禁局部占领 |
| "没人做完整 CaseLoop" | **仍成立**（agent 质量案件 OS + 组件实验归因 + 信任账本整链） |
| 最硬护城河 | 把 agent 故障当案件：实验臂 = prompt / 知识 / 模型（+ 可扩到 tool/skill/memory）→ Δ+CI 三态 → 门禁发布权 → 自治晋升 |

## 5. 主控补评：这份分析反而强化了 pitch

1. **"earned autonomy"心智已被标准+产品双验证**——CSA ATF（标准）与 Cleric（产品+Gartner Cool Vendor）证明这个市场认知存在且有预算。我们不用教育市场"自治是赚来的"，只需展示"标准没规定的统计内核，我们做出来了"（Wilson LB/证据纪元/逐动作样本/与质量案件证据链绑定）。
2. **Cleric 是最好的参照系而非威胁**：它证明"验证修复+分级放权"在 infra SRE 域卖得动；业务 LLM agent 质量案件域（客服、营销、内部工具）比 infra 域大得多，且 Cleric 的 DNA（告警/工单/runbook）决定它不会自然长过来。
3. **答辩策略改变**：评委若提 CSA/Cleric，从"否认对方存在"改为"引用对方背书"——"ATF 把晋升写进标准的同一年，我们实现了它的统计内核；Cleric 在 infra 域验证了哲学，我们在业务质量域建成了闭环"。这比声称真空更可信、更难被击穿。
