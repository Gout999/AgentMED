"""B1–B4 故障注入（x-internal，演示/测试用）。

- B1 prompt 回归：线上 prompt 切到 P1（v1.4.3，退货需人工审核）。
- B2 KB 回归：X200 续航 30h → 8h（物理改条目内容，digest 重算）。
- B3 model params 漂移：temperature 0.0→1.2, max_tokens 1024→64。
- B4 交互：prompt 引用 KB trade_in_program_v2 + 活动条款更新。

ground-truth 冻结于 contracts/fixtures/b1..b4-*.yaml；InjectResult.detail/ground_truth_ref 与之对应。
reset 恢复所有覆盖并清理故障状态。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import kb
from app.models import FaultState

# B2 目标条目与字段（对齐 contracts/fixtures/b2-kb-regression.yaml）
B2_KB_ID = "products"
B2_ENTRY_ID = "x200-earbuds"
B2_FROM = "续航 30 小时"
B2_TO = "续航 8 小时"
B2_GT = "contracts/fixtures/b2-kb-regression.yaml"

# B4：campaign 条目 v1 -> v2 覆盖（trade_in_program_v2 字段）
B4_KB_ID = "campaigns"
B4_ENTRY_ID = "trade-in-program"
B4_TITLE = "以旧换新活动"
B4_CONTENT_V2 = (
    "以旧换新活动（trade_in_program_v2）：旧耳机换新最高抵 300 元，"
    "旧手机换新最高抵 1000 元，回收寄出后 24 小时内发放抵扣券，"
    "与 7 天无理由退货权益互不冲突。"
)
B4_GT = "contracts/fixtures/b4-interaction.yaml"

GROUND_TRUTH = {
    "B1": "contracts/fixtures/b1-prompt-regression.yaml",
    "B2": B2_GT,
    "B3": "contracts/fixtures/b3-model-params-regression.yaml",
    "B4": B4_GT,
}


def _existing(db: Session, fault_id: str) -> FaultState | None:
    return db.get(FaultState, fault_id)


def _put(db: Session, fault_id: str, payload: dict[str, Any], snapshot: dict[str, Any]) -> None:
    row = _existing(db, fault_id)
    if row is None:
        db.add(FaultState(fault_id=fault_id, payload=payload, snapshot=snapshot))
    else:
        row.payload = payload
        row.snapshot = snapshot
    db.commit()


def _inject_b1(db: Session) -> dict[str, Any]:
    payload = {
        "channel": "prompt",
        "prompt_section": "售后政策",
        "prompt_ref": "prompts/system.md",
        "base_version": "v1.4.2",
        "injected_version": "v1.4.3",
        "detail": "prompt 回归：售后政策改为「退货需经人工审核，已激活商品不支持退货」",
        "ground_truth_ref": GROUND_TRUTH["B1"],
    }
    snapshot = {"channel": "prompt", "base": "active_versionset"}
    _put(db, "B1", payload, snapshot)
    return payload


def _inject_b2(db: Session) -> dict[str, Any]:
    entry = kb.find_entry(db, B2_KB_ID, B2_ENTRY_ID)
    if entry is None:
        raise KeyError(f"{B2_KB_ID}/{B2_ENTRY_ID} 种子缺失，无法注入 B2")
    snapshot = {
        "channel": "kb",
        "kb_id": B2_KB_ID,
        "entry_id": B2_ENTRY_ID,
        "original_content": entry.content,
        "original_digest": entry.digest,
    }
    kb.update_entry_content(db, B2_KB_ID, B2_ENTRY_ID, entry.content.replace(B2_FROM, B2_TO))
    payload = {
        "channel": "kb",
        "kb_entry": "products/X200-earbuds.yaml",
        "field": "battery_life_hours",
        "before": "30",
        "after": "8",
        "detail": "KB 回归：X200 续航参数被改错（30 小时 → 8 小时）",
        "ground_truth_ref": B2_GT,
    }
    _put(db, "B2", payload, snapshot)
    return payload


def _inject_b3(db: Session) -> dict[str, Any]:
    payload = {
        "channel": "model_params",
        "before": {"temperature": 0.0, "max_tokens": 1024},
        "after": {"temperature": 1.2, "max_tokens": 64},
        "detail": "model params 漂移：temperature 0.0→1.2, max_tokens 1024→64",
        "ground_truth_ref": GROUND_TRUTH["B3"],
    }
    snapshot = {"channel": "model_params", "base": "active_versionset"}
    _put(db, "B3", payload, snapshot)
    return payload


def _inject_b4(db: Session) -> dict[str, Any]:
    entry = kb.find_entry(db, B4_KB_ID, B4_ENTRY_ID)
    snapshot_kb = None
    if entry is not None:
        snapshot_kb = {
            "kb_id": B4_KB_ID,
            "entry_id": B4_ENTRY_ID,
            "original_content": entry.content,
            "original_digest": entry.digest,
        }
    kb.upsert_entry(
        db,
        B4_KB_ID,
        B4_ENTRY_ID,
        title=B4_TITLE,
        content=B4_CONTENT_V2,
        category="product",
        keywords=["以旧换新", "换新", "补贴", "抵扣", "回收", "trade_in_program_v2"],
    )
    payload = {
        "channel": "interaction",
        "changes": [
            {"channel": "prompt", "prompt_section": "以旧换新活动", "note": "引用 KB 新字段 trade_in_program_v2"},
            {"channel": "kb", "kb_entry": "campaigns/trade-in.yaml", "note": "新增字段 trade_in_program_v2"},
        ],
        "detail": "交互回归：prompt 引用 KB 新字段 trade_in_program_v2 + 活动条款更新",
        "ground_truth_ref": B4_GT,
    }
    snapshot = {"channel": "interaction", "prompt": "v1.4.4", "kb": snapshot_kb}
    _put(db, "B4", payload, snapshot)
    return payload


def inject_fault(db: Session, fault_id: str) -> dict[str, Any]:
    """注入故障。返回 InjectResult payload。未实现/非法 fault_id 抛 KeyError。"""
    handlers = {"B1": _inject_b1, "B2": _inject_b2, "B3": _inject_b3, "B4": _inject_b4}
    handler = handlers.get(fault_id)
    if handler is None:
        raise KeyError(f"unknown fault: {fault_id}")
    return handler(db)


def list_active_faults(db: Session) -> list[str]:
    rows = db.execute(select(FaultState.fault_id)).scalars().all()
    return sorted(rows)


def reset_faults(db: Session) -> list[str]:
    """清除全部故障并恢复基线。返回被清除的 fault_id 列表。"""
    rows = db.execute(select(FaultState)).scalars().all()
    cleared = sorted(f.fault_id for f in rows)

    # 先恢复物理修改（B2 恢复 x200；B4 恢复 campaign）
    for f in rows:
        snap = f.snapshot or {}
        channel = snap.get("channel")
        if channel == "kb":
            kb.update_entry_content(
                db, snap["kb_id"], snap["entry_id"], snap.get("original_content", "")
            )
        elif channel == "interaction":
            kb_snap = snap.get("kb")
            if kb_snap and kb_snap.get("original_content") is not None:
                kb.upsert_entry(
                    db,
                    kb_snap["kb_id"],
                    kb_snap["entry_id"],
                    title=B4_TITLE,
                    content=kb_snap["original_content"],
                    category="product",
                    keywords=["以旧换新", "换新", "补贴", "抵扣", "回收"],
                )

    # 再清空故障状态
    for f in rows:
        db.delete(f)
    if rows:
        db.commit()
    return cleared
