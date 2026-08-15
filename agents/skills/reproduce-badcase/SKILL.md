---
name: reproduce-badcase
description: Turn a complaint Case into a reproducible probe (badcase replay) and run a controlled A/B experiment through the model path. Used by the attributionist.
assign_when: A Case has evidence (trace/score) and attribution needs a controlled reproduction.
---

# reproduce-badcase（agentmed 版）

归因师专用。目标：把坏例变成可重放、可对照的实验，定位「代码/prompt/模型/环境」哪一层。

## 工具（eval-runner 投影）

- `versionset.list` / `versionset.get`：绑定当时的不可变版本集；
- `experiment.plan`：对照计划（原配置 vs 候选配置，同一坏例输入）；
- `experiment.run` / `experiment.execute`：经模型路径真实执行；
- `experiment.report`：三态裁决（Δ + 95%CI），不写未定义指标；
- `probe.freeze`：把复现输入冻结为探针（digest 绑定）。

## 纪律

- 先重放坏例确认「修前失败」可复现，再谈候选；
- 裁决引用具体实验证据（case_id + run id + digest）；
- 不做发布，只产结论；实验产物落 `shared/tasks/{task-id}/experiments/`。
