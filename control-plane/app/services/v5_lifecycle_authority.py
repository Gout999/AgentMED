"""Internal append-only lifecycle persistence for D-014.

This module is deliberately below the Application Catalog workflow layer.  It
does not authenticate ``system-manifests.import``, authorize activation, write
events/audits/receipts, or commit a transaction.  R2 must perform those actions
in the same PostgreSQL unit of work and call ``append_activation_revision``
only after the workflow authorization has been established.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v5_tables import (
    AIApplication,
    AIApplicationLifecycleRevision,
    SystemComponent,
    SystemComponentLifecycleRevision,
)
from app.services.v5_authority import V5AuthorityError, V5AuthorityService
from app.utils.v4_integrity import V4IntegrityError
from app.utils.v5_integrity import V5_HASH_RULE, assert_v5_record_digest


class V5LifecycleAuthorityError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class V5LifecycleAppendResult:
    history: AIApplicationLifecycleRevision | SystemComponentLifecycleRevision
    replayed: bool


_LIFECYCLE_SPECS: dict[str, dict[str, Any]] = {
    "AI_APPLICATION": {
        "projection_model": AIApplication,
        "history_model": AIApplicationLifecycleRevision,
        "id_attr": "application_id",
        "previous_attr": "exact_previous_application_binding",
        "fields": (
            "application_id",
            "workspace_id",
            "project_id",
            "slug",
            "display_name",
            "owner_principal_ids",
            "criticality",
            "data_classification",
            "governance_mode",
            "lifecycle_state",
        ),
    },
    "SYSTEM_COMPONENT": {
        "projection_model": SystemComponent,
        "history_model": SystemComponentLifecycleRevision,
        "id_attr": "component_id",
        "previous_attr": "exact_previous_system_component_binding",
        "fields": (
            "component_id",
            "workspace_id",
            "application_id",
            "component_kind",
            "logical_name",
            "owner_principal_ids",
            "criticality",
            "data_classification",
            "permission_classification",
            "effect_classification",
            "dataset_role",
            "lifecycle_state",
        ),
    },
}

_RECORD_ENVELOPE_FIELDS = {
    "schema_version",
    "workspace_id",
    "revision",
    "recorded_by_principal",
    "recorded_at",
    "immutable",
    "hash_rule",
    "record_digest",
    "authority_receipt_id",
}


def _wire_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_wire_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise V5LifecycleAuthorityError("v5.lifecycle.recorded_at_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise V5LifecycleAuthorityError("v5.lifecycle.recorded_at_invalid") from exc
    if parsed.tzinfo is None:
        raise V5LifecycleAuthorityError("v5.lifecycle.recorded_at_invalid")
    return parsed.astimezone(timezone.utc)


class V5LifecycleAuthorityService:
    """Persist lifecycle revisions without owning the surrounding workflow."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.authority = V5AuthorityService(session)

    @staticmethod
    def _validate_envelope(
        *, kind: str, envelope_payload: dict[str, Any], revision: int, state: str
    ) -> tuple[str, str, str, datetime, dict[str, Any] | None]:
        spec = _LIFECYCLE_SPECS.get(kind)
        if spec is None or not isinstance(envelope_payload, dict):
            raise V5LifecycleAuthorityError("v5.lifecycle.kind_invalid")
        previous_field = (
            "exact_previous_application_binding"
            if kind == "AI_APPLICATION"
            else "exact_previous_system_component_binding"
        )
        expected_previous_field = (
            f"{previous_field}_or_null" if revision == 1 else previous_field
        )
        expected_fields = set(spec["fields"]) | {
            expected_previous_field,
            "record_envelope",
        }
        actual_fields = set(envelope_payload)
        allowed_shapes = {frozenset(expected_fields)}
        if kind == "SYSTEM_COMPONENT":
            allowed_shapes.add(frozenset(expected_fields - {"dataset_role"}))
        if frozenset(actual_fields) not in allowed_shapes:
            raise V5LifecycleAuthorityError("v5.lifecycle.envelope_fields_mismatch")
        try:
            digest = assert_v5_record_digest(envelope_payload)
        except (V4IntegrityError, AttributeError, TypeError) as exc:
            raise V5LifecycleAuthorityError("v5.lifecycle.integrity_invalid") from exc
        record_envelope = envelope_payload.get("record_envelope")
        if (
            not isinstance(record_envelope, dict)
            or set(record_envelope) != _RECORD_ENVELOPE_FIELDS
            or record_envelope.get("schema_version") != "2.0"
            or record_envelope.get("immutable") is not True
            or record_envelope.get("hash_rule") != V5_HASH_RULE
        ):
            raise V5LifecycleAuthorityError("v5.lifecycle.envelope_invalid")
        subject_id = envelope_payload.get(spec["id_attr"])
        workspace_id = envelope_payload.get("workspace_id")
        receipt_id = record_envelope.get("authority_receipt_id")
        principal = record_envelope.get("recorded_by_principal")
        if (
            not isinstance(subject_id, str)
            or not isinstance(workspace_id, str)
            or not isinstance(receipt_id, str)
            or not isinstance(principal, str)
            or record_envelope.get("workspace_id") != workspace_id
            or record_envelope.get("revision") != revision
            or envelope_payload.get("lifecycle_state") != state
        ):
            raise V5LifecycleAuthorityError("v5.lifecycle.envelope_invalid")
        recorded_at = _parse_wire_time(record_envelope.get("recorded_at"))
        previous = envelope_payload.get(expected_previous_field)
        return subject_id, workspace_id, receipt_id, recorded_at, previous

    def append_registration_revision(
        self, *, kind: str, envelope_payload: dict[str, Any]
    ) -> V5LifecycleAppendResult:
        """Append revision 1 REGISTERED and create its current-head projection."""

        subject_id, workspace_id, receipt_id, recorded_at, previous = (
            self._validate_envelope(
                kind=kind,
                envelope_payload=envelope_payload,
                revision=1,
                state="REGISTERED",
            )
        )
        if previous is not None:
            raise V5LifecycleAuthorityError("v5.lifecycle.registration_previous_forbidden")
        spec = _LIFECYCLE_SPECS[kind]
        history_model = spec["history_model"]
        projection_model = spec["projection_model"]
        digest = envelope_payload["record_envelope"]["record_digest"]
        existing = self.session.get(history_model, (workspace_id, subject_id, 1))
        if existing is not None:
            if existing.record_digest != digest:
                raise V5LifecycleAuthorityError("v5.lifecycle.revision_conflict")
            try:
                self.authority.validate_exact_lifecycle_binding(
                    workspace_id=workspace_id,
                    binding={
                        "kind": kind,
                        "id": subject_id,
                        "revision": 1,
                        "digest": digest,
                    },
                )
            except V5AuthorityError as exc:
                raise V5LifecycleAuthorityError(
                    "v5.lifecycle.replay_integrity_invalid"
                ) from exc
            return V5LifecycleAppendResult(history=existing, replayed=True)
        if self.session.get(projection_model, subject_id) is not None:
            raise V5LifecycleAuthorityError("v5.lifecycle.projection_without_history")

        application: AIApplication | None = None
        if kind == "SYSTEM_COMPONENT":
            application = self.session.get(
                AIApplication, envelope_payload.get("application_id")
            )
            if (
                application is None
                or application.workspace_id != workspace_id
                or application.lifecycle_state != "ACTIVE"
            ):
                raise V5LifecycleAuthorityError(
                    "v5.lifecycle.component_application_not_active"
                )

        projection_values = {
            field: envelope_payload.get(field) for field in spec["fields"]
        }
        projection = projection_model(
            **projection_values,
            revision=1,
            envelope_payload=envelope_payload,
            record_digest=digest,
            authority_receipt_id=receipt_id,
            recorded_by_principal=envelope_payload["record_envelope"][
                "recorded_by_principal"
            ],
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        self.session.add(projection)
        self.session.flush()
        history_values: dict[str, Any] = {
            "workspace_id": workspace_id,
            spec["id_attr"]: subject_id,
            "revision": 1,
            "lifecycle_state": "REGISTERED",
            spec["previous_attr"]: None,
            "envelope_payload": envelope_payload,
            "record_digest": digest,
            "authority_receipt_id": receipt_id,
            "recorded_by_principal": envelope_payload["record_envelope"][
                "recorded_by_principal"
            ],
            "recorded_at": recorded_at,
        }
        if kind == "SYSTEM_COMPONENT":
            history_values["application_id"] = envelope_payload["application_id"]
        history = history_model(**history_values)
        self.session.add(history)
        self.session.flush()
        return V5LifecycleAppendResult(history=history, replayed=False)

    def append_activation_revision(
        self, *, kind: str, envelope_payload: dict[str, Any]
    ) -> V5LifecycleAppendResult:
        """Deny direct activation until the R2 composition is implemented.

        A lifecycle controller, internal caller, or transport cannot obtain
        activation authority merely by reaching this persistence service.
        """

        del kind, envelope_payload
        raise V5LifecycleAuthorityError("v5.lifecycle.composition_required")

    def _append_activation_revision_for_foundation_test(
        self, *, kind: str, envelope_payload: dict[str, Any]
    ) -> V5LifecycleAppendResult:
        """Exercise the R1 storage CAS in tests; never a production issuer.

        This private seam proves persistence mechanics only.  It deliberately
        cannot prove manifest-import authorization or business activation.
        """

        subject_id, workspace_id, receipt_id, recorded_at, previous = (
            self._validate_envelope(
                kind=kind,
                envelope_payload=envelope_payload,
                revision=2,
                state="ACTIVE",
            )
        )
        digest = envelope_payload["record_envelope"]["record_digest"]
        spec = _LIFECYCLE_SPECS[kind]
        history_model = spec["history_model"]
        projection_model = spec["projection_model"]
        if not isinstance(previous, dict) or set(previous) != {
            "kind",
            "id",
            "revision",
            "digest",
        }:
            raise V5LifecycleAuthorityError("v5.lifecycle.activation_previous_invalid")

        projection = self.session.scalar(
            select(projection_model)
            .where(
                projection_model.workspace_id == workspace_id,
                getattr(projection_model, spec["id_attr"]) == subject_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if projection is None:
            raise V5LifecycleAuthorityError("v5.lifecycle.current_head_missing")

        existing = self.session.get(history_model, (workspace_id, subject_id, 2))
        if projection.revision == 2 and existing is not None:
            if existing.record_digest != digest or projection.record_digest != digest:
                raise V5LifecycleAuthorityError("v5.lifecycle.revision_conflict")
            try:
                self.authority.validate_exact_lifecycle_binding(
                    workspace_id=workspace_id,
                    binding={
                        "kind": kind,
                        "id": subject_id,
                        "revision": 2,
                        "digest": digest,
                    },
                    require_current=True,
                    require_active=True,
                    application_id=(
                        envelope_payload.get("application_id")
                        if kind == "SYSTEM_COMPONENT"
                        else None
                    ),
                )
            except V5AuthorityError as exc:
                raise V5LifecycleAuthorityError(
                    "v5.lifecycle.replay_integrity_invalid"
                ) from exc
            return V5LifecycleAppendResult(history=existing, replayed=True)
        if projection.revision != 1 or projection.lifecycle_state != "REGISTERED":
            raise V5LifecycleAuthorityError("v5.lifecycle.activation_stale_head")
        expected_previous = {
            "kind": kind,
            "id": subject_id,
            "revision": 1,
            "digest": projection.record_digest,
        }
        if previous != expected_previous:
            raise V5LifecycleAuthorityError("v5.lifecycle.activation_previous_invalid")
        try:
            self.authority.validate_exact_lifecycle_binding(
                workspace_id=workspace_id,
                binding=expected_previous,
                require_current=True,
                application_id=(
                    envelope_payload.get("application_id")
                    if kind == "SYSTEM_COMPONENT"
                    else None
                ),
            )
        except V5AuthorityError as exc:
            raise V5LifecycleAuthorityError("v5.lifecycle.current_head_invalid") from exc

        for field in spec["fields"]:
            if field != "lifecycle_state" and getattr(projection, field) != envelope_payload.get(
                field
            ):
                raise V5LifecycleAuthorityError(
                    "v5.lifecycle.activation_business_mutation_forbidden"
                )
        projection.lifecycle_state = "ACTIVE"
        projection.revision = 2
        projection.envelope_payload = envelope_payload
        projection.record_digest = digest
        projection.authority_receipt_id = receipt_id
        projection.recorded_by_principal = envelope_payload["record_envelope"][
            "recorded_by_principal"
        ]
        projection.updated_at = recorded_at

        history_values: dict[str, Any] = {
            "workspace_id": workspace_id,
            spec["id_attr"]: subject_id,
            "revision": 2,
            "lifecycle_state": "ACTIVE",
            spec["previous_attr"]: previous,
            "envelope_payload": envelope_payload,
            "record_digest": digest,
            "authority_receipt_id": receipt_id,
            "recorded_by_principal": envelope_payload["record_envelope"][
                "recorded_by_principal"
            ],
            "recorded_at": recorded_at,
        }
        if kind == "SYSTEM_COMPONENT":
            history_values["application_id"] = envelope_payload["application_id"]
        history = history_model(**history_values)
        self.session.add(history)
        self.session.flush()
        return V5LifecycleAppendResult(history=history, replayed=False)


__all__ = [
    "V5LifecycleAppendResult",
    "V5LifecycleAuthorityError",
    "V5LifecycleAuthorityService",
]
