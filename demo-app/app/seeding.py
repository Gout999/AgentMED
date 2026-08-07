"""启动初始化：建表 → 种子 prompt 版本 → 种子 KB → 基线 VersionSet（active）。

基线 = P0（v1.4.2）+ 全量 KB manifest + step-3.7-flash(temperature=0)。
固定 ID vs_baseline0000000001，重复启动幂等。
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import jcs, kb
from app.config import get_settings
from app.db import SessionLocal, init_schema
from app.models import TransitionRecord, VersionSet
from app.prompts_registry import PROMPTS_DIR, seed_prompt_versions

BASELINE_ID = "vs_baseline0000000001"
BASELINE_PROMPT_VERSION = "v1.4.2"


def ensure_baseline_versionset(db: Session) -> VersionSet:
    existing = db.get(VersionSet, BASELINE_ID)
    if existing is not None:
        return existing

    settings = get_settings()
    entries = kb.get_all_entries(db)
    kb_manifest = kb.build_manifest(entries)
    model_params = {"temperature": 0.0, "max_tokens": 1024}
    model_obj = {
        "provider": "stepfun",
        "model": settings.stepfun_model,
        "params": model_params,
        "digest": jcs.model_digest("stepfun", settings.stepfun_model, model_params),
    }
    from app.prompts_registry import resolve_prompt

    prompt_content, prompt_digest = resolve_prompt(
        db, "prompts/system.md", BASELINE_PROMPT_VERSION
    )
    prompt_obj = {
        "prompt_id": "prompts/system.md",
        "version": BASELINE_PROMPT_VERSION,
        "digest": prompt_digest,
    }
    content = {
        "prompt": prompt_obj,
        "kb_manifest": kb_manifest,
        "model": model_obj,
    }
    content["digest"] = jcs.versionset_digest(prompt_obj, kb_manifest, model_obj)

    vs = VersionSet(
        versionset_id=BASELINE_ID,
        revision=1,
        status="active",
        content=content,
        digest=content["digest"],
        canary_percent=100,
    )
    db.add(vs)
    db.add(
        TransitionRecord(
            versionset_id=BASELINE_ID,
            from_status="draft",
            to_status="active",
            operation_id="op_baseline_seed",
            actor="system",
        )
    )
    db.commit()
    db.refresh(vs)
    return vs


def init_app() -> None:
    """启动一次性初始化（FastAPI lifespan 调用）。"""
    init_schema()
    db = SessionLocal()
    try:
        seed_prompt_versions(db)
        kb.seed_kb_entries(db)
        ensure_baseline_versionset(db)
    finally:
        db.close()
