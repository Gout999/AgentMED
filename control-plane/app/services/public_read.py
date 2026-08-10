"""Workspace-bound Stage 1A public read projections.

The service reads only authoritative PostgreSQL projections, writes the public
read audit in the same caller-owned transaction, and never commits or rolls
back.  Missing and cross-workspace resources deliberately share one audited
``RESOURCE_NOT_FOUND`` outcome.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, NoReturn, Sequence

from pydantic import ValidationError
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.tables import Event
from app.models.v4_tables import (
    QualityCase,
    Signal,
    SignalCaseLink,
    TraceEvidenceReceipt as TraceEvidenceReceiptRow,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.models import (
    CaseData,
    CaseResponse,
    CaseTimelineData,
    CaseTimelineResponse,
    CapabilityPrincipal,
    EnabledIntent,
    EvidenceData,
    EvidenceResponse,
    EvidenceSummary,
    NextAction,
    ServerCapabilitiesData,
    ServerCapabilitiesResponse,
    TimelineEvent,
    TimelinePage,
    TimelineSnapshot,
    TraceEvidenceReceipt,
)
from app.services.authority import AuthorityError, AuthorityService
from app.services.v4_audit import V4AuditService, V4AuditUnavailable
from app.utils.v4_integrity import (
    V4IntegrityError,
    assert_record_digest,
    canonical_digest,
    canonicalize,
)


_ORDER = "occurred_at,event_id"
_CURSOR_PREFIX = "cur_"
_CURSOR_SIGNATURE_BYTES = 32
_MAX_CURSOR_LENGTH = 516  # ``cur_`` plus the wire contract's 512 opaque chars.


class PublicReadError(Exception):
    """Stable, secret-safe failure consumed by the public HTTP boundary."""

    def __init__(
        self,
        code: str,
        *,
        details: dict[str, object] | None = None,
        audit_ref: str | None = None,
        rollback_required: bool = True,
        workspace_id: str | None = None,
    ) -> None:
        self.code = code
        self.details = {} if details is None else details
        self.audit_ref = audit_ref
        self.rollback_required = rollback_required
        self.workspace_id = workspace_id
        super().__init__(code)


class PublicReadDenial(PublicReadError):
    """Audited read-only denial that the HTTP boundary may commit by itself."""

    def __init__(
        self,
        code: str,
        *,
        audit_ref: str,
        workspace_id: str,
        details: dict[str, object] | None = None,
    ) -> None:
        if code not in {
            "RESOURCE_NOT_FOUND",
            "SCOPE_FORBIDDEN",
            "VALIDATION_FAILED",
        }:
            raise ValueError("public read denials support only non-mutating denial codes")
        super().__init__(
            code,
            details=details,
            audit_ref=audit_ref,
            rollback_required=False,
            workspace_id=workspace_id,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _epoch_microseconds(value: datetime) -> int:
    aware = _aware(value)
    return int(aware.timestamp()) * 1_000_000 + aware.microsecond


def _from_epoch_microseconds(value: int) -> datetime:
    seconds, microseconds = divmod(value, 1_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
        microsecond=microseconds
    )


def _payload_time(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise V4IntegrityError("v4.projection_time_invalid")
    try:
        return _aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise V4IntegrityError("v4.projection_time_invalid") from exc


class PublicReadService:
    """Build frozen public wire responses from workspace-scoped v4 records."""

    def __init__(self, session: Session, cursor_signing_key: str | bytes) -> None:
        if isinstance(cursor_signing_key, str):
            key = cursor_signing_key.encode("utf-8")
        elif isinstance(cursor_signing_key, bytes):
            key = cursor_signing_key
        else:
            raise TypeError("cursor_signing_key must be str or bytes")
        if not key:
            raise ValueError("cursor_signing_key must not be empty")
        self.session = session
        self._cursor_key = key
        self._audit = V4AuditService(session)
        self._authority = AuthorityService(session)

    def get_case(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        case_id: str,
    ) -> CaseResponse:
        action = "public.cases.get"
        self._require_scope(
            principal=principal,
            required_scope="cases:read",
            request_id=request_id,
            action=action,
            target=f"quality_case:{case_id}",
        )
        case = self._load_case_or_deny(
            principal=principal,
            request_id=request_id,
            case_id=case_id,
            action=action,
        )
        try:
            data = self._case_data(case, principal=principal)
        except (ValidationError, ValueError, TypeError, V4IntegrityError) as exc:
            self._raise_invariant_failure(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
                reason=type(exc).__name__,
            )
        audit = self._record_audit(
            principal=principal,
            action=action,
            target=f"quality_case:{case_id}",
            params={"request_id": request_id, "case_id": case_id},
            evidence_refs={"case_record_digest": case.record_digest},
        )
        try:
            return CaseResponse(
                schema_version="1.0",
                workspace_id=principal.workspace_id,
                request_id=request_id,
                audit_ref=audit.audit_ref,
                data=data,
            )
        except ValidationError as exc:
            raise PublicReadError(
                "INTERNAL_ERROR",
                audit_ref=audit.audit_ref,
                workspace_id=principal.workspace_id,
            ) from exc

    def get_case_timeline(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        case_id: str,
        cursor: str | None,
        limit: int,
    ) -> CaseTimelineResponse:
        action = "public.cases.timeline"
        target = f"quality_case:{case_id}"
        self._require_scope(
            principal=principal,
            required_scope="cases:read",
            request_id=request_id,
            action=action,
            target=target,
        )
        self._load_case_or_deny(
            principal=principal,
            request_id=request_id,
            case_id=case_id,
            action=action,
        )
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            self._cursor_error(
                principal=principal,
                request_id=request_id,
                case_id=case_id,
                reason="limit",
            )

        filter_digest = canonical_digest(
            {
                "contract_version": "v4",
                "event_version": "1.0",
                "correlation_id": case_id,
            }
        )
        if cursor is None:
            watermark = self.session.execute(
                select(Event.occurred_at, Event.event_id)
                .where(*self._timeline_scope(principal.workspace_id, case_id))
                .order_by(Event.occurred_at.desc(), Event.event_id.desc())
                .limit(1)
            ).first()
            if watermark is None:
                self._raise_invariant_failure(
                    principal=principal,
                    request_id=request_id,
                    action=action,
                    target=target,
                    reason="timeline_missing",
                )
            watermark_at = _aware(watermark.occurred_at)
            watermark_event_id = watermark.event_id
            after: tuple[datetime, str] | None = None
            scope_digest = self._cursor_scope_digest(
                principal=principal,
                case_id=case_id,
                filter_digest=filter_digest,
                watermark_at=watermark_at,
                watermark_event_id=watermark_event_id,
                limit=limit,
            )
        else:
            decoded = self._decode_cursor_or_error(
                cursor,
                principal=principal,
                request_id=request_id,
                case_id=case_id,
            )
            watermark_at = decoded["watermark_at"]
            watermark_event_id = decoded["watermark_event_id"]
            after = (decoded["after_at"], decoded["after_event_id"])
            scope_digest = self._cursor_scope_digest(
                principal=principal,
                case_id=case_id,
                filter_digest=filter_digest,
                watermark_at=watermark_at,
                watermark_event_id=watermark_event_id,
                limit=limit,
            )
            if not hmac.compare_digest(decoded["scope_digest"], scope_digest):
                self._cursor_error(
                    principal=principal,
                    request_id=request_id,
                    case_id=case_id,
                    reason="scope",
                )

        predicates = [
            *self._timeline_scope(principal.workspace_id, case_id),
            or_(
                Event.occurred_at < watermark_at,
                and_(
                    Event.occurred_at == watermark_at,
                    Event.event_id <= watermark_event_id,
                ),
            ),
        ]
        if after is not None:
            after_at, after_event_id = after
            predicates.append(
                or_(
                    Event.occurred_at > after_at,
                    and_(
                        Event.occurred_at == after_at,
                        Event.event_id > after_event_id,
                    ),
                )
            )
        rows = list(
            self.session.scalars(
                select(Event)
                .where(*predicates)
                .order_by(Event.occurred_at.asc(), Event.event_id.asc())
                .limit(limit + 1)
            ).all()
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        if not page_rows:
            self._cursor_error(
                principal=principal,
                request_id=request_id,
                case_id=case_id,
                reason="position",
            )

        try:
            for row in page_rows:
                self._validate_timeline_event_authority(row)
            events = [self._timeline_event(row) for row in page_rows]
        except (ValidationError, ValueError, TypeError, V4IntegrityError) as exc:
            self._raise_invariant_failure(
                principal=principal,
                request_id=request_id,
                action=action,
                target=target,
                reason=type(exc).__name__,
            )
        next_cursor = None
        if has_more:
            last = page_rows[-1]
            next_cursor = self._encode_cursor(
                scope_digest=scope_digest,
                limit=limit,
                watermark_at=watermark_at,
                watermark_event_id=watermark_event_id,
                after_at=_aware(last.occurred_at),
                after_event_id=last.event_id,
            )

        snapshot = TimelineSnapshot(
            watermark_event_id=watermark_event_id,
            order=_ORDER,
            filter_digest=filter_digest,
            cursor_scope_digest=scope_digest,
        )
        data = CaseTimelineData(
            case_id=case_id,
            events=events,
            page=TimelinePage(
                limit=limit,
                next_cursor=next_cursor,
                has_more=has_more,
                snapshot=snapshot,
            ),
        )
        audit = self._record_audit(
            principal=principal,
            action=action,
            target=target,
            params={
                "request_id": request_id,
                "case_id": case_id,
                "limit": limit,
                "cursor_supplied": cursor is not None,
                "cursor_scope_digest": scope_digest,
            },
            evidence_refs={
                "watermark_event_id": watermark_event_id,
                "returned_event_ids": [row.event_id for row in page_rows],
            },
        )
        try:
            return CaseTimelineResponse(
                schema_version="1.0",
                workspace_id=principal.workspace_id,
                request_id=request_id,
                audit_ref=audit.audit_ref,
                data=data,
            )
        except ValidationError as exc:
            raise PublicReadError(
                "INTERNAL_ERROR",
                audit_ref=audit.audit_ref,
                workspace_id=principal.workspace_id,
            ) from exc

    def get_evidence(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        receipt_id: str,
    ) -> EvidenceResponse:
        action = "public.evidence.get"
        target = f"trace_evidence_receipt:{receipt_id}"
        self._require_scope(
            principal=principal,
            required_scope="artifacts:read",
            request_id=request_id,
            action=action,
            target=target,
        )
        row = self.session.scalar(
            select(TraceEvidenceReceiptRow).where(
                TraceEvidenceReceiptRow.workspace_id == principal.workspace_id,
                TraceEvidenceReceiptRow.receipt_id == receipt_id,
            )
        )
        if row is None:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=target,
            )
        signal = self.session.scalar(
            select(Signal).where(
                Signal.workspace_id == principal.workspace_id,
                Signal.signal_id == row.signal_id,
            )
        )
        if signal is None:
            self._raise_invariant_failure(
                principal=principal,
                request_id=request_id,
                action=action,
                target=target,
                reason="evidence_signal_missing",
            )
        try:
            self._validate_signal_integrity(signal)
            self._validate_authority_receipt(
                authority_receipt_id=signal.authority_receipt_id,
                workspace_id=signal.workspace_id,
                subject_kind="SIGNAL_RECORD",
                subject_id=signal.signal_id,
                subject_revision=None,
                subject_digest=signal.signal_digest,
            )
        except (V4IntegrityError, TypeError, ValueError) as exc:
            self._raise_invariant_failure(
                principal=principal,
                request_id=request_id,
                action=action,
                target=target,
                reason=type(exc).__name__,
            )
        if not self._resource_granted(principal, signal.project_id, signal.environment_id):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=target,
            )

        verified_at = _utc_now()
        try:
            receipt = self._validate_evidence_integrity(row)
            self._validate_authority_receipt(
                authority_receipt_id=row.authority_receipt_id,
                workspace_id=row.workspace_id,
                subject_kind="TRACE_EVIDENCE_RECEIPT",
                subject_id=row.receipt_id,
                subject_revision=None,
                subject_digest=row.receipt_digest,
            )
            self._validate_evidence_signal_binding(row, signal)
            data = EvidenceData(
                receipt_kind="TRACE_EVIDENCE_RECEIPT",
                receipt=receipt,
                receipt_digest=row.receipt_digest,
                verification_status="VERIFIED",
                verified_at=verified_at,
                superseded_by=None,
            )
        except (ValidationError, V4IntegrityError, TypeError, ValueError) as exc:
            self._raise_invariant_failure(
                principal=principal,
                request_id=request_id,
                action=action,
                target=target,
                reason=type(exc).__name__,
            )

        audit = self._record_audit(
            principal=principal,
            action=action,
            target=target,
            params={"request_id": request_id, "receipt_id": receipt_id},
            evidence_refs={"receipt_digest": row.receipt_digest},
        )
        try:
            return EvidenceResponse(
                schema_version="1.0",
                workspace_id=principal.workspace_id,
                request_id=request_id,
                audit_ref=audit.audit_ref,
                data=data,
            )
        except ValidationError as exc:
            raise PublicReadError(
                "INTERNAL_ERROR",
                audit_ref=audit.audit_ref,
                workspace_id=principal.workspace_id,
            ) from exc

    @staticmethod
    def _validate_case_integrity(case: QualityCase) -> None:
        payload = case.snapshot_payload
        if not isinstance(payload, dict):
            raise V4IntegrityError("v4.case_snapshot_invalid")
        assert_record_digest(payload, self_digest_field="record_digest")
        expected = {
            "schema_version": "1.0",
            "case_id": case.case_id,
            "workspace_id": case.workspace_id,
            "status": case.state,
            "revision": case.revision,
            "title": case.title,
            "project_id": case.project_id,
            "environment_id": case.environment_id,
            "governed_agent_id": case.governed_agent_id,
            "correlation_status": case.correlation_status,
            "triage_status": case.triage_status,
            "opening_signal_id": case.opening_signal_id,
            "authority_receipt_id": case.authority_receipt_id,
            "immutable": True,
            "hash_rule": (
                "jcs-rfc8785-v1+sha256(excluding:/record_digest)"
            ),
            "record_digest": case.record_digest,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise V4IntegrityError("v4.case_projection_binding_mismatch")
        time_bindings = (
            (_payload_time(payload.get("opened_at")), _aware(case.opened_at)),
            (_payload_time(payload.get("updated_at")), _aware(case.updated_at)),
            (
                _payload_time(payload.get("resolved_at")),
                _aware(case.resolved_at) if case.resolved_at is not None else None,
            ),
        )
        if any(left != right for left, right in time_bindings):
            raise V4IntegrityError("v4.case_projection_time_mismatch")

    @staticmethod
    def _validate_signal_integrity(signal: Signal) -> None:
        payload = signal.envelope_payload
        if not isinstance(payload, dict):
            raise V4IntegrityError("v4.signal_envelope_invalid")
        assert_record_digest(payload, self_digest_field="signal_digest")
        expected = {
            "schema_version": "1.0",
            "signal_id": signal.signal_id,
            "workspace_id": signal.workspace_id,
            "project_id": signal.project_id,
            "environment_id": signal.environment_id,
            "governed_agent_id": signal.governed_agent_id,
            "source": {
                "source_id": signal.source_id,
                "adapter_kind": signal.adapter_kind,
                "source_event_id": signal.source_event_id,
                "source_event_version": signal.source_event_version,
                "provider_origin": signal.provider_origin,
                "payload_digest": signal.source_payload_digest,
            },
            "signal_kind": signal.signal_kind,
            "reporter": {
                "kind": signal.reporter_kind,
                "ref": signal.reporter_ref,
            },
            "content_ref": signal.content_ref,
            "agent_run_ref_id": signal.agent_run_ref_id,
            "privacy": signal.privacy,
            "completeness": signal.completeness,
            "missing_fields": signal.missing_fields,
            "untrusted_content": signal.untrusted_content,
            "immutable": True,
            "hash_rule": (
                "jcs-rfc8785-v1+sha256(excluding:/signal_digest)"
            ),
            "signal_digest": signal.signal_digest,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise V4IntegrityError("v4.signal_projection_binding_mismatch")
        if (
            _payload_time(payload.get("occurred_at")) != _aware(signal.occurred_at)
            or _payload_time(payload.get("observed_at"))
            != _aware(signal.observed_at)
        ):
            raise V4IntegrityError("v4.signal_projection_time_mismatch")

    @staticmethod
    def _validate_link_integrity(link: SignalCaseLink) -> None:
        payload = link.link_payload
        if not isinstance(payload, dict):
            raise V4IntegrityError("v4.signal_case_link_invalid")
        assert_record_digest(payload, self_digest_field="link_digest")
        expected = {
            "schema_version": "1.0",
            "signal_case_link_id": link.signal_case_link_id,
            "workspace_id": link.workspace_id,
            "signal_id": link.signal_id,
            "case_id": link.case_id,
            "revision": link.revision,
            "state": link.state,
            "authority_receipt_id": link.authority_receipt_id,
            "immutable": True,
            "hash_rule": (
                "jcs-rfc8785-v1+sha256(excluding:/link_digest)"
            ),
            "link_digest": link.link_digest,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise V4IntegrityError("v4.signal_case_link_binding_mismatch")
        if _payload_time(payload.get("created_at")) != _aware(link.created_at):
            raise V4IntegrityError("v4.signal_case_link_time_mismatch")

    @staticmethod
    def _validate_evidence_integrity(
        row: TraceEvidenceReceiptRow,
    ) -> TraceEvidenceReceipt:
        payload = row.receipt_payload
        if not isinstance(payload, dict):
            raise V4IntegrityError("v4.evidence_receipt_invalid")
        receipt = TraceEvidenceReceipt.model_validate(payload)
        assert_record_digest(payload, self_digest_field="receipt_digest")
        expected = {
            "schema_version": "1.0",
            "receipt_id": row.receipt_id,
            "workspace_id": row.workspace_id,
            "source_id": row.source_id,
            "signal_id": row.signal_id,
            "signal_digest": row.signal_digest,
            "collection_mode": row.collection_mode,
            "agent_run_ref_id": row.agent_run_ref_id,
            "agent_run_ref_digest": row.agent_run_ref_digest,
            "query": row.query,
            "requested_fields": row.requested_fields,
            "field_results": row.field_results,
            "completeness": row.completeness,
            "artifact_ref": row.artifact_ref,
            "source_payload_digest": row.source_payload_digest,
            "deep_link": row.deep_link,
            "failure": row.failure,
            "authority_receipt_id": row.authority_receipt_id,
            "immutable": True,
            "hash_rule": (
                "jcs-rfc8785-v1+sha256(excluding:/receipt_digest)"
            ),
            "receipt_digest": row.receipt_digest,
        }
        if any(payload.get(key) != value for key, value in expected.items()):
            raise V4IntegrityError("v4.evidence_projection_binding_mismatch")
        time_bindings = (
            (_payload_time(payload.get("collected_at")), _aware(row.collected_at)),
            (
                _payload_time(payload.get("retention_expires_at")),
                _aware(row.retention_expires_at)
                if row.retention_expires_at is not None
                else None,
            ),
        )
        if any(left != right for left, right in time_bindings):
            raise V4IntegrityError("v4.evidence_projection_time_mismatch")
        return receipt

    def get_capabilities(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        server_version: str,
        implemented_intents: Sequence[dict[str, object]],
    ) -> ServerCapabilitiesResponse:
        action = "public.capabilities.get"
        target = "public_server:capabilities"
        self._require_scope(
            principal=principal,
            required_scope="capabilities:read",
            request_id=request_id,
            action=action,
            target=target,
        )
        generated_at = _utc_now()
        try:
            enabled: list[EnabledIntent] = []
            for raw in implemented_intents:
                intent = EnabledIntent.model_validate(raw)
                if intent.scope in principal.scopes:
                    enabled.append(intent)
            data = ServerCapabilitiesData(
                server_version=server_version,
                public_api_major=1,
                supported_contract_versions=["1.0"],
                principal=CapabilityPrincipal(
                    principal_id=principal.principal_id,
                    principal_type=principal.principal_type,
                    scopes=principal.scopes,
                    credential_expires_at=principal.expires_at,
                ),
                enabled_intents=enabled,
                generated_at=generated_at,
            )
        except (ValidationError, TypeError, ValueError) as exc:
            self._raise_invariant_failure(
                principal=principal,
                request_id=request_id,
                action=action,
                target=target,
                reason=type(exc).__name__,
            )

        audit = self._record_audit(
            principal=principal,
            action=action,
            target=target,
            params={
                "request_id": request_id,
                "server_version": server_version,
                "enabled_intents": [intent.name for intent in data.enabled_intents],
            },
        )
        try:
            return ServerCapabilitiesResponse(
                schema_version="1.0",
                workspace_id=principal.workspace_id,
                request_id=request_id,
                audit_ref=audit.audit_ref,
                data=data,
            )
        except ValidationError as exc:
            raise PublicReadError(
                "INTERNAL_ERROR",
                audit_ref=audit.audit_ref,
                workspace_id=principal.workspace_id,
            ) from exc

    def _case_data(
        self,
        case: QualityCase,
        *,
        principal: AcceptedPrincipalContext,
    ) -> CaseData:
        self._validate_case_integrity(case)
        links = list(
            self.session.scalars(
                select(SignalCaseLink)
                .where(
                    SignalCaseLink.workspace_id == principal.workspace_id,
                    SignalCaseLink.case_id == case.case_id,
                    SignalCaseLink.state == "LINKED",
                )
                .order_by(SignalCaseLink.created_at.asc(), SignalCaseLink.signal_id.asc())
            ).all()
        )
        signal_refs = list(dict.fromkeys(link.signal_id for link in links))
        if not signal_refs or case.opening_signal_id not in signal_refs:
            raise V4IntegrityError("v4.case_signal_projection_incomplete")
        for link in links:
            self._validate_link_integrity(link)
            self._validate_authority_receipt(
                authority_receipt_id=link.authority_receipt_id,
                workspace_id=link.workspace_id,
                subject_kind="SIGNAL_CASE_LINK",
                subject_id=link.signal_case_link_id,
                subject_revision=link.revision,
                subject_digest=link.link_digest,
            )
        signals = list(
            self.session.scalars(
                select(Signal).where(
                    Signal.workspace_id == principal.workspace_id,
                    Signal.signal_id.in_(signal_refs),
                )
            ).all()
        )
        for signal in signals:
            self._validate_signal_integrity(signal)
            self._validate_authority_receipt(
                authority_receipt_id=signal.authority_receipt_id,
                workspace_id=signal.workspace_id,
                subject_kind="SIGNAL_RECORD",
                subject_id=signal.signal_id,
                subject_revision=None,
                subject_digest=signal.signal_digest,
            )
        signals_by_id = {signal.signal_id: signal for signal in signals}
        self._validate_case_signal_graph(
            case=case,
            links=links,
            signals_by_id=signals_by_id,
        )
        run_refs = list(
            dict.fromkeys(
                signal.agent_run_ref_id
                for signal in signals
                if signal.agent_run_ref_id is not None
            )
        )
        evidence = self.session.scalar(
            select(TraceEvidenceReceiptRow)
            .where(
                TraceEvidenceReceiptRow.workspace_id == principal.workspace_id,
                TraceEvidenceReceiptRow.signal_id.in_(signal_refs),
            )
            .order_by(
                TraceEvidenceReceiptRow.collected_at.desc(),
                TraceEvidenceReceiptRow.receipt_id.desc(),
            )
            .limit(1)
        )
        if evidence is None:
            raise V4IntegrityError("v4.case_evidence_projection_missing")
        payload = self._validate_evidence_integrity(evidence)
        self._validate_authority_receipt(
            authority_receipt_id=evidence.authority_receipt_id,
            workspace_id=evidence.workspace_id,
            subject_kind="TRACE_EVIDENCE_RECEIPT",
            subject_id=evidence.receipt_id,
            subject_revision=None,
            subject_digest=evidence.receipt_digest,
        )
        evidence_signal = signals_by_id.get(evidence.signal_id)
        if evidence_signal is None:
            raise V4IntegrityError("v4.case_evidence_signal_graph_mismatch")
        self._validate_evidence_signal_binding(evidence, evidence_signal)
        missing_fields = [
            result.name for result in payload.field_results if result.status == "MISSING"
        ]
        evidence_summary = EvidenceSummary(
            status=evidence.completeness,
            receipt_id=evidence.receipt_id,
            receipt_digest=evidence.receipt_digest,
            agent_run_ref_id=evidence.agent_run_ref_id,
            missing_fields=missing_fields,
        )
        snapshot = case.snapshot_payload if isinstance(case.snapshot_payload, dict) else {}
        resolution_ref = snapshot.get("resolution_ref")
        return CaseData(
            case_id=case.case_id,
            status=case.state,
            revision=case.revision,
            title=case.title,
            project_id=case.project_id,
            environment_id=case.environment_id,
            governed_agent_id=case.governed_agent_id,
            correlation_status=case.correlation_status,
            triage_status=case.triage_status,
            signal_refs=signal_refs,
            run_refs=run_refs,
            evidence_summary=evidence_summary,
            input_summary=None,
            output_summary=None,
            opened_at=_aware(case.opened_at),
            updated_at=_aware(case.updated_at),
            resolved_at=_aware(case.resolved_at) if case.resolved_at is not None else None,
            resolution_ref=resolution_ref,
            next_action=self._case_next_action(case),
        )

    @staticmethod
    def _case_next_action(case: QualityCase) -> NextAction:
        if case.state == "RESOLVED":
            return NextAction(code="NONE", command=None, href=None)
        if case.correlation_status == "NEEDS_CORRELATION":
            return NextAction(
                code="CORRELATE_TRACE", command=None, href=None
            )
        return NextAction(code="WAIT_FOR_INVESTIGATION", command=None, href=None)

    @staticmethod
    def _timeline_scope(workspace_id: str, case_id: str) -> tuple[Any, ...]:
        return (
            Event.contract_version == "v4",
            Event.event_version == "1.0",
            Event.workspace_id == workspace_id,
            Event.correlation_id == case_id,
        )

    @staticmethod
    def _timeline_event(row: Event) -> TimelineEvent:
        return TimelineEvent(
            event_id=row.event_id,
            event_type=row.event_type,
            event_version=row.event_version,
            occurred_at=_aware(row.occurred_at),
            causation_id=None if row.causation_id == "none" else row.causation_id,
            correlation_id=row.correlation_id,
            actor_principal_id=row.actor_principal,
            transaction_id=row.transaction_id,
            payload_ref={
                "uri": f"caseloop-artifact://event/{row.event_id}/payload",
                "digest": row.payload_digest,
                "media_type": "application/json",
            },
            payload_digest=row.payload_digest,
            redaction_status="NOT_REQUIRED",
        )

    def _load_case_or_deny(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        case_id: str,
        action: str,
    ) -> QualityCase:
        case = self.session.scalar(
            select(QualityCase).where(
                QualityCase.workspace_id == principal.workspace_id,
                QualityCase.case_id == case_id,
            )
        )
        if case is None:
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
            )
        try:
            self._validate_case_integrity(case)
            self._validate_authority_receipt(
                authority_receipt_id=case.authority_receipt_id,
                workspace_id=case.workspace_id,
                subject_kind="QUALITY_CASE",
                subject_id=case.case_id,
                subject_revision=case.revision,
                subject_digest=case.record_digest,
            )
        except (V4IntegrityError, TypeError, ValueError) as exc:
            self._raise_invariant_failure(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
                reason=type(exc).__name__,
            )
        if not self._resource_granted(
            principal, case.project_id, case.environment_id
        ):
            self._deny_not_found(
                principal=principal,
                request_id=request_id,
                action=action,
                target=f"quality_case:{case_id}",
            )
        return case

    def _validate_authority_receipt(
        self,
        *,
        authority_receipt_id: str,
        workspace_id: str,
        subject_kind: str,
        subject_id: str,
        subject_revision: int | None,
        subject_digest: str,
    ) -> None:
        try:
            self._authority.validate_receipt_binding(
                authority_receipt_id=authority_receipt_id,
                workspace_id=workspace_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                subject_revision=subject_revision,
                subject_digest=subject_digest,
            )
        except (AuthorityError, V4IntegrityError, TypeError, ValueError) as exc:
            raise V4IntegrityError("v4.authority_receipt_binding_invalid") from exc

    def _validate_timeline_event_authority(self, row: Event) -> None:
        payload = row.payload
        if not isinstance(payload, dict):
            raise V4IntegrityError("v4.timeline_event_payload_invalid")
        authority_receipt_id = payload.get("authority_receipt_id")
        subject_kind = payload.get("subject_kind")
        subject_id = payload.get("subject_id")
        subject_revision = payload.get("subject_revision")
        subject_digest = payload.get("subject_digest")
        if not all(
            isinstance(value, str) and value
            for value in (
                authority_receipt_id,
                subject_kind,
                subject_id,
                subject_digest,
            )
        ) or not (
            subject_revision is None
            or (
                isinstance(subject_revision, int)
                and not isinstance(subject_revision, bool)
                and subject_revision >= 1
            )
        ):
            raise V4IntegrityError("v4.timeline_event_authority_fields_invalid")
        self._validate_authority_receipt(
            authority_receipt_id=authority_receipt_id,
            workspace_id=row.workspace_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            subject_revision=subject_revision,
            subject_digest=subject_digest,
        )
        if subject_kind == "TRACE_EVIDENCE_RECEIPT":
            self._validate_timeline_evidence_signal_graph(
                row=row,
                authority_receipt_id=authority_receipt_id,
                subject_id=subject_id,
            )

    @staticmethod
    def _validate_case_signal_graph(
        *,
        case: QualityCase,
        links: list[SignalCaseLink],
        signals_by_id: dict[str, Signal],
    ) -> None:
        linked_signal_ids = [link.signal_id for link in links]
        if (
            not linked_signal_ids
            or case.opening_signal_id not in linked_signal_ids
            or len(set(linked_signal_ids)) != len(linked_signal_ids)
            or set(linked_signal_ids) != set(signals_by_id)
            or any(
                link.workspace_id != case.workspace_id
                or link.case_id != case.case_id
                or link.signal_id not in signals_by_id
                for link in links
            )
            or any(
                signal.workspace_id != case.workspace_id
                or signal.signal_id != signal_id
                for signal_id, signal in signals_by_id.items()
            )
        ):
            raise V4IntegrityError("v4.case_signal_graph_binding_mismatch")

    @staticmethod
    def _validate_evidence_signal_binding(
        evidence: TraceEvidenceReceiptRow,
        signal: Signal,
    ) -> None:
        if (
            evidence.workspace_id != signal.workspace_id
            or evidence.signal_id != signal.signal_id
            or evidence.signal_digest != signal.signal_digest
        ):
            raise V4IntegrityError("v4.evidence_signal_binding_mismatch")

    def _validate_timeline_evidence_signal_graph(
        self,
        *,
        row: Event,
        authority_receipt_id: str,
        subject_id: str,
    ) -> None:
        evidence = self.session.get(TraceEvidenceReceiptRow, subject_id)
        if (
            evidence is None
            or evidence.workspace_id != row.workspace_id
            or evidence.authority_receipt_id != authority_receipt_id
        ):
            raise V4IntegrityError("v4.timeline_evidence_projection_missing")
        self._validate_evidence_integrity(evidence)
        signal = self.session.get(Signal, evidence.signal_id)
        if signal is None or signal.workspace_id != row.workspace_id:
            raise V4IntegrityError("v4.timeline_evidence_signal_missing")
        self._validate_signal_integrity(signal)
        self._validate_authority_receipt(
            authority_receipt_id=signal.authority_receipt_id,
            workspace_id=signal.workspace_id,
            subject_kind="SIGNAL_RECORD",
            subject_id=signal.signal_id,
            subject_revision=None,
            subject_digest=signal.signal_digest,
        )
        self._validate_evidence_signal_binding(evidence, signal)
        links = list(
            self.session.scalars(
                select(SignalCaseLink).where(
                    SignalCaseLink.workspace_id == row.workspace_id,
                    SignalCaseLink.case_id == row.correlation_id,
                    SignalCaseLink.signal_id == signal.signal_id,
                    SignalCaseLink.state == "LINKED",
                )
            ).all()
        )
        if len(links) != 1:
            raise V4IntegrityError("v4.timeline_evidence_case_link_mismatch")
        link = links[0]
        self._validate_link_integrity(link)
        self._validate_authority_receipt(
            authority_receipt_id=link.authority_receipt_id,
            workspace_id=link.workspace_id,
            subject_kind="SIGNAL_CASE_LINK",
            subject_id=link.signal_case_link_id,
            subject_revision=link.revision,
            subject_digest=link.link_digest,
        )

    @staticmethod
    def _resource_granted(
        principal: AcceptedPrincipalContext,
        project_id: str | None,
        environment_id: str | None,
    ) -> bool:
        if project_id is not None and project_id not in principal.project_ids:
            return False
        if environment_id is not None and environment_id not in principal.environment_ids:
            return False
        return True

    def _require_scope(
        self,
        *,
        principal: AcceptedPrincipalContext,
        required_scope: str,
        request_id: str,
        action: str,
        target: str,
    ) -> None:
        if (
            required_scope in principal.scopes
            and principal.requested_context.workspace_id == principal.workspace_id
            and principal.requested_context.required_scope == required_scope
        ):
            return
        audit = self._record_audit(
            principal=principal,
            action=action,
            target=target,
            params={"request_id": request_id, "required_scope": required_scope},
            result="denied",
            error_code="SCOPE_FORBIDDEN",
        )
        raise PublicReadDenial(
            "SCOPE_FORBIDDEN",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

    def _deny_not_found(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        action: str,
        target: str,
    ) -> NoReturn:
        audit = self._record_audit(
            principal=principal,
            action=action,
            target=target,
            params={"request_id": request_id, "resource_requested": True},
            result="denied",
            error_code="RESOURCE_NOT_FOUND",
        )
        raise PublicReadDenial(
            "RESOURCE_NOT_FOUND",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
            details={},
        )

    def _raise_invariant_failure(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        action: str,
        target: str,
        reason: str,
    ) -> NoReturn:
        audit = self._record_audit(
            principal=principal,
            action=action,
            target=target,
            params={"request_id": request_id, "invariant_failure": reason},
            result="error",
            error_code="INTERNAL_ERROR",
        )
        raise PublicReadError(
            "INTERNAL_ERROR",
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

    def _cursor_error(
        self,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        case_id: str,
        reason: str,
    ) -> NoReturn:
        audit = self._record_audit(
            principal=principal,
            action="public.cases.timeline",
            target=f"quality_case:{case_id}",
            params={"request_id": request_id, "cursor_rejected": reason},
            result="denied",
            error_code="VALIDATION_FAILED",
        )
        raise PublicReadDenial(
            "VALIDATION_FAILED",
            details={"fields": ["cursor"]},
            audit_ref=audit.audit_ref,
            workspace_id=principal.workspace_id,
        )

    def _record_audit(
        self,
        *,
        principal: AcceptedPrincipalContext,
        action: str,
        target: str,
        params: dict[str, object],
        result: str = "success",
        error_code: str | None = None,
        evidence_refs: dict[str, Any] | None = None,
    ):
        try:
            return self._audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action=action,
                target=target,
                params=params,
                result=result,
                error_code=error_code,
                trace_id=request_id_to_trace_id(params.get("request_id")),
                evidence_refs=evidence_refs,
            )
        except V4AuditUnavailable as exc:
            raise PublicReadError(
                "AUDIT_UNAVAILABLE",
                workspace_id=principal.workspace_id,
                rollback_required=True,
            ) from exc

    def _cursor_scope_digest(
        self,
        *,
        principal: AcceptedPrincipalContext,
        case_id: str,
        filter_digest: str,
        watermark_at: datetime,
        watermark_event_id: str,
        limit: int,
    ) -> str:
        return canonical_digest(
            {
                "workspace_id": principal.workspace_id,
                "principal_id": principal.principal_id,
                "case_id": case_id,
                "order": _ORDER,
                "filter_digest": filter_digest,
                "watermark_event_id": watermark_event_id,
                "watermark_occurred_at": _aware(watermark_at).isoformat(),
                "limit": limit,
            }
        )

    def _encode_cursor(
        self,
        *,
        scope_digest: str,
        limit: int,
        watermark_at: datetime,
        watermark_event_id: str,
        after_at: datetime,
        after_event_id: str,
    ) -> str:
        payload = {
            "v": 1,
            "s": scope_digest,
            "l": limit,
            "w": [_epoch_microseconds(watermark_at), watermark_event_id],
            "a": [_epoch_microseconds(after_at), after_event_id],
        }
        raw = canonicalize(payload)
        signature = hmac.new(self._cursor_key, raw, hashlib.sha256).digest()
        opaque = base64.urlsafe_b64encode(raw + signature).rstrip(b"=").decode("ascii")
        token = _CURSOR_PREFIX + opaque
        if len(token) > _MAX_CURSOR_LENGTH:
            raise PublicReadError("INTERNAL_ERROR")
        return token

    def _decode_cursor_or_error(
        self,
        cursor: str,
        *,
        principal: AcceptedPrincipalContext,
        request_id: str,
        case_id: str,
    ) -> dict[str, Any]:
        try:
            if (
                not isinstance(cursor, str)
                or not cursor.startswith(_CURSOR_PREFIX)
                or len(cursor) > _MAX_CURSOR_LENGTH
            ):
                raise ValueError("cursor shape")
            encoded = cursor[len(_CURSOR_PREFIX) :]
            padding = "=" * (-len(encoded) % 4)
            combined = base64.b64decode(
                encoded + padding,
                altchars=b"-_",
                validate=True,
            )
            if len(combined) <= _CURSOR_SIGNATURE_BYTES:
                raise ValueError("cursor length")
            raw = combined[:-_CURSOR_SIGNATURE_BYTES]
            supplied_signature = combined[-_CURSOR_SIGNATURE_BYTES:]
            expected_signature = hmac.new(
                self._cursor_key, raw, hashlib.sha256
            ).digest()
            if not hmac.compare_digest(supplied_signature, expected_signature):
                raise ValueError("cursor signature")
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) != {"v", "s", "l", "w", "a"}:
                raise ValueError("cursor fields")
            if payload["v"] != 1 or payload["l"] is True or not isinstance(payload["l"], int):
                raise ValueError("cursor version")
            if payload["l"] < 1 or payload["l"] > 200:
                raise ValueError("cursor limit")
            if not isinstance(payload["s"], str):
                raise ValueError("cursor scope")
            watermark = payload["w"]
            after = payload["a"]
            if (
                not isinstance(watermark, list)
                or not isinstance(after, list)
                or len(watermark) != 2
                or len(after) != 2
                or isinstance(watermark[0], bool)
                or isinstance(after[0], bool)
                or not isinstance(watermark[0], int)
                or not isinstance(after[0], int)
                or not isinstance(watermark[1], str)
                or not isinstance(after[1], str)
            ):
                raise ValueError("cursor position")
            return {
                "scope_digest": payload["s"],
                "limit": payload["l"],
                "watermark_at": _from_epoch_microseconds(watermark[0]),
                "watermark_event_id": watermark[1],
                "after_at": _from_epoch_microseconds(after[0]),
                "after_event_id": after[1],
            }
        except (ValueError, TypeError, json.JSONDecodeError, OverflowError):
            self._cursor_error(
                principal=principal,
                request_id=request_id,
                case_id=case_id,
                reason="invalid",
            )


def request_id_to_trace_id(value: object) -> str | None:
    """Use an already-public request ID as the audit trace correlation value."""

    return value if isinstance(value, str) else None


__all__ = ["PublicReadDenial", "PublicReadError", "PublicReadService"]
