"""状态机迁移表单元测试（对齐 contracts/events/state-machines.yaml）。"""
import pytest

from app.services.state_machines import (
    CASE_INITIAL,
    CHANGESET_INITIAL,
    EXPERIMENT_INITIAL,
    NOTIFICATION_INITIAL,
    RELEASE_INITIAL,
    IllegalTransition,
    initial_state,
    next_state,
)


def test_initial_states():
    assert initial_state("case") == CASE_INITIAL == "RECEIVED"
    assert initial_state("experiment") == EXPERIMENT_INITIAL == "REQUESTED"
    assert initial_state("changeset") == CHANGESET_INITIAL == "DRAFTED"
    assert initial_state("release") == RELEASE_INITIAL == "REQUESTED"
    assert initial_state("notification") == NOTIFICATION_INITIAL == "QUEUED"
    with pytest.raises(ValueError):
        initial_state("nope")


def test_case_promote_path():
    # RECEIVED → OPEN → DISPATCHED → ATTRIBUTING → AWAITING_FIX → AWAITING_APPROVAL → RELEASING → NOTIFYING → CLOSED
    s = next_state("case", "RECEIVED", "case.opened")
    assert s == "OPEN"
    s = next_state("case", s, "case.dispatched")
    assert s == "DISPATCHED"
    s = next_state("case", s, "experiment.requested")
    assert s == "ATTRIBUTING"
    s = next_state("case", s, "case.attribution_completed", guard="verdict=ATTRIBUTED")
    assert s == "AWAITING_FIX"
    s = next_state("case", s, "changeset.approval_requested")
    assert s == "AWAITING_APPROVAL"
    s = next_state("case", s, "changeset.approved")
    assert s == "RELEASING"
    s = next_state("case", s, "case.resolved")
    assert s == "NOTIFYING"
    s = next_state("case", s, "case.closed")
    assert s == "CLOSED"


def test_case_escalation_is_global():
    for state in ("OPEN", "DISPATCHED", "ATTRIBUTING", "AWAITING_FIX", "RELEASING", "NOTIFYING"):
        assert next_state("case", state, "case.escalated") == "ESCALATED"


def test_case_attribution_requires_guard():
    # 带 guard 的迁移不显式给 guard → 拒绝（严格语义）
    with pytest.raises(IllegalTransition):
        next_state("case", "ATTRIBUTING", "case.attribution_completed")


def test_case_worker_lost():
    assert next_state("case", "DISPATCHED", "case.worker_lost") == "OPEN"


def test_illegal_transition():
    with pytest.raises(IllegalTransition):
        next_state("case", "OPEN", "case.attribution_completed")
    with pytest.raises(IllegalTransition):
        next_state("case", "CLOSED", "case.opened")


def test_release_machine():
    s = next_state("release", "REQUESTED", "release.staged")
    assert s == "STAGING"
    s = next_state("release", s, "release.canary_started")
    assert s == "CANARYING"
    s = next_state("release", s, "release.verification_completed")
    assert s == "VERIFYING"
    s = next_state("release", s, "release.promoted", guard="verification=passed")
    assert s == "COMPLETED"


def test_release_rollback_requires_guard():
    with pytest.raises(IllegalTransition):
        next_state("release", "VERIFYING", "release.rollback_started")
    assert next_state("release", "VERIFYING", "release.rollback_started", guard="verification=failed") == "ROLLING_BACK"


def test_release_unknown_reconcile():
    for st in ("REQUESTED", "STAGING", "CANARYING", "VERIFYING", "PROMOTING", "ROLLING_BACK"):
        assert next_state("release", st, "release.unknown_detected") == "UNKNOWN"
    assert next_state("release", "UNKNOWN", "release.reconciled", guard="action=resume") == "REQUESTED"
    assert next_state("release", "UNKNOWN", "release.reconciled", guard="action=apply_canary") == "STAGING"
    assert next_state("release", "UNKNOWN", "release.reconciled", guard="action=confirm_promote") == "VERIFYING"
    assert next_state("release", "UNKNOWN", "release.reconciled", guard="action=compensate") == "ROLLING_BACK"
    assert next_state("release", "UNKNOWN", "release.rollback_failed") == "FAILED_ESCALATED"


def test_experiment_machine():
    s = next_state("experiment", "REQUESTED", "experiment.protocol_frozen")
    assert s == "PROTOCOL_FROZEN"
    s = next_state("experiment", s, "experiment.started")
    assert s == "RUNNING"
    s = next_state("experiment", s, "experiment.cell_completed")  # 自迁移：状态不变
    assert s == "RUNNING"
    s = next_state("experiment", s, "experiment.verdict_computed")
    assert s == "VERDICT_COMPUTED"
    s = next_state("experiment", s, "experiment.escalated_full_factorial", guard="verdict=CONFOUNDED")
    assert s == "PROTOCOL_FROZEN"


def test_experiment_cancel_global():
    assert next_state("experiment", "RUNNING", "experiment.cancelled") == "CANCELLED"


def test_changeset_machine():
    s = next_state("changeset", "DRAFTED", "changeset.gate_attached")
    assert s == "GATE_ATTACHED"
    s = next_state("changeset", s, "changeset.approval_requested")
    assert s == "AWAITING_APPROVAL"
    s = next_state("changeset", s, "changeset.approved")
    assert s == "APPROVED"
    s = next_state("changeset", s, "changeset.committed")
    assert s == "COMMITTED"


def test_notification_machine():
    assert next_state("notification", "QUEUED", "notification.sent") == "SENT"
    assert next_state("notification", "QUEUED", "notification.failed", guard="retryable=true") == "RETRYING"
    assert next_state("notification", "QUEUED", "notification.failed", guard="retryable=false") == "DEAD_LETTERED"
    s = next_state("notification", "RETRYING", "notification.retry_scheduled")
    assert s == "QUEUED"
    assert next_state("notification", "RETRYING", "notification.dead_lettered") == "DEAD_LETTERED"


def test_unknown_machine():
    with pytest.raises(IllegalTransition):
        next_state("wat", "X", "y.z")
