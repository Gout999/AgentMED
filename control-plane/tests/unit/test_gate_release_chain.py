"""P0-1 GateReport -> WorkOrder -> Approval -> Release fail-closed chain."""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.models.tables import Approval, GateReportRecord, WorkOrder
from app.api.deps import require_approval_authority, require_internal_write
from app.quality.client import FakeQualityClient
from app.services.gate_service import GateServiceError
from app.services.release_service import ReleaseService, ReleaseServiceError
from app.utils.jcs import canonical_json_digest
from tests.conftest import (
    make_approval,
    make_gate_report,
    make_workorder,
    register_gate_for_workorder,
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


def test_missing_gate_report_rejects_workorder(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_missinggate1",
        nonce="00000000-0000-0000-0000-000000000101",
        case_id="case_gate",
    )
    with pytest.raises(ReleaseServiceError) as exc:
        svc.register_workorder(wo)
    assert exc.value.code == "gate_missing"
    assert sqlite_session.get(WorkOrder, wo["workorder_id"]) is None


def test_failed_gate_report_rejects_workorder(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_failedgate1",
        nonce="00000000-0000-0000-0000-000000000102",
        case_id="case_gate",
    )
    register_gate_for_workorder(svc, wo, overall_status="failed")
    with pytest.raises(ReleaseServiceError) as exc:
        svc.register_workorder(wo)
    assert exc.value.code == "gate_failed"


def test_error_gate_report_is_persisted_but_rejects_workorder(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_errorgate01",
        nonce="00000000-0000-0000-0000-000000000107",
        case_id="case_gate",
    )
    report = register_gate_for_workorder(svc, wo, overall_status="error")
    stored = sqlite_session.get(GateReportRecord, report["eval_id"])
    assert stored is not None and stored.overall_status == "error"
    with pytest.raises(ReleaseServiceError) as exc:
        svc.register_workorder(wo)
    assert exc.value.code == "gate_failed"


def test_gate_workorder_hash_binding_mismatch_blocks_approval(sqlite_session):
    svc, _ = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_hashbind001",
        nonce="00000000-0000-0000-0000-000000000103",
        case_id="case_gate",
    )
    report = register_gate_for_workorder(svc, wo)
    svc.register_workorder(wo)
    row = sqlite_session.get(GateReportRecord, report["eval_id"])
    assert row is not None
    row.workorder_hash = "0" * 64
    sqlite_session.flush()

    with pytest.raises(ReleaseServiceError) as exc:
        svc.grant_approval(make_approval(wo, "ap_hashbind001"))
    assert exc.value.code == "hash_mismatch"
    assert sqlite_session.get(Approval, "ap_hashbind001") is None


def test_target_revision_drift_blocks_release_without_consuming_nonce(sqlite_session):
    svc, quality = _service(sqlite_session)
    wo = make_workorder(
        workorder_id="wo_revision001",
        nonce="00000000-0000-0000-0000-000000000104",
        case_id="case_gate",
    )
    register_gate_for_workorder(svc, wo, target_revision=1)
    svc.register_workorder(wo)
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
    svc.register_workorder(wo)
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
    svc.register_workorder(wo)
    approval = make_approval(wo, "ap_gatepass001")
    svc.grant_approval(approval)
    result = svc.start_release(
        workorder_id=wo["workorder_id"],
        approval_id=approval["approval_id"],
        versionset_id="vs_demo001fixedversionset01",
    )
    assert result["state"] == "REQUESTED"


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
    # Read views are a separate Console-facing boundary.
    assert client.get("/v1/gates").status_code == 200


@pytest.mark.parametrize("dependency", [require_internal_write, require_approval_authority])
def test_authority_dependencies_reject_equal_control_and_approval_tokens(dependency):
    settings = SimpleNamespace(
        control_plane_internal_token="same-token",
        approval_authority_token="same-token",
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    with pytest.raises(Exception) as exc:
        dependency(request, "Bearer same-token")
    assert exc.value.status_code == 503
    assert exc.value.detail["code"] == "auth_misconfigured"
