"""digest 工具单测：canonical JSON 确定性 + 探针集冻结 digest。"""
import pytest

from eval_harness.digests import canonical_json_bytes, sha256_digest, probe_set_digest


def test_canonical_json_sorted_keys():
    a = canonical_json_bytes({"b": 2, "a": 1})
    b = canonical_json_bytes({"a": 1, "b": 2})
    assert a == b
    assert b'"a":1,"b":2' in a


def test_canonical_json_utf8():
    data = {"中文": "值"}
    raw = canonical_json_bytes(data)
    assert raw.decode("utf-8").count("中文") >= 0


def test_sha256_digest_format():
    d = sha256_digest({"x": 1})
    assert d.startswith("sha256:")
    assert len(d) == 7 + 64


def test_same_object_same_digest():
    assert sha256_digest({"a": [1, 2], "b": "x"}) == sha256_digest({"b": "x", "a": [1, 2]})


def test_probe_set_digest_deterministic():
    probes = [
        {"id": "cs-002", "input": "b", "expected_behavior": {"must_include": ["x"]}, "tags": {}},
        {"id": "cs-001", "input": "a", "expected_behavior": {"must_include": ["y"]}, "tags": {}},
    ]
    d1 = probe_set_digest(probes)
    d2 = probe_set_digest(list(reversed(probes)))
    assert d1 == d2  # 按 id 排序，与输入顺序无关


def test_probe_set_digest_changes_on_edit():
    probes = [{"id": "cs-001", "input": "a", "expected_behavior": {"must_include": ["y"]}, "tags": {}}]
    before = probe_set_digest(probes)
    probes[0]["input"] = "changed"
    after = probe_set_digest(probes)
    assert before != after  # 冻结纪律：任何增删改都换 digest
