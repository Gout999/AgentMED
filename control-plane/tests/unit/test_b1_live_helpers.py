"""Fail-closed tests for the live B1 runner's process/evidence boundaries."""
from __future__ import annotations

import base64
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from conftest import make_gate_report, make_workorder
from app.utils.jcs import canonical_json_digest, workorder_hash
from app.services.b1_fixture import load_b1_complaint_fixture


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_b1_live import (  # noqa: E402
    LiveRunError,
    _PROVIDER_ENV,
    _agent_trace_from_command,
    _agent_workorder_from_command,
    _approval_from_command,
    _child_env,
    _compensate_incomplete_b1,
    _decode_inline_json,
    _decide_live_preflight,
    _feishu_message_created_at,
    _feishu_message_id_from_command,
    _inline_gate_artifacts,
    _inline_live_probe_outputs,
    _preflight,
    _publish_verified_manifest,
    _run_child_command,
    _verify_agentteams_receipt,
)
from validate_b1_run import (  # noqa: E402
    B1ValidationError,
    _report_semantics,
    _require_fixed_agent_worker_roles,
    _require_official_provider_origins,
    _require_portable_replay_uris,
    _require_unique_agent_taskflow_ids,
    _validate_live_gate,
    _validate_live_inbound_notification_binding,
    _validate_persisted_live_gate,
    load_probe_set,
)
from run_b1_replay import (  # noqa: E402
    _publish_verified_manifest as _publish_verified_replay_manifest,
    _require_portable_output_dir,
    _repo_uri,
)
from agentteams_attestation import canonical_receipt_bytes  # noqa: E402


_AGENTTEAMS_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
_AGENTTEAMS_PUBLIC_KEY = base64.b64encode(
    _AGENTTEAMS_PRIVATE_KEY.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
).decode("ascii")


@pytest.fixture(autouse=True)
def _agentteams_attestation_key(monkeypatch):
    monkeypatch.setenv("CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY", _AGENTTEAMS_PUBLIC_KEY)


def _attest(receipt: dict) -> dict:
    signed = dict(receipt)
    signature = _AGENTTEAMS_PRIVATE_KEY.sign(canonical_receipt_bytes(signed))
    signed["attestation"] = {
        "algorithm": "ed25519",
        "key_id": "sha256:"
        + hashlib.sha256(base64.b64decode(_AGENTTEAMS_PUBLIC_KEY)).hexdigest(),
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    return signed


def _digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def test_final_replay_evidence_requires_repository_portability(tmp_path):
    with pytest.raises(RuntimeError, match="repo:/// URIs"):
        _require_portable_output_dir(tmp_path / "run", allow_dirty=False)

    _require_portable_output_dir(REPO_ROOT / "evidence" / "run", allow_dirty=False)
    _require_portable_output_dir(tmp_path / "test-run", allow_dirty=True)
    assert _repo_uri(REPO_ROOT / "evidence" / "run" / "artifact.json") == (
        "repo:///evidence/run/artifact.json"
    )
    with pytest.raises(B1ValidationError, match="non-portable file URI"):
        _require_portable_replay_uris(
            {"artifact": {"uri": "file:///Users/someone/evidence.json"}},
            label="manifest",
        )


def test_child_environment_never_leaks_control_or_write_authority(monkeypatch):
    values = {
        "STEPFUN_API_KEY": "athlete-secret",
        "CASELOOP_READ_TOKEN": "quality-read",
        "CONTROL_PLANE_TOKEN": "control-secret",
        "APPROVAL_AUTHORITY_TOKEN": "approval-secret",
        "GATE_AUTHORITY_TOKEN": "gate-secret",
        "CASELOOP_QUALITY_API_TOKEN": "quality-write-secret",
        "FEISHU_APP_SECRET": "feishu-secret",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    provider = _child_env(_PROVIDER_ENV)
    empty = _child_env()

    assert provider["STEPFUN_API_KEY"] == "athlete-secret"
    assert provider["CASELOOP_READ_TOKEN"] == "quality-read"
    for forbidden in (
        "CONTROL_PLANE_TOKEN",
        "APPROVAL_AUTHORITY_TOKEN",
        "GATE_AUTHORITY_TOKEN",
        "CASELOOP_QUALITY_API_TOKEN",
        "FEISHU_APP_SECRET",
    ):
        assert forbidden not in provider
    assert "STEPFUN_API_KEY" not in empty
    assert "CASELOOP_READ_TOKEN" not in empty


def test_live_preflight_checks_clean_tree_before_creating_evidence_dir(
    monkeypatch, tmp_path
):
    output_dir = tmp_path / "evidence" / "b1run_live_exact"
    monkeypatch.setattr("run_b1_live._preflight", lambda _args: ({"ok": "1"}, []))

    def clean_status(*args):
        assert args == ("status", "--porcelain")
        assert not output_dir.exists()
        return ""

    monkeypatch.setattr("run_b1_live._git", clean_status)
    values, blockers, working_tree = _decide_live_preflight(
        argparse.Namespace(), output_dir
    )

    assert values == {"ok": "1"}
    assert blockers == []
    assert working_tree == ""
    assert not output_dir.exists()


def test_live_preflight_rejects_nonofficial_stepfun_origin(monkeypatch):
    monkeypatch.setenv("STEPFUN_BASE_URL", "http://127.0.0.1:9999/v1")

    values, blockers = _preflight(argparse.Namespace(eval_python=sys.executable))

    assert values["STEPFUN_BASE_URL"] == "http://127.0.0.1:9999/v1"
    assert any("official live endpoint" in blocker for blocker in blockers)


def test_feishu_message_command_runs_after_injection_without_authority_env(
    monkeypatch, tmp_path
):
    fixture = load_b1_complaint_fixture()
    monkeypatch.setenv("CONTROL_PLANE_TOKEN", "must-not-leak")
    observed: dict = {}

    def run(command, *, input_text, env, **kwargs):
        observed.update(json.loads(input_text))
        assert command == ["feishu-acquire"]
        assert "CONTROL_PLANE_TOKEN" not in env
        assert kwargs["timeout_seconds"] == 600
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "schema_version": "0.1.0",
                    "provider": "feishu",
                    "message_id": "om_post_injection_001",
                }
            ),
            stderr="",
        )

    monkeypatch.setattr("run_b1_live._run_child_command", run)
    message_id, evidence = _feishu_message_id_from_command(
        "feishu-acquire",
        injection_operation_id="b1-live-inject-exact",
        injection_receipt={"injected_at": "2026-08-08T00:00:00+00:00"},
        fixture=fixture,
        evidence_dir=tmp_path,
    )

    assert message_id == "om_post_injection_001"
    assert observed["not_before"] == "2026-08-08T00:00:00+00:00"
    assert observed["fixture_text_digest"] == fixture.text_digest
    assert evidence["receipt"]["message_id"] == message_id
    assert (tmp_path / "feishu-message-acquisition.json").is_file()


def test_child_command_heartbeats_and_kills_on_lease_loss():
    beats: list[int] = []
    completed = _run_child_command(
        [
            sys.executable,
            "-c",
            "import sys,time; sys.stdin.read(); time.sleep(0.12); print('done')",
        ],
        input_text="request",
        env=_child_env(),
        heartbeat=lambda: beats.append(len(beats) + 1),
        timeout_seconds=2,
        poll_seconds=0.03,
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "done"
    assert len(beats) >= 2

    def lose_lease() -> None:
        raise LiveRunError("lease lost")

    with pytest.raises(LiveRunError, match="lease lost"):
        _run_child_command(
            [sys.executable, "-c", "import sys,time; sys.stdin.read(); time.sleep(2)"],
            input_text="request",
            env=_child_env(),
            heartbeat=lose_lease,
            timeout_seconds=2,
            poll_seconds=0.03,
        )


def test_live_approval_command_returns_only_id_and_runner_reads_persisted_grant(
    monkeypatch, tmp_path
):
    workorder = make_workorder(
        workorder_id="wo_independentapproval1",
        nonce="00000000-0000-0000-0000-000000000903",
        case_id="case_independentapproval1",
    )
    persisted = {
        "schema_version": "0.1.0",
        "approval_id": "appr_independentapproval1",
        "workorder_id": workorder["workorder_id"],
        "workorder_hash": workorder["hash"],
        "nonce": workorder["nonce"],
        "expiry": workorder["expiry"],
        "approver": {"type": "human", "identity": "human:test"},
        "decision": "approved",
        "decided_at": workorder["created_at"],
        "nonce_consumed": False,
        "authorization": None,
    }

    class ApprovalAPI:
        def get(self, path: str):
            assert path == "/v1/approvals/appr_independentapproval1"
            return dict(persisted)

    monkeypatch.setattr(
        "run_b1_live.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["independent-approval"],
            returncode=0,
            stdout='{"approval_id":"appr_independentapproval1"}',
            stderr="",
        ),
    )

    grant = _approval_from_command(
        "independent-approval",
        api=ApprovalAPI(),
        phase="initial",
        workorder=workorder,
        evidence_dir=tmp_path,
    )

    assert grant == persisted


def test_live_agentteams_trace_binds_fixed_pool_taskflow_matrix_and_skill(
    monkeypatch, tmp_path
):
    skill_path = REPO_ROOT / "agents" / "skills" / "caseloop-b1-loop" / "SKILL.md"
    skill = {"name": "caseloop-b1-loop", "digest": _digest_bytes(skill_path.read_bytes())}
    roles = [
        "quality-officer",
        "collector",
        "attributionist",
        "repairer",
        "gatekeeper",
        "case-officer",
    ]
    sources = {role: [f"evt_{role}"] for role in roles}
    observed_envs = []
    repairer_product_path = tmp_path / "repairer-workorder-product.json"
    repairer_product_path.write_text(
        json.dumps({"placeholder": True}), encoding="utf-8"
    )
    repairer_product_ref = {
        "uri": repairer_product_path.resolve().as_uri(),
        "digest": _digest_bytes(repairer_product_path.read_bytes()),
    }
    phase_receipts = {}
    for role in roles:
        if role == "repairer":
            product_ref = repairer_product_ref
            phase = "workorder"
        else:
            product_path = tmp_path / f"product-{role}.json"
            product_path.write_text(json.dumps({"role": role}), encoding="utf-8")
            product_ref = {
                "uri": product_path.resolve().as_uri(),
                "digest": _digest_bytes(product_path.read_bytes()),
            }
            phase = f"phase-{role}"
        phase_receipts[role] = [
            _attest({
                "schema_version": "0.1.0",
                "phase": phase,
                "platform": "AgentTeams",
                "platform_version": "v1.2.1",
                "team": "caseloop-team",
                "session_id": "session-b1-live",
                "room_id": "!caseloop:matrix.local",
                "role": role,
                "task_id": f"task-{role}",
                "ack_receipt_id": f"ack-{role}",
                "matrix_event_ids": [f"$event-{role}"],
                "skill": skill,
                "artifact_ref": product_ref,
            })
        ]
    duplicate_task_ids = [False]

    def command(*_args, **kwargs):
        observed_envs.append(kwargs["env"])
        request = json.loads(kwargs["input"])
        if request["phase"] == "start":
            receipt = {
                "schema_version": "0.1.0",
                "phase": "start",
                "platform": "AgentTeams",
                "platform_version": "v1.2.1",
                "team": "caseloop-team",
                "session_id": "session-b1-live",
                "room_id": "!caseloop:matrix.local",
                "skill": skill,
                "dispatch_event_id": "$dispatch-b1",
                "workers": roles,
            }
        else:
            assert request["session_id"] == "session-b1-live"
            assert request["expected_sources"] == sources
            assert request["expected_products"] == {
                role: [item["artifact_ref"] for item in phase_receipts[role]]
                for role in roles
            }
            runs = []
            for role in roles:
                identity_role = roles[0] if duplicate_task_ids[0] else role
                handoff_path = tmp_path / f"handoff-{role}.json"
                payload = {"product_refs": request["expected_products"][role]}
                if role == "repairer":
                    payload["workorder_ref"] = repairer_product_ref
                handoff_path.write_text(
                    json.dumps(
                        {
                            "schema_version": "0.1.0",
                            "kind": "task-handoff",
                            "role": role,
                            "task_id": f"task-{identity_role}",
                            "session_id": "session-b1-live",
                            "case_id": "case_live12345678",
                            "source_ids": sources[role],
                            "payload": payload,
                        }
                    ),
                    encoding="utf-8",
                )
                runs.append(
                    {
                        "role": role,
                        "task_id": f"task-{identity_role}",
                        "ack_receipt_id": f"ack-{identity_role}",
                        "submit_receipt_id": f"submit-{identity_role}",
                        "matrix_event_ids": [f"$event-{role}"],
                        "skill": skill,
                        "source_ids": sources[role],
                        "artifact_ref": {
                            "uri": handoff_path.resolve().as_uri(),
                            "digest": _digest_bytes(handoff_path.read_bytes()),
                        },
                    }
                )
            receipt = {
                "schema_version": "0.1.0",
                "phase": "complete",
                "platform": "AgentTeams",
                "platform_version": "v1.2.1",
                "team": "caseloop-team",
                "session_id": "session-b1-live",
                "room_id": "!caseloop:matrix.local",
                "skill": skill,
                "dispatch_event_id": "$dispatch-b1",
                "completion_event_id": "$complete-b1",
                "runs": runs,
            }
        return subprocess.CompletedProcess(
            args=["agentteams-trace"], returncode=0, stdout=json.dumps(_attest(receipt)), stderr=""
        )

    monkeypatch.setenv("CONTROL_PLANE_TOKEN", "must-not-leak")
    monkeypatch.setenv("AGENTTEAMS_ADMIN_TOKEN", "must-not-leak")
    monkeypatch.setattr("run_b1_live.subprocess.run", command)
    start = _agent_trace_from_command(
        "agentteams-trace",
        phase="start",
        context={"run_id": "b1run_live12345678", "case_id": "case_live12345678"},
        evidence_dir=tmp_path,
    )
    complete = _agent_trace_from_command(
        "agentteams-trace",
        phase="complete",
        context={"run_id": "b1run_live12345678", "case_id": "case_live12345678"},
        evidence_dir=tmp_path,
        start_receipt=start,
        expected_sources=sources,
        expected_repairer_workorder_ref=repairer_product_ref,
        expected_phase_receipts=phase_receipts,
    )

    assert complete["completion_event_id"] == "$complete-b1"
    assert len(complete["runs"]) == 6
    assert all("CONTROL_PLANE_TOKEN" not in env for env in observed_envs)
    assert all("AGENTTEAMS_ADMIN_TOKEN" not in env for env in observed_envs)

    duplicate_task_ids[0] = True
    with pytest.raises(LiveRunError, match="reuses a cross-role task_id"):
        _agent_trace_from_command(
            "agentteams-trace",
            phase="complete",
            context={"run_id": "b1run_live12345678", "case_id": "case_live12345678"},
            evidence_dir=tmp_path,
            start_receipt=start,
            expected_sources=sources,
            expected_repairer_workorder_ref=repairer_product_ref,
            expected_phase_receipts=phase_receipts,
        )


def test_live_validator_rejects_cross_role_taskflow_receipt_reuse():
    rows = [
        {
            "task_id": "shared-task",
            "ack_receipt_id": f"ack-{index}",
            "submit_receipt_id": f"submit-{index}",
        }
        for index in range(6)
    ]

    with pytest.raises(B1ValidationError, match="reuses a cross-role task_id"):
        _require_unique_agent_taskflow_ids(rows)


def test_live_validator_rejects_duplicate_fixed_worker_role():
    expected = {
        "quality-officer",
        "collector",
        "attributionist",
        "repairer",
        "gatekeeper",
        "case-officer",
    }
    duplicated = [*sorted(expected), "collector"]

    with pytest.raises(B1ValidationError, match="duplicates or omits"):
        _require_fixed_agent_worker_roles(duplicated, expected)


def test_live_validator_rejects_stub_provider_origin():
    origins = {
        "stepfun": {
            "runner_provider_origin": "https://api.stepfun.com/step_plan/v1",
            "quality_log_origins": ["https://api.stepfun.com/step_plan/v1"],
            "canary_response_origin": "https://api.stepfun.com/step_plan/v1",
            "required_origin": "https://api.stepfun.com/step_plan/v1",
        },
        "feishu": {
            "inbound_provider_origin": "https://open.feishu.cn",
            "notification_provider_origin": "https://open.feishu.cn",
            "required_origin": "https://open.feishu.cn",
        },
    }
    _require_official_provider_origins(origins)
    origins["stepfun"]["quality_log_origins"] = ["http://127.0.0.1:9999/v1"]

    with pytest.raises(B1ValidationError, match="official StepFun and Feishu"):
        _require_official_provider_origins(origins)


def test_agentteams_attestation_fails_closed_for_missing_wrong_or_mutated_receipt(
    monkeypatch,
):
    base = {
        "schema_version": "0.1.0",
        "phase": "start",
        "platform": "AgentTeams",
        "platform_version": "v1.2.1",
        "team": "caseloop-team",
        "session_id": "session-b1-live",
        "room_id": "!caseloop:matrix.local",
        "skill": {"name": "caseloop-b1-loop", "digest": "sha256:" + "a" * 64},
        "dispatch_event_id": "$dispatch-b1",
        "workers": [
            "quality-officer",
            "collector",
            "attributionist",
            "repairer",
            "gatekeeper",
            "case-officer",
        ],
    }

    with pytest.raises(LiveRunError, match="attestation is invalid"):
        _verify_agentteams_receipt(base, phase="start")

    signed = _attest(base)
    mutated = json.loads(json.dumps(signed))
    mutated["workers"][0] = "forged-worker"
    with pytest.raises(LiveRunError, match="signature is invalid"):
        _verify_agentteams_receipt(mutated, phase="start")

    wrong_private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65)))
    wrong_public_key = base64.b64encode(
        wrong_private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    monkeypatch.setenv("CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY", wrong_public_key)
    with pytest.raises(LiveRunError, match="unexpected attestation key"):
        _verify_agentteams_receipt(signed, phase="start")

    monkeypatch.setenv("CASELOOP_B1_AGENT_TRACE_PUBLIC_KEY", _AGENTTEAMS_PUBLIC_KEY)
    malformed = json.loads(json.dumps(signed))
    malformed["attestation"]["signature"] = "not-base64"
    with pytest.raises(LiveRunError, match="not valid base64"):
        _verify_agentteams_receipt(malformed, phase="start")


def test_live_workorder_must_come_from_bound_agentteams_repairer_artifact(
    monkeypatch, tmp_path
):
    skill_path = REPO_ROOT / "agents" / "skills" / "caseloop-b1-loop" / "SKILL.md"
    skill = {"name": "caseloop-b1-loop", "digest": _digest_bytes(skill_path.read_bytes())}
    workorder = make_workorder(
        workorder_id="wo_agentteamsrepair001",
        nonce="00000000-0000-0000-0000-000000000921",
        case_id="case_agentteamsrepair001",
    )
    workorder["created_by"] = "repairer"
    workorder["diff"]["digest"] = _digest_bytes(
        workorder["diff"]["content"].encode("utf-8")
    )
    workorder["hash"] = workorder_hash(workorder)
    product_path = tmp_path / "repairer-workorder.json"
    product_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1.0",
                "kind": "immutable-workorder",
                "role": "repairer",
                "task_id": "task-repairer",
                "session_id": "session-b1-live",
                "case_id": workorder["case_id"],
                "workorder": workorder,
            }
        ),
        encoding="utf-8",
    )
    product_ref = {
        "uri": product_path.resolve().as_uri(),
        "digest": _digest_bytes(product_path.read_bytes()),
    }
    receipt = _attest({
        "schema_version": "0.1.0",
        "phase": "workorder",
        "platform": "AgentTeams",
        "platform_version": "v1.2.1",
        "team": "caseloop-team",
        "session_id": "session-b1-live",
        "room_id": "!caseloop:matrix.local",
        "role": "repairer",
        "task_id": "task-repairer",
        "ack_receipt_id": "ack-repairer",
        "matrix_event_ids": ["$repairer-workorder"],
        "skill": skill,
        "artifact_ref": product_ref,
    })
    monkeypatch.setattr(
        "run_b1_live._run_child_command",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["agentteams-trace"],
            returncode=0,
            stdout=json.dumps(receipt),
            stderr="",
        ),
    )
    expected = {
        key: workorder[key]
        for key in (
            "schema_version",
            "workorder_id",
            "case_id",
            "channel",
            "base_versionset_digest",
            "target_versionset_digest",
            "input_versions",
            "gate_report_ref",
            "created_by",
            "hash_rule",
        )
    }
    returned, returned_receipt = _agent_workorder_from_command(
        "agentteams-trace",
        context={
            "run_id": "b1run_live12345678",
            "case_id": workorder["case_id"],
            "expected_workorder_binding": expected,
        },
        evidence_dir=tmp_path,
        start_receipt={
            "session_id": "session-b1-live",
            "room_id": "!caseloop:matrix.local",
        },
    )

    assert returned == workorder
    assert returned_receipt["artifact_ref"] == product_ref


def test_live_agentteams_trace_rejects_direct_runner_or_missing_role(monkeypatch, tmp_path):
    skill_path = REPO_ROOT / "agents" / "skills" / "caseloop-b1-loop" / "SKILL.md"
    invalid = _attest({
        "schema_version": "0.1.0",
        "phase": "start",
        "platform": "AgentTeams",
        "platform_version": "v1.2.1",
        "team": "caseloop-team",
        "session_id": "session-b1-live",
        "room_id": "!caseloop:matrix.local",
        "skill": {"name": "caseloop-b1-loop", "digest": _digest_bytes(skill_path.read_bytes())},
        "dispatch_event_id": "$dispatch-b1",
        "workers": ["quality-officer"],
    })
    monkeypatch.setattr(
        "run_b1_live.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["agentteams-trace"], returncode=0, stdout=json.dumps(invalid), stderr=""
        ),
    )

    with pytest.raises(LiveRunError, match="fixed six-worker pool"):
        _agent_trace_from_command(
            "agentteams-trace",
            phase="start",
            context={"run_id": "b1run_live12345678"},
            evidence_dir=tmp_path,
        )


class _RecoveryAPI:
    def __init__(
        self,
        *,
        bad_status: str,
        good_status: str,
        active: list[dict] | None = None,
        candidate_status: str | None = None,
    ):
        self.bad = {
            "versionset_id": "vs-bad",
            "digest": "sha256:" + "b" * 64,
            "revision": 2,
            "status": bad_status,
        }
        self.good = {
            "versionset_id": "vs-good",
            "digest": "sha256:" + "a" * 64,
            "revision": 2,
            "status": good_status,
        }
        self.active = active or []
        self.candidate = (
            {
                "versionset_id": "vs-candidate",
                "digest": "sha256:" + "c" * 64,
                "revision": 3,
                "status": candidate_status,
            }
            if candidate_status
            else None
        )
        self.posts: list[tuple[str, dict]] = []

    def get(self, path: str, *, quality: bool = False):
        assert quality is True
        if path == "/v2/versionsets/vs-bad":
            return dict(self.bad)
        if path == "/v2/versionsets/vs-good":
            return dict(self.good)
        if path.startswith("/v2/versionsets?status=active"):
            return {"items": self.active}
        if path == "/v2/versionsets/vs-candidate" and self.candidate is not None:
            return dict(self.candidate)
        raise AssertionError(path)

    def post(self, path: str, body: dict):
        self.posts.append((path, body))
        assert path == "/v1/demo/faults/B1/recover"
        self.bad.update(status="draft", revision=3)
        self.good.update(status="active", revision=3)
        receipt = {
            "fault_id": "B1",
            "restored_versionset_id": "vs-good",
            "restored_versionset_digest": self.good["digest"],
            "restored_revision": self.good["revision"],
            "fault_versionset_id": "vs-bad",
            "fault_versionset_digest": self.bad["digest"],
            "fault_revision": self.bad["revision"],
            "duplicate": False,
        }
        if body.get("quarantine_versionset_id"):
            assert self.candidate is not None
            self.candidate.update(status="rolled_back", revision=4)
            receipt.update(
                {
                    "quarantined_versionset_id": self.candidate["versionset_id"],
                    "quarantined_versionset_digest": self.candidate["digest"],
                    "quarantined_revision": self.candidate["revision"],
                    "quarantined_status": self.candidate["status"],
                }
            )
        return receipt


def test_live_failure_compensation_restores_only_active_b1_fault():
    values = {
        "CASELOOP_B1_BAD_VERSIONSET_ID": "vs-bad",
        "CASELOOP_B1_GOOD_VERSIONSET_ID": "vs-good",
    }
    api = _RecoveryAPI(bad_status="active", good_status="superseded")

    receipt = _compensate_incomplete_b1(api, values, run_id="run-1")

    assert receipt["status"] == "recovered"
    assert api.good["status"] == "active" and api.bad["status"] == "draft"
    assert api.posts[0][1]["idempotency_key"] == "b1-live-recover-run-1"


def test_live_failure_compensation_quarantines_canary_traffic_target():
    values = {
        "CASELOOP_B1_BAD_VERSIONSET_ID": "vs-bad",
        "CASELOOP_B1_GOOD_VERSIONSET_ID": "vs-good",
    }
    api = _RecoveryAPI(
        bad_status="active", good_status="superseded", candidate_status="canary"
    )

    receipt = _compensate_incomplete_b1(
        api,
        values,
        run_id="run-canary",
        quarantine_versionset_id="vs-candidate",
    )

    assert receipt["status"] == "recovered"
    assert api.candidate is not None and api.candidate["status"] == "rolled_back"
    assert api.posts[0][1]["quarantine_versionset_id"] == "vs-candidate"


def test_live_failure_compensation_never_demotes_unknown_promoted_target():
    values = {
        "CASELOOP_B1_BAD_VERSIONSET_ID": "vs-bad",
        "CASELOOP_B1_GOOD_VERSIONSET_ID": "vs-good",
    }
    api = _RecoveryAPI(
        bad_status="superseded",
        good_status="superseded",
        active=[
            {
                "versionset_id": "vs-fixed",
                "digest": "sha256:" + "c" * 64,
                "revision": 4,
                "status": "active",
            }
        ],
    )

    receipt = _compensate_incomplete_b1(api, values, run_id="run-2")

    assert receipt["status"] == "not_required_nonfault_active"
    assert api.posts == []


def test_live_probe_outputs_are_digest_checked_and_inlined(tmp_path):
    artifact = {"request_id": "req-live", "answer": "bound"}
    raw = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path = tmp_path / "probe.json"
    path.write_bytes(raw)
    bundle = {
        "cells": {
            "C": {
                "results": [
                    {"output_ref": path.resolve().as_uri(), "output_digest": _digest_bytes(raw)}
                ]
            }
        }
    }

    result = _inline_live_probe_outputs(bundle)

    uri = result["cells"]["C"]["results"][0]["output_ref"]
    assert uri.startswith("data:application/json;base64,")
    assert _decode_inline_json(uri) == artifact


def test_live_probe_output_digest_drift_and_non_file_input_fail_closed(tmp_path):
    path = tmp_path / "probe.json"
    path.write_text("{}", encoding="utf-8")
    mismatched = {
        "cells": {
            "C": {
                "results": [
                    {
                        "output_ref": path.resolve().as_uri(),
                        "output_digest": "sha256:" + "0" * 64,
                    }
                ]
            }
        }
    }
    with pytest.raises(LiveRunError, match="digest mismatch"):
        _inline_live_probe_outputs(mismatched)

    non_file = {
        "cells": {
            "C": {
                "results": [
                    {
                        "output_ref": "data:application/json;base64,e30=",
                        "output_digest": _digest_bytes(b"{}"),
                    }
                ]
            }
        }
    }
    with pytest.raises(LiveRunError, match="omitted its local output"):
        _inline_live_probe_outputs(non_file)


def test_gate_artifacts_are_rehashed_inlined_and_suite_refs_rebound(tmp_path):
    contract = tmp_path / "contract.json"
    candidate = tmp_path / "candidate.json"
    contract.write_text('{"exit_code":0,"output":"2 passed\\n"}', encoding="utf-8")
    candidate.write_text('{"responses":[],"judge_responses":[]}', encoding="utf-8")
    contract_ref = {"uri": contract.resolve().as_uri(), "digest": _digest_bytes(contract.read_bytes())}
    candidate_ref = {"uri": candidate.resolve().as_uri(), "digest": _digest_bytes(candidate.read_bytes())}
    report = {
        "artifact_refs": [contract_ref, candidate_ref],
        "deterministic_tests": {"suites": [{"report_ref": contract_ref["uri"]}]},
        "live_provider_e2e": {"suites": [{"report_ref": candidate_ref["uri"]}]},
    }

    result = _inline_gate_artifacts(report)

    assert all(ref["uri"].startswith("data:application/json;base64,") for ref in result["artifact_refs"])
    assert result["deterministic_tests"]["suites"][0]["report_ref"] == result["artifact_refs"][0]["uri"]
    assert result["live_provider_e2e"]["suites"][0]["report_ref"] == result["artifact_refs"][1]["uri"]


def test_live_gate_evidence_validator_replays_provider_and_judge_bindings():
    workorder = make_workorder(
        workorder_id="wo_livevalidator01",
        nonce="00000000-0000-0000-0000-000000000901",
        case_id="case_livevalidator01",
    )
    report = make_gate_report(
        workorder["workorder_id"],
        dataset_id=load_probe_set(REPO_ROOT).probe_set_id,
    )
    candidate = _decode_inline_json(report["artifact_refs"][2]["uri"])
    provider_logs = {
        item["request_id"]: {
            "request_id": item["request_id"],
            "status": "ok",
            "provider_origin": "https://api.stepfun.com/step_plan/v1",
            "trace_id": item["trace_id"],
            "versionset_id": item["versionset_id"],
            "prompt_digest": item["prompt_digest"],
            "kb_manifest_digest": item["kb_manifest_digest"],
            "model_digest": item["model_digest"],
            "answer_digest": "sha256:"
            + hashlib.sha256(item["answer"].encode("utf-8")).hexdigest(),
        }
        for item in candidate["responses"]
    }
    target = {
        "versionset_id": candidate["target_versionset_id"],
        "digest": candidate["target_versionset_digest"],
        "revision": candidate["target_revision"],
    }

    verified_candidate, count = _validate_live_gate(
        name="focused-live",
        report=report,
        workorder=workorder,
        target_versionset=target,
        probe_digest=report["subject"]["probe_set_digest"],
        probe_version="1.0.0",
        provider_logs=provider_logs,
    )

    assert verified_candidate == candidate
    assert count == len(candidate["responses"])


def test_persisted_live_gate_is_bound_to_exact_workorder_and_report():
    workorder = make_workorder(
        workorder_id="wo_livepersist01",
        nonce="00000000-0000-0000-0000-000000000902",
        case_id="case_livepersist01",
    )
    report = make_gate_report(
        workorder["workorder_id"],
        dataset_id=load_probe_set(REPO_ROOT).probe_set_id,
    )
    candidate = _decode_inline_json(report["artifact_refs"][2]["uri"])
    report_hash = canonical_json_digest(report, prefix=False)
    evidence_digest = canonical_json_digest(report["artifact_refs"])
    candidate_digest = canonical_json_digest(
        {
            "workorder_id": workorder["workorder_id"],
            "target_versionset_id": candidate["target_versionset_id"],
            "target_versionset_digest": report["subject"]["target_versionset_digest"],
            "target_revision": candidate["target_revision"],
            "dataset_id": candidate["dataset_id"],
            "dataset_version": candidate["dataset_version"],
            "dataset_digest": report["subject"]["probe_set_digest"],
            "regression_suite_digest": report["subject"]["regression_suite_digest"],
            "evidence_digest": evidence_digest,
        }
    )
    binding_digest = canonical_json_digest(
        {
            "eval_id": report["eval_id"],
            "report_hash": report_hash,
            "candidate_digest": candidate_digest,
            "workorder_hash": workorder["hash"],
            "target_versionset_id": candidate["target_versionset_id"],
            "target_versionset_digest": report["subject"]["target_versionset_digest"],
            "target_revision": candidate["target_revision"],
            "dataset_id": candidate["dataset_id"],
            "dataset_version": candidate["dataset_version"],
            "dataset_digest": report["subject"]["probe_set_digest"],
            "evidence_digest": evidence_digest,
        }
    )
    row = {
        "eval_id": report["eval_id"],
        "report_id": report["report_id"],
        "workorder_id": workorder["workorder_id"],
        "workorder_hash": workorder["hash"],
        "target_versionset_id": candidate["target_versionset_id"],
        "target_versionset_digest": report["subject"]["target_versionset_digest"],
        "target_revision": candidate["target_revision"],
        "dataset_id": candidate["dataset_id"],
        "dataset_version": candidate["dataset_version"],
        "dataset_digest": report["subject"]["probe_set_digest"],
        "evidence_digest": evidence_digest,
        "candidate_digest": candidate_digest,
        "report_hash": report_hash,
        "binding_digest": binding_digest,
        "authorization_digest": None,
        "overall_status": "passed",
        "report": report,
        "bound_at": "2026-08-08T00:00:00+00:00",
    }

    assert (
        _validate_persisted_live_gate(
            name="initial",
            row=row,
            report=report,
            candidate=candidate,
            workorder=workorder,
            expected_authorization_digest=None,
        )
        == binding_digest
    )
    row["workorder_hash"] = "0" * 64
    with pytest.raises(B1ValidationError, match="projection/binding mismatch"):
        _validate_persisted_live_gate(
            name="initial",
            row=row,
            report=report,
            candidate=candidate,
            workorder=workorder,
            expected_authorization_digest=None,
        )


def test_live_provider_report_counts_are_derived_from_enumerated_checks():
    report = {
        "status": "passed",
        "passed": 2,
        "failed": 0,
        "checks": [
            {"check": "provider_receipt", "passed": True},
            {"check": "authority_trace", "passed": True},
        ],
    }
    assert _report_semantics("live-provider", report) == ("passed", 2, 0)
    report["passed"] = 3
    with pytest.raises(Exception, match="counts differ"):
        _report_semantics("live-provider", report)


def test_live_notification_must_reply_to_exact_inbound_thread():
    manifest = {
        "transaction_id": "om_original_001",
        "case_id": "case_live12345678",
    }
    fixture = load_b1_complaint_fixture()
    inbound = {
        "provider": "feishu",
        "provider_origin": "https://open.feishu.cn",
        "message_id": "om_original_001",
        "channel": "feishu:oc_chat_001",
        "thread_ref": "feishu:oc_chat_001:om_original_001",
        "text_digest": fixture.text_digest,
        "create_time": "1786212345000",
    }
    injection_id = "inject-b1-live-exact"
    domain = {
        "inbound_dedup": {
            "message_acquisition": {
                "schema_version": "0.1.0",
                "adapter": "external-post-injection-feishu-message-command",
                "requested_at": "2026-08-08T00:01:00+00:00",
                "completed_at": "2026-08-08T00:02:00+00:00",
                "request": {
                    "phase": "await-post-injection-complaint",
                    "provider": "feishu",
                    "fixture_ref": fixture.repository_ref,
                    "fixture_text_digest": fixture.text_digest,
                    "injection_operation_id": injection_id,
                    "not_before": "2026-08-08T00:00:00+00:00",
                },
                "command": {
                    "executable": "feishu-acquire",
                    "argv_digest": "sha256:" + "c" * 64,
                    "exit_code": 0,
                },
                "receipt": {
                    "schema_version": "0.1.0",
                    "provider": "feishu",
                    "message_id": manifest["transaction_id"],
                },
            },
            "inbound": inbound,
            "inbox": {
                "case_id": manifest["case_id"],
                "source": "webhook",
                "external_id": manifest["transaction_id"],
                "disposition": "FILED",
                "raw_payload": {
                    "external_id": manifest["transaction_id"],
                    "channel": inbound["channel"],
                    "thread_ref": inbound["thread_ref"],
                    "demo_fault_injection_id": injection_id,
                    "provider_origin": inbound["provider_origin"],
                    "provider_create_time": inbound["create_time"],
                    "source_text_digest": fixture.text_digest,
                },
            },
            "complaint_event": {
                "aggregate_type": "case",
                "aggregate_id": manifest["case_id"],
                "event_type": "complaint.received",
                "payload": {
                    "external_id": manifest["transaction_id"],
                    "channel": inbound["channel"],
                    "thread_ref": inbound["thread_ref"],
                    "demo_fault_injection_id": injection_id,
                    "provider_origin": inbound["provider_origin"],
                    "provider_create_time": inbound["create_time"],
                    "source_text_digest": fixture.text_digest,
                    "attachments": [f"feishu-text-digest:{inbound['text_digest']}"],
                },
            },
            "case_projection": {
                "aggregate_id": manifest["case_id"],
                "payload": {
                    "provider_origin": inbound["provider_origin"],
                    "provider_create_time": inbound["create_time"],
                    "source_text_digest": fixture.text_digest,
                },
            },
            "duplicate_audits": [
                {
                    "action": "complaint.duplicate",
                    "target": manifest["case_id"],
                    "result": "success",
                }
            ],
        }
    }
    frozen = {
        "complaint_fixture_ref": fixture.repository_ref,
        "complaint_text_digest": fixture.text_digest,
        "injection_receipt": {"injected_at": "2026-08-08T00:00:00+00:00"},
        "injection_authority": {"aggregate": {"aggregate_id": injection_id}},
    }
    notification = {
        "payload": {
            "channel": inbound["channel"],
            "thread_ref": inbound["thread_ref"],
        }
    }

    _validate_live_inbound_notification_binding(
        manifest=manifest,
        domain=domain,
        frozen=frozen,
        notification=notification,
    )
    notification["payload"]["thread_ref"] = "feishu:oc_chat_999:om_other"
    with pytest.raises(B1ValidationError, match="original Feishu channel/thread"):
        _validate_live_inbound_notification_binding(
            manifest=manifest,
            domain=domain,
            frozen=frozen,
            notification=notification,
        )

    notification["payload"]["thread_ref"] = inbound["thread_ref"]
    original_digest = inbound["text_digest"]
    inbound["text_digest"] = "sha256:" + hashlib.sha256(b"hello").hexdigest()
    with pytest.raises(B1ValidationError, match="repository-owned B1 fixture"):
        _validate_live_inbound_notification_binding(
            manifest=manifest,
            domain=domain,
            frozen=frozen,
            notification=notification,
        )

    inbound["text_digest"] = original_digest
    inbound["create_time"] = "1700000000000"
    with pytest.raises(B1ValidationError, match="predates the B1 injection"):
        _validate_live_inbound_notification_binding(
            manifest=manifest,
            domain=domain,
            frozen=frozen,
            notification=notification,
        )


@pytest.mark.parametrize("value", [None, "", "not-a-time", "1700000000", True])
def test_feishu_message_create_time_fails_closed(value):
    with pytest.raises(LiveRunError, match="create_time"):
        _feishu_message_created_at(value)


def test_failed_self_validation_never_publishes_passed_manifest_or_report(
    monkeypatch, tmp_path
):
    live_test_path = tmp_path / "live-provider-test-report.json"
    live_test_path.write_text(
        json.dumps(
            {
                "status": "passed",
                "passed": 1,
                "failed": 0,
                "checks": [
                    {
                        "check": "provider_receipt",
                        "passed": True,
                        "evidence_refs": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "run_b1_live.validate_b1_run",
        lambda _path: (_ for _ in ()).throw(B1ValidationError("tampered evidence")),
    )

    with pytest.raises(B1ValidationError, match="tampered evidence"):
        _publish_verified_manifest(
            output_dir=tmp_path,
            manifest={"status": "passed"},
            live_test_path=live_test_path,
        )

    assert not (tmp_path / "b1-run-manifest.json").exists()
    assert not (tmp_path / ".b1-run-manifest.candidate.json").exists()
    failed_report = json.loads(live_test_path.read_text(encoding="utf-8"))
    assert failed_report["status"] == "failed"
    assert failed_report["failed"] == 1
    assert failed_report["checks"][-1] == {
        "check": "evidence_bundle_self_verified",
        "passed": False,
        "evidence_refs": [],
    }


def test_failed_replay_self_validation_never_publishes_passed_manifest(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "run_b1_replay.validate_b1_run",
        lambda _path, **_kwargs: (_ for _ in ()).throw(
            B1ValidationError("tampered replay evidence")
        ),
    )

    with pytest.raises(B1ValidationError, match="tampered replay evidence"):
        _publish_verified_replay_manifest(
            output_dir=tmp_path,
            manifest={"status": "passed"},
            allow_dirty=True,
        )

    assert not (tmp_path / "b1-run-manifest.json").exists()
    assert not (tmp_path / ".b1-run-manifest.candidate.json").exists()
