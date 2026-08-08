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
