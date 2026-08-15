"""Server-side resolver for independent opaque public bearer credentials."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.v4_tables import PublicCredential, PublicPrincipal
from app.public_api.auth_contract import (
    AcceptedPrincipalContext,
    RequestedPrincipalContext,
)
from app.utils.v4_integrity import V4IntegrityError, canonical_digest


_HASH_ALGORITHM = "hmac-sha256-v1"
_PUBLIC_AUDIENCE = "caseloop-public-api"
_DUMMY_CREDENTIAL_HASH = "sha256:" + "0" * 64


_ERROR_MESSAGES = {
    "TOKEN_INVALID": "The opaque bearer credential is invalid.",
    "TOKEN_EXPIRED": "The opaque bearer credential has expired.",
    "TOKEN_NOT_YET_VALID": "The opaque bearer credential is not yet valid.",
    "TOKEN_REVOKED": "The opaque bearer credential has been revoked.",
    "AUDIENCE_MISMATCH": "The opaque bearer audience is not accepted.",
    "ISSUER_MISMATCH": "The opaque bearer issuer is not accepted.",
    "WORKSPACE_ACCESS_DENIED": "The principal is not bound to the requested workspace or resource context.",
    "SCOPE_FORBIDDEN": "The principal lacks the required public scope.",
    "DEPENDENCY_UNAVAILABLE": "Public credential resolution is not configured.",
}


@dataclass(frozen=True)
class CredentialResolutionError(Exception):
    """Secret-safe failure with optional already-resolved workspace context."""

    code: str
    workspace_id: str | None = None
    details: dict[str, object] = field(default_factory=dict)
    audit_ref: None = None
    rollback_required: bool = True

    def __str__(self) -> str:
        return _ERROR_MESSAGES.get(self.code, "The public credential could not be resolved.")


def _secret_value(value: SecretStr | str | bytes) -> bytes:
    if isinstance(value, SecretStr):
        return value.get_secret_value().encode("utf-8")
    if isinstance(value, bytes):
        return value
    return value.encode("utf-8")


def hash_opaque_bearer(
    bearer_token: SecretStr | str,
    pepper: SecretStr | str | bytes,
) -> str:
    """Return the DB lookup digest without persisting or echoing raw bearer data."""

    pepper_bytes = _secret_value(pepper)
    if not pepper_bytes:
        raise ValueError("public credential hash pepper is required")
    token_bytes = _secret_value(bearer_token)
    digest = hmac.new(pepper_bytes, token_bytes, hashlib.sha256).hexdigest()
    return f"sha256:{digest}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def digest_public_subject(subject: str) -> str:
    """Canonical digest stored on ``PublicPrincipal`` for credential binding."""

    return "sha256:" + hashlib.sha256(subject.encode("utf-8")).hexdigest()


def _canonical_claims_digest(
    *,
    issuer: str,
    subject: str,
    principal_type: str,
    audiences: list[str],
    workspace_id: str,
    project_ids: list[str],
    environment_ids: list[str],
    scopes: list[str],
) -> str:
    """Recreate the exact Stage 1A bootstrap claims digest."""

    return canonical_digest(
        {
            "schema_version": "1.0",
            "issuer": issuer,
            "subject": subject,
            "principal_type": principal_type,
            "audiences": audiences,
            "workspace_id": workspace_id,
            "project_ids": project_ids,
            "environment_ids": environment_ids,
            "scopes": scopes,
        }
    )


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _exact_grants_match(
    credential: PublicCredential,
    principal: PublicPrincipal,
) -> bool:
    grant_values = (
        credential.audiences,
        credential.project_ids,
        credential.environment_ids,
        credential.scopes,
        principal.audiences,
        principal.project_ids,
        principal.environment_ids,
        principal.scopes,
    )
    if not all(_string_list(value) for value in grant_values):
        return False
    if not all(
        isinstance(value, str) and value
        for value in (
            credential.issuer,
            credential.subject,
            credential.workspace_id,
            credential.principal_id,
            credential.claims_digest,
            principal.workspace_id,
            principal.principal_id,
            principal.principal_type,
            principal.subject_digest,
            principal.claims_digest,
        )
    ):
        return False

    try:
        credential_claims_digest = _canonical_claims_digest(
            issuer=credential.issuer,
            subject=credential.subject,
            principal_type=principal.principal_type,
            audiences=credential.audiences,
            workspace_id=credential.workspace_id,
            project_ids=credential.project_ids,
            environment_ids=credential.environment_ids,
            scopes=credential.scopes,
        )
        principal_claims_digest = _canonical_claims_digest(
            issuer=credential.issuer,
            subject=credential.subject,
            principal_type=principal.principal_type,
            audiences=principal.audiences,
            workspace_id=principal.workspace_id,
            project_ids=principal.project_ids,
            environment_ids=principal.environment_ids,
            scopes=principal.scopes,
        )
        subject_digest = digest_public_subject(credential.subject)
        digest_matches = all(
            (
                hmac.compare_digest(
                    credential.claims_digest, principal.claims_digest
                ),
                hmac.compare_digest(
                    credential.claims_digest, credential_claims_digest
                ),
                hmac.compare_digest(
                    principal.claims_digest, principal_claims_digest
                ),
                hmac.compare_digest(
                    credential_claims_digest, principal_claims_digest
                ),
                hmac.compare_digest(subject_digest, principal.subject_digest),
            )
        )
    except (AttributeError, TypeError, UnicodeError, V4IntegrityError):
        return False

    return all(
        (
            credential.workspace_id == principal.workspace_id,
            credential.principal_id == principal.principal_id,
            credential.audiences == principal.audiences,
            credential.project_ids == principal.project_ids,
            credential.environment_ids == principal.environment_ids,
            credential.scopes == principal.scopes,
            digest_matches,
        )
    )


class PublicCredentialResolver:
    """Resolve one opaque bearer into an exact accepted principal context."""

    def __init__(
        self,
        session: Session,
        *,
        hash_pepper: SecretStr | str | bytes,
        expected_issuer: str,
    ) -> None:
        self.session = session
        self.hash_pepper = hash_pepper
        self.expected_issuer = expected_issuer

    def resolve(
        self,
        bearer_token: SecretStr,
        *,
        requested_workspace_id: str,
        required_scope: str,
        project_id: str | None = None,
        environment_id: str | None = None,
        evaluated_at: datetime | None = None,
    ) -> AcceptedPrincipalContext:
        if not _secret_value(self.hash_pepper) or not self.expected_issuer:
            raise CredentialResolutionError("DEPENDENCY_UNAVAILABLE")

        token_bytes = _secret_value(bearer_token)
        if not token_bytes:
            candidate_hash = _DUMMY_CREDENTIAL_HASH
        else:
            candidate_hash = hash_opaque_bearer(bearer_token, self.hash_pepper)
        credential = self.session.execute(
            select(PublicCredential).where(
                PublicCredential.credential_hash == candidate_hash
            )
        ).scalar_one_or_none()

        # The comparison always runs, including the unknown-hash path.  The DB
        # remains indexed; no full-table token scan is introduced.
        stored_hash = (
            credential.credential_hash
            if credential is not None
            else _DUMMY_CREDENTIAL_HASH
        )
        hash_matches = hmac.compare_digest(candidate_hash, stored_hash)
        if credential is None or not hash_matches or not token_bytes:
            raise CredentialResolutionError("TOKEN_INVALID")
        if credential.hash_algorithm != _HASH_ALGORITHM:
            raise CredentialResolutionError("TOKEN_INVALID")

        now = _as_utc(evaluated_at or datetime.now(timezone.utc))
        if not all(
            _string_list(value)
            for value in (
                credential.audiences,
                credential.project_ids,
                credential.environment_ids,
                credential.scopes,
            )
        ):
            raise CredentialResolutionError("TOKEN_INVALID")
        try:
            issued_at = _as_utc(credential.issued_at)
            not_before = _as_utc(credential.not_before)
            expires_at = _as_utc(credential.expires_at)
        except (AttributeError, TypeError, ValueError):
            raise CredentialResolutionError("TOKEN_INVALID") from None
        if not issued_at <= not_before < expires_at:
            raise CredentialResolutionError("TOKEN_INVALID")
        if credential.issuer != self.expected_issuer:
            raise CredentialResolutionError("ISSUER_MISMATCH")
        if _PUBLIC_AUDIENCE not in credential.audiences:
            raise CredentialResolutionError("AUDIENCE_MISMATCH")
        if credential.revoked_at is not None or credential.state == "REVOKED":
            raise CredentialResolutionError("TOKEN_REVOKED")
        if credential.state == "EXPIRED" or now >= expires_at:
            raise CredentialResolutionError("TOKEN_EXPIRED")
        if credential.state != "ACTIVE":
            raise CredentialResolutionError("TOKEN_INVALID")
        if now < not_before:
            raise CredentialResolutionError("TOKEN_NOT_YET_VALID")
        if now < issued_at:
            raise CredentialResolutionError("TOKEN_NOT_YET_VALID")

        principal = self.session.get(PublicPrincipal, credential.principal_id)
        if principal is None or not _exact_grants_match(credential, principal):
            raise CredentialResolutionError("TOKEN_INVALID")
        if principal.state == "REVOKED" or principal.revoked_at is not None:
            raise CredentialResolutionError("TOKEN_REVOKED")
        if principal.state != "ACTIVE":
            raise CredentialResolutionError("TOKEN_INVALID")
        if requested_workspace_id != principal.workspace_id:
            raise CredentialResolutionError("WORKSPACE_ACCESS_DENIED")

        resolved_workspace_id = principal.workspace_id
        if required_scope not in credential.scopes:
            raise CredentialResolutionError(
                "SCOPE_FORBIDDEN", workspace_id=resolved_workspace_id
            )
        if project_id is not None and project_id not in credential.project_ids:
            raise CredentialResolutionError(
                "WORKSPACE_ACCESS_DENIED", workspace_id=resolved_workspace_id
            )
        if (
            environment_id is not None
            and environment_id not in credential.environment_ids
        ):
            raise CredentialResolutionError(
                "WORKSPACE_ACCESS_DENIED", workspace_id=resolved_workspace_id
            )

        try:
            return AcceptedPrincipalContext(
                schema_version="1.0",
                principal_id=principal.principal_id,
                principal_type=principal.principal_type,
                issuer=credential.issuer,
                subject=credential.subject,
                audiences=list(credential.audiences),
                workspace_id=principal.workspace_id,
                project_ids=list(credential.project_ids),
                environment_ids=list(credential.environment_ids),
                scopes=list(credential.scopes),
                credential_id=credential.credential_id,
                jti_digest=credential.jti_digest,
                issued_at=issued_at,
                not_before=not_before,
                expires_at=expires_at,
                revoked_at=None,
                revocation_checked_at=now,
                requested_context=RequestedPrincipalContext(
                    workspace_id=principal.workspace_id,
                    project_id=project_id,
                    environment_id=environment_id,
                    required_scope=required_scope,
                ),
                evaluated_at=now,
                claims_digest=credential.claims_digest,
            )
        except ValidationError:
            raise CredentialResolutionError("TOKEN_INVALID") from None

    def bind_requested_context(
        self,
        principal: AcceptedPrincipalContext,
        *,
        project_id: str | None,
        environment_id: str | None,
        required_scope: str,
    ) -> AcceptedPrincipalContext:
        """Bind body project/environment after pre-body bearer authentication."""

        if required_scope not in principal.scopes:
            raise CredentialResolutionError(
                "SCOPE_FORBIDDEN", workspace_id=principal.workspace_id
            )
        if project_id is not None and project_id not in principal.project_ids:
            raise CredentialResolutionError(
                "WORKSPACE_ACCESS_DENIED", workspace_id=principal.workspace_id
            )
        if (
            environment_id is not None
            and environment_id not in principal.environment_ids
        ):
            raise CredentialResolutionError(
                "WORKSPACE_ACCESS_DENIED", workspace_id=principal.workspace_id
            )
        payload: dict[str, Any] = principal.model_dump(mode="python")
        payload["requested_context"] = {
            "workspace_id": principal.workspace_id,
            "project_id": project_id,
            "environment_id": environment_id,
            "required_scope": required_scope,
        }
        try:
            return AcceptedPrincipalContext.model_validate(payload)
        except ValidationError:
            raise CredentialResolutionError("TOKEN_INVALID") from None


__all__ = [
    "CredentialResolutionError",
    "PublicCredentialResolver",
    "digest_public_subject",
    "hash_opaque_bearer",
]
