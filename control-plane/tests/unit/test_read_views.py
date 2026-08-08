"""T8 read projection tests over SQLite with no external dependency."""
from __future__ import annotations

import pytest
from sqlalchemy import select, text

from app.models.tables import Aggregate, Event, GateReportRecord, WorkOrder
from app.services.trust_service import TrustService
from app.utils.jcs import canonical_json_digest, workorder_hash
from tests.conftest import make_gate_report, make_workorder


def _create_mcp_tables(engine) -> None:
    """按 mcp-servers/001_init.sql 的 mcp_* 列形状建表（sqlite 子集，可被 JSON 列读回）。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mcp_trust_ledger (
                  risk_class TEXT NOT NULL,
                  action_type TEXT NOT NULL,
                  epoch INTEGER NOT NULL,
                  successes INTEGER NOT NULL DEFAULT 0,
                  trials INTEGER NOT NULL DEFAULT 0,
                  autonomy_state TEXT NOT NULL DEFAULT 'MANUAL',
                  suspended_until TIMESTAMP,
                  pending_promotion_ref TEXT,
                  payload TEXT NOT NULL DEFAULT '{}',
                  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (risk_class, action_type, epoch)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mcp_audit (
                  audit_id TEXT PRIMARY KEY,
                  ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                  actor TEXT NOT NULL,
                  action TEXT NOT NULL,
                  target TEXT NOT NULL,
                  params_digest TEXT NOT NULL,
                  result TEXT NOT NULL DEFAULT 'success',
                  error_code TEXT,
                  trace_id TEXT NOT NULL,
                  evidence_refs TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS mcp_eval_runs (
                  eval_id TEXT PRIMARY KEY,
                  workorder_id TEXT NOT NULL,
                  suite_digest TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'queued',
                  report TEXT,
                  report_hash TEXT,
                  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )


# ------------------------------------------------------------------ 1. GET /v1/env


def test_env_returns_active_versionset_digest(app_client):
    client, quality = app_client
    quality.seed_versionset(
        "vs_baseline0000000001",
        status="active",
        revision=1,
        digest="sha256:8fc6a7ac413b640d759293053d3a414eb2c781a831d973c26794750f63dc47a1",
    )
    r = client.get("/v1/env")
    assert r.status_code == 200
    body = r.json()
    assert body["demo_app"]["versionset_id"] == "vs_baseline0000000001"
    assert body["demo_app"]["digest"].startswith("sha256:8fc6a7ac")
    assert body["demo_app"]["status"] == "active"
    assert body["demo_app"]["revision"] == 1


def test_env_unavailable_when_no_active_versionset(app_client):
    client, _ = app_client
    r = client.get("/v1/env")
    assert r.status_code == 200  # 降级为 200，不 5xx
    assert r.json() == {"demo_app": "unavailable"}


def test_env_unavailable_on_network_error(app_client):
    client, quality = app_client
    quality.fail_next = "network"
    r = client.get("/v1/env")
    assert r.status_code == 200
    assert r.json() == {"demo_app": "unavailable"}


@pytest.mark.parametrize(
    "malformed",
    [
        {"status": "active"},
        {
            "versionset_id": "vs_bad",
            "status": "active",
            "revision": 0,
            "digest": "not-a-digest",
        },
    ],
)
def test_env_unavailable_for_malformed_active_projection(app_client, malformed):
    client, quality = app_client
    quality.list_versionsets = lambda **_kwargs: {"items": [malformed]}  # type: ignore[method-assign]
    r = client.get("/v1/env")
    assert r.status_code == 200
    assert r.json() == {"demo_app": "unavailable"}


# ------------------------------------------------------------------ 2. GET /v1/trust/ledger


def test_trust_ledger_authoritative_empty_state(app_client):
    client, _ = app_client
    r = client.get("/v1/trust/ledger")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_trust_ledger_ignores_disconnected_mcp_shadow_table(app_client, sqlite_engine):
    _create_mcp_tables(sqlite_engine)
    with sqlite_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO mcp_trust_ledger
                  (risk_class, action_type, epoch, successes, trials, autonomy_state)
                VALUES ('R1_REVERSIBLE_WRITE', 'shadow_only', 1, 99, 99, 'AUTO_ENABLED')
                """
            )
        )
    client, _ = app_client
    r = client.get("/v1/trust/ledger")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_trust_ledger_computes_wilson_from_counts(app_client, sqlite_engine):
    with sqlite_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO trust_ledger
                  (risk_class, action_type, epoch, successes, trials, autonomy_state, payload, updated_at)
                VALUES
                  ('R2_HIGH_IMPACT', 'release_outcome', 1, 3, 3, 'MANUAL', '{"promotion_eligible": false}', CURRENT_TIMESTAMP),
                  ('R0_READ', 'case_list', 1, 10, 10, 'ELIGIBLE', '{"promotion_eligible": true}', CURRENT_TIMESTAMP)
                """
            )
        )
    client, _ = app_client
    r = client.get("/v1/trust/ledger")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2

    triage = next(i for i in items if i["action_type"] == "release_outcome")
    assert triage["successes"] == 3 and triage["trials"] == 3
    # 3/3 → Wilson 双侧 95% 下界 ≈ 0.438494（contracts/wilson 事实源）
    assert abs(triage["LB"] - 0.438494) < 1e-5
    assert triage["promotion_eligible"] is False
    assert triage["autonomy_state"] == "MANUAL"

    case_list = next(i for i in items if i["action_type"] == "case_list")
    assert case_list["LB"] > 0.7  # 10/10 → 下界 ≈ 0.722


# ------------------------------------------------------------------ 3. GET /v1/trust/denials


def test_trust_denials_authoritative_empty_state(app_client):
    client, _ = app_client
    r = client.get("/v1/trust/denials")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_trust_denials_ignores_mcp_shadow_audit(app_client, sqlite_engine):
    _create_mcp_tables(sqlite_engine)
    client, _ = app_client
    r = client.get("/v1/trust/denials")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_trust_denials_lists_real_release_outcomes(app_client, sqlite_engine):
    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=sqlite_engine)()
    try:
        service = TrustService(session)
        for index in range(3):
            service.record_outcome(
                source_event_id=f"evt_real_{index}",
                action_ref=f"rel_real_{index}",
                success=True,
                detail="operation-bound promote receipt",
            )
        session.commit()
    finally:
        session.close()
    client, _ = app_client
    r = client.get("/v1/trust/denials")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 3
    d = items[0]
    assert d["action_type"] == "release_outcome"
    assert d["risk_class"] == "R2_HIGH_IMPACT"
    assert d["result"] == "denied"
    assert d["successes"] == 3 and d["trials"] == 3
    assert "0.4385" in d["reason"]


# ------------------------------------------------------------------ 4. GET /v1/cases/{id}/events


def test_case_events_ordered_and_carries_evidence_refs(app_client):
    client, _ = app_client
    resp = client.post("/v1/complaints", json={"source": "webhook", "text": "手机屏碎了", "external_id": "msg-ev-1"})
    assert resp.status_code == 200
    case_id = resp.json()["case_id"]

    r = client.get(f"/v1/cases/{case_id}/events")
    assert r.status_code == 200
    body = r.json()
    assert body["case_id"] == case_id
    items = body["items"]
    assert len(items) >= 2
    seqs = [i["seq"] for i in items]
    assert seqs == sorted(seqs)
    assert items[0]["event_type"] == "complaint.received"
    # 事件 payload 里的证据引用字段被投影（complaint.received 带 text_ref）
    assert items[0]["evidence_refs"].get("text_ref") == f"inline:{case_id}"


def test_case_events_404_for_missing_case(app_client):
    client, _ = app_client
    r = client.get("/v1/cases/case_doesnotexist00000000000001/events")
    assert r.status_code == 404


# ------------------------------------------------------------------ 5. GET /v1/experiments/{id}?_view=full


def _new_running_experiment(client, case_id: str = "case_1234567890abcdef") -> str:
    resp = client.post("/v1/experiments", json={"case_id": case_id, "hypothesis_layer": "prompt"})
    assert resp.status_code == 200
    exp_id = resp.json()["experiment_id"]
    client.post(
        f"/v1/experiments/{exp_id}/protocol",
        json={
            "probe_set_digest": "sha256:" + "a" * 64,
            "discovery": ["p1"],
            "hidden_confirmation": ["p2"],
            "unaffected_controls": ["p3"],
            "repetitions": 1,
            "versions": {"prompt": "v1"},
            "random_seed_ref": "seed-1",
        },
    )
    client.post(
        f"/v1/experiments/{exp_id}/start",
        json={"runner_id": "runner-1", "lease_id": "lease-1", "fencing_token": 1},
    )
    return exp_id


def test_experiment_full_view_returns_cells_before_verdict(app_client):
    client, _ = app_client
    exp_id = _new_running_experiment(client)
    client.post(f"/v1/experiments/{exp_id}/cells", json={"cell": "C", "arm_order_index": 0, "recovery_rate": 0.0})
    client.post(f"/v1/experiments/{exp_id}/cells", json={"cell": "RP", "arm_order_index": 1, "recovery_rate": 0.6})

    r = client.get(f"/v1/experiments/{exp_id}", params={"_view": "full"})
    assert r.status_code == 200
    body = r.json()
    assert body["experiment_id"] == exp_id
    assert [c["cell"] for c in body["cells"]] == ["C", "RP"]
    assert [c["recovery_rate"] for c in body["cells"]] == [0.0, 0.6]
    assert body["deltas"] is None
    assert body["verdict"] is None
    assert body["attributed_layer"] is None
    assert body["confidence_intervals"] is None


def test_experiment_full_view_with_verdict_projection(app_client):
    client, _ = app_client
    exp_id = _new_running_experiment(client)
    for cell, idx, rate in [("C", 0, 0.0), ("RP", 1, 0.6), ("RK", 2, 0.5), ("RM", 3, 0.55), ("G", 4, 0.6)]:
        client.post(
            f"/v1/experiments/{exp_id}/cells",
            json={"cell": cell, "arm_order_index": idx, "recovery_rate": rate},
        )
    client.post(
        f"/v1/experiments/{exp_id}/verdict",
        json={
            "verdict": "ATTRIBUTED",
            "deltas": {"RP": 0.6, "RK": 0.5, "RM": 0.55, "G": 0.6},
            "evidence_bundle_ref": "bundle://e1",
            "report_ref": "eval://e1",
            "attributed_layer": "prompt",
        },
    )

    r = client.get(f"/v1/experiments/{exp_id}", params={"_view": "full"})
    assert r.status_code == 200
    body = r.json()
    assert body["verdict"] == "ATTRIBUTED"
    assert body["attributed_layer"] == "prompt"
    assert body["deltas"] == {"RP": 0.6, "RK": 0.5, "RM": 0.55, "G": 0.6}
    assert body["evidence_bundle_ref"] == "bundle://e1"
    assert body["report_ref"] == "eval://e1"
    assert len(body["cells"]) == 5
    assert body["confidence_intervals"] is None  # 无数据字段 → null 不报错


def test_experiment_full_view_404_for_missing(app_client):
    client, _ = app_client
    r = client.get("/v1/experiments/exp_doesnotexist00000000001", params={"_view": "full"})
    assert r.status_code == 404


def test_experiment_default_view_unchanged(app_client):
    """不带 _view 时仍返回原聚合视图（回归防护）。"""
    client, _ = app_client
    exp_id = _new_running_experiment(client)
    r = client.get(f"/v1/experiments/{exp_id}")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"experiment_id", "state", "revision", "payload"}
    assert "cells" not in body


# ------------------------------------------------------------------ 6. GET /v1/workorders


def _new_awaiting_approval_changeset(client, seed: int = 1) -> tuple[str, dict]:
    workorder_id = f"wo_{seed:012d}"
    wo = make_workorder(
        workorder_id=workorder_id,
        nonce=f"00000000-0000-0000-0000-{seed:012d}",
        case_id="case_1234567890abcdef",
    )
    report = make_gate_report(workorder_id, eval_id=f"eval_readview{seed:012d}")
    report_hash = canonical_json_digest(report, prefix=False)
    wo["gate_report_ref"] = {
        "uri": f"eval://{report['eval_id']}",
        "digest": f"sha256:{report_hash}",
    }
    wo["hash"] = workorder_hash(wo)

    gate = client.post(
        "/v1/gate-reports",
        json={
            "report": report,
            "report_hash": report_hash,
            "workorder_id": workorder_id,
            "target_versionset_id": "vs_demo001fixedversionset01",
            "target_revision": 1,
            "dataset_id": "customer-service-regression",
            "dataset_version": "1.0.0",
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
        },
    )
    assert gate.status_code == 200, gate.text
    registered = client.post("/v1/workorders", json=wo)
    assert registered.status_code == 200, registered.text
    cs_id = f"cs_{workorder_id}"
    attached = client.post(
        f"/v1/changesets/{cs_id}/gate",
        json={"eval_id": report["eval_id"], "report_hash": report_hash},
    )
    assert attached.status_code == 200, attached.text
    requested = client.post(
        f"/v1/changesets/{cs_id}/approval-request",
        json={
            "workorder_hash": wo["hash"],
            "nonce": wo["nonce"],
            "expiry": "2099-01-01T00:00:00+00:00",
            "channel": "feishu",
        },
    )
    assert requested.status_code == 200, requested.text
    return cs_id, wo


def test_workorders_empty(app_client):
    client, _ = app_client
    r = client.get("/v1/workorders")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_workorders_list_immutable_rows_with_changeset_lifecycle_metadata(app_client):
    client, _ = app_client
    cs_id, expected = _new_awaiting_approval_changeset(client)

    r = client.get("/v1/workorders")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    wo = items[0]
    assert wo["changeset_id"] == cs_id
    assert wo["workorder_id"] == "wo_000000000001"
    assert wo["hash"] == expected["hash"]
    assert wo["freeze_at"] == "2099-01-01T00:00:00+00:00"  # approval_requested 的 expiry
    assert wo["requester"] == "repairer-1"  # changeset.drafted 事件 author_agent
    assert wo["channel"] == "prompt"
    assert wo["nonce"] == expected["nonce"]
    assert wo["state"] == "AWAITING_APPROVAL"
    assert "payload" not in wo
    assert wo["projection_warning"] is None
    assert wo["workorder_integrity_status"] == "verified"
    assert wo["gate_integrity_status"] == "verified"
    assert wo["gate_target_revision"] == 1
    assert wo["gate_target_versionset_id"] == "vs_demo001fixedversionset01"
    assert wo["gate_binding_digest"].startswith("sha256:")


def test_workorders_do_not_trust_tampered_changeset_hash(app_client, sqlite_session):
    client, _ = app_client
    cs_id, expected = _new_awaiting_approval_changeset(client)
    changeset = sqlite_session.get(
        Aggregate,
        {"aggregate_type": "changeset", "aggregate_id": cs_id},
    )
    assert changeset is not None
    changeset.payload = {**(changeset.payload or {}), "workorder_hash": "0" * 64}
    sqlite_session.commit()

    item = client.get("/v1/workorders").json()["items"][0]
    assert item["hash"] == expected["hash"]
    assert item["projection_warning"] == "changeset_hash_mismatch"


def test_workorders_hide_tampered_immutable_payload(app_client, sqlite_session):
    client, _ = app_client
    _, expected = _new_awaiting_approval_changeset(client)
    row = sqlite_session.get(WorkOrder, expected["workorder_id"])
    assert row is not None
    row.payload = {**row.payload, "created_by": "attacker"}
    sqlite_session.commit()

    item = client.get("/v1/workorders").json()["items"][0]
    assert item["workorder_integrity_status"] == "integrity_error"
    assert item["workorder_integrity_error"] == "workorder_hash_mismatch"
    assert item["gate_integrity_status"] == "integrity_error"
    assert "payload" not in item
    assert item["nonce"] is None
    assert item["target_versionset_digest"] is None


def test_workorders_fail_closed_when_projection_columns_are_tampered(app_client, sqlite_session):
    client, _ = app_client
    _, expected = _new_awaiting_approval_changeset(client)
    row = sqlite_session.get(WorkOrder, expected["workorder_id"])
    assert row is not None
    row.case_id = "case_attacker"
    row.channel = "model"
    sqlite_session.commit()

    item = client.get("/v1/workorders").json()["items"][0]
    assert item["workorder_integrity_status"] == "integrity_error"
    assert item["workorder_integrity_error"] == "workorder_projection_mismatch"
    assert item["case_id"] is None
    assert item["channel"] == "UNKNOWN"
    assert item["state"] == "UNKNOWN"
    assert "payload" not in item


def test_workorders_multi_changeset(app_client):
    client, _ = app_client
    _new_awaiting_approval_changeset(client, seed=1)
    _new_awaiting_approval_changeset(client, seed=2)
    r = client.get("/v1/workorders")
    assert r.status_code == 200
    assert len(r.json()["items"]) == 2


# ------------------------------------------------------------------ 7. GET /v1/gates


def test_gates_authoritative_empty_state_without_mcp_tables(app_client):
    client, _ = app_client
    r = client.get("/v1/gates")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_gates_empty_state(app_client, sqlite_engine):
    _create_mcp_tables(sqlite_engine)
    client, _ = app_client
    r = client.get("/v1/gates")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert "warning" not in body


def test_gates_projects_dual_track_report(app_client, sqlite_engine):
    del sqlite_engine  # authoritative gates no longer read the MCP projection table
    client, _ = app_client
    report = make_gate_report("wo_000000000001", eval_id="eval_readviewgate1")
    report_hash = canonical_json_digest(report, prefix=False)
    registered = client.post(
        "/v1/gate-reports",
        json={
            "report": report,
            "report_hash": report_hash,
            "workorder_id": "wo_000000000001",
            "target_versionset_id": "vs_demo001fixedversionset01",
            "target_revision": 1,
            "dataset_id": "customer-service-regression",
            "dataset_version": "1.0.0",
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
        },
    )
    assert registered.status_code == 200, registered.text
    r = client.get("/v1/gates")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    g = items[0]
    assert g["eval_id"] == "eval_readviewgate1"
    assert g["report_id"] == report["report_id"]
    assert g["rule_track"] == "passed"
    assert g["judge_track"] == "passed"
    assert g["deterministic_tests"] == "passed"
    assert g["live_provider_e2e"] == "passed"
    assert g["verdict"] == "passed"
    assert g["report_hash"] == report_hash
    assert g["status"] == "unbound"
    assert g["binding_status"] == "UNBOUND"
    assert g["binding_error"] is None

    evidence = client.get("/v1/evidence").json()["items"]
    gate_item = next(item for item in evidence if item["source_type"] == "gate")
    assert gate_item["kind"] == "gate_report_binding"
    assert gate_item["binding_status"] == "UNKNOWN"
    assert gate_item["integrity_error"] == "gate_unbound"
    assert not any(item["kind"].startswith("artifact_ref_") for item in evidence)


def test_gates_marks_workorder_bound_report_verified(app_client):
    client, _ = app_client
    _, expected = _new_awaiting_approval_changeset(client)

    item = client.get("/v1/gates").json()["items"][0]
    assert item["workorder_id"] == expected["workorder_id"]
    assert item["status"] == "completed"
    assert item["binding_status"] == "VERIFIED"
    assert item["binding_error"] is None
    assert item["verdict"] == "passed"


def test_gates_fail_closed_when_binding_digest_is_tampered(app_client, sqlite_session):
    client, _ = app_client
    _changeset_id, expected = _new_awaiting_approval_changeset(client)
    eval_id = expected["gate_report_ref"]["uri"].removeprefix("eval://")
    row = sqlite_session.get(GateReportRecord, eval_id)
    assert row is not None
    row.binding_digest = "sha256:" + "0" * 64
    sqlite_session.commit()

    item = client.get("/v1/gates").json()["items"][0]
    assert item["status"] == "integrity_error"
    assert item["binding_status"] == "UNKNOWN"
    assert item["binding_error"] == "hash_mismatch"
    assert item["verdict"] == "error"
    assert "passed" not in item.values()


def test_gates_fail_closed_when_persisted_report_is_tampered(app_client, sqlite_session):
    client, _ = app_client
    report = make_gate_report("wo_000000000002", eval_id="eval_readviewtampered1")
    report_hash = canonical_json_digest(report, prefix=False)
    registered = client.post(
        "/v1/gate-reports",
        json={
            "report": report,
            "report_hash": report_hash,
            "workorder_id": "wo_000000000002",
            "target_versionset_id": "vs_demo001fixedversionset01",
            "target_revision": 1,
            "dataset_id": "customer-service-regression",
            "dataset_version": "1.0.0",
            "evidence_digest": canonical_json_digest(report["artifact_refs"]),
        },
    )
    assert registered.status_code == 200, registered.text

    row = sqlite_session.get(GateReportRecord, report["eval_id"])
    assert row is not None
    row.report = {**row.report, "overall_status": "failed"}
    sqlite_session.commit()

    response = client.get("/v1/gates")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["eval_id"] == report["eval_id"]
    assert item["status"] == "integrity_error"
    assert item["integrity_error"] == "hash_mismatch"
    assert item["verdict"] == "error"
    assert item["rule_track"] == "error"
    assert "passed" not in item.values()


# ------------------------------------------------------------------ 8. GET /v1/evidence


def test_evidence_projects_recorded_refs_without_claiming_artifact_verified(app_client):
    client, _ = app_client
    complaint = client.post(
        "/v1/complaints",
        json={"source": "webhook", "text": "evidence projection", "external_id": "evidence-1"},
    )
    assert complaint.status_code == 200
    case_id = complaint.json()["case_id"]

    response = client.get("/v1/evidence", params={"case_id": case_id})
    assert response.status_code == 200
    body = response.json()
    assert body["artifact_store"] == "unavailable"
    assert body["warning"] == "artifact_content_unavailable"
    text_ref = next(item for item in body["items"] if item["kind"] == "text_ref")
    assert text_ref["case_id"] == case_id
    assert text_ref["reference"] == f"inline:{case_id}"
    assert text_ref["binding_status"] == "REFERENCE_RECORDED"
    assert text_ref["artifact_status"] == "UNKNOWN"
    assert not any(item["kind"] == "complainant_ref" for item in body["items"])
    assert not any(item.get("artifact_status") == "VERIFIED" for item in body["items"])
    assert all("value" not in item for item in body["items"])


def test_evidence_includes_bound_workorder_and_gate_refs(app_client):
    client, _ = app_client
    _, expected = _new_awaiting_approval_changeset(client)
    response = client.get("/v1/evidence", params={"case_id": expected["case_id"]})
    assert response.status_code == 200
    items = response.json()["items"]
    gate_ref = next(
        item
        for item in items
        if item["source_type"] == "workorder" and item["kind"] == "gate_report_ref"
    )
    assert gate_ref["reference"].startswith("eval://")
    assert gate_ref["digest"].startswith("sha256:")
    assert gate_ref["binding_status"] == "BOUND"
    assert gate_ref["artifact_status"] == "UNKNOWN"
    gate_digest = next(
        item
        for item in items
        if item["source_type"] == "gate" and item["kind"] == "evidence_digest"
    )
    assert gate_digest["case_id"] == expected["case_id"]
    assert gate_digest["integrity_status"] == "recorded"
    assert all("value" not in item for item in items)
    assert "fix output format" not in str(items)


def test_evidence_rejects_malformed_digest_claim(app_client, sqlite_session):
    client, _ = app_client
    complaint = client.post(
        "/v1/complaints",
        json={"source": "webhook", "text": "digest check", "external_id": "bad-digest-1"},
    )
    case_id = complaint.json()["case_id"]
    event = sqlite_session.scalar(
        select(Event).where(
            Event.aggregate_id == case_id,
            Event.event_type == "complaint.received",
        )
    )
    assert event is not None
    event.payload = {**event.payload, "evidence_digest": "not-a-sha256-digest"}
    sqlite_session.commit()

    items = client.get("/v1/evidence", params={"case_id": case_id}).json()["items"]
    digest = next(item for item in items if item["kind"] == "evidence_digest")
    assert digest["digest"] is None
    assert digest["binding_status"] == "UNKNOWN"
    assert digest["integrity_status"] == "invalid_digest"


def test_evidence_fail_closed_for_tampered_gate_report(app_client, sqlite_session):
    client, _ = app_client
    _, expected = _new_awaiting_approval_changeset(client)
    eval_id = expected["gate_report_ref"]["uri"].removeprefix("eval://")
    row = sqlite_session.get(GateReportRecord, eval_id)
    assert row is not None
    row.report = {**row.report, "overall_status": "failed"}
    sqlite_session.commit()

    items = client.get("/v1/evidence").json()["items"]
    gate_items = [item for item in items if item["source_type"] == "gate"]
    assert len(gate_items) == 1
    assert gate_items[0]["kind"] == "gate_report_integrity"
    assert gate_items[0]["case_id"] == expected["case_id"]
    assert gate_items[0]["binding_status"] == "UNKNOWN"
    assert gate_items[0]["integrity_status"] == "source_integrity_error"
    assert gate_items[0]["integrity_error"] == "hash_mismatch"
    assert not any(item["kind"].startswith("artifact_ref_") for item in gate_items)

    workorder_gate = next(
        item
        for item in items
        if item["source_type"] == "workorder" and item["kind"] == "gate_report_ref"
    )
    assert workorder_gate["binding_status"] == "UNKNOWN"
    assert workorder_gate["integrity_status"] == "source_integrity_error"
