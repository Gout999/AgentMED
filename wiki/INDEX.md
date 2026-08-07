# CaseLoop Wiki —— 施工知识库（LLM 向）

> 用途：任何施工 agent（Kimi / Claude Code / Grok）领任务前先读本索引指定的页面，
> 不要全盘通读仓库。每页都是「够干活的最小上下文」。
> 维护纪律：谁施工发现新事实（平台行为、坑、契约变更），谁回写对应页。

## 页面地图

| 页面 | 内容 | 什么时候必读 |
|------|------|--------------|
| `project-brief.md` | 一页看懂 CaseLoop：定位、闭环、阶段 | 任何任务 |
| `decisions.md` | T0–T10 架构决策卡 + 每条的施工影响 | 写任何架构相关代码/文档前 |
| `environment.md` | 本机环境事实：端口、凭证纪律、StepFun、Docker | 一切本地运行/调试任务 |
| `platform-agentteams.md` | AgentTeams 实测百科：安装坑、agt/REST/Matrix 技巧、S0-001 缺陷 | 碰 agents/、deploy/、Matrix、MCP 挂载时 |
| `contracts-map.md` | contracts/ 契约地图：读哪个文件、字段速查 | 写 demo-app / control-plane / eval-harness / mcp-servers 前 |
| `build-guide.md` | 施工规则：并行 scope 纪律、验证标准、commit 规范、委派规则 | 任何代码任务 |
| `glossary.md` | 术语表（控制面/执行面、WorkOrder、信任账本…） | 读文档看不懂词时 |

## 源头文档（权威，wiki 只是蒸馏）

- 终态蓝图（唯一事实源）：`docs/plan-v3.md`
- PRD：`docs/prd.md` ｜ 技术 spec：`docs/spec.md`（起草中）
- 契约：`contracts/`（冻结中）
- Spike 证据：`evidence/spike/`
