"""Case Controller 领域服务：投诉接入 / 立案 / 领单 / 状态迁移。"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models.tables import Aggregate, Inbox
from app.services.audit import AuditService, AuditWriteError
from app.services.event_store import CASConflict, EventStore
from app.services.lease import LeaseConflict, LeaseLost, LeaseService
from app.services.state_machines import IllegalTransition
from app.utils.ids import new_case_id, new_trace_id
from app.utils.pii import PIIRedactionError, normalize_for_dedup, redact_text

logger = logging.getLogger(__name__)


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
        complainant_ref: str = "anon",
        attachments: Optional[list[str]] = None,
        app_ref: str = "demo-app",
        title: Optional[str] = None,
        auto_open: bool = True,
    ) -> dict[str, Any]:
        """POST /v1/complaints 核心逻辑。

        去重：dedup_key = sha256(source|external_id)；无 external_id 时按 D-001 Q4
        （先 PII 脱敏、再归一化、后 sha256）内容指纹。去重窗内重复 → 返回已有 case；
        窗外 → 换键重新立案并关联历史 case_id（spec §7.3）。
        """
        if source not in ("webhook", "poll"):
            raise CaseServiceError("validation_failed", "source must be webhook|poll")

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
            "complainant_ref": complainant_ref,
            "text": redacted.text,  # 已脱敏
            "attachments": attachments or [],
            "app_ref": app_ref,
            "previous_case_id": previous_case_id,
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
                "complainant_ref": complainant_ref,
                "text_ref": f"inline:{case_id}",
                "attachments": attachments or [],
            },
            causation_id="none",
            correlation_id=case_id,
            actor="controller:case",
            trace_id=trace_id,
            machine="case",
            new_state="RECEIVED",
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
            params={"dedup_key": dedup_key, "source": source},
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

        # 已 DISPATCHED 且 lease 过期 → 先回收（case.worker_lost → OPEN）
        if agg.state == "DISPATCHED" and self.leases.is_expired(case_id):
            self._worker_lost(case_id, reason="lease_expired")
            agg = self.store.get_aggregate("case", case_id)
            assert agg is not None

        if agg.state not in ("OPEN", "DISPATCHED"):
            raise CaseServiceError(
                "illegal_transition",
                f"cannot claim from state {agg.state}",
                current_state=agg.state,
            )

        try:
            lease = self.leases.claim(case_id, worker_id)
        except LeaseConflict as exc:
            raise CaseServiceError("lease_conflict", str(exc), current_state=agg.state) from exc

        # OPEN → DISPATCHED；DISPATCHED 同 owner 重领 → 更新 payload（不重复立案）
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

    def reclaim_if_expired(self, case_id: str) -> Optional[dict[str, Any]]:
        """lease 过期 → case.worker_lost → OPEN。"""
        agg = self.store.get_aggregate("case", case_id)
        if agg is None or agg.state != "DISPATCHED":
            return None
        if not self.leases.is_expired(case_id):
            return None
        return self._worker_lost(case_id, reason="lease_expired")

    def _worker_lost(self, case_id: str, reason: str) -> dict[str, Any]:
        agg = self.store.get_aggregate("case", case_id)
        if agg is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")
        payload = agg.payload or {}
        self.store.append_event(
            aggregate_type="case",
            aggregate_id=case_id,
            event_type="case.worker_lost",
            payload={
                "worker_id": payload.get("worker_id", "unknown"),
                "lease_id": payload.get("lease_id", "unknown"),
                "requeued": True,
                "reason": reason,
            },
            causation_id="lease_watchdog",
            correlation_id=case_id,
            actor="controller:case",
            expected_revision=agg.revision,
            machine="case",
            merge_payload={"worker_id": None, "lease_id": None},
        )
        self.audit.record(
            actor="controller:case",
            action="case.worker_lost",
            target=case_id,
            params={"reason": reason},
            result="success",
        )
        agg = self.store.get_aggregate("case", case_id)
        return {"case_id": case_id, "state": agg.state if agg else "OPEN", "revision": agg.revision if agg else 0}

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
        if event_type in {"case.closed", "notification.sent", "notification.dead_lettered"}:
            raise CaseServiceError(
                "forbidden_transition",
                f"{event_type} is dispatcher-owned and requires a provider receipt",
            )
        agg = self.store.get_aggregate("case", case_id)
        if agg is None:
            raise CaseServiceError("not_found", f"case {case_id} not found")

        # 持有 lease 的写必须带有效 fencing token
        if fencing_token is not None:
            try:
                self.leases.check_fencing(case_id, fencing_token)
            except LeaseLost as exc:
                raise CaseServiceError("lease_lost", str(exc), current_state=agg.state) from exc

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
