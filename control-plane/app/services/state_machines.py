"""状态机迁移表（权威：contracts/events/state-machines.yaml）。

实现七个状态机（case / experiment / changeset / eval / release / notification / trust）。
纪律：状态权威源 = 控制面数据库（PG aggregate/event）；LLM 永远不是状态权威源。
guard 语义：带 guard 的迁移必须由调用方显式给出匹配的 guard，否则拒绝（防止走错分支）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class IllegalTransition(Exception):
    def __init__(self, machine: str, from_state: str, event: str, message: str = ""):
        self.machine = machine
        self.from_state = from_state
        self.event = event
        super().__init__(message or f"illegal transition: {machine} {from_state} --{event}--> ?")


@dataclass(frozen=True)
class Transition:
    from_state: str
    event: str
    to_state: str
    guard: Optional[str] = None


# ---------- Case ----------
CASE_STATES = {
    "RECEIVED",
    "OPEN",
    "DISPATCHED",
    "ATTRIBUTING",
    "AWAITING_FIX",
    "AWAITING_APPROVAL",
    "RELEASING",
    "NOTIFYING",
    "ESCALATED",
    "CLOSED",
    "MERGED",
    "DUPLICATE_DISMISSED",
}
CASE_INITIAL = "RECEIVED"
CASE_TERMINAL = {"CLOSED", "MERGED", "DUPLICATE_DISMISSED"}

CASE_TRANSITIONS: list[Transition] = [
    Transition("RECEIVED", "case.opened", "OPEN"),
    Transition("RECEIVED", "case.duplicate_dismissed", "DUPLICATE_DISMISSED"),
    Transition("OPEN", "case.merged", "MERGED"),
    Transition("OPEN", "case.dispatched", "DISPATCHED"),
    Transition("DISPATCHED", "case.worker_lost", "OPEN"),
    Transition("DISPATCHED", "experiment.requested", "ATTRIBUTING"),
    Transition("ATTRIBUTING", "case.attribution_completed", "AWAITING_FIX", guard="verdict=ATTRIBUTED"),
    Transition("ATTRIBUTING", "case.escalated", "ESCALATED"),
    Transition("AWAITING_FIX", "changeset.approval_requested", "AWAITING_APPROVAL"),
    Transition("AWAITING_APPROVAL", "changeset.approved", "RELEASING"),
    Transition("AWAITING_APPROVAL", "changeset.rejected", "AWAITING_FIX"),
    Transition("AWAITING_APPROVAL", "changeset.expired", "AWAITING_FIX"),
    Transition("RELEASING", "case.resolved", "NOTIFYING"),
    Transition("RELEASING", "release.rollback_failed", "ESCALATED"),
    Transition("NOTIFYING", "notification.sent", "CLOSED"),
    Transition("NOTIFYING", "notification.dead_lettered", "ESCALATED"),
    # 全局人工接管
    Transition("*", "case.escalated", "ESCALATED"),
]

# ---------- Experiment ----------
EXPERIMENT_STATES = {
    "REQUESTED",
    "PROTOCOL_FROZEN",
    "RUNNING",
    "ANALYZING",
    "VERDICT_COMPUTED",
    "CANCELLED",
}
EXPERIMENT_INITIAL = "REQUESTED"
EXPERIMENT_TERMINAL = {"VERDICT_COMPUTED", "CANCELLED"}

EXPERIMENT_TRANSITIONS: list[Transition] = [
    Transition("REQUESTED", "experiment.protocol_frozen", "PROTOCOL_FROZEN"),
    Transition("PROTOCOL_FROZEN", "experiment.started", "RUNNING"),
    Transition("RUNNING", "experiment.runner_lost", "PROTOCOL_FROZEN"),
    Transition("RUNNING", "experiment.cell_completed", "RUNNING"),
    Transition("RUNNING", "experiment.verdict_computed", "VERDICT_COMPUTED"),
    Transition("VERDICT_COMPUTED", "experiment.escalated_full_factorial", "PROTOCOL_FROZEN", guard="verdict=CONFOUNDED"),
    Transition("*", "experiment.cancelled", "CANCELLED"),
]

# ---------- ChangeSet ----------
CHANGESET_STATES = {
    "DRAFTED",
    "GATE_ATTACHED",
    "AWAITING_APPROVAL",
    "APPROVED",
    "COMMITTED",
    "REJECTED",
    "EXPIRED",
    "SUPERSEDED",
}
CHANGESET_INITIAL = "DRAFTED"
CHANGESET_TERMINAL = {"COMMITTED", "REJECTED", "EXPIRED", "SUPERSEDED"}

CHANGESET_TRANSITIONS: list[Transition] = [
    Transition("DRAFTED", "changeset.gate_attached", "GATE_ATTACHED"),
    Transition("DRAFTED", "changeset.drafted", "SUPERSEDED"),
    Transition("GATE_ATTACHED", "changeset.approval_requested", "AWAITING_APPROVAL"),
    Transition("AWAITING_APPROVAL", "changeset.approved", "APPROVED"),
    Transition("AWAITING_APPROVAL", "changeset.rejected", "REJECTED"),
    Transition("AWAITING_APPROVAL", "changeset.expired", "EXPIRED"),
    Transition("APPROVED", "changeset.committed", "COMMITTED"),
    Transition("APPROVED", "changeset.expired", "EXPIRED"),
]

# ---------- Eval ----------
EVAL_STATES = {"REQUESTED", "RUNNING", "PASSED", "FAILED"}
EVAL_INITIAL = "REQUESTED"
EVAL_TERMINAL = {"PASSED", "FAILED"}

EVAL_TRANSITIONS: list[Transition] = [
    Transition("REQUESTED", "eval.started", "RUNNING"),
    Transition("RUNNING", "eval.rule_track_completed", "RUNNING"),
    Transition("RUNNING", "eval.judge_track_completed", "RUNNING"),
    Transition("RUNNING", "eval.passed", "PASSED"),
    Transition("RUNNING", "eval.failed", "FAILED"),
    Transition("RUNNING", "eval.error", "REQUESTED", guard="retryable=true"),
    Transition("RUNNING", "eval.error", "FAILED", guard="retryable=false"),
]

# ---------- Release ----------
RELEASE_STATES = {
    "REQUESTED",
    "STAGING",
    "CANARYING",
    "VERIFYING",
    "PROMOTING",
    "COMPLETED",
    "ROLLING_BACK",
    "ROLLED_BACK",
    "UNKNOWN",
    "FAILED_ESCALATED",
}
RELEASE_INITIAL = "REQUESTED"
RELEASE_TERMINAL = {"COMPLETED", "ROLLED_BACK", "FAILED_ESCALATED"}

RELEASE_TRANSITIONS: list[Transition] = [
    Transition("REQUESTED", "release.staged", "STAGING"),
    Transition("STAGING", "release.canary_started", "CANARYING"),
    Transition("CANARYING", "release.verification_completed", "VERIFYING"),
    Transition("VERIFYING", "release.promoted", "COMPLETED", guard="verification=passed"),
    Transition("VERIFYING", "release.rollback_started", "ROLLING_BACK", guard="verification=failed"),
    Transition("PROMOTING", "release.promoted", "COMPLETED"),
    Transition("ROLLING_BACK", "release.rolled_back", "ROLLED_BACK"),
    Transition("ROLLING_BACK", "release.rollback_failed", "FAILED_ESCALATED"),
    Transition("REQUESTED", "release.unknown_detected", "UNKNOWN"),
    Transition("STAGING", "release.unknown_detected", "UNKNOWN"),
    Transition("CANARYING", "release.unknown_detected", "UNKNOWN"),
    Transition("VERIFYING", "release.unknown_detected", "UNKNOWN"),
    Transition("PROMOTING", "release.unknown_detected", "UNKNOWN"),
    Transition("ROLLING_BACK", "release.unknown_detected", "UNKNOWN"),
    # reconcile 多目标：由 guard=action:X 选择
    Transition("UNKNOWN", "release.reconciled", "REQUESTED", guard="action=resume"),
    Transition("UNKNOWN", "release.reconciled", "STAGING", guard="action=apply_canary"),
    Transition("UNKNOWN", "release.reconciled", "VERIFYING", guard="action=confirm_promote"),
    Transition("UNKNOWN", "release.reconciled", "ROLLING_BACK", guard="action=compensate"),
    Transition("UNKNOWN", "release.rollback_failed", "FAILED_ESCALATED"),
]

# ---------- Notification ----------
NOTIFICATION_STATES = {"QUEUED", "SENDING", "RETRYING", "SENT", "DEAD_LETTERED"}
NOTIFICATION_INITIAL = "QUEUED"
NOTIFICATION_TERMINAL = {"SENT", "DEAD_LETTERED"}

NOTIFICATION_TRANSITIONS: list[Transition] = [
    Transition("QUEUED", "notification.sent", "SENT"),
    Transition("QUEUED", "notification.failed", "RETRYING", guard="retryable=true"),
    # 语义补全（state-machines.yaml failure_semantics：不可重试错误直接死信）
    Transition("QUEUED", "notification.failed", "DEAD_LETTERED", guard="retryable=false"),
    Transition("SENDING", "notification.sent", "SENT"),
    Transition("SENDING", "notification.failed", "RETRYING", guard="retryable=true"),
    Transition("SENDING", "notification.failed", "DEAD_LETTERED", guard="retryable=false"),
    Transition("RETRYING", "notification.retry_scheduled", "QUEUED"),
    Transition("RETRYING", "notification.dead_lettered", "DEAD_LETTERED"),
]

# ---------- Trust ----------
TRUST_STATES = {
    "MANUAL",
    "ELIGIBLE",
    "AWAITING_CONFIRMATION",
    "AUTO_ENABLED",
    "SUSPENDED",
    "BLOCKED_UNKNOWN",
}
TRUST_INITIAL = "MANUAL"

TRUST_TRANSITIONS: list[Transition] = [
    Transition("MANUAL", "trust.evidence_recorded", "ELIGIBLE", guard="r1_whitelist"),
    Transition("ELIGIBLE", "trust.evidence_recorded", "ELIGIBLE", guard="below_threshold"),
    Transition("ELIGIBLE", "trust.promotion_proposed", "AWAITING_CONFIRMATION", guard="wilson_lower>0.9"),
    Transition("AWAITING_CONFIRMATION", "trust.promotion_confirmed", "AUTO_ENABLED"),
    Transition("AWAITING_CONFIRMATION", "trust.promotion_rejected_by_human", "ELIGIBLE"),
    Transition("AUTO_ENABLED", "trust.suspended", "SUSPENDED", guard="verification_failed"),
    Transition("AUTO_ENABLED", "trust.blocked_unknown", "BLOCKED_UNKNOWN"),
    Transition("SUSPENDED", "trust.reinstated", "ELIGIBLE"),
    Transition("BLOCKED_UNKNOWN", "trust.reinstated", "MANUAL"),
    Transition("ELIGIBLE", "trust.blocked_unknown", "BLOCKED_UNKNOWN"),
]

# 初始状态查表（首事件懒创建聚合时使用）
INITIAL_STATES = {
    "case": CASE_INITIAL,
    "experiment": EXPERIMENT_INITIAL,
    "changeset": CHANGESET_INITIAL,
    "eval": EVAL_INITIAL,
    "release": RELEASE_INITIAL,
    "notification": NOTIFICATION_INITIAL,
    "trust": TRUST_INITIAL,
}

_MACHINES: dict[str, list[Transition]] = {
    "case": CASE_TRANSITIONS,
    "experiment": EXPERIMENT_TRANSITIONS,
    "changeset": CHANGESET_TRANSITIONS,
    "eval": EVAL_TRANSITIONS,
    "release": RELEASE_TRANSITIONS,
    "notification": NOTIFICATION_TRANSITIONS,
    "trust": TRUST_TRANSITIONS,
}


def initial_state(machine: str) -> str:
    if machine not in INITIAL_STATES:
        raise ValueError(f"unknown machine: {machine}")
    return INITIAL_STATES[machine]


def next_state(
    machine: str,
    from_state: str,
    event: str,
    *,
    guard: Optional[str] = None,
) -> str:
    """查迁移表；非法或 guard 未满足则抛 IllegalTransition。"""
    table = _MACHINES.get(machine)
    if table is None:
        raise IllegalTransition(machine, from_state, event, f"unknown machine: {machine}")

    candidates = [
        t
        for t in table
        if t.event == event and (t.from_state == from_state or t.from_state == "*")
    ]
    if not candidates:
        raise IllegalTransition(machine, from_state, event)

    if guard is not None:
        # 精确 guard 优先
        for t in candidates:
            if t.guard == guard:
                return t.to_state
        # 无 guard 的通配迁移可作为兜底
        free = [t for t in candidates if t.guard is None]
        if free:
            return free[0].to_state
        raise IllegalTransition(machine, from_state, event, f"guard={guard!r} not satisfied")

    # 未指定 guard：只允许无 guard 的迁移；若候选全要求 guard → 拒绝
    free = [t for t in candidates if t.guard is None]
    if free:
        return free[0].to_state
    raise IllegalTransition(machine, from_state, event, "transition requires guard")
