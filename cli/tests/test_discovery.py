"""``caseloop init`` discovery safety and determinism tests."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from caseloop_cli.discovery import DiscoveryError, discover, render_draft


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "workload"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@caseloop.dev")
    _git(root, "config", "user.name", "Test")
    (root / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("def main():\n    return 42\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "initial")
    return root


def test_discover_git_metadata_project_and_application_code(repo: Path) -> None:
    result = discover(repo)
    assert result.git_revision is not None
    assert result.project_type == "python"
    assert result.test_commands == ["pytest"]
    kinds = {component.component_kind for component in result.components}
    assert "APPLICATION_CODE" in kinds
    code = next(component for component in result.components if component.component_kind == "APPLICATION_CODE")
    assert code.identity_assurance == "IMMUTABLE_DIGEST"
    assert code.content_digest is not None and code.content_digest.startswith("sha256:")
    assert code.artifact_refs and code.artifact_refs[0]["kind"] == "git_commit"


def test_discover_repeat_scan_is_stable(repo: Path) -> None:
    first = render_draft(discover(repo))
    second = render_draft(discover(repo))
    assert first == second
    draft = json.loads(first)
    assert "scanned_at" not in draft["_discovery"]


def test_discover_detects_recognized_component_dirs(repo: Path) -> None:
    (repo / "prompts").mkdir()
    (repo / "prompts" / "triage.md").write_text("# Triage prompt\n", encoding="utf-8")
    (repo / "retrievers").mkdir()
    (repo / "retrievers" / "indexer.py").write_text("def index():\n    pass\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "components")
    result = discover(repo)
    kinds = {component.component_kind for component in result.components}
    assert "PROMPT" in kinds
    assert "RETRIEVER" in kinds


def test_discover_redacts_secret_files_and_never_leaks_content(repo: Path) -> None:
    secret = "super-secret-credential-value-never-leak"
    (repo / ".env").write_text(f"API_KEY={secret}\n", encoding="utf-8")
    (repo / "credentials.json").write_text(f'{{"token": "{secret}"}}\n', encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "secrets")
    result = discover(repo)
    assert any(".env" in path for path in result.redacted_paths)
    assert any("credentials.json" in path for path in result.redacted_paths)
    draft = render_draft(result)
    assert secret not in draft
    assert "API_KEY" not in draft


def test_discover_symlink_inside_repo_is_not_followed(repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "leak.txt").write_text("outside-content\n", encoding="utf-8")
    (repo / "escape").symlink_to(outside, target_is_directory=True)
    result = discover(repo)
    draft = render_draft(result)
    assert "outside-content" not in draft
    assert "escape" not in draft


def test_discover_root_symlink_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    _git(real, "init", "-q", "-b", "main")
    _git(real, "config", "user.email", "test@caseloop.dev")
    _git(real, "config", "user.name", "Test")
    (real / "a.txt").write_text("x\n", encoding="utf-8")
    _git(real, "add", ".")
    _git(real, "commit", "-q", "-m", "init")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(DiscoveryError) as raised:
        discover(link)
    assert raised.value.code == "DISCOVERY_ROOT_SYMLINK"


def test_discover_non_git_directory_rejected(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(DiscoveryError) as raised:
        discover(plain)
    assert raised.value.code == "DISCOVERY_NOT_GIT_REPOSITORY"


def test_discover_missing_root_rejected(tmp_path: Path) -> None:
    with pytest.raises(DiscoveryError) as raised:
        discover(tmp_path / "missing")
    assert raised.value.code == "DISCOVERY_ROOT_UNREACHABLE"
