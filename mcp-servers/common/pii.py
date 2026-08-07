"""PII 入口脱敏（与 control-plane/app/utils/pii.py 口径一致；spec §11.2）。

脱敏失败拒收；审计与 Evidence Bundle 只存 params_digest。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 中国大陆手机号
_PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
# 邮箱
_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
# 18 位身份证（含末位 X）
_ID_CARD = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
# 15 位旧身份证
_ID_CARD_15 = re.compile(r"(?<!\d)(\d{15})(?!\d)")


class PIIRedactionError(Exception):
    """脱敏失败：拒收。"""


@dataclass
class RedactionResult:
    text: str
    redacted: bool
    hits: dict[str, int]


def redact_text(text: str) -> RedactionResult:
    """对文本做手机/邮箱/身份证掩码。

    掩码规则（稳定假名非本阶段要求，正则掩码即可）：
    - 手机：138****1234
    - 邮箱：a***@example.com
    - 身份证：前 4 + **** + 后 4
    """
    if text is None:
        raise PIIRedactionError("text is None")
    if not isinstance(text, str):
        raise PIIRedactionError("text must be str")

    hits = {"phone": 0, "email": 0, "id_card": 0}
    out = text

    def _mask_phone(m: re.Match[str]) -> str:
        hits["phone"] += 1
        s = m.group(1)
        return s[:3] + "****" + s[-4:]

    def _mask_email(m: re.Match[str]) -> str:
        hits["email"] += 1
        s = m.group(0)
        local, _, domain = s.partition("@")
        if not local or not domain:
            raise PIIRedactionError(f"malformed email: {s}")
        head = local[0] if local else "*"
        return f"{head}***@{domain}"

    def _mask_id(m: re.Match[str]) -> str:
        hits["id_card"] += 1
        s = m.group(1)
        if len(s) < 8:
            raise PIIRedactionError(f"id_card too short: {s}")
        return s[:4] + "****" + s[-4:]

    try:
        out = _PHONE.sub(_mask_phone, out)
        out = _EMAIL.sub(_mask_email, out)
        out = _ID_CARD.sub(_mask_id, out)
        out = _ID_CARD_15.sub(_mask_id, out)
    except PIIRedactionError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PIIRedactionError(f"redaction failed: {exc}") from exc

    return RedactionResult(text=out, redacted=any(hits.values()), hits=hits)


def normalize_for_dedup(text: str) -> str:
    """D-001 Q4：PII 脱敏后再 小写 + 连续空白折叠 + trim。"""
    redacted = redact_text(text).text
    lowered = redacted.lower()
    collapsed = re.sub(r"\s+", " ", lowered).strip()
    return collapsed
