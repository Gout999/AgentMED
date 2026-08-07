# CaseLoop —— AI 应用质量自治底座

> 核心架构原则：**确定性控制面 + 概率性执行面**——AI 负责动脑子，系统负责管规矩。
> LLM 永远不是状态与权限的权威源。

任何 LLM 应用实现 Quality API 契约即可被纳管；多 Agent 团队（AgentTeams 编排）自动完成
badcase 全生命周期闭环：**投诉进来 → 对照实验归因 → 自由起草修复 → 评测门禁 → 灰度发布 →
回复投诉原处 → 沉淀为评测与知识资产**。

## 仓库结构

| 目录 | 内容 |
|------|------|
| `demo-app/` | 演示应用「小智客服」（FastAPI RAG，prompt git 版本化，pgvector KB，LLM 真实调用 StepFun，Quality API v2 实现，B1–B4 注入端点） |
| `control-plane/` | Case Controller / Release Controller / Caseload Controller / Experiment Runner（非 LLM，唯一事实源） |
| `eval-harness/` | 回归评测集、双轨跑分、对照实验执行器、变异巡检器、质量周报 |
| `mcp-servers/` | 5 个 MCP Server + trust-ledger 模块 + 重写的 common（审批防重放、审计失败即拒） |
| `agents/` | team.yaml（4 常设 + 弹性模板 + Team/Human CR）+ 6 份 SOUL.md + 安装 runbook（AgentTeams v1.2.1） |
| `contracts/` | Quality API OpenAPI、事件/状态定义、WorkOrder/Approval schema、Evidence Bundle schema、B1–B4 ground-truth fixtures、Wilson 测试向量、conformance suite |
| `casebase/` | pgvector schema、种子数据、入库/检索工具 |
| `console/` | 治理控制台前端（案例/实验/审批/信任账本可视化） |
| `deploy/` | docker-compose（app + pgvector + mcp + feishu mock）；AgentTeams 本地安装 |
| `docs/` | PRD、spec、skills、mcp-contracts、agent-identity、competition/（过程材料） |
| `evidence/` | 各阶段验收证据（日志/截图/报告导出物；审计权威源在数据库） |

## 文档入口

- 最终目标完整实现方案（终态蓝图）：`docs/plan-v3.md`
- PRD：`docs/prd.md`
- 技术 spec：`docs/spec.md`

## License

Apache License 2.0
