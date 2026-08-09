"""JCS (RFC 8785 子集) + WorkOrder hash 单元测试。"""
import json

import pytest

from app.utils.jcs import canonical_json_digest, jcs_subset, params_digest, workorder_hash


def test_jcs_primitives():
    assert jcs_subset(None) == b"null"
    assert jcs_subset(True) == b"true"
    assert jcs_subset(False) == b"false"
    assert jcs_subset(123) == b"123"
    assert jcs_subset("abc") == b'"abc"'


def test_jcs_ordered_keys():
    # 键按 UTF-16 码位排序，与输入顺序无关
    a = jcs_subset({"b": 1, "a": 2})
    b = jcs_subset({"a": 2, "b": 1})
    assert a == b == b'{"a":2,"b":1}'


def test_jcs_rejects_float_and_non_ascii():
    with pytest.raises(ValueError):
        jcs_subset(1.5)
    with pytest.raises(ValueError):
        jcs_subset("中文")
    with pytest.raises(ValueError):
        jcs_subset("has\nnewline")


def test_workorder_hash_deterministic():
    payload = {
        "schema_version": "0.1.0",
        "workorder_id": "wo_12345678",
        "channel": "prompt",
        "hash": "ignored",
    }
    h1 = workorder_hash(payload)
    h2 = workorder_hash({**payload, "hash": "different"})
    assert h1 == h2  # hash 字段本身不参与计算
    assert len(h1) == 64
    assert h1 == h1.lower()


def test_workorder_hash_changes_with_content():
    a = {"workorder_id": "wo_12345678", "channel": "prompt", "diff": {"format": "json_patch", "content": "x"}}
    b = {"workorder_id": "wo_12345678", "channel": "prompt", "diff": {"format": "json_patch", "content": "y"}}
    assert workorder_hash(a) != workorder_hash(b)


def test_params_digest():
    d = params_digest({"action": "approve", "n": 1})
    assert d.startswith("sha256:")
    assert len(d) == 7 + 64


def test_canonical_json_digest_rejects_non_finite_numbers():
    with pytest.raises(ValueError):
        canonical_json_digest({"score": float("nan")})
