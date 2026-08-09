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
        "policy_profile": "live",
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


class _QA:
    def __init__(self):
        self.gets = []

    def get(self, path, params=None, **_kwargs):
        self.gets.append((path, params))
        if path == "/v2/versionsets":
            return {"items": [{"versionset_id": "vs_active", "status": "active"}]}
        return {
            "versionset_id": "vs_active",
            "status": "active",
            "revision": 3,
            "digest": "sha256:" + "a" * 64,
            "content": {"prompt": {"version": "v1"}, "kb_manifest": {}, "model": {}},
        }


def test_repairer_versionset_tools_are_read_only_quality_calls(monkeypatch):
    quality = _QA()
    monkeypatch.setattr(release_admin, "_qa", lambda: quality)

    listed = release_admin.versionset_list(status="active", limit=50)
    exact = release_admin.versionset_get("vs_active")

    assert listed["items"][0]["status"] == "active"
    assert exact["content"]["prompt"] == {"version": "v1"}
    assert quality.gets == [
        ("/v2/versionsets", {"limit": 50, "status": "active"}),
        ("/v2/versionsets/vs_active", None),
    ]


def test_workorder_freeze_requires_atomic_authoritative_lease(session, monkeypatch):
    draft = _draft("wo_fencing0001")
    draft.gate_report_ref = "eval://eval_fencing0001"
    draft.gate_report_digest = "sha256:" + "8" * 64
    session.add(draft)
    session.flush()
    monkeypatch.setattr(release_admin, "session_scope", _scope(session))

    class _LeaseCP(_CP):
        def post(self, path, json_body=None, **kwargs):
            self.posts.append((path, json_body or {}))
            workorder = (json_body or {})["workorder"]
            return {
                "workorder_id": workorder["workorder_id"],
                "hash": workorder["hash"],
                "duplicate": False,
            }

    cp = _LeaseCP()
    monkeypatch.setattr(release_admin, "_cp", lambda: cp)

    result = release_admin.workorder_freeze("wo_fencing0001", fencing_token=7)

    assert result["status"] == "FROZEN"
    assert len(cp.posts) == 1
    assert cp.posts[0][0] == "/v1/workorders"
    assert cp.posts[0][1]["worker_id"] == "repairer"
    assert cp.posts[0][1]["fencing_token"] == 7
    assert cp.posts[0][1]["workorder"]["case_id"] == "case_gate"


@pytest.mark.parametrize(
    "receipt",
    [
        {"duplicate": False},
        {
            "workorder_id": "wo_swapped0001",
            "hash": "0" * 64,
            "duplicate": False,
        },
        {
            "workorder_id": "wo_fencing0003",
            "hash": "0" * 64,
            "duplicate": False,
        },
        {
            "workorder_id": "wo_fencing0003",
            "hash": "unused",
            "duplicate": "false",
        },
    ],
)
def test_workorder_freeze_rejects_non_authoritative_receipt(
    session, monkeypatch, receipt
):
    draft = _draft("wo_fencing0003")
    draft.gate_report_ref = "eval://eval_fencing0003"
    draft.gate_report_digest = "sha256:" + "8" * 64
    session.add(draft)
    session.flush()
    monkeypatch.setattr(release_admin, "session_scope", _scope(session))

    class _BadReceiptCP(_CP):
        def post(self, path, json_body=None, **kwargs):
            self.posts.append((path, json_body or {}))
            if receipt.get("hash") == "unused":
                return {**receipt, "hash": (json_body or {})["workorder"]["hash"]}
            return receipt

    cp = _BadReceiptCP()
    monkeypatch.setattr(release_admin, "_cp", lambda: cp)

    with pytest.raises(release_admin.McpError) as exc:
        release_admin.workorder_freeze("wo_fencing0003", fencing_token=7)

    assert exc.value.error_code == "DEPENDENCY_UNAVAILABLE"
    assert draft.status == "FREEZE_PENDING"
    assert draft.hash is not None
    assert draft.frozen_payload["hash"] == draft.hash


def test_workorder_freeze_stale_lease_never_registers(session, monkeypatch):
    draft = _draft("wo_fencing0002")
    draft.gate_report_ref = "eval://eval_fencing0002"
    draft.gate_report_digest = "sha256:" + "8" * 64
    session.add(draft)
    session.flush()
    monkeypatch.setattr(release_admin, "session_scope", _scope(session))

    class _StaleCP(_CP):
        def post(self, path, json_body=None, **kwargs):
            self.posts.append((path, json_body or {}))
            raise release_admin.McpError("LEASE_LOST", "stale token")

    cp = _StaleCP()
    monkeypatch.setattr(release_admin, "_cp", lambda: cp)

    with pytest.raises(release_admin.McpError) as exc:
        release_admin.workorder_freeze("wo_fencing0002", fencing_token=3)

    assert exc.value.error_code == "LEASE_LOST"
    assert [path for path, _ in cp.posts] == ["/v1/workorders"]
    assert draft.status == "FREEZE_PENDING"


def test_workorder_freeze_lost_response_retries_exact_immutable_payload(session, monkeypatch):
    draft = _draft("wo_freezeretry01")
    draft.gate_report_ref = "eval://eval_freezeretry01"
    draft.gate_report_digest = "sha256:" + "8" * 64
    session.add(draft)
    session.flush()
    monkeypatch.setattr(release_admin, "session_scope", _scope(session))

    class _LostResponseCP(_CP):
        def post(self, path, json_body=None, **kwargs):
            body = json_body or {}
            self.posts.append((path, body))
            if len(self.posts) == 1:
                raise RuntimeError("controller committed but response was lost")
            workorder = body["workorder"]
            return {
                "workorder_id": workorder["workorder_id"],
                "hash": workorder["hash"],
                "duplicate": True,
            }

    cp = _LostResponseCP()
    monkeypatch.setattr(release_admin, "_cp", lambda: cp)

    with pytest.raises(release_admin.McpError) as exc:
        release_admin.workorder_freeze("wo_freezeretry01", fencing_token=7)
    assert exc.value.error_code == "DEPENDENCY_UNAVAILABLE"
    first_payload = cp.posts[0][1]["workorder"]
    assert draft.status == "FREEZE_PENDING"
    assert draft.frozen_payload == first_payload
    assert draft.hash == first_payload["hash"]

    retried = release_admin.workorder_freeze("wo_freezeretry01", fencing_token=8)

    assert retried["status"] == "FROZEN"
    assert retried["duplicate"] is True
    assert cp.posts[1][1]["workorder"] == first_payload
    assert cp.posts[1][1]["workorder"]["nonce"] == first_payload["nonce"]
    assert cp.posts[1][1]["workorder"]["created_at"] == first_payload["created_at"]
    assert cp.posts[1][1]["workorder"]["expiry"] == first_payload["expiry"]


def test_evaluator_timeout_is_persisted_as_error(session, monkeypatch, tmp_path):
    workorder_id = "wo_timeout0001"
    session.add(_draft(workorder_id))
    session.flush()
    monkeypatch.setattr(eval_runner, "session_scope", _scope(session))
    cp = _CP()
    monkeypatch.setattr(eval_runner, "_cp", lambda: cp)
    monkeypatch.setattr(eval_runner, "_gate_cp", lambda: cp)

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
    gate_post = next(body for path, body in cp.posts if path == "/v1/gate-reports")
    assert gate_post["report"]["overall_status"] == "error"
    assert timeout_quality.evaluated and timeout_quality.evaluated[0][0] == "vs_gate_target"
    assert gate_post["report"]["deterministic_tests"]["status"] == "error"


def test_gate_run_rejects_caller_selected_suite_digest(session, monkeypatch):
    workorder_id = "wo_suitedigest1"
    session.add(_draft(workorder_id))
    session.flush()
    monkeypatch.setattr(eval_runner, "session_scope", _scope(session))
    with pytest.raises(eval_runner.McpError) as exc:
        eval_runner.gate_run(workorder_id, "sha256:" + "0" * 64)
    assert "repository-owned gate suite" in exc.value.message


def test_post_canary_gate_uses_release_context_and_attaches_report(session, monkeypatch):
    monkeypatch.setattr(eval_runner, "session_scope", _scope(session))
    context = {
        "release_id": "rel_verify0001",
        "workorder_id": "wo_final000001",
        "target_versionset_id": "vs_canary000001",
        "target_versionset_digest": "sha256:" + "b" * 64,
        "target_revision": 3,
        "canary_observation": {"complete": True},
    }

    class _VerificationCP:
        def __init__(self):
            self.posts = []
            self.gets = []

        def get(self, path, **_kwargs):
            self.gets.append(path)
            return context

        def post(self, path, json_body=None, **_kwargs):
            self.posts.append((path, json_body or {}))
            return {"state": "VERIFYING", "verification": "passed"}

    cp = _VerificationCP()
    monkeypatch.setattr(eval_runner, "_cp", lambda: cp)
    captured = {}

    def _run(**kwargs):
        captured.update(kwargs)
        return {
            "eval_id": "eval_postcanary01",
            "status": "completed",
            "verdict": "passed",
            "report_hash": "a" * 64,
            "candidate_digest": "sha256:" + "c" * 64,
        }

    monkeypatch.setattr(eval_runner, "_run_and_register_gate", _run)
    result = eval_runner.gate_run_verification("rel_verify0001")

    assert cp.gets == ["/v1/releases/rel_verify0001/verification-context"]
    assert captured["workorder_id"] == "wo_final000001"
    assert captured["context"] is context
    assert cp.posts == [
        (
            "/v1/releases/rel_verify0001/verification",
            {"eval_id": "eval_postcanary01", "report_hash": "a" * 64},
        )
    ]
    assert result["verification_receipt"]["state"] == "VERIFYING"


def test_candidate_create_submits_digest_only_to_release_controller(session, monkeypatch):
    monkeypatch.setattr(release_admin, "session_scope", _scope(session))
    monkeypatch.setattr(
        release_admin,
        "_settings",
        lambda: release_admin.Settings(mcp_worker_id="repairer"),
    )
    cp = _CP()
    monkeypatch.setattr(release_admin, "_cp", lambda: cp)
    content = {
        "prompt": {
            "prompt_id": "prompts/system.md",
            "version": "v2",
            "digest": "sha256:" + "1" * 64,
        },
        "kb_manifest": {
            "entries": [],
            "manifest_digest": "sha256:" + "2" * 64,
        },
        "model": {
            "provider": "stepfun",
            "model": "step-3.7-flash",
            "params": {"temperature": 0},
            "digest": "sha256:" + "3" * 64,
        },
    }
    result = release_admin.candidate_create(
        case_id="case_candidate01",
        worker_id="repairer",
        fencing_token=42,
        channel="prompt",
        attribution_report_digest="sha256:" + "4" * 64,
        base_versionset_id="vs_base00000001",
        base_versionset_digest="sha256:" + "5" * 64,
        base_revision=1,
        target_prompt_digest="sha256:" + "1" * 64,
        content=content,
        idempotency_key="candidate-0001",
    )
    assert cp.posts[0][0] == "/v1/release-candidates"
    body = cp.posts[0][1]
    proposal = {
        key: value
        for key, value in body.items()
        if key not in {"proposal_digest", "idempotency_key", "worker_id", "fencing_token"}
    }
    assert body["proposal_digest"] == release_admin.params_digest(proposal)
    assert result["candidate_digest"] == body.get("candidate_digest")


def test_gate_submit_rejects_cross_workorder_report(session, monkeypatch):
    monkeypatch.setattr(release_admin, "session_scope", _scope(session))
    monkeypatch.setattr(
        release_admin,
        "_settings",
        lambda: release_admin.Settings(mcp_worker_id="gatekeeper"),
    )
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
    monkeypatch.setattr(
        release_admin,
        "_settings",
        lambda: release_admin.Settings(mcp_worker_id="gatekeeper"),
    )
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
