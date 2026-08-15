# Flow-first 收敛计划（流程粒度）

> 依据：D-015 范围裁决（flow-first / Agent Station / 出口 2）。本文只谈流程与卡点，不谈技术 stage。

## 七段闭环收口施工计划（2026-08-14 实测定稿）

总目标：真实跑通 段1-6，以出口 2（VerifiedCandidate / NOT DEPLOYED）收口；段7（部署观察回滚）后置。

| 段 | 现状（实测） | 施工 | 验收证据 |
|---|---|---|---|
| 1 立案 | ✅ 真实（langfuse 信号→公共案+内部聚合案双写） | 无 | 已闭环 |
| 2 取证 | 🟡 collector 接单未验证落库；langfuse 技能未部署到 collector；证据无落库路径（receipt missing_fields 未补） | ① langfuse 技能+凭证经平台 skill_pool 分发；② collector 拉真实 trace/score 写 shared/tasks；③ leader 把 evidence_refs 汇入 case 时间线 | receipt missing_fields 减少 |
| 3 归因 | ❌ mcp_eval_runs=0；eval-harness 对照实验指向 demo-app Quality API（被治理对象已换 Agent Station，实验面未改） | 定义 Agent Station 实验形态：badcase 回放对照（原 prompt vs 候选，经 8088 模型路径，probe_judge 判定）；attributionist 跑一次真实实验 | eval_runs=1 + Δ+95%CI 三态裁决落库 |
| 4 修复 | ❌ mcp_workorders=0；工具与控制面齐、未运转 | repairer 出候选补丁（prompt/配置）+ workorder hash 绑定 | workorders=1 |
| 5 沙箱验证 | ❌ gate_reports=0；「把测试 agent 拿进容器跑」从未真实发生（eval-harness 是 API 级评测非容器沙箱） | 建沙箱 runner 薄层（隔离容器跑坏例回放，修前 fail/修后 pass 观测对比）；gate.run/gate.run_verification 走它 | gate_reports=1 + 修前/修后 digest |
| 6 人工放行 | 🟡 机制四缺二：Matrix 审批请求通知未接（A2）、reader 未建；CLI decide ✅、approval.request ✅ | ① approval.request 发 Matrix 审批请求（A2 通知写真 Matrix）；② 建 reader（读 Matrix 事件→验 nonce→grant/consume_nonce→落 VerifiedCandidate） | grant 落库 + case 到 VerifiedCandidate/NOT DEPLOYED（出口 2） |

全局前置：① 每段开跑前探活模型路径（8088/relay；stepfun 外部不稳定，4-worker+本地 models 已降误杀）；② agentmed 脏工作区（V5-1A/B/C 162 文件，818 单测已绿）按纪律语义提交，为 A2 通知适配器改动清路。

顺序与依赖：段2→3→4→5→6 串行（取证喂归因、归因喂修复、候选喂验证、验证报告喂审批）；每段完成 = 一段真实演示 + DB 证据 + 审计日志更新；全程两个人工卡点不变（机器统计判决 + 审批 nonce）。

## 目标闭环（一段投诉跑全程）

```
投诉(signal) → 立案(case) → 取证(evidence) → 归因(attribution) → 修复(fix)
   → 沙箱验证(verification) → 人工卡点(approval) → VerifiedCandidate / NOT DEPLOYED
```

每一段都有：谁做（哪个角色）、从哪拿输入、输出什么、失败去哪（Escalate 或归档）。
全链复用存量服务：signal_intake / case_service / attribution / gate_service /
approval / outbox_relay / eval-harness。

## 五步施工（每步 = 一段闭环，做完即可演示）

### 第 0 步：去冗余砍代码（先于闭环，owner 已拍板）
- A 替换原生：审批走 Human CR Matrix（CLI/MCP 封装不变）；通知/留痕写真 Matrix；领单/心跳交平台任务管理（fencing 保留）；skill 分发走 skill_pool + file-sync；git 走 git-delegation；MCP 挂载走 mcporter（保留角色 ACL 投影思想）；启用 model-switch 与空闲自动停机；
- B 合并重构：复核后无死代码可归档——改为四个合并包（事件存储/authority/audit/v4 API 退役），前置条件 = V5-1A/B/C 收口 + 专用分支；
- C 精简：acceptance 冻结到 V5-4 前；Wilson 统计简化；三份自动机收敛为一份权威；7 状态机精简；proxy 遥测剥离；
- D 重定向：store→agentteams-fs+MinIO；runtime-local→worker-management；api→平台 API 薄适配；cli→agt CLI；
- 依据与逐项清单：`docs/plans/native-capability-audit.md`（16 项冗余判定表）；
- **完成=能演示**：审计表 16 项逐项处置闭环，第 1 步在原生通道上起步。

### 第 1 步：部署治理团队（agent team 上线，全走原生）
- 被治理对象接入登记：Agent Station 作为 Application，其组件（代码 / prompt / 模型）挂到 Langfuse 可观测；
- 六角色团队（quality-officer / collector / gatekeeper / case-officer / attributionist / repairer）+ 人工审批人（agentmed-approver，Human CR 接 Matrix 审批）部署就位；skills 挂平台 skill_pool 角色级 skill，不再挂单巨 skill；
- **完成=能演示**：人工触发一条投诉信号，quality-officer 判定立案并分发。

进度（2026-08-14）：✅ 团队已部署且 Active（6 worker Running、ReadyWorkers 5/5、Human CR Active）；待办：agentmed MCP servers 平台注册（mcporter）+ 控制面拉起（PG/迁移）+ 角色级 skills 分发。

### 第 2 步：接 Langfuse 信号（投诉入口跑通）
- 读 Langfuse 观测（scores / observations）→ 负向信号 → 立案；
- 取证：collector 从 Langfuse 拉 trace 与 score，落 evidence；
- **完成=能演示**：一条真实负分 trace 自动立案并带证据。

### 第 3 步：归因 + 修复（闭环核心）
- 归因：定位是代码 / prompt / 模型 / 环境，落 Attribution；
- 修复：repairer 出补丁，落 Gate（不发布）；
- **完成=能演示**：同一 badcase 定位到具体组件并给出补丁。

### 第 4 步：沙箱验证（机器放行前的证明）
- 补丁进入沙箱：跑坏例回放 + 回归，出观测对比（修前 vs 修后）；
- **完成=能演示**：沙箱报告展示「修前 fail / 修后 pass」，可复核。

### 第 5 步：人工卡点 + 出口 2 关闭
- 审批请求走团队原生通道：系统在 Matrix 向审批人（agentmed-approver）发结构化消息（工单 + nonce + 风险摘要）；
- 审批人回复批准/否决：Element 直回，或用 CLI/MCP 封装发送同样结构的消息；系统读 Matrix 事件、验 nonce、核发；
- 结果落 VerifiedCandidate（修好已验证）或 NOT DEPLOYED（验证不过，退回）；
- **完成=能演示**：一段投诉从入口到出口 2 完整走通，含人工否决路径。

## 每一段用 team 原生能力承载

| 闭环段 | 原生承载 |
|---|---|
| 团队/审批人 | team.yaml 六角色 + Human CR |
| 投诉入口/取证 | langfuse-inspect skill + MCP（已部署） |
| 归因/修复 | 角色 skills + worker workspace |
| 沙箱验证 | sandbox runner 挂为 MCP 工具，agent 直接调用 |
| 人工卡点 | Matrix 消息（Human CR 通道）+ CLI/MCP 封装 |

原则：审批与验证都借用 team 已有机制（房间消息、CR、MCP），不另建 Console。

## 人工卡点（全程两处）

1. **机器统计判决**（gatekeeper 出判定，人类可复核，不阻塞）；
2. **人类审批**（出口前，走 team 原生 Matrix 通道：请求 → 回复 → 验 nonce 核发，落审计）。

## 证明层（渐进加固，不阻塞 flow）

- flow 跑通后按需启用：不可变 SystemVersionSet、Episode Snapshot 封存、two-purpose Gate；
- 在此之前每段结果 = 「人可复核」；之后 = 「机器可证明」。

## 边界与不做

- 不做部署/上线（出口 3 不进首闭环）；
- 不做凭证轮换（当前测试 key，待闭环后再换）；
- 废弃链路归档冻结，不删除。

## 附录：平台原生能力清单（实测 AgentTeams v1.2.2，本机 manager）

| 能力 | 平台机制 | 闭环用法 |
|---|---|---|
| 人类角色 | team.yaml Human CR + human-management | agentmed-approver 进团队，DM/房间消息审批 |
| 团队/成员 | team-management | 六角色建团、增删成员 |
| 运行时重组 | worker-management（create/reset/start/stop） | 按需增减角色，不停整个团队 |
| 市场导入 | agentteams-find-worker + Nacos | 直接导入 agentmed 六角色包 |
| 任务编排 | task-management / task-coordination + 心跳 | 立案分发、状态跟踪、逾期提醒 |
| 项目隔离 | project-management + channel-management | 每个 complaint 一个项目房间 |
| 共享文件 | agentteams-fs + MinIO + file-sync | 证据、补丁、观测全队分发 |
| 代管 git | git-delegation（.processing 标记） | 修复落 Gate 的受控 git 操作 |
| 技能池 | skills + skill_pool 镜像分发 | agentmed skill 池已在平台就位 |
| 工具挂载 | mcp-server-management + mcporter | 沙箱 runner、langfuse、approval 挂为 MCP 工具 |
| 模型路由 | model-switch / worker-model-switch | 贵模型给归因/修复，便宜模型给取证 |
| 成本治理 | 空闲自动停机（720 分钟留痕） | worker 闲置自动回收 |
| 宿主机访问 | host-share 权限模型（人类授权） | 沙箱执行的真实底座 |
| 跨会话记忆 | memory 每日文件 + MEMORY.md | 归因上下文跨轮延续 |
| 自托管 Matrix | matrix-server-management | 审批通道本体 |

平台不原生、需自补的薄层只有三样：nonce 生成/验证、沙箱 runner 本体、控制面状态机（V5 已有）。
