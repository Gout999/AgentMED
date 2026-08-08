"""P0-1 MCP gate execution persistence and cross-WorkOrder substitution defenses."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
import requests

from common.tables import EvalRun, WorkOrderDraft
from eval_harness.digests import sha256_digest
from eval_harness.gate import SuiteResult
from servers import eval_runner, release_admin


def _scope(session):
    @contextmanager
    def scope(*_args, **_kwargs):
        yield session
        session.flush()

    return scope


def _draft(workorder_id: str) -> WorkOrderDraft:
    return WorkOrderDraft(
        workorder_id=workorder_id,
        case_id="case_gate",
        channel="prompt",
        status="DRAFT",
        created_by="repairer",
        draft_payload={
            "target_versionset_id": "vs_gate_target",
            "target_versionset_digest": "sha256:" + "b" * 64,
            "target_revision": 7,
            "input_versions": {
                "prompt_digest": "sha256:" + "c" * 64,
                "kb_manifest_digest": "sha256:" + "d" * 64,
                "model_digest": "sha256:" + "e" * 64,
            },
            "diff": {
                "format": "unified_diff",
                "content_ref": "file:///tmp/fix.diff",
                "digest": "sha256:" + "f" * 64,
            },
        },
    )


def _passed_report(eval_id: str) -> dict:
    return {
        "schema_version": "0.1.0",
        "report_id": f"gate_{eval_id.removeprefix('eval_')}",
        "eval_id": eval_id,
        "subject": {
            "target_versionset_digest": "sha256:" + "b" * 64,
            "regression_suite_digest": "sha256:" + "a" * 64,
            "probe_set_digest": "sha256:" + "9" * 64,
        },
        "rule_track": {"status": "passed", "checks": [{"check_id": "rule", "status": "passed"}]},
        "judge_track": {
            "status": "passed",
            "judge_model_digest": "sha256:" + "7" * 64,
            "athlete_model_digest": "sha256:" + "8" * 64,
            "pass_threshold": 0.8,
            "scores": [{"probe_id": "cs-001", "score": 0.95, "pass": True}],
        },
        "deterministic_tests": {
            "status": "passed",
            "suites": [
                {"suite": "contract", "kind": "contract", "status": "passed", "n_passed": 3, "n_failed": 0},
                {"suite": "replay", "kind": "replay", "status": "passed", "n_passed": 3, "n_failed": 0},
            ],
        },
        "live_provider_e2e": {
            "status": "passed",
            "provider": "test",
            "suites": [{"suite": "live", "status": "passed", "n_passed": 3, "n_failed": 0}],
        },
        "overall_status": "passed",
        "artifact_refs": [{"uri": "file:///tmp/gate.json", "digest": "sha256:" + "6" * 64}],
        "created_at": "2026-08-08T00:00:00+00:00",
    }


class _CP:
    def __init__(self, gate: dict | None = None):
        self.gate = gate or {}
        self.posts = []

    def post(self, path, json_body=None, **_kwargs):
        self.posts.append((path, json_body or {}))
        return {"candidate_digest": (json_body or {}).get("candidate_digest")}

    def get(self, path, **_kwargs):
        return self.gate


def test_evaluator_timeout_is_persisted_as_error(session, monkeypatch, tmp_path):
    workorder_id = "wo_timeout0001"
    session.add(_draft(workorder_id))
    session.flush()
    monkeypatch.setattr(eval_runner, "session_scope", _scope(session))
    cp = _CP()
    monkeypatch.setattr(eval_runner, "_cp", lambda: cp)

    settings = eval_runner.Settings(
        gate_evaluation_timeout_seconds=2,
        gate_evidence_dir=str(tmp_path),
    )
    monkeypatch.setattr(eval_runner, "_settings", lambda: settings)

    class _Suites:
        def __init__(self, **_kwargs):
            pass

        def run(self, *, suite, kind, **_kwargs):
            digest = "sha256:" + ("1" if kind == "contract" else "2") * 64
            uri = (tmp_path / f"{kind}.json").as_uri()
            return SimpleNamespace(
                result=SuiteResult(
                    suite=suite,
                    kind=kind,
                    status="passed",
                    n_passed=1,
                    n_failed=0,
                    report_ref=uri,
                    report_digest=digest,
                ),
                artifact_ref={"uri": uri, "digest": digest},
            )

    class _TimeoutQuality:
        evaluated = []

        def get_versionset(self, versionset_id, **_kwargs):
            return {
                "versionset_id": versionset_id,
                "revision": 7,
                "digest": "sha256:" + "b" * 64,
                "content": {},
            }

        def evaluate_versionset(self, versionset_id, message, **_kwargs):
            self.evaluated.append((versionset_id, message))
            raise requests.Timeout("candidate provider deadline exceeded")

    timeout_quality = _TimeoutQuality()
    monkeypatch.setattr(eval_runner, "CommandSuiteRunner", _Suites)
    monkeypatch.setattr(eval_runner, "QualityAPIClient", lambda _settings: timeout_quality)
    result = eval_runner.gate_run(workorder_id)
    stored = session.get(EvalRun, result["eval_id"])
    assert result["status"] == "completed"
    assert result["verdict"] == "error"
    assert stored is not None and stored.report["overall_status"] == "error"
    assert cp.posts[0][0] == "/v1/gate-reports"
    assert cp.posts[0][1]["report"]["overall_status"] == "error"
    assert timeout_quality.evaluated and timeout_quality.evaluated[0][0] == "vs_gate_target"
    assert cp.posts[0][1]["report"]["deterministic_tests"]["status"] == "error"


def test_gate_run_rejects_caller_selected_suite_digest(session, monkeypatch):
    workorder_id = "wo_suitedigest1"
    session.add(_draft(workorder_id))
    session.flush()
    monkeypatch.setattr(eval_runner, "session_scope", _scope(session))
    with pytest.raises(eval_runner.McpError) as exc:
        eval_runner.gate_run(workorder_id, "sha256:" + "0" * 64)
    assert "repository-owned gate suite" in exc.value.message


def test_gate_submit_rejects_cross_workorder_report(session, monkeypatch):
    monkeypatch.setattr(release_admin, "session_scope", _scope(session))
    draft = _draft("wo_target0001")
    session.add(draft)
    report = _passed_report("eval_cross0001")
    report_hash = release_admin._gate_report_hash(report)
    session.add(
        EvalRun(
            eval_id=report["eval_id"],
            workorder_id="wo_other00001",
            suite_digest="sha256:" + "a" * 64,
            target_versionset_id="vs_gate_target",
            target_revision=7,
            dataset_id="regression",
            dataset_version="1",
            dataset_digest="sha256:" + "9" * 64,
            evidence_digest=sha256_digest(report["artifact_refs"]),
            candidate_digest="sha256:" + "5" * 64,
            status="completed",
            report=report,
            report_hash=report_hash,
        )
    )
    session.flush()
    with pytest.raises(release_admin.McpError) as exc:
        release_admin.gate_submit(draft.workorder_id, report["eval_id"], report_hash)
    assert "different workorder" in exc.value.message


def test_gate_submit_requires_every_track_passed(session, monkeypatch):
    monkeypatch.setattr(release_admin, "session_scope", _scope(session))
    draft = _draft("wo_judgefail01")
    session.add(draft)
    report = _passed_report("eval_judgefail01")
    report["judge_track"]["status"] = "failed"
    report["judge_track"]["scores"][0] = {"probe_id": "cs-001", "score": 0.1, "pass": False}
    report["overall_status"] = "failed"
    report_hash = release_admin._gate_report_hash(report)
    session.add(
        EvalRun(
            eval_id=report["eval_id"],
            workorder_id=draft.workorder_id,
            suite_digest="sha256:" + "a" * 64,
            target_versionset_id="vs_gate_target",
            target_revision=7,
            dataset_id="regression",
            dataset_version="1",
            dataset_digest="sha256:" + "9" * 64,
            evidence_digest=sha256_digest(report["artifact_refs"]),
            candidate_digest="sha256:" + "5" * 64,
            status="completed",
            report=report,
            report_hash=report_hash,
        )
    )
    session.flush()
    with pytest.raises(release_admin.McpError) as exc:
        release_admin.gate_submit(draft.workorder_id, report["eval_id"], report_hash)
    assert exc.value.error_code == release_admin.GATE_FAILED
