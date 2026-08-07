# 术语表

| 术语 | 含义 |
|------|------|
| 控制面 | 非 LLM 的确定性系统（Case/Release/Caseload Controller、PG），状态与权限唯一权威 |
| 执行面 | Agent 团队（LLM），只产出建议与产物，不持有权威状态 |
| Quality API | 被治理 LLM 应用必须实现的契约：VersionSet 生命周期 + /logs + /feedback |
| VersionSet | 不可变版本集合 {prompt, KB manifest, model+params}，带完整 digest |
| Case Controller | 控制面核心：立案（inbox 去重）、派单（lease+fencing token）、幂等、outbox |
| Release Controller | 唯一可调 Quality API 写面的组件；灰度/验证/全量/回滚，全程 CAS |
| Caseload Controller | Phase 2：弹性 Worker 扩缩（Agent 申请、控制面决策执行，DRAINING 语义） |
| 5-cell 实验 | 归因最小矩阵：C=(P1,K1,M1)、RP=(P0,K1,M1)、RK=(P1,K0,M1)、RM=(P1,K1,M0)、G=(P0,K0,M0) |
| 三态裁决 | ATTRIBUTED（可归因）/ INCONCLUSIVE（补实验或升级人工）/ CONFOUNDED（强制 2³ 全因子） |
| WorkOrder | 修复产物，不可变，hash 绑定 目标/输入版本/diff/门禁报告/expiry/nonce |
| ApprovalGrant | 人工审批凭证，绑定 workorder_hash+nonce+expiry，一次性防重放 |
| 双轨门禁 | 规则轨（确定性断言）+ 裁判轨（LLM 裁判，裁判模型≠运动员模型） |
| 信任账本 | risk_class × autonomy_state 二维记账；Wilson 双侧 95% 下界 >0.9 才可晋升 |
| risk_class | R0_READ / R1_REVERSIBLE_WRITE / R2_HIGH_IMPACT（R2 永远逐次审批） |
| autonomy_state | MANUAL / ELIGIBLE / AWAITING_CONFIRMATION / AUTO_ENABLED / SUSPENDED / BLOCKED_UNKNOWN |
| evidence epoch | 信任证据的原始整数计数周期；一次动作=一个样本 |
| 变异巡检 | 变异算子库→探测用例→周期攻击→质量周报（自评分：检出率/归因准确率/门禁拦截率/一次通过率） |
| warm pool | Phase 1 的固定规模弹性 Worker 池（不宣称动态扩缩） |
| feishu mock | 飞书未就绪前的明示降级演示路径，接口与真凭证一致 |
| SOUL | AgentTeams 中 Agent 的人设/规程定义文件（SOUL.md） |
| Team CR / Worker CR / Human CR | AgentTeams 声明式资源（agt apply -f 应用，不做拓扑排序） |
| kine | AgentTeams controller 的 SQLite KV 存储（etcd 语义），排查删除问题可直查 |
