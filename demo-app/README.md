# demo-app「小智客服」—— CaseLoop 被治理的演示应用

3C 数码电商客服（FastAPI RAG），作为 CaseLoop 治理层的**被治理对象**：
实现 **Quality API v2 契约**并内置 **B1–B4 故障注入端点**，供治理层演示
"投诉→归因→修复→门禁→灰度"全闭环。

- LLM：**真实调用 StepFun** `step-3.7-flash`（无 mock），集中限速 **8 RPM** + 429 指数退避。
- 知识库：pgvector 表结构已建（向量列 1024 **预留不启用**）；Phase 1 检索用**全文 + 元数据过滤**。
- Prompt：git 版本化（`prompts/` 文件 + `versions.json` 版本元数据）。
- OTel：请求级 trace（LLM/检索/版本 digest 挂 span 属性），OTLP 端点可配，无 collector 时 no-op。

## 目录结构

```
demo-app/
├── app/
│   ├── main.py              # FastAPI 装配（lifespan 初始化）
│   ├── config.py            # 环境变量配置
│   ├── db.py / models.py    # SQLAlchemy 2.x（demo_app 库，11 张表）
│   ├── jcs.py               # JCS(RFC 8785)+SHA-256 digest
│   ├── rate_limit.py        # 滑动窗口限速器（8 RPM）
│   ├── llm.py               # StepFun 客户端（真实调用 + 指数退避）
│   ├── retrieval.py         # Phase 1 全文+元数据检索
│   ├── live_config.py       # 线上配置解析（active versionset + 故障覆盖）
│   ├── versionset_service.py / operations.py  # Quality API v2 状态机
│   ├── faults.py            # B1–B4 注入逻辑
│   └── routers/             # quality / chat / feedback / admin
├── prompts/                 # prompt 模板（git 版本化）+ versions.json
├── seeds/kb_entries.yaml    # 36 条种子 KB
├── tests/unit/              # 纯逻辑单测
├── tests/integration/       # 对活服务 E2E
└── Dockerfile
```

## 跑法（compose）

```bash
# 1. 导出 StepFun key（不入库；demo-app 容器通过环境变量读取）
export STEPFUN_API_KEY=sk-xxx
export STEPFUN_BASE_URL=https://api.stepfun.com/v1

# 2. 起 postgres + demo-app（compose 服务名：postgres / demo-app）
docker compose -f deploy/compose.yaml up -d --build postgres demo-app

# 3. 验证
curl -s http://127.0.0.1:8080/health          # {"status":"ok"}
curl -s http://127.0.0.1:8080/v2/versionsets  # 需带 Bearer 令牌
```

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `STEPFUN_API_KEY` | 空 | StepFun key（**不入库**，仅环境注入） |
| `STEPFUN_BASE_URL` | `https://api.stepfun.com/v1` | OpenAI 兼容端点 |
| `STEPFUN_MODEL` | `step-3.7-flash` | 运动员模型 |
| `DATABASE_URL` | `postgresql+psycopg://caseloop:caseloop@postgres:5432/demo_app` | demo_app 库 |
| `CASELOOP_READ_TOKEN` | `conformance-read-token` | quality:read 演示令牌 |
| `CASELOOP_WRITE_TOKEN` | `conformance-write-token` | quality:write 演示令牌（仅 Release Controller） |
| `LLM_RPM_LIMIT` | `8` | 集中限速（D-001：留 2 余量给 AgentTeams worker） |
| `OPERATION_TTL_HOURS` | `24` | 异步 operation TTL（Q1 裁决） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 空 | OTLP 导出端点；空 → no-op 降级 |

## 接口速览

- `POST /chat`：客服对话（body `{message}`；返回 `request_id/answer/digests`，每次落 `/logs`）。
- `POST /feedback`：用户反馈（`{request_id, rating, comment}`；comment 入口 PII 脱敏）。
- `POST /oauth/token`：client_credentials 签发演示令牌。
- `/v2/versionsets`：CRUD + `/stage` `/canary` `/promote` `/rollback` + `/status`。
- `/v2/operations/{id}`：异步写操作查询（TTL 24h，过期 410）。
- `/v2/logs`、`/v2/feedback`：读面（cursor 分页 + 时间窗 + versionset_id/rating 过滤）。
- `/admin/inject/{B1,B2,B3,B4}`、`/admin/reset`：故障注入（x-internal，生产必须移除）。

## 种子数据

`seeds/kb_entries.yaml` 共 **36 条**：售后政策 10 / 产品参数 14 / 物流规则 12，
与 `contracts/fixtures/probes-customer-service.yaml` 的探针 ground-truth 对齐
（如 cs-006「X200 续航 30 小时」、cs-009「Z30 20000mAh/74Wh 可上飞机」）。
启动时幂等种子 + 建立**基线 VersionSet** `vs_baseline0000000001`（active，
P0 `prompts/system.md` v1.4.2 + 全量 KB manifest + step-3.7-flash temperature=0）。

## 故障注入端点用法

| 故障 | 注入动作 | 效果 |
|---|---|---|
| B1 prompt 回归 | `POST /admin/inject/B1` | 线上 prompt 切到 P1（v1.4.3，退货需人工审核）→ `/logs` prompt_digest 偏离基线 |
| B2 KB 回归 | `POST /admin/inject/B2` | X200 续航 30h→8h（条目内容/digest 重算）→ kb_manifest_digest 偏离 |
| B3 model 漂移 | `POST /admin/inject/B3` | temperature 0→1.2, max_tokens 1024→64 → model_digest 偏离 |
| B4 交互 | `POST /admin/inject/B4` | prompt 引用 KB `trade_in_program_v2` + 活动条款更新 |
| 恢复 | `POST /admin/reset` | 清除全部故障，恢复基线 |

行为与 `contracts/fixtures/b1..b4-*.yaml` ground-truth 对齐；`InjectResult.detail`/`ground_truth_ref` 回显出处。

## 测试

```bash
# 单元（纯逻辑，无需 DB）
python3 -m venv /tmp/caseloop-demo-venv && /tmp/caseloop-demo-venv/bin/pip install -r requirements.txt -r requirements-dev.txt
/tmp/caseloop-demo-venv/bin/pytest tests/unit -q

# 集成（需 compose 起真 PG + 服务）
/tmp/caseloop-demo-venv/bin/pytest tests/integration -q -s \
  -o addopts="" # 或设置 CASELOOP_QUALITY_API_BASE_URL 指向你的服务

# 本地冒烟（服务层直连 PG，无需容器）：python scripts/smoke_local.py

# conformance（最终验收：39 全绿）
cd contracts/conformance && CASELOOP_QUALITY_API_BASE_URL=http://127.0.0.1:8080 pytest -q
```

conformance 纪律：**禁止修改测试放水**；`test_quality_api.py` 15 项对空实现全红，
只有真实现才能转绿。`test_schemas.py`(19) + `test_wilson.py`(5) 是契约资产自洽，必须常绿。

## 关键设计决策（详见 `OPEN-ISSUES.md`）

- 写面仅 Release Controller 持有 `quality:write`；演示令牌经环境变量配置。
- 生命周期迁移为**异步 operation**（pending→succeeded），受理时同步完成 CAS/合法性校验。
- 检索作用于 live KB 全量条目；KB manifest 用于 digest 绑定（Phase 1 简化，Phase 2 改向量检索）。
