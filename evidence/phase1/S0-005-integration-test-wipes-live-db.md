# S0-005 integration 测试清活库事故（2026-08-08）

## 事故

T8 施工期间本地跑 `pytest tests/integration`，`pg_engine` fixture（`tests/conftest.py`）对
`TEST_DATABASE_URL` 默认值 `postgresql+psycopg://…:5432/control_plane`（**活库**）执行
`Base.metadata.drop_all` → control_plane 库 12 张业务表被清空，全部现存 case/experiment/changeset
数据丢失（均为早期测试残留，无真实业务损失）。mcp_* 表因不在同一 metadata 而幸存。
同期主控在 T2 验收时也跑过同一套测试——彼时库里只有 T2 自己的测试残留，无人察觉。

## 根因

`TEST_DATABASE_URL` 默认值指活库 + fixture 无条件 `drop_all`（setup 与 teardown 各一次）。
设计地雷：任何人任何时刻跑 integration 测试都会清活库。

## 修复（commit 见本文件同提交）

1. `control-plane/tests/conftest.py`：`TEST_DATABASE_URL` 默认改为 `control_plane_test`（scratch 库），并留此注释警告。
2. `deploy/postgres/init/01-create-databases.sql`：追加 `CREATE DATABASE control_plane_test`（新环境自带）。
3. 现存环境手工 `CREATE DATABASE control_plane_test`。
4. 活库 schema 经 `compose restart control-plane`（entrypoint alembic upgrade head）恢复。

## 验证

- `pytest tests/integration` 在 scratch 库 6/6 绿；活库 21 表前后不变。
- 教训写入 wiki/build-guide：凡是测试/脚本的数据库连接默认值，一律指 scratch，活库必须显式 env 覆盖。

## 纪律（冻结）

- 任何测试/脚手架的数据库默认值**禁止指向活库**；活库连接必须显式 `DATABASE_URL` 环境变量注入。
- PR/验收检查清单新增一项：grep `drop_all` / `DROP TABLE` / `TRUNCATE` 的作用域。
