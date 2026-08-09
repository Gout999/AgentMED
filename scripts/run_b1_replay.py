#!/usr/bin/env python3
"""Run the complete B1 loop against explicit contract/replay adapters.

This command never calls a live provider and never labels replay evidence as
live.  It exercises the production control-plane services, immutable Quality
lifecycle semantics, transactional outbox dispatcher, notification receipt,
Case archival, and Trust consumer against a disposable SQLite database.  The
only substitutes are named in the evidence: recorded provider responses,
FakeQualityClient, a recorded deterministic judge, and FeishuMockAdapter.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import time
from typing import Any
from urllib.parse import unquote, urlparse
import uuid


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "eval-harness"))
# Keep the authoritative control-plane root ahead of eval-harness.  Both trees
# contain a ``tests`` namespace; reversing this order makes combined pytest
# collection resolve ``tests.conftest`` from the wrong service.
sys.path.insert(0, str(REPO_ROOT / "control-plane"))

from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.config import Settings as ControlSettings  # noqa: E402
from app.models.tables import (  # noqa: E402
    Aggregate,
    Approval,
    Audit,
    Base,
    ControllerOperation,
    Event,
    GateReportRecord,
    Outbox,
    OutboxDeliveryReceipt,
    ReleaseClosure,
    TrustLedger,
    TrustLedgerEntry,
    WorkOrder,
)
from app.notifications.adapters import FeishuMockAdapter  # noqa: E402
from app.quality.client import FakeQualityClient  # noqa: E402
from app.services.attribution import newcombe_wilson_diff  # noqa: E402
from app.services.case_closure_service import CaseClosureService  # noqa: E402
from app.services.case_service import CaseService  # noqa: E402
from app.services.experiment_service import ExperimentService  # noqa: E402
from app.services.gate_service import GateService  # noqa: E402
from app.services.outbox_relay import DOMAIN_EVENT_TYPES, OutboxDispatcher  # noqa: E402
from app.services.release_service import ReleaseService  # noqa: E402
from app.utils.jcs import canonical_json_digest, workorder_hash  # noqa: E402
from eval_harness.config import Settings as EvalSettings  # noqa: E402
from eval_harness.gate import GateCandidate, GateRunner, SuiteResult  # noqa: E402
from eval_harness.probe_judge import judge_probe  # noqa: E402
from eval_harness.probe_loader import frozen_digest, load_probe_set  # noqa: E402
from validate_b1_run import validate_b1_run  # noqa: E402


B1_DISCOVERY = ["cs-001", "cs-002", "cs-003"]
B1_HIDDEN = ["cs-004", "cs-005"]
B1_CONTROLS = ["cs-013", "cs-014", "cs-015", "cs-016"]
B1_PROBES = B1_DISCOVERY + B1_HIDDEN + B1_CONTROLS
CELL_STATE = {"C": "b1_fault", "RP": "baseline", "RK": "b1_fault", "RM": "b1_fault", "G": "baseline"}
CELL_COMPONENTS = {
    "C": ("P1", "K1", "M1"),
    "RP": ("P0", "K1", "M1"),
    "RK": ("P1", "K0", "M1"),
    "RM": ("P1", "K1", "M0"),
    "G": ("P0", "K0", "M0"),
}
REQUIRED_DOMAIN_EVENTS = {
    "CASE_CREATED",
    "ATTRIBUTION_DECIDED",
    "GATE_COMPLETED",
    "RELEASE_STARTED",
    "RELEASE_PROMOTED",
    "NOTIFICATION_SENT",
    "CASE_ARCHIVED",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _row(row: Any) -> dict[str, Any]:
    return {
        column.name: _jsonable(getattr(row, column.name))
        for column in row.__table__.columns
    }


def _write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _write_text(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return path


def _publish_verified_manifest(
    *, output_dir: Path, manifest: dict[str, Any], allow_dirty: bool
) -> Path:
    """Expose a passed replay manifest only after semantic self-validation."""

    candidate_path = output_dir / ".b1-run-manifest.candidate.json"
    final_path = output_dir / "b1-run-manifest.json"
    _write_json(candidate_path, manifest)
    try:
        validate_b1_run(candidate_path, allow_dirty=allow_dirty)
    except Exception:
        candidate_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        raise
    candidate_path.replace(final_path)
    return final_path


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_uri(path: Path) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(REPO_ROOT)
    except ValueError:
        return resolved.as_uri()
    return "repo:///" + relative.as_posix()


def _require_portable_output_dir(output_dir: Path, *, allow_dirty: bool) -> None:
    """Final replay evidence must resolve through repository-relative URIs."""

    if allow_dirty:
        return
    try:
        output_dir.resolve().relative_to(REPO_ROOT)
    except ValueError as exc:
        raise RuntimeError(
            "final B1 replay evidence must be generated inside the repository so all "
            "artifact references are portable repo:/// URIs"
        ) from exc


def _artifact(path: Path) -> dict[str, str]:
    return {"uri": _repo_uri(path), "digest": _file_digest(path)}


def _inline_json_artifact(path: Path) -> dict[str, str]:
    raw = path.read_bytes()
    if len(raw) > 2_000_000:
        raise RuntimeError(f"Gate artifact exceeds 2 MB: {path}")
    json.loads(raw.decode("utf-8"))
    return {
        "uri": "data:application/json;base64," + base64.b64encode(raw).decode("ascii"),
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }


def _path_from_evidence_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme == "repo":
        return (REPO_ROOT / unquote(parsed.path).lstrip("/")).resolve()
    if parsed.scheme == "file":
        return Path(unquote(parsed.path)).resolve()
    raise RuntimeError(f"Gate suite artifact URI is unsupported: {uri}")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


def _parse_pytest_counts(output: str) -> tuple[int, int]:
    passed = sum(int(value) for value in re.findall(r"(\d+) passed", output))
    failed = sum(int(value) for value in re.findall(r"(\d+) failed", output))
    return passed, failed


def _run_suite(
    *,
    kind: str,
    command: list[str],
    cwd: Path,
    report_path: Path,
) -> SuiteResult:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(cwd)
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=180,
        check=False,
    )
    report = {
        "kind": kind,
        "command": command,
        "cwd": str(cwd),
        "exit_code": completed.returncode,
        "output": completed.stdout,
    }
    _write_json(report_path, report)
    passed, failed = _parse_pytest_counts(completed.stdout)
    if completed.returncode != 0 and failed == 0:
        failed = 1
    status = "passed" if completed.returncode == 0 and passed > 0 and failed == 0 else "failed"
    return SuiteResult(
        suite=f"b1-{kind}",
        kind=kind,
        status=status,
        n_passed=passed,
        n_failed=failed,
        report_ref=_repo_uri(report_path),
        report_digest=_file_digest(report_path),
    )


def _embedded_suites(
    *,
    output_dir: Path,
    evidence_bundle: dict[str, Any],
    attribution_report: dict[str, Any],
    probe_set: Any,
    answers: dict[str, str],
) -> tuple[SuiteResult, SuiteResult]:
    contract_errors: list[str] = []
    for schema_name, value in (
        ("evidence-bundle.schema.json", evidence_bundle),
        ("attribution-report.schema.json", attribution_report),
    ):
        schema = json.loads((REPO_ROOT / "contracts" / "schemas" / schema_name).read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value))
        contract_errors.extend(f"{schema_name}:{error.message}" for error in errors)
    contract_report = _write_json(
        output_dir / "contract-suite.json",
        {"checks": 2, "errors": contract_errors, "source": "jsonschema-draft-2020-12"},
    )
    contract = SuiteResult(
        suite="b1-artifact-contracts",
        kind="contract",
        status="passed" if not contract_errors else "failed",
        n_passed=2 - min(2, len(contract_errors)),
        n_failed=len(contract_errors),
        report_ref=_repo_uri(contract_report),
        report_digest=_file_digest(contract_report),
    )

    replay_results = {}
    for probe in probe_set.probes:
        answer = answers.get(probe.id, "")
        passed, reasons = judge_probe(probe, answer)
        replay_results[probe.id] = {"passed": passed, "reasons": reasons}
    failures = sum(1 for result in replay_results.values() if not result["passed"])
    replay_report = _write_json(
        output_dir / "replay-suite.json",
        {"checks": replay_results, "source": "recorded-b1-responses+deterministic-probe-judge"},
    )
    replay = SuiteResult(
        suite="b1-recorded-probe-replay",
        kind="replay",
        status="passed" if failures == 0 else "failed",
        n_passed=len(replay_results) - failures,
        n_failed=failures,
        report_ref=_repo_uri(replay_report),
        report_digest=_file_digest(replay_report),
    )
    return contract, replay


class RecordedReplayJudge:
    """Contract/replay substitute; never presented as a live LLM judge."""

    model = "recorded-replay-judge"

    @property
    def model_digest(self) -> str:
        return canonical_json_digest(
            {"adapter": "recorded-replay-judge", "rule": "eval_harness.probe_judge", "version": 1}
        )

    def score(self, probe: Any, answer: str) -> dict[str, Any]:
        passed, reasons = judge_probe(probe, answer)
        return {
            "score": 1.0 if passed else 0.0,
            "pass": passed,
            "rationale": "deterministic replay: " + ("passed" if passed else "; ".join(reasons)),
        }


def _build_attribution_artifacts(
    *,
    run_suffix: str,
    output_dir: Path,
    experiment_id: str,
    case_id: str,
    probe_digest: str,
    versions: dict[str, str],
    responses: dict[str, Any],
    probe_set: Any,
    repetitions: int,
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    probe_index = probe_set.by_id()
    rng = random.Random(seed)
    arm_order = ["C", "RP", "RK", "RM", "G"]
    rng.shuffle(arm_order)
    random_arm_order = [f"{arm}@{probe_id}" for arm in arm_order for probe_id in B1_DISCOVERY + B1_HIDDEN]
    outputs: dict[str, Any] = {}
    cells: dict[str, Any] = {}
    summaries: dict[str, Any] = {}
    affected = set(B1_DISCOVERY + B1_HIDDEN)
    controls = set(B1_CONTROLS)

    for arm in ("C", "RP", "RK", "RM", "G"):
        state = CELL_STATE[arm]
        prompt_key, kb_key, model_key = CELL_COMPONENTS[arm]
        results = []
        arm_outputs: dict[str, Any] = {}
        for probe_id in B1_PROBES:
            answer = responses["states"][state][probe_id]["answer"]
            passed, reasons = judge_probe(probe_index[probe_id], answer)
            arm_outputs[probe_id] = {
                "answer": answer,
                "passed": passed,
                "reasons": reasons,
                "source_state": state,
            }
            for repetition in range(1, repetitions + 1):
                raw_output = {
                    "experiment_id": experiment_id,
                    "case_id": case_id,
                    "arm": arm,
                    "probe_id": probe_id,
                    "repetition": repetition,
                    "recovered": passed,
                    "answer": answer,
                    "reasons": reasons,
                    "source_state": state,
                    "prompt_digest": versions[prompt_key],
                    "kb_manifest_digest": versions[kb_key],
                    "model_digest": versions[model_key],
                    "status": "recorded-replay",
                }
                raw_path = _write_json(
                    output_dir
                    / "probe-outputs"
                    / arm
                    / f"{probe_id}-rep{repetition}.json",
                    raw_output,
                )
                results.append(
                    {
                        "probe_id": probe_id,
                        "repetition": repetition,
                        "recovered": passed,
                        "output_ref": raw_path.resolve().as_uri(),
                        "output_digest": canonical_json_digest(raw_output),
                    }
                )
        outputs[arm] = arm_outputs
        affected_results = [item["recovered"] for item in results if item["probe_id"] in affected]
        control_results = [item["recovered"] for item in results if item["probe_id"] in controls]
        recovery_rate = sum(affected_results) / len(affected_results)
        control_rate = sum(control_results) / len(control_results)
        cells[arm] = {
            "versions": {
                "prompt_digest": versions[prompt_key],
                "kb_manifest_digest": versions[kb_key],
                "model_digest": versions[model_key],
            },
            "results": results,
            "recovery_rate": round(recovery_rate, 4),
            "control_pass_rate": round(control_rate, 4),
        }
        summaries[arm] = {
            "recovery_rate": round(recovery_rate, 4),
            "n_probes": len(affected),
            "n_trials": len(affected_results),
            "control_pass_rate": round(control_rate, 4),
        }

    n = len(affected) * repetitions
    c_rate = cells["C"]["recovery_rate"]
    effects: dict[str, Any] = {}
    significant: list[str] = []
    for arm, layer in (("RP", "prompt"), ("RK", "kb"), ("RM", "model_params")):
        rate = cells[arm]["recovery_rate"]
        lower, upper = newcombe_wilson_diff(rate, n, c_rate, n)
        is_significant = lower > 0.2
        effects[layer] = {
            "delta": round(rate - c_rate, 4),
            "ci95_lower": round(lower, 4),
            "ci95_upper": round(upper, 4),
            "significant": is_significant,
        }
        if is_significant:
            significant.append(layer)
    effects["method"] = "newcombe_wilson_diff"
    controls_ok = all(cells[arm]["control_pass_rate"] == 1.0 for arm in cells)
    g_lower, _ = newcombe_wilson_diff(cells["G"]["recovery_rate"], n, c_rate, n)
    g_recovered = g_lower > 0.2
    hidden_ids = set(B1_HIDDEN)
    hidden_prompt = [
        item["recovered"]
        for item in cells["RP"]["results"]
        if item["probe_id"] in hidden_ids
    ]
    hidden_control = [
        item["recovered"]
        for item in cells["C"]["results"]
        if item["probe_id"] in hidden_ids
    ]
    hidden_reproduced = bool(hidden_prompt) and all(hidden_prompt) and not any(hidden_control)
    decision = "ATTRIBUTED" if controls_ok and g_recovered and significant == ["prompt"] and hidden_reproduced else "INCONCLUSIVE"
    layer = "prompt" if decision == "ATTRIBUTED" else None
    rationale = (
        "recorded outputs satisfy R1-R4: only prompt replacement recovers and hidden probes reproduce"
        if decision == "ATTRIBUTED"
        else "recorded outputs did not satisfy the deterministic attribution rules"
    )
    created_at = _iso()
    bundle = {
        "schema_version": "0.1.0",
        "bundle_id": f"eb_{run_suffix}",
        "experiment_id": experiment_id,
        "case_id": case_id,
        "protocol": {
            "matrix": "five_cell",
            "repetitions": repetitions,
            "random_arm_order": random_arm_order,
            "random_seed_ref": f"seed://{experiment_id}/{seed}",
            "frozen_at": created_at,
            "confidence": 0.95,
        },
        "probe_set": {
            "probe_set_digest": probe_digest,
            "discovery": B1_DISCOVERY,
            "hidden_confirmation": B1_HIDDEN,
            "unaffected_controls": B1_CONTROLS,
        },
        "cells": cells,
        "effects": effects,
        "verdict": {
            "decision": decision,
            "attributed_layer": layer,
            "rationale": rationale,
            "hidden_confirmation_reproduced": hidden_reproduced,
        },
        "created_at": created_at,
    }
    report = {
        "schema_version": "0.1.0",
        "report_id": f"attr_{run_suffix}",
        "experiment_id": experiment_id,
        "case_id": case_id,
        "probe_set_digest": probe_digest,
        "version_digests": versions,
        "cells": summaries,
        "deltas": {
            layer_name: {
                "estimate": effect["delta"],
                "ci95_lower": effect["ci95_lower"],
                "ci95_upper": effect["ci95_upper"],
            }
            for layer_name, effect in effects.items()
            if layer_name != "method"
        },
        "verdict": {
            "decision": decision,
            "attributed_layer": layer,
            "interaction_detected": False,
            "full_factorial_required": False,
            "rationale": rationale,
        },
        "evidence_bundle_ref": {
            "uri": _repo_uri(output_dir / "evidence-bundle.json"),
            "digest": canonical_json_digest(bundle),
        },
        "generated_at": created_at,
    }
    report["deltas"]["method"] = "newcombe_wilson_diff"
    return bundle, report, outputs


def _rebind_probe_output_artifacts(
    *,
    output_dir: Path,
    evidence_bundle: dict[str, Any],
    probe_outputs: dict[str, Any],
    cell_versionsets: dict[str, dict[str, Any]],
) -> None:
    """Persist raw outputs with the final authoritative Case/Experiment ids."""

    experiment_id = evidence_bundle["experiment_id"]
    case_id = evidence_bundle["case_id"]
    for arm, cell in evidence_bundle["cells"].items():
        versions = cell["versions"]
        versionset = cell_versionsets[arm]
        for trial in cell["results"]:
            probe_id = trial["probe_id"]
            recorded = probe_outputs[arm][probe_id]
            raw_output = {
                "experiment_id": experiment_id,
                "case_id": case_id,
                "arm": arm,
                "probe_id": probe_id,
                "repetition": trial["repetition"],
                "recovered": trial["recovered"],
                "answer": recorded["answer"],
                "reasons": recorded["reasons"],
                "source_state": recorded["source_state"],
                "versionset_id": versionset["versionset_id"],
                "versionset_digest": versionset["digest"],
                "versionset_revision": versionset["revision"],
                "prompt_digest": versions["prompt_digest"],
                "kb_manifest_digest": versions["kb_manifest_digest"],
                "model_digest": versions["model_digest"],
                "status": "recorded-replay",
            }
            raw_path = _write_json(
                output_dir
                / "probe-outputs"
                / arm
                / f"{probe_id}-rep{trial['repetition']}.json",
                raw_output,
            )
            trial["output_ref"] = raw_path.resolve().as_uri()
            trial["output_digest"] = canonical_json_digest(raw_output)


def _approval(
    *,
    approval_id: str,
    workorder: dict[str, Any],
    expiry: str,
    authorization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nonce = (
        workorder["nonce"]
        if authorization is None
        else str(uuid.uuid5(uuid.NAMESPACE_URL, f"caseloop-b1-replay:{approval_id}"))
    )
    payload = {
        "schema_version": "0.1.0",
        "approval_id": approval_id,
        "workorder_hash": workorder["hash"],
        "workorder_id": workorder["workorder_id"],
        "nonce": nonce,
        "expiry": expiry,
        "approver": {"type": "human", "identity": "human:contract-replay-fixture"},
        "decision": "approved",
        "decided_at": _iso(),
        "nonce_consumed": False,
        "proof": {
            "method": "server_recorded",
            "ref": f"replay://human-approval-fixture/{approval_id}",
        },
    }
    if authorization is not None:
        payload["authorization"] = authorization
    return payload


def _gate_report(
    *,
    run_suffix: str,
    phase: str,
    target_digest: str,
    probe_digest: str,
    probe_set: Any,
    answers: dict[str, str],
    athlete_digest: str,
    contract_result: SuiteResult,
    replay_result: SuiteResult,
    candidate_evidence: Path,
) -> dict[str, Any]:
    runner = GateRunner(
        EvalSettings(),
        probe_set,
        judge=RecordedReplayJudge(),
        frozen_probe_set_digest=probe_digest,
    )
    contract_ref = _inline_json_artifact(_path_from_evidence_uri(contract_result.report_ref))
    replay_ref = _inline_json_artifact(_path_from_evidence_uri(replay_result.report_ref))
    candidate_ref = _inline_json_artifact(candidate_evidence)
    gate_contract = SuiteResult(
        suite=contract_result.suite,
        kind=contract_result.kind,
        status=contract_result.status,
        n_passed=contract_result.n_passed,
        n_failed=contract_result.n_failed,
        report_ref=contract_ref["uri"],
        report_digest=contract_ref["digest"],
    )
    gate_replay = SuiteResult(
        suite=replay_result.suite,
        kind=replay_result.kind,
        status=replay_result.status,
        n_passed=replay_result.n_passed,
        n_failed=replay_result.n_failed,
        report_ref=replay_ref["uri"],
        report_digest=replay_ref["digest"],
    )
    return runner.run(
        GateCandidate(
            target_versionset_digest=target_digest,
            probe_set_digest=probe_digest,
            regression_suite_digest=canonical_json_digest(
                {"fixture": "b1-prompt-regression.yaml", "phase": phase}
            ),
            answers=answers,
            athlete_model_digest=athlete_digest,
            source="replay",
        ),
        contract_result=gate_contract,
        replay_result=gate_replay,
        artifact_refs=[contract_ref, replay_ref, candidate_ref],
        live_available=False,
        policy_profile="isolated-replay",
        eval_id=f"eval_{run_suffix}{phase}",
        report_id=f"gate_{run_suffix}{phase}",
    )


def run_replay(
    *,
    output_dir: Path,
    repetitions: int,
    seed: int,
    external_suites: bool,
    allow_dirty: bool,
    suite_python: str | None = None,
) -> Path:
    started_at = _utcnow()
    _require_portable_output_dir(output_dir, allow_dirty=allow_dirty)
    if allow_dirty:
        working_tree_before_run = "dirty-check-bypassed-for-explicit-test-run"
    else:
        working_tree_before_run = _git("status", "--short")
        if working_tree_before_run:
            raise RuntimeError(
                "refusing to generate final B1 evidence from an uncommitted working tree; "
                "commit the implementation or pass --allow-dirty only in an explicit test run"
            )
    run_suffix = output_dir.name.removeprefix("b1run_")
    if re.fullmatch(r"[0-9A-Za-z]{8,64}", run_suffix) is None:
        run_suffix = hashlib.sha256(str(output_dir).encode("utf-8")).hexdigest()[:16]
    run_id = f"b1run_{run_suffix}"
    transaction_id = f"tx_{run_suffix}"
    case_channel = f"feishu-mock:b1-replay:{run_suffix}"
    output_dir.mkdir(parents=True, exist_ok=False)

    responses = json.loads(
        (REPO_ROOT / "eval-harness" / "samples" / "b1_probe_responses.json").read_text(encoding="utf-8")
    )
    probe_set = load_probe_set(REPO_ROOT)
    probe_digest = frozen_digest(probe_set)
    if responses.get("probe_set_digest") != probe_digest:
        raise RuntimeError("recorded B1 responses do not match the frozen probe-set digest")

    good_prompt_ref = {"prompt_id": "prompts/system.md", "version": "v1.4.2"}
    bad_prompt_ref = {"prompt_id": "prompts/system.md", "version": "v1.4.3-b1"}
    p0 = canonical_json_digest(good_prompt_ref)
    p1 = canonical_json_digest(bad_prompt_ref)
    kb_entries = [
        {"kb_id": "customer-service", "entry_id": "returns-policy", "version": "1.0.0"},
        {"kb_id": "customer-service", "entry_id": "shipping-policy", "version": "1.0.0"},
    ]
    normalized_kb_entries = [
        {**entry, "digest": canonical_json_digest(entry)} for entry in kb_entries
    ]
    normalized_kb_entries.sort(key=lambda item: (item["kb_id"], item["entry_id"], item["version"]))
    k0 = canonical_json_digest({"entries": normalized_kb_entries})
    model_ref = {
        "provider": "recorded-replay",
        "model": "athlete-v1",
        "params": {"temperature": 0},
    }
    m0 = canonical_json_digest(model_ref)
    versions = {"P0": p0, "P1": p1, "K0": k0, "K1": k0, "M0": m0, "M1": m0}
    good_content = {
        "prompt": {**good_prompt_ref, "digest": p0},
        "kb_manifest": {"entries": normalized_kb_entries, "manifest_digest": k0},
        "model": {**model_ref, "digest": m0},
    }
    bad_content = {
        "prompt": {**bad_prompt_ref, "digest": p1},
        "kb_manifest": dict(good_content["kb_manifest"]),
        "model": dict(good_content["model"]),
    }
    good_vs_digest = canonical_json_digest(good_content)
    bad_vs_digest = canonical_json_digest(bad_content)
    bad_versionset_id = f"vs_{run_suffix}bad"
    good_versionset_id = f"vs_{run_suffix}good"

    case_id_placeholder = f"case_{run_suffix}"
    experiment_id_placeholder = f"exp_{run_suffix}"
    bundle, attribution_report, probe_outputs = _build_attribution_artifacts(
        run_suffix=run_suffix,
        output_dir=output_dir,
        experiment_id=experiment_id_placeholder,
        case_id=case_id_placeholder,
        probe_digest=probe_digest,
        versions=versions,
        responses=responses,
        probe_set=probe_set,
        repetitions=repetitions,
        seed=seed,
    )

    audit_path = output_dir / "audit-export.jsonl"
    with tempfile.TemporaryDirectory(prefix="caseloop-b1-replay-") as temp_dir:
        database_path = Path(temp_dir) / "control-plane.sqlite3"
        engine = create_engine(f"sqlite+pysqlite:///{database_path}")
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        settings = ControlSettings(
            database_url=f"sqlite+pysqlite:///{database_path}",
            audit_jsonl_path=str(audit_path),
            gate_policy_profile="isolated-replay",
            allow_isolated_replay_gate=True,
            allow_isolated_replay_attribution=True,
            notification_adapter="feishu-mock",
            canary_steps="5",
            canary_observation_seconds=1,
            operation_poll_timeout_seconds=0.05,
            reconcile_backoff_initial_seconds=0,
            reconcile_backoff_max_seconds=0,
            outbox_retry_initial_seconds=0,
            outbox_retry_max_seconds=0,
        )
        quality = FakeQualityClient()
        quality.seed_versionset(
            bad_versionset_id,
            status="active",
            revision=1,
            digest=bad_vs_digest,
            content=bad_content,
        )
        quality.seed_versionset(
            good_versionset_id,
            status="superseded",
            revision=1,
            digest=good_vs_digest,
            content=good_content,
        )

        with SessionLocal() as session:
            cases = CaseService(session, settings)
            complaint = cases.ingest_complaint(
                source="webhook",
                text="Yesterday returns were allowed, but today the assistant says activated headphones cannot be returned.",
                external_id=transaction_id,
                channel=case_channel,
                thread_ref=f"thread:{run_suffix}",
                complainant_ref=f"replay-user:{run_suffix}",
                app_ref="demo-app:b1-replay",
                title="B1 prompt regression replay",
            )
            duplicate = cases.ingest_complaint(
                source="webhook",
                text="duplicate delivery of the same B1 complaint",
                external_id=transaction_id,
                channel=case_channel,
                thread_ref=f"thread:{run_suffix}",
            )
            if not duplicate.get("duplicate") or duplicate.get("case_id") != complaint["case_id"]:
                raise RuntimeError("B1 inbox did not deduplicate to the original Case")
            case_id = complaint["case_id"]
            lease = cases.claim(case_id, "attributionist:fixed-warm-pool-replay")
            experiments = ExperimentService(session, settings, quality)
            experiment_id = experiments.create(case_id=case_id, hypothesis_layer="prompt")["experiment_id"]
            session.commit()

        bad_ref = {"versionset_id": bad_versionset_id, "digest": bad_vs_digest, "revision": 1}
        good_ref = {"versionset_id": good_versionset_id, "digest": good_vs_digest, "revision": 1}
        cell_versionsets = {"C": bad_ref, "RP": good_ref, "RK": bad_ref, "RM": bad_ref, "G": good_ref}

        bundle["case_id"] = case_id
        bundle["experiment_id"] = experiment_id
        bundle["protocol"]["random_seed_ref"] = f"seed://{experiment_id}/{seed}"
        _rebind_probe_output_artifacts(
            output_dir=output_dir,
            evidence_bundle=bundle,
            probe_outputs=probe_outputs,
            cell_versionsets=cell_versionsets,
        )
        attribution_report["case_id"] = case_id
        attribution_report["experiment_id"] = experiment_id
        attribution_report["evidence_bundle_ref"] = {
            "uri": _repo_uri(output_dir / "evidence-bundle.json"),
            "digest": canonical_json_digest(bundle),
        }
        _write_json(output_dir / "probe-outputs.json", probe_outputs)
        _write_json(output_dir / "evidence-bundle.json", bundle)
        _write_json(output_dir / "attribution-report.json", attribution_report)

        frozen = {
            "execution_profile": "isolated-replay",
            "probe_set_digest": probe_digest,
            "discovery": B1_DISCOVERY,
            "hidden_confirmation": B1_HIDDEN,
            "unaffected_controls": B1_CONTROLS,
            "repetitions": repetitions,
            "versions": versions,
            "cell_versionsets": cell_versionsets,
            "random_seed_ref": f"seed://{experiment_id}/{seed}",
            "confidence": 0.95,
        }
        with SessionLocal() as session:
            experiments = ExperimentService(session, settings, quality)
            experiments.freeze_protocol(experiment_id, **frozen)
            experiments.start(
                experiment_id,
                runner_id="attributionist:fixed-warm-pool-replay",
                lease_id=lease["lease_id"],
                fencing_token=lease["fencing_token"],
            )
            order = [item.split("@", 1)[0] for item in bundle["protocol"]["random_arm_order"]]
            unique_order = list(dict.fromkeys(order))
            for index, arm in enumerate(unique_order):
                for trial in bundle["cells"][arm]["results"]:
                    experiments.trial_completed(
                        experiment_id,
                        cell=arm,
                        probe_id=trial["probe_id"],
                        repetition=trial["repetition"],
                        recovered=trial["recovered"],
                        output_ref=trial["output_ref"],
                        output_digest=trial["output_digest"],
                        fencing_token=lease["fencing_token"],
                    )
                experiments.cell_completed(
                    experiment_id,
                    cell=arm,
                    arm_order_index=index,
                    recovery_rate=bundle["cells"][arm]["recovery_rate"],
                    fencing_token=lease["fencing_token"],
                )
            verdict = experiments.verdict_computed(
                experiment_id,
                fencing_token=lease["fencing_token"],
                evidence_bundle=bundle,
                attribution_report=attribution_report,
            )
            if verdict["payload"].get("verdict") != "ATTRIBUTED" or verdict["payload"].get("attributed_layer") != "prompt":
                raise RuntimeError("authoritative B1 attribution was not ATTRIBUTED/prompt")
            session.commit()

        candidate_evidence = _write_json(
            output_dir / "gate-candidate-responses.json",
            {
                "source": "recorded-replay",
                "versionset_digest": good_vs_digest,
                "answers": {probe_id: item["answer"] for probe_id, item in responses["states"]["baseline"].items()},
            },
        )
        if external_suites:
            suite_interpreter = suite_python or sys.executable
            contract_result = _run_suite(
                kind="contract",
                command=[suite_interpreter, "-m", "pytest", "-q", "conformance/test_schemas.py", "conformance/test_wilson.py"],
                cwd=REPO_ROOT / "contracts",
                report_path=output_dir / "contract-suite.json",
            )
            replay_result = _run_suite(
                kind="replay",
                command=[suite_interpreter, "-m", "pytest", "-q", "tests/unit/test_probe_judge.py", "tests/unit/test_gate.py"],
                cwd=REPO_ROOT / "eval-harness",
                report_path=output_dir / "replay-suite.json",
            )
        else:
            contract_result, replay_result = _embedded_suites(
                output_dir=output_dir,
                evidence_bundle=bundle,
                attribution_report=attribution_report,
                probe_set=probe_set,
                answers={probe_id: item["answer"] for probe_id, item in responses["states"]["baseline"].items()},
            )
        answers = {probe_id: item["answer"] for probe_id, item in responses["states"]["baseline"].items()}

        workorder_id = f"wo_{run_suffix}"
        initial_gate = _gate_report(
            run_suffix=run_suffix,
            phase="initial",
            target_digest=good_vs_digest,
            probe_digest=probe_digest,
            probe_set=probe_set,
            answers=answers,
            athlete_digest=m0,
            contract_result=contract_result,
            replay_result=replay_result,
            candidate_evidence=candidate_evidence,
        )

        with SessionLocal() as session:
            repair_worker_id = "repairer:fixed-warm-pool-replay"
            repair_lease = CaseService(session, settings).claim(case_id, repair_worker_id)
            releases = ReleaseService(session, quality, settings)
            proposal = {
                "case_id": case_id,
                "channel": "prompt",
                "attribution_report_digest": canonical_json_digest(attribution_report),
                "base_versionset_id": bad_versionset_id,
                "base_versionset_digest": bad_vs_digest,
                "base_revision": 1,
                "target_prompt_digest": p0,
                "content": good_content,
            }
            candidate = releases.create_candidate(
                **proposal,
                worker_id=repair_worker_id,
                fencing_token=repair_lease["fencing_token"],
                proposal_digest=canonical_json_digest(proposal),
                idempotency_key=f"candidate-{run_suffix}",
            )
            if candidate["digest"] != good_vs_digest:
                raise RuntimeError("candidate receipt did not match recorded last-known-good VersionSet")
            initial_gate["subject"]["target_versionset_digest"] = candidate["digest"]
            initial_hash = canonical_json_digest(initial_gate, prefix=False)
            releases.gates.register_report(
                {
                    "report": initial_gate,
                    "report_hash": initial_hash,
                    "workorder_id": workorder_id,
                    "target_versionset_id": candidate["versionset_id"],
                    "target_revision": candidate["revision"],
                    "dataset_id": probe_set.probe_set_id,
                    "dataset_version": probe_set.version,
                    "evidence_digest": canonical_json_digest(initial_gate["artifact_refs"]),
                    "correlation_id": case_id,
                }
            )
            expiry = _iso(_utcnow() + timedelta(minutes=30))
            diff_content = "Replace the activated-item denial with the approved seven-day no-reason return policy."
            workorder = {
                "schema_version": "0.1.0",
                "workorder_id": workorder_id,
                "case_id": case_id,
                "channel": "prompt",
                "base_versionset_digest": bad_vs_digest,
                "target_versionset_digest": candidate["digest"],
                "input_versions": candidate["input_versions"],
                "diff": {
                    "format": "unified_diff",
                    "content": diff_content,
                    "digest": "sha256:" + hashlib.sha256(diff_content.encode("utf-8")).hexdigest(),
                },
                "gate_report_ref": {
                    "uri": f"eval://{initial_gate['eval_id']}",
                    "digest": f"sha256:{initial_hash}",
                },
                "expiry": expiry,
                "nonce": str(uuid.uuid5(uuid.NAMESPACE_URL, f"caseloop-b1-workorder:{run_suffix}")),
                "created_at": _iso(),
                "created_by": repair_worker_id,
                "hash_rule": "jcs-rfc8785+sha256",
            }
            workorder["hash"] = workorder_hash(workorder)
            releases.register_workorder(
                workorder,
                worker_id=repair_worker_id,
                fencing_token=repair_lease["fencing_token"],
            )
            initial_approval = _approval(
                approval_id=f"appr_{run_suffix}initial",
                workorder=workorder,
                expiry=expiry,
            )
            releases.grant_approval(initial_approval)
            release_id = f"rel_{run_suffix}"
            start_receipt = releases.start_release(
                workorder_id=workorder_id,
                approval_id=initial_approval["approval_id"],
                versionset_id=candidate["versionset_id"],
                release_id=release_id,
            )
            stage_receipt = releases.stage(release_id, idempotency_key=f"stage-{run_suffix}")
            canary_context = releases.action_authorization_context(release_id, "canary")
            canary_approval = _approval(
                approval_id=f"appr_{run_suffix}canary",
                workorder=workorder,
                expiry=expiry,
                authorization=canary_context["authorization"],
            )
            releases.grant_approval(canary_approval)
            canary_receipt = releases.canary(
                release_id,
                percent=canary_context["authorization"]["params"]["percent"],
                idempotency_key=f"canary-{run_suffix}",
                approval_id=canary_approval["approval_id"],
            )
            time.sleep(1.05)
            verification_context = releases.verification_context(release_id)
            session.commit()

        post_gate = _gate_report(
            run_suffix=run_suffix,
            phase="postcanary",
            target_digest=verification_context["target_versionset_digest"],
            probe_digest=probe_digest,
            probe_set=probe_set,
            answers=answers,
            athlete_digest=m0,
            contract_result=contract_result,
            replay_result=replay_result,
            candidate_evidence=candidate_evidence,
        )
        post_hash = canonical_json_digest(post_gate, prefix=False)
        reply_body_path = _write_text(
            output_dir / "reply-body.txt",
            "Your reported return-policy regression was attributed to the prompt layer, fixed, gated, and promoted.\n",
        )
        reply_body_ref = _repo_uri(reply_body_path)
        reply_body_digest = _file_digest(reply_body_path)
        with SessionLocal() as session:
            releases = ReleaseService(session, quality, settings)
            releases.gates.register_report(
                {
                    "report": post_gate,
                    "report_hash": post_hash,
                    "workorder_id": workorder_id,
                    "target_versionset_id": verification_context["target_versionset_id"],
                    "target_revision": verification_context["target_revision"],
                    "dataset_id": probe_set.probe_set_id,
                    "dataset_version": probe_set.version,
                    "evidence_digest": canonical_json_digest(post_gate["artifact_refs"]),
                    "correlation_id": case_id,
                }
            )
            verification_receipt = releases.record_verification(
                release_id,
                eval_id=post_gate["eval_id"],
                report_hash=post_hash,
            )
            promote_context = releases.action_authorization_context(release_id, "promote")
            promote_approval = _approval(
                approval_id=f"appr_{run_suffix}promote",
                workorder=workorder,
                expiry=expiry,
                authorization=promote_context["authorization"],
            )
            releases.grant_approval(promote_approval)
            closure_context = releases.configure_closure(
                release_id,
                channel=case_channel,
                thread_ref=f"thread:{run_suffix}",
                body_ref=reply_body_ref,
                body_digest=reply_body_digest,
            )
            promote_receipt = releases.promote(
                release_id,
                idempotency_key=f"promote-{run_suffix}",
                approval_id=promote_approval["approval_id"],
            )
            if promote_receipt["state"] != "COMPLETED":
                raise RuntimeError("B1 replay did not perform a real Quality promote operation")
            # Promotion commits an authoritative Release transaction.  The
            # RELEASE_PROMOTED outbox consumer owns the durable continuation.
            session.commit()

        adapter = FeishuMockAdapter()
        dispatcher = OutboxDispatcher(
            SessionLocal,
            settings,
            notification_adapter=adapter,
            worker_id="outbox-worker:fixed-warm-pool-replay",
        )
        dispatch_totals = Counter()
        for _ in range(20):
            result = dispatcher.dispatch_batch(limit=100)
            dispatch_totals.update(result)
            if result["claimed"] == 0:
                break
        duplicate_dispatch = dispatcher.dispatch_batch(limit=100)

        with SessionLocal() as session:
            persisted_closure = session.get(ReleaseClosure, release_id)
            if (
                persisted_closure is None
                or persisted_closure.status != "queued"
                or not persisted_closure.notification_id
            ):
                raise RuntimeError("RELEASE_PROMOTED outbox did not durably continue to notification")
            notification_id = persisted_closure.notification_id

        with SessionLocal() as session:
            closure_retry = CaseClosureService(session, settings).resolve_and_queue(
                release_id=release_id,
                channel=case_channel,
                thread_ref=f"thread:{run_suffix}",
                body_ref=reply_body_ref,
                body_digest=reply_body_digest,
            )
            if (
                closure_retry["notification"]["notification_id"] != notification_id
                or not closure_retry["notification"].get("duplicate")
            ):
                raise RuntimeError("B1 closure retry was not idempotent after notification SENT")
            session.commit()

        with SessionLocal() as session:
            case = session.get(Aggregate, {"aggregate_type": "case", "aggregate_id": case_id})
            notification = session.get(
                Aggregate,
                {"aggregate_type": "notification", "aggregate_id": notification_id},
            )
            trust = session.scalar(
                select(TrustLedger)
                .where(
                    TrustLedger.risk_class == "R2_HIGH_IMPACT",
                    TrustLedger.action_type == "release_outcome",
                )
                .order_by(TrustLedger.epoch.desc())
                .limit(1)
            )
            trust_entries = list(session.scalars(select(TrustLedgerEntry).order_by(TrustLedgerEntry.recorded_at)).all())
            outboxes = list(session.scalars(select(Outbox).order_by(Outbox.created_at, Outbox.outbox_id)).all())
            receipts = list(
                session.scalars(select(OutboxDeliveryReceipt).order_by(OutboxDeliveryReceipt.delivered_at)).all()
            )
            events = list(session.scalars(select(Event).order_by(Event.created_at, Event.event_id)).all())
            audits = list(session.scalars(select(Audit).order_by(Audit.ts, Audit.audit_id)).all())
            operations = list(
                session.scalars(select(ControllerOperation).order_by(ControllerOperation.created_at)).all()
            )
            persisted_approvals = list(
                session.scalars(select(Approval).order_by(Approval.created_at, Approval.approval_id)).all()
            )
            persisted_workorders = list(
                session.scalars(
                    select(WorkOrder).where(WorkOrder.workorder_id == workorder_id)
                ).all()
            )
            persisted_gate_reports = list(
                session.scalars(
                    select(GateReportRecord)
                    .where(GateReportRecord.workorder_id == workorder_id)
                    .order_by(GateReportRecord.created_at, GateReportRecord.eval_id)
                ).all()
            )
            if case is None or case.state != "CLOSED":
                raise RuntimeError(f"B1 Case did not archive; state={case.state if case else 'missing'}")
            if notification is None or notification.state != "SENT":
                raise RuntimeError("B1 notification did not obtain a provider-bound SENT receipt")
            if trust is None or trust.trials != 1 or trust.successes != 1 or len(trust_entries) != 1:
                raise RuntimeError("B1 Trust consumer did not record exactly one action sample")
            if (trust.payload or {}).get("promotion_eligible") is not False or trust.autonomy_state != "MANUAL":
                raise RuntimeError("B1 Trust decision did not deny autonomy promotion")
            if any(row.status != "SENT" for row in outboxes):
                raise RuntimeError("B1 outbox contains undelivered or dead rows")
            observed_domain_events = {row.event_type for row in outboxes if row.channel == "domain.events"}
            missing_events = REQUIRED_DOMAIN_EVENTS - observed_domain_events
            if missing_events:
                raise RuntimeError(f"B1 domain-event evidence is incomplete: {sorted(missing_events)}")
            if duplicate_dispatch["claimed"] != 0 or len(trust_entries) != 1:
                raise RuntimeError("outbox replay changed the one-action-one-sample Trust count")

            event_rows = [_row(row) for row in events]
            audit_rows = [_row(row) for row in audits]
            outbox_rows = [_row(row) for row in outboxes]
            receipt_rows = [_row(row) for row in receipts]
            operation_rows = [_row(row) for row in operations]
            persisted_workorder_rows = [_row(row) for row in persisted_workorders]
            persisted_gate_report_rows = [_row(row) for row in persisted_gate_reports]
            approval_evidence = [
                {
                    **dict(row.payload or {}),
                    "persistence": {
                        "status": row.status,
                        "decision": row.decision,
                        "expiry": _jsonable(row.expiry),
                        "decided_at": _jsonable(row.decided_at),
                        "consumed_at": _jsonable(row.consumed_at),
                    },
                }
                for row in persisted_approvals
            ]
            case_payload = dict(case.payload or {})
            notification_payload = dict(notification.payload or {})
            trust_payload = dict(trust.payload or {})
            trust_entry_payload = dict(trust_entries[-1].payload or {})

        frozen_path = _write_json(
            output_dir / "frozen-versionset.json",
            {
                "fixture": "contracts/fixtures/b1-prompt-regression.yaml",
                "injection_adapter": "FakeQualityClient:isolated-replay",
                "badcase_injected": True,
                "active_bad_versionset": bad_ref,
                "known_good_versionset": good_ref,
                "component_digests": versions,
                "cell_versionsets": cell_versionsets,
                "digest": canonical_json_digest({"versions": versions, "cell_versionsets": cell_versionsets}),
            },
        )
        plan_path = _write_json(
            output_dir / "experiment-plan.json",
            {
                "transaction_id": transaction_id,
                "experiment_id": experiment_id,
                "case_id": case_id,
                "protocol": frozen,
                "random_arm_order": bundle["protocol"]["random_arm_order"],
            },
        )
        probes_path = _write_json(
            output_dir / "probes.json",
            {
                "probe_set_id": probe_set.probe_set_id,
                "version": probe_set.version,
                "digest": probe_digest,
                "selected": B1_PROBES,
                "fixture": "contracts/fixtures/probes-customer-service.yaml",
            },
        )
        workorder_path = _write_json(output_dir / "workorder.json", workorder)
        gates_path = _write_json(
            output_dir / "gate-reports.json",
            {"policy_profile": "isolated-replay", "initial": initial_gate, "post_canary": post_gate},
        )
        approvals_path = _write_json(
            output_dir / "approval-grants.json",
            {
                "adapter": "human-approval-contract-replay-fixture",
                "not_live_human_approval": True,
                "grants": approval_evidence,
            },
        )
        release_receipts_path = _write_json(
            output_dir / "release-receipts.json",
            {
                "start": start_receipt,
                "stage": stage_receipt,
                "canary": canary_receipt,
                "verification": verification_receipt,
                "promote": promote_receipt,
                "controller_operations": operation_rows,
                "quality_call_log": quality.call_log,
                "persisted_workorders": persisted_workorder_rows,
                "persisted_gate_reports": persisted_gate_report_rows,
            },
        )
        canary_path = _write_json(
            output_dir / "canary-metrics.json",
            {
                "mode": "isolated-replay",
                "traffic_routed": False,
                "measurement": "exact-target recorded probe replay after a real Quality canary lifecycle operation",
                "canary_percent": canary_context["authorization"]["params"]["percent"],
                "observation": verification_context["canary_observation"],
                "probe_count": len(answers),
                "error_count": 0,
                "verification_eval_id": post_gate["eval_id"],
                "rule_track": post_gate["rule_track"],
                "judge_track": post_gate["judge_track"],
                "live_provider_e2e": post_gate["live_provider_e2e"],
            },
        )
        terminal_path = _write_json(output_dir / "promote-receipt.json", promote_receipt)
        notification_path = _write_json(
            output_dir / "notification-receipt.json",
            {
                "adapter": "feishu-mock:contract-replay-only",
                "notification_id": notification_id,
                "state": notification.state,
                "payload": notification_payload,
                "closure_context": closure_context,
                "closure_retry": closure_retry,
            },
        )
        audit_events_path = _write_json(output_dir / "audit-events.json", audit_rows)
        domain_events_path = _write_json(
            output_dir / "domain-events.json",
            {
                "required_catalog": sorted(REQUIRED_DOMAIN_EVENTS),
                "observed": sorted(observed_domain_events),
                "rows": [row for row in outbox_rows if row["channel"] == "domain.events"],
            },
        )
        outbox_receipts_path = _write_json(
            output_dir / "outbox-receipts.json",
            {
                "dispatch_totals": dict(dispatch_totals),
                "duplicate_dispatch": duplicate_dispatch,
                "outbox": outbox_rows,
                "receipts": receipt_rows,
            },
        )
        trust_path = _write_json(
            output_dir / "trust-decision.json",
            {
                "ledger": trust_payload,
                "entry": trust_entry_payload,
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
        trace_path = _write_json(
            output_dir / "trace.json",
            {
                "transaction_id": transaction_id,
                "correlation_id": case_id,
                "otel_exported": False,
                "otel_status": "isolated replay exports event-envelope trace references only",
                "trace_ids": sorted({row["trace_id"] for row in event_rows if row.get("trace_id")}),
                "events": event_rows,
            },
        )
        execution_sources = [
            (
                "complaint-ingest",
                [row["event_id"] for row in event_rows if row["event_type"] == "complaint.received"],
            ),
            (
                "experiment-controller",
                [
                    row["event_id"]
                    for row in event_rows
                    if row["event_type"] in {"experiment.requested", "experiment.verdict_computed"}
                ],
            ),
            (
                "repair-proposal-controller",
                [
                    row["audit_id"]
                    for row in audit_rows
                    if row["action"] in {"candidate.create.intent", "candidate.create.complete", "workorder.register"}
                ],
            ),
            (
                "gate-controller",
                [row["event_id"] for row in event_rows if row["event_type"] == "eval.passed"],
            ),
            (
                "release-controller",
                [
                    row["event_id"]
                    for row in event_rows
                    if row["event_type"] in {"release.staged", "release.canary_started", "release.promoted"}
                ],
            ),
            (
                "notification-closure-controller",
                [
                    row["event_id"]
                    for row in event_rows
                    if row["event_type"] in {"notification.sent", "case.closed"}
                ],
            ),
        ]
        if any(not source_ids for _, source_ids in execution_sources):
            missing_sources = [component for component, source_ids in execution_sources if not source_ids]
            raise RuntimeError(f"B1 execution trace is missing authoritative sources: {missing_sources}")
        agent_runs_path = _write_json(
            output_dir / "agent-runs.json",
            {
                "pool": "phase-1-fixed-warm-pool",
                "dynamic_scaling": False,
                "mode": "isolated-replay",
                "recording_kind": "deterministic-replay-execution-trace",
                "not_live_agent_execution": True,
                "runs": [
                    {
                        "component": component,
                        "llm_invoked": False,
                        "source_ids": source_ids,
                    }
                    for component, source_ids in execution_sources
                ],
            },
        )
        commits_path = _write_json(
            output_dir / "commits.json",
            {
                "branch": _git("branch", "--show-current"),
                "repository_start_commit": _git("rev-parse", "origin/main"),
                "repository_end_commit": _git("rev-parse", "HEAD"),
                "origin_main_commit": _git("rev-parse", "origin/main"),
                "working_tree": working_tree_before_run,
                "working_tree_observed_at": "before evidence directory creation",
                "recent": _git("log", "--oneline", "-12"),
            },
        )
        contract_replay_path = _write_json(
            output_dir / "contract-replay-report.json",
            {
                "contract": _jsonable(contract_result.__dict__),
                "replay": _jsonable(replay_result.__dict__),
                "gate_policy_profile": "isolated-replay",
                "live_provider_counted_as_pass": False,
            },
        )
        live_runner_environment = [
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
        ]
        live_external_services = [
            "reachable live Control Plane configured with the Feishu live adapter",
            "reachable Quality API plus immutable good/bad B1 VersionSets",
            "StepFun athlete credentials and a distinct live judge model",
            "independently credentialed human ApprovalGrant command",
            "post-injection Feishu complaint acquisition command",
            "AgentTeams v1.2.1 fixed warm pool, Matrix room, skill execution command, and externally anchored Ed25519 exporter key",
        ]
        live_path = _write_json(
            output_dir / "live-provider-report.json",
            {
                "status": "blocked",
                "mode": "isolated-replay",
                "reason": "this command intentionally does not call live providers",
                "missing_or_external_prerequisites": [
                    *live_runner_environment,
                    *live_external_services,
                ],
                "required_runner_environment": live_runner_environment,
                "external_services_and_credentials": live_external_services,
                "command": "make demo-b1-live",
            },
        )

        artifacts = {
            "agent_runs": _artifact(agent_runs_path),
            "frozen_versionset": _artifact(frozen_path),
            "experiment_plan": _artifact(plan_path),
            "probes": _artifact(probes_path),
            "probe_outputs": _artifact(output_dir / "probe-outputs.json"),
            "evidence_bundle": _artifact(output_dir / "evidence-bundle.json"),
            "attribution_report": _artifact(output_dir / "attribution-report.json"),
            "workorder": _artifact(workorder_path),
            "gate_report": _artifact(gates_path),
            "approval_grants": _artifact(approvals_path),
            "release_receipts": _artifact(release_receipts_path),
            "canary_metrics": _artifact(canary_path),
            "release_terminal_receipt": _artifact(terminal_path),
            "notification_receipt": _artifact(notification_path),
            "audit_events": _artifact(audit_events_path),
            "domain_events": _artifact(domain_events_path),
            "outbox_receipts": _artifact(outbox_receipts_path),
            "trust_decision": _artifact(trust_path),
            "trace": _artifact(trace_path),
            "commits": _artifact(commits_path),
            "contract_replay_report": _artifact(contract_replay_path),
            "live_provider_report": _artifact(live_path),
        }
        target_remote = quality.get_versionset(candidate["versionset_id"])
        manifest = {
            "schema_version": "0.1.0",
            "fixture_id": "B1",
            "run_id": run_id,
            "mode": "isolated-replay",
            "status": "passed",
            "started_at": _iso(started_at),
            "completed_at": _iso(),
            "transaction_id": transaction_id,
            "case_id": case_id,
            "experiment_id": experiment_id,
            "workorder_id": workorder_id,
            "workorder_hash": workorder["hash"],
            "release_id": release_id,
            "notification_id": notification_id,
            "versions": {
                "repository_start_commit": _git("rev-parse", "origin/main"),
                "repository_end_commit": _git("rev-parse", "HEAD"),
                "base_versionset": bad_ref,
                "target_versionset": {
                    "versionset_id": target_remote["versionset_id"],
                    "digest": target_remote["digest"],
                    "revision": target_remote["revision"],
                },
            },
            "outcomes": {
                "deduplicated": True,
                "attribution": {"decision": "ATTRIBUTED", "fault_layer": "prompt"},
                "release": "promoted",
                "notification": {"status": "sent", "provider": "feishu-mock"},
                "case": case.state,
                "trust": {
                    "samples_added": 1,
                    "autonomy_state": trust.autonomy_state,
                    "promotion_decision": "denied",
                    "wilson_lower": trust_payload["wilson_lower"],
                },
                "live_provider": "blocked",
            },
            "artifacts": artifacts,
            "test_reports": [
                {
                    "kind": "contract",
                    "status": contract_result.status,
                    "passed": contract_result.n_passed,
                    "failed": contract_result.n_failed,
                    "report_ref": {"uri": contract_result.report_ref, "digest": contract_result.report_digest},
                },
                {
                    "kind": "replay",
                    "status": replay_result.status,
                    "passed": replay_result.n_passed,
                    "failed": replay_result.n_failed,
                    "report_ref": {"uri": replay_result.report_ref, "digest": replay_result.report_digest},
                },
                {
                    "kind": "live-provider",
                    "status": "blocked",
                    "passed": 0,
                    "failed": 0,
                    "report_ref": _artifact(live_path),
                },
            ],
            "external_blockers": [
                "live StepFun athlete and distinct judge credentials are external",
                "live Control Plane, Quality API, and Feishu provider configuration are external",
                "human approval authority and attested AgentTeams/Matrix fixed warm pool are external",
            ],
        }
        schema = json.loads(
            (REPO_ROOT / "contracts" / "schemas" / "b1-run-manifest.schema.json").read_text(encoding="utf-8")
        )
        errors = sorted(
            Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(manifest),
            key=lambda error: list(error.absolute_path),
        )
        if errors:
            raise RuntimeError(
                "B1 manifest contract failed: "
                + "; ".join(f"{list(error.absolute_path)}: {error.message}" for error in errors[:5])
            )
        manifest_path = _publish_verified_manifest(
            output_dir=output_dir,
            manifest=manifest,
            allow_dirty=allow_dirty,
        )
        engine.dispose()
        return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, default=REPO_ROOT / "evidence" / "p0" / "p0-4-b1")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20260807)
    parser.add_argument(
        "--suite-python",
        default=sys.executable,
        help="Python environment containing contracts and eval-harness test dependencies",
    )
    parser.add_argument(
        "--embedded-suites",
        action="store_true",
        help="run schema/probe checks in-process; default CLI runs the focused pytest contract/replay suites",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="test-only: permit evidence generation while the repository has local changes",
    )
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    if not Path(args.suite_python).is_file():
        parser.error(f"--suite-python is unavailable: {args.suite_python}")
    suffix = uuid.uuid4().hex[:16]
    output_dir = (args.out_dir or (args.evidence_root / f"b1run_{suffix}")).resolve()
    if output_dir.exists():
        raise SystemExit(f"refusing to overwrite existing evidence directory: {output_dir}")
    manifest = run_replay(
        output_dir=output_dir,
        repetitions=args.repetitions,
        seed=args.seed,
        external_suites=not args.embedded_suites,
        allow_dirty=args.allow_dirty,
        suite_python=args.suite_python,
    )
    print(json.dumps({"status": "passed", "manifest": str(manifest)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
