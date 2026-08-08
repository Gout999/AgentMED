# 施工指南（所有施工 agent 必读）

## 角色分工
- **Kimi（主控）**：规划、拆任务、契约冻结把关、验收、e2e、视觉/前端验证、证据归档
- **Claude Code（coder subagent）**：仓库内中等粒度实现 + 自测
- **Grok 4.5（grok_run）**：成块后端基建，session 续接做修订

## 并行纪律
- 按文件 scope 切分，互不相交：`demo-app/` `control-plane/` `eval-harness/` `mcp-servers/` `contracts/` `agents/` `console/` 各归一家
- `contracts/` 冻结前不开工任何实现；契约变更必须主控批准并同步全部相关方
- 委派任务必须自包含：目标、精确文件路径、上下文、可碰/不可碰边界、验收标准

## 验证标准（不接受"我觉得好了"）
- 代码：测试实跑通过（贴结果摘要）；契约级变更必须跑 conformance suite
- 平台行为：实机验证 + 证据落 `evidence/`（日志/截图/导出物）
- 文档：中文、表格优先、字段英文；与 plan-v3 冲突时以 plan-v3 为准并标【待定】

## 仓库纪律
- 已授权自主 commit + push main；关键节点（契约冻结、Phase 出口）由主控打 tag
- commit message：`<type>(<scope>): 中文摘要`，type ∈ feat/fix/docs/chore/test
- **密钥永不入库**；`.env*` 已 gitignore；发现误提交立即报告主控
- 不做 git rebase/reset/force-push 等破坏性操作

## 发现即回写
- 平台新行为/新坑 → `wiki/platform-agentteams.md`（重大缺陷另存 `evidence/spike/`）
- 契约歧义 → `contracts/OPEN-QUESTIONS.md`
- 环境变化 → `wiki/environment.md`

## 禁止事项
- 不发明 plan-v3 没有的架构决策；不引入仓库没有的依赖（先查再报主控）
- 不 mock LLM 调用（demo-app 全真实 StepFun）；不把 audit.jsonl 当权威源
- 不做"置信≥0.8"式未定义指标；归因输出必须 Δ+95%CI+三态

## 运维地雷补充（2026-08-08 e2e 实战）

1. **MCP server 必须从主仓启动**：它们是宿主长驻进程，从 worktree 启动会因 worktree 清理而变成"目录已删的孤儿"；且启动 env 必须显式 `CONTROL_PLANE_BASE_URL=http://127.0.0.1:18090`（默认 8090 是 AgentTeams controller，不是 CaseLoop 控制面）。
2. **control-plane 容器重建后重启 MCP server**：server 的 httpx 连接池持有旧容器死连接，表现为 `case controller unreachable` 但宿主 curl 正常。
3. **integration 测试默认指 scratch 库**（S0-005）：`DATABASE_URL` 不设时是 `control_plane_test`，指活库必须显式覆盖。
4. **demo-app compose up 必须带 env**：`set -a && source ~/Documents/kimi/workspace/ACL-team/.env && set +a`，否则 `STEPFUN_API_KEY` 被空值覆盖，chat 静默回兜底文案。
5. **Matrix 房间代发三件套**：容器内 `docker exec agentteams-controller`，URL 用 `$AGENTTEAMS_MATRIX_URL`，token 用 `$AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN`，`?user_id=` 指定代发身份；**m.mentions 的 MXID 必须与真实 sender 一致**（工人是 `@quality-officer:…` 不是 `@caseloop-quality-officer:…`，写错 mentions=工人收不到）；payload 先写 /tmp/*.json 再 `docker cp` 进容器，避免引号地狱。
6. **worker 单线程 ReAct 循环**：工人在 loop 里时新消息只排队不消费；`docker restart` 会打断 loop 但**恰在消费瞬间重启=消息丢失**（sync token 已推进，新进程视其为已读）——重启前先确认 worker 空闲，丢了就用新 txn id 重发。
7. **agent 报"工具没有/行为不对"时先做隔离测试**：用 `mcp-servers/scripts/mcp_client.py <port> <tool> '<json>'` 以正确参数直调，平台正常=agent 参数构造错，平台异常=平台缺陷；S0-006 即靠此把"空实验"精确定位到 agent 传错 probe_set 键名 + probe.freeze 零校验。
8. **e2e 期间 demo-app 故障态是前提资产**：归因执行机（DemoAppB1Driver）会 inject/reset 切换故障臂，跑完务必确认 B1 仍注入（`curl :8080/chat` 问退货，漂移应答+prompt_digest=81122ca0… 为正常）；恢复基线用 `demo-app/scripts/reset_state.sh`。
9. **macOS 系统代理会劫持 httpx 的 localhost 调用（S0-007）**：httpx 默认 `trust_env=True` → `urllib.getproxies()` 在 macOS 回退读系统偏好代理；本机代理（Clash 类 127.0.0.1:7892）对 loopback 一律回 502，MCP→控制面请求根本没到控制面（容器日志无记录）。表象 `DEPENDENCY_UNAVAILABLE 502` 与根因严重脱节。判据：**curl 通 ≠ httpx 通**；修复=内部调用 `trust_env=False`（common/http.py 已改），跑 mcp_client.py 前缀 `NO_PROXY='*'`。

## 平台改进候选（2026-08-08 e2e 实战发现，按发现序）

| # | 缺口 | 实战证据 | 建议 |
|---|------|---------|------|
| G1 | eval-runner 无 probe.list 工具 | 归因师 RBAC 拿不到冻结探针清单，靠主控喂 | eval-runner 加自描述执行清单工具 |
| G2 | conformance 套件收尾不还原 demo-app | 复跑后 active 版本集留 v-test-* 残留，chat 兜底 | 套件 teardown 自动 reset_state |
| G3 | heartbeat 抑制域工作 | 质量官醒后收 heartbeat "do not do domain work"，8 分钟静默未执行主控指令 | heartbeat 与域消息分优先级，或限定"仅本条心跳回合" |
| G4 | worker JWT 1h 过期不自愈 | agt 401 后 4 小时无人管，手工 docker rm 重建 | controller 周期重铸或 sidecar 自刷 |
| G5 | 门禁规则轨不对账 live digest | 修复师伪造 digest（模式补全值）规则轨放行，hash_binding 只查内部一致性 | 规则轨加"digest 必须存在于 live 观测/版本集内容" |
| G6 | 绑定层错误表象脱节 | quality 绑定失败→502 quality_api_error，与"digest 不存在"根因脱节（注：本次实为代理拦截，见地雷#9；但绑定层若失败同样 502，仍值得改） | 绑定失败返 422+具体不匹配字段 |
| G7 | release 生命周期无 noop-close | B1 为运行时偏离（target==active declared），stage/canary 合法拒绝，release 永卡 REQUESTED | 加 reconcile/noop-close 迁移；WorkOrder diff 增 runtime_reconcile 类型 |
| G8 | case 无 close/resolve 迁移 | case_admin 工具面无关闭，本案终态只能停 ESCALATED | case 状态机补 close（附 postmortem 引用） |
| G9 | 信任账本无 MCP 写入工具 | case-officer 无账面可写，用 Markdown 文档顶替并宣称"平衡"（宣告≠执行复发） | 暴露 ledger.record_outcome/evaluate 工具，或发布完成事件平台自动记账 |
| G10 | 模型错误直接上墙 | StepFun RPM 错误原文（含内部路径）贴进房间 | copaw channel 错误包装或静默重试 |
