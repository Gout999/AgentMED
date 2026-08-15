# AgentMED Coding Team（设计清单）

Status: **APPROVED DESIGN / NOT CREATED / NOT RUN**

本目录冻结 v4 `agentmed-coding-team` 的角色和目标 manifest，供 Stage 0 contract review 使用。它不是 AgentTeams v1.2.1 可直接部署的 CR；`team.yaml` 使用 AgentMED 的 design-only `TeamManifestDraft`，故意不能被 `agt apply` 接受。其三个独立状态字段为 `design_status=APPROVED`、`lifecycle_status=NOT_CREATED`、`runtime_status=NOT_RUN`。

Stage 2A Durable Work 与 Stage 2B Claude Code Runtime Adapter 是 Stage 2C 的 Entry 前置。**Stage 2C 自身的第一项交付**是从这个已批准 design manifest 生成钉住 AgentTeams 版本的 deployment manifest，保存 source/design digest 与语义 diff，完成审查后再 apply、回查资源并运行因果验收。只有“生成 → 审查 → 部署 → 验收”全部通过，Stage 2C 才能关闭；不得把 deployment manifest 的生成放到 2C 通过之后形成循环。

三个角色：

- `coding-planner`：复现、冻结基线、提出 ResolutionContract；
- `coding-generator`：提交委托，由 Claude Code child Attempt 产出 patch；
- `coding-reviewer`：独立操作 sandbox 并提交 `Finding`；不决定 Gate。

确定性 Code Gate、Repo Controller 与 Release Controller 不属于 Agent Team。Code Gate 只消费已冻结 EvaluationPlan、sandbox/test receipts、Reviewer Finding 和其他必选轨，由确定性 Controller 计算终态。GLM-5.2 是目标主模型而不是已验证现状；实际 provider/model 只以 Attempt receipt 为准。
