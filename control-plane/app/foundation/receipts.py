"""Closed V5 event specifications and receipt-payload mechanics.

Major-aware event specifications (``EVENT_BUSINESS_FIELDS`` /
``EVENT_BUSINESS_FIELDS_BY_EVENT``) describe which business fields a
registered event payload must carry, and the exact AuthorityReceipt payload
construction/projection primitives live here as well.  This module defines
data and verification mechanics only (import rule: stdlib + ``app.models`` +
``app.utils``); it never owns domain commands, capability activation or
coordinator decisions, and it never imports a domain service, API, CLI,
Console or adapter.
"""
from __future__ import annotations

from typing import Any

from app.foundation.records import RECORD_ENVELOPE_FIELDS
from app.utils.v4_integrity import record_digest

# Envelope fields re-exported so ``app.foundation.receipts`` exposes the same
# closed record-envelope surface as ``app.foundation.records``.
__all__ = [
    "EVENT_BUSINESS_FIELDS",
    "EVENT_BUSINESS_FIELDS_BY_EVENT",
    "RECORD_ENVELOPE_FIELDS",
    "EventBusinessFieldsError",
    "build_receipt_payload",
    "expected_receipt_projection",
    "validate_event_business_fields",
]

# Business fields that the registered event payload must carry, extracted from
# the subject envelope payload per subject kind.
EVENT_BUSINESS_FIELDS: dict[str, tuple[str, ...]] = {
    "AI_APPLICATION": ("application_id", "project_id", "slug", "lifecycle_state"),
    "ENVIRONMENT": ("environment_id", "application_id", "logical_name", "lifecycle_state"),
    "SYSTEM_COMPONENT": (
        "component_id",
        "application_id",
        "component_kind",
        "logical_name",
        "lifecycle_state",
    ),
    "DEPENDENCY_EDGE": (
        "edge_id",
        "application_id",
        "from_component_id",
        "to_component_id",
        "relation",
        "edge_digest",
    ),
    "COMPONENT_REVISION": (
        "component_revision_id",
        "component_id",
        "component_kind",
        "identity_assurance",
        "configuration_digest",
    ),
    "TOPOLOGY_REVISION": (
        "topology_revision_id",
        "application_id",
        "topology_digest",
    ),
    "SYSTEM_VERSION_SET": (
        "system_version_set_id",
        "application_id",
        "declared_environment_id",
        "version_set_digest",
    ),
    "BOOTSTRAP_ATTESTATION": (
        "bootstrap_attestation_id",
        "application_id",
        "environment_id",
        "attester_principal_id",
        "attester_trust_role",
        "attestation_scope",
    ),
    "SYSTEM_ASSIGNMENT": (
        "assignment_id",
        "application_id",
        "environment_id",
        "generation",
        "exposure",
    ),
}

# V5-1C per-event business fields for case-controller records.  The propose and
# confirm events carry different field sets (per contracts/v5/events.yaml), so
# the exact extraction cannot be a single subject-kind set.  Each spec entry is
# ``(event_payload_field, envelope_field)``; ``None`` as the envelope field
# means the value is the derived exact subject binding (kind/id/revision/digest)
# built from the record identity + envelope digest.
EVENT_BUSINESS_FIELDS_BY_EVENT: dict[
    tuple[str, str], tuple[tuple[str, str | None], ...]
] = {
    ("APPLICATION_CASE_BINDING", "case.application_bound"): (
        ("exact_application_case_binding", None),
        ("exact_case_binding", "exact_case_binding"),
        ("application_id", "application_id"),
        ("environment_id", "environment_id"),
        (
            "declared_system_version_set_binding_or_unknown",
            "declared_system_version_set_binding_or_unknown",
        ),
    ),
    ("ACCEPTANCE_CRITERIA_REVISION", "acceptance_criteria.proposed"): (
        ("exact_acceptance_criteria_revision_binding", None),
        ("exact_case_binding", "exact_case_binding"),
        ("exact_resolution_contract_binding", "exact_resolution_contract_binding"),
        ("confirmation_status", "confirmation_status"),
        ("proposer_principal", "proposer_principal"),
        ("proposed_at", "proposed_at"),
        ("acceptance_source", "acceptance_source"),
        ("expected_behavior", "expected_behavior"),
        ("applicable_workload_profile", "applicable_workload_profile"),
        ("applicable_deployment_profile", "applicable_deployment_profile"),
        ("acceptance_digest", "acceptance_digest"),
    ),
    ("ACCEPTANCE_CRITERIA_REVISION", "acceptance_criteria.confirmed"): (
        ("exact_acceptance_criteria_revision_binding", None),
        (
            "exact_previous_proposed_revision_binding",
            "exact_previous_proposed_revision_binding",
        ),
        ("exact_case_binding", "exact_case_binding"),
        ("exact_resolution_contract_binding", "exact_resolution_contract_binding"),
        ("confirmation_status", "confirmation_status"),
        ("confirmer_principal", "confirmer_principal"),
        ("confirmed_at", "confirmed_at"),
        ("acceptance_source", "acceptance_source"),
        ("expected_behavior", "expected_behavior"),
        ("applicable_workload_profile", "applicable_workload_profile"),
        ("applicable_deployment_profile", "applicable_deployment_profile"),
        ("acceptance_digest", "acceptance_digest"),
    ),
}

_EXACT_BINDING_ID_FIELD: dict[str, str] = {
    "APPLICATION_CASE_BINDING": "application_case_binding_id",
    "ACCEPTANCE_CRITERIA_REVISION": "acceptance_criteria_revision_id",
}


class EventBusinessFieldsError(Exception):
    """Event business-payload validation failure carrying the wire error code.

    Callers translate ``code`` into their own error surface so the exact
    wire error code of the pre-extraction implementation is preserved.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _derived_exact_subject_binding(
    subject_kind: str, envelope: dict[str, Any]
) -> dict[str, Any]:
    envelope_payload = envelope.get("record_envelope")
    id_field = _EXACT_BINDING_ID_FIELD.get(subject_kind)
    if (
        not isinstance(envelope_payload, dict)
        or id_field is None
        or envelope.get(id_field) is None
        or not isinstance(envelope_payload.get("record_digest"), str)
    ):
        raise EventBusinessFieldsError("v5.authority.subject_binding_invalid")
    return {
        "kind": subject_kind,
        "id": envelope[id_field],
        "revision": None,
        "digest": envelope_payload["record_digest"],
    }


def _validate_event_business_fields_by_event(
    *,
    payload: dict[str, Any] | None,
    subject_kind: str,
    envelope: dict[str, Any],
    correlation_id: str | None,
    spec: tuple[tuple[str, str | None], ...],
) -> None:
    """Per-event business-field validation for V5-1C case records.

    ``None`` envelope paths resolve to the derived exact subject binding
    (the self reference that cannot live inside the hashed envelope without
    creating a digest cycle).
    """

    expected_business: dict[str, Any] = {}
    for field_name, envelope_path in spec:
        if envelope_path is None:
            value = _derived_exact_subject_binding(subject_kind, envelope)
        else:
            if envelope_path not in envelope:
                raise EventBusinessFieldsError(
                    "v5.authority.event_business_fields_mismatch"
                )
            value = envelope[envelope_path]
        expected_business[field_name] = value
    event_payload = payload or {}
    expected: dict[str, Any] = {
        **expected_business,
        "subject_kind": subject_kind,
        "subject_id": event_payload.get("subject_id"),
        "subject_revision": event_payload.get("subject_revision"),
        "subject_digest": event_payload.get("subject_digest"),
        "authority_receipt_id": event_payload.get("authority_receipt_id"),
    }
    if event_payload != expected:
        raise EventBusinessFieldsError("v5.authority.event_binding_mismatch")
    # Case records correlate on the exact case binding's case id.
    exact_case_binding = envelope.get("exact_case_binding")
    correlation_case_id = (
        exact_case_binding.get("case_id")
        if isinstance(exact_case_binding, dict)
        else None
    )
    if (
        not isinstance(correlation_case_id, str)
        or correlation_id != correlation_case_id
    ):
        raise EventBusinessFieldsError("v5.authority.event_correlation_mismatch")


def validate_event_business_fields(
    *,
    payload: dict[str, Any] | None,
    event_type: str,
    subject_kind: str,
    envelope: dict[str, Any],
    correlation_id: str | None,
    exact_subject_binding: dict[str, Any] | None = None,
    subject_digest: str | None = None,
    major2: bool = False,
    lifecycle_binding: dict[str, Any] | None = None,
    lifecycle_previous_binding: dict[str, Any] | None = None,
    spec: tuple[tuple[str, str | None], ...] | None = None,
) -> None:
    """Validate one event business payload against the frozen event spec.

    Generic (major-1) and V5-1C by-event validation run when ``major2`` is
    false; ``spec`` overrides the per-event spec lookup.  Major-2 closed
    shapes run when ``major2`` is true: non-lifecycle kinds build their
    expected payload from ``exact_subject_binding`` + ``subject_digest``,
    lifecycle kinds (AI_APPLICATION / SYSTEM_COMPONENT) from the pre-built
    ``lifecycle_binding`` and ``lifecycle_previous_binding``.  Failures raise
    :class:`EventBusinessFieldsError` with the caller-facing wire error code.
    """
    if not major2:
        by_event = (
            spec
            if spec is not None
            else EVENT_BUSINESS_FIELDS_BY_EVENT.get((subject_kind, event_type))
        )
        if by_event is not None:
            _validate_event_business_fields_by_event(
                payload=payload,
                subject_kind=subject_kind,
                envelope=envelope,
                correlation_id=correlation_id,
                spec=by_event,
            )
            return
        fields = EVENT_BUSINESS_FIELDS.get(subject_kind)
        if fields is None:
            raise EventBusinessFieldsError("v5.authority.subject_kind_not_implemented")
        expected_business = {
            field: envelope[field] for field in fields if field in envelope
        }
        if set(expected_business) != set(fields):
            raise EventBusinessFieldsError(
                "v5.authority.event_business_fields_mismatch"
            )
        event_payload = payload or {}
        expected: dict[str, Any] = {
            **expected_business,
            "subject_kind": subject_kind,
            "subject_id": event_payload.get("subject_id"),
            "subject_revision": event_payload.get("subject_revision"),
            "subject_digest": event_payload.get("subject_digest"),
            "authority_receipt_id": event_payload.get("authority_receipt_id"),
        }
        if event_payload != expected:
            raise EventBusinessFieldsError("v5.authority.event_binding_mismatch")
        if correlation_id != envelope.get("application_id"):
            raise EventBusinessFieldsError("v5.authority.event_correlation_mismatch")
        return

    if lifecycle_binding is not None:
        if event_type == "application.registered":
            expected = {
                "exact_previous_application_binding_or_null": None,
                "exact_application_binding": lifecycle_binding,
                "project_id": envelope.get("project_id"),
                "slug": envelope.get("slug"),
                "lifecycle_state": "REGISTERED",
            }
        elif event_type == "system_component.registered":
            expected = {
                "exact_previous_system_component_binding_or_null": None,
                "exact_system_component_binding": lifecycle_binding,
                "application_id": envelope.get("application_id"),
                "component_kind": envelope.get("component_kind"),
                "logical_name": envelope.get("logical_name"),
                "lifecycle_state": "REGISTERED",
            }
        elif event_type == "application.activated":
            expected = {
                "exact_previous_application_binding": lifecycle_previous_binding,
                "exact_application_binding": lifecycle_binding,
                "lifecycle_state": "ACTIVE",
            }
        elif event_type == "system_component.activated":
            expected = {
                "exact_previous_system_component_binding": (
                    lifecycle_previous_binding
                ),
                "exact_system_component_binding": lifecycle_binding,
                "lifecycle_state": "ACTIVE",
            }
        else:
            raise EventBusinessFieldsError(
                "v5.authority.major2_lifecycle_event_invalid"
            )
        if any(payload.get(field) != value for field, value in expected.items()):
            raise EventBusinessFieldsError("v5.authority.event_binding_mismatch")
        return

    exact = {
        "kind": subject_kind,
        "id": exact_subject_binding["id"],
        "revision": exact_subject_binding["revision"],
        "digest": subject_digest,
    }
    expected_by_kind: dict[str, dict[str, Any]] = {
        "ENVIRONMENT": {
            "exact_environment_binding": exact,
            "application_id": envelope.get("application_id"),
            "logical_name": envelope.get("logical_name"),
            "lifecycle_state": envelope.get("lifecycle_state"),
        },
        "DEPENDENCY_EDGE": {
            "exact_dependency_edge_binding": exact,
            "application_id": envelope.get("application_id"),
            "from_component_id": envelope.get("from_component_id"),
            "to_component_id": envelope.get("to_component_id"),
            "relation": envelope.get("relation"),
            "edge_digest": envelope.get("edge_digest"),
        },
        "COMPONENT_REVISION": {
            "exact_component_revision_binding": exact,
            "exact_system_component_binding": envelope.get(
                "exact_system_component_binding"
            ),
            "component_kind": envelope.get("component_kind"),
            "identity_assurance": envelope.get("identity_assurance"),
            "configuration_digest": envelope.get("configuration_digest"),
        },
        "TOPOLOGY_REVISION": {
            "exact_topology_revision_binding": exact,
            "application_id": envelope.get("application_id"),
            "exact_edge_revision_bindings": envelope.get(
                "exact_edge_revision_bindings"
            ),
            "topology_digest": envelope.get("topology_digest"),
        },
        "SYSTEM_VERSION_SET": {
            "exact_system_version_set_binding": exact,
            "application_id": envelope.get("application_id"),
            "declared_environment_id": envelope.get("declared_environment_id"),
            "exact_component_revision_bindings": envelope.get(
                "exact_component_revision_bindings"
            ),
            "exact_topology_revision_binding": envelope.get(
                "exact_topology_revision_binding"
            ),
            "version_set_digest": envelope.get("version_set_digest"),
        },
        "BOOTSTRAP_ATTESTATION": {
            "exact_bootstrap_attestation_binding": exact,
            "application_id": envelope.get("application_id"),
            "environment_id": envelope.get("environment_id"),
            "exact_initial_system_version_set_binding": envelope.get(
                "exact_initial_system_version_set_binding"
            ),
            "attester_principal_id": envelope.get("attester_principal_id"),
            "attester_trust_role": envelope.get("attester_trust_role"),
            "attestation_scope": envelope.get("attestation_scope"),
        },
    }
    if subject_kind == "SYSTEM_ASSIGNMENT":
        authority = envelope.get("exact_assignment_authority_binding") or {}
        slots = envelope.get("exact_slot_version_set_bindings") or []
        initial = (
            {key: value for key, value in slots[0].items() if key != "slot"}
            if len(slots) == 1 and isinstance(slots[0], dict)
            else None
        )
        expected_by_kind[subject_kind] = {
            "exact_assignment_binding": exact,
            "exact_bootstrap_attestation_binding": {
                "kind": "BOOTSTRAP_ATTESTATION",
                "id": authority.get("id"),
                "revision": authority.get("revision"),
                "digest": authority.get("digest"),
            },
            "exact_initial_system_version_set_binding": initial,
            "application_id": envelope.get("application_id"),
            "environment_id": envelope.get("environment_id"),
            "generation": envelope.get("generation"),
            "exposure": envelope.get("exposure"),
        }
    expected = expected_by_kind.get(subject_kind)
    if expected is None or payload != expected:
        raise EventBusinessFieldsError("v5.authority.event_binding_mismatch")


def build_receipt_payload(
    *,
    authority_receipt_id: str,
    workspace_id: str,
    controller_registration_id: str,
    controller_registration_revision: int,
    controller_registration_digest: str,
    subject_kind: str,
    subject_id: str,
    subject_revision: int | None,
    subject_digest: str,
    owner: str,
    controller_principal: str,
    command: str,
    source_event_id: str,
    transaction_id: str,
    audit_ref: str,
    recorded_at: str,
    closed_major2: bool,
    resource: str | None = None,
    event_type: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Build the exact self-hashed AuthorityReceipt payload (both shapes).

    ``recorded_at`` is the already wire-normalized timestamp (``...Z``).  The
    closed major-2 shape carries ``source_event_id`` +
    ``controller_registration.contract_major: 1``; the 1A/1B compatibility
    shape replaces ``source_event_id`` with ``resource``/``event_type``/
    ``event_id``.  The payload digest is computed exactly as the durable row
    stores it.
    """
    payload: dict[str, Any] = {
        "schema_version": "2.0",
        "authority_receipt_id": authority_receipt_id,
        "workspace_id": workspace_id,
        "controller_registration": {
            "kind": "CONTROLLER_REGISTRATION",
            "id": controller_registration_id,
            "revision": controller_registration_revision,
            "digest": controller_registration_digest,
        },
        "subject": {
            "kind": subject_kind,
            "id": subject_id,
            "revision": subject_revision,
            "digest": subject_digest,
        },
        "owner": owner,
        "controller_principal": controller_principal,
        "command": command,
        "source_event_id": source_event_id,
        "transaction_id": transaction_id,
        "audit_ref": audit_ref,
        "recorded_at": recorded_at,
        "immutable": True,
        "hash_rule": (
            "jcs-rfc8785-v1+sha256(excluding:/authority_receipt_digest)"
        ),
        "authority_receipt_digest": "",
    }
    if not closed_major2:
        # Compatibility shape for existing V5-1A/1B producers.  New
        # lifecycle major-2 receipts use the frozen closed shape above.
        payload.pop("source_event_id")
        payload.update(
            {
                "resource": resource,
                "event_type": event_type,
                "event_id": event_id,
            }
        )
    else:
        payload["controller_registration"]["contract_major"] = 1
    digest = record_digest(
        payload, self_digest_field="authority_receipt_digest"
    )
    payload["authority_receipt_digest"] = digest
    return payload


def expected_receipt_projection(
    *,
    authority_receipt_id: str,
    workspace_id: str,
    controller_registration_id: str,
    controller_registration_revision: int,
    controller_registration_digest: str,
    subject_kind: str,
    subject_id: str,
    subject_revision: int | None,
    subject_digest: str,
    owner: str,
    controller_principal: str,
    command: str,
    source_event_id: str,
    transaction_id: str,
    audit_ref: str,
    recorded_at: str,
    authority_receipt_digest: str,
    closed_major2: bool,
    resource: str | None = None,
    event_type: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Project the exact expected receipt payload from durable row values.

    Mirrors :func:`build_receipt_payload` (both shapes, including
    ``controller_registration.contract_major`` for major-2) without computing
    a digest; ``authority_receipt_digest`` is the stored digest to compare
    against.
    """
    expected = {
        "schema_version": "2.0",
        "authority_receipt_id": authority_receipt_id,
        "workspace_id": workspace_id,
        "controller_registration": {
            "kind": "CONTROLLER_REGISTRATION",
            "id": controller_registration_id,
            "revision": controller_registration_revision,
            "digest": controller_registration_digest,
        },
        "subject": {
            "kind": subject_kind,
            "id": subject_id,
            "revision": subject_revision,
            "digest": subject_digest,
        },
        "owner": owner,
        "controller_principal": controller_principal,
        "command": command,
        "source_event_id": source_event_id,
        "transaction_id": transaction_id,
        "audit_ref": audit_ref,
        "recorded_at": recorded_at,
        "immutable": True,
        "hash_rule": (
            "jcs-rfc8785-v1+sha256(excluding:/authority_receipt_digest)"
        ),
        "authority_receipt_digest": authority_receipt_digest,
    }
    if not closed_major2:
        expected.pop("source_event_id")
        expected.update(
            {
                "resource": resource,
                "event_type": event_type,
                "event_id": event_id,
            }
        )
    else:
        expected["controller_registration"]["contract_major"] = 1
    return expected
