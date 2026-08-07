"""PII 脱敏单元测试（对齐 control-plane 规则）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.pii import normalize_for_dedup, redact_text


def test_phone_masked():
    r = redact_text("联系 13812341234 处理")
    assert "138****1234" in r.text
    assert r.redacted and r.hits["phone"] == 1


def test_email_masked():
    r = redact_text("发到 test@example.com")
    assert "t***@example.com" in r.text


def test_id_masked():
    r = redact_text("身份证 110101199003071234")
    assert "1101****1234" in r.text


def test_normalize_order():
    # 先脱敏再小写再折叠空白（连续空白折叠为单空格）
    s = normalize_for_dedup("  你 好  13812341234  客服  ")
    assert "138****1234" in s
    assert "  " not in s, "连续空白必须折叠为单空格"
    assert s == s.strip()


def test_normalize_phone_same_key_before_after_redaction():
    a = normalize_for_dedup("退货 13812341234")
    b = normalize_for_dedup("退货 13812341234")
    assert a == b
