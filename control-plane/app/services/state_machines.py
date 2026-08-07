"""状态机迁移表（权威：contracts/events/state-machines.yaml）。

只实现 Case / Release / ChangeSet 施工面所需迁移；不发明 contracts 没有的状态。
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
    # 允许从 CANARYING 直接 promote 的简化路径不在契约；须经 VERIFYING
    # 从 CANARYING 也可直接 promote 若观察通过：契约要求 verification_completed → VERIFYING → promoted
    Transition("PROMOTING", "release.promoted", "COMPLETED"),
    Transition("VERIFYING", "release.promoted", "COMPLETED"),  # promote 事件
    Transition("ROLLING_BACK", "release.rolled_back", "ROLLED_BACK"),
    Transition("ROLLING_BACK", "release.rollback_failed", "FAILED_ESCALATED"),
    Transition("STAGING", "release.unknown_detected", "UNKNOWN"),
    Transition("CANARYING", "release.unknown_detected", "UNKNOWN"),
    Transition("PROMOTING", "release.unknown_detected", "UNKNOWN"),
    Transition("ROLLING_BACK", "release.unknown_detected", "UNKNOWN"),
    # reconcile 多目标：由 guard 参数选择
    Transition("UNKNOWN", "release.reconciled", "REQUESTED", guard="action=resume"),
    Transition("UNKNOWN", "release.reconciled", "COMPLETED", guard="action=confirm"),
    Transition("UNKNOWN", "release.reconciled", "ROLLING_BACK", guard="action=compensate"),
    Transition("UNKNOWN", "release.rollback_failed", "FAILED_ESCALATED"),
    # 人工可从 REQUESTED 直接开始 canary（若 stage 已在外部完成）— 不发明；保持契约
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


def next_state(
    machine: str,
    from_state: str,
    event: str,
    *,
    guard: Optional[str] = None,
) -> str:
    """查迁移表；非法则抛 IllegalTransition。"""
    table = {
        "case": CASE_TRANSITIONS,
        "release": RELEASE_TRANSITIONS,
        "changeset": CHANGESET_TRANSITIONS,
    }.get(machine)
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
        for t in candidates:
            if t.guard == guard or t.guard is None:
                # 优先精确 guard 匹配
                pass
        exact = [t for t in candidates if t.guard == guard]
        if exact:
            return exact[0].to_state
        # 无 guard 的通配
        free = [t for t in candidates if t.guard is None]
        if free:
            return free[0].to_state
        # 有多个不同 guard 的候选但未指定匹配 → 取第一个（调用方应传 guard）
        return candidates[0].to_state

    # 无 guard 入参：优先无 guard 的迁移
    free = [t for t in candidates if t.guard is None]
    if free:
        return free[0].to_state
    return candidates[0].to_state
