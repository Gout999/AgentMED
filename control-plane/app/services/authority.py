"""ControllerRegistration resolution and post-record AuthorityReceipt writing."""
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
from app.models.v4_tables import (
    AuthorityReceipt,
    ControllerRegistration,
    QualityCase,
    Signal,
    SignalCaseLink,
    TraceEvidenceReceipt,
)
from app.utils.v4_integrity import (
    V4IntegrityError,
    assert_record_digest,
    authority_subject_identity_key,
    canonical_digest,
    record_digest,
)
from app.services.v4_audit import V4AuditIntegrityError, validate_v4_audit_row
from app.services.v4_event_store import (
    V4EventIntegrityError,
    validate_stage1_event_semantics,
    validate_v4_event_row,
    validate_v4_outbox_row,
)


class AuthorityError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ContractCatalog:
    root: Path
    ownership_digest: str
    event_catalog_digest: str
    record_authority: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class BuiltControllerRegistration:
    payload: dict[str, Any]
    registration_digest: str
    row_values: dict[str, Any]


@dataclass(frozen=True)
class ResolvedController:
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


def _payload_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AuthorityError("authority.registration_binding_mismatch")
    try:
        return _as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError as exc:
        raise AuthorityError("authority.registration_binding_mismatch") from exc


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _normalize_contracts_root(candidate: Path) -> Path | None:
    options = (candidate, candidate / "contracts" / "v4")
    for root in options:
        if (root / "aggregate-ownership.yaml").is_file() and (
            root / "events" / "events.yaml"
        ).is_file():
            return root.resolve()
    return None


def discover_contracts_root(explicit: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(Path(explicit))
    module_repo = Path(__file__).resolve().parents[3]
    candidates.extend(
        [
            module_repo / "contracts" / "v4",
            Path("/srv/contracts/v4"),
            Path("/app/contracts/v4"),
        ]
    )
    for candidate in candidates:
        root = _normalize_contracts_root(candidate)
        if root is not None:
            return root
    raise AuthorityError("authority.contract_catalog_unavailable")


@lru_cache(maxsize=8)
def _load_contract_catalog_cached(root_text: str) -> ContractCatalog:
    root = Path(root_text)
    try:
        ownership = yaml.safe_load(
            (root / "aggregate-ownership.yaml").read_text(encoding="utf-8")
        )
        events = yaml.safe_load(
            (root / "events" / "events.yaml").read_text(encoding="utf-8")
        )
    except Exception as exc:  # noqa: BLE001 - stable fail-closed boundary
        raise AuthorityError("authority.contract_catalog_unavailable") from exc
    record_authority = ownership.get("record_authority")
    if not isinstance(record_authority, dict):
        raise AuthorityError("authority.contract_catalog_invalid")
    return ContractCatalog(
        root=root,
        ownership_digest=canonical_digest(ownership),
        event_catalog_digest=canonical_digest(events),
        record_authority=record_authority,
    )


def load_contract_catalog(explicit: str | Path | None = None) -> ContractCatalog:
    return _load_contract_catalog_cached(str(discover_contracts_root(explicit)))


def contract_catalog_digests(
    explicit: str | Path | None = None,
) -> tuple[str, str]:
    catalog = load_contract_catalog(explicit)
    return catalog.ownership_digest, catalog.event_catalog_digest


def build_controller_registration_record(
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
) -> BuiltControllerRegistration:
    """Build one exact self-hashed trust-root record without database writes."""

    if revision < 1:
        raise AuthorityError("authority.registration_revision_invalid")
    catalog = load_contract_catalog(contracts_root)
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
        raise AuthorityError("authority.registration_commands_invalid")
    if _as_utc(registered_at) < _as_utc(valid_from) or (
        expires_at is not None and _as_utc(expires_at) <= _as_utc(valid_from)
    ):
        raise AuthorityError("authority.registration_validity_invalid")

    payload: dict[str, Any] = {
        "schema_version": "1.0",
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
            "version": "1.0",
            "digest": catalog.ownership_digest,
        },
        "event_catalog": {
            "version": "1.0",
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
    return BuiltControllerRegistration(
        payload=payload,
        registration_digest=digest,
        row_values=row_values,
    )


class AuthorityService:
    def __init__(
        self, session: Session, *, contracts_root: str | Path | None = None
    ) -> None:
        self.session = session
        self.catalog = load_contract_catalog(contracts_root)

    def resolve_controller(
        self,
        *,
        workspace_id: str,
        subject_kind: str,
        command: str,
        event_type: str,
        recorded_at: datetime,
    ) -> ResolvedController:
        rule = self.catalog.record_authority.get(subject_kind)
        if not isinstance(rule, dict):
            raise AuthorityError("authority.subject_kind_not_registered")
        command_events = rule.get("command_events", {})
        if event_type not in command_events.get(command, []):
            raise AuthorityError("authority.command_event_mapping_mismatch")
        resource = rule.get("resource")
        owner = rule.get("owner")
        if not isinstance(resource, str) or not isinstance(owner, str):
            raise AuthorityError("authority.contract_catalog_invalid")

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
            raise AuthorityError("authority.registration_not_authorized")
        registration = authorized[0]
        self._validate_registration_at(registration, recorded_at=at)
        return ResolvedController(
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
            raise AuthorityError("authority.catalog_digest_mismatch")
        payload = row.registration_payload or {}
        try:
            assert_record_digest(
                payload, self_digest_field="registration_digest"
            )
        except V4IntegrityError as exc:
            raise AuthorityError("authority.registration_integrity_invalid") from exc
        expected = {
            "schema_version": "1.0",
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
                "version": "1.0",
                "digest": self.catalog.ownership_digest,
            },
            "event_catalog": {
                "version": "1.0",
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
            raise AuthorityError("authority.registration_binding_mismatch")
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
            raise AuthorityError("authority.registration_semantics_invalid")

        audit_id = row.registration_audit_ref.removeprefix("audit://")
        registration_audit = self.session.get(Audit, audit_id)
        if not row.registration_audit_ref.startswith("audit://aud_"):
            raise AuthorityError("authority.registration_audit_binding_mismatch")
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
            raise AuthorityError(
                "authority.registration_audit_binding_mismatch"
            ) from exc
        if _as_utc(row.registered_at) != _as_utc(exact_registration_audit.ts):
            raise AuthorityError("authority.registration_audit_time_mismatch")

    def _validate_registration_at(
        self, row: ControllerRegistration, *, recorded_at: datetime
    ) -> None:
        """Require a valid trust root at the immutable receipt timestamp."""

        self._validate_registration(row)
        at = _as_utc(recorded_at)
        if (
            row.state != "ACTIVE"
            or _as_utc(row.registered_at) > at
            or _as_utc(row.valid_from) > at
            or (row.expires_at is not None and at >= _as_utc(row.expires_at))
        ):
            raise AuthorityError("authority.registration_not_authorized_at_receipt")

    def _validate_controller_chain(
        self,
        *,
        resolved: ResolvedController,
        authority_receipt_id: str,
        workspace_id: str,
        subject_id: str,
        subject_revision: int | None,
        subject_digest: str,
        event_id: str,
        transaction_id: str,
        audit_ref: str,
        recorded_at: datetime,
        require_complete_stage1_graph: bool,
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
            validate_stage1_event_semantics(
                self.session,
                event=exact_event,
                controller_trace_id=exact_audit.trace_id,
                require_complete_graph=require_complete_stage1_graph,
            )
        except (V4AuditIntegrityError, V4EventIntegrityError) as exc:
            raise AuthorityError("authority.controller_chain_binding_mismatch") from exc
        assert audit is not None
        return exact_event, audit, outboxes[0]

    def record_receipt(
        self,
        *,
        resolved: ResolvedController,
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
        self._validate_subject(
            kind=resolved.subject_kind,
            workspace_id=workspace_id,
            subject_id=subject_id,
            subject_revision=subject_revision,
            subject_digest=subject_digest,
            authority_receipt_id=authority_receipt_id,
        )
        self._validate_controller_chain(
            resolved=resolved,
            authority_receipt_id=authority_receipt_id,
            workspace_id=workspace_id,
            subject_id=subject_id,
            subject_revision=subject_revision,
            subject_digest=subject_digest,
            event_id=event_id,
            transaction_id=transaction_id,
            audit_ref=audit_ref,
            recorded_at=recorded_at,
            require_complete_stage1_graph=False,
        )

        registration = resolved.registration
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "authority_receipt_id": authority_receipt_id,
            "workspace_id": workspace_id,
            "controller_registration": {
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
            "resource": resolved.resource,
            "owner": resolved.owner,
            "controller_principal": registration.controller_principal,
            "command": resolved.command,
            "event_type": resolved.event_type,
            "event_id": event_id,
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
        identity = authority_subject_identity_key(
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
        """Re-verify a durable receipt before replaying an existing subject."""

        row = self.session.get(AuthorityReceipt, authority_receipt_id)
        if (
            row is None
            or row.workspace_id != workspace_id
            or row.subject_kind != subject_kind
            or row.subject_id != subject_id
            or row.subject_revision != subject_revision
            or row.subject_identity_key
            != authority_subject_identity_key(
                subject_kind=subject_kind,
                subject_id=subject_id,
                subject_revision=subject_revision,
            )
            or row.subject_digest != subject_digest
        ):
            raise AuthorityError("authority.receipt_subject_binding_mismatch")
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
            raise AuthorityError("authority.receipt_registration_binding_mismatch")
        self._validate_registration_at(
            registration, recorded_at=row.recorded_at
        )
        rule = self.catalog.record_authority.get(subject_kind)
        command_events = rule.get("command_events", {}) if isinstance(rule, dict) else {}
        if (
            not isinstance(rule, dict)
            or row.resource != rule.get("resource")
            or row.owner != rule.get("owner")
            or row.controller_principal != registration.controller_principal
            or row.command not in registration.allowed_commands
            or row.event_type not in command_events.get(row.command, [])
        ):
            raise AuthorityError("authority.receipt_route_binding_mismatch")
        resolved = ResolvedController(
            registration=registration,
            subject_kind=row.subject_kind,
            resource=row.resource,
            owner=row.owner,
            command=row.command,
            event_type=row.event_type,
        )

        payload = row.receipt_payload or {}
        try:
            assert_record_digest(
                payload, self_digest_field="authority_receipt_digest"
            )
        except V4IntegrityError as exc:
            raise AuthorityError("authority.receipt_integrity_invalid") from exc
        expected = {
            "schema_version": "1.0",
            "authority_receipt_id": row.authority_receipt_id,
            "workspace_id": row.workspace_id,
            "controller_registration": {
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
            "resource": row.resource,
            "owner": row.owner,
            "controller_principal": row.controller_principal,
            "command": row.command,
            "event_type": row.event_type,
            "event_id": row.event_id,
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
            raise AuthorityError("authority.receipt_projection_binding_mismatch")

        self._validate_controller_chain(
            resolved=resolved,
            authority_receipt_id=row.authority_receipt_id,
            workspace_id=row.workspace_id,
            subject_id=row.subject_id,
            subject_revision=row.subject_revision,
            subject_digest=row.subject_digest,
            event_id=row.event_id,
            transaction_id=row.transaction_id,
            audit_ref=row.audit_ref,
            recorded_at=row.recorded_at,
            require_complete_stage1_graph=True,
        )
        return row

    def _validate_subject(
        self,
        *,
        kind: str,
        workspace_id: str,
        subject_id: str,
        subject_revision: int | None,
        subject_digest: str,
        authority_receipt_id: str,
    ) -> None:
        bindings: dict[str, tuple[type[Any], str, str, str, str | None]] = {
            "SIGNAL_RECORD": (
                Signal,
                "signal_id",
                "signal_digest",
                "authority_receipt_id",
                None,
            ),
            "QUALITY_CASE": (
                QualityCase,
                "case_id",
                "record_digest",
                "authority_receipt_id",
                "revision",
            ),
            "SIGNAL_CASE_LINK": (
                SignalCaseLink,
                "signal_case_link_id",
                "link_digest",
                "authority_receipt_id",
                "revision",
            ),
            "TRACE_EVIDENCE_RECEIPT": (
                TraceEvidenceReceipt,
                "receipt_id",
                "receipt_digest",
                "authority_receipt_id",
                None,
            ),
        }
        binding = bindings.get(kind)
        if binding is None:
            raise AuthorityError("authority.subject_kind_not_implemented")
        model, id_attr, digest_attr, receipt_attr, revision_attr = binding
        row = self.session.get(model, subject_id)
        actual_revision = getattr(row, revision_attr) if row is not None and revision_attr else None
        if (
            row is None
            or getattr(row, "workspace_id") != workspace_id
            or getattr(row, id_attr) != subject_id
            or getattr(row, digest_attr) != subject_digest
            or getattr(row, receipt_attr) != authority_receipt_id
            or actual_revision != subject_revision
        ):
            raise AuthorityError("authority.subject_binding_mismatch")


__all__ = [
    "AuthorityError",
    "AuthorityService",
    "BuiltControllerRegistration",
    "ContractCatalog",
    "ResolvedController",
    "build_controller_registration_record",
    "contract_catalog_digests",
    "discover_contracts_root",
    "load_contract_catalog",
]
