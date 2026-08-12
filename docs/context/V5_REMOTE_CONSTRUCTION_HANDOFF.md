# V5 远端施工交接（remote construction handoff）

> 本文件是 2026-08-12 本地收口之后，交给远端施工机（强模型、无 Docker、无 provider
> key）的唯一交接入口。本文件所在的 commit 即为远端施工的起点基线。

## 分工边界（product owner 裁决，2026-08-12）

- **远端机**：负责 V5-2A 起所有代码施工（契约冻结、runtime 实现、单元/契约/CLI
  层验证、语义提交、阶段 evidence）。
- **本地机**：负责 disposable PostgreSQL journey、live/provider facet、最终 evidence
  归档与全项目收口。远端回传的每个阶段，PG journey 由本地补跑后才能记为
  `replay=PASS`。
- 原因：远端机没有 Docker（跑不了 disposable PostgreSQL）和 provider key（跑不了
  live）。远端的"全绿"不等于阶段关闭；缺少 PG journey 的阶段在远端最多记为
  `VERIFYING（replay=LOCAL_VERIFICATION_PENDING）`。

## 起点基线

- 分支：`codex/v5-convergence`。本文件所在 commit 即交接 HEAD；其直接祖先
  `4d0005a` 是 R4 status commit，语义主体 `365c2c8`。
- 已关闭：C0–C5 收敛系列、D2（contract-only）、R3-full（record/get/diff runtime）、
  R4（First System Case closure）。R4 的 remediation 已经过第二轮 detached
  clean-checkout 复查，全部门禁绿（见
  `evidence/v5/stage-1/first-system-case/r4firstcase_20260812T072114Z_365c2c8/verification.md`
  的 "Remediation confirmation pass" 节）。
- 唯一 `ELIGIBLE` package：**V5-2A-0 contract/owner/migration freeze**，随后是
  V5-2A Durable Work Kernel 实现（Master §17.6）。V5-2B/2C/3A/4/5 仍被 Master §17
  前驱链锁定，禁止跨层堆叠。

## 阅读顺序（零先验接手）

1. `AGENTS.md`（权威与安全铁律，含 canonical evidence-facet 词汇表）；
2. `docs/README.md`（文档权威与归档策略）；
3. `docs/plans/v5-master-execution-plan.md`（当前 version `2026-08-12.10`，重点
   §17.6 V5-2A）；
4. `docs/context/PROJECT_STATE.md`、`docs/context/LAST_HANDOFF.md`；
5. D2/R3/R4 的 evidence bundle（`evidence/v5/`）作为"阶段关闭长什么样"的样板。

## 远端可执行的验证（无 Docker 安全子集）

```bash
# 编译器确定性（每次改 contracts/v5 后必跑）
cd contracts/compiler && PYTHONPATH=.. python3 -m compiler emit && git -C .. diff --exit-code -- v5/generated/

# 全量 conformance（V3/V4/V5）
cd contracts && /path/to/eval-harness/.venv/bin/python -m pytest \
  conformance/test_schemas.py conformance/test_wilson.py conformance/test_v4_*.py conformance/test_v5_*.py -q

# control-plane 单元 + C1–C5 wave checkers（SQLite，显式去掉 PG 环境变量）
cd control-plane && env -u CASELOOP_ALLOW_INTEGRATION_RESET -u DATABASE_URL \
  .venv/bin/python -m pytest tests/unit tests/test_v5_c1_shadow_parity.py \
  tests/test_v5_c2_foundation.py tests/test_v5_c2_graph.py \
  tests/test_v5_c3_import_graph.py tests/test_v5_c4_allowlist_diff.py \
  tests/test_v5_c4_fallback_drill.py tests/test_v5_c5_rollback_drill.py -q

# CLI
cd cli && /path/to/control-plane/.venv/bin/python -m pytest tests -q
```

（`.venv` 路径按远端实际 checkout 位置调整；若远端无现成 venv，按各目录
`requirements*.txt` 新建。）

## 远端不可执行、必须留给本地的验证

- `control-plane/tests/integration/`（disposable PostgreSQL，需
  `CASELOOP_ALLOW_INTEGRATION_RESET=true` + `DATABASE_URL`）：远端没有 Docker，
  **不要尝试绕过**，也不要把 PG gate 的缺失记成 PASS。远端 evidence 的
  `replay` facet 记 `LOCAL_VERIFICATION_PENDING`，由本地补跑后改记。
- 任何 live/provider/agent 外部写 facet：远端没有 key，全部保持 `NOT_RUN`。
- alembic migration 的 PG 实测：远端只能做 SQLite 层与静态检查，PG upgrade/downgrade
  journey 由本地跑。

## 施工纪律（与本地一致，摘自 AGENTS.md 与 Master）

- 一个阶段一个语义系列 + 独立 verifier pass + `evidence/v5/` 下的 bundle +
  status 文档同步，然后才允许开下一个阶段。
- 诚实的 facet 记录：没跑的就是 `NOT_RUN` / `LOCAL_VERIFICATION_PENDING`，禁止把
  SQLite 单测说成 replay/live 证据。
- 不得 push、不得开 PR、不得触碰 production/外部写——回传方式（push 或 bundle）由
  product owner 决定。
- 不得批量提升 `docs/context/V5_CONVERGENCE_WIP_INVENTORY.md` 中列出的
  v4-foundation WIP 路径。

## 已知坑（不要顺手"修"，除非属于当前阶段的 allowlist）

1. R2 遗留的 from-issue e2e 在 PG journey 中保持 skip：CLI v1 workspace header
   处理有 `WORKSPACE_ACCESS_DENIED` bug；该路径已被 CLI 单测与 R4 HTTP journey
   覆盖。修它属于独立工作项，不属于 V5-2A。
2. CLI `_FROZEN_OPERATIONS` fallback 表靠归一化补偿把
   `acceptance-criteria confirm` 映射到 `case acceptance-criteria confirm`（R4
   契约的 cli 字段写法）。动 CLI 命令注册时保持这个约定，或按 C1 单一来源原则
   从源头修，但不要两处各修一半。
3. R4 激活的 5 个 intent 的 `delivery_slice` 元数据不一致（有的标 V5-0C 有的标
   V5-1C）。这是已记录的历史事实，不是新 bug；统一元数据属于独立的
   status/activation single-sourcing 工作项（C1 范畴）。
4. `contracts/v5/` 已冻结的 wire 面（11-intent V2 surface + D2/R3/R4 激活面）不得
   在 V5-2A 施工中被顺手改动；契约变更必须走自己阶段的 freeze 流程。
