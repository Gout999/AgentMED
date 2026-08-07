"""ChangeSet（修复变更集）状态机服务（contracts/events/state-machines.yaml#changeset）。

审批即批 WorkOrder hash；拒绝/过期都回起草侧且旧单作废。
门禁报告引用与目标 digest 不一致 → 禁止进入审批（stale_gate 由调用方校验）。
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Aggregate
from app.services.audit import AuditService, AuditWriteError
from app.services.event_store import CASConflict, EventStore
from app.services.state_machines import IllegalTransition
from app.utils.ids import new_changeset_id, new_trace_id


class ChangeSetServiceError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


class ChangeSetService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.store = EventStore(session)
        self.audit = AuditService(session, self.settings)

    def create(self, *, case_id: str, workorder_ref: str, workorder_hash: str, channel: str, author_agent: str, changeset_id: Optional[str] = None) -> dict[str, Any]:
        """起草 ChangeSet（对应 changeset.drafted；register_workorder 内部也走此事件）。"""
        cs_id = changeset_id or new_changeset_id()
        if self.store.get_aggregate("changeset", cs_id) is not None:
            raise ChangeSetServiceError("validation_failed", f"changeset {cs_id} already exists")
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=cs_id,
            event_type="changeset.drafted",
            payload={
                "case_id": case_id,
                "workorder_ref": workorder_ref,
                "workorder_hash": workorder_hash,
                "channel": channel,
                "author_agent": author_agent,
            },
            causation_id="case.attribution_completed",
            correlation_id=case_id,
            actor="controller:changeset",
            machine="changeset",
            merge_payload={"case_id": case_id, "workorder_ref": workorder_ref, "workorder_hash": workorder_hash},
        )
        self.audit.record(
            actor=author_agent,
            action="changeset.drafted",
            target=cs_id,
            params={"workorder_hash": workorder_hash, "channel": channel},
            result="success",
        )
        return self._view(self._require(cs_id))

    def attach_gate(self, changeset_id: str, *, gate_report_ref: str, gate_status: str = "passed") -> dict[str, Any]:
        if gate_status != "passed":
            raise ChangeSetServiceError("validation_failed", "gate_status must be passed to attach")
        agg = self._require(changeset_id)
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.gate_attached",
            payload={"gate_report_ref": gate_report_ref, "gate_status": gate_status},
            causation_id="eval.passed",
            correlation_id=(agg.payload or {}).get("case_id") or changeset_id,
            actor="controller:changeset",
            expected_revision=agg.revision,
            machine="changeset",
            merge_payload={"gate_report_ref": gate_report_ref},
        )
        self.audit.record(
            actor="controller:changeset",
            action="changeset.gate_attached",
            target=changeset_id,
            params={"gate_report_ref": gate_report_ref},
            result="success",
        )
        return self._view(self._require(changeset_id))

    def request_approval(
        self,
        changeset_id: str,
        *,
        workorder_hash: str,
        nonce: str,
        expiry: str,
        channel: str = "feishu",
    ) -> dict[str, Any]:
        agg = self._require(changeset_id)
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.approval_requested",
            payload={"workorder_hash": workorder_hash, "nonce": nonce, "expiry": expiry, "channel": channel},
            causation_id="changeset.gate_attached",
            correlation_id=(agg.payload or {}).get("case_id") or changeset_id,
            actor="controller:changeset",
            expected_revision=agg.revision,
            machine="changeset",
            merge_payload={"nonce": nonce, "expiry": expiry},
        )
        self.audit.record(
            actor="controller:changeset",
            action="changeset.approval_requested",
            target=changeset_id,
            params={"workorder_hash": workorder_hash},
            result="success",
        )
        return self._view(self._require(changeset_id))

    def approve(self, changeset_id: str, *, approval_id: str, approver: str, workorder_hash: str) -> dict[str, Any]:
        agg = self._require(changeset_id)
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.approved",
            payload={"approval_id": approval_id, "approver": approver, "workorder_hash": workorder_hash, "nonce_consumed": True},
            causation_id="human_approval",
            correlation_id=(agg.payload or {}).get("case_id") or changeset_id,
            actor=approver,
            expected_revision=agg.revision,
            machine="changeset",
            merge_payload={"approval_id": approval_id, "approved": True},
        )
        self.audit.record(
            actor=approver,
            action="changeset.approved",
            target=changeset_id,
            params={"approval_id": approval_id, "workorder_hash": workorder_hash},
            result="success",
        )
        return self._view(self._require(changeset_id))

    def reject(self, changeset_id: str, *, approval_id: str, approver: str, reason: str) -> dict[str, Any]:
        agg = self._require(changeset_id)
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.rejected",
            payload={"approval_id": approval_id, "approver": approver, "reason": reason},
            causation_id="human_approval",
            correlation_id=(agg.payload or {}).get("case_id") or changeset_id,
            actor=approver,
            expected_revision=agg.revision,
            machine="changeset",
            merge_payload={"rejected": True, "reason": reason},
        )
        self.audit.record(
            actor=approver,
            action="changeset.rejected",
            target=changeset_id,
            params={"approval_id": approval_id, "reason": reason},
            result="success",
        )
        return self._view(self._require(changeset_id))

    def expire(self, changeset_id: str, *, workorder_hash: str, expiry: str) -> dict[str, Any]:
        agg = self._require(changeset_id)
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.expired",
            payload={"workorder_hash": workorder_hash, "expiry": expiry},
            causation_id="expiry_timer",
            correlation_id=(agg.payload or {}).get("case_id") or changeset_id,
            actor="controller:changeset",
            expected_revision=agg.revision,
            machine="changeset",
            merge_payload={"expired": True},
        )
        self.audit.record(
            actor="controller:changeset",
            action="changeset.expired",
            target=changeset_id,
            params={"workorder_hash": workorder_hash},
            result="success",
        )
        return self._view(self._require(changeset_id))

    def commit(self, changeset_id: str, *, release_id: str) -> dict[str, Any]:
        agg = self._require(changeset_id)
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.committed",
            payload={"release_id": release_id},
            causation_id="release.requested",
            correlation_id=(agg.payload or {}).get("case_id") or changeset_id,
            actor="controller:release",
            expected_revision=agg.revision,
            machine="changeset",
            merge_payload={"release_id": release_id},
        )
        self.audit.record(
            actor="controller:release",
            action="changeset.committed",
            target=changeset_id,
            params={"release_id": release_id},
            result="success",
        )
        return self._view(self._require(changeset_id))

    def supersede(self, changeset_id: str, *, replaced_by: str) -> dict[str, Any]:
        agg = self._require(changeset_id)
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.drafted",
            payload={"replaced_by": replaced_by},
            causation_id="changeset.drafted",
            correlation_id=(agg.payload or {}).get("case_id") or changeset_id,
            actor="controller:changeset",
            expected_revision=agg.revision,
            machine="changeset",
            merge_payload={"superseded_by": replaced_by},
        )
        self.audit.record(
            actor="controller:changeset",
            action="changeset.superseded",
            target=changeset_id,
            params={"replaced_by": replaced_by},
            result="success",
        )
        return self._view(self._require(changeset_id))

    def get(self, changeset_id: str) -> dict[str, Any]:
        return self._view(self._require(changeset_id))

    def list_changesets(self, *, state: Optional[str] = None, limit: int = 100, cursor: int = 0) -> dict[str, Any]:
        q = select(Aggregate).where(Aggregate.aggregate_type == "changeset").order_by(Aggregate.aggregate_id)
        if state:
            q = q.where(Aggregate.state == state)
        rows = list(self.session.scalars(q.offset(cursor).limit(limit)).all())
        return {
            "items": [self._view(r) for r in rows],
            "next_cursor": cursor + len(rows) if len(rows) == limit else None,
        }

    def _require(self, changeset_id: str) -> Aggregate:
        agg = self.store.get_aggregate("changeset", changeset_id)
        if agg is None:
            raise ChangeSetServiceError("not_found", f"changeset {changeset_id} not found")
        return agg

    @staticmethod
    def _view(agg: Aggregate) -> dict[str, Any]:
        return {
            "changeset_id": agg.aggregate_id,
            "state": agg.state,
            "revision": agg.revision,
            "payload": agg.payload,
        }
