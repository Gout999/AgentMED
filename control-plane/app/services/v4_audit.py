"""PostgreSQL-only authoritative audit writer for public v4 services."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Audit
from app.utils.ids import new_audit_id, new_trace_id, new_transaction_id
from app.utils.v4_integrity import canonical_digest, record_digest


Clock = Callable[[], datetime]


class V4AuditUnavailable(RuntimeError):
    """The authoritative audit row could not be flushed; caller must roll back."""


class V4AuditIntegrityError(ValueError):
    """A persisted v4 audit row is missing, tampered, or rebound."""


@dataclass(frozen=True)
class RecordedV4Audit:
    row: Audit
    audit_ref: str
    transaction_id: str
    audit_digest: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _wire_time(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def audit_ref_for(audit_id: str) -> str:
    return f"audit://{audit_id}"


def validate_v4_audit_row(
    row: Audit | None,
    *,
    workspace_id: str,
    actor_principal: str,
    action: str,
    target: str,
    params: Any,
    result: str,
    error_code: str | None,
    transaction_id: str,
    evidence_refs: dict[str, Any] | None,
) -> Audit:
    """Rebuild the immutable audit record and verify its exact business binding."""

    expected_params_digest = canonical_digest(params if params is not None else {})
    if (
        row is None
        or row.contract_version != "v4"
        or row.workspace_id != workspace_id
        or row.actor != actor_principal
        or row.actor_principal != actor_principal
        or row.action != action
        or row.target != target
        or row.params_digest != expected_params_digest
        or row.result != result
        or row.error_code != error_code
        or row.transaction_id != transaction_id
        or row.evidence_refs != evidence_refs
        or not isinstance(row.audit_id, str)
        or not row.audit_id.startswith("aud_")
        or not isinstance(row.ts, datetime)
        or not isinstance(row.transaction_id, str)
        or not row.transaction_id
        or not isinstance(row.trace_id, str)
        or not row.trace_id
        or not isinstance(row.audit_digest, str)
    ):
        raise V4AuditIntegrityError("v4.audit_binding_mismatch")
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "audit_id": row.audit_id,
        "workspace_id": row.workspace_id,
        "actor_principal": row.actor_principal,
        "action": row.action,
        "target": row.target,
        "params_digest": row.params_digest,
        "result": row.result,
        "error_code": row.error_code,
        "trace_id": row.trace_id,
        "transaction_id": row.transaction_id,
        "evidence_refs": row.evidence_refs,
        "recorded_at": _wire_time(row.ts),
        "immutable": True,
        "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/audit_digest)",
        "audit_digest": row.audit_digest,
    }
    if record_digest(payload, self_digest_field="audit_digest") != row.audit_digest:
        raise V4AuditIntegrityError("v4.audit_digest_mismatch")
    return row


class V4AuditService:
    """Write one v4 audit row without commit, rollback, or pre-commit export."""

    def __init__(
        self,
        session: Session,
        *,
        clock: Clock | None = None,
        force_fail: bool | None = None,
        fail_on_call: int | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or _utc_now
        self.force_fail = (
            get_settings().audit_force_fail if force_fail is None else force_fail
        )
        self.fail_on_call = fail_on_call
        self._calls = 0

    def record(
        self,
        *,
        workspace_id: str,
        actor_principal: str,
        action: str,
        target: str,
        params: Any,
        result: str = "success",
        error_code: str | None = None,
        transaction_id: str | None = None,
        trace_id: str | None = None,
        evidence_refs: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> RecordedV4Audit:
        self._calls += 1
        if self.force_fail or (
            self.fail_on_call is not None and self._calls == self.fail_on_call
        ):
            raise V4AuditUnavailable("AUDIT_UNAVAILABLE")

        recorded_at = occurred_at or self.clock()
        transaction = transaction_id or new_transaction_id()
        audit_id = new_audit_id()
        request_trace = trace_id or new_trace_id()
        params_digest = canonical_digest(params if params is not None else {})
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "audit_id": audit_id,
            "workspace_id": workspace_id,
            "actor_principal": actor_principal,
            "action": action,
            "target": target,
            "params_digest": params_digest,
            "result": result,
            "error_code": error_code,
            "trace_id": request_trace,
            "transaction_id": transaction,
            "evidence_refs": evidence_refs,
            "recorded_at": _wire_time(recorded_at),
            "immutable": True,
            "hash_rule": "jcs-rfc8785-v1+sha256(excluding:/audit_digest)",
            "audit_digest": "",
        }
        digest = record_digest(payload, self_digest_field="audit_digest")

        row = Audit(
            audit_id=audit_id,
            ts=recorded_at,
            actor=actor_principal,
            action=action,
            target=target,
            params_digest=params_digest,
            result=result,
            error_code=error_code,
            trace_id=request_trace,
            evidence_refs=evidence_refs,
            contract_version="v4",
            workspace_id=workspace_id,
            transaction_id=transaction,
            actor_principal=actor_principal,
            audit_digest=digest,
        )
        try:
            self.session.add(row)
            self.session.flush()
        except Exception as exc:  # noqa: BLE001 - stable fail-closed boundary
            raise V4AuditUnavailable("AUDIT_UNAVAILABLE") from exc
        return RecordedV4Audit(
            row=row,
            audit_ref=audit_ref_for(audit_id),
            transaction_id=transaction,
            audit_digest=digest,
        )


__all__ = [
    "RecordedV4Audit",
    "V4AuditService",
    "V4AuditIntegrityError",
    "V4AuditUnavailable",
    "audit_ref_for",
    "validate_v4_audit_row",
]
