"""JCS 规范化 + digest 单元测试。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import jcs


def test_digest_format():
    d = jcs.content_digest({"a": 1})
    assert d.startswith("sha256:")
    assert len(d) == len("sha256:") + 64


def test_jcs_deterministic():
    a = {"b": 2, "a": [1, "x", True, None], "f": 0.0}
    b = {"a": [1, "x", True, None], "b": 2, "f": 0.0}
    assert jcs.jcs_canonical(a) == jcs.jcs_canonical(b)


def test_jcs_key_order():
    s = jcs.jcs_canonical({"z": 1, "a": 2})
    assert s.index("a") < s.index("z")
    assert s == '{"a":2,"z":1}'


def test_jcs_float_zero():
    assert jcs.jcs_canonical({"t": 0.0}) == '{"t":0.0}'
    assert jcs.jcs_canonical({"t": 1.0}) == '{"t":1.0}'
    assert jcs.jcs_canonical(1.0) == "1.0"
    assert jcs.jcs_canonical(1) == "1"


def test_jcs_chinese_escaped():
    s = jcs.jcs_canonical("退货")
    assert "\\u" in s  # ensure_ascii
    assert json.loads(s) == "退货"


def test_digests_align_with_conformance_style():
    """与 conformance test_schemas._jcs_subset 对 ASCII/整数一致（无浮点/中文时）。"""
    wo = {"nonce": "n1", "channel": "prompt"}
    assert jcs.jcs_canonical(wo) == '{"channel":"prompt","nonce":"n1"}'
    assert jcs.content_digest(wo) == "sha256:" + __import__("hashlib").sha256(
        b'{"channel":"prompt","nonce":"n1"}'
    ).hexdigest()


def test_model_digest_includes_params():
    d0 = jcs.model_digest("stepfun", "step-3.7-flash", {"temperature": 0.0, "max_tokens": 1024})
    d1 = jcs.model_digest("stepfun", "step-3.7-flash", {"temperature": 1.2, "max_tokens": 64})
    assert d0 != d1  # B3 漂移必须改变 digest
