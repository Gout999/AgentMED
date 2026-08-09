#!/usr/bin/env python3
"""Run the complete B1 loop against deployed live providers.

This command has no replay fallback and never manufactures or submits an
approval.  A separate human-approval adapter receives each exact WorkOrder/action
context on stdin, submits it with authority held outside this process, and
returns the persisted approval id on stdout.  Missing authority, credentials,
provider receipts, or any UNKNOWN state fails closed.
"""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.parse import unquote, urlparse
import uuid

import httpx


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eval-harness"))
sys.path.insert(0, str(REPO_ROOT / "control-plane"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from app.utils.jcs import canonical_json_digest, workorder_hash  # noqa: E402
from app.services.b1_fixture import (  # noqa: E402
    B1ComplaintFixture,
    B1FixtureError,
    load_b1_complaint_fixture,
)
from agentteams_attestation import (  # noqa: E402
    AgentTeamsAttestationError,
    public_key_id as agentteams_public_key_id,
    verify_receipt as verify_agentteams_receipt,
)
from eval_harness.probe_loader import frozen_digest, load_probe_set  # noqa: E402
from validate_b1_run import validate_b1_run  # noqa: E402


DISCOVERY = ["cs-001", "cs-002", "cs-003"]
HIDDEN = ["cs-004", "cs-005"]
CONTROLS = ["cs-013", "cs-014", "cs-015", "cs-016"]
ARMS = ("C", "RP", "RK", "RM", "G")
REQUIRED_DOMAIN_EVENTS = {
    "CASE_CREATED",
    "ATTRIBUTION_DECIDED",
    "GATE_COMPLETED",
    "RELEASE_STARTED",
    "RELEASE_PROMOTED",
    "NOTIFICATION_SENT",
    "CASE_ARCHIVED",
}
_SAFE_RUNTIME_ENV = {
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONPYCACHEPREFIX",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TMPDIR",
}
_PROVIDER_ENV = {
    "CASELOOP_QUALITY_API_BASE_URL",
    "CASELOOP_QUALITY_API_TIMEOUT_SECONDS",
    "CASELOOP_READ_TOKEN",
    "EXPERIMENT_CONFIDENCE",
    "EXPERIMENT_DELTA_MIN",
    "GATE_JUDGE_PASS_THRESHOLD",
    "GATE_PROVIDER_TIMEOUT_SECONDS",
    "JUDGE_MODEL",
    "LLM_RPM_LIMIT",
    "STEPFUN_API_KEY",
    "STEPFUN_BASE_URL",
    "STEPFUN_MODEL",
}
_OFFICIAL_STEPFUN_BASE_URL = "https://api.stepfun.com/step_plan/v1"
_OFFICIAL_FEISHU_BASE_URL = "https://open.feishu.cn"
_B1_AGENT_ROLES = (
    "quality-officer",
    "collector",
    "attributionist",
    "repairer",
    "gatekeeper",
    "case-officer",
)
_B1_SKILL_NAME = "caseloop-b1-loop"
_B1_SKILL_PATH = REPO_ROOT / "agents" / "skills" / _B1_SKILL_NAME / "SKILL.md"


class LiveRunError(RuntimeError):
    pass


def _child_env(extra_names: set[str] | frozenset[str] = frozenset()) -> dict[str, str]:
    """Build a purpose-scoped child environment with dotenv loading disabled."""

    names = _SAFE_RUNTIME_ENV | set(extra_names)
    child = {name: os.environ[name] for name in names if os.environ.get(name)}
    child["CASELOOP_DISABLE_DOTENV"] = "1"
    child["PYTHONUNBUFFERED"] = "1"
    return child


def _iso(value: datetime | None = None) -> str:
    return (value or datetime.now(timezone.utc)).isoformat()


def _feishu_message_created_at(value: Any) -> datetime:
    raw = str(value or "")
    if re.fullmatch(r"[1-9][0-9]{12}", raw) is None:
        raise LiveRunError("Feishu create_time is not a millisecond epoch timestamp")
    try:
        return datetime.fromtimestamp(int(raw) / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise LiveRunError("Feishu create_time is outside the supported range") from exc


def _provider_injected_at(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise LiveRunError("Quality injection receipt has no injected_at timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveRunError("Quality injection receipt has an invalid injected_at timestamp") from exc
    if parsed.tzinfo is None:
        raise LiveRunError("Quality injection receipt injected_at is timezone-naive")
    return parsed.astimezone(timezone.utc)


def _write(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _publish_verified_manifest(
    *,
    output_dir: Path,
    manifest: dict[str, Any],
    live_test_path: Path,
) -> tuple[Path, dict[str, Any]]:
    """Validate a private candidate before atomically exposing a passed manifest.

    A self-validation failure must not leave either a public passed manifest or
    a standalone passed live-test report behind.  The latter is rewritten with
    an explicit failed self-verification check before the exception escapes.
    """

    candidate_path = output_dir / ".b1-run-manifest.candidate.json"
    final_path = output_dir / "b1-run-manifest.json"
    _write(candidate_path, manifest)
    try:
        verification = validate_b1_run(candidate_path)
    except Exception:
        candidate_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        if live_test_path.is_file():
            try:
                live_report = json.loads(live_test_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - replace malformed success evidence too
                live_report = {}
            checks = [
                row
                for row in (live_report.get("checks") or [])
                if isinstance(row, dict)
                and row.get("check") != "evidence_bundle_self_verified"
            ]
            checks.append(
                {
                    "check": "evidence_bundle_self_verified",
                    "passed": False,
                    "evidence_refs": [],
                }
            )
            passed = sum(1 for row in checks if row.get("passed") is True)
            _write(
                live_test_path,
                {
                    **live_report,
                    "status": "failed",
                    "passed": passed,
                    "failed": len(checks) - passed,
                    "checks": checks,
                },
            )
        raise
    candidate_path.replace(final_path)
    if isinstance(verification, dict) and "manifest" in verification:
        verification = {**verification, "manifest": str(final_path)}
    return final_path, verification


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LiveRunError(f"invalid or missing artifact: {path}") from exc
    if not isinstance(value, dict):
        raise LiveRunError(f"artifact is not a JSON object: {path}")
    return value


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact_ref(path: Path) -> dict[str, str]:
    return {"uri": path.resolve().as_uri(), "digest": _file_digest(path)}


def _load_agent_artifact(
    ref: dict[str, Any], *, evidence_dir: Path, label: str
) -> tuple[Path, dict[str, Any]]:
    if not isinstance(ref, dict) or set(ref) != {"uri", "digest"}:
        raise LiveRunError(f"{label} reference is invalid")
    parsed = urlparse(str(ref.get("uri") or ""))
    if parsed.scheme != "file" or not parsed.path:
        raise LiveRunError(f"{label} must be exported as a file URI")
    path = Path(unquote(parsed.path)).resolve()
    try:
        path.relative_to(evidence_dir.resolve())
    except ValueError as exc:
        raise LiveRunError(f"{label} escapes the B1 evidence directory") from exc
    if not path.is_file() or _file_digest(path) != ref.get("digest"):
        raise LiveRunError(f"{label} is missing or changed after AgentTeams submission")
    if path.stat().st_size > 2_000_000:
        raise LiveRunError(f"{label} exceeds 2 MB")
    return path, _read(path)


def _verify_agentteams_receipt(receipt: dict[str, Any], *, phase: str) -> str:
    """Verify a receipt against the deployment-pinned AgentTeams exporter key."""

    try:
        return verify_agentteams_receipt(
            receipt, os.environ.get("CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY", "")
        )
    except AgentTeamsAttestationError as exc:
        raise LiveRunError(
            f"AgentTeams {phase} receipt attestation is invalid: {exc}"
        ) from exc


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise LiveRunError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _decode_inline_bytes(uri: str) -> bytes:
    header, separator, encoded = uri.partition(",")
    if separator != "," or header != "data:application/json;base64" or len(encoded) > 2_700_000:
        raise LiveRunError("inline JSON evidence URI is invalid or oversized")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise LiveRunError("inline JSON evidence is invalid") from exc
    if len(raw) > 2_000_000:
        raise LiveRunError("inline JSON evidence exceeds 2 MB")
    return raw


def _decode_inline_json(uri: str) -> dict[str, Any]:
    try:
        raw = _decode_inline_bytes(uri)
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise LiveRunError("inline JSON evidence is invalid") from exc
    if not isinstance(value, dict):
        raise LiveRunError("inline JSON evidence is not an object")
    return value


def _decode_gate_artifacts(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    for index, ref in enumerate(report.get("artifact_refs") or []):
        uri = str(ref.get("uri") or "")
        raw = _decode_inline_bytes(uri)
        if "sha256:" + hashlib.sha256(raw).hexdigest() != ref.get("digest"):
            raise LiveRunError(f"Gate artifact {index} digest mismatch")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LiveRunError(f"Gate artifact {index} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise LiveRunError(f"Gate artifact {index} is not an object")
        artifacts[uri] = value
    return artifacts


def _pytest_counts(output: str) -> tuple[int, int]:
    return (
        sum(int(value) for value in re.findall(r"(\d+) passed", output)),
        sum(int(value) for value in re.findall(r"(\d+) failed", output)),
    )


def _suite_semantics(value: dict[str, Any]) -> tuple[str, int, int]:
    exit_code = value.get("exit_code")
    output = value.get("output")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or not isinstance(output, str):
        raise LiveRunError("Gate suite artifact has no process exit code/output")
    passed, failed = _pytest_counts(output)
    if exit_code != 0 and failed == 0:
        failed = 1
    status = "passed" if exit_code == 0 and passed > 0 and failed == 0 else "failed"
    return status, passed, failed


def _gate_suite(
    report: dict[str, Any], artifacts: dict[str, dict[str, Any]], kind: str
) -> tuple[dict[str, Any], tuple[str, int, int]]:
    suites = report.get("deterministic_tests", {}).get("suites") or []
    suite = next((item for item in suites if item.get("kind") == kind), None)
    if not isinstance(suite, dict) or suite.get("report_ref") not in artifacts:
        raise LiveRunError(f"Gate {kind} suite artifact is missing")
    semantics = _suite_semantics(artifacts[suite["report_ref"]])
    if semantics != (suite.get("status"), suite.get("n_passed"), suite.get("n_failed")):
        raise LiveRunError(f"Gate {kind} suite summary differs from executed report")
    return artifacts[suite["report_ref"]], semantics


def _gate_candidate(
    report: dict[str, Any], artifacts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    suites = report.get("live_provider_e2e", {}).get("suites") or []
    if len(suites) != 1 or suites[0].get("report_ref") not in artifacts:
        raise LiveRunError("Gate live candidate artifact is missing")
    candidate = artifacts[suites[0]["report_ref"]]
    if not isinstance(candidate.get("responses"), list):
        raise LiveRunError("Gate live candidate response evidence is invalid")
    return candidate


def _exact_quality_log(api: "API", request_id: str) -> dict[str, Any]:
    page = api.get(f"/v2/logs?request_id={request_id}&limit=2", quality=True)
    exact = [
        item
        for item in page.get("items", [])
        if isinstance(item, dict) and item.get("request_id") == request_id
    ]
    if len(exact) != 1:
        raise LiveRunError(
            f"expected exactly one authoritative Quality log for request_id={request_id}"
        )
    return exact[0]


def _provider_logs_for_outputs(
    api: "API", bundle: dict[str, Any], gate_candidates: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    request_versionsets: list[tuple[str, str]] = []
    for cell in (bundle.get("cells") or {}).values():
        for trial in cell.get("results") or []:
            output_ref = trial.get("output_ref")
            raw = _decode_inline_json(output_ref) if isinstance(output_ref, str) else {}
            request_id = raw.get("request_id")
            versionset_id = raw.get("versionset_id")
            if (
                not isinstance(request_id, str)
                or not request_id
                or not isinstance(versionset_id, str)
                or not versionset_id
            ):
                raise LiveRunError("live attribution output has no provider request id")
            request_versionsets.append((request_id, versionset_id))
    for candidate in gate_candidates:
        for response in candidate.get("responses") or []:
            request_id = response.get("request_id")
            versionset_id = response.get("versionset_id")
            if (
                not isinstance(request_id, str)
                or not request_id
                or not isinstance(versionset_id, str)
                or not versionset_id
            ):
                raise LiveRunError("live Gate response has no provider request id")
            request_versionsets.append((request_id, versionset_id))
    request_ids = [request_id for request_id, _ in request_versionsets]
    if len(request_ids) != len(set(request_ids)):
        raise LiveRunError("provider request id was reused across B1 evidence")
    pages: dict[str, list[dict[str, Any]]] = {}
    for versionset_id in sorted({versionset_id for _, versionset_id in request_versionsets}):
        page = api.get(f"/v2/logs?versionset_id={versionset_id}&limit=500", quality=True)
        items = [item for item in page.get("items", []) if isinstance(item, dict)]
        if page.get("next_cursor"):
            raise LiveRunError(
                f"Quality provider logs for {versionset_id} exceed the evidence page limit"
            )
        pages[versionset_id] = items
    logs: dict[str, dict[str, Any]] = {}
    for request_id, versionset_id in request_versionsets:
        exact = [item for item in pages[versionset_id] if item.get("request_id") == request_id]
        if len(exact) != 1:
            raise LiveRunError(
                f"expected exactly one authoritative Quality log for request_id={request_id}"
            )
        if exact[0].get("provider_origin") != _OFFICIAL_STEPFUN_BASE_URL:
            raise LiveRunError(
                f"Quality provider log {request_id} did not use the official StepFun origin"
            )
        logs[request_id] = exact[0]
    return logs


class API:
    """Small receipt-recording HTTP client with separated authorities."""

    def __init__(
        self,
        *,
        control_base: str,
        quality_base: str,
        control_token: str,
        gate_token: str,
        read_token: str,
        evidence_dir: Path,
    ) -> None:
        self.control_base = control_base.rstrip("/")
        self.quality_base = quality_base.rstrip("/")
        self.control_token = control_token
        self.gate_token = gate_token
        self.read_token = read_token
        self.client = httpx.Client(timeout=95.0, trust_env=False)
        self.receipts: list[dict[str, Any]] = []
        self.receipt_path = evidence_dir / "http-receipts.json"

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        authority: str = "control",
        quality: bool = False,
        allow_error: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        if quality:
            base = self.quality_base
            token = self.read_token
        else:
            base = self.control_base
            token = self.gate_token if authority == "gate" else self.control_token
        try:
            response = self.client.request(
                method,
                base + path,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise LiveRunError(f"{method} {path} unavailable: {exc}") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = {"raw": response.text[:1000]}
        if not isinstance(payload, dict):
            payload = {"value": payload}
        self.receipts.append(
            {
                "ts": _iso(),
                "method": method,
                "path": path,
                "authority": "quality-read" if quality else authority,
                "status_code": response.status_code,
                "response": payload,
            }
        )
        _write(self.receipt_path, self.receipts)
        if response.status_code >= 400 and not allow_error:
            raise LiveRunError(
                f"{method} {path} failed HTTP {response.status_code}: "
                f"{json.dumps(payload, ensure_ascii=False)[:800]}"
            )
        return response.status_code, payload

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        authority: str = "control",
        quality: bool = False,
    ) -> dict[str, Any]:
        return self.request(
            "POST", path, body=body, authority=authority, quality=quality
        )[1]

    def get(
        self, path: str, *, authority: str = "control", quality: bool = False
    ) -> dict[str, Any]:
        return self.request("GET", path, authority=authority, quality=quality)[1]


def _run_provider(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    heartbeat: Callable[[], None] | None = None,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    started = time.monotonic()
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=_child_env(_PROVIDER_ENV),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = ""
    while True:
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            process.kill()
            output = process.communicate()[0] or output
            result = {
                "command": command,
                "cwd": str(cwd),
                "exit_code": 124,
                "output": output,
                "error": "timeout",
            }
            _write(log_path, result)
            return result
        try:
            output, _ = process.communicate(timeout=min(15.0, remaining))
            break
        except subprocess.TimeoutExpired:
            if heartbeat is not None:
                try:
                    heartbeat()
                except Exception:
                    process.kill()
                    process.communicate()
                    raise
    result = {
        "command": command,
        "cwd": str(cwd),
        "exit_code": int(process.returncode or 0),
        "output": output,
    }
    _write(log_path, result)
    return result


def _run_child_command(
    command: list[str],
    *,
    input_text: str,
    env: dict[str, str],
    heartbeat: Callable[[], None] | None = None,
    timeout_seconds: float = 600,
    poll_seconds: float = 15.0,
) -> subprocess.CompletedProcess[str]:
    """Run an evidence adapter while renewing any lease held by the caller."""

    started = time.monotonic()
    process = subprocess.Popen(
        command,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    first_communicate = True
    while True:
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            process.kill()
            stdout, stderr = process.communicate()
            raise LiveRunError(
                f"child command timed out after {timeout_seconds:g}s: {command[0]}; "
                f"stderr={stderr[-500:]} stdout={stdout[-500:]}"
            )
        try:
            stdout, stderr = process.communicate(
                input=input_text if first_communicate else None,
                timeout=min(poll_seconds, remaining),
            )
            return subprocess.CompletedProcess(
                command, process.returncode, stdout=stdout, stderr=stderr
            )
        except subprocess.TimeoutExpired:
            first_communicate = False
            if heartbeat is not None:
                try:
                    heartbeat()
                except Exception:
                    process.kill()
                    process.communicate()
                    raise


def _feishu_message_id_from_command(
    command: str,
    *,
    injection_operation_id: str,
    injection_receipt: dict[str, Any],
    fixture: B1ComplaintFixture,
    evidence_dir: Path,
) -> tuple[str, dict[str, Any]]:
    """Acquire a newly created complaint only after B1 is serving.

    The external command may wait for a human/live webhook, but it receives no
    Control Plane, Quality, StepFun, or Feishu credentials from this process.
    Its message id is only a locator: the Control Plane still fetches and
    validates the authoritative Feishu message, digest, and provider time.
    """

    argv = shlex.split(command)
    if not argv:
        raise LiveRunError("Feishu post-injection message command is empty")
    requested_at = _iso()
    request = {
        "schema_version": "0.1.0",
        "phase": "await-post-injection-complaint",
        "provider": "feishu",
        "fixture_ref": fixture.repository_ref,
        "fixture_text_digest": fixture.text_digest,
        "injection_operation_id": injection_operation_id,
        "not_before": injection_receipt.get("injected_at"),
        "instruction": (
            "Wait for a newly created Feishu message matching the frozen fixture, "
            "then return only schema_version, provider, and message_id as JSON."
        ),
    }
    try:
        timeout_seconds = float(
            os.environ.get("CASELOOP_B1_FEISHU_MESSAGE_TIMEOUT_SECONDS", "600")
        )
    except ValueError as exc:
        raise LiveRunError("CASELOOP_B1_FEISHU_MESSAGE_TIMEOUT_SECONDS is invalid") from exc
    if not 1 <= timeout_seconds <= 3600:
        raise LiveRunError(
            "CASELOOP_B1_FEISHU_MESSAGE_TIMEOUT_SECONDS must be between 1 and 3600"
        )
    completed = _run_child_command(
        argv,
        input_text=json.dumps(request, ensure_ascii=False),
        env=_child_env(),
        timeout_seconds=timeout_seconds,
    )
    completed_at = _iso()
    try:
        receipt = json.loads(completed.stdout)
    except ValueError as exc:
        raise LiveRunError("Feishu post-injection message command returned invalid JSON") from exc
    message_id = receipt.get("message_id") if isinstance(receipt, dict) else None
    if (
        completed.returncode != 0
        or not isinstance(receipt, dict)
        or set(receipt) != {"schema_version", "provider", "message_id"}
        or receipt.get("schema_version") != "0.1.0"
        or receipt.get("provider") != "feishu"
        or not isinstance(message_id, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{8,256}", message_id) is None
    ):
        raise LiveRunError(
            "Feishu post-injection message command failed or returned an invalid receipt"
        )
    evidence = {
        "schema_version": "0.1.0",
        "adapter": "external-post-injection-feishu-message-command",
        "requested_at": requested_at,
        "completed_at": completed_at,
        "request": request,
        "command": {
            "executable": argv[0],
            "argv_digest": canonical_json_digest(argv),
            "exit_code": completed.returncode,
            "stderr": completed.stderr,
        },
        "receipt": receipt,
    }
    _write(evidence_dir / "feishu-message-acquisition.json", evidence)
    return message_id, evidence


def _approval_from_command(
    command: str,
    *,
    api: API,
    phase: str,
    workorder: dict[str, Any],
    evidence_dir: Path,
    authorization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = {
        "schema_version": "0.1.0",
        "phase": phase,
        "requested_at": _iso(),
        "workorder_id": workorder["workorder_id"],
        "workorder_hash": workorder["hash"],
        "workorder_nonce": workorder["nonce"],
        "workorder_expiry": workorder["expiry"],
        "authorization": authorization,
        "instruction": (
            "After a human approves this exact context, submit one fresh ApprovalGrant to "
            "the Control Plane using authority held outside the B1 runner, then return JSON "
            "containing only its approval_id. Do not reuse a nonce or alter authorization."
        ),
    }
    _write(evidence_dir / f"approval-context-{phase}.json", context)
    argv = shlex.split(command)
    completed = subprocess.run(
        argv,
        input=json.dumps(context, ensure_ascii=False),
        env=_child_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
        check=False,
    )
    _write(
        evidence_dir / f"approval-command-{phase}.json",
        {
            "executable": argv[0],
            "argv_digest": canonical_json_digest(argv),
            "exit_code": completed.returncode,
            "stderr": completed.stderr,
        },
    )
    if completed.returncode != 0:
        raise LiveRunError(f"human approval command failed for {phase}")
    try:
        command_receipt = json.loads(completed.stdout)
    except ValueError as exc:
        raise LiveRunError(f"human approval command returned invalid JSON for {phase}") from exc
    if not isinstance(command_receipt, dict):
        raise LiveRunError(f"human approval command returned non-object for {phase}")
    approval_id = command_receipt.get("approval_id")
    if not isinstance(approval_id, str) or not approval_id:
        raise LiveRunError(f"human approval command returned no approval_id for {phase}")
    grant = api.get(f"/v1/approvals/{approval_id}")
    approver = grant.get("approver") or {}
    if (
        grant.get("decision") != "approved"
        or grant.get("nonce_consumed") is not False
        or approver.get("type") != "human"
        or not approver.get("identity")
        or grant.get("workorder_id") != workorder["workorder_id"]
        or grant.get("workorder_hash") != workorder["hash"]
        or grant.get("expiry") != workorder["expiry"]
    ):
        raise LiveRunError(f"human ApprovalGrant binding is invalid for {phase}")
    if authorization is None:
        if grant.get("nonce") != workorder["nonce"] or grant.get("authorization") is not None:
            raise LiveRunError("initial ApprovalGrant nonce/authorization is invalid")
    else:
        if grant.get("authorization") != authorization or grant.get("nonce") == workorder["nonce"]:
            raise LiveRunError(f"action ApprovalGrant binding is invalid for {phase}")
        try:
            uuid.UUID(str(grant.get("nonce")))
        except (ValueError, TypeError, AttributeError) as exc:
            raise LiveRunError(f"action ApprovalGrant nonce is invalid for {phase}") from exc
    _write(evidence_dir / f"approval-grant-{phase}.json", grant)
    return grant


def _agent_trace_from_command(
    command: str,
    *,
    phase: str,
    context: dict[str, Any],
    evidence_dir: Path,
    start_receipt: dict[str, Any] | None = None,
    expected_sources: dict[str, list[str]] | None = None,
    expected_repairer_workorder_ref: dict[str, str] | None = None,
    expected_phase_receipts: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Call an independently credentialed AgentTeams evidence adapter.

    The B1 runner never receives AgentTeams/Matrix management credentials.  The
    adapter must dispatch the task before domain work and later return the exact
    v1.2.1 taskflow, Matrix, and repository-skill receipts.  A direct runner
    trace can therefore never be relabelled as AgentTeams execution.
    """

    if phase not in {"start", "complete"}:
        raise LiveRunError(f"unsupported AgentTeams evidence phase {phase!r}")
    if not _B1_SKILL_PATH.is_file():
        raise LiveRunError(f"repository B1 AgentTeams skill is missing: {_B1_SKILL_PATH}")
    argv = shlex.split(command)
    if not argv:
        raise LiveRunError("AgentTeams evidence command is empty")
    request = {
        "schema_version": "0.1.0",
        "phase": phase,
        "platform": "AgentTeams",
        "platform_version": "v1.2.1",
        "team": "caseloop-team",
        "required_roles": list(_B1_AGENT_ROLES),
        "required_skill": {
            "name": _B1_SKILL_NAME,
            "digest": _file_digest(_B1_SKILL_PATH),
        },
        "evidence_export_dir": str(evidence_dir.resolve()),
        "context": context,
        **(
            {
                "session_id": start_receipt["session_id"],
                "room_id": start_receipt["room_id"],
                "expected_sources": expected_sources,
                "expected_products": {
                    role: [item["artifact_ref"] for item in receipts]
                    for role, receipts in (expected_phase_receipts or {}).items()
                },
            }
            if phase == "complete" and start_receipt is not None
            else {}
        ),
    }
    completed = subprocess.run(
        argv,
        input=json.dumps(request, ensure_ascii=False),
        env=_child_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=600,
        check=False,
    )
    _write(
        evidence_dir / f"agentteams-command-{phase}.json",
        {
            "executable": argv[0],
            "argv_digest": canonical_json_digest(argv),
            "request_digest": canonical_json_digest(request),
            "exit_code": completed.returncode,
            "stderr": completed.stderr,
        },
    )
    if completed.returncode != 0:
        raise LiveRunError(f"AgentTeams evidence command failed during {phase}")
    try:
        receipt = json.loads(completed.stdout)
    except ValueError as exc:
        raise LiveRunError(f"AgentTeams evidence command returned invalid JSON during {phase}") from exc
    if not isinstance(receipt, dict):
        raise LiveRunError(f"AgentTeams evidence command returned non-object during {phase}")
    _verify_agentteams_receipt(receipt, phase=phase)

    common = {
        "schema_version",
        "phase",
        "platform",
        "platform_version",
        "team",
        "session_id",
        "room_id",
        "skill",
        "attestation",
    }
    phase_fields = {"dispatch_event_id", "workers"} if phase == "start" else {
        "dispatch_event_id",
        "completion_event_id",
        "runs",
    }
    if set(receipt) != common | phase_fields:
        raise LiveRunError(f"AgentTeams {phase} receipt has an unexpected schema")
    if (
        receipt.get("schema_version") != "0.1.0"
        or receipt.get("phase") != phase
        or receipt.get("platform") != "AgentTeams"
        or receipt.get("platform_version") != "v1.2.1"
        or receipt.get("team") != "caseloop-team"
        or not isinstance(receipt.get("session_id"), str)
        or len(receipt["session_id"]) < 8
        or re.fullmatch(r"![^\s:]+:[^\s]+", str(receipt.get("room_id") or "")) is None
        or re.fullmatch(r"\$[^\s]+", str(receipt.get("dispatch_event_id") or "")) is None
        or receipt.get("skill")
        != {"name": _B1_SKILL_NAME, "digest": _file_digest(_B1_SKILL_PATH)}
    ):
        raise LiveRunError(f"AgentTeams {phase} receipt identity/skill binding is invalid")

    if phase == "start":
        workers = receipt.get("workers")
        if (
            not isinstance(workers, list)
            or len(workers) != len(_B1_AGENT_ROLES)
            or set(workers) != set(_B1_AGENT_ROLES)
        ):
            raise LiveRunError("AgentTeams start receipt does not bind the fixed six-worker pool")
    else:
        if (
            start_receipt is None
            or expected_sources is None
            or expected_phase_receipts is None
            or set(expected_phase_receipts) != set(_B1_AGENT_ROLES)
            or any(not rows for rows in expected_phase_receipts.values())
        ):
            raise LiveRunError("AgentTeams completion validation lacks its start/source binding")
        if (
            receipt.get("session_id") != start_receipt.get("session_id")
            or receipt.get("room_id") != start_receipt.get("room_id")
            or receipt.get("dispatch_event_id") != start_receipt.get("dispatch_event_id")
            or re.fullmatch(r"\$[^\s]+", str(receipt.get("completion_event_id") or "")) is None
        ):
            raise LiveRunError("AgentTeams completion receipt is detached from its dispatch")
        runs = receipt.get("runs")
        if not isinstance(runs, list) or len(runs) != len(_B1_AGENT_ROLES):
            raise LiveRunError("AgentTeams completion receipt does not contain six worker runs")
        indexed = {
            row.get("role"): row for row in runs if isinstance(row, dict)
        }
        if len(indexed) != len(runs) or set(indexed) != set(_B1_AGENT_ROLES):
            raise LiveRunError("AgentTeams completion receipt duplicates or omits a worker role")
        for field in ("task_id", "ack_receipt_id", "submit_receipt_id"):
            identities = [row.get(field) for row in indexed.values()]
            if (
                any(not isinstance(value, str) or not value for value in identities)
                or len(set(identities)) != len(_B1_AGENT_ROLES)
            ):
                raise LiveRunError(
                    f"AgentTeams completion receipt reuses a cross-role {field}"
                )
        expected_run_fields = {
            "role",
            "task_id",
            "ack_receipt_id",
            "submit_receipt_id",
            "matrix_event_ids",
            "skill",
            "source_ids",
            "artifact_ref",
        }
        for role, row in indexed.items():
            matrix_event_ids = row.get("matrix_event_ids")
            source_ids = row.get("source_ids")
            artifact_ref = row.get("artifact_ref")
            if (
                set(row) != expected_run_fields
                or not isinstance(row.get("task_id"), str)
                or not row["task_id"]
                or not isinstance(row.get("ack_receipt_id"), str)
                or not row["ack_receipt_id"]
                or not isinstance(row.get("submit_receipt_id"), str)
                or not row["submit_receipt_id"]
                or not isinstance(matrix_event_ids, list)
                or not matrix_event_ids
                or len(set(matrix_event_ids)) != len(matrix_event_ids)
                or any(re.fullmatch(r"\$[^\s]+", str(value)) is None for value in matrix_event_ids)
                or row.get("skill") != receipt["skill"]
                or not isinstance(source_ids, list)
                or not source_ids
                or len(set(source_ids)) != len(source_ids)
                or set(source_ids) != set(expected_sources.get(role) or [])
            ):
                raise LiveRunError(f"AgentTeams worker receipt is invalid for {role}")
            _, handoff = _load_agent_artifact(
                artifact_ref,
                evidence_dir=evidence_dir,
                label=f"AgentTeams {role} handoff",
            )
            if (
                handoff.get("schema_version") != "0.1.0"
                or handoff.get("kind") != "task-handoff"
                or handoff.get("role") != role
                or handoff.get("task_id") != row["task_id"]
                or handoff.get("session_id") != receipt["session_id"]
                or handoff.get("case_id") != context.get("case_id")
                or set(handoff.get("source_ids") or []) != set(source_ids)
                or not isinstance(handoff.get("payload"), dict)
            ):
                raise LiveRunError(f"AgentTeams {role} handoff artifact binding is invalid")
            phase_receipts = expected_phase_receipts[role]
            for item in phase_receipts:
                _verify_agentteams_receipt(
                    item, phase=f"{role} pre-action product"
                )
            expected_product_refs = [item["artifact_ref"] for item in phase_receipts]
            handoff_product_refs = (handoff.get("payload") or {}).get("product_refs")
            if (
                any(
                    item.get("role") != role
                    or item.get("task_id") != row["task_id"]
                    or item.get("ack_receipt_id") != row["ack_receipt_id"]
                    or item.get("session_id") != receipt["session_id"]
                    or item.get("room_id") != receipt["room_id"]
                    or item.get("skill") != receipt["skill"]
                    or not set(item.get("matrix_event_ids") or []) <= set(matrix_event_ids)
                    for item in phase_receipts
                )
                or not isinstance(handoff_product_refs, list)
                or sorted(handoff_product_refs, key=lambda item: item["uri"])
                != sorted(expected_product_refs, key=lambda item: item["uri"])
            ):
                raise LiveRunError(
                    f"AgentTeams {role} handoff is detached from its pre-action products"
                )
            if role == "repairer" and (
                expected_repairer_workorder_ref is None
                or (handoff.get("payload") or {}).get("workorder_ref")
                != expected_repairer_workorder_ref
                or expected_repairer_workorder_ref not in expected_product_refs
            ):
                raise LiveRunError(
                    "AgentTeams repairer handoff does not bind its immutable WorkOrder artifact"
                )

    _write(evidence_dir / f"agentteams-receipt-{phase}.json", receipt)
    return receipt


def _agent_workorder_from_command(
    command: str,
    *,
    context: dict[str, Any],
    evidence_dir: Path,
    start_receipt: dict[str, Any],
    heartbeat: Callable[[], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Obtain the WorkOrder from the assigned AgentTeams repairer task.

    This process has no AgentTeams/Matrix credentials and does not author the
    WorkOrder.  It verifies the exported task artifact and then lets the
    deterministic Control Plane validate/freeze it.
    """

    argv = shlex.split(command)
    if not argv:
        raise LiveRunError("AgentTeams evidence command is empty")
    request = {
        "schema_version": "0.1.0",
        "phase": "workorder",
        "platform": "AgentTeams",
        "platform_version": "v1.2.1",
        "team": "caseloop-team",
        "required_role": "repairer",
        "required_skill": {
            "name": _B1_SKILL_NAME,
            "digest": _file_digest(_B1_SKILL_PATH),
        },
        "session_id": start_receipt["session_id"],
        "room_id": start_receipt["room_id"],
        "evidence_export_dir": str(evidence_dir.resolve()),
        "context": context,
    }
    completed = _run_child_command(
        argv,
        input_text=json.dumps(request, ensure_ascii=False),
        env=_child_env(),
        heartbeat=heartbeat,
    )
    _write(
        evidence_dir / "agentteams-command-workorder.json",
        {
            "executable": argv[0],
            "argv_digest": canonical_json_digest(argv),
            "request_digest": canonical_json_digest(request),
            "exit_code": completed.returncode,
            "stderr": completed.stderr,
        },
    )
    if completed.returncode != 0:
        raise LiveRunError("AgentTeams repairer WorkOrder command failed")
    try:
        receipt = json.loads(completed.stdout)
    except ValueError as exc:
        raise LiveRunError("AgentTeams repairer WorkOrder command returned invalid JSON") from exc
    if not isinstance(receipt, dict):
        raise LiveRunError("AgentTeams repairer WorkOrder command returned non-object")
    _verify_agentteams_receipt(receipt, phase="workorder")
    expected_fields = {
        "schema_version",
        "phase",
        "platform",
        "platform_version",
        "team",
        "session_id",
        "room_id",
        "role",
        "task_id",
        "ack_receipt_id",
        "matrix_event_ids",
        "skill",
        "artifact_ref",
        "attestation",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_fields:
        raise LiveRunError("AgentTeams repairer WorkOrder receipt schema is invalid")
    matrix_event_ids = receipt.get("matrix_event_ids")
    if (
        receipt.get("schema_version") != "0.1.0"
        or receipt.get("phase") != "workorder"
        or receipt.get("platform") != "AgentTeams"
        or receipt.get("platform_version") != "v1.2.1"
        or receipt.get("team") != "caseloop-team"
        or receipt.get("session_id") != start_receipt.get("session_id")
        or receipt.get("room_id") != start_receipt.get("room_id")
        or receipt.get("role") != "repairer"
        or not isinstance(receipt.get("task_id"), str)
        or not receipt["task_id"]
        or not isinstance(receipt.get("ack_receipt_id"), str)
        or not receipt["ack_receipt_id"]
        or not isinstance(matrix_event_ids, list)
        or not matrix_event_ids
        or any(re.fullmatch(r"\$[^\s]+", str(value)) is None for value in matrix_event_ids)
        or receipt.get("skill") != request["required_skill"]
    ):
        raise LiveRunError("AgentTeams repairer WorkOrder receipt binding is invalid")
    _, artifact = _load_agent_artifact(
        receipt["artifact_ref"],
        evidence_dir=evidence_dir,
        label="AgentTeams repairer WorkOrder",
    )
    workorder = artifact.get("workorder")
    if (
        artifact.get("schema_version") != "0.1.0"
        or artifact.get("kind") != "immutable-workorder"
        or artifact.get("role") != "repairer"
        or artifact.get("task_id") != receipt["task_id"]
        or artifact.get("session_id") != receipt["session_id"]
        or artifact.get("case_id") != context.get("case_id")
        or not isinstance(workorder, dict)
    ):
        raise LiveRunError("AgentTeams repairer WorkOrder artifact binding is invalid")
    expected = context["expected_workorder_binding"]
    if any(workorder.get(key) != value for key, value in expected.items()):
        raise LiveRunError("AgentTeams repairer WorkOrder differs from authoritative bindings")
    diff = workorder.get("diff") or {}
    diff_content = diff.get("content")
    if (
        not isinstance(diff_content, str)
        or not diff_content
        or diff.get("format") != "unified_diff"
        or diff.get("digest")
        != "sha256:" + hashlib.sha256(diff_content.encode("utf-8")).hexdigest()
        or workorder.get("hash_rule") != "jcs-rfc8785+sha256"
        or workorder.get("hash") != workorder_hash(workorder)
    ):
        raise LiveRunError("AgentTeams repairer WorkOrder hash/diff is invalid")
    try:
        uuid.UUID(str(workorder.get("nonce")))
        created_at = datetime.fromisoformat(str(workorder.get("created_at")))
        expiry = datetime.fromisoformat(str(workorder.get("expiry")))
    except (ValueError, TypeError) as exc:
        raise LiveRunError("AgentTeams repairer WorkOrder nonce/time binding is invalid") from exc
    if created_at.tzinfo is None or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
        raise LiveRunError("AgentTeams repairer WorkOrder is expired or timezone-naive")
    _write(evidence_dir / "agentteams-receipt-workorder.json", receipt)
    return workorder, receipt


def _agent_phase_product_from_command(
    command: str,
    *,
    phase: str,
    role: str,
    artifact_kind: str,
    context: dict[str, Any],
    evidence_dir: Path,
    start_receipt: dict[str, Any],
    heartbeat: Callable[[], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one pre-action AgentTeams task phase and verify its immutable product."""

    if role not in _B1_AGENT_ROLES or role == "repairer" and phase == "workorder":
        raise LiveRunError("invalid generic AgentTeams role/phase")
    argv = shlex.split(command)
    if not argv:
        raise LiveRunError("AgentTeams evidence command is empty")
    request = {
        "schema_version": "0.1.0",
        "phase": phase,
        "platform": "AgentTeams",
        "platform_version": "v1.2.1",
        "team": "caseloop-team",
        "required_role": role,
        "required_artifact_kind": artifact_kind,
        "required_skill": {
            "name": _B1_SKILL_NAME,
            "digest": _file_digest(_B1_SKILL_PATH),
        },
        "session_id": start_receipt["session_id"],
        "room_id": start_receipt["room_id"],
        "evidence_export_dir": str(evidence_dir.resolve()),
        "context": context,
    }
    completed = _run_child_command(
        argv,
        input_text=json.dumps(request, ensure_ascii=False),
        env=_child_env(),
        heartbeat=heartbeat,
    )
    _write(
        evidence_dir / f"agentteams-command-{phase}.json",
        {
            "executable": argv[0],
            "argv_digest": canonical_json_digest(argv),
            "request_digest": canonical_json_digest(request),
            "exit_code": completed.returncode,
            "stderr": completed.stderr,
        },
    )
    if completed.returncode != 0:
        raise LiveRunError(f"AgentTeams {role} phase {phase} failed")
    try:
        receipt = json.loads(completed.stdout)
    except ValueError as exc:
        raise LiveRunError(f"AgentTeams {role} phase {phase} returned invalid JSON") from exc
    if not isinstance(receipt, dict):
        raise LiveRunError(f"AgentTeams {role} phase {phase} returned non-object")
    _verify_agentteams_receipt(receipt, phase=phase)
    expected_fields = {
        "schema_version",
        "phase",
        "platform",
        "platform_version",
        "team",
        "session_id",
        "room_id",
        "role",
        "task_id",
        "ack_receipt_id",
        "matrix_event_ids",
        "skill",
        "artifact_ref",
        "attestation",
    }
    matrix_event_ids = receipt.get("matrix_event_ids") if isinstance(receipt, dict) else None
    if (
        not isinstance(receipt, dict)
        or set(receipt) != expected_fields
        or receipt.get("schema_version") != "0.1.0"
        or receipt.get("phase") != phase
        or receipt.get("platform") != "AgentTeams"
        or receipt.get("platform_version") != "v1.2.1"
        or receipt.get("team") != "caseloop-team"
        or receipt.get("session_id") != start_receipt.get("session_id")
        or receipt.get("room_id") != start_receipt.get("room_id")
        or receipt.get("role") != role
        or not isinstance(receipt.get("task_id"), str)
        or not receipt["task_id"]
        or not isinstance(receipt.get("ack_receipt_id"), str)
        or not receipt["ack_receipt_id"]
        or not isinstance(matrix_event_ids, list)
        or not matrix_event_ids
        or any(re.fullmatch(r"\$[^\s]+", str(value)) is None for value in matrix_event_ids)
        or receipt.get("skill") != request["required_skill"]
    ):
        raise LiveRunError(f"AgentTeams {role} phase {phase} receipt binding is invalid")
    _, artifact = _load_agent_artifact(
        receipt["artifact_ref"],
        evidence_dir=evidence_dir,
        label=f"AgentTeams {role} {phase} product",
    )
    payload = artifact.get("payload")
    if (
        artifact.get("schema_version") != "0.1.0"
        or artifact.get("kind") != artifact_kind
        or artifact.get("role") != role
        or artifact.get("task_id") != receipt["task_id"]
        or artifact.get("session_id") != receipt["session_id"]
        or artifact.get("case_id") != context.get("case_id")
        or not isinstance(payload, dict)
    ):
        raise LiveRunError(f"AgentTeams {role} phase {phase} product binding is invalid")
    _write(evidence_dir / f"agentteams-receipt-{phase}.json", receipt)
    return payload, receipt


def _gate_registration(
    report: dict[str, Any],
    *,
    workorder_id: str,
    target: dict[str, Any],
    dataset_id: str,
    dataset_version: str,
    case_id: str,
) -> tuple[dict[str, Any], str]:
    report_hash = canonical_json_digest(report, prefix=False)
    return (
        {
            "report": report,
            "report_hash": report_hash,
            "workorder_id": workorder_id,
            "target_versionset_id": target["versionset_id"],
            "target_revision": target["revision"],
            "dataset_id": dataset_id,
            "dataset_version": dataset_version,
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
            "correlation_id": case_id,
        },
        report_hash,
    )


def _canary_routing_key(run_id: str, percent: int) -> tuple[str, int]:
    if not isinstance(percent, int) or isinstance(percent, bool) or not 1 <= percent <= 100:
        raise LiveRunError("canary percent is invalid for routed verification")
    for index in range(10_000):
        key = f"b1-canary:{run_id}:{index}"
        bucket = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big") % 100
        if bucket < percent:
            return key, bucket
    raise LiveRunError("unable to derive a deterministic canary routing key")


def _inline_live_probe_outputs(bundle: dict[str, Any]) -> dict[str, Any]:
    """Replace runner-local files with process-independent immutable JSON URIs."""

    for cell_name, cell in (bundle.get("cells") or {}).items():
        for trial in cell.get("results") or []:
            output_ref = trial.get("output_ref")
            if not isinstance(output_ref, str) or not output_ref.startswith("file://"):
                raise LiveRunError(f"live {cell_name} trial omitted its local output artifact")
            parsed = urlparse(output_ref)
            if parsed.scheme != "file" or not parsed.path:
                raise LiveRunError(f"live {cell_name} trial output URI is invalid")
            artifact = _read(Path(unquote(parsed.path)))
            if canonical_json_digest(artifact) != trial.get("output_digest"):
                raise LiveRunError(f"live {cell_name} trial output digest mismatch before upload")
            encoded = base64.b64encode(
                json.dumps(
                    artifact,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).decode("ascii")
            trial["output_ref"] = "data:application/json;base64," + encoded
    return bundle


def _inline_gate_artifacts(report: dict[str, Any]) -> dict[str, Any]:
    """Inline and hash Gate artifacts before sending them to the controller."""

    replacements: dict[str, str] = {}
    refs: list[dict[str, str]] = []
    for ref in report.get("artifact_refs") or []:
        uri = str(ref.get("uri") or "")
        parsed = urlparse(uri)
        if parsed.scheme == "data":
            refs.append(ref)
            continue
        if parsed.scheme != "file" or not parsed.path:
            raise LiveRunError("live Gate artifact is not a local immutable file")
        path = Path(unquote(parsed.path)).resolve()
        payload = path.read_bytes()
        if len(payload) > 2_000_000:
            raise LiveRunError("live Gate artifact exceeds 2 MB")
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if digest != ref.get("digest"):
            raise LiveRunError("live Gate artifact changed before registration")
        inline_uri = "data:application/json;base64," + base64.b64encode(payload).decode("ascii")
        replacements[uri] = inline_uri
        refs.append({"uri": inline_uri, "digest": digest})
    report["artifact_refs"] = refs
    for track in (report.get("deterministic_tests") or {}, report.get("live_provider_e2e") or {}):
        for suite in track.get("suites") or []:
            if suite.get("report_ref") in replacements:
                suite["report_ref"] = replacements[suite["report_ref"]]
    return report


def _preflight(args: argparse.Namespace) -> tuple[dict[str, str], list[str]]:
    names = (
        "STEPFUN_API_KEY",
        "JUDGE_MODEL",
        "CASELOOP_B1_BAD_VERSIONSET_ID",
        "CASELOOP_B1_GOOD_VERSIONSET_ID",
        "CASELOOP_QUALITY_API_BASE_URL",
        "CASELOOP_READ_TOKEN",
        "CONTROL_PLANE_BASE_URL",
        "CONTROL_PLANE_TOKEN",
        "GATE_AUTHORITY_TOKEN",
        "CASELOOP_B1_APPROVAL_COMMAND",
        "CASELOOP_B1_AGENT_TRACE_COMMAND",
        "CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY",
        "CASELOOP_B1_FEISHU_MESSAGE_COMMAND",
    )
    values = {name: os.environ.get(name, "") for name in names}
    values["STEPFUN_BASE_URL"] = os.environ.get(
        "STEPFUN_BASE_URL", _OFFICIAL_STEPFUN_BASE_URL
    ).rstrip("/")
    blockers = [name for name, value in values.items() if not value]
    if values["STEPFUN_BASE_URL"] != _OFFICIAL_STEPFUN_BASE_URL:
        blockers.append(
            "STEPFUN_BASE_URL must be the official live endpoint "
            f"{_OFFICIAL_STEPFUN_BASE_URL}"
        )
    authority_tokens = [values["CONTROL_PLANE_TOKEN"], values["GATE_AUTHORITY_TOKEN"]]
    if all(authority_tokens) and len(set(authority_tokens)) != len(authority_tokens):
        blockers.append("control and gate authority tokens must differ")
    if values["JUDGE_MODEL"] and values["JUDGE_MODEL"] == os.environ.get(
        "STEPFUN_MODEL", "step-3.7-flash"
    ):
        blockers.append("JUDGE_MODEL must differ from STEPFUN_MODEL")
    if not values["CASELOOP_B1_FEISHU_MESSAGE_COMMAND"]:
        blockers.append(
            "CASELOOP_B1_FEISHU_MESSAGE_COMMAND is required to acquire a post-injection complaint"
        )
    if not values["CASELOOP_B1_APPROVAL_COMMAND"]:
        blockers.append(
            "three fresh human ApprovalGrants require an independently authorized approval command"
        )
    if not values["CASELOOP_B1_AGENT_TRACE_COMMAND"]:
        blockers.append(
            "live completion requires an independently credentialed AgentTeams v1.2.1 "
            "taskflow/Matrix/skill trace command"
        )
    if values["CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY"]:
        try:
            agentteams_public_key_id(values["CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY"])
        except AgentTeamsAttestationError as exc:
            blockers.append(f"CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY is invalid: {exc}")
    try:
        load_b1_complaint_fixture()
    except B1FixtureError as exc:
        blockers.append(f"repository-owned B1 fixture is unavailable: {exc}")
    if not Path(args.eval_python).is_file():
        blockers.append(f"eval Python is unavailable: {args.eval_python}")
    return values, blockers


def _decide_live_preflight(
    args: argparse.Namespace, output_dir: Path
) -> tuple[dict[str, str], list[str], str]:
    """Decide whether live mutation is allowed before creating evidence files."""

    values, blockers = _preflight(args)
    working_tree = ""
    if output_dir.exists():
        blockers.append(f"live evidence directory already exists: {output_dir}")
    if not blockers:
        working_tree = _git("status", "--porcelain")
        if working_tree:
            blockers.append(
                "git working tree must be clean before live provider mutation and evidence capture"
            )
    return values, blockers, working_tree


def _compensate_incomplete_b1(
    api: API,
    values: dict[str, str],
    *,
    run_id: str,
    quarantine_versionset_id: str | None = None,
) -> dict[str, Any]:
    """Restore the exact baseline only while the injected fault remains active."""

    bad_id = values["CASELOOP_B1_BAD_VERSIONSET_ID"]
    good_id = values["CASELOOP_B1_GOOD_VERSIONSET_ID"]
    bad = api.get(f"/v2/versionsets/{bad_id}", quality=True)
    good = api.get(f"/v2/versionsets/{good_id}", quality=True)
    observation = {
        "fault": {key: bad.get(key) for key in ("versionset_id", "digest", "revision", "status")},
        "baseline": {key: good.get(key) for key in ("versionset_id", "digest", "revision", "status")},
    }
    if bad.get("status") == "active":
        receipt = api.post(
            "/v1/demo/faults/B1/recover",
            {
                "expected_active_fault_versionset_id": bad_id,
                "restore_versionset_id": good_id,
                **(
                    {"quarantine_versionset_id": quarantine_versionset_id}
                    if quarantine_versionset_id
                    else {}
                ),
                "idempotency_key": f"b1-live-recover-{run_id}",
            },
        )
        restored = api.get(f"/v2/versionsets/{good_id}", quality=True)
        recovered_fault = api.get(f"/v2/versionsets/{bad_id}", quality=True)
        quarantined = (
            api.get(f"/v2/versionsets/{quarantine_versionset_id}", quality=True)
            if quarantine_versionset_id
            else None
        )
        if (
            receipt.get("fault_id") != "B1"
            or receipt.get("restored_versionset_id") != good_id
            or receipt.get("fault_versionset_id") != bad_id
            or receipt.get("restored_versionset_digest") != restored.get("digest")
            or receipt.get("fault_versionset_digest") != recovered_fault.get("digest")
            or receipt.get("restored_revision") != restored.get("revision")
            or receipt.get("fault_revision") != recovered_fault.get("revision")
            or restored.get("status") != "active"
            or recovered_fault.get("status") == "active"
            or (
                quarantined is not None
                and (
                    receipt.get("quarantined_versionset_id")
                    != quarantine_versionset_id
                    or receipt.get("quarantined_versionset_digest")
                    != quarantined.get("digest")
                    or receipt.get("quarantined_revision") != quarantined.get("revision")
                    or receipt.get("quarantined_status") != quarantined.get("status")
                    or quarantined.get("status") not in {"draft", "rolled_back"}
                )
            )
        ):
            raise LiveRunError("B1 compensation receipt/state binding is invalid")
        return {
            "status": "recovered",
            "fault_was_active": True,
            "observation_before": observation,
            "receipt": receipt,
        }
    if good.get("status") == "active":
        return {
            "status": "already_recovered",
            "fault_was_active": False,
            "observation_before": observation,
        }
    active = api.get("/v2/versionsets?status=active&limit=5", quality=True)
    active_items = active.get("items") if isinstance(active, dict) else None
    if (
        isinstance(active_items, list)
        and len(active_items) == 1
        and active_items[0].get("versionset_id") != bad_id
    ):
        # A promote response may have been lost after Quality committed.  A
        # different exact active VersionSet means the bad fault is no longer
        # serving; never demote it via demo compensation.
        return {
            "status": "not_required_nonfault_active",
            "fault_was_active": False,
            "observation_before": observation,
            "active_versionset": {
                key: active_items[0].get(key)
                for key in ("versionset_id", "digest", "revision", "status")
            },
        }
    raise LiveRunError(
        "B1 compensation cannot determine one safe active VersionSet; high-risk state is UNKNOWN"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eval-python", default=str(REPO_ROOT / "eval-harness" / ".venv" / "bin" / "python")
    )
    parser.add_argument(
        "--evidence-root", type=Path, default=REPO_ROOT / "evidence" / "p0" / "p0-4-b1-live"
    )
    args = parser.parse_args()

    started_at = _iso()
    run_id = f"b1run_live{uuid.uuid4().hex[:16]}"
    output_dir = (args.evidence_root / run_id).resolve()
    values, blockers, working_tree_before_run = _decide_live_preflight(
        args, output_dir
    )
    # The decision above intentionally precedes this first repository write;
    # otherwise the evidence directory would make a clean tree self-block.
    output_dir.mkdir(parents=True, exist_ok=False)
    report_path = output_dir / "live-provider-report.json"
    if blockers:
        report = {
            "schema_version": "0.1.0",
            "run_id": run_id,
            "mode": "live-provider",
            "status": "blocked",
            "started_at": started_at,
            "completed_at": _iso(),
            "provider_calls_attempted": False,
            "blockers": blockers,
            "replay_fallback_used": False,
        }
        _write(report_path, report)
        print(json.dumps({"status": "blocked", "report": str(report_path), "blockers": blockers}, ensure_ascii=False))
        return 2

    completed_phases: list[str] = []
    ids: dict[str, str] = {"run_id": run_id}
    provider_calls_attempted = False
    injection_attempted = False
    api = API(
        control_base=values["CONTROL_PLANE_BASE_URL"],
        quality_base=values["CASELOOP_QUALITY_API_BASE_URL"],
        control_token=values["CONTROL_PLANE_TOKEN"],
        gate_token=values["GATE_AUTHORITY_TOKEN"],
        read_token=values["CASELOOP_READ_TOKEN"],
        evidence_dir=output_dir,
    )
    try:
        health_status, health = api.request("GET", "/healthz")
        if health_status != 200 or health.get("status") != "ok":
            raise LiveRunError("control-plane health check failed")

        provider_calls_attempted = True
        bad_before = api.get(
            f"/v2/versionsets/{values['CASELOOP_B1_BAD_VERSIONSET_ID']}", quality=True
        )
        good_before = api.get(
            f"/v2/versionsets/{values['CASELOOP_B1_GOOD_VERSIONSET_ID']}", quality=True
        )
        bad_content_before = bad_before.get("content") or {}
        good_content_before = good_before.get("content") or {}
        if (
            bad_before.get("versionset_id") != values["CASELOOP_B1_BAD_VERSIONSET_ID"]
            or good_before.get("versionset_id") != values["CASELOOP_B1_GOOD_VERSIONSET_ID"]
            or (bad_content_before.get("prompt") or {}).get("digest")
            == (good_content_before.get("prompt") or {}).get("digest")
            or (bad_content_before.get("kb_manifest") or {}).get("manifest_digest")
            != (good_content_before.get("kb_manifest") or {}).get("manifest_digest")
            or (bad_content_before.get("model") or {}).get("digest")
            != (good_content_before.get("model") or {}).get("digest")
        ):
            raise LiveRunError("live VersionSets are not an immutable prompt-only B1 pair")
        fresh_injection = (
            good_before.get("status") == "active" and bad_before.get("status") != "active"
        )
        resumed_injection = (
            good_before.get("status") == "superseded" and bad_before.get("status") == "active"
        )
        if not fresh_injection and not resumed_injection:
            raise LiveRunError(
                "B1 VersionSet lifecycle is neither injectable nor resumable; inspect Quality "
                "history and perform an explicitly approved rollback before retrying"
            )
        _write(
            output_dir / "versionset-inputs-before-injection.json",
            {"bad": bad_before, "good": good_before},
        )
        injection_attempted = True
        injection_operation_id = f"b1-live-inject-{run_id}"
        injection = api.post(
            "/v1/demo/faults/B1/inject",
            {
                "expected_active_versionset_id": good_before["versionset_id"],
                "fault_versionset_id": bad_before["versionset_id"],
                "idempotency_key": injection_operation_id,
            },
        )
        bad = api.get(
            f"/v2/versionsets/{values['CASELOOP_B1_BAD_VERSIONSET_ID']}", quality=True
        )
        good = api.get(
            f"/v2/versionsets/{values['CASELOOP_B1_GOOD_VERSIONSET_ID']}", quality=True
        )
        bad_content = bad.get("content") or {}
        good_content = good.get("content") or {}
        if (
            injection.get("fault_id") != "B1"
            or injection.get("previous_versionset_id") != good["versionset_id"]
            or injection.get("fault_versionset_id") != bad["versionset_id"]
            or injection.get("previous_versionset_digest") != good["digest"]
            or injection.get("fault_versionset_digest") != bad["digest"]
            or injection.get("previous_revision") != good["revision"]
            or injection.get("fault_revision") != bad["revision"]
            or injection.get("duplicate") is not False
            or injection.get("provider_duplicate") is not resumed_injection
            or good.get("status") != "superseded"
            or bad.get("status") != "active"
        ):
            raise LiveRunError("Release Controller B1 injection receipt/state binding is invalid")
        _write(output_dir / "fault-injection-receipt.json", injection)
        _write(output_dir / "versionset-inputs.json", {"bad": bad, "good": good})
        completed_phases.extend(
            [
                "versionsets_verified",
                "badcase_injection_resumed" if resumed_injection else "badcase_injected",
            ]
        )

        complaint_fixture = load_b1_complaint_fixture()
        transaction_id, message_acquisition = _feishu_message_id_from_command(
            values["CASELOOP_B1_FEISHU_MESSAGE_COMMAND"],
            injection_operation_id=injection_operation_id,
            injection_receipt=injection,
            fixture=complaint_fixture,
            evidence_dir=output_dir,
        )
        completed_phases.append("post_injection_feishu_message_acquired")
        complaint_request = {
            "app_ref": "demo-app:b1-live",
            "title": "B1 live prompt regression",
            "demo_fault_injection_id": injection_operation_id,
        }
        inbound_path = f"/v1/inbox/feishu/messages/{transaction_id}/complaint"
        complaint = api.post(inbound_path, complaint_request)
        duplicate = api.post(inbound_path, complaint_request)
        case_id = complaint.get("case_id")
        inbound = complaint.get("inbound") or {}
        if (
            not case_id
            or duplicate.get("duplicate") is not True
            or duplicate.get("case_id") != case_id
            or inbound.get("provider") != "feishu"
            or inbound.get("provider_origin") != _OFFICIAL_FEISHU_BASE_URL
            or inbound.get("message_id") != transaction_id
            or not str(inbound.get("channel") or "").startswith("feishu:")
            or inbound.get("thread_ref")
            != f"{inbound.get('channel')}:{transaction_id}"
            or not str(inbound.get("text_digest") or "").startswith("sha256:")
            or inbound.get("text_digest") != complaint_fixture.text_digest
            or complaint.get("demo_fault_injection_id") != injection_operation_id
            or duplicate.get("demo_fault_injection_id") != injection_operation_id
        ):
            raise LiveRunError("live complaint inbox did not deduplicate")
        injection_created_at = _provider_injected_at(injection.get("injected_at"))
        complaint_created_at = _feishu_message_created_at(inbound.get("create_time"))
        if complaint_created_at <= injection_created_at:
            raise LiveRunError(
                "Feishu complaint predates the authoritative B1 injection; causal provenance failed"
            )
        _write(output_dir / "feishu-inbound-receipt.json", inbound)
        original_channel = inbound["channel"]
        original_thread_ref = inbound["thread_ref"]
        ids.update({"transaction_id": transaction_id, "case_id": case_id})
        agent_trace_start = _agent_trace_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            phase="start",
            context={
                "fixture_id": "B1",
                "run_id": run_id,
                "transaction_id": transaction_id,
                "case_id": case_id,
                "inbound_text_digest": inbound["text_digest"],
            },
            evidence_dir=output_dir,
        )
        agent_phase_receipts: dict[str, list[dict[str, Any]]] = {
            role: [] for role in _B1_AGENT_ROLES
        }
        dispatch_product, dispatch_phase = _agent_phase_product_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            phase="dispatch-review",
            role="quality-officer",
            artifact_kind="dispatch-intent",
            context={
                "run_id": run_id,
                "case_id": case_id,
                "transaction_id": transaction_id,
                "injection_operation_id": injection_operation_id,
                "inbound_text_digest": inbound["text_digest"],
            },
            evidence_dir=output_dir,
            start_receipt=agent_trace_start,
        )
        if dispatch_product != {
            "case_id": case_id,
            "injection_operation_id": injection_operation_id,
            "next_role": "collector",
        }:
            raise LiveRunError("AgentTeams quality-officer dispatch intent is invalid")
        agent_phase_receipts["quality-officer"].append(dispatch_phase)
        collection_product, collection_phase = _agent_phase_product_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            phase="collect-complaint",
            role="collector",
            artifact_kind="complaint-evidence",
            context={
                "run_id": run_id,
                "case_id": case_id,
                "transaction_id": transaction_id,
                "inbound": inbound,
            },
            evidence_dir=output_dir,
            start_receipt=agent_trace_start,
        )
        if collection_product != {
            "message_id": transaction_id,
            "channel": inbound["channel"],
            "thread_ref": inbound["thread_ref"],
            "text_digest": inbound["text_digest"],
        }:
            raise LiveRunError("AgentTeams collector evidence differs from Feishu authority")
        agent_phase_receipts["collector"].append(collection_phase)
        completed_phases.append("agentteams_task_dispatched")
        runner_id = "eval-runner"
        experiment = api.post(
            "/v1/experiments", {"case_id": case_id, "hypothesis_layer": "prompt"}
        )
        experiment_id = experiment.get("experiment_id")
        if not experiment_id:
            raise LiveRunError("control-plane did not create an Experiment")
        ids["experiment_id"] = experiment_id

        probe_set = load_probe_set(REPO_ROOT)
        probe_digest = frozen_digest(probe_set)
        versions = {
            "P0": good_content["prompt"]["digest"],
            "P1": bad_content["prompt"]["digest"],
            "K0": good_content["kb_manifest"]["manifest_digest"],
            "K1": bad_content["kb_manifest"]["manifest_digest"],
            "M0": good_content["model"]["digest"],
            "M1": bad_content["model"]["digest"],
        }
        bad_ref = {key: bad[key] for key in ("versionset_id", "digest", "revision")}
        good_ref = {key: good[key] for key in ("versionset_id", "digest", "revision")}
        cell_refs = {"C": bad_ref, "RP": good_ref, "RK": bad_ref, "RM": bad_ref, "G": good_ref}
        frozen_protocol = {
            "execution_profile": "live",
            "probe_set_digest": probe_digest,
            "discovery": DISCOVERY,
            "hidden_confirmation": HIDDEN,
            "unaffected_controls": CONTROLS,
            "repetitions": 3,
            "versions": versions,
            "cell_versionsets": cell_refs,
            "random_seed_ref": f"seed://{experiment_id}/20260807",
            "confidence": 0.95,
        }
        attribution_plan, attribution_phase = _agent_phase_product_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            phase="attribution-plan",
            role="attributionist",
            artifact_kind="experiment-plan",
            context={
                "run_id": run_id,
                "case_id": case_id,
                "experiment_id": experiment_id,
                "proposed_protocol": frozen_protocol,
            },
            evidence_dir=output_dir,
            start_receipt=agent_trace_start,
        )
        if (
            attribution_plan.get("experiment_id") != experiment_id
            or attribution_plan.get("hypothesis_layer") != "prompt"
            or attribution_plan.get("protocol") != frozen_protocol
        ):
            raise LiveRunError("AgentTeams attribution plan differs from immutable B1 inputs")
        frozen_protocol = attribution_plan["protocol"]
        agent_phase_receipts["attributionist"].append(attribution_phase)
        lease = api.post(f"/v1/cases/{case_id}/claim", {"worker_id": runner_id})
        api.post(
            f"/v1/experiments/{experiment_id}/protocol",
            frozen_protocol,
        )
        api.post(
            f"/v1/experiments/{experiment_id}/start",
            {
                "runner_id": runner_id,
                "lease_id": lease["lease_id"],
                "fencing_token": lease["fencing_token"],
            },
        )
        completed_phases.append("complaint_case_protocol_frozen")

        attribution_root = output_dir / "attribution"

        def heartbeat() -> None:
            api.post(
                f"/v1/cases/{case_id}/heartbeat",
                {"worker_id": runner_id, "fencing_token": lease["fencing_token"]},
            )

        attribution_run = _run_provider(
            [
                args.eval_python,
                "scripts/run_b1_experiment.py",
                "--reps",
                "3",
                "--seed",
                "20260807",
                "--case-id",
                case_id,
                "--experiment-id",
                experiment_id,
                "--bad-versionset-id",
                bad["versionset_id"],
                "--good-versionset-id",
                good["versionset_id"],
                "--out-dir",
                str(attribution_root),
            ],
            cwd=REPO_ROOT / "eval-harness",
            log_path=output_dir / "attribution-command.json",
            heartbeat=heartbeat,
        )
        if attribution_run["exit_code"] != 0:
            raise LiveRunError("live attribution command failed")
        attribution_dir = attribution_root / experiment_id
        bundle = _read(attribution_dir / "evidence-bundle.json")
        attribution_report = _read(attribution_dir / "attribution-report.json")
        bundle = _inline_live_probe_outputs(bundle)
        bundle_bytes = json.dumps(
            bundle,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        attribution_report["evidence_bundle_ref"] = {
            "uri": "data:application/json;base64,"
            + base64.b64encode(bundle_bytes).decode("ascii"),
            "digest": canonical_json_digest(bundle),
        }
        _write(attribution_dir / "evidence-bundle.json", bundle)
        _write(attribution_dir / "attribution-report.json", attribution_report)
        unique_order = list(
            dict.fromkeys(item.split("@", 1)[0] for item in bundle["protocol"]["random_arm_order"])
        )
        if set(unique_order) != set(ARMS):
            raise LiveRunError("live attribution arm order is incomplete")
        for index, arm in enumerate(unique_order):
            api.post(
                f"/v1/experiments/{experiment_id}/cells",
                {
                    "cell": arm,
                    "arm_order_index": index,
                    "recovery_rate": bundle["cells"][arm]["recovery_rate"],
                    "fencing_token": lease["fencing_token"],
                },
            )
        verdict = api.post(
            f"/v1/experiments/{experiment_id}/verdict",
            {
                "fencing_token": lease["fencing_token"],
                "evidence_bundle": bundle,
                "attribution_report": attribution_report,
            },
        )
        if verdict.get("payload", {}).get("verdict") != "ATTRIBUTED" or verdict.get(
            "payload", {}
        ).get("attributed_layer") != "prompt":
            raise LiveRunError("authoritative live verdict is not ATTRIBUTED/prompt")
        completed_phases.append("attributed_prompt")

        repairer_id = "repairer"
        proposed_repair = {
            "case_id": case_id,
            "channel": "prompt",
            "attribution_report_digest": canonical_json_digest(attribution_report),
            "base_versionset_id": bad["versionset_id"],
            "base_versionset_digest": bad["digest"],
            "base_revision": bad["revision"],
            "target_prompt_digest": good_content["prompt"]["digest"],
            "content": good_content,
        }
        repair_product, repair_phase = _agent_phase_product_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            phase="repair-proposal",
            role="repairer",
            artifact_kind="repair-proposal",
            context={
                "run_id": run_id,
                "case_id": case_id,
                "attribution_report_ref": _artifact_ref(
                    attribution_dir / "attribution-report.json"
                ),
                "authoritative_base": bad,
                "recommended_prompt_only_repair": proposed_repair,
            },
            evidence_dir=output_dir,
            start_receipt=agent_trace_start,
        )
        proposal = repair_product.get("proposal") or {}
        proposal_content = proposal.get("content") or {}
        if (
            any(
                proposal.get(key) != proposed_repair[key]
                for key in (
                    "case_id",
                    "channel",
                    "attribution_report_digest",
                    "base_versionset_id",
                    "base_versionset_digest",
                    "base_revision",
                )
            )
            or proposal_content.get("kb_manifest") != bad_content.get("kb_manifest")
            or proposal_content.get("model") != bad_content.get("model")
            or (proposal_content.get("prompt") or {}).get("digest")
            != proposal.get("target_prompt_digest")
            or proposal.get("target_prompt_digest")
            == (bad_content.get("prompt") or {}).get("digest")
        ):
            raise LiveRunError("AgentTeams repairer proposal violates prompt-only binding")
        agent_phase_receipts["repairer"].append(repair_phase)
        repair_lease = api.post(
            f"/v1/cases/{case_id}/claim", {"worker_id": repairer_id}
        )
        candidate = api.post(
            "/v1/release-candidates",
            {
                **proposal,
                "worker_id": repairer_id,
                "fencing_token": repair_lease["fencing_token"],
                "proposal_digest": canonical_json_digest(proposal),
                "idempotency_key": f"b1-live-candidate-{run_id}",
            },
        )
        if candidate.get("status") != "draft":
            raise LiveRunError("Release Controller did not return a draft candidate")
        ids["candidate_versionset_id"] = candidate["versionset_id"]

        def repair_heartbeat() -> None:
            api.post(
                f"/v1/cases/{case_id}/heartbeat",
                {
                    "worker_id": repairer_id,
                    "fencing_token": repair_lease["fencing_token"],
                },
            )

        workorder_id = f"wo_{uuid.uuid4().hex[:20]}"
        ids["workorder_id"] = workorder_id
        gate_request, initial_gate_phase = _agent_phase_product_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            phase="initial-gate",
            role="gatekeeper",
            artifact_kind="gate-request",
            context={
                "run_id": run_id,
                "case_id": case_id,
                "stage": "initial",
                "workorder_id": workorder_id,
                "target_versionset": {
                    key: candidate[key]
                    for key in ("versionset_id", "digest", "revision")
                },
                "suite_digest": probe_digest,
            },
            evidence_dir=output_dir,
            start_receipt=agent_trace_start,
            heartbeat=repair_heartbeat,
        )
        if gate_request != {
            "stage": "initial",
            "workorder_id": workorder_id,
            "target_versionset_id": candidate["versionset_id"],
            "target_versionset_digest": candidate["digest"],
            "target_revision": candidate["revision"],
            "suite_digest": probe_digest,
        }:
            raise LiveRunError("AgentTeams gatekeeper initial request binding is invalid")
        agent_phase_receipts["gatekeeper"].append(initial_gate_phase)
        initial_gate_dir = output_dir / "gate-initial"
        initial_gate_run = _run_provider(
            [
                args.eval_python,
                "scripts/run_gate.py",
                "--versionset-id",
                candidate["versionset_id"],
                "--out-dir",
                str(initial_gate_dir),
            ],
            cwd=REPO_ROOT / "eval-harness",
            log_path=output_dir / "gate-initial-command.json",
            heartbeat=repair_heartbeat,
        )
        initial_gate = _inline_gate_artifacts(_read(initial_gate_dir / "gate-report.json"))
        _write(initial_gate_dir / "gate-report.json", initial_gate)
        if initial_gate_run["exit_code"] != 0 or initial_gate.get("overall_status") != "passed":
            raise LiveRunError("initial live GateReport did not pass")
        initial_registration, initial_gate_hash = _gate_registration(
            initial_gate,
            workorder_id=workorder_id,
            target=candidate,
            dataset_id=probe_set.probe_set_id,
            dataset_version=probe_set.version,
            case_id=case_id,
        )
        api.post("/v1/gate-reports", initial_registration, authority="gate")
        bad_prompt_json = json.dumps(bad_content["prompt"], ensure_ascii=False, sort_keys=True)
        good_prompt_json = json.dumps(
            proposal_content["prompt"], ensure_ascii=False, sort_keys=True
        )
        expected_workorder_binding = {
            "schema_version": "0.1.0",
            "workorder_id": workorder_id,
            "case_id": case_id,
            "channel": "prompt",
            "base_versionset_digest": bad["digest"],
            "target_versionset_digest": candidate["digest"],
            "input_versions": candidate["input_versions"],
            "gate_report_ref": {
                "uri": f"eval://{initial_gate['eval_id']}",
                "digest": f"sha256:{initial_gate_hash}",
            },
            "created_by": repairer_id,
            "hash_rule": "jcs-rfc8785+sha256",
        }
        workorder, agent_workorder_receipt = _agent_workorder_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            context={
                "run_id": run_id,
                "case_id": case_id,
                "attribution_report_ref": _artifact_ref(
                    attribution_dir / "attribution-report.json"
                ),
                "candidate_receipt": candidate,
                "initial_gate_report_ref": _artifact_ref(
                    initial_gate_dir / "gate-report.json"
                ),
                "expected_workorder_binding": expected_workorder_binding,
                "repair_diff_inputs": {
                    "base_prompt_json": bad_prompt_json,
                    "target_prompt_json": good_prompt_json,
                },
            },
            evidence_dir=output_dir,
            start_receipt=agent_trace_start,
            heartbeat=repair_heartbeat,
        )
        agent_phase_receipts["repairer"].append(agent_workorder_receipt)
        _write(output_dir / "workorder.json", workorder)
        api.post(
            "/v1/workorders",
            {
                "workorder": workorder,
                "worker_id": repairer_id,
                "fencing_token": repair_lease["fencing_token"],
            },
        )
        completed_phases.append("candidate_gate_workorder_frozen")

        approval_command = values["CASELOOP_B1_APPROVAL_COMMAND"]
        initial_approval = _approval_from_command(
            approval_command,
            api=api,
            phase="initial",
            workorder=workorder,
            evidence_dir=output_dir,
        )
        release_id = f"rel_{uuid.uuid4().hex[:20]}"
        ids["release_id"] = release_id
        start_receipt = api.post(
            "/v1/releases",
            {
                "workorder_id": workorder_id,
                "approval_id": initial_approval["approval_id"],
                "versionset_id": candidate["versionset_id"],
                "release_id": release_id,
            },
        )
        stage_receipt = api.post(
            f"/v1/releases/{release_id}/stage",
            {"idempotency_key": f"b1-live-stage-{run_id}"},
        )
        if (
            stage_receipt.get("state") != "STAGING"
            or stage_receipt.get("status") != "succeeded"
            or (stage_receipt.get("payload") or {}).get("remote_revision") != 2
        ):
            raise LiveRunError("stage receipt is not the exact authoritative transition")
        canary_context = api.post(
            f"/v1/releases/{release_id}/approval-context",
            {"action": "canary", "reason": "B1 live canary"},
        )
        canary_approval = _approval_from_command(
            approval_command,
            api=api,
            phase="canary",
            workorder=workorder,
            evidence_dir=output_dir,
            authorization=canary_context["authorization"],
        )
        canary_receipt = api.post(
            f"/v1/releases/{release_id}/canary",
            {
                "idempotency_key": f"b1-live-canary-{run_id}",
                "percent": canary_context["authorization"]["params"]["percent"],
                "approval_id": canary_approval["approval_id"],
            },
        )
        if (
            canary_receipt.get("state") != "CANARYING"
            or canary_receipt.get("status") != "succeeded"
            or (canary_receipt.get("payload") or {}).get("remote_revision") != 3
        ):
            raise LiveRunError("canary receipt is not the exact authoritative transition")
        canary_percent = canary_context["authorization"]["params"]["percent"]
        canary_session_id, canary_bucket = _canary_routing_key(run_id, canary_percent)
        canary_routed_chat = api.post(
            "/chat",
            {
                "message": probe_set.get(DISCOVERY[0]).input,
                "session_id": canary_session_id,
            },
            quality=True,
        )
        if (
            canary_routed_chat.get("status") != "ok"
            or canary_routed_chat.get("provider_origin")
            != _OFFICIAL_STEPFUN_BASE_URL
            or canary_routed_chat.get("versionset_id") != candidate["versionset_id"]
            or canary_routed_chat.get("prompt_digest")
            != (candidate["content"].get("prompt") or {}).get("digest")
            or canary_routed_chat.get("kb_manifest_digest")
            != (candidate["content"].get("kb_manifest") or {}).get("manifest_digest")
            or canary_routed_chat.get("model_digest")
            != (candidate["content"].get("model") or {}).get("digest")
        ):
            raise LiveRunError("real /chat request was not routed to the exact canary")
        canary_routed_log = _exact_quality_log(
            api, str(canary_routed_chat.get("request_id") or "")
        )
        for key in (
            "request_id",
            "versionset_id",
            "prompt_digest",
            "kb_manifest_digest",
            "model_digest",
            "provider_origin",
            "answer_digest",
            "trace_id",
            "status",
        ):
            if canary_routed_log.get(key) != canary_routed_chat.get(key):
                raise LiveRunError(f"canary /chat provider log differs on {key}")
        _write(
            output_dir / "release-receipts-pre-verification.json",
            {
                "start": start_receipt,
                "stage": stage_receipt,
                "canary": canary_receipt,
                "routed_request": canary_routed_chat,
                "routed_log": canary_routed_log,
            },
        )
        completed_phases.append("stage_canary_executed")

        deadline = time.monotonic() + 600
        verification_context: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            status, candidate_context = api.request(
                "GET", f"/v1/releases/{release_id}/verification-context", allow_error=True
            )
            if status == 200:
                observation = candidate_context.get("canary_observation") or {}
                if observation.get("complete") is True:
                    verification_context = candidate_context
                    break
                remaining = observation.get("remaining_seconds")
                if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
                    time.sleep(max(1.0, min(5.0, float(remaining))))
                    continue
                raise LiveRunError("canary observation receipt is incomplete")
            if status != 422:
                raise LiveRunError("post-canary verification context is unavailable")
            time.sleep(5)
        if verification_context is None:
            raise LiveRunError("canary observation window did not complete")
        _write(output_dir / "canary-verification-context.json", verification_context)

        post_gate_request, post_gate_phase = _agent_phase_product_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            phase="post-canary-gate",
            role="gatekeeper",
            artifact_kind="gate-request",
            context={
                "run_id": run_id,
                "case_id": case_id,
                "release_id": release_id,
                "workorder_id": workorder_id,
                "verification_context": verification_context,
                "suite_digest": probe_digest,
            },
            evidence_dir=output_dir,
            start_receipt=agent_trace_start,
        )
        if post_gate_request != {
            "stage": "post-canary",
            "release_id": release_id,
            "workorder_id": workorder_id,
            "target_versionset_id": verification_context["target_versionset_id"],
            "target_versionset_digest": verification_context["target_versionset_digest"],
            "target_revision": verification_context["target_revision"],
            "suite_digest": probe_digest,
        }:
            raise LiveRunError("AgentTeams gatekeeper post-canary request binding is invalid")
        agent_phase_receipts["gatekeeper"].append(post_gate_phase)
        post_gate_dir = output_dir / "gate-post-canary"
        post_gate_run = _run_provider(
            [
                args.eval_python,
                "scripts/run_gate.py",
                "--versionset-id",
                verification_context["target_versionset_id"],
                "--out-dir",
                str(post_gate_dir),
            ],
            cwd=REPO_ROOT / "eval-harness",
            log_path=output_dir / "gate-post-canary-command.json",
        )
        post_gate = _inline_gate_artifacts(_read(post_gate_dir / "gate-report.json"))
        _write(post_gate_dir / "gate-report.json", post_gate)
        if post_gate_run["exit_code"] not in (0, 1):
            raise LiveRunError("post-canary Gate runner errored")
        post_target = {
            "versionset_id": verification_context["target_versionset_id"],
            "digest": verification_context["target_versionset_digest"],
            "revision": verification_context["target_revision"],
        }
        post_registration, post_gate_hash = _gate_registration(
            post_gate,
            workorder_id=workorder_id,
            target=post_target,
            dataset_id=probe_set.probe_set_id,
            dataset_version=probe_set.version,
            case_id=case_id,
        )
        api.post("/v1/gate-reports", post_registration, authority="gate")
        verification_receipt = api.post(
            f"/v1/releases/{release_id}/verification",
            {"eval_id": post_gate["eval_id"], "report_hash": post_gate_hash},
        )
        completed_phases.append("post_canary_gate_persisted")

        if post_gate.get("overall_status") != "passed":
            rollback_reply = (
                "Your reported return-policy regression was attributed to the prompt layer. "
                "The candidate failed the post-canary gate, so it was rolled back and the "
                "known-good demo baseline was restored; no failed candidate was promoted."
            )
            rollback_reply_bytes = rollback_reply.encode("utf-8")
            rollback_body_ref = (
                "data:text/plain;base64,"
                + base64.b64encode(rollback_reply_bytes).decode("ascii")
            )
            rollback_body_digest = (
                "sha256:" + hashlib.sha256(rollback_reply_bytes).hexdigest()
            )
            rollback_closure = api.post(
                f"/v1/releases/{release_id}/closure-context",
                {
                    "channel": original_channel,
                    "thread_ref": original_thread_ref,
                    "body_ref": rollback_body_ref,
                    "body_digest": rollback_body_digest,
                },
            )
            rollback_context = api.post(
                f"/v1/releases/{release_id}/approval-context",
                {"action": "rollback", "reason": "post-canary GateReport failed"},
            )
            rollback_approval = _approval_from_command(
                approval_command,
                api=api,
                phase="rollback",
                workorder=workorder,
                evidence_dir=output_dir,
                authorization=rollback_context["authorization"],
            )
            rollback_receipt = api.post(
                f"/v1/releases/{release_id}/rollback",
                {
                    "idempotency_key": f"b1-live-rollback-{run_id}",
                    "approval_id": rollback_approval["approval_id"],
                    "reason": "post-canary GateReport failed",
                },
            )
            if (
                rollback_receipt.get("state") != "ROLLED_BACK"
                or rollback_receipt.get("status") != "succeeded"
                or (rollback_receipt.get("payload") or {}).get("restored_digest")
                != workorder["base_versionset_digest"]
            ):
                raise LiveRunError("post-canary rollback receipt is not authoritative")
            recovery = _compensate_incomplete_b1(
                api,
                values,
                run_id=run_id,
                quarantine_versionset_id=candidate["versionset_id"],
            )
            rollback_dispatches = []
            rollback_case = None
            for _ in range(30):
                rollback_dispatches.append(api.post("/v1/outbox/relay?limit=500"))
                rollback_case = api.get(f"/v1/cases/{case_id}")
                if rollback_case.get("state") == "CLOSED":
                    break
                time.sleep(1)
            rollback_notifications = api.get("/v1/notifications?limit=500")
            rollback_notification = next(
                (
                    item
                    for item in rollback_notifications.get("items", [])
                    if (item.get("payload") or {}).get("release_id") == release_id
                ),
                None,
            )
            rollback_trust = next(
                (
                    item
                    for item in api.get("/v1/trust/ledger").get("items", [])
                    if item.get("last_action_ref") == release_id
                ),
                None,
            )
            if (
                rollback_case is None
                or rollback_case.get("state") != "CLOSED"
                or rollback_notification is None
                or rollback_notification.get("state") != "SENT"
                or ((rollback_notification.get("payload") or {}).get("receipt") or {}).get(
                    "provider"
                )
                != "feishu"
                or ((rollback_notification.get("payload") or {}).get("receipt") or {}).get(
                    "provider_origin"
                )
                != _OFFICIAL_FEISHU_BASE_URL
                or rollback_trust is None
                or rollback_trust.get("promotion_eligible") is not False
                or rollback_trust.get("last_action_ref") != release_id
            ):
                raise LiveRunError(
                    "post-canary rollback did not finish notification/archive/Trust closure"
                )
            _write(
                output_dir / "release-receipts-final.json",
                {
                    "verification": verification_receipt,
                    "closure": rollback_closure,
                    "rollback": rollback_receipt,
                    "known_good_recovery": recovery,
                },
            )
            _write(
                output_dir / "rollback-closure-evidence.json",
                {
                    "case": rollback_case,
                    "notification": rollback_notification,
                    "trust": rollback_trust,
                    "outbox_dispatches": rollback_dispatches,
                },
            )
            completed_phases.append("rolled_back_notified_archived_trust_recorded")
            raise LiveRunError(
                "post-canary gate failed; real rollback, known-good recovery, notification, "
                "archive, and Trust accounting completed; promotion was correctly refused"
            )

        proposed_reply_text = (
            "Your reported return-policy regression was attributed to the prompt layer, "
            "fixed, independently gated, canary verified, and promoted."
        )
        closure_product, closure_phase = _agent_phase_product_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            phase="closure",
            role="case-officer",
            artifact_kind="closure-intent",
            context={
                "run_id": run_id,
                "case_id": case_id,
                "release_id": release_id,
                "channel": original_channel,
                "thread_ref": original_thread_ref,
                "suggested_body_text": proposed_reply_text,
                "post_canary_eval_id": post_gate["eval_id"],
            },
            evidence_dir=output_dir,
            start_receipt=agent_trace_start,
        )
        if closure_product != {
            "case_id": case_id,
            "release_id": release_id,
            "channel": original_channel,
            "thread_ref": original_thread_ref,
            "body_text": proposed_reply_text,
        }:
            raise LiveRunError("AgentTeams case-officer closure intent is invalid")
        agent_phase_receipts["case-officer"].append(closure_phase)
        reply_text = closure_product["body_text"]
        reply_bytes = reply_text.encode("utf-8")
        reply_body_ref = "data:text/plain;base64," + base64.b64encode(reply_bytes).decode("ascii")
        reply_body_digest = "sha256:" + hashlib.sha256(reply_bytes).hexdigest()
        _write(
            output_dir / "reply-body-binding.json",
            {"body_ref": reply_body_ref, "body_digest": reply_body_digest, "text": reply_text},
        )
        promote_context = api.post(
            f"/v1/releases/{release_id}/approval-context",
            {"action": "promote", "reason": "B1 live verified promotion"},
        )
        promote_approval = _approval_from_command(
            approval_command,
            api=api,
            phase="promote",
            workorder=workorder,
            evidence_dir=output_dir,
            authorization=promote_context["authorization"],
        )
        closure_context = api.post(
            f"/v1/releases/{release_id}/closure-context",
            {
                "channel": original_channel,
                "thread_ref": original_thread_ref,
                "body_ref": reply_body_ref,
                "body_digest": reply_body_digest,
            },
        )
        promote_receipt = api.post(
            f"/v1/releases/{release_id}/promote",
            {
                "idempotency_key": f"b1-live-promote-{run_id}",
                "approval_id": promote_approval["approval_id"],
            },
        )
        if promote_receipt.get("state") != "COMPLETED":
            raise LiveRunError("live promote did not return an authoritative COMPLETED receipt")
        promoted_versionset = api.get(
            f"/v2/versionsets/{candidate['versionset_id']}", quality=True
        )
        if (
            promoted_versionset.get("versionset_id") != candidate["versionset_id"]
            or promoted_versionset.get("digest") != candidate["digest"]
            or promoted_versionset.get("status") != "active"
            or promoted_versionset.get("revision")
            != (promote_receipt.get("payload") or {}).get("remote_revision")
        ):
            raise LiveRunError("promote receipt is not reflected by the live active VersionSet")
        _write(output_dir / "promoted-versionset.json", promoted_versionset)
        _write(
            output_dir / "release-receipts-final.json",
            {
                "verification": verification_receipt,
                "closure": closure_context,
                "promote": promote_receipt,
            },
        )
        completed_phases.append("promoted")

        relay_receipts = []
        final_case = None
        for _ in range(30):
            relay_receipts.append(api.post("/v1/outbox/relay?limit=500"))
            final_case = api.get(f"/v1/cases/{case_id}")
            if final_case.get("state") == "CLOSED":
                break
            time.sleep(1)
        if final_case is None or final_case.get("state") != "CLOSED":
            raise LiveRunError("notification provider receipt did not close/archive the Case")

        notifications = api.get("/v1/notifications?limit=500")
        notification = next(
            (
                item
                for item in notifications.get("items", [])
                if (item.get("payload") or {}).get("release_id") == release_id
            ),
            None,
        )
        if (
            notification is None
            or notification.get("state") != "SENT"
            or ((notification.get("payload") or {}).get("receipt") or {}).get("provider") != "feishu"
            or ((notification.get("payload") or {}).get("receipt") or {}).get(
                "provider_origin"
            )
            != _OFFICIAL_FEISHU_BASE_URL
        ):
            raise LiveRunError("Case closed without a bound live Feishu SENT receipt")
        ids["notification_id"] = notification["notification_id"]
        trust = api.get("/v1/trust/ledger")
        trust_denials = api.get("/v1/trust/denials")
        trust_row = next(
            (
                item
                for item in trust.get("items", [])
                if item.get("risk_class") == "R2_HIGH_IMPACT"
                and item.get("action_type") == "release_outcome"
                and item.get("last_action_ref") == release_id
            ),
            None,
        )
        if trust_row is None or trust_row.get("trials", 0) < 1 or trust_row.get("promotion_eligible") is not False:
            raise LiveRunError("Trust Ledger did not record-and-deny the real release action")
        trust_denial = next(
            (
                item
                for item in trust_denials.get("items", [])
                if item.get("action_ref") == release_id
                and item.get("risk_class") == "R2_HIGH_IMPACT"
                and item.get("action_type") == "release_outcome"
                and item.get("trust_entry_id")
            ),
            None,
        )
        if trust_denial is None:
            raise LiveRunError("Trust promotion denial is not bound to this release sample")
        projections = {
            "case": final_case,
            "case_events": api.get(f"/v1/cases/{case_id}/events"),
            "notification": notification,
            "trust": trust,
            "trust_denials": trust_denials,
            "evidence": api.get(f"/v1/evidence?case_id={case_id}&limit=500"),
            "workorders": api.get("/v1/workorders?limit=500"),
            "gates": api.get("/v1/gates?limit=500"),
            "release": api.get(f"/v1/releases/{release_id}"),
            "outbox_relay": relay_receipts,
        }
        _write(output_dir / "control-plane-projections.json", projections)
        completed_phases.append("notified_archived_trust_recorded")

        duplicate_dispatch = None
        for _ in range(10):
            candidate_dispatch = api.post("/v1/outbox/relay?limit=500")
            relay_receipts.append(candidate_dispatch)
            if candidate_dispatch.get("claimed") == 0:
                duplicate_dispatch = candidate_dispatch
                break
        if duplicate_dispatch is None:
            raise LiveRunError("B1 outbox did not drain to an idempotent zero-claim replay")

        authority = api.get(
            f"/v1/internal/evidence/b1?case_id={case_id}&release_id={release_id}"
        )
        if (
            authority.get("case_id") != case_id
            or authority.get("release_id") != release_id
            or (authority.get("case") or {}).get("state") != "CLOSED"
        ):
            raise LiveRunError("internal authority export is not bound to the closed B1 Case")
        event_rows = authority.get("events") or []
        audit_rows = authority.get("audit_events") or []
        inbox_rows = authority.get("inbox") or []
        complaint_events = [
            row
            for row in event_rows
            if row.get("aggregate_type") == "case"
            and row.get("aggregate_id") == case_id
            and row.get("event_type") == "complaint.received"
        ]
        duplicate_audits = [
            row
            for row in audit_rows
            if row.get("action") == "complaint.duplicate"
            and row.get("target") == case_id
            and row.get("result") == "success"
        ]
        inbox_payload = (inbox_rows[0].get("raw_payload") or {}) if len(inbox_rows) == 1 else {}
        case_payload = (authority.get("case") or {}).get("payload") or {}
        complaint_payload = (
            (complaint_events[0].get("payload") or {}) if len(complaint_events) == 1 else {}
        )
        if (
            len(inbox_rows) != 1
            or inbox_rows[0].get("case_id") != case_id
            or inbox_rows[0].get("disposition") != "FILED"
            or inbox_rows[0].get("source") != "webhook"
            or inbox_rows[0].get("external_id") != transaction_id
            or len(complaint_events) != 1
            or len(duplicate_audits) != 1
            or inbox_payload.get("external_id") != transaction_id
            or inbox_payload.get("channel") != inbound["channel"]
            or inbox_payload.get("thread_ref") != inbound["thread_ref"]
            or inbox_payload.get("demo_fault_injection_id") != injection_operation_id
            or inbox_payload.get("provider_origin") != _OFFICIAL_FEISHU_BASE_URL
            or inbox_payload.get("provider_create_time") != inbound["create_time"]
            or inbox_payload.get("source_text_digest") != complaint_fixture.text_digest
            or complaint_payload.get("external_id") != transaction_id
            or complaint_payload.get("channel") != inbound["channel"]
            or complaint_payload.get("thread_ref") != inbound["thread_ref"]
            or complaint_payload.get("demo_fault_injection_id") != injection_operation_id
            or complaint_payload.get("provider_origin") != _OFFICIAL_FEISHU_BASE_URL
            or complaint_payload.get("provider_create_time") != inbound["create_time"]
            or complaint_payload.get("source_text_digest") != complaint_fixture.text_digest
            or f"feishu-text-digest:{inbound['text_digest']}"
            not in (complaint_payload.get("attachments") or [])
            or case_payload.get("provider_origin") != _OFFICIAL_FEISHU_BASE_URL
            or case_payload.get("provider_create_time") != inbound["create_time"]
            or case_payload.get("source_text_digest") != complaint_fixture.text_digest
        ):
            raise LiveRunError("authority export lacks the exact Feishu inbox/dedup/Case binding")
        inbound_dedup = {
            "message_acquisition": message_acquisition,
            "inbound": inbound,
            "inbox": inbox_rows[0],
            "complaint_event": complaint_events[0],
            "case_projection": authority.get("case"),
            "duplicate_audits": duplicate_audits,
        }
        demo_fault_rows = authority.get("demo_fault_operations") or []
        if len(demo_fault_rows) != 1:
            raise LiveRunError("authority export lacks the unique Case-bound B1 injection intent")
        injection_row = demo_fault_rows[0]
        persisted_injection = (injection_row.get("payload") or {}).get("receipt") or {}
        injection_events = [
            row
            for row in event_rows
            if row.get("aggregate_type") == "demo_fault_injection"
            and row.get("aggregate_id") == injection_row.get("aggregate_id")
        ]
        injection_audits = [
            row
            for row in audit_rows
            if row.get("action") in {"demo_fault.B1.inject.intent", "demo_fault.B1.injected"}
            and row.get("target") == injection_row.get("aggregate_id")
        ]
        injection_fields = {
            "fault_id",
            "previous_versionset_id",
            "previous_versionset_digest",
            "previous_revision",
            "fault_versionset_id",
            "fault_versionset_digest",
            "fault_revision",
        }
        if (
            injection_row.get("aggregate_type") != "demo_fault_injection"
            or injection_row.get("state") != "COMPLETED"
            or any(persisted_injection.get(key) != injection.get(key) for key in injection_fields)
            or persisted_injection.get("duplicate")
            is not injection.get("provider_duplicate")
            or injection.get("duplicate") is not False
            or {row.get("event_type") for row in injection_events}
            != {"demo_fault.inject_started", "demo_fault.inject_completed"}
            or {row.get("action") for row in injection_audits}
            != {"demo_fault.B1.inject.intent", "demo_fault.B1.injected"}
        ):
            raise LiveRunError("persisted B1 injection intent/receipt/audit binding is invalid")
        approval_rows = authority.get("approval_grants") or []
        if (
            len(approval_rows) != 3
            or any((row.get("persistence") or {}).get("status") != "consumed" for row in approval_rows)
        ):
            raise LiveRunError("authority export lacks three consumed ApprovalGrants")
        operation_rows = authority.get("controller_operations") or []
        if (
            {row.get("kind") for row in operation_rows} != {"stage", "canary", "promote"}
            or any(row.get("status") != "succeeded" for row in operation_rows)
        ):
            raise LiveRunError("authority export lacks successful stage/canary/promote operations")
        outbox_rows = authority.get("outbox") or []
        delivery_rows = authority.get("outbox_delivery_receipts") or []
        if not outbox_rows or any(row.get("status") != "SENT" for row in outbox_rows):
            raise LiveRunError("authority export contains an undelivered B1 outbox row")
        if len({row.get("outbox_id") for row in delivery_rows}) != len(outbox_rows):
            raise LiveRunError("authority export lacks one immutable receipt per B1 outbox row")
        domain_rows = [row for row in outbox_rows if row.get("channel") == "domain.events"]
        observed_domain_events = {row.get("event_type") for row in domain_rows}
        if not REQUIRED_DOMAIN_EVENTS <= observed_domain_events:
            raise LiveRunError(
                "live B1 domain event chain is incomplete: "
                f"{sorted(REQUIRED_DOMAIN_EVENTS - observed_domain_events)}"
            )
        trust_entries = authority.get("trust_entries") or []
        if len(trust_entries) != 1 or trust_entries[0].get("action_ref") != release_id:
            raise LiveRunError("authority export does not contain exactly one Trust action sample")
        trust_entry = trust_entries[0].get("payload") or {}
        if (
            trust_entry.get("sample_rule") != "one_action_one_sample"
            or (trust_entry.get("promotion") or {}).get("eligible") is not False
            or (trust_entry.get("outcome") or {}).get("action_ref") != release_id
        ):
            raise LiveRunError("Trust entry is not a denied one-action release sample")
        trace_references = authority.get("trace_references") or []
        if not audit_rows or not event_rows or not trace_references:
            raise LiveRunError("authority export lacks audit/event/trace evidence")

        initial_artifacts = _decode_gate_artifacts(initial_gate)
        post_artifacts = _decode_gate_artifacts(post_gate)
        contract_report, contract_semantics = _gate_suite(
            initial_gate, initial_artifacts, "contract"
        )
        replay_report, replay_semantics = _gate_suite(initial_gate, initial_artifacts, "replay")
        _gate_suite(post_gate, post_artifacts, "contract")
        _gate_suite(post_gate, post_artifacts, "replay")
        initial_candidate = _gate_candidate(initial_gate, initial_artifacts)
        post_candidate = _gate_candidate(post_gate, post_artifacts)
        provider_logs = _provider_logs_for_outputs(
            api, bundle, [initial_candidate, post_candidate]
        )
        routed_request_id = str(canary_routed_chat["request_id"])
        if routed_request_id in provider_logs:
            raise LiveRunError("canary routed request id collided with probe evidence")
        provider_logs[routed_request_id] = canary_routed_log

        contract_path = _write(output_dir / "contract-suite-live.json", contract_report)
        replay_path = _write(output_dir / "replay-suite-live.json", replay_report)
        _write(output_dir / "gate-candidate-initial.json", initial_candidate)
        _write(output_dir / "gate-candidate-post-canary.json", post_candidate)
        raw_outputs = []
        for arm, cell in bundle["cells"].items():
            for trial in cell["results"]:
                raw_outputs.append(
                    {"arm": arm, "trial": trial, "output": _decode_inline_json(trial["output_ref"])}
                )
        probe_outputs_path = _write(
            output_dir / "probe-outputs.json",
            {
                "execution_profile": "live",
                "raw_outputs": raw_outputs,
                "quality_provider_logs": provider_logs,
                "gate_candidates": {
                    "initial": initial_candidate,
                    "post_canary": post_candidate,
                },
                "canary_routed_request": canary_routed_chat,
            },
        )
        frozen_path = _write(
            output_dir / "frozen-versionset.json",
            {
                "fixture": "contracts/fixtures/b1-prompt-regression.yaml",
                "complaint_fixture_ref": complaint_fixture.repository_ref,
                "complaint_text_digest": complaint_fixture.text_digest,
                "injection_adapter": "ReleaseService+QualityAPI:live",
                "badcase_injected": True,
                "injection_receipt": injection,
                "injection_authority": {
                    "aggregate": injection_row,
                    "events": injection_events,
                    "audits": injection_audits,
                },
                "active_bad_versionset": bad_ref,
                "known_good_versionset": good_ref,
                "component_digests": versions,
                "cell_versionsets": cell_refs,
                "digest": canonical_json_digest(
                    {"versions": versions, "cell_versionsets": cell_refs}
                ),
            },
        )
        plan_path = _write(
            output_dir / "experiment-plan.json",
            {
                "transaction_id": transaction_id,
                "experiment_id": experiment_id,
                "case_id": case_id,
                "complaint_fixture_ref": complaint_fixture.repository_ref,
                "complaint_text_digest": complaint_fixture.text_digest,
                "protocol": frozen_protocol,
                "random_arm_order": bundle["protocol"]["random_arm_order"],
            },
        )
        probes_path = _write(
            output_dir / "probes.json",
            {
                "probe_set_id": probe_set.probe_set_id,
                "version": probe_set.version,
                "digest": probe_digest,
                "selected": DISCOVERY + HIDDEN + CONTROLS,
                "fixture": "contracts/fixtures/probes-customer-service.yaml",
            },
        )
        gates_path = _write(
            output_dir / "gate-reports.json",
            {"policy_profile": "live", "initial": initial_gate, "post_canary": post_gate},
        )
        approvals_path = _write(
            output_dir / "approval-grants.json",
            {
                "adapter": "independent-human-approval-command:live",
                "not_live_human_approval": False,
                "grants": approval_rows,
            },
        )
        release_receipts_path = _write(
            output_dir / "release-receipts.json",
            {
                "start": start_receipt,
                "stage": stage_receipt,
                "canary": canary_receipt,
                "verification": verification_receipt,
                "closure": closure_context,
                "promote": promote_receipt,
                "controller_operations": operation_rows,
                "persisted_workorders": authority.get("workorders") or [],
                "persisted_gate_reports": authority.get("gate_reports") or [],
                "release_aggregate": authority.get("release"),
            },
        )
        canary_path = _write(
            output_dir / "canary-metrics.json",
            {
                "mode": "live-provider",
                "traffic_routed": True,
                "measurement": "real /chat request selected by the authoritative canary router plus exact-target gate probes",
                "verification_probes_used_router": False,
                "canary_percent": canary_percent,
                "routing": {
                    "algorithm": "sha256-first-8-bytes-mod-100",
                    "session_id": canary_session_id,
                    "bucket": canary_bucket,
                    "request_id": canary_routed_chat["request_id"],
                    "versionset_id": canary_routed_chat["versionset_id"],
                    "provider_log": canary_routed_log,
                },
                "observation": verification_context["canary_observation"],
                "probe_count": len(post_candidate["responses"]),
                "error_count": 0,
                "verification_eval_id": post_gate["eval_id"],
                "rule_track": post_gate["rule_track"],
                "judge_track": post_gate["judge_track"],
                "live_provider_e2e": post_gate["live_provider_e2e"],
            },
        )
        terminal_path = _write(output_dir / "promote-receipt.json", promote_receipt)
        notification_path = _write(
            output_dir / "notification-receipt.json",
            {
                "adapter": "feishu:live",
                "notification_id": notification["notification_id"],
                "state": notification["state"],
                "payload": notification["payload"],
                "closure_context": authority.get("release_closure"),
            },
        )
        audit_path = _write(output_dir / "audit-events.json", audit_rows)
        domain_path = _write(
            output_dir / "domain-events.json",
            {
                "required_catalog": sorted(REQUIRED_DOMAIN_EVENTS),
                "observed": sorted(str(value) for value in observed_domain_events),
                "rows": domain_rows,
                "inbound_dedup": inbound_dedup,
            },
        )
        outbox_path = _write(
            output_dir / "outbox-receipts.json",
            {
                "dispatches": relay_receipts,
                "duplicate_dispatch": duplicate_dispatch,
                "outbox": outbox_rows,
                "receipts": delivery_rows,
            },
        )
        trust_path = _write(
            output_dir / "trust-decision.json",
            {
                "ledger": trust_row,
                "entry": trust_entry,
                "entry_row": trust_entries[0],
                "denial": trust_denial,
                "samples_added": 1,
                "promotion_decision": "denied",
                "three_of_three_reference": {
                    "wilson_two_sided_95_lower": 0.4385,
                    "threshold": 0.9,
                    "decision": "denied",
                    "test": "control-plane/tests/unit/test_outbox_dispatcher.py",
                },
            },
        )
        provider_trace_ids = sorted(
            {
                str(row.get("trace_id"))
                for row in provider_logs.values()
                if row.get("trace_id")
            }
        )
        trace_path = _write(
            output_dir / "trace.json",
            {
                "transaction_id": transaction_id,
                "correlation_id": case_id,
                "otel_exported": False,
                "otel_status": (
                    "no OpenTelemetry exporter receipt is configured; preserving authoritative "
                    "event/audit and provider trace references only"
                ),
                "trace_ids": sorted({*trace_references, *provider_trace_ids}),
                "authority_trace_references": trace_references,
                "provider_trace_ids": provider_trace_ids,
                "events": event_rows,
            },
        )
        source_sets = {
            "quality-officer": [
                row["event_id"] for row in event_rows if row.get("event_type") == "case.dispatched"
            ],
            "collector": [
                row["event_id"] for row in event_rows if row.get("event_type") == "complaint.received"
            ],
            "attributionist": [
                row["event_id"]
                for row in event_rows
                if row.get("event_type") in {"experiment.requested", "experiment.verdict_computed"}
            ],
            "repairer": [
                row["audit_id"]
                for row in audit_rows
                if row.get("action")
                in {"candidate.create.intent", "candidate.create.complete", "workorder.register"}
            ],
            "gatekeeper": [
                row["event_id"] for row in event_rows if row.get("event_type") == "eval.passed"
            ],
            "case-officer": [
                row["event_id"]
                for row in event_rows
                if row.get("event_type") in {"notification.sent", "case.closed"}
            ],
        }
        if any(not values for values in source_sets.values()):
            raise LiveRunError(
                "live agent execution trace lacks authoritative sources: "
                f"{sorted(key for key, values in source_sets.items() if not values)}"
            )
        agent_trace = _agent_trace_from_command(
            values["CASELOOP_B1_AGENT_TRACE_COMMAND"],
            phase="complete",
            context={
                "fixture_id": "B1",
                "run_id": run_id,
                "transaction_id": transaction_id,
                "case_id": case_id,
                "experiment_id": experiment_id,
                "workorder_id": workorder_id,
                "workorder_hash": workorder["hash"],
                "release_id": release_id,
                "notification_id": notification["notification_id"],
            },
            evidence_dir=output_dir,
            start_receipt=agent_trace_start,
            expected_sources=source_sets,
            expected_repairer_workorder_ref=agent_workorder_receipt["artifact_ref"],
            expected_phase_receipts=agent_phase_receipts,
        )
        agent_runs_path = _write(
            output_dir / "agent-runs.json",
            {
                "pool": "phase-1-fixed-warm-pool",
                "dynamic_scaling": False,
                "mode": "live-provider",
                "recording_kind": "agentteams-v1.2.1-taskflow-matrix-skill-trace",
                "agent_runtime_executed": True,
                "agent_domain_authority": False,
                "domain_executor": "deterministic-caseloop-control-plane",
                "source_ids_semantics": "post-action-authority-observations-not-agent-causation",
                "not_live_agent_execution": False,
                "platform": agent_trace["platform"],
                "platform_version": agent_trace["platform_version"],
                "team": agent_trace["team"],
                "session_id": agent_trace["session_id"],
                "room_id": agent_trace["room_id"],
                "dispatch_event_id": agent_trace["dispatch_event_id"],
                "completion_event_id": agent_trace["completion_event_id"],
                "configured_skills": [agent_trace["skill"]],
                "attestation_key_id": agentteams_public_key_id(
                    values["CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY"]
                ),
                "start_receipt": agent_trace_start,
                "completion_receipt": agent_trace,
                "phase_receipts": agent_phase_receipts,
                "repairer_workorder_receipt": agent_workorder_receipt,
                "skill_invocations": [
                    {
                        "role": row["role"],
                        "task_id": row["task_id"],
                        "skill": row["skill"],
                    }
                    for row in agent_trace["runs"]
                ],
                "runs": agent_trace["runs"],
            },
        )
        repository_start = _git("rev-parse", "origin/main")
        repository_end = _git("rev-parse", "HEAD")
        commits_path = _write(
            output_dir / "commits.json",
            {
                "branch": _git("branch", "--show-current"),
                "repository_start_commit": repository_start,
                "repository_end_commit": repository_end,
                "origin_main_commit": repository_start,
                "working_tree": working_tree_before_run,
                "working_tree_observed_at": "before evidence directory creation",
                "recent": _git("log", "--oneline", "-12"),
            },
        )
        contract_replay_path = _write(
            output_dir / "contract-replay-report.json",
            {
                "contract": {
                    "status": contract_semantics[0],
                    "n_passed": contract_semantics[1],
                    "n_failed": contract_semantics[2],
                },
                "replay": {
                    "status": replay_semantics[0],
                    "n_passed": replay_semantics[1],
                    "n_failed": replay_semantics[2],
                },
                "gate_policy_profile": "live",
                "live_provider_counted_as_pass": False,
            },
        )
        provider_origins_path = _write(
            output_dir / "provider-origins.json",
            {
                "schema_version": "0.1.0",
                "stepfun": {
                    "runner_provider_origin": values["STEPFUN_BASE_URL"],
                    "quality_log_origins": sorted(
                        {
                            str(row.get("provider_origin") or "")
                            for row in provider_logs.values()
                        }
                    ),
                    "canary_response_origin": canary_routed_chat.get(
                        "provider_origin"
                    ),
                    "required_origin": _OFFICIAL_STEPFUN_BASE_URL,
                },
                "feishu": {
                    "inbound_provider_origin": inbound["provider_origin"],
                    "notification_provider_origin": (
                        (notification.get("payload") or {}).get("receipt") or {}
                    ).get("provider_origin"),
                    "required_origin": _OFFICIAL_FEISHU_BASE_URL,
                },
            },
        )
        live_checks = [
            {
                "check": "official_provider_origins_pinned",
                "passed": values["STEPFUN_BASE_URL"] == _OFFICIAL_STEPFUN_BASE_URL
                and {
                    row.get("provider_origin") for row in provider_logs.values()
                }
                == {_OFFICIAL_STEPFUN_BASE_URL}
                and canary_routed_chat.get("provider_origin")
                == _OFFICIAL_STEPFUN_BASE_URL
                and inbound.get("provider_origin") == _OFFICIAL_FEISHU_BASE_URL
                and ((notification.get("payload") or {}).get("receipt") or {}).get(
                    "provider_origin"
                )
                == _OFFICIAL_FEISHU_BASE_URL,
                "evidence_refs": [_artifact_ref(provider_origins_path)],
            },
            {
                "check": "agentteams_taskflow_matrix_skill_trace_verified",
                "passed": agent_trace.get("platform") == "AgentTeams"
                and len(agent_trace.get("runs") or []) == len(_B1_AGENT_ROLES),
                "evidence_refs": [_artifact_ref(agent_runs_path)],
            },
            {
                "check": "complaint_inbox_deduplicated",
                "passed": duplicate.get("duplicate") is True
                and duplicate.get("case_id") == case_id
                and len(duplicate_audits) == 1,
                "evidence_refs": [_artifact_ref(domain_path)],
            },
            {
                "check": "post_injection_message_acquired",
                "passed": (message_acquisition.get("receipt") or {}).get("message_id")
                == transaction_id,
                "evidence_refs": [
                    _artifact_ref(output_dir / "feishu-message-acquisition.json")
                ],
            },
            {
                "check": "complaint_created_after_injection",
                "passed": complaint_created_at > injection_created_at,
                "evidence_refs": [
                    _artifact_ref(frozen_path),
                    _artifact_ref(domain_path),
                ],
            },
            {
                "check": "complaint_matches_b1_fixture",
                "passed": inbound.get("text_digest") == complaint_fixture.text_digest
                and inbox_payload.get("source_text_digest") == complaint_fixture.text_digest
                and complaint_payload.get("source_text_digest")
                == complaint_fixture.text_digest,
                "evidence_refs": [
                    _artifact_ref(frozen_path),
                    _artifact_ref(domain_path),
                ],
            },
            {
                "check": "prompt_attribution_recomputed",
                "passed": verdict.get("payload", {}).get("verdict") == "ATTRIBUTED"
                and verdict.get("payload", {}).get("attributed_layer") == "prompt",
                "evidence_refs": [
                    _artifact_ref(attribution_dir / "evidence-bundle.json"),
                    _artifact_ref(attribution_dir / "attribution-report.json"),
                ],
            },
            {
                "check": "initial_gate_authoritatively_passed",
                "passed": initial_gate.get("overall_status") == "passed"
                and any(row.get("eval_id") == initial_gate.get("eval_id") for row in authority.get("gate_reports") or []),
                "evidence_refs": [_artifact_ref(gates_path)],
            },
            {
                "check": "three_approval_grants_consumed",
                "passed": len(approval_rows) == 3
                and all((row.get("persistence") or {}).get("status") == "consumed" for row in approval_rows),
                "evidence_refs": [_artifact_ref(approvals_path)],
            },
            {
                "check": "stage_canary_promote_operations_succeeded",
                "passed": {row.get("kind") for row in operation_rows}
                == {"stage", "canary", "promote"}
                and all(row.get("status") == "succeeded" for row in operation_rows),
                "evidence_refs": [_artifact_ref(release_receipts_path)],
            },
            {
                "check": "post_canary_gate_authoritatively_passed",
                "passed": post_gate.get("overall_status") == "passed"
                and verification_receipt.get("state") == "VERIFYING",
                "evidence_refs": [_artifact_ref(gates_path), _artifact_ref(canary_path)],
            },
            {
                "check": "promoted_versionset_refetched_active",
                "passed": promoted_versionset.get("status") == "active"
                and promoted_versionset.get("digest") == candidate.get("digest"),
                "evidence_refs": [_artifact_ref(output_dir / "promoted-versionset.json")],
            },
            {
                "check": "feishu_provider_receipt_accepted",
                "passed": notification.get("state") == "SENT"
                and ((notification.get("payload") or {}).get("receipt") or {}).get("provider")
                == "feishu",
                "evidence_refs": [_artifact_ref(notification_path)],
            },
            {
                "check": "case_archived",
                "passed": final_case.get("state") == "CLOSED",
                "evidence_refs": [_artifact_ref(domain_path)],
            },
            {
                "check": "outbox_redelivery_idempotent",
                "passed": duplicate_dispatch.get("claimed") == 0
                and all(row.get("status") == "SENT" for row in outbox_rows),
                "evidence_refs": [_artifact_ref(outbox_path)],
            },
            {
                "check": "trust_action_sample_recorded_and_denied",
                "passed": trust_entries[0].get("action_ref") == release_id
                and (trust_entry.get("promotion") or {}).get("eligible") is False
                and trust_denial.get("action_ref") == release_id,
                "evidence_refs": [_artifact_ref(trust_path)],
            },
            {
                "check": "quality_provider_logs_bound",
                "passed": len(provider_logs)
                == sum(len(cell.get("results") or []) for cell in bundle.get("cells", {}).values())
                + len(initial_candidate.get("responses") or [])
                + len(post_candidate.get("responses") or [])
                + 1,
                "evidence_refs": [_artifact_ref(probe_outputs_path)],
            },
            {
                "check": "injection_intent_receipt_audit_bound",
                "passed": injection_row.get("state") == "COMPLETED"
                and len(injection_events) == 2
                and len(injection_audits) == 2,
                "evidence_refs": [_artifact_ref(frozen_path), _artifact_ref(audit_path)],
            },
            {
                "check": "audit_and_trace_chain_present",
                "passed": bool(audit_rows and event_rows and trace_references),
                "evidence_refs": [_artifact_ref(audit_path), _artifact_ref(trace_path)],
            },
        ]
        live_passed = sum(1 for check in live_checks if check["passed"] is True)
        live_failed = len(live_checks) - live_passed
        live_status = "passed" if live_passed > 0 and live_failed == 0 else "failed"
        live_test_path = _write(
            output_dir / "live-provider-test-report.json",
            {
                "status": live_status,
                "passed": live_passed,
                "failed": live_failed,
                "checks": live_checks,
                "provider_calls_attempted": True,
                "replay_fallback_used": False,
            },
        )
        if live_status != "passed":
            raise LiveRunError("semantic live-provider checks did not all pass")
        artifacts = {
            "agent_runs": _artifact_ref(agent_runs_path),
            "frozen_versionset": _artifact_ref(frozen_path),
            "experiment_plan": _artifact_ref(plan_path),
            "probes": _artifact_ref(probes_path),
            "probe_outputs": _artifact_ref(probe_outputs_path),
            "evidence_bundle": _artifact_ref(attribution_dir / "evidence-bundle.json"),
            "attribution_report": _artifact_ref(attribution_dir / "attribution-report.json"),
            "workorder": _artifact_ref(output_dir / "workorder.json"),
            "gate_report": _artifact_ref(gates_path),
            "approval_grants": _artifact_ref(approvals_path),
            "release_receipts": _artifact_ref(release_receipts_path),
            "canary_metrics": _artifact_ref(canary_path),
            "release_terminal_receipt": _artifact_ref(terminal_path),
            "notification_receipt": _artifact_ref(notification_path),
            "audit_events": _artifact_ref(audit_path),
            "domain_events": _artifact_ref(domain_path),
            "outbox_receipts": _artifact_ref(outbox_path),
            "trust_decision": _artifact_ref(trust_path),
            "trace": _artifact_ref(trace_path),
            "commits": _artifact_ref(commits_path),
            "contract_replay_report": _artifact_ref(contract_replay_path),
            "live_provider_report": _artifact_ref(live_test_path),
        }
        target_ref = {
            key: promoted_versionset[key] for key in ("versionset_id", "digest", "revision")
        }
        completed_at = _iso()
        manifest = {
            "schema_version": "0.1.0",
            "fixture_id": "B1",
            "run_id": run_id,
            "mode": "live-provider",
            "status": "passed",
            "started_at": started_at,
            "completed_at": completed_at,
            "transaction_id": transaction_id,
            "case_id": case_id,
            "experiment_id": experiment_id,
            "workorder_id": workorder_id,
            "workorder_hash": workorder["hash"],
            "release_id": release_id,
            "notification_id": notification["notification_id"],
            "versions": {
                "repository_start_commit": repository_start,
                "repository_end_commit": repository_end,
                "base_versionset": bad_ref,
                "target_versionset": target_ref,
            },
            "outcomes": {
                "deduplicated": True,
                "attribution": {"decision": "ATTRIBUTED", "fault_layer": "prompt"},
                "release": "promoted",
                "notification": {"status": "sent", "provider": "feishu"},
                "case": "CLOSED",
                "trust": {
                    "samples_added": 1,
                    "autonomy_state": trust_row["autonomy_state"],
                    "promotion_decision": "denied",
                    "wilson_lower": (trust_entry.get("wilson") or {})["lower"],
                },
                "live_provider": "passed",
            },
            "artifacts": artifacts,
            "test_reports": [
                {
                    "kind": "contract",
                    "status": contract_semantics[0],
                    "passed": contract_semantics[1],
                    "failed": contract_semantics[2],
                    "report_ref": _artifact_ref(contract_path),
                },
                {
                    "kind": "replay",
                    "status": replay_semantics[0],
                    "passed": replay_semantics[1],
                    "failed": replay_semantics[2],
                    "report_ref": _artifact_ref(replay_path),
                },
                {
                    "kind": "live-provider",
                    "status": live_status,
                    "passed": live_passed,
                    "failed": live_failed,
                    "report_ref": _artifact_ref(live_test_path),
                },
            ],
            "external_blockers": [],
        }
        manifest_path, verification = _publish_verified_manifest(
            output_dir=output_dir,
            manifest=manifest,
            live_test_path=live_test_path,
        )
        completed_phases.append("evidence_bundle_self_verified")
        report = {
            "schema_version": "0.1.0",
            "run_id": run_id,
            "mode": "live-provider",
            "status": "completed",
            "started_at": started_at,
            "completed_at": completed_at,
            "provider_calls_attempted": True,
            "provider_checks_passed": True,
            "replay_fallback_used": False,
            "ids": ids,
            "completed_phases": completed_phases,
            "manifest": _artifact_ref(manifest_path),
            "verification": verification,
            "evidence_dir": str(output_dir),
        }
        _write(report_path, report)
        print(
            json.dumps(
                {
                    "status": "completed",
                    "report": str(report_path),
                    "manifest": str(manifest_path),
                    "case_id": case_id,
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - turn every unknown into explicit failure evidence
        compensation: dict[str, Any] = {
            "status": "not_required_before_injection",
        }
        blockers = [str(exc)]
        if injection_attempted and "promoted" not in completed_phases:
            try:
                compensation = _compensate_incomplete_b1(
                    api,
                    values,
                    run_id=run_id,
                    quarantine_versionset_id=ids.get("candidate_versionset_id"),
                )
            except Exception as recovery_exc:  # noqa: BLE001 - UNKNOWN must be explicit
                compensation = {
                    "status": "unknown",
                    "error": str(recovery_exc),
                    "retry": {
                        "method": "POST",
                        "path": "/v1/demo/faults/B1/recover",
                        "body": {
                            "expected_active_fault_versionset_id": values[
                                "CASELOOP_B1_BAD_VERSIONSET_ID"
                            ],
                            "restore_versionset_id": values[
                                "CASELOOP_B1_GOOD_VERSIONSET_ID"
                            ],
                            **(
                                {
                                    "quarantine_versionset_id": ids[
                                        "candidate_versionset_id"
                                    ]
                                }
                                if ids.get("candidate_versionset_id")
                                else {}
                            ),
                            "idempotency_key": f"b1-live-recover-{run_id}",
                        },
                        "authority": "Release Controller CONTROL_PLANE_TOKEN only",
                    },
                }
                blockers.append(
                    "B1 compensation UNKNOWN; retry the exact Release Controller request in the report: "
                    + str(recovery_exc)
                )
        elif injection_attempted:
            compensation = {
                "status": "not_required_after_promote",
                "reason": "the fixed VersionSet was authoritatively promoted",
            }
        report = {
            "schema_version": "0.1.0",
            "run_id": run_id,
            "mode": "live-provider",
            "status": "failed",
            "started_at": started_at,
            "completed_at": _iso(),
            "provider_calls_attempted": provider_calls_attempted,
            "blockers": blockers,
            "completed_phases": completed_phases,
            "ids": ids,
            "compensation": compensation,
            "replay_fallback_used": False,
            "evidence_dir": str(output_dir),
        }
        _write(report_path, report)
        print(
            json.dumps(
                {
                    "status": "failed",
                    "report": str(report_path),
                    "error": str(exc),
                    "compensation": compensation,
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
