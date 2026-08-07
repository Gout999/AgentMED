# LAST-RUN —— conformance suite 最近一次实跑记录

- 时间：2026-08-08（Phase 0B 冻结跑）
- 环境：macOS，Python 3.14.4，venv `/tmp/caseloop-contracts-venv`
- 依赖：按 `requirements.txt` 钉版（pytest 9.1.1 / jsonschema 4.26.0 / PyYAML 6.0.3 / requests 2.34.2）
- 被测实现：**空实现**（`http://127.0.0.1:8080` 无服务监听）

## 命令 1（规定动作）

```
cd /Users/xiejiachen/caseloop
/tmp/caseloop-contracts-venv/bin/pytest contracts/conformance -x -q
```

结果：`1 failed in 0.68s` —— 首个用例 `test_quality_api.py::test_create_draft_versionset`
即 `requests.exceptions.ConnectionError`（Errno 61 Connection refused），-x 停止。
**符合 Phase 0B 出口判据：套件可对空实现跑红。**

## 命令 2（全量计数）

```
/tmp/caseloop-contracts-venv/bin/pytest contracts/conformance -q
```

结果：**15 failed, 24 passed in 0.86s**

| 文件 | 结果 | 说明 |
|------|------|------|
| `test_quality_api.py` | **15 红 / 0 绿** | 全部 ConnectionError——空实现下的预期失败，未做任何放水（无 skip、无宽松断言）。覆盖：创建/幂等重放/key 复用/缺 If-Match/错误 revision/expected_revision 替代/全链路/非法迁移/rollback/scope/读面/B1–B4 注入 |
| `test_schemas.py` | **0 红 / 19 绿** | 6 样例过 schema + draft 2020-12 声明 + 7 反例必败 + WorkOrder hash 可复核 + Approval 绑定校验 + 信任样例 MVP 口径 + events.yaml 结构 + state-machines.yaml 结构（含七种失败语义断言） |
| `test_wilson.py` | **0 红 / 5 绿** | 13 组向量全过（容差 1e-3）；3/3→0.438494 拒绝晋升；30/30 仍拒、100/100 放行；0/0 全区间约定；区间不越界 |

## 修复记录

首轮全量跑曾出现 `test_schemas.py::test_state_machines_structure` 失败：
`events/state-machines.yaml` 第 59 行 `VERDICT_COMPUTED:"..."` 冒号后缺空格
（YAML ScannerError）。已修正为 `VERDICT_COMPUTED: "..."`，复跑转绿。

## 结论

契约资产自洽（24 绿）；API 契约测试对空实现全红（15 红）。
demo-app 的 Quality API 实现应以「本套件 39 全绿」为完成判据，
**禁止通过修改本套件断言来转绿**。
