"""P0-1 GateReport -> WorkOrder -> Approval -> Release fail-closed chain."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import pytest
from types import SimpleNamespace

from app.config import Settings
from app.models.tables import Approval, GateReportRecord, Lease, WorkOrder
from app.api.deps import (
    require_approval_authority,
    require_gate_authority,
    require_internal_write,
)
from app.quality.client import FakeQualityClient
from app.services.gate_service import GateServiceError
from app.services.case_service import CaseService
from app.services.release_service import ReleaseService, ReleaseServiceError
from app.utils.jcs import canonical_json_digest
from tests.conftest import (
    TEST_GATE_TOKEN,
    make_approval,
    make_gate_report,
    make_workorder,
    register_gate_for_workorder,
    register_release_verification,
    register_workorder_with_lease,
)


def _service(session):
    quality = FakeQualityClient()
    quality.seed_versionset(
        "vs_demo001fixedversionset01",
        status="draft",
        revision=1,
        digest="sha256:" + "b" * 64,
    )
    return ReleaseService(session, quality), quality


def _inline_payload(ref):
    return json.loads(base64.b64decode(ref["uri"].split(",", 1)[1]))


def _replace_inline_artifact(report, index, payload):
    old_uri = report["artifact_refs"][index]["uri"]
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    new_ref = {
        "uri": "data:application/json;base64," + base64.b64encode(raw).decode("ascii"),
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
    }
    report["artifact_refs"][index] = new_ref
    for track in (report["deterministic_tests"], report["live_provider_e2e"]):
        for suite in track.get("suites") or []:
            if suite.get("report_ref") == old_uri:
                suite["report_ref"] = new_ref["uri"]


def _seed_candidate_logs(quality, candidate):
    for item in candidate["responses"]:
        quality.seed_log(
            item["request_id"],
            status="ok",
            provider_origin=item.get("provider_origin"),
            trace_id=item["trace_id"],
            versionset_id=item["versionset_id"],
            prompt_digest=item["prompt_digest"],
            kb_manifest_digest=item["kb_manifest_digest"],
            model_digest=item["model_digest"],
            answer_digest="sha256:"
            + hashlib.sha256(item["answer"].encode("utf-8")).hexdigest(),
        )


def _register_gate_envelope(svc, report, workorder_id):
    return svc.gates.register_report(
        {
            "report": report,
            "report_hash": canonical_json_digest(report, prefix=False),
            "workorder_id": workorder_id,
            "target_versionset_id": "vs_demo001fixedversionset01",
            "target_revision": 1,
            "dataset_id": "customer-service-regression",
            "dataset_version": "1.0.0",
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
        }
    )


def test_missing_gate_report_rejects_workorder(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_missinggate1",
        nonce="00000000-0000-0000-0000-000000000101",
        case_id="case_gate",
    )
    with pytest.raises(ReleaseServiceError) as exc:
        register_workorder_with_lease(svc, wo)
    assert exc.value.code == "gate_missing"
    assert sqlite_session.get(WorkOrder, wo["workorder_id"]) is None


def test_failed_gate_report_binds_workorder_but_blocks_approval(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_failedgate1",
        nonce="00000000-0000-0000-0000-000000000102",
        case_id="case_gate",
    )
    report = register_gate_for_workorder(svc, wo, overall_status="failed")
    registered = register_workorder_with_lease(svc, wo)
    row = sqlite_session.get(GateReportRecord, report["eval_id"])
    assert registered["duplicate"] is False
    assert row is not None and row.workorder_hash == wo["hash"] and row.binding_digest
    assert any(
        event.event_type == "eval.bound" and event.payload["overall_status"] == "failed"
        for event in svc.store.list_events(report["eval_id"])
    )
    changeset = svc.store.get_aggregate("changeset", f"cs_{wo['workorder_id']}")
    assert changeset is not None and changeset.state == "DRAFTED"
    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(make_approval(wo, "ap_failedgate1"))
    assert exc.value.code == "gate_failed"


def test_error_gate_report_binds_workorder_but_blocks_approval(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_errorgate01",
        nonce="00000000-0000-0000-0000-000000000107",
        case_id="case_gate",
    )
    report = register_gate_for_workorder(svc, wo, overall_status="error")
    stored = sqlite_session.get(GateReportRecord, report["eval_id"])
    assert stored is not None and stored.overall_status == "error"
    registered = register_workorder_with_lease(svc, wo)
    assert registered["duplicate"] is False
    assert stored.workorder_hash == wo["hash"] and stored.binding_digest
    assert any(
        event.event_type == "eval.bound" and event.payload["overall_status"] == "error"
        for event in svc.store.list_events(report["eval_id"])
    )
    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(make_approval(wo, "ap_errorgate01"))
    assert exc.value.code == "gate_failed"


def test_gate_workorder_hash_binding_mismatch_blocks_approval(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_hashbind001",
        nonce="00000000-0000-0000-0000-000000000103",
        case_id="case_gate",
    )
    report = register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    row = sqlite_session.get(GateReportRecord, report["eval_id"])
    assert row is not None
    row.workorder_hash = "0" * 64
    sqlite_session.flush()

    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(make_approval(wo, "ap_hashbind001"))
    assert exc.value.code == "hash_mismatch"
    assert sqlite_session.get(Approval, "ap_hashbind001") is None


def test_initial_and_post_canary_gates_bind_same_workorder_hash(sqlite_session):
    svc, quality = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_multigate001",
        nonce="00000000-0000-0000-0000-000000000108",
        case_id="case_gate",
    )
    initial = register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    remote = quality.get_versionset("vs_demo001fixedversionset01")
    post = register_release_verification(
        svc,
        wo,
        remote,
        overall_status="passed",
        eval_id="eval_postmultigate001",
    )
    sqlite_session.flush()

    initial_row = sqlite_session.get(GateReportRecord, initial["eval_id"])
    post_row = sqlite_session.get(GateReportRecord, post["eval_id"])
    assert initial_row is not None and post_row is not None
    assert initial_row.workorder_hash == post_row.workorder_hash == wo["hash"]
    assert initial_row.binding_digest and post_row.binding_digest
    assert initial_row.binding_digest != post_row.binding_digest
    assert initial_row.authorization_digest is None
    assert post_row.authorization_digest
    assert svc.gates.get(post["eval_id"])["workorder_hash"] == wo["hash"]


def test_synchronous_gate_registration_records_honest_contract_receipt(sqlite_session):
    svc, _ = _service(sqlite_session)
    workorder = make_workorder(
        workorder_id="wo_syncreceipt1",
        nonce="00000000-0000-0000-0000-000000000109",
        case_id="case_gate",
    )
    report = register_gate_for_workorder(svc, workorder)
    events = svc.store.list_events(report["eval_id"])

    assert [event.event_type for event in events] == [
        "eval.requested",
        "eval.report_received",
        "eval.passed",
    ]
    requested = events[0].payload
    assert requested["changeset_id"] == f"cs_{workorder['workorder_id']}"
    assert requested["target_versionset_digest"] == report["subject"]["target_versionset_digest"]
    assert requested["regression_suite_digest"] == report["subject"]["regression_suite_digest"]
    assert requested["probe_set_digest"] == report["subject"]["probe_set_digest"]
    assert requested["trigger"] == "gate"
    receipt = events[1].payload
    assert receipt["execution_mode"] == "completed_report_import"
    assert receipt["rule_status"] == receipt["judge_status"] == "passed"
    assert receipt["n_rule_passed"] >= 1 and receipt["n_rule_failed"] == 0
    assert receipt["judge_model_digest"] != receipt["athlete_model_digest"]


def test_target_revision_drift_blocks_release_without_consuming_nonce(sqlite_session):
    svc, quality = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_revision001",
        nonce="00000000-0000-0000-0000-000000000104",
        case_id="case_gate",
    )
    register_gate_for_workorder(svc, wo, target_revision=1)
    register_workorder_with_lease(svc, wo)
    approval = make_approval(wo, "ap_revision001")
    svc.grant_approval(approval)
    sqlite_session.flush()
    # Simulate a target mutation after gate/approval.
    quality._vs["vs_demo001fixedversionset01"].revision = 2

    with pytest.raises(ReleaseServiceError) as exc:
        svc.start_release(
            workorder_id=wo["workorder_id"],
            approval_id=approval["approval_id"],
            versionset_id="vs_demo001fixedversionset01",
        )
    assert exc.value.code == "revision_conflict"
    stored = sqlite_session.get(Approval, approval["approval_id"])
    assert stored is not None and stored.status == "pending" and stored.consumed_at is None


def test_passed_overall_with_skipped_live_is_rejected(sqlite_session):
    svc, _ = _service(sqlite_session)
    workorder_id = "wo_inconsistent1"
    report = make_gate_report(workorder_id)
    report["live_provider_e2e"] = {
        "status": "skipped",
        "provider": "stepfun",
        "suites": [{"suite": "live", "status": "skipped", "n_passed": 0, "n_failed": 0}],
    }
    with pytest.raises(GateServiceError) as exc:
        svc.gates.register_report(
            {
                "report": report,
                "workorder_id": workorder_id,
                "target_versionset_id": "vs_demo001fixedversionset01",
                "target_revision": 1,
                "dataset_id": "customer-service-regression",
                "dataset_version": "1.0.0",
                "evidence_digest": canonical_json_digest(report["artifact_refs"]),
            }
        )
    assert "inconsistent" in str(exc.value)


def test_isolated_replay_gate_requires_explicit_sqlite_controller(sqlite_session):
    workorder_id = "wo_isolatedpolicy"
    report = make_gate_report(workorder_id, policy_profile="isolated-replay")
    envelope = {
        "report": report,
        "workorder_id": workorder_id,
        "target_versionset_id": "vs_demo001fixedversionset01",
        "target_revision": 1,
        "dataset_id": "customer-service-regression",
        "dataset_version": "1.0.0",
        "evidence_digest": canonical_json_digest(report["artifact_refs"]),
    }
    quality = FakeQualityClient()
    disabled = ReleaseService(
        sqlite_session,
        quality,
        Settings(
            database_url="sqlite:///:memory:",
            gate_policy_profile="isolated-replay",
            allow_isolated_replay_gate=False,
        ),
    )
    with pytest.raises(GateServiceError) as exc:
        disabled.gates.register_report(envelope)
    assert "disabled" in exc.value.message

    production = ReleaseService(
        sqlite_session,
        quality,
        Settings(
            database_url="postgresql+psycopg://controller.example/caseloop",
            gate_policy_profile="isolated-replay",
            allow_isolated_replay_gate=True,
        ),
    )
    with pytest.raises(GateServiceError) as exc:
        production.gates.register_report(envelope)
    assert "SQLite" in exc.value.message

    isolated = ReleaseService(
        sqlite_session,
        quality,
        Settings(
            database_url="sqlite:///:memory:",
            gate_policy_profile="isolated-replay",
            allow_isolated_replay_gate=True,
        ),
    )
    registered = isolated.gates.register_report(envelope)
    assert registered["overall_status"] == "passed"


@pytest.mark.parametrize(
    ("field", "value"),
    [("pass_threshold", float("nan")), ("score", float("nan"))],
)
def test_gate_report_rejects_non_finite_judge_numbers(sqlite_session, field, value):
    svc, _ = _service(sqlite_session)
    workorder_id = f"wo_nonfinite_{field}"
    report = make_gate_report(workorder_id)
    if field == "score":
        report["judge_track"]["scores"][0][field] = value
    else:
        report["judge_track"][field] = value

    with pytest.raises(GateServiceError) as exc:
        svc.gates.register_report(
            {
                "report": report,
                "workorder_id": workorder_id,
                "target_versionset_id": "vs_demo001fixedversionset01",
                "target_revision": 1,
                "dataset_id": "customer-service-regression",
                "dataset_version": "1.0.0",
                "evidence_digest": canonical_json_digest(report["artifact_refs"]),
            }
        )

    assert exc.value.code == "validation_failed"
    assert sqlite_session.get(GateReportRecord, report["eval_id"]) is None


def test_persisted_gate_report_tamper_fails_closed(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_reporttamper",
        nonce="00000000-0000-0000-0000-000000000105",
        case_id="case_gate",
    )
    report = register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    row = sqlite_session.get(GateReportRecord, report["eval_id"])
    assert row is not None
    row.report = {**row.report, "overall_status": "failed"}
    sqlite_session.flush()
    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(make_approval(wo, "ap_reporttamper"))
    assert exc.value.code == "hash_mismatch"


def test_exact_gate_target_allows_release_start(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_gatepass001",
        nonce="00000000-0000-0000-0000-000000000106",
        case_id="case_gate",
    )
    register_gate_for_workorder(svc, wo)
    register_workorder_with_lease(svc, wo)
    approval = make_approval(wo, "ap_gatepass001")
    svc.grant_approval(approval)
    result = svc.start_release(
        workorder_id=wo["workorder_id"],
        approval_id=approval["approval_id"],
        versionset_id="vs_demo001fixedversionset01",
    )
    assert result["state"] == "REQUESTED"


def test_passed_gate_rejects_tampered_executed_suite_counts(sqlite_session):
    svc, _quality = _service(sqlite_session)
    workorder_id = "wo_suite_tamper01"
    report = make_gate_report(workorder_id)
    contract = _inline_payload(report["artifact_refs"][0])
    contract["output"] = "2 passed in 0.01s\n"
    _replace_inline_artifact(report, 0, contract)

    with pytest.raises(GateServiceError) as exc:
        _register_gate_envelope(svc, report, workorder_id)

    assert exc.value.code == "validation_failed"
    assert "suite summary" in exc.value.message


def test_passed_gate_rejects_failing_raw_answer_with_all_outer_digests_updated(
    sqlite_session,
):
    svc, quality = _service(sqlite_session)
    workorder_id = "wo_answer_tamper01"
    report = make_gate_report(workorder_id)
    candidate = _inline_payload(report["artifact_refs"][2])
    response = candidate["responses"][0]
    response["answer"] = "No applicable policy or resolution is available."
    candidate["judge_responses"][0]["answer_digest"] = (
        "sha256:" + hashlib.sha256(response["answer"].encode("utf-8")).hexdigest()
    )
    _replace_inline_artifact(report, 2, candidate)
    _seed_candidate_logs(quality, candidate)

    with pytest.raises(GateServiceError) as exc:
        _register_gate_envelope(svc, report, workorder_id)

    assert exc.value.code == "validation_failed"
    assert "failing raw answers" in exc.value.message


def test_passed_gate_rejects_missing_authoritative_quality_log(sqlite_session):
    svc, _quality = _service(sqlite_session)
    workorder_id = "wo_log_missing001"
    report = make_gate_report(workorder_id)

    with pytest.raises(GateServiceError) as exc:
        _register_gate_envelope(svc, report, workorder_id)

    assert exc.value.code == "validation_failed"
    assert "Quality provider log missing" in exc.value.message


@pytest.mark.parametrize(
    ("location", "origin"),
    [
        ("response", None),
        ("response", "http://127.0.0.1:9999/v1"),
        ("log", None),
        ("log", "http://127.0.0.1:9999/v1"),
    ],
)
def test_passed_gate_rejects_missing_or_nonofficial_provider_origin(
    sqlite_session,
    location,
    origin,
):
    svc, quality = _service(sqlite_session)
    workorder_id = f"wo_origin_{location}_{'missing' if origin is None else 'stub'}"
    report = make_gate_report(workorder_id)
    candidate = _inline_payload(report["artifact_refs"][2])
    if location == "response":
        if origin is None:
            candidate["responses"][0].pop("provider_origin", None)
        else:
            candidate["responses"][0]["provider_origin"] = origin
        _replace_inline_artifact(report, 2, candidate)
        _seed_candidate_logs(quality, candidate)
        # Keep the authoritative log official so this case isolates the raw
        # response receipt boundary.
        first = candidate["responses"][0]
        quality.seed_log(
            first["request_id"],
            status="ok",
            provider_origin="https://api.stepfun.com/step_plan/v1",
            trace_id=first["trace_id"],
            versionset_id=first["versionset_id"],
            prompt_digest=first["prompt_digest"],
            kb_manifest_digest=first["kb_manifest_digest"],
            model_digest=first["model_digest"],
            answer_digest="sha256:"
            + hashlib.sha256(first["answer"].encode("utf-8")).hexdigest(),
        )
    else:
        _seed_candidate_logs(quality, candidate)
        first = candidate["responses"][0]
        provider_log = quality.get_log(first["request_id"])
        if origin is None:
            provider_log.pop("provider_origin", None)
        else:
            provider_log["provider_origin"] = origin
        quality.seed_log(
            first["request_id"],
            **{
                key: value
                for key, value in provider_log.items()
                if key != "request_id"
            },
        )

    with pytest.raises(GateServiceError) as exc:
        _register_gate_envelope(svc, report, workorder_id)

    assert exc.value.code == "validation_failed"
    assert sqlite_session.get(GateReportRecord, report["eval_id"]) is None


def test_passed_gate_rejects_reused_provider_log_across_probes(sqlite_session):
    svc, quality = _service(sqlite_session)
    workorder_id = "wo_log_reuse0001"
    report = make_gate_report(workorder_id)
    candidate = _inline_payload(report["artifact_refs"][2])
    candidate["responses"][1]["request_id"] = candidate["responses"][0]["request_id"]
    candidate["responses"][1]["trace_id"] = candidate["responses"][0]["trace_id"]
    _replace_inline_artifact(report, 2, candidate)
    _seed_candidate_logs(quality, candidate)

    with pytest.raises(GateServiceError) as exc:
        _register_gate_envelope(svc, report, workorder_id)

    assert exc.value.code == "validation_failed"
    assert "unique request_id and trace_id" in exc.value.message


def test_passed_gate_rejects_reused_judge_receipt_across_probes(sqlite_session):
    svc, quality = _service(sqlite_session)
    workorder_id = "wo_judge_reuse001"
    report = make_gate_report(workorder_id)
    candidate = _inline_payload(report["artifact_refs"][2])
    candidate["judge_responses"][1]["provider_request_id"] = candidate[
        "judge_responses"
    ][0]["provider_request_id"]
    _replace_inline_artifact(report, 2, candidate)
    _seed_candidate_logs(quality, candidate)

    with pytest.raises(GateServiceError) as exc:
        _register_gate_envelope(svc, report, workorder_id)

    assert exc.value.code == "validation_failed"
    assert "unique provider_request_id" in exc.value.message


def test_passed_gate_rejects_judge_raw_response_parse_drift(sqlite_session):
    svc, quality = _service(sqlite_session)
    workorder_id = "wo_judge_tamper01"
    report = make_gate_report(workorder_id)
    candidate = _inline_payload(report["artifact_refs"][2])
    judge = candidate["judge_responses"][0]
    judge["raw_response"] = json.dumps(
        {"score": 0.95, "pass": False, "rationale": "tampered"},
        separators=(",", ":"),
    )
    judge["raw_response_digest"] = (
        "sha256:" + hashlib.sha256(judge["raw_response"].encode("utf-8")).hexdigest()
    )
    _replace_inline_artifact(report, 2, candidate)
    _seed_candidate_logs(quality, candidate)

    with pytest.raises(GateServiceError) as exc:
        _register_gate_envelope(svc, report, workorder_id)

    assert exc.value.code == "validation_failed"
    assert "parsed faithfully" in exc.value.message


def _leased_workorder(sqlite_session, suffix: str):
    svc, _ = _service(sqlite_session)
    cases = CaseService(sqlite_session)
    case_id = cases.ingest_complaint(
        source="webhook",
        text="atomic workorder fencing",
        external_id=f"workorder-fencing-{suffix}",
    )["case_id"]
    claim = cases.claim(case_id, "repairer-1")
    wo = make_workorder(
        workorder_id=f"wo_fence{suffix}",
        nonce=f"00000000-0000-0000-0000-{int(suffix):012d}",
        case_id=case_id,
    )
    register_gate_for_workorder(svc, wo)
    return svc, wo, claim


def test_workorder_registration_locks_and_validates_lease_in_write_transaction(sqlite_session):
    svc, wo, claim = _leased_workorder(sqlite_session, "201")

    result = svc.register_workorder(
        wo, worker_id="repairer-1", fencing_token=claim["fencing_token"]
    )

    assert result["duplicate"] is False
    assert sqlite_session.get(WorkOrder, wo["workorder_id"]) is not None


def test_workorder_registration_has_no_direct_service_lease_bypass(sqlite_session):
    svc, wo, _claim = _leased_workorder(sqlite_session, "205")

    with pytest.raises(ReleaseServiceError) as exc:
        svc.register_workorder(wo)

    assert exc.value.code == "lease_lost"
    assert sqlite_session.get(WorkOrder, wo["workorder_id"]) is None


@pytest.mark.parametrize(
    ("worker_id", "token_offset"),
    [("other-worker", 0), ("repairer-1", 1)],
)
def test_workorder_registration_wrong_owner_or_stale_token_has_zero_write(
    sqlite_session, worker_id, token_offset
):
    suffix = "202" if worker_id == "other-worker" else "203"
    svc, wo, claim = _leased_workorder(sqlite_session, suffix)

    with pytest.raises(ReleaseServiceError) as exc:
        svc.register_workorder(
            wo,
            worker_id=worker_id,
            fencing_token=claim["fencing_token"] + token_offset,
        )

    assert exc.value.code == "lease_lost"
    assert sqlite_session.get(WorkOrder, wo["workorder_id"]) is None


def test_workorder_registration_expired_lease_has_zero_write(sqlite_session):
    svc, wo, claim = _leased_workorder(sqlite_session, "204")
    lease = sqlite_session.get(Lease, wo["case_id"])
    lease.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    sqlite_session.flush()

    with pytest.raises(ReleaseServiceError) as exc:
        svc.register_workorder(
            wo, worker_id="repairer-1", fencing_token=claim["fencing_token"]
        )

    assert exc.value.code == "lease_lost"
    assert sqlite_session.get(WorkOrder, wo["workorder_id"]) is None


def test_workorder_http_registration_requires_fencing_envelope(app_client):
    client, _ = app_client

    response = client.post("/v1/workorders", json={"workorder": {}})

    assert response.status_code == 422


def test_authoritative_mutation_routes_reject_unauthenticated_callers(app_client):
    client, _ = app_client
    token = client.headers.pop("Authorization")
    try:
        assert client.post("/v1/gate-reports", json={}).status_code == 401
        assert client.post("/v1/workorders", json={}).status_code == 401
        assert client.post("/v1/approvals", json={}).status_code == 401
        assert client.post(
            "/v1/releases",
            json={"workorder_id": "wo_x", "approval_id": "ap_x", "versionset_id": "vs_x"},
        ).status_code == 401
        assert client.post(
            "/v1/changesets",
            json={
                "case_id": "case_x",
                "workorder_ref": "wo_x",
                "workorder_hash": "0" * 64,
                "channel": "prompt",
                "author_agent": "repairer",
            },
        ).status_code == 401
    finally:
        client.headers["Authorization"] = token

    # Agent/control token is deliberately not human-approval authority.
    assert client.post("/v1/approvals", json={}).status_code == 401
    # Gate evaluator authority is also isolated from the general controller.
    assert client.post("/v1/gate-reports", json={}).status_code == 401
    control_authorization = client.headers["Authorization"]
    try:
        client.headers["Authorization"] = f"Bearer {TEST_GATE_TOKEN}"
        assert client.post("/v1/gate-reports", json={}).status_code == 422
    finally:
        client.headers["Authorization"] = control_authorization
    # Read views are a separate Console-facing boundary.
    assert client.get("/v1/gates").status_code == 200


@pytest.mark.parametrize(
    "dependency",
    [require_internal_write, require_approval_authority, require_gate_authority],
)
def test_authority_dependencies_reject_equal_control_and_approval_tokens(dependency):
    settings = SimpleNamespace(
        control_plane_internal_token="same-token",
        approval_authority_token="same-token",
        gate_authority_token="gate-token",
        control_plane_role_tokens_json="{}",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    with pytest.raises(Exception) as exc:
        dependency(request, "Bearer same-token")
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "auth_misconfigured"
