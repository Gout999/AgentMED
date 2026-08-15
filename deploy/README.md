# deploy/ —— 环境部署

## AgentTeams 本地安装（v1.2.1，StepFun 大脑）

前置：Docker Desktop 完全启动（Mac M 系列 ≥ 4.39.0），Docker VM 内存 ≥ 8 GB；
端口 18080 / 18001 / 18088 / 18888 / 13000 空闲；系统/浏览器代理放行 `127.0.0.1` 与
`*-local.agentteams.io`。

```bash
# 密钥不入库：从本地安全来源导出（勿写入任何 git 跟踪文件）
export STEPFUN_API_KEY=<stepfun key>
export STEPFUN_BASE_URL=https://api.stepfun.com/v1

AGENTTEAMS_NON_INTERACTIVE=1 \
AGENTTEAMS_LANGUAGE=zh \
AGENTTEAMS_VERSION=v1.2.1 \
AGENTTEAMS_LLM_PROVIDER=openai-compat \
AGENTTEAMS_OPENAI_BASE_URL="$STEPFUN_BASE_URL" \
AGENTTEAMS_LLM_API_KEY="$STEPFUN_API_KEY" \
AGENTTEAMS_DEFAULT_MODEL=step-3.7-flash \
AGENTTEAMS_ADMIN_PASSWORD='<本地演示管理员密码，自行设定>' \
bash <(curl -sSL https://raw.githubusercontent.com/agentscope-ai/AgentTeams/main/install/agentteams-install.sh)
```

安装脚本（4697 行）已核对的环境变量契约（2026-08-07，main 分支）：

- `AGENTTEAMS_LLM_PROVIDER=openai-compat` + `AGENTTEAMS_OPENAI_BASE_URL`：任意 OpenAI 兼容端点（须带 `/v1`）
- `AGENTTEAMS_VERSION`：钉版本，不加为 latest
- `AGENTTEAMS_ADMIN_USER`（默认 admin）/ `AGENTTEAMS_ADMIN_PASSWORD`（不设则自动生成，≥8 位）
- **不要设 `AGENTTEAMS_DATA_DIR`**：脚本把它当 docker volume 名用，传绝对路径会直接报错（2026-08-07 实测踩坑）
- 检测到已有 `~/agentteams-manager.env` 时会进入升级分支，非交互模式也会在终端弹"升级方式"菜单
  —— 要全新安装就先 `install.sh uninstall` 并删掉该 env 文件
- 卸载：`bash agentteams-install.sh uninstall`（停删 Manager/Worker/controller 容器、网络与日志）

安装后验证：

```bash
docker ps | grep -E "agentteams-controller|agentteams-manager"
docker exec agentteams-controller agt version          # 期望 v1.2.1（不再是 dev）
curl -s http://127.0.0.1:18001/                         # Higress 控制台
docker exec agentteams-controller curl -sf http://127.0.0.1:9000/minio/health/live
```

组件与端口地图、常见坑见 `../agents/README.md`（随 agents/ 定义一起完善）。

## Docker Compose 本地栈

`compose.yaml` 当前包含 PostgreSQL/pgvector、demo-app、control-plane、固定 legacy-v3
outbox dispatcher 和 console。它是本地演示栈，不是生产部署证明；
`NOTIFICATION_ADAPTER=feishu-mock` 也只能作为 contract/replay 配置，不能描述为 live Feishu。

### 首次配置与启动

所有宿主端口默认只绑定 `127.0.0.1`。不要为了方便把 `AGENTMED_BIND_HOST` 改成
`0.0.0.0`；确需远程访问时，应先配置受认证的 ingress 和防火墙策略。

```bash
# 从仓库根目录执行。deploy/.env 不入库；每个空白必填项都要使用独立值。
cp deploy/.env.example deploy/.env
chmod 600 deploy/.env
# 编辑 deploy/.env 后先做静态校验；缺任一必填项会直接报错。
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet

docker compose --env-file deploy/.env -f deploy/compose.yaml \
  up -d --build postgres demo-app control-plane outbox-dispatcher console
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
```

`deploy/.env.example` 中必须填写：PostgreSQL password、demo read/write bearer token、
两类 OAuth client secret、control-plane/approval/gate authority token、完整 role-token JSON，
以及两项独立的 public credential/cursor secret。demo bearer/OAuth 两组凭证和 public 两项
secret 都有禁止复用的 fail-closed 校验；role-token JSON 中的值也应各自独立。
`STEPFUN_API_KEY` 对栈启动不是必填，但真实 `/chat` provider 调用需要它。

默认宿主入口：

| 服务 | 默认地址 | 边界 |
|---|---|---|
| PostgreSQL | `127.0.0.1:5432` | 仅本机；三逻辑业务库由 init 脚本创建 |
| demo-app | `http://127.0.0.1:8080` | `/health` liveness；Quality API 读写面需凭证 |
| control-plane | `http://127.0.0.1:18090` | `/healthz` liveness；Compose 使用 `/readyz` readiness |
| console | `http://127.0.0.1:8088` | 通过 nginx 反代 control-plane |

`POSTGRES_PASSWORD` 只初始化全新的 `agentmed_pgdata` volume；修改 `.env` 不会轮换既有
数据库角色密码。既有 volume 必须由操作员单独完成数据库凭证轮换。

### demo-app Alembic 启动与旧库接管

demo-app 镜像 entrypoint 在 uvicorn 前执行 `python -m alembic upgrade head`。初始
revision `001` 管理 9 张业务表和 PostgreSQL `vector` extension；应用建连和 lifespan
不会执行 schema DDL。迁移或 head 校验失败时服务拒绝启动。

全新数据库直接使用正常 `up` 即可。早期 `create_all()` 生成、没有
`alembic_version` 的旧库必须先走只读 verifier，禁止自动 stamp：

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d postgres
docker compose --env-file deploy/.env -f deploy/compose.yaml build demo-app

# 只读；成功输出 VERIFIED，但不会写 alembic_version。
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  run --rm --no-deps --entrypoint python demo-app \
  scripts/verify_schema_adoption.py

# 只允许在 verifier 成功且人工确认目标库正确后执行。
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  run --rm --no-deps --entrypoint python demo-app \
  -m alembic stamp 001

docker compose --env-file deploy/.env -f deploy/compose.yaml up -d demo-app
```

缺表、列/索引/约束漂移、缺少 `vector` extension 或已存在 Alembic 版本记录都会让
verifier 返回 `REFUSED`。此时必须调查实际状态，不能通过手工 stamp 掩盖差异。

## 凭证纪律

- StepFun key 只出现在：Higress 网关侧配置、`~/agentteams-manager.env`（安装脚本落盘）、本地 shell 环境
- 仓库内只允许 `.env.example` 模板；`deploy/.env` 必须保持未跟踪
- Worker 永远只持 Consumer Token，不持真实 LLM Key

## 附属运行件（自 Agent Station 并入，两仓库口径）

Agent Station 仓库的 GitHub 镜像已删除（本地目录保留）；其运行期胶水件随本仓库分发：

- `relay/openai-content-length-proxy.mjs` — 模型路径长度代理（网关 → 8089 → AgentMED(8088) → StepFun）；
  本机正在运行的 relay 进程即该文件（拷贝自 agent-station commit e8a123f）。
- `sandbox-image/` — 沙箱验证隔离镜像 `agent-station/copaw-worker:s0-acceptance-v123` 的构建件
  （Dockerfile + CoPaw task-result acceptance overlay + langfuse-inspect skill）；
  本机演示直接使用已构建镜像，重建 = 在该目录 docker build。
