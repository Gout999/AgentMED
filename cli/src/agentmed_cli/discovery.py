"""Local read-only manifest discovery for ``agentmed init <repo>``.

Discovery never writes server state and never calls the canonical import;
it only produces a manifest DRAFT that a human must confirm before import.
Safety guards implemented here:

- root escape: the scanned root is resolved and verified to be a git work
  tree (or a directory inside one); symlinked directories are not followed
  (``os.walk(followlinks=False)``), so a symlink cannot pull the scan outside
  the requested repo.
- secret redaction: well-known secret file names are skipped entirely and
  reported as redacted; their content is never read.
- unstable repeat-scan protection: every emitted list is sorted, and digests
  are derived from git metadata (commit + tree) instead of timestamps, so two
  scans of the same repo produce byte-identical drafts.
- no fabricated assets: only components that can be reliably confirmed by
  file-name/directory-name heuristics are emitted; everything else is
  reported as ``UNKNOWN`` or omitted.  The draft marks ``application`` and
  ``environment`` as required human input.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

try:
    import rfc8785
except Exception:  # pragma: no cover - import guard mirrors client.py
    rfc8785 = None  # type: ignore[assignment]


_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SECRET_FILE_RE = re.compile(
    r"(^|[./])("
    r"(\.env[^/]*)$"
    r"|.*\.(pem|p12|pfx|key)$"
    r"|^(id_rsa|id_ed25519|credentials\.json|credentials\.yml|credentials\.yaml)$"
    r"|.*(secret|credential)[^/]*\.(json|yaml|yml|toml|ini)$"
    r")",
    re.IGNORECASE,
)
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".tox",
        ".nox",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".eggs",
        "target",
    }
)
_RECOGNIZED_KINDS = frozenset(
    {"APPLICATION_CODE", "PROMPT", "MODEL_BINDING", "RETRIEVER", "INDEX"}
)


class DiscoveryError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GitMetadata:
    commit: str | None
    tree: str | None
    ref: str | None
    tags: tuple[str, ...] = ()
    dirty: bool | None = None

    @property
    def available(self) -> bool:
        return bool(self.commit and self.tree)


@dataclass(frozen=True)
class DiscoveredComponent:
    logical_name: str
    component_kind: str
    identity_locator: dict[str, object]
    identity_assurance: str
    content_digest: str | None
    unknown_reason: str | None
    artifact_refs: list[dict[str, object]]
    provenance: dict[str, object]


@dataclass
class DiscoveryResult:
    root: str
    git_revision: str | None
    git_tree: str | None
    git_ref: str | None
    project_type: str | None
    test_commands: list[str]
    components: list[DiscoveredComponent] = field(default_factory=list)
    redacted_paths: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    scanned_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_manifest_draft(self) -> dict[str, object]:
        return {
            "schema_version": "2.0",
            "application": None,
            "environment": None,
            "components": [
                {
                    "logical_name": component.logical_name,
                    "component_kind": component.component_kind,
                    "owner_principal_ids": [],
                    "criticality": "P2",
                    "data_classification": "INTERNAL",
                    "permission_classification": "READ_WRITE"
                    if component.component_kind == "APPLICATION_CODE"
                    else "READ_ONLY",
                    "effect_classification": "LOCAL"
                    if component.component_kind == "APPLICATION_CODE"
                    else "NONE",
                    "revision": {
                        "identity_locator": component.identity_locator,
                        "identity_assurance": component.identity_assurance,
                        "content_digest": component.content_digest,
                        "unknown_reason": component.unknown_reason,
                        "artifact_refs": component.artifact_refs,
                    },
                }
                for component in self.components
            ],
            "dependency_edges": [],
            "approver_policy": None,
            "_discovery": {
                "root": self.root,
                "git_revision": self.git_revision,
                "git_tree": self.git_tree,
                "git_ref": self.git_ref,
                "project_type": self.project_type,
                "test_commands": self.test_commands,
                "redacted_paths": self.redacted_paths,
                "notes": self.notes,
                "incomplete_fields": ["application", "environment"],
            },
        }


def _run_git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _canonical_digest(value: object) -> str | None:
    if rfc8785 is None:
        return None
    try:
        canonical = rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, TypeError, ValueError):
        return None
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _git_metadata(root: Path) -> GitMetadata:
    commit = _run_git(root, "rev-parse", "HEAD")
    tree = _run_git(root, "rev-parse", "HEAD^{tree}")
    ref = _run_git(root, "symbolic-ref", "--short", "HEAD")
    tags: tuple[str, ...] = ()
    tag_output = _run_git(root, "tag", "--points-at", "HEAD")
    if tag_output:
        tags = tuple(sorted(line.strip() for line in tag_output.splitlines() if line.strip()))
    try:
        status = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=normal",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        dirty: bool | None = None
    else:
        dirty = bool(status.stdout) if status.returncode == 0 else None
    return GitMetadata(commit=commit, tree=tree, ref=ref, tags=tags, dirty=dirty)


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return slug[:64] if slug else "application"


def _project_type(root: Path) -> tuple[str | None, list[str]]:
    if (root / "pyproject.toml").is_file():
        return "python", ["pytest"]
    if (root / "package.json").is_file():
        return "node", ["npm test"]
    if (root / "go.mod").is_file():
        return "go", ["go test ./..."]
    if (root / "Cargo.toml").is_file():
        return "rust", ["cargo test"]
    return None, []


def _walk(root: Path) -> list[tuple[Path, str]]:
    """Yield (path, kind) for candidate component roots without following symlinks."""
    entries: list[tuple[Path, str]] = []
    try:
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            # Drop symlinked and skipped directories in place so os.walk prunes them.
            pruned: list[str] = []
            for dirname in sorted(dirnames):
                child = current_path / dirname
                if dirname in _SKIP_DIRS or child.is_symlink():
                    pruned.append(dirname)
                    continue
                entries.append((child, "dir"))
            for dirname in pruned:
                dirnames.remove(dirname)
            for filename in sorted(filenames):
                if _SECRET_FILE_RE.search(filename):
                    entries.append((current_path / filename, "secret"))
                    continue
                entries.append((current_path / filename, "file"))
    except OSError:
        pass
    return entries


def _subtree_digest(root: Path, rel_path: str, git: GitMetadata) -> str | None:
    """Deterministic digest of one repository sub-path from git tree metadata."""
    if git.available and git.dirty is False:
        tree_entry = _run_git(root, "rev-parse", f"HEAD:{rel_path}")
        if tree_entry:
            return _canonical_digest({"commit": git.commit, "path": rel_path, "tree": tree_entry})
    return None


def _detect_components(
    root: Path,
    git: GitMetadata,
    entries: list[tuple[Path, str]],
) -> tuple[list[DiscoveredComponent], list[str]]:
    components: list[DiscoveredComponent] = []
    notes: list[str] = []
    redacted: list[str] = [str(path) for path, kind in entries if kind == "secret"]

    rel_entries: list[tuple[str, str]] = []
    for path, kind in entries:
        if kind == "secret":
            continue
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        rel_entries.append((rel.as_posix(), kind))
    rel_entries.sort()

    # APPLICATION_CODE is always present for the workload root.
    git_digest = (
        _canonical_digest({"commit": git.commit, "tree": git.tree, "ref": git.ref})
        if git.available and git.dirty is False
        else None
    )
    components.append(
        DiscoveredComponent(
            logical_name=_slugify(root.name),
            component_kind="APPLICATION_CODE",
            identity_locator={"type": "git", "path": "."},
            identity_assurance="IMMUTABLE_DIGEST" if git_digest else "UNKNOWN",
            content_digest=git_digest,
            unknown_reason=(
                None
                if git_digest
                else (
                    "worktree has tracked or untracked changes"
                    if git.dirty is True
                    else "no immutable git digest available for the repository"
                )
            ),
            artifact_refs=(
                [{"kind": "git_commit", "ref": git.commit, "digest": git_digest}]
                if git_digest
                else []
            ),
            provenance={"git": {"commit": git.commit, "tree": git.tree, "tags": list(git.tags)}},
        )
    )
    if not git.available:
        notes.append("git metadata unavailable; APPLICATION_CODE assurance=UNKNOWN")
    elif git.dirty is True:
        notes.append("git worktree is dirty; git-backed components assurance=UNKNOWN")
    elif git.dirty is None:
        notes.append("git worktree state unavailable; git-backed components assurance=UNKNOWN")

    # Recognizable sub-path patterns -> component kinds.  Only paths that
    # reliably indicate a component are emitted; nothing is fabricated.
    patterns: list[tuple[str, str, str]] = [
        ("prompts/", "PROMPT", "prompt"),
        ("prompt-templates/", "PROMPT", "prompt"),
        ("model-bindings/", "MODEL_BINDING", "model"),
        ("llm.yaml", "MODEL_BINDING", "model"),
        ("llm.yml", "MODEL_BINDING", "model"),
        ("retrievers/", "RETRIEVER", "retriever"),
        ("indexers/", "RETRIEVER", "retriever"),
        ("indexes/", "INDEX", "index"),
    ]
    for rel, _kind in rel_entries:
        lowered = rel.lower()
        for pattern, component_kind, label in patterns:
            if lowered == pattern.rstrip("/") or lowered.startswith(pattern):
                matched = rel.rstrip("/")
                path_obj = root / matched
                rel_path = matched if path_obj.is_dir() else str(Path(matched).parent)
                digest = _subtree_digest(root, rel_path, git)
                components.append(
                    DiscoveredComponent(
                        logical_name=f"{label}-{_slugify(rel_path)}",
                        component_kind=component_kind,
                        identity_locator={"type": "git", "path": rel_path},
                        identity_assurance="IMMUTABLE_DIGEST" if digest else "UNKNOWN",
                        content_digest=digest,
                        unknown_reason=None if digest else "no immutable digest for sub-path",
                        artifact_refs=(
                            [{"kind": "git_tree", "path": rel_path, "digest": digest}]
                            if digest
                            else []
                        ),
                        provenance={},
                    )
                )
                break

    # De-duplicate by (component_kind, logical_name).
    seen: set[tuple[str, str]] = set()
    deduped: list[DiscoveredComponent] = []
    for component in sorted(components, key=lambda item: (item.component_kind, item.logical_name)):
        key = (component.component_kind, component.logical_name)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(component)
    return deduped, notes


def discover(root: str | Path) -> DiscoveryResult:
    requested = Path(root).expanduser()
    if requested.is_symlink():
        raise DiscoveryError("DISCOVERY_ROOT_SYMLINK")
    try:
        resolved = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DiscoveryError("DISCOVERY_ROOT_UNREACHABLE") from exc
    if not resolved.is_dir():
        raise DiscoveryError("DISCOVERY_ROOT_NOT_DIRECTORY")

    # A git work tree (or subdirectory of one) is the supported discovery
    # target.  The scan is anchored at the work tree root so sub-path digests
    # are stable regardless of the requested subdirectory.
    anchor = resolved
    if not (anchor / ".git").exists():
        anchor = _find_git_root(resolved)
        if anchor is None:
            raise DiscoveryError("DISCOVERY_NOT_GIT_REPOSITORY")

    git = _git_metadata(anchor)
    project_type, test_commands = _project_type(anchor)
    entries = _walk(anchor)
    components, notes = _detect_components(anchor, git, entries)
    redacted = sorted({str(path) for path, kind in entries if kind == "secret"})
    return DiscoveryResult(
        root=str(anchor),
        git_revision=git.commit,
        git_tree=git.tree,
        git_ref=git.ref,
        project_type=project_type,
        test_commands=test_commands,
        components=components,
        redacted_paths=sorted(set(redacted)),
        notes=notes,
    )


def _find_git_root(path: Path) -> Path | None:
    current = path
    while True:
        if (current / ".git").exists():
            return current
        if current.parent == current:
            return None
        current = current.parent


def render_draft(result: DiscoveryResult) -> str:
    return json.dumps(
        result.to_manifest_draft(),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
