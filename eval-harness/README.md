# eval-harness —— CaseLoop「考试局」

AI 应用质量自治底座的评测与归因执行面（T3）。职责：**评测门禁**（双轨）、**对照实验执行器**
（5-cell / 全因子）、**变异巡检**（单次版）、**质量周报**。

> 输出纪律（T4）：一切「最终状态」断言可机器复核，归因一律 **Δ+95%CI+三态裁决**，
> 禁止「置信≥0.8」式未定义指标。所有报告必须通过 `contracts/schemas/` 对应 JSON Schema 校验。

## 目录结构

```
eval-harness/
├── eval_harness/
│   ├── config.py          # 环境变量 + .env 配置（含主控 ACL-team 凭证来源）
│   ├── digests.py         # canonical JSON + SHA-256（probe_set_digest 等）
│   ├── models.py          # Probe / ProbeSet / ExperimentPlan
│   ├── probe_loader.py    # 加载 contracts/fixtures 探针集（冻结 digest）
│   ├── probe_judge.py     # 探针判定（must_include 连续/子序列、must_not_include、JSON、长度）
│   ├── stats.py           # Wilson 双侧区间 + Newcombe hybrid 差值 95%CI
│   ├── rate_limit.py      # 令牌桶限速（8 RPM）+ 429 指数退避
│   ├── llm.py             # StepFun 直连（temperature=0，记录模型 digest）
│   ├── client.py          # Quality API 客户端（/chat + /admin 注入/复位）
│   ├── adjudicate.py      # R1–R5 三态裁决（确定性代码）
│   ├── experiment.py      # 5-cell 对照实验执行器 + 报告构建
│   ├── gate.py            # 双轨门禁（规则轨 + 裁判轨 + 确定性/live 分离）
│   ├── mutation.py        # 变异算子库（≥6）+ 单次巡检
│   └── weekly.py          # 质量周报生成
├── scripts/               # CLI：run_b1_experiment / run_gate / run_mutation / build_weekly
├── tests/
│   ├── unit/              # 纯逻辑单测（Wilson 向量对拍、裁决规则、探针判定、schema 自洽…）
│   └── integration/       # live 集成（对 demo-app 跑 B1 实验 / 门禁；服务不可用自动 skip）
└── samples/               # 录制样例（B1 基线/故障响应，离线回放自测）
```

## 安装

```bash
cd eval-harness
python3.11 -m venv .venv                 # 或 python3.12
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env                     # 按需修改；密钥不入库
```

## 跑测试

```bash
# 单元（无网络，快）
.venv/bin/pytest tests/unit -q

# 全量（含 live 集成；demo-app 可达 + STEPFUN_API_KEY 存在才真跑，否则 skip）
.venv/bin/pytest -q
```

live 集成依赖：demo-app 运行于 `CASELOOP_QUALITY_API_BASE_URL`（默认 `http://127.0.0.1:8080`）、
`STEPFUN_API_KEY`（主控约定读 `~/Documents/kimi/workspace/ACL-team/.env`）。

## 跑 B1 对照实验（交付证据）

```bash
.venv/bin/python scripts/run_b1_experiment.py --reps 3 --seed 20260807
# 产出 evidence/exp_b1run*/evidence-bundle.json + attribution-report.json
# 预期：裁决=ATTRIBUTED，故障层=prompt，Δ_prompt>0 且 CI 下界>δ_min=0.2
```

## 跑真实候选门禁

```bash
.venv/bin/python scripts/run_gate.py \
  --versionset-id vs_candidate \
  --out-dir evidence/gate-vs-candidate
```

命令只调用 Quality API 读接口，并始终执行指定 VersionSet；不会追随 `active` 指针，
也不会由评测进程注入或复位故障。contract/replay、候选响应和 live-provider 结果分别落证据；
任何轨道为 failed/error/skipped 时都会以非零退出码 fail closed。

## 关键设计（与契约对齐）

| 项 | 实现 | 契约依据 |
|----|------|---------|
| 归因裁决 | R1–R5 顺序判定，确定性代码，LLM 不参与裁决 | spec §4.6 |
| Δ 95%CI | Newcombe hybrid（`method=newcombe_wilson_diff`） | D-001 Q6 / spec §4.5 |
| δ_min / n / 补实验上限 | 0.2 / 5（B1 集成按 fixture 冻结值 3）/ 2 次后升级人工 | D-001 #2/#8/#9 |
| 门禁 | 规则轨 + 裁判轨；**裁判模型 digest≠运动员 digest 硬校验**；contract/replay 与 live E2E 分开报告 | spec §3.4 / T6 |
| live UNAVAILABLE | 不得仅凭确定性轨放行，转人工 | D-001 #3 |
| 变异巡检 | 8 个算子（prompt 改写 4 / 知识过时化 2 / 参数漂移 2），单次 | spec §10.5 / T10 I2 |
| 探针判定 | 空白归一化 + 有序子序列放行 must_include；must_not_include 严格连续匹配 | contracts fixtures |
| 限速 | 令牌桶 8 RPM + 429 指数退避 | D-001 §3 |

### 探针判定口径（为什么是子序列）

冻结探针的 `must_include` 是「关键短语」，而真实 LLM（temperature=0）会自然改写，如
`must_include=["7 天无理由"]` 被模型输出为「7 天内都可以申请无理由退货」。纯连续子串匹配会
误杀良基答案，破坏基线「应全过」的前提。因此放行侧采用**空白归一化 + 有序子序列**（各字按序
出现即命中），拒绝侧（`must_not_include`）保持严格连续匹配防漏检。该组合是确定性的，
见 `tests/unit/test_probe_judge.py` 的对抗用例。

## 运行态纪律

live 集成会注入故障/改状态。**每次集成测试结束（无论成败）必须执行**：

```bash
bash demo-app/scripts/reset_state.sh
```

## 开放问题

见 `OPEN-ISSUES.md`。
