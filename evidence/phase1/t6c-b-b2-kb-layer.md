# T6c-B 泛化用例：未见故障层 × B2 KB 回归（进行中）

case_01KZGJG21KWQ4P8Z68KFBXACY9｜2026-08-08 11:36 UTC 立案｜设计：evidence/phase1/t6c-generalization-design.md

## 用例设置
- 投诉原文：「商品页写 X200 续航 30 小时，你们客服跟我说只有 8 小时？页面和客服总有一个在撒谎吧」
- 立案回执：{"duplicate":false,"dedup_key":"sha256:83891c5e…","state":"OPEN"}
- 故障注入：B2（KB 回归：X200 续航 30h→8h）→ live 实测：「单次续航 8 小时」
  kb=4aa0bcc1…（漂移）/ prompt=b469e958（基线不变）/ model=f371ce6e（不变）——KB 层单因子 ✓

## 判别力观察点
- 采集员取证 digest 分组应落在 **kb_manifest_digest**（T6b/T6c-A 都是 prompt_digest——层间判别）
- 归因师假设层应为 **kb**（若仍 prompt → 层间泛化失败）
- 修复师 WorkOrder 应指向 KB 条目回滚（不是 prompt 模板）

（观察持续追加）

### 11:42 取证+分诊：层间判别初步成立
- 采集员：先排除环境污染（主动核验 versionset/model 回基线——上一案学到的自检），
  40/40 服务正常；**捕获 kb_manifest_digest 异常值 4aa0bcc1…（真实漂移值）**；
  如实标注证据缺口（app.logs 已脱敏无法验证答复内容）+「故障层未知，不预设与上一案同类」。
- 质量官委派归因：**先验 kb 层**（非 prompt）——层间判别正确。
- 注：app.logs 是历史窗，B1 期 prompt 双分组残留仍在（A:20/B:20），采集员未被旧信号带偏，记正向。

### 11:49 归因实验：ATTRIBUTED/kb，payload 亲验全真
- exp_01KZGJQRNMPAPH28J1B5977NVH → VERDICT_COMPUTED → **ATTRIBUTED**
- versions 亲验：kb_baseline=5df39e2d…（真基线）/ kb_abnormal=4aa0bcc1…（真漂移）
- version_refs={kb_baseline, kb_abnormal}——KB 双臂实验真实执行（非 prompt 臂套壳）
- **层间泛化判定：归因层正确=kb ✓**（通过线核心项达成）

### 12:05 纠偏 #1：heartbeat 抑制域工作复发（G3 实证）
- QO 11:49 确认归因 SUCCESS 后 ~15 分钟未委派 repairer。worker 日志实证：11:51 与 12:01
  两轮 Team Leader Heartbeat（console 通道，"do not assign tasks"）均触发 reply 循环，
  QO 把"不做域工作"从心跳回合泛化到了整个周期——G3 模式复发。
- 已发纠偏消息（域消息通道）：明确 heartbeat 约束范围 + 指令立即委派 KB 回滚候选。
- 评价注记：此为 agent 行为纠偏 #1（T6c-B），计入泛化评价（通过线 ≤2）。

## 收官段（12:05-12:20 UTC）

### 门禁→审批→发布
- 纠偏后 QO 冲刺：委派 repair（KB 回滚候选）→ 委派门禁 → gatekeeper 一次 freeze 通过
- wo_01KZGMEME8HE3SK63WHP9TV8S2 亲验：**diff.type=kb_rollback**，目标 X200 battery_life 8h→30h，
  channel=kb，全真 digest（kb 目标=5df39e2d 基线，prompt/model 不变）——修复层正确 ✓
- 审批 appr_01KZGMG2QTF9SZD399B37BP9S5 → release rel_01KZGMM32ZPKSSK149RHYYDJ9J

### 修复实测 before/after
- before：「X200 单次续航为 **8 小时**」（kb=4aa0bcc1 漂移）
- apply（cleared=['B2']）→ after：kb=5df39e2d 恢复，应答引用「总续航 30 小时」基线条目
- 注：两次空应答（transient，无关问题正常——记为 LLM 偶发空响应观察项，非修复问题）

### 真实账本（设计时刻兑现）
- R1/case.triage **3/3 → Wilson LB=0.4385 < 0.9 → denied**——与 trust_demo.py 设计预期逐位一致，
  三起真实闭环换来"记账但拒绝晋升"的统计纪律实证
- R2/release.canary_step → not_evaluable（永远逐次审批）

### 泛化判定（T6c-B）
- 归因层正确=kb ✓（采集 kb 异常信号→先验 kb→KB 双臂实验 ATTRIBUTED→KB 回滚 WorkOrder）
- 闭环走通 ✓；剧本复读：未发现；纠偏 1 次（heartbeat 抑制复发，G3）≤2 ✓
- **通过线达成**
