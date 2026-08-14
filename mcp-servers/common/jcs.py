"""JCS (RFC 8785) 子集 + SHA-256（与 control-plane/app/utils/jcs.py 同实现）。

WorkOrder hash 契约：除 hash 外全部字段做 JCS 规范序列化 → SHA-256 → 小写 hex。
字符串按 RFC 8785 转义（非 ASCII → \\uXXXX，换行/控制字符同理），因此内联
中文 unified_diff 可直接进 hash；浮点数仍拒绝（子集约束）。
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
        # RFC 8785 §3.2.2.2：非 ASCII/控制字符一律 \uXXXX 转义（ensure_ascii=True
        # 已实现），任意 UTF-8 字符串都可规范序列化——此前对非 ASCII 直接拒绝与
        # RFC 8785 不符，并堵死了 B1 内联中文 unified_diff。
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
