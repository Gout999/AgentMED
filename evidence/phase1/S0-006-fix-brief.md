# S0-006 修复任务：eval MCP 实验执行链路 + 协议校验（e2e 实证缺口）

## 背景（你只许基于这些事实施工，不要猜测其他部分）

CaseLoop 仓库 `/Users/xiejiachen/caseloop`（git main 已含 T8 合并，HEAD≈9afcefa）。
T6b e2e 实战发现（存证 evidence/phase1/S0-006-attributionist-empty-experiment.md）：
归因师 agent 无法完成 5-cell 归因实验，根因四处：

1. **eval MCP server 缺执行工具**（最致命）：`mcp-servers/servers/eval_runner.py` 只有
   `experiment.plan/run/report + probe.freeze + gate.run/report`。`experiment.run` 只是把
   控制面实验状态翻成 RUNNING（POST /v1/experiments/{id}/start），**没有任何工具能真正执行
   探针、记录 cells、提交 verdict**。agent 只能空轮询。
2. **probe.freeze 零结构校验**：probe_set 任意 dict 都接受，缺键静默默认空列表 → 空实验
   静默冻结成功。agent 传错键名（顶层多套一层）三次，全部静默通过。
3. **experiment.plan 的 version_refs 参数被签名接受但实现丢弃**（误导性契约）。
4. 控制面 `POST /v1/experiments/{id}/protocol` 也无领域校验（纵深防御缺失）。

## 既有基建（必须复用，禁止重造）

- **eval-harness 执行机**：`/Users/xiejiachen/caseloop/eval-harness/eval_harness/experiment.py`
  - `ExperimentRunner(client, probe_set, settings).run(plan...)`：完整 5-cell 执行→聚合→裁决→报告
  - `DemoAppB1Driver`（ArmDriver）：通过 `QualityAPIClient.inject_fault("B1")/reset_faults()`
    切换 demo-app 故障臂（漂移/基线），`chat()` 发探针
  - `eval_harness/client.py QualityAPIClient`、`eval_harness/rate_limit.py`（RPM 节流，StepFun RPM=10 硬约束）
  - T3 曾用此跑通 B1：ATTRIBUTED/prompt Δ=1.0 CI[0.712,1.0]
- **控制面结果记录端点已存在**（`control-plane/app/api/experiments.py`）：
  - `POST /v1/experiments/{id}/cells`（cell_completed）
  - `POST /v1/experiments/{id}/verdict`（verdict_computed）
  - `POST /v1/experiments/{id}/cancel`（cancel）
- eval_runner.py 顶部已有 `_cp()` 控制面 HTTP 封装、`validation()`/`dependency_unavailable()` 错误助手。

## 施工内容（四处改动，scope 严格限定）

### A. eval MCP server 加 `experiment.execute`（异步执行工具）
文件：`mcp-servers/servers/eval_runner.py`
- 新工具 `experiment.execute(experiment_id: str)`：
  1. 从控制面 GET 实验，校验状态 == PROTOCOL_FROZEN（否则 validation 错误，提示正确前置）
  2. 从冻结协议读 probe_set（discovery/hidden_confirmation/unaffected_controls/repetitions/versions），
     **校验三探针集非空**（空 → validation 错误，明确指出哪个集为空——这条错误消息是写给 LLM
     看的操作手册，要说清正确 probe_set 结构）
  3. **后台线程**异步执行：构造 eval-harness 的 ProbeSet/ExperimentPlan，用 DemoAppB1Driver 跑
     ExperimentRunner；执行中把每个 cell 结果 POST /cells，完成时 POST /verdict（含
     hypothesis_layer=最高 Δ 层、Δ/CI、原始计数）；异常时 POST /cancel 并在控制面留错误信息
  4. 工具调用本身**立即返回** `{status:"executing", experiment_id}`（MCP 调用不能挂 20 分钟）
- eval-harness 以源码依赖方式 import（monorepo 同仓，`pip install -e ../eval-harness` 已体现在
  mcp-servers/requirements.txt 则照用；没有就加上）
- RPM 纪律：执行机自带 rate_limit，沿用默认 8 RPM

### B. probe.freeze 结构校验
- probe_set 必须含顶层键 `discovery`/`hidden_confirmation`/`unaffected_controls`（均为非空数组），
  `versions` 允许为空（版本由执行机现场捕获 digest）
- 不满足 → validation 错误，消息里给出正确结构示例（教 agent 正确键名）
- 同时修 experiment.plan：删除 version_refs 参数（契约对齐实现；改 docstring 说明版本由执行时捕获）

### C. 控制面纵深校验
文件：`control-plane/app/api/experiments.py` + 对应 service
- `POST /v1/experiments/{id}/protocol`：discovery/hidden_confirmation/unaffected_controls 任一为
  空数组 → 400（code=validation_error，message 指明空集名称）
- 不破坏既有 conformance 测试语义（契约测试若断言空协议可冻，按新领域规则更新测试并说明）

### D. agents 定义补条款
文件：`agents/` 下归因师（attributionist）SOUL 定义（找到对应 md/yaml）
- 补两条：①probe.freeze 后必须 GET 回读确认三探针集非空再 experiment.run；②runner 是自己：
  run 之后调 experiment.execute 驱动执行，之后轮询 experiment.report 直到 VERDICT_COMPUTED
- 风格与既有 SOUL 条款一致，中文

## 硬约束（违反=打回）

- **测试数据库纪律（S0-005）**：任何 pytest 运行必须显式
  `TEST_DATABASE_URL=postgresql+psycopg://caseloop:caseloop@127.0.0.1:5432/control_plane_test`
  （scratch 库）。绝不允许默认连活库 control_plane。conftest 已有此保护，不要动 conftest 默认值。
- 禁止改：contracts/（冻结）、demo-app/、console/、其他 4 个 MCP server、agents 其他角色定义
- 新测试：A/B/C 每项都要有；execute 的端到端测试用 fake/mock 执行机（不要真跑 20 分钟 LLM），
  但允许一个标记为 slow 的真机测试（默认跳过）
- 全程中文注释/commit message，风格跟仓库现状
- git：在隔离 worktree 施工（`git worktree add ../caseloop-wt-s0006 -b s0006/eval-execute main`），
  完成后 commit 到分支，不要 merge 不要 push main

## 验收标准（我会逐条复验）

1. `:8003` 工具列表出现 experiment.execute；probe.freeze 传空/错键 probe_set 返回明确校验错误
2. 全量 pytest 绿（scratch 库），含新测试；contracts conformance 39 测不破
3. 控制面 protocol 端点对空 cells 返回 400
4. diff 只触及上述 ABCD 范围文件
5. 归因师 SOUL 新增条款存在且与既有条款风格一致

## 完成后报告

输出：分支名、commit hash、改动文件清单、测试结果原文、每项验收标准的自证证据、
遇到的意外与处理方式。诚实报告——部分完成就说部分完成。

## 主控补充（派单后 2 分钟，验收时按此执行）
experiment.execute 的状态前置校验应接受 **PROTOCOL_FROZEN 或 RUNNING**（RUNNING=agent 已调
experiment.run 的常态；两者都合法）。当前 live 实验 exp_01KZFGV56Y79XT0H9NBPJ7R6XX 就是
RUNNING+cells 正确，修复上线后归因师应对它直接 execute。若 Claude 已按 FROZEN-only 实现，
验收时放宽为一行改动。
