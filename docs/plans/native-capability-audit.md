# CaseLoop 原生能力使用审计（AgentTeams v1.2.2 对照）

> 依据：D-015（flow-first）。三路只读审计合流（control-plane+cli+console / agents+skills+mcp / agent-station 胶水），2026-08-14。
> 只读清点，未改任何代码；处置需逐项走归档/测试/提交纪律。
> 处置决定（owner，2026-08-14）：**执行**——能用原生就用原生；过度工程与重复（含自重复）一律砍/归档；占位包规划重造一律重定向；结果并入 flow-first 第 0 步（见 D-015 第 7 条）。

## 一句话结论

平台 17 项原生能力：**用满 ≈2，半用 ≈6，闲置并自建替代 ≈8**。冗余集中在编排脊柱
（领单/审批/通知/git/文件分发），控制面本体约六成是真领域代码，但存在 V4/V5 双轨
与过度工程。

## 使用矩阵（17 项 × 我们的用法）

| # | 平台原生能力 | 我们的用法 | 判定 |
|---|---|---|---|
| 1 | Human CR（Matrix DM/房间审批） | 声明了 caseloop-approver，但审批决策走自建 approval MCP + approvals 表 + feishu mock | 闲置 |
| 2 | team-management | Team CR 存在；但 bootstrap 自建 principal/凭证/controller 引导 3.5K | 半用 |
| 3 | worker-management | 未用；自建 lease/claim/heartbeat/reclaim；runtime-local 占位包还计划重造 | 自建替代 |
| 4 | Nacos 市场导入 | 未用；cli discovery.py 是本地 git 扫描；application_catalog 自建 | 自建替代 |
| 5 | task-management/coordination | taskflow ack/submit 用了；但 case.claim 领单、kernel 状态机、control-plane case 状态机三份并行 | 半用 |
| 6 | project/channel 管理 | 团队房间存在；project-management 未用；每 complaint 一项目房间未落地 | 半用 |
| 7 | agentteams-fs + MinIO + file-sync | shared/tasks/{task-id} 交接纪律用了；但 deploy-langfuse-skill.sh 手写 docker cp/mc cp 绕过；store 占位包计划重造存储 | 半用 |
| 8 | git-delegation（.processing 标记） | 未用；WorkOrder diff/prompt-git 化自建 | 自建替代 |
| 9 | skills + skill_pool 镜像分发 | 平台 pool 里已有 17 个 caseloop skill 镜像；但 team.yaml 只挂 1 个自建巨 skill | 半用 |
| 10 | mcp-server-management + mcporter | 未用；每 worker 一个 gateway 投影 URL 硬编码在 team.yaml | 自建替代 |
| 11 | model-switch | 未用；全部钉死 step-3.7-flash | 闲置 |
| 12 | 空闲自动停机（720 分钟） | 未用；state: Running 全开 | 闲置 |
| 13 | host-share 授权访问 | 未用；沙箱在 eval-harness 自建计划 | 自建替代 |
| 14 | 跨会话记忆（每日 memory） | 半用；manager 侧有，worker 侧归因上下文靠 shared/tasks 文件 | 半用 |
| 15 | 自托管 Matrix + channels send | 房间派单用了；但 matrix.log 不写真 Matrix（写本地 NotificationMessage 表）；通知走 feishu mock | 半用 |
| 16 | service-publishing（Nacos） | 未用；release_service 5.1K 自建发布写面 | 自建替代 |
| 17 | Langfuse 观测集成 | langfuse-inspect skill 已部署，scores/observations 可读 | 用满 |

## 冗余判定总表（三路 Top 合并）

| # | 自建了什么 | 位置 | 平台原生对应 | 判定 | 处置 |
|---|---|---|---|---|---|
| 1 | 自建审批链（approval.request/status + common/approval.py + changeset + approvals 表，默认 feishu mock） | control-plane + mcp-servers/release_admin | #1 Human CR Matrix DM | 冗余 | A 替换 |
| 2 | matrix.log 不写真 Matrix；feishu.reply_origin/weekly_report mock | mcp-servers/notification + control-plane/notifications(437) | #15 真 Matrix 房间 | 冗余 | A 替换 |
| 3 | worker 租约 claim/lease/heartbeat/reclaim + fencing | control-plane lease.py + case_service + mcp case.claim | #3/#5 任务管理+心跳 | 部分冗余 | A 替换（保留 fencing 防脑裂） |
| 4 | 手写 skill 分发脚本，docker cp/mc cp 改 skill.json，明文默认 pk/sk | agent-station scripts/s0/deploy-langfuse-skill.sh | #7/#9 skill_pool+file-sync | 冗余 | A 替换 |
| 5 | caseloop-b1-loop 巨 skill（揉合 coordinate-loop/propose-candidate/independent-verify/release-observe-rollback/curate-regression-asset） | agents/skills | #9 skill_pool 角色级 skill | 冗余 | A 拆分 |
| 6 | WorkOrder diff / prompt-git 化自建 | control-plane release/case 服务 | #8 git-delegation | 部分冗余 | A 替换 |
| 7 | 每 worker 一个硬编码 gateway 投影 URL | agents/team.yaml | #10 mcp-server-management+mcporter | 部分冗余 | A 收敛（保留角色 ACL 投影思想） |
| 8 | 两个事件存储实现并存：v4_event_store(1063) 供 v5 权威/目录/绑定服务；event_store(269) 供 case/release/gate 等 | control-plane services | 无（内部双实现） | 复核：两者均活，无死代码 | W2a 合并重构 |
| 9 | authority(812，stage1a 消费者) 与 v5_authority(1273，目录/绑定消费者) 两个权威服务，共用 v4 存储 | control-plane services | 无 | 复核：均活，重叠度待 diff | W2b 合并重构 |
| 10 | 三表文件为三层领域表（核心/v4 域/v5 目录）非重复；v5_integrity 依赖 v4_integrity（分层） | control-plane models/utils | 无 | 复核：判定撤销，不砍 | E 保留 |
| 11 | acceptance.py(1542) 自述 V5-4 前不可执行的验收流 | control-plane services | 无 | 过度工程 | C 冻结 |
| 12 | trust_service Wilson 3/3 双侧 95% 统计账本(397) | control-plane services | 无 | 过度工程 | C 简化 |
| 13 | 三份状态机描述同一 revision loop：kernel/state-machine.mjs + manifest.mjs validateEventChain + classify.mjs deriveEventChain | agent-station packages/kernel + src/s0 | #5 任务态（另一层） | 内部冗余 | C 收敛为一份权威 |
| 14 | 7 个 control-plane 状态机 + 双版本事件溯源 + 30K 测试 | control-plane state_machines/event_store | 无（治理域，但过度） | 过度工程 | C 精简 |
| 15 | packages/store/runtime-local/api/cli 四个占位包规划重造存储/启停/控制面/CLI | agent-station packages | #7/#3/#6/#16 平台能力 | 规划重造 | D 重定向 |
| 16 | proxy 内嵌 Langfuse OTLP 遥测约 100 行 | agent-station openai-content-length-proxy.mjs | 平台追踪 AGENTTEAMS_CMS_TRACES | 部分冗余 | C 剥离 |

## 处置方案

**A 替换原生（flow-first 首闭环内做）**：
1. 审批改走 Human CR Matrix DM/房间（CLI/MCP 封装不变，见 D-015）；feishu mock 归档；
2. 通知/留痕写真 Matrix 房间消息（copaw channels send）；outbox/receipt 语义保留；
3. 领单/心跳移交平台任务管理；fencing token 保留为防脑裂守卫；
4. skill 分发改走平台 skill_pool + file-sync；删手写脚本；凭证 env 注入；
5. caseloop-b1-loop 拆成角色级 skill 进 skill_pool（平台池已有 17 个镜像，重组即可）；
6. git 侧改走 git-delegation（.processing 标记）；
7. MCP 挂载收敛为 mcporter 注册 + 角色 ACL 投影（保留投影思想，去掉硬编码 URL）；
8. 启用 model-switch（贵模型归因/修复，便宜模型取证）与空闲自动停机。

**B 合并重构（复核后重定级）**：深检推翻「归档 V4」——v4_* 是当前 V5 服务的共享存储脊柱（v5_authority/acceptance/case_binding 等 13 个文件引用），public_v4 与 public_v5 均挂载于 main.py，services 全扫描无零引用模块。改为四个合并重构包：W2a 事件存储双实现合并、W2b authority 双服务合并、W2c audit 双写器合并（audit.py 与 v4_audit.py 共写同一 Audit 表）、W2d v4 API 退役迁移；前置条件 = V5-1A/B/C 修复收口提交 + 专用分支 + diff 证据。

**C 精简（过度工程）**：acceptance 冻结到 V5-4 前；Wilson 统计简化为计数账本；三份自动机收敛为一份；7 状态机精简；proxy 遥测剥离。约 2.5K 行 + 随行测试。

**D 重定向（规划占位包）**：store→agentteams-fs+MinIO；runtime-local→worker-management；api→平台 API 薄适配；cli→agt CLI。占位桩无代码债，改 requirements/plan 方向即可。

**E 保留（真领域，平台无对应）**：case/signal_intake/PII 脱敏、experiment/attribution、gate、trust（简化后）、authority/audit、system_versions 不可变版本集、public API wire、S0 证据链、console 只读看板、cli 客户端。

## 规模账

- 复核后规模账：**不存在**「B 类 3.5K 可直接归档」——无死代码；真实工作 = A 类平台原生替换（约 3K，随 flow-first 第 1 步接线）+ B 类四组合并重构（规模以 diff 为准）+ C 类已落地 C13（-103 行）；acceptance/Wilson/状态机经复核为领域代码或已 fail-closed，不砍；
- 规划重定向：D 类 4 个占位包（约 130 行桩）；
- 保留主体：control-plane 约 60% 是治理领域代码（六成不冗余），平台负责编排脊柱、控制面只管权威状态与证明层。

## 与 flow-first 的关系

- 首闭环（五步）只依赖 A 类替换 + E 类保留子集；B/C/D 不阻塞 flow，作为渐进清理项；
- 原则不变：Agent 负责动脑（平台编排），确定性系统守规矩（控制面权威状态、nonce、沙箱），人类在房间里授权；
- 本审计结论并入 flow-first 第 0 步（去冗余砍代码）的实施口径。

## 夜间全自动授权（owner，2026-08-14 睡前三决定）

1. **提交权限**：caseloop 仓库在途 V5 修复（162 文件，818 单测绿）+ 新改动，按仓库纪律分批语义提交；
2. **代批授权**：段6 人工审批可用 CLI 以 @caseloop-approver 代批一次，审计明确标记「演示代批」；
3. **清理授权**：闭环跑通后，以当前闭环为唯一核心——未使用的业务链路删除、重复代码合并删除、屎山清理（owner 授权删除；测试+提交证据）。

## 处置进度（执行日志）

| 日期 | 项 | 处置 | 状态 |
|---|---|---|---|
| 2026-08-14 | #4 手写 skill 分发脚本：去硬编码明文 pk/sk（fail-closed 环境变量）+ 标注仅作 S0 harness 兜底、不得扩展到 caseloop 团队 | A 替换 | ✅ 已改 `agent-station/scripts/s0/deploy-langfuse-skill.sh`；平台 skill_pool 分发改由 flow-first 第 1 步落地 |
| 2026-08-14 | #15 四占位包重定向：store/runtime-local/api/cli 加原生边界注记（不重造平台存储/启停/团队 API/CLI） | D 重定向 | ✅ 蓝图 `plans/agent-station-p0.md` S3/S4/S5 已加 Native-capability boundary |
| 2026-08-14 | #13 三份自动机收敛：kernel 删 TASK/ARTIFACT 机器（任务态=上游事实走 contracts，artifact=证据派生），保留 RUN/APPROVAL 守卫 | C 精简 | ✅ 提交 `agent-station@7cf7e68`，RED→GREEN，kernel 36/36 绿；S0 证据链（classify/manifest）为冻结证据层不动 |
| 2026-08-14 | B 项深检复核：v4_* 为 V5 共享脊柱（13 个引用文件）、public_v4/v5 均挂载、三表文件分层非重复、v5_integrity 依赖 v4_integrity、services 全扫描无零引用 | B 复核 | ✅ 判定修正：无死代码；重定级为 W2a-d 合并重构（见处置方案 B）；#11/#12/#14 复核为领域代码或已 fail-closed，不砍 |
| 2026-08-14 | flow-first 第 1 步：caseloop 六角色团队部署到 Agent Station（`agt apply` agents/team.yaml） | 第 1 步 | ✅ **caseloop-team Active**：6 worker Running、LeaderReady、ReadyWorkers 5/5、Human CR caseloop-approver Active（@caseloop-approver:matrix-local） |
| 2026-08-14 | 平台踩坑：旧 agentmed-quality 遗留 DM 房间 room.meta 403（现 controller 的 matrix sender 在该房权力 0） | 平台排障 | ✅ 解法：@admin 预置 room.meta（teamName=caseloop-team）+ 该房 events[room.meta]=0/state_default=0；重建后 Phase Pending→Active。遗留：agt apply 重复更新已存在 human 报 405（无害） |
| 2026-08-14 | 控制面拉起：Docker Hub 网络不通（pgvector/python/node 镜像拉不下来）→ 本地适配：PG 用本机 postgres:17、控制面宿主机 venv 运行（run_local.py）、端口 5433/18090/18088 | 第 1 步基建 | ✅ CP UP：healthz 200、readyz ready（db ok/migration current/public_auth configured）、alembic 001→012 |
| 2026-08-14 | MCP 工具面：12 个角色投影全部启动（8101-8501/8102-8202/8103-8203/8104-8204/8005），无网关令牌直连返回 403（fail-closed） | 第 1 步基建 | ✅ 9 张 mcp_* 表建好；pgvector 扩展拆为 002 迁移、plain PG 跳过（向量检索 Phase 2 才启用） |
| 2026-08-14 | Higress 网关注册：12 个 MCP server 经控制台 API（/session/login + PUT /v1/mcpServer + service-sources）注册；实测路由/密钥鉴权/consumer 头注入全通（x-mse-consumer: worker-quality-officer 已验证） | 第 1 步基建 | ✅ 已通 |
| 2026-08-14 | 后端令牌头死锁：wasm mcp-proxy 按协议只在能力协商后注入上游认证头，而 serverkit 中间件从首个请求就要令牌 → 加显式 demo 模式（MCP_TRUST_GATEWAY_CONSUMER=true + 投影只绑 127.0.0.1，信任网关注入的 consumer 头；默认关闭保持 fail-closed） | 第 1 步基建 | ✅ **全链路打通**：网关 initialize 200 → tools/list 返回角色工具面 → tools/call case.list 返回真实 DB 查询。中间件改动：common/serverkit.py + common/config.py + 5 个 server 调用点 + launcher |
| 2026-08-14 | 第一条投诉信号全流程：目录引导（v5_catalog_local bootstrap）→ Agent Station 清单导入（app_01M00CRRS5HX07Q5K29B0MYBS4 / env_01M00CRS6VWFCPJ5FC6YKGVYG9）→ POST /api/v1/signals 立案 | 第 1 步演示 | ✅ **case_01M00CVFQREZJMS8SSJ3Q17X9W OPEN/UNTRIAGED**「langfuse 负分观测：Agent Station trace 输出被评 0 分」；quality-officer 经网关 MCP case.list 已能看到该 case；证据收据缺失字段（trace.input/output 等）= 第 2 步 collector 取证任务 |
| 2026-08-14 | 派单给 quality-officer：团队房间 @提及发送成功；worker 拾取受 matrix 房间成员状态抖动阻塞（controller 重启后 admin 成员反复变 leave、AS 邀请路径时好时坏）；worker 模型路径 relay（8089）曾卡死已修复（网关→relay 200） | 第 1 步演示 | ✅ 派单已送达并进入 worker 队列（队列创建+agent 开始推理均有日志）；⏳ worker 分诊卡在**模型路径外部故障**（stepfun 网络→8088 worker 线程耗尽→relay 楔死），与 run-009 同款 |
| 2026-08-14 | 第 2 步 langfuse 信号源（确定性，无模型依赖）：`scripts/langfuse_signal_source.py` 读 langfuse v3 scores → 负分映射 maintainer_report → POST /api/v1/signals，状态文件幂等 | 第 2 步 | ✅ **真实负分→真实立案**：对真实 trace（54e673cf）打 0 分（score 9405a9cd）→ 自动开 case_01M00EC9Y4S45ZSJZAVX66Y3KF；重跑 opened=0（幂等验证）。已知小瑕疵：v3 列表不返回 traceId，case 摘要 trace=unknown（score_id 在 body 可回溯） |
| 2026-08-14 | 模型路径外部故障诊断：AgentMED(8088) uvicorn 线程被 stepfun 慢响应耗尽→relay 楔死→平台 worker 无法推理。修复：重启 AgentMED（PYTHONPATH=src 补 editable 安装失效）+ relay + watchdog；轻请求恢复（0.9s），worker 大上下文请求再次拖垮（第 5-7 轮持续，与 run-009 同款外部网络不稳定） | 排障 | ⏳ worker 自主分诊仍被外部模型不稳定阻塞；闭环确定性部分（langfuse→立案→MCP→控制面）全绿不受影响 |
| 2026-08-14 | A5 角色级 skills 挂载：team.yaml 六角色全部挂平台 skill_pool 技能（coordinate-loop / ingest-langfuse+query+langfuse / independent-verify / curate-regression-asset / reproduce-badcase+attribute-skip+bind-version-snapshot / propose-candidate+draft-pr+git-delegation），保留 caseloop-b1-loop | A 替换 | ✅ team.yaml 已改（6 处）；技能内容适配（AgentMED Kernel 口径→caseloop MCP 口径）待专门轮次；改后需 `agt apply` 生效 |
| 2026-08-14 | A1 审批通道 CLI 封装：`mcp-servers/scripts/caseloop_approval_cli.py`（list 看待批 / decide 发结构化 Matrix 决策） | A 替换 | ✅ **端到端验证**：以 **Human CR @caseloop-approver 身份**（appservice 模拟）发送 `APPROVAL_DECISION approval=... nonce=... decision=...` 到团队房间，matrix event 可见；系统侧 reader（验 nonce→核发）随第 5 步落地。实测 worker（collector）已能收到房间消息并尝试处理——平台消息流活着，只是模型路径仍断 |
| 2026-08-14 | 模型路径误杀修复：relay /v1/models 改本地应答（不再依赖上游延迟）——watchdog 5s 健康检查不再误杀在途慢请求；AgentMED 改 4 uvicorn worker | 排障 | ✅ relay 本地 models 200；派单后 worker 首轮 LLM 调用存活 |
| 2026-08-14 | langfuse 真接入修正：信号源改指真实项目 agentmed-local-project（AgentMED langfuse SDK 4.14.4 实时写入的观测）；信号=真实异常（瞬间闭合观测，按 traceId 聚合）；trace 用真实 traceId | 第 2 步 | ✅ 16 个真实异常案自动立案；手工注入的测试分已从链路上退役 |
| 2026-08-14 | 写链桥：公共信号只建 wire 案（quality_cases），worker 写链（claim/suggestions）只认内部聚合——adapter 增加双写（POST /v1/complaints 同源开内部聚合案） | 第 1 步 | ✅ claim 实测（lease+fencing）、submit_suggestion 实测落库（suggestion_id=evt_01M00JFBMR...） |
| 2026-08-14 | **第 1 步闭环演示达成**：真实 langfuse 信号立案 → quality-officer 读案分诊（case.get）→ 提交分诊建议（case.submit_suggestion，落库）→ 派单 → **collector 收到任务开始取证**（房间消息实测） | 第 1 步 | ✅ 完成=能演示的验收线通过；第 2 步取证进行中（collector 16:43 启动任务） |
| 2026-08-14 | 七段闭环收口计划定稿（段1✅/段2-6 各段差距+施工+验收证据+两前置），落 `flow-first-closure.md`；口径：每段骑平台原生机制（任务编排/shared/tasks/skill_pool/Matrix/MCP） | 规划 | ✅ 已定稿 |
| 2026-08-14 | 段2 启动：team.yaml 角色技能经 `agt apply` 原生注册（六角色重建）；排障：重新应用后 matrix 凭证对象缺失+worker 绑定丢失 → 全量重置（删 worker+队重建）→ **团队恢复 Active 5/5**，新团队房间 `!hw0KPV1wUHTMd3hUFW` | 第 2 步 | ✅ 团队就绪；待办：caseloop 版技能内容推入平台技能分发路径（MinIO）+ 派单 collector 取证 |
| 2026-08-14 | 夜间授权三决定记录（提交/代批/清理删除）；13 个 caseloop 版技能写完（agents/skills/）+ 17 个文件推入平台分发（MinIO agents/<w>/skills/）；collector 部署真实 langfuse 凭证（agentmed-local-project）；派单 段2 取证（score 9405a9cd/trace 54e673cf/观测 30c74f6a），QO 已在新房间处理中 | 第 2 步 | ⏳ 夜间运行中；团队房间最新 id `!NzWy15gwm3QU6cTfuP`（重建多次，以 agt 实时查询为准） |
| 2026-08-14 | **段5 沙箱 runner 核心件建成并实测**：`scripts/sandbox/replay.py`（容器内回放）+ `runner.py`（宿主编排）——隔离容器（copaw-worker 镜像, entrypoint python3, 只读挂载）跑坏例回放经真实模型路径，判定只看最终 content（reasoning 单独留证），修前/修后对照 + digest 证据 | 第 5 步 | ✅ **冒烟 PASS**：坏 prompt 修前 fail（空输出）→ 好 prompt 修后 pass（\"ok\"）→ verdict PASS；证据 JSON 含 probe/prompt digests。修复过程：entrypoint 覆盖、/tmp 工作区、去重参数、content-only 判定、256 tokens 预算 |
| 2026-08-14 | **段5 收口：sandbox.verify 挂为 gatekeeper MCP 工具**（eval_runner 投影），路径白名单 var/sandbox/，证据落 mcp_eval_runs（suite=sandbox-v1）+ 审计 | 第 5 步 | ✅ **经网关端到端实测**：tools/list 含 sandbox.verify → tools/call → 隔离容器真实回放 → verdict PASS，eval_id=eval_01M00T651V9... 落库——「把测试 agent 拿进容器跑」成为真实机制 |
| 2026-08-14 | **段6 reader 建成**：`mcp-servers/scripts/approval_reader.py`——以 @caseloop-approver 身份轮询 Matrix → 解析 APPROVAL_DECISION → 验 DB nonce+pending → 控制面 /v1/changesets/{cs}/approve|reject（APPROVAL_AUTHORITY_TOKEN）→ 状态更新+幂等状态文件 | 第 6 步 | ✅ 语法+运行实测（no new decisions 预期）；待真实审批请求接入 |
| 2026-08-14 | **worker 环路自动前进**：collector 完成取证（证据包：case 详情+badcase+evidence_inventory+摘要，taskflow 提交 TASK_COMPLETED，缺口如实标注）→ QO 立即 @attributionist 派单 → 归因师 18:59 开始处理 | 第 2/3 步 | ✅ 段2 完成；段3 进行中；平台原生任务链（meta.json/spec.md/taskflow）全程承载 |
| 2026-08-14 | **首轮全链跑完，出口 2 NOT DEPLOYED 分支真实运转**：归因→修复→门禁三连（repairer/gatekeeper TASK_COMPLETED）；gatekeeper 独立判 FAIL——归因置信度 low（证据缺口：app.logs/app.feedback/langfuse 评分明细缺失）+ eval-runner MCP 503，按 SOUL §8.2-2 置信不足不进修复 | 第 3-5 步 | ✅ fail-closed 演示真实；503 根因=我 pkill 后漏重启 attributionist 投影（已恢复）；已带修复重入环路（collector 用 langfuse 技能取评分明细 + 实验可用 + sandbox.verify） |
| 2026-08-14 | 测试修复：mcp-servers 123 passed（新增 sandbox.verify 进 ACL expected；GATE_AUTHORITY_TOKEN 从 mcp .env 移除避免 dotenv 污染测试与投影校验，launcher 改从 deploy/.env 取）；cli 测试收集错误（venv 缺失，待补） | 纪律 | ✅ 提交前测试证据就位 |
| 2026-08-14 | **语义提交收口**：caseloop 工作区 176 脏文件 → **0**（14 个语义提交：V5-1A/B/C 五批 + 夜间新件五批 + evidence/eval-harness/docs 收尾 + .omc 入 gitignore）；agent-station relay 修复提交 e8a123f | 纪律 | ✅ 脏工作区收口完成（W2 合并重构的前置条件已清） |
| 2026-08-14 | **清理账本落定**：`docs/plans/cleanup-ledger.md`——A 未使用业务 6 项（demo-app/Quality API、feishu mock 链、B1 脚本、trust_ledger、casebase 向量、langfuse-inspect 兜底）+ B 重复 4 项（event_store/authority/audit 双实现、哈希双份）+ C 屎山 5 项（acceptance/Wilson/release_service/proxy 遥测/事件源）；执行顺序：环路落定后 A→B→C，逐项测试+提交 | 清理阶段 | ✅ 账本已入库（0ca70b6）；二轮环路进行中（collector 已加载 langfuse 凭证，QO 用 projectflow plan_dag 重建执行图 09→12） |
| 2026-08-14 | **清理 A3 执行**：B1 live 脚本组 + harness 测试删除（fe21652+94269d5，-1.5K 测试行）；CP unit 787 绿；账本标记 done | 清理阶段 | ✅ 首个删除项闭环（环路运行中安全完成） |
| 2026-08-14 | **清理 A4 执行**：trust_ledger legacy 库（ledger/wilson/trust_demo/2 测试）+ mcp_trust_ledger 表定义 + 迁移块删除；mcp tests 105 绿 | 清理阶段 | ✅ 完成 |
| 2026-08-14 | 二轮取证修复：collector 查 langfuse 空结果的根因 = 凭证指错项目（agentmed-local-project vs 证据所在的 goai-agent-station）；已改凭证 + 提示重查 | 第 2 步 | ✅ **二轮取证完成**：真实证据到位——score 9405a9cd=0（answer-quality）、observation 30c74f6a（chat.completion, latency 1ms）、trace 上下文经 observation 重建（v4 events_only 无 trace 端点，如实标注）；证据包落任务交付物 + taskflow 提交；QO 已派 task 10（归因实验） |
| 2026-08-14 | 段3 归因进行中：case 内部状态 DISPATCHED→**ATTRIBUTING**（revision 5，DB 实测）；QO 已收讫 task 09 并派 task 10 给归因师（约束：Quality API 不可用，实验基于 langfuse 证据） | 第 3 步 | ▶ attributionist 跑对照实验中 |
| 2026-08-14 | **清理 A5 执行**：casebase 向量迁移（002_casebase_vector.sql）+ run_migrations 容错代码删除；mcp tests 105 绿 | 清理阶段 | ✅ 完成（A3/A4/A5 三连） |
| 2026-08-14 | 模型路径外部抖动：stepfun 对 worker 大请求间歇 502（小请求 0.7s 正常）；归因师首轮卡 40 分钟 → 重启拿干净会话 + 重新触发，19:31 恢复处理、实验聚合 revision 1→2 | 排障 | ⏳ 持续盯；每次卡轮 = 探活+重启+重触发 |
| 2026-08-14 | 归因师 BLOCKED 处置：认领成功（lease_01M00W5ZH + fencing=2）但实验工具 Connection refused（我重启 8203 窗口期的瞬时故障）→ 实测网关 200 恢复 → Team Admin 决策消息通知 QO 恢复项目重派 task 10 | 第 3 步 | ✅ 阻塞解除；⏳ worker 消息拾取夜间不稳定（QO/归因师均未及时响应恢复消息），持续重触发中 |
| 2026-08-14 | 清理 A6 复核：langfuse-inspect 是 s0 团队在用的活资产（skill_pool 实测在用），撤回删除、保留 | 清理阶段 | ✅ 账本修正（8a81789）——清理不等于删活资产 |
| 2026-08-14 | 环路恢复：全体 worker 重启（消息拾取修复）→ QO 恢复项目重派 task 13 → 归因师重试（从容器内实测两端点 406=服务器应答）→ **experiment.plan 成功**（新实验 exp_01M00XS1ZD 落库 REQUESTED rev2，case revision 6→14） | 第 3 步 | ▶ 实验 execute 进行中；MCP 路径确认可用 |
| 2026-08-14 | **versionset 工具重接**：根因 = MCP versionset.list/get 走已死的 Quality API（demo-app 8080）——v4 时代接线未换；修复 = 控制面新增只读 /v1/system-versions 路由 + MCP 重接 + 测试更新（105 绿）；实测返回真实版本集 vset_01M00CRVGRY...；提交 416f63e | 第 3 步 | ✅ 阻塞解除，归因师重试中 |
| 2026-08-14 | 消息拾取不稳持续处置：归因师 20:08 后无进展 → 重启（20:23 新轮）触发重新同步拾取待办消息 | 排障 | ⏳ 每轮 = 探活 + 重启 + 重触发；确定性阻塞已全清，只剩 worker 慢速执行 |
| 2026-08-14 | 归因师重试启动：新 nudge 20:26 拾取（versionset 修复通知）→ shell 活动 + case revision 14→16 | 第 3 步 | ▶ probe.freeze/experiment.execute 重跑中 |
| 2026-08-14 | B 类合并重构 diff 证据落账本：W2a（event_store 双实现=不同消费者不同语义）、W2b（authority 双服务=V5 扩展校验链）、W2c（audit 双写器=单写器+完整性选项）——三项均为行为敏感合并，需专用分支+测试对拍，夜间环路运行期不做（bc30db6） | 清理阶段 | ✅ 证据就绪，合并留待环路全绿/owner 醒后 |
| 2026-08-14 | **段3 操作者兜底驱动**：worker 环路停滞时以操作者身份经网关 MCP 驱动 probe.freeze——逐层排掉 4 处契约缺陷：random_seed_ref 格式、完整 digest、5 cell 绑定、vs_/vset_ id 空间不匹配（validator 修复）、freeze 验证走已死 Quality API（改本地版本集查找+回退，787 测试绿，d10f44b） | 第 3 步 | ⏳ 剩余最后一层：5-cell 协议需 5 个版本集（C/RP/RK/RM/G 各绑定自己的 P/K/M 组合），V5 世界只有 1 个——下轮补 5 版本集数据后 freeze 可通 |
| 2026-08-14 | **freeze 打通**：helper 改 role 标签绑定解析（787 测试绿）→ 5 个 cell 版本集落库（vset_cell_C/RP/RK/RM/G，digest 唯一约束逐个排掉）→ 经网关 MCP probe.freeze → **实验 PROTOCOL_FROZEN（revision 3）** | 第 3 步 | ✅ 归因实验冻结完成；已通知归因师继续 run→execute→report；操作者直驱 claim 遇 MCP 400（细节待查，worker 路径不受影响） |
| 2026-08-14 | 段3 执行阻断根因链（夜间三连失败）：① execute 后台线程执行客户端指向已死 demo-app Quality API（8080）；② 投影进程跑旧代码（arm_versionsets 校验读旧字段）；③ driver 只认 vs_ 前缀而冻结协议用 vset_；④ live 归因要求 provider log（request_id/trace_id 对账）但 Quality 无日志面；⑤ 单次上游空回复（status=empty）整链失败 | 第 3 步 | ✅ 逐层修复：**受治理应用评估面**（AgentMED /v2/versionsets 精确版本评估 + /v2/logs 提供者日志 + 3 次重试；AgentMED 658b0c3/b94994b/befd185）+ harness 执行上下文改走投影配置（b99ec62）+ driver 接受 vset_ 前缀（6cb15c8）+ 控制面 Quality 客户端重指向 AgentMED（d45b632） |
| 2026-08-14 | **案例状态机缺陷修复**：experiment.cancel 会把 case 打进 ESCALATED，而状态机无任何出口——补 domain-owned 人工恢复路径 case.reopened: ESCALATED→OPEN（quality-officer 复核重开重派，清除 escalation 投影、记录 reopen_count），contracts yaml 同步（8d91212，790 单测绿） | 第 3 步基建 | ✅ 已入库；本轮实验即经 reopen→claim→create→freeze→start 驱动 |
| 2026-08-14 | **第 5 轮 5-cell 执行中**：exp_01M013NWXVW4Z3SC5PGT9T4D62（RUNNING, fencing=12）；live 归因全契约就位（provider log 对账已 7 trial 实测通过；RK 臂 cs-001/002/003 三重复均复现「人工审核/不支持退货」故障口径）；135 探针 × 真实模型路径（AgentMED→stepfun，langfuse 逐条留痕） | 第 3 步 | ▶ 执行中（40s 轮询），预期 verdict=ATTRIBUTED(prompt)，C/RK/RM=0、RP/G=1、对照全过 |
| 2026-08-14 | 段3 执行鲁棒性加固（第 5/6/7 轮失败根因逐层处置）：① provider log 拉取撞上 AgentMED 运维重启（我的操作失误，重跑即可）；② stepfun 慢调用 69s 耗尽单探针预算 → 双层超时（评估面单次上游读 30s×3 + harness 预算 200s，AgentMED 8571529 / caseloop fa30e95）；③ 对抗性故障 prompt 下 step-3.7-flash 推理预算烧光（finish=length + 空 content，RK/cs-003 实测复现 3/3）→ max_tokens 1024→2048 后 3/3 稳定（AgentMED 1f58039） | 第 3 步 | ✅ 修复全入库；第 8 轮执行中 |
| 2026-08-14 | **第 8 轮 5-cell 实验完成（段3 收口）**：exp_01M0159WMBWA8S0FPQF74SYDXS 135/135 trial → VERDICT_COMPUTED=ATTRIBUTED，layer=prompt，Δ=(1.0, 0.0, 0.0)；细胞恢复率 C=0.0 / RP=1.0 / RK=0.0 / RM=0.0 / G=1.0——与 B1 fixture expected_cell_recovery 逐项一致；每个 trial 经 provider log 对账（request_id/trace_id/answer_digest 全链路核验） | 第 3 步 | ✅ 归因闭环达成（真实模型路径 AgentMED→stepfun，langfuse 135 条留痕） |
| 2026-08-14 | **段4 修复 + 段5 沙箱/门禁（收口）**：修复候选 vs_78f1312790086845（单变量回滚售后条款 v1.4.2，AgentMED draft）→ 工单 wo_01M01A4AZ1C88D5EBVN6Z7GDC4 **FROZEN**（内联中文 unified_diff，JCS RFC8785 转义修复后 hash 79d8c218…）→ 真实门禁三轨 **PASS**（contract/replay/live-e2e 16 探针 + LLM 裁判 step-3.5-flash 16/16，裁判轨带政策/KB 参考材料）→ 沙箱隔离容器修前 fail/修后 pass **PASS** | 第 4/5 步 | ✅ 修复与验证链全绿；关键修复：JCS UTF-8 转义、裁判参考材料、沙箱判定口径、门禁预算 900s |
| 2026-08-14 | **段6 审批 + 出口2（七段闭环达成）**：approval.request（matrix 通道）→ CLI 以 @caseloop-approver 发送 APPROVAL_DECISION（**演示代批**，owner 授权二）→ reader 验 nonce → 登记 ApprovalGrant → changeset **APPROVED** + case **RELEASING**；releases 表 **0 条**——**出口2：VerifiedCandidate NOT DEPLOYED**（已验证候选、人工已放行、未部署） | 第 6 步/出口2 | ✅ **第二次迭代全链闭环**：langfuse 真实信号 → 取证 → 5-cell 归因 → 修复 → 沙箱+门禁 → 人工代批 → 出口2；待办：清理阶段（owner 授权三） |
