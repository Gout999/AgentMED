"""知识库服务：种子加载、条目读取/修改、manifest 快照。

Phase 1 检索作用于 live KB 全量条目（全文+元数据过滤）；KB manifest 用于 digest 绑定。
B2/B4 注入直接修改条目 content（digest 重算），reset 从 snapshot 恢复。
"""
from __future__ import annotations

import pathlib
from typing import Any, Optional

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jcs
from app.models import KBEntry

SEEDS_FILE = pathlib.Path(__file__).resolve().parents[1] / "seeds" / "kb_entries.yaml"


def _entry_schema_dict(e: KBEntry) -> dict[str, Any]:
    return {
        "kb_id": e.kb_id,
        "entry_id": e.entry_id,
        "version": e.version,
        "digest": e.digest,
    }


def build_manifest(entries: list[KBEntry]) -> dict[str, Any]:
    """KBManifest：逐条目快照 + manifest_digest。"""
    snapshots = [_entry_schema_dict(e) for e in entries]
    return {
        "entries": snapshots,
        "manifest_digest": jcs.kb_manifest_digest(snapshots),
    }


def seed_kb_entries(db: Session) -> int:
    """从 seeds/kb_entries.yaml 种子（幂等：表非空则跳过）。返回新增条数。"""
    if db.execute(select(KBEntry.id).limit(1)).first() is not None:
        return 0
    doc = yaml.safe_load(SEEDS_FILE.read_text(encoding="utf-8"))
    inserted = 0
    for raw in doc["entries"]:
        entry_id = raw["entry_id"]
        kb_id = raw["kb_id"]
        content = raw["content"].strip()
        digest = jcs.kb_entry_digest(kb_id, entry_id, raw.get("version", "1.0.0"), content)
        db.add(
            KBEntry(
                entry_id=entry_id,
                kb_id=kb_id,
                category=raw.get("category", "product"),
                title=raw.get("title", entry_id),
                content=content,
                keywords=raw.get("keywords", []),
                slug=raw.get("slug"),
                version=raw.get("version", "1.0.0"),
                digest=digest,
            )
        )
        inserted += 1
    db.commit()
    return inserted


def get_all_entries(db: Session) -> list[KBEntry]:
    return list(db.execute(select(KBEntry).order_by(KBEntry.id)).scalars())


def find_entry(db: Session, kb_id: str, entry_id: str) -> Optional[KBEntry]:
    return db.execute(
        select(KBEntry).where(KBEntry.kb_id == kb_id, KBEntry.entry_id == entry_id)
    ).scalar_one_or_none()


def update_entry_content(db: Session, kb_id: str, entry_id: str, content: str) -> KBEntry:
    """注入侧改内容（重算 digest，返回更新后的条目）。"""
    e = find_entry(db, kb_id, entry_id)
    if e is None:
        raise KeyError(f"KB entry not found: {kb_id}/{entry_id}")
    e.content = content.strip()
    e.digest = jcs.kb_entry_digest(e.kb_id, e.entry_id, e.version, e.content)
    db.commit()
    db.refresh(e)
    return e


def upsert_entry(
    db: Session,
    kb_id: str,
    entry_id: str,
    *,
    title: str,
    content: str,
    category: str = "product",
    keywords: list[str] | None = None,
    version: str = "1.0.0",
) -> KBEntry:
    """B4 注入新增/覆盖条目。"""
    e = find_entry(db, kb_id, entry_id)
    content = content.strip()
    digest = jcs.kb_entry_digest(kb_id, entry_id, version, content)
    if e is None:
        e = KBEntry(
            entry_id=entry_id,
            kb_id=kb_id,
            category=category,
            title=title,
            content=content,
            keywords=keywords or [],
            version=version,
            digest=digest,
        )
        db.add(e)
    else:
        e.title = title
        e.content = content
        e.keywords = keywords or []
        e.version = version
        e.digest = digest
    db.commit()
    db.refresh(e)
    return e
