"""ChangeSet（修复变更集）状态机服务（contracts/events/state-machines.yaml#changeset）。

审批即批 WorkOrder hash；拒绝/过期都回起草侧且旧单作废。
门禁报告引用与目标 digest 不一致 → 禁止进入审批（stale_gate 由调用方校验）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Aggregate, Approval, WorkOrder
from app.services.audit import AuditService, AuditWriteError
from app.services.event_store import CASConflict, EventStore
from app.services.gate_service import GateService, GateServiceError
from app.services.state_machines import IllegalTransition
from app.utils.ids import new_changeset_id, new_trace_id
from app.utils.jcs import workorder_hash as compute_workorder_hash


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
        self.gates = GateService(session, self.settings)

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

    def attach_gate(self, changeset_id: str, *, eval_id: str, report_hash: str) -> dict[str, Any]:
        agg = self._require(changeset_id)
        workorder_id = (agg.payload or {}).get("workorder_ref")
        workorder = self.session.get(WorkOrder, workorder_id)
        if workorder is None:
            raise ChangeSetServiceError("validation_failed", "changeset has no registered immutable WorkOrder")
        try:
            gate = self.gates.validate_for_workorder(workorder)
        except GateServiceError as exc:
            raise ChangeSetServiceError(exc.code, exc.message, **exc.extra) from exc
        if gate.eval_id != eval_id or gate.report_hash != report_hash:
            raise ChangeSetServiceError("hash_mismatch", "submitted gate id/hash does not match WorkOrder binding")
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.gate_attached",
            payload={
                "gate_report_ref": f"eval://{eval_id}",
                "gate_report_hash": report_hash,
                "gate_status": gate.overall_status,
                "evidence_digest": gate.evidence_digest,
            },
            causation_id="eval.passed",
            correlation_id=(agg.payload or {}).get("case_id") or changeset_id,
            actor="controller:changeset",
            expected_revision=agg.revision,
            machine="changeset",
            merge_payload={"gate_report_ref": f"eval://{eval_id}", "gate_report_hash": report_hash},
        )
        self.audit.record(
            actor="controller:changeset",
            action="changeset.gate_attached",
            target=changeset_id,
            params={"eval_id": eval_id, "report_hash": report_hash},
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
        expected_hash = (agg.payload or {}).get("workorder_hash")
        if expected_hash != workorder_hash:
            raise ChangeSetServiceError("hash_mismatch", "approval request WorkOrder hash mismatch")
        workorder = self.session.get(WorkOrder, (agg.payload or {}).get("workorder_ref"))
        if workorder is None:
            raise ChangeSetServiceError("validation_failed", "approval request has no registered WorkOrder")
        try:
            recomputed = compute_workorder_hash(workorder.payload)
        except (TypeError, ValueError) as exc:
            raise ChangeSetServiceError("hash_mismatch", "WorkOrder payload is not canonical") from exc
        if recomputed != workorder.hash or workorder.hash != workorder_hash:
            raise ChangeSetServiceError("hash_mismatch", "approval request WorkOrder was modified")
        if nonce != workorder.payload.get("nonce") or expiry != workorder.payload.get("expiry"):
            raise ChangeSetServiceError(
                "hash_mismatch",
                "approval request nonce/expiry must be copied from the immutable WorkOrder",
            )
        try:
            expiry_at = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ChangeSetServiceError("validation_failed", "approval request expiry is invalid") from exc
        if expiry_at.tzinfo is None:
            expiry_at = expiry_at.replace(tzinfo=timezone.utc)
        if expiry_at <= datetime.now(timezone.utc):
            raise ChangeSetServiceError("approval_expired", "WorkOrder approval window has expired")
        try:
            self.gates.validate_for_workorder(workorder)
        except GateServiceError as exc:
            raise ChangeSetServiceError(exc.code, exc.message, **exc.extra) from exc
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.approval_requested",
            payload={
                "workorder_hash": workorder.hash,
                "nonce": workorder.payload["nonce"],
                "expiry": workorder.payload["expiry"],
                "channel": channel,
            },
            causation_id="changeset.gate_attached",
            correlation_id=(agg.payload or {}).get("case_id") or changeset_id,
            actor="controller:changeset",
            expected_revision=agg.revision,
            machine="changeset",
            merge_payload={
                "nonce": workorder.payload["nonce"],
                "expiry": workorder.payload["expiry"],
            },
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
        self._validate_initial_approval(
            agg,
            approval_id=approval_id,
            approver=approver,
            decision="approved",
            declared_workorder_hash=workorder_hash,
        )
        self.store.append_event(
            aggregate_type="changeset",
            aggregate_id=changeset_id,
            event_type="changeset.approved",
            payload={"approval_id": approval_id, "approver": approver, "workorder_hash": workorder_hash},
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
        self._validate_initial_approval(
            agg,
            approval_id=approval_id,
            approver=approver,
            decision="rejected",
        )
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

    def _validate_initial_approval(
        self,
        aggregate: Aggregate,
        *,
        approval_id: str,
        approver: str,
        decision: str,
        declared_workorder_hash: str | None = None,
    ) -> Approval:
        """Bind a ChangeSet decision to its own immutable initial grant."""

        aggregate_payload = aggregate.payload or {}
        workorder_id = aggregate_payload.get("workorder_ref") or aggregate_payload.get("workorder_id")
        workorder = self.session.get(WorkOrder, workorder_id) if workorder_id else None
        approval = self.session.get(Approval, approval_id)
        if workorder is None or approval is None:
            raise ChangeSetServiceError(
                "validation_failed",
                "ChangeSet decision requires its registered WorkOrder and ApprovalGrant",
            )
        try:
            recomputed = compute_workorder_hash(workorder.payload)
        except (TypeError, ValueError) as exc:
            raise ChangeSetServiceError("hash_mismatch", "WorkOrder payload is not canonical") from exc
        expected_status = "pending" if decision == "approved" else "rejected"
        approval_payload = approval.payload or {}
        approval_identity = (approval.approver or {}).get("identity")
        if (
            recomputed != workorder.hash
            or aggregate_payload.get("workorder_hash") != workorder.hash
            or (declared_workorder_hash is not None and declared_workorder_hash != workorder.hash)
            or approval.workorder_id != workorder.workorder_id
            or approval.workorder_hash != workorder.hash
            or approval.nonce != workorder.payload.get("nonce")
            or approval.decision != decision
            or approval.status != expected_status
            or approval_identity != approver
            or approval_payload.get("authorization") is not None
            or approval_payload.get("nonce") != workorder.payload.get("nonce")
            or approval_payload.get("nonce_consumed") is not False
        ):
            raise ChangeSetServiceError(
                "hash_mismatch",
                "ApprovalGrant is not the initial grant for this ChangeSet WorkOrder",
            )
        now = datetime.now(timezone.utc)
        approval_expiry = approval.expiry
        if approval_expiry.tzinfo is None:
            approval_expiry = approval_expiry.replace(tzinfo=timezone.utc)
        try:
            workorder_expiry = datetime.fromisoformat(
                str(workorder.payload.get("expiry", "")).replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise ChangeSetServiceError("validation_failed", "WorkOrder expiry is invalid") from exc
        if workorder_expiry.tzinfo is None:
            workorder_expiry = workorder_expiry.replace(tzinfo=timezone.utc)
        if approval_expiry <= now or workorder_expiry <= now or approval_expiry > workorder_expiry:
            raise ChangeSetServiceError("approval_expired", "ApprovalGrant is outside its authorization window")
        try:
            self.gates.validate_for_workorder(workorder)
        except GateServiceError as exc:
            raise ChangeSetServiceError(exc.code, exc.message, **exc.extra) from exc
        return approval

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
        release = self.store.get_aggregate("release", release_id)
        release_payload = (release.payload or {}) if release is not None else {}
        changeset_payload = agg.payload or {}
        approval_id = changeset_payload.get("approval_id")
        approval = self.session.get(Approval, approval_id) if approval_id else None
        if (
            release is None
            or release_payload.get("changeset_id") != changeset_id
            or release_payload.get("workorder_id") != changeset_payload.get("workorder_ref")
            or release_payload.get("workorder_hash") != changeset_payload.get("workorder_hash")
            or release_payload.get("approval_id") != approval_id
            or approval is None
            or approval.status != "consumed"
            or (approval.payload or {}).get("authorization") is not None
        ):
            raise ChangeSetServiceError(
                "hash_mismatch",
                "changeset commit requires the exact bound Release and consumed initial ApprovalGrant",
            )
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
