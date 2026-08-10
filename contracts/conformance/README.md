# CaseLoop conformance suite（契约级，确定性）

本目录同时承载两层互不冒充的契约：v3 Phase 0B 是客服 Quality API 当前实施基线；`test_v4_*.py` 是 v4 Stage 0 target contract 的确定性自洽检查。v4 测试转绿只证明契约冻结，不证明 migration、runtime、provider live 或 Agent 因果执行。

Quality API live conformance 的出口判据仍是：**本套件可对空实现跑红**。任何组件声称「实现了 Quality API 契约」前，必须先让对应 live conformance 转绿。

## 运行

```bash
python3 -m venv /tmp/caseloop-contracts-venv
/tmp/caseloop-contracts-venv/bin/pip install -r contracts/conformance/requirements.txt
cd /Users/xiejiachen/caseloop
/tmp/caseloop-contracts-venv/bin/pytest contracts/conformance -x -q
```

环境变量：

| 变量 | 默认 | 说明 |
|------|------|------|
| `CASELOOP_QUALITY_API_BASE_URL` | `http://127.0.0.1:8080` | 被测 Quality API 根地址 |
| `CASELOOP_READ_TOKEN` | `conformance-read-token` | quality:read scope 令牌 |
| `CASELOOP_WRITE_TOKEN` | `conformance-write-token` | quality:write scope 令牌（扮演 Release Controller） |

## 测试层职责

- `test_quality_api.py` — Quality API v2 写面/读面契约（对 `quality-api/openapi.yaml`）：
  缺 If-Match → 412/428；错误 revision → 409 revision_conflict；
  Idempotency-Key 重放 → 同一资源/operation；key 复用配不同 body → 422；
  draft→stage→canary→promote 全链路状态断言；rollback 恢复 previous；
  非法迁移 → 422；read scope 调写面 → 401/403；B1–B4 注入端点。
  **空实现下全部连接错误（红）——这是预期，不许放水。**
- `test_schemas.py` — 契约资产自洽（不依赖服务，必须常绿）：
  fixtures/samples 六个样例全部通过各自 JSON Schema（draft 2020-12）；
  7 个反例（缺字段/错枚举/越权审批人）必须失败；
  WorkOrder hash 按 JCS+SHA-256 可复核；Approval 绑定样例 WorkOrder；
  events.yaml 七聚合事件四要素齐全；state-machines.yaml 七种失败语义齐全。
- `test_wilson.py` — 统计口径（不依赖服务，必须常绿）：
  `wilson_interval(successes, trials, z=1.96)` 参考实现，
  对 wilson-vectors.json 全向量断言（容差 1e-3）；
  MVP 演示用例 3/3 → 下界≈0.438<0.9（记账但拒绝晋升）。
- `test_v4_schemas.py` — v4 JSON Schema、正反 fixtures、不可重复字段/digest 与 secret 边界。
- `test_v4_ownership.py` — command/event 单一 owner、精确 record-authority command→event 映射、projection 禁写、Coordinator/Adapter/Exporter 禁权。
- `test_v4_intents.py` / `test_v4_openapi.py` — 精确 Intent Registry、execution mode、transport activation stage、CLI canonical/alias、scope、幂等、人类专属动作与 OpenAPI 3.1 映射。
- `test_v4_contract_docs.py` — canonical facet/category、Stage 1 OpenAPI 路径、Public MCP Stage 6 与 Runtime CapabilityLease/receipt 术语一致性。
- `test_v4_cutover.py` — v3/v4 并存、单一 lease authority、drain/reconcile/cutover/rollback 语义。
- `test_v4_integrity.py` / `test_v4_authority.py` — 全记录 JCS self-hash、同 ID revision/previous snapshot、签发时历史资源绑定、ControllerRegistration、单向 post-record AuthorityReceipt，以及 coordinated rehash/revision/authority 攻击反例。离线 authority bundle 只证明 `contract` facet。
- 其他 `test_v4_*.py` — v4 event/state recovery、transaction 和后续 Stage 0 slice；以文件名与 `contracts/v4/README.md` 为准。

仓库固定的纯离线验证命令：

```bash
cd /Users/xiejiachen/caseloop/contracts
../eval-harness/.venv/bin/python -m pytest \
  conformance/test_schemas.py conformance/test_wilson.py \
  conformance/test_v4_*.py -q
```

## 空实现下的预期结果

- `test_quality_api.py`：**全红**（`requests.exceptions.ConnectionError`）。
  这就是「对空实现跑红」的出口判据——转绿只能靠真实现，不能靠改测试。
- `test_schemas.py` / `test_wilson.py` / `test_v4_*.py`：**全绿**（纯契约资产自洽验证；v4 绿不升级为 runtime/live 证明）。

最近一次实跑结果见 `LAST-RUN.md`。
