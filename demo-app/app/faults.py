"""B1–B4 故障注入（x-internal，演示/测试用）。

- B1 prompt 回归：线上 prompt 切到 P1（v1.4.3，退货需人工审核）。
- B2 KB 回归：X200 续航 30h → 8h（物理改条目内容，digest 重算）。
- B3 model params 漂移：temperature 0.0→1.2, max_tokens 1024→64。
- B4 交互：prompt 引用 KB trade_in_program_v2 + 活动条款更新。

ground-truth 冻结于 contracts/fixtures/b1..b4-*.yaml；InjectResult.detail/ground_truth_ref 与之对应。
reset 恢复所有覆盖并清理故障状态。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import kb
from app.models import FaultState, TransitionRecord, VersionSet
from app.seeding import B1_FAULT_ID, B1_FAULT_PROMPT_VERSION, BASELINE_ID, BASELINE_PROMPT_VERSION

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


def _put(
    db: Session,
    fault_id: str,
    payload: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    injected_at: datetime | None = None,
) -> None:
    row = _existing(db, fault_id)
    if row is None:
        db.add(
            FaultState(
                fault_id=fault_id,
                injected_at=injected_at or datetime.now(timezone.utc),
                payload=payload,
                snapshot=snapshot,
            )
        )
    else:
        row.payload = payload
        row.snapshot = snapshot
    db.commit()


def _inject_b1(
    db: Session,
    *,
    expected_active_versionset_id: str = BASELINE_ID,
    fault_versionset_id: str = B1_FAULT_ID,
) -> dict[str, Any]:
    existing = _existing(db, "B1")
    if existing is not None:
        payload = existing.payload or {}
        if (
            payload.get("previous_versionset_id") == expected_active_versionset_id
            and payload.get("fault_versionset_id") == fault_versionset_id
        ):
            injected_at = existing.injected_at
            if injected_at.tzinfo is None:
                injected_at = injected_at.replace(tzinfo=timezone.utc)
            return {
                **payload,
                "injected_at": injected_at.astimezone(timezone.utc).isoformat(),
                "duplicate": True,
            }
        raise KeyError("B1 is already injected with a different VersionSet binding")

    active_rows = list(
        db.execute(
            select(VersionSet).where(VersionSet.status == "active").with_for_update()
        ).scalars()
    )
    previous = active_rows[0] if len(active_rows) == 1 else None
    fault = db.execute(
        select(VersionSet)
        .where(VersionSet.versionset_id == fault_versionset_id)
        .with_for_update()
    ).scalar_one_or_none()
    if previous is None or previous.versionset_id != expected_active_versionset_id:
        raise KeyError("B1 injection requires the exact single expected active VersionSet")
    if fault is None or fault.versionset_id == previous.versionset_id:
        raise KeyError("B1 fault VersionSet is unavailable or equals the active baseline")
    previous_prompt = ((previous.content or {}).get("prompt") or {}).get("version")
    fault_prompt = ((fault.content or {}).get("prompt") or {}).get("version")
    if (
        previous_prompt != BASELINE_PROMPT_VERSION
        or fault_prompt != B1_FAULT_PROMPT_VERSION
        or (previous.content or {}).get("kb_manifest") != (fault.content or {}).get("kb_manifest")
        or (previous.content or {}).get("model") != (fault.content or {}).get("model")
    ):
        raise KeyError("B1 injection artifacts are not a prompt-only P0/P1 pair")

    previous_status = previous.status
    fault_status = fault.status
    previous.status = "superseded"
    previous.canary_percent = 0
    previous.revision += 1
    fault.status = "active"
    fault.canary_percent = 100
    fault.revision += 1
    operation_id = "op_b1_fault_injection"
    db.add_all(
        [
            TransitionRecord(
                versionset_id=previous.versionset_id,
                from_status=previous_status,
                to_status="superseded",
                operation_id=operation_id,
                actor="release-controller:demo-injection",
            ),
            TransitionRecord(
                versionset_id=fault.versionset_id,
                from_status=fault_status,
                to_status="active",
                operation_id=operation_id,
                actor="release-controller:demo-injection",
            ),
        ]
    )
    injected_at = datetime.now(timezone.utc)
    payload = {
        "channel": "prompt",
        "prompt_section": "售后政策",
        "prompt_ref": "prompts/system.md",
        "base_version": "v1.4.2",
        "injected_version": "v1.4.3",
        "detail": "prompt 回归：售后政策改为「退货需经人工审核，已激活商品不支持退货」",
        "ground_truth_ref": GROUND_TRUTH["B1"],
        "previous_versionset_id": previous.versionset_id,
        "previous_versionset_digest": previous.digest,
        "previous_revision": previous.revision,
        "fault_versionset_id": fault.versionset_id,
        "fault_versionset_digest": fault.digest,
        "fault_revision": fault.revision,
        "injected_at": injected_at.isoformat(),
        "duplicate": False,
    }
    snapshot = {
        "channel": "versionset_lifecycle",
        "previous_versionset_id": previous.versionset_id,
        "previous_status": previous_status,
        "fault_versionset_id": fault.versionset_id,
        "fault_status": fault_status,
    }
    _put(db, "B1", payload, snapshot, injected_at=injected_at)
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


def inject_fault(
    db: Session,
    fault_id: str,
    *,
    expected_active_versionset_id: str = BASELINE_ID,
    fault_versionset_id: str = B1_FAULT_ID,
) -> dict[str, Any]:
    """注入故障。返回 InjectResult payload。未实现/非法 fault_id 抛 KeyError。"""
    handlers = {"B2": _inject_b2, "B3": _inject_b3, "B4": _inject_b4}
    if fault_id == "B1":
        return _inject_b1(
            db,
            expected_active_versionset_id=expected_active_versionset_id,
            fault_versionset_id=fault_versionset_id,
        )
    handler = handlers.get(fault_id)
    if handler is None:
        raise KeyError(f"unknown fault: {fault_id}")
    return handler(db)


def list_active_faults(db: Session) -> list[str]:
    rows = db.execute(select(FaultState.fault_id)).scalars().all()
    return sorted(rows)


def recover_b1(
    db: Session,
    *,
    expected_active_fault_versionset_id: str = B1_FAULT_ID,
    restore_versionset_id: str = BASELINE_ID,
    quarantine_versionset_id: str | None = None,
) -> dict[str, Any]:
    """Idempotently compensate an incomplete controlled B1 injection.

    This is deliberately narrower than ``reset_faults``: it only restores the
    exact prompt-only VersionSet pair recorded by the B1 injection receipt and
    refuses to overwrite a later promotion.
    """

    marker = db.execute(
        select(FaultState).where(FaultState.fault_id == "B1").with_for_update()
    ).scalar_one_or_none()
    identities = [expected_active_fault_versionset_id, restore_versionset_id]
    if quarantine_versionset_id:
        if quarantine_versionset_id in identities:
            raise KeyError("B1 recovery quarantine target must be a distinct VersionSet")
        identities.append(quarantine_versionset_id)
    rows = list(
        db.execute(
            select(VersionSet)
            .where(VersionSet.versionset_id.in_(identities))
            .with_for_update()
        ).scalars()
    )
    by_id = {row.versionset_id: row for row in rows}
    fault = by_id.get(expected_active_fault_versionset_id)
    restore = by_id.get(restore_versionset_id)
    quarantine = by_id.get(quarantine_versionset_id) if quarantine_versionset_id else None
    if fault is None or restore is None or fault.versionset_id == restore.versionset_id:
        raise KeyError("B1 recovery requires two exact registered VersionSets")
    if quarantine_versionset_id and quarantine is None:
        raise KeyError("B1 recovery quarantine VersionSet is unavailable")

    active_rows = list(
        db.execute(select(VersionSet).where(VersionSet.status == "active").with_for_update()).scalars()
    )
    if marker is None:
        if len(active_rows) == 1 and active_rows[0].versionset_id == restore.versionset_id:
            receipt = {
                "fault_id": "B1",
                "restored_versionset_id": restore.versionset_id,
                "restored_versionset_digest": restore.digest,
                "restored_revision": restore.revision,
                "fault_versionset_id": fault.versionset_id,
                "fault_versionset_digest": fault.digest,
                "fault_revision": fault.revision,
                "duplicate": True,
            }
            if quarantine is not None:
                if quarantine.status not in {"draft", "rolled_back"}:
                    raise KeyError("B1 recovery quarantine state changed")
                receipt.update(
                    {
                        "quarantined_versionset_id": quarantine.versionset_id,
                        "quarantined_versionset_digest": quarantine.digest,
                        "quarantined_revision": quarantine.revision,
                        "quarantined_status": quarantine.status,
                    }
                )
            return receipt
        raise KeyError("B1 recovery refused because a later VersionSet is active")

    payload = marker.payload or {}
    snapshot = marker.snapshot or {}
    if (
        snapshot.get("channel") != "versionset_lifecycle"
        or payload.get("fault_versionset_id") != fault.versionset_id
        or payload.get("previous_versionset_id") != restore.versionset_id
        or snapshot.get("fault_versionset_id") != fault.versionset_id
        or snapshot.get("previous_versionset_id") != restore.versionset_id
        or len(active_rows) != 1
        or active_rows[0].versionset_id != fault.versionset_id
    ):
        raise KeyError("B1 recovery binding/state changed; refusing compensation")

    fault_from = fault.status
    restore_from = restore.status
    quarantine_from = quarantine.status if quarantine is not None else None
    if quarantine is not None and quarantine.status not in {"draft", "staged", "canary"}:
        raise KeyError("B1 recovery quarantine target is no longer safely reversible")
    fault.status = snapshot.get("fault_status", "draft")
    fault.canary_percent = 0
    fault.revision += 1
    restore.status = snapshot.get("previous_status", "active")
    restore.canary_percent = 100
    restore.revision += 1
    operation_id = "op_b1_fault_recovery"
    transitions = [
            TransitionRecord(
                versionset_id=fault.versionset_id,
                from_status=fault_from,
                to_status=fault.status,
                operation_id=operation_id,
                actor="release-controller:demo-compensation",
            ),
            TransitionRecord(
                versionset_id=restore.versionset_id,
                from_status=restore_from,
                to_status=restore.status,
                operation_id=operation_id,
                actor="release-controller:demo-compensation",
            ),
        ]
    if quarantine is not None and quarantine.status in {"staged", "canary"}:
        quarantine.status = "rolled_back"
        quarantine.canary_percent = 0
        quarantine.revision += 1
        transitions.append(
            TransitionRecord(
                versionset_id=quarantine.versionset_id,
                from_status=str(quarantine_from),
                to_status="rolled_back",
                operation_id=operation_id,
                actor="release-controller:demo-compensation",
            )
        )
    db.add_all(transitions)
    db.delete(marker)
    db.commit()
    receipt = {
        "fault_id": "B1",
        "restored_versionset_id": restore.versionset_id,
        "restored_versionset_digest": restore.digest,
        "restored_revision": restore.revision,
        "fault_versionset_id": fault.versionset_id,
        "fault_versionset_digest": fault.digest,
        "fault_revision": fault.revision,
        "duplicate": False,
    }
    if quarantine is not None:
        receipt.update(
            {
                "quarantined_versionset_id": quarantine.versionset_id,
                "quarantined_versionset_digest": quarantine.digest,
                "quarantined_revision": quarantine.revision,
                "quarantined_status": quarantine.status,
            }
        )
    return receipt


def reset_faults(db: Session) -> list[str]:
    """清除全部故障并恢复基线。返回被清除的 fault_id 列表。"""
    rows = db.execute(select(FaultState)).scalars().all()
    cleared = sorted(f.fault_id for f in rows)

    # 先恢复物理修改（B2 恢复 x200；B4 恢复 campaign）
    for f in rows:
        snap = f.snapshot or {}
        channel = snap.get("channel")
        if channel == "versionset_lifecycle":
            previous = db.get(VersionSet, snap.get("previous_versionset_id"))
            fault = db.get(VersionSet, snap.get("fault_versionset_id"))
            current_active = list(
                db.execute(select(VersionSet).where(VersionSet.status == "active")).scalars()
            )
            if (
                previous is None
                or fault is None
                or len(current_active) != 1
                or current_active[0].versionset_id != fault.versionset_id
            ):
                raise KeyError("cannot reset B1 after active VersionSet changed")
            fault.status = snap.get("fault_status", "draft")
            fault.canary_percent = 0
            fault.revision += 1
            previous.status = snap.get("previous_status", "active")
            previous.canary_percent = 100
            previous.revision += 1
        elif channel == "kb":
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
