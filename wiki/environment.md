# 本地环境与运行边界

[返回 Wiki 索引](INDEX.md)

> 本页区分可复用配置与一次性机器快照。端口、容器、凭证可随开发机变化；执行前必须重新检查，不能把本页日期当实时健康证明。当前仓库状态看 [PROJECT_STATE](../docs/context/PROJECT_STATE.md)。

## 开源项目的基础环境

- Python 服务各自使用本地 `.venv` 和锁定的 `requirements*.txt`。
- Console 使用 `npm ci`、项目测试命令和 `npm run build`。
- 持久化 schema 通过 Alembic migration 部署；不能依赖开发态 `create_all`。
- PostgreSQL 测试必须显式指定 disposable database，例如 `control_plane_test`；不要依赖 shell 中可能遗留的 `DATABASE_URL`。
- AgentTeams、StepFun、飞书和 Langfuse 都不是安装 AgentMED 核心代码的永久硬依赖；是否需要由当前 workload 和验收轨决定。

## 当前 v3 Compose profile

来源以 [deploy/compose.yaml](../deploy/compose.yaml) 为准。

| 组件 | 宿主入口 | 说明 |
|---|---|---|
| PostgreSQL / pgvector | `127.0.0.1:5432` | control-plane、demo-app 与 casebase 的数据底座 |
| 小智 demo-app | `http://127.0.0.1:8080` | 当前客服参考 workload 的 Quality API |
| AgentMED control-plane | `http://127.0.0.1:18090` | Compose 把宿主 `18090` 映射到容器 `8090`；原生启动默认宿主 `8090` |
| Console | `http://127.0.0.1:8088` | 只读运营界面，经 nginx 代理 control-plane |
| outbox-dispatcher | 无 HTTP 端口 | Phase 1 固定进程；通知、archive 与 Trust 等闭环不能省略它 |

只启动 API 不等于闭环栈就绪。需要根据本次任务检查数据库 migration、health、outbox worker、被治理应用和所需外部 provider。

## 当前客服参考 provider

- 小智 live 路径当前使用 StepFun 作为被测 LLM provider；模型与端点以配置和 evidence 中的确切 digest / origin 为准，不把某个型号写成产品身份。
- live Gate 的裁判必须与被测模型分离，并明确配置独立 judge identity / digest；缺失、同一身份或不可验证来源都应为 UNAVAILABLE / fail closed。
- unit、contract 与 replay 可以使用具名 fake / fixture；它们必须与 live-provider 结果分栏。
- 外部连接失败、限流、skip、空结果或 UNKNOWN 都是失败或阻塞，不得改写为 pass。

## AgentTeams v1.2.1 profile（可选适配器快照）

2026-08-07 的本地参考 profile 使用 AgentTeams v1.2.1；处理 AgentTeams 或对应 B1 证据时，必须先复核实际安装版本。更完整边界见 [AgentTeams 实测页](platform-agentteams.md) 和 [agents/RUNBOOK.md](../agents/RUNBOOK.md)。

| 入口 | 参考端口 |
|---|---|
| Higress 网关 | `18080` |
| Higress Console | `18001` |
| Element Web | `18088` |
| Manager UI | `18888` |
| Dashboard | `13000` |

这是 AgentMED 内部 Worker 的协作适配器，与被治理 Agent 使用什么 runtime / model 无关。当前 v3 六个 Worker CR 是静态部署；没有 Caseload Controller 实现，也没有动态扩缩证据。

## 通知配置

- 默认 `NOTIFICATION_ADAPTER=disabled`，任何需要通知回执的流程都应 fail closed。
- `feishu-mock` 只允许明确命名的 contract/replay，不能证明真实飞书 API、真人投诉或真人审批。
- live Feishu 需要真实 adapter、最小权限凭证、provider receipt 和精确消息绑定；缺任一项都不能称为 live。

## Langfuse 状态

Langfuse 双向集成目前是已确认需求，**尚未在仓库实现**：

- 当前 Compose 没有 Langfuse 服务或 Langfuse adapter。
- demo-app 有最小 OTel exporter 配置位，但这不等于 trace 已导出或可从 Langfuse 读取。
- 后续应通过标准 trace 传播和可插拔 `TraceSource` 接入，不能把密钥、project 或 vendor schema 写死进业务代码。

## 凭证与日志纪律

- 真实 key、token、role-token JSON、AppService token 与管理员密码只存在于本地安全来源或 secret manager；仓库只放 `.env.example`。
- 只注入当前进程需要的最小变量；不要 `source` 其他仓库或整份未知 `.env`。
- 不在 Wiki、issue、PR、evidence 或工具输出中记录密钥值、个人账号、Keychain 状态或本机私有 secret 路径。
- `docker compose config` 会展开环境变量并可能把 secret 打进终端日志。共享会话中优先读 compose 文件，或只用不会展开值的服务/镜像查询；不得复制未脱敏输出。
- Worker 只持自己的最小权限 token；只读 Exporter 不应拥有 Matrix / MinIO / control-plane 写权限。

## Git 与当前工作树

- 不直接提交或推送 `main`，不 force-push，不重写公共历史。
- 先运行 `git status --short --branch`，区分用户已有改动与本次改动，禁止清理或覆盖不相关文件。
- 使用 `codex/*` 或用户指定分支，聚焦提交；当前用户要求每次 Git 写操作前先询问。
- 2026-08-10 接手快照为 `main` / `origin/main` 同在 `81ae706`，工作树包含既有代码改动和未提交文档更新；这是日期快照，不是未来事实。

## 运行前检查

1. 读 [AGENTS.md](../AGENTS.md)、[PLANS.md](../PLANS.md) 与最新 context。
2. 只读检查 Git、端口、服务、版本与所需环境变量；不要顺手启动或重建未授权服务。
3. 明确本次是 unit，或 canonical facet 中的 `contract`、`replay`、`domain-provider-live`、`agent-causal` 等哪一项；unit/mock 本身不是成功 facet。
4. live 前确认独立 judge、真实通知/审批输入、权威数据库和 outbox worker；缺项即停止。
5. 运行后记录精确 commit、契约版本、provider identity、命令和 evidence digest，而不是只写“跑通”。
