# D-002 执行器路由决策

日期：2026-08-07（Phase 1 Wave 1 施工中）
状态：已冻结（用户指令）

## 决策

| 执行器 | 角色 |
|---|---|
| Claude Code（`claude -p` headless，走 claude-code-router → openagents 网关） | **默认施工执行器**：全部实现任务（T1–T4 及后续） |
| Grok（mcp__grok-agent__grok_run） | **搜索/X 数据专用**，不再承担施工 |
| 主控（Kimi） | 规划、任务书、验收（复跑测试+读 diff+行为抽查）、视觉/前端 |

## 背景与理由

- Grok token 配额告罄（用户 2026-08-07 指令："grok 快没 token 了，就当搜索的工具用"）。
- Claude 成本极低（实测 20M token ≈ ¥1.1），用户指令"好用多用，猛猛用"。
- claude-code-router 上游为 openagents 网关（非 StepFun），并行多个 claude -p 不占 CaseLoop 自身的 StepFun RPM=10 预算（S0-002 约束不受影响）。

## 影响

- T2 control-plane：Grok 中断（WIP 存档 commit c8f4332），转 Claude 续作（任务书 /tmp/caseloop-task-t2-control-plane.md）。
- T3 eval-harness 原排 Grok，改派 Claude。
- Wave 2 起允许两个 claude -p 实例并行（各自 scope 不重叠；共改文件以 git rebase 协调）。
- 修订纪律不变：Claude 打回用 `claude -p --continue` 同 session 续；Grok 若恢复使用需主控重估配额。

## 不变项

- 验收四道链不变（机器可验终判 → 主控复跑+读 diff → 行为抽查 → 证据落 evidence/）。
- RPM=10 串行编排（D-001）仍是 CaseLoop 运行时硬约束，与执行器编排无关。
