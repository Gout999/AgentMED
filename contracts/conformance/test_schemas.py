"""契约 schema 自洽测试：fixtures/samples 全部样例必须通过各自 JSON Schema；
构造的反例必须失败；events.yaml / state-machines.yaml 结构完整性检查。

本文件不依赖任何服务实现——它验证的是契约资产本身的自洽性，必须常绿。
"""
import copy
import hashlib
import json

import jsonschema
import pytest
import yaml

from conftest import (
    EVENTS_YAML,
    SAMPLES_DIR,
    SCHEMAS_DIR,
    STATE_MACHINES_YAML,
)

# 样例文件 → schema 文件的冻结映射（缺一即红）
SAMPLE_TO_SCHEMA = {
    "sample-workorder.json": "workorder.schema.json",
    "sample-approval.json": "approval.schema.json",
    "sample-evidence-bundle.json": "evidence-bundle.schema.json",
    "sample-attribution-report.json": "attribution-report.schema.json",
    "sample-trust-ledger-entry.json": "trust-ledger-entry.schema.json",
    "sample-gate-report.json": "gate-report.schema.json",
}


def _load(schema_file: str) -> jsonschema.Draft202012Validator:
    schema = json.loads((SCHEMAS_DIR / schema_file).read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)  # schema 自身必须合法
    return jsonschema.Draft202012Validator(schema)


def _sample(sample_file: str) -> dict:
    return json.loads((SAMPLES_DIR / sample_file).read_text(encoding="utf-8"))


@pytest.mark.parametrize("sample_file,schema_file", sorted(SAMPLE_TO_SCHEMA.items()))
def test_sample_validates(sample_file, schema_file):
    validator = _load(schema_file)
    errors = list(validator.iter_errors(_sample(sample_file)))
    assert not errors, f"{sample_file} 未通过 {schema_file}:\n" + "\n".join(
        f"  - {[str(p) for p in e.absolute_path]}: {e.message}" for e in errors[:5]
    )


def test_all_schemas_declare_draft_2020_12():
    for schema_file in SCHEMAS_DIR.glob("*.schema.json"):
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema", \
            f"{schema_file.name} 必须声明 JSON Schema draft 2020-12"


# ------------------------------------------------------------ 反例（必须失败）

NEGATIVE_CASES = [
    # (样例, schema, 变异函数, 说明)
    ("sample-workorder.json", "workorder.schema.json",
     lambda d: d.pop("nonce"),
     "WorkOrder 缺 nonce（防重放必需字段）"),
    ("sample-workorder.json", "workorder.schema.json",
     lambda d: d.update({"hash": "DEADBEEF" * 8}),
     "WorkOrder hash 非小写 hex sha256 格式"),
    ("sample-workorder.json", "workorder.schema.json",
     lambda d: d.update({"channel": "database"}),
     "channel 非三通道枚举（违反三通道纪律）"),
    ("sample-approval.json", "approval.schema.json",
     lambda d: d["approver"].update({"type": "agent"}),
     "审批人类型为 agent（LLM 不是权限权威源，必须 human）"),
    ("sample-trust-ledger-entry.json", "trust-ledger-entry.schema.json",
     lambda d: d.update({"risk_class": "R3_DESTRUCTIVE"}),
     "risk_class 非法枚举"),
    ("sample-evidence-bundle.json", "evidence-bundle.schema.json",
     lambda d: d["verdict"].update({"decision": "PROBABLY_ATTRIBUTED"}),
     "裁决非法枚举（必须严格三态）"),
    ("sample-gate-report.json", "gate-report.schema.json",
     lambda d: d.pop("live_provider_e2e"),
     "门禁报告缺 live_provider_e2e（必须与确定性测试分开报告）"),
]


@pytest.mark.parametrize(
    "sample_file,schema_file,mutate,desc",
    NEGATIVE_CASES,
    ids=[c[3] for c in NEGATIVE_CASES],
)
def test_negative_cases_must_fail(sample_file, schema_file, mutate, desc):
    validator = _load(schema_file)
    broken = copy.deepcopy(_sample(sample_file))
    mutate(broken)
    errors = list(validator.iter_errors(broken))
    assert errors, f"反例「{desc}」竟通过了 {schema_file}——schema 太松"


# ------------------------------------------------------------ WorkOrder hash 可复核

def _jcs_subset(value) -> bytes:
    """JCS (RFC 8785) 的 ASCII/整数/布尔子集（与生成器同一套规则）。

    样例被刻意构造为无浮点、无非 ASCII、无控制字符，此时子集与 RFC 8785 等价。
    """
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, int):
        return str(value).encode("ascii")
    if isinstance(value, float):
        raise ValueError("subset JCS 不支持浮点数")
    if isinstance(value, str):
        if any(ord(c) > 0x7E or ord(c) < 0x20 for c in value):
            raise ValueError(f"subset JCS 仅支持 ASCII 可打印字符: {value!r}")
        return json.dumps(value, ensure_ascii=True).encode("ascii")
    if isinstance(value, list):
        return b"[" + b",".join(_jcs_subset(v) for v in value) + b"]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0])
        return b"{" + b",".join(_jcs_subset(k) + b":" + _jcs_subset(v) for k, v in items) + b"}"
    raise TypeError(type(value))


def test_workorder_hash_recomputable():
    """hash 计算规则可机器复核：对除 hash 外全部字段做 JCS+SHA-256 必须等于 hash 字段。"""
    wo = _sample("sample-workorder.json")
    canonical = _jcs_subset({k: v for k, v in wo.items() if k != "hash"})
    recomputed = hashlib.sha256(canonical).hexdigest()
    assert recomputed == wo["hash"], \
        f"WorkOrder hash 不可复核: 重算 {recomputed} != 声明 {wo['hash']}"


def test_approval_binds_sample_workorder():
    """ApprovalGrant 必须绑定样例 WorkOrder 的 hash 与 nonce（防掉包/防重放绑定关系）。"""
    wo = _sample("sample-workorder.json")
    appr = _sample("sample-approval.json")
    assert appr["workorder_hash"] == wo["hash"], "grant 未绑定样例 WorkOrder hash"
    assert appr["nonce"] == wo["nonce"], "grant 未复制 WorkOrder nonce"
    assert appr["expiry"] <= wo["expiry"], "grant expiry 不得晚于 WorkOrder expiry"


def test_trust_entry_matches_mvp_demo_numbers():
    """信任账本样例必须体现 MVP 演示口径：3/3 → 下界≈0.438<0.9 → 记账但拒绝晋升。"""
    entry = _sample("sample-trust-ledger-entry.json")
    assert (entry["epoch_successes"], entry["epoch_trials"]) == (3, 3)
    assert abs(entry["wilson"]["lower"] - 0.438494) < 1e-3
    assert entry["promotion"]["eligible"] is False
    assert entry["promotion"]["decision"] == "denied"
    assert entry["sample_rule"] == "one_action_one_sample"


# ------------------------------------------------------------ events / 状态机结构完整性

SEVEN_AGGREGATES = {"case", "experiment", "changeset", "eval", "release", "notification", "trust"}


def test_events_yaml_structure():
    doc = yaml.safe_load(EVENTS_YAML.read_text(encoding="utf-8"))
    assert set(doc["aggregates"].keys()) == SEVEN_AGGREGATES, "必须恰好覆盖七个聚合"
    envelope_required = set(doc["envelope"]["required"])
    assert {"event_type", "aggregate_id", "causation_id", "payload"} <= envelope_required
    # 每个事件：event_type / aggregate_id / causation_id / payload 四要素齐全
    for agg_name, agg in doc["aggregates"].items():
        assert agg["events"], f"{agg_name} 聚合没有事件"
        for ev in agg["events"]:
            for key in ("event_type", "aggregate_id", "causation_id", "payload"):
                assert key in ev, f"{agg_name} 事件 {ev.get('event_type', '?')} 缺 {key}"
            assert ev["event_type"].split(".")[0] in (*SEVEN_AGGREGATES, "complaint"), \
                f"事件前缀异常: {ev['event_type']}"
    # 投诉接入：webhook/poll 双来源 + inbox 去重键定义
    sources = {s["id"] for s in doc["ingestion"]["sources"]}
    assert sources == {"webhook", "poll"}
    assert "dedup_key" in doc["ingestion"]["dedup"]


def test_state_machines_structure():
    doc = yaml.safe_load(STATE_MACHINES_YAML.read_text(encoding="utf-8"))
    machines = doc["machines"]
    assert set(machines.keys()) == SEVEN_AGGREGATES, "必须恰好七个状态机"
    for name, m in machines.items():
        states = set(m["states"].keys())
        assert m["initial"] in states, f"{name}: initial 不在 states 内"
        for t in m["transitions"]:
            assert t["from"] == "*" or t["from"] in states, \
                f"{name}: 迁移 from={t['from']} 未声明"
            assert t["to"] in states, f"{name}: 迁移 to={t['to']} 未声明"
        assert m.get("failure_semantics"), f"{name}: 缺 failure_semantics（§2.3.5 要求）"

    # §2.3.5 点名的七种失败语义必须可定位（键级精确断言，行为细节在各机迁移表）
    fs_text = yaml.dump({k: v.get("failure_semantics", {}) for k, v in machines.items()},
                        allow_unicode=True)
    assert "duplicate_and_merge" in fs_text, "缺「重复与合并」失败语义"
    assert "approval_rejected" in fs_text and "approval_expired" in fs_text, "缺「审批拒绝/过期」失败语义"
    assert "worker_lost" in fs_text, "缺「Worker 丢失」失败语义"
    assert "unknown_reconcile" in fs_text, "缺「发布 UNKNOWN→reconcile」失败语义"
    assert "rollback_failed" in fs_text, "缺「回滚失败」失败语义"
    assert "notification_failed" in fs_text, "缺「通知失败」失败语义"
    assert "manual_takeover" in fs_text, "缺「人工接管」失败语义"
