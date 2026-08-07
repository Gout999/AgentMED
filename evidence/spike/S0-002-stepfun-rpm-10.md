# Spike 发现 S0-002：StepFun 阶梯限速 RPM=10，多 Agent 并发即触顶

> 日期：2026-08-07 ｜ 环境：AgentTeams v1.2.1 + StepFun `step-3.7-flash`
> 严重度：**高**——直接影响 6 Agent 团队与对照实验的可行性，需用户决策。

## 现象

spike 交接演练中：leader（spike-leader）正常完成 DAG 拆解与 `delegate_task` 派单，
spike-worker-a 接单后首轮 LLM 调用即失败，房间报 `Error: Model quota exceeded: step-3.7-flash`。
worker 容器内 `/tmp/copaw_query_error_*.json` 完整 traceback 末端：

```
openai.RateLimitError: 429 - request limited RPM reached, current: 11, limit: 10.
Please top up at https://platform.stepfun.com/top-up.
```

## 根因

StepFun 阶梯限速：当前账号 `step-3.7-flash` **RPM=10**（每分钟 10 次请求，不是余额问题）。
3 个 copaw worker 并发（每个 worker 单步任务内含多轮 LLM 调用）轻松越过 10 RPM。
注意：control-plane 的 Experiment Runner（5-cell × n 次重复 × 探针集）与 AgentTeams
worker 共用同一个账号/key，**两个面共享这 10 RPM**。

## 影响面

1. 6 Agent 团队全员并发时必然限流；worker 报错的任务会中断（copaw 未自动重试——待验证）
2. 对照实验执行器必须自备 RPM 预算与 pacing（我们是确定性的，可控）
3. 演示现场风险：闭环演示中任何一个 worker 被 429 卡住都会打断叙事

## 对策选项（待用户/主控决策）

- A. StepFun 充值升档提 RPM（用户侧操作，最彻底）
- B. 演示编排降并发：同一时刻活跃 worker ≤2，控制面派单串行化（工程可做，叙事无损）
- C. 双 key/双账号分流：AgentTeams worker 一个 key，Experiment Runner 一个 key
- D. copaw 侧 429 退避重试配置（待查证是否支持）

## 证据

- worker 错误详情：本文件同目录 `s0-002-ratelimit-429.json`（traceback 摘录）
- 房间时间线：spike-leader 成功派单（含 DAG 两任务），spike-worker-a 接单后 429
