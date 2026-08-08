"""Allowlisted deterministic suite execution for GateReport evidence.

This module turns a real subprocess exit/result into a `SuiteResult` and persists the
captured command/output as an artifact. It deliberately has no arbitrary-command API:
the caller supplies an argv list assembled from repository-owned allowlists.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .digests import digest_of_bytes
from .gate import SuiteResult


@dataclass(frozen=True)
class CommandExecution:
    result: SuiteResult
    artifact_ref: dict[str, str]


class CommandSuiteRunner:
    def __init__(self, *, repo_root: Path, evidence_dir: Path, timeout_seconds: int = 300):
        self.repo_root = repo_root.resolve()
        self.evidence_dir = evidence_dir.resolve()
        self.timeout_seconds = max(1, int(timeout_seconds))

    def run(self, *, suite: str, kind: str, argv: Sequence[str], artifact_name: str) -> CommandExecution:
        """Run one repository-owned command and persist a reproducible JSON report."""

        if not argv:
            raise ValueError("suite argv must not be empty")
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = (self.evidence_dir / artifact_name).resolve()
        if self.evidence_dir not in artifact_path.parents:
            raise ValueError("artifact_name escapes evidence_dir")

        timed_out = False
        try:
            completed = subprocess.run(
                list(argv),
                cwd=self.repo_root,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            returncode = int(completed.returncode)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            returncode = 124
            stdout = _decode_timeout_stream(exc.stdout)
            stderr = _decode_timeout_stream(exc.stderr)

        n_passed = _pytest_count(stdout + "\n" + stderr, "passed")
        n_failed = (
            _pytest_count(stdout + "\n" + stderr, "failed")
            + _pytest_count(stdout + "\n" + stderr, "error")
            + _pytest_count(stdout + "\n" + stderr, "errors")
        )
        if timed_out:
            status = "error"
            n_failed = max(1, n_failed)
        elif returncode == 0 and n_passed > 0 and n_failed == 0:
            status = "passed"
        elif returncode != 0 and n_failed > 0:
            status = "failed"
        else:
            # Empty/unknown output is infrastructure error, never success.
            status = "error"
            n_failed = max(1, n_failed)

        artifact = {
            "schema_version": "0.1.0",
            "suite": suite,
            "kind": kind,
            "argv": list(argv),
            "cwd": str(self.repo_root),
            "timeout_seconds": self.timeout_seconds,
            "timed_out": timed_out,
            "returncode": returncode,
            "n_passed": n_passed,
            "n_failed": n_failed,
            "stdout": stdout,
            "stderr": stderr,
        }
        artifact_bytes = json.dumps(
            artifact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        artifact_path.write_bytes(artifact_bytes)
        digest = digest_of_bytes(artifact_bytes)
        uri = artifact_path.as_uri()
        return CommandExecution(
            result=SuiteResult(
                suite=suite,
                kind=kind,
                status=status,
                n_passed=n_passed,
                n_failed=n_failed,
                report_ref=uri,
                report_digest=digest,
            ),
            artifact_ref={"uri": uri, "digest": digest},
        )


def frozen_gate_suite_digest(repo_root: Path) -> str:
    """Digest the repository-owned gate code, tests, schemas, and Wilson vectors.

    The caller may label a run, but it cannot choose the digest that is bound into
    GateReport.  Any change to an executed test or a resource those tests consume
    changes this manifest digest.
    """

    root = repo_root.resolve()
    fixed = {
        root / "contracts/conformance/test_schemas.py",
        root / "contracts/conformance/test_wilson.py",
        root / "contracts/quality-api/openapi.yaml",
        root / "contracts/fixtures/probes-customer-service.yaml",
        root / "eval-harness/samples/b1_probe_responses.json",
        root / "eval-harness/tests/unit/test_gate.py",
        root / "eval-harness/tests/unit/test_probe_judge.py",
        root / "eval-harness/tests/unit/test_digests.py",
        root / "eval-harness/eval_harness/digests.py",
        root / "eval-harness/eval_harness/probe_judge.py",
        root / "eval-harness/eval_harness/probe_loader.py",
        root / "eval-harness/eval_harness/gate.py",
    }
    discovered = set((root / "contracts/schemas").glob("*.json"))
    discovered.update(path for path in (root / "contracts/wilson").rglob("*") if path.is_file())
    manifest = []
    for path in sorted(fixed | discovered):
        resolved = path.resolve()
        if root not in resolved.parents or not resolved.is_file():
            raise FileNotFoundError(f"gate suite resource is missing or outside repository: {path}")
        manifest.append(
            {
                "path": resolved.relative_to(root).as_posix(),
                "digest": digest_of_bytes(resolved.read_bytes()),
            }
        )
    body = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return digest_of_bytes(body)


def write_json_artifact(path: Path, payload: dict) -> dict[str, str]:
    """Persist canonical JSON evidence and return its URI/digest reference."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    path.write_bytes(body)
    return {"uri": path.as_uri(), "digest": digest_of_bytes(body)}


def _pytest_count(output: str, word: str) -> int:
    matches = re.findall(rf"(?<!\d)(\d+)\s+{re.escape(word)}\b", output)
    return sum(int(value) for value in matches)


def _decode_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
