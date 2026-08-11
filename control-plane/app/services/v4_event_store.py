"""Versioned event/outbox writer with byte-stable v4 compatibility routes.

The class name remains ``V4EventStore`` because it is an established internal
API.  Major-2 activation is deliberately route-and-payload gated: the frozen
V5 lifecycle payload selects the V5 envelope, while the already-shipped V4
routes retain their previous payload and envelope bytes.

Since the C2 foundation extraction, the closed route specifications and the
v4/v5 event/outbox verifiers live in :mod:`app.foundation.events`; this module
re-exports every original symbol (including the legacy ``_Route``/``_ROUTES``
names) and keeps the persisting writer, seq allocation, disabled-route gates
and the Stage 1A graph semantics.
"""
from __future__ import annotations

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
from app.utils.v4_integrity import canonical_digest
from app.services.v4_audit import V4AuditIntegrityError, validate_v4_audit_row

from app.foundation import events as _foundation_events
from app.foundation.events import (
    EVENT_ROUTES,
    EXACT_BINDING_FIELDS,
    MANIFEST_ACTIVATION_CONTEXT_FIELDS,
    V4_DOMAIN_EVENT_CHANNEL,
    V4EventIntegrityError,
    V4EventStoreError,
    V5_DOMAIN_EVENT_CHANNEL,
    V5EventRoute,
    V5_EVENT_ROUTES,
    EventRoute,
    require_exactly_one,
    select_v5_route,
    v4_outbox_envelope,
    v5_outbox_envelope,
    v5_routing_key,
    validate_exact_binding,
    validate_manifest_activation_context,
    validate_v4_event_row,
    validate_v4_outbox_row,
    validate_v5_event_row,
    validate_v5_outbox_row,
)
from app.foundation.events import (
    _CONTROLLER_FIELDS,
    _as_utc,
    _validate_route_payload,
    _validate_v5_route_payload,
    _wire_time,
)

# C2 foundation extraction: closed route specs and verifiers live in
# app.foundation.events.  Re-export every original symbol (legacy private
# names included) so established internal importers, tests and patch targets
# keep working unchanged.
_Route = EventRoute
_V5Route = V5EventRoute
_ROUTES = EVENT_ROUTES
_V5_ROUTES = V5_EVENT_ROUTES
_select_v5_route = select_v5_route
_validate_exact_binding = validate_exact_binding
_validate_manifest_activation_context = validate_manifest_activation_context
_v5_routing_key = v5_routing_key
_exact_one = require_exactly_one


# R1 freezes the major-2 envelope and lifecycle-event foundation only.  The
# activated route definitions remain pure structural-validator primitives;
# production activation writers belong to the later manifest-import slice.
_DISABLED_V5_WRITER_ROUTES = frozenset(
    {
        ("ai_application", "application.activated"),
        ("system_component", "system_component.activated"),
    }
)


# Component-revision producer/receipt/revision semantics are an R3 activation;
# reject its frozen payload marker instead of silently falling back to the
# pre-R1 v4 construction route.
_DISABLED_V5_ROUTE_MARKERS: dict[tuple[str, str], str] = {
    ("component_revision", "component_revision.recorded"): (
        "exact_component_revision_binding"
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
        authority_receipt_id: str | None = None,
    ) -> Event:
        return self._append_event(
            workspace_id=workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
            actor_principal=actor_principal,
            transaction_id=transaction_id,
            occurred_at=occurred_at,
            authority_receipt_id=authority_receipt_id,
            allow_manifest_activation=False,
        )

    def append_composed_activation_event(
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
        authority_receipt_id: str | None = None,
        composition_capability: object,
    ) -> Event:
        route_key = (aggregate_type, event_type)
        binding_fields = {
            ("ai_application", "application.activated"): (
                "AI_APPLICATION",
                "exact_previous_application_binding",
                "exact_application_binding",
            ),
            ("system_component", "system_component.activated"): (
                "SYSTEM_COMPONENT",
                "exact_previous_system_component_binding",
                "exact_system_component_binding",
            ),
        }
        binding_spec = binding_fields.get(route_key)
        if binding_spec is None:
            raise V4EventStoreError("v5.event_route_not_activated")
        subject_kind, previous_field, new_field = binding_spec
        previous = payload.get(previous_field)
        new = payload.get(new_field)
        if not isinstance(previous, dict) or not isinstance(new, dict):
            raise V4EventStoreError("v5.event_binding_invalid")
        try:
            from app.services.v5_manifest_import_coordinator import (
                ManifestImportCompositionError,
                _consume_activation_composition_capability,
            )

            _consume_activation_composition_capability(
                composition_capability,
                session=self.session,
                purpose="EVENT_ACTIVATE",
                workspace_id=workspace_id,
                transaction_id=transaction_id,
                subject_kind=subject_kind,
                subject_id=aggregate_id,
                previous_binding=previous,
                new_binding=new,
                event_type=event_type,
                manifest_activation_context=payload.get("manifest_activation_context"),
            )
        except ManifestImportCompositionError as exc:
            raise V4EventStoreError(exc.code) from exc
        return self._append_event(
            workspace_id=workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
            actor_principal=actor_principal,
            transaction_id=transaction_id,
            occurred_at=occurred_at,
            authority_receipt_id=authority_receipt_id,
            allow_manifest_activation=True,
        )

    def append_composed_manifest_record_event(
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
        authority_receipt_id: str,
        composition_capability: object,
    ) -> Event:
        route = _V5_ROUTES.get((aggregate_type, event_type))
        if route is None:
            raise V4EventStoreError("v5.event_route_not_activated")
        exact_subject = payload.get(route.self_binding_field)
        if not isinstance(exact_subject, dict):
            raise V4EventStoreError("v5.event_binding_invalid")
        try:
            from app.services.v5_manifest_import_coordinator import (
                ManifestImportCompositionError,
                _consume_activation_composition_capability,
            )

            _consume_activation_composition_capability(
                composition_capability,
                session=self.session,
                purpose="EVENT_RECORD",
                workspace_id=workspace_id,
                transaction_id=transaction_id,
                subject_kind=route.subject_kind,
                subject_id=aggregate_id,
                previous_binding={},
                new_binding=exact_subject,
                event_type=event_type,
            )
        except ManifestImportCompositionError as exc:
            raise V4EventStoreError(exc.code) from exc
        return self._append_event(
            workspace_id=workspace_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            event_type=event_type,
            payload=payload,
            causation_id=causation_id,
            correlation_id=correlation_id,
            actor_principal=actor_principal,
            transaction_id=transaction_id,
            occurred_at=occurred_at,
            authority_receipt_id=authority_receipt_id,
            allow_manifest_activation=True,
        )

    def _append_event(
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
        occurred_at: datetime | None,
        authority_receipt_id: str | None,
        allow_manifest_activation: bool,
    ) -> Event:
        route_key = (aggregate_type, event_type)
        if route_key in _DISABLED_V5_WRITER_ROUTES and not allow_manifest_activation:
            raise V4EventStoreError("v5.event_route_not_activated")
        disabled_marker = _DISABLED_V5_ROUTE_MARKERS.get(
            route_key
        )
        if (
            disabled_marker is not None
            and disabled_marker in payload
            and not allow_manifest_activation
        ):
            raise V4EventStoreError("v5.event_route_not_activated")
        v5_route = _select_v5_route(aggregate_type, event_type, payload)
        v4_route = None if v5_route is not None else _ROUTES.get(
            (aggregate_type, event_type)
        )
        if v5_route is None and v4_route is None:
            raise V4EventStoreError("v4.event_route_not_allowed")
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

        exact_subject_binding: dict[str, Any] | None = None
        if v5_route is not None:
            if not isinstance(authority_receipt_id, str) or not authority_receipt_id:
                raise V4EventStoreError("v5.event_authority_receipt_id_invalid")
            payload_digest, exact_subject_binding = _validate_v5_route_payload(
                v5_route,
                payload,
                workspace_id=workspace_id,
            )
            if aggregate_id != exact_subject_binding["id"]:
                raise V4EventStoreError("v5.event_aggregate_id_mismatch")
            contract_version = "v5"
            event_version = "2.0"
            event_contract_major = 2
            routing_key = _v5_routing_key(exact_subject_binding)
            owner = v5_route.owner
            channel = V5_DOMAIN_EVENT_CHANNEL
        else:
            assert v4_route is not None
            if aggregate_id != payload.get(v4_route.aggregate_id_field):
                raise V4EventStoreError("v4.event_aggregate_id_mismatch")
            payload_digest = _validate_route_payload(v4_route, payload)
            contract_version = "v4"
            event_version = "1.0"
            event_contract_major = None
            routing_key = None
            owner = v4_route.owner
            channel = V4_DOMAIN_EVENT_CHANNEL

        at = occurred_at or _utc_now()
        seq = int(
            self.session.scalar(
                select(func.coalesce(func.max(Event.seq), 0)).where(
                    Event.contract_version == contract_version,
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
            actor=owner,
            trace_id=None,
            occurred_at=at,
            created_at=at,
            contract_version=contract_version,
            workspace_id=workspace_id,
            event_version=event_version,
            event_contract_major=event_contract_major,
            routing_key=routing_key,
            exact_subject_binding=exact_subject_binding,
            authority_receipt_id=authority_receipt_id if v5_route is not None else None,
            transaction_id=transaction_id,
            actor_principal=actor_principal,
            payload_digest=payload_digest,
        )
        envelope = (
            v5_outbox_envelope(event)
            if v5_route is not None
            else v4_outbox_envelope(event)
        )
        outbox = Outbox(
            outbox_id=new_outbox_id(),
            aggregate_id=aggregate_id,
            source_event_id=event.event_id,
            source_event_seq=seq,
            channel=channel,
            event_type=event_type,
            payload=envelope,
            payload_digest=canonical_digest(envelope),
            status="PENDING",
            attempts=0,
            created_at=at,
            contract_version=contract_version,
            workspace_id=workspace_id,
            aggregate_type=aggregate_type,
            event_version=event_version,
            event_contract_major=event_contract_major,
            transaction_id=transaction_id,
            actor_principal=actor_principal,
        )
        self.session.add_all([event, outbox])
        self.session.flush()
        if v5_route is not None:
            assert exact_subject_binding is not None
            validate_v5_event_row(
                event,
                workspace_id=workspace_id,
                event_type=event_type,
                transaction_id=transaction_id,
                actor_principal=actor_principal,
                subject_kind=exact_subject_binding["kind"],
                subject_id=exact_subject_binding["id"],
                subject_revision=exact_subject_binding["revision"],
                subject_digest=exact_subject_binding["digest"],
                authority_receipt_id=authority_receipt_id,
            )
            validate_v5_outbox_row(outbox, event=event)
        return event


__all__ = [
    "V4_DOMAIN_EVENT_CHANNEL",
    "V5_DOMAIN_EVENT_CHANNEL",
    "V4EventIntegrityError",
    "V4EventStore",
    "V4EventStoreError",
    "v4_outbox_envelope",
    "v5_outbox_envelope",
    "validate_v4_event_row",
    "validate_v4_outbox_row",
    "validate_v5_event_row",
    "validate_v5_outbox_row",
    "EVENT_ROUTES",
    "V5_EVENT_ROUTES",
    "EXACT_BINDING_FIELDS",
    "MANIFEST_ACTIVATION_CONTEXT_FIELDS",
    "EventRoute",
    "V5EventRoute",
    "select_v5_route",
    "validate_exact_binding",
    "validate_manifest_activation_context",
    "v5_routing_key",
    "require_exactly_one",
]
