"""digest 工具：canonical JSON + SHA-256（契约口径：key 排序、无空白、UTF-8）。

契约约定 digest = sha256(canonical JSON)，表示为 `sha256:<64 hex>`。
本模块提供：
- canonical_json(obj)：递归 key 排序 + compact 序列化（ensure_ascii=False, UTF-8）。
- sha256_digest(obj)：对任意可 JSON 序列化对象算 `sha256:...`。
- probe_set_digest(probes)：冻结探针集 digest（按 probe id 排序的规范化结构）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# JSON 标量类型：None/bool/int/float/str 直接递归；dict/list 需处理。
_JSON_TYPES = (type(None), bool, int, float, str, list, dict)


def _canonical(obj: Any) -> Any:
    """递归规范化：dict 按键排序，其余原样（假定输入为 JSON 兼容类型）。"""
    if isinstance(obj, dict):
        return {k: _canonical(obj[k]) for k in sorted(obj.keys(), key=lambda s: str(s))}
    if isinstance(obj, list):
        return [_canonical(v) for v in obj]
    return obj


def canonical_json_bytes(obj: Any) -> bytes:
    """序列化为紧凑 UTF-8 bytes（key 排序、无空白）。"""
    canon = _canonical(obj)
    text = json.dumps(
        canon,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return text.encode("utf-8")


def sha256_digest(obj: Any) -> str:
    """返回 `sha256:<64 hex>`（对对象的 canonical JSON 做 SHA-256）。"""
    return "sha256:" + hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def digest_of_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def probe_set_digest(probes: list[dict]) -> str:
    """冻结探针集 digest：只取对判定有语义的字段（id/input/expected_behavior/tags），
    按 id 排序后规范化。任何探针增删改都会改变 digest（契约：冻结后不可变）。"""
    stripped = []
    for p in sorted(probes, key=lambda x: str(x.get("id", ""))):
        stripped.append(
            {
                "id": p.get("id"),
                "input": p.get("input"),
                "expected_behavior": p.get("expected_behavior"),
                "tags": p.get("tags"),
            }
        )
    return sha256_digest({"probe_set": stripped})
