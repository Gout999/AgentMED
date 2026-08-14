# demo-app OPEN-ISSUES（T1 施工过程中发现的契约/实现问题）

> 按 scope 纪律：契约问题不直接改 contracts/，记录于此供主控裁决。日期 2026-08-07。

## 1. 合成 VersionSet 的 prompt digest 无内容绑定

- **现象**：conformance `_sample_content()` 提交的 prompt 只有 `{prompt_id, version, digest}`，
  无正文。服务端无法按「内容」计算 prompt.digest。
- **当前实现**：已注册版本（`prompts/versions.json` 内）→ digest 内容绑定；
  未注册版本 → digest = JCS({prompt_id, version})（元数据绑定）。
- **影响**：两个同 `prompt_id+version` 但正文不同的版本 digest 相同（仅在未注册合成版本上可能）。
  基线/真实版本不受影响。Phase 2 建议引入「prompt 注册必须含内容摘要」约束。

## 2. /logs 的 versionset_id 语义（故障注入期间）

- **现象**：B1 注入后，线上 prompt 偏离任何已注册 VersionSet（契约要求如此）。
  /logs 仍记 `versionset_id=活跃基线 id`，但 `prompt_digest` 已偏离该版本的 prompt.digest。
- **当前实现**：versionset_id 恒为「当前 active VersionSet」，digests 反映 live 实际内容。
  归因层用 digest 差异发现漂移（这是本意）。
- **待主控确认**：是否要求 /logs 在注入期间给 `versionset_id` 一个特殊值（如 `""` 或 `faulted:B1`）
  以显式标记「偏离注册版本」。当前选型利于归因「版本+digest 联合看」。

## 3. canary → canary 再灰度允许

- **现象**：openapi 只画了 staged→canary；control-plane 的 FakeQualityClient 允许 staged/canary→canary。
- **当前实现**：允许 canary 从 staged 或 canary 发起（re-canary 更新 percent），promote 从 staged 或 canary。
- **理由**：与控制面 Fake 行为对齐；契约未明文禁止。若主控要求严格单跳，改 `ALLOWED_FROM` 即可。

## 4. rollback_to=digest 的匹配口径

- **现象**：RollbackRequest.rollback_to 支持 `previous` 或「完整 digest」。
- **当前实现**：`previous` = 最近被 promote 顶掉的 superseded 版本；digest 匹配 = 全等 VersionSet.digest。
- **边界**：若某版本从未 active 或已被 rolled_back，digest 匹配也能恢复为 active（契约未禁止，但语义上
  回滚目标应为「曾承担流量」的版本）。建议 Phase 2 收紧。

## 5. 检索的 KB 范围简化

- **现象**：D-001 #12 说 Phase 1 案例检索用「全文+元数据过滤」，向量检索 Phase 2 启用。
- **当前实现**：检索作用于 live KB 全量条目（关键词子串打分），KB manifest 只用于 digest 绑定，
  未做「按 manifest 条目白名单过滤」。
- **影响**：合成 VersionSet（manifest 只含 x200）promote 后 chat 仍能检索全部种子条目。
  对演示闭环无影响；Phase 2 接入向量检索时建议把 manifest 作为条目作用域。

## 6. [已解决 2026-08-11] pgvector 与表结构的部署所有权

- **原问题**：demo-app 曾在每次数据库建连时执行 `CREATE EXTENSION IF NOT EXISTS vector`，
  lifespan 再通过 `Base.metadata.create_all()` 建表；这不是可审计的部署迁移路径。
- **当前实现**：`alembic/versions/001_initial_demo_schema.py` 是 9 张业务表和 `vector`
  extension 的部署迁移。容器 entrypoint 在 uvicorn 前执行 `alembic upgrade head`；应用建连和
  lifespan 不再执行 schema DDL。`deploy/postgres/init/01-create-databases.sql` 对新 volume 的
  extension 初始化只是幂等 bootstrap，不能替代 Alembic 版本记录。
- **既有库边界**：未版本化旧库不会自动 stamp。必须先运行只读
  `scripts/verify_schema_adoption.py`，确认 schema/extension 精确匹配后，再由操作员显式
  `alembic stamp 001`；任意 drift 都拒绝接管。

## 7. [已解决 2026-08-11] /oauth/token 校验 client_secret

- **原问题**：openapi 声明 client_credentials tokenUrl=/oauth/token，但演示实现曾只按
  `client_id` 签发令牌。
- **当前实现**：`release-controller` 与 `quality-reader/reader` 都必须提交各自精确匹配的
  `client_secret`；缺失配置返回 503，错误凭证返回 401。read/write bearer token 或两类
  OAuth client secret 相同会 fail closed，避免权限边界退化。

## 8. 业务端点鉴权为有意简化（主控确认保留）

`POST /chat`、`POST /feedback` **不要求鉴权**：无 Authorization 头、垃圾 token 均返回 200。
这是**有意设计**，理由：

- openapi 全局 `security: []`、逐端点 opt-in——这两个业务端点**不在 Quality API 契约约束内**
  （契约只约束 `/v2/*` 与 `/admin/*`）。
- 治理面的权威边界是 **Quality API 写面**：`quality:write` 令牌仅签发给 Release Controller，
  只有它能改 VersionSet 生命周期、做故障注入；业务端点（用户问客服、点踩）面向终端用户，
  演示环境不做用户侧认证，真实部署应在网关层加用户会话鉴权。

## 9. conformance / integration 测试会污染运行态（有 reset 闭环）

`conformance`/`integration` 测试会创建并 promote 自己的 VersionSet（`v-test-*`），
把基线 `vs_baseline0000000001` 顶成 `superseded`，`active` 变为测试残留；
且测试版本 `model=step-2-16k`（本账号不存在）导致 `/chat` 全挂（`chat_logs.status=provider_error`）。
这是 promote→active 契约语义的预期副作用，**不是测试放水**。

**跑完 conformance / integration 后必须执行**（幂等，可重复跑）：

```bash
bash demo-app/scripts/reset_state.sh   # 清 v-test-* 残留 + 恢复基线 active + 验证
```

验收标准姿势：conformance 39 绿 → `reset_state.sh` → `curl /chat` 真实可用（answer 非兜底、`chat_logs.status=ok`）。
