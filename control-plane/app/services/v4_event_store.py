"""Independent v4 event/outbox writer; never uses v3 event-name routing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Audit, Event, Outbox
from app.models.v4_tables import (
    PublicCommandIdempotency,
    QualityCase,
    Signal,
    SignalCaseLink,
    TraceEvidenceReceipt,
)
from app.utils.ids import new_event_id, new_outbox_id
from app.utils.v4_integrity import V4IntegrityError, canonical_digest
from app.services.v4_audit import V4AuditIntegrityError, validate_v4_audit_row


V4_DOMAIN_EVENT_CHANNEL = "v4.domain.events"


class V4EventStoreError(ValueError):
    """Stable validation failure before a v4 event is persisted."""


class V4EventIntegrityError(ValueError):
    """A persisted v4 event or its transactional outbox row is not exact."""


@dataclass(frozen=True)
class _Route:
    owner: str
    subject_kind: str
    subject_revisioned: bool
    required: frozenset[str]
    subject_id_field: str
    subject_digest_field: str | None
    aggregate_id_field: str


_CONTROLLER_FIELDS = frozenset(
    {
        "subject_kind",
        "subject_id",
        "subject_revision",
        "subject_digest",
        "authority_receipt_id",
    }
)

_ROUTES: dict[tuple[str, str], _Route] = {
    ("signal", "signal.received"): _Route(
        owner="signal-controller",
        subject_kind="SIGNAL_RECORD",
        subject_revisioned=False,
        required=frozenset(
            {"signal_id", "signal_digest", "source_id", "source_event_id"}
        ),
        subject_id_field="signal_id",
        subject_digest_field="signal_digest",
        aggregate_id_field="signal_id",
    ),
    ("quality_case", "case.opened"): _Route(
        owner="case-controller",
        subject_kind="QUALITY_CASE",
        subject_revisioned=True,
        required=frozenset({"case_id", "opening_signal_id"}),
        subject_id_field="case_id",
        subject_digest_field=None,
        aggregate_id_field="case_id",
    ),
    ("signal", "signal_case_link.linked"): _Route(
        owner="signal-controller",
        subject_kind="SIGNAL_CASE_LINK",
        subject_revisioned=True,
        required=frozenset({"signal_id", "case_id", "link_digest"}),
        subject_id_field="subject_id",
        subject_digest_field="link_digest",
        aggregate_id_field="signal_id",
    ),
    ("evidence_receipt", "evidence.recorded"): _Route(
        owner="evidence-controller",
        subject_kind="TRACE_EVIDENCE_RECEIPT",
        subject_revisioned=False,
        required=frozenset({"receipt_id", "evidence_digest", "completeness"}),
        subject_id_field="receipt_id",
        subject_digest_field="evidence_digest",
        aggregate_id_field="receipt_id",
    ),
}

_STAGE1_EVENT_TYPES = (
    "signal.received",
    "case.opened",
    "signal_case_link.linked",
    "evidence.recorded",
)
_STAGE1_SEQ = {
    "signal.received": 1,
    "case.opened": 1,
    "signal_case_link.linked": 2,
    "evidence.recorded": 1,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _validate_route_payload(route: _Route, payload: dict[str, Any]) -> str:
    expected_fields = route.required | _CONTROLLER_FIELDS
    if set(payload) != expected_fields:
        raise V4EventStoreError("v4.event_payload_fields_mismatch")
    if payload.get("subject_kind") != route.subject_kind:
        raise V4EventStoreError("v4.event_subject_kind_mismatch")
    expected_subject_id = payload.get(route.subject_id_field)
    if payload.get("subject_id") != expected_subject_id:
        raise V4EventStoreError("v4.event_subject_id_mismatch")
    revision = payload.get("subject_revision")
    if route.subject_revisioned:
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise V4EventStoreError("v4.event_subject_revision_invalid")
    elif revision is not None:
        raise V4EventStoreError("v4.event_singleton_revision_must_be_null")
    if route.subject_digest_field is not None and payload.get(
        "subject_digest"
    ) != payload.get(route.subject_digest_field):
        raise V4EventStoreError("v4.event_subject_digest_mismatch")
    try:
        return canonical_digest(payload)
    except V4IntegrityError as exc:
        raise V4EventStoreError("v4.event_payload_integrity_invalid") from exc


def validate_v4_event_row(
    row: Event | None,
    *,
    workspace_id: str,
    event_type: str,
    transaction_id: str,
    actor_principal: str,
    subject_kind: str,
    subject_id: str,
    subject_revision: int | None,
    subject_digest: str,
    authority_receipt_id: str,
) -> Event:
    """Recompute a persisted event digest and verify its exact route binding."""

    if row is None:
        raise V4EventIntegrityError("v4.event_missing")
    route = _ROUTES.get((row.aggregate_type, row.event_type))
    if route is None:
        raise V4EventIntegrityError("v4.event_route_mismatch")
    try:
        payload_digest = _validate_route_payload(route, row.payload or {})
    except V4EventStoreError as exc:
        raise V4EventIntegrityError("v4.event_payload_binding_mismatch") from exc
    payload = row.payload or {}
    if (
        row.contract_version != "v4"
        or row.event_version != "1.0"
        or row.workspace_id != workspace_id
        or row.event_type != event_type
        or row.aggregate_id != payload.get(route.aggregate_id_field)
        or row.transaction_id != transaction_id
        or row.actor != route.owner
        or row.actor_principal != actor_principal
        or row.trace_id is not None
        or not isinstance(row.seq, int)
        or isinstance(row.seq, bool)
        or row.seq < 1
        or not isinstance(row.causation_id, str)
        or not row.causation_id
        or not isinstance(row.correlation_id, str)
        or not row.correlation_id
        or _as_utc(row.created_at) != _as_utc(row.occurred_at)
        or row.payload_digest != payload_digest
        or payload.get("subject_kind") != subject_kind
        or payload.get("subject_id") != subject_id
        or payload.get("subject_revision") != subject_revision
        or payload.get("subject_digest") != subject_digest
        or payload.get("authority_receipt_id") != authority_receipt_id
    ):
        raise V4EventIntegrityError("v4.event_binding_mismatch")
    return row


def _exact_one(rows: list[Any], code: str) -> Any:
    if len(rows) != 1:
        raise V4EventIntegrityError(code)
    return rows[0]


def _stage1_subject_graph(
    session: Session, event: Event
) -> tuple[Signal, QualityCase, SignalCaseLink, TraceEvidenceReceipt]:
    """Resolve the immutable Stage 1A graph without trusting event correlation."""

    payload = event.payload or {}
    if event.event_type == "signal.received":
        signal = session.get(Signal, payload.get("signal_id"))
        if signal is None:
            raise V4EventIntegrityError("v4.stage1_signal_missing")
        cases = list(
            session.scalars(
                select(QualityCase).where(
                    QualityCase.workspace_id == event.workspace_id,
                    QualityCase.opening_signal_id == signal.signal_id,
                )
            ).all()
        )
        # The opening case and link are preallocated in the same intake slice.
        # Their record timestamps bind the initial graph while remaining stable
        # if the signal is linked to additional cases later.
        cases = [
            row
            for row in cases
            if _as_utc(row.opened_at) == _as_utc(signal.created_at)
        ]
        quality_case = _exact_one(cases, "v4.stage1_case_cardinality_mismatch")
    elif event.event_type == "case.opened":
        quality_case = session.get(QualityCase, payload.get("case_id"))
        if quality_case is None:
            raise V4EventIntegrityError("v4.stage1_case_missing")
        signal = session.get(Signal, quality_case.opening_signal_id)
    elif event.event_type == "signal_case_link.linked":
        link = session.get(SignalCaseLink, payload.get("subject_id"))
        if link is None:
            raise V4EventIntegrityError("v4.stage1_link_missing")
        signal = session.get(Signal, link.signal_id)
        quality_case = session.get(QualityCase, link.case_id)
    elif event.event_type == "evidence.recorded":
        evidence = session.get(TraceEvidenceReceipt, payload.get("receipt_id"))
        if evidence is None:
            raise V4EventIntegrityError("v4.stage1_evidence_missing")
        signal = session.get(Signal, evidence.signal_id)
        links = list(
            session.scalars(
                select(SignalCaseLink).where(
                    SignalCaseLink.workspace_id == event.workspace_id,
                    SignalCaseLink.signal_id == evidence.signal_id,
                )
            ).all()
        )
        links = [
            row
            for row in links
            if _as_utc(row.created_at) == _as_utc(evidence.collected_at)
        ]
        link = _exact_one(links, "v4.stage1_link_cardinality_mismatch")
        quality_case = session.get(QualityCase, link.case_id)
    else:
        raise V4EventIntegrityError("v4.stage1_event_type_mismatch")

    if signal is None or quality_case is None:
        raise V4EventIntegrityError("v4.stage1_subject_graph_missing")
    if event.event_type != "signal_case_link.linked":
        links = list(
            session.scalars(
                select(SignalCaseLink).where(
                    SignalCaseLink.workspace_id == event.workspace_id,
                    SignalCaseLink.signal_id == signal.signal_id,
                    SignalCaseLink.case_id == quality_case.case_id,
                )
            ).all()
        )
        link = _exact_one(links, "v4.stage1_link_cardinality_mismatch")
    if event.event_type != "evidence.recorded":
        evidence_rows = list(
            session.scalars(
                select(TraceEvidenceReceipt).where(
                    TraceEvidenceReceipt.workspace_id == event.workspace_id,
                    TraceEvidenceReceipt.signal_id == signal.signal_id,
                    TraceEvidenceReceipt.collected_at == signal.created_at,
                )
            ).all()
        )
        evidence = _exact_one(
            evidence_rows, "v4.stage1_evidence_cardinality_mismatch"
        )
    if (
        signal.workspace_id != event.workspace_id
        or quality_case.workspace_id != event.workspace_id
        or link.workspace_id != event.workspace_id
        or evidence.workspace_id != event.workspace_id
        or quality_case.opening_signal_id != signal.signal_id
        or link.signal_id != signal.signal_id
        or link.case_id != quality_case.case_id
        or evidence.signal_id != signal.signal_id
    ):
        raise V4EventIntegrityError("v4.stage1_subject_graph_mismatch")
    return signal, quality_case, link, evidence


def _stage1_payloads(
    signal: Signal,
    quality_case: QualityCase,
    link: SignalCaseLink,
    evidence: TraceEvidenceReceipt,
) -> dict[str, dict[str, Any]]:
    return {
        "signal.received": {
            "signal_id": signal.signal_id,
            "signal_digest": signal.signal_digest,
            "source_id": signal.source_id,
            "source_event_id": signal.source_event_id,
            "subject_kind": "SIGNAL_RECORD",
            "subject_id": signal.signal_id,
            "subject_revision": None,
            "subject_digest": signal.signal_digest,
            "authority_receipt_id": signal.authority_receipt_id,
        },
        "case.opened": {
            "case_id": quality_case.case_id,
            "opening_signal_id": quality_case.opening_signal_id,
            "subject_kind": "QUALITY_CASE",
            "subject_id": quality_case.case_id,
            "subject_revision": quality_case.revision,
            "subject_digest": quality_case.record_digest,
            "authority_receipt_id": quality_case.authority_receipt_id,
        },
        "signal_case_link.linked": {
            "signal_id": link.signal_id,
            "case_id": link.case_id,
            "link_digest": link.link_digest,
            "subject_kind": "SIGNAL_CASE_LINK",
            "subject_id": link.signal_case_link_id,
            "subject_revision": link.revision,
            "subject_digest": link.link_digest,
            "authority_receipt_id": link.authority_receipt_id,
        },
        "evidence.recorded": {
            "receipt_id": evidence.receipt_id,
            "evidence_digest": evidence.receipt_digest,
            "completeness": evidence.completeness,
            "subject_kind": "TRACE_EVIDENCE_RECEIPT",
            "subject_id": evidence.receipt_id,
            "subject_revision": None,
            "subject_digest": evidence.receipt_digest,
            "authority_receipt_id": evidence.authority_receipt_id,
        },
    }


def validate_stage1_event_semantics(
    session: Session,
    *,
    event: Event,
    controller_trace_id: str,
    require_complete_graph: bool,
) -> Event:
    """Verify Stage 1A business fields and the exact four-event causal graph."""

    signal, quality_case, link, evidence = _stage1_subject_graph(session, event)
    payloads = _stage1_payloads(signal, quality_case, link, evidence)
    expected_payload = payloads.get(event.event_type)
    if (
        expected_payload is None
        or event.payload != expected_payload
        or event.correlation_id != quality_case.case_id
        or event.seq != _STAGE1_SEQ[event.event_type]
    ):
        raise V4EventIntegrityError("v4.stage1_event_semantics_mismatch")

    required_types = _STAGE1_EVENT_TYPES if require_complete_graph else (
        _STAGE1_EVENT_TYPES[: _STAGE1_EVENT_TYPES.index(event.event_type) + 1]
        if event.event_type != "evidence.recorded"
        else _STAGE1_EVENT_TYPES
    )
    rows = list(
        session.scalars(
            select(Event).where(
                Event.contract_version == "v4",
                Event.workspace_id == event.workspace_id,
                Event.transaction_id == event.transaction_id,
                Event.event_type.in_(required_types),
            )
        ).all()
    )
    by_type: dict[str, Event] = {}
    for event_type in required_types:
        candidates = [row for row in rows if row.event_type == event_type]
        candidate = _exact_one(
            candidates, "v4.stage1_event_cardinality_mismatch"
        )
        if (
            candidate.payload != payloads[event_type]
            or candidate.correlation_id != quality_case.case_id
            or candidate.seq != _STAGE1_SEQ[event_type]
        ):
            raise V4EventIntegrityError("v4.stage1_event_graph_mismatch")
        by_type[event_type] = candidate

    signal_event = by_type["signal.received"]
    if signal_event.causation_id != controller_trace_id:
        raise V4EventIntegrityError("v4.stage1_public_command_causation_mismatch")
    expected_predecessors = {
        "case.opened": "signal.received",
        "signal_case_link.linked": "case.opened",
        "evidence.recorded": "signal.received",
    }
    for child_type, parent_type in expected_predecessors.items():
        if child_type not in by_type:
            continue
        child = by_type[child_type]
        parent = by_type[parent_type]
        if (
            child.causation_id != parent.event_id
            or _as_utc(child.occurred_at) <= _as_utc(parent.occurred_at)
        ):
            raise V4EventIntegrityError("v4.stage1_event_causation_mismatch")

    if require_complete_graph:
        if not (
            _as_utc(by_type["signal.received"].occurred_at)
            < _as_utc(by_type["case.opened"].occurred_at)
            < _as_utc(by_type["signal_case_link.linked"].occurred_at)
            < _as_utc(by_type["evidence.recorded"].occurred_at)
        ):
            raise V4EventIntegrityError("v4.stage1_event_order_mismatch")
        commands = list(
            session.scalars(
                select(PublicCommandIdempotency).where(
                    PublicCommandIdempotency.workspace_id == event.workspace_id,
                    PublicCommandIdempotency.request_id == signal_event.causation_id,
                    PublicCommandIdempotency.state == "COMPLETED",
                    PublicCommandIdempotency.intent == "signals.submit",
                )
            ).all()
        )
        command = _exact_one(
            commands, "v4.stage1_public_command_cardinality_mismatch"
        )
        # Reuse the public-command verifier so the row, response and immutable
        # idempotency receipt must form one exact terminal record.
        from app.services.public_idempotency import (  # local avoids a module cycle
            PublicIdempotencyError,
            PublicIdempotencyService,
        )

        try:
            response = PublicIdempotencyService(session).replay_signal_response(
                command
            )
        except PublicIdempotencyError as exc:
            raise V4EventIntegrityError(
                "v4.stage1_public_command_integrity_invalid"
            ) from exc
        if (
            command.resource_kind != "signal"
            or command.resource_id != signal.signal_id
            or response.case.disposition != "NEW"
            or response.signal.signal_id != signal.signal_id
        ):
            raise V4EventIntegrityError("v4.stage1_public_command_binding_mismatch")
        command_audit = (
            session.get(Audit, command.audit_ref.removeprefix("audit://"))
            if isinstance(command.audit_ref, str)
            and command.audit_ref.startswith("audit://aud_")
            else None
        )
        try:
            exact_command_audit = validate_v4_audit_row(
                command_audit,
                workspace_id=event.workspace_id,
                actor_principal=command.principal_id,
                action="signals.submit",
                target=signal.signal_id,
                params={
                    "request_fingerprint": command.request_fingerprint,
                    "source_id": signal.source_id,
                    "source_event_id": signal.source_event_id,
                },
                result="success",
                error_code=None,
                transaction_id=event.transaction_id,
                evidence_refs={
                    "signal_id": signal.signal_id,
                    "case_id": quality_case.case_id,
                    "evidence_receipt_id": evidence.receipt_id,
                },
            )
        except V4AuditIntegrityError as exc:
            raise V4EventIntegrityError(
                "v4.stage1_public_command_audit_invalid"
            ) from exc
        if (
            command.audit_ref != f"audit://{exact_command_audit.audit_id}"
            or exact_command_audit.trace_id != signal_event.causation_id
            or command.completed_at is None
            or _as_utc(exact_command_audit.ts) != _as_utc(command.completed_at)
        ):
            raise V4EventIntegrityError(
                "v4.stage1_public_command_audit_binding_mismatch"
            )
    return event


def v4_outbox_envelope(event: Event) -> dict[str, Any]:
    """Return the exact immutable outbox envelope derived from one v4 event."""

    return {
        "contract_version": "v4",
        "event_id": event.event_id,
        "event_type": event.event_type,
        "event_version": "1.0",
        "aggregate_type": event.aggregate_type,
        "aggregate_id": event.aggregate_id,
        "occurred_at": _wire_time(event.occurred_at),
        "causation_id": event.causation_id,
        "correlation_id": event.correlation_id,
        "actor_principal": event.actor_principal,
        "transaction_id": event.transaction_id,
        "payload": event.payload,
        "payload_digest": event.payload_digest,
    }


def validate_v4_outbox_row(row: Outbox | None, *, event: Event) -> Outbox:
    """Verify immutable event/outbox bindings without inspecting delivery state."""

    if row is None:
        raise V4EventIntegrityError("v4.outbox_missing")
    envelope = v4_outbox_envelope(event)
    envelope_digest = canonical_digest(envelope)
    if (
        row.contract_version != "v4"
        or row.source_event_id != event.event_id
        or row.source_event_seq != event.seq
        or row.channel != V4_DOMAIN_EVENT_CHANNEL
        or row.workspace_id != event.workspace_id
        or row.aggregate_type != event.aggregate_type
        or row.aggregate_id != event.aggregate_id
        or row.event_type != event.event_type
        or row.event_version != event.event_version
        or row.transaction_id != event.transaction_id
        or row.actor_principal != event.actor_principal
        or row.payload != envelope
        or row.payload_digest != envelope_digest
        or _as_utc(row.created_at) != _as_utc(event.occurred_at)
    ):
        raise V4EventIntegrityError("v4.outbox_binding_mismatch")
    return row


class V4EventStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def append_event(
        self,
        *,
        workspace_id: str,
        aggregate_type: str,
        aggregate_id: str,
        event_type: str,
        payload: dict[str, Any],
        causation_id: str,
        correlation_id: str,
        actor_principal: str,
        transaction_id: str,
        occurred_at: datetime | None = None,
    ) -> Event:
        route = _ROUTES.get((aggregate_type, event_type))
        if route is None:
            raise V4EventStoreError("v4.event_route_not_allowed")
        if aggregate_id != payload.get(route.aggregate_id_field):
            raise V4EventStoreError("v4.event_aggregate_id_mismatch")
        if not all(
            isinstance(value, str) and value
            for value in (
                workspace_id,
                aggregate_id,
                causation_id,
                correlation_id,
                actor_principal,
                transaction_id,
            )
        ):
            raise V4EventStoreError("v4.event_context_invalid")
        payload_digest = _validate_route_payload(route, payload)

        at = occurred_at or _utc_now()
        seq = int(
            self.session.scalar(
                select(func.coalesce(func.max(Event.seq), 0)).where(
                    Event.contract_version == "v4",
                    Event.workspace_id == workspace_id,
                    Event.aggregate_type == aggregate_type,
                    Event.aggregate_id == aggregate_id,
                )
            )
            or 0
        ) + 1
        event = Event(
            event_id=new_event_id(),
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            seq=seq,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
            actor=route.owner,
            trace_id=None,
            occurred_at=at,
            created_at=at,
            contract_version="v4",
            workspace_id=workspace_id,
            event_version="1.0",
            transaction_id=transaction_id,
            actor_principal=actor_principal,
            payload_digest=payload_digest,
        )
        envelope = v4_outbox_envelope(event)
        outbox = Outbox(
            outbox_id=new_outbox_id(),
            aggregate_id=aggregate_id,
            source_event_id=event.event_id,
            source_event_seq=seq,
            channel=V4_DOMAIN_EVENT_CHANNEL,
            event_type=event_type,
            payload=envelope,
            payload_digest=canonical_digest(envelope),
            status="PENDING",
            attempts=0,
            created_at=at,
            contract_version="v4",
            workspace_id=workspace_id,
            aggregate_type=aggregate_type,
            event_version="1.0",
            transaction_id=transaction_id,
            actor_principal=actor_principal,
        )
        self.session.add_all([event, outbox])
        self.session.flush()
        return event


__all__ = [
    "V4_DOMAIN_EVENT_CHANNEL",
    "V4EventIntegrityError",
    "V4EventStore",
    "V4EventStoreError",
    "v4_outbox_envelope",
    "validate_v4_event_row",
    "validate_v4_outbox_row",
]
