# OPEN-ISSUES —— eval-harness 施工期发现与待裁决项

> 范围纪律：只改 `eval-harness/`。以下问题均不动 `contracts/` / `demo-app/`，逐条列明现状与建议裁决。

## 1. 探针判定语义：`must_include` 需容忍自然改写（已用确定性方案解决，待契约口径确认）

- **现象**：冻结探针 `cs-001.must_include=["7 天无理由", "退"]`，但基线 LLM（temperature=0）把政策
  改写为「签收次日起 7 天内都可以申请无理由退货」。纯连续子串匹配会**误杀良基答案**，导致基线
  「应全过」前提不成立、B1 实验 RP/G 臂无法恢复。
- **现状**：`probe_judge.py` 采用「空白归一化 + 有序子序列」放行 `must_include`（各字按序出现即命中）；
  `must_not_include` 保持严格连续匹配防漏检。确定性、可机器复核，且有对抗用例。
- **待裁决**：主控是否认可该语义。若要求严格连续匹配，则需回改 demo-app prompt（让模型逐字引述）
  或改探针 must_include 分词，二选一须主控定。

## 2. demo-app 无法跑任意版本组合的 5-cell（B1 单因素下 K0==K1、M0==M1）

- **现象**：demo-app 的 live 配置只能「注入单故障 / 复位」，无法对任意 (P,K,M) 组合做版本固定对话。
  B1 只改 prompt 层，故 `K0==K1`、`M0==M1`，RP 与 G、RK 与 RM 在实际内容上重合。
- **现状**：实验执行器忠实记录每 cell 实际 chat 返回的 digest（对账口径），裁决仍正确
  （Δ_kb=Δ_model=0，CI 覆盖 0，由实验数据自然给出）。B1 单因素场景下 5-cell 退化为两态，
  但不影响归因正确性。
- **待裁决**：Phase 2 做 B2–B4 时，demo-app 需支持「版本固定对话」（如 `/chat?versionset_id=`），
  才能真正区分 K1≠K0、M1≠M0。建议列为 Phase 2 demo-app 增强项。

## 3. n=5（D-001）与 B1 fixture 冻结值 repetitions=3 的张力

- **现状**：`D-001 #8` 定 n=5；`contracts/fixtures/b1-prompt-regression.yaml` 冻结
  `experiment_protocol.repetitions: 3`。实现按「配置默认 5，B1 集成/报告按 fixture 冻结值 3」执行。
- **待裁决**：B1 证据是跑 3 还是 5 次。当前 B1 集成测试读 fixture 的 3（与样例 evidence-bundle 一致）。
  若验收要求 n=5，把 `EXPERIMENT_REPETITIONS=5` 设进环境即可，无需改码。

## 4. 裁判模型缺失：T6 硬校验使裁判轨在无独立 JUDGE_MODEL 时不可用

- **现象**：StepFun 账号若只有 `step-3.7-flash`（运动员模型），无独立裁判模型时裁判轨标 `error`，
  门禁正确**阻断自动放行**（D-001 #3 口径）。这不是缺陷，但演示「裁判轨真实打分」需要配置第二个模型。
- **待裁决**：是否为本演示账号再配一个 StepFun 模型（或外部厂商）作裁判模型，写入 `JUDGE_MODEL`。

## 5. 运动员模型 digest 算法与裁判 digest 算法不一致（已加模型名硬校验兜底）

- **现状**：demo-app `/chat` 返回的 `model_digest` 用 JCS(RFC 8785)；eval-harness 裁判 digest 用
  自定义 canonical JSON。两者算法不同 → 同模型也会算出不同 digest，T6 的 digest 相等性校验可能失敏。
  已加**模型名相等**硬校验兜底（`settings.judge_model == settings.stepfun_model` 即拒绝）。
- **建议**：后续引入统一 digest 注册表（model 名 → canonical digest），消除双算法分歧。

## 6. 录制样例为真实 LLM 响应（2026-08-07），非 PII 敏感

- 样例 `samples/b1_probe_responses.json` 为客服对话，无用户 PII；用于离线单测回放。
- 若策略要求样例脱敏，标注即可，内容本身不含敏感字段。

## 7. 集成测试时长（8 RPM 硬约束下的现实）

- B1 5-cell（reps=3）≈ 135 次 chat ≈ 17 分钟；门禁 live ≈ 2–3 分钟。这是 8 RPM 限速下的物理下限，
  非实现低效。验收环境需预留 ~20 分钟跑 live 集成。
