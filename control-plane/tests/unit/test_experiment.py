"""Experiment（归因对照实验）状态机服务单元测试。"""
import pytest

from app.config import Settings
from app.services.experiment_service import ExperimentService, ExperimentServiceError
from app.services.state_machines import IllegalTransition


def _svc(session) -> ExperimentService:
    return ExperimentService(session, Settings())


def test_experiment_lifecycle(sqlite_session):
    svc = _svc(sqlite_session)
    r = svc.create(case_id="case_x", hypothesis_layer="prompt")
    eid = r["experiment_id"]
    assert r["state"] == "REQUESTED"

    r = svc.freeze_protocol(
        eid,
        probe_set_digest="sha256:" + "1" * 64,
        discovery=["p1"],
        hidden_confirmation=["p2"],
        unaffected_controls=["p3"],
        repetitions=5,
        versions={"P0": "v0", "P1": "v1", "K0": "v0", "K1": "v1", "M0": "v0", "M1": "v1"},
        random_seed_ref="seed:1",
    )
    assert r["state"] == "PROTOCOL_FROZEN"

    r = svc.start(eid, runner_id="runner-1", lease_id="lease-1", fencing_token=7)
    assert r["state"] == "RUNNING"

    for cell, i in (("C", 0), ("RP", 1), ("RK", 2), ("RM", 3), ("G", 4)):
        r = svc.cell_completed(eid, cell=cell, arm_order_index=i, recovery_rate=0.8)
        assert r["state"] == "RUNNING"  # 自迁移

    r = svc.verdict_computed(
        eid,
        verdict="ATTRIBUTED",
        deltas={"prompt": 0.5, "kb": 0.0, "model_params": 0.0},
        evidence_bundle_ref="eb://1",
        report_ref="rep://1",
        attributed_layer="prompt",
    )
    assert r["state"] == "VERDICT_COMPUTED"
    assert (r["payload"] or {}).get("verdict") == "ATTRIBUTED"


def test_invalid_cell_rejected(sqlite_session):
    svc = _svc(sqlite_session)
    eid = svc.create(case_id="case_x")["experiment_id"]
    with pytest.raises(ExperimentServiceError) as exc:
        svc.cell_completed(eid, cell="NOPE", arm_order_index=0, recovery_rate=0.5)
    assert exc.value.code == "validation_failed"


def test_escalate_full_factorial_requires_confounded_guard(sqlite_session):
    svc = _svc(sqlite_session)
    eid = svc.create(case_id="case_x")["experiment_id"]
    # 未到 VERDICT_COMPUTED 直接 escalate → 状态机拒绝（服务透传 IllegalTransition）
    with pytest.raises(IllegalTransition):
        svc.escalate_full_factorial(eid, reason="confounded_control")


def test_cancel(sqlite_session):
    svc = _svc(sqlite_session)
    eid = svc.create(case_id="case_x")["experiment_id"]
    r = svc.cancel(eid, reason="case merged")
    assert r["state"] == "CANCELLED"


# ---------- S0-006：冻结协议领域校验（空探针集不得冻结） ----------


def test_freeze_protocol_rejects_empty_discovery(sqlite_session):
    svc = _svc(sqlite_session)
    eid = svc.create(case_id="case_x")["experiment_id"]
    with pytest.raises(ExperimentServiceError) as exc:
        svc.freeze_protocol(
            eid,
            probe_set_digest="sha256:" + "1" * 64,
            discovery=[],
            hidden_confirmation=["p2"],
            unaffected_controls=["p3"],
            repetitions=5,
            versions={},
            random_seed_ref="seed:1",
        )
    assert exc.value.code == "validation_error"
    assert "discovery" in exc.value.message


def test_freeze_protocol_rejects_empty_hidden_confirmation(sqlite_session):
    svc = _svc(sqlite_session)
    eid = svc.create(case_id="case_x")["experiment_id"]
    with pytest.raises(ExperimentServiceError) as exc:
        svc.freeze_protocol(
            eid,
            probe_set_digest="sha256:" + "1" * 64,
            discovery=["p1"],
            hidden_confirmation=[],
            unaffected_controls=["p3"],
            repetitions=5,
            versions={},
            random_seed_ref="seed:1",
        )
    assert exc.value.code == "validation_error"
    assert "hidden_confirmation" in exc.value.message


def test_freeze_protocol_rejects_empty_unaffected_controls(sqlite_session):
    svc = _svc(sqlite_session)
    eid = svc.create(case_id="case_x")["experiment_id"]
    with pytest.raises(ExperimentServiceError) as exc:
        svc.freeze_protocol(
            eid,
            probe_set_digest="sha256:" + "1" * 64,
            discovery=["p1"],
            hidden_confirmation=["p2"],
            unaffected_controls=[],
            repetitions=5,
            versions={},
            random_seed_ref="seed:1",
        )
    assert exc.value.code == "validation_error"
    assert "unaffected_controls" in exc.value.message


def test_freeze_protocol_empty_probe_set_via_api_returns_400(app_client):
    """纵深防御：HTTP 层对空 discovery 冻结返回 400 + code=validation_error。"""
    client, _ = app_client
    r = client.post("/v1/experiments", json={"case_id": "case_x"})
    assert r.status_code == 200
    eid = r.json()["experiment_id"]
    resp = client.post(
        f"/v1/experiments/{eid}/protocol",
        json={
            "probe_set_digest": "sha256:" + "1" * 64,
            "discovery": [],
            "hidden_confirmation": ["p2"],
            "unaffected_controls": ["p3"],
            "repetitions": 5,
            "versions": {},
            "random_seed_ref": "seed:1",
        },
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "validation_error"
    assert "discovery" in detail["message"]
