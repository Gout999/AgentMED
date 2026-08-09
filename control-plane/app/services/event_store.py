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
from app.utils.jcs import canonical_json_digest
from app.utils.ids import new_event_id, new_outbox_id


DOMAIN_EVENT_CHANNEL = "domain.events"
DOMAIN_EVENT_TYPES = {
    "case.opened": "CASE_CREATED",
    "case.attribution_completed": "ATTRIBUTION_DECIDED",
    "eval.bound": "GATE_COMPLETED",
    "release.requested": "RELEASE_STARTED",
    "release.promoted": "RELEASE_PROMOTED",
    "release.rolled_back": "RELEASE_ROLLED_BACK",
    "release.unknown_detected": "RELEASE_UNKNOWN",
    "notification.sent": "NOTIFICATION_SENT",
    "case.closed": "CASE_ARCHIVED",
}


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

    def get_aggregate_for_update(
        self, aggregate_type: str, aggregate_id: str
    ) -> Optional[Aggregate]:
        """Lock and refresh an aggregate before an authoritative mutation.

        ``Session.get`` may return a stale identity-map object after this
        transaction waited on a lease or another controller transaction.  A
        database row lock plus ``populate_existing`` makes the following CAS
        compare use the committed revision, never the cached one.
        """

        return self.session.scalar(
            select(Aggregate)
            .where(
                Aggregate.aggregate_type == aggregate_type,
                Aggregate.aggregate_id == aggregate_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

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
        agg = self.get_aggregate_for_update(aggregate_type, aggregate_id)
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

        domain_event_type = DOMAIN_EVENT_TYPES.get(event_type)
        if domain_event_type:
            envelope = {
                "schema_version": "0.1.0",
                "domain_event_type": domain_event_type,
                "source_event_id": event.event_id,
                "source_event_type": event.event_type,
                "aggregate_type": aggregate_type,
                "aggregate_id": aggregate_id,
                "aggregate_seq": event.seq,
                "causation_id": causation_id,
                "correlation_id": correlation_id or aggregate_id,
                "actor": actor,
                "trace_id": trace_id,
                "occurred_at": now.isoformat(),
                "payload": payload,
            }
            self._enqueue_outbox(
                event=event,
                channel=DOMAIN_EVENT_CHANNEL,
                event_type=domain_event_type,
                payload=envelope,
                outbox_id=None,
                now=now,
            )

        if outbox:
            self._enqueue_outbox(
                event=event,
                channel=outbox["channel"],
                event_type=outbox.get("event_type") or event.event_type,
                payload=outbox.get("payload") or {},
                outbox_id=outbox.get("outbox_id"),
                now=now,
            )

        self.session.flush()
        return event

    def _enqueue_outbox(
        self,
        *,
        event: Event,
        channel: str,
        event_type: str,
        payload: dict[str, Any],
        outbox_id: Optional[str],
        now: datetime,
    ) -> Outbox:
        """Bind one delivery to the exact source event and canonical payload digest."""

        row = Outbox(
            outbox_id=outbox_id or new_outbox_id(),
            aggregate_id=event.aggregate_id,
            source_event_id=event.event_id,
            source_event_seq=event.seq,
            channel=channel,
            event_type=event_type,
            payload=payload,
            payload_digest=canonical_json_digest(payload),
            status="PENDING",
            attempts=0,
            created_at=now,
        )
        self.session.add(row)
        return row

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
