#!/usr/bin/env python3
"""Reload and semantically verify one CaseLoop B1 evidence manifest."""
from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import unquote, urlparse

from jsonschema import Draft202012Validator, FormatChecker


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eval-harness"))
sys.path.insert(0, str(REPO_ROOT / "control-plane"))

from app.services.attribution import validate_attribution_artifacts  # noqa: E402
from app.services.b1_fixture import (  # noqa: E402
    B1FixtureError,
    load_b1_complaint_fixture,
)
from agentteams_attestation import (  # noqa: E402
    AgentTeamsAttestationError,
    public_key_id as agentteams_public_key_id,
    verify_receipt as verify_agentteams_receipt,
)
from eval_harness.probe_judge import judge_probe  # noqa: E402
from eval_harness.probe_loader import frozen_digest, load_probe_set  # noqa: E402


class B1ValidationError(RuntimeError):
    pass


_OFFICIAL_STEPFUN_BASE_URL = "https://api.stepfun.com/step_plan/v1"
_OFFICIAL_FEISHU_BASE_URL = "https://open.feishu.cn"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise B1ValidationError(message)


def _require_unique_agent_taskflow_ids(rows: list[dict[str, Any]]) -> None:
    """Reject one AgentTeams task/receipt being relabelled as multiple workers."""

    for field in ("task_id", "ack_receipt_id", "submit_receipt_id"):
        identities = [row.get(field) for row in rows]
        _require(
            all(isinstance(value, str) and bool(value) for value in identities)
            and len(set(identities)) == len(rows),
            f"live AgentTeams trace reuses a cross-role {field}",
        )


def _require_fixed_agent_worker_roles(workers: Any, expected_roles: set[str]) -> None:
    """Independently reproduce the fixed warm-pool cardinality constraint."""

    _require(
        isinstance(workers, list)
        and len(workers) == len(expected_roles)
        and len(set(workers)) == len(workers)
        and set(workers) == expected_roles,
        "live AgentTeams start receipt duplicates or omits a fixed worker role",
    )


def _require_official_provider_origins(provider_origins: dict[str, Any]) -> None:
    """Reject labels backed by a non-official StepFun or Feishu endpoint."""

    _require(
        (provider_origins.get("stepfun") or {}).get("runner_provider_origin")
        == _OFFICIAL_STEPFUN_BASE_URL
        and (provider_origins.get("stepfun") or {}).get("quality_log_origins")
        == [_OFFICIAL_STEPFUN_BASE_URL]
        and (provider_origins.get("stepfun") or {}).get("canary_response_origin")
        == _OFFICIAL_STEPFUN_BASE_URL
        and (provider_origins.get("stepfun") or {}).get("required_origin")
        == _OFFICIAL_STEPFUN_BASE_URL
        and (provider_origins.get("feishu") or {}).get("inbound_provider_origin")
        == _OFFICIAL_FEISHU_BASE_URL
        and (provider_origins.get("feishu") or {}).get(
            "notification_provider_origin"
        )
        == _OFFICIAL_FEISHU_BASE_URL
        and (provider_origins.get("feishu") or {}).get("required_origin")
        == _OFFICIAL_FEISHU_BASE_URL,
        "live provider evidence does not pin official StepFun and Feishu origins",
    )


def _canonical_digest(value: Any, *, prefix: bool = True) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"sha256:{digest}" if prefix else digest


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_dt(value: Any, label: str) -> datetime:
    _require(isinstance(value, str) and value, f"{label} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise B1ValidationError(f"{label} is not a valid timestamp") from exc
    _require(parsed.tzinfo is not None, f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _parse_epoch_ms(value: Any, label: str) -> datetime:
    raw = str(value or "")
    _require(
        re.fullmatch(r"[1-9][0-9]{12}", raw) is not None,
        f"{label} must be a millisecond epoch timestamp",
    )
    try:
        return datetime.fromtimestamp(int(raw) / 1000, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise B1ValidationError(f"{label} is outside the supported range") from exc


def _pytest_counts(output: str) -> tuple[int, int]:
    passed = sum(int(value) for value in re.findall(r"(\d+) passed", output))
    failed = sum(int(value) for value in re.findall(r"(\d+) failed", output))
    return passed, failed


def _report_semantics(kind: str, value: dict[str, Any]) -> tuple[str, int, int]:
    """Derive status/counts from the report content, never its manifest label."""

    if "exit_code" in value:
        exit_code = value.get("exit_code")
        output = value.get("output")
        _require(isinstance(exit_code, int) and not isinstance(exit_code, bool), f"{kind} exit_code is invalid")
        _require(isinstance(output, str), f"{kind} pytest output is missing")
        passed, failed = _pytest_counts(output)
        if exit_code != 0 and failed == 0:
            failed = 1
        status = "passed" if exit_code == 0 and passed > 0 and failed == 0 else "failed"
        return status, passed, failed
    if kind == "contract":
        checks = value.get("checks")
        errors = value.get("errors")
        _require(isinstance(checks, int) and checks > 0, "embedded contract report has no checks")
        _require(isinstance(errors, list), "embedded contract report errors is invalid")
        failed = len(errors)
        passed = max(0, checks - failed)
        return ("passed" if failed == 0 and passed > 0 else "failed"), passed, failed
    if kind == "replay":
        checks = value.get("checks")
        _require(isinstance(checks, dict) and checks, "embedded replay report has no checks")
        results = [item for item in checks.values() if isinstance(item, dict)]
        _require(len(results) == len(checks), "embedded replay report contains invalid check results")
        failed = sum(1 for item in results if item.get("passed") is not True)
        passed = len(results) - failed
        return ("passed" if failed == 0 and passed > 0 else "failed"), passed, failed
    if kind == "live-provider":
        status = value.get("status")
        _require(status in {"passed", "failed", "blocked"}, "live-provider report status is invalid")
        passed = value.get("passed", 0)
        failed = value.get("failed", 0)
        _require(
            isinstance(passed, int) and not isinstance(passed, bool)
            and isinstance(failed, int) and not isinstance(failed, bool),
            "live-provider report counts are invalid",
        )
        checks = value.get("checks")
        if checks is not None:
            _require(isinstance(checks, list) and checks, "live-provider checks are invalid or empty")
            _require(
                all(
                    isinstance(item, dict)
                    and isinstance(item.get("check"), str)
                    and item.get("check")
                    and isinstance(item.get("passed"), bool)
                    for item in checks
                ),
                "live-provider check evidence is malformed",
            )
            derived_passed = sum(1 for item in checks if item["passed"] is True)
            derived_failed = len(checks) - derived_passed
            _require(
                (passed, failed) == (derived_passed, derived_failed),
                "live-provider counts differ from enumerated checks",
            )
        if status == "passed":
            _require(passed > 0 and failed == 0, "live-provider report claims an empty/failing pass")
        return status, passed, failed
    raise B1ValidationError(f"unsupported test report kind: {kind}")


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    _require(completed.returncode == 0, f"git {' '.join(args)} failed during evidence verification")
    return completed.stdout.strip()


def _resolve_uri(uri: str, *, run_dir: Path) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme == "file" and parsed.path:
        path = Path(unquote(parsed.path)).resolve()
    elif parsed.scheme == "repo" and parsed.path:
        path = (REPO_ROOT / unquote(parsed.path).lstrip("/")).resolve()
    else:
        raise B1ValidationError(f"unsupported evidence URI: {uri!r}")
    try:
        path.relative_to(run_dir)
    except ValueError as exc:
        raise B1ValidationError(f"evidence URI escapes its run directory: {uri}") from exc
    _require(path.is_file(), f"evidence artifact is missing: {path}")
    return path


def _load_ref(ref: dict[str, Any], *, run_dir: Path, label: str) -> tuple[Path, Any]:
    _require(isinstance(ref, dict), f"{label} reference is not an object")
    path = _resolve_uri(str(ref.get("uri") or ""), run_dir=run_dir)
    actual = _file_digest(path)
    _require(actual == ref.get("digest"), f"{label} file digest mismatch")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise B1ValidationError(f"{label} is not valid JSON: {path}") from exc
    return path, value


def _validate_probe_outputs(
    bundle: dict[str, Any],
    *,
    run_dir: Path,
    experiment_id: str,
    case_id: str,
    cell_versionsets: dict[str, dict[str, Any]],
) -> int:
    count = 0
    for arm in ("C", "RP", "RK", "RM", "G"):
        cell = bundle["cells"][arm]
        versions = cell["versions"]
        for trial in cell["results"]:
            path = _resolve_uri(str(trial.get("output_ref") or ""), run_dir=run_dir)
            raw = json.loads(path.read_text(encoding="utf-8"))
            _require(
                _canonical_digest(raw) == trial.get("output_digest"),
                f"raw probe output digest mismatch: {arm}/{trial.get('probe_id')}",
            )
            expected = {
                "experiment_id": experiment_id,
                "case_id": case_id,
                "arm": arm,
                "probe_id": trial.get("probe_id"),
                "repetition": trial.get("repetition"),
                "recovered": trial.get("recovered"),
            }
            _require(
                all(raw.get(key) == value for key, value in expected.items()),
                f"raw probe output identity mismatch: {arm}/{trial.get('probe_id')}",
            )
            actual_versions = {
                "prompt_digest": raw.get("prompt_digest"),
                "kb_manifest_digest": raw.get("kb_manifest_digest"),
                "model_digest": raw.get("model_digest"),
            }
            _require(
                actual_versions == versions,
                f"raw probe output VersionSet mismatch: {arm}/{trial.get('probe_id')}",
            )
            actual_versionset = {
                "versionset_id": raw.get("versionset_id"),
                "digest": raw.get("versionset_digest"),
                "revision": raw.get("versionset_revision"),
            }
            _require(
                actual_versionset == cell_versionsets[arm],
                f"raw probe output exact VersionSet mismatch: {arm}/{trial.get('probe_id')}",
            )
            _require(
                raw.get("status") == "recorded-replay",
                f"isolated B1 raw output falsely claims provider execution: {arm}/{trial.get('probe_id')}",
            )
            count += 1
    _require(count > 0, "evidence bundle contains no raw probe outputs")
    return count


def _load_inline_json_ref(ref: dict[str, Any], *, label: str) -> dict[str, Any]:
    _require(isinstance(ref, dict), f"{label} reference is invalid")
    uri = ref.get("uri")
    _require(isinstance(uri, str), f"{label} URI is missing")
    header, separator, encoded = uri.partition(",")
    _require(
        separator == "," and header == "data:application/json;base64",
        f"{label} is not immutable inline JSON",
    )
    _require(len(encoded) <= 2_700_000, f"{label} is oversized")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise B1ValidationError(f"{label} base64 is invalid") from exc
    _require(len(raw) <= 2_000_000, f"{label} exceeds 2 MB")
    _require(
        "sha256:" + hashlib.sha256(raw).hexdigest() == ref.get("digest"),
        f"{label} inline digest mismatch",
    )
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise B1ValidationError(f"{label} JSON is invalid") from exc
    _require(isinstance(value, dict), f"{label} is not a JSON object")
    return value


def _load_notification_body(uri: Any) -> bytes:
    _require(isinstance(uri, str), "notification body_ref is missing")
    header, separator, encoded = uri.partition(",")
    _require(
        separator == "," and header == "data:text/plain;base64",
        "live notification body is not immutable inline text",
    )
    try:
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise B1ValidationError("live notification body base64 is invalid") from exc
    _require(len(body) <= 1_000_000, "live notification body exceeds 1 MB")
    return body


def _validate_live_gate(
    *,
    name: str,
    report: dict[str, Any],
    workorder: dict[str, Any],
    target_versionset: dict[str, Any],
    probe_digest: str,
    probe_version: str,
    provider_logs: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    gate_schema = json.loads(
        (REPO_ROOT / "contracts" / "schemas" / "gate-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    _require(
        not list(Draft202012Validator(gate_schema, format_checker=FormatChecker()).iter_errors(report)),
        f"{name} live GateReport schema failed",
    )
    _require(report.get("policy_profile") == "live", f"{name} Gate is not live policy")
    _require(report.get("overall_status") == "passed", f"{name} live Gate did not pass")
    _require(
        report.get("subject", {}).get("target_versionset_digest")
        == workorder.get("target_versionset_digest")
        == target_versionset.get("digest"),
        f"{name} Gate target digest differs from WorkOrder/promoted VersionSet",
    )
    _require(
        report.get("subject", {}).get("probe_set_digest") == probe_digest,
        f"{name} Gate probe set differs from frozen probes",
    )
    _require(
        all(
            report.get(track, {}).get("status") == "passed"
            for track in ("rule_track", "judge_track", "deterministic_tests", "live_provider_e2e")
        ),
        f"{name} Gate contains a non-passing track",
    )
    rule_checks = report.get("rule_track", {}).get("checks") or []
    _require(
        rule_checks and all(check.get("status") == "passed" for check in rule_checks),
        f"{name} passed rule track contains a failing/empty check list",
    )
    _require(
        report["judge_track"].get("judge_model_digest")
        != report["judge_track"].get("athlete_model_digest"),
        f"{name} Gate judge is not independent",
    )
    artifact_values: dict[str, dict[str, Any]] = {}
    for index, ref in enumerate(report.get("artifact_refs") or []):
        artifact_values[ref.get("uri")] = _load_inline_json_ref(
            ref, label=f"{name}.artifact_refs[{index}]"
        )
    for suite in report.get("deterministic_tests", {}).get("suites") or []:
        kind = suite.get("kind")
        _require(kind in {"contract", "replay"}, f"{name} has an unknown suite kind")
        value = artifact_values.get(suite.get("report_ref"))
        _require(value is not None, f"{name} {kind} suite has no bound artifact")
        _require(
            _report_semantics(kind, value)
            == (suite.get("status"), suite.get("n_passed"), suite.get("n_failed")),
            f"{name} {kind} suite summary is false",
        )
        _require(
            suite.get("status") == "passed"
            and suite.get("n_passed", 0) > 0
            and suite.get("n_failed") == 0,
            f"{name} passed deterministic track contains a failing/empty {kind} suite",
        )
    live_suites = report.get("live_provider_e2e", {}).get("suites") or []
    _require(len(live_suites) == 1, f"{name} live suite is missing or ambiguous")
    candidate = artifact_values.get(live_suites[0].get("report_ref"))
    _require(isinstance(candidate, dict), f"{name} live candidate artifact is missing")
    _require(
        candidate.get("target_versionset_id") == target_versionset.get("versionset_id")
        and candidate.get("target_versionset_digest") == target_versionset.get("digest")
        and isinstance(candidate.get("target_revision"), int)
        and candidate.get("target_revision") <= target_versionset.get("revision")
        and candidate.get("dataset_digest") == probe_digest
        and candidate.get("dataset_version") == probe_version,
        f"{name} live candidate identity is not frozen to its target/dataset",
    )
    probe_set = load_probe_set(REPO_ROOT)
    probes = probe_set.by_id()
    _require(
        candidate.get("dataset_id") == probe_set.probe_set_id,
        f"{name} candidate dataset id is not the canonical frozen probe set",
    )
    responses = candidate.get("responses")
    _require(isinstance(responses, list), f"{name} live responses are missing")
    indexed = {row.get("probe_id"): row for row in responses if isinstance(row, dict)}
    _require(
        len(indexed) == len(responses) and set(indexed) == set(probes),
        f"{name} live response coverage is incomplete",
    )
    answers: dict[str, str] = {}
    athlete_models: set[str] = set()
    for probe_id, row in indexed.items():
        answer = row.get("answer")
        request_id = row.get("request_id")
        log = provider_logs.get(request_id)
        _require(
            isinstance(answer, str)
            and isinstance(request_id, str)
            and isinstance(log, dict)
            and row.get("provider_status") == "ok",
            f"{name} live response {probe_id} has no authoritative provider evidence",
        )
        expected_log = {
            "request_id": request_id,
            "status": "ok",
            "provider_origin": _OFFICIAL_STEPFUN_BASE_URL,
            "trace_id": row.get("trace_id"),
            "versionset_id": target_versionset.get("versionset_id"),
            "prompt_digest": row.get("prompt_digest"),
            "kb_manifest_digest": row.get("kb_manifest_digest"),
            "model_digest": row.get("model_digest"),
            "answer_digest": "sha256:" + hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        }
        _require(
            all(log.get(key) == value for key, value in expected_log.items()),
            f"{name} response {probe_id} differs from its Quality provider log",
        )
        _require(
            row.get("provider_origin") == _OFFICIAL_STEPFUN_BASE_URL,
            f"{name} response {probe_id} did not use the official StepFun origin",
        )
        passed, _reasons = judge_probe(probes[probe_id], answer)
        _require(passed, f"{name} passed rule track contains a failing raw answer: {probe_id}")
        answers[probe_id] = answer
        athlete_models.add(str(row.get("model_digest")))
    _require(
        athlete_models == {report["judge_track"]["athlete_model_digest"]},
        f"{name} athlete model evidence differs from the GateReport",
    )
    scores = {
        row.get("probe_id"): row
        for row in report.get("judge_track", {}).get("scores") or []
        if isinstance(row, dict)
    }
    judge_rows = candidate.get("judge_responses")
    _require(isinstance(judge_rows, list), f"{name} judge response evidence is missing")
    judge_index = {row.get("probe_id"): row for row in judge_rows if isinstance(row, dict)}
    _require(
        len(judge_index) == len(judge_rows)
        and set(judge_index) == set(probes)
        and set(scores) == set(probes),
        f"{name} judge evidence coverage is incomplete",
    )
    judge_request_ids: set[str] = set()
    for probe_id, row in judge_index.items():
        raw = row.get("raw_response")
        parsed = row.get("parsed")
        request_id = row.get("provider_request_id")
        _require(
            isinstance(raw, str)
            and isinstance(parsed, dict)
            and isinstance(request_id, str)
            and request_id
            and request_id not in judge_request_ids
            and row.get("model_digest") == report["judge_track"]["judge_model_digest"]
            and row.get("answer_digest")
            == "sha256:" + hashlib.sha256(answers[probe_id].encode("utf-8")).hexdigest()
            and row.get("raw_response_digest")
            == "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            f"{name} judge evidence {probe_id} binding is invalid",
        )
        judge_request_ids.add(request_id)
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        try:
            decoded = json.loads(match.group(0)) if match else None
            decoded_score = float(decoded.get("score")) if isinstance(decoded, dict) else None
        except (ValueError, TypeError, json.JSONDecodeError):
            decoded = None
            decoded_score = None
        declared = scores[probe_id]
        threshold = float(report["judge_track"].get("pass_threshold", 0.0))
        _require(
            isinstance(decoded, dict)
            and isinstance(decoded.get("pass"), bool)
            and decoded_score is not None
            and math.isfinite(decoded_score)
            and parsed.get("pass") is decoded.get("pass")
            and float(parsed.get("score", -1)) == max(0.0, min(1.0, decoded_score))
            and declared.get("pass") is parsed.get("pass")
            and float(declared.get("score", -1)) == float(parsed.get("score", -2)),
            f"{name} judge evidence {probe_id} was not parsed faithfully",
        )
        _require(
            declared.get("pass") is True
            and math.isfinite(float(declared.get("score", -1)))
            and float(declared.get("score", -1)) >= threshold,
            f"{name} passed judge track contains a failing score: {probe_id}",
        )
    _require(
        (live_suites[0].get("status"), live_suites[0].get("n_passed"), live_suites[0].get("n_failed"))
        == ("passed", len(probes), 0),
        f"{name} live suite counts differ from raw evidence",
    )
    return candidate, len(responses)


def _validate_persisted_live_gate(
    *,
    name: str,
    row: dict[str, Any],
    report: dict[str, Any],
    candidate: dict[str, Any],
    workorder: dict[str, Any],
    expected_authorization_digest: str | None,
) -> str:
    """Recompute every persisted projection used by Release Controller."""

    subject = report["subject"]
    report_hash = _canonical_digest(report, prefix=False)
    evidence_digest = _canonical_digest(report.get("artifact_refs") or [])
    candidate_digest = _canonical_digest(
        {
            "workorder_id": workorder["workorder_id"],
            "target_versionset_id": candidate["target_versionset_id"],
            "target_versionset_digest": subject["target_versionset_digest"],
            "target_revision": candidate["target_revision"],
            "dataset_id": candidate["dataset_id"],
            "dataset_version": candidate["dataset_version"],
            "dataset_digest": subject["probe_set_digest"],
            "regression_suite_digest": subject["regression_suite_digest"],
            "evidence_digest": evidence_digest,
        }
    )
    binding_digest = _canonical_digest(
        {
            "eval_id": report["eval_id"],
            "report_hash": report_hash,
            "candidate_digest": candidate_digest,
            "workorder_hash": workorder["hash"],
            "target_versionset_id": candidate["target_versionset_id"],
            "target_versionset_digest": subject["target_versionset_digest"],
            "target_revision": candidate["target_revision"],
            "dataset_id": candidate["dataset_id"],
            "dataset_version": candidate["dataset_version"],
            "dataset_digest": subject["probe_set_digest"],
            "evidence_digest": evidence_digest,
        }
    )
    expected = {
        "eval_id": report["eval_id"],
        "report_id": report["report_id"],
        "workorder_id": workorder["workorder_id"],
        "workorder_hash": workorder["hash"],
        "target_versionset_id": candidate["target_versionset_id"],
        "target_versionset_digest": subject["target_versionset_digest"],
        "target_revision": candidate["target_revision"],
        "dataset_id": candidate["dataset_id"],
        "dataset_version": candidate["dataset_version"],
        "dataset_digest": subject["probe_set_digest"],
        "evidence_digest": evidence_digest,
        "candidate_digest": candidate_digest,
        "report_hash": report_hash,
        "binding_digest": binding_digest,
        "authorization_digest": expected_authorization_digest,
        "overall_status": report["overall_status"],
        "report": report,
    }
    _require(
        all(row.get(key) == value for key, value in expected.items())
        and isinstance(row.get("bound_at"), str)
        and row["bound_at"],
        f"{name} persisted GateReport projection/binding mismatch",
    )
    return binding_digest


def _validate_live_inbound_notification_binding(
    *,
    manifest: dict[str, Any],
    domain: dict[str, Any],
    frozen: dict[str, Any],
    notification: dict[str, Any],
) -> None:
    """Prove dedupe and reply-to-origin from one exact Feishu complaint."""

    evidence = domain.get("inbound_dedup") or {}
    acquisition = evidence.get("message_acquisition") or {}
    inbound = evidence.get("inbound") or {}
    inbox = evidence.get("inbox") or {}
    raw_payload = inbox.get("raw_payload") or {}
    complaint_event = evidence.get("complaint_event") or {}
    complaint_payload = complaint_event.get("payload") or {}
    case_projection = evidence.get("case_projection") or {}
    case_payload = case_projection.get("payload") or {}
    duplicate_audits = evidence.get("duplicate_audits") or []
    injection_aggregate = (frozen.get("injection_authority") or {}).get("aggregate") or {}
    notification_payload = notification.get("payload") or {}
    transaction_id = manifest.get("transaction_id")
    case_id = manifest.get("case_id")
    expected_attachment = f"feishu-text-digest:{inbound.get('text_digest')}"
    try:
        complaint_fixture = load_b1_complaint_fixture()
    except B1FixtureError as exc:
        raise B1ValidationError(f"repository-owned B1 fixture is unavailable: {exc}") from exc
    _require(
        inbound.get("provider") == "feishu"
        and inbound.get("provider_origin") == _OFFICIAL_FEISHU_BASE_URL
        and inbound.get("message_id") == transaction_id
        and isinstance(inbound.get("text_digest"), str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", inbound["text_digest"]) is not None
        and inbound.get("thread_ref")
        == f"{inbound.get('channel')}:{transaction_id}",
        "live Feishu inbound identity/digest is invalid",
    )
    _require(
        inbound.get("text_digest") == complaint_fixture.text_digest
        and frozen.get("complaint_fixture_ref") == complaint_fixture.repository_ref
        and frozen.get("complaint_text_digest") == complaint_fixture.text_digest,
        "live Feishu complaint does not match the repository-owned B1 fixture",
    )
    acquisition_request = acquisition.get("request") or {}
    acquisition_receipt = acquisition.get("receipt") or {}
    acquisition_command = acquisition.get("command") or {}
    requested_at = _parse_dt(
        acquisition.get("requested_at"), "Feishu acquisition requested_at"
    )
    completed_at = _parse_dt(
        acquisition.get("completed_at"), "Feishu acquisition completed_at"
    )
    injected_at = _parse_dt(
        (frozen.get("injection_receipt") or {}).get("injected_at"),
        "live B1 injected_at",
    )
    _require(
        acquisition.get("schema_version") == "0.1.0"
        and acquisition.get("adapter")
        == "external-post-injection-feishu-message-command"
        and requested_at >= injected_at
        and completed_at >= requested_at
        and acquisition_request.get("phase") == "await-post-injection-complaint"
        and acquisition_request.get("provider") == "feishu"
        and acquisition_request.get("fixture_ref") == complaint_fixture.repository_ref
        and acquisition_request.get("fixture_text_digest")
        == complaint_fixture.text_digest
        and acquisition_request.get("injection_operation_id")
        == injection_aggregate.get("aggregate_id")
        and acquisition_request.get("not_before")
        == (frozen.get("injection_receipt") or {}).get("injected_at")
        and acquisition_receipt
        == {
            "schema_version": "0.1.0",
            "provider": "feishu",
            "message_id": transaction_id,
        }
        and acquisition_command.get("exit_code") == 0
        and isinstance(acquisition_command.get("executable"), str)
        and bool(acquisition_command.get("executable"))
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(acquisition_command.get("argv_digest") or ""),
        )
        is not None,
        "live Feishu message was not acquired after the authoritative B1 injection",
    )
    _require(
        _parse_epoch_ms(inbound.get("create_time"), "live Feishu create_time")
        > _parse_dt(
            (frozen.get("injection_receipt") or {}).get("injected_at"),
            "live B1 injected_at",
        ),
        "live Feishu complaint predates the B1 injection",
    )
    _require(
        inbox.get("case_id") == case_id
        and inbox.get("source") == "webhook"
        and inbox.get("external_id") == transaction_id
        and inbox.get("disposition") == "FILED"
        and raw_payload.get("external_id") == transaction_id
        and raw_payload.get("channel") == inbound.get("channel")
        and raw_payload.get("thread_ref") == inbound.get("thread_ref")
        and raw_payload.get("demo_fault_injection_id")
        == injection_aggregate.get("aggregate_id")
        and raw_payload.get("provider_origin") == _OFFICIAL_FEISHU_BASE_URL
        and raw_payload.get("provider_create_time") == inbound.get("create_time")
        and raw_payload.get("source_text_digest") == complaint_fixture.text_digest,
        "live Feishu Inbox row is not bound to the Case/injection",
    )
    _require(
        complaint_event.get("aggregate_type") == "case"
        and complaint_event.get("aggregate_id") == case_id
        and complaint_event.get("event_type") == "complaint.received"
        and complaint_payload.get("external_id") == transaction_id
        and complaint_payload.get("channel") == inbound.get("channel")
        and complaint_payload.get("thread_ref") == inbound.get("thread_ref")
        and complaint_payload.get("demo_fault_injection_id")
        == injection_aggregate.get("aggregate_id")
        and complaint_payload.get("provider_origin") == _OFFICIAL_FEISHU_BASE_URL
        and complaint_payload.get("provider_create_time") == inbound.get("create_time")
        and complaint_payload.get("source_text_digest") == complaint_fixture.text_digest
        and expected_attachment in (complaint_payload.get("attachments") or []),
        "live complaint event is not bound to the original Feishu message",
    )
    _require(
        case_projection.get("aggregate_id") == case_id
        and case_payload.get("provider_origin") == _OFFICIAL_FEISHU_BASE_URL
        and case_payload.get("provider_create_time") == inbound.get("create_time")
        and case_payload.get("source_text_digest") == complaint_fixture.text_digest,
        "live Case projection is not bound to the official Feishu message",
    )
    _require(
        len(duplicate_audits) == 1
        and duplicate_audits[0].get("action") == "complaint.duplicate"
        and duplicate_audits[0].get("target") == case_id
        and duplicate_audits[0].get("result") == "success",
        "live complaint dedupe has no exact authoritative duplicate audit",
    )
    _require(
        notification_payload.get("channel") == inbound.get("channel")
        and notification_payload.get("thread_ref") == inbound.get("thread_ref"),
        "live notification did not reply to the original Feishu channel/thread",
    )


def _validate_live_b1(
    *,
    manifest: dict[str, Any],
    artifacts: dict[str, Any],
    reports: dict[str, Any],
    run_dir: Path,
    completed_at: datetime,
    allow_dirty: bool,
) -> dict[str, Any]:
    _require(manifest.get("status") == "passed", "live B1 manifest is not passed")
    _require(
        reports["live-provider"]["status"] == "passed"
        and reports["live-provider"]["passed"] > 0
        and reports["live-provider"]["failed"] == 0,
        "live B1 has no passing live-provider report",
    )
    live_report = artifacts["live_provider_report"]
    expected_live_checks = {
        "official_provider_origins_pinned",
        "agentteams_taskflow_matrix_skill_trace_verified",
        "complaint_inbox_deduplicated",
        "post_injection_message_acquired",
        "complaint_created_after_injection",
        "complaint_matches_b1_fixture",
        "prompt_attribution_recomputed",
        "initial_gate_authoritatively_passed",
        "three_approval_grants_consumed",
        "stage_canary_promote_operations_succeeded",
        "post_canary_gate_authoritatively_passed",
        "promoted_versionset_refetched_active",
        "feishu_provider_receipt_accepted",
        "case_archived",
        "outbox_redelivery_idempotent",
        "trust_action_sample_recorded_and_denied",
        "quality_provider_logs_bound",
        "injection_intent_receipt_audit_bound",
        "audit_and_trace_chain_present",
    }
    live_check_rows = live_report.get("checks") or []
    live_check_names = [
        row.get("check") for row in live_check_rows if isinstance(row, dict)
    ]
    _require(
        len(live_check_names) == len(live_check_rows) == len(expected_live_checks)
        and len(set(live_check_names)) == len(live_check_names)
        and set(live_check_names) == expected_live_checks,
        "live-provider semantic check catalog is incomplete or duplicated",
    )
    for row in live_check_rows:
        refs = row.get("evidence_refs")
        _require(
            row.get("passed") is True and isinstance(refs, list) and bool(refs),
            f"live-provider check is not passed/evidenced: {row.get('check')}",
        )
        for index, ref in enumerate(refs):
            _load_ref(
                ref,
                run_dir=run_dir,
                label=f"live_provider_check.{row['check']}[{index}]",
            )
    origin_check = next(
        row
        for row in live_check_rows
        if row.get("check") == "official_provider_origins_pinned"
    )
    provider_origins = _load_ref(
        origin_check["evidence_refs"][0],
        run_dir=run_dir,
        label="official_provider_origins",
    )
    _require_official_provider_origins(provider_origins)
    case_id = manifest["case_id"]
    experiment_id = manifest["experiment_id"]
    workorder_id = manifest["workorder_id"]
    release_id = manifest["release_id"]
    notification_id = manifest["notification_id"]
    base_versionset = manifest["versions"]["base_versionset"]
    target_versionset = manifest["versions"]["target_versionset"]

    frozen = artifacts["frozen_versionset"]
    injection = frozen.get("injection_receipt") or {}
    injection_authority = frozen.get("injection_authority") or {}
    injection_aggregate = injection_authority.get("aggregate") or {}
    injection_events = injection_authority.get("events") or []
    injection_audits = injection_authority.get("audits") or []
    _require(frozen.get("badcase_injected") is True, "live B1 fault injection is absent")
    _require(
        injection.get("fault_id") == "B1"
        and injection.get("fault_versionset_id") == base_versionset["versionset_id"]
        and injection.get("fault_versionset_digest") == base_versionset["digest"]
        and injection.get("previous_versionset_id") == frozen["known_good_versionset"]["versionset_id"],
        "live B1 fault injection receipt binding is invalid",
    )
    injection_fields = {
        "fault_id",
        "previous_versionset_id",
        "previous_versionset_digest",
        "previous_revision",
        "fault_versionset_id",
        "fault_versionset_digest",
        "fault_revision",
        "injected_at",
    }
    persisted_injection = (injection_aggregate.get("payload") or {}).get("receipt") or {}
    _require(
        injection_aggregate.get("aggregate_type") == "demo_fault_injection"
        and injection_aggregate.get("state") == "COMPLETED"
        and all(persisted_injection.get(key) == injection.get(key) for key in injection_fields)
        and persisted_injection.get("duplicate")
        is injection.get("provider_duplicate")
        and injection.get("duplicate") is False
        and len(injection_events) == 2
        and {row.get("event_type") for row in injection_events}
        == {"demo_fault.inject_started", "demo_fault.inject_completed"}
        and all(
            row.get("aggregate_id") == injection_aggregate.get("aggregate_id")
            for row in injection_events
        )
        and len(injection_audits) == 2
        and {row.get("action") for row in injection_audits}
        == {"demo_fault.B1.inject.intent", "demo_fault.B1.injected"}
        and all(
            row.get("target") == injection_aggregate.get("aggregate_id")
            for row in injection_audits
        ),
        "live B1 persisted injection intent/completion/audit evidence is invalid",
    )
    frozen_binding = {
        "versions": frozen["component_digests"],
        "cell_versionsets": frozen["cell_versionsets"],
    }
    _require(frozen.get("digest") == _canonical_digest(frozen_binding), "live frozen digest mismatch")
    _require(frozen.get("active_bad_versionset") == base_versionset, "live base is not injected bad VersionSet")

    plan = artifacts["experiment_plan"]
    protocol = plan.get("protocol") or {}
    _require(
        plan.get("transaction_id") == manifest["transaction_id"]
        and plan.get("case_id") == case_id
        and plan.get("experiment_id") == experiment_id,
        "live experiment plan identity mismatch",
    )
    complaint_fixture = load_b1_complaint_fixture()
    _require(
        plan.get("complaint_fixture_ref") == complaint_fixture.repository_ref
        and plan.get("complaint_text_digest") == complaint_fixture.text_digest,
        "live experiment plan does not bind the repository-owned B1 complaint",
    )
    _require(protocol.get("execution_profile") == "live", "live experiment profile is not live")
    _require(
        protocol.get("versions") == frozen["component_digests"]
        and protocol.get("cell_versionsets") == frozen["cell_versionsets"],
        "live experiment plan differs from frozen VersionSets",
    )
    probes_artifact = artifacts["probes"]
    probe_set = load_probe_set(REPO_ROOT)
    probe_digest = frozen_digest(probe_set)
    _require(
        probes_artifact.get("digest") == protocol.get("probe_set_digest") == probe_digest,
        "live frozen probe digest mismatch",
    )
    _require(set(probes_artifact.get("selected") or []) == set(probe_set.by_id()), "live probes are incomplete")

    bundle = artifacts["evidence_bundle"]
    attribution = artifacts["attribution_report"]
    _require(
        bundle.get("case_id") == case_id
        and bundle.get("experiment_id") == experiment_id
        and attribution.get("case_id") == case_id
        and attribution.get("experiment_id") == experiment_id,
        "live attribution identity mismatch",
    )
    _require(
        attribution.get("evidence_bundle_ref", {}).get("digest") == _canonical_digest(bundle),
        "live AttributionReport does not bind the EvidenceBundle",
    )
    probe_outputs = artifacts["probe_outputs"]
    provider_logs = probe_outputs.get("quality_provider_logs")
    _require(isinstance(provider_logs, dict) and provider_logs, "live Quality logs are missing")
    adjudication = validate_attribution_artifacts(
        experiment_id=experiment_id,
        case_id=case_id,
        frozen=protocol,
        evidence_bundle=bundle,
        attribution_report=attribution,
        delta_min=0.2,
        provider_log_resolver=lambda request_id: provider_logs[request_id],
    )
    _require(
        adjudication.get("verdict") == "ATTRIBUTED"
        and adjudication.get("attributed_layer") == "prompt",
        "live raw-answer adjudication is not ATTRIBUTED/prompt",
    )
    expected_trials = sum(len(cell.get("results") or []) for cell in bundle["cells"].values())
    raw_outputs = probe_outputs.get("raw_outputs")
    _require(isinstance(raw_outputs, list) and len(raw_outputs) == expected_trials, "live raw output index is incomplete")
    for item in raw_outputs:
        trial = item.get("trial") or {}
        output = item.get("output") or {}
        log = provider_logs.get(output.get("request_id")) or {}
        _require(
            _canonical_digest(output) == trial.get("output_digest"),
            "live raw output index digest mismatch",
        )
        _require(
            output.get("provider_origin") == _OFFICIAL_STEPFUN_BASE_URL
            and log.get("provider_origin") == _OFFICIAL_STEPFUN_BASE_URL,
            "live attribution output/log did not use the official StepFun origin",
        )

    workorder = artifacts["workorder"]
    _require(
        workorder.get("workorder_id") == workorder_id
        and workorder.get("case_id") == case_id
        and workorder.get("base_versionset_digest") == base_versionset["digest"]
        and workorder.get("target_versionset_digest") == target_versionset["digest"],
        "live WorkOrder identity/target mismatch",
    )
    computed_hash = _canonical_digest(
        {key: value for key, value in workorder.items() if key != "hash"}, prefix=False
    )
    _require(
        computed_hash == workorder.get("hash") == manifest["workorder_hash"],
        "live WorkOrder hash mismatch",
    )
    gates = artifacts["gate_report"]
    _require(gates.get("policy_profile") == "live", "live gate wrapper policy mismatch")
    initial_candidate, initial_probe_count = _validate_live_gate(
        name="initial",
        report=gates["initial"],
        workorder=workorder,
        target_versionset=target_versionset,
        probe_digest=probe_digest,
        probe_version=probe_set.version,
        provider_logs=provider_logs,
    )
    post_candidate, post_probe_count = _validate_live_gate(
        name="post_canary",
        report=gates["post_canary"],
        workorder=workorder,
        target_versionset=target_versionset,
        probe_digest=probe_digest,
        probe_version=probe_set.version,
        provider_logs=provider_logs,
    )
    initial_provider_ids = {
        row.get("request_id") for row in initial_candidate.get("responses") or []
    }
    post_provider_ids = {row.get("request_id") for row in post_candidate.get("responses") or []}
    initial_judge_ids = {
        row.get("provider_request_id") for row in initial_candidate.get("judge_responses") or []
    }
    post_judge_ids = {
        row.get("provider_request_id") for row in post_candidate.get("judge_responses") or []
    }
    _require(
        gates["initial"]["eval_id"] != gates["post_canary"]["eval_id"]
        and initial_provider_ids.isdisjoint(post_provider_ids)
        and initial_judge_ids.isdisjoint(post_judge_ids),
        "post-canary Gate reused initial eval/provider/judge evidence",
    )
    _require(
        workorder.get("gate_report_ref")
        == {
            "uri": f"eval://{gates['initial']['eval_id']}",
            "digest": f"sha256:{_canonical_digest(gates['initial'], prefix=False)}",
        },
        "live WorkOrder does not bind the initial GateReport",
    )

    approvals = artifacts["approval_grants"].get("grants") or []
    _require(len(approvals) == 3, "live initial/canary/promote ApprovalGrants are incomplete")
    _require(len({row.get("nonce") for row in approvals}) == 3, "live ApprovalGrant nonce reused")
    action_grants = {
        (row.get("authorization") or {}).get("action"): row
        for row in approvals
        if row.get("authorization") is not None
    }
    initial_grants = [row for row in approvals if row.get("authorization") is None]
    _require(len(initial_grants) == 1 and set(action_grants) == {"canary", "promote"}, "live approvals have wrong actions")
    for grant in approvals:
        persistence = grant.get("persistence") or {}
        _require(
            grant.get("workorder_id") == workorder_id
            and grant.get("workorder_hash") == workorder["hash"]
            and grant.get("decision") == "approved"
            and grant.get("nonce_consumed") is True
            and persistence.get("status") == "consumed"
            and persistence.get("decision") == "approved",
            "live ApprovalGrant binding/consumption is invalid",
        )
        decided = _parse_dt(grant.get("decided_at"), "ApprovalGrant.decided_at")
        consumed = _parse_dt(persistence.get("consumed_at"), "ApprovalGrant.consumed_at")
        expiry = _parse_dt(grant.get("expiry"), "ApprovalGrant.expiry")
        _require(decided <= consumed <= completed_at < expiry, "live ApprovalGrant time ordering is invalid")
    _require(
        initial_grants[0].get("nonce") == workorder.get("nonce")
        and _parse_dt(initial_grants[0]["expiry"], "initial ApprovalGrant.expiry")
        == _parse_dt(workorder["expiry"], "WorkOrder.expiry"),
        "live initial ApprovalGrant nonce/expiry differs from WorkOrder",
    )

    receipts = artifacts["release_receipts"]
    _require(
        receipts.get("start", {}).get("release_id") == release_id
        and receipts.get("start", {}).get("workorder_hash") == workorder["hash"]
        and receipts.get("start", {}).get("versionset_id") == target_versionset["versionset_id"],
        "live release start receipt mismatch",
    )
    _require(
        receipts.get("verification", {}).get("verification") == "passed"
        and receipts.get("promote", {}).get("state") == "COMPLETED",
        "live post-canary verification/promote did not complete",
    )
    operations = receipts.get("controller_operations") or []
    by_kind = {row.get("kind"): row for row in operations}
    _require(len(operations) == 3 and set(by_kind) == {"stage", "canary", "promote"}, "live controller operations incomplete")
    expected_revision = initial_candidate["target_revision"]
    for kind, expected_status in (("stage", "staged"), ("canary", "canary"), ("promote", "active")):
        row = by_kind[kind]
        result = row.get("result") or {}
        result_body = result.get("result") or {}
        _require(
            row.get("release_id") == release_id
            and row.get("status") == "succeeded"
            and row.get("expected_revision") == expected_revision
            and result.get("operation_id") == row.get("remote_operation_id")
            and result.get("kind") == kind
            and result.get("status") == "succeeded"
            and result.get("versionset_id") == target_versionset["versionset_id"]
            and result_body.get("status") == expected_status
            and result_body.get("revision") == expected_revision + 1,
            f"live {kind} ControllerOperation/Quality receipt mismatch",
        )
        step = receipts.get(kind) or {}
        _require(
            step.get("operation_id") == row.get("operation_id")
            and step.get("kind") == kind
            and step.get("status") == "succeeded",
            f"live {kind} API receipt differs from persisted operation",
        )
        expected_revision += 1
    _require(expected_revision == target_versionset["revision"], "live promoted revision chain is incomplete")
    _require(
        post_candidate["target_revision"] == by_kind["canary"]["result"]["result"]["revision"],
        "post-canary Gate did not execute against the canary revision",
    )
    _require(
        by_kind["canary"].get("approval_id") == action_grants["canary"].get("approval_id")
        and by_kind["promote"].get("approval_id") == action_grants["promote"].get("approval_id"),
        "live operations do not bind consumed action ApprovalGrants",
    )
    for action in ("canary", "promote"):
        authorization = action_grants[action]["authorization"]
        _require(
            authorization.get("release_id") == release_id
            and authorization.get("target_revision") == by_kind[action]["expected_revision"]
            and authorization.get("params_digest") == _canonical_digest(authorization.get("params")),
            f"live {action} authorization binding mismatch",
        )
    canary_params = action_grants["canary"]["authorization"]["params"]
    promote_params = action_grants["promote"]["authorization"]["params"]
    _require(
        canary_params.get("percent")
        == by_kind["canary"].get("result", {}).get("result", {}).get("canary_percent"),
        "live canary ApprovalGrant percent differs from the Quality operation",
    )
    _require(
        promote_params.get("expected_active_digest") == base_versionset["digest"]
        and promote_params.get("verification_eval_id") == gates["post_canary"]["eval_id"]
        and promote_params.get("verification_report_hash")
        == _canonical_digest(gates["post_canary"], prefix=False),
        "live promote ApprovalGrant is not bound to base revision/post-canary Gate",
    )
    persisted_workorders = receipts.get("persisted_workorders") or []
    persisted_gates = receipts.get("persisted_gate_reports") or []
    _require(
        len(persisted_workorders) == 1
        and persisted_workorders[0].get("workorder_id") == workorder_id
        and persisted_workorders[0].get("case_id") == case_id
        and persisted_workorders[0].get("hash") == workorder["hash"]
        and persisted_workorders[0].get("channel") == workorder["channel"]
        and persisted_workorders[0].get("payload") == workorder,
        "live persisted WorkOrder differs from the immutable artifact",
    )
    persisted_gate_by_id = {row.get("eval_id"): row for row in persisted_gates}
    _require(
        len(persisted_gate_by_id) == len(persisted_gates) == 2
        and set(persisted_gate_by_id)
        == {gates["initial"]["eval_id"], gates["post_canary"]["eval_id"]},
        "live GateReports were not exported exactly once from persistence",
    )
    initial_binding_digest = _validate_persisted_live_gate(
        name="initial",
        row=persisted_gate_by_id[gates["initial"]["eval_id"]],
        report=gates["initial"],
        candidate=initial_candidate,
        workorder=workorder,
        expected_authorization_digest=None,
    )
    verification_authorization_digest = _canonical_digest(
        {
            "workorder_id": workorder_id,
            "workorder_hash": workorder["hash"],
            "initial_eval_id": gates["initial"]["eval_id"],
            "initial_report_hash": _canonical_digest(gates["initial"], prefix=False),
            "initial_binding_digest": initial_binding_digest,
            "target_versionset_id": initial_candidate["target_versionset_id"],
            "target_versionset_digest": gates["initial"]["subject"][
                "target_versionset_digest"
            ],
        }
    )
    _validate_persisted_live_gate(
        name="post_canary",
        row=persisted_gate_by_id[gates["post_canary"]["eval_id"]],
        report=gates["post_canary"],
        candidate=post_candidate,
        workorder=workorder,
        expected_authorization_digest=verification_authorization_digest,
    )
    _require(artifacts["release_terminal_receipt"] == receipts["promote"], "live terminal receipt differs from promote")

    canary = artifacts["canary_metrics"]
    observation = canary.get("observation") or {}
    routing = canary.get("routing") or {}
    routed = probe_outputs.get("canary_routed_request") or {}
    routed_request_id = routed.get("request_id")
    routed_log = provider_logs.get(routed_request_id) if isinstance(routed_request_id, str) else None
    session_id = routing.get("session_id")
    routed_bucket = (
        int.from_bytes(hashlib.sha256(session_id.encode("utf-8")).digest()[:8], "big") % 100
        if isinstance(session_id, str) and session_id
        else None
    )
    _require(
        isinstance(routed_log, dict)
        and routed.get("status") == "ok"
        and routed.get("provider_origin") == _OFFICIAL_STEPFUN_BASE_URL
        and routed_log.get("provider_origin") == _OFFICIAL_STEPFUN_BASE_URL
        and routed.get("versionset_id") == post_candidate.get("target_versionset_id")
        and routed.get("answer_digest")
        == "sha256:" + hashlib.sha256(str(routed.get("answer") or "").encode("utf-8")).hexdigest()
        and routing.get("algorithm") == "sha256-first-8-bytes-mod-100"
        and routing.get("request_id") == routed_request_id
        and routing.get("versionset_id") == routed.get("versionset_id")
        and routing.get("bucket") == routed_bucket
        and isinstance(routed_bucket, int)
        and routed_bucket < canary_params.get("percent", 0),
        "live routed canary request identity/bucket is invalid",
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
        _require(routed_log.get(key) == routed.get(key), f"live routed canary log differs on {key}")
        _require(
            (routing.get("provider_log") or {}).get(key) == routed_log.get(key),
            f"live canary metrics provider log differs on {key}",
        )
    _require(
        canary.get("mode") == "live-provider"
        and canary.get("traffic_routed") is True
        and canary.get("verification_probes_used_router") is False
        and observation.get("complete") is True
        and observation.get("elapsed_seconds", 0) >= observation.get("required_seconds", 1)
        and canary.get("probe_count") == post_probe_count
        and canary.get("error_count") == 0
        and canary.get("canary_percent") == canary_params.get("percent")
        and canary.get("verification_eval_id") == gates["post_canary"]["eval_id"],
        "live exact-target canary verification evidence is invalid",
    )

    notification = artifacts["notification_receipt"]
    payload = notification.get("payload") or {}
    closure = notification.get("closure_context") or {}
    provider_receipt = payload.get("receipt") or {}
    _require(
        notification.get("notification_id") == notification_id
        and notification.get("state") == "SENT"
        and closure.get("release_id") == release_id
        and closure.get("case_id") == case_id
        and closure.get("status") == "queued",
        "live Feishu closure identity/state mismatch",
    )
    for key in ("channel", "thread_ref", "body_ref", "body_digest"):
        _require(closure.get(key) == payload.get(key), f"live notification differs from closure {key}")
    thread_parts = str(payload.get("thread_ref") or "").split(":", 2)
    _require(
        len(thread_parts) == 3
        and thread_parts[0] == "feishu"
        and payload.get("channel") == f"feishu:{thread_parts[1]}",
        "live Feishu thread_ref is not feishu:<chat_id>:<message_id>",
    )
    body = _load_notification_body(payload.get("body_ref"))
    _require(
        "sha256:" + hashlib.sha256(body).hexdigest() == payload.get("body_digest"),
        "live notification body digest mismatch",
    )
    _require(
        provider_receipt.get("status") == "sent"
        and provider_receipt.get("provider") == "feishu"
        and provider_receipt.get("provider_origin") == _OFFICIAL_FEISHU_BASE_URL
        and provider_receipt.get("thread_ref") == payload.get("thread_ref")
        and provider_receipt.get("body_digest") == payload.get("body_digest")
        and provider_receipt.get("outbox_id") == payload.get("outbox_id")
        and provider_receipt.get("provider_message_id"),
        "live Feishu provider receipt is not exact",
    )

    domain = artifacts["domain_events"]
    _validate_live_inbound_notification_binding(
        manifest=manifest,
        domain=domain,
        frozen=frozen,
        notification=notification,
    )
    _require(set(domain.get("required_catalog") or []) <= set(domain.get("observed") or []), "live domain events missing")
    domain_rows = domain.get("rows") or []
    _require(domain_rows, "live domain event evidence is empty")
    domain_types = [row.get("event_type") for row in domain_rows]
    causal_order = [
        "CASE_CREATED",
        "ATTRIBUTION_DECIDED",
        "RELEASE_STARTED",
        "RELEASE_PROMOTED",
        "NOTIFICATION_SENT",
        "CASE_ARCHIVED",
    ]
    positions = [domain_types.index(kind) for kind in causal_order]
    _require(positions == sorted(positions), "live domain event causal order is invalid")
    gate_positions = [index for index, kind in enumerate(domain_types) if kind == "GATE_COMPLETED"]
    _require(
        len(gate_positions) == 2
        and gate_positions[0] < domain_types.index("RELEASE_STARTED")
        and gate_positions[1] < domain_types.index("RELEASE_PROMOTED"),
        "live initial/post-canary Gate events are not in the release chain",
    )
    outbox = artifacts["outbox_receipts"]
    _require(outbox.get("duplicate_dispatch", {}).get("claimed") == 0, "live outbox replay is not idempotent")
    outbox_rows = outbox.get("outbox") or []
    delivery_rows = outbox.get("receipts") or []
    _require(outbox_rows and all(row.get("status") == "SENT" for row in outbox_rows), "live outbox has non-SENT rows")
    receipt_map = {row.get("outbox_id"): row for row in delivery_rows}
    _require(len(receipt_map) == len(outbox_rows), "live outbox receipts are incomplete")
    for row in outbox_rows:
        _require(row.get("payload_digest") == _canonical_digest(row.get("payload")), "live outbox payload digest mismatch")
        delivery = receipt_map.get(row.get("outbox_id")) or {}
        _require(
            delivery.get("payload_digest") == row.get("payload_digest")
            and (delivery.get("receipt") or {}).get("payload_digest") == row.get("payload_digest"),
            "live outbox delivery receipt binding mismatch",
        )
    notification_outbox = next(
        (row for row in outbox_rows if row.get("event_type") == "NOTIFICATION_DELIVERY_REQUESTED"),
        None,
    )
    _require(
        notification_outbox is not None
        and notification_outbox.get("outbox_id") == payload.get("outbox_id")
        and notification_outbox.get("payload", {}).get("body_digest") == payload.get("body_digest"),
        "live notification outbox differs from Feishu receipt",
    )
    notification_delivery = receipt_map.get(payload.get("outbox_id")) or {}
    _require(
        notification_outbox.get("aggregate_id") == notification_id
        and notification_outbox.get("channel") == "notification.delivery"
        and notification_outbox.get("payload")
        == {
            "notification_id": notification_id,
            "case_id": case_id,
            "release_id": release_id,
            "channel": payload.get("channel"),
            "thread_ref": payload.get("thread_ref"),
            "body_ref": payload.get("body_ref"),
            "body_digest": payload.get("body_digest"),
        }
        and notification_outbox.get("receipt") == provider_receipt
        and notification_delivery.get("source_event_id")
        == notification_outbox.get("source_event_id")
        and notification_delivery.get("channel") == "notification.delivery"
        and notification_delivery.get("payload_digest")
        == notification_outbox.get("payload_digest")
        and notification_delivery.get("receipt") == provider_receipt,
        "live Feishu receipt is not bound to the exact notification outbox delivery",
    )

    trust = artifacts["trust_decision"]
    entry = trust.get("entry") or {}
    entry_row = trust.get("entry_row") or {}
    _require(
        trust.get("samples_added") == 1
        and entry_row.get("entry_id") == entry.get("entry_id")
        and entry_row.get("payload") == entry
        and entry_row.get("risk_class") == entry.get("risk_class")
        and entry_row.get("action_type") == entry.get("action_type")
        and entry_row.get("action_ref") == release_id
        and entry_row.get("epoch") == entry.get("evidence_epoch")
        and entry_row.get("outcome") == entry.get("outcome", {}).get("status")
        and entry_row.get("successes") == entry.get("epoch_successes")
        and entry_row.get("trials") == entry.get("epoch_trials")
        and entry.get("sample_rule") == "one_action_one_sample"
        and entry.get("outcome", {}).get("action_ref") == release_id
        and entry.get("outcome", {}).get("status") == "success"
        and entry.get("promotion", {}).get("eligible") is False
        and trust.get("denial", {}).get("trust_entry_id") == entry.get("entry_id"),
        "live Trust decision is not one exact denied release action",
    )
    reference = trust.get("three_of_three_reference") or {}
    _require(
        abs(float(reference.get("wilson_two_sided_95_lower", -1)) - 0.4385) < 0.0001
        and reference.get("threshold") == 0.9
        and reference.get("decision") == "denied",
        "live Trust 3/3 Wilson reference does not deny promotion",
    )
    _require(
        manifest["outcomes"]["trust"]["wilson_lower"] == entry.get("wilson", {}).get("lower"),
        "live manifest Trust outcome differs from immutable entry",
    )

    trace = artifacts["trace"]
    trace_events = trace.get("events") or []
    event_ids = {row.get("event_id") for row in trace_events}
    _require(trace.get("trace_ids") and trace_events, "live trace evidence is empty")
    _require(
        trace.get("otel_exported") is False
        and "no OpenTelemetry exporter receipt" in str(trace.get("otel_status") or ""),
        "live trace artifact falsely claims OpenTelemetry export",
    )
    _require(
        all(row.get("source_event_id") in event_ids for row in domain_rows),
        "live domain outbox references an event absent from trace",
    )
    promoted_event = next(
        (
            row
            for row in trace_events
            if row.get("aggregate_id") == release_id
            and row.get("event_type") == "release.promoted"
        ),
        None,
    )
    resolved_event = next(
        (
            row
            for row in trace_events
            if row.get("aggregate_id") == case_id and row.get("event_type") == "case.resolved"
        ),
        None,
    )
    queued_event = next(
        (
            row
            for row in trace_events
            if row.get("aggregate_id") == notification_id
            and row.get("event_type") == "notification.queued"
        ),
        None,
    )
    sent_event = next(
        (
            row
            for row in trace_events
            if row.get("aggregate_id") == notification_id
            and row.get("event_type") == "notification.sent"
        ),
        None,
    )
    closed_event = next(
        (
            row
            for row in trace_events
            if row.get("aggregate_id") == case_id and row.get("event_type") == "case.closed"
        ),
        None,
    )
    _require(
        all(
            row is not None
            for row in (promoted_event, resolved_event, queued_event, sent_event, closed_event)
        ),
        "live promote/resolve/notify/archive events are incomplete",
    )
    receipt_digest = _canonical_digest(provider_receipt)
    _require(
        promoted_event.get("causation_id") == by_kind["promote"].get("remote_operation_id")
        and promoted_event.get("correlation_id") == case_id
        and resolved_event.get("causation_id") == promoted_event.get("event_id")
        and resolved_event.get("payload", {}).get("release_id") == release_id
        and resolved_event.get("payload", {}).get("release_event_id")
        == promoted_event.get("event_id")
        and queued_event.get("causation_id") == resolved_event.get("event_id")
        and queued_event.get("correlation_id") == case_id
        and queued_event.get("payload")
        == {
            "case_id": case_id,
            "release_id": release_id,
            "channel": payload.get("channel"),
            "thread_ref": payload.get("thread_ref"),
            "body_ref": payload.get("body_ref"),
            "body_digest": payload.get("body_digest"),
            "outbox_id": payload.get("outbox_id"),
        }
        and notification_outbox.get("source_event_id") == queued_event.get("event_id")
        and sent_event.get("causation_id") == receipt_digest
        and sent_event.get("correlation_id") == case_id
        and sent_event.get("payload")
        == {
            "provider_message_id": provider_receipt.get("provider_message_id"),
            "provider": "feishu",
            "outbox_id": payload.get("outbox_id"),
            "payload_digest": notification_outbox.get("payload_digest"),
            "receipt_digest": receipt_digest,
        }
        and closed_event.get("causation_id") == sent_event.get("event_id"),
        "live promote-to-resolve-to-notify-to-archive causation chain is broken",
    )
    _require(
        closed_event.get("correlation_id") == case_id
        and closed_event.get("payload")
        == {
            "resolution": "fixed",
            "notification_id": notification_id,
            "notification_receipt_digest": receipt_digest,
        },
        "live Case archive is not bound to the exact Feishu provider receipt",
    )
    _require(
        entry.get("causation_id") == promoted_event.get("event_id")
        and entry_row.get("source_event_id") == promoted_event.get("event_id")
        and trust.get("ledger", {}).get("last_action_ref") == release_id
        and trust.get("ledger", {}).get("last_source_event_id")
        == promoted_event.get("event_id")
        and trust.get("denial", {}).get("action_ref") == release_id
        and trust.get("denial", {}).get("risk_class") == entry.get("risk_class")
        and trust.get("denial", {}).get("action_type") == entry.get("action_type")
        and trust.get("denial", {}).get("successes") == entry.get("epoch_successes")
        and trust.get("denial", {}).get("trials") == entry.get("epoch_trials"),
        "live Trust row/denial is not caused by the exact promoted event",
    )
    audits = artifacts["audit_events"]
    audit_ids = {row.get("audit_id") for row in audits}
    _require(audits, "live audit evidence is empty")
    _require(
        {row.get("event_id") for row in injection_events} <= event_ids
        and {row.get("audit_id") for row in injection_audits} <= audit_ids,
        "live injection authority copies are absent from the exported event/audit chain",
    )
    agent_runs = artifacts["agent_runs"]
    expected_roles = {
        "quality-officer",
        "collector",
        "attributionist",
        "repairer",
        "gatekeeper",
        "case-officer",
    }
    skill_path = REPO_ROOT / "agents" / "skills" / "caseloop-b1-loop" / "SKILL.md"
    _require(skill_path.is_file(), "repository B1 AgentTeams skill is missing")
    expected_skill = {
        "name": "caseloop-b1-loop",
        "digest": _file_digest(skill_path),
    }
    run_rows = agent_runs.get("runs") or []
    run_by_role = {
        row.get("role"): row for row in run_rows if isinstance(row, dict)
    }
    agentteams_public_key = os.environ.get("CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY", "")
    try:
        expected_attestation_key_id = agentteams_public_key_id(agentteams_public_key)
        start_agent_receipt = agent_runs.get("start_receipt") or {}
        completion_agent_receipt = agent_runs.get("completion_receipt") or {}
        verify_agentteams_receipt(start_agent_receipt, agentteams_public_key)
        verify_agentteams_receipt(completion_agent_receipt, agentteams_public_key)
    except AgentTeamsAttestationError as exc:
        raise B1ValidationError(
            f"live AgentTeams receipt attestation is invalid: {exc}"
        ) from exc
    _require(
        agent_runs.get("pool") == "phase-1-fixed-warm-pool"
        and agent_runs.get("dynamic_scaling") is False
        and agent_runs.get("recording_kind")
        == "agentteams-v1.2.1-taskflow-matrix-skill-trace"
        and agent_runs.get("agent_runtime_executed") is True
        and agent_runs.get("agent_domain_authority") is False
        and agent_runs.get("domain_executor") == "deterministic-caseloop-control-plane"
        and agent_runs.get("source_ids_semantics")
        == "post-action-authority-observations-not-agent-causation"
        and agent_runs.get("not_live_agent_execution") is False
        and agent_runs.get("platform") == "AgentTeams"
        and agent_runs.get("platform_version") == "v1.2.1"
        and agent_runs.get("team") == "caseloop-team"
        and isinstance(agent_runs.get("session_id"), str)
        and len(agent_runs["session_id"]) >= 8
        and re.fullmatch(r"![^\s:]+:[^\s]+", str(agent_runs.get("room_id") or ""))
        is not None
        and re.fullmatch(r"\$[^\s]+", str(agent_runs.get("dispatch_event_id") or ""))
        is not None
        and re.fullmatch(r"\$[^\s]+", str(agent_runs.get("completion_event_id") or ""))
        is not None
        and agent_runs.get("configured_skills") == [expected_skill],
        "live completion lacks a bound AgentTeams taskflow/Matrix/skill trace",
    )
    _require_fixed_agent_worker_roles(
        start_agent_receipt.get("workers"), expected_roles
    )
    _require(
        agent_runs.get("attestation_key_id") == expected_attestation_key_id
        and start_agent_receipt.get("phase") == "start"
        and start_agent_receipt.get("platform") == "AgentTeams"
        and start_agent_receipt.get("platform_version") == "v1.2.1"
        and start_agent_receipt.get("team") == "caseloop-team"
        and start_agent_receipt.get("session_id") == agent_runs["session_id"]
        and start_agent_receipt.get("room_id") == agent_runs["room_id"]
        and start_agent_receipt.get("dispatch_event_id")
        == agent_runs["dispatch_event_id"]
        and start_agent_receipt.get("skill") == expected_skill
        and completion_agent_receipt.get("phase") == "complete"
        and completion_agent_receipt.get("platform") == "AgentTeams"
        and completion_agent_receipt.get("platform_version") == "v1.2.1"
        and completion_agent_receipt.get("team") == "caseloop-team"
        and completion_agent_receipt.get("session_id") == agent_runs["session_id"]
        and completion_agent_receipt.get("room_id") == agent_runs["room_id"]
        and completion_agent_receipt.get("dispatch_event_id")
        == agent_runs["dispatch_event_id"]
        and completion_agent_receipt.get("completion_event_id")
        == agent_runs["completion_event_id"]
        and completion_agent_receipt.get("skill") == expected_skill
        and completion_agent_receipt.get("runs") == run_rows,
        "live AgentTeams attested start/completion receipts differ from the run record",
    )
    _require(
        len(run_by_role) == len(run_rows) == len(expected_roles)
        and set(run_by_role) == expected_roles,
        "live AgentTeams run record is missing or duplicates a fixed worker role",
    )
    _require_unique_agent_taskflow_ids(run_rows)
    expected_sources = {
        "quality-officer": {
            row.get("event_id") for row in trace_events if row.get("event_type") == "case.dispatched"
        },
        "collector": {
            row.get("event_id") for row in trace_events if row.get("event_type") == "complaint.received"
        },
        "attributionist": {
            row.get("event_id")
            for row in trace_events
            if row.get("event_type") in {"experiment.requested", "experiment.verdict_computed"}
        },
        "repairer": {
            row.get("audit_id")
            for row in audits
            if row.get("action")
            in {"candidate.create.intent", "candidate.create.complete", "workorder.register"}
        },
        "gatekeeper": {
            row.get("event_id") for row in trace_events if row.get("event_type") == "eval.passed"
        },
        "case-officer": {
            row.get("event_id")
            for row in trace_events
            if row.get("event_type") in {"notification.sent", "case.closed"}
        },
    }
    _require(all(expected_sources.values()), "live AgentTeams trace source catalog is incomplete")
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
    expected_phase_kinds = {
        "quality-officer": {"dispatch-review": "dispatch-intent"},
        "collector": {"collect-complaint": "complaint-evidence"},
        "attributionist": {"attribution-plan": "experiment-plan"},
        "repairer": {
            "repair-proposal": "repair-proposal",
            "workorder": "immutable-workorder",
        },
        "gatekeeper": {
            "initial-gate": "gate-request",
            "post-canary-gate": "gate-request",
        },
        "case-officer": {"closure": "closure-intent"},
    }
    phase_receipts = agent_runs.get("phase_receipts")
    _require(
        isinstance(phase_receipts, dict)
        and set(phase_receipts) == expected_roles
        and all(isinstance(rows, list) and bool(rows) for rows in phase_receipts.values()),
        "live AgentTeams pre-action product receipt catalog is incomplete",
    )
    repairer_workorder_receipt = agent_runs.get("repairer_workorder_receipt") or {}
    expected_phase_receipt_fields = {
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
    for role, run in run_by_role.items():
        sources = set(run.get("source_ids") or [])
        matrix_event_ids = run.get("matrix_event_ids") or []
        _require(
            set(run) == expected_run_fields
            and isinstance(run.get("task_id"), str)
            and bool(run["task_id"])
            and isinstance(run.get("ack_receipt_id"), str)
            and bool(run["ack_receipt_id"])
            and isinstance(run.get("submit_receipt_id"), str)
            and bool(run["submit_receipt_id"])
            and isinstance(matrix_event_ids, list)
            and bool(matrix_event_ids)
            and len(set(matrix_event_ids)) == len(matrix_event_ids)
            and all(re.fullmatch(r"\$[^\s]+", str(value)) for value in matrix_event_ids)
            and run.get("skill") == expected_skill
            and sources == expected_sources[role]
            and sources <= event_ids | audit_ids,
            f"live AgentTeams worker trace is not authoritative: {role}",
        )
        _, handoff = _load_ref(
            run.get("artifact_ref"),
            run_dir=run_dir,
            label=f"agent_handoff.{role}",
        )
        _require(
            handoff.get("schema_version") == "0.1.0"
            and handoff.get("kind") == "task-handoff"
            and handoff.get("role") == role
            and handoff.get("task_id") == run["task_id"]
            and handoff.get("session_id") == agent_runs["session_id"]
            and handoff.get("case_id") == case_id
            and set(handoff.get("source_ids") or []) == sources
            and isinstance(handoff.get("payload"), dict),
            f"live AgentTeams handoff artifact is invalid: {role}",
        )
        role_phase_receipts = phase_receipts[role]
        phase_by_name = {
            receipt.get("phase"): receipt
            for receipt in role_phase_receipts
            if isinstance(receipt, dict)
        }
        _require(
            len(phase_by_name) == len(role_phase_receipts)
            and set(phase_by_name) == set(expected_phase_kinds[role]),
            f"live AgentTeams pre-action phases are incomplete or duplicated: {role}",
        )
        product_refs = []
        products_by_phase: dict[str, dict[str, Any]] = {}
        for phase, receipt in phase_by_name.items():
            try:
                verify_agentteams_receipt(receipt, agentteams_public_key)
            except AgentTeamsAttestationError as exc:
                raise B1ValidationError(
                    f"live AgentTeams pre-action attestation is invalid: {role}/{phase}: {exc}"
                ) from exc
            receipt_matrix_ids = receipt.get("matrix_event_ids") or []
            _require(
                set(receipt) == expected_phase_receipt_fields
                and receipt.get("schema_version") == "0.1.0"
                and receipt.get("platform") == "AgentTeams"
                and receipt.get("platform_version") == "v1.2.1"
                and receipt.get("team") == "caseloop-team"
                and receipt.get("role") == role
                and receipt.get("task_id") == run["task_id"]
                and receipt.get("ack_receipt_id") == run["ack_receipt_id"]
                and receipt.get("session_id") == agent_runs["session_id"]
                and receipt.get("room_id") == agent_runs["room_id"]
                and receipt.get("skill") == expected_skill
                and isinstance(receipt_matrix_ids, list)
                and bool(receipt_matrix_ids)
                and len(set(receipt_matrix_ids)) == len(receipt_matrix_ids)
                and all(
                    re.fullmatch(r"\$[^\s]+", str(value)) for value in receipt_matrix_ids
                )
                and set(receipt_matrix_ids) <= set(matrix_event_ids),
                f"live AgentTeams pre-action receipt is detached: {role}/{phase}",
            )
            _, product = _load_ref(
                receipt.get("artifact_ref"),
                run_dir=run_dir,
                label=f"agent_product.{role}.{phase}",
            )
            product_refs.append(receipt["artifact_ref"])
            _require(
                product.get("schema_version") == "0.1.0"
                and product.get("kind") == expected_phase_kinds[role][phase]
                and product.get("role") == role
                and product.get("task_id") == run["task_id"]
                and product.get("session_id") == agent_runs["session_id"]
                and product.get("case_id") == case_id,
                f"live AgentTeams pre-action product is detached: {role}/{phase}",
            )
            if phase == "workorder":
                _require(
                    product.get("workorder") == workorder,
                    "live AgentTeams WorkOrder product differs from the immutable WorkOrder",
                )
            else:
                _require(
                    isinstance(product.get("payload"), dict),
                    f"live AgentTeams pre-action product payload is invalid: {role}/{phase}",
                )
            products_by_phase[phase] = product
        handoff_payload = handoff.get("payload") or {}
        _require(
            isinstance(handoff_payload.get("product_refs"), list)
            and sorted(handoff_payload["product_refs"], key=lambda item: item["uri"])
            == sorted(product_refs, key=lambda item: item["uri"]),
            f"live AgentTeams handoff omits its pre-action products: {role}",
        )

        inbound = (domain.get("inbound_dedup") or {}).get("inbound") or {}
        if role == "quality-officer":
            _require(
                products_by_phase["dispatch-review"].get("payload")
                == {
                    "case_id": case_id,
                    "injection_operation_id": injection_aggregate.get("aggregate_id"),
                    "next_role": "collector",
                },
                "live AgentTeams dispatch product differs from authority",
            )
        elif role == "collector":
            _require(
                products_by_phase["collect-complaint"].get("payload")
                == {
                    "message_id": manifest["transaction_id"],
                    "channel": inbound.get("channel"),
                    "thread_ref": inbound.get("thread_ref"),
                    "text_digest": inbound.get("text_digest"),
                },
                "live AgentTeams collector product differs from inbound authority",
            )
        elif role == "attributionist":
            _require(
                products_by_phase["attribution-plan"].get("payload")
                == {
                    "experiment_id": experiment_id,
                    "hypothesis_layer": "prompt",
                    "protocol": protocol,
                },
                "live AgentTeams attribution product differs from the frozen protocol",
            )
        elif role == "repairer":
            proposal = products_by_phase["repair-proposal"].get("payload", {}).get(
                "proposal"
            ) or {}
            proposal_content = proposal.get("content") or {}
            candidate_component_sets = {
                key: {
                    row.get(key)
                    for row in initial_candidate.get("responses") or []
                    if isinstance(row, dict)
                }
                for key in ("prompt_digest", "kb_manifest_digest", "model_digest")
            }
            _require(
                proposal.get("case_id") == case_id
                and proposal.get("channel") == "prompt"
                and proposal.get("attribution_report_digest") == _canonical_digest(attribution)
                and proposal.get("base_versionset_id") == base_versionset["versionset_id"]
                and proposal.get("base_versionset_digest") == base_versionset["digest"]
                and proposal.get("base_revision") == base_versionset["revision"]
                and (proposal_content.get("prompt") or {}).get("digest")
                == proposal.get("target_prompt_digest")
                and candidate_component_sets["prompt_digest"]
                == {proposal.get("target_prompt_digest")}
                and candidate_component_sets["kb_manifest_digest"]
                == {(proposal_content.get("kb_manifest") or {}).get("manifest_digest")}
                and candidate_component_sets["model_digest"]
                == {(proposal_content.get("model") or {}).get("digest")}
                and (proposal_content.get("kb_manifest") or {}).get("manifest_digest")
                == protocol["versions"]["K1"]
                and (proposal_content.get("model") or {}).get("digest")
                == protocol["versions"]["M1"]
                and proposal.get("target_prompt_digest") != protocol["versions"]["P1"],
                "live AgentTeams repair proposal violates prompt-only authority binding",
            )
        elif role == "gatekeeper":
            _require(
                products_by_phase["initial-gate"].get("payload")
                == {
                    "stage": "initial",
                    "workorder_id": workorder_id,
                    "target_versionset_id": initial_candidate["target_versionset_id"],
                    "target_versionset_digest": gates["initial"]["subject"][
                        "target_versionset_digest"
                    ],
                    "target_revision": initial_candidate["target_revision"],
                    "suite_digest": probe_digest,
                }
                and products_by_phase["post-canary-gate"].get("payload")
                == {
                    "stage": "post-canary",
                    "release_id": release_id,
                    "workorder_id": workorder_id,
                    "target_versionset_id": post_candidate["target_versionset_id"],
                    "target_versionset_digest": gates["post_canary"]["subject"][
                        "target_versionset_digest"
                    ],
                    "target_revision": post_candidate["target_revision"],
                    "suite_digest": probe_digest,
                },
                "live AgentTeams gate products differ from authoritative Gate inputs",
            )
        elif role == "case-officer":
            _require(
                products_by_phase["closure"].get("payload")
                == {
                    "case_id": case_id,
                    "release_id": release_id,
                    "channel": payload.get("channel"),
                    "thread_ref": payload.get("thread_ref"),
                    "body_text": body.decode("utf-8"),
                },
                "live AgentTeams closure product differs from notification authority",
            )
        if role == "repairer":
            workorder_ref = (handoff.get("payload") or {}).get("workorder_ref")
            _require(
                isinstance(repairer_workorder_receipt, dict)
                and repairer_workorder_receipt.get("phase") == "workorder"
                and repairer_workorder_receipt.get("role") == "repairer"
                and repairer_workorder_receipt.get("task_id") == run["task_id"]
                and repairer_workorder_receipt.get("ack_receipt_id")
                == run["ack_receipt_id"]
                and set(repairer_workorder_receipt.get("matrix_event_ids") or [])
                <= set(matrix_event_ids)
                and repairer_workorder_receipt.get("session_id")
                == agent_runs["session_id"]
                and repairer_workorder_receipt.get("room_id") == agent_runs["room_id"]
                and repairer_workorder_receipt.get("skill") == expected_skill
                and repairer_workorder_receipt.get("artifact_ref") == workorder_ref,
                "live repairer trace is detached from its WorkOrder task receipt",
            )
            _require(
                repairer_workorder_receipt in role_phase_receipts,
                "live repairer WorkOrder receipt is absent from pre-action receipts",
            )
            try:
                verify_agentteams_receipt(
                    repairer_workorder_receipt, agentteams_public_key
                )
            except AgentTeamsAttestationError as exc:
                raise B1ValidationError(
                    f"live AgentTeams WorkOrder receipt attestation is invalid: {exc}"
                ) from exc
            _, repairer_product = _load_ref(
                workorder_ref,
                run_dir=run_dir,
                label="agent_product.repairer_workorder",
            )
            _require(
                repairer_product.get("schema_version") == "0.1.0"
                and repairer_product.get("kind") == "immutable-workorder"
                and repairer_product.get("role") == "repairer"
                and repairer_product.get("task_id") == run["task_id"]
                and repairer_product.get("session_id") == agent_runs["session_id"]
                and repairer_product.get("case_id") == case_id
                and repairer_product.get("workorder") == workorder,
                "live WorkOrder was not produced by the bound AgentTeams repairer artifact",
            )
    skill_invocations = agent_runs.get("skill_invocations") or []
    _require(
        isinstance(skill_invocations, list)
        and len(skill_invocations) == len(expected_roles)
        and {
            (row.get("role"), row.get("task_id"), _canonical_digest(row.get("skill")))
            for row in skill_invocations
            if isinstance(row, dict)
        }
        == {
            (role, run["task_id"], _canonical_digest(expected_skill))
            for role, run in run_by_role.items()
        },
        "live AgentTeams skill invocation receipts are incomplete",
    )

    commits = artifacts["commits"]
    _require(
        commits.get("repository_start_commit") == manifest["versions"]["repository_start_commit"]
        == commits.get("origin_main_commit")
        and commits.get("repository_end_commit") == manifest["versions"]["repository_end_commit"],
        "live commit evidence binding mismatch",
    )
    current_head = _git("rev-parse", "HEAD")
    _git("merge-base", "--is-ancestor", commits["repository_end_commit"], current_head)
    _git("merge-base", "--is-ancestor", commits["repository_start_commit"], commits["repository_end_commit"])
    if current_head != commits["repository_end_commit"]:
        changes = set(
            filter(
                None,
                _git("diff", "--name-only", f"{commits['repository_end_commit']}..{current_head}").splitlines(),
            )
        )
        _require(
            all(path == "PLANS.md" or path.startswith(("evidence/", "docs/context/")) for path in changes),
            "implementation files changed after the live evidence end commit",
        )
    if not allow_dirty:
        _require(commits.get("working_tree") == "", "live evidence was generated from an uncommitted tree")

    contract_replay = artifacts["contract_replay_report"]
    for kind in ("contract", "replay"):
        summary = contract_replay.get(kind) or {}
        _require(
            (summary.get("status"), summary.get("n_passed"), summary.get("n_failed"))
            == (reports[kind]["status"], reports[kind]["passed"], reports[kind]["failed"]),
            f"live contract/replay summary differs from {kind} report",
        )
    _require(contract_replay.get("live_provider_counted_as_pass") is False, "live run merged provider and replay counts")
    _require(artifacts["live_provider_report"].get("status") == "passed", "live provider artifact is not passed")
    _require(manifest["outcomes"] == {
        "deduplicated": True,
        "attribution": {"decision": "ATTRIBUTED", "fault_layer": "prompt"},
        "release": "promoted",
        "notification": {"status": "sent", "provider": "feishu"},
        "case": "CLOSED",
        "trust": manifest["outcomes"]["trust"],
        "live_provider": "passed",
    }, "live manifest outcomes are inconsistent")
    return {
        "status": "verified",
        "manifest": str(run_dir / "b1-run-manifest.json"),
        "artifact_count": len(artifacts),
        "probe_output_count": expected_trials,
        "gate_probe_output_count": initial_probe_count + post_probe_count,
        "provider_log_count": len(provider_logs),
    }


def validate_b1_run(manifest_path: Path, *, allow_dirty: bool = False) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    run_dir = manifest_path.parent
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (REPO_ROOT / "contracts" / "schemas" / "b1-run-manifest.schema.json").read_text(
            encoding="utf-8"
        )
    )
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest),
        key=lambda error: list(error.absolute_path),
    )
    if errors:
        raise B1ValidationError(
            "B1 manifest schema failed: "
            + "; ".join(
                f"{list(error.absolute_path)}: {error.message}" for error in errors[:5]
            )
        )
    _require(manifest.get("fixture_id") == "B1", "manifest is not the B1 fixture")
    started_at = _parse_dt(manifest["started_at"], "manifest.started_at")
    completed_at = _parse_dt(manifest["completed_at"], "manifest.completed_at")
    _require(completed_at >= started_at, "manifest completed_at precedes started_at")

    artifacts: dict[str, Any] = {}
    for name, ref in manifest["artifacts"].items():
        _, value = _load_ref(ref, run_dir=run_dir, label=f"artifact.{name}")
        artifacts[name] = value

    reports = {item["kind"]: item for item in manifest["test_reports"]}
    _require(set(reports) == {"contract", "replay", "live-provider"}, "test report kinds are incomplete")
    for kind, report in reports.items():
        _, report_value = _load_ref(
            report["report_ref"], run_dir=run_dir, label=f"test_report.{kind}"
        )
        actual_status, actual_passed, actual_failed = _report_semantics(kind, report_value)
        _require(report["status"] == actual_status, f"{kind} report status does not match its content")
        _require(report["passed"] == actual_passed, f"{kind} passed count does not match its content")
        _require(report["failed"] == actual_failed, f"{kind} failed count does not match its content")
    if manifest["status"] == "passed":
        for kind in ("contract", "replay"):
            report = reports[kind]
            _require(
                report["status"] == "passed" and report["passed"] > 0 and report["failed"] == 0,
                f"passed manifest contains a non-passing {kind} report",
            )
    if manifest["mode"] == "isolated-replay":
        _require(reports["live-provider"]["status"] == "blocked", "replay must not claim live success")
    if manifest["mode"] == "live-provider":
        return _validate_live_b1(
            manifest=manifest,
            artifacts=artifacts,
            reports=reports,
            run_dir=run_dir,
            completed_at=completed_at,
            allow_dirty=allow_dirty,
        )

    case_id = manifest["case_id"]
    experiment_id = manifest["experiment_id"]
    workorder_id = manifest["workorder_id"]
    release_id = manifest["release_id"]
    notification_id = manifest["notification_id"]

    plan = artifacts["experiment_plan"]
    _require(plan["transaction_id"] == manifest["transaction_id"], "transaction id binding mismatch")
    _require(plan["case_id"] == case_id and plan["experiment_id"] == experiment_id, "experiment plan identity mismatch")
    bundle = artifacts["evidence_bundle"]
    _require(bundle["case_id"] == case_id and bundle["experiment_id"] == experiment_id, "EvidenceBundle identity mismatch")
    _require(bundle["verdict"]["decision"] == "ATTRIBUTED", "EvidenceBundle verdict is not ATTRIBUTED")
    _require(bundle["verdict"]["attributed_layer"] == "prompt", "EvidenceBundle fault layer is not prompt")
    frozen_versionset = artifacts["frozen_versionset"]
    _require(
        frozen_versionset.get("fixture") == "contracts/fixtures/b1-prompt-regression.yaml",
        "frozen VersionSet fixture is not B1",
    )
    _require(frozen_versionset.get("badcase_injected") is True, "B1 badcase injection was not recorded")
    frozen_binding = {
        "versions": frozen_versionset["component_digests"],
        "cell_versionsets": frozen_versionset["cell_versionsets"],
    }
    _require(frozen_versionset.get("digest") == _canonical_digest(frozen_binding), "frozen VersionSet digest mismatch")
    _require(
        manifest["versions"]["base_versionset"] == frozen_versionset["active_bad_versionset"],
        "manifest base VersionSet does not match the injected bad VersionSet",
    )
    _require(
        plan.get("protocol", {}).get("execution_profile") == "isolated-replay",
        "B1 replay experiment profile is not isolated-replay",
    )
    _require(
        plan["protocol"].get("versions") == frozen_versionset["component_digests"]
        and plan["protocol"].get("cell_versionsets") == frozen_versionset["cell_versionsets"],
        "experiment plan does not bind the frozen VersionSets",
    )
    probes = artifacts["probes"]
    _require(
        probes.get("digest") == plan["protocol"].get("probe_set_digest") == bundle["probe_set"].get("probe_set_digest"),
        "probe digest differs across plan/probes/EvidenceBundle",
    )
    _require(set(probes.get("selected") or []) == {
        *bundle["probe_set"].get("discovery", []),
        *bundle["probe_set"].get("hidden_confirmation", []),
        *bundle["probe_set"].get("unaffected_controls", []),
    }, "selected probes differ from the frozen EvidenceBundle groups")
    probe_output_count = _validate_probe_outputs(
        bundle,
        run_dir=run_dir,
        experiment_id=experiment_id,
        case_id=case_id,
        cell_versionsets=frozen_versionset["cell_versionsets"],
    )

    attribution = artifacts["attribution_report"]
    _require(attribution["case_id"] == case_id and attribution["experiment_id"] == experiment_id, "AttributionReport identity mismatch")
    _require(attribution["verdict"]["decision"] == "ATTRIBUTED", "AttributionReport is not ATTRIBUTED")
    _require(attribution["verdict"]["attributed_layer"] == "prompt", "AttributionReport fault layer mismatch")
    _require(
        attribution["evidence_bundle_ref"]["digest"] == _canonical_digest(bundle),
        "AttributionReport does not bind the EvidenceBundle",
    )
    adjudication = validate_attribution_artifacts(
        experiment_id=experiment_id,
        case_id=case_id,
        frozen=plan["protocol"],
        evidence_bundle=bundle,
        attribution_report=attribution,
        delta_min=0.2,
    )
    _require(
        adjudication.get("verdict") == "ATTRIBUTED"
        and adjudication.get("attributed_layer") == "prompt",
        "repository-owned raw-answer adjudication did not attribute B1 to prompt",
    )

    workorder = artifacts["workorder"]
    _require(workorder["workorder_id"] == workorder_id and workorder["case_id"] == case_id, "WorkOrder identity mismatch")
    computed_workorder_hash = _canonical_digest(
        {key: value for key, value in workorder.items() if key != "hash"},
        prefix=False,
    )
    _require(computed_workorder_hash == workorder["hash"] == manifest["workorder_hash"], "WorkOrder hash mismatch")

    gates = artifacts["gate_report"]
    _require(gates["policy_profile"] == "isolated-replay", "gate wrapper policy mismatch")
    gate_schema = json.loads(
        (REPO_ROOT / "contracts" / "schemas" / "gate-report.schema.json").read_text(encoding="utf-8")
    )
    gate_validator = Draft202012Validator(gate_schema, format_checker=FormatChecker())
    for name in ("initial", "post_canary"):
        report = gates[name]
        _require(not list(gate_validator.iter_errors(report)), f"{name} GateReport schema failed")
        _require(report["policy_profile"] == "isolated-replay", f"{name} Gate policy is unbound")
        _require(report["overall_status"] == "passed", f"{name} Gate did not pass")
        _require(report["live_provider_e2e"]["status"] == "skipped", f"{name} Gate falsely claims live evidence")
        _require(
            report["subject"]["target_versionset_digest"] == workorder["target_versionset_digest"],
            f"{name} Gate target differs from WorkOrder",
        )
        _require(
            report["subject"]["probe_set_digest"] == bundle["probe_set"]["probe_set_digest"],
            f"{name} Gate probe set differs from attribution",
        )
        _require(
            report["rule_track"]["status"] == "passed"
            and report["judge_track"]["status"] == "passed"
            and report["deterministic_tests"]["status"] == "passed",
            f"{name} Gate contains a non-passing track",
        )
        _require(
            report["judge_track"]["judge_model_digest"]
            != report["judge_track"]["athlete_model_digest"],
            f"{name} Gate judge is not independent from the athlete",
        )
        nested: dict[str, Any] = {}
        for index, ref in enumerate(report["artifact_refs"]):
            if str(ref.get("uri") or "").startswith("data:"):
                nested_value = _load_inline_json_ref(
                    ref, label=f"{name}.artifact_refs[{index}]"
                )
            else:
                _, nested_value = _load_ref(
                    ref,
                    run_dir=run_dir,
                    label=f"{name}.artifact_refs[{index}]",
                )
            nested[ref["uri"]] = nested_value
        for suite in report["deterministic_tests"]["suites"]:
            suite_kind = suite["kind"]
            _require(suite["report_ref"] in nested, f"{name} Gate suite has no bound artifact")
            actual_status, actual_passed, actual_failed = _report_semantics(
                suite_kind,
                nested[suite["report_ref"]],
            )
            _require(
                (suite["status"], suite["n_passed"], suite["n_failed"])
                == (actual_status, actual_passed, actual_failed),
                f"{name} Gate {suite_kind} suite summary is false",
            )
    initial_hash = _canonical_digest(gates["initial"], prefix=False)
    _require(
        workorder["gate_report_ref"] == {
            "uri": f"eval://{gates['initial']['eval_id']}",
            "digest": f"sha256:{initial_hash}",
        },
        "WorkOrder does not bind the initial GateReport",
    )

    approvals = artifacts["approval_grants"]["grants"]
    _require(len(approvals) == 3, "initial/canary/promote ApprovalGrants are incomplete")
    _require(len({grant["nonce"] for grant in approvals}) == len(approvals), "ApprovalGrant nonce reused")
    initial_grants = [grant for grant in approvals if grant.get("authorization") is None]
    action_grants = {
        grant.get("authorization", {}).get("action"): grant
        for grant in approvals
        if grant.get("authorization") is not None
    }
    _require(len(initial_grants) == 1, "exactly one initial WorkOrder ApprovalGrant is required")
    _require(set(action_grants) == {"canary", "promote"}, "canary/promote ApprovalGrants are incomplete")
    for grant in approvals:
        _require(grant["workorder_id"] == workorder_id, "ApprovalGrant WorkOrder id mismatch")
        _require(grant["workorder_hash"] == workorder["hash"], "ApprovalGrant WorkOrder hash mismatch")
        _require(grant["decision"] == "approved", "B1 action lacks an approved grant")
        _require(grant.get("nonce_consumed") is True, "ApprovalGrant evidence is not the consumed persisted grant")
        persistence = grant.get("persistence") or {}
        _require(
            persistence.get("status") == "consumed" and persistence.get("decision") == "approved",
            "ApprovalGrant persistence is not consumed/approved",
        )
        decided_at = _parse_dt(grant.get("decided_at"), "ApprovalGrant.decided_at")
        consumed_at = _parse_dt(persistence.get("consumed_at"), "ApprovalGrant.consumed_at")
        expiry = _parse_dt(grant.get("expiry"), "ApprovalGrant.expiry")
        persisted_expiry = _parse_dt(persistence.get("expiry"), "ApprovalGrant.persistence.expiry")
        _require(
            decided_at <= consumed_at <= completed_at < expiry == persisted_expiry,
            "ApprovalGrant decision/consumption/expiry ordering is invalid",
        )
        authorization = grant.get("authorization")
        if authorization is not None:
            _require(authorization.get("release_id") == release_id, "action ApprovalGrant release mismatch")
            _require(
                authorization.get("params_digest") == _canonical_digest(authorization.get("params")),
                "action ApprovalGrant params digest mismatch",
            )
    _require(initial_grants[0]["nonce"] == workorder["nonce"], "initial ApprovalGrant nonce differs from WorkOrder")
    _require(
        _parse_dt(workorder["expiry"], "WorkOrder.expiry")
        == _parse_dt(initial_grants[0]["expiry"], "initial ApprovalGrant.expiry"),
        "initial ApprovalGrant outlives or shortens the WorkOrder",
    )

    receipts = artifacts["release_receipts"]
    _require(receipts["start"]["release_id"] == release_id, "release start receipt mismatch")
    _require(receipts["promote"]["state"] == "COMPLETED", "release was not promoted")
    _require(receipts["verification"]["verification"] == "passed", "post-canary verification did not pass")
    target_versionset = manifest["versions"]["target_versionset"]
    base_versionset = manifest["versions"]["base_versionset"]
    _require(workorder["base_versionset_digest"] == base_versionset["digest"], "WorkOrder base digest mismatch")
    _require(workorder["target_versionset_digest"] == target_versionset["digest"], "WorkOrder target digest mismatch")
    _require(receipts["start"]["versionset_id"] == target_versionset["versionset_id"], "release target id mismatch")
    _require(receipts["start"]["workorder_hash"] == workorder["hash"], "release start WorkOrder hash mismatch")
    operations = receipts.get("controller_operations") or []
    _require(len(operations) == 3, "stage/canary/promote ControllerOperations are incomplete")
    by_kind = {operation.get("kind"): operation for operation in operations}
    _require(set(by_kind) == {"stage", "canary", "promote"}, "ControllerOperation kinds are incomplete")
    expected_remote = {
        "stage": ("staged", 1, 2),
        "canary": ("canary", 2, 3),
        "promote": ("active", 3, 4),
    }
    remote_ids: set[str] = set()
    for kind, (remote_status, expected_before, expected_after) in expected_remote.items():
        operation = by_kind[kind]
        result = operation.get("result") or {}
        result_body = result.get("result") or {}
        _require(
            operation.get("release_id") == release_id
            and operation.get("status") == "succeeded"
            and operation.get("expected_revision") == expected_before,
            f"{kind} ControllerOperation binding/status mismatch",
        )
        _require(
            result.get("operation_id") == operation.get("remote_operation_id")
            and result.get("kind") == kind
            and result.get("status") == "succeeded"
            and result.get("versionset_id") == target_versionset["versionset_id"],
            f"{kind} Quality receipt identity mismatch",
        )
        _require(
            result_body.get("status") == remote_status
            and result_body.get("revision") == expected_after,
            f"{kind} Quality transition receipt mismatch",
        )
        remote_id = operation.get("remote_operation_id")
        _require(isinstance(remote_id, str) and remote_id not in remote_ids, f"{kind} remote operation id reused")
        remote_ids.add(remote_id)
        step_receipt = receipts[kind]
        _require(
            step_receipt.get("release_id") == release_id
            and step_receipt.get("operation_id") == operation.get("operation_id")
            and step_receipt.get("kind") == kind
            and step_receipt.get("status") == "succeeded",
            f"{kind} controller receipt mismatch",
        )
    _require(target_versionset["revision"] == 4, "manifest target revision is not the promoted revision")
    _require(
        by_kind["canary"].get("approval_id") == action_grants["canary"]["approval_id"]
        and by_kind["promote"].get("approval_id") == action_grants["promote"]["approval_id"],
        "ControllerOperations do not bind the consumed action ApprovalGrants",
    )
    _require(
        action_grants["canary"]["authorization"]["target_revision"] == by_kind["canary"]["expected_revision"]
        and action_grants["promote"]["authorization"]["target_revision"] == by_kind["promote"]["expected_revision"],
        "ApprovalGrant target revision differs from its ControllerOperation",
    )
    promote_params = action_grants["promote"]["authorization"]["params"]
    _require(
        promote_params.get("expected_active_digest") == base_versionset["digest"]
        and promote_params.get("verification_eval_id") == gates["post_canary"]["eval_id"]
        and promote_params.get("verification_report_hash") == _canonical_digest(gates["post_canary"], prefix=False),
        "promote ApprovalGrant is not bound to the base revision and post-canary GateReport",
    )
    _require(
        receipts.get("quality_call_log") == ["create_versionset", "stage", "canary", "promote"],
        "Quality call log is missing, reordered, or contains an unapproved write",
    )
    terminal = artifacts["release_terminal_receipt"]
    _require(terminal == receipts["promote"], "terminal release receipt differs from promote receipt")

    canary = artifacts["canary_metrics"]
    observation = canary["observation"]
    _require(canary["traffic_routed"] is False, "isolated replay must not claim routed canary traffic")
    _require(observation["complete"] is True, "canary observation window was incomplete")
    _require(observation["elapsed_seconds"] >= observation["required_seconds"], "canary observation elapsed time is too short")
    _require(canary["error_count"] == 0 and canary["probe_count"] > 0, "canary probe metrics are not passing")
    _require(
        canary["canary_percent"] == action_grants["canary"]["authorization"]["params"]["percent"]
        == by_kind["canary"]["result"]["result"]["canary_percent"],
        "canary percent differs across approval, operation, and metrics",
    )
    _require(
        canary["verification_eval_id"] == gates["post_canary"]["eval_id"],
        "canary metrics do not bind the post-canary GateReport",
    )

    notification = artifacts["notification_receipt"]
    _require(notification["notification_id"] == notification_id, "notification id mismatch")
    _require(notification["state"] == "SENT", "notification has no accepted provider receipt")
    notification_payload = notification["payload"]
    closure_context = notification.get("closure_context") or {}
    _require(
        closure_context.get("status") == "configured"
        and closure_context.get("release_id") == release_id
        and closure_context.get("case_id") == case_id,
        "notification closure was not durably configured before promote",
    )
    for key in ("channel", "thread_ref", "body_ref", "body_digest"):
        _require(
            closure_context.get(key) == notification_payload.get(key),
            f"notification payload differs from frozen closure {key}",
        )
    body_path = _resolve_uri(notification_payload.get("body_ref") or "", run_dir=run_dir)
    _require(
        _file_digest(body_path) == notification_payload.get("body_digest"),
        "notification reply body digest mismatch",
    )
    retry = notification["closure_retry"]["notification"]
    _require(retry["notification_id"] == notification_id and retry["duplicate"] is True, "post-SENT closure retry is not idempotent")
    provider_receipt = notification_payload.get("receipt") or {}
    _require(
        provider_receipt.get("status") == "sent"
        and provider_receipt.get("provider") == "feishu-mock"
        and provider_receipt.get("thread_ref") == notification_payload.get("thread_ref")
        and provider_receipt.get("body_digest") == notification_payload.get("body_digest")
        and provider_receipt.get("outbox_id") == notification_payload.get("outbox_id"),
        "notification provider receipt is not bound to the exact reply",
    )

    domain = artifacts["domain_events"]
    _require(set(domain["required_catalog"]) <= set(domain["observed"]), "required domain events are missing")
    domain_rows = domain.get("rows") or []
    _require(domain_rows, "domain event evidence is empty")
    _require(
        all((row.get("payload") or {}).get("correlation_id") == case_id for row in domain_rows),
        "B1 domain event correlation does not remain on the Case",
    )
    domain_types = [row.get("event_type") for row in domain_rows]
    ordered = [
        "CASE_CREATED",
        "ATTRIBUTION_DECIDED",
        "RELEASE_STARTED",
        "RELEASE_PROMOTED",
        "NOTIFICATION_SENT",
        "CASE_ARCHIVED",
    ]
    positions = [domain_types.index(event_type) for event_type in ordered]
    _require(positions == sorted(positions), "B1 domain events violate the required causal order")
    gate_positions = [index for index, event_type in enumerate(domain_types) if event_type == "GATE_COMPLETED"]
    _require(
        len(gate_positions) == 2
        and gate_positions[0] < domain_types.index("RELEASE_STARTED")
        and gate_positions[1] < domain_types.index("RELEASE_PROMOTED"),
        "initial and post-canary Gate events are not in the release chain",
    )
    outbox = artifacts["outbox_receipts"]
    _require(outbox["duplicate_dispatch"]["claimed"] == 0, "outbox redispatch was not idempotent")
    _require(all(row["status"] == "SENT" for row in outbox["outbox"]), "outbox has a non-SENT row")
    receipt_by_outbox = {row["outbox_id"]: row for row in outbox.get("receipts") or []}
    _require(len(receipt_by_outbox) == len(outbox["outbox"]), "not every SENT outbox has one receipt")
    for row in outbox["outbox"]:
        _require(row["payload_digest"] == _canonical_digest(row["payload"]), "outbox payload digest mismatch")
        delivery = receipt_by_outbox.get(row["outbox_id"])
        _require(delivery is not None, "outbox receipt is missing")
        _require(
            delivery.get("payload_digest") == row["payload_digest"]
            and (delivery.get("receipt") or {}).get("payload_digest") == row["payload_digest"],
            "outbox receipt does not bind its exact payload",
        )
    notification_outbox = next(
        (row for row in outbox["outbox"] if row["event_type"] == "NOTIFICATION_DELIVERY_REQUESTED"),
        None,
    )
    _require(notification_outbox is not None, "notification delivery outbox is missing")
    _require(
        notification_outbox["outbox_id"] == notification_payload["outbox_id"]
        and notification_outbox["payload"].get("body_digest") == notification_payload["body_digest"]
        and notification_outbox["payload"].get("release_id") == release_id,
        "notification delivery outbox is not bound to the closure payload",
    )
    trust = artifacts["trust_decision"]
    _require(trust["samples_added"] == 1, "Trust Ledger sample count is not one action")
    _require(trust["entry"]["epoch_trials"] == 1, "Trust Ledger contains duplicate action samples")
    _require(trust["entry"]["promotion"]["eligible"] is False, "Trust Ledger incorrectly allowed promotion")
    _require(
        trust["entry"]["outcome"]["action_ref"] == release_id
        and trust["entry"]["outcome"]["status"] == "success"
        and trust["entry"]["sample_rule"] == "one_action_one_sample",
        "Trust entry is not the real promoted release action",
    )
    reference = trust.get("three_of_three_reference") or {}
    _require(
        abs(float(reference.get("wilson_two_sided_95_lower", -1)) - 0.4385) < 0.0001
        and reference.get("threshold") == 0.9
        and reference.get("decision") == "denied",
        "3/3 Wilson reference does not deny promotion",
    )

    event_ids = {row["event_id"] for row in artifacts["trace"]["events"]}
    trace_events = artifacts["trace"]["events"]
    event_by_id = {row["event_id"]: row for row in trace_events}
    _require(
        all(row["source_event_id"] in event_by_id for row in domain_rows),
        "domain outbox references an event absent from the trace",
    )
    promoted_event = next(
        row for row in trace_events
        if row.get("aggregate_id") == release_id and row.get("event_type") == "release.promoted"
    )
    resolved_event = next(
        row for row in trace_events
        if row.get("aggregate_id") == case_id and row.get("event_type") == "case.resolved"
    )
    sent_event = next(
        row for row in trace_events
        if row.get("aggregate_id") == notification_id and row.get("event_type") == "notification.sent"
    )
    closed_event = next(
        row for row in trace_events
        if row.get("aggregate_id") == case_id and row.get("event_type") == "case.closed"
    )
    _require(
        promoted_event.get("causation_id") == by_kind["promote"]["remote_operation_id"]
        and promoted_event.get("correlation_id") == case_id,
        "release.promoted is not caused by the exact Quality operation",
    )
    _require(
        resolved_event.get("causation_id") == promoted_event["event_id"]
        and sent_event.get("correlation_id") == case_id
        and closed_event.get("causation_id") == sent_event["event_id"],
        "promote-to-resolve-to-notify-to-archive causation chain is broken",
    )
    audit_ids = {row["audit_id"] for row in artifacts["audit_events"]}
    agent_trace = artifacts["agent_runs"]
    _require(agent_trace["recording_kind"] == "deterministic-replay-execution-trace", "agent runs falsely claim live execution")
    for run in agent_trace["runs"]:
        sources = set(run["source_ids"])
        _require(sources and sources <= event_ids | audit_ids, f"agent trace source is not authoritative: {run['component']}")

    commits = artifacts["commits"]
    _require(commits["repository_end_commit"] == manifest["versions"]["repository_end_commit"], "repository end commit mismatch")
    _require(commits["repository_start_commit"] == manifest["versions"]["repository_start_commit"], "repository start commit mismatch")
    _require(commits["origin_main_commit"] == commits["repository_start_commit"], "evidence start is not the verified origin/main")
    current_head = _git("rev-parse", "HEAD")
    _git("merge-base", "--is-ancestor", commits["repository_end_commit"], current_head)
    if current_head != commits["repository_end_commit"]:
        post_evidence_changes = set(
            filter(
                None,
                _git(
                    "diff",
                    "--name-only",
                    f"{commits['repository_end_commit']}..{current_head}",
                ).splitlines(),
            )
        )
        allowed_prefixes = ("evidence/", "docs/context/")
        allowed_files = {"PLANS.md"}
        _require(
            all(
                path in allowed_files or path.startswith(allowed_prefixes)
                for path in post_evidence_changes
            ),
            "implementation files changed after the evidence end commit",
        )
    _git("merge-base", "--is-ancestor", commits["repository_start_commit"], commits["repository_end_commit"])
    if not allow_dirty:
        _require(commits["working_tree"] == "", "evidence was generated from an uncommitted working tree")

    contract_replay = artifacts["contract_replay_report"]
    for kind in ("contract", "replay"):
        summary = contract_replay[kind]
        _require(
            (summary.get("status"), summary.get("n_passed"), summary.get("n_failed"))
            == (reports[kind]["status"], reports[kind]["passed"], reports[kind]["failed"]),
            f"contract-replay summary disagrees with the {kind} report",
        )
    _require(contract_replay.get("live_provider_counted_as_pass") is False, "replay counted live provider as passed")
    _require(artifacts["live_provider_report"]["status"] == "blocked", "replay live-provider boundary is dishonest")
    return {
        "status": "verified",
        "manifest": str(manifest_path),
        "artifact_count": len(artifacts),
        "probe_output_count": probe_output_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="test-only: accept an evidence run whose commits artifact records local changes",
    )
    args = parser.parse_args()
    try:
        result = validate_b1_run(args.manifest, allow_dirty=args.allow_dirty)
    except (B1ValidationError, OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
