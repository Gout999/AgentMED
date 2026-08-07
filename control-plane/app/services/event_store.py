"""事件溯源存储：追加事件 + CAS 更新聚合；支持从 events 重放状态。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import Aggregate, Event, Outbox
from app.services.state_machines import IllegalTransition, next_state
from app.utils.ids import new_event_id, new_outbox_id


class CASConflict(Exception):
    def __init__(self, aggregate_type: str, aggregate_id: str, expected: int, actual: Optional[int]):
        self.aggregate_type = aggregate_type
        self.aggregate_id = aggregate_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"CAS conflict {aggregate_type}/{aggregate_id}: expected revision={expected}, actual={actual}"
        )


class EventStore:
    def __init__(self, session: Session):
        self.session = session

    def get_aggregate(self, aggregate_type: str, aggregate_id: str) -> Optional[Aggregate]:
        return self.session.get(Aggregate, {"aggregate_type": aggregate_type, "aggregate_id": aggregate_id})

    def create_aggregate(
        self,
        aggregate_type: str,
        aggregate_id: str,
        state: str,
        payload: Optional[dict[str, Any]] = None,
    ) -> Aggregate:
        agg = Aggregate(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            state=state,
            payload=payload or {},
            revision=1,
            updated_at=datetime.now(timezone.utc),
        )
        self.session.add(agg)
        self.session.flush()
        return agg

    def append_event(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        causation_id: str = "none",
        correlation_id: str = "",
        actor: str = "system",
        trace_id: Optional[str] = None,
        expected_revision: Optional[int] = None,
        new_state: Optional[str] = None,
        machine: Optional[str] = None,
        guard: Optional[str] = None,
        merge_payload: Optional[dict[str, Any]] = None,
        outbox: Optional[dict[str, Any]] = None,
    ) -> Event:
        """同事务：CAS 迁移聚合 + 追加事件 + 可选 outbox。

        状态机迁移：若 machine 给定，用 next_state 计算 new_state。
        若 new_state 直接给定则使用之（创建首事件等）。
        """
        agg = self.get_aggregate(aggregate_type, aggregate_id)
        now = datetime.now(timezone.utc)

        if agg is None:
            # 首事件：创建聚合
            initial_state = new_state or "RECEIVED"
            if machine and new_state is None:
                # 首事件不经迁移表
                pass
            agg = self.create_aggregate(aggregate_type, aggregate_id, initial_state, payload={})
            seq = 1
            if expected_revision is not None and expected_revision != 0:
                raise CASConflict(aggregate_type, aggregate_id, expected_revision, None)
        else:
            if expected_revision is not None and agg.revision != expected_revision:
                raise CASConflict(aggregate_type, aggregate_id, expected_revision, agg.revision)
            seq = self._next_seq(aggregate_id)

            target_state = new_state
            if machine is not None:
                try:
                    target_state = next_state(machine, agg.state, event_type, guard=guard)
                except IllegalTransition:
                    raise
            if target_state is not None and target_state != agg.state:
                agg.state = target_state

            if merge_payload:
                merged = dict(agg.payload or {})
                merged.update(merge_payload)
                agg.payload = merged
            agg.revision = int(agg.revision) + 1
            agg.updated_at = now

        event = Event(
            event_id=new_event_id(),
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id or aggregate_id,
            actor=actor,
            trace_id=trace_id,
            occurred_at=now,
            created_at=now,
        )
        self.session.add(event)

        if outbox:
            ob = Outbox(
                outbox_id=outbox.get("outbox_id") or new_outbox_id(),
                aggregate_id=aggregate_id,
                channel=outbox["channel"],
                payload=outbox.get("payload") or {},
                status="PENDING",
                attempts=0,
                created_at=now,
            )
            self.session.add(ob)

        self.session.flush()
        return event

    def _next_seq(self, aggregate_id: str) -> int:
        rows = self.session.scalars(
            select(Event.seq).where(Event.aggregate_id == aggregate_id).order_by(Event.seq.desc()).limit(1)
        ).all()
        return (rows[0] if rows else 0) + 1

    def list_events(self, aggregate_id: str) -> list[Event]:
        return list(
            self.session.scalars(
                select(Event).where(Event.aggregate_id == aggregate_id).order_by(Event.seq.asc())
            ).all()
        )

    def replay(self, aggregate_type: str, aggregate_id: str, machine: str) -> tuple[str, dict[str, Any], int]:
        """从 events 重放状态（不读 aggregates 投影）。

        返回 (state, payload_merged, event_count)。
        """
        events = self.list_events(aggregate_id)
        if not events:
            raise ValueError(f"no events for {aggregate_type}/{aggregate_id}")

        # 首事件建立初始状态
        state = "RECEIVED" if machine == "case" else "REQUESTED" if machine == "release" else "DRAFTED"
        payload: dict[str, Any] = {}

        # 若首事件是 case.opened 等，需从初始状态迁移
        # 约定：创建聚合时已有 RECEIVED，首条业务事件驱动迁移
        for i, ev in enumerate(events):
            payload.update(ev.payload or {})
            if i == 0 and machine == "case" and ev.event_type == "complaint.received":
                state = "RECEIVED"
                continue
            if i == 0 and machine == "case" and ev.event_type == "case.opened":
                # opened 从 RECEIVED
                state = next_state("case", "RECEIVED", "case.opened")
                continue
            try:
                state = next_state(machine, state, ev.event_type)
            except IllegalTransition:
                # 部分事件不改变状态（自迁移等）——保持
                pass
        return state, payload, len(events)
