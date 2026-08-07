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
