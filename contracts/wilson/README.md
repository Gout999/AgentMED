# Wilson 测试向量（信任账本统计口径）

对应 plan-v3 §2.3.4 / T8 / §6。本目录是信任账本统计纪律的唯一事实源：
`wilson-vectors.json` 中的全部向量必须被任何实现复算通过（断言容差 1e-3，
见 `contracts/conformance/test_wilson.py`）。

## 公式（Wilson score interval，双侧 95%，z = 1.96）

记 `s` = successes，`n` = trials，`p̂ = s/n`，`z = 1.96`（z² = 3.8416）：

```
denominator = 1 + z²/n
center      = (p̂ + z²/(2n)) / denominator
margin      = (z / denominator) · √( p̂(1−p̂)/n + z²/(4n²) )
lower       = center − margin        （实现可对结果做 [0,1] 截断）
upper       = center + margin
```

特殊约定：`n = 0`（无证据）时区间定义为全区间 `[0.0, 1.0]`，
`promotion_eligible = false`——没有证据就没有信任。

**双侧口径是硬约束**（plan-v3 T8 明确）：不使用单侧区间。单侧 z=1.645
会把 3/3 的下界抬到约 0.51，属于口径放水，禁止。

## 样本口径：一次动作 = 一个样本

`trials` 统计的是**动作次数**，不是探针条数。一次发布/回滚动作内部跑了
多少条探针，都只算 1 个样本（`sample_rule = one_action_one_sample`，
见 `trust-ledger-entry.schema.json`）。计数为**原始整数**（evidence epoch
内的 `epoch_successes` / `epoch_trials`），不使用任何平滑、衰减或加权。

## 晋升判据

```
promotion_eligible  ⇔  wilson_lower > 0.9
```

且仅白名单内 **R1_REVERSIBLE_WRITE** 动作可晋升 `AUTO_ENABLED`；
**R2_HIGH_IMPACT 永远逐次审批**，即使下界 > 0.9 也不自动放行。

流程：系统攒证据 → eligible 后飞书带证据表提请（AWAITING_CONFIRMATION）
→ 人工确认 → AUTO_ENABLED。验证失败 → SUSPENDED + 冷却，新 epoch 从零重攒。

## MVP 演示口径：记账但拒绝晋升

- `3/3` → `lower ≈ 0.438494 < 0.9` → **denied**（plan-v3 写作「0.44」）
- `10/10` → `lower ≈ 0.722 < 0.9` → denied
- `30/30` → `lower ≈ 0.886 < 0.9` → **denied**（三十连胜仍不放行，统计纪律演示点）
- `40/40` → `lower ≈ 0.912 > 0.9` → 满足统计条件（仍需人工确认）
- `100/100` → `lower ≈ 0.963 > 0.9` → 满足统计条件

§6 验证口径：三轮 B1 后必须断言到**拒绝晋升**事件（下界 0.44 < 0.9）。
