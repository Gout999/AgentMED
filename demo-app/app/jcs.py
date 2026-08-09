"""JCS (RFC 8785) 规范化 + SHA-256 digest。

与 contracts/quality-api/openapi.yaml 的 digest 规则对齐：
所有 digest = sha256(JCS(canonical JSON))，表示为 `sha256:<64 hex>`。

与 control-plane 的 JCS 子集不同，这里允许浮点（模型 params 里 temperature 等），
字符串允许任意 Unicode（KB/prompt 为中文），序列化用 ensure_ascii=True 保证确定性。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def jcs_canonical(value: Any) -> str:
    """RFC 8785 风格 canonical JSON 字符串（None/bool/int/float/str/list/dict）。

    属性：确定性（同值同串）、键按排序、字符串转义 ASCII。
    浮点用 Python 最短往返 repr（0.0 → "0.0"），非有限值拒绝。
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("JCS: 不支持非有限浮点")
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(jcs_canonical(v) for v in value) + "]"
    if isinstance(value, dict):
        items = []
        for k in sorted(value.keys(), key=lambda x: str(x)):
            items.append(jcs_canonical(str(k)) + ":" + jcs_canonical(value[k]))
        return "{" + ",".join(items) + "}"
    raise TypeError(f"JCS 不支持类型: {type(value)!r}")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def content_digest(obj: Any) -> str:
    """`sha256:<64 hex>`，JCS canonical 后做 SHA-256。"""
    return f"sha256:{sha256_hex(jcs_canonical(obj).encode('utf-8'))}"


# ---------------------------------------------------------------- digest 构造（语义对齐 openapi）
# prompt.digest 覆盖 {prompt_id, version, content}；
# model.digest 覆盖 {provider, model, params}；
# KB 条目 digest 覆盖实际影响检索/回答的完整快照；
# manifest_digest 覆盖全部条目快照；VersionSet 完整 digest 覆盖 {prompt, kb_manifest, model} 三元组。


def prompt_digest(prompt_id: str, version: str, content: str) -> str:
    return content_digest({"prompt_id": prompt_id, "version": version, "content": content})


def model_digest(provider: str, model: str, params: dict) -> str:
    return content_digest({"provider": provider, "model": model, "params": params})


def kb_entry_digest(
    kb_id: str,
    entry_id: str,
    version: str,
    content: str,
    *,
    title: str = "",
    category: str = "",
    keywords: list[str] | None = None,
) -> str:
    """Bind every persisted field that can change deterministic retrieval.

    The public manifest remains compact and carries this digest.  Exact-candidate
    reconstruction recomputes it from the registered row, so stale digests cannot
    hide content or retrieval-metadata drift.
    """

    return content_digest(
        {
            "kb_id": kb_id,
            "entry_id": entry_id,
            "version": version,
            "title": title,
            "category": category,
            "keywords": list(keywords or []),
            "content": content,
        }
    )


def kb_manifest_digest(entries: list[dict]) -> str:
    """entries: [{kb_id, entry_id, version, digest}]，按 (kb_id, entry_id) 排序后整体 digest。"""
    ordered = sorted(entries, key=lambda e: (e.get("kb_id", ""), e.get("entry_id", "")))
    return content_digest({"entries": ordered})


def versionset_digest(prompt: dict, kb_manifest: dict, model: dict) -> str:
    """完整版本 digest = sha256(JCS({prompt, kb_manifest, model}))。"""
    return content_digest({"prompt": prompt, "kb_manifest": kb_manifest, "model": model})
