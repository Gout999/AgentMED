# CaseLoop

给 AI 应用配一个质量部门。

你的 AI 客服上线了。第三周，它开始跟用户说「激活过的耳机不能退货」——但你们的售后政策明明是七天无理由。用户投诉，运营把截图甩进技术群，技术查了半小时：prompt 没人动过，知识库看着也没问题，重启一下好像又行了。下周，同样的事再发生一遍。

这是今天绝大多数 LLM 应用的真实运维状态：出了问题靠猜，修完靠赌，有没有真的修好，靠下一个用户再踩一次才知道。

CaseLoop 把这件事变成自动的。一条用户投诉进来，系统自动立案、取证、跑对照实验找出是哪一层坏了——是 prompt、知识库，还是模型参数——然后起草修复、跑回归评测、提交给人审批、灰度上线、回到投诉原处回复用户，最后把这次事故存进案例库。AI 负责动脑子，但动不了规矩：立案、发布、审批、记账这些权力，全在确定性的控制面手里，每个关键动作都有 hash 可审计。

## 历史 Phase 1 演示记录

`evidence/phase1/` 保留了 2026-08-08 三个早期演示场景的日志、digest 与截图。它们是历史材料，**不是**当前 P0-4 所要求的、可由 manifest 独立重验的 live-provider B1 纵向闭环证明；当前结论只以 `evidence/p0/p0-4-b1/`、`evidence/p0/p0-4-b1-live/` 和 `docs/context/PROJECT_STATE.md` 为准。

| 场景 | 历史注入内容 | 早期材料记录 |
|------|------------|-----------|
| 售后政策矛盾 | 改掉 prompt，让客服坚称「激活不可退」 | 全链路首次走通，修复后实测答复恢复正确 |
| 同上，但换措辞 | 用剧本之外的话术投诉同一件事 | 零人工纠偏走完全程 |
| 知识库过时 | 把 KB 里某耳机续航从 30 小时改成 8 小时 | 见下 |

这些场景仍适合说明产品目标，但在当前门禁下必须分别重跑 contract/replay 与 live-provider 证据，不能把旧截图或房间日志直接升级成「真实闭环已完成」。

## 为什么敢让 AI 干这个

答案是：不完全敢。而且这个「不敢」被写进了代码里。

- **信任账本**。每个真实 release outcome 通过 outbox 记为一次行动样本。即使 3/3 成功，Wilson 双侧 95% 下界也约为 0.4385，低于 0.9，确定性策略仍会**拒绝晋升**。
- **高风险操作永远逐次人审**。审批批的是 WorkOrder 的 hash——换一个字节都过不了闸。
- **裁判和运动员不是同一个模型**。门禁的评测模型与被修的应用模型相互独立。

## 架构一句话

**确定性控制面 + 概率性执行面。** LLM 出主意，系统管规矩；LLM 永远不是状态和权限的权威源，一切分歧以控制面数据库和实验数据裁决。

```
用户投诉 → 立案(非LLM，去重幂等) → 取证 → 对照实验归因(Δ效应量+置信区间)
        → 统计显著才放行 → 修复自由起草 → 双轨评测门禁 → 人工审批(批hash)
        → 灰度发布(CAS写面) → 回复投诉原处 → 事故入案例库 → 账本记账
```

技术栈：FastAPI + pgvector（演示应用与案例库）、自研控制面（PG 事件溯源）、MCP（agent 工具面）、AgentTeams（多 agent 编排）、StepFun `step-3.7-flash`（LLM）、React 控制台。

## 跑起来看看

```bash
# 业务栈：演示应用 + 控制面 + 控制台 + Postgres
cd deploy && docker compose up -d

# 控制台：http://127.0.0.1:8088 （案例、实验、审批、账本可视化）
# 控制面 API：:18090 ｜ 演示应用：:8080
```

多 agent 编排层（AgentTeams）安装稍重，步骤与密钥纪律见 `deploy/README.md`。
想接着开发，先读 `STATUS.md`（当前状态、必知的坑）和 `wiki/INDEX.md`（施工知识库）。

## 仓库里有什么

| 目录 | 内容 |
|------|------|
| `demo-app/` | 演示应用「小智客服」：FastAPI RAG，prompt git 版本化，B1–B4 故障注入端点 |
| `control-plane/` | 确定性控制面：立案 / 实验 / 发布控制器，PG 事件溯源，唯一事实源 |
| `eval-harness/` | 回归评测集、双轨跑分、对照实验执行器 |
| `mcp-servers/` | agent 工具面：5 个 MCP Server + 信任账本 |
| `agents/` | Phase 1 固定六角色 warm pool 与角色 SOUL；未声称动态扩缩容 |
| `contracts/` | Quality API 契约、事件/状态定义、conformance 套件 |
| `console/` | 治理控制台前端 |
| `evidence/` | 历史 Phase 1 材料与当前 P0 replay/live 分离证据；是否完成以各 manifest 验证结果为准 |
| `docs/` | 终态蓝图 `plan-v3.md`、PRD、技术 spec |

## 诚实的边界

这是黑客松作品，不是生产系统。可重复的 `make demo-b1-replay` 使用明确标注的 replay/feishu-mock adapter；它不会被算作 live-provider 成功。真实 StepFun、飞书、人工审批及 AgentTeams 链路由独立的 `make demo-b1-live` 执行，并锁定官方 provider origin；缺少凭证、服务或可验证回执时会 fail closed 并报告 BLOCKED。当前可核实的测试数量与 live 阻塞以 `docs/context/PROJECT_STATE.md` 及每次 Evidence Bundle 为准，不再用历史总数宣称「全绿」。

我们知道从「演示」到「敢上生产」之间还差什么——缺口清单一条条列在 `wiki/build-guide.md`（G1–G10），欢迎来看，也欢迎来挑。

## License

Apache License 2.0
