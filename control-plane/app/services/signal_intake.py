"""Stage 1A authenticated maintainer Signal intake transaction."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import hashlib
import hmac
import re

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.tables import Audit
from app.models.v4_tables import (
    PublicCommandIdempotency,
    PublicPrincipal,
    QualityCase,
    Signal,
    SignalCaseLink,
    SignalContent,
    SourceConnection,
    TraceEvidenceReceipt,
)
from app.public_api.auth_contract import AcceptedPrincipalContext
from app.public_api.credential_resolver import digest_public_subject
from app.public_api.models import (
    SignalSubmission,
    SignalSubmissionResponse,
    TraceEvidenceReceipt as TraceEvidenceReceiptWire,
)
from app.services.authority import AuthorityError, AuthorityService, ResolvedController
from app.services.public_idempotency import (
    PublicIdempotencyError,
    PublicIdempotencyService,
)
from app.services.v4_audit import (
    V4AuditIntegrityError,
    V4AuditService,
    V4AuditUnavailable,
    validate_v4_audit_row,
)
from app.services.v4_event_store import V4EventStore, V4EventStoreError
from app.utils.ids import (
    new_authority_receipt_id,
    new_case_id,
    new_idempotency_receipt_id,
    new_request_id,
    new_signal_case_link_id,
    new_signal_content_id,
    new_signal_id,
    new_trace_evidence_receipt_id,
    new_transaction_id,
)
from app.utils.v4_integrity import (
    V4IntegrityError,
    assert_record_digest,
    canonical_digest,
    record_digest,
)


Clock = Callable[[], datetime]
MISSING_TRACE_FIELDS = [
    "trace.input",
    "trace.output",
    "observations.model",
    "observations.tools",
]
# The response digest deliberately covers the base SignalSubmissionResponse with
# the top-level idempotency delivery removed.  The receipt then binds that
# digest; hashing the receipt-bearing response would create a digest cycle.
BASE_RESPONSE_DIGEST_RULE = (
    "canonical_digest(SignalSubmissionResponse excluding:/idempotency)"
)
_SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "authorization",
    "bearer",
    "credential",
    "password",
    "raw_jti",
    "secret",
    "token",
}


class SignalIntakeError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        details: dict[str, object] | None = None,
        audit_ref: str | None = None,
    ) -> None:
        self.code = code
        self.details = details or {}
        self.audit_ref = audit_ref
        self.rollback_required = True
        super().__init__(code)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _has_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in _SENSITIVE_CONFIG_KEYS or any(
                marker in normalized for marker in ("password", "secret", "token")
            ):
                return True
            if _has_sensitive_key(child):
                return True
    elif isinstance(value, list):
        return any(_has_sensitive_key(item) for item in value)
    return False


class SignalIntakeService:
    def __init__(
        self,
        session: Session,
        *,
        clock: Clock | None = None,
        contracts_root: str | Path | None = None,
        audit_service: V4AuditService | None = None,
        event_store: V4EventStore | None = None,
        authority_service: AuthorityService | None = None,
        idempotency_service: PublicIdempotencyService | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or _utc_now
        self.audit = audit_service or V4AuditService(session, clock=self.clock)
        self.events = event_store or V4EventStore(session)
        self.authority = authority_service or AuthorityService(
            session, contracts_root=contracts_root
        )
        self.idempotency = idempotency_service or PublicIdempotencyService(session)

    def submit(
        self,
        submission: SignalSubmission,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_id: str | None = None,
    ) -> SignalSubmissionResponse:
        request = request_id or new_request_id()
        self._validate_request(submission, principal)
        self._validate_principal_row(principal)
        body = submission.model_dump(mode="json")
        request_fingerprint = self.idempotency.fingerprint(body)
        try:
            lookup = self.idempotency.acquire(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent="signals.submit",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
        except PublicIdempotencyError as exc:
            raise SignalIntakeError(exc.code) from exc
        if lookup.record is not None:
            try:
                response = self.idempotency.replay_signal_response(lookup.record)
                self._validate_same_key_replay(
                    record=lookup.record,
                    response=response,
                    submission=submission,
                    principal=principal,
                )
                return response
            except PublicIdempotencyError as exc:
                raise SignalIntakeError(exc.code) from exc
            except SignalIntakeError:
                raise
            except (
                AuthorityError,
                V4AuditIntegrityError,
                V4IntegrityError,
                TypeError,
                ValueError,
            ) as exc:
                raise SignalIntakeError("INTERNAL_ERROR") from exc

        source = self.session.scalar(
            select(SourceConnection)
            .where(
                SourceConnection.workspace_id == principal.workspace_id,
                SourceConnection.source_id == submission.source_id,
                SourceConnection.connector_kind == "manual",
                SourceConnection.state == "ACTIVE",
            )
            .with_for_update()
        )
        if source is None:
            raise SignalIntakeError("RESOURCE_NOT_FOUND")
        self._validate_source_connection(
            source,
            principal=principal,
            expected_source_id=submission.source_id,
        )
        if _has_sensitive_key(source.config or {}):
            raise SignalIntakeError("INTERNAL_ERROR")
        provider_origin = (source.config or {}).get(
            "provider_origin", "https://caseloop.local"
        )
        if not isinstance(provider_origin, str) or not provider_origin.startswith(
            ("https://", "http://")
        ):
            raise SignalIntakeError("INTERNAL_ERROR")

        # Public-command idempotency and connector-event dedupe are independent
        # namespaces.  Serialize the latter too, otherwise two different public
        # keys can both observe an absent Signal and race into the unique index.
        self._lock_source_event(
            workspace_id=principal.workspace_id,
            source_id=source.source_id,
            source_event_id=submission.source_event_id,
        )

        existing = self.session.scalar(
            select(Signal)
            .where(
                Signal.workspace_id == principal.workspace_id,
                Signal.source_id == submission.source_id,
                Signal.source_event_id == submission.source_event_id,
            )
            .with_for_update()
        )
        if existing is not None:
            if existing.source_payload_digest != request_fingerprint:
                raise SignalIntakeError(
                    "VALIDATION_FAILED",
                    details={"reason": "SOURCE_EVENT_PAYLOAD_CONFLICT"},
                )
            return self._complete_duplicate_source_event(
                existing=existing,
                principal=principal,
                submission=submission,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                request_id=request,
            )
        return self._create_signal_slice(
            source=source,
            principal=principal,
            submission=submission,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request,
            provider_origin=provider_origin,
        )

    @staticmethod
    def _validate_source_connection(
        source: SourceConnection,
        *,
        principal: AcceptedPrincipalContext,
        expected_source_id: str,
    ) -> None:
        if (
            source.workspace_id != principal.workspace_id
            or source.source_id != expected_source_id
            or source.connector_kind != "manual"
            or source.state != "ACTIVE"
            or source.credential_ref is not None
            or not isinstance(source.config, dict)
            or source.revision != 1
            or not isinstance(source.created_by_principal, str)
            or re.fullmatch(
                r"prn_[0-9A-Za-z]{8,64}", source.created_by_principal
            )
            is None
        ):
            raise SignalIntakeError("INTERNAL_ERROR")
        record = {
            "schema_version": "1.0",
            "workspace_id": source.workspace_id,
            "source_id": source.source_id,
            "connector_kind": source.connector_kind,
            "state": source.state,
            "credential_ref": source.credential_ref,
            "config": source.config,
            "revision": source.revision,
            "created_by_principal": source.created_by_principal,
        }
        try:
            expected_digest = canonical_digest(record)
        except V4IntegrityError as exc:
            raise SignalIntakeError("INTERNAL_ERROR") from exc
        if not isinstance(source.connection_digest, str) or not hmac.compare_digest(
            source.connection_digest, expected_digest
        ):
            raise SignalIntakeError("INTERNAL_ERROR")

    def _validate_same_key_replay(
        self,
        *,
        record: PublicCommandIdempotency,
        response: SignalSubmissionResponse,
        submission: SignalSubmission,
        principal: AcceptedPrincipalContext,
    ) -> None:
        signal = self.session.get(Signal, response.signal.signal_id)
        quality_case = self.session.get(QualityCase, response.case.case_id)
        evidence = self.session.get(
            TraceEvidenceReceipt, response.evidence.receipt_id
        )
        links = list(
            self.session.scalars(
                select(SignalCaseLink).where(
                    SignalCaseLink.workspace_id == principal.workspace_id,
                    SignalCaseLink.signal_id == response.signal.signal_id,
                    SignalCaseLink.case_id == response.case.case_id,
                )
            ).all()
        )
        if (
            signal is None
            or quality_case is None
            or evidence is None
            or len(links) != 1
        ):
            raise SignalIntakeError("INTERNAL_ERROR")
        link = links[0]

        if response.case.disposition == "NEW":
            expected_result = "success"
            duplicate_binding_valid = (
                response.signal.duplicate_of_signal_id is None
            )
        elif response.case.disposition == "DUPLICATE":
            expected_result = "duplicate"
            duplicate_binding_valid = (
                response.signal.duplicate_of_signal_id == signal.signal_id
            )
        else:
            raise SignalIntakeError("INTERNAL_ERROR")

        if (
            not duplicate_binding_valid
            or signal.workspace_id != principal.workspace_id
            or signal.source_id != submission.source_id
            or signal.source_event_id != submission.source_event_id
            or signal.source_payload_digest != record.request_fingerprint
            or signal.signal_digest != response.signal.signal_digest
            or signal.source_event_id != response.signal.source_event_id
            or quality_case.workspace_id != principal.workspace_id
            or quality_case.state != response.case.status
            or quality_case.revision != response.case.revision
            or quality_case.correlation_status != response.case.correlation_status
            or quality_case.triage_status != response.case.triage_status
            or evidence.workspace_id != principal.workspace_id
            or evidence.receipt_digest != response.evidence.receipt_digest
            or evidence.completeness != response.evidence.status
            or evidence.agent_run_ref_id != response.evidence.agent_run_ref_id
            or evidence.requested_fields != response.evidence.missing_fields
            or response.missing_fields != response.evidence.missing_fields
        ):
            raise SignalIntakeError("INTERNAL_ERROR")

        self._validate_duplicate_slice(
            signal=signal,
            quality_case=quality_case,
            link=link,
            evidence=evidence,
        )

        audit_ref = record.audit_ref
        if not isinstance(audit_ref, str) or not audit_ref.startswith("audit://aud_"):
            raise SignalIntakeError("INTERNAL_ERROR")
        audit = self.session.get(Audit, audit_ref.removeprefix("audit://"))
        if audit is None or record.completed_at is None:
            raise SignalIntakeError("INTERNAL_ERROR")
        validate_v4_audit_row(
            audit,
            workspace_id=record.workspace_id,
            actor_principal=record.principal_id,
            action="signals.submit",
            target=signal.signal_id,
            params={
                "request_fingerprint": record.request_fingerprint,
                "source_id": signal.source_id,
                "source_event_id": signal.source_event_id,
            },
            result=expected_result,
            error_code=None,
            transaction_id=audit.transaction_id,
            evidence_refs={
                "signal_id": signal.signal_id,
                "case_id": quality_case.case_id,
                "evidence_receipt_id": evidence.receipt_id,
            },
        )
        if (
            audit_ref != f"audit://{audit.audit_id}"
            or audit.trace_id != record.request_id
            or _as_utc(audit.ts) != _as_utc(record.completed_at)
        ):
            raise SignalIntakeError("INTERNAL_ERROR")

    def _validate_request(
        self, submission: SignalSubmission, principal: AcceptedPrincipalContext
    ) -> None:
        if submission.signal_kind != "maintainer_report":
            raise SignalIntakeError(
                "VALIDATION_FAILED",
                details={"reason": "UNSUPPORTED_MANUAL_SIGNAL_KIND"},
            )
        if (
            submission.reporter.kind != "maintainer"
            or submission.reporter.source_subject_ref != principal.subject
        ):
            raise SignalIntakeError(
                "VALIDATION_FAILED",
                details={"reason": "REPORTER_BINDING_MISMATCH"},
            )
        if submission.run_locator is not None:
            raise SignalIntakeError(
                "VALIDATION_FAILED", details={"reason": "TRACE_SOURCE_NOT_ENABLED"}
            )
        if submission.privacy_classification not in {"PUBLIC", "INTERNAL"}:
            raise SignalIntakeError(
                "VALIDATION_FAILED",
                details={"reason": "RAW_CONTENT_PROTECTION_UNAVAILABLE"},
            )
        if len(submission.content.summary) > 256:
            raise SignalIntakeError(
                "VALIDATION_FAILED", details={"reason": "CASE_TITLE_TOO_LONG"}
            )
        if (
            principal.requested_context.required_scope != "signals:write"
            or "signals:write" not in principal.scopes
        ):
            raise SignalIntakeError("SCOPE_FORBIDDEN")
        if (
            submission.project_id != principal.requested_context.project_id
            or submission.environment_id
            != principal.requested_context.environment_id
        ):
            raise SignalIntakeError("WORKSPACE_ACCESS_DENIED")
        if submission.project_id is not None and submission.project_id not in principal.project_ids:
            raise SignalIntakeError("WORKSPACE_ACCESS_DENIED")
        if (
            submission.environment_id is not None
            and submission.environment_id not in principal.environment_ids
        ):
            raise SignalIntakeError("WORKSPACE_ACCESS_DENIED")

    def _validate_principal_row(self, principal: AcceptedPrincipalContext) -> None:
        row = self.session.get(PublicPrincipal, principal.principal_id)
        if (
            row is None
            or row.workspace_id != principal.workspace_id
            or row.state != "ACTIVE"
            or row.revoked_at is not None
            or row.claims_digest != principal.claims_digest
            or row.principal_type != principal.principal_type
            or row.subject_digest != digest_public_subject(principal.subject)
            or row.audiences != principal.audiences
            or row.project_ids != principal.project_ids
            or row.environment_ids != principal.environment_ids
            or row.scopes != principal.scopes
        ):
            raise SignalIntakeError("TOKEN_INVALID")

    def _lock_source_event(
        self, *, workspace_id: str, source_id: str, source_event_id: str
    ) -> None:
        dialect = self.session.get_bind().dialect.name
        if dialect == "sqlite":
            return
        if dialect != "postgresql":
            raise SignalIntakeError("DEPENDENCY_UNAVAILABLE")
        namespace = "\x1f".join(
            ("v4-source-event", workspace_id, source_id, source_event_id)
        )
        lock_id = int.from_bytes(
            hashlib.sha256(namespace.encode("utf-8")).digest()[:8],
            "big",
            signed=True,
        )
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": lock_id},
        )

    def _resolve_controllers(
        self, workspace_id: str, at: datetime
    ) -> dict[str, ResolvedController]:
        specs = {
            "signal": ("SIGNAL_RECORD", "signals.submit", "signal.received"),
            "case": ("QUALITY_CASE", "cases.open-from-signal", "case.opened"),
            "link": (
                "SIGNAL_CASE_LINK",
                "signals.link-case",
                "signal_case_link.linked",
            ),
            "evidence": (
                "TRACE_EVIDENCE_RECEIPT",
                "evidence.record",
                "evidence.recorded",
            ),
        }
        try:
            return {
                name: self.authority.resolve_controller(
                    workspace_id=workspace_id,
                    subject_kind=kind,
                    command=command,
                    event_type=event,
                    recorded_at=at,
                )
                for name, (kind, command, event) in specs.items()
            }
        except AuthorityError as exc:
            raise SignalIntakeError("INTERNAL_ERROR") from exc

    def _create_signal_slice(
        self,
        *,
        source: SourceConnection,
        principal: AcceptedPrincipalContext,
        submission: SignalSubmission,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        provider_origin: str,
    ) -> SignalSubmissionResponse:
        now = _as_utc(self.clock())
        transaction_id = new_transaction_id()
        controllers = self._resolve_controllers(principal.workspace_id, now)
        signal_id = new_signal_id()
        content_id = new_signal_content_id()
        case_id = new_case_id()
        link_id = new_signal_case_link_id()
        evidence_id = new_trace_evidence_receipt_id()
        authority_ids = {name: new_authority_receipt_id() for name in controllers}

        content_payload = submission.content.model_dump(mode="json")
        content_digest = canonical_digest(content_payload)
        content_ref = {
            "uri": f"caseloop-artifact://signal/{signal_id}/content",
            "digest": content_digest,
            "media_type": "application/json",
        }
        privacy = {
            "classification": submission.privacy_classification,
            "redaction_status": "NOT_REQUIRED",
            "raw_content_persisted": True,
            "retention_expires_at": None,
        }
        signal_payload: dict[str, Any] = {
            "schema_version": "1.0",
            "signal_id": signal_id,
            "workspace_id": principal.workspace_id,
            "project_id": submission.project_id,
            "environment_id": submission.environment_id,
            "governed_agent_id": submission.governed_agent_id,
            "source": {
                "source_id": submission.source_id,
                "adapter_kind": "manual",
                "source_event_id": submission.source_event_id,
                "source_event_version": submission.source_event_version,
                "provider_origin": provider_origin,
                "payload_digest": request_fingerprint,
            },
            "signal_kind": submission.signal_kind,
            "reporter": {
                "kind": submission.reporter.kind,
                "ref": submission.reporter.source_subject_ref,
            },
            "occurred_at": _wire_time(submission.occurred_at),
            "observed_at": _wire_time(now),
            "content_ref": content_ref,
            "agent_run_ref_id": None,
            "privacy": privacy,
            "completeness": "UNKNOWN",
            "missing_fields": MISSING_TRACE_FIELDS,
            "untrusted_content": True,
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/signal_digest)",
            "signal_digest": "",
        }
        signal_digest = record_digest(signal_payload, self_digest_field="signal_digest")
        signal_payload["signal_digest"] = signal_digest

        case_payload: dict[str, Any] = {
            "schema_version": "1.0",
            "case_id": case_id,
            "workspace_id": principal.workspace_id,
            "revision": 1,
            "status": "OPEN",
            "title": submission.content.summary,
            "project_id": submission.project_id,
            "environment_id": submission.environment_id,
            "governed_agent_id": submission.governed_agent_id,
            "correlation_status": "NEEDS_CORRELATION",
            "triage_status": "UNTRIAGED",
            "opening_signal_id": signal_id,
            "authority_receipt_id": authority_ids["case"],
            "opened_at": _wire_time(now),
            "updated_at": _wire_time(now),
            "resolved_at": None,
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_digest)",
            "record_digest": "",
        }
        case_digest = record_digest(case_payload, self_digest_field="record_digest")
        case_payload["record_digest"] = case_digest

        link_payload: dict[str, Any] = {
            "schema_version": "1.0",
            "signal_case_link_id": link_id,
            "workspace_id": principal.workspace_id,
            "signal_id": signal_id,
            "case_id": case_id,
            "revision": 1,
            "state": "LINKED",
            "authority_receipt_id": authority_ids["link"],
            "created_at": _wire_time(now),
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/link_digest)",
            "link_digest": "",
        }
        link_digest = record_digest(link_payload, self_digest_field="link_digest")
        link_payload["link_digest"] = link_digest
        evidence_payload = self._no_locator_evidence_payload(
            receipt_id=evidence_id,
            workspace_id=principal.workspace_id,
            source_id=source.source_id,
            signal_id=signal_id,
            signal_digest=signal_digest,
            authority_receipt_id=authority_ids["evidence"],
            collected_at=now,
        )
        evidence_digest = evidence_payload["receipt_digest"]

        projection_rows = [
                SignalContent(
                    signal_content_id=content_id,
                    workspace_id=principal.workspace_id,
                    uri=content_ref["uri"],
                    media_type="application/json",
                    content_digest=content_digest,
                    content_payload=content_payload,
                    privacy_classification=submission.privacy_classification,
                    redaction_status="NOT_REQUIRED",
                    raw_content_persisted=True,
                    retention_expires_at=None,
                    created_at=now,
                ),
                Signal(
                    signal_id=signal_id,
                    workspace_id=principal.workspace_id,
                    project_id=submission.project_id,
                    environment_id=submission.environment_id,
                    governed_agent_id=submission.governed_agent_id,
                    source_id=source.source_id,
                    source_event_id=submission.source_event_id,
                    source_event_version=submission.source_event_version,
                    source_payload_digest=request_fingerprint,
                    adapter_kind="manual",
                    provider_origin=provider_origin,
                    signal_kind=submission.signal_kind,
                    reporter_kind=submission.reporter.kind,
                    reporter_ref=submission.reporter.source_subject_ref,
                    occurred_at=_as_utc(submission.occurred_at),
                    observed_at=now,
                    signal_content_id=content_id,
                    content_ref=content_ref,
                    agent_run_ref_id=None,
                    privacy=privacy,
                    completeness="UNKNOWN",
                    missing_fields=MISSING_TRACE_FIELDS,
                    untrusted_content=True,
                    envelope_payload=signal_payload,
                    signal_digest=signal_digest,
                    authority_receipt_id=authority_ids["signal"],
                    created_at=now,
                ),
                QualityCase(
                    case_id=case_id,
                    workspace_id=principal.workspace_id,
                    state="OPEN",
                    revision=1,
                    title=submission.content.summary,
                    project_id=submission.project_id,
                    environment_id=submission.environment_id,
                    governed_agent_id=submission.governed_agent_id,
                    correlation_status="NEEDS_CORRELATION",
                    triage_status="UNTRIAGED",
                    opening_signal_id=signal_id,
                    snapshot_payload=case_payload,
                    record_digest=case_digest,
                    authority_receipt_id=authority_ids["case"],
                    opened_at=now,
                    updated_at=now,
                    resolved_at=None,
                ),
                SignalCaseLink(
                    signal_case_link_id=link_id,
                    workspace_id=principal.workspace_id,
                    signal_id=signal_id,
                    case_id=case_id,
                    revision=1,
                    state="LINKED",
                    link_payload=link_payload,
                    link_digest=link_digest,
                    authority_receipt_id=authority_ids["link"],
                    created_at=now,
                ),
                TraceEvidenceReceipt(
                    receipt_id=evidence_id,
                    workspace_id=principal.workspace_id,
                    source_id=source.source_id,
                    signal_id=signal_id,
                    signal_digest=signal_digest,
                    collection_mode="NO_LOCATOR",
                    agent_run_ref_id=None,
                    agent_run_ref_digest=None,
                    query=None,
                    requested_fields=MISSING_TRACE_FIELDS,
                    field_results=evidence_payload["field_results"],
                    completeness="UNKNOWN",
                    artifact_ref=None,
                    source_payload_digest=None,
                    collected_at=now,
                    retention_expires_at=None,
                    deep_link=None,
                    failure=evidence_payload["failure"],
                    authority_receipt_id=authority_ids["evidence"],
                    receipt_payload=evidence_payload,
                    receipt_digest=evidence_digest,
                    created_at=now,
                ),
            ]
        # These ORM models deliberately do not expose writable relationships.
        # Therefore SQLAlchemy cannot always infer the composite-FK dependency
        # order on PostgreSQL from one add_all() unit.  Flush each projection in
        # its authoritative dependency order while retaining the caller-owned
        # transaction: content -> signal -> case -> link -> evidence.
        for projection_row in projection_rows:
            self.session.add(projection_row)
            self.session.flush()

        subjects = {
            "signal": (signal_id, None, signal_digest),
            "case": (case_id, 1, case_digest),
            "link": (link_id, 1, link_digest),
            "evidence": (evidence_id, None, evidence_digest),
        }
        event_specs = {
            "signal": (
                "signal",
                signal_id,
                "signal.received",
                {
                    "signal_id": signal_id,
                    "signal_digest": signal_digest,
                    "source_id": source.source_id,
                    "source_event_id": submission.source_event_id,
                },
            ),
            "case": (
                "quality_case",
                case_id,
                "case.opened",
                {"case_id": case_id, "opening_signal_id": signal_id},
            ),
            "link": (
                "signal",
                signal_id,
                "signal_case_link.linked",
                {"signal_id": signal_id, "case_id": case_id, "link_digest": link_digest},
            ),
            "evidence": (
                "evidence_receipt",
                evidence_id,
                "evidence.recorded",
                {
                    "receipt_id": evidence_id,
                    "evidence_digest": evidence_digest,
                    "completeness": "UNKNOWN",
                },
            ),
        }
        event_rows: dict[str, Any] = {}
        for index, name in enumerate(("signal", "case", "link", "evidence")):
            aggregate_type, aggregate_id, event_type, business = event_specs[name]
            subject_id, subject_revision, subject_digest = subjects[name]
            if name == "signal":
                causation_id = request_id
            elif name == "case":
                causation_id = event_rows["signal"].event_id
            elif name == "link":
                causation_id = event_rows["case"].event_id
            else:
                causation_id = event_rows["signal"].event_id
            controller = controllers[name]
            at = now + timedelta(microseconds=index)
            payload = {
                **business,
                "subject_kind": controller.subject_kind,
                "subject_id": subject_id,
                "subject_revision": subject_revision,
                "subject_digest": subject_digest,
                "authority_receipt_id": authority_ids[name],
            }
            try:
                event = self.events.append_event(
                    workspace_id=principal.workspace_id,
                    aggregate_type=aggregate_type,
                    aggregate_id=aggregate_id,
                    event_type=event_type,
                    payload=payload,
                    causation_id=causation_id,
                    correlation_id=case_id,
                    actor_principal=controller.controller_principal,
                    transaction_id=transaction_id,
                    occurred_at=at,
                )
                audit = self.audit.record(
                    workspace_id=principal.workspace_id,
                    actor_principal=controller.controller_principal,
                    action=f"controller.{event_type}",
                    target=subject_id,
                    params={"command": controller.command},
                    transaction_id=transaction_id,
                    trace_id=request_id,
                    evidence_refs={
                        "subject_kind": controller.subject_kind,
                        "subject_id": subject_id,
                        "subject_revision": subject_revision,
                        "subject_digest": subject_digest,
                        "event_id": event.event_id,
                    },
                    occurred_at=at,
                )
                self.authority.record_receipt(
                    resolved=controller,
                    authority_receipt_id=authority_ids[name],
                    workspace_id=principal.workspace_id,
                    subject_id=subject_id,
                    subject_revision=subject_revision,
                    subject_digest=subject_digest,
                    event_id=event.event_id,
                    transaction_id=transaction_id,
                    audit_ref=audit.audit_ref,
                    recorded_at=at,
                )
            except (V4EventStoreError, AuthorityError) as exc:
                raise SignalIntakeError("INTERNAL_ERROR") from exc
            except V4AuditUnavailable as exc:
                raise SignalIntakeError("AUDIT_UNAVAILABLE") from exc
            event_rows[name] = event

        try:
            command_audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action="signals.submit",
                target=signal_id,
                params={
                    "request_fingerprint": request_fingerprint,
                    "source_id": source.source_id,
                    "source_event_id": submission.source_event_id,
                },
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "signal_id": signal_id,
                    "case_id": case_id,
                    "evidence_receipt_id": evidence_id,
                },
                occurred_at=now + timedelta(microseconds=4),
            )
        except V4AuditUnavailable as exc:
            raise SignalIntakeError("AUDIT_UNAVAILABLE") from exc
        return self._persist_response(
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            audit_ref=command_audit.audit_ref,
            signal=signal_id,
            signal_digest=signal_digest,
            source_event_id=submission.source_event_id,
            duplicate_of=None,
            case_id=case_id,
            disposition="NEW",
            evidence_id=evidence_id,
            evidence_digest=evidence_digest,
            completed_at=now + timedelta(microseconds=4),
        )

    def _complete_duplicate_source_event(
        self,
        *,
        existing: Signal,
        principal: AcceptedPrincipalContext,
        submission: SignalSubmission,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
    ) -> SignalSubmissionResponse:
        links = list(
            self.session.scalars(
                select(SignalCaseLink).where(
                    SignalCaseLink.workspace_id == principal.workspace_id,
                    SignalCaseLink.signal_id == existing.signal_id,
                )
            ).all()
        )
        evidence_rows = list(
            self.session.scalars(
                select(TraceEvidenceReceipt).where(
                    TraceEvidenceReceipt.workspace_id == principal.workspace_id,
                    TraceEvidenceReceipt.signal_id == existing.signal_id,
                    TraceEvidenceReceipt.collection_mode == "NO_LOCATOR",
                )
            ).all()
        )
        if len(links) != 1 or len(evidence_rows) != 1:
            raise SignalIntakeError("INTERNAL_ERROR")
        link = links[0]
        evidence = evidence_rows[0]
        quality_case = self.session.get(QualityCase, link.case_id)
        if quality_case is None or quality_case.workspace_id != principal.workspace_id:
            raise SignalIntakeError("INTERNAL_ERROR")
        try:
            self._validate_duplicate_slice(
                signal=existing,
                quality_case=quality_case,
                link=link,
                evidence=evidence,
            )
        except (AuthorityError, V4IntegrityError, ValueError, TypeError) as exc:
            raise SignalIntakeError("INTERNAL_ERROR") from exc
        now = _as_utc(self.clock())
        transaction_id = new_transaction_id()
        try:
            command_audit = self.audit.record(
                workspace_id=principal.workspace_id,
                actor_principal=principal.principal_id,
                action="signals.submit",
                target=existing.signal_id,
                params={
                    "request_fingerprint": request_fingerprint,
                    "source_id": submission.source_id,
                    "source_event_id": submission.source_event_id,
                },
                result="duplicate",
                transaction_id=transaction_id,
                trace_id=request_id,
                evidence_refs={
                    "signal_id": existing.signal_id,
                    "case_id": quality_case.case_id,
                    "evidence_receipt_id": evidence.receipt_id,
                },
                occurred_at=now,
            )
        except V4AuditUnavailable as exc:
            raise SignalIntakeError("AUDIT_UNAVAILABLE") from exc
        return self._persist_response(
            principal=principal,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            request_id=request_id,
            audit_ref=command_audit.audit_ref,
            signal=existing.signal_id,
            signal_digest=existing.signal_digest,
            source_event_id=existing.source_event_id,
            duplicate_of=existing.signal_id,
            case_id=quality_case.case_id,
            disposition="DUPLICATE",
            evidence_id=evidence.receipt_id,
            evidence_digest=evidence.receipt_digest,
            completed_at=now,
        )

    def _validate_duplicate_slice(
        self,
        *,
        signal: Signal,
        quality_case: QualityCase,
        link: SignalCaseLink,
        evidence: TraceEvidenceReceipt,
    ) -> None:
        content = self.session.get(SignalContent, signal.signal_content_id)
        if (
            content is None
            or content.workspace_id != signal.workspace_id
            or canonical_digest(content.content_payload) != content.content_digest
            or signal.content_ref
            != {
                "uri": content.uri,
                "digest": content.content_digest,
                "media_type": content.media_type,
            }
        ):
            raise V4IntegrityError("v4.signal_content_binding_mismatch")

        signal_payload = signal.envelope_payload
        assert_record_digest(signal_payload, self_digest_field="signal_digest")
        expected_signal = {
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
            "reporter": {"kind": signal.reporter_kind, "ref": signal.reporter_ref},
            "occurred_at": _wire_time(signal.occurred_at),
            "observed_at": _wire_time(signal.observed_at),
            "content_ref": signal.content_ref,
            "agent_run_ref_id": signal.agent_run_ref_id,
            "privacy": signal.privacy,
            "completeness": signal.completeness,
            "missing_fields": signal.missing_fields,
            "untrusted_content": signal.untrusted_content,
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/signal_digest)",
            "signal_digest": signal.signal_digest,
        }
        if signal_payload != expected_signal:
            raise V4IntegrityError("v4.signal_projection_binding_mismatch")

        case_payload = quality_case.snapshot_payload
        assert_record_digest(case_payload, self_digest_field="record_digest")
        expected_case = {
            "schema_version": "1.0",
            "case_id": quality_case.case_id,
            "workspace_id": quality_case.workspace_id,
            "status": quality_case.state,
            "revision": quality_case.revision,
            "title": quality_case.title,
            "project_id": quality_case.project_id,
            "environment_id": quality_case.environment_id,
            "governed_agent_id": quality_case.governed_agent_id,
            "correlation_status": quality_case.correlation_status,
            "triage_status": quality_case.triage_status,
            "opening_signal_id": quality_case.opening_signal_id,
            "authority_receipt_id": quality_case.authority_receipt_id,
            "opened_at": _wire_time(quality_case.opened_at),
            "updated_at": _wire_time(quality_case.updated_at),
            "resolved_at": (
                _wire_time(quality_case.resolved_at)
                if quality_case.resolved_at is not None
                else None
            ),
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/record_digest)",
            "record_digest": quality_case.record_digest,
        }
        if case_payload != expected_case:
            raise V4IntegrityError("v4.case_projection_binding_mismatch")

        link_payload = link.link_payload
        assert_record_digest(link_payload, self_digest_field="link_digest")
        expected_link = {
            "schema_version": "1.0",
            "signal_case_link_id": link.signal_case_link_id,
            "workspace_id": link.workspace_id,
            "signal_id": link.signal_id,
            "case_id": link.case_id,
            "revision": link.revision,
            "state": link.state,
            "authority_receipt_id": link.authority_receipt_id,
            "created_at": _wire_time(link.created_at),
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/link_digest)",
            "link_digest": link.link_digest,
        }
        if link_payload != expected_link:
            raise V4IntegrityError("v4.signal_case_link_binding_mismatch")

        evidence_payload = evidence.receipt_payload
        TraceEvidenceReceiptWire.model_validate(evidence_payload)
        assert_record_digest(evidence_payload, self_digest_field="receipt_digest")
        expected_evidence = {
            "schema_version": "1.0",
            "receipt_id": evidence.receipt_id,
            "workspace_id": evidence.workspace_id,
            "source_id": evidence.source_id,
            "signal_id": evidence.signal_id,
            "signal_digest": evidence.signal_digest,
            "collection_mode": evidence.collection_mode,
            "agent_run_ref_id": evidence.agent_run_ref_id,
            "agent_run_ref_digest": evidence.agent_run_ref_digest,
            "query": evidence.query,
            "requested_fields": evidence.requested_fields,
            "field_results": evidence.field_results,
            "completeness": evidence.completeness,
            "artifact_ref": evidence.artifact_ref,
            "source_payload_digest": evidence.source_payload_digest,
            "collected_at": _wire_time(evidence.collected_at),
            "retention_expires_at": (
                _wire_time(evidence.retention_expires_at)
                if evidence.retention_expires_at is not None
                else None
            ),
            "deep_link": evidence.deep_link,
            "failure": evidence.failure,
            "authority_receipt_id": evidence.authority_receipt_id,
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/receipt_digest)",
            "receipt_digest": evidence.receipt_digest,
        }
        if evidence_payload != expected_evidence:
            raise V4IntegrityError("v4.evidence_projection_binding_mismatch")
        if (
            link.signal_id != signal.signal_id
            or quality_case.opening_signal_id != signal.signal_id
            or link.case_id != quality_case.case_id
            or evidence.signal_id != signal.signal_id
            or evidence.signal_digest != signal.signal_digest
            or signal.agent_run_ref_id is not None
        ):
            raise V4IntegrityError("v4.signal_slice_relation_mismatch")

        for receipt_id, subject_kind, subject_id, revision, digest in (
            (
                signal.authority_receipt_id,
                "SIGNAL_RECORD",
                signal.signal_id,
                None,
                signal.signal_digest,
            ),
            (
                quality_case.authority_receipt_id,
                "QUALITY_CASE",
                quality_case.case_id,
                quality_case.revision,
                quality_case.record_digest,
            ),
            (
                link.authority_receipt_id,
                "SIGNAL_CASE_LINK",
                link.signal_case_link_id,
                link.revision,
                link.link_digest,
            ),
            (
                evidence.authority_receipt_id,
                "TRACE_EVIDENCE_RECEIPT",
                evidence.receipt_id,
                None,
                evidence.receipt_digest,
            ),
        ):
            self.authority.validate_receipt_binding(
                authority_receipt_id=receipt_id,
                workspace_id=signal.workspace_id,
                subject_kind=subject_kind,
                subject_id=subject_id,
                subject_revision=revision,
                subject_digest=digest,
            )

    def _persist_response(
        self,
        *,
        principal: AcceptedPrincipalContext,
        idempotency_key: str,
        request_fingerprint: str,
        request_id: str,
        audit_ref: str,
        signal: str,
        signal_digest: str,
        source_event_id: str,
        duplicate_of: str | None,
        case_id: str,
        disposition: str,
        evidence_id: str,
        evidence_digest: str,
        completed_at: datetime,
    ) -> SignalSubmissionResponse:
        core: dict[str, Any] = {
            "schema_version": "1.0",
            "workspace_id": principal.workspace_id,
            "request_id": request_id,
            "audit_ref": audit_ref,
            "signal": {
                "signal_id": signal,
                "signal_digest": signal_digest,
                "source_event_id": source_event_id,
                "duplicate_of_signal_id": duplicate_of,
            },
            "case": {
                "case_id": case_id,
                "status": "OPEN",
                "revision": 1,
                "disposition": disposition,
                "correlation_status": "NEEDS_CORRELATION",
                "triage_status": "UNTRIAGED",
            },
            "evidence": {
                "status": "UNKNOWN",
                "receipt_id": evidence_id,
                "receipt_digest": evidence_digest,
                "agent_run_ref_id": None,
                "missing_fields": MISSING_TRACE_FIELDS,
            },
            "missing_fields": MISSING_TRACE_FIELDS,
            "next_action": {
                "code": "CORRELATE_TRACE",
                "command": None,
                "href": None,
            },
        }
        response_digest = canonical_digest(core)
        receipt_id = new_idempotency_receipt_id()
        receipt: dict[str, Any] = {
            "schema_version": "1.0",
            "workspace_id": principal.workspace_id,
            "principal_id": principal.principal_id,
            "intent": "signals.submit",
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "resource": {"kind": "signal", "id": signal},
            "operation_id": None,
            "request_id": request_id,
            "audit_ref": audit_ref,
            "status": "COMPLETED",
            "response_digest": response_digest,
            "created_at": _wire_time(completed_at),
            "idempotency_receipt_id": receipt_id,
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/receipt_digest)",
            "receipt_digest": "",
        }
        receipt_digest = record_digest(receipt, self_digest_field="receipt_digest")
        receipt["receipt_digest"] = receipt_digest
        try:
            self.idempotency.store_completed(
                workspace_id=principal.workspace_id,
                principal_id=principal.principal_id,
                intent="signals.submit",
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                resource_kind="signal",
                resource_id=signal,
                request_id=request_id,
                audit_ref=audit_ref,
                response_payload=core,
                response_digest=response_digest,
                receipt_payload=receipt,
                receipt_digest=receipt_digest,
                idempotency_receipt_id=receipt_id,
                completed_at=completed_at,
            )
        except PublicIdempotencyError as exc:
            raise SignalIntakeError(exc.code) from exc
        return SignalSubmissionResponse.model_validate(
            {**core, "idempotency": {"receipt": receipt, "replayed": False}}
        )

    @staticmethod
    def _no_locator_evidence_payload(
        *,
        receipt_id: str,
        workspace_id: str,
        source_id: str,
        signal_id: str,
        signal_digest: str,
        authority_receipt_id: str,
        collected_at: datetime,
    ) -> dict[str, Any]:
        field_results = [
            {
                "name": name,
                "status": "MISSING",
                "reason_digest": canonical_digest(
                    {"code": "NO_TRACE_LOCATOR", "field": name}
                ),
            }
            for name in MISSING_TRACE_FIELDS
        ]
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "receipt_id": receipt_id,
            "workspace_id": workspace_id,
            "source_id": source_id,
            "signal_id": signal_id,
            "signal_digest": signal_digest,
            "collection_mode": "NO_LOCATOR",
            "agent_run_ref_id": None,
            "agent_run_ref_digest": None,
            "query": None,
            "requested_fields": MISSING_TRACE_FIELDS,
            "field_results": field_results,
            "completeness": "UNKNOWN",
            "artifact_ref": None,
            "source_payload_digest": None,
            "collected_at": _wire_time(collected_at),
            "retention_expires_at": None,
            "deep_link": None,
            "failure": {
                "code": "NO_TRACE_LOCATOR",
                "retryable": False,
                "message_digest": canonical_digest({"code": "NO_TRACE_LOCATOR"}),
            },
            "authority_receipt_id": authority_receipt_id,
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/receipt_digest)",
            "receipt_digest": "",
        }
        payload["receipt_digest"] = record_digest(
            payload, self_digest_field="receipt_digest"
        )
        return payload


__all__ = [
    "BASE_RESPONSE_DIGEST_RULE",
    "MISSING_TRACE_FIELDS",
    "SignalIntakeError",
    "SignalIntakeService",
]
