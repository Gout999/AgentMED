"""PII 入口脱敏（D-001 Q4 顺序：先脱敏 → 归一化 → 哈希）。"""
import hashlib

import pytest

from app.utils.pii import PIIRedactionError, normalize_for_dedup, redact_text


def test_redact_phone():
    r = redact_text("联系 13800138000 处理")
    assert "138****8000" in r.text
    assert r.hits["phone"] == 1


def test_redact_email():
    r = redact_text("发邮件到 a.b@example.com 给我")
    assert "a***@example.com" in r.text
    assert r.hits["email"] == 1


def test_redact_id_card():
    r = redact_text("身份证 110101199001011234")
    assert "1101****1234" in r.text
    assert r.hits["id_card"] == 1


def test_no_pii_passthrough():
    r = redact_text("你好世界")
    assert r.text == "你好世界"
    assert r.redacted is False
    assert r.hits == {"phone": 0, "email": 0, "id_card": 0}


def test_redact_rejects_none():
    with pytest.raises(PIIRedactionError):
        redact_text(None)  # type: ignore[arg-type]


def test_normalize_order_matters():
    # 脱敏先于小写/空白折叠；同投诉脱敏前后同键
    a = normalize_for_dedup("  联系 13800138000  处理  ")
    b = normalize_for_dedup("联系 13800138000 处理")
    assert a == b == "联系 138****8000 处理"
    # 归一化后哈希
    h = hashlib.sha256(a.encode("utf-8")).hexdigest()
    assert len(h) == 64
