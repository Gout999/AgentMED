"""测试辅助：构造合法 WorkOrder payload（JCS 可序列化 ASCII）。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from common.jcs import workorder_hash


def make_workorder(
    nonce: str,
    *,
    workorder_id: str = "wo_test0000000000001",
    case_id: str = "case_test000000000001",
    expiry: Optional[str] = None,
    diff_content: str = "",
    **overrides: Any,
) -> dict[str, Any]:
    """构造合法 WorkOrder payload。

    diff 用 content_ref（JCS 子集不支持换行/非 ASCII 的内联 content）。
    """
    payload: dict[str, Any] = {
        "schema_version": "0.1.0",
        "workorder_id": workorder_id,
        "case_id": case_id,
        "channel": "prompt",
        "base_versionset_digest": "sha256:" + "a" * 64,
        "target_versionset_digest": "sha256:" + "b" * 64,
        "input_versions": {
            "prompt_digest": "sha256:" + "c" * 64,
            "kb_manifest_digest": "sha256:" + "d" * 64,
            "model_digest": "sha256:" + "e" * 64,
        },
        "diff": {
            "format": "unified_diff",
            "content_ref": diff_content or "minio://agentmed/prompts/fix-v1.diff",
            "digest": "sha256:" + "f" * 64,
        },
        "gate_report_ref": {"uri": "eval://eval_test00000001", "digest": "sha256:" + "g" * 64},
        "expiry": expiry or (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "nonce": nonce,
        "created_at": "2026-08-07T00:00:00+00:00",
        "created_by": "repairer",
    }
    payload.update(overrides)
    # hash_rule 属被 hash 绑定字段的一部分，须先于 hash 计算写入（与 control-plane 口径一致）
    payload["hash_rule"] = "jcs-rfc8785+sha256"
    payload["hash"] = workorder_hash(payload)
    return payload


def nonce() -> str:
    import uuid

    return str(uuid.uuid4())
