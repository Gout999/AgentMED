"""JCS (RFC 8785) ASCII 子集 + SHA-256（与 control-plane/app/utils/jcs.py 同实现）。

WorkOrder hash 契约：除 hash 外全部字段做 JCS 规范序列化 → SHA-256 → 小写 hex。
含换行/非 ASCII 的 diff 请用 content_ref（spec §5.1 注释）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def jcs_subset(value: Any) -> bytes:
    if value is None:
        return b"null"
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value).encode("ascii")
    if isinstance(value, float):
        raise ValueError("subset JCS 不支持浮点数")
    if isinstance(value, str):
        if any(ord(c) > 0x7E or ord(c) < 0x20 for c in value):
            raise ValueError(f"subset JCS 仅支持 ASCII 可打印字符: {value!r}")
        return json.dumps(value, ensure_ascii=True).encode("ascii")
    if isinstance(value, list):
        return b"[" + b",".join(jcs_subset(v) for v in value) + b"]"
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda kv: kv[0])
        return b"{" + b",".join(jcs_subset(k) + b":" + jcs_subset(v) for k, v in items) + b"}"
    raise TypeError(type(value))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def workorder_hash(payload: dict[str, Any]) -> str:
    """对除 hash 外全部字段做 JCS+SHA-256，输出小写 hex（无 sha256: 前缀）。"""
    body = {k: v for k, v in payload.items() if k != "hash"}
    return sha256_hex(jcs_subset(body))


def params_digest(params: Any) -> str:
    """审计参数 digest：sha256:<hex>。JCS 不可支持时降级 sort_keys JSON。"""
    try:
        data = jcs_subset(params)
    except (ValueError, TypeError):
        data = json.dumps(params, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"sha256:{sha256_hex(data)}"


def content_sha256_hex(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))
