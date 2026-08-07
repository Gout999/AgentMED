"""PII 入口脱敏（手机/邮箱/身份证正则级）+ 归一化（D-001 Q4 顺序固定）。

与 control-plane/app/utils/pii.py 同一套规则；这里是 demo-app 侧自包含实现。
反馈评论（FeedbackEntry.comment）必须已做 PII 脱敏——铁律「PII 入口脱敏」。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_EMAIL = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
_ID_CARD = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
_ID_CARD_15 = re.compile(r"(?<!\d)(\d{15})(?!\d)")


class PIIRedactionError(Exception):
    """脱敏失败：拒收。"""


@dataclass
class RedactionResult:
    text: str
    redacted: bool
    hits: dict[str, int]


def redact_text(text: str) -> RedactionResult:
    if not isinstance(text, str):
        raise PIIRedactionError("text must be str")

    hits = {"phone": 0, "email": 0, "id_card": 0}
    out = text

    def _mask_phone(m: re.Match) -> str:
        hits["phone"] += 1
        s = m.group(1)
        return s[:3] + "****" + s[-4:]

    def _mask_email(m: re.Match) -> str:
        hits["email"] += 1
        s = m.group(0)
        local, _, domain = s.partition("@")
        if not local or not domain:
            raise PIIRedactionError(f"malformed email: {s}")
        return f"{local[0]}***@{domain}"

    def _mask_id(m: re.Match) -> str:
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
    """D-001 Q4：先 PII 脱敏、再小写、连续空白折叠为单空格、trim。顺序不可调换。"""
    redacted = redact_text(text).text
    lowered = redacted.lower()
    collapsed = re.sub(r"\s+", " ", lowered).strip()
    return collapsed
