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

## docker-compose 演示环境（demo-app + pgvector + mcp + feishu mock）

待 Phase 1 建造后补齐：`compose.yaml` 由本目录维护，服务清单与 plan-v3 §3 对齐。

## 凭证纪律

- StepFun key 只出现在：Higress 网关侧配置、`~/agentteams-manager.env`（安装脚本落盘）、本地 shell 环境
- 仓库内只允许 `.env.example` 模板；`.env*` 已 gitignore
- Worker 永远只持 Consumer Token，不持真实 LLM Key
