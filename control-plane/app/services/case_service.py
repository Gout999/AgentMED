"""Case Controller 领域服务：投诉接入 / 立案 / 领单 / 状态迁移。"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Aggregate, Event, Inbox, WorkerSuggestionReceipt, WorkOrder
from app.services.audit import AuditService, AuditWriteError
from app.services.event_store import CASConflict, EventStore
from app.services.lease import LeaseConflict, LeaseLost, LeaseService
from app.services.state_machines import IllegalTransition
from app.utils.ids import new_case_id, new_trace_id
from app.utils.jcs import canonical_json_digest
from app.utils.pii import PIIRedactionError, normalize_for_dedup, redact_text

logger = logging.getLogger(__name__)

SUGGESTION_KINDS = {"triage", "attribution", "fix", "gate", "verify"}


class CaseServiceError(Exception):
    def __init__(self, code: str, message: str, **extra: Any):
        self.code = code
        self.message = message
        self.extra = extra
        super().__init__(message)


class CaseService:
    def __init__(self, session: Session, settings: Settings | None = None):
        self.session = session
        self.settings = settings or get_settings()
        self.store = EventStore(session)
        self.leases = LeaseService(session, self.settings)
        self.audit = AuditService(session, self.settings)

    # ---------- 投诉接入 ----------

    def ingest_complaint(
        self,
        *,
        source: str,
        text: str,
        external_id: Optional[str] = None,
        channel: str = "feishu-mock:default:",
        thread_ref: Optional[str] = None,
        complainant_ref: str = "anon",
        attachments: Optional[list[str]] = None,
        app_ref: str = "demo-app",
        title: Optional[str] = None,
        auto_open: bool = True,
        demo_fault_injection_id: Optional[str] = None,
        provider_origin: Optional[str] = None,
        provider_create_time: Optional[str] = None,
        source_text_digest: Optional[str] = None,
    ) -> dict[str, Any]:
        """POST /v1/complaints 核心逻辑。

        去重：dedup_key = sha256(source|external_id)；无 external_id 时按 D-001 Q4
        （先 PII 脱敏、再归一化、后 sha256）内容指纹。去重窗内重复 → 返回已有 case；
        窗外 → 换键重新立案并关联历史 case_id（spec §7.3）。
        """
        if source not in ("webhook", "poll"):
            raise CaseServiceError("validation_failed", "source must be webhook|poll")
        if demo_fault_injection_id is not None and not (
            isinstance(demo_fault_injection_id, str)
            and 8 <= len(demo_fault_injection_id) <= 128
        ):
            raise CaseServiceError(
                "validation_failed",
                "demo_fault_injection_id must contain 8..128 characters",
            )
        provenance_fields = (
            provider_origin,
            provider_create_time,
            source_text_digest,
        )
        if any(value is not None for value in provenance_fields) and not all(
            value is not None for value in provenance_fields
        ):
            raise CaseServiceError(
                "validation_failed",
                "provider_origin, provider_create_time and source_text_digest must be supplied together",
            )
        if provider_create_time is not None and (
            re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?(?:/[A-Za-z0-9._~/-]*)?", str(provider_origin or ""))
            is None
            or
            re.fullmatch(r"[1-9][0-9]{12}", provider_create_time) is None
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(source_text_digest or "")) is None
        ):
            raise CaseServiceError(
                "validation_failed",
                "provider complaint provenance is malformed",
            )
        resolved_thread_ref = thread_ref or external_id
        if not isinstance(resolved_thread_ref, str) or not resolved_thread_ref:
            raise CaseServiceError(
                "validation_failed",
                "thread_ref is required when the source has no external_id",
            )

        try:
            redacted = redact_text(text)
        except PIIRedactionError as exc:
            raise CaseServiceError("pii_redaction_failed", str(exc)) from exc

        if external_id:
            ext = external_id
        else:
            # D-001 Q4：脱敏 → 小写+空白折叠+trim → sha256
            norm = normalize_for_dedup(text)
            ext = hashlib.sha256(norm.encode("utf-8")).hexdigest()

        dedup_key = self._dedup_key(source, ext)
        now = datetime.now(timezone.utc)
        window = timedelta(hours=self.settings.complaint_dedup_window_hours)

        existing = self.session.get(Inbox, dedup_key)
        if existing is not None:
            received = existing.received_at
            if received.tzinfo is None:
                received = received.replace(tzinfo=timezone.utc)
            if now - received <= window:
                persisted_injection_id = (existing.raw_payload or {}).get(
                    "demo_fault_injection_id"
                )
                if (
                    demo_fault_injection_id is not None
                    and persisted_injection_id != demo_fault_injection_id
                ):
                    raise CaseServiceError(
                        "idempotency_conflict",
                        "duplicate complaint is bound to another demo fault injection",
                    )
                if provider_create_time is not None and (
                    (existing.raw_payload or {}).get("provider_origin")
                    != provider_origin
                    or (existing.raw_payload or {}).get("provider_create_time")
                    != provider_create_time
                    or (existing.raw_payload or {}).get("source_text_digest")
                    != source_text_digest
                ):
                    raise CaseServiceError(
                        "idempotency_conflict",
                        "duplicate complaint provider provenance changed",
                    )
                # 重复：返回已有 case（不再立案）
                self.audit.record(
                    actor="controller:case",
                    action="complaint.duplicate",
                    target=existing.case_id or dedup_key,
                    params={"dedup_key": dedup_key, "source": source},
                    result="success",
                )
                return {
                    "duplicate": True,
                    "dedup_key": dedup_key,
                    "existing_case_id": existing.case_id,
                    "case_id": existing.case_id,
                    "disposition": existing.disposition,
                    "demo_fault_injection_id": persisted_injection_id,
                    "provider_origin": (existing.raw_payload or {}).get(
                        "provider_origin"
                    ),
                    "provider_create_time": (existing.raw_payload or {}).get(
                        "provider_create_time"
                    ),
                    "source_text_digest": (existing.raw_payload or {}).get(
                        "source_text_digest"
                    ),
                }
            # 去重窗外：换键新立案，关联历史 case_id（spec §7.3）
            previous_case_id = existing.case_id
            dedup_key = self._dedup_key(source, ext, suffix=f"refile:{previous_case_id}")
        else:
            previous_case_id = None

        # 新投诉：inbox + case 聚合（首事件懒创建）
        case_id = new_case_id()
        trace_id = new_trace_id()
        safe_payload = {
            "source": source,
            "external_id": ext,
            "dedup_key": dedup_key,
            "channel": channel,
            "thread_ref": resolved_thread_ref,
            "complainant_ref": complainant_ref,
            "text": redacted.text,  # 已脱敏
            "attachments": attachments or [],
            "app_ref": app_ref,
            "previous_case_id": previous_case_id,
            "demo_fault_injection_id": demo_fault_injection_id,
            "provider_origin": provider_origin,
            "provider_create_time": provider_create_time,
            "source_text_digest": source_text_digest,
        }

        inbox = Inbox(
            dedup_key=dedup_key,
            source=source,
            external_id=ext,
            raw_payload=safe_payload,
            received_at=now,
            case_id=case_id,
            disposition="FILED",
        )
        self.session.add(inbox)

        self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="complaint.received",
            payload={
                "source": source,
                "external_id": ext,
                "dedup_key": dedup_key,
                "channel": channel,
                "thread_ref": resolved_thread_ref,
                "complainant_ref": complainant_ref,
                "text_ref": f"inline:{case_id}",
                "attachments": attachments or [],
                "demo_fault_injection_id": demo_fault_injection_id,
                "provider_origin": provider_origin,
                "provider_create_time": provider_create_time,
                "source_text_digest": source_text_digest,
            },
            causation_id="none",
            correlation_id=case_id,
            actor="controller:case",
            trace_id=trace_id,
            machine="case",
            new_state="RECEIVED",
            merge_payload={
                "source": source,
                "external_id": ext,
                "dedup_key": dedup_key,
                "channel": channel,
                "thread_ref": resolved_thread_ref,
                "complainant_ref": complainant_ref,
                "text_ref": f"inline:{case_id}",
                "attachments": attachments or [],
                "demo_fault_injection_id": demo_fault_injection_id,
                "provider_origin": provider_origin,
                "provider_create_time": provider_create_time,
                "source_text_digest": source_text_digest,
            },
        )

        if auto_open:
            self._open_case(
                case_id=case_id,
                dedup_key=dedup_key,
                title=title or (redacted.text[:80] if redacted.text else "complaint"),
                app_ref=app_ref,
                causation_event="complaint.received",
                trace_id=trace_id,
            )

        self.audit.record(
            actor="controller:case",
            action="complaint.received",
            target=case_id,
            params={
                "dedup_key": dedup_key,
                "source": source,
                "demo_fault_injection_id": demo_fault_injection_id,
            },
            result="success",
            trace_id=trace_id,
        )

        agg = self.store.get_aggregate("case", case_id)
        return {
            "duplicate": False,
            "dedup_key": dedup_key,
            "case_id": case_id,
            "state": agg.state if agg else "OPEN",
            "revision": agg.revision if agg else 1,
            "demo_fault_injection_id": demo_fault_injection_id,
            "provider_origin": provider_origin,
            "provider_create_time": provider_create_time,
            "source_text_digest": source_text_digest,
        }

    @staticmethod
    def _dedup_key(source: str, external_id: str, suffix: str = "") -> str:
        material = f"{source}|{external_id}" + (f"|{suffix}" if suffix else "")
        return f"sha256:{hashlib.sha256(material.encode('utf-8')).hexdigest()}"

    def _open_case(
        self,
        *,
        case_id: str,
        dedup_key: str,
        title: str,
        app_ref: str,
        causation_event: str,
        trace_id: str,
    ) -> None:
        agg = self.store.get_aggregate("case", case_id)
        if agg is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")
        self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="case.opened",
            payload={"dedup_key": dedup_key, "title": title, "app_ref": app_ref, "severity": "medium"},
            causation_id=causation_event,
            correlation_id=case_id,
            actor="controller:case",
            trace_id=trace_id,
            expected_revision=agg.revision,
            machine="case",
            merge_payload={"title": title, "app_ref": app_ref},
        )

    # ---------- 领单 / 心跳 / 回收 ----------

    def claim(self, case_id: str, worker_id: str) -> dict[str, Any]:
        agg = self.store.get_aggregate("case", case_id)
        if agg is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")

        # Lease→Case lock order matches heartbeat/claim and revalidates expiry
        # after waiting, so a concurrent successful heartbeat cannot be lost.
        if agg.state in {"DISPATCHED", "ATTRIBUTING"}:
            if self.reclaim_if_expired(case_id) is not None:
                agg = self.store.get_aggregate("case", case_id)
                assert agg is not None

        if agg.state not in ("OPEN", "DISPATCHED", "AWAITING_FIX"):
            raise CaseServiceError(
                "illegal_transition",
                f"cannot claim from state {agg.state}",
                current_state=agg.state,
            )

        try:
            lease = self.leases.claim(case_id, worker_id)
        except LeaseConflict as exc:
            raise CaseServiceError("lease_conflict", str(exc), current_state=agg.state) from exc

        # The lease wait may have outlived the earlier projection read.  Lock
        # and refresh the Case before choosing a transition/revision.
        agg = self.store.get_aggregate_for_update("case", case_id)
        if agg is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")
        if agg.state not in ("OPEN", "DISPATCHED", "AWAITING_FIX"):
            raise CaseServiceError(
                "illegal_transition",
                f"cannot claim from state {agg.state}",
                current_state=agg.state,
            )

        # OPEN → DISPATCHED；归因后 AWAITING_FIX 由修复师以新 lease 接手。
        if agg.state == "OPEN":
            attempt = int((agg.payload or {}).get("dispatch_attempt", 0)) + 1
            self.store.append_event(
                aggregate_type="case",
                aggregate_id=case_id,
                event_type="case.dispatched",
                payload={
                    "worker_id": worker_id,
                    "lease_id": lease.lease_id,
                    "fencing_token": lease.fencing_token,
                    "attempt": attempt,
                },
                causation_id="claim",
                correlation_id=case_id,
                actor=worker_id,
                expected_revision=agg.revision,
                machine="case",
                merge_payload={
                    "worker_id": worker_id,
                    "lease_id": lease.lease_id,
                    "fencing_token": lease.fencing_token,
                    "dispatch_attempt": attempt,
                },
            )
        elif agg.state == "AWAITING_FIX":
            self.store.append_event(
                aggregate_type="case",
                aggregate_id=case_id,
                event_type="case.worker_handed_off",
                payload={
                    "worker_id": worker_id,
                    "lease_id": lease.lease_id,
                    "fencing_token": lease.fencing_token,
                    "from_role": (agg.payload or {}).get("worker_id"),
                },
                causation_id=(agg.payload or {}).get("experiment_id", "attribution-completed"),
                correlation_id=case_id,
                actor=worker_id,
                expected_revision=agg.revision,
                machine="case",
                merge_payload={
                    "worker_id": worker_id,
                    "lease_id": lease.lease_id,
                    "fencing_token": lease.fencing_token,
                },
            )
        else:
            # 已 DISPATCHED 重领：lease 已换发新 fencing token，同步投影
            agg.payload = {
                **(agg.payload or {}),
                "worker_id": worker_id,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
            }
            self.session.flush()

        self.audit.record(
            actor=worker_id,
            action="case.claim",
            target=case_id,
            params={"fencing_token": lease.fencing_token, "lease_id": lease.lease_id},
            result="success",
        )
        agg = self.store.get_aggregate("case", case_id)
        return {
            "case_id": case_id,
            "state": agg.state if agg else "DISPATCHED",
            "lease_id": lease.lease_id,
            "fencing_token": lease.fencing_token,
            "expires_at": lease.expires_at.isoformat(),
            "owner_id": worker_id,
            "revision": agg.revision if agg else 0,
        }

    def heartbeat(self, case_id: str, worker_id: str, fencing_token: int) -> dict[str, Any]:
        try:
            lease = self.leases.heartbeat(case_id, worker_id, fencing_token)
        except LeaseLost as exc:
            raise CaseServiceError("lease_lost", str(exc)) from exc
        return {
            "case_id": case_id,
            "lease_id": lease.lease_id,
            "fencing_token": lease.fencing_token,
            "expires_at": lease.expires_at.isoformat(),
        }

    def validate_active_lease(
        self, case_id: str, worker_id: str, fencing_token: int
    ) -> dict[str, Any]:
        """Validate the exact active owner/token tuple without renewing it."""

        if self.store.get_aggregate("case", case_id) is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")
        try:
            lease = self.leases.check_active(case_id, worker_id, fencing_token)
        except LeaseLost as exc:
            raise CaseServiceError("lease_lost", str(exc)) from exc
        return {
            "case_id": case_id,
            "worker_id": lease.owner_id,
            "lease_id": lease.lease_id,
            "fencing_token": lease.fencing_token,
            "expires_at": lease.expires_at.isoformat(),
            "active": True,
        }

    def submit_suggestion(
        self,
        *,
        case_id: str,
        worker_id: str,
        fencing_token: int,
        idempotency_key: str,
        kind: str,
        payload: dict[str, Any],
        evidence_refs: list[str],
    ) -> dict[str, Any]:
        """Atomically authorize and record a non-authoritative worker suggestion."""

        if kind not in SUGGESTION_KINDS:
            raise CaseServiceError(
                "validation_failed", f"kind must be one of {sorted(SUGGESTION_KINDS)}"
            )
        if not isinstance(payload, dict):
            raise CaseServiceError("validation_failed", "suggestion payload must be an object")
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
            raise CaseServiceError(
                "validation_failed", "idempotency_key must contain 8..128 characters"
            )
        if not isinstance(evidence_refs, list) or not all(
            isinstance(item, str) and item for item in evidence_refs
        ):
            raise CaseServiceError(
                "validation_failed", "evidence_refs must contain non-empty strings"
            )
        request_digest = canonical_json_digest(
            {
                "case_id": case_id,
                "worker_id": worker_id,
                "fencing_token": fencing_token,
                "kind": kind,
                "payload": payload,
                "evidence_refs": evidence_refs,
            }
        )
        def existing_receipt() -> WorkerSuggestionReceipt | None:
            return self.session.scalar(
                select(WorkerSuggestionReceipt)
                .where(WorkerSuggestionReceipt.idempotency_key == idempotency_key)
                .with_for_update()
            )

        def duplicate_receipt(existing: WorkerSuggestionReceipt) -> dict[str, Any]:
            if (
                existing.case_id != case_id
                or existing.worker_id != worker_id
                or existing.request_digest != request_digest
            ):
                raise CaseServiceError(
                    "idempotency_conflict",
                    "suggestion idempotency key is already bound to another request",
                )
            return {
                "accepted": True,
                "suggestion_id": existing.event_id,
                "event_id": existing.event_id,
                "event_type": "case.suggestion_recorded",
                "case_id": case_id,
                "duplicate": True,
            }

        existing = existing_receipt()
        if existing is not None:
            return duplicate_receipt(existing)

        if self.store.get_aggregate("case", case_id) is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")
        try:
            lease = self.leases.check_active(case_id, worker_id, fencing_token)
        except LeaseLost as exc:
            raise CaseServiceError("lease_lost", str(exc)) from exc
        aggregate = self.store.get_aggregate_for_update("case", case_id)
        if aggregate is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")
        # A missing-row FOR UPDATE cannot serialize first use of a new key.
        # The lease row above is the per-Case serialization point; re-read the
        # receipt after that lock so a concurrent exact retry returns the same
        # authoritative event instead of racing the unique constraint.
        existing = existing_receipt()
        if existing is not None:
            return duplicate_receipt(existing)
        event = self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="case.suggestion_recorded",
            payload={
                "kind": kind,
                "payload": payload,
                "evidence_refs": evidence_refs,
                "worker_id": worker_id,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
            },
            causation_id=idempotency_key,
            correlation_id=case_id,
            actor=worker_id,
            expected_revision=aggregate.revision,
        )
        self.session.add(
            WorkerSuggestionReceipt(
                idempotency_key=idempotency_key,
                case_id=case_id,
                worker_id=worker_id,
                request_digest=request_digest,
                event_id=event.event_id,
            )
        )
        self.audit.record(
            actor=worker_id,
            action="case.suggestion.record",
            target=case_id,
            params={
                "suggestion_id": event.event_id,
                "kind": kind,
                "fencing_token": lease.fencing_token,
            },
            result="success",
        )
        return {
            "accepted": True,
            "suggestion_id": event.event_id,
            "event_id": event.event_id,
            "event_type": "case.suggestion_recorded",
            "case_id": case_id,
            "duplicate": False,
        }

    def reclaim_if_expired(self, case_id: str) -> Optional[dict[str, Any]]:
        """Reclaim one expired exact lease without losing experiment progress."""
        lease = self.leases.lock_if_expired(case_id)
        if lease is None:
            return None
        agg = self.store.get_aggregate_for_update("case", case_id)
        if agg is None or agg.state not in {"DISPATCHED", "ATTRIBUTING"}:
            return None
        payload = agg.payload or {}
        if (
            payload.get("lease_id") != lease.lease_id
            or int(payload.get("fencing_token") or 0) != int(lease.fencing_token)
            or payload.get("worker_id") != lease.owner_id
        ):
            return None
        if agg.state == "ATTRIBUTING":
            return self._experiment_runner_lost(
                case_id,
                aggregate=agg,
                lease=lease,
            )
        return self._worker_lost(
            case_id,
            reason="lease_expired",
            aggregate=agg,
            lease=lease,
        )

    def _worker_lost(self, case_id: str, reason: str, *, aggregate, lease) -> dict[str, Any]:
        agg = aggregate
        payload = agg.payload or {}
        self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="case.worker_lost",
            payload={
                "worker_id": lease.owner_id,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "requeued": True,
                "reason": reason,
            },
            causation_id="lease_watchdog",
            correlation_id=case_id,
            actor="controller:case",
            expected_revision=agg.revision,
            machine="case",
            merge_payload={"worker_id": None, "lease_id": None, "fencing_token": None},
        )
        self.audit.record(
            actor="controller:case",
            action="case.worker_lost",
            target=case_id,
            params={"reason": reason},
            result="success",
        )
        self.session.delete(lease)
        self.session.flush()
        agg = self.store.get_aggregate("case", case_id)
        return {"case_id": case_id, "state": agg.state if agg else "OPEN", "revision": agg.revision if agg else 0}

    def _experiment_runner_lost(self, case_id: str, *, aggregate, lease) -> dict[str, Any]:
        """Atomically return attribution work to the fixed warm pool."""

        case_payload = aggregate.payload or {}
        experiment_id = case_payload.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            raise CaseServiceError(
                "illegal_transition", "ATTRIBUTING Case has no bound experiment"
            )
        experiment = self.store.get_aggregate_for_update("experiment", experiment_id)
        if experiment is None or (experiment.payload or {}).get("case_id") != case_id:
            raise CaseServiceError(
                "illegal_transition", "ATTRIBUTING Case experiment binding is missing"
            )
        if experiment.state not in {"REQUESTED", "PROTOCOL_FROZEN", "RUNNING"}:
            raise CaseServiceError(
                "illegal_transition",
                f"cannot reclaim experiment from state {experiment.state}",
            )
        experiment_payload = experiment.payload or {}
        if experiment.state == "RUNNING" and (
            experiment_payload.get("runner_id") != lease.owner_id
            or experiment_payload.get("lease_id") != lease.lease_id
            or int(experiment_payload.get("fencing_token") or 0)
            != int(lease.fencing_token)
        ):
            raise CaseServiceError(
                "lease_lost", "running experiment lease binding changed before reclaim"
            )
        previous_state = experiment.state
        runner_lost = self.store.append_event(
            aggregate_type="experiment",
            aggregate_id=experiment_id,
            event_type="experiment.runner_lost",
            payload={
                "runner_id": lease.owner_id,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "previous_state": previous_state,
                "requeued": True,
            },
            causation_id="lease_watchdog",
            correlation_id=case_id,
            actor="controller:case",
            expected_revision=experiment.revision,
            machine="experiment",
            merge_payload={
                "runner_id": None,
                "lease_id": None,
                "fencing_token": None,
                "runner_loss_count": int(experiment_payload.get("runner_loss_count") or 0)
                + 1,
            },
        )
        self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="experiment.runner_lost",
            payload={
                "experiment_id": experiment_id,
                "runner_id": lease.owner_id,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "requeued": True,
            },
            causation_id=runner_lost.event_id,
            correlation_id=case_id,
            actor="controller:case",
            expected_revision=aggregate.revision,
            machine="case",
            merge_payload={
                "worker_id": None,
                "lease_id": None,
                "fencing_token": None,
            },
        )
        self.session.delete(lease)
        self.audit.record(
            actor="controller:case",
            action="experiment.runner_lost",
            target=experiment_id,
            params={
                "case_id": case_id,
                "runner_id": lease.owner_id,
                "lease_id": lease.lease_id,
                "fencing_token": lease.fencing_token,
                "previous_state": previous_state,
            },
            result="requeued",
        )
        self.session.flush()
        case = self.store.get_aggregate("case", case_id)
        current_experiment = self.store.get_aggregate("experiment", experiment_id)
        return {
            "case_id": case_id,
            "state": case.state if case else "DISPATCHED",
            "revision": case.revision if case else 0,
            "experiment_id": experiment_id,
            "experiment_state": (
                current_experiment.state if current_experiment else previous_state
            ),
            "requeued": True,
        }

    # ---------- 状态迁移 ----------

    def transition(
        self,
        case_id: str,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        expected_revision: Optional[int] = None,
        fencing_token: Optional[int] = None,
        actor: str = "system",
        guard: Optional[str] = None,
    ) -> dict[str, Any]:
        domain_owned = {
            "experiment.requested",
            "case.attribution_completed",
            "changeset.approval_requested",
            "changeset.approved",
            "changeset.rejected",
            "changeset.expired",
            "case.resolved",
            "release.rollback_failed",
            "case.closed",
            "notification.sent",
            "notification.dead_lettered",
        }
        agg = self.store.get_aggregate("case", case_id)
        if agg is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")

        # 持有 lease 的写必须带有效 fencing token
        if fencing_token is not None:
            try:
                self.leases.check_fencing(case_id, fencing_token)
            except LeaseLost as exc:
                raise CaseServiceError("lease_lost", str(exc), current_state=agg.state) from exc

        if event_type in domain_owned:
            raise CaseServiceError(
                "forbidden_transition",
                f"{event_type} is domain-owned and cannot be asserted through the generic Case API",
            )

        try:
            self.store.append_event(
                aggregate_type="case",
                aggregate_id=case_id,
                event_type=event_type,
                payload=payload or {},
                causation_id="api",
                correlation_id=case_id,
                actor=actor,
                expected_revision=expected_revision if expected_revision is not None else agg.revision,
                machine="case",
                guard=guard,
                merge_payload=payload,
            )
        except IllegalTransition as exc:
            raise CaseServiceError(
                "illegal_transition",
                str(exc),
                current_state=agg.state,
                event=event_type,
            ) from exc
        except CASConflict as exc:
            raise CaseServiceError(
                "revision_conflict",
                str(exc),
                current_state=agg.state,
                expected_revision=exc.expected,
                actual_revision=exc.actual,
            ) from exc

        self.audit.record(
            actor=actor,
            action=event_type,
            target=case_id,
            params={"event_type": event_type, "payload_keys": list((payload or {}).keys())},
            result="success",
        )
        agg = self.store.get_aggregate("case", case_id)
        return {
            "case_id": case_id,
            "state": agg.state if agg else None,
            "revision": agg.revision if agg else None,
            "payload": agg.payload if agg else {},
        }

    def project_changeset_event(
        self,
        *,
        case_id: str,
        changeset_id: str,
        source_event_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Advance Case only from a persisted, case-bound ChangeSet event."""

        allowed = {
            "changeset.approval_requested",
            "changeset.approved",
            "changeset.rejected",
            "changeset.expired",
        }
        if event_type not in allowed:
            raise CaseServiceError("forbidden_transition", f"unsupported ChangeSet projection {event_type}")
        source = self.session.get(Event, source_event_id)
        changeset = self.store.get_aggregate("changeset", changeset_id)
        case = self.store.get_aggregate("case", case_id)
        if source is None or changeset is None or case is None:
            raise CaseServiceError("not_found", "Case projection source is missing")
        if (
            source.aggregate_type != "changeset"
            or source.aggregate_id != changeset_id
            or source.event_type != event_type
            or (changeset.payload or {}).get("case_id") != case_id
        ):
            raise CaseServiceError("hash_mismatch", "ChangeSet event is not bound to this Case")
        projected = self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type=event_type,
            payload={"changeset_id": changeset_id, **payload},
            causation_id=source_event_id,
            correlation_id=case_id,
            actor="controller:case",
            expected_revision=case.revision,
            machine="case",
            merge_payload={"changeset_id": changeset_id, **payload},
        )
        self.audit.record(
            actor="controller:case",
            action=f"case.project.{event_type}",
            target=case_id,
            params={"changeset_id": changeset_id, "source_event_id": source_event_id},
            result="success",
        )
        return {
            "case_id": case_id,
            "state": self.store.get_aggregate("case", case_id).state,
            "event_id": projected.event_id,
        }

    def resolve_from_release(self, *, release_id: str) -> dict[str, Any]:
        """Project one receipt-verified terminal Release into Case.NOTIFYING.

        This is the only production bridge for ``case.resolved``.  It binds the
        Case to the immutable WorkOrder and the actual promoted/rolled-back
        event instead of trusting a caller-provided success flag.
        """

        release = self.session.scalar(
            select(Aggregate)
            .where(
                Aggregate.aggregate_type == "release",
                Aggregate.aggregate_id == release_id,
            )
            .with_for_update()
        )
        if release is None:
            raise CaseServiceError("not_found", f"release {release_id} not found")
        workorder_id = (release.payload or {}).get("workorder_id")
        workorder = self.session.get(WorkOrder, workorder_id) if workorder_id else None
        if workorder is None:
            raise CaseServiceError("hash_mismatch", "Release has no immutable WorkOrder binding")
        case = self.session.scalar(
            select(Aggregate)
            .where(
                Aggregate.aggregate_type == "case",
                Aggregate.aggregate_id == workorder.case_id,
            )
            .with_for_update()
        )
        if case is None:
            raise CaseServiceError("not_found", f"case {workorder.case_id} not found")
        if case.state in ("NOTIFYING", "CLOSED") and (case.payload or {}).get("resolved_release_id") == release_id:
            events = [event for event in self.store.list_events(case.aggregate_id) if event.event_type == "case.resolved"]
            return {
                "case_id": case.aggregate_id,
                "state": case.state,
                "event_id": events[-1].event_id if events else None,
                "duplicate": True,
            }
        release_payload = release.payload or {}
        if release.state == "COMPLETED" and release_payload.get("promoted"):
            resolution = "fixed"
            source_event_type = "release.promoted"
            resolution_digest = workorder.payload.get("target_versionset_digest")
        elif release.state == "ROLLED_BACK" and release_payload.get("rolled_back"):
            resolution = "rolled_back"
            source_event_type = "release.rolled_back"
            resolution_digest = release_payload.get("restored_digest")
            if (
                not isinstance(resolution_digest, str)
                or resolution_digest != workorder.payload.get("base_versionset_digest")
            ):
                raise CaseServiceError(
                    "hash_mismatch",
                    "rolled-back Release does not prove restoration of the WorkOrder baseline",
                )
        else:
            raise CaseServiceError(
                "illegal_transition",
                "only a receipt-verified promoted or rolled-back Release may resolve a Case",
            )
        if case.state != "RELEASING":
            raise CaseServiceError(
                "illegal_transition",
                f"Case must be RELEASING before resolution; got {case.state}",
            )
        if (
            (release.payload or {}).get("workorder_hash") != workorder.hash
            or (release.payload or {}).get("target_versionset_digest")
            != workorder.payload.get("target_versionset_digest")
        ):
            raise CaseServiceError("hash_mismatch", "Release/WorkOrder binding drifted before Case resolution")
        terminal_event = self.session.scalar(
            select(Event)
            .where(
                Event.aggregate_type == "release",
                Event.aggregate_id == release_id,
                Event.event_type == source_event_type,
            )
            .order_by(Event.seq.desc())
            .limit(1)
        )
        if terminal_event is None:
            raise CaseServiceError(
                "hash_mismatch",
                f"Release projection lacks an authoritative {source_event_type} event",
            )
        complaint = self.session.scalar(
            select(Event)
            .where(
                Event.aggregate_type == "case",
                Event.aggregate_id == case.aggregate_id,
                Event.event_type == "complaint.received",
            )
            .order_by(Event.seq.asc())
            .limit(1)
        )
        original_channel = (complaint.payload or {}).get("channel") if complaint is not None else None
        original_thread_ref = (complaint.payload or {}).get("thread_ref") if complaint is not None else None
        if not isinstance(original_channel, str) or not original_channel:
            raise CaseServiceError(
                "hash_mismatch",
                "Case has no authoritative original complaint channel",
            )
        if not isinstance(original_thread_ref, str) or not original_thread_ref:
            raise CaseServiceError(
                "hash_mismatch",
                "Case has no authoritative original complaint thread_ref",
            )
        resolved = self.store.append_event(
            aggregate_type="case",
            aggregate_id=case.aggregate_id,
            event_type="case.resolved",
            payload={
                "release_id": release_id,
                "workorder_id": workorder.workorder_id,
                "workorder_hash": workorder.hash,
                "resolution": resolution,
                "target_versionset_digest": workorder.payload["target_versionset_digest"],
                "resolution_digest": resolution_digest,
                "release_event_id": terminal_event.event_id,
                "original_channel": original_channel,
                "original_thread_ref": original_thread_ref,
            },
            causation_id=terminal_event.event_id,
            correlation_id=case.aggregate_id,
            actor="controller:case",
            expected_revision=case.revision,
            machine="case",
            merge_payload={
                "resolved_release_id": release_id,
                "resolution": resolution,
                "resolution_digest": resolution_digest,
                "target_versionset_digest": workorder.payload["target_versionset_digest"],
                "original_channel": original_channel,
                "original_thread_ref": original_thread_ref,
            },
        )
        self.audit.record(
            actor="controller:case",
            action="case.resolve_from_release",
            target=case.aggregate_id,
            params={
                "release_id": release_id,
                "release_event_id": terminal_event.event_id,
                "resolution": resolution,
            },
            result="success",
        )
        return {
            "case_id": case.aggregate_id,
            "state": "NOTIFYING",
            "event_id": resolved.event_id,
            "resolution": resolution,
            "duplicate": False,
        }

    def get_case(self, case_id: str) -> dict[str, Any]:
        agg = self.store.get_aggregate("case", case_id)
        if agg is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")
        events = self.store.list_events(case_id)
        return {
            "case_id": case_id,
            "state": agg.state,
            "revision": agg.revision,
            "payload": agg.payload,
            "updated_at": agg.updated_at.isoformat() if agg.updated_at else None,
            "event_count": len(events),
        }

    def list_cases(self, *, state: Optional[str] = None, limit: int = 100, cursor: int = 0) -> dict[str, Any]:
        q = select(Aggregate).where(Aggregate.aggregate_type == "case").order_by(Aggregate.aggregate_id)
        if state:
            q = q.where(Aggregate.state == state)
        rows = list(self.session.scalars(q.offset(cursor).limit(limit)).all())
        return {
            "items": [
                {
                    "case_id": r.aggregate_id,
                    "state": r.state,
                    "revision": r.revision,
                    "title": (r.payload or {}).get("title"),
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
                for r in rows
            ],
            "next_cursor": cursor + len(rows) if len(rows) == limit else None,
        }

    def write_with_fencing(
        self,
        case_id: str,
        fencing_token: int,
        event_type: str,
        payload: Optional[dict[str, Any]] = None,
        actor: str = "worker",
    ) -> dict[str, Any]:
        """带 fencing 的产物提交（旧 token 一律拒绝）。"""
        return self.transition(
            case_id,
            event_type,
            payload,
            fencing_token=fencing_token,
            actor=actor,
        )
