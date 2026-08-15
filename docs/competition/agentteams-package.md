# AgentTeams 代码包（比赛提交 · 可选项应答）

> 对应比赛要求「AgentTeams 代码包：可选。如提交，应包含运行入口、依赖说明、配置文件、
> 样例输入输出和运行证据」。本文是这五项的一页式应答，全部指向仓库内真实存在的文件与
> 真实运行记录，不引用任何需要口头解释才能成立的材料。
> 更新日期：2026-08-15（第二次全自动闭环运行落定后）。

## 代码包组成与仓库链接

CaseLoop 的治理闭环跑在 AgentTeams 平台上：AgentTeams 提供团队/任务/房间/MCP 托管等编排能力，
CaseLoop 提供治理领域（确定性控制面 + 六角色 Agent 团队 + 评测装置），AgentMED 是被治理应用的
模型内核（同时承载「精确版本评估面」，是归因实验与门禁的运动员轨），Agent Station 是模型路径胶水
（网关 → 长度代理 → AgentMED → StepFun）。三个仓库组成完整代码包：

| 仓库 | 角色 | 链接 |
|---|---|---|
| **CaseLoop** | 治理控制面 + 六角色团队 + MCP 工具投影 + 评测装置 + 证据 | https://github.com/Gout999/CaseLoop |
| **AgentMED** | 被治理应用内核（模型路径 + 精确版本评估面 + 修复候选写面 + provider log） | https://github.com/er-s-an/AgentMED |
| **Agent Station** | 模型路径胶水（OpenAI 长度代理/watchdog）+ S0 证据门禁 | https://github.com/er-s-an/agent-station |

平台依赖：AgentTeams v1.2.2（https://github.com/agentscope-ai/AgentTeams ，本机容器化运行），
Higress 网关（MCP 托管与 worker 密钥注入），PostgreSQL 17，Langfuse（观测），MinIO（skill 分发）。

## 一、运行入口

### 1.1 六角色 Agent 团队（AgentTeams 原生）

```bash
# 团队定义（声明式 Team CR：六角色 worker + Human CR approver + 角色级 MCP 挂载）
cat agents/team.yaml
# 部署到 AgentTeams：
agt apply agents/team.yaml   # manager 容器内，AGENTTEAMS_AUTH_TOKEN 取 controller /var/run/agentteams/cli-token
```

### 1.2 确定性控制面（PG 权威状态）

```bash
cd control-plane
.venv/bin/python run_local.py     # 加载 deploy/.env，监听 127.0.0.1:18090
# 依赖 Postgres（本机 5433，库 control_plane；库初始化见 deploy/postgres/init/01-create-databases.sql）
```

### 1.3 角色 MCP 工具投影（12 个，网关托管）

```bash
cd mcp-servers
scripts/launch-projections.sh    # 一次性拉起 12 个角色投影（8101-8501/8102-8202/8103-8203/8104-8204/8005）
scripts/register_gateway.py      # 把投影注册进 Higress 网关（/mcp-servers/<name>/mcp，key-auth + consumer 头注入）
```

### 1.4 被治理应用内核（AgentMED）

```bash
cd AgentMED
PYTHONPATH=src .venv/bin/python .venv/bin/uvicorn agentmed.api:app --host 0.0.0.0 --port 8088 --workers 4
# 评估面：POST/GET /v2/versionsets[/{id}/evaluate] + /v2/logs（CaseLoop 实验/门禁直连）
```

### 1.5 全自动闭环的确定性驱动入口（操作者可复核重放）

闭环每段都有可独立触发的确定性入口（本仓库 scripts/ 或 mcp-servers/scripts/）：

| 段 | 入口 | 说明 |
|---|---|---|
| 段1 投诉 | `scripts/langfuse_signal_source.py` | 读 Langfuse 真实负分 → POST /api/v1/signals 立案（幂等） |
| 段3 归因 | `mcp-servers/` 投影工具 `experiment.plan/execute`（网关 MCP） | 5-cell 对照实验：freeze → start → execute → verdict |
| 段4 修复 | 投影工具 `candidate.create/workorder.draft/workorder.freeze` | 单变量修复候选 + 不可变工单 |
| 段5 验证 | 投影工具 `gate.run` + `sandbox.verify`（`scripts/sandbox/runner.py` 隔离容器） | 真实门禁三轨 + 修前/修后对照 |
| 段6 审批 | `mcp-servers/scripts/caseloop_approval_cli.py` + `scripts/approval_reader.py` | Matrix 审批消息 + nonce 验签落 grant |

### 1.6 Makefile 目标（本仓库根目录）

```
make test              # 三套测试：control-plane 790 + mcp 106 + eval-harness 81（+ conformance 24）
make control-plane     # 起控制面（后台）
make projections       # 起 12 个 MCP 角色投影（后台）
make sandbox-verify    # 隔离容器修前/修后对照（段5 样例）
make approval-reader   # 段6 reader（--once）
```

## 二、依赖说明

| 组件 | 运行时 | 依赖清单 | 安装 |
|---|---|---|---|
| control-plane | Python 3.11+ | `control-plane/requirements.txt`（fastapi/sqlalchemy/alembic/psycopg/ulid-py 等 19 项钉版本） | `python -m venv .venv && pip install -r control-plane/requirements.txt` |
| mcp-servers | Python 3.12 | `mcp-servers/requirements.txt`（fastmcp/sqlalchemy/httpx 等） | 同上（`mcp-servers/requirements.txt`） |
| eval-harness | Python 3.12 | `eval-harness/pyproject.toml` + `requirements.txt` + `requirements-dev.txt` | `pip install -e "eval-harness[dev]"` |
| AgentMED | Python 3.11+ | `AgentMED/pyproject.toml`（fastapi/httpx/openai/langfuse 等） | `pip install -e ".[dev]"` |
| Agent Station 代理 | Node.js 20+ | `agent-station/package.json`（原生 http，零运行时依赖） | 无需安装 |
| 外部服务 | Docker | AgentTeams v1.2.2 / Higress / PostgreSQL 17 / Langfuse / MinIO | 平台容器 + `deploy/compose.yaml` |
| 模型 | API | StepFun `step-3.7-flash`（运动员）+ `step-3.5-flash`（裁判，刻意异构） | 只需 `STEP_API_KEY` |

## 三、配置文件

全部密钥走环境文件注入，仓库只保留带占位的示例（真实密钥从未入库）：

| 配置文件 | 内容 | 位置 |
|---|---|---|
| `deploy/.env.example` | 控制面/团队/审批全套环境变量模板（角色令牌 JSON、审批权威令牌、DB、feishu 位、PG 端口） | `deploy/` |
| `mcp-servers/.env.example` | MCP 投影环境模板（DB、控制面地址、Quality 面、角色令牌、超时预算） | `mcp-servers/` |
| `AgentMED/.env.example` | 模型密钥/端点、Langfuse、评估面令牌（`CASELOOP_EVAL_TOKEN`）、registry 路径 | AgentMED 仓库根 |
| `agents/team.yaml` | 六角色 Team CR（SOUL 引用、角色级 MCP 挂载、Human CR approver） | `agents/` |
| `contracts/fixtures/*.yaml` | 冻结探针集、B1 故障注入 ground-truth、状态机契约 | `contracts/` |
| AgentMED `workloads/xiaozhi-customer-service/registry.json` | 受治理应用版本集注册表（P0/P1 提示词、KB、模型绑定 + 冻结 digest） | AgentMED 仓库 |

安全口径：`MCP_TRUST_GATEWAY_CONSUMER` 仅在 127.0.0.1 演示投影上开启（信任网关注入的 consumer 头），
生产默认 fail-closed；worker 不持有模型密钥（Higress 托管）。

## 四、样例输入输出

### 输入（B1 样例，`contracts/fixtures/b1-prompt-regression.yaml` + `probes-customer-service.yaml`）

- 投诉样例：『你们客服昨天还说 7 天无理由退货，今天就说激活后不能退了？我耳机都拆了！』
- 故障注入（ground-truth）：售后政策条款 v1.4.2『我们支持 7 天无理由退货…』被改为
  v1.4.3『退货需经人工审核，已激活商品不支持退货』（prompt 单层回归）。
- 探针集：16 条冻结探针（售后政策 5 / 产品参数 4 / 输出纪律 3 / 物流对照 4），
  实验取 discovery cs-001..003、hidden cs-004..005、对照 cs-013..016，每臂 3 次重复。

### 输出（第二次全自动闭环的真实裁决，与 B1 期望逐项一致）

| 输出项 | 期望（fixture） | 实际 | 证据位置 |
|---|---|---|---|
| 5-cell 裁决 | ATTRIBUTED / prompt | **ATTRIBUTED / prompt**，Δ=(1.0, 0.0, 0.0) | `evidence/experiments/exp_01M0159WMBWA8S0FPQF74SYDXS/attribution-report.json` |
| 细胞恢复率 | C/RK/RM=false，RP/G=true | **C=0.0 / RP=1.0 / RK=0.0 / RM=0.0 / G=1.0**（135 trial） | 同上 `evidence-bundle.json` |
| 修复工单 | prompt 单变量回滚 P0 | 工单 `wo_01M01A4AZ1C88D5EBVN6Z7GDC4` FROZEN（内联 unified_diff） | 控制面 `workorders` 表 + 下节证据 |
| 门禁 | 三轨 passed | rule / deterministic / live-e2e / 裁判 16/16 全 passed | `evidence/gate/eval_01M01A4BEV9QN9EBTJAAEM3320/` |
| 沙箱 | 修前 fail、修后 pass | **PASS**（隔离容器真实回放） | `var/sandbox/wo_01M01A4AZ1C88D5EBVN6Z7GDC4-sandbox-evidence.json` |
| 审批 | 人工放行 | Matrix 决策（@caseloop-approver）→ changeset APPROVED → case RELEASING | 下节 |
| 出口 | 出口 2 | **VerifiedCandidate / NOT DEPLOYED**（releases 表 0 条） | 控制面 |

## 五、运行证据

见 [`run-evidence.md`](run-evidence.md)：全部为控制面 PG、Langfuse、Matrix 房间与仓库证据目录里的
可复核记录（实验/工单/门禁/审批/代批消息的 ID、hash、digest、时间戳与核验命令），无任何手写转述结论。

## 六、快速核验（评委一分钟路径）

```bash
git clone https://github.com/Gout999/CaseLoop.git && cd CaseLoop
make test                      # 790 + 106 + 81 + 24 全绿（单元层）
ls evidence/experiments/exp_01M0159WMBWA8S0FPQF74SYDXS/   # 135 份 trial 产物 + 报告
ls evidence/gate/eval_01M01A4BEV9QN9EBTJAAEM3320/         # 门禁三轨报告 + 裁判证据
# 运行态核验见 run-evidence.md 的 curl 清单（控制面 18090 / AgentMED 8088 在线时）
```