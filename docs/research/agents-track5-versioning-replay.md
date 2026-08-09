 item="赛道5：Agent 版本化与确定性重放的工程实践。调研对象：agent 配置的版本管理事实标准（LangGraph 的 assistant/version 概念、OpenAI Agents SDK、Claude Code 的 settings/agents 文件、MCP server 版本协商、system prompt git 化的实践）；LLM 调用的 record/replay 技术（vcr/cassette 式、LangSmith 的 replay、Hyperparam 快照）；agent 任务沙箱（e2b、Modal、Daytona、Firecracker microVM、docker snapshot/restore）；SWE-bench/Terminal-Bench 的可复现基础设施怎么做的。重点回答：①一个 agent 的'版本集'应该包含哪些可哈希组件（prompt/skill manifest/tool schema/model id/harness 版本/环境镜像 digest）业界有没有共识 ②确定性重放的可行层级：全回放(录制的 LLM 响应)、半回放(录制工具结果+live LLM)、全 live 各自的保真度与成本 ③环境依赖型任务(coding agent 改代码跑测试)怎么固化环境" outcome="completed">调研完成，共 22 次有效搜索/抓取。以下为赛道5报告。

---

# 赛道5 调研报告：Agent 版本化与确定性重放的工程实践

## 1. 全景地图

这个赛道呈"四分天下、中间空白"的格局：**配置版本化**（LangGraph assistants、Claude Code 文件化配置、Langfuse/PromptLayer prompt 版本库、AGENTS.md/SKILL.md 开放标准）已相当成熟；**协议版本协商**（MCP per-request 版本声明）刚完成一次大改；**录制回放**（VCR/cassette 系、agent-cassette、LangSmith/Phoenix trace replay）工具链活跃但全停留在"调试/CI 回归"定位；**环境固化**（SWE-bench 三层 Docker 镜像、Terminal-Bench/Harbor per-task 镜像、E2B/Modal/Daytona 沙箱快照）是被 leaderboard 验证过的事实标准。**空白在交叉点：没有任何一方把"prompt+skills+tool schema+model+harness+环境镜像"绑成单一可哈希、可审批、可归因的版本集**——各家各管一段，AIBOM 标准（CycloneDX ML-BOM/SPDX 3.0）只做安全清点不管运行时归因。

清单（按类）：
- 配置版本：LangGraph Assistants、OpenAI Prompts（API 对象 2026-11-30 下线）、Claude Code `.claude/` 文件体系、Langfuse/PromptLayer/Maxim/Humanloop、PromptVer（prompt 语义化版本规范）
- 开放标准：AGENTS.md、Agent Skills（SKILL.md, agentskills.io）、MCP versioning、CycloneDX ML-BOM / SPDX 3.0 AI Profile
- 录制回放：agent-cassette、GitHub `record-replay` topic 42 个仓库（含 OpenAI/Anthropic VCR 代理）、LangSmith experiment/trace replay、Phoenix span replay、ByteDance DeerFlow ReplayChatModel
- 沙箱/环境：E2B、Modal、Daytona、Blaxel、Vercel、Runloop 等 15 家；SWE-bench harness、Terminal-Bench 2.0 + Harbor
- 底层原语：Firecracker microVM、gVisor、CRIU/docker checkpoint（仍 experimental）

## 2. 逐对象速览

**LangGraph Assistants（配置版本化的最完整参照）**
是什么：assistant = 同一张 graph + 一份配置（prompt/模型选择/tools），配置与图逻辑分离。做到什么程度：每次编辑自动生成新版本，任意版本可 promote/rollback，API+UI 双管，官方把 A/B 测试、灰度（staging→prod）列为核心用例。和我们的关系：这是"版本集"概念在业界的最高完成度，但它只管 config，不管环境镜像、不管证据链、没有 hash 绑定审批。[docs.langchain.com/langsmith/assistants](https://docs.langchain.com/langsmith/assistants)（2026-08 抓取）

**MCP 版本协商**
是什么：client/server 的协议版本对齐机制。做到什么程度：2026-07-28 版起**取消握手**，改为每请求在 `_meta`/`MCP-Protocol-Version` 头声明版本，不匹配返回 `UnsupportedProtocolVersionError` 并列支持清单；≤2025-11-25 旧版靠 `initialize` 握手。真实世界里版本错配导致 agent 静默不调工具的事故频发（thingsboard、stripe 均有 issue）。和我们的关系：证明"tool schema/协议版本"是 agent 行为的真实变量，必须进版本集。[modelcontextprotocol.io/specification/draft/basic/versioning](https://modelcontextprotocol.io/specification/draft/basic/versioning)；错配案例 [github.com/thingsboard/thingsboard-mcp/issues/35](https://github.com/thingsboard/thingsboard-mcp/issues/35)（2026-04）

**Claude Code 文件化配置（git 化事实标准）**
是什么：`.claude/settings.json`（127+ 设置，权限/hooks 硬强制层）、`.claude/agents/*.md` 子 agent 定义（frontmatter）、`CLAUDE.md`、`.mcp.json`、skills 目录，设计为提交 git（`settings.local.json` 自动 gitignore）。和我们的关系：agent 配置"天然是文件、天然可 hash"——CaseLoop 的版本集 hash 对这类 agent 几乎零成本落地。注意已暴露的弱点：frontmatter 字段被全局设置静默覆盖（issue #64706），文件化≠强一致。[claudefa.st/blog/guide/settings-reference](https://claudefa.st/blog/guide/settings-reference)（2026-08）

**OpenAI Prompts / Agents SDK**
做到什么程度：dashboard 创建的 prompt 有版本号，Agents SDK 可按 id+version 引用；但 Prompt Objects API 将于 **2026-11-30 下线**，官方迁移方向是"代码内 versioned prompts"——即承认 git 化才是归宿。【事实，截至 2026-06】[community.openai.com/t/.../1382593](https://community.openai.com/t/deprecation-notice-prompt-objects-in-the-api-will-be-shut-down-on-november-30th-2026/1382593)

**Agent Skills（SKILL.md）开放标准**
2025-12-18 Anthropic 发布为开放标准（agentskills.io），截至 2026 Q1 已有 26–30+ 平台采纳（Cursor、VS Code、Copilot、Gemini CLI、Codex），复制了 MCP 的扩散轨迹。和我们的关系："skill 清单"作为版本集组件有了跨厂商标准格式，且社区共识是"skills 要像代码一样版本化+打 tag+配 eval 套件防 description-drift"。[articsledge.com/post/skill-engineering](https://www.articsledge.com/post/skill-engineering)（2026-07）

**agent-cassette（VCR 式回放的代表）**
是什么：录制 LLM+tool calls 为 JSONL，replay 时按语义 hash 匹配返回录制结果（0ms、0 token）。做到什么程度：v0 显式 wrapper 稳定可用，Docker 化；其招牌用例正是"Golden Run 录制后，CI 回放证明 GPT-4o→GPT-5 升级不破坏 JSON schema"。和我们的关系：证明 cassette 模式对 agent 成立，但它只做全回放，对"模型质量变化"零灵敏度——适合做 harness 回归，不能做门禁裁决。GitHub `record-replay` topic 下还有 42 个同类项目（含 Go 写的 OpenAI/Anthropic/SSE/WebSocket VCR 代理、TS SDK 边界录制），说明这是活跃公共模式。[github.com/yuan-cloud/agent-cassette](https://github.com/yuan-cloud/agent-cassette)（2026-01）；[github.com/topics/record-replay](https://github.com/topics/record-replay)

**SWE-bench harness（环境固化事实标准）**
三层 Docker 镜像（base/env/instance）+ `run_evaluation` harness；Docker 强制（FAQ 原话："No. Docker is required"）；缓存全量 instance 镜像约 2000GB；gold patch 模式先验证基准自身的可解性。和我们的关系：coding agent 探针集的环境固化照抄这个就行，包括"先跑 gold 验证判分器"这一步。[swebench.com/SWE-bench/reference/harness](https://www.swebench.com/SWE-bench/reference/harness/)

**Terminal-Bench 2.0 + Harbor（任务封装与判分器自验证）**
任务 = instruction + Docker image + tests + example solution + time limit 五元组；89 个任务全部 3 人独立人工验证；Harbor 框架每任务配专属 Docker 镜像，**Oracle agent 跑 reference solution 必须拿满分——既验证任务可解，也验证 verifier 本身正确**。[tbench.ai/news/announcement-2-0](https://www.tbench.ai/news/announcement-2-0)（2025-11-07）；[arxiv.org/html/2601.11868v1](https://arxiv.org/html/2601.11868v1)

**沙箱快照/分叉（E2B/Modal/Daytona）**
实测冷启动：E2B 717ms create/662ms resume（Firecracker，内存+文件系统真 pause/resume）；Daytona 742ms/1254ms（Docker 默认，Kata 可选）；Modal 2437ms create，`snapshot_filesystem()` 产出可复用镜像，可 fork N 个沙箱从同一 prepared state 起跑。LogRocket 原文："Pause/resume 回答'agent 能否继续'，snapshot/fork 回答'agent 能否分支'——**从固定基线跑多条轨迹的 eval harness 要的是后者**"。和我们的关系：双臂对照实验（同基线 fork 两臂）的基础设施已被产品化，不用自建。[blog.logrocket.com/comparing-ai-agent-sandbox-platforms...](https://blog.logrocket.com/comparing-ai-agent-sandbox-platforms-e2b-modal-daytona-and-more/)（2026-08-05）

**CRIU/docker checkpoint（进程级快照，不成熟）**
Docker checkpoint 至今 experimental；5GB 堆 checkpoint 40s/restore 20s；Ubuntu 24.04+kernel 6.8 直接挂起（issue #2516）；K8s 原生支持有限。和我们的关系：对 coding agent 场景不需要——环境状态=文件系统+依赖，进程内存可丢弃。【事实，截至 2026 年仍 experimental】[github.com/checkpoint-restore/criu/issues/2516](https://github.com/checkpoint-restore/criu/issues/2516)；[devzero.io/blog/checkpoint-restore-with-criu](https://www.devzero.io/blog/checkpoint-restore-with-criu)（2025-07）

**CycloneDX ML-BOM / SPDX 3.0 AI Profile（AIBOM）**
覆盖 model/dataset/agent/SDK 清单，面向供应链安全与监管申报（OWASP/Linux Foundation 双标准）。和我们的关系：清单格式可借，但它不管"哪个组件变化导致行为变化"，更没有绑定审批 hash。[cyclonedx.org/capabilities/mlbom](https://cyclonedx.org/capabilities/mlbom/)

**Provider 端确定性的天花板**
OpenAI `seed`+`system_fingerprint` 只是 best-effort；论文实测同一模型换推理后端，greedy decoding 下输出仍有方差，jailbreak 率波动近 9%，建议多 seed 平均。和我们的关系：全 live 评测的噪声地板客观存在，CaseLoop 的 Δ+95%CI 统计设计恰好是对症方案。[arxiv.org/html/2605.19537v2](https://arxiv.org/html/2605.19537v2)（2026-05）

**ByteDance DeerFlow 评测平台 RFC（交叉验证）**
其 roadmap 明写"Capture model/config/commit/tool/suite fingerprints"+ 复用 replay golden 基础设施——一线团队的 agent 评测平台正在收敛到"指纹采集+回放"组合，与我们版本集思路同向，但同样没有做到 hash 绑定审批。[github.com/bytedance/deer-flow/issues/4083](https://github.com/bytedance/deer-flow/issues/4083)（2026-07-11）

## 3. 与 CaseLoop 的对照表

| 直接可用 | 需改造可用 | 确认是缺口 |
|---|---|---|
| 任务环境固化 = per-task Docker 镜像 + digest pin（SWE-bench/Harbor 模式，照抄） | VCR/cassette 全回放 → 改造成"录制工具结果+live LLM"半回放才能服务归因（现成工具只做全回放） | **统一可哈希 agent 版本集**：prompt+skill manifest+tool schema hash+model snapshot id+harness commit+image digest 六元组，无标准、无产品 |
| agent 配置 git 化（Claude Code `.claude/`、AGENTS.md、SKILL.md 天然是文件，hash 零成本） | LangGraph assistant 版本概念 → 借用"config 与 logic 分离"，但要补上环境、证据链、审批 hash | **半回放工具**：LangSmith/Phoenix replay 是全 live 调试定位，agent-cassette 是全回放，中间层没人做 |
| 双臂对照实验的执行底座 = 沙箱 snapshot/fork（Modal/E2B/Daytona 原语已产品化） | AIBOM（CycloneDX ML-BOM）→ 借清单格式，加运行时归因语义 | **"对照实验"产品形态**：fork 原语有了，但"同基线两臂跑两个 agent 版本、出 Δ+CI"没有现成系统 |
| 判分器自验证 = Oracle agent/reference solution 模式（Terminal-Bench） | Langfuse/PromptLayer 的版本+label → 只覆盖 prompt 一层，需扩展为全版本集 | provider 端模型漂移（system_fingerprint 类）目前只有观测、无人纳入版本治理 |
| MCP 版本协商机制 → tool 协议版本进版本集 | CRIU 路线 → 文件系统快照已够，进程级快照不投 | |

## 4. 关键事实清单

1. LangGraph assistant：配置（prompt/模型/tools）与 graph 分离、自动版本化、任意版本 promote/rollback，A/B 与灰度为官方用例 — https://docs.langchain.com/langsmith/assistants（2026-08 抓取）
2. MCP 2026-07-28 版取消协商握手，改为每请求声明版本+`UnsupportedProtocolVersionError`；真实版本错配可致 agent 静默不调工具 — https://modelcontextprotocol.io/specification/draft/basic/versioning ；https://github.com/thingsboard/thingsboard-mcp/issues/35（2026-04）
3. OpenAI Prompt Objects API 将于 2026-11-30 下线，官方导向代码内 versioned prompts — https://community.openai.com/t/deprecation-notice-prompt-objects-in-the-api-will-be-shut-down-on-november-30th-2026/1382593（2026-06-03）
4. Agent Skills（SKILL.md）2025-12-18 成为开放标准，2026 Q1 已 26–30+ 平台采纳 — https://www.articsledge.com/post/skill-engineering（2026-07）
5. SWE-bench 评测强制 Docker，三层镜像+gold patch 自验证，全量镜像缓存约 2000GB — https://www.swebench.com/SWE-bench/reference/harness/ ；https://www.swebench.com/SWE-bench/faq/
6. Terminal-Bench 2.0 任务五元组（instruction+Docker image+tests+reference solution+time limit），Harbor 用 Oracle agent 验证 verifier 本身 — https://www.tbench.ai/news/announcement-2-0（2025-11-07）；https://arxiv.org/html/2601.11868v1
7. agent-cassette：JSONL 录制+语义 hash 回放，0ms/0 token，主打用例即"CI 回放证明模型升级不破坏行为" — https://github.com/yuan-cloud/agent-cassette（2026-01）
8. 沙箱实测：E2B 717ms create/662ms resume；Modal snapshot/fork 被明确定位为 eval harness 从固定基线跑多轨迹的模型 — https://blog.logrocket.com/comparing-ai-agent-sandbox-platforms-e2b-modal-daytona-and-more/（2026-08-05）
9. Docker checkpoint/CRIU 截至 2026 年仍 experimental，新内核上可挂起，不适合作为生产依赖 — https://github.com/checkpoint-restore/criu/issues/2516 ；https://www.devzero.io/blog/checkpoint-restore-with-criu（2025-07）
10. provider 端确定性无保证：seed/system_fingerprint 仅 best-effort，换推理后端 greedy decoding 也有方差，需多 seed 平均 — https://arxiv.org/html/2605.19537v2（2026-05）

## 5. 对 CaseLoop-for-Agents 的设计启示

1. **版本集 hash 的六元组件清单可以直接定稿，且这是真空地带**：system prompt + skill manifest（SKILL.md 目录 hash）+ tool schema hash（取证时记录 `tools/list` 响应 hash）+ model snapshot id（禁 alias，pin  dated 快照）+ harness git commit + 环境镜像 digest。业界每块都有碎片，拼成可审批 hash 的没有——CaseLoop 的 hash 绑定 WorkOrder 设计**保留并作为差异化核心**。【推断】
2. **双臂对照实验不要自建环境分支系统**：直接架在沙箱 snapshot/fork 原语上（Modal `snapshot_filesystem`、E2B pause、Daytona fork），从同一 frozen 基线 fork 两臂跑 v1/v2。原语刚被产品化、亚秒级，自建是浪费。此设计**保留**。
3. **探针回放分级，明确"全回放不能用于门禁裁决"**：harness/管线回归用全回放（cassette，零成本进 CI）；归因实验用半回放（冻结环境镜像+live LLM，即 SWE-bench 被 leaderboard 验证过的保真度/成本平衡点）；门禁终审用全 live+多 seed。这条是对现有"双轨评测门禁"的**修改/增补**：三态裁决需要注明证据来自哪一层回放，因为全回放对模型质量变化零灵敏度。【推断】
4. **判分器自验证直接抄 Terminal-Bench Oracle 模式**：探针集发布前用 reference solution 跑满分验证 verifier 本身。这是 CaseLoop"裁判≠运动员"的自然延伸——判分器自己也要先过考。**新增，成本低收益高**。
5. **环境固化放弃进程级快照路线**：CRIU/docker checkpoint 截至 2026 仍 experimental；coding agent 的环境状态=文件系统+依赖，image digest+volume snapshot 已够。**放弃 CRIU，保留 Docker digest 锚定**。
6. **如实承认已经有人做得很好的部分**：配置版本管理（LangGraph assistant）、prompt 版本库（Langfuse）、环境固化（SWE-bench/Harbor）、回放工具（cassette 系）都成熟，CaseLoop-for-Agents 不应重造这些，而是做它们之上的"绑定+归因+审批"控制面。

## 6. 一句话结论

业界已分别把"环境固化"（per-task Docker digest）、"配置版本化"（assistant/SKILL.md/git 化）、"录制回放"（cassette 系）做成事实标准，但把六元组绑成单一可哈希版本集并用于归因-门禁-灰度闭环这件事无人做——CaseLoop-for-Agents 的缺口真实存在，且双臂对照所需的 fork 原语恰好刚被沙箱厂商产品化，时机正好。</subagent>
