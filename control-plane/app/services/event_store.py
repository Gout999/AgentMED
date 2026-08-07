"""事件溯源存储：追加事件 + CAS 更新聚合；支持从 events 重放状态。

聚合在首事件时懒创建（revision=1, seq=1）；此后每个事件 revision+1、seq+1。
revision 即事件序号，CAS 依据（spec §7.2：revision BIGINT 每次迁移 +1，初始 1）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import Aggregate, Event, Outbox
from app.services.state_machines import IllegalTransition, initial_state, next_state
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
        """预创建聚合（无事件）；一般无需调用——append_event 会在首事件时懒创建。"""
        if self.get_aggregate(aggregate_type, aggregate_id) is not None:
            raise CASConflict(aggregate_type, aggregate_id, 0, None)
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

        - 聚合不存在 → 懒创建（state = new_state or 机器初始状态），seq=1。
          expected_revision 必须为 None 或 0。
        - 聚合存在 → CAS 校验 expected_revision == agg.revision；随后状态迁移、
          revision+1、seq=last+1。
        """
        agg = self.get_aggregate(aggregate_type, aggregate_id)
        now = datetime.now(timezone.utc)

        if agg is None:
            if expected_revision not in (None, 0):
                raise CASConflict(aggregate_type, aggregate_id, expected_revision, None)
            state = new_state or (initial_state(machine) if machine else "RECEIVED")
            agg = Aggregate(
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                state=state,
                payload=dict(merge_payload or {}),
                revision=1,
                updated_at=now,
            )
            self.session.add(agg)
            seq = 1
        else:
            if expected_revision is not None and int(agg.revision) != expected_revision:
                raise CASConflict(aggregate_type, aggregate_id, expected_revision, int(agg.revision))
            seq = self._next_seq(aggregate_id)

            if machine is not None:
                target_state = next_state(machine, agg.state, event_type, guard=guard)
                agg.state = target_state
            elif new_state is not None:
                agg.state = new_state

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

        state = initial_state(machine)
        payload: dict[str, Any] = {}
        for ev in events:
            payload.update(ev.payload or {})
            try:
                state = next_state(machine, state, ev.event_type)
            except IllegalTransition:
                # 部分事件不改变状态（自迁移/需 guard 的事件）——保持
                pass
        return state, payload, len(events)
