# Phase 0A Spike — 验证汇总与证据索引

> 日期：2026-08-07 ｜ 环境：AgentTeams v1.2.1（docker）+ Higress 网关 + StepFun `step-3.7-flash`
> 团队：spike-team（spike-leader / spike-worker-a / spike-worker-b），大脑 `step-3.7-flash`
> 目的：在进入 Phase 1 施工前，用最小团队实测 AgentTeams 平台的关键能力与缺陷，形成证据与设计输入。

## 验证项汇总

| # | 验证项 | 结果 | 说明 / 证据 |
|---|--------|------|-------------|
| 1 | 3 Agent Running | ✅ | spike-leader / spike-worker-a / spike-worker-b 三容器 Up；`agt get workers` 正常 |
| 2 | Matrix @mention 响应 | ✅ | Team Room 内 `@<完整 Matrix ID>` 才会被 worker 响应，无 @ 静默忽略；回执走 `m.mentions` 元数据 |
| 3 | task 生命周期交接 | ✅ | 交接必须走 `taskflow ack_task / submit_task`，产物放 `shared/tasks/{task-id}/` 自动同步（见 S0-003） |
| 4 | 自定义 Skill 同步与调用 | ✅ | 推 `agents/<worker>/skills/<skill>/SKILL.md` 到 MinIO 自动同步进容器；spike-echo 技能实测被 worker 调用返回 `SPIKE-ECHO OK` |
| 5 | MCP 网关调用 | ✅ | controller 与 worker 均经 Higress `mcp-spike` 代理调 `caseloop_ping` 返回 PONG；见 `mcp-gateway-*.txt` 与 S0-004 |
| 6 | sleep=删容器 / wake=按 CR 重建 | ✅ | worker 休眠态可整体删容器，按 CR（Worker 自定义资源）重建恢复 |
| 7 | controller 重启恢复 | ✅ | controller 容器重启后集群状态恢复，kine（SQLite）持久化 /registry 键 |

## 缺陷清单（S0-xxx，细节见各文件）

- **S0-001 Team 删除假成功 + Leader detach 死锁**：`agt delete team` 返回成功但团队仍 Active；
  根因是 detach 时 restore Manager 的 invite 非幂等（已在房间则 403），controller 把删除判死无限重试。
  → 施工对策：删 CR 后必须四样对账（CR + 容器 + Matrix 房间 + MinIO 用户）。见 `S0-001-team-delete-broken.md` + `finding-team-delete-403.log`。
- **S0-002 StepFun RPM=10 限流**：当前账号 `step-3.7-flash` RPM=10，多 worker 并发即 429
  `request limited RPM reached, current: 11, limit: 10`；控制面与 worker 共用同一 key 共享预算。
  → 影响 6 Agent 团队与对照实验可行性，需用户决策（升档 / 降并发 / 分 key）。见 `S0-002-stepfun-rpm-10.md` + `s0-002-ratelimit-429.json.txt`。
- **S0-003 artifact 交接路径语义**：任务外直接写 `shared/<任意路径>` 只落在写者自己的 MinIO workspace，
  其他 worker 读不到；交接必须走 task 生命周期同步（`shared/tasks/{task-id}/`）。见 `S0-003-artifact-handoff-semantics.md`。
- **S0-004 MCP 网关注册与调用三个坑**：官方 `setup-mcp-proxy.sh` 硬编码 CONSOLE_URL 连不上 console（只能手工作业 API）；
  提取 worker 网关钥匙必须 `tr` 掉引号与 `\r`（否则 key-auth 401）；网关把原始路径透传上游，须 PathRewrite 成 `/mcp`。
  最终链路 controller/worker → Higress → spike-mcp → PONG 全通。见 `S0-004-mcp-gateway-e2e.md`。

## 证据文件索引

- `mcp-gateway-initialize.txt` / `mcp-gateway-initialized-and-tools-list.txt` / `mcp-gateway-tools-call.txt`
  —— MCP 网关端到端原始请求/响应（initialize → tools/list → tools/call，钥匙已打码）
- `S0-001-team-delete-broken.md` + `finding-team-delete-403.log` + `team-spec.json` + `team-before-delete.json`
- `S0-002-stepfun-rpm-10.md` + `s0-002-ratelimit-429.json.txt`
- `S0-003-artifact-handoff-semantics.md`
- `S0-004-mcp-gateway-e2e.md`

## 施工知识回写

平台行为实测（含 MCP 注册路径）已回写 `wiki/platform-agentteams.md`；安装/凭证纪律见 `deploy/README.md`。
