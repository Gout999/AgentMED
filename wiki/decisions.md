# T0–T10 架构决策卡（施工影响速查）

> 源头：`docs/plan-v3.md` §1。施工时只需要知道"这条决策让我什么能做、什么不能做"。

| # | 决策 | 施工影响 |
|---|------|----------|
| T0 | 治理层产品，Quality API 契约纳管任意 LLM 应用 | demo-app 与治理层严格分离；治理层不许 import demo-app 内部实现，只走契约 |
| T1 | 小智客服 LLM 全部真实调用 StepFun，无 mock 层 | demo-app 不得出现假 LLM；确定性靠 temperature=0 + 冻结探针集 |
| T2 | 4 常设 + 2 类弹性；AgentTeams v1.2.1 无原生 autoscaling | 扩缩由 Caseload Controller 做（Phase 2）；Phase 1 固定 warm pool，对外禁说"动态扩缩" |
| T3 | 事件驱动+轮询兜底，统一经 inbox 去重立案 | Case Controller 入口必须先过 inbox 去重，不允许绕过立案直接派单 |
| T4 | 严格实验协议：5-cell 最小矩阵，Δ+95%CI，三态裁决 | 禁用"置信≥0.8"这类未定义指标；归因输出必须带效应量与 CI |
| T5 | 三通道修复+自由起草；不可变 WorkOrder hash 绑定 | 审批对象永远是 WorkOrder hash，不是 diff 文本；WorkOrder 落库后任何字段不可改 |
| T6 | 双轨门禁；contract/replay 与 live-provider E2E 分开报告 | 门禁报告必须拆两类结果，不得合并成一个 pass/fail |
| T7 | 飞书对外、Matrix 对内，对外动作双向留痕 | 对外回复（含 mock）必须落 Notification 事件 + DB |
| T8 | 信任账本 risk_class × autonomy_state 二维；Wilson 双侧；一次动作=一样本 | 多条探针在一次动作中只算 1 个样本；R2 永远逐次审批；MVP 只演示拒绝晋升 |
| T9 | 案例库 pgvector；审计权威源=数据库，audit.jsonl 仅导出物；OTel | 审计写库失败即拒业务（不放行）；不许把 audit.jsonl 当权威源读 |
| T10 | I1/I2/I3 创新轴：MVP 兑现记账拒升、单次巡检、Skill 候选+回放+人工批准 | Skill 不得自动上架；供应链未完备前人工批准是硬门禁 |

## 叙事纪律（比赛材料约束，写对外文档/PPT 时遵守）
- 前半场统一口径"以 AgentTeams 为协同设计基点深度映射"，**"可替换适配层"字样不出现在前半场**
- 扩缩容统一说"Agent 申请、控制面决策执行"，禁用"质量官现场创建"
- 飞书 mock 是明示的降级演示路径，不是造假
