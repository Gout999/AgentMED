"""JCS (RFC 8785) 子集 + SHA-256。

字符串按 RFC 8785 §3.2.2.2 转义（非 ASCII/控制字符 → \\uXXXX），因此内联
中文 unified_diff 可直接进 WorkOrder hash；浮点仍拒绝（子集约束）。
与 contracts/conformance/test_schemas.py 保持一致，保证 WorkOrder hash 可复核。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def jcs_subset(value: Any) -> bytes:
    """JCS 子集：无浮点；字符串按 RFC 8785 转义（任意 UTF-8 可序列化）。"""
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
    """审计参数 digest：sha256:<hex>。

    优先 JCS（确定性）；含浮点/非 ASCII 等 JCS 子集不支持的值时降级为
    sort_keys JSON（审计 digest 仅用于防篡改，无契约格式约束）。
    """
    try:
        data = jcs_subset(params)
    except (ValueError, TypeError):
        data = json.dumps(
            params,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    return f"sha256:{sha256_hex(data)}"


def content_sha256_hex(text: str) -> str:
    return sha256_hex(text.encode("utf-8"))


def canonical_json_digest(value: Any, *, prefix: bool = True) -> str:
    """Digest arbitrary JSON (including UTF-8 strings/floats) using sorted compact JSON.

    GateReport contains judge scores and non-ASCII rationale text, so the intentionally narrow
    WorkOrder JCS subset cannot hash it. This matches eval-harness/common report hashing.
    """

    data = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = sha256_hex(data)
    return f"sha256:{digest}" if prefix else digest
