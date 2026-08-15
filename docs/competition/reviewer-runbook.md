# 评审冷启动手册（reviewer cold-start runbook）

目标：评审 clone 本仓库后，**不依赖 Docker、不依赖任何 secret、不依赖运行环境**，
即可复现全部测试基线。本手册的命令均在仓库根目录执行。

## 0. 前提

- Python **3.11 或 3.12**：control-plane 钉死 `psycopg-binary==3.2.3`，3.13/3.14 无轮子，
  3.14 下 `pip install -r control-plane/requirements.txt` 会直接失败（实测：不是仓库破损，
  是钉版依赖的 Python 版本上限，见 control-plane/requirements.txt 头部注释）。
- git、网络（pip / npm 拉依赖）；无其他要求。

## 1. 五条测试基线

```bash
git clone https://github.com/Gout999/AgentMED.git && cd AgentMED

# ① 控制面单测 → 790 passed
python3.11 -m venv control-plane/.venv
control-plane/.venv/bin/pip install -r control-plane/requirements.txt
control-plane/.venv/bin/python -m pytest control-plane/tests/unit -q

# ② MCP 工具面单测 → 106 passed, 1 skipped（真机冒烟默认跳过，AGENTMED_LIVE_TESTS=1 才跑）
python3.11 -m venv mcp-servers/.venv
mcp-servers/.venv/bin/pip install -r mcp-servers/requirements.txt
mcp-servers/.venv/bin/python -m pytest mcp-servers/tests/ -q

# ③ eval-harness 回归评测单测 → 81 passed（复用 ② 的 venv）
mcp-servers/.venv/bin/python -m pytest eval-harness/tests/unit -q

# ④ 契约一致性套件（离线口径）→ 525 passed, 15 deselected, 4 xfailed
python3.11 -m venv /tmp/caseloop-contracts-venv
/tmp/caseloop-contracts-venv/bin/pip install -r contracts/conformance/requirements.txt
/tmp/caseloop-contracts-venv/bin/pytest contracts/conformance -q -m "not live"

# ⑤ 控制台前端 vitest → 9 passed
cd console && npm install && npm run test:unit
```

①–④ 也可以一次跑：`make test`（④ 的 venv 自动创建；①/② 的 `.venv` 需先按上面命令建好）。

## 2. 为什么不是「全绿零跳过」——两处是有意为之、且写在文件里

评审常见问题：这套套件有两类「非绿」，都是设计行为而非破损：

- **15 条 live（deselected）**：`contracts/conformance/test_quality_api.py` 是 Quality API
  v2 写面契约的 live 契约测试。仓库纪律（该文件 docstring 明文）：对空实现必须全红、
  不许放水。demo-app 已随清理 A1 移除，当前没有实现该契约的目标，因此全文件标记 `live`，
  默认 `-m "not live"` 排除。想验证出口判据：
  `CASELOOP_QUALITY_API_BASE_URL=http://<target> pytest contracts/conformance/test_quality_api.py -q`
  ——对空实现应全红（ConnectionError），转绿只能靠真实现。
- **4 条 xfailed**：`contracts/conformance/test_v5_lifecycle_decision.py` 中 4 条断言 D-014
  `application_component_activation_lifecycle` 段的测试，与 3b7e511「close 1A/1B/1C contracts」
  重写后的 YAML 不一致（段已移除 / 命令序重写）。owner 待决：恢复 D-014 段落，或按 3b7e511
  重写取代这 4 条。文件内 NOTE 与 xfail reason 同口径；D-014 fixture 与 ADR 未动。

## 3. 跑完测试 ≠ 跑通产品

测试全绿只证明代码基线可构建、契约自洽。真实闭环（立案→取证→归因→修复→沙箱验证→
人工放行→VerifiedCandidate）需要 docker compose 运行时 + 外部 provider，见 README「跑起来看看」
与 docs/competition/run-evidence.md（历史运行证据，含改名前的命名说明）。

## 4. 评审时可能想问的

- 为什么 wire 常量还是 `caseloop.dev` / `caseloop-public-api`：它们是冻结合约的协议身份，
  改名不动协议（提交 531dc9b 与 contracts/README 有说明）。
- 为什么证据文件里是历史 caseloop-* 运行名：那批运行发生在改名之前，证据保持 verbatim +
  命名说明，不重算 digest（run-evidence.md）。

