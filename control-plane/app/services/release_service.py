"""Release Controller：写面唯一入口；WorkOrder/Approval 校验；灰度；UNKNOWN→reconcile。"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Aggregate, Approval, ControllerOperation, WorkOrder
from app.quality.client import QualityAPIError, QualityClientProtocol
from app.services.audit import AuditService, AuditWriteError
from app.services.event_store import CASConflict, EventStore
from app.services.gate_service import GateService, GateServiceError
from app.services.state_machines import IllegalTransition
from app.utils.ids import new_operation_id, new_release_id, new_trace_id
from app.utils.jcs import canonical_json_digest, workorder_hash

logger = logging.getLogger(__name__)


class ReleaseServiceError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


class ReleaseService:
    def __init__(
        self,
        session: Session,
        quality: QualityClientProtocol,
        settings: Settings | None = None,
    ):
        self.session = session
        self.quality = quality
        self.settings = settings or get_settings()
        self.store = EventStore(session)
        self.audit = AuditService(session, self.settings)
        self.gates = GateService(session, self.settings)

    # ---------- WorkOrder ----------

    def register_workorder(self, payload: dict[str, Any]) -> dict[str, Any]:
        """登记不可变 WorkOrder：六要素 hash 校验（JCS+SHA-256）。"""
        required = [
            "schema_version",
            "workorder_id",
            "case_id",
            "channel",
            "base_versionset_digest",
            "target_versionset_digest",
            "input_versions",
            "diff",
            "gate_report_ref",
            "expiry",
            "nonce",
            "created_at",
            "created_by",
            "hash",
            "hash_rule",
        ]
        missing = [k for k in required if k not in payload]
        if missing:
            raise ReleaseServiceError("validation_failed", f"missing fields: {missing}")
        if payload.get("hash_rule") != "jcs-rfc8785+sha256":
            raise ReleaseServiceError("validation_failed", "hash_rule must be jcs-rfc8785+sha256")
        if payload.get("channel") not in ("prompt", "kb", "model_params"):
            raise ReleaseServiceError("validation_failed", "channel must be prompt|kb|model_params")

        try:
            recomputed = workorder_hash(payload)
        except (ValueError, TypeError) as exc:
            raise ReleaseServiceError("validation_failed", f"hash compute failed: {exc}") from exc

        declared = payload["hash"]
        if recomputed != declared:
            raise ReleaseServiceError(
                "hash_mismatch",
                f"workorder hash mismatch: declared={declared} recomputed={recomputed}",
            )

        if _parse_dt(payload["expiry"]) <= datetime.now(timezone.utc):
            raise ReleaseServiceError("approval_expired", "WorkOrder already expired at registration time")

        existing = self.session.get(WorkOrder, payload["workorder_id"])
        if existing is not None:
            if existing.hash != declared:
                raise ReleaseServiceError("validation_failed", "workorder_id exists with different hash")
            self._gate_call(self.gates.validate_for_workorder, existing)
            return {"workorder_id": existing.workorder_id, "hash": existing.hash, "duplicate": True}

        by_hash = self.session.scalar(select(WorkOrder).where(WorkOrder.hash == declared))
        if by_hash is not None:
            raise ReleaseServiceError("validation_failed", "hash already registered under another id")

        # GateReport is registered before WorkOrder freeze. Seal the final WorkOrder hash and
        # report hash together in this same authoritative transaction.
        self._gate_call(self.gates.bind_workorder, payload)

        wo = WorkOrder(
            workorder_id=payload["workorder_id"],
            case_id=payload["case_id"],
            hash=declared,
            channel=payload["channel"],
            payload=payload,
            created_at=datetime.now(timezone.utc),
        )
        self.session.add(wo)

        # ChangeSet 聚合（首事件懒创建 DRAFTED）
        cs_id = f"cs_{payload['workorder_id']}"
        if self.store.get_aggregate("changeset", cs_id) is None:
            self.store.append_event(
                aggregate_type="changeset",
                aggregate_id=cs_id,
                event_type="changeset.drafted",
                payload={
                    "case_id": payload["case_id"],
                    "workorder_ref": payload["workorder_id"],
                    "workorder_hash": declared,
                    "channel": payload["channel"],
                    "author_agent": payload.get("created_by", "unknown"),
                },
                correlation_id=payload["case_id"],
                actor="controller:release",
                machine="changeset",
                merge_payload={
                    "case_id": payload["case_id"],
                    "workorder_ref": payload["workorder_id"],
                    "workorder_hash": declared,
                    "channel": payload["channel"],
                    "author_agent": payload.get("created_by", "unknown"),
                },
            )

        self.audit.record(
            actor="controller:release",
            action="workorder.register",
            target=payload["workorder_id"],
            params={"hash": declared, "channel": payload["channel"]},
            result="success",
        )
        self.session.flush()
        return {"workorder_id": wo.workorder_id, "hash": wo.hash, "duplicate": False}

    # ---------- Approval ----------

    def grant_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        """登记 ApprovalGrant；绑定 workorder_hash + nonce；Q7 server_recorded。"""
        required = [
            "schema_version",
            "approval_id",
            "workorder_hash",
            "workorder_id",
            "nonce",
            "expiry",
            "approver",
            "decision",
            "decided_at",
            "nonce_consumed",
        ]
        missing = [k for k in required if k not in payload]
        if missing:
            raise ReleaseServiceError("validation_failed", f"missing fields: {missing}")
        if payload["schema_version"] != "0.1.0":
            raise ReleaseServiceError("validation_failed", "ApprovalGrant schema_version must be 0.1.0")
        if payload["nonce_consumed"] is not False:
            raise ReleaseServiceError("nonce_replay", "new ApprovalGrant must have nonce_consumed=false")
        try:
            UUID(str(payload["nonce"]))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ReleaseServiceError("validation_failed", "ApprovalGrant nonce must be a UUID") from exc

        approver = payload["approver"]
        if (
            not isinstance(approver, dict)
            or approver.get("type") != "human"
            or not isinstance(approver.get("identity"), str)
            or not approver.get("identity", "").strip()
        ):
            raise ReleaseServiceError("validation_failed", "approver.type must be human")

        wo = self.session.get(WorkOrder, payload["workorder_id"])
        if wo is None:
            raise ReleaseServiceError("not_found", f"workorder {payload['workorder_id']} not found")
        if wo.hash != payload["workorder_hash"]:
            raise ReleaseServiceError("hash_mismatch", "approval workorder_hash does not match registered WorkOrder")
        gate = self._gate_call(self.gates.validate_for_workorder, wo)

        authorization = payload.get("authorization")
        if authorization is None:
            if wo.payload.get("nonce") != payload["nonce"]:
                raise ReleaseServiceError("validation_failed", "initial grant nonce must match WorkOrder.nonce")
            action_context = None
        else:
            action_context = self._validate_action_grant_registration(
                wo,
                authorization,
            )

        # nonce 唯一（防重放：同一次 nonce 只能登记一次 approval）
        existing_nonce = self.session.scalar(select(Approval).where(Approval.nonce == payload["nonce"]))
        if existing_nonce is not None:
            raise ReleaseServiceError(
                "nonce_replay",
                f"nonce already used by approval {existing_nonce.approval_id}",
            )

        expiry = _parse_dt(payload["expiry"])
        now = datetime.now(timezone.utc)
        workorder_expiry = _parse_dt(wo.payload.get("expiry", ""))
        if workorder_expiry <= now:
            raise ReleaseServiceError("approval_expired", "WorkOrder already expired at grant time")
        if expiry <= now:
            raise ReleaseServiceError("approval_expired", "approval already expired at grant time")
        if expiry > workorder_expiry:
            raise ReleaseServiceError(
                "validation_failed",
                "ApprovalGrant expiry must not be later than WorkOrder expiry",
            )
        decided_at = _parse_dt(payload["decided_at"])
        if decided_at > expiry or decided_at > workorder_expiry:
            raise ReleaseServiceError(
                "approval_expired",
                "ApprovalGrant was decided after its authorization window",
            )

        decision = payload["decision"]
        if decision not in ("approved", "rejected"):
            raise ReleaseServiceError("validation_failed", "decision must be approved|rejected")

        status = "pending" if decision == "approved" else "rejected"
        proof = payload.get("proof") or {
            "method": "server_recorded",
            "ref": f"audit://control-plane/approval/{payload['approval_id']}",
        }
        if proof.get("method") not in ("server_recorded", "hmac_sha256", "ed25519_signature"):
            raise ReleaseServiceError("validation_failed", "ApprovalGrant proof.method is invalid")

        appr = Approval(
            approval_id=payload["approval_id"],
            workorder_id=payload["workorder_id"],
            workorder_hash=payload["workorder_hash"],
            nonce=payload["nonce"],
            status=status,
            decision=decision,
            approver=approver,
            expiry=expiry,
            decided_at=decided_at,
            payload={
                **payload,
                "proof": proof,
                "nonce_consumed": False,
                "authorized_target_revision": gate.target_revision,
                "authorized_gate_binding_digest": gate.binding_digest,
                **({"validated_action_context": action_context} if action_context else {}),
            },
            created_at=now,
        )
        self.session.add(appr)

        cs_id = f"cs_{payload['workorder_id']}"
        cs = self.store.get_aggregate("changeset", cs_id)
        if cs and decision == "approved" and authorization is None:
            self._advance_changeset_to_approved(cs_id, payload, gate)

        self.audit.record(
            actor=approver.get("identity", "human"),
            action=(
                f"release.{authorization['action']}.approve"
                if decision == "approved" and authorization is not None
                else "workorder.approve" if decision == "approved" else "workorder.reject"
            ),
            target=payload["approval_id"],
            params={
                "workorder_hash": payload["workorder_hash"],
                "decision": decision,
                **({"authorization": authorization} if authorization is not None else {}),
            },
            result="success",
            evidence_refs={"proof": proof},
        )
        self.session.flush()
        return {
            "approval_id": appr.approval_id,
            "status": appr.status,
            "nonce_consumed": False,
            "proof": proof,
            **({"authorization": authorization} if authorization is not None else {}),
        }

    def _validate_action_grant_registration(
        self,
        workorder: WorkOrder,
        authorization: Any,
    ) -> dict[str, Any]:
        """Validate a human grant against current authoritative release state."""

        if not isinstance(authorization, dict):
            raise ReleaseServiceError("validation_failed", "authorization must be an object")
        required = {"action", "release_id", "target_revision", "params", "params_digest"}
        missing = sorted(required - set(authorization))
        if missing:
            raise ReleaseServiceError(
                "validation_failed",
                f"action authorization missing fields: {missing}",
            )
        action = authorization.get("action")
        if action not in ("canary", "promote", "rollback"):
            raise ReleaseServiceError(
                "validation_failed",
                "action authorization must target canary|promote|rollback",
            )
        release_id = authorization.get("release_id")
        if not isinstance(release_id, str) or not release_id:
            raise ReleaseServiceError("validation_failed", "authorization.release_id is required")
        aggregate = self.store.get_aggregate("release", release_id)
        if aggregate is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")
        aggregate_payload = aggregate.payload or {}
        if (
            aggregate_payload.get("workorder_id") != workorder.workorder_id
            or aggregate_payload.get("workorder_hash") != workorder.hash
        ):
            raise ReleaseServiceError(
                "hash_mismatch",
                "action authorization release is not bound to this WorkOrder",
            )
        self._validate_original_release_authorization(aggregate, workorder)
        if action in ("promote", "rollback"):
            self._validate_persisted_release_verification(
                aggregate,
                workorder,
                expected_result="passed" if action == "promote" else "failed",
            )

        params = authorization.get("params")
        if not isinstance(params, dict):
            raise ReleaseServiceError("validation_failed", "authorization.params must be an object")
        expected_context = self._expected_action_context(aggregate, action, params=params)
        if authorization.get("target_revision") != expected_context["target_revision"]:
            raise ReleaseServiceError(
                "revision_conflict",
                "action grant target revision does not match release state",
                expected_revision=expected_context["target_revision"],
                current_revision=authorization.get("target_revision"),
            )
        expected_params = expected_context["params"]
        expected_digest = canonical_json_digest(expected_params)
        if params != expected_params or authorization.get("params_digest") != expected_digest:
            raise ReleaseServiceError(
                "hash_mismatch",
                "action grant parameters do not match the deterministic release action",
            )
        return expected_context

    def _advance_changeset_to_approved(self, cs_id: str, payload: dict[str, Any], gate: Any) -> None:
        """Advance using a server-validated GateReport; never synthesize a passed gate."""
        cs = self.store.get_aggregate("changeset", cs_id)
        if cs is None or cs.state == "APPROVED":
            return

        def _append(event_type: str, body: dict[str, Any]) -> None:
            nonlocal cs
            cs = self.store.get_aggregate("changeset", cs_id)
            assert cs is not None
            self.store.append_event(
                aggregate_type="changeset",
                aggregate_id=cs_id,
                event_type=event_type,
                payload=body,
                correlation_id=payload.get("workorder_id", cs_id),
                actor="controller:release",
                expected_revision=cs.revision,
                machine="changeset",
                merge_payload=body,
            )

        if cs.state == "DRAFTED":
            _append(
                "changeset.gate_attached",
                {
                    "gate_report_ref": f"eval://{gate.eval_id}",
                    "gate_report_hash": gate.report_hash,
                    "gate_status": gate.overall_status,
                    "evidence_digest": gate.evidence_digest,
                },
            )
        if cs.state == "GATE_ATTACHED":
            _append(
                "changeset.approval_requested",
                {
                    "workorder_hash": payload["workorder_hash"],
                    "nonce": payload["nonce"],
                    "expiry": payload["expiry"],
                    "channel": "api",
                },
            )
        if cs.state == "AWAITING_APPROVAL":
            _append(
                "changeset.approved",
                {
                    "approval_id": payload["approval_id"],
                    "approver": payload["approver"].get("identity"),
                    "workorder_hash": payload["workorder_hash"],
                },
            )

    # ---------- Release 流程 ----------

    def start_release(
        self,
        *,
        workorder_id: str,
        approval_id: str,
        versionset_id: str,
        release_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """校验 ApprovalGrant（hash 绑定 + nonce 一次性消费 + expiry）后创建 Release。"""
        wo = self.session.get(WorkOrder, workorder_id)
        if wo is None:
            raise ReleaseServiceError("not_found", f"workorder {workorder_id} not found")
        # PostgreSQL row lock serializes nonce consumption.  The second
        # concurrent request observes `consumed` after the first transaction
        # commits and cannot create a second Release.
        appr = self.session.scalar(
            select(Approval).where(Approval.approval_id == approval_id).with_for_update()
        )
        if appr is None:
            raise ReleaseServiceError("not_found", f"approval {approval_id} not found")
        if appr.status == "consumed":
            raise ReleaseServiceError("nonce_replay", "approval nonce already consumed")
        if appr.status == "rejected":
            raise ReleaseServiceError("validation_failed", "approval was rejected")
        if appr.status == "expired":
            raise ReleaseServiceError("approval_expired", "approval expired")

        approval_payload = appr.payload or {}
        if (
            appr.workorder_id != wo.workorder_id
            or appr.workorder_hash != wo.hash
            or appr.nonce != wo.payload.get("nonce")
            or approval_payload.get("nonce") != wo.payload.get("nonce")
            or approval_payload.get("authorization") is not None
            or appr.decision != "approved"
            or approval_payload.get("decision") != "approved"
            or approval_payload.get("nonce_consumed") is not False
        ):
            raise ReleaseServiceError(
                "hash_mismatch",
                "release start requires the unconsumed initial WorkOrder ApprovalGrant",
            )

        # Revalidate immutable gate/binding before inspecting or consuming the approval nonce.
        bound_gate = self._gate_call(self.gates.validate_for_workorder, wo)

        now = datetime.now(timezone.utc)
        workorder_expiry = _parse_dt(wo.payload.get("expiry", ""))
        if workorder_expiry <= now:
            appr.status = "expired"
            self.session.flush()
            raise ReleaseServiceError("approval_expired", "WorkOrder TTL exceeded")
        exp = appr.expiry
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            appr.status = "expired"
            self.session.flush()
            raise ReleaseServiceError("approval_expired", "approval TTL exceeded")
        if exp > workorder_expiry:
            raise ReleaseServiceError(
                "validation_failed",
                "persisted ApprovalGrant outlives its WorkOrder",
            )
        if (
            approval_payload.get("authorized_target_revision") != bound_gate.target_revision
            or approval_payload.get("authorized_gate_binding_digest") != bound_gate.binding_digest
        ):
            raise ReleaseServiceError(
                "hash_mismatch",
                "ApprovalGrant target revision or GateReport binding drifted",
            )

        # 重算 WorkOrder hash（审批即批此 hash，发布前逐字节核对）
        recomputed = workorder_hash(wo.payload)
        if recomputed != appr.workorder_hash or recomputed != wo.hash:
            raise ReleaseServiceError("hash_mismatch", "workorder hash drift detected at release time")

        try:
            remote_versionset = self.quality.get_versionset(versionset_id)
        except QualityAPIError as exc:
            raise ReleaseServiceError(
                "quality_api_error", f"cannot verify gated target VersionSet: {exc}", quality_code=exc.code
            ) from exc
        gate = self._gate_call(
            self.gates.validate_for_release,
            wo,
            versionset_id=versionset_id,
            remote_versionset=remote_versionset,
        )

        # 消费 nonce（一次性）
        appr.status = "consumed"
        appr.consumed_at = now
        if appr.payload:
            appr.payload = {**appr.payload, "nonce_consumed": True, "consumed_at": now.isoformat()}

        rid = release_id or new_release_id()
        target_digest = wo.payload["target_versionset_digest"]

        self.store.append_event(
            aggregate_type="release",
            aggregate_id=rid,
            event_type="release.requested",
            payload={
                "changeset_id": f"cs_{workorder_id}",
                "workorder_hash": wo.hash,
                "target_versionset_digest": target_digest,
                "expected_restore_digest": wo.payload["base_versionset_digest"],
                "target_revision": gate.target_revision,
                "gate_report_hash": gate.report_hash,
                "approval_id": approval_id,
            },
            correlation_id=wo.case_id,
            actor="controller:release",
            machine="release",
            merge_payload={
                "workorder_id": workorder_id,
                "changeset_id": f"cs_{workorder_id}",
                "workorder_hash": wo.hash,
                "approval_id": approval_id,
                "versionset_id": versionset_id,
                "target_versionset_digest": target_digest,
                "expected_restore_digest": wo.payload["base_versionset_digest"],
                "target_revision": gate.target_revision,
                "gate_report_hash": gate.report_hash,
                "canary_step_index": 0,
                "canary_percent": 0,
            },
        )

        # changeset committed
        cs_id = f"cs_{workorder_id}"
        cs = self.store.get_aggregate("changeset", cs_id)
        if cs and cs.state == "APPROVED":
            self.store.append_event(
                aggregate_type="changeset",
                aggregate_id=cs_id,
                event_type="changeset.committed",
                payload={"release_id": rid},
                correlation_id=wo.case_id,
                actor="controller:release",
                expected_revision=cs.revision,
                machine="changeset",
            )

        self.audit.record(
            actor="controller:release",
            action="release.start",
            target=rid,
            params={"workorder_id": workorder_id, "approval_id": approval_id, "versionset_id": versionset_id},
            result="success",
        )
        self.session.flush()
        agg = self.store.get_aggregate("release", rid)
        return {
            "release_id": rid,
            "state": agg.state if agg else "REQUESTED",
            "revision": agg.revision if agg else 1,
            "versionset_id": versionset_id,
            "workorder_hash": wo.hash,
        }

    @staticmethod
    def _gate_call(func: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except GateServiceError as exc:
            raise ReleaseServiceError(exc.code, exc.message, **exc.extra) from exc

    def _validate_original_release_authorization(
        self,
        aggregate: Aggregate,
        workorder: WorkOrder,
        *,
        require_fresh: bool = True,
    ) -> tuple[Any, Approval]:
        """Revalidate the complete immutable authorization chain before a write."""

        aggregate_payload = aggregate.payload or {}
        try:
            recomputed = workorder_hash(workorder.payload)
        except (TypeError, ValueError) as exc:
            raise ReleaseServiceError("hash_mismatch", "persisted WorkOrder is not canonical") from exc
        if (
            recomputed != workorder.hash
            or aggregate_payload.get("workorder_hash") != workorder.hash
            or aggregate_payload.get("workorder_id") != workorder.workorder_id
        ):
            raise ReleaseServiceError(
                "hash_mismatch",
                "persisted WorkOrder or release binding was modified after approval",
            )

        approval_id = aggregate_payload.get("approval_id")
        original = self.session.get(Approval, approval_id) if approval_id else None
        if original is None:
            raise ReleaseServiceError("validation_failed", "release initial ApprovalGrant is missing")
        if (
            original.workorder_id != workorder.workorder_id
            or original.workorder_hash != workorder.hash
            or original.nonce != workorder.payload.get("nonce")
            or original.decision != "approved"
            or original.status != "consumed"
            or original.consumed_at is None
        ):
            raise ReleaseServiceError(
                "hash_mismatch",
                "release initial ApprovalGrant binding is invalid",
            )

        now = datetime.now(timezone.utc)
        workorder_expiry = _parse_dt(workorder.payload.get("expiry", ""))
        approval_expiry = original.expiry
        if approval_expiry.tzinfo is None:
            approval_expiry = approval_expiry.replace(tzinfo=timezone.utc)
        if require_fresh and (workorder_expiry <= now or approval_expiry <= now):
            raise ReleaseServiceError(
                "approval_expired",
                "WorkOrder or initial ApprovalGrant expired before lifecycle write",
            )
        if approval_expiry > workorder_expiry:
            raise ReleaseServiceError(
                "hash_mismatch",
                "persisted initial ApprovalGrant outlives its WorkOrder",
            )

        gate = self._gate_call(self.gates.validate_for_workorder, workorder)
        original_payload = original.payload or {}
        if (
            original_payload.get("authorization") is not None
            or original_payload.get("nonce") != workorder.payload.get("nonce")
            or original_payload.get("decision") != "approved"
            or original_payload.get("nonce_consumed") is not True
            or original_payload.get("authorized_target_revision") != gate.target_revision
            or original_payload.get("authorized_gate_binding_digest") != gate.binding_digest
        ):
            raise ReleaseServiceError(
                "hash_mismatch",
                "initial ApprovalGrant no longer matches the GateReport authorization",
            )
        return gate, original

    def _validate_persisted_release_verification(
        self,
        aggregate: Aggregate,
        workorder: WorkOrder,
        *,
        remote_versionset: dict[str, Any] | None = None,
        expected_result: str | None = None,
    ) -> Any:
        """Revalidate the immutable post-canary report before high-impact writes.

        The release projection is only a convenience view.  The persisted
        GateReport remains authoritative and must still hash, bind to the final
        WorkOrder, and target the exact candidate revision approved for this
        action.
        """

        payload = aggregate.payload or {}
        eval_id = payload.get("verification_eval_id")
        report_hash = payload.get("verification_report_hash")
        versionset_id = payload.get("versionset_id")
        if not all(isinstance(value, str) and value for value in (eval_id, report_hash, versionset_id)):
            raise ReleaseServiceError(
                "validation_failed",
                "release is missing its persisted post-canary GateReport binding",
            )
        if remote_versionset is None:
            try:
                remote_versionset = self.quality.get_versionset(versionset_id)
            except QualityAPIError as exc:
                raise ReleaseServiceError(
                    "quality_api_error",
                    f"cannot revalidate post-canary GateReport target: {exc}",
                    quality_code=exc.code,
                ) from exc
        gate = self._gate_call(
            self.gates.validate_release_verification,
            workorder,
            eval_id=eval_id,
            report_hash=report_hash,
            remote_versionset=remote_versionset,
        )
        projected = {
            "verification": gate.overall_status,
            "verification_eval_id": gate.eval_id,
            "verification_report_hash": gate.report_hash,
            "verification_evidence_digest": gate.evidence_digest,
            "verification_target_revision": gate.target_revision,
        }
        if any(payload.get(key) != value for key, value in projected.items()):
            raise ReleaseServiceError(
                "hash_mismatch",
                "release verification projection no longer matches the authoritative GateReport",
            )
        if expected_result is not None and gate.overall_status != expected_result:
            raise ReleaseServiceError(
                "gate_failed",
                f"{expected_result} post-canary verification required; got {gate.overall_status}",
            )
        return gate

    def _expected_action_context(
        self,
        aggregate: Aggregate,
        action: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the controller-owned action parameters a human must approve."""

        payload = aggregate.payload or {}
        if action == "canary":
            if aggregate.state != "STAGING":
                raise ReleaseServiceError(
                    "illegal_transition",
                    f"cannot approve canary from {aggregate.state}",
                )
            steps = self.settings.canary_step_list
            idx = int(payload.get("canary_step_index", 0))
            action_params = {"percent": steps[min(idx, len(steps) - 1)]}
            target_revision = payload.get("remote_revision")
        elif action in ("promote", "rollback"):
            required_verification = "passed" if action == "promote" else "failed"
            allowed_states = ("VERIFYING",) if action == "promote" else ("VERIFYING", "ROLLING_BACK")
            if aggregate.state not in allowed_states or payload.get("verification") != required_verification:
                raise ReleaseServiceError(
                    "illegal_transition",
                    f"cannot approve {action} from {aggregate.state} with verification={payload.get('verification')}",
                )
            action_params = {
                "verification_eval_id": payload.get("verification_eval_id"),
                "verification_report_hash": payload.get("verification_report_hash"),
            }
            if action == "promote":
                action_params = {
                    **action_params,
                    "expected_active_digest": payload.get("expected_restore_digest"),
                }
            else:
                reason = (params or {}).get("reason")
                if not isinstance(reason, str) or not reason.strip():
                    raise ReleaseServiceError(
                        "validation_failed",
                        "rollback action grant must bind a non-empty reason",
                    )
                action_params = {
                    **action_params,
                    "reason": reason,
                    "rollback_to": payload.get("expected_restore_digest"),
                }
            target_revision = payload.get("verification_target_revision")
        else:
            raise ReleaseServiceError("validation_failed", f"unsupported action grant {action}")

        if not isinstance(target_revision, int) or isinstance(target_revision, bool):
            raise ReleaseServiceError(
                "validation_failed",
                f"release is missing authoritative target revision for {action}",
            )
        if any(value is None for value in action_params.values()):
            raise ReleaseServiceError(
                "validation_failed",
                f"release is missing authoritative parameters for {action}",
            )
        return {"target_revision": target_revision, "params": action_params}

    def _consume_action_grant(
        self,
        *,
        approval_id: str | None,
        release_id: str,
        workorder: WorkOrder,
        gate: Any,
        action: str,
        target_revision: int,
        params: dict[str, Any],
        operation_id: str,
        allow_consumed: bool,
    ) -> Approval:
        if not approval_id:
            raise ReleaseServiceError(
                "validation_failed",
                f"{action} is R2_HIGH_IMPACT and requires a fresh ApprovalGrant",
            )
        grant = self.session.scalar(
            select(Approval).where(Approval.approval_id == approval_id).with_for_update()
        )
        if grant is None:
            raise ReleaseServiceError("not_found", f"approval {approval_id} not found")
        stored = grant.payload or {}
        authorization = stored.get("authorization")
        if not isinstance(authorization, dict):
            raise ReleaseServiceError(
                "validation_failed",
                "initial WorkOrder approval cannot authorize an R2 lifecycle action",
            )
        if (
            grant.workorder_id != workorder.workorder_id
            or grant.workorder_hash != workorder.hash
            or grant.decision != "approved"
            or authorization.get("release_id") != release_id
            or authorization.get("action") != action
            or authorization.get("target_revision") != target_revision
            or authorization.get("params") != params
            or authorization.get("params_digest") != canonical_json_digest(params)
            or stored.get("authorized_target_revision") != gate.target_revision
            or stored.get("authorized_gate_binding_digest") != gate.binding_digest
        ):
            raise ReleaseServiceError(
                "hash_mismatch",
                "action ApprovalGrant does not match this release action",
            )

        now = datetime.now(timezone.utc)
        expiry = grant.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        workorder_expiry = _parse_dt(workorder.payload.get("expiry", ""))
        if expiry <= now or workorder_expiry <= now:
            raise ReleaseServiceError("approval_expired", f"{action} ApprovalGrant expired")
        if expiry > workorder_expiry:
            raise ReleaseServiceError("hash_mismatch", "action ApprovalGrant outlives WorkOrder")

        consumed_operation_id = stored.get("consumed_operation_id")
        if grant.status == "consumed":
            if not allow_consumed or consumed_operation_id != operation_id:
                raise ReleaseServiceError("nonce_replay", "action ApprovalGrant nonce already consumed")
            return grant
        if grant.status != "pending" or stored.get("nonce_consumed") is not False:
            raise ReleaseServiceError(
                "validation_failed",
                f"action ApprovalGrant is not executable from status {grant.status}",
            )

        grant.status = "consumed"
        grant.consumed_at = now
        grant.payload = {
            **stored,
            "nonce_consumed": True,
            "consumed_at": now.isoformat(),
            "consumed_operation_id": operation_id,
        }
        self.audit.record(
            actor=(grant.approver or {}).get("identity", "human"),
            action=f"release.{action}.authorization.consume",
            target=release_id,
            params={
                "approval_id": grant.approval_id,
                "operation_id": operation_id,
                "target_revision": target_revision,
                "params_digest": authorization.get("params_digest"),
            },
            result="success",
        )
        self.session.flush()
        return grant

    def _validate_reconcile_operation_authorization(
        self,
        *,
        aggregate: Aggregate,
        release_id: str,
        kind: str,
        operation: ControllerOperation | None,
        workorder: WorkOrder,
        gate: Any,
    ) -> dict[str, Any]:
        """Verify the immutable operation/grant chain without requiring fresh TTL.

        Reconcile observes a side effect already attempted while authorization
        was live. It may run after expiry, but it must never accept a different
        operation, action, target revision, or tampered consumed grant.
        """

        if (
            operation is None
            or operation.release_id != release_id
            or operation.kind != kind
            or operation.status != "unknown"
            or not isinstance(operation.expected_revision, int)
            or isinstance(operation.expected_revision, bool)
        ):
            raise ReleaseServiceError(
                "hash_mismatch",
                "UNKNOWN marker is not bound to the authoritative ControllerOperation",
            )
        if kind == "stage":
            if operation.approval_id is not None:
                raise ReleaseServiceError(
                    "hash_mismatch",
                    "stage operation unexpectedly references an action ApprovalGrant",
                )
            return {}

        verification_target = {
            "versionset_id": (aggregate.payload or {}).get("versionset_id"),
            "digest": workorder.payload.get("target_versionset_digest"),
            "revision": operation.expected_revision,
        }
        if kind in ("promote", "rollback"):
            self._validate_persisted_release_verification(
                aggregate,
                workorder,
                remote_versionset=verification_target,
                expected_result="passed" if kind == "promote" else "failed",
            )

        grant = self.session.get(Approval, operation.approval_id) if operation.approval_id else None
        if grant is None:
            raise ReleaseServiceError(
                "hash_mismatch",
                f"{kind} UNKNOWN operation is missing its consumed ApprovalGrant",
            )
        stored = grant.payload or {}
        authorization = stored.get("authorization")
        payload = aggregate.payload or {}
        if kind == "canary":
            steps = self.settings.canary_step_list
            idx = int(payload.get("canary_step_index", 0))
            expected_params = {"percent": steps[min(idx, len(steps) - 1)]}
            expected_revision = payload.get("remote_revision")
        elif kind == "promote":
            expected_params = {
                "verification_eval_id": payload.get("verification_eval_id"),
                "verification_report_hash": payload.get("verification_report_hash"),
                "expected_active_digest": payload.get("expected_restore_digest"),
            }
            expected_revision = payload.get("verification_target_revision")
        elif kind == "rollback":
            expected_params = {
                "verification_eval_id": payload.get("verification_eval_id"),
                "verification_report_hash": payload.get("verification_report_hash"),
                "reason": payload.get("rollback_reason"),
                "rollback_to": payload.get("expected_restore_digest"),
            }
            expected_revision = payload.get("verification_target_revision")
        else:
            raise ReleaseServiceError("validation_failed", f"unknown reconcile kind {kind}")

        if (
            not isinstance(authorization, dict)
            or grant.status != "consumed"
            or grant.decision != "approved"
            or grant.workorder_id != workorder.workorder_id
            or grant.workorder_hash != workorder.hash
            or stored.get("nonce_consumed") is not True
            or stored.get("consumed_operation_id") != operation.operation_id
            or stored.get("authorized_target_revision") != gate.target_revision
            or stored.get("authorized_gate_binding_digest") != gate.binding_digest
            or authorization.get("release_id") != release_id
            or authorization.get("action") != kind
            or authorization.get("target_revision") != operation.expected_revision
            or authorization.get("target_revision") != expected_revision
            or authorization.get("params") != expected_params
            or authorization.get("params_digest") != canonical_json_digest(expected_params)
        ):
            raise ReleaseServiceError(
                "hash_mismatch",
                f"{kind} UNKNOWN operation ApprovalGrant binding is invalid",
            )
        return expected_params

    def stage(self, release_id: str, *, idempotency_key: str) -> dict[str, Any]:
        return self._write_step(release_id, "stage", idempotency_key=idempotency_key)

    def canary(
        self,
        release_id: str,
        *,
        percent: Optional[int] = None,
        idempotency_key: str,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        return self._write_step(
            release_id,
            "canary",
            idempotency_key=idempotency_key,
            percent=percent,
            approval_id=approval_id,
        )

    def record_verification(self, release_id: str, *, eval_id: str, report_hash: str) -> dict[str, Any]:
        """Attach an independently persisted post-canary GateReport.

        Verification is deliberately separate from ``promote``/``rollback`` so
        neither write path can manufacture its own success or failure signal.
        """

        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")
        payload = agg.payload or {}
        if agg.state == "VERIFYING":
            if payload.get("verification_eval_id") == eval_id and payload.get("verification_report_hash") == report_hash:
                return {
                    "release_id": release_id,
                    "state": agg.state,
                    "revision": agg.revision,
                    "verification": payload.get("verification"),
                    "duplicate": True,
                }
            raise ReleaseServiceError("idempotency_conflict", "release already has a different verification report")
        if agg.state != "CANARYING":
            raise ReleaseServiceError(
                "illegal_transition",
                f"cannot record verification from state {agg.state}",
                current_state=agg.state,
            )

        workorder_id = payload.get("workorder_id")
        versionset_id = payload.get("versionset_id")
        workorder = self.session.get(WorkOrder, workorder_id) if workorder_id else None
        if workorder is None:
            raise ReleaseServiceError("validation_failed", "release WorkOrder is missing")
        if not versionset_id:
            raise ReleaseServiceError("validation_failed", "release VersionSet is missing")
        try:
            remote_versionset = self.quality.get_versionset(versionset_id)
        except QualityAPIError as exc:
            raise ReleaseServiceError("quality_api_error", f"cannot verify canary target: {exc}") from exc
        gate = self._gate_call(
            self.gates.validate_release_verification,
            workorder,
            eval_id=eval_id,
            report_hash=report_hash,
            remote_versionset=remote_versionset,
        )
        result = gate.overall_status
        event_payload = {
            "result": result,
            "probe_set_digest": gate.dataset_digest,
            "eval_id": gate.eval_id,
            "report_hash": gate.report_hash,
            "evidence_digest": gate.evidence_digest,
            "target_revision": gate.target_revision,
        }
        self.store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.verification_completed",
            payload=event_payload,
            correlation_id=release_id,
            actor="controller:release",
            expected_revision=agg.revision,
            machine="release",
            merge_payload={
                "verification": result,
                "verification_eval_id": gate.eval_id,
                "verification_report_hash": gate.report_hash,
                "verification_evidence_digest": gate.evidence_digest,
                "verification_target_revision": gate.target_revision,
            },
        )
        self.audit.record(
            actor="controller:release",
            action="release.verification.record",
            target=release_id,
            params={"eval_id": gate.eval_id, "report_hash": gate.report_hash, "result": result},
            result="success",
            evidence_refs={"evidence_digest": gate.evidence_digest},
        )
        self.session.flush()
        updated = self.store.get_aggregate("release", release_id)
        return {
            "release_id": release_id,
            "state": updated.state if updated else "VERIFYING",
            "revision": updated.revision if updated else agg.revision + 1,
            "verification": result,
            "duplicate": False,
        }

    def promote(
        self,
        release_id: str,
        *,
        idempotency_key: str,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """Promote only after a persisted verification GateReport passed."""
        return self._write_step(
            release_id,
            "promote",
            idempotency_key=idempotency_key,
            approval_id=approval_id,
        )

    def rollback(
        self,
        release_id: str,
        *,
        reason: str = "manual",
        idempotency_key: str,
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        """回滚：仅可从灰度观察态（CANARYING / VERIFYING）触发；ROLLING_BACK 可重试。"""
        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")

        if agg.state not in ("VERIFYING", "ROLLING_BACK"):
            raise ReleaseServiceError(
                "illegal_transition",
                f"cannot rollback from state {agg.state}",
                current_state=agg.state,
            )

        return self._write_step(
            release_id,
            "rollback",
            idempotency_key=idempotency_key,
            reason=reason,
            approval_id=approval_id,
        )

    def _write_step(
        self,
        release_id: str,
        kind: str,
        *,
        idempotency_key: str,
        percent: Optional[int] = None,
        reason: str = "",
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")

        vs_id = (agg.payload or {}).get("versionset_id")
        if not vs_id:
            raise ReleaseServiceError("validation_failed", "release missing versionset_id")

        payload = agg.payload or {}

        request_fingerprint = canonical_json_digest(
            {
                "release_id": release_id,
                "kind": kind,
                "versionset_id": vs_id,
                "percent": percent if kind == "canary" else None,
                "reason": reason if kind == "rollback" else None,
                "approval_id": approval_id if kind != "stage" else None,
                "expected_active_digest": (
                    payload.get("expected_restore_digest") if kind == "promote" else None
                ),
            },
            prefix=False,
        )

        # Idempotency is same key + same semantic request, never key alone.
        existing_op = self.session.scalar(
            select(ControllerOperation).where(ControllerOperation.idempotency_key == idempotency_key)
        )
        if existing_op is not None:
            if existing_op.release_id != release_id or existing_op.kind != kind:
                raise ReleaseServiceError(
                    "idempotency_conflict",
                    "Idempotency-Key was already used for a different release operation",
                )
            if existing_op.approval_id != (approval_id if kind != "stage" else None):
                raise ReleaseServiceError(
                    "idempotency_conflict",
                    "Idempotency-Key was reused with a different action ApprovalGrant",
                )
            if existing_op.request_fingerprint != request_fingerprint:
                raise ReleaseServiceError(
                    "idempotency_conflict",
                    "Idempotency-Key was reused with different operation parameters",
                )
            return {
                "release_id": release_id,
                "operation_id": existing_op.operation_id,
                "status": existing_op.status,
                "state": agg.state,
                "duplicate": True,
            }

        workorder_id = (agg.payload or {}).get("workorder_id")
        workorder = self.session.get(WorkOrder, workorder_id) if workorder_id else None
        if workorder is None:
            raise ReleaseServiceError("validation_failed", "release missing registered WorkOrder")
        # Re-check the full WorkOrder, initial ApprovalGrant and GateReport chain
        # before every Quality API write. A start-time check is not sufficient:
        # the authorization can expire or stored JSON can be tampered later.
        initial_gate, _ = self._validate_original_release_authorization(agg, workorder)
        action_context: dict[str, Any] | None = None
        if kind == "stage":
            self._validate_step_state(agg, kind)
        else:
            action_context = self._expected_action_context(
                agg,
                kind,
                params={"reason": reason} if kind == "rollback" else None,
            )
            if kind != "rollback":
                self._validate_step_state(agg, kind)

        # Read the remote candidate, then require it to be the exact gated object
        # at the exact revision expected for this lifecycle step.  Reading the
        # latest revision and blindly using it as If-Match would authorize drift.
        try:
            vs = self.quality.get_versionset(vs_id)
        except QualityAPIError as exc:
            raise ReleaseServiceError("quality_api_error", str(exc)) from exc

        current_rev = vs.get("revision")
        if not isinstance(current_rev, int) or isinstance(current_rev, bool) or current_rev <= 0:
            raise ReleaseServiceError(
                "quality_api_error",
                "Quality API returned a missing or invalid candidate revision",
            )

        if vs.get("versionset_id") != vs_id or vs.get("digest") != workorder.payload.get(
            "target_versionset_digest"
        ):
            raise ReleaseServiceError(
                "target_mismatch",
                "Quality API candidate no longer matches the gated WorkOrder target",
            )

        if kind == "stage":
            expected_revision = initial_gate.target_revision
        else:
            assert action_context is not None
            expected_revision = action_context["target_revision"]
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool):
            raise ReleaseServiceError(
                "validation_failed",
                f"release is missing the authoritative expected revision for {kind}",
            )
        if current_rev != expected_revision:
            raise ReleaseServiceError(
                "revision_conflict",
                f"cannot {kind}: expected gated revision {expected_revision}, current revision {current_rev}",
                expected_revision=expected_revision,
                current_revision=current_rev,
            )

        if kind in ("promote", "rollback"):
            self._validate_persisted_release_verification(
                agg,
                workorder,
                remote_versionset=vs,
                expected_result="passed" if kind == "promote" else "failed",
            )

        requested_canary_percent: int | None = None
        if kind == "canary":
            steps = self.settings.canary_step_list
            idx = int(payload.get("canary_step_index", 0))
            requested_canary_percent = steps[min(idx, len(steps) - 1)]
            if percent is not None and percent != requested_canary_percent:
                raise ReleaseServiceError(
                    "validation_failed",
                    f"canary percent {percent} is not the controller-owned next step {requested_canary_percent}",
                )

        if_match = str(current_rev)
        local_op_id = new_operation_id()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.settings.operation_ttl_hours)

        if kind != "stage":
            assert action_context is not None
            self._consume_action_grant(
                approval_id=approval_id,
                release_id=release_id,
                workorder=workorder,
                gate=initial_gate,
                action=kind,
                target_revision=expected_revision,
                params=action_context["params"],
                operation_id=local_op_id,
                allow_consumed=False,
            )

        if kind == "rollback" and agg.state == "VERIFYING":
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.rollback_started",
                payload={
                    "rollback_to": (agg.payload or {}).get("expected_restore_digest"),
                    "reason": reason,
                    "approval_id": approval_id,
                },
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                guard="verification=failed",
                merge_payload={"rollback_reason": reason, "rollback_approval_id": approval_id},
            )
            agg = self.store.get_aggregate("release", release_id)
            assert agg is not None
            self._validate_step_state(agg, kind)

        # Fail before the external side effect when the authoritative audit sink
        # cannot accept an operation intent.  P0-2 turns this intent into a
        # transactional command outbox for crash-safe delivery/receipt handling.
        self.audit.record(
            actor="controller:release",
            action=f"release.{kind}.intent",
            target=release_id,
            params={
                "idempotency_key": idempotency_key,
                "versionset_id": vs_id,
                "expected_revision": current_rev,
            },
            result="pending",
        )

        cop = ControllerOperation(
            operation_id=local_op_id,
            release_id=release_id,
            kind=kind,
            status="pending",
            idempotency_key=idempotency_key,
            approval_id=approval_id if kind != "stage" else None,
            expected_revision=current_rev,
            request_fingerprint=request_fingerprint,
            created_at=now,
            expires_at=expires,
        )
        self.session.add(cop)
        self.session.flush()

        remote_op: Optional[dict[str, Any]] = None
        unknown = False
        try:
            if kind == "stage":
                remote_op = self.quality.stage(vs_id, if_match=if_match, idempotency_key=idempotency_key)
            elif kind == "canary":
                assert requested_canary_percent is not None
                remote_op = self.quality.canary(
                    vs_id,
                    requested_canary_percent,
                    if_match=if_match,
                    idempotency_key=idempotency_key,
                )
            elif kind == "promote":
                assert action_context is not None
                remote_op = self.quality.promote(
                    vs_id,
                    action_context["params"]["expected_active_digest"],
                    if_match=if_match,
                    idempotency_key=idempotency_key,
                )
            elif kind == "rollback":
                expected_restore_digest = workorder.payload.get("base_versionset_digest")
                if not isinstance(expected_restore_digest, str):
                    raise ReleaseServiceError(
                        "validation_failed",
                        "WorkOrder is missing the approved rollback target digest",
                    )
                remote_op = self.quality.rollback(
                    vs_id,
                    expected_restore_digest,
                    if_match=if_match,
                    idempotency_key=idempotency_key,
                )
            else:
                raise ReleaseServiceError("validation_failed", f"unknown kind {kind}")
        except QualityAPIError as exc:
            if exc.status_code in (0, 410) or exc.code in ("network_error", "operation_expired"):
                unknown = True
            else:
                cop.status = "failed"
                cop.result = {"error": exc.code, "message": exc.message}
                self.session.flush()
                if kind == "rollback":
                    return self._escalate_rollback_failure(
                        release_id,
                        operation_id=local_op_id,
                        reason=f"Quality API rejected rollback: {exc.code}",
                    )
                raise ReleaseServiceError("quality_api_error", str(exc), quality_code=exc.code) from exc

        if unknown:
            return self._enter_unknown(release_id, local_op_id, kind, last_known=agg.state)

        assert remote_op is not None
        remote_id = remote_op.get("operation_id")
        cop.remote_operation_id = remote_id
        remote_status = remote_op.get("status", "unknown")

        if remote_status in ("pending", "running"):
            remote_status, remote_op = self._poll_operation(remote_id or "", local_op_id)

        if remote_status == "unknown":
            return self._enter_unknown(release_id, local_op_id, kind, last_known=agg.state)

        if remote_status != "succeeded":
            cop.status = "failed"
            cop.result = remote_op
            self.session.flush()
            if kind == "rollback":
                return self._escalate_rollback_failure(
                    release_id,
                    operation_id=local_op_id,
                    reason=f"rollback operation ended {remote_status}",
                )
            raise ReleaseServiceError("quality_api_error", f"operation {remote_status}", result=remote_op)

        if not self._valid_remote_receipt(
            kind,
            remote_op,
            expected_operation_id=remote_id,
            expected_versionset_id=vs_id,
            expected_revision=current_rev + 1,
            expected_canary_percent=requested_canary_percent,
            expected_restore_digest=(
                workorder.payload.get("base_versionset_digest") if kind == "rollback" else None
            ),
        ):
            return self._enter_unknown(release_id, local_op_id, kind, last_known=agg.state)

        cop.status = "succeeded"
        cop.result = remote_op
        result = self._apply_success(release_id, kind, remote_op, percent=percent)
        result["operation_id"] = local_op_id  # 幂等跟踪用本地 operation_id（get_operation 用它查）
        self.audit.record(
            actor="controller:release",
            action=f"release.{kind}",
            target=release_id,
            params={"idempotency_key": idempotency_key, "remote_operation_id": remote_id},
            result="success",
        )
        self.session.flush()
        return result

    @staticmethod
    def _validate_step_state(agg: Aggregate, kind: str) -> None:
        expected: dict[str, tuple[str, str | None]] = {
            "stage": ("REQUESTED", None),
            "canary": ("STAGING", None),
            "promote": ("VERIFYING", "passed"),
            "rollback": ("ROLLING_BACK", "failed"),
        }
        required_state, verification = expected[kind]
        payload = agg.payload or {}
        actual_verification = payload.get("verification")
        if payload.get("unknown_op"):
            raise ReleaseServiceError(
                "illegal_transition",
                f"cannot {kind} while a prior operation is UNKNOWN; reconcile is required",
                current_state=agg.state,
            )
        if agg.state != required_state or (verification is not None and actual_verification != verification):
            raise ReleaseServiceError(
                "illegal_transition",
                f"cannot {kind} from state {agg.state} with verification={actual_verification}",
                current_state=agg.state,
            )

    @staticmethod
    def _valid_remote_receipt(
        kind: str,
        remote_op: dict[str, Any],
        *,
        expected_operation_id: str | None,
        expected_versionset_id: str,
        expected_revision: int,
        expected_canary_percent: int | None,
        expected_restore_digest: str | None,
    ) -> bool:
        operation_id = remote_op.get("operation_id")
        result = remote_op.get("result")
        if (
            not isinstance(operation_id, str)
            or not operation_id
            or operation_id != expected_operation_id
            or remote_op.get("status") != "succeeded"
            or remote_op.get("kind") != kind
            or remote_op.get("versionset_id") != expected_versionset_id
            or not isinstance(result, dict)
        ):
            return False
        revision = result.get("revision")
        if (
            not isinstance(revision, int)
            or isinstance(revision, bool)
            or revision != expected_revision
        ):
            return False
        expected_status = {
            "stage": "staged",
            "canary": "canary",
            "promote": "active",
            "rollback": "rolled_back",
        }[kind]
        if result.get("status") != expected_status:
            return False
        if kind == "canary" and result.get("canary_percent") != expected_canary_percent:
            return False
        if kind == "rollback":
            restored = result.get("restored_digest")
            if (
                not isinstance(restored, str)
                or not restored.startswith("sha256:")
                or len(restored) != 71
                or any(char not in "0123456789abcdef" for char in restored.removeprefix("sha256:"))
                or restored != expected_restore_digest
            ):
                return False
        return True

    def _poll_operation(self, remote_id: str, local_op_id: str) -> tuple[str, dict[str, Any]]:
        if not remote_id:
            return "unknown", {}
        deadline = time.time() + self.settings.operation_poll_timeout_seconds
        last: dict[str, Any] = {}
        while time.time() < deadline:
            try:
                last = self.quality.get_operation(remote_id)
                st = last.get("status", "pending")
                if st in ("succeeded", "failed"):
                    return st, last
            except QualityAPIError as exc:
                if exc.status_code == 410 or exc.code == "network_error":
                    return "unknown", {}
            time.sleep(0.05)
        return "unknown", last

    def _enter_unknown(self, release_id: str, op_id: str, kind: str, last_known: str) -> dict[str, Any]:
        agg = self.store.get_aggregate("release", release_id)
        assert agg is not None
        cop = self.session.get(ControllerOperation, op_id)
        if cop:
            cop.status = "unknown"
        expected_state = {
            "stage": "REQUESTED",
            "canary": "STAGING",
            "promote": "VERIFYING",
            "rollback": "ROLLING_BACK",
        }.get(kind)
        if expected_state is None or agg.state != expected_state:
            raise ReleaseServiceError(
                "illegal_transition",
                f"cannot record UNKNOWN for {kind} from state {agg.state}",
                current_state=agg.state,
            )
        self.store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.unknown_detected",
            payload={"operation_id": op_id, "last_known": last_known, "kind": kind},
            correlation_id=release_id,
            actor="controller:release",
            expected_revision=agg.revision,
            machine="release",
            merge_payload={"unknown_op": op_id, "unknown_kind": kind},
        )

        self.audit.record(
            actor="controller:release",
            action="release.unknown_detected",
            target=release_id,
            params={"operation_id": op_id, "kind": kind},
            result="success",
        )
        agg = self.store.get_aggregate("release", release_id)
        return {
            "release_id": release_id,
            "state": agg.state if agg else "UNKNOWN",
            "status": "unknown",
            "operation_id": op_id,
            "reconcile_required": True,
        }

    def _escalate_rollback_failure(
        self,
        release_id: str,
        *,
        operation_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Persist the contract terminal state for a failed rollback."""

        aggregate = self.store.get_aggregate("release", release_id)
        if aggregate is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")
        if aggregate.state not in ("ROLLING_BACK", "UNKNOWN"):
            raise ReleaseServiceError(
                "illegal_transition",
                f"cannot record rollback failure from {aggregate.state}",
            )
        self.store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.rollback_failed",
            payload={"reason": reason, "escalated": True, "operation_id": operation_id},
            correlation_id=release_id,
            actor="controller:release",
            expected_revision=aggregate.revision,
            machine="release",
            merge_payload={
                "rollback_failed": True,
                "rollback_failure_reason": reason,
                "manual_intervention_required": True,
            },
        )
        self.audit.record(
            actor="controller:release",
            action="release.rollback_failed",
            target=release_id,
            params={"operation_id": operation_id, "reason": reason},
            result="escalated",
        )
        self.session.flush()
        updated = self.store.get_aggregate("release", release_id)
        return {
            "release_id": release_id,
            "state": updated.state if updated else "FAILED_ESCALATED",
            "status": "failed",
            "operation_id": operation_id,
            "manual_intervention_required": True,
        }

    # ---------- reconcile（UNKNOWN→对账，指数退避由调用方循环） ----------

    def reconcile(self, release_id: str) -> dict[str, Any]:
        """Resolve UNKNOWN by replaying the exact authorized Quality operation.

        A status read alone cannot distinguish an operation that was never
        accepted from one that is still pending and may apply later.  Reconcile
        therefore replays the original Idempotency-Key and original request,
        then requires an operation-bound transition in ``status.history``
        before leaving UNKNOWN.  An unproven or still-pending outcome remains
        fail-closed in UNKNOWN.
        """
        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")
        if agg.state != "UNKNOWN" and not (agg.payload or {}).get("unknown_op"):
            raise ReleaseServiceError(
                "illegal_transition", f"cannot reconcile from {agg.state}", current_state=agg.state
            )

        vs_id = (agg.payload or {}).get("versionset_id")
        kind = (agg.payload or {}).get("unknown_kind", "stage")
        op_id = (agg.payload or {}).get("unknown_op")
        if not isinstance(vs_id, str) or not isinstance(op_id, str):
            raise ReleaseServiceError(
                "hash_mismatch",
                "UNKNOWN release is missing its target or ControllerOperation marker",
            )

        workorder_id = (agg.payload or {}).get("workorder_id")
        workorder = self.session.get(WorkOrder, workorder_id) if workorder_id else None
        if workorder is None:
            raise ReleaseServiceError("validation_failed", "reconcile release WorkOrder is missing")
        initial_gate, _ = self._validate_original_release_authorization(
            agg,
            workorder,
            require_fresh=False,
        )
        cop = self.session.get(ControllerOperation, op_id) if op_id else None
        authorized_params = self._validate_reconcile_operation_authorization(
            aggregate=agg,
            release_id=release_id,
            kind=kind,
            operation=cop,
            workorder=workorder,
            gate=initial_gate,
        )
        assert cop is not None
        expected_before = cop.expected_revision
        assert isinstance(expected_before, int) and not isinstance(expected_before, bool)

        # The replay itself may cause the previously accepted operation to run,
        # so audit the intent before making the external call.
        self.audit.record(
            actor="controller:release",
            action="release.reconcile.replay.intent",
            target=release_id,
            params={
                "operation_id": op_id,
                "kind": kind,
                "idempotency_key": cop.idempotency_key,
                "expected_revision": expected_before,
            },
            result="pending",
        )
        self.session.flush()

        replay: dict[str, Any] = {}
        replay_error: QualityAPIError | None = None
        try:
            if kind == "stage":
                replay = self.quality.stage(
                    vs_id,
                    if_match=str(expected_before),
                    idempotency_key=cop.idempotency_key,
                )
            elif kind == "canary":
                replay = self.quality.canary(
                    vs_id,
                    int(authorized_params["percent"]),
                    if_match=str(expected_before),
                    idempotency_key=cop.idempotency_key,
                )
            elif kind == "promote":
                replay = self.quality.promote(
                    vs_id,
                    str(authorized_params["expected_active_digest"]),
                    if_match=str(expected_before),
                    idempotency_key=cop.idempotency_key,
                )
            elif kind == "rollback":
                replay = self.quality.rollback(
                    vs_id,
                    str(authorized_params["rollback_to"]),
                    if_match=str(expected_before),
                    idempotency_key=cop.idempotency_key,
                )
            else:
                raise ReleaseServiceError("validation_failed", f"unknown reconcile kind {kind}")
        except QualityAPIError as exc:
            # A transport error, expired operation record, or terminal rejection
            # is not permission to infer success.  A known remote operation may
            # still be proven below by exact transition history.
            replay_error = exc

        remote_id = replay.get("operation_id") if isinstance(replay, dict) else None
        if remote_id is not None and (not isinstance(remote_id, str) or not remote_id):
            raise ReleaseServiceError("hash_mismatch", "Quality replay returned an invalid operation identity")
        if cop.remote_operation_id and remote_id and cop.remote_operation_id != remote_id:
            raise ReleaseServiceError(
                "hash_mismatch",
                "exact Idempotency-Key replay returned a different Quality operation",
            )
        if remote_id:
            cop.remote_operation_id = remote_id
        remote_id = cop.remote_operation_id

        replay_status = replay.get("status") if isinstance(replay, dict) else None
        receipt = replay
        if replay_status in ("pending", "running") and isinstance(remote_id, str):
            replay_status, polled = self._poll_operation(remote_id, op_id)
            if polled:
                receipt = polled

        try:
            vs = self.quality.get_versionset(vs_id)
            status_view = self.quality.get_status(vs_id)
        except QualityAPIError as exc:
            raise ReleaseServiceError("quality_api_error", f"reconcile status failed: {exc}") from exc

        remote_revision = vs.get("revision")
        remote_status = status_view.get("status")
        if (
            vs.get("versionset_id") != vs_id
            or vs.get("digest") != workorder.payload.get("target_versionset_digest")
            or status_view.get("versionset_id") != vs_id
            or status_view.get("revision") != remote_revision
            or remote_status != vs.get("status")
            or not isinstance(remote_revision, int)
            or isinstance(remote_revision, bool)
        ):
            raise ReleaseServiceError(
                "target_mismatch",
                "reconcile target does not match the approved WorkOrder",
            )

        expected_status = {
            "stage": "staged",
            "canary": "canary",
            "promote": "active",
            "rollback": "rolled_back",
        }[kind]
        history = status_view.get("history")
        exact_history = None
        if isinstance(history, list) and isinstance(remote_id, str):
            exact_history = next(
                (
                    item
                    for item in reversed(history)
                    if isinstance(item, dict)
                    and item.get("operation_id") == remote_id
                    and item.get("to") == expected_status
                ),
                None,
            )

        applied = (
            exact_history is not None
            and remote_status == expected_status
            and remote_revision == expected_before + 1
        )
        if applied and replay_status == "failed":
            raise ReleaseServiceError(
                "hash_mismatch",
                "Quality reports a failed operation whose transition is nevertheless visible; state remains UNKNOWN",
            )
        if applied and replay_error is not None and not (
            replay_error.status_code in (0, 404, 410)
            or replay_error.code in ("network_error", "not_found", "operation_expired")
        ):
            raise ReleaseServiceError(
                "quality_api_error",
                "Quality replay rejection conflicts with the observed operation history; state remains UNKNOWN",
                quality_code=replay_error.code,
            )
        if not applied:
            safe_before_statuses = {
                "stage": {"draft"},
                "canary": {"staged"},
                "promote": {"canary"},
                "rollback": {"canary", "active"},
            }[kind]
            if remote_revision != expected_before or remote_status not in safe_before_statuses:
                raise ReleaseServiceError(
                    "revision_conflict",
                    "reconcile observed un-attributed target drift; state remains UNKNOWN",
                    expected_revision=expected_before,
                    current_revision=remote_revision,
                )
            cop.result = {
                "reconciled": False,
                "remote_status": remote_status,
                "remote_operation_status": replay_status,
                **(
                    {"replay_error": replay_error.code}
                    if replay_error is not None
                    else {}
                ),
            }
            self.audit.record(
                actor="controller:release",
                action="release.reconcile.pending",
                target=release_id,
                params={
                    "operation_id": op_id,
                    "remote_operation_id": remote_id,
                    "remote_status": remote_status,
                    "remote_operation_status": replay_status,
                },
                result="unknown",
            )
            self.session.flush()
            return {
                "release_id": release_id,
                "state": "UNKNOWN",
                "remote_status": remote_status,
                "action": "wait",
                "revision": agg.revision,
                "operation_id": op_id,
                "remote_operation_id": remote_id,
            }

        if not isinstance(remote_id, str):
            raise ReleaseServiceError(
                "hash_mismatch",
                "applied transition is not attributable to the authorized Quality operation",
            )

        canary_percent: int | None = None
        restored_digest: str | None = None
        if kind == "canary":
            canary_obj = status_view.get("canary")
            canary_percent = (
                canary_obj.get("percent")
                if isinstance(canary_obj, dict)
                else status_view.get("canary_percent")
            )
            if canary_percent != authorized_params.get("percent"):
                raise ReleaseServiceError(
                    "target_mismatch",
                    "reconcile canary percent does not match the consumed ApprovalGrant",
                )
        if kind == "rollback":
            try:
                active = self.quality.list_versionsets(status="active", limit=50).get("items") or []
            except QualityAPIError as exc:
                raise ReleaseServiceError(
                    "quality_api_error", f"cannot resolve rollback receipt during reconcile: {exc}"
                ) from exc
            expected_restore_digest = workorder.payload.get("base_versionset_digest")
            restored = next(
                (
                    item
                    for item in active
                    if isinstance(item, dict) and item.get("digest") == expected_restore_digest
                ),
                None,
            )
            restored_digest = restored.get("digest") if restored else None
            if restored_digest != expected_restore_digest:
                raise ReleaseServiceError(
                    "target_mismatch",
                    "rollback did not restore the WorkOrder-approved active baseline",
                )

        if replay_status == "succeeded":
            if not self._valid_remote_receipt(
                kind,
                receipt,
                expected_operation_id=remote_id,
                expected_versionset_id=vs_id,
                expected_revision=expected_before + 1,
                expected_canary_percent=canary_percent,
                expected_restore_digest=restored_digest,
            ):
                raise ReleaseServiceError(
                    "hash_mismatch",
                    "Quality operation receipt does not match its transition history",
                )
            authoritative_receipt = receipt
        else:
            # The transition history is itself an operation-bound authoritative
            # receipt.  This covers read-path lag or a purged operation record
            # without substituting the local ControllerOperation id.
            authoritative_receipt = {
                "operation_id": remote_id,
                "status": "succeeded",
                "kind": kind,
                "versionset_id": vs_id,
                "result": {
                    "revision": remote_revision,
                    "status": remote_status,
                    "canary_percent": canary_percent,
                    **({"restored_digest": restored_digest} if restored_digest else {}),
                },
                "receipt_source": "status_history",
            }

        action, guard = {
            "stage": ("resume", "action=resume"),
            "canary": ("apply_canary", "action=apply_canary"),
            "promote": ("confirm_promote", "action=confirm_promote"),
            "rollback": ("compensate", "action=compensate"),
        }[kind]
        current = self.store.get_aggregate("release", release_id)
        assert current is not None and current.state == "UNKNOWN"
        self.store.append_event(
            aggregate_type="release",
            aggregate_id=release_id,
            event_type="release.reconciled",
            payload={"operation_id": remote_id, "resolved_status": remote_status, "action": action},
            correlation_id=release_id,
            actor="controller:release",
            expected_revision=current.revision,
            machine="release",
            guard=guard,
            merge_payload={"reconciled": True, "remote_status": remote_status},
        )
        self._apply_success(release_id, kind, authoritative_receipt, percent=canary_percent)
        self._clear_unknown_marker(release_id, remote_status)
        cop.status = "succeeded"
        cop.result = {**authoritative_receipt, "reconciled": True}
        self.audit.record(
            actor="controller:release",
            action="release.reconciled",
            target=release_id,
            params={
                "operation_id": op_id,
                "remote_operation_id": remote_id,
                "remote_status": remote_status,
                "action": action,
            },
            result="success",
        )
        self.session.flush()
        updated = self.store.get_aggregate("release", release_id)
        return {
            "release_id": release_id,
            "state": updated.state if updated else None,
            "remote_status": remote_status,
            "action": action,
            "revision": updated.revision if updated else None,
            "operation_id": op_id,
            "remote_operation_id": remote_id,
        }

    def reconcile_loop(self, release_id: str, max_attempts: int = 5) -> dict[str, Any]:
        """带指数退避的 reconcile 循环（5s 起，5min 上限；测试中可配小）。"""
        delay = float(self.settings.reconcile_backoff_initial_seconds)
        max_delay = float(self.settings.reconcile_backoff_max_seconds)
        last: dict[str, Any] = {}
        for attempt in range(max_attempts):
            try:
                last = self.reconcile(release_id)
                if last.get("state") != "UNKNOWN":
                    return last
            except ReleaseServiceError as exc:
                if attempt == max_attempts - 1:
                    aggregate = self.store.get_aggregate("release", release_id)
                    payload = (aggregate.payload or {}) if aggregate is not None else {}
                    if payload.get("unknown_kind") == "rollback":
                        return self._escalate_rollback_failure(
                            release_id,
                            operation_id=str(payload.get("unknown_op") or "unknown"),
                            reason=f"rollback reconcile did not converge: {exc.code}",
                        )
                    raise
            time.sleep(min(delay, max_delay))
            delay = min(delay * 2, max_delay)
        aggregate = self.store.get_aggregate("release", release_id)
        payload = (aggregate.payload or {}) if aggregate is not None else {}
        if aggregate is not None and aggregate.state == "UNKNOWN" and payload.get("unknown_kind") == "rollback":
            return self._escalate_rollback_failure(
                release_id,
                operation_id=str(payload.get("unknown_op") or "unknown"),
                reason="rollback reconcile exhausted without a terminal observation",
            )
        return last

    def _clear_unknown_marker(self, release_id: str, remote_status: str) -> None:
        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            return
        p = dict(agg.payload or {})
        p.pop("unknown_op", None)
        p.pop("unknown_kind", None)
        p["reconciled"] = True
        p["remote_status"] = remote_status
        agg.payload = p
        self.session.flush()

    def _apply_success(
        self,
        release_id: str,
        kind: str,
        remote_op: dict[str, Any],
        percent: Optional[int] = None,
    ) -> dict[str, Any]:
        agg = self.store.get_aggregate("release", release_id)
        assert agg is not None
        result = remote_op.get("result") or remote_op
        rev = int(result["revision"])
        vs_id = (agg.payload or {}).get("versionset_id")

        if kind == "stage":
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.staged",
                payload={"versionset_id": vs_id, "revision": rev, "operation_id": remote_op.get("operation_id", "")},
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                merge_payload={"remote_revision": rev},
            )
        elif kind == "canary":
            steps = self.settings.canary_step_list
            idx = int((agg.payload or {}).get("canary_step_index", 0))
            pct = percent if percent is not None else int(result.get("canary_percent") or steps[min(idx, len(steps) - 1)])
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.canary_started",
                payload={
                    "versionset_id": vs_id,
                    "revision": rev,
                    "percent": pct,
                    "operation_id": remote_op.get("operation_id", ""),
                },
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                merge_payload={"canary_percent": pct, "canary_step_index": idx + 1, "remote_revision": rev},
            )
        elif kind == "promote":
            if agg.state != "VERIFYING" or (agg.payload or {}).get("verification") != "passed":
                raise ReleaseServiceError("illegal_transition", "promote requires a passed verification GateReport")
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.promoted",
                payload={"versionset_id": vs_id, "revision": rev, "operation_id": remote_op.get("operation_id", "")},
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                guard="verification=passed",
                merge_payload={"remote_revision": rev, "promoted": True},
            )
        elif kind == "rollback":
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.rolled_back",
                payload={
                    "restored_digest": result["restored_digest"],
                    "operation_id": remote_op.get("operation_id", ""),
                },
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                merge_payload={"rolled_back": True},
            )

        agg = self.store.get_aggregate("release", release_id)
        return {
            "release_id": release_id,
            "state": agg.state if agg else None,
            "revision": agg.revision if agg else None,
            "status": "succeeded",
            "kind": kind,
            "operation_id": remote_op.get("operation_id", ""),
            "payload": agg.payload if agg else {},
        }

    def get_release(self, release_id: str) -> dict[str, Any]:
        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")
        return {
            "release_id": release_id,
            "state": agg.state,
            "revision": agg.revision,
            "payload": agg.payload,
        }

    def list_releases(self, *, state: Optional[str] = None, limit: int = 100, cursor: int = 0) -> dict[str, Any]:
        q = select(Aggregate).where(Aggregate.aggregate_type == "release").order_by(Aggregate.aggregate_id)
        if state:
            q = q.where(Aggregate.state == state)
        rows = list(self.session.scalars(q.offset(cursor).limit(limit)).all())
        return {
            "items": [
                {"release_id": r.aggregate_id, "state": r.state, "revision": r.revision}
                for r in rows
            ],
            "next_cursor": cursor + len(rows) if len(rows) == limit else None,
        }

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        op = self.session.get(ControllerOperation, operation_id)
        if op is None:
            raise ReleaseServiceError("not_found", f"operation {operation_id} not found")
        now = datetime.now(timezone.utc)
        exp = op.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now:
            return {
                "operation_id": operation_id,
                "status": "expired",
                "expired": True,
            }
        return {
            "operation_id": op.operation_id,
            "release_id": op.release_id,
            "kind": op.kind,
            "status": op.status,
            "remote_operation_id": op.remote_operation_id,
            "result": op.result,
            "expires_at": op.expires_at.isoformat() if op.expires_at else None,
        }


def _parse_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
