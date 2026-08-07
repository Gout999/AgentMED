# D-001：开放问题裁决记录（spec 15 项 + contracts 8 项）

> 日期：2026-08-07 ｜ 裁决人：主控（Kimi）｜ 状态：**已冻结，施工按此执行**
> 依据：plan-v3（上位）、PRD/spec 建议值、0A Spike 实测约束（StepFun RPM=10、串行编排已确认）
> 变更程序：任何一项要改，须主控批准并同步全部受影响组件。

## 一、contracts/OPEN-QUESTIONS 8 项

| # | 裁决 | 理由 |
|---|------|------|
| Q1 operation TTL | **24h**，照建议 | 足够覆盖审批往返 |
| Q2 /logs·/feedback 分页过滤 | **cursor 分页 + 时间窗 + versionset_id/rating 过滤**，照建议 | 通用性够，不加更多维度 |
| Q3 灰度验证判据 | **探针集回放通过率 ≥ 灰度前基线（同集同比）；最短观察窗 MVP 2min**（结构字段 min_observation_minutes 保留，生产建议 10min） | 演示可压缩、判据确定性 |
| Q4 去重键归一化 | **先 PII 脱敏、再归一化（小写化+连续空白折叠为单空格+trim）、后 sha256**；顺序定死不可调换 | 与"PII 入口脱敏"铁律一致；脱敏后哈希保证同一投诉脱敏前后同键 |
| Q5 thread_ref 格式 | 不透明字符串，格式 `feishu:<chat_id>:<root_id>`（无 thread 时 root_id 为空段）；mock 通道 `feishu-mock:<room>:<msg_ref>` | 一处定义两处（真/mock）兼容 |
| Q6 Δ 95%CI 方法 | **newcombe_wilson_diff**（Newcombe hybrid score，Wilson 差值区间，无连续性校正），method 字段如实记录 | 与 spec §4 公式一致，小样本稳健 |
| Q7 ApprovalGrant 证明 | **server_recorded + audit URI**（MVP）；HMAC 签名列为 Phase 3 硬化项 | MVP 不引入密钥管理复杂度 |
| Q8 epoch 滚动 | **SUSPENDED 冷却 24h；冷却结束计数清零开新 epoch，且必须人工确认才 reinstate（不自动恢复）** | 冷却+人工双闸，与"信任是挣来的"叙事一致 |

## 二、spec §12 15 项

| # | 裁决 | 备注 |
|---|------|------|
| 1 投诉去重窗 | **24h** | 与 Q4 归一化配套 |
| 2 INCONCLUSIVE 补实验上限 | **2 次，超限升级人工** | |
| 3 live 轨 UNAVAILABLE | **MVP 不可仅凭确定性轨放行，转人工** | 门禁宁严勿宽 |
| 4 灰度阶梯 | **5%→25%→100%，每阶梯 ≥10min；MVP 演示压缩至 2min**（参数可配） | |
| 5 reconcile 退避 | **5s 起步指数退避，5min 上限** | |
| 6 通知重试上限 | **5 次** | |
| 7 SUSPENDED 冷却 | **24h**（与 Q8 一致） | |
| 8 重复调用次数 n | **n=5**（契约不变）；执行器自带 pacing 适应 RPM=10，探针按臂串行 | 串行编排已用户确认 |
| 9 δ_min | **0.2**，随探针规模校准 | |
| 10 ApprovalGrant TTL | **30min** | |
| 11 Worker lease | **60s，心跳续租** | |
| 12 casebase 向量维度 | **1024 预留；嵌入模型选型后定**。Phase 1 案例检索用全文+元数据过滤（pgvector 表结构先建好），向量检索 Phase 2 启用 | StepFun 无 embedding 模型；候选：裁判模型厂商 embedding 或本地 bge-m3 |
| 13 desired 公式参数 | **concurrency=1、drain_horizon=30min**，Phase 2 实测校准 | |
| 14 飞书凭证 | 用户睡醒后提供；**feishu mock 先行**，接口同真 | 用户已确认 |
| 15 信任晋升演示 | **Phase 2**（Phase 1 只演示记账+拒绝晋升，口径不变） | |

## 三、随裁决同步的工程约束（RPM=10 串行纪律）

1. 控制面所有 LLM 调用（实验执行器、裁判轨）自带限速器：默认 8 RPM 上限，留 2 RPM 余量给 AgentTeams worker
2. 演示编排同一时刻活跃 worker ≤2（用户已确认接受串行编排）
3. e2e 测试脚本须容忍限流退避，不得把 429 当功能失败
