"""live config：线上运行配置解析（active versionset 内容 + 故障覆盖 -> 实际生效的 prompt/KB/model）。

- 无故障时 live = active versionset 内容（prompt 经注册表取正文、KB 取全量条目、model 取 content.model）。
- 有故障时被覆盖：B1/B4 覆盖 prompt，B2/B4 覆盖 KB 条目内容，B3 覆盖 model params。
- /logs 记录的三个 digest 全部来自 live config，归因层据此发现「线上偏离已注册版本」。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import jcs, kb
from app.config import get_settings
from app.models import FaultState, KBEntry, VersionSet

DEFAULT_PROVIDER = "stepfun"
DEFAULT_MODEL = "step-3.7-flash"
DEFAULT_PARAMS = {"temperature": 0.0, "max_tokens": 1024}

# B1/B4 注入的 prompt 版本（与 prompts/versions.json 对齐）
B1_PROMPT_VERSION = "v1.4.3"
B4_PROMPT_VERSION = "v1.4.4"
PROMPT_ID = "prompts/system.md"


@dataclass
class LivePrompt:
    prompt_id: str
    version: str
    content: str
    digest: str


@dataclass
class LiveModel:
    provider: str
    model: str
    params: dict
    digest: str


@dataclass
class LiveConfig:
    versionset_id: str
    prompt: LivePrompt
    entries: list[KBEntry]
    kb_manifest: dict
    model: LiveModel

    @property
    def kb_manifest_digest(self) -> str:
        return self.kb_manifest["manifest_digest"]


def get_active_versionset(db: Session) -> Optional[VersionSet]:
    return db.execute(
        select(VersionSet)
        .where(VersionSet.status == "active")
        .order_by(VersionSet.updated_at.desc(), VersionSet.created_at.desc())
    ).scalars().first()


def _load_faults(db: Session) -> dict[str, FaultState]:
    rows = db.execute(select(FaultState)).scalars().all()
    return {f.fault_id: f for f in rows}


def resolve_live_config(db: Session) -> LiveConfig:
    from app import prompts_registry

    settings = get_settings()
    active = get_active_versionset(db)
    faults = _load_faults(db)
    base_content = active.content if active else None
    base_prompt = (base_content or {}).get("prompt", {})
    base_model = (base_content or {}).get("model", {})

    versionset_id = active.versionset_id if active else ""

    # ---- prompt ----
    if "B1" in faults:
        content, digest = prompts_registry.resolve_prompt(db, PROMPT_ID, B1_PROMPT_VERSION)
        live_prompt = LivePrompt(prompt_id=PROMPT_ID, version=B1_PROMPT_VERSION, content=content, digest=digest)
    elif "B4" in faults:
        content, digest = prompts_registry.resolve_prompt(db, PROMPT_ID, B4_PROMPT_VERSION)
        live_prompt = LivePrompt(prompt_id=PROMPT_ID, version=B4_PROMPT_VERSION, content=content, digest=digest)
    else:
        prompt_id = base_prompt.get("prompt_id", PROMPT_ID)
        version = base_prompt.get("version", "")
        content, digest = prompts_registry.resolve_prompt(db, prompt_id, version)
        live_prompt = LivePrompt(prompt_id=prompt_id, version=version, content=content, digest=digest)

    # ---- KB（全量条目；B2/B4 注入已物理修改条目内容）----
    entries = kb.get_all_entries(db)
    kb_manifest = kb.build_manifest(entries)

    # ---- model ----
    if "B3" in faults:
        params = {"temperature": 1.2, "max_tokens": 64}
        provider = DEFAULT_PROVIDER
        model = DEFAULT_MODEL
    else:
        provider = base_model.get("provider", DEFAULT_PROVIDER)
        model = base_model.get("model", DEFAULT_MODEL)
        params = base_model.get("params", DEFAULT_PARAMS)
    model_digest = jcs.model_digest(provider, model, params)

    return LiveConfig(
        versionset_id=versionset_id,
        prompt=live_prompt,
        entries=entries,
        kb_manifest=kb_manifest,
        model=LiveModel(provider=provider, model=model, params=params, digest=model_digest),
    )
