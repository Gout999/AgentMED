"""Durable public-command idempotency, separate from connector event dedupe."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.v4_tables import PublicCommandIdempotency
from app.public_api.models import IdempotencyReceipt, SignalSubmissionResponse
from app.utils.ids import new_idempotency_record_id
from app.utils.v4_integrity import (
    V4IntegrityError,
    assert_record_digest,
    canonical_digest,
)


class PublicIdempotencyError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class IdempotencyLookup:
    record: PublicCommandIdempotency | None
    request_fingerprint: str

    @property
    def is_replay(self) -> bool:
        return self.record is not None


class PublicIdempotencyService:
    def __init__(self, session: Session, *, retention_days: int = 3650) -> None:
        self.session = session
        self.retention_days = retention_days

    @staticmethod
    def fingerprint(request_payload: Any) -> str:
        return canonical_digest(request_payload)

    def acquire(
        self,
        *,
        workspace_id: str,
        principal_id: str,
        intent: str,
        idempotency_key: str,
        request_fingerprint: str,
        verify_terminal: Callable[[PublicCommandIdempotency], None] | None = None,
    ) -> IdempotencyLookup:
        if not 8 <= len(idempotency_key) <= 128:
            raise PublicIdempotencyError("IDEMPOTENCY_KEY_REQUIRED")
        namespace = "\x1f".join(
            (workspace_id, principal_id, intent, idempotency_key)
        )
        dialect = self.session.get_bind().dialect.name
        if dialect == "postgresql":
            lock_id = int.from_bytes(
                hashlib.sha256(namespace.encode("utf-8")).digest()[:8],
                "big",
                signed=True,
            )
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
        elif dialect != "sqlite":
            raise PublicIdempotencyError("DEPENDENCY_UNAVAILABLE")

        row = self.session.scalar(
            select(PublicCommandIdempotency)
            .where(
                PublicCommandIdempotency.workspace_id == workspace_id,
                PublicCommandIdempotency.principal_id == principal_id,
                PublicCommandIdempotency.intent == intent,
                PublicCommandIdempotency.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        if row is None:
            return IdempotencyLookup(None, request_fingerprint)
        if row.request_fingerprint != request_fingerprint:
            raise PublicIdempotencyError("IDEMPOTENCY_CONFLICT")
        if row.state not in {"ACCEPTED", "COMPLETED"}:
            raise PublicIdempotencyError("DEPENDENCY_UNAVAILABLE")
        if verify_terminal is not None:
            verify_terminal(row)
        else:
            if row.state != "COMPLETED":
                raise PublicIdempotencyError("INTERNAL_ERROR")
            self._verify_terminal(row)
        return IdempotencyLookup(row, request_fingerprint)

    def replay_signal_response(
        self, record: PublicCommandIdempotency
    ) -> SignalSubmissionResponse:
        self._verify_terminal(record)
        assert record.response_payload is not None
        assert record.receipt_payload is not None
        return SignalSubmissionResponse.model_validate(
            {
                **record.response_payload,
                "idempotency": {
                    "receipt": record.receipt_payload,
                    "replayed": True,
                },
            }
        )

    def store_completed(
        self,
        *,
        workspace_id: str,
        principal_id: str,
        intent: str,
        idempotency_key: str,
        request_fingerprint: str,
        resource_kind: str,
        resource_id: str,
        request_id: str,
        audit_ref: str,
        response_payload: dict[str, Any],
        response_digest: str,
        receipt_payload: dict[str, Any],
        receipt_digest: str,
        idempotency_receipt_id: str,
        completed_at: datetime,
    ) -> PublicCommandIdempotency:
        row = PublicCommandIdempotency(
            idempotency_record_id=new_idempotency_record_id(),
            workspace_id=workspace_id,
            principal_id=principal_id,
            intent=intent,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            state="COMPLETED",
            resource_kind=resource_kind,
            resource_id=resource_id,
            operation_id=None,
            request_id=request_id,
            audit_ref=audit_ref,
            response_payload=response_payload,
            response_digest=response_digest,
            idempotency_receipt_id=idempotency_receipt_id,
            receipt_payload=receipt_payload,
            receipt_digest=receipt_digest,
            created_at=completed_at,
            completed_at=completed_at,
            expires_at=completed_at + timedelta(days=self.retention_days),
        )
        self._verify_terminal(row)
        self.session.add(row)
        self.session.flush()
        return row

    # --- V5 catalog intents: generic terminal verification and replay. -------
    # The frozen ``IdempotencyReceipt`` validator knows only the v1 intents, so
    # the v5 transports validate their receipt payloads with a v5 receipt model
    # and a generic verifier instead of the signal-bound one below.

    @staticmethod
    def _verify_terminal_catalog(
        row: PublicCommandIdempotency,
        *,
        response_model: type[BaseModel],
        receipt_model: type[BaseModel],
        resource_kind: str,
        resource_field: str,
        resource_id_field: str,
        expected_state: str = "COMPLETED",
    ) -> None:
        if (
            row.state != expected_state
            or row.response_payload is None
            or row.response_digest is None
            or row.resource_kind is None
            or row.resource_id is None
            or row.audit_ref is None
            or row.idempotency_receipt_id is None
            or row.receipt_payload is None
            or row.receipt_digest is None
        ):
            raise PublicIdempotencyError("INTERNAL_ERROR")
        try:
            if canonical_digest(row.response_payload) != row.response_digest:
                raise PublicIdempotencyError("INTERNAL_ERROR")
            assert_record_digest(
                row.receipt_payload, self_digest_field="receipt_digest"
            )
            receipt = receipt_model.model_validate(row.receipt_payload)
        except PublicIdempotencyError:
            raise
        except (V4IntegrityError, ValidationError, TypeError, ValueError) as exc:
            raise PublicIdempotencyError("INTERNAL_ERROR") from exc

        created_at = _as_utc(row.created_at)
        completed_at = _as_utc(row.completed_at)
        receipt_created_at = _as_utc(receipt.created_at)
        receipt_matches_row = (
            receipt.workspace_id == row.workspace_id
            and receipt.principal_id == row.principal_id
            and receipt.intent == row.intent
            and receipt.idempotency_key == row.idempotency_key
            and receipt.request_fingerprint == row.request_fingerprint
            and receipt.resource.kind == row.resource_kind
            and receipt.resource.id == row.resource_id
            and receipt.operation_id == row.operation_id
            and receipt.request_id == row.request_id
            and receipt.audit_ref == row.audit_ref
            and receipt.status == row.state
            and receipt.response_digest == row.response_digest
            and receipt.idempotency_receipt_id == row.idempotency_receipt_id
            and receipt.receipt_digest == row.receipt_digest
            and receipt.immutable is True
            and created_at is not None
            and completed_at is not None
            and created_at == completed_at == receipt_created_at
        )
        if not receipt_matches_row:
            raise PublicIdempotencyError("INTERNAL_ERROR")
        if receipt.resource.kind != resource_kind:
            raise PublicIdempotencyError("INTERNAL_ERROR")
        if "idempotency" in row.response_payload:
            raise PublicIdempotencyError("INTERNAL_ERROR")
        try:
            response = response_model.model_validate(
                {
                    **row.response_payload,
                    "idempotency": {
                        "receipt": row.receipt_payload,
                        "replayed": False,
                    },
                }
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise PublicIdempotencyError("INTERNAL_ERROR") from exc

        resource = getattr(response, resource_field)
        resource_id = getattr(resource, resource_id_field)
        if (
            response.model_dump(mode="json", exclude={"idempotency"})
            != row.response_payload
            or response.idempotency.receipt.model_dump(mode="json")
            != row.receipt_payload
            or response.workspace_id != row.workspace_id
            or response.request_id != row.request_id
            or response.audit_ref != row.audit_ref
            or resource_id != row.resource_id
            or response.idempotency.receipt.resource.kind != row.resource_kind
            or response.idempotency.receipt.response_digest != row.response_digest
            or response.idempotency.receipt.receipt_digest != row.receipt_digest
            or response.idempotency.receipt.immutable is not True
        ):
            raise PublicIdempotencyError("INTERNAL_ERROR")

    def replay_catalog_response(
        self,
        record: PublicCommandIdempotency,
        *,
        response_model: type[BaseModel],
        receipt_model: type[BaseModel],
        resource_kind: str,
        resource_field: str,
        resource_id_field: str,
        expected_state: str = "COMPLETED",
    ) -> BaseModel:
        self._verify_terminal_catalog(
            record,
            response_model=response_model,
            receipt_model=receipt_model,
            resource_kind=resource_kind,
            resource_field=resource_field,
            resource_id_field=resource_id_field,
            expected_state=expected_state,
        )
        assert record.response_payload is not None
        assert record.receipt_payload is not None
        return response_model.model_validate(
            {
                **record.response_payload,
                "idempotency": {
                    "receipt": record.receipt_payload,
                    "replayed": True,
                },
            }
        )

    def store_completed_catalog(
        self,
        *,
        workspace_id: str,
        principal_id: str,
        intent: str,
        idempotency_key: str,
        request_fingerprint: str,
        resource_kind: str,
        resource_id: str,
        request_id: str,
        audit_ref: str,
        response_payload: dict[str, Any],
        response_digest: str,
        receipt_payload: dict[str, Any],
        receipt_digest: str,
        idempotency_receipt_id: str,
        completed_at: datetime,
        response_model: type[BaseModel],
        receipt_model: type[BaseModel],
        resource_field: str,
        resource_id_field: str,
        operation_id: str | None = None,
        state: str = "COMPLETED",
    ) -> PublicCommandIdempotency:
        row = PublicCommandIdempotency(
            idempotency_record_id=new_idempotency_record_id(),
            workspace_id=workspace_id,
            principal_id=principal_id,
            intent=intent,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            state=state,
            resource_kind=resource_kind,
            resource_id=resource_id,
            operation_id=operation_id,
            request_id=request_id,
            audit_ref=audit_ref,
            response_payload=response_payload,
            response_digest=response_digest,
            idempotency_receipt_id=idempotency_receipt_id,
            receipt_payload=receipt_payload,
            receipt_digest=receipt_digest,
            created_at=completed_at,
            completed_at=completed_at,
            expires_at=completed_at + timedelta(days=self.retention_days),
        )
        self._verify_terminal_catalog(
            row,
            response_model=response_model,
            receipt_model=receipt_model,
            resource_kind=resource_kind,
            resource_field=resource_field,
            resource_id_field=resource_id_field,
            expected_state=state,
        )
        self.session.add(row)
        self.session.flush()
        return row

    @staticmethod
    def verify_terminal_presence(row: PublicCommandIdempotency) -> None:
        """Light terminal check for non-v1 intents; full verification happens in
        the v5 replay/verify path with the intent-specific receipt model."""

        if (
            row.state not in {"ACCEPTED", "COMPLETED"}
            or row.response_payload is None
            or row.response_digest is None
            or row.resource_kind is None
            or row.resource_id is None
            or row.audit_ref is None
            or row.idempotency_receipt_id is None
            or row.receipt_payload is None
            or row.receipt_digest is None
        ):
            raise PublicIdempotencyError("INTERNAL_ERROR")

    @staticmethod
    def _verify_terminal(row: PublicCommandIdempotency) -> None:
        if (
            row.state != "COMPLETED"
            or row.response_payload is None
            or row.response_digest is None
            or row.resource_kind is None
            or row.resource_id is None
            or row.audit_ref is None
            or row.idempotency_receipt_id is None
            or row.receipt_payload is None
            or row.receipt_digest is None
        ):
            raise PublicIdempotencyError("INTERNAL_ERROR")
        try:
            if canonical_digest(row.response_payload) != row.response_digest:
                raise PublicIdempotencyError("INTERNAL_ERROR")
            assert_record_digest(
                row.receipt_payload, self_digest_field="receipt_digest"
            )
            receipt = IdempotencyReceipt.model_validate(row.receipt_payload)
        except PublicIdempotencyError:
            raise
        except (V4IntegrityError, ValidationError, TypeError, ValueError) as exc:
            raise PublicIdempotencyError("INTERNAL_ERROR") from exc

        created_at = _as_utc(row.created_at)
        completed_at = _as_utc(row.completed_at)
        receipt_created_at = _as_utc(receipt.created_at)
        receipt_matches_row = (
            receipt.workspace_id == row.workspace_id
            and receipt.principal_id == row.principal_id
            and receipt.intent == row.intent
            and receipt.idempotency_key == row.idempotency_key
            and receipt.request_fingerprint == row.request_fingerprint
            and receipt.resource.kind == row.resource_kind
            and receipt.resource.id == row.resource_id
            and receipt.operation_id == row.operation_id
            and receipt.request_id == row.request_id
            and receipt.audit_ref == row.audit_ref
            and receipt.status == row.state
            and receipt.response_digest == row.response_digest
            and receipt.idempotency_receipt_id == row.idempotency_receipt_id
            and receipt.receipt_digest == row.receipt_digest
            and receipt.immutable is True
            and created_at is not None
            and completed_at is not None
            and created_at == completed_at == receipt_created_at
        )
        if not receipt_matches_row:
            raise PublicIdempotencyError("INTERNAL_ERROR")

        if "idempotency" in row.response_payload:
            raise PublicIdempotencyError("INTERNAL_ERROR")
        try:
            response = SignalSubmissionResponse.model_validate(
                {
                    **row.response_payload,
                    "idempotency": {
                        "receipt": row.receipt_payload,
                        "replayed": False,
                    },
                }
            )
        except (ValidationError, TypeError, ValueError) as exc:
            raise PublicIdempotencyError("INTERNAL_ERROR") from exc

        # The authoritative row, base response, and immutable receipt are three
        # representations of one result.  Each boundary is exact so a valid,
        # self-hashed receipt or response from another command cannot be swapped
        # into this row and replayed under its identity.
        if (
            response.model_dump(mode="json", exclude={"idempotency"})
            != row.response_payload
            or response.idempotency.receipt.model_dump(mode="json")
            != row.receipt_payload
            or response.workspace_id != row.workspace_id
            or response.request_id != row.request_id
            or response.audit_ref != row.audit_ref
            or response.signal.signal_id != row.resource_id
            or response.idempotency.receipt.resource.kind != row.resource_kind
            or response.idempotency.receipt.response_digest
            != row.response_digest
            or response.idempotency.receipt.receipt_digest
            != row.receipt_digest
            or response.idempotency.receipt.immutable is not True
        ):
            raise PublicIdempotencyError("INTERNAL_ERROR")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = [
    "IdempotencyLookup",
    "PublicIdempotencyError",
    "PublicIdempotencyService",
]
