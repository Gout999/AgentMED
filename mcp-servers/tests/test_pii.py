"""PII 入口脱敏（spec §11.2；与 control-plane 口径一致）。"""
from __future__ import annotations

import pytest

from common.pii import PIIRedactionError, normalize_for_dedup, redact_text


def test_redact_phone():
    result = redact_text("联系 13812345678 处理")
    assert "13812345678" not in result.text
    assert "138****5678" in result.text
    assert result.redacted is True
    assert result.hits["phone"] == 1


def test_redact_email():
    result = redact_text("邮箱 alex.wang@example.com 收")
    assert "alex.wang" not in result.text
    assert "a***@example.com" in result.text
    assert result.hits["email"] == 1


def test_redact_id_card():
    result = redact_text("证件 110101199003078516 备案")
    assert "110101199003078516" not in result.text
    assert "1101****8516" in result.text
    assert result.hits["id_card"] == 1


def test_redact_no_pii_no_change():
    result = redact_text("没有任何敏感信息，正常文本。")
    assert result.text == "没有任何敏感信息，正常文本。"
    assert result.redacted is False


def test_redact_failure_rejects():
    with pytest.raises(PIIRedactionError):
        redact_text(None)  # type: ignore[arg-type]
    with pytest.raises(PIIRedactionError):
        redact_text(12345)  # type: ignore[arg-type]


def test_normalize_for_dedup_after_redaction():
    """D-001 Q4：先脱敏 → 小写 → 连续空白折叠 → trim。"""
    a = normalize_for_dedup("  Hello   13812345678  World  ")
    b = normalize_for_dedup("hello 138****5678 world")
    assert a == b == "hello 138****5678 world"


def test_same_complaint_redact_then_dedup_same_key():
    """同一投诉（仅空白/大小写差异）脱敏后哈希一致（Q4 顺序定死：先脱敏再归一化再哈希）。"""
    import hashlib

    text1 = "Hello 13912345678 world"
    text2 = "  hello 13912345678  world  "
    k1 = hashlib.sha256(normalize_for_dedup(text1).encode("utf-8")).hexdigest()
    k2 = hashlib.sha256(normalize_for_dedup(text2).encode("utf-8")).hexdigest()
    assert k1 == k2
    assert "13912345678" not in normalize_for_dedup(text1)  # 先脱敏
