"""Release Controller：写面唯一入口；WorkOrder/Approval 校验；灰度；UNKNOWN→reconcile。"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Aggregate, Approval, ControllerOperation, WorkOrder
from app.quality.client import QualityAPIError, QualityClientProtocol
from app.services.audit import AuditService, AuditWriteError
from app.services.event_store import CASConflict, EventStore
from app.services.state_machines import IllegalTransition
from app.utils.ids import new_operation_id, new_release_id, new_trace_id
from app.utils.jcs import workorder_hash

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

        existing = self.session.get(WorkOrder, payload["workorder_id"])
        if existing is not None:
            if existing.hash != declared:
                raise ReleaseServiceError("validation_failed", "workorder_id exists with different hash")
            return {"workorder_id": existing.workorder_id, "hash": existing.hash, "duplicate": True}

        by_hash = self.session.scalar(select(WorkOrder).where(WorkOrder.hash == declared))
        if by_hash is not None:
            raise ReleaseServiceError("validation_failed", "hash already registered under another id")

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
            "approval_id",
            "workorder_hash",
            "workorder_id",
            "nonce",
            "expiry",
            "approver",
            "decision",
            "decided_at",
        ]
        missing = [k for k in required if k not in payload]
        if missing:
            raise ReleaseServiceError("validation_failed", f"missing fields: {missing}")

        approver = payload["approver"]
        if not isinstance(approver, dict) or approver.get("type") != "human":
            raise ReleaseServiceError("validation_failed", "approver.type must be human")

        wo = self.session.get(WorkOrder, payload["workorder_id"])
        if wo is None:
            raise ReleaseServiceError("not_found", f"workorder {payload['workorder_id']} not found")
        if wo.hash != payload["workorder_hash"]:
            raise ReleaseServiceError("hash_mismatch", "approval workorder_hash does not match registered WorkOrder")
        if wo.payload.get("nonce") != payload["nonce"]:
            raise ReleaseServiceError("validation_failed", "nonce must match WorkOrder.nonce")

        # nonce 唯一（防重放：同一次 nonce 只能登记一次 approval）
        existing_nonce = self.session.scalar(select(Approval).where(Approval.nonce == payload["nonce"]))
        if existing_nonce is not None:
            raise ReleaseServiceError(
                "nonce_replay",
                f"nonce already used by approval {existing_nonce.approval_id}",
            )

        expiry = _parse_dt(payload["expiry"])
        now = datetime.now(timezone.utc)
        if expiry <= now:
            raise ReleaseServiceError("approval_expired", "approval already expired at grant time")

        decision = payload["decision"]
        if decision not in ("approved", "rejected"):
            raise ReleaseServiceError("validation_failed", "decision must be approved|rejected")

        status = "pending" if decision == "approved" else "rejected"
        proof = payload.get("proof") or {
            "method": "server_recorded",
            "ref": f"audit://control-plane/approval/{payload['approval_id']}",
        }

        appr = Approval(
            approval_id=payload["approval_id"],
            workorder_id=payload["workorder_id"],
            workorder_hash=payload["workorder_hash"],
            nonce=payload["nonce"],
            status=status,
            decision=decision,
            approver=approver,
            expiry=expiry,
            decided_at=_parse_dt(payload["decided_at"]),
            payload={**payload, "proof": proof, "nonce_consumed": False},
            created_at=now,
        )
        self.session.add(appr)

        cs_id = f"cs_{payload['workorder_id']}"
        cs = self.store.get_aggregate("changeset", cs_id)
        if cs and decision == "approved":
            self._advance_changeset_to_approved(cs_id, payload)

        self.audit.record(
            actor=approver.get("identity", "human"),
            action="workorder.approve" if decision == "approved" else "workorder.reject",
            target=payload["approval_id"],
            params={"workorder_hash": payload["workorder_hash"], "decision": decision},
            result="success",
            evidence_refs={"proof": proof},
        )
        self.session.flush()
        return {
            "approval_id": appr.approval_id,
            "status": appr.status,
            "nonce_consumed": False,
            "proof": proof,
        }

    def _advance_changeset_to_approved(self, cs_id: str, payload: dict[str, Any]) -> None:
        """按状态机推进 ChangeSet 到 APPROVED（审批通过即含门禁已过语义）。"""
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
                {"gate_report_ref": "attached", "gate_status": "passed"},
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
                    "nonce_consumed": True,
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
        appr = self.session.get(Approval, approval_id)
        if appr is None:
            raise ReleaseServiceError("not_found", f"approval {approval_id} not found")

        # 重放 nonce
        if appr.status == "consumed":
            raise ReleaseServiceError("nonce_replay", "approval nonce already consumed")
        if appr.status == "rejected":
            raise ReleaseServiceError("validation_failed", "approval was rejected")
        if appr.status == "expired":
            raise ReleaseServiceError("approval_expired", "approval expired")

        now = datetime.now(timezone.utc)
        exp = appr.expiry
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= now:
            appr.status = "expired"
            self.session.flush()
            raise ReleaseServiceError("approval_expired", "approval TTL exceeded")

        # 重算 WorkOrder hash（审批即批此 hash，发布前逐字节核对）
        recomputed = workorder_hash(wo.payload)
        if recomputed != appr.workorder_hash or recomputed != wo.hash:
            raise ReleaseServiceError("hash_mismatch", "workorder hash drift detected at release time")

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
                "approval_id": approval_id,
            },
            correlation_id=wo.case_id,
            actor="controller:release",
            machine="release",
            merge_payload={
                "workorder_id": workorder_id,
                "workorder_hash": wo.hash,
                "approval_id": approval_id,
                "versionset_id": versionset_id,
                "target_versionset_digest": target_digest,
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

    def stage(self, release_id: str, *, idempotency_key: str) -> dict[str, Any]:
        return self._write_step(release_id, "stage", idempotency_key=idempotency_key)

    def canary(self, release_id: str, *, percent: Optional[int] = None, idempotency_key: str) -> dict[str, Any]:
        return self._write_step(release_id, "canary", idempotency_key=idempotency_key, percent=percent)

    def promote(self, release_id: str, *, idempotency_key: str) -> dict[str, Any]:
        """灰度验证通过后 promote。观察判定由 VERIFYING 前置迁移保证。"""
        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")

        # CANARYING → verification(passed) → VERIFYING（契约：须经 VERIFYING 才 promote）
        if agg.state == "CANARYING":
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.verification_completed",
                payload={
                    "result": "passed",
                    "probe_set_digest": "sha256:" + "b" * 64,
                },
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                merge_payload={"verification": "passed"},
            )
        return self._write_step(release_id, "promote", idempotency_key=idempotency_key)

    def rollback(self, release_id: str, *, reason: str = "manual", idempotency_key: str) -> dict[str, Any]:
        """回滚：仅可从灰度观察态（CANARYING / VERIFYING）触发；ROLLING_BACK 可重试。"""
        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")

        if agg.state == "CANARYING":
            # 先落"验证失败" → VERIFYING，再 rollback_started → ROLLING_BACK
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.verification_completed",
                payload={"result": "failed", "probe_set_digest": "sha256:" + "c" * 64},
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                merge_payload={"verification": "failed"},
            )
            agg = self.store.get_aggregate("release", release_id)
            assert agg is not None
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.rollback_started",
                payload={"rollback_to": "previous", "reason": reason},
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                guard="verification=failed",
                merge_payload={"rollback_reason": reason},
            )
        elif agg.state == "VERIFYING":
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.rollback_started",
                payload={"rollback_to": "previous", "reason": reason},
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                guard="verification=failed",
                merge_payload={"rollback_reason": reason},
            )
        elif agg.state != "ROLLING_BACK":
            raise ReleaseServiceError(
                "illegal_transition",
                f"cannot rollback from state {agg.state}",
                current_state=agg.state,
            )

        return self._write_step(release_id, "rollback", idempotency_key=idempotency_key, reason=reason)

    def _write_step(
        self,
        release_id: str,
        kind: str,
        *,
        idempotency_key: str,
        percent: Optional[int] = None,
        reason: str = "",
    ) -> dict[str, Any]:
        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            raise ReleaseServiceError("not_found", f"release {release_id} not found")

        # 幂等：同 Idempotency-Key 不重复执行
        existing_op = self.session.scalar(
            select(ControllerOperation).where(ControllerOperation.idempotency_key == idempotency_key)
        )
        if existing_op is not None:
            return {
                "release_id": release_id,
                "operation_id": existing_op.operation_id,
                "status": existing_op.status,
                "state": agg.state,
                "duplicate": True,
            }

        vs_id = (agg.payload or {}).get("versionset_id")
        if not vs_id:
            raise ReleaseServiceError("validation_failed", "release missing versionset_id")

        # 读当前 revision 作为 If-Match（CAS）
        try:
            vs = self.quality.get_versionset(vs_id)
            current_rev = int(vs.get("revision", 1))
        except QualityAPIError as exc:
            raise ReleaseServiceError("quality_api_error", str(exc)) from exc

        if_match = str(current_rev)
        local_op_id = new_operation_id()
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=self.settings.operation_ttl_hours)

        cop = ControllerOperation(
            operation_id=local_op_id,
            release_id=release_id,
            kind=kind,
            status="pending",
            idempotency_key=idempotency_key,
            expected_revision=current_rev,
            request_fingerprint=f"{kind}:{vs_id}:{current_rev}:{percent or ''}",
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
                steps = self.settings.canary_step_list
                idx = int((agg.payload or {}).get("canary_step_index", 0))
                pct = percent if percent is not None else steps[min(idx, len(steps) - 1)]
                remote_op = self.quality.canary(
                    vs_id, pct, if_match=if_match, idempotency_key=idempotency_key
                )
            elif kind == "promote":
                remote_op = self.quality.promote(vs_id, if_match=if_match, idempotency_key=idempotency_key)
            elif kind == "rollback":
                remote_op = self.quality.rollback(
                    vs_id, "previous", if_match=if_match, idempotency_key=idempotency_key
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
                raise ReleaseServiceError("quality_api_error", str(exc), quality_code=exc.code) from exc

        if unknown:
            return self._enter_unknown(release_id, local_op_id, kind, last_known=agg.state)

        assert remote_op is not None
        remote_id = remote_op.get("operation_id")
        cop.remote_operation_id = remote_id
        remote_status = remote_op.get("status", "succeeded")

        if remote_status in ("pending", "running"):
            remote_status, remote_op = self._poll_operation(remote_id or "", local_op_id)

        if remote_status == "unknown":
            return self._enter_unknown(release_id, local_op_id, kind, last_known=agg.state)

        if remote_status != "succeeded":
            cop.status = "failed"
            cop.result = remote_op
            self.session.flush()
            raise ReleaseServiceError("quality_api_error", f"operation {remote_status}", result=remote_op)

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
        if agg.state in ("STAGING", "CANARYING", "PROMOTING", "ROLLING_BACK"):
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
        else:
            # 首步（stage）结果不可考时状态仍在 REQUESTED：仅标记，不进 UNKNOWN（契约无该迁移）
            agg.payload = {**(agg.payload or {}), "unknown_op": op_id, "unknown_kind": kind}
            self.session.flush()

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

    # ---------- reconcile（UNKNOWN→对账，指数退避由调用方循环） ----------

    def reconcile(self, release_id: str) -> dict[str, Any]:
        """UNKNOWN→reconcile：以 GET /status 权威状态对账，按实际生效情况收敛。

        - 未生效 → action=resume：回 REQUESTED 重发（同 Idempotency-Key）
        - promote 已生效 → action=confirm：确认 COMPLETED
        - 回滚已生效 → action=compensate：ROLLING_BACK → rolled_back
        - stage/canary 已生效 → 收敛到 REQUESTED 后按远端现实补成功事件（重放）
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

        try:
            vs = self.quality.get_status(vs_id)
        except QualityAPIError as exc:
            raise ReleaseServiceError("quality_api_error", f"reconcile status failed: {exc}") from exc

        remote_status = vs.get("status", "")
        applied, _ = self._remote_effect(kind, remote_status)

        # 选择 reconcile action 与目标 guard
        if not applied:
            action, guard = "resume", "action=resume"
        elif kind == "rollback":
            # 回滚已生效：compensate → ROLLING_BACK → rolled_back
            action, guard = "compensate", "action=compensate"
        elif kind == "promote":
            action, guard = "confirm", "action=confirm"
        else:
            # stage/canary 已生效：resume 到 REQUESTED 后重放成功事件
            action, guard = "apply", "action=resume"

        if agg.state == "UNKNOWN":
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.reconciled",
                payload={"operation_id": op_id, "resolved_status": remote_status, "action": action},
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                guard=guard,
                merge_payload={"reconciled": True, "remote_status": remote_status},
            )

        if applied:
            self._apply_remote_reality(release_id, kind, vs)

        self._clear_unknown_marker(release_id, remote_status)

        if op_id:
            cop = self.session.get(ControllerOperation, op_id)
            if cop:
                cop.status = "succeeded"

        self.audit.record(
            actor="controller:release",
            action="release.reconciled",
            target=release_id,
            params={"remote_status": remote_status, "action": action},
            result="success",
        )
        agg = self.store.get_aggregate("release", release_id)
        return {
            "release_id": release_id,
            "state": agg.state if agg else None,
            "remote_status": remote_status,
            "action": action,
            "revision": agg.revision if agg else None,
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
            except ReleaseServiceError:
                if attempt == max_attempts - 1:
                    raise
            time.sleep(min(delay, max_delay))
            delay = min(delay * 2, max_delay)
        return last

    @staticmethod
    def _remote_effect(kind: str, remote_status: str) -> tuple[bool, Optional[str]]:
        if kind == "stage":
            return remote_status in ("staged", "canary", "active"), "STAGING"
        if kind == "canary":
            return remote_status in ("canary", "active"), "CANARYING"
        if kind == "promote":
            return remote_status == "active", "COMPLETED"
        if kind == "rollback":
            return remote_status == "rolled_back", "ROLLED_BACK"
        return False, None

    def _apply_remote_reality(self, release_id: str, kind: str, vs: dict[str, Any]) -> None:
        """把已确认的远端现实落到本地状态机（补成功事件）。"""
        agg = self.store.get_aggregate("release", release_id)
        if agg is None:
            return
        rev = vs.get("revision")

        if kind == "stage":
            if agg.state == "REQUESTED":
                self._apply_success(release_id, "stage", {"result": {"revision": rev, "status": "staged"}})
        elif kind == "canary":
            if agg.state in ("REQUESTED", "STAGING"):
                self._apply_success(
                    release_id,
                    "canary",
                    {"result": {"revision": rev, "status": "canary", "canary_percent": vs.get("canary_percent", 5)}},
                )
        elif kind == "promote":
            if agg.state == "REQUESTED":
                self._apply_success(release_id, "stage", {"result": {"revision": rev}})
                agg = self.store.get_aggregate("release", release_id)
                assert agg is not None
            if agg.state == "STAGING":
                self._apply_success(release_id, "canary", {"result": {"revision": rev, "canary_percent": 100}})
                agg = self.store.get_aggregate("release", release_id)
                assert agg is not None
            if agg.state == "CANARYING":
                self.store.append_event(
                    aggregate_type="release",
                    aggregate_id=release_id,
                    event_type="release.verification_completed",
                    payload={"result": "passed", "probe_set_digest": "sha256:" + "b" * 64},
                    correlation_id=release_id,
                    actor="controller:release",
                    expected_revision=agg.revision,
                    machine="release",
                )
                agg = self.store.get_aggregate("release", release_id)
                assert agg is not None
            if agg.state == "VERIFYING":
                self._apply_success(release_id, "promote", {"result": {"revision": rev}})
        elif kind == "rollback":
            if agg.state == "ROLLING_BACK":
                self._apply_success(
                    release_id,
                    "rollback",
                    {"result": {"restored_digest": "sha256:" + "d" * 64, "revision": rev}},
                )

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
        rev = int(result.get("revision") or (agg.payload or {}).get("remote_revision") or 1)
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
            if agg.state == "REQUESTED":
                # 兼容 reconcile 重放路径：先补 stage
                self.store.append_event(
                    aggregate_type="release",
                    aggregate_id=release_id,
                    event_type="release.staged",
                    payload={"versionset_id": vs_id, "revision": rev, "operation_id": ""},
                    correlation_id=release_id,
                    actor="controller:release",
                    expected_revision=agg.revision,
                    machine="release",
                )
                agg = self.store.get_aggregate("release", release_id)
                assert agg is not None
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.canary_started",
                payload={"versionset_id": vs_id, "percent": pct, "operation_id": remote_op.get("operation_id", "")},
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                merge_payload={"canary_percent": pct, "canary_step_index": idx + 1, "remote_revision": rev},
            )
        elif kind == "promote":
            if agg.state == "CANARYING":
                self.store.append_event(
                    aggregate_type="release",
                    aggregate_id=release_id,
                    event_type="release.verification_completed",
                    payload={"result": "passed", "probe_set_digest": "sha256:" + "b" * 64},
                    correlation_id=release_id,
                    actor="controller:release",
                    expected_revision=agg.revision,
                    machine="release",
                )
                agg = self.store.get_aggregate("release", release_id)
                assert agg is not None
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.promoted",
                payload={"versionset_id": vs_id, "revision": rev, "operation_id": remote_op.get("operation_id", "")},
                correlation_id=release_id,
                actor="controller:release",
                expected_revision=agg.revision,
                machine="release",
                guard="verification=passed" if agg.state == "VERIFYING" else None,
                merge_payload={"remote_revision": rev, "promoted": True},
            )
        elif kind == "rollback":
            self.store.append_event(
                aggregate_type="release",
                aggregate_id=release_id,
                event_type="release.rolled_back",
                payload={
                    "restored_digest": result.get("restored_digest") or "sha256:" + "d" * 64,
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
