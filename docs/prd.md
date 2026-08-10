# CaseLoop 产品需求文档（PRD）

> 版本：v1.0 ｜ 日期：2026-08-07 ｜ 状态：草案待评审
> 适用范围：当前“小智客服纵切”参考工作负载的 v1 PRD，不是通用 Agent 产品 PRD。产品战略与范围取舍以 `docs/product-principles.md` 为准；当前实现约束以 `docs/plan-v3.md`、`contracts/` 和可执行测试为准。
> 本文档不引入 plan-v3 之外的架构决策。通用 Agent、Langfuse/TraceSource、Signal/Closure 适配器和第二工作负载已经由 `docs/prd-v2.md` / `docs/plan-v4.md` 批准并进入 Stage 0 target contracts，但尚无相应 migration/runtime/live 证明；不能从本 v1 文档或 target contract 推断为已实现。
> 读者：目标用户、产品、工程、安全与运营协作者。比赛评委材料单列在 `docs/competition/`；技术实现细节见 `docs/spec.md`。

---

## 1. 产品定位

### 1.1 一句话叙事

**CaseLoop 是 AI 应用质量治理的 meta 层（自治底座）：任何 LLM 应用只要实现 Quality API 契约即可被纳管，多 Agent 团队自动完成 badcase 全生命周期闭环——投诉进来 → 对照实验归因 → 自由起草修复 → 评测门禁 → 灰度发布 → 回复投诉原处 → 沉淀为评测与知识资产。**

### 1.2 我们不是什么：区别于「方向二智能客服」

CaseLoop **不是客服工单系统，也不是又一个客服机器人**。演示应用「小智客服」只是被治理对象（病人），CaseLoop 是给任意 LLM 应用看病的「医疗体系」：客服场景只是第一个病种。判断标准很简单——把小智客服换成任何其他 LLM 应用（代码助手、营销文案、法务问答），只要它实现 Quality API，CaseLoop 的闭环原样运转。这正是"治理层 / meta 层"与"做一款智能客服产品"的本质区别。

### 1.3 核心理念

| 理念 | 含义 |
|------|------|
| 确定性控制面 + 概率性执行面 | AI 负责动脑子，系统负责管规矩；**LLM 永远不是状态与权限的权威源** |
| 信任是挣来的，且有数学纪律 | Trust Ledger 按 risk_class × autonomy_state 二维记账，晋升判据是 Wilson 双侧 95% 下界 >0.9，不是感觉 |
| 组织是活的 | 4 常设 Agent + 2 类弹性 Agent，为 AgentTeams 补上其缺失的弹性面（Caseload Controller） |
| 每次事故让系统变强 | 案例即资产：badcase 沉淀为评测集与知识库，门禁不只考应用，也考 Agent 自己 |

### 1.4 问题陈述

LLM 应用上线后，质量事故（badcase）的处理仍是手工作坊：用户在群里骂 → 运营截图转给算法 → 算法凭经验猜是 prompt、知识库还是模型的问题 → 改完直接上线或随便回归一下 → 同样的 badcase 下个月换个姿势再来一次。痛点有四：

1. **归因靠猜**：prompt / 知识库 / 模型参数三个变量纠缠，没有对照实验，修复靠玄学；
2. **修复无门禁**：改完 prompt 直接上线，没有回归评测，修一个坏三个；
3. **发布无灰度**：全量切换，出问题再手忙脚乱回滚；
4. **经验不沉淀**：每次处理完就散了，评测集和知识库不生长，系统不会变强。

CaseLoop 把这条链路变成一个有统计纪律、有门禁、有灰度、有沉淀的自动化闭环。

---

## 2. 目标用户与使用场景

### 2.1 目标用户

| 用户 | 画像 | 核心诉求 |
|------|------|---------|
| LLM 应用团队 | 用 LLM 构建对话/问答/生成类应用的工程团队（自有模型或调 API 均可） | badcase 有人管、修得准、发得稳，不用养一个专职"擦屁股"小组 |
| Agent 应用团队 | 基于多 Agent 框架构建业务应用的团队 | Agent 自身能力（Skill/工具/归因规则）的变更也要走评测门禁，防止"Agent 越改越笨" |

### 2.2 典型使用场景

| # | 场景 | 说明 |
|---|------|------|
| S1 | 投诉自动闭环 | 用户在飞书群投诉回答错误 → 系统自动立案、归因、修复、门禁、灰度、回复原群，人只在发布前按一次确认 |
| S2 | 定期质量巡检 | 变异巡检器周期性地用变异算子攻击被治理应用，主动发现潜在 badcase，输出质量周报（检出率/门禁拦截率趋势） |
| S3 | 多应用纳管 | 第二个 LLM 应用实现 Quality API 适配器后接入，同一套治理闭环零改动复用（Phase 2 证明可复制性） |
| S4 | Agent 能力自演化 | 案例官从历史 badcase 起草 Skill 改进候选，经 holdout 回放与人工批准后上架——Agent 自身变更与应用修复走同一条 eval 门禁管道（Phase 2） |

---

## 3. 核心闭环用户旅程

单条 badcase 的完整旅程（以 B1 prompt 回归为例）。关键纪律：**状态与权限的权威源永远是控制面（Case Controller / Release Controller），Agent 只产出建议与产物**。

| 步骤 | 名称 | 谁在做什么 | 关键产物 |
|------|------|-----------|---------|
| 1 | 投诉接入 | 用户在**飞书群**投诉；webhook 推送 + 轮询兜底双通道到达 **Case Controller**（非 LLM 组件），inbox 去重后立案；重复投诉合并到已有 case | `case` 记录（权威状态） |
| 2 | 分诊 | **采集员**（常设 Agent）领单，调用 `GET /logs`、`GET /feedback` 取证，把投诉翻译成结构化 badcase 与候选探针 | badcase 简报、取证引用 |
| 3 | 对照实验归因 | **归因师**（弹性 Agent）按归因实验协议跑 5-cell 最小矩阵（C/RP/RK/RM/G），输出 Δ 效应量 + 95% CI + 三态裁决（ATTRIBUTED / INCONCLUSIVE / CONFOUNDED）；INCONCLUSIVE 补实验或升级人工，CONFOUNDED 强制 2³ 全因子 | 归因实验报告（机器可验证） |
| 4 | 自由起草修复 | **修复师**（弹性 Agent）按归因结论选择修复通道（prompt git 化 / KB 修订 / 模型参数切换），自由起草修复内容，产物为**不可变 WorkOrder**（hash 绑定目标/输入版本/diff/门禁报告/expiry/nonce），遵守单变量纪律 | WorkOrder（不可变） |
| 5 | 评测门禁 | **守门员**（常设 Agent）经 eval-runner 跑双轨门禁：规则轨 + LLM 裁判轨（裁判模型 ≠ 运动员模型）；contract/replay 确定性测试与 live-provider E2E 分开报告；门禁报告成为 WorkOrder 的必要附件 | 门禁报告 |
| 6 | 审批 | 门禁通过后，系统在飞书向人提请审批；审批即批 WorkOrder hash（防掉包、防重放） | ApprovalGrant |
| 7 | 灰度发布 | **Release Controller**（非 LLM，唯一能调 Quality API 写面的组件）执行 draft→stage→canary→promote，全程 CAS revision；异常进 UNKNOWN→reconcile；指标不达标自动 rollback | 发布记录 |
| 8 | 回复投诉原处 | 发布验证通过后，经 notification 通道把处理结果**回复到原飞书群**（哪来回哪），闭环收口 | 回复消息（留痕） |
| 9 | 沉淀 | **案例官**（常设 Agent）把全链路证据归档：badcase 进回归评测集、案例进 pgvector 案例库、信任账本记一笔、证据包落库 | 评测资产 + 案例资产 |

人工介入点全程开放：任意时刻人可接管（人工接管后自动流转暂停）；审批是发布的强制关卡（MVP 阶段全部动作逐次审批，信任只记账不晋升）。

---

## 4. 被治理演示应用「小智客服」设定

「小智客服」是 CaseLoop 的第一个被治理对象，一个**刻意保持简单但完全真实**的 LLM 应用：

| 维度 | 设定 |
|------|------|
| 业务 | 3C 数码电商客服：回答售后政策、产品参数、物流规则三类问题 |
| 技术栈 | FastAPI + RAG；LLM 全部真实调用 StepFun（无 mock 层）；prompt 模板 git 版本化 |
| 知识库 | pgvector 向量库，种子数据 = 售后政策 / 产品参数 / 物流规则三类文档 |
| 治理接口 | 实现 Quality API v2（VersionSet / draft→stage→canary→promote/rollback→status / GET /logs / GET /feedback） |
| 故障注入 | 内置 B1–B4 注入端点（仅演示环境启用），每个注入对应一份 ground-truth fixture |
| 可观测 | OTel trace 输出；反馈端点接收用户评价 |

选型纪律：LLM 用 StepFun 是**刻意异构**——证明治理层模型无关，可一键切换 Qwen 等任意 OpenAI 兼容端点。

---

## 5. 故障场景定义（B1–B4）

B1–B4 是四类注入故障，定位为：**可重复演示 + ground-truth 使归因结论可机器验证**。注入不是"造假演示"，而是让归因正确性有了客观判卷标准——归因实验报告的结论（故障层）必须与注入的 ground-truth 一致才算 ATTRIBUTED 成立。演示之外，真实 badcase 语料来源（用户投诉语料）作为背书补充。

| # | 故障类型 | 定义 | 预期归因表现（5-cell 矩阵） |
|---|---------|------|---------------------------|
| B1 | prompt 模板回归 | prompt 模板被改动（如售后政策模板丢失关键约束段落），回答开始编造规则 | 仅 RP 臂（换旧 prompt）恢复 → 归因 prompt 层 |
| B2 | KB 知识过时 | 知识库内容过时（如保修政策已更新但 KB 未更新），检索到旧政策导致错误回答 | 仅 RK 臂（换旧/正确 KB manifest）恢复 → 归因 KB 层 |
| B3 | 模型参数漂移 | 模型版本或采样参数漂移（如模型被切换或 temperature 被误改），回答质量漂移 | 仅 RM 臂（换回原模型+参数）恢复 → 归因模型层 |
| B4 | 多因素交互 | 两层同时带病（如 prompt 回归 + KB 过时并存），单独回滚任一层都不恢复 | 多臂同恢复或均不恢复 → 强制 2³ 全因子识别交互项 |

分层兑现节奏：**Phase 1 只承诺 B1 纵切**（一条可信纵切，全链路证据）；B2–B4 在 Phase 2 全量兑现。

---

## 6. 功能需求

按 plan-v3 §4 的分阶段路线分层。FR 编号供 spec 与测试追溯。

### 6.1 Phase 1 · 一条可信 B1 纵切（对应初赛）

Phase 1 的承诺边界：**B1 纵切 + 固定 warm pool + 信任记账但拒绝晋升 + 单次巡检周报**。不承诺动态扩缩、不承诺 B2–B4、不承诺信任晋升。

| FR | 需求 | 验收要点 |
|----|------|---------|
| FR-P1-01 | 投诉接入：飞书 webhook + 轮询兜底，统一经 Case Controller inbox 去重后立案；重复投诉合并 | 同一投诉重复投递只立案一次；合并事件追加到主 case |
| FR-P1-02 | B1 全闭环：投诉→归因→修复→门禁→审批→灰度→回复原群→归档，端到端自动运行（审批为人） | e2e 断言全绿：归因裁决=ATTRIBUTED 且故障层=prompt、门禁报告存在、灰度后 promote、原群收到回复、案例归档 |
| FR-P1-03 | 单层归因实验：5-cell 最小矩阵（C/RP/RK/RM/G），冻结探针集（discovery + hidden confirmation），输出 Δ 效应量 + 95% CI + 三态裁决 | 归因报告含完整版本 digest、每臂统计数据、裁决理由；结论与 B1 ground-truth 一致 |
| FR-P1-04 | 修复 WorkOrder：修复师自由起草，产物不可变（hash 绑定目标/输入版本/diff/门禁报告/expiry/nonce），单变量纪律 | WorkOrder schema 校验通过；hash 变更即失效 |
| FR-P1-05 | 评测门禁双轨：规则轨 + LLM 裁判轨（裁判模型 ≠ 运动员模型）；contract/replay 确定性测试与 live-provider E2E **分开报告** | 门禁报告双轨分列；门禁不通过则 WorkOrder 不可进入审批 |
| FR-P1-06 | 灰度发布：Release Controller 唯一可调 Quality API 写面；draft→stage→canary→promote\|rollback 全程 CAS（If-Match/expected_revision + idempotency-key + 异步结果查询） | 异常注入（控制器重启/结果未知）后 reconcile 到正确终态；可演示 rollback |
| FR-P1-07 | 飞书原群回复：处理结果回复到投诉原群，双向留痕 | 原群可见回复；消息与 case 关联可查（飞书凭证为前置条件，未到位时以 feishu mock 为明示降级路径） |
| FR-P1-08 | 信任账本记账：每次动作按 risk_class × autonomy_state 记账（evidence epoch 原始整数计数，一次动作=一个样本）；MVP 口径**记账但拒绝晋升** | 三轮 B1 后断言拒绝晋升事件：3/3 的 Wilson 双侧 95% 下界 0.44 < 0.9；Wilson 测试向量全过 |
| FR-P1-09 | 单次变异巡检 + 质量周报：变异算子库 → 探测用例 → 单次攻击 → 飞书周报 | 周报含变异用例数 / 检出率 / 门禁拦截率 |
| FR-P1-10 | Agent 组织就位：4 常设（质量官/采集员/守门员/案例官）+ 弹性模板（归因师/修复师），**固定 warm pool**，钉 AgentTeams v1.2.1 | team.yaml + 6 SOUL.md；不宣称动态扩缩 |
| FR-P1-11 | 案例沉淀：badcase 进回归评测集，案例写 pgvector 案例库 | 归档后案例可检索；回归集条目可用于下次门禁 |

### 6.2 Phase 2 · 横向证明（对应复赛）

| FR | 需求 | 验收要点 |
|----|------|---------|
| FR-P2-01 | B2–B4 全场景闭环：KB 层/模型层/多因素交互 | B4 场景强制 2³ 全因子并正确定位交互项；结论与 ground-truth 一致 |
| FR-P2-02 | 第二个极小 Quality API 适配器：证明治理层可复制、非为小智客服定制 | 第二应用接入后闭环原样运转 |
| FR-P2-03 | Caseload Controller 真实 create/drain/remove：Agent 申请、控制面决策执行；缩容走 DRAINING→停止新 claim→lease=0→outbox 清空→摘出 Team→Sleep/Delete→**资源凭证对账**（CR + 容器 + Matrix 房间 + 对象存储用户四样齐全才算成功） | 对账失败不进 Sleep；drain 过程不丢 case |
| FR-P2-04 | 周期巡检：变异巡检按周期运行，自评分趋势（检出率/归因准确率/门禁拦截率/一次通过率） | 多期周报趋势可查 |
| FR-P2-05 | Skill 自演化（兑现到"候选 + holdout 回放 + 人工批准"）：案例官起草能力变更候选 → 历史 badcase 时序 holdout 回放 → 人工批准上架 → 能力注册中心版本化 | 供应链未完备前不自动上架；候选未批准不上架 |

### 6.3 Phase 3 · 决赛集成与硬化（对应决赛）

| FR | 需求 | 验收要点 |
|----|------|---------|
| FR-P3-01 | 云产品集成：LoongSuite（OTel GenAI）/ PolarDB（替换本地 pgvector，同接口） | trace 入 LoongSuite；存储切换仅连接串差异 |
| FR-P3-02 | 硬化：故障演练、SLO 定义与度量、备份恢复 | 演练报告；备份恢复演练通过 |
| FR-P3-03 | 安全与租户隔离 | 多租户数据隔离验证 |
| — | 边界声明 | **Phase 3 不宣称"生产完成"**；Production Go 以 Phase 4 真实 SaaS pilot 为准 |

---

## 7. 非功能需求

| NFR | 需求 | 说明 |
|-----|------|------|
| NFR-01 | 确定性控制面 | 所有状态迁移、权限裁决、lease、幂等、outbox 由非 LLM 组件（Case/Release/Caseload Controller）执行；LLM 输出永远只是"建议"，控制面持有全部权威状态 |
| NFR-02 | 审计权威源 = 数据库 | 审计写入数据库权威存储，**写失败即拒业务（不放行）**；audit.jsonl 仅是导出物，不是权威源（zeroops 的 audit.py 失败放行模式不沿用） |
| NFR-03 | PII 入口脱敏 | 投诉与日志在入口侧脱敏后才进入流水线与文档链 |
| NFR-04 | 审批防重放 | 审批绑定 WorkOrder hash + nonce + expiry，一次性消费；静态 token 可重放模式（zeroops common/approval.py）不沿用，重写仅参考 |
| NFR-05 | 演示可重复性 | LLM 调用 temperature=0 + 冻结探针集 + B1–B4 ground-truth fixtures，保证演示确定性、归因结论机器可验证 |
| NFR-06 | 模型无关性 | 治理层不绑定具体模型；StepFun 为刻意异构选择，可一键切换 Qwen 等 OpenAI 兼容端点；裁判模型 ≠ 运动员模型 |
| NFR-07 | 契约稳定性 | Quality API 以 OpenAPI 冻结并附 conformance suite；契约可对空实现跑红后再实现 |
| NFR-08 | 写面唯一入口 | Quality API 写面仅 Release Controller 可调用；任何 Agent 不可直连写面 |

---

## 8. 量化价值叙事

> 以下基线为**行业常识数字，标注"行业基线，待自测校准"**——Phase 1 结束后用自测数据替换。

| 指标 | 行业基线（待自测校准） | CaseLoop 目标 |
|------|----------------------|---------------|
| badcase MTTR（投诉→修复上线） | 2–5 个工作日：客诉流转半天、人工复现与定位 1–2 天、修复与回归 1 天、发布半天 | B1 类单因素 badcase 演示闭环**分钟级**（审批即时前提下） |
| 单 case 人力消耗 | 3–5 人·日（客服运营 + 算法 + 评测 + 发布串行接力） | **1 次审批动作**（分钟级）；人只在门禁后的发布关卡出现 |
| 归因方式 | 凭经验猜，改错层返工常见 | 对照实验 + Δ 效应量 + 95% CI + 三态裁决，归因结论机器可验证 |
| 回归保障 | 改完 prompt 直接上线，修一个坏三个 | 双轨门禁 + 灰度 + 自动回滚，回归评测集随案例库持续生长 |
| 经验沉淀 | 处理完即散，评测集不生长 | 每条 badcase 自动沉淀为评测与知识资产，系统越用越强 |

长期叙事：质量治理是每支 LLM 应用团队都绕不开的公共底座。CaseLoop 以杭州为落地起点（呼应生态沉淀意图），先以比赛完成 B1–B4 场景验证与 AgentTeams 弹性面回馈（Caseload Controller 整理为上游 issue/PR），Phase 4 走向真实 SaaS pilot。

---

## 9. 成功度量

对应 plan-v3 §4 各 Phase 出口标准。

### 9.1 初赛（Phase 1 出口）

- e2e 全绿 + 初赛三件套（500 字简介 + 方案 PPT + 代码包）提交；
- B1 全闭环证据链齐全：飞书原群回复、单层归因实验报告（Δ+CI+裁决，且裁决=ATTRIBUTED、故障层=prompt）、门禁报告、灰度与回滚演示、归档记录；
- contract/replay 确定性测试与 live-provider E2E **分开报告**；
- 信任账本：Wilson 测试向量全过；三轮 B1 后断言**拒绝晋升**事件（3/3 下界 0.44 < 0.9）；
- 巡检：周报含变异用例数 / 检出率 / 门禁拦截率；
- conformance suite 对实现全绿（契约先行，曾跑红）。

### 9.2 复赛（Phase 2 出口）

- 可运行 Demo + 评测报告；
- B2–B4 全场景闭环，B4 的 2³ 全因子正确定位交互项；
- 第二个极小 Quality API 适配器接入并跑通闭环（可复制性证明）；
- Caseload Controller 真实 create/drain/remove + 资源对账演示；
- 周期巡检多期周报与自评分趋势；
- Skill 候选 + holdout 回放 + 人工批准上架演示。

### 9.3 决赛（Phase 3 出口）

- 决赛演示就绪：LoongSuite/PolarDB 集成、故障演练报告、SLO 度量、备份恢复演练、安全与租户隔离验证；
- 明确边界：**不宣称"生产完成"**，Production Go 留给 Phase 4 真实 SaaS pilot（第二租户、真实回滚、数据删除/SLO 达标）。

---

## 10. 开放问题（【待定】汇总）

| # | 问题 | 当前处理 | 待决策方 |
|---|------|---------|---------|
| 1 | 飞书自建应用凭证到位时间（用户操作，Phase 1 前置） | 未到位前以 feishu mock 为明示降级演示路径 | 用户 / 赛程 |
| 2 | 信任晋升（AUTO_ENABLED）完整演示放在哪个 Phase | plan-v3 仅明确 MVP 口径"记账但拒绝晋升"，晋升路径已设计（ELIGIBLE→AWAITING_CONFIRMATION→AUTO_ENABLED）但演示时点未定 | 评审对齐后定 |
| 3 | MTTR/人力基线数字的自测校准 | 现用行业常识数字并已标注，Phase 1 后以实测替换 | Phase 1 出口时 |
| 4 | 双模型（运动员/裁判）额度是否支撑全程双轨 live E2E | 0A Spike 扫雷项；额度不足时 live 轨缩小探针规模，确定性轨不受影响 | Spike 结论 |
| 5 | 杭州落地/长期计划的具体形态（开源回馈节奏、社区运营） | 已有叙事方向（Caseload Controller 回馈上游、质量治理底座），未细化 | 复赛材料前 |

---

*本文档与 `docs/spec.md` 互为配套：PRD 回答"做什么、为什么、做到什么程度"，SPEC 回答"怎么精确实现"。两者冲突时以上位文档 `docs/plan-v3.md` 为准。*
