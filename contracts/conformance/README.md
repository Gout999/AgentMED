# CaseLoop conformance suite（契约级，确定性）

Phase 0B 出口判据（plan-v3 §4）：**本套件可对空实现跑红**。
任何组件声称「实现了 Quality API 契约」前，必须先让本套件转绿。

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

## 三个测试文件的职责

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

## 空实现下的预期结果

- `test_quality_api.py`：**全红**（`requests.exceptions.ConnectionError`）。
  这就是「对空实现跑红」的出口判据——转绿只能靠真实现，不能靠改测试。
- `test_schemas.py` / `test_wilson.py`：**全绿**（纯契约资产自洽验证）。

最近一次实跑结果见 `LAST-RUN.md`。
