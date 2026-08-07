"""prompt 模板注册表：文件（git 版本化）+ 版本元数据 -> DB 中的内容与 digest。

digest 语义（app/jcs.py）：覆盖 {prompt_id, version, content}。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app import jcs
from app.models import PromptVersion

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def _default_prompt_content() -> str:
    """兜底：注册表缺版本时用的默认 system prompt（不应发生在基线版本上）。"""
    return (
        "你是「小智客服」，3C 数码电商官方售后客服助手。"
        "请礼貌、简洁地回答用户问题；不确定时建议联系人工客服。"
    )


def load_prompt_versions_from_disk() -> dict[tuple[str, str], str]:
    """读 prompts/versions.json + 对应文件，返回 {(prompt_id, version): content}。"""
    versions_file = PROMPTS_DIR / "versions.json"
    if not versions_file.exists():
        return {}
    meta = json.loads(versions_file.read_text(encoding="utf-8"))
    prompt_id = meta["prompt_id"]
    result: dict[tuple[str, str], str] = {}
    for v in meta.get("versions", []):
        f = PROMPTS_DIR / v["file"]
        if f.exists():
            result[(prompt_id, v["version"])] = f.read_text(encoding="utf-8")
    return result


def seed_prompt_versions(db: Session) -> int:
    """把磁盘上的 prompt 版本注册进 DB（git 文件是唯一事实源）。

    幂等：已存在且内容一致则跳过；内容不一致则覆盖并重算 digest（版本元数据不变时，
    文件变更即视为该版本的内容演进）。
    """
    loaded = load_prompt_versions_from_disk()
    changed = 0
    for (prompt_id, version), content in loaded.items():
        digest = jcs.prompt_digest(prompt_id, version, content)
        row = db.get(PromptVersion, (prompt_id, version))
        if row is None:
            db.add(
                PromptVersion(
                    prompt_id=prompt_id,
                    version=version,
                    content=content,
                    digest=digest,
                    meta={"git_tracked": True, "note": "seeded"},
                )
            )
            changed += 1
        elif row.content != content:
            row.content = content
            row.digest = digest
            changed += 1
    if changed:
        db.commit()
    return changed


def get_prompt_version(
    db: Session, prompt_id: str, version: str
) -> Optional[PromptVersion]:
    return db.get(PromptVersion, (prompt_id, version))


def resolve_prompt(
    db: Session, prompt_id: str, version: str
) -> tuple[str, str]:
    """返回 (content, digest)。未注册版本返回兜底内容 + 兜底 digest。"""
    row = get_prompt_version(db, prompt_id, version)
    if row is not None:
        return row.content, row.digest
    fallback = _default_prompt_content()
    return fallback, jcs.prompt_digest(prompt_id, version, fallback)
