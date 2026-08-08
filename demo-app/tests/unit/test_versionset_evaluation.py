"""Exact-candidate VersionSet reconstruction fails closed on asset drift."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import jcs, kb, prompts_registry
from app.live_config import VersionSetConfigError, resolve_versionset_config
from app.models import VersionSet


def _fixture():
    prompt_content = "registered candidate prompt"
    prompt_digest = jcs.prompt_digest("prompts/system.md", "v9", prompt_content)
    prompt = SimpleNamespace(content=prompt_content, digest=prompt_digest)
    entry = SimpleNamespace(
        kb_id="support",
        entry_id="returns",
        version="1.0.0",
        content="returns content",
        title="Returns",
        category="after_sales",
        keywords=[],
    )
    entry.digest = jcs.kb_entry_digest(
        entry.kb_id,
        entry.entry_id,
        entry.version,
        entry.content,
        title=entry.title,
        category=entry.category,
        keywords=entry.keywords,
    )
    manifest = kb.build_manifest([entry])
    model = {
        "provider": "stepfun",
        "model": "step-3.7-flash",
        "params": {"temperature": 0.0},
    }
    model["digest"] = jcs.model_digest(model["provider"], model["model"], model["params"])
    prompt_obj = {
        "prompt_id": "prompts/system.md",
        "version": "v9",
        "digest": prompt_digest,
    }
    content = {"prompt": prompt_obj, "kb_manifest": manifest, "model": model}
    content["digest"] = jcs.versionset_digest(prompt_obj, manifest, model)
    versionset = SimpleNamespace(
        versionset_id="vs_candidate",
        content=content,
        digest=content["digest"],
    )
    return prompt, entry, versionset


def test_resolve_exact_candidate_uses_registered_assets(monkeypatch):
    prompt, entry, versionset = _fixture()
    db = SimpleNamespace(get=lambda model, identity: versionset if model is VersionSet else None)
    monkeypatch.setattr(prompts_registry, "get_prompt_version", lambda *_args: prompt)
    monkeypatch.setattr(kb, "find_entry", lambda *_args: entry)

    resolved = resolve_versionset_config(db, "vs_candidate")

    assert resolved.versionset_id == "vs_candidate"
    assert resolved.prompt.content == prompt.content
    assert resolved.kb_manifest_digest == versionset.content["kb_manifest"]["manifest_digest"]
    assert resolved.model.digest == versionset.content["model"]["digest"]


def test_resolve_exact_candidate_rejects_kb_digest_drift(monkeypatch):
    prompt, entry, versionset = _fixture()
    entry.digest = "sha256:" + "0" * 64
    db = SimpleNamespace(get=lambda model, identity: versionset if model is VersionSet else None)
    monkeypatch.setattr(prompts_registry, "get_prompt_version", lambda *_args: prompt)
    monkeypatch.setattr(kb, "find_entry", lambda *_args: entry)

    with pytest.raises(VersionSetConfigError) as exc:
        resolve_versionset_config(db, "vs_candidate")

    assert exc.value.code == "asset_digest_mismatch"


def test_resolve_exact_candidate_rejects_kb_content_drift_with_stale_digest(monkeypatch):
    prompt, entry, versionset = _fixture()
    entry.content = "mutated content with the old persisted digest"
    db = SimpleNamespace(get=lambda model, identity: versionset if model is VersionSet else None)
    monkeypatch.setattr(prompts_registry, "get_prompt_version", lambda *_args: prompt)
    monkeypatch.setattr(kb, "find_entry", lambda *_args: entry)

    with pytest.raises(VersionSetConfigError) as exc:
        resolve_versionset_config(db, "vs_candidate")

    assert exc.value.code == "asset_digest_mismatch"


@pytest.mark.parametrize("field", ["title", "category", "keywords"])
def test_resolve_exact_candidate_rejects_retrieval_metadata_drift(monkeypatch, field):
    prompt, entry, versionset = _fixture()
    setattr(entry, field, ["mutated"] if field == "keywords" else "mutated")
    db = SimpleNamespace(get=lambda model, identity: versionset if model is VersionSet else None)
    monkeypatch.setattr(prompts_registry, "get_prompt_version", lambda *_args: prompt)
    monkeypatch.setattr(kb, "find_entry", lambda *_args: entry)

    with pytest.raises(VersionSetConfigError) as exc:
        resolve_versionset_config(db, "vs_candidate")

    assert exc.value.code == "asset_digest_mismatch"
