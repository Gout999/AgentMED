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
│   ├── db.py / models.py    # SQLAlchemy 2.x（demo_app 库，9 张业务表）
│   ├── schema.py            # Alembic head 校验 + 旧库只读接管校验
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
├── alembic/                 # 部署迁移（001：9 张表 + PostgreSQL vector extension）
├── docker-entrypoint.sh     # upgrade head 成功后才启动服务
├── scripts/verify_schema_adoption.py  # 未版本化旧库的只读接管 verifier
├── tests/unit/              # 纯逻辑单测
├── tests/integration/       # 对活服务 E2E
└── Dockerfile
```

## 跑法（compose）

```bash
# 1. 从仓库根目录创建本地配置；填完模板中所有空白必填项。
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env；需要真实聊天时再填 STEPFUN_API_KEY。

# 2. 先做 Compose 静态配置校验，再启动 postgres + demo-app。
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  up -d --build postgres demo-app

# 3. 默认仅绑定 127.0.0.1；验证 liveness 与带鉴权读面。
curl -s http://127.0.0.1:8080/health          # {"status":"ok"}
curl -s http://127.0.0.1:8080/v2/versionsets \
  -H 'Authorization: Bearer <CASELOOP_READ_TOKEN>'
```

`demo-app` 镜像的 entrypoint 会在 uvicorn 前执行 `python -m alembic upgrade head`。
迁移失败时容器不会启动；lifespan 只校验当前 revision 并幂等写入种子数据，不会建表。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `STEPFUN_API_KEY` | 空 | StepFun key（**不入库**，仅环境注入） |
| `STEPFUN_BASE_URL` | `https://api.stepfun.com/step_plan/v1` | StepFun 套餐端点；live B1 只接受此官方地址 |
| `STEPFUN_MODEL` | `step-3.7-flash` | 运动员模型 |
| `DATABASE_URL` | Compose 从 `POSTGRES_USER/POSTGRES_PASSWORD` 构造 | demo_app 库；Compose 下不要另行硬编码 |
| `CASELOOP_READ_TOKEN` | 空（必填） | quality:read 令牌；未配置时授权面 fail closed |
| `CASELOOP_WRITE_TOKEN` | 空（必填） | quality:write 令牌（仅 Release Controller；必须与 read token 不同） |
| `RELEASE_CONTROLLER_CLIENT_SECRET` | 空（必填） | 仅用于签发 Release Controller 写令牌 |
| `QUALITY_READER_CLIENT_SECRET` | 空（必填） | 只读 client_credentials 凭证；必须与写凭证不同 |
| `LLM_RPM_LIMIT` | `8` | 集中限速（D-001：留 2 余量给 AgentTeams worker） |
| `OPERATION_TTL_HOURS` | `24` | 异步 operation TTL（Q1 裁决） |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 空 | OTLP 导出端点；空 → no-op 降级 |

## 数据库迁移与既有库接管

全新数据库无需手工迁移：容器 entrypoint 会执行 `upgrade head`。只有早期由
`create_all()` 建出的、没有 `alembic_version` 的既有库需要一次性接管，而且必须 fail closed：

```bash
# postgres 必须已启动；先构建包含当前 migration/verifier 的镜像。
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d postgres
docker compose --env-file deploy/.env -f deploy/compose.yaml build demo-app

# 只读核对 9 张表、列/索引/约束及 PostgreSQL vector extension。
# verifier 成功也不会创建 alembic_version，更不会自动 stamp。
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  run --rm --no-deps --entrypoint python demo-app \
  scripts/verify_schema_adoption.py

# 仅在上一步 VERIFIED 且操作员确认目标库正确后，显式登记初始 revision。
docker compose --env-file deploy/.env -f deploy/compose.yaml \
  run --rm --no-deps --entrypoint python demo-app \
  -m alembic stamp 001

# 随后恢复正常 entrypoint；当前及未来 migration 会按顺序执行。
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d demo-app
```

verifier 返回 `REFUSED` 时不得 stamp；应先调查 schema drift。直接让未版本化旧库走正常
entrypoint 也会因表已存在而失败，并且不会把失败状态伪装成已迁移。

## 接口速览

- `POST /chat`：客服对话（body `{message}`；返回 `request_id/answer/digests`，每次落 `/logs`）。
- `POST /feedback`：用户反馈（`{request_id, rating, comment}`；comment 入口 PII 脱敏）。
- `POST /oauth/token`：client_credentials 签发演示令牌。
- `/v2/versionsets`：CRUD + `/stage` `/canary` `/promote` `/rollback` + `/status`；promote
  必须携带 Release Controller 已审批的 `expected_active_digest`，并在全局 active-set 锁内核对。
- `/v2/operations/{id}`：异步写操作查询（TTL 24h，过期 410）。
- `/v2/logs`、`/v2/feedback`：读面（cursor 分页 + 时间窗 + versionset_id/rating 过滤）。
- `/admin/inject/{B1,B2,B3,B4}`、`/admin/reset`：故障注入（x-internal，生产必须移除）。

## 种子数据

`seeds/kb_entries.yaml` 共 **36 条**：售后政策 10 / 产品参数 14 / 物流规则 12，
与 `contracts/fixtures/probes-customer-service.yaml` 的探针 ground-truth 对齐
（如 cs-006「X200 续航 30 小时」、cs-009「Z30 20000mAh/74Wh 可上飞机」）。
迁移 revision 校验通过后，启动时幂等种子 + 建立**基线 VersionSet**
`vs_baseline0000000001`（active，
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
# 以下命令从仓库根目录执行。
# offline 单元（migration tests 使用 disposable SQLite，无需外部 DB）
python3 -m venv /tmp/caseloop-demo-venv
/tmp/caseloop-demo-venv/bin/pip install -r demo-app/requirements.txt -r demo-app/requirements-dev.txt
/tmp/caseloop-demo-venv/bin/pytest demo-app/tests/unit -q

# 集成（需 compose 起真 PG + 服务）
/tmp/caseloop-demo-venv/bin/pytest demo-app/tests/integration -q -s \
  -o addopts="" # 或设置 CASELOOP_QUALITY_API_BASE_URL 指向你的服务

# 本地冒烟（服务层直连 PG，无需容器）：python scripts/smoke_local.py
```

## ⚠️ 运行态污染与一键恢复（跑完测试必读）

**conformance / integration 测试会污染运行态**：测试会创建并 promote 自己的
VersionSet（`v-test-*`），把基线 `vs_baseline0000000001` 顶成 `superseded`，
`active` 变成测试残留；且测试版本的 `model=step-2-16k`（该模型在本账号不存在），
导致 `/chat` 全挂（`chat_logs.status=provider_error`，返回兜底文案）。
这是 promote→active 契约语义的预期副作用，不是测试放水。

**跑完 conformance / integration 后必须执行一键恢复**（幂等，可重复跑）：

```bash
bash demo-app/scripts/reset_state.sh
# 效果：清掉全部 v-test-* 残留 + 恢复基线 active + 验证输出
```

验收/联调的标准姿势：

```bash
# Quality API live conformance（会污染 active；数量随合同演进，不固定写死）
cd /path/to/caseloop
CASELOOP_QUALITY_API_BASE_URL=http://127.0.0.1:8080 \
  eval-harness/.venv/bin/python -m pytest contracts/conformance/test_quality_api.py -q
# 一键恢复交付终态
bash demo-app/scripts/reset_state.sh
# 再验证 chat 真实可用
curl -s -X POST http://127.0.0.1:8080/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"X200 续航是多久？"}'
```

conformance 纪律：**禁止修改测试放水**；`test_quality_api.py` 对空实现应失败，只有真实
实现才能转绿。离线 schema/Wilson/V4/V5 contract tests 是契约资产自洽，必须常绿；
测试数量随合同演进，不在 README 固定写死。

## 关键设计决策（详见 `OPEN-ISSUES.md`）

- 写面仅 Release Controller 持有 `quality:write`；演示令牌经环境变量配置。
- 生命周期迁移为**异步 operation**（pending→succeeded），受理时同步完成 CAS/合法性校验。
- 检索作用于 live KB 全量条目；KB manifest 用于 digest 绑定（Phase 1 简化，Phase 2 改向量检索）。
