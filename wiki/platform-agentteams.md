# AgentTeams 平台实测百科

> 只记**实测确认**的行为。官方文档推断但未验证的，标【未验证】。
> 新发现回写本页；重大缺陷另存 `evidence/spike/`。

## 安装（v1.2.1，详见 deploy/README.md）
- 安装脚本支持 `AGENTTEAMS_LLM_PROVIDER=openai-compat` + `AGENTTEAMS_OPENAI_BASE_URL` 接任意 OpenAI 兼容端点
- **坑 1**：`AGENTTEAMS_DATA_DIR` 传绝对路径会被当作 docker volume 名直接报错——不要设
- **坑 2**：存在旧 `~/agentteams-manager.env` 时非交互模式也会弹升级菜单；全新安装先 uninstall + 删 env 文件
- 卸载 `install.sh uninstall`：停删 Manager/Worker/controller 容器、agentteams-net 网络、安装日志
- 内嵌组件：controller 容器内含 Higress + Tuwunel(Matrix) + MinIO + Element Web + Go controller

## 认证与内部调用
- controller REST（容器内 :8090）要 bearer token：`/var/run/agentteams/cli-token`（JWT）
  `docker exec agentteams-controller sh -c 'curl -H "Authorization: Bearer $(cat /var/run/agentteams/cli-token)" http://127.0.0.1:8090/api/v1/...'`
- Matrix 管理操作可用 AppService token 伪装任意用户：
  容器内环境变量 `AGENTTEAMS_MATRIX_APPSERVICE_AS_TOKEN`，调
  `http://127.0.0.1:6167/_matrix/client/v3/...?user_id=<urlencoded 完整 Matrix ID>`
  （可读房间成员、代发消息、代为 leave；不能 kick 自己，kick 要更高 power level）
- 日志：controller 业务日志在容器内 `/var/log/agentteams/agentteams-controller(-error).log`，
  `docker logs` 只有 supervisord 输出

## CR 行为（v1beta1）
- `agt apply -f` 按文档顺序执行，**不做拓扑排序**：先 Worker 后 Team 再 Human
- Team 校验：workerMembers 必须**有且仅有一个 team_leader**；PUT 不允许清空
- 摘除普通成员：PUT workerMembers（只留 leader）可行，摘除后 worker 可正常 delete
- Team 删除：CLI/REST 都返回成功样式，但实际走 reconcile 异步清理——**可能假成功**（见 S0-001）
- Worker 删除：是团队成员时 409，须先摘除

## 已确认缺陷
### S0-001 Team 删除假成功 + Leader detach 死锁（证据：evidence/spike/S0-001-*）
- 根因：detach 成员时"restore Manager to Worker personal room"步骤的 invite **非幂等**——
  @manager 已在房间时 Matrix 返回 403 joined/banned，controller 将整个删除判死并无限重试
- 即使手动把 @manager 改为 leave 仍报同一错误（不检查现状、不容忍"已是目标状态"）
- **施工对策**（Caseload Controller 设计输入）：删 CR 后必须回查四样——
  `agt get teams/workers` + docker 容器 + Matrix 房间 + MinIO 用户齐全消失才算成功；
  摘出顺序先普通成员后 leader

## 存储
- controller 持久化 = kine（SQLite）`/data/agentteams-controller/agentteams.db`，
  key 形如 `/registry/agentteams.io/{teams,workers,managers}/default/<name>`，
  删除是 tombstone（deleted=1）——排查"删没删干净"可直接拷出 DB 查

## 消息规则【部分来自 zeroops 调研，v1.2.1 待复验】
- 群聊必须 @完整 Matrix ID 才响应，无 @ 静默忽略；v1.2.1 起 mention 经 `m.mentions` 元数据投递
- 单任务上限 30 分钟；session 损坏房间内发 `/new` 重置
- 自定义 Skill 下发：推 Worker MinIO workspace `agents/<worker>/skills/` 自动发现（约 300ms），
  或容器内 `agentteams-sync` 立即拉取
- MCP：先在 Higress 网关侧注册（Manager 的 mcp-server-management 技能 / setup-mcp-server.sh），
  再把 `worker-<name>` 加入 allowedConsumers；403 = 未授权 Consumer
- 外部注入房间消息：自建 relay 作为 Matrix 客户端直接发 `m.room.message`（须带 m.mentions）

## MCP 注册与调用（Higress 网关，2026-08-07 实测）
真实操作路径（console API 手工作业）：
1. `POST /v1/service-sources` → 建 service source（DNS 型，后端 `host.docker.internal:<port>/mcp`）
2. `PUT /v1/mcpServer` → 建 MCP 代理，`type=OPEN_API` + `mcp-proxy` `rawConfigurations` 指向该 source
3. `PUT /v1/mcpServer/consumers` → **全量替换语义**：每次必须带完整 `allowedConsumers`（`manager` + `worker-<name>`…），不是增量追加
4. worker 侧：controller `/root/agentteams-fs/agents/<name>/config/mcporter.json` 加 `mcpServers.<name>` 条目
   （url=网关 `/mcp-servers/<name>/mcp`、transport=http、headers 带 `Authorization: Bearer <WORKER_GATEWAY_KEY>`），
   落盘后经 MinIO `agents/<name>/config/mcporter.json` **周期同步**进 worker `/root/.copaw-worker/<name>/config/mcporter.json`

坑（每个一行）：
- 官方 `setup-mcp-proxy.sh` 在 manager 容器内跑不通：硬编码 `CONSOLE_URL=127.0.0.1:8001`，console 实际在 controller :18001 → 只能手工作业 console API
- 401 引号坑：`/data/worker-creds/<name>.env` 的值带双引号与 `\r`，拼 Authorization 前必须 `tr -d "\"'\'' \r"`，否则 key-auth 401
- 路径透传坑：Higress mcp-proxy 把 `/mcp-servers/<name>/mcp` **原样透传**给上游，上游必须 PathRewrite 成 `/mcp`（FastMCP 只认 /mcp，否则 404）
- consumers 是**全量替换**：漏发已授权 consumer 会被踢出，403 = 未授权 Consumer
- worker 无 `agentteams-sync`；`copaw-sync` 指向缺失脚本（v1.2.1 镜像瑕疵），靠 MinIO 周期同步即可
- mcporter 按 cwd 解析 `./config/mcporter.json`：worker 真干活目录是 `/root/.copaw-worker/<name>/`（有 config/），pid1 的 cwd `/root/agentteams-fs/agents/<name>/` 下没有 config/ 会显示 No servers
