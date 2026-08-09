"""Complete B1 contract/replay integration through production services."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlparse

import pytest

from app.services.attribution import AttributionValidationError


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_b1_replay import run_replay  # noqa: E402
from validate_b1_run import B1ValidationError, validate_b1_run  # noqa: E402


def _canonical_digest(value):
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def test_b1_replay_closes_case_and_emits_bound_evidence(tmp_path):
    out = tmp_path / "b1-contract-replay"
    # Exercise the production replay entry point in-process. On macOS
    # FileProvider worktrees, spawning a second interpreter after the earlier
    # integration cases can block indefinitely while hydrating source files.
    # The public CLI is still exercised independently by ``make demo-b1-replay``.
    manifest_path = run_replay(
        output_dir=out,
        repetitions=3,
        seed=20260807,
        external_suites=False,
        allow_dirty=True,
        suite_python=sys.executable,
    )
    assert manifest_path == out / "b1-run-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["mode"] == "isolated-replay"
    assert manifest["status"] == "passed"
    assert manifest["outcomes"]["deduplicated"] is True
    assert manifest["outcomes"]["attribution"] == {
        "decision": "ATTRIBUTED",
        "fault_layer": "prompt",
    }
    assert manifest["outcomes"]["release"] == "promoted"
    assert manifest["outcomes"]["notification"] == {
        "status": "sent",
        "provider": "feishu-mock",
    }
    assert manifest["outcomes"]["case"] == "CLOSED"
    assert manifest["outcomes"]["trust"]["samples_added"] == 1
    assert manifest["outcomes"]["trust"]["promotion_decision"] == "denied"
    assert manifest["outcomes"]["live_provider"] == "blocked"

    domain = json.loads((out / "domain-events.json").read_text(encoding="utf-8"))
    assert set(domain["required_catalog"]) <= set(domain["observed"])
    trust = json.loads((out / "trust-decision.json").read_text(encoding="utf-8"))
    assert (trust["entry"]["epoch_successes"], trust["entry"]["epoch_trials"]) == (1, 1)
    assert trust["entry"]["promotion"]["eligible"] is False
    assert trust["three_of_three_reference"]["wilson_two_sided_95_lower"] == 0.4385
    assert trust["three_of_three_reference"]["decision"] == "denied"

    for ref in manifest["artifacts"].values():
        assert ref["digest"].startswith("sha256:")
    assert manifest["test_reports"][0]["status"] == "passed"
    assert manifest["test_reports"][1]["status"] == "passed"
    assert manifest["test_reports"][2]["status"] == "blocked"

    verified = validate_b1_run(out / "b1-run-manifest.json", allow_dirty=True)
    assert verified["status"] == "verified"

    # Semantic fraud must still fail after the attacker recomputes the direct
    # file digest and updates the manifest reference.
    manifest_path = out / "b1-run-manifest.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    contract_path = out / "contract-suite.json"
    original_contract = contract_path.read_text(encoding="utf-8")
    contract = json.loads(original_contract)
    contract["errors"] = ["forged semantic failure"]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")
    manifest = json.loads(original_manifest)
    contract_digest = "sha256:" + hashlib.sha256(contract_path.read_bytes()).hexdigest()
    next(item for item in manifest["test_reports"] if item["kind"] == "contract")["report_ref"][
        "digest"
    ] = contract_digest
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(B1ValidationError, match="status does not match its content"):
        validate_b1_run(manifest_path, allow_dirty=True)
    contract_path.write_text(original_contract, encoding="utf-8")
    manifest_path.write_text(original_manifest, encoding="utf-8")

    approval_path = out / "approval-grants.json"
    original_approvals = approval_path.read_text(encoding="utf-8")
    approvals = json.loads(original_approvals)
    promote_grant = next(
        grant
        for grant in approvals["grants"]
        if (grant.get("authorization") or {}).get("action") == "promote"
    )
    promote_grant["authorization"]["target_revision"] = 999
    approval_path.write_text(json.dumps(approvals), encoding="utf-8")
    manifest = json.loads(original_manifest)
    manifest["artifacts"]["approval_grants"]["digest"] = (
        "sha256:" + hashlib.sha256(approval_path.read_bytes()).hexdigest()
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(B1ValidationError, match="target revision"):
        validate_b1_run(manifest_path, allow_dirty=True)
    approval_path.write_text(original_approvals, encoding="utf-8")
    manifest_path.write_text(original_manifest, encoding="utf-8")

    # Recomputing every outer digest must not let a changed raw answer retain a
    # stale recovered=true claim. The repository-owned probe oracle is the
    # authority, not the trial summary.
    bundle_path = out / "evidence-bundle.json"
    attribution_path = out / "attribution-report.json"
    original_bundle = bundle_path.read_text(encoding="utf-8")
    original_attribution = attribution_path.read_text(encoding="utf-8")
    bundle = json.loads(original_bundle)
    trial = next(
        item
        for cell in bundle["cells"].values()
        for item in cell["results"]
        if item.get("recovered") is True
    )
    parsed_output = urlparse(trial["output_ref"])
    raw_path = Path(unquote(parsed_output.path))
    original_raw = raw_path.read_text(encoding="utf-8")
    raw = json.loads(original_raw)
    raw["answer"] = "No return policy, escalation, or resolution is available."
    raw_path.write_text(json.dumps(raw), encoding="utf-8")
    trial["output_digest"] = _canonical_digest(raw)
    bundle_path.write_text(json.dumps(bundle), encoding="utf-8")
    attribution = json.loads(original_attribution)
    attribution["evidence_bundle_ref"]["digest"] = _canonical_digest(bundle)
    attribution_path.write_text(json.dumps(attribution), encoding="utf-8")
    manifest = json.loads(original_manifest)
    manifest["artifacts"]["evidence_bundle"]["digest"] = (
        "sha256:" + hashlib.sha256(bundle_path.read_bytes()).hexdigest()
    )
    manifest["artifacts"]["attribution_report"]["digest"] = (
        "sha256:" + hashlib.sha256(attribution_path.read_bytes()).hexdigest()
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(AttributionValidationError, match="recovery differs"):
        validate_b1_run(manifest_path, allow_dirty=True)
    raw_path.write_text(original_raw, encoding="utf-8")
    bundle_path.write_text(original_bundle, encoding="utf-8")
    attribution_path.write_text(original_attribution, encoding="utf-8")
    manifest_path.write_text(original_manifest, encoding="utf-8")

    canary_path = out / "canary-metrics.json"
    canary = json.loads(canary_path.read_text(encoding="utf-8"))
    canary["error_count"] = 1
    canary_path.write_text(json.dumps(canary), encoding="utf-8")
    with pytest.raises(B1ValidationError, match="digest mismatch"):
        validate_b1_run(out / "b1-run-manifest.json", allow_dirty=True)


def test_b1_live_preflight_reports_provider_and_closure_blockers(tmp_path):
    env = dict(os.environ)
    for name in (
        "STEPFUN_API_KEY",
        "JUDGE_MODEL",
        "CASELOOP_B1_BAD_VERSIONSET_ID",
        "CASELOOP_B1_GOOD_VERSIONSET_ID",
        "CASELOOP_QUALITY_API_BASE_URL",
        "CASELOOP_READ_TOKEN",
        "CONTROL_PLANE_BASE_URL",
        "CONTROL_PLANE_TOKEN",
        "APPROVAL_AUTHORITY_TOKEN",
        "GATE_AUTHORITY_TOKEN",
        "CASELOOP_B1_APPROVAL_COMMAND",
        "CASELOOP_B1_AGENT_TRACE_COMMAND",
        "CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY",
        "CASELOOP_B1_FEISHU_MESSAGE_COMMAND",
    ):
        env.pop(name, None)
    completed = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "run_b1_live.py"),
            "--eval-python",
            sys.executable,
            "--evidence-root",
            str(tmp_path / "live"),
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 2, completed.stdout
    reports = list((tmp_path / "live").glob("*/live-provider-report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["provider_calls_attempted"] is False
    assert report["replay_fallback_used"] is False
    assert "STEPFUN_API_KEY" in report["blockers"]
    assert any(
        "CASELOOP_B1_FEISHU_MESSAGE_COMMAND" in blocker
        and "post-injection" in blocker
        for blocker in report["blockers"]
    )
    assert any("human ApprovalGrants" in blocker for blocker in report["blockers"])
    assert any("AgentTeams v1.2.1" in blocker for blocker in report["blockers"])
