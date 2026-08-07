# 一页看懂 CaseLoop

## 定位
**AI 应用质量自治底座**（治理 meta 层，不是客服工单系统，不是又一个 Agent 框架）。
任何 LLM 应用实现 **Quality API 契约** 即可被纳管；多 Agent 团队（AgentTeams 平台编排）
自动完成 badcase 全生命周期闭环。

## 核心闭环（运行时那条龙）
```
投诉/反馈进来（飞书→先 mock）
 → Case Controller 立案（非LLM：inbox 去重、租约、幂等、outbox；PG 是唯一事实源）
 → 质量官领单 → 采集员取证（GET /logs、GET /feedback）
 → 归因师跑 5-cell 对照实验（Δ效应量+95%CI → ATTRIBUTED/INCONCLUSIVE/CONFOUNDED）
 → ATTRIBUTED 才放行 → 修复师自由起草（prompt git化 / KB修订 / 模型参数）
 → 产出不可变 WorkOrder（hash 绑定目标/输入版本/diff/门禁报告/expiry/nonce）
 → 守门员双轨评测门禁（规则轨 + LLM 裁判轨；裁判模型≠运动员模型）
 → 人工审批（批的是 WorkOrder hash，防掉包防重放）
 → Release Controller 灰度→验证→全量/回滚（唯一可调 Quality API 写面，CAS）
 → 回复投诉原群 → 案例官归档 pgvector 案例库
 → 信任账本记账（risk_class × autonomy_state；MVP 只演示"记账但拒绝晋升"）
 → 变异巡检器周期攻击 + 质量周报
```

## 两条铁律
1. **确定性控制面 + 概率性执行面**：AI 负责动脑子，系统负责管规矩。
2. **LLM 永远不是状态与权限的权威源**——一切分歧以 Case Controller 权威状态与实验数据裁决。

## Agent 组织
4 常设：质量官（领单协调）、采集员、守门员、案例官；
2 类弹性：归因师、修复师（Caseload Controller 管扩缩，Phase 2 才真做动态）。
冲突仲裁：守门员一票否决放行 ＞ 一切；归因置信不足不得进修复。

## 演示应用
「小智客服」：3C 数码电商客服，FastAPI RAG，prompt git 版本化，pgvector 知识库
（售后政策/产品参数/物流规则种子数据），LLM 真实调用 StepFun `step-3.7-flash`，
Quality API v2 实现，B1–B4 故障注入端点。
B1=prompt 回归（Phase 1）｜B2=KB 过时｜B3=模型参数漂移｜B4=多因素交互（Phase 2）。

## 阶段
- 0A Spike：平台行为验证（3A+1S+1MCP+交接+sleep/wake+重启）
- 0B 契约冻结：contracts/ 全量 + conformance suite 对空实现跑红
- Phase 1：B1 单场景纵切全闭环（固定 warm pool）
- Phase 2：B2–B4 + 真扩缩 + Skill 演化到"候选+holdout回放+人工批准"
- Phase 3：硬化（不宣称生产完成）

## 信任账本 MVP 口径
只演示「记账但拒绝晋升」：3/3 成功时 Wilson 双侧 95% 下界 ≈0.44 < 0.9 → 拒绝晋升。
拒绝是统计纪律的证据，不是功能缺失。
