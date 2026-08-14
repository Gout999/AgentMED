"""V5 application-catalog controller authority (schema-major-2).

The v4 ``AuthorityService`` loads the frozen v4 ownership/event catalogs and its
receipt chain validation is Stage-1A-specific.  The V5 catalog controller is
registered against the ``contracts/v5`` ownership + event catalogs instead; this
module replicates the trust-root resolution and receipt writing for the four
V5 catalog subject kinds without touching v4 semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Audit, Event, Outbox
from app.models.v4_tables import AuthorityReceipt, ControllerRegistration
from app.models.v5_tables import (
    AIApplication,
    AcceptanceCriteriaRevision,
    ApplicationCaseBinding,
    BootstrapAttestation,
    ComponentRevision,
    DependencyEdge,
    Environment,
    SystemAssignment,
    SystemComponent,
    SystemVersionSet,
    TopologyRevision,
)
from app.services.v4_audit import V4AuditIntegrityError, validate_v4_audit_row
from app.services.v4_event_store import (
    V4EventIntegrityError,
    validate_v4_event_row,
    validate_v4_outbox_row,
)
from app.utils.v4_integrity import V4IntegrityError, canonical_digest, record_digest
from app.utils.v5_integrity import (
    assert_v5_record_digest,
    v5_subject_identity_key,
)

V5_CATALOG_OWNER = "application-catalog-controller"


class V5AuthorityError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class V5ContractCatalog:
    root: Path
    ownership_digest: str
    event_catalog_digest: str
    record_authority: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class V5BuiltControllerRegistration:
    payload: dict[str, Any]
    registration_digest: str
    row_values: dict[str, Any]


@dataclass(frozen=True)
class V5ResolvedController:
    registration: ControllerRegistration
    subject_kind: str
    resource: str
    owner: str
    command: str
    event_type: str

    @property
    def controller_principal(self) -> str:
        return self.registration.controller_principal


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _v5_catalog_root(candidate: Path) -> Path | None:
    options = (candidate, candidate / "contracts" / "v5")
    for root in options:
        if (root / "aggregate-ownership.yaml").is_file() and (
            root / "events.yaml"
        ).is_file():
            return root.resolve()
    return None


def discover_v5_contracts_root(explicit: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    module_repo = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            module_repo / "contracts" / "v5",
            Path("/srv/contracts/v5"),
            Path("/app/contracts/v5"),
        ]
    )
    for candidate in candidates:
        root = _v5_catalog_root(candidate)
        if root is not None:
            return root
    raise V5AuthorityError("v5.authority.contract_catalog_unavailable")


@lru_cache(maxsize=8)
def _load_v5_catalog_cached(root_text: str) -> V5ContractCatalog:
    root = Path(root_text)
    try:
        ownership = yaml.safe_load(
            (root / "aggregate-ownership.yaml").read_text(encoding="utf-8")
        )
        events = yaml.safe_load((root / "events.yaml").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - stable fail-closed boundary
        raise V5AuthorityError("v5.authority.contract_catalog_unavailable") from exc
    record_authority = ownership.get("record_authority")
    if not isinstance(record_authority, dict):
        raise V5AuthorityError("v5.authority.contract_catalog_invalid")
    return V5ContractCatalog(
        root=root,
        ownership_digest=canonical_digest(ownership),
        event_catalog_digest=canonical_digest(events),
        record_authority=record_authority,
    )


def load_v5_contract_catalog(
    explicit: str | Path | None = None,
) -> V5ContractCatalog:
    return _load_v5_catalog_cached(str(discover_v5_contracts_root(explicit)))


def build_v5_controller_registration_record(
    *,
    controller_registration_id: str,
    workspace_id: str,
    owner: str,
    controller_principal: str,
    allowed_commands: list[str] | tuple[str, ...],
    service_identity_digest: str,
    registered_by_human_principal: str,
    registration_audit_ref: str,
    valid_from: datetime,
    registered_at: datetime,
    expires_at: datetime | None = None,
    revision: int = 1,
    previous_snapshot: dict[str, Any] | None = None,
    contracts_root: str | Path | None = None,
) -> V5BuiltControllerRegistration:
    """Build one exact self-hashed v5 trust-root record without DB writes."""

    if revision < 1:
        raise V5AuthorityError("v5.authority.registration_revision_invalid")
    catalog = load_v5_contract_catalog(contracts_root)
    catalog_commands: set[str] = set()
    for rule in catalog.record_authority.values():
        if isinstance(rule, dict) and rule.get("owner") == owner:
            command_events = rule.get("command_events", {})
            if isinstance(command_events, dict):
                catalog_commands.update(str(item) for item in command_events)
    normalized_commands = sorted(set(allowed_commands))
    if (
        not normalized_commands
        or len(normalized_commands) != len(allowed_commands)
        or not set(normalized_commands) <= catalog_commands
    ):
        raise V5AuthorityError("v5.authority.registration_commands_invalid")
    if _as_utc(registered_at) < _as_utc(valid_from) or (
        expires_at is not None and _as_utc(expires_at) <= _as_utc(valid_from)
    ):
        raise V5AuthorityError("v5.authority.registration_validity_invalid")

    payload: dict[str, Any] = {
        "schema_version": "2.0",
        "controller_registration_id": controller_registration_id,
        "revision": revision,
        "previous_snapshot": previous_snapshot,
        "state": "ACTIVE",
        "workspace_id": workspace_id,
        "owner": owner,
        "controller_principal": controller_principal,
        "principal_type": "CONTROLLER_SERVICE",
        "allowed_commands": normalized_commands,
        "ownership_contract": {
            "version": "2.0",
            "digest": catalog.ownership_digest,
        },
        "event_catalog": {
            "version": "2.0",
            "digest": catalog.event_catalog_digest,
        },
        "valid_from": _wire_time(valid_from),
        "expires_at": _wire_time(expires_at) if expires_at is not None else None,
        "service_identity_digest": service_identity_digest,
        "registered_by_human_principal": registered_by_human_principal,
        "registration_audit_ref": registration_audit_ref,
        "registered_at": _wire_time(registered_at),
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/registration_digest)",
        "registration_digest": "",
    }
    digest = record_digest(payload, self_digest_field="registration_digest")
    payload["registration_digest"] = digest
    row_values = {
        "controller_registration_id": controller_registration_id,
        "revision": revision,
        "workspace_id": workspace_id,
        "previous_snapshot": previous_snapshot,
        "state": "ACTIVE",
        "owner": owner,
        "controller_principal": controller_principal,
        "allowed_commands": normalized_commands,
        "ownership_contract_digest": catalog.ownership_digest,
        "event_catalog_digest": catalog.event_catalog_digest,
        "valid_from": _as_utc(valid_from),
        "expires_at": _as_utc(expires_at) if expires_at is not None else None,
        "service_identity_digest": service_identity_digest,
        "registered_by_human_principal": registered_by_human_principal,
        "registration_audit_ref": registration_audit_ref,
        "registered_at": _as_utc(registered_at),
        "registration_payload": payload,
        "registration_digest": digest,
    }
    return V5BuiltControllerRegistration(
        payload=payload,
        registration_digest=digest,
        row_values=row_values,
    )


# V5 catalog subject kinds -> (model, id attr, digest attr, receipt attr, revision attr)
_V5_SUBJECT_BINDINGS: dict[str, tuple[type[Any], str, str, str, str | None]] = {
    "AI_APPLICATION": (
        AIApplication,
        "application_id",
        "record_digest",
        "authority_receipt_id",
        "revision",
    ),
    "ENVIRONMENT": (
        Environment,
        "environment_id",
        "record_digest",
        "authority_receipt_id",
        "revision",
    ),
    "SYSTEM_COMPONENT": (
        SystemComponent,
        "component_id",
        "record_digest",
        "authority_receipt_id",
        "revision",
    ),
    "DEPENDENCY_EDGE": (
        DependencyEdge,
        "edge_id",
        "record_digest",
        "authority_receipt_id",
        None,
    ),
    "COMPONENT_REVISION": (
        ComponentRevision,
        "component_revision_id",
        "record_digest",
        "authority_receipt_id",
        None,
    ),
    "TOPOLOGY_REVISION": (
        TopologyRevision,
        "topology_revision_id",
        "record_digest",
        "authority_receipt_id",
        None,
    ),
    "SYSTEM_VERSION_SET": (
        SystemVersionSet,
        "system_version_set_id",
        "record_digest",
        "authority_receipt_id",
        None,
    ),
    "BOOTSTRAP_ATTESTATION": (
        BootstrapAttestation,
        "bootstrap_attestation_id",
        "record_digest",
        "authority_receipt_id",
        None,
    ),
    "SYSTEM_ASSIGNMENT": (
        SystemAssignment,
        "assignment_id",
        "record_digest",
        "authority_receipt_id",
        "revision",
    ),
    "APPLICATION_CASE_BINDING": (
        ApplicationCaseBinding,
        "application_case_binding_id",
        "record_digest",
        "authority_receipt_id",
        "revision",
    ),
    "ACCEPTANCE_CRITERIA_REVISION": (
        AcceptanceCriteriaRevision,
        "acceptance_criteria_revision_id",
        "record_digest",
        "authority_receipt_id",
        "revision",
    ),
}

# Business fields that the registered event payload must carry, extracted from
# the subject envelope payload per subject kind.
_V5_EVENT_BUSINESS_FIELDS: dict[str, tuple[str, ...]] = {
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
        "exact_edge_revision_bindings",
        "topology_digest",
    ),
    "SYSTEM_VERSION_SET": (
        "system_version_set_id",
        "application_id",
        "declared_environment_id",
        "exact_component_revision_bindings",
        "exact_topology_revision_binding",
        "version_set_digest",
    ),
    "BOOTSTRAP_ATTESTATION": (
        "bootstrap_attestation_id",
        "application_id",
        "environment_id",
        "exact_initial_system_version_set_binding",
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

_V5_EVENT_SELF_BINDING_FIELD: dict[str, str] = {
    "AI_APPLICATION": "exact_application_binding",
    "ENVIRONMENT": "exact_environment_binding",
    "SYSTEM_COMPONENT": "exact_system_component_binding",
    "DEPENDENCY_EDGE": "exact_dependency_edge_binding",
    "COMPONENT_REVISION": "exact_component_revision_binding",
    "TOPOLOGY_REVISION": "exact_topology_revision_binding",
    "SYSTEM_VERSION_SET": "exact_system_version_set_binding",
    "BOOTSTRAP_ATTESTATION": "exact_bootstrap_attestation_binding",
    "SYSTEM_ASSIGNMENT": "exact_assignment_binding",
}

# V5-1C per-event business fields for case-controller records.  The propose and
# confirm events carry different field sets (per contracts/v5/events.yaml), so
# the exact extraction cannot be a single subject-kind set.  Each spec entry is
# ``(event_payload_field, envelope_field)``; ``None`` as the envelope field
# means the value is the derived exact subject binding (kind/id/revision/digest)
# built from the record identity + envelope digest.
_V5_EVENT_BUSINESS_FIELDS_BY_EVENT: dict[
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
        (
            "resolution_contract_binding_status",
            "resolution_contract_binding_status",
        ),
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
        (
            "resolution_contract_binding_status",
            "resolution_contract_binding_status",
        ),
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

_V5_EXACT_BINDING_ID_FIELD: dict[str, str] = {
    "AI_APPLICATION": "application_id",
    "ENVIRONMENT": "environment_id",
    "SYSTEM_COMPONENT": "component_id",
    "DEPENDENCY_EDGE": "edge_id",
    "COMPONENT_REVISION": "component_revision_id",
    "TOPOLOGY_REVISION": "topology_revision_id",
    "SYSTEM_VERSION_SET": "system_version_set_id",
    "BOOTSTRAP_ATTESTATION": "bootstrap_attestation_id",
    "SYSTEM_ASSIGNMENT": "assignment_id",
    "APPLICATION_CASE_BINDING": "application_case_binding_id",
    "ACCEPTANCE_CRITERIA_REVISION": "acceptance_criteria_revision_id",
}


def _derived_exact_subject_binding(
    subject_kind: str, envelope: dict[str, Any]
) -> dict[str, Any]:
    envelope_payload = envelope.get("record_envelope")
    id_field = _V5_EXACT_BINDING_ID_FIELD.get(subject_kind)
    if (
        not isinstance(envelope_payload, dict)
        or id_field is None
        or envelope.get(id_field) is None
        or not isinstance(envelope_payload.get("revision"), int)
        or isinstance(envelope_payload.get("revision"), bool)
        or envelope_payload["revision"] < 1
        or not isinstance(envelope_payload.get("record_digest"), str)
    ):
        raise V5AuthorityError("v5.authority.subject_binding_invalid")
    return {
        "kind": subject_kind,
        "id": envelope[id_field],
        "revision": envelope_payload["revision"],
        "digest": envelope_payload["record_digest"],
    }


class V5AuthorityService:
    def __init__(
        self, session: Session, *, contracts_root: str | Path | None = None
    ) -> None:
        self.session = session
        self.catalog = load_v5_contract_catalog(contracts_root)

    def resolve_controller(
        self,
        *,
        workspace_id: str,
        subject_kind: str,
        command: str,
        event_type: str,
        recorded_at: datetime,
    ) -> V5ResolvedController:
        rule = self.catalog.record_authority.get(subject_kind)
        if not isinstance(rule, dict):
            raise V5AuthorityError("v5.authority.subject_kind_not_registered")
        command_events = rule.get("command_events", {})
        if event_type not in command_events.get(command, []):
            raise V5AuthorityError("v5.authority.command_event_mapping_mismatch")
        resource = rule.get("resource")
        owner = rule.get("owner")
        if not isinstance(resource, str) or not isinstance(owner, str):
            raise V5AuthorityError("v5.authority.contract_catalog_invalid")

        rows = list(
            self.session.scalars(
                select(ControllerRegistration)
                .where(
                    ControllerRegistration.workspace_id == workspace_id,
                    ControllerRegistration.owner == owner,
                    ControllerRegistration.state == "ACTIVE",
                )
                .order_by(
                    ControllerRegistration.revision.desc(),
                    ControllerRegistration.controller_registration_id.asc(),
                )
                .with_for_update()
            ).all()
        )
        at = _as_utc(recorded_at)
        authorized = [
            row
            for row in rows
            if command in (row.allowed_commands or [])
            and _as_utc(row.valid_from) <= at
            and (row.expires_at is None or at < _as_utc(row.expires_at))
        ]
        if len(authorized) != 1:
            raise V5AuthorityError("v5.authority.registration_not_authorized")
        registration = authorized[0]
        self._validate_registration_at(registration, recorded_at=at)
        return V5ResolvedController(
            registration=registration,
            subject_kind=subject_kind,
            resource=resource,
            owner=owner,
            command=command,
            event_type=event_type,
        )

    def _validate_registration(self, row: ControllerRegistration) -> None:
        if (
            row.ownership_contract_digest != self.catalog.ownership_digest
            or row.event_catalog_digest != self.catalog.event_catalog_digest
        ):
            raise V5AuthorityError("v5.authority.catalog_digest_mismatch")
        payload = row.registration_payload or {}
        try:
            from app.utils.v4_integrity import assert_record_digest as _assert_reg_digest

            _assert_reg_digest(payload, self_digest_field="registration_digest")
        except V4IntegrityError as exc:
            raise V5AuthorityError("v5.authority.registration_integrity_invalid") from exc
        expected = {
            "schema_version": "2.0",
            "controller_registration_id": row.controller_registration_id,
            "revision": row.revision,
            "previous_snapshot": row.previous_snapshot,
            "state": row.state,
            "workspace_id": row.workspace_id,
            "owner": row.owner,
            "controller_principal": row.controller_principal,
            "principal_type": "CONTROLLER_SERVICE",
            "allowed_commands": row.allowed_commands,
            "ownership_contract": {
                "version": "2.0",
                "digest": self.catalog.ownership_digest,
            },
            "event_catalog": {
                "version": "2.0",
                "digest": self.catalog.event_catalog_digest,
            },
            "valid_from": _wire_time(row.valid_from),
            "expires_at": (
                _wire_time(row.expires_at) if row.expires_at is not None else None
            ),
            "service_identity_digest": row.service_identity_digest,
            "registered_by_human_principal": row.registered_by_human_principal,
            "registration_audit_ref": row.registration_audit_ref,
            "registered_at": _wire_time(row.registered_at),
            "immutable": True,
            "hash_rule": (
                "jcs-rfc8785-v1+sha256(excluding:/registration_digest)"
            ),
            "registration_digest": row.registration_digest,
        }
        if payload != expected:
            raise V5AuthorityError("v5.authority.registration_binding_mismatch")
        catalog_commands: set[str] = set()
        for rule in self.catalog.record_authority.values():
            if isinstance(rule, dict) and rule.get("owner") == row.owner:
                command_events = rule.get("command_events", {})
                if isinstance(command_events, dict):
                    catalog_commands.update(str(item) for item in command_events)
        if (
            row.revision < 1
            or (row.revision == 1) != (row.previous_snapshot is None)
            or not row.allowed_commands
            or row.allowed_commands != sorted(set(row.allowed_commands))
            or not set(row.allowed_commands) <= catalog_commands
            or _as_utc(row.registered_at) < _as_utc(row.valid_from)
            or (
                row.expires_at is not None
                and _as_utc(row.expires_at) <= _as_utc(row.valid_from)
            )
        ):
            raise V5AuthorityError("v5.authority.registration_semantics_invalid")

        audit_id = row.registration_audit_ref.removeprefix("audit://")
        registration_audit = self.session.get(Audit, audit_id)
        if not row.registration_audit_ref.startswith("audit://aud_"):
            raise V5AuthorityError("v5.authority.registration_audit_binding_mismatch")
        try:
            exact_registration_audit = validate_v4_audit_row(
                registration_audit,
                workspace_id=row.workspace_id,
                actor_principal=row.registered_by_human_principal,
                action="controllers.register",
                target=row.controller_registration_id,
                params={
                    "owner": row.owner,
                    "service_identity_digest": row.service_identity_digest,
                },
                result="success",
                error_code=None,
                transaction_id=(
                    registration_audit.transaction_id
                    if registration_audit is not None
                    else ""
                ),
                evidence_refs={
                    "owner": row.owner,
                    "controller_registration_id": row.controller_registration_id,
                    "controller_principal": row.controller_principal,
                },
            )
        except V4AuditIntegrityError as exc:
            raise V5AuthorityError(
                "v5.authority.registration_audit_binding_mismatch"
            ) from exc
        if _as_utc(row.registered_at) != _as_utc(exact_registration_audit.ts):
            raise V5AuthorityError("v5.authority.registration_audit_time_mismatch")

    def _validate_registration_at(
        self, row: ControllerRegistration, *, recorded_at: datetime
    ) -> None:
        self._validate_registration(row)
        at = _as_utc(recorded_at)
        if (
            row.state != "ACTIVE"
            or _as_utc(row.registered_at) > at
            or _as_utc(row.valid_from) > at
            or (row.expires_at is not None and at >= _as_utc(row.expires_at))
        ):
            raise V5AuthorityError("v5.authority.registration_not_authorized_at_receipt")

    def _validate_v5_subject(
        self,
        *,
        kind: str,
        workspace_id: str,
        subject_id: str,
        subject_revision: int | None,
        subject_digest: str,
        authority_receipt_id: str,
    ) -> Any:
        binding = _V5_SUBJECT_BINDINGS.get(kind)
        if binding is None:
            raise V5AuthorityError("v5.authority.subject_kind_not_implemented")
        model, id_attr, digest_attr, receipt_attr, revision_attr = binding
        row = self.session.get(model, subject_id)
        if row is None:
            raise V5AuthorityError("v5.authority.subject_missing")
        envelope = row.envelope_payload
        try:
            verified_digest = assert_v5_record_digest(envelope)
        except V4IntegrityError as exc:
            raise V5AuthorityError("v5.authority.subject_integrity_invalid") from exc
        record_envelope = envelope.get("record_envelope")
        if not isinstance(record_envelope, dict):
            raise V5AuthorityError("v5.authority.subject_binding_invalid")
        envelope_revision = record_envelope.get("revision")
        if (
            not isinstance(envelope_revision, int)
            or isinstance(envelope_revision, bool)
            or envelope_revision < 1
        ):
            raise V5AuthorityError("v5.authority.subject_binding_invalid")
        actual_revision = (
            getattr(row, revision_attr)
            if revision_attr is not None
            else envelope_revision
        )
        if (
            getattr(row, "workspace_id") != workspace_id
            or getattr(row, id_attr) != subject_id
            or getattr(row, digest_attr) != subject_digest
            or verified_digest != subject_digest
            or getattr(row, receipt_attr) != authority_receipt_id
            or actual_revision != subject_revision
            or envelope_revision != subject_revision
        ):
            raise V5AuthorityError("v5.authority.subject_binding_mismatch")
        return row

    def _validate_dependent_exact_binding(
        self,
        binding: Any,
        *,
        expected_kind: str,
        workspace_id: str,
    ) -> None:
        if (
            not isinstance(binding, dict)
            or set(binding) != {"kind", "id", "revision", "digest"}
            or binding.get("kind") != expected_kind
            or not isinstance(binding.get("id"), str)
            or not isinstance(binding.get("revision"), int)
            or isinstance(binding.get("revision"), bool)
            or binding["revision"] < 1
            or not isinstance(binding.get("digest"), str)
        ):
            raise V5AuthorityError("v5.authority.dependent_binding_invalid")
        subject_spec = _V5_SUBJECT_BINDINGS.get(expected_kind)
        if subject_spec is None:
            raise V5AuthorityError("v5.authority.subject_kind_not_implemented")
        model, _id_attr, _digest_attr, receipt_attr, _revision_attr = subject_spec
        row = self.session.get(model, binding["id"])
        if row is None:
            raise V5AuthorityError("v5.authority.dependent_binding_missing")
        receipt_id = getattr(row, receipt_attr)
        self._validate_v5_subject(
            kind=expected_kind,
            workspace_id=workspace_id,
            subject_id=binding["id"],
            subject_revision=binding["revision"],
            subject_digest=binding["digest"],
            authority_receipt_id=receipt_id,
        )
        self.validate_receipt_binding(
            authority_receipt_id=receipt_id,
            workspace_id=workspace_id,
            subject_kind=expected_kind,
            subject_id=binding["id"],
            subject_revision=binding["revision"],
            subject_digest=binding["digest"],
        )

    def _validate_v5_event_dependencies(
        self,
        *,
        subject_kind: str,
        workspace_id: str,
        expected_business: dict[str, Any],
    ) -> None:
        dependencies: list[tuple[Any, str]] = []
        if subject_kind == "COMPONENT_REVISION":
            dependencies.append(
                (expected_business.get("exact_system_component_binding"), "SYSTEM_COMPONENT")
            )
        elif subject_kind == "TOPOLOGY_REVISION":
            dependencies.extend(
                (binding, "DEPENDENCY_EDGE")
                for binding in expected_business.get("exact_edge_revision_bindings", [])
            )
        elif subject_kind == "SYSTEM_VERSION_SET":
            dependencies.extend(
                (binding, "COMPONENT_REVISION")
                for binding in expected_business.get(
                    "exact_component_revision_bindings", []
                )
            )
            dependencies.append(
                (
                    expected_business.get("exact_topology_revision_binding"),
                    "TOPOLOGY_REVISION",
                )
            )
        elif subject_kind == "BOOTSTRAP_ATTESTATION":
            dependencies.append(
                (
                    expected_business.get("exact_initial_system_version_set_binding"),
                    "SYSTEM_VERSION_SET",
                )
            )
        elif subject_kind == "SYSTEM_ASSIGNMENT":
            dependencies.extend(
                [
                    (
                        expected_business.get("exact_bootstrap_attestation_binding"),
                        "BOOTSTRAP_ATTESTATION",
                    ),
                    (
                        expected_business.get("exact_initial_system_version_set_binding"),
                        "SYSTEM_VERSION_SET",
                    ),
                ]
            )
        for binding, expected_kind in dependencies:
            self._validate_dependent_exact_binding(
                binding,
                expected_kind=expected_kind,
                workspace_id=workspace_id,
            )

    def _validate_v5_event_business_payload(
        self, event: Event, *, subject_kind: str, row: Any
    ) -> None:
        by_event = _V5_EVENT_BUSINESS_FIELDS_BY_EVENT.get(
            (subject_kind, event.event_type)
        )
        if by_event is not None:
            self._validate_v5_event_business_payload_by_event(
                event, subject_kind=subject_kind, row=row, spec=by_event
            )
            return
        fields = _V5_EVENT_BUSINESS_FIELDS.get(subject_kind)
        if fields is None:
            raise V5AuthorityError("v5.authority.subject_kind_not_implemented")
        envelope = row.envelope_payload
        expected_business = {
            field: envelope[field] for field in fields if field in envelope
        }
        if set(expected_business) != set(fields):
            raise V5AuthorityError("v5.authority.event_business_fields_mismatch")
        payload = event.payload or {}
        self_binding_field = _V5_EVENT_SELF_BINDING_FIELD.get(subject_kind)
        if self_binding_field is None:
            raise V5AuthorityError("v5.authority.event_self_binding_field_missing")
        expected_business[self_binding_field] = _derived_exact_subject_binding(
            subject_kind, envelope
        )
        if subject_kind == "COMPONENT_REVISION":
            exact_component = payload.get("exact_system_component_binding")
            if (
                not isinstance(exact_component, dict)
                or exact_component.get("id") != envelope.get("component_id")
            ):
                raise V5AuthorityError("v5.authority.event_dependency_binding_mismatch")
            expected_business["exact_system_component_binding"] = exact_component
        elif subject_kind == "SYSTEM_ASSIGNMENT":
            authority = envelope.get("exact_assignment_authority_binding")
            slots = envelope.get("exact_slot_version_set_bindings")
            if (
                not isinstance(authority, dict)
                or authority.get("binding_kind") != "BOOTSTRAP_ATTESTATION"
                or not isinstance(slots, list)
                or len(slots) != 1
            ):
                raise V5AuthorityError("v5.authority.event_dependency_binding_mismatch")
            expected_business["exact_bootstrap_attestation_binding"] = {
                "kind": authority.get("binding_kind"),
                "id": authority.get("id"),
                "revision": authority.get("revision"),
                "digest": authority.get("digest"),
            }
            expected_business["exact_initial_system_version_set_binding"] = slots[0]
        self._validate_v5_event_dependencies(
            subject_kind=subject_kind,
            workspace_id=row.workspace_id,
            expected_business=expected_business,
        )
        expected: dict[str, Any] = {
            **expected_business,
            "subject_kind": subject_kind,
            "subject_id": payload.get("subject_id"),
            "subject_revision": payload.get("subject_revision"),
            "subject_digest": payload.get("subject_digest"),
            "authority_receipt_id": payload.get("authority_receipt_id"),
        }
        if payload != expected:
            raise V5AuthorityError("v5.authority.event_binding_mismatch")
        if getattr(event, "exact_subject_binding", None) != _derived_exact_subject_binding(
            subject_kind, envelope
        ):
            raise V5AuthorityError("v5.authority.event_exact_subject_binding_mismatch")
        if event.correlation_id != envelope.get("application_id"):
            raise V5AuthorityError("v5.authority.event_correlation_mismatch")

    def _validate_v5_event_business_payload_by_event(
        self,
        event: Event,
        *,
        subject_kind: str,
        row: Any,
        spec: tuple[tuple[str, str | None], ...],
    ) -> None:
        """Per-event business-field validation for V5-1C case records.

        ``None`` envelope paths resolve to the derived exact subject binding
        (the self reference that cannot live inside the hashed envelope without
        creating a digest cycle).
        """

        envelope = row.envelope_payload
        expected_business: dict[str, Any] = {}
        for field_name, envelope_path in spec:
            if envelope_path is None:
                value = _derived_exact_subject_binding(subject_kind, envelope)
            else:
                if envelope_path not in envelope:
                    raise V5AuthorityError("v5.authority.event_business_fields_mismatch")
                value = envelope[envelope_path]
            expected_business[field_name] = value
        payload = event.payload or {}
        expected: dict[str, Any] = {
            **expected_business,
            "subject_kind": subject_kind,
            "subject_id": payload.get("subject_id"),
            "subject_revision": payload.get("subject_revision"),
            "subject_digest": payload.get("subject_digest"),
            "authority_receipt_id": payload.get("authority_receipt_id"),
        }
        if payload != expected:
            raise V5AuthorityError("v5.authority.event_binding_mismatch")
        if getattr(event, "exact_subject_binding", None) != _derived_exact_subject_binding(
            subject_kind, envelope
        ):
            raise V5AuthorityError("v5.authority.event_exact_subject_binding_mismatch")
        # Case records correlate on the exact case binding's case id.
        exact_case_binding = envelope.get("exact_case_binding")
        correlation_id = (
            exact_case_binding.get("case_id")
            if isinstance(exact_case_binding, dict)
            else None
        )
        if not isinstance(correlation_id, str) or event.correlation_id != correlation_id:
            raise V5AuthorityError("v5.authority.event_correlation_mismatch")

    def _validate_v5_controller_chain(
        self,
        *,
        resolved: V5ResolvedController,
        subject_row: Any,
        authority_receipt_id: str,
        workspace_id: str,
        subject_id: str,
        subject_revision: int | None,
        subject_digest: str,
        event_id: str,
        transaction_id: str,
        audit_ref: str,
        recorded_at: datetime,
    ) -> tuple[Event, Audit, Outbox]:
        event = self.session.get(Event, event_id)
        expected_evidence = {
            "subject_kind": resolved.subject_kind,
            "subject_id": subject_id,
            "subject_revision": subject_revision,
            "subject_digest": subject_digest,
            "event_id": event_id,
        }
        audit = (
            self.session.get(Audit, audit_ref.removeprefix("audit://"))
            if audit_ref.startswith("audit://aud_")
            else None
        )
        outboxes = list(
            self.session.scalars(
                select(Outbox).where(Outbox.source_event_id == event_id)
            ).all()
        )
        try:
            exact_event = validate_v4_event_row(
                event,
                workspace_id=workspace_id,
                event_type=resolved.event_type,
                transaction_id=transaction_id,
                actor_principal=resolved.controller_principal,
                subject_kind=resolved.subject_kind,
                subject_id=subject_id,
                subject_revision=subject_revision,
                subject_digest=subject_digest,
                authority_receipt_id=authority_receipt_id,
            )
            exact_audit = validate_v4_audit_row(
                audit,
                workspace_id=workspace_id,
                actor_principal=resolved.controller_principal,
                action=f"controller.{resolved.event_type}",
                target=subject_id,
                params={"command": resolved.command},
                result="success",
                error_code=None,
                transaction_id=transaction_id,
                evidence_refs=expected_evidence,
            )
            if len(outboxes) != 1:
                raise V4EventIntegrityError("v4.outbox_cardinality_mismatch")
            exact_outbox = validate_v4_outbox_row(outboxes[0], event=exact_event)
            chain_time = _as_utc(recorded_at)
            if any(
                _as_utc(value) != chain_time
                for value in (
                    exact_event.occurred_at,
                    exact_event.created_at,
                    exact_audit.ts,
                    exact_outbox.created_at,
                )
            ):
                raise V4EventIntegrityError("v4.controller_chain_time_mismatch")
            self._validate_v5_event_business_payload(
                exact_event, subject_kind=resolved.subject_kind, row=subject_row
            )
        except (V4AuditIntegrityError, V4EventIntegrityError) as exc:
            raise V5AuthorityError(
                "v5.authority.controller_chain_binding_mismatch"
            ) from exc
        assert audit is not None
        return exact_event, audit, outboxes[0]

    def record_receipt(
        self,
        *,
        resolved: V5ResolvedController,
        authority_receipt_id: str,
        workspace_id: str,
        subject_id: str,
        subject_revision: int | None,
        subject_digest: str,
        event_id: str,
        transaction_id: str,
        audit_ref: str,
        recorded_at: datetime,
    ) -> AuthorityReceipt:
        self._validate_registration_at(
            resolved.registration, recorded_at=recorded_at
        )
        subject_row = self._validate_v5_subject(
            kind=resolved.subject_kind,
            workspace_id=workspace_id,
            subject_id=subject_id,
            subject_revision=subject_revision,
            subject_digest=subject_digest,
            authority_receipt_id=authority_receipt_id,
        )
        self._validate_v5_controller_chain(
            resolved=resolved,
            subject_row=subject_row,
            authority_receipt_id=authority_receipt_id,
            workspace_id=workspace_id,
            subject_id=subject_id,
            subject_revision=subject_revision,
            subject_digest=subject_digest,
            event_id=event_id,
            transaction_id=transaction_id,
            audit_ref=audit_ref,
            recorded_at=recorded_at,
        )

        registration = resolved.registration
        payload: dict[str, Any] = {
            "schema_version": "2.0",
            "authority_receipt_id": authority_receipt_id,
            "workspace_id": workspace_id,
            "controller_registration": {
                "contract_major": 1,
                "kind": "CONTROLLER_REGISTRATION",
                "id": registration.controller_registration_id,
                "revision": registration.revision,
                "digest": registration.registration_digest,
            },
            "subject": {
                "kind": resolved.subject_kind,
                "id": subject_id,
                "revision": subject_revision,
                "digest": subject_digest,
            },
            "owner": resolved.owner,
            "controller_principal": registration.controller_principal,
            "command": resolved.command,
            "source_event_id": event_id,
            "transaction_id": transaction_id,
            "audit_ref": audit_ref,
            "recorded_at": _wire_time(recorded_at),
            "immutable": True,
            "hash_rule": (
                "jcs-rfc8785-v1+sha256(excluding:/authority_receipt_digest)"
            ),
            "authority_receipt_digest": "",
        }
        digest = record_digest(
            payload, self_digest_field="authority_receipt_digest"
        )
        payload["authority_receipt_digest"] = digest
        identity = v5_subject_identity_key(
            subject_kind=resolved.subject_kind,
            subject_id=subject_id,
            subject_revision=subject_revision,
        )
        row = AuthorityReceipt(
            authority_receipt_id=authority_receipt_id,
            workspace_id=workspace_id,
            controller_registration_id=registration.controller_registration_id,
            controller_registration_revision=registration.revision,
            controller_registration_digest=registration.registration_digest,
            subject_kind=resolved.subject_kind,
            subject_id=subject_id,
            subject_revision=subject_revision,
            subject_identity_key=identity,
            subject_digest=subject_digest,
            resource=resolved.resource,
            owner=resolved.owner,
            controller_principal=registration.controller_principal,
            command=resolved.command,
            event_type=resolved.event_type,
            event_id=event_id,
            transaction_id=transaction_id,
            audit_ref=audit_ref,
            recorded_at=recorded_at,
            receipt_payload=payload,
            authority_receipt_digest=digest,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def validate_receipt_binding(
        self,
        *,
        authority_receipt_id: str,
        workspace_id: str,
        subject_kind: str,
        subject_id: str,
        subject_revision: int | None,
        subject_digest: str,
    ) -> AuthorityReceipt:
        """Re-verify a durable v5 receipt before replaying an existing subject."""

        row = self.session.get(AuthorityReceipt, authority_receipt_id)
        if (
            row is None
            or row.workspace_id != workspace_id
            or row.subject_kind != subject_kind
            or row.subject_id != subject_id
            or row.subject_revision != subject_revision
            or row.subject_identity_key
            != v5_subject_identity_key(
                subject_kind=subject_kind,
                subject_id=subject_id,
                subject_revision=subject_revision,
            )
            or row.subject_digest != subject_digest
        ):
            raise V5AuthorityError("v5.authority.receipt_subject_binding_mismatch")
        registration = self.session.get(
            ControllerRegistration,
            (row.controller_registration_id, row.controller_registration_revision),
        )
        if (
            registration is None
            or registration.workspace_id != workspace_id
            or registration.registration_digest
            != row.controller_registration_digest
        ):
            raise V5AuthorityError("v5.authority.receipt_registration_binding_mismatch")
        self._validate_registration_at(registration, recorded_at=row.recorded_at)
        rule = self.catalog.record_authority.get(subject_kind)
        command_events = (
            rule.get("command_events", {}) if isinstance(rule, dict) else {}
        )
        if (
            not isinstance(rule, dict)
            or row.resource != rule.get("resource")
            or row.owner != rule.get("owner")
            or row.controller_principal != registration.controller_principal
            or row.command not in registration.allowed_commands
            or row.event_type not in command_events.get(row.command, [])
        ):
            raise V5AuthorityError("v5.authority.receipt_route_binding_mismatch")
        resolved = V5ResolvedController(
            registration=registration,
            subject_kind=row.subject_kind,
            resource=row.resource,
            owner=row.owner,
            command=row.command,
            event_type=row.event_type,
        )

        payload = row.receipt_payload or {}
        try:
            from app.utils.v4_integrity import assert_record_digest as _assert_receipt_digest

            _assert_receipt_digest(payload, self_digest_field="authority_receipt_digest")
        except V4IntegrityError as exc:
            raise V5AuthorityError("v5.authority.receipt_integrity_invalid") from exc
        expected = {
            "schema_version": "2.0",
            "authority_receipt_id": row.authority_receipt_id,
            "workspace_id": row.workspace_id,
            "controller_registration": {
                "contract_major": 1,
                "kind": "CONTROLLER_REGISTRATION",
                "id": row.controller_registration_id,
                "revision": row.controller_registration_revision,
                "digest": row.controller_registration_digest,
            },
            "subject": {
                "kind": row.subject_kind,
                "id": row.subject_id,
                "revision": row.subject_revision,
                "digest": row.subject_digest,
            },
            "owner": row.owner,
            "controller_principal": row.controller_principal,
            "command": row.command,
            "source_event_id": row.event_id,
            "transaction_id": row.transaction_id,
            "audit_ref": row.audit_ref,
            "recorded_at": _wire_time(row.recorded_at),
            "immutable": True,
            "hash_rule": (
                "jcs-rfc8785-v1+sha256(excluding:/authority_receipt_digest)"
            ),
            "authority_receipt_digest": row.authority_receipt_digest,
        }
        if payload != expected:
            raise V5AuthorityError("v5.authority.receipt_projection_binding_mismatch")

        subject_row = self._validate_v5_subject(
            kind=row.subject_kind,
            workspace_id=row.workspace_id,
            subject_id=row.subject_id,
            subject_revision=row.subject_revision,
            subject_digest=row.subject_digest,
            authority_receipt_id=row.authority_receipt_id,
        )
        self._validate_v5_controller_chain(
            resolved=resolved,
            subject_row=subject_row,
            authority_receipt_id=row.authority_receipt_id,
            workspace_id=row.workspace_id,
            subject_id=row.subject_id,
            subject_revision=row.subject_revision,
            subject_digest=row.subject_digest,
            event_id=row.event_id,
            transaction_id=row.transaction_id,
            audit_ref=row.audit_ref,
            recorded_at=row.recorded_at,
        )
        return row


__all__ = [
    "V5AuthorityError",
    "V5AuthorityService",
    "V5BuiltControllerRegistration",
    "V5ContractCatalog",
    "V5ResolvedController",
    "V5_CATALOG_OWNER",
    "build_v5_controller_registration_record",
    "discover_v5_contracts_root",
    "load_v5_contract_catalog",
]
