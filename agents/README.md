# agents/ —— CaseLoop Agent 团队定义

> T5 成稿，钉 AgentTeams **v1.2.1**。团队名：`caseloop-team`。
> 设计蓝本（冻结）：`docs/plans/wave3-soul-design.md`。组织与仲裁：`docs/spec.md` §8。

## 目录

| 文件 | 内容 |
|------|------|
| `team.yaml` | 6 Worker CR（4 常设 + 2 弹性 warm pool）+ Team CR + Human CR（审批人），`soul` 内联 |
| `souls/quality-officer.md` | 质量官（Team Leader，常设）：分诊/领单/升级/扩缩容申请 |
| `souls/collector.md` | 采集员（常设）：投诉取证 → badcase + 候选探针 |
| `souls/gatekeeper.md` | 守门员（常设）：门禁主持 + 放行一票否决 |
| `souls/case-officer.md` | 案例官（常设）：沉淀/案例库唯一写权/周报 |
| `souls/attributionist.md` | 归因师（弹性）：实验计划建议 + 报告解读 |
| `souls/repairer.md` | 修复师（弹性）：自由起草修复 → 不可变 WorkOrder |
| `skills/caseloop-b1-loop/SKILL.md` | 六角色共用的 B1 taskflow/MCP/证据纪律；live evidence 绑定此文件 digest |
| `RUNBOOK.md` | 从零到团队可领单的安装 runbook（16 步，Step 0–15） |
| `scripts/verify-soul-sync.py` | 校验 `team.yaml` 内联 soul 与 `souls/*.md` 逐字一致（防漂移） |
| `OPEN-ISSUES.md` | 成稿对冻结设计的异议与解释（待主控裁决） |
| `spike/` | Phase 0A 平台验证用的最小团队（非最终定义，勿部署为正式团队） |

## 关键事实

- **编制**：6 Worker 全部在 Team 内，`quality-officer` 为唯一 `team_leader`；审批人 Human 为 `caseloop-approver`（Team admin）。
- **MCP 挂载**：`spec.mcpServers` 指向 Higress 网关 `/mcp-servers/<name>/mcp`；Authorization 由 controller 从 `/data/worker-creds/<name>.env` 自动注入。
- **交接**：产物一律落 `shared/tasks/{task-id}/`，经 taskflow ack/submit 自动同步（S0-003）。
- **B1 Skill**：六个 Worker 的 `spec.skills` 都固定为 `caseloop-b1-loop`；未取得 taskflow、Matrix 及该 Skill digest 回执时，live B1 不得通过。
- **串行纪律**：同一时刻活跃 worker ≤2、StepFun 8 RPM 全局预算（D-001）。
- **工具名对拍**：SOUL §2 的工具名全部来自 `mcp-servers/README.md`（真实实现），签名见 `docs/spec.md` §9。

## 修改纪律

- 改 SOUL 正文 → 同步更新 `team.yaml` 对应 Worker 的 `soul:` 内联块（二者必须逐字一致，`scripts/verify-soul-sync.py` 或 RUNBOOK Step 5 可校验）。
- 改工具面 → 同步 `souls/*.md` §2 与 `RUNBOOK.md` Step 10 的 consumers 矩阵。
- 团队删除/缩容 → 遵守 `RUNBOOK.md` Step 15 的 S0-001 对账姿势。
