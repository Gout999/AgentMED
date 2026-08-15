# 赛道5 调研报告：Agent 版本化与确定性重放的工程实践

> **阅读口径更新（2026-08-10）**：本文是 2026-08-09 的研究快照。六元组是 MVP 假设，不是最终通用标准；memory/RAG、policy、secret grant、network policy 和外部依赖是否入版本集仍待讨论。“半回放”在正文中同时指工具结果回放和冻结环境 live 执行，后续契约必须拆开。sandbox/replay 默认优先复用成熟原语，但当国内部署、数据边界、可靠性或退出机制不满足时保留兼容性重实现权。

## 1. 全景地图

本轮样本可分成四类：**配置版本化**（LangGraph assistants、Claude Code 文件化配置、Langfuse/PromptLayer prompt 版本库、AGENTS.md/SKILL.md）；**协议版本协商**（MCP per-request 版本声明）；**录制回放**（VCR/cassette 系、agent-cassette、LangSmith/Phoenix trace replay）；**环境固化**（SWE-bench 三层 Docker 镜像、Terminal-Bench/Harbor per-task 镜像、E2B/Modal/Daytona 沙箱快照）。本轮样本未见单一实现把 prompt、skills、tool schema、model、harness 和环境镜像绑成同一可哈希、可审批、可归因版本集；这只是一个待验证的组合需求，不是全球空白结论。

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
是什么：`.claude/settings.json`（127+ 设置，权限/hooks 硬强制层）、`.claude/agents/*.md` 子 agent 定义（frontmatter）、`CLAUDE.md`、`.mcp.json`、skills 目录，设计为提交 git（`settings.local.json` 自动 gitignore）。和我们的关系：agent 配置"天然是文件、天然可 hash"——AgentMED 的版本集 hash 对这类 agent 几乎零成本落地。注意已暴露的弱点：frontmatter 字段被全局设置静默覆盖（issue #64706），文件化≠强一致。[claudefa.st/blog/guide/settings-reference](https://claudefa.st/blog/guide/settings-reference)（2026-08）

**OpenAI Prompts / Agents SDK**
做到什么程度：dashboard 创建的 prompt 有版本号，Agents SDK 可按 id+version 引用；但 Prompt Objects API 将于 **2026-11-30 下线**，官方迁移方向是"代码内 versioned prompts"——即承认 git 化才是归宿。【事实，截至 2026-06】[community.openai.com/t/.../1382593](https://community.openai.com/t/deprecation-notice-prompt-objects-in-the-api-will-be-shut-down-on-november-30th-2026/1382593)

**Agent Skills（SKILL.md）开放标准**
2025-12-18 Anthropic 发布为开放标准（agentskills.io），截至 2026 Q1 已有 26–30+ 平台采纳（Cursor、VS Code、Copilot、Gemini CLI、Codex），复制了 MCP 的扩散轨迹。和我们的关系："skill 清单"作为版本集组件有了跨厂商标准格式，且社区共识是"skills 要像代码一样版本化+打 tag+配 eval 套件防 description-drift"。[articsledge.com/post/skill-engineering](https://www.articsledge.com/post/skill-engineering)（2026-07）

**agent-cassette（VCR 式回放的代表）**
是什么：录制 LLM+tool calls 为 JSONL，replay 时按语义 hash 匹配返回录制结果（0ms、0 token）。做到什么程度：v0 显式 wrapper 稳定可用，Docker 化；其招牌用例正是"Golden Run 录制后，CI 回放证明 GPT-4o→GPT-5 升级不破坏 JSON schema"。和我们的关系：证明 cassette 模式对 agent 成立，但它只做全回放，对"模型质量变化"零灵敏度——适合做 harness 回归，不能做门禁裁决。GitHub `record-replay` topic 下还有 42 个同类项目（含 Go 写的 OpenAI/Anthropic/SSE/WebSocket VCR 代理、TS SDK 边界录制），说明这是活跃公共模式。[github.com/yuan-cloud/agent-cassette](https://github.com/yuan-cloud/agent-cassette)（2026-01）；[github.com/topics/record-replay](https://github.com/topics/record-replay)

**SWE-bench harness（环境固化事实标准）**
三层 Docker 镜像（base/env/instance）+ `run_evaluation` harness；Docker 强制（FAQ 原话："No. Docker is required"）；缓存全量 instance 镜像约 2000GB；gold patch 模式先验证基准自身的可解性。和我们的关系：可把它作为 coding Agent 环境固化与 reference-solution 验证的候选基线，再按隔离、数据驻留、成本和任务适配性验收。[swebench.com/SWE-bench/reference/harness](https://www.swebench.com/SWE-bench/reference/harness/)

**Terminal-Bench 2.0 + Harbor（任务封装与判分器自验证）**
任务 = instruction + Docker image + tests + example solution + time limit 五元组；89 个任务全部 3 人独立人工验证；Harbor 框架每任务配专属 Docker 镜像，**Oracle agent 跑 reference solution 必须拿满分——既验证任务可解，也验证 verifier 本身正确**。[tbench.ai/news/announcement-2-0](https://www.tbench.ai/news/announcement-2-0)（2025-11-07）；[arxiv.org/html/2601.11868v1](https://arxiv.org/html/2601.11868v1)

**沙箱快照/分叉（E2B/Modal/Daytona）**
实测冷启动：E2B 717ms create/662ms resume（Firecracker，内存+文件系统真 pause/resume）；Daytona 742ms/1254ms（Docker 默认，Kata 可选）；Modal 2437ms create，`snapshot_filesystem()` 产出可复用镜像，可 fork N 个沙箱从同一 prepared state 起跑。LogRocket 原文："Pause/resume 回答'agent 能否继续'，snapshot/fork 回答'agent 能否分支'——**从固定基线跑多条轨迹的 eval harness 要的是后者**"。和我们的关系：双臂对照实验（同基线 fork 两臂）的基础设施已被产品化，不用自建。[blog.logrocket.com/comparing-ai-agent-sandbox-platforms...](https://blog.logrocket.com/comparing-ai-agent-sandbox-platforms-e2b-modal-daytona-and-more/)（2026-08-05）

**CRIU/docker checkpoint（进程级快照，不成熟）**
Docker checkpoint 至今 experimental；5GB 堆 checkpoint 40s/restore 20s；Ubuntu 24.04+kernel 6.8 直接挂起（issue #2516）；K8s 原生支持有限。和我们的关系：对 coding agent 场景不需要——环境状态=文件系统+依赖，进程内存可丢弃。【事实，截至 2026 年仍 experimental】[github.com/checkpoint-restore/criu/issues/2516](https://github.com/checkpoint-restore/criu/issues/2516)；[devzero.io/blog/checkpoint-restore-with-criu](https://www.devzero.io/blog/checkpoint-restore-with-criu)（2025-07）

**CycloneDX ML-BOM / SPDX 3.0 AI Profile（AIBOM）**
覆盖 model/dataset/agent/SDK 清单，面向供应链安全与监管申报（OWASP/Linux Foundation 双标准）。和我们的关系：清单格式可借，但它不管"哪个组件变化导致行为变化"，更没有绑定审批 hash。[cyclonedx.org/capabilities/mlbom](https://cyclonedx.org/capabilities/mlbom/)

**Provider 端确定性的天花板**
OpenAI `seed`+`system_fingerprint` 只是 best-effort；论文实测同一模型换推理后端，greedy decoding 下输出仍有方差，jailbreak 率波动近 9%，建议多 seed 平均。和我们的关系：全 live 评测的噪声地板客观存在，AgentMED 的 Δ+95%CI 统计设计恰好是对症方案。[arxiv.org/html/2605.19537v2](https://arxiv.org/html/2605.19537v2)（2026-05）

**ByteDance DeerFlow 评测平台 RFC（交叉验证）**
其 roadmap 明写"Capture model/config/commit/tool/suite fingerprints"+ 复用 replay golden 基础设施——一线团队的 agent 评测平台正在收敛到"指纹采集+回放"组合，与我们版本集思路同向，但同样没有做到 hash 绑定审批。[github.com/bytedance/deer-flow/issues/4083](https://github.com/bytedance/deer-flow/issues/4083)（2026-07-11）

## 3. 与 AgentMED 的对照表

| 可优先评估复用 | 需适配或验证 | 本轮样本未见相同组合 |
|---|---|---|
| 任务环境固化 = per-task Docker 镜像 + digest pin（以 SWE-bench/Harbor 为候选基线） | VCR/cassette 全回放 → 增加工具结果回放+live LLM 等分级 | **统一可哈希 Agent 版本集**：本轮样本未见相同六元组合；完整组件仍待需求冻结 |
| agent 配置 git 化（Claude Code `.claude/`、AGENTS.md、SKILL.md 天然是文件，hash 零成本） | LangGraph assistant 版本概念 → 借用"config 与 logic 分离"，但要补上环境、证据链、审批 hash | **半回放工具**：LangSmith/Phoenix replay 是全 live 调试定位，agent-cassette 是全回放，中间层没人做 |
| 双臂对照实验的执行底座 = 沙箱 snapshot/fork（Modal/E2B/Daytona 原语已产品化） | AIBOM（CycloneDX ML-BOM）→ 借清单格式，加运行时归因语义 | **"对照实验"产品形态**：fork 原语有了，但"同基线两臂跑两个 agent 版本、出 Δ+CI"没有现成系统 |
| 判分器自验证 = Oracle agent/reference solution 模式（Terminal-Bench） | Langfuse/PromptLayer 的版本+label → 按用户需要扩展到更完整 VersionSet | provider 端模型漂移在本轮样本中主要是观测能力，如何纳入治理仍待验证 |
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

## 5. 对 AgentMED-for-Agents 的设计启示

1. **把六元组作为可扩展 MVP 假设**：system prompt + skill manifest + tool schema + model snapshot + harness commit + environment image。后续根据可重现性、审批和用户工作加入 memory/RAG、policy、secret/network 等组件，不能直接定成通用标准。
2. **sandbox/fork 采用 adapter-first**：优先评估 Modal、E2B、Daytona 等原语；按隔离、数据驻留、自托管、可靠性、成本和退出机制验收。不满足国内用户条件时保留兼容性重实现权。
3. **探针回放分级，明确"全回放不能用于门禁裁决"**：harness/管线回归用全回放（cassette，零成本进 CI）；归因实验用半回放（冻结环境镜像+live LLM，即 SWE-bench 被 leaderboard 验证过的保真度/成本平衡点）；门禁终审用全 live+多 seed。这条是对现有"双轨评测门禁"的**修改/增补**：三态裁决需要注明证据来自哪一层回放，因为全回放对模型质量变化零灵敏度。【推断】
4. **判分器自验证参考 Terminal-Bench Oracle 模式**：探针集发布前用 reference solution 检查任务可解与 verifier 行为，并结合人工审查和版本化；不能把单一模式当成所有任务的充分证明。
5. **环境固化放弃进程级快照路线**：CRIU/docker checkpoint 截至 2026 仍 experimental；coding agent 的环境状态=文件系统+依赖，image digest+volume snapshot 已够。**放弃 CRIU，保留 Docker digest 锚定**。
6. **优先参考已有成熟部分**：配置版本管理、prompt 版本库、环境固化和回放工具都可作为组件或协议基线。是否复用、适配或实现自己的兼容能力，以目标用户和 `docs/product-principles.md` 的工程标准决定。

## 6. 当前综合结论

环境固化、配置版本和录制回放已有可复用基线。AgentMED 需要从重现、审批、归因和用户部署要求推导一个可扩展的最小 VersionSet，并明确各 replay level 的证据能力；本轮样本未见完全相同组合，但这不等于全球无人做，也不自动说明六元组已经定稿。
