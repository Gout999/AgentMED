# 运行证据（第二次全自动闭环 · 出口 2）

> 与 [`agentteams-package.md`](agentteams-package.md) 配套；每条证据都给出可独立核验的
> 定位（控制面 PG / 仓库证据目录 / Langfuse / Matrix），结论均直接来自这些记录。
> 时间均为 UTC。
>
> **命名说明**：本次运行发生在产品改名（CaseLoop → AgentMED，2026-08-15）**之前**，
> 因此下文的团队名/审批人/Langfuse 标签沿用当时的 caseloop-* 名称——与原始证据
> （DB、Matrix 事件、Langfuse 观测）逐字一致，不是笔误。

## 证据总览

| 环节 | 记录 | 值 |
|---|---|---|
| 立案 | 案例 | `case_01M00JDK8JHWQ5MMDN1JTE5MZR`（来源 langfuse 负分观测，app_ref=agent-station） |
| 段2 取证 | Langfuse | 项目 goai-agent-station；score `9405a9cd`（answer-quality=0）；trace `54e673cfcaaec6c00f80c6ccd02827e4`；observation `30c74f6a21f93620` |
| 段3 归因 | 实验 | `exp_01M0159WMBWA8S0FPQF74SYDXS` → **VERDICT_COMPUTED / ATTRIBUTED / prompt**，Δ=(1.0, 0.0, 0.0)，135/135 trial |
| 段4 修复 | 候选+工单 | `vs_78f1312790086845`（draft）+ 工单 `wo_01M01A4AZ1C88D5EBVN6Z7GDC4` FROZEN，hash `79d8c218…` |
| 段5 验证 | 门禁+沙箱 | gate `eval_01M01A4BEV9QN9EBTJAAEM3320` passed（三轨+裁判 16/16）；sandbox `eval_01M01AFWDE2HMTHRZSQ5TVB1MX` PASS |
| 段6 审批 | Matrix+grant | 决策事件 `$GelnQ7gB-mFw_c-Bw6Us8VN7bXKAoGrYu_wcuL3YizY`（@caseloop-approver，演示代批）→ changeset APPROVED → case RELEASING |
| 出口 2 | 发布 | **releases 表 0 条** → VerifiedCandidate / NOT DEPLOYED |

## 1. 段3 归因实验（控制面 PG + `evidence/experiments/exp_01M0159WMBWA8S0FPQF74SYDXS/`）

冻结协议（不可变，实验全程对账）：

| 字段 | 值 |
|---|---|
| probe_set_digest | `sha256:f51fbbee2810467c96658f93e4fc2b64b5b843b80e55bf5029f30fa26bb9dbf0` |
| 探针分组 | discovery cs-001..003；hidden cs-004..005；unaffected cs-013..016；repetitions=3 |
| 随机种子 | `seed://exp_01M0159WMBWA8S0FPQF74SYDXS/0` |
| 组件身份（冻结 digest） | P0=K0=`sha256:258a…503c7`（基线）；P1=M0=M1=`sha256:4dd5…45a67`（模型绑定/挑战者） |
| 细胞版本集 | C `26f97c…` · RP `2ca809…` · RK `94ca13…` · RM `4227e0…` · G `9a8d33…` |

实测裁决（与控制面 `_view=full` 一致）：

| 臂 | 恢复率（affected 探针） | 期望（B1 fixture） |
|---|---|---|
| C | **0.0** | false |
| RP | **1.0** | true |
| RK | **0.0** | false |
| RM | **0.0** | false |
| G | **1.0** | true |

每个 trial 都经过 live 归因对账（`attribution.py`）：provider log（AgentMED `/v2/logs`）
的 request_id/trace_id/versionset_id/组件 digest/answer_digest 必须与冻结协议逐项一致；
135 份探针产物在证据目录 probe-outputs/ 下（每份含 request_id/trace_id/answer/组件 digest）。
运动员轨真实模型路径：AgentMED(8088) → StepFun `step-3.7-flash`，Langfuse 逐条留痕
（role=caseloop-eval，tag=versionset_id）。

## 2. 段4 修复（AgentMED 写面 + 控制面）

- 修复候选：`vs_78f1312790086845`（status=draft，digest `sha256:9ae4d1cf…`）——单变量回滚
  prompt 层：v1.4.3『退货需经人工审核…』→ v1.4.2『7 天无理由退货…』，kb_manifest 与 model 绑定不变。
- 工单：`wo_01M01A4AZ1C88D5EBVN6Z7GDC4`，**FROZEN**，hash `79d8c218356601c2fe56d665ced1529f33acefc0edfe8bfb3baf25919478decb`
  （JCS+RFC8785 转义覆盖内联中文 unified_diff）。
- 归因报告摘要：`sha256:c8d9a6f644ef9463e7b89ac6d9e0e5c28a15b7b5c38bcf62953a20f311a9f6d4`。

## 3. 段5 门禁 + 沙箱

门禁（`evidence/gate/eval_01M01A4BEV9QN9EBTJAAEM3320/`）：

| 轨 | 状态 | 内容 |
|---|---|---|
| deterministic_tests | passed | contract-assets（`contracts/conformance/test_schemas.py`）+ frozen-probe-replay（probe_judge/digests/gate 单测） |
| live_provider_e2e | passed | 16 条冻结探针经 AgentMED 评估面打修复候选，逐条与 provider log 对账 |
| judge_track | passed | 裁判 `step-3.5-flash`（≠运动员）16/16；裁判轨带政策原文+KB 参考材料，原始打分在 candidate-answers.json |
| rule_track | passed | 候选结构合规（digest 前缀 / provider_origin=官方 StepFun 端点 / 探针集 digest 一致） |

沙箱（`var/sandbox/wo_01M01A4AZ1C88D5EBVN6Z7GDC4-sandbox-evidence.json`，eval `eval_01M01AFWDE2HMTHRZSQ5TVB1MX`）：

- probe digest `5142593db2…a22f92`；prompt_before（v1.4.3 故障）`be202155…`；prompt_after（v1.4.2 修复）`ac328249…`；
- 隔离容器（copaw-worker 镜像，只读挂载）真实回放：修前 fail（人工审核/不支持退货）、修后 pass（7 天无理由）→ **verdict PASS**。

## 4. 段6 审批（Matrix + 控制面）

- 审批请求：`appr_01M01AFWSW7JYSYTE29REJ890S`（channel=matrix，nonce `01M01AC9QMAERZGYYH57CPD2NR`）。
- Matrix 决策：团队房间（`!NzWy15gwm3QU6cTfuP:matrix-local.agentteams.io:18080`）事件
  `$GelnQ7gB-mFw_c-Bw6Us8VN7bXKAoGrYu_wcuL3YizY`（2026-08-14T23:44:37Z），以 @caseloop-approver 身份发送，
  reason 明标「演示代批」（owner 授权）。
- reader 核验：nonce 匹配 → 登记 ApprovalGrant → changeset `cs_wo_01M01A4AZ1C88D5EBVN6Z7GDC4`
  **APPROVED**、案例 **RELEASING**（2026-08-14T23:50:57Z）。

## 5. 出口 2 核验

```bash
curl -s http://127.0.0.1:18090/v1/experiments/exp_01M0159WMBWA8S0FPQF74SYDXS | jq '{state, payload: {verdict: .payload.verdict, attributed_layer: .payload.attributed_layer}}'
curl -s "http://127.0.0.1:18090/v1/experiments/exp_01M0159WMBWA8S0FPQF74SYDXS?_view=full" | jq '.cells[] | {cell, recovery_rate}'
curl -s http://127.0.0.1:18090/v1/cases/case_01M00JDK8JHWQ5MMDN1JTE5MZR | jq '{state, fault_layer: .payload.fault_layer}'
curl -s "http://127.0.0.1:18090/v1/releases?case_id=case_01M00JDK8JHWQ5MMDN1JTE5MZR" | jq '.items | length'   # 0 → NOT DEPLOYED
curl -s http://127.0.0.1:8088/v2/versionsets/vs_78f1312790086845 -H 'Authorization: Bearer <AGENTMED_READ_TOKEN>' | jq '{versionset_id, status, digest}'
```

## 6. 历史首轮闭环（补充背景）

首轮闭环（2026-08-14 白天）在同一条链路上演示了 gatekeeper 独立判 FAIL（证据缺口时 fail-closed，
NOT DEPLOYED）；对应历史材料在 `evidence/phase1/` 与 `evidence/p0/`（见 README 历史记录章节）。
本文件第 1-5 节只陈述第二次全自动闭环的可复核记录。